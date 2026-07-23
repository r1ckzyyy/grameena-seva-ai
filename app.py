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
        "conversation": None,
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
    if st.session_state.farmer_id is None:
        farmer_id = st.session_state.conversation_service.start_new_farmer()
        st.session_state.farmer_id = farmer_id
        st.session_state.conversation = ConversationState(farmer_id=farmer_id)


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
        .block-container {max-width:760px;padding-top:1.25rem;padding-bottom:3rem;}
        .brand {text-align:center;color:#176b35;font-size:2.2rem;font-weight:800;margin-bottom:.2rem;}
        .subtitle {text-align:center;color:#765d3b;font-size:1rem;margin-bottom:1.6rem;}
        .bubble {border-radius:20px;padding:1rem 1.2rem;margin:.7rem 0;font-size:1.08rem;line-height:1.55;white-space:pre-wrap;}
        .farmer-bubble {background:#d9edcf;color:#245b2a;margin-left:15%;border-top-right-radius:5px;}
        .assistant-bubble {background:#fff1d6;color:#4d3826;margin-right:8%;border:1px solid #ead3a6;border-top-left-radius:5px;}
        div[data-testid="stAudioInput"] {background:#e7f2df;border:1px solid #b7d3a8;border-radius:18px;padding:.35rem;}
        div[data-testid="stAudioInput"] button {background:#5b9b4b;color:#fff;border-radius:12px;}
        div[data-testid="stAudioInput"] button:hover {background:#3f7e37;color:#fff;}
        .stAudio {margin-top:1rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_chat(conversation: ConversationState) -> None:
    if not conversation.turns:
        return
    for turn in conversation.turns:
        bubble = "farmer-bubble" if turn["role"] == "farmer" else "assistant-bubble"
        st.markdown(
            f'<div class="bubble {bubble}">{html.escape(turn["text"])}</div>',
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


def process_audio(audio_bytes: bytes) -> None:
    st.session_state.error_message = ""
    st.session_state.recorder_reset_token += 1
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
    audio = st.audio_input("", label_visibility="collapsed", key=f"farmer_audio_{st.session_state.recorder_reset_token}")
    if audio is not None:
        audio_bytes = audio.getvalue()
        audio_hash = str(hash(audio_bytes))
        if audio_hash != st.session_state.last_audio_hash:
            st.session_state.last_audio_hash = audio_hash
            process_audio(audio_bytes)
            st.rerun()

if st.session_state.tts_audio:
    st.audio(st.session_state.tts_audio, format="audio/wav", autoplay=False)
