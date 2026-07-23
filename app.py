"""Streamlit UI for the farmer government-scheme inquiry assistant."""

from __future__ import annotations

import html

import streamlit as st

from config.settings import database_path, knowledge_cache_ttl_seconds, secret
from models.conversation import ConversationState
from repositories import create_repositories
from repositories.farmers import normalize_spoken_phone
from services.conversation import ConversationService
from services.eligibility import EligibilityService
from services.farmer_profile import FarmerProfileService
from services.knowledge import KnowledgeService
from services.sarvam import text_to_speech, transcribe


st.set_page_config(page_title="Grameen Seva AI Hub", page_icon="🌾", layout="centered")


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
        "identity_pending": True,
        "onboarding_message": "",
        "tts_audio": None,
        "tts_token": 0,
        "last_played_tts_token": -1,
        "last_audio_hash": "",
        "recorder_reset_token": 0,
        "phone_audio_hash": "",
        "error_message": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    if not st.session_state.conversation.turns:
        st.session_state.conversation.add_turn(
            "assistant",
            "Please tell me your 10-digit mobile number so I can find your farmer details.",
        )


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
        #MainMenu, footer {visibility:hidden;}
        .stApp {background:#fbf7ef;color:#2f3b2f;}
        .block-container {max-width:760px;padding-top:2.5rem;padding-bottom:18rem;}
        .brand {text-align:center;color:#0d631b;font:700 2.2rem Montserrat;margin-bottom:.25rem;}
        .subtitle {text-align:center;color:#40493d;font-size:1.05rem;margin-bottom:1.5rem;}
        .empty-card {background:#fff;border:1px solid #dce7d8;border-radius:24px;padding:2rem;text-align:center;color:#0d631b;font-size:1.35rem;font-weight:600;margin:1rem 0 2rem;box-shadow:0 10px 30px #2e7d3214;}
        .empty-card small {color:#596653;font-size:1rem;font-weight:400;}
        .bubble {border-radius:22px;padding:1rem 1.2rem;margin:.7rem 0;font-size:1.15rem;line-height:1.55;white-space:pre-wrap;}
        .bubble-label {font-size:.8rem;font-weight:700;margin-bottom:.25rem;opacity:.75;}
        .farmer-bubble {background:#dcefd4;color:#245b2a;margin-left:15%;border-top-right-radius:5px;}
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
        st.markdown('<div class="empty-card">Tap the microphone and speak naturally.<br><small>I will detect your language automatically.</small></div>', unsafe_allow_html=True)
        return
    for turn in conversation.turns:
        bubble = "farmer-bubble" if turn["role"] == "farmer" else "assistant-bubble"
        label = "You" if turn["role"] == "farmer" else "Grameen AI"
        st.markdown(
            f'<div class="bubble {bubble}"><div class="bubble-label">{label}</div>{html.escape(turn["text"])}</div>',
            unsafe_allow_html=True,
        )


def render_profile_summary() -> None:
    farmer = st.session_state.conversation_service.farmer_profiles.get(st.session_state.farmer_id)
    if not farmer:
        return
    values = [
        ("Mobile", farmer.phone), ("Name", farmer.name), ("State", farmer.state), ("District", farmer.district),
        ("Land", farmer.land_size), ("Category", farmer.farmer_category),
        ("Crops", ", ".join(farmer.current_crops)),
    ]
    known = [f"**{label}:** {html.escape(value)}" for label, value in values if value]
    if known:
        st.markdown("### Farmer profile summary")
        st.markdown("  \n".join(known))
    if farmer.recommendations:
        st.caption("Previous recommendations: " + ", ".join(farmer.recommendations[-5:]))
    if farmer.conversation_summaries:
        st.caption("Recent conversation: " + farmer.conversation_summaries[-1])


def render_result(conversation: ConversationState) -> None:
    result = conversation.result
    if not result.conversation_complete or result.goodbye_detected:
        return
    st.markdown("### Eligibility result")
    st.metric("Status", result.eligibility_status.replace("_", " "))
    st.caption(f"Confidence: {result.eligibility_confidence:.0%}")
    if result.eligibility_reasons:
        st.markdown("**Reasoning**")
        st.markdown("\n".join(f"- {html.escape(item)}" for item in result.eligibility_reasons))
    if result.recommended_next_action:
        st.info(result.recommended_next_action)
    if result.scheme_name:
        st.markdown(f"**Suggested scheme:** {html.escape(result.scheme_name)}")
    if result.equipment_or_input:
        st.markdown(f"**For:** {html.escape(result.equipment_or_input)}")
    if result.benefits:
        st.markdown("**Benefits**")
        st.markdown("\n".join(f"- {html.escape(item)}" for item in result.benefits))
    if result.required_documents:
        st.markdown("**Required documents**")
        st.markdown("\n".join(f"- {html.escape(item)}" for item in result.required_documents))
    if result.application_process:
        st.markdown(f"**Application process:** {html.escape(result.application_process)}")
    cols = st.columns(2)
    cols[0].metric("Subsidy", f"{result.subsidy_percent}%" if result.subsidy_percent else "Not stated")
    cols[1].metric("Maximum amount", format_inr(result.max_claim_inr))
    if result.source_url:
        st.markdown("### Official source")
        st.link_button("Open official source", result.source_url, use_container_width=True)


def process_text(text: str) -> None:
    st.session_state.error_message = ""
    outcome = st.session_state.conversation_service.process_text(
        text,
        st.session_state.conversation,
        farmer_id=st.session_state.farmer_id,
        gemini_key=secret("GEMINI_API_KEY"),
        tavily_key=secret("TAVILY_API_KEY"),
        firecrawl_key=secret("FIRECRAWL_API_KEY"),
    )
    st.session_state.error_message = outcome.error_message


def speak(text: str, language: str = "en-IN") -> None:
    try:
        audio = text_to_speech(text, language, secret("SARVAM_API_KEY"))
    except Exception:
        return
    st.session_state.tts_audio = audio
    st.session_state.tts_token += 1


def process_identity_audio(audio_bytes: bytes) -> None:
    """Resolve the farmer from the first spoken turn before normal chat begins."""
    try:
        transcript, detected_language = transcribe(audio_bytes, secret("SARVAM_API_KEY"))
    except Exception:
        return
    phone = normalize_spoken_phone(transcript)
    if not phone:
        conversation: ConversationState = st.session_state.conversation
        conversation.add_turn("farmer", transcript or "")
        response = "Please tell me your 10-digit mobile number first, one digit at a time."
        conversation.add_turn("assistant", response)
        speak(response, detected_language or "en-IN")
        return
    try:
        farmer, conversation, returning = st.session_state.conversation_service.load_or_create_farmer(phone)
    except ValueError:
        return
    st.session_state.farmer_id = farmer.id
    st.session_state.conversation = conversation
    st.session_state.identity_pending = False
    if returning:
        response = f"Welcome back {farmer.name or 'farmer'}. I found your saved details. What farming help do you need today?"
    else:
        response = "Namaskaram. Your farmer profile is ready. Tell me what farming support you need."
        conversation.add_turn("assistant", response)
    speak(response, conversation.language_code or detected_language or "en-IN")


def process_audio(audio_bytes: bytes) -> None:
    st.session_state.error_message = ""
    st.session_state.recorder_reset_token += 1
    if st.session_state.identity_pending:
        process_identity_audio(audio_bytes)
        return
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
    if not outcome.success:
        st.session_state.error_message = outcome.error_message
        return
    if outcome.audio:
        st.session_state.tts_audio = outcome.audio
        st.session_state.tts_token += 1


def reset_farmer() -> None:
    st.session_state.farmer_id = None
    st.session_state.conversation = None
    st.session_state.onboarding_message = ""
    st.session_state.tts_audio = None
    st.session_state.error_message = ""
    st.session_state.last_audio_hash = ""


def render_onboarding() -> None:
    """Identify the farmer through the microphone before starting the conversation."""
    audio = st.audio_input("", label_visibility="collapsed", key=f"phone_audio_{st.session_state.recorder_reset_token}")
    if audio is None:
        return
    audio_bytes = audio.getvalue()
    audio_hash = str(hash(audio_bytes))
    if audio_hash == st.session_state.phone_audio_hash:
        return
    st.session_state.phone_audio_hash = audio_hash
    try:
        transcript, _ = transcribe(audio_bytes, secret("SARVAM_API_KEY"))
        phone = normalize_spoken_phone(transcript)
    except Exception:
        return
    if not phone:
        return
    try:
        farmer, conversation, returning = st.session_state.conversation_service.load_or_create_farmer(phone)
    except ValueError:
        return
    st.session_state.farmer_id = farmer.id
    st.session_state.conversation = conversation
    if returning:
        name = farmer.name or "kaka"
        st.session_state.onboarding_message = (
            f"Welcome back {name}! Your saved profile and previous conversation are ready. "
            "What farming help do you need today?"
        )
    else:
        st.session_state.onboarding_message = (
            "Namaskaram! Your farmer profile is ready. Tell me what farming support you need, "
            "and I will remember the details for next time."
        )
    try:
        st.session_state.tts_audio = text_to_speech(
            st.session_state.onboarding_message,
            conversation.language_code or "en-IN",
            secret("SARVAM_API_KEY"),
        )
        st.session_state.tts_token += 1
    except Exception:
        st.session_state.tts_audio = None
    st.rerun()


init_state()
render_styles()
st.markdown('<div class="brand">🌾 Grameen Seva AI Hub</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">AI-powered government scheme and subsidy assistant for farmers</div>', unsafe_allow_html=True)

conversation: ConversationState = st.session_state.conversation
render_chat(conversation)

if not conversation.result.conversation_complete:
    audio = st.audio_input("Record your question", key=f"farmer_audio_{st.session_state.recorder_reset_token}")
    if audio is not None:
        audio_bytes = audio.getvalue()
        audio_hash = str(hash(audio_bytes))
        if audio_hash != st.session_state.last_audio_hash:
            st.session_state.last_audio_hash = audio_hash
            process_audio(audio_bytes)
            st.rerun()

if st.session_state.tts_audio:
    autoplay = st.session_state.last_played_tts_token != st.session_state.tts_token
    st.audio(st.session_state.tts_audio, format="audio/wav", autoplay=autoplay)
    st.session_state.last_played_tts_token = st.session_state.tts_token
