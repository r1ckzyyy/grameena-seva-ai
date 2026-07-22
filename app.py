import base64
import logging
import time
import streamlit as st

# Setup Logging
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------
# GEMINI SDK DYNAMIC COMPATIBILITY
# Handles both 'google-genai' and 'google-generativeai'
# ---------------------------------------------------------
GEMINI_KEY = st.secrets.get("gemini_api_key") or st.secrets.get("GEMINI_API_KEY")

try:
    from google import genai
    from google.genai.errors import APIError
    SDK_MODE = "google-genai"
except ImportError:
    import google.generativeai as genai
    APIError = Exception
    SDK_MODE = "google-generativeai"

if GEMINI_KEY:
    try:
        if SDK_MODE == "google-genai":
            ai_client = genai.Client(api_key=GEMINI_KEY)
        else:
            genai.configure(api_key=GEMINI_KEY)
            ai_client = True
    except Exception as e:
        logging.error(f"Gemini initialization error: {e}")
        ai_client = None
else:
    ai_client = None
    st.error("Configuration Error: GEMINI_API_KEY is missing from Streamlit secrets.")

# ---------------------------------------------------------
# PROJECT IMPORTS
# ---------------------------------------------------------
from models.conversation import ConversationState
from agents.conversation import process_conversation, AgentResult
from services.recorder import autonomous_recorder
from services.sarvam import speech_to_text, text_to_speech
from services.research import tavily_search, firecrawl_scrape

# ---------------------------------------------------------
# PAGE CONFIGURATION & KIOSK STYLING
# ---------------------------------------------------------
st.set_page_config(
    page_title="Grameen Seva AI Hub",
    page_icon="🌾",
    layout="centered",
    initial_sidebar_state="collapsed"
)

