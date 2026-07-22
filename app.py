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
    "last_played_tts_token": -1,
    "error_message": None,
}.items():
    st.session_state.setdefault(key, default)

# ---------------------------------------------------------------------------
# Custom CSS — high-contrast kiosk styling (readable from ~5 feet)
# ---------------------------------------------------------------------------

st.markdown(
    """
    <script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
    <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet">
    <script id="tailwind-config">
        tailwind.config = {
            darkMode: "class",
            theme: {
                extend: {
                    colors: {
                        "on-surface-variant": "#40493d",
                        "on-secondary-fixed-variant": "#07521d",
                        "on-primary-container": "#cbffc2",
                        "tertiary-container": "#8c6800",
                        "surface-container-low": "#f6f3f2",
                        "primary": "#0d631b",
                        "primary-fixed-dim": "#88d982",
                        "inverse-primary": "#88d982",
                        "tertiary-fixed-dim": "#fabd00",
                        "on-error": "#ffffff",
                        "on-primary": "#ffffff",
                        "secondary": "#286b33",
                        "on-primary-fixed": "#002204",
                        "secondary-container": "#abf4ac",
                        "on-surface": "#1b1b1b",
                        "on-tertiary-container": "#ffefd6",
                        "inverse-on-surface": "#f3f0ef",
                        "surface-container-highest": "#e5e2e1",
                        "on-secondary-container": "#2e7238",
                        "on-tertiary": "#ffffff",
                        "on-background": "#1b1b1b",
                        "inverse-surface": "#313030",
                        "surface-variant": "#e5e2e1",
                        "outline-variant": "#bfcaba",
                        "primary-fixed": "#a3f69c",
                        "error-container": "#ffdad6",
                        "error": "#ba1a1a",
                        "on-primary-fixed-variant": "#005312",
                        "on-error-container": "#93000a",
                        "on-tertiary-fixed": "#261a00",
                        "surface-dim": "#dcd9d9",
                        "secondary-fixed-dim": "#90d792",
                        "on-secondary-fixed": "#002107",
                        "surface-container-lowest": "#ffffff",
                        "tertiary-fixed": "#ffdf9e",
                        "on-tertiary-fixed-variant": "#5b4300",
                        "surface-bright": "#fcf9f8",
                        "outline": "#707a6c",
                        "tertiary": "#6d5100",
                        "on-secondary": "#ffffff",
                        "surface-container-high": "#eae7e7",
                        "surface-tint": "#1b6d24",
                        "surface": "#fcf9f8",
                        "secondary-fixed": "#abf4ac",
                        "background": "#fcf9f8",
                        "primary-container": "#2e7d32",
                        "surface-container": "#f0eded"
                    },
                    borderRadius: {
                        "DEFAULT": "0.25rem",
                        "lg": "0.5rem",
                        "xl": "0.75rem",
                        "full": "9999px"
                    },
                    spacing: {
                        "section-gap": "40px",
                        "touch-target-min": "56px",
                        "base": "8px",
                        "container-max": "1200px",
                        "card-padding": "24px"
                    },
                    fontFamily: {
                        "button-text": ["Montserrat"],
                        "body-md": ["Inter"],
                        "label-lg": ["Inter"],
                        "display-lg": ["Montserrat"],
                        "body-lg": ["Inter"],
                        "headline-lg-mobile": ["Montserrat"],
                        "headline-lg": ["Montserrat"]
                    },
                    fontSize: {
                        "button-text": ["20px", { "lineHeight": "24px", "fontWeight": "700" }],
                        "body-md": ["18px", { "lineHeight": "28px", "fontWeight": "400" }],
                        "label-lg": ["16px", { "lineHeight": "24px", "letterSpacing": "0.05em", "fontWeight": "600" }],
                        "display-lg": ["48px", { "lineHeight": "56px", "letterSpacing": "-0.02em", "fontWeight": "700" }],
                        "body-lg": ["20px", { "lineHeight": "32px", "fontWeight": "400" }],
                        "headline-lg-mobile": ["28px", { "lineHeight": "36px", "fontWeight": "600" }],
                        "headline-lg": ["32px", { "lineHeight": "40px", "fontWeight": "600" }]
                    }
                }
            }
        }
    </script>
    <style>
        .material-symbols-outlined {
            font-variation-settings: 'FILL' 1;
        }
        .glass-panel {
            background: rgba(255, 255, 255, 0.9);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            box-shadow: 0px 10px 30px rgba(46, 125, 50, 0.08);
        }
        .glass-panel-heavy {
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            box-shadow: 0px 15px 40px rgba(46, 125, 50, 0.12);
        }
        .pulse-amber {
            animation: pulse-ring 2s cubic-bezier(0.215, 0.61, 0.355, 1) infinite;
        }
        @keyframes pulse-ring {
            0% { transform: scale(0.8); box-shadow: 0 0 0 0 rgba(250, 189, 0, 0.7); }
            70% { transform: scale(1); box-shadow: 0 0 0 20px rgba(250, 189, 0, 0); }
            100% { transform: scale(0.8); box-shadow: 0 0 0 0 rgba(250, 189, 0, 0); }
        }
        .fade-in-up {
            animation: fadeInUp 0.6s ease-out forwards;
            opacity: 0;
            transform: translateY(20px);
        }
        @keyframes fadeInUp {
            to { opacity: 1; transform: translateY(0); }
        }
        .chat-bubble-delay-1 { animation-delay: 0.2s; }
        .chat-bubble-delay-2 { animation-delay: 0.6s; }
        .chat-bubble-delay-3 { animation-delay: 1.0s; }
        
        #MainMenu, footer { visibility: hidden; }
        .stApp {
            background: #FCF9F8;
            color: #1B1B1B;
        }
        div[data-testid="stCustomComponentV1"] {
            position: fixed !important;
            z-index: 1000 !important;
            left: 50% !important;
            bottom: 0.25rem !important;
            transform: translateX(-50%) !important;
            width: 320px !important;
            max-width: 320px !important;
            height: 320px !important;
            min-height: 320px !important;
            margin: 0 !important;
            padding: 0 !important;
            background: transparent !important;
            border: none !important;
        }
        .block-container {
            max-width: 800px !important;
            padding-bottom: 24rem !important;
        }
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
    st.session_state.error_message = None
    conversation: ConversationState = st.session_state.conversation
    conversation.set_state("PROCESSING")
    work = st.status("Working on your request…", expanded=True)
    work.write("✅ Recording received")
    work.write("🎧 Converting your voice into text…")
    with st.spinner("Listening…"):
        try:
            transcript, detected_language = transcribe(audio_bytes, get_secret("SARVAM_API_KEY"))
        except Exception as exc:
            st.session_state.error_message = f"Speech recognition failed: {exc}"
            st.session_state.card_status = "error"
            conversation.set_state("LISTENING")
            work.update(label="Speech recognition failed", state="error", expanded=True)
            return False
        st.session_state.transcript = transcript

    if not transcript:
        st.session_state.error_message = "I could not hear the recording. Please speak closer to the microphone and try again."
        st.session_state.card_status = "error"
        conversation.set_state("LISTENING")
        work.update(label="I could not hear the recording", state="error", expanded=True)
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
            st.session_state.error_message = f"Assistant request failed: {exc}"
            st.session_state.card_status = "error"
            conversation.set_state("LISTENING")
            work.update(label="The assistant could not process the request", state="error", expanded=True)
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
            st.session_state.error_message = f"TTS failed: {exc}"
            conversation.set_state("LISTENING")
            work.update(label="Reply text is ready, but voice playback failed", state="error", expanded=True)
            return False

    conversation.set_state("COMPLETED" if result.conversation_complete else "LISTENING")
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
# Conversation-first kiosk home screen (Tailwind Premium Design)
# ---------------------------------------------------------------------------

conversation: ConversationState = st.session_state.conversation

# Render Header Section
st.markdown(
    """
    <header class="text-center mb-12 fade-in-up">
        <h1 class="font-display-lg text-[48px] font-bold text-[#0d631b] mb-4">Grameen Seva AI Hub</h1>
        <p class="font-body-lg text-[20px] text-[#1b1b1b] max-w-2xl mx-auto">
            I can help you with government schemes and agricultural advice.
        </p>
    </header>
    """,
    unsafe_allow_html=True,
)

# Render Language Badge if detected
detected_code = (conversation.language_code or "").lower()
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
detected_name = next((name for code, name in language_names.items() if detected_code.startswith(code)), "")
if detected_name:
    st.markdown(
        f"""
        <div class="flex justify-center mb-8 fade-in-up">
            <div class="bg-[#abf4ac] text-[#2e7238] px-4 py-1.5 rounded-full font-semibold text-[16px] flex items-center gap-2">
                <span class="material-symbols-outlined text-[18px]">language</span>
                Language detected: {html.escape(detected_name)}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# Render Chat History
chat_html = ""
for i, turn in enumerate(conversation.turns):
    role = "farmer" if turn["role"] == "farmer" else "assistant"
    text = html.escape(turn["text"])
    delay_class = f"chat-bubble-delay-{(i % 3) + 1}"
    if role == "farmer":
        chat_html += f"""
        <div class="flex justify-end w-full fade-in-up {delay_class} mb-4">
            <div class="bg-[#81C784] text-white rounded-3xl rounded-tr-sm px-6 py-4 max-w-[80%] shadow-md">
                <p class="font-body-lg text-[20px]">{text}</p>
            </div>
        </div>
        """
    else:
        chat_html += f"""
        <div class="flex justify-start w-full fade-in-up {delay_class} mb-4">
            <div class="glass-panel-heavy text-[#1b1b1b] rounded-3xl rounded-tl-sm px-6 py-4 max-w-[85%] shadow-md border border-[#bfcaba]/30">
                <div class="flex items-center gap-2 mb-2 text-[#0d631b] font-semibold text-[16px]">
                    <span class="material-symbols-outlined text-[18px]">smart_toy</span>
                    <span class="">Grameen AI</span>
                </div>
                <p class="font-body-lg text-[20px]">{text}</p>
            </div>
        </div>
        """

# Render Result Card if completed
result_html = ""
result = conversation.result
if result.conversation_complete:
    benefit_val = f"{format_inr(result.max_claim_inr)} / Year" if result.max_claim_inr > 0 else "—"
    subsidy_val = f"{result.subsidy_percent}%" if result.subsidy_percent > 0 else "—"
    equipment_val = result.equipment_or_input or "—"
    scheme_val = result.scheme_name or "—"
    
    docs_list = "".join(f"<li class='mb-1'>{html.escape(d)}</li>" for d in result.required_documents)
    docs_section = ""
    if docs_list:
        docs_section = f"""
        <div class="flex items-start gap-3">
            <span class="material-symbols-outlined text-[#8c6800] mt-1">description</span>
            <div>
                <h4 class="font-semibold text-[16px] text-[#1b1b1b]">Required Documents</h4>
                <ul class="list-disc pl-5 font-normal text-[18px] text-[#40493d]">{docs_list}</ul>
            </div>
        </div>
        """
        
    eligibility_section = ""
    if conversation.eligibility_status:
        eligibility_section = f"""
        <div class="flex items-start gap-3">
            <span class="material-symbols-outlined text-[#0d631b] mt-1">check_circle</span>
            <div>
                <h4 class="font-semibold text-[16px] text-[#1b1b1b]">Eligibility</h4>
                <p class="font-normal text-[18px] text-[#40493d]">{html.escape(conversation.eligibility_status.title())}</p>
            </div>
        </div>
        """
        
    source_url = result.source_url or conversation.researched_url
    source_section = ""
    if source_url:
        safe_url = html.escape(source_url, quote=True)
        source_section = f"""
        <div class="mt-6">
            <a href="{safe_url}" target="_blank" class="w-full bg-[#0d631b] hover:bg-[#0d631b]/90 text-white font-bold text-[20px] py-4 rounded-xl shadow-md transition-transform active:scale-95 flex items-center justify-center gap-2" style="text-decoration: none; color: white;">
                Open Official Source
                <span class="material-symbols-outlined">arrow_forward</span>
            </a>
        </div>
        """
        
    result_html = f"""
    <div class="flex justify-start w-full fade-in-up chat-bubble-delay-3 mt-6">
        <div class="glass-panel-heavy rounded-3xl p-6 w-full max-w-[90%] shadow-lg border-2 border-[#0d631b]/20 relative overflow-hidden">
            <div class="absolute top-0 right-0 bg-[#fabd00] text-[#5b4300] px-4 py-1 rounded-bl-xl font-semibold text-[16px] flex items-center gap-1">
                <span class="material-symbols-outlined text-[16px]">verified</span>
                Verified Source
            </div>
            <h3 class="font-bold text-[28px] text-[#0d631b] mb-4 pr-32">{html.escape(scheme_val)}</h3>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
                <div class="bg-[#f0eded] rounded-xl p-4 border border-[#bfcaba]/20">
                    <span class="block font-semibold text-[16px] text-[#40493d] mb-1">Financial Benefit</span>
                    <span class="font-bold text-[28px] text-[#0d631b]">{benefit_val}</span>
                </div>
                <div class="bg-[#f0eded] rounded-xl p-4 border border-[#bfcaba]/20">
                    <span class="block font-semibold text-[16px] text-[#40493d] mb-1">Subsidy / Equipment</span>
                    <span class="font-bold text-[28px] text-[#0d631b]">{subsidy_val} ({html.escape(equipment_val)})</span>
                </div>
            </div>
            <div class="space-y-3">
                {eligibility_section}
                {docs_section}
            </div>
            {source_section}
        </div>
    </div>
    """

# Render all conversation inside the Chat Area container
if chat_html or result_html:
    st.markdown(
        f"""
        <div class="w-full flex flex-col space-y-6 mb-16 px-4">
            {chat_html}
            {result_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
elif not conversation.turns:
    # Render empty state
    st.markdown(
        """
        <div class="glass-panel rounded-3xl p-12 text-center max-w-lg mx-auto mb-12 fade-in-up">
            <p class="font-semibold text-[32px] text-[#0d631b]">Tap the microphone and speak in Hindi, Telugu, or Tamil.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

# Render Persistent Error in Parent UI if exists
if st.session_state.error_message:
    st.error(st.session_state.error_message)

# Render Microphone Component
audio = autonomous_recorder(
    active=conversation.state != "COMPLETED",
    auto_start=False,
    tts_audio=None,
    tts_token=0,
    resume_after_tts=False,
)

# Autoplay TTS voice response if it is new and conversation is active
if st.session_state.tts_audio_bytes and conversation.state != "COMPLETED":
    if st.session_state.get("last_played_tts_token", -1) != st.session_state.tts_token:
        st.audio(st.session_state.tts_audio_bytes, format="audio/wav", autoplay=True)
        st.session_state.last_played_tts_token = st.session_state.tts_token

# Autoplay TTS voice response on first completion
if conversation.result.conversation_complete and st.session_state.tts_audio_bytes:
    if st.session_state.get("last_played_tts_token", -1) != st.session_state.tts_token:
        st.audio(st.session_state.tts_audio_bytes, format="audio/wav", autoplay=True)
        st.session_state.last_played_tts_token = st.session_state.tts_token
    # Show native streamlit audio controls for replaying the answer
    st.write("")
    st.write("")
    st.audio(st.session_state.tts_audio_bytes, format="audio/wav", autoplay=False)

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
        # Capture error and persist it
        st.session_state.error_message = audio.get("message") or "Microphone permission is required."
        conversation.set_state("IDLE")
        conversation.listening_started = False
        st.rerun()
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
