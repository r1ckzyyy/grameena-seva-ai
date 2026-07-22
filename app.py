# app.py
import streamlit as st
import time
import logging

import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, RetryError, DeadlineExceeded

# ---------------------------------------------------------
# GEMINI CONFIGURATION (Single Client)
# ---------------------------------------------------------
if "gemini_api_key" in st.secrets:
    genai.configure(api_key=st.secrets["gemini_api_key"])
else:
    st.error("Configuration Error: GEMINI_API_KEY missing from secrets.")
    st.stop()

# ---------------------------------------------------------
# PROJECT IMPORTS
# ---------------------------------------------------------
from models.conversation import ConversationState
from agents.conversation import process_conversation, AgentResult
from services.recorder import autonomous_recorder
from services.sarvam import speech_to_text, text_to_speech
from services.research import tavily_search, firecrawl_scrape

# ---------------------------------------------------------
# PHASE 9: FRONTEND QUALITY
# ---------------------------------------------------------
st.set_page_config(
    page_title="Grameen Seva AI Hub",
    page_icon="🌾",
    layout="centered",
    initial_sidebar_state="collapsed"
)

st.markdown("""
    <style>
    .stApp { background-color: #F8FBF8; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
    .main-title { color: #1B5E20; font-size: 3.2rem; text-align: center; font-weight: 800; margin-bottom: 0.2rem; }
    .sub-title { color: #558B2F; font-size: 1.3rem; text-align: center; margin-bottom: 2.5rem; font-weight: 500; }
    .chat-container { background: #FFFFFF; border-radius: 16px; padding: 24px; margin-bottom: 24px; box-shadow: 0 8px 16px rgba(0,0,0,0.04); }
    .user-msg { color: #2E7D32; font-weight: 700; margin-bottom: 6px; font-size: 1.2rem; }
    .ai-msg { color: #333333; margin-bottom: 20px; font-size: 1.15rem; border-left: 4px solid #4CAF50; padding-left: 12px; line-height: 1.6; }
    /* Hide developer info & clutter for kiosk mode */
    #MainMenu, footer, header { visibility: hidden; }
    .stSpinner > div > div { border-color: #4CAF50 transparent transparent transparent !important; }
    </style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# PHASE 8: CONVERSATION STATE
# ---------------------------------------------------------
def init_session_state():
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
    if "last_processed_audio_hash" not in st.session_state:
        st.session_state.last_processed_audio_hash = None
    if "tts_playback_queue" not in st.session_state:
        st.session_state.tts_playback_queue = None

# ---------------------------------------------------------
# PHASE 6: GEMINI STABILITY
# ---------------------------------------------------------
def safe_gemini_execution(func, *args, **kwargs):
    """Executes Gemini API calls with exponential backoff for resilience."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except (ResourceExhausted, RetryError, DeadlineExceeded) as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            logging.error(f"Gemini API Quota/Timeout Error: {e}")
            return None
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "exhausted" in err_str or "timeout" in err_str:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
            logging.error(f"Unexpected Execution Error: {e}")
            return None

# ---------------------------------------------------------
# PHASE 7: SEARCH PIPELINE
# ---------------------------------------------------------
def execute_search_pipeline(state: ConversationState) -> str:
    """Strict search sequence: Gov URL selection -> Firecrawl Scrape -> Gemini Summarization."""
    loc = state.state or ""
    cat = state.farmer_category or ""
    
    # 1. Official Government Search Only
    query = f"government agricultural schemes subsidies {loc} {cat} site:myscheme.gov.in OR site:gov.in"
    search_res = tavily_search(query)
    
    if search_res and "results" in search_res and search_res["results"]:
        official_url = search_res["results"][0].get("url")
        
        # 2. Extract Data via Firecrawl
        if official_url and ("gov.in" in official_url):
            scrape_content = firecrawl_scrape(official_url)
            
            # 3. Grounded Gemini Summarization in Detected Language
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = f"""
            Summarize the exact subsidy/scheme details based ONLY on the provided data. 
            Do NOT hallucinate or invent eligibility rules. 
            Write the response entirely in this language code: {state.language}. 
            Data: {scrape_content}
            """
            
            summary = safe_gemini_execution(model.generate_content, prompt)
            if summary and summary.text:
                return summary.text
                
    # Fallback if no verified scheme is found
    fallback_msgs = {
        "hi-IN": "सरकारी योजना की जानकारी अभी उपलब्ध नहीं है। कृपया बाद में पुनः प्रयास करें।",
        "te-IN": "అధికారిక పథకం సమాచారం ప్రస్తుతం అందుబాటులో లేదు. దయచేసి తర్వాత మళ్లీ ప్రయత్నించండి.",
        "ta-IN": "அரசு திட்டத் தகவல் தற்போது கிடைக்கவில்லை. சிறிது நேரம் கழித்து மீண்டும் முயற்சிக்கவும்."
    }
    return fallback_msgs.get(state.language, "Official scheme information is not available right now. Please try again later.")

# ---------------------------------------------------------
# PHASE 3: MAIN APP LIFECYCLE
# ---------------------------------------------------------
def main():
    init_session_state()
    
    st.markdown("<div class='main-title'>🌾 Grameen Seva AI Hub</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-title'>Speak naturally. We are here to help you.</div>", unsafe_allow_html=True)

    # Render Conversation History
    if st.session_state.state.history:
        st.markdown("<div class='chat-container'>", unsafe_allow_html=True)
        for msg in st.session_state.state.history:
            if msg["role"] == "user":
                st.markdown(f"<div class='user-msg'>Farmer:</div><div style='margin-bottom: 15px;'>{msg['content']}</div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<div class='ai-msg'>{msg['content']}</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    # PHASE 5: MICROPHONE EXPERIENCE (Single Component)
    st.write("")
    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        audio_data = autonomous_recorder()

    # AUDIO PROCESSING PATH (Phases 3, 4, 7)
    if audio_data:
        current_hash = hash(audio_data)
        if current_hash != st.session_state.last_processed_audio_hash:
            st.session_state.last_processed_audio_hash = current_hash
            
            with st.spinner("Processing..."):
                
                # 1. Language Detection & STT
                stt_result = speech_to_text(audio_data)
                transcript = stt_result.get("transcript")
                lang_code = stt_result.get("language_code")
                
                if transcript:
                    # Lock language if this is the first turn
                    if not st.session_state.state.language and lang_code:
                        st.session_state.state.language = lang_code
                        
                    st.session_state.state.history.append({"role": "user", "content": transcript})
                    
                    # 2. Standard Agent Processing
                    agent_result: AgentResult = safe_gemini_execution(
                        process_conversation,
                        st.session_state.state,
                        transcript
                    )
                    
                    final_text = ""
                    if agent_result:
                        st.session_state.state = agent_result.updated_state
                        final_text = agent_result.response_text
                        
                        # 3. Trigger Gov Search Pipeline when criteria met
                        if st.session_state.state.conversation_complete:
                            final_text = execute_search_pipeline(st.session_state.state)
                    else:
                        final_text = "The system is currently busy. Please try speaking again."

                    st.session_state.state.history.append({"role": "assistant", "content": final_text})
                    
                    # 4. Sarvam TTS using locked language
                    tts_audio_b64 = text_to_speech(final_text, st.session_state.state.language)
                    st.session_state.tts_playback_queue = tts_audio_b64
                    
                    st.rerun()

    # PHASE 4: PLAYBACK SYSTEM
    if st.session_state.tts_playback_queue:
        audio_html = f'''
            <audio autoplay style="display:none;">
                <source src="data:audio/wav;base64,{st.session_state.tts_playback_queue}" type="audio/wav">
            </audio>
        '''
        st.markdown(audio_html, unsafe_allow_html=True)
        st.session_state.tts_playback_queue = None

if __name__ == "__main__":
    main()