st.markdown("""
    <style>
    /* Hide Streamlit navbar/footer and browser audio player element */
    #MainMenu, footer, header { visibility: hidden !important; }
    audio { display: none !important; visibility: hidden !important; height: 0px !important; }
    
    .stApp {
        background-color: #F8FBF8;
        font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    }
    .header-container {
        text-align: center;
        padding-top: 1rem;
        padding-bottom: 0.5rem;
    }
    .main-title {
        color: #1B5E20;
        font-size: 2.8rem;
        font-weight: 800;
        margin-bottom: 0.2rem;
        letter-spacing: -0.5px;
    }
    .sub-title {
        color: #388E3C;
        font-size: 1.15rem;
        font-weight: 500;
        margin-bottom: 1rem;
    }
    .lang-badge {
        display: inline-block;
        background-color: #E8F5E9;
        color: #2E7D32;
        padding: 6px 16px;
        border-radius: 20px;
        font-size: 0.95rem;
        font-weight: 600;
        border: 1px solid #A5D6A7;
        margin-bottom: 1.5rem;
    }
    .chat-card {
        background: #FFFFFF;
        border-radius: 16px;
        padding: 20px;
        margin-bottom: 16px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.03);
        border: 1px solid #E0E0E0;
    }
    .user-label {
        color: #2E7D32;
        font-weight: 700;
        font-size: 1.1rem;
        margin-bottom: 4px;
    }
    .user-text {
        color: #1C2A1E;
        font-size: 1.1rem;
        line-height: 1.5;
        margin-bottom: 16px;
    }
    .ai-label {
        color: #1B5E20;
        font-weight: 700;
        font-size: 1.1rem;
        margin-bottom: 4px;
    }
    .ai-text {
        color: #263238;
        font-size: 1.1rem;
        line-height: 1.6;
        border-left: 4px solid #4CAF50;
        padding-left: 12px;
        background-color: #FAFAFA;
        padding-top: 8px;
        padding-bottom: 8px;
        border-radius: 0 8px 8px 0;
    }
    </style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# SESSION STATE INITIALIZATION
# ---------------------------------------------------------
def init_state():
    if "state" not in st.session_state:
        st.session_state.state = ConversationState(
            language=None,
            history=[],
            district=None,
            state=None,
            land_size=None,
            farmer_category=None,
            equipment_need=None,
            documents=[],
            eligibility_status=None,
            conversation_complete=False
        )
    if "last_audio_hash" not in st.session_state:
        st.session_state.last_audio_hash = None
    if "tts_audio_b64" not in st.session_state:
        st.session_state.tts_audio_b64 = None

# ---------------------------------------------------------
# HELPER UTILITIES
# ---------------------------------------------------------
def generate_gemini_summary(prompt: str) -> str:
    """Executes Gemini generation with retry logic supporting both SDKs."""
    if not ai_client:
        return ""
        
    for attempt in range(3):
        try:
            if SDK_MODE == "google-genai":
                res = ai_client.models.generate_content(
                    model="gemini-1.5-flash",
                    contents=prompt
                )
                if res and res.text:
                    return res.text
            else:
                model = genai.GenerativeModel("gemini-1.5-flash")
                res = model.generate_content(prompt)
                if res and res.text:
                    return res.text
        except Exception as e:
            logging.warning(f"Gemini API attempt {attempt + 1} failed: {e}")
            time.sleep(1.5 * (attempt + 1))
    return ""

def format_audio_b64(raw_tts) -> str:
    """Ensures TTS output is formatted as a valid base64 string."""
    if not raw_tts:
        return ""
    if isinstance(raw_tts, bytes):
        return base64.b64encode(raw_tts).decode("utf-8")
    return str(raw_tts)

# ---------------------------------------------------------
# SEARCH PIPELINE
# ---------------------------------------------------------
def run_gov_search_pipeline(state: ConversationState) -> str:
    """Executes Tavily search strictly on gov domains, scrapes via Firecrawl, and summarizes via Gemini."""
    district = getattr(state, "district", "") or ""
    st_name = getattr(state, "state", "") or ""
    category = getattr(state, "farmer_category", "") or ""
    equipment = getattr(state, "equipment_need", "") or ""
    lang = getattr(state, "language", None) or "hi-IN"
    
    query = f"government scheme subsidy {equipment} {category} {district} {st_name} site:myscheme.gov.in OR site:gov.in"
    
    try:
        search_results = tavily_search(query)
        if search_results and "results" in search_results and search_results["results"]:
            official_url = None
            for item in search_results["results"]:
                url = item.get("url", "")
                if "gov.in" in url:
                    official_url = url
                    break
                    
            if official_url:
                page_text = firecrawl_scrape(official_url)
                if page_text:
                    prompt = f"""
                    You are an agricultural advisor assisting an Indian farmer at a government kiosk.
                    Summarize the verified government subsidy/scheme details clearly and concisely.
                    Rely strictly on the provided web content. Do NOT invent eligibility rules or subsidy amounts.
                    MUST reply entirely in the farmer's detected language (Language code: {lang}).
                    
                    Scraped Web Content:
                    {page_text[:4000]}
                    """
                    summary = generate_gemini_summary(prompt)
                    if summary:
                        return summary
    except Exception as e:
        logging.error(f"Search pipeline execution error: {e}")
        
    fallback_map = {
        "te-IN": "మీ పరిధిలోని ప్రభుత్వ సబ్సిడీ వివరాల సమాచారం కోసం అధికారిక వెబ్‌సైట్ myscheme.gov.in చూడవచ్చు.",
        "hi-IN": "आपकी सरकारी सब्सिडी योजना की जानकारी के लिए myscheme.gov.in पर देखें।",
        "ta-IN": "உங்கள் அரசு மானிய திட்ட தகவல்களுக்கு myscheme.gov.in வலைத்தளத்தை பார்க்கவும்.",
        "kn-IN": "ನಿಮ್ಮ ಅರ್ಹತೆಗೆ ಸೂಕ್ತವಾದ ಸರ್ಕಾರಿ ಯೋಜನೆಗಳ ಮಾಹಿತಿಗಾಗಿ myscheme.gov.in ಗೆ ಭೇಟಿ ನೀಡಿ.",
        "ml-IN": "നിങ്ങളുടെ യോഗ്യതയ്ക്കനുസരിച്ചുള്ള സർക്കാർ പദ്ധതികളുടെ വിവരങ്ങൾക്ക് myscheme.gov.in സന്ദർശിക്കുക."
    }
    return fallback_map.get(lang, "For official government scheme details, please visit myscheme.gov.in.")

# ---------------------------------------------------------
# MAIN KIOSK APPLICATION
# ---------------------------------------------------------
def main():
    init_state()
    
    # 1. Single Title & Subtitle Header
    st.markdown("""
        <div class="header-container">
            <div class="main-title">🌾 Grameen Seva AI Hub</div>
            <div class="sub-title">Voice-First Government Subsidy Finder for Indian Farmers</div>
        </div>
    """, unsafe_allow_html=True)
    
    # 2. Language Indicator
    if st.session_state.state.language:
        lang_display = {
            "te-IN": "తెలుగు (Telugu)",
            "hi-IN": "हिंदी (Hindi)",
            "ta-IN": "தமிழ் (Tamil)",
            "kn-IN": "ಕನ್ನಡ (Kannada)",
            "ml-IN": "മലയാളം (Malayalam)",
            "mr-IN": "मराठी (Marathi)",
            "bn-IN": "বাংলা (Bengali)",
            "gu-IN": "ગુજરાતી (Gujarati)",
            "pa-IN": "ਪੰਜਾਬੀ (Punjabi)",
            "en-IN": "English"
        }.get(st.session_state.state.language, st.session_state.state.language)
        
        st.markdown(f'<div style="text-align:center;"><span class="lang-badge">🌐 Detected Language: {lang_display}</span></div>', unsafe_allow_html=True)

    # 3. Conversation Card
    if st.session_state.state.history:
        st.markdown('<div class="chat-card">', unsafe_allow_html=True)
        for msg in st.session_state.state.history:
            if msg["role"] == "user":
                st.markdown('<div class="user-label">🧑‍🌾 Farmer:</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="user-text">{msg["content"]}</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="ai-label">🤖 Grameen AI:</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="ai-text">{msg["content"]}</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # 4. Microphone Kiosk Control
    st.write("")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        audio_data = autonomous_recorder()

    # 5. Speech & Agent Pipeline Processing
    if audio_data:
        curr_hash = hash(audio_data)
        if curr_hash != st.session_state.last_audio_hash:
            st.session_state.last_audio_hash = curr_hash
            
            with st.spinner("Processing speech..."):
                # STT via Sarvam
                stt_res = speech_to_text(audio_data) or {}
                transcript = stt_res.get("transcript")
                detected_lang = stt_res.get("language_code")
                
                if transcript:
                    # Lock language automatically
                    if detected_lang and not st.session_state.state.language:
                        st.session_state.state.language = detected_lang
                    elif not st.session_state.state.language:
                        st.session_state.state.language = "hi-IN"
                        
                    st.session_state.state.history.append({"role": "user", "content": transcript})
                    
                    # Agent Logic Execution
                    reply_text = ""
                    try:
                        agent_res = process_conversation(st.session_state.state, transcript)
                        
                        if hasattr(agent_res, "response_text") and agent_res.response_text:
                            reply_text = agent_res.response_text
                            if hasattr(agent_res, "updated_state") and agent_res.updated_state:
                                st.session_state.state = agent_res.updated_state
                        elif isinstance(agent_res, dict):
                            reply_text = agent_res.get("response_text", "")
                            if "updated_state" in agent_res:
                                st.session_state.state = agent_res["updated_state"]
                    except Exception as err:
                        logging.error(f"Error processing conversation turn: {err}")
                        
                    if not reply_text:
                        fallbacks = {
                            "te-IN": "దయచేసి మీ ప్రశ్నను మరొకసారి చెప్పండి.",
                            "hi-IN": "कृपया अपना प्रश्न दोबारा कहें।",
                            "ta-IN": "தயவுசெய்து உங்கள் கேள்வியை மீண்டும் சொல்லுங்கள்."
                        }
                        reply_text = fallbacks.get(st.session_state.state.language, "Please repeat your question.")

                    # Search Pipeline Trigger (when profiles/requirements are collected)
                    if getattr(st.session_state.state, "conversation_complete", False):
                        search_summary = run_gov_search_pipeline(st.session_state.state)
                        if search_summary:
                            reply_text = search_summary
                            
                    st.session_state.state.history.append({"role": "assistant", "content": reply_text})
                    
                    # TTS Voice Response via Sarvam
                    raw_tts = text_to_speech(reply_text, st.session_state.state.language)
                    st.session_state.tts_audio_b64 = format_audio_b64(raw_tts)
                    
                    st.rerun()

    # 6. Invisible Background Audio Autoplay
    if st.session_state.get("tts_audio_b64"):
        b64_str = st.session_state.tts_audio_b64
        st.markdown(
            f'''
            <audio autoplay style="display:none !important; visibility:hidden !important;">
                <source src="data:audio/wav;base64,{b64_str}" type="audio/wav">
            </audio>
            ''',
            unsafe_allow_html=True
        )
        st.session_state.tts_audio_b64 = None

if __name__ == "__main__":
    main()
