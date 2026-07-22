"""
Grameen Seva AI Hub — Voice-first agricultural subsidy finder for science-fair kiosk.

Pipeline: Mic → Sarvam STT → Gemini 2.0 Flash-Lite (Tavily + Firecrawl tools) → Metric cards → Sarvam TTS
"""

from __future__ import annotations

import base64
import html
import io
import json
import re
from typing import Any

import qrcode
import streamlit as st
from google.genai import types
from agents.conversation import GEMINI_MODEL, _localized_fallback, run_conversation
from models.conversation import ConversationState
from services.recorder import autonomous_recorder
from services.sarvam import text_to_speech, transcribe

# ---------------------------------------------------------------------------
# Page config — wide desktop / kiosk layout
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Grameen Seva AI Hub",
    page_icon="🌾",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Session state defaults
for key, default in {
    "transcript": "",
    "voice_response": "",
    "scheme_name": "",
    "equipment": "",
    "subsidy_percent": 0,
    "max_claim_inr": 0,
    "missing_criteria": "",
    "card_status": "idle",
    "tts_audio_bytes": None,
    "last_audio_hash": None,
    "replay_counter": 0,
    "tts_token": 0,
    "last_component_event_id": None,
    "conversation": ConversationState(),
}.items():
    st.session_state.setdefault(key, default)

# ---------------------------------------------------------------------------
# Custom CSS — high-contrast kiosk styling (readable from ~5 feet)
# ---------------------------------------------------------------------------

