"""Voice-first Streamlit UI for the Grameen Seva farmer assistant.

The presentation and interaction flow mirror the kiosk reference app. The
conversation itself continues to use the farmer app's persistent repositories,
knowledge cache, eligibility service, and shared Twilio-compatible service.
"""

from __future__ import annotations

import html

import streamlit as st

from agents.conversation import _localized_fallback
from config.settings import database_path, knowledge_cache_ttl_seconds, required_secrets, secret
from models.conversation import ConversationState
from repositories import create_repositories
from repositories.farmers import normalize_spoken_phone
from services.conversation import ConversationService
from services.eligibility import EligibilityService
from services.farmer_profile import FarmerProfileService
from services.knowledge import KnowledgeService
from services.sarvam import text_to_speech, transcribe


st.set_page_config(
    page_title="Grameen Seva AI Hub",
    page_icon="🌾",
    layout="centered",
    initial_sidebar_state="collapsed",
)


def init_state() -> None:
    if "conversation_service" not in st.session_state:
        repositories = create_repositories(database_path())
        knowledge = KnowledgeService(
            repositories.research_cache,
            scheme_repository=repositories.schemes,
            ttl_seconds=knowledge_cache_ttl_seconds(),
        )
        profiles = FarmerProfileService(repositories.farmers, repositories.conversations)
        st.session_state.conversation_service = ConversationService(
            repositories.conversations, knowledge, profiles, EligibilityService()
        )

    defaults = {
        "farmer_id": None,
        "conversation": ConversationState(),
        "tts_audio": None,
        "tts_token": 0,
        "last_played_tts_token": -1,
        "last_audio_hash": "",
        "phone_audio_hash": "",
        "error_message": "",
        "processing_steps": [],
        "processing_status": "",
        "recorder_reset_token": 0,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    # Streamlit can retain session values across deployments/reloads. Discard
    # stale values from an older app version instead of crashing on startup.
    conversation = st.session_state.get("conversation")
    if not isinstance(conversation, ConversationState):
        conversation = ConversationState()
        st.session_state["conversation"] = conversation
    if st.session_state.farmer_id is None:
        st.session_state.farmer_id = st.session_state.conversation_service.start_new_farmer()
    conversation.farmer_id = st.session_state.farmer_id


def language_name(code: str) -> str:
    names = {
        "hi": "हिन्दी", "te": "తెలుగు", "ta": "தமிழ்", "kn": "ಕನ್ನಡ",
        "mr": "मराठी", "bn": "বাংলা", "gu": "ગુજરાતી", "pa": "ਪੰਜਾਬੀ",
    }
    code = (code or "").lower()
    return next((name for prefix, name in names.items() if code.startswith(prefix)), "")


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


def render_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=Montserrat:wght@600;700&display=swap');
        #MainMenu, footer {visibility:hidden;}
        .stApp {background:#fcf9f8;}
        .block-container {max-width:760px;padding-top:2.5rem;padding-bottom:18rem;}
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
        div[data-testid="stAudioInput"] button {background:#0d631b;color:#fff;border-radius:12px;}
        </style>
        """,
        unsafe_allow_html=True,
    )


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
    cols[0].metric("Subsidy", f"{result.subsidy_percent}%" if result.subsidy_percent else "Not stated")
    cols[1].metric("Maximum amount", format_inr(result.max_claim_inr))
    if result.scheme_name:
        st.markdown(f"**Scheme:** {html.escape(result.scheme_name)}")
    if result.equipment_or_input:
        st.markdown(f"**For:** {html.escape(result.equipment_or_input)}")
    details = " · ".join(filter(None, [result.farmer_category, result.district, result.land_size]))
    if details:
        st.caption(f"Details considered: {html.escape(details)}")
    if result.required_documents:
        st.markdown("**Documents usually required**")
        st.markdown("\n".join(f"- {html.escape(item)}" for item in result.required_documents))
    if result.source_url:
        st.link_button("Open official source", result.source_url, use_container_width=True)


def speak(text: str, language: str = "en-IN") -> bool:
    try:
        audio = text_to_speech(text, language, secret("SARVAM_API_KEY"))
    except Exception:
        return False
    st.session_state.tts_audio = audio
    st.session_state.tts_token += 1
    return True


def record_processing_step(message: str, status=None) -> None:
    st.session_state.processing_steps.append(message)
    if status:
        status.write(message)


def quirky_greeting(language: str, name: str = "") -> str:
    code = (language or "").casefold()
    suffix = f" {name}" if name else ""
    if code.startswith("te"):
        return f"నమస్తే కాకా, ఏం సంగతులు{suffix}? మీ వివరాలు నాకు గుర్తున్నాయి."
    if code.startswith("hi"):
        return f"कैसे हो चाचा, क्या हाल-चाल{suffix}? आपकी जानकारी मुझे याद है।"
    if code.startswith("ta"):
        return f"வணக்கம் மாமா, எப்படி இருக்கீங்க{suffix}? உங்கள் விவரங்கள் எனக்கு நினைவில் இருக்கிறது."
    if code.startswith("kn"):
        return f"ನಮಸ್ಕಾರ ಕಾಕಾ, ಹೇಗಿದ್ದೀರಾ{suffix}? ನಿಮ್ಮ ವಿವರಗಳು ನನಗೆ ನೆನಪಿದೆ."
    return f"Namaste kaka, how are you{suffix}? I remember your farmer details."


def process_audio(audio_bytes: bytes, status=None) -> bool:
    st.session_state.error_message = ""
    st.session_state.recorder_reset_token += 1
    record_processing_step("Converting your voice to text…", status)
    record_processing_step("Understanding your farming question…", status)
    record_processing_step("Checking official government scheme information…", status)
    outcome = st.session_state.conversation_service.process_audio(
        audio_bytes,
        st.session_state.conversation,
        transcribe_fn=transcribe,
        text_to_speech_fn=text_to_speech,
        sarvam_key=secret("SARVAM_API_KEY"),
        gemini_key=secret("GEMINI_API_KEY"),
        tavily_key=secret("TAVILY_API_KEY"),
        firecrawl_key=secret("FIRECRAWL_API_KEY"),
        farmer_id=st.session_state.farmer_id,
    )
    st.session_state.error_message = outcome.error_message
    candidate_phone = normalize_spoken_phone(
        st.session_state.conversation.result.mobile_number
        or st.session_state.conversation.transcript
    )
    if candidate_phone and candidate_phone != st.session_state.farmer_id:
        try:
            farmer_id, returning = st.session_state.conversation_service.bind_phone_identity(
                st.session_state.conversation,
                st.session_state.farmer_id,
                candidate_phone,
            )
            st.session_state.farmer_id = farmer_id
            if returning and not st.session_state.conversation.result.conversation_complete:
                farmer = st.session_state.conversation_service.farmer_profiles.get(farmer_id)
                greeting = quirky_greeting(
                    st.session_state.conversation.language_code,
                    farmer.name if farmer else "",
                )
                st.session_state.conversation.add_turn("assistant", greeting)
                st.session_state.tts_audio = text_to_speech(
                    greeting,
                    st.session_state.conversation.language_code or "en-IN",
                    secret("SARVAM_API_KEY"),
                )
                st.session_state.tts_token += 1
        except Exception:
            st.session_state.error_message = (st.session_state.error_message + " Your chat will continue, but I could not save your mobile identity.").strip()
    if outcome.audio:
        st.session_state.tts_audio = outcome.audio
        st.session_state.tts_token += 1
        record_processing_step("Preparing the spoken reply…", status)
    return outcome.success


init_state()
render_styles()
conversation: ConversationState = st.session_state.conversation

st.markdown('<div class="brand">🌾 Grameen Seva AI Hub</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Your voice-first guide to government farming schemes</div>', unsafe_allow_html=True)

if conversation.language_code:
    detected_name = language_name(conversation.language_code)
    if detected_name:
        st.success(f"Language detected: {detected_name}")

render_chat(conversation)
render_result(conversation)

if st.session_state.error_message:
    st.warning(st.session_state.error_message)

missing = [key for key in required_secrets() if not secret(key)]
if missing:
    st.info("Add the required API keys to Streamlit secrets before using the microphone: " + ", ".join(missing))

if not missing and not conversation.result.conversation_complete:
    st.markdown("### Speak your farming need")
    st.caption("Tell me what scheme or subsidy you need. I’ll ask one question at a time, including your name and mobile number later so I can remember you.")
    audio = st.audio_input("Record your question", key=f"farmer_audio_{st.session_state.recorder_reset_token}")
    if audio is not None:
        audio_bytes = audio.getvalue()
        audio_hash = str(hash(audio_bytes))
        if audio_hash != st.session_state.last_audio_hash:
            st.session_state.last_audio_hash = audio_hash
            st.session_state.processing_steps = ["Starting voice processing…"]
            st.session_state.processing_status = "Processing your voice…"
            with st.status("Processing your voice…", expanded=True) as status:
                success = process_audio(audio_bytes, status)
                st.session_state.processing_status = "Reply ready" if success else "Please try speaking again"
                status.update(
                    label=st.session_state.processing_status,
                    state="complete" if success else "error",
                    expanded=False,
                )
            st.rerun()

if st.session_state.processing_status:
    with st.status(st.session_state.processing_status, expanded=True) as status:
        for step in st.session_state.processing_steps:
            status.write(step)
        status.update(
            label=st.session_state.processing_status,
            state="complete" if st.session_state.processing_status == "Reply ready" else "error",
            expanded=True,
        )

if st.session_state.tts_audio:
    autoplay = st.session_state.last_played_tts_token != st.session_state.tts_token
    st.audio(st.session_state.tts_audio, format="audio/wav", autoplay=autoplay)
    st.session_state.last_played_tts_token = st.session_state.tts_token
