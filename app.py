"""Grameen Seva AI Hub: voice-first subsidy assistant for Indian farmers."""

from __future__ import annotations

import html
import json
import re
import smtplib
from email.message import EmailMessage
from typing import Any

import streamlit as st
from google.genai import types

from agents.conversation import GEMINI_MODEL, _gemini_client, _localized_fallback, run_conversation
from models.conversation import ConversationState
from services.sarvam import text_to_speech, transcribe


st.set_page_config(
    page_title="Grameen Seva AI Hub",
    page_icon="🌾",
    layout="centered",
    initial_sidebar_state="collapsed",
)


def secret(name: str) -> str:
    """Read a deployment secret without making the app crash when it is absent."""
    try:
        value = st.secrets.get(name, "")
    except (FileNotFoundError, KeyError, TypeError):
        value = ""
    return str(value or "")


def init_state() -> None:
    defaults = {
        "conversation": ConversationState(),
        "tts_audio": None,
        "tts_token": 0,
        "last_played_tts_token": -1,
        "last_audio_hash": "",
        "error_message": "",
        "form_data": {},
        "form_image_hash": "",
        "form_image_bytes": None,
        "form_image_type": "image/jpeg",
        "email_draft": "",
        "claim_intent": "undecided",
        "email_sent": False,
        "recorder_reset_token": 0,
        "uploaded_docs_data": {},
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def format_inr(amount: int) -> str:
    if amount <= 0:
        return "Not stated on the official source"
    value = str(int(amount))
    if len(value) <= 3:
        return f"₹{value}"
    last_three, rest = value[-3:], value[:-3]
    groups: list[str] = []
    while len(rest) > 2:
        groups.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        groups.insert(0, rest)
    return f"₹{','.join(groups + [last_three])}"


def language_name(code: str) -> str:
    names = {
        "hi": "हिन्दी", "te": "తెలుగు", "ta": "தமிழ்", "kn": "ಕನ್ನಡ",
        "mr": "मराठी", "bn": "বাংলা", "gu": "ગુજરાતી", "pa": "ਪੰਜਾਬੀ",
    }
    code = (code or "").lower()
    return next((name for prefix, name in names.items() if code.startswith(prefix)), "")


def parse_json_object(text: str) -> dict[str, str]:
    cleaned = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        value = json.loads(cleaned)
        return {str(key): str(item or "") for key, item in value.items()} if isinstance(value, dict) else {}
    except (json.JSONDecodeError, AttributeError, TypeError):
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                value = json.loads(match.group())
                return {str(key): str(item or "") for key, item in value.items()} if isinstance(value, dict) else {}
            except (json.JSONDecodeError, AttributeError, TypeError):
                return {}
    return {}


def extract_form(image_bytes: bytes, mime_type: str) -> dict[str, str]:
    prompt = """
You extract information from an Indian government agricultural subsidy form.
Read only text that is visible in the image or PDF. Do not guess or fill missing values.
Return only JSON with these keys:
farmer_name, father_or_spouse_name, mobile_number, address, state, district,
village, land_size, farmer_category, equipment_or_input, scheme_name,
application_number, documents_visible, unclear_fields.
Use an empty string for fields that are not visible. Keep names and addresses
in the script shown on the form. Do not provide eligibility advice.
"""
    client = _gemini_client(secret("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[types.Content(role="user", parts=[
            types.Part(text=prompt),
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type or "image/jpeg"),
        ])],
        config=types.GenerateContentConfig(temperature=0.1),
    )
    return parse_json_object(response.text or "")