st.markdown(
    """
<style>
    .stApp {
        background: #FCF9F8;
        color: #1B1B1B;
        font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .stApp::after {
        content: "";
        position: fixed;
        z-index: 998;
        left: 0;
        right: 0;
        bottom: 0;
        height: 250px;
        pointer-events: none;
        background: linear-gradient(to top, #FCF9F8 25%, rgba(252,249,248,0.92) 58%, rgba(252,249,248,0));
    }
    section[data-testid="stMain"] > div {
        max-width: 800px;
        margin: 0 auto;
    }
    .block-container {
        max-width: 800px !important;
        padding: 2.5rem 1.5rem 19rem !important;
    }
    .main-header {
        text-align: center;
        color: #0D631B;
        font-family: Montserrat, Inter, sans-serif;
        font-size: 2rem;
        font-weight: 900;
        letter-spacing: 0.5px;
        margin: 0.25rem 0 0.35rem 0;
        line-height: 1.15;
    }
    .brand-mark {
        width: 58px;
        height: 58px;
        display: grid;
        place-items: center;
        margin: 0.8rem auto 0;
        border-radius: 50%;
        background: #E8F5E9;
        border: 2px solid #A5D6A7;
        box-shadow: 0 8px 20px rgba(46, 125, 50, 0.12);
        font-size: 2rem;
    }
    .main-subtitle {
        text-align: center;
        color: #2E7D32;
        font-size: 1.35rem;
        font-weight: 600;
        margin-bottom: 2.25rem;
        font-family: Inter, sans-serif;
        font-size: 1.05rem;
    }
    .language-badge {
        width: fit-content;
        margin: 0 auto 0.6rem;
        padding: 0.35rem 0.8rem;
        border-radius: 999px;
        background: #E8F5E9;
        border: 1px solid #A5D6A7;
        color: #1B5E20;
        font-size: 0.9rem;
        font-weight: 800;
    }
    .panel-title {
        color: #2E7D32;
        font-size: 1.8rem;
        font-weight: 800;
        border-bottom: 4px solid #2E7D32;
        padding-bottom: 0.5rem;
        margin-bottom: 1.25rem;
    }
    .control-box {
        background: #FFFFFF;
        border: 2px solid #A5D6A7;
        border-radius: 16px;
        padding: 1.5rem;
        margin-bottom: 1rem;
        box-shadow: 0 6px 18px rgba(46, 125, 50, 0.12);
    }
    .mic-heading {
        text-align: center;
        color: #1B5E20;
        font-size: 1.6rem;
        font-weight: 800;
        margin: 1rem 0 0.75rem;
    }
    .mic-help {
        text-align: center;
        position: fixed;
        z-index: 1001;
        left: 50%;
        bottom: 14rem;
        transform: translateX(-50%);
        color: #5B4300;
        background: #FABD00;
        border-radius: 999px;
        padding: 0.45rem 1rem;
        font-size: 0.88rem;
        font-weight: 800;
        box-shadow: 0 8px 20px rgba(250, 189, 0, 0.22);
        white-space: nowrap;
    }
    .mic-stage {
        position: relative;
        width: 230px;
        height: 230px;
        margin: 1.25rem auto 0.5rem;
        display: grid;
        place-items: center;
    }
    .mic-stage::before,
    .mic-stage::after {
        content: "";
        position: absolute;
        inset: 8px;
        border: 3px solid rgba(46, 125, 50, 0.28);
        border-radius: 50%;
        animation: mic-pulse 2.2s ease-out infinite;
    }
    .mic-stage::after {
        animation-delay: 1.1s;
    }
    .mic-orb {
        position: relative;
        z-index: 1;
        width: 150px;
        height: 150px;
        display: grid;
        place-items: center;
        border-radius: 50%;
        color: #FFFFFF;
        background: radial-gradient(circle at 35% 25%, #66BB6A, #2E7D32 68%, #1B5E20);
        box-shadow: 0 12px 30px rgba(46, 125, 50, 0.32), 0 0 0 12px rgba(165, 214, 167, 0.34);
        animation: mic-breathe 1.8s ease-in-out infinite;
        font-size: 4.5rem;
        line-height: 1;
    }
    .mic-orb span {
        transform: translateY(-2px);
        filter: drop-shadow(0 2px 2px rgba(0,0,0,0.18));
    }
    @keyframes mic-pulse {
        0% { transform: scale(0.72); opacity: 0.85; }
        75%, 100% { transform: scale(1.08); opacity: 0; }
    }
    @keyframes mic-breathe {
        0%, 100% { transform: scale(0.98); }
        50% { transform: scale(1.03); }
    }
    div[data-testid="stCustomComponentV1"] {
        max-width: 260px;
        min-height: 58px;
        margin: -2.5rem auto 1rem;
        position: relative;
        z-index: 2;
        text-align: center;
    }
    .status-message {
        max-width: 30rem;
        margin: 0.75rem auto;
        padding: 0.9rem 1rem;
        border-radius: 12px;
        background: #FFFDE7;
        border: 2px solid #F9A825;
        color: #5D4037;
        text-align: center;
        font-size: 1.15rem;
        font-weight: 700;
    }
    div[data-testid="stAudioInput"] {
        max-width: 30rem;
        margin: 0 auto;
        padding: 1rem 1.25rem 0.75rem;
        background: transparent;
        border: 0;
    }
    div[data-testid="stAudioInput"] button {
        min-height: 2.75rem !important;
        border-radius: 0.75rem !important;
        background: #FFFFFF !important;
        color: #1B5E20 !important;
        border: 2px solid #A5D6A7 !important;
        box-shadow: none !important;
        font-size: 1rem !important;
    }
    div[data-testid="stAudioInput"] button:first-of-type {
        width: 100% !important;
        min-height: 5.5rem !important;
        border-radius: 1.25rem !important;
        background: #2E7D32 !important;
        color: #FFFFFF !important;
        border: 0 !important;
        box-shadow: 0 8px 20px rgba(46, 125, 50, 0.3) !important;
        font-size: 1.35rem !important;
        font-weight: 800 !important;
    }
    div[data-testid="stAudioInput"] button:hover {
        background: #1B5E20 !important;
    }
    div[data-testid="stAudioInput"] button:not(:first-of-type) {
        display: inline-flex !important;
        width: 3rem !important;
        min-height: 2.5rem !important;
        margin: 0.5rem 0.25rem 0;
        padding: 0.4rem !important;
    }
    /* Keep Streamlit's recorder lifecycle intact; only make the primary control compact. */
    div[data-testid="stAudioInput"] button:first-of-type {
        width: auto !important;
        min-height: 2.75rem !important;
        border-radius: 0.75rem !important;
        box-shadow: none !important;
        font-size: 1rem !important;
    }
    div[data-testid="stAudioInput"] label {
        display: block !important;
        text-align: center;
        font-size: 1.1rem !important;
        font-weight: 800 !important;
        color: #1B5E20 !important;
        margin-bottom: 0.5rem;
    }
    .chat-shell {
        max-width: 760px;
        max-height: 48vh;
        overflow-y: auto;
        display: flex;
        flex-direction: column-reverse;
        gap: 0.2rem;
        margin: 0 auto 2rem;
        padding: 0.25rem 0.5rem;
        scroll-behavior: smooth;
    }
    .chat-bubble {
        padding: 1rem 1.25rem;
        border-radius: 1.5rem;
        margin: 0.45rem 0;
        font-size: 1.05rem;
        line-height: 1.55;
        white-space: pre-wrap;
        box-shadow: 0 8px 22px rgba(46, 125, 50, 0.08);
        animation: chat-in 0.35s ease-out both;
    }
    @keyframes chat-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
    .chat-bubble.farmer {
        margin-left: 18%;
        background: #81C784;
        border: 0;
        color: #FFFFFF;
        border-top-right-radius: 0.35rem;
    }
    .chat-bubble.assistant {
        margin-right: 10%;
        background: rgba(255, 255, 255, 0.94);
        border: 1px solid rgba(191, 202, 186, 0.65);
        color: #1B1B1B;
        border-top-left-radius: 0.35rem;
        box-shadow: 0 10px 30px rgba(46, 125, 50, 0.08);
    }
    .chat-label {
        display: block;
        font-size: 0.9rem;
        font-weight: 800;
        margin-bottom: 0.25rem;
        opacity: 0.8;
    }
    .documents-box {
        background: #FFFFFF;
        border: 2px solid #A5D6A7;
        border-radius: 16px;
        padding: 1.25rem 1.5rem;
        margin-top: 1.25rem;
        font-size: 1.2rem;
        color: #1B5E20;
    }
    .transcript-box {
        background: #E3F2FD;
        border-left: 8px solid #1565C0;
        border-radius: 12px;
        padding: 1.25rem 1.5rem;
        font-size: 1.45rem;
        font-weight: 600;
        color: #0D47A1;
        line-height: 1.5;
        margin-bottom: 1.5rem;
    }
    .metric-grid {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 1.25rem;
        margin: 1.25rem 0;
        padding: 1.25rem;
        background: rgba(255, 255, 255, 0.94);
        border: 1px solid rgba(191, 202, 186, 0.65);
        border-radius: 1.5rem;
        box-shadow: 0 15px 40px rgba(46, 125, 50, 0.12);
    }
    .metric-card {
        background: #FFFFFF;
        border-radius: 16px;
        padding: 1.5rem;
        text-align: center;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
        border: 1px solid #D6E6D3;
        border-top: 5px solid #2E7D32;
    }
    .metric-card.warning {
        border-top-color: #F9A825;
        background: #FFFDE7;
    }
    .metric-label {
        font-size: 1.1rem;
        font-weight: 700;
        color: #546E7A;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 0.5rem;
    }
    .metric-value {
        font-size: 2.6rem;
        font-weight: 900;
        color: #1B5E20;
        line-height: 1.15;
    }
    .metric-value.highlight {
        font-size: 3.2rem;
        color: #2E7D32;
    }
    .scheme-banner {
        background: #FFFFFF;
        border: 2px solid rgba(13, 99, 27, 0.18);
        color: white;
        color: #0D631B;
        border-radius: 1.5rem;
        padding: 1.25rem 1.5rem;
        font-size: 1.6rem;
        font-weight: 800;
        margin-bottom: 1.25rem;
        text-align: left;
        box-shadow: 0 10px 30px rgba(46, 125, 50, 0.08);
    }
    .verified-badge {
        float: right;
        background: #FABD00;
        color: #5B4300;
        border-radius: 999px;
        padding: 0.3rem 0.6rem;
        font-size: 0.72rem;
        font-weight: 800;
    }
    .panel-title {
        color: #0D631B;
        font-family: Montserrat, Inter, sans-serif;
        font-size: 1.35rem;
        border: 0;
        text-align: center;
    }
    div[data-testid="stCustomComponentV1"] {
        position: fixed !important;
        z-index: 1000 !important;
        left: 50% !important;
        bottom: 0.25rem !important;
        transform: translateX(-50%) !important;
        width: 240px !important;
        max-width: 240px !important;
        height: 235px !important;
        min-height: 235px !important;
        margin: 0 !important;
        padding-top: 0.5rem;
        border-radius: 2rem 2rem 0 0;
        background: linear-gradient(to top, rgba(252,249,248,1) 44%, rgba(252,249,248,0));
    }
    .documents-box {
        background: rgba(255, 255, 255, 0.94);
        border: 1px solid rgba(191, 202, 186, 0.65);
        border-radius: 1.5rem;
        box-shadow: 0 10px 30px rgba(46, 125, 50, 0.08);
    }
    .missing-banner {
        background: #FFF3E0;
        border-left: 8px solid #EF6C00;
        border-radius: 12px;
        padding: 1rem 1.25rem;
        font-size: 1.25rem;
        font-weight: 700;
        color: #E65100;
        margin-top: 1rem;
    }
    div[data-testid="stSelectbox"] label,
    div[data-testid="column"] label {
        font-size: 1.1rem !important;
        font-weight: 600 !important;
    }
    #MainMenu, footer { visibility: hidden; }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INDIAN_STATES = [
    "Telangana",
    "Andhra Pradesh",
    "Karnataka",
    "Tamil Nadu",
    "Maharashtra",
    "Gujarat",
    "Rajasthan",
    "Punjab",
    "Uttar Pradesh",
    "Bihar",
    "West Bengal",
    "Kerala",
    "Madhya Pradesh",
    "Odisha",
]

FARMER_CATEGORIES = [
    "Small/Marginal Farmer",
    "General Farmer",
    "SC/ST Farmer",
]

LANGUAGE_OPTIONS = {
    "Hindi (hi-IN)": "hi-IN",
    "Telugu (te-IN)": "te-IN",
    "Tamil (ta-IN)": "ta-IN",
}

SYSTEM_PROMPT = """You are Grameen Seva AI Hub, an expert assistant helping Indian farmers
find government agricultural subsidies and schemes.

Use your tools to search myscheme.gov.in and gov.in, then read promising pages for details.

Extract:
- equipment_or_input: what the farmer needs (e.g., drip kit, tractor, seeds)
- scheme_name: official scheme name
- subsidy_percent: numeric percentage (0 if unknown)
- max_claim_inr: maximum claimable amount in INR as integer (0 if unknown)
- missing_criteria: ONE missing detail blocking full eligibility, or null if complete
- voice_response: 3-5 sentence spoken summary in the farmer's selected language script

Respond with ONLY valid JSON (no markdown fences):
{
  "equipment_or_input": "...",
  "scheme_name": "...",
  "subsidy_percent": 60,
  "max_claim_inr": 120000,
  "missing_criteria": null,
  "voice_response": "...",
  "source_url": "https://..."
}

Be accurate. Only cite schemes found via tools. Never invent amounts or scheme names.
"""


# ---------------------------------------------------------------------------
# Secrets & cached clients
# ---------------------------------------------------------------------------


def get_secret(name: str) -> str | None:
    try:
        return st.secrets[name]
    except (KeyError, FileNotFoundError, TypeError):
        return None


def missing_secrets() -> list[str]:
    return [k for k in ("SARVAM_API_KEY", "GEMINI_API_KEY", "TAVILY_API_KEY", "FIRECRAWL_API_KEY") if not get_secret(k)]


@st.cache_resource
def sarvam_client(api_key: str):
    from sarvamai import SarvamAI

    return SarvamAI(api_subscription_key=api_key)


@st.cache_resource
def gemini_client(api_key: str):
    # Legacy compatibility wrapper. The active agent owns the single cached
    # Gemini client; this function never constructs another one.
    from agents.conversation import _gemini_client

    return _gemini_client(api_key)


@st.cache_resource
def tavily_client(api_key: str):
    from tavily import TavilyClient

    return TavilyClient(api_key=api_key)


@st.cache_resource
def firecrawl_client(api_key: str):
    import firecrawl

    client_class = getattr(firecrawl, "Firecrawl", None) or getattr(firecrawl, "FirecrawlApp", None)
    if client_class is None:
        raise RuntimeError("Installed firecrawl-py does not expose a supported client")
    return client_class(api_key=api_key)


# ---------------------------------------------------------------------------
# Sarvam STT & TTS
# ---------------------------------------------------------------------------


def legacy_transcribe(audio_bytes: bytes, language_code: str) -> str:
    """Send WAV bytes from st.audio_input to Sarvam saaras:v3."""
    client = sarvam_client(get_secret("SARVAM_API_KEY"))
    buffer = io.BytesIO(audio_bytes)
    result = client.speech_to_text.transcribe(
        file=buffer,
        model="saaras:v3",
        language_code=language_code,
    )
    return (result.transcript or "").strip()


def legacy_text_to_speech(text: str, language_code: str) -> bytes:
    """Convert agent summary to WAV bytes via Sarvam bulbul:v3."""
    client = sarvam_client(get_secret("SARVAM_API_KEY"))
    spoken = text[:2500] if len(text) > 2500 else text
    result = client.text_to_speech.convert(
        text=spoken,
        target_language_code=language_code,
        model="bulbul:v3",
        speaker="shubh",
    )
    raw = result.audios[0]
    return base64.b64decode(raw) if isinstance(raw, str) else raw


# ---------------------------------------------------------------------------
# Agent tools
# ---------------------------------------------------------------------------


def search_schemes(query: str, state: str) -> str:
    """Search government subsidy schemes via Tavily (myscheme.gov.in / gov.in)."""
    client = tavily_client(get_secret("TAVILY_API_KEY"))
    scoped = f"{query} {state} agricultural subsidy site:myscheme.gov.in OR site:gov.in"
    response = client.search(
        query=scoped,
        search_depth="advanced",
        max_results=5,
        include_domains=["myscheme.gov.in", "gov.in"],
    )
    hits = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", "")[:1200],
            "score": r.get("score", 0),
        }
        for r in response.get("results", [])
    ]
    return json.dumps({"query": scoped, "results": hits}, ensure_ascii=False)


def get_scheme_details(url: str) -> str:
    """Scrape full scheme page content via Firecrawl."""
    client = firecrawl_client(get_secret("FIRECRAWL_API_KEY"))
    doc = client.scrape(url, formats=["markdown"]) if hasattr(client, "scrape") else client.scrape_url(
        url, params={"formats": ["markdown"]}
    )
    md = doc.markdown if hasattr(doc, "markdown") else doc.get("markdown", "")
    return (md[:8000] + "\n[truncated]") if len(md) > 8000 else md


# ---------------------------------------------------------------------------
# Gemini agent with manual function calling
# ---------------------------------------------------------------------------


def _tools() -> list[types.Tool]:
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="search_schemes",
                    description="Search Indian govt subsidy schemes on myscheme.gov.in and gov.in.",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "query": types.Schema(type="STRING", description="Farmer need or product"),
                            "state": types.Schema(type="STRING", description="Indian state"),
                        },
                        required=["query", "state"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="get_scheme_details",
                    description="Read full markdown content from a scheme webpage URL.",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={"url": types.Schema(type="STRING", description="Scheme page URL")},
                        required=["url"],
                    ),
                ),
            ]
        )
    ]


def _run_tool(name: str, args: dict[str, Any], state: str) -> str:
    if name == "search_schemes":
        return search_schemes(args.get("query", ""), args.get("state", state))
    if name == "get_scheme_details":
        return get_scheme_details(args.get("url", ""))
    return json.dumps({"error": f"Unknown tool: {name}"})


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {
        "equipment_or_input": "",
        "scheme_name": "Information Pending",
        "subsidy_percent": 0,
        "max_claim_inr": 0,
        "missing_criteria": "Could not parse agent response",
        "voice_response": text[:400] if text else "क्षमा करें, जानकारी प्राप्त नहीं हो सकी।",
        "source_url": "",
    }


def run_agent(transcript: str, state: str, category: str, language_code: str) -> dict[str, Any]:
    """Legacy Gemini 2.0 Flash-Lite agent loop retained for compatibility."""
    client = gemini_client(get_secret("GEMINI_API_KEY"))
    user_msg = (
        f"Farmer said: {transcript}\n"
        f"State: {state}\nCategory: {category}\nLanguage: {language_code}\n"
        "Search schemes, calculate subsidy, return JSON."
    )
    contents: list[types.Content] = [types.Content(role="user", parts=[types.Part(text=user_msg)])]

    for _ in range(8):
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=_tools(),
            ),
        )
        candidate = response.candidates[0]
        parts = candidate.content.parts if candidate.content else []
        calls = [p.function_call for p in parts if p.function_call]

        if not calls:
            return _parse_json(response.text or "")

        contents.append(candidate.content)
        tool_parts = []
        for call in calls:
            tool_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=call.name,
                        response={"result": _run_tool(call.name, dict(call.args or {}), state)},
                    )
                )
            )
        contents.append(types.Content(role="user", parts=tool_parts))

    return _parse_json("Agent reached maximum search steps.")


# ---------------------------------------------------------------------------
# Formatting & UI helpers
# ---------------------------------------------------------------------------


def format_inr(amount: int | float) -> str:
    n = int(amount)
    if n <= 0:
        return "—"
    s = str(n)
    if len(s) <= 3:
        return f"₹{s}"
    last3 = s[-3:]
    rest = s[:-3]
    parts = []
    while len(rest) > 2:
        parts.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        parts.insert(0, rest)
    return f"₹{','.join(parts + [last3])}"


def automatic_recording() -> bytes | None:
    """Backward-compatible no-op; active UI uses autonomous_recorder."""
    return None


def process_recording(audio_bytes: bytes) -> bool:
    conversation: ConversationState = st.session_state.conversation
    conversation.set_state("PROCESSING")
    work = st.status("Working on your request…", expanded=True)
    work.write("✅ Recording received")
    work.write("🎧 Converting your voice into text…")
    with st.spinner("Listening…"):
        try:
            transcript, detected_language = transcribe(audio_bytes, get_secret("SARVAM_API_KEY"))
        except Exception as exc:
            st.session_state.card_status = "error"
            conversation.set_state("LISTENING")
            work.update(label="Speech recognition failed", state="error", expanded=True)
            st.error(f"Speech recognition failed: {exc}")
            return False
        st.session_state.transcript = transcript

    if not transcript:
        st.session_state.card_status = "error"
        conversation.set_state("LISTENING")
        work.update(label="I could not hear the recording", state="error", expanded=True)
        st.error("I could not hear the recording. Please speak closer to the microphone and try again.")
        return False

    conversation.transcript = transcript
    conversation.set_state("THINKING")
    if detected_language:
        conversation.language_code = detected_language
    conversation.add_turn("farmer", transcript)

    work.write("🧠 Understanding what you need and checking which detail is missing…")
    with st.spinner("Understanding your request…"):
        try:
            result = run_conversation(
                conversation,
                get_secret("GEMINI_API_KEY"),
                get_secret("TAVILY_API_KEY"),
                get_secret("FIRECRAWL_API_KEY"),
            )
        except Exception as exc:
            st.session_state.card_status = "error"
            conversation.set_state("LISTENING")
            work.update(label="The assistant could not process the request", state="error", expanded=True)
            st.error(f"Assistant request failed: {exc}")
            return False
    conversation.result = result
    if result.goodbye_detected:
        result.conversation_complete = True
    if result.conversation_complete and not conversation.eligibility_status:
        conversation.eligibility_status = "complete"
    # The text shown and the text spoken must always be identical.
    spoken_response = (result.voice_response or "").strip()
    if not spoken_response:
        spoken_response = _localized_fallback(conversation.language_code, "prompt")
    previous_assistant_messages = {
        turn["text"] for turn in conversation.turns if turn["role"] == "assistant"
    }
    if spoken_response in previous_assistant_messages:
        spoken_response = _localized_fallback(conversation.language_code, "repeat")
    result.voice_response = spoken_response
    result.next_question = ""
    conversation.add_turn("assistant", spoken_response)

    st.session_state.equipment = result.equipment_or_input
    st.session_state.scheme_name = result.scheme_name or ""
    st.session_state.subsidy_percent = result.subsidy_percent
    st.session_state.max_claim_inr = result.max_claim_inr
    st.session_state.missing_criteria = ", ".join(result.missing_criteria)
    st.session_state.voice_response = spoken_response
    st.session_state.card_status = "success" if result.conversation_complete else "warning"
    conversation.set_state("SPEAKING")

    if result.conversation_complete:
        work.write("🔎 Enough information collected. Searching official government sources…")
        work.write("📄 Reading official scheme details and preparing the answer…")
    else:
        work.write("❓ I need one more important detail before searching government schemes…")
    work.write("🔊 Preparing the spoken reply in your language…")

    with st.spinner("Generating voice response…"):
        try:
            st.session_state.tts_audio_bytes = text_to_speech(
                st.session_state.voice_response,
                conversation.language_code or "hi-IN",
                get_secret("SARVAM_API_KEY"),
            )
            st.session_state.tts_token += 1
        except Exception as exc:
            st.session_state.tts_audio_bytes = None
            conversation.set_state("LISTENING")
            work.update(label="Reply text is ready, but voice playback failed", state="error", expanded=True)
            st.error(f"TTS failed: {exc}")
            return False

    conversation.set_state("DISPLAY_RESULTS" if result.conversation_complete else "LISTENING")
    work.update(label="Reply ready — see the conversation below", state="complete", expanded=False)
    return True


def render_metrics() -> None:
    result = st.session_state.conversation.result
    if not result.conversation_complete:
        return

    status = st.session_state.card_status
    # Unknown values are omitted instead of displayed as empty dashboard data.
    if st.session_state.scheme_name:
        st.markdown(
            f'<div class="scheme-banner"><span class="verified-badge">✓ Verified Source</span>{html.escape(st.session_state.scheme_name)}</div>',
            unsafe_allow_html=True,
        )
    cards = []
    card_class = "metric-card warning" if status == "warning" else "metric-card"
    if result.subsidy_percent > 0:
        cards.append(f'<div class="{card_class}"><div class="metric-label">Subsidy Percentage</div><div class="metric-value">{result.subsidy_percent}%</div></div>')
    if result.max_claim_inr > 0:
        cards.append(f'<div class="{card_class}"><div class="metric-label">Maximum Subsidy</div><div class="metric-value highlight">{format_inr(result.max_claim_inr)}</div></div>')
    if st.session_state.equipment:
        cards.append(f'<div class="metric-card"><div class="metric-label">Equipment / Input</div><div class="metric-value" style="font-size:1.6rem;">{html.escape(st.session_state.equipment)}</div></div>')
    if st.session_state.scheme_name:
        cards.append(f'<div class="metric-card"><div class="metric-label">Eligible Scheme</div><div class="metric-value" style="font-size:1.5rem;">{html.escape(st.session_state.scheme_name)}</div></div>')
    eligibility = st.session_state.conversation.eligibility_status
    if eligibility:
        cards.append(f'<div class="metric-card"><div class="metric-label">Eligibility</div><div class="metric-value" style="font-size:1.5rem;">{html.escape(eligibility.title())}</div></div>')
    source_url = result.source_url or st.session_state.conversation.researched_url
    if source_url:
        safe_url = html.escape(source_url, quote=True)
        cards.append(f'<div class="metric-card"><div class="metric-label">Official Government Source</div><div class="metric-value" style="font-size:1rem;"><a href="{safe_url}" target="_blank" rel="noopener">Open official source</a></div></div>')
    if cards:
        st.markdown(f'<div class="metric-grid">{"".join(cards)}</div>', unsafe_allow_html=True)
    documents = result.required_documents
    if documents:
        items = "".join(f"<li>{html.escape(document)}</li>" for document in documents)
        st.markdown(
            f'<div class="documents-box"><strong>Required documents</strong><ul>{items}</ul></div>',
            unsafe_allow_html=True,
        )
    return
    if st.session_state.scheme_name:
        st.markdown(
            f'<div class="scheme-banner">📋 {st.session_state.scheme_name}</div>',
            unsafe_allow_html=True,
        )

    warn = status == "warning"
    pct = st.session_state.subsidy_percent
    claim = st.session_state.max_claim_inr

    st.markdown(
        f"""
<div class="metric-grid">
  <div class="metric-card{' warning' if warn else ''}">
    <div class="metric-label">Subsidy Percentage</div>
    <div class="metric-value">{pct}%</div>
  </div>
  <div class="metric-card{' warning' if warn else ''}">
    <div class="metric-label">Maximum Claimable Amount</div>
    <div class="metric-value highlight">{format_inr(claim)}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Equipment / Input</div>
    <div class="metric-value" style="font-size:1.6rem;">{st.session_state.equipment or '—'}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Eligible Scheme</div>
    <div class="metric-value" style="font-size:1.5rem;">{st.session_state.scheme_name or '—'}</div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    if st.session_state.missing_criteria:
        st.markdown(
            f'<div class="missing-banner">⚠️ Missing: {st.session_state.missing_criteria}</div>',
            unsafe_allow_html=True,
        )

    documents = result.required_documents
    if documents:
        items = "".join(f"<li>{html.escape(document)}</li>" for document in documents)
        st.markdown(
            f'<div class="documents-box"><strong>Required documents</strong><ul>{items}</ul></div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.markdown('<div class="brand-mark" aria-label="Agriculture logo">🌾</div>', unsafe_allow_html=True)
st.markdown('<h1 class="main-header">Grameen Seva AI Hub</h1>', unsafe_allow_html=True)
st.markdown(
    '<p class="main-subtitle">Voice-First Government Subsidy Finder for Indian Farmers</p>',
    unsafe_allow_html=True,
)

language_names = {
    "hi": "हिन्दी",
    "te": "తెలుగు",
    "ta": "தமிழ்",
    "kn": "ಕನ್ನಡ",
    "mr": "मराठी",
    "bn": "বাংলা",
    "gu": "ગુજરાતી",
    "pa": "ਪੰਜਾਬੀ",
}
detected_code = (st.session_state.conversation.language_code or "").lower()
detected_name = next((name for code, name in language_names.items() if detected_code.startswith(code)), "")
if detected_name:
    st.markdown(
        f'<div class="language-badge">🟢 Language detected: {html.escape(detected_name)}</div>',
        unsafe_allow_html=True,
    )

missing = missing_secrets()
if missing:
    st.error(f"Missing API keys in secrets.toml: {', '.join(missing)}")
    st.stop()

# ---------------------------------------------------------------------------
# Conversation-first kiosk home screen
# ---------------------------------------------------------------------------

conversation: ConversationState = st.session_state.conversation
state_labels = {
    "IDLE": "🎙️ Tap once to begin",
    "LISTENING": "🎤 Listening",
    "PROCESSING": "📝 Understanding",
    "THINKING": "🤖 Thinking",
    "SPEAKING": "🔊 Speaking",
    "SEARCHING": "🔍 Searching official schemes",
    "DISPLAY_RESULTS": "📄 Preparing response",
    "COMPLETED": "✅ Conversation complete",
}
st.markdown(
    f'<div class="mic-help">{state_labels.get(conversation.state, conversation.state)}</div>',
    unsafe_allow_html=True,
)

mic_col_left, mic_col_center, mic_col_right = st.columns([1, 2, 1])
with mic_col_center:
    audio = autonomous_recorder(
        active=conversation.state != "COMPLETED",
        auto_start=conversation.listening_started and conversation.state == "LISTENING",
        tts_audio=(st.session_state.tts_audio_bytes if conversation.state != "COMPLETED" else None),
        tts_token=st.session_state.tts_token,
        resume_after_tts=not conversation.result.conversation_complete,
    )

if not conversation.turns:
    st.markdown(
        '<div class="status-message">🎙️ Tap once and speak. I will listen, understand, and reply automatically.</div>',
        unsafe_allow_html=True,
    )
if conversation.turns:
    st.markdown('<div class="chat-shell">', unsafe_allow_html=True)
    for turn in reversed(conversation.turns):
        role = "farmer" if turn["role"] == "farmer" else "assistant"
        label = "You" if role == "farmer" else "Grameen Seva AI"
        text = html.escape(turn["text"])
        st.markdown(
            f'<div class="chat-bubble {role}"><span class="chat-label">{label}</span>{text}</div>',
            unsafe_allow_html=True,
        )
    st.markdown('</div>', unsafe_allow_html=True)

if conversation.result.conversation_complete:
    st.markdown('<div class="panel-title">📊 Your official scheme result</div>', unsafe_allow_html=True)
    render_metrics()
    if st.session_state.tts_audio_bytes:
        st.markdown("##### 🔊 Listen to the answer")
        if st.button("🔊 Replay answer", use_container_width=True):
            st.session_state.replay_counter += 1
            st.audio(
                st.session_state.tts_audio_bytes,
                format="audio/wav",
                autoplay=True,
                key=f"replay_{st.session_state.replay_counter}",
            )

if isinstance(audio, dict):
    event = audio.get("event")
    event_id = audio.get("id")
    fresh_event = event_id != st.session_state.last_component_event_id
    if fresh_event and event_id is not None:
        st.session_state.last_component_event_id = event_id
    if not fresh_event:
        event = None
    if event == "listening":
        conversation.listening_started = True
        conversation.set_state("LISTENING")
    if event == "completed":
        conversation.set_state("COMPLETED")
        st.rerun()
    if event == "error":
        st.error("Microphone permission is required to start the conversation.")
        conversation.set_state("IDLE")
        conversation.listening_started = False
        st.stop()
    audio_payload = audio.get("audio", "")
    audio_bytes = base64.b64decode(audio_payload) if audio_payload else None
elif audio is not None:
    audio_bytes = bytes(audio) if isinstance(audio, (bytes, bytearray)) else audio.getvalue()
else:
    audio_bytes = None

if audio_bytes:
    audio_hash = hash(audio_bytes)
    if audio_hash != st.session_state.last_audio_hash:
        if process_recording(audio_bytes):
            st.session_state.last_audio_hash = audio_hash
            st.rerun()

# The legacy layout below is retained in source only while this migration is staged.
# It is unreachable so no old controls or duplicate dashboard are rendered.
st.stop()

# Two-column kiosk layout: 35% control | 65% dashboard
# ---------------------------------------------------------------------------

col_left, col_right = st.columns([0.35, 0.65], gap="large")

with col_left:
    st.markdown('<p class="panel-title">🎙️ Voice Control Center</p>', unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown(
            "<p style='font-size:1.25rem;color:#2E7D32;font-weight:700;'>"
            "Just speak naturally. I will detect your language and ask one question at a time.</p>",
            unsafe_allow_html=True,
        )

    st.markdown('<div class="control-box">', unsafe_allow_html=True)
    audio = st.audio_input(
        "Tap Mic & Speak / बोलने के लिए दबाएं",
        key="kiosk_mic",
    )
    st.markdown("</div>", unsafe_allow_html=True)

    # Optional QR for judges to open on mobile (uses deploy URL if set)
    deploy_url = get_secret("DEPLOY_URL") or "https://share.streamlit.io"
    qr = qrcode.make(deploy_url)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    st.caption("Scan to open on your phone")
    st.image(buf.getvalue(), width=140)

with col_right:
    st.markdown('<p class="panel-title">📊 Subsidy Intelligence Dashboard</p>', unsafe_allow_html=True)

    if st.session_state.transcript:
        st.markdown(
            f'<div class="transcript-box">🗣️ <strong>You said:</strong> {st.session_state.transcript}</div>',
            unsafe_allow_html=True,
        )

    conversation: ConversationState = st.session_state.conversation
    for turn in conversation.turns:
        if turn["role"] == "assistant" and turn["text"]:
            st.info(f"🤖 {turn['text']}")

    render_metrics()

    if st.session_state.tts_audio_bytes:
        st.markdown("##### 🔊 AI Voice Response")
        st.audio(st.session_state.tts_audio_bytes, format="audio/wav", autoplay=True)

        if st.button("🔊 Listen Again (फिर से सुनें)", use_container_width=True):
            st.session_state.replay_counter += 1
            st.audio(
                st.session_state.tts_audio_bytes,
                format="audio/wav",
                autoplay=True,
                key=f"replay_{st.session_state.replay_counter}",
            )

# Process new audio outside columns to avoid duplicate reruns
if audio is not None:
    audio_bytes = bytes(audio) if isinstance(audio, (bytes, bytearray)) else audio.getvalue()
    audio_hash = hash(audio_bytes)
    if audio_hash != st.session_state.last_audio_hash:
        st.session_state.last_audio_hash = audio_hash
        process_recording(audio_bytes)
        st.rerun()