def build_email_draft(form_data: dict[str, str], result: object, language_code: str, uploaded_labels: list[str]) -> str:
    attachments_str = ", ".join(uploaded_labels) if uploaded_labels else "None"
    prompt = f"""
Draft a polite email to the appropriate Indian government agriculture office
asking how to claim the subsidy. Do not claim that the farmer is eligible and
do not invent facts, amounts, scheme names, or email addresses. Mention that
the attached form is being submitted for guidance. Also mention that the following
required documents are attached: {attachments_str}.
Write the email in English unless the detected language is Telugu, Hindi, or Tamil,
in which case write it in that language. Return only the email body, with a clear subject line.

Detected language: {language_code}
Form details: {json.dumps(form_data, ensure_ascii=False)}
Conversation details: {json.dumps({
    'scheme_name': getattr(result, 'scheme_name', ''),
    'equipment_or_input': getattr(result, 'equipment_or_input', ''),
    'district': getattr(result, 'district', ''),
    'state': getattr(result, 'state', ''),
}, ensure_ascii=False)}
"""
    client = _gemini_client(secret("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.2),
    )
    return (response.text or "").strip()


def send_claim_email(body: str, form_image_bytes: bytes | None, form_image_type: str, additional_attachments: list[dict[str, Any]] = None) -> None:
    """Send only after the farmer explicitly clicks the send button."""
    username = secret("SMTP_USERNAME")
    password = secret("SMTP_PASSWORD")
    if not username or not password:
        raise RuntimeError("SMTP_USERNAME and SMTP_PASSWORD are missing from Streamlit secrets")
    message = EmailMessage()
    message["From"] = username
    message["To"] = "santoshdende@ewsattapur.com"
    message["Subject"] = "Request for help claiming an agricultural subsidy"
    message.set_content(body)
    if form_image_bytes:
        mime_type = form_image_type or "image/jpeg"
        maintype, subtype = mime_type.split("/", 1)
        message.add_attachment(
            form_image_bytes,
            maintype=maintype,
            subtype=subtype,
            filename=_form_attachment_filename(mime_type),
        )
    if additional_attachments:
        for att in additional_attachments:
            if att.get("bytes"):
                filename = att.get("name") or "document.jpg"
                mime_type = att.get("type") or "image/jpeg"
                maintype, subtype = mime_type.split("/", 1)
                message.add_attachment(att["bytes"], maintype=maintype, subtype=subtype, filename=filename)
    with smtplib.SMTP(secret("SMTP_HOST") or "smtp.gmail.com", int(secret("SMTP_PORT") or "587")) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(message)


def _store_form_image(image_bytes: bytes, mime_type: str) -> None:
    image_hash = str(hash(image_bytes))
    if image_hash != st.session_state.form_image_hash:
        st.session_state.form_image_hash = image_hash
        st.session_state.form_image_bytes = image_bytes
        st.session_state.form_image_type = mime_type or "image/jpeg"
        st.session_state.form_data = {}
        st.session_state.email_draft = ""
        st.session_state.email_sent = False


def _form_attachment_filename(mime_type: str) -> str:
    if mime_type == "application/pdf":
        return "subsidy_form.pdf"
    if mime_type == "image/png":
        return "subsidy_form.png"
    return "subsidy_form.jpg"


def render_form_assistant(conversation: ConversationState) -> None:
    st.markdown("### Scan a subsidy form")
    st.caption("Take a photo or attach a file of your printed form. Review every extracted detail before using it.")
    if not secret("GEMINI_API_KEY"):
        st.info("Add GEMINI_API_KEY to use form scanning.")
        return

    form_bytes: bytes | None = None
    form_mime = "image/jpeg"

    camera_image = st.camera_input("Capture the form", key="subsidy_form_camera")
    uploaded_form = st.file_uploader(
        "Or attach a form photo or PDF",
        type=["png", "jpg", "jpeg", "pdf"],
        key="subsidy_form_upload",
    )

    if uploaded_form is not None:
        form_bytes = uploaded_form.getvalue()
        form_mime = uploaded_form.type or "image/jpeg"
    elif camera_image is not None:
        form_bytes = camera_image.getvalue()
        form_mime = camera_image.type or "image/jpeg"

    if form_bytes is not None:
        _store_form_image(form_bytes, form_mime)
        if st.button("Read form", key="read_form", use_container_width=True):
            try:
                with st.spinner("Reading the form…"):
                    st.session_state.form_data = extract_form(form_bytes, form_mime)
                if not st.session_state.form_data:
                    st.warning("I could not read the form. Place it flat in good light and try again.")
                else:
                    st.success("Form details extracted. Please review them below.")
            except Exception:
                st.error("The form could not be read right now. Check the photo and try again.")

    form_data = st.session_state.form_data
    if not form_data:
        return
    st.markdown("#### Review extracted details")
    editable_keys = [
        "farmer_name", "father_or_spouse_name", "mobile_number", "address",
        "state", "district", "village", "land_size", "farmer_category",
        "equipment_or_input", "scheme_name", "application_number",
        "documents_visible", "unclear_fields",
    ]
    cols = st.columns(2)
    for index, key in enumerate(editable_keys):
        label = key.replace("_", " ").title()
        with cols[index % 2]:
            form_data[key] = st.text_input(label, value=form_data.get(key, ""), key=f"form_{key}")
    st.session_state.form_data = form_data

    # Dynamic document upload interface based on required documents
    st.markdown("---")
    st.markdown("#### Upload required documents")
    st.caption("Take a photo or attach a file for each required document:")

    required_docs = conversation.result.required_documents
    if not required_docs:
        required_docs = ["Aadhar Card Photo", "Land Documents Photo"]

    uploaded_files = []
    for doc in required_docs:
        doc_slug = doc.lower().replace(" ", "_")
        doc_key = f"doc_upload_{doc_slug}"
        doc_cols = st.columns(2)
        with doc_cols[0]:
            camera_doc = st.camera_input(f"Photo: {doc}", key=f"doc_camera_{doc_slug}")
        with doc_cols[1]:
            uploaded_file = st.file_uploader(
                f"Attach file: {doc}",
                type=["png", "jpg", "jpeg", "pdf"],
                key=doc_key,
            )

        attachment_bytes: bytes | None = None
        attachment_name = ""
        attachment_type = "image/jpeg"
        if uploaded_file is not None:
            attachment_bytes = uploaded_file.getvalue()
            attachment_name = uploaded_file.name
            attachment_type = uploaded_file.type or "image/jpeg"
        elif camera_doc is not None:
            attachment_bytes = camera_doc.getvalue()
            attachment_name = f"{doc_slug}.jpg"
            attachment_type = camera_doc.type or "image/jpeg"

        if attachment_bytes is not None:
            uploaded_files.append({
                "name": attachment_name,
                "bytes": attachment_bytes,
                "type": attachment_type,
                "label": doc,
            })

    if st.button("Draft email to government office", key="draft_email", use_container_width=True):
        try:
            with st.spinner("Drafting the email…"):
                uploaded_labels = [f["label"] for f in uploaded_files]
                st.session_state.email_draft = build_email_draft(
                    form_data, 
                    conversation.result, 
                    conversation.language_code,
                    uploaded_labels
                )
        except Exception as exc:
            st.error(f"The email draft could not be prepared: {exc}")
            
    if st.session_state.email_draft:
        st.markdown("#### Email draft — review before sending")
        st.text_area("Draft", value=st.session_state.email_draft, height=300, key="email_preview")
        if st.button("Send claim request to santoshdende@ewsattapur.com", key="send_claim_email", use_container_width=True):
            try:
                with st.spinner("Sending the claim request…"):
                    send_claim_email(
                        st.session_state.get("email_preview", st.session_state.email_draft),
                        st.session_state.form_image_bytes,
                        st.session_state.form_image_type,
                        uploaded_files
                    )
                st.session_state.email_sent = True
            except Exception as exc:
                st.error(f"Email was not sent: {exc}")
        if st.session_state.email_sent:
            st.success("The claim request was sent to the test government mailbox.")
        st.download_button(
            "Download draft",
            data=st.session_state.email_draft,
            file_name="subsidy_claim_email.txt",
            mime="text/plain",
            use_container_width=True,
        )


def handle_recording(audio_bytes: bytes) -> bool:
    conversation: ConversationState = st.session_state.conversation
    st.session_state.error_message = ""
    # Force the browser component out of its previous state.
    st.session_state.recorder_reset_token += 1
    conversation.set_state("PROCESSING")

    with st.status("Processing your request…", expanded=True) as status:
        status.write("Converting your voice to text…")
        try:
            transcript, detected_language = transcribe(audio_bytes, secret("SARVAM_API_KEY"))
        except Exception:
            st.session_state.error_message = _localized_fallback(conversation.language_code, "temporary")
            conversation.set_state("LISTENING")
            status.update(label="Please try again", state="error", expanded=False)
            return False

        if not transcript:
            st.session_state.error_message = _localized_fallback(conversation.language_code, "repeat")
            conversation.set_state("LISTENING")
            status.update(label="No speech was detected", state="error", expanded=False)
            return False

        conversation.transcript = transcript
        if detected_language:
            conversation.language_code = detected_language
        conversation.add_turn("farmer", transcript)
        conversation.set_state("THINKING")
        status.write("Understanding your question…")

        result = run_conversation(
            conversation,
            secret("GEMINI_API_KEY"),
            secret("TAVILY_API_KEY"),
            secret("FIRECRAWL_API_KEY"),
        )
        conversation.result = result

        response_text = (result.voice_response or "").strip()
        if not response_text:
            response_text = _localized_fallback(conversation.language_code, "prompt")
        result.voice_response = response_text
        result.next_question = ""
        conversation.add_turn("assistant", response_text)
        conversation.goodbye_detected = result.goodbye_detected
        conversation.set_state("SPEAKING")
        status.write("Preparing your answer in the same language…")

        try:
            st.session_state.tts_audio = text_to_speech(
                response_text,
                conversation.language_code or result.language or "hi-IN",
                secret("SARVAM_API_KEY"),
            )
            st.session_state.tts_token += 1
        except Exception:
            # The text answer remains useful even if audio generation is temporarily unavailable.
            st.session_state.tts_audio = None
            st.session_state.error_message = response_text

        conversation.set_state("COMPLETED" if result.conversation_complete else "LISTENING")
        status.update(label="Answer ready", state="complete", expanded=False)
    return True


def render_chat(conversation: ConversationState) -> None:
    if not conversation.turns:
        st.markdown(
            '<div class="empty-card">Tap the microphone and speak naturally.<br><small>I will detect your language automatically.</small></div>',
            unsafe_allow_html=True,
        )
        return
    for turn in conversation.turns:
        bubble = "farmer-bubble" if turn["role"] == "farmer" else "assistant-bubble"
        label = "You" if turn["role"] == "farmer" else "Grameen AI"
        st.markdown(
            f'<div class="bubble {bubble}"><div class="bubble-label">{label}</div>{html.escape(turn["text"])}</div>',
            unsafe_allow_html=True,
        )


def render_result(conversation: ConversationState) -> None:
    result = conversation.result
    if not result.conversation_complete or result.goodbye_detected:
        return

    st.markdown('<div class="result-title">Verified government information</div>', unsafe_allow_html=True)
    cols = st.columns(2)
    with cols[0]:
        st.metric("Subsidy", f"{result.subsidy_percent}%" if result.subsidy_percent else "Not stated")
    with cols[1]:
        st.metric("Maximum amount", format_inr(result.max_claim_inr))
    if result.scheme_name:
        st.markdown(f"**Scheme:** {html.escape(result.scheme_name)}")
    if result.equipment_or_input:
        st.markdown(f"**For:** {html.escape(result.equipment_or_input)}")
    if result.farmer_category or result.district or result.land_size:
        details = " · ".join(filter(None, [result.farmer_category, result.district, result.land_size]))
        st.caption(f"Details considered: {details}")
    if result.required_documents:
        st.markdown("**Documents usually required**")
        st.markdown("\n".join(f"- {html.escape(item)}" for item in result.required_documents))
    if result.source_url:
        st.link_button("Open official source", result.source_url, use_container_width=True)
    st.markdown("### Would you like help claiming this subsidy?")
    st.caption("I can read your form, prepare a request, and send it to the test mailbox only after you approve it.")
    claim_cols = st.columns(2)
    with claim_cols[0]:
        if st.button("Yes, help me claim", key="claim_yes", use_container_width=True):
            st.session_state.claim_intent = "yes"
            st.rerun()
    with claim_cols[1]:
        if st.button("No, not now", key="claim_no", use_container_width=True):
            st.session_state.claim_intent = "no"
            st.rerun()


def render_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=Montserrat:wght@600;700&display=swap');
        #MainMenu, footer {visibility:hidden;} .stApp {background:#fcf9f8;}
        .block-container {max-width:760px; padding-top:2.5rem; padding-bottom:18rem;}
        .brand {text-align:center;color:#0d631b;font:700 2.2rem Montserrat;margin-bottom:.25rem;}
        .subtitle {text-align:center;color:#40493d;font-size:1.05rem;margin-bottom:1.5rem;}
        .empty-card {background:#fff;border:1px solid #dce7d8;border-radius:24px;padding:2rem;text-align:center;color:#0d631b;font-size:1.35rem;font-weight:600;margin:1rem 0 2rem;box-shadow:0 10px 30px #2e7d3214;}
        .empty-card small {color:#596653;font-size:1rem;font-weight:400;}
        .bubble {border-radius:22px;padding:1rem 1.2rem;margin:.7rem 0;font-size:1.15rem;line-height:1.55;white-space:pre-wrap;}
        .bubble-label {font-size:.8rem;font-weight:700;margin-bottom:.25rem;opacity:.75;}
        .farmer-bubble {background:#81c784;color:#fff;margin-left:15%;border-top-right-radius:5px;}
        .assistant-bubble {background:#fff;color:#1b1b1b;margin-right:8%;border:1px solid #dce7d8;box-shadow:0 8px 24px #2e7d3210;border-top-left-radius:5px;}
        .result-title {color:#0d631b;font:700 1.3rem Montserrat;margin-top:1.5rem;margin-bottom:.7rem;}
        div[data-testid="stMetric"] {background:#fff;border:1px solid #dce7d8;border-radius:16px;padding:1rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )


init_state()
render_styles()
conversation: ConversationState = st.session_state.conversation

st.markdown('<div class="brand">🌾 Grameen Seva AI Hub</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Your voice-first guide to government farming schemes</div>', unsafe_allow_html=True)

if conversation.language_code:
    name = language_name(conversation.language_code)
    if name:
        st.success(f"Language detected: {name}")

render_chat(conversation)
render_result(conversation)

with st.expander(
    "📄 Scan a form and send a claim request",
    expanded=st.session_state.claim_intent == "yes",
):
    render_form_assistant(conversation)

if st.session_state.error_message:
    st.warning(st.session_state.error_message)

missing = [key for key in ("SARVAM_API_KEY", "GEMINI_API_KEY", "TAVILY_API_KEY", "FIRECRAWL_API_KEY") if not secret(key)]
if missing:
    st.info("Add the required API keys to Streamlit secrets before using the microphone: " + ", ".join(missing))

if conversation.result.conversation_complete:
    if st.button("Start a new question", use_container_width=True):
        for key in ("conversation", "tts_audio", "last_audio_hash", "error_message"):
            st.session_state.pop(key, None)
        st.rerun()

audio = None
if not conversation.result.conversation_complete and not missing:
    st.markdown("### Speak your question")
    st.caption("Click the microphone to start speaking, and click it again to finish and send.")
    # Key rotated dynamically to reset the widget to a fresh state after processing is complete.
    audio = st.audio_input(
        "Record your question", 
        key=f"farmer_audio_{st.session_state.recorder_reset_token}"
    )

audio_bytes: bytes | None = None
if audio is not None:
    try:
        audio_bytes = bytes(audio) if isinstance(audio, (bytes, bytearray)) else audio.getvalue()
    except (AttributeError, TypeError, ValueError):
        audio_bytes = None

if audio_bytes:
    audio_hash = str(hash(audio_bytes))
    if audio_hash != st.session_state.last_audio_hash:
        st.session_state.last_audio_hash = audio_hash
        handle_recording(audio_bytes)
        st.rerun()

if st.session_state.tts_audio and st.session_state.last_played_tts_token != st.session_state.tts_token:
    st.audio(st.session_state.tts_audio, format="audio/wav", autoplay=True)
    st.session_state.last_played_tts_token = st.session_state.tts_token
elif st.session_state.tts_audio:
    st.audio(st.session_state.tts_audio, format="audio/wav", autoplay=False)
