# Grameen Seva AI Hub

**Voice-first agricultural subsidy and government scheme finder** — built for desktop/kiosk display at science fairs. Farmers speak their question; the AI searches official Indian government portals, calculates subsidy amounts, and responds with spoken audio in Hindi, Telugu, or Tamil.

---

## Features

| Feature | Technology |
|---------|------------|
| Voice input | Autonomous browser recorder with ~2 second silence detection |
| Speech-to-Text | Sarvam AI `saaras:v3` |
| AI Agent | Google Gemini stable Flash alias + function calling, one cached client |
| Web search | Tavily API (`myscheme.gov.in`, `gov.in`) |
| Page reading | Firecrawl API |
| Text-to-Speech | Sarvam AI `bulbul:v3` |
| Kiosk UI | Wide two-column layout, high-contrast metric cards |

---

## Project Structure

```
.
├── app.py                    # Main Streamlit application
├── requirements.txt          # Pinned dependencies
├── .gitignore
├── .streamlit/
│   └── secrets.toml          # API keys (not committed)
└── README.md
```

---

## Local Setup

### 1. Create virtual environment & install

```bash
cd "farmer app"
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure secrets

API keys are already in `.streamlit/secrets.toml`. To use your own keys, edit that file:

```toml
SARVAM_API_KEY = "your-key"
GEMINI_API_KEY = "your-key"
TAVILY_API_KEY = "your-key"
FIRECRAWL_API_KEY = "your-key"
# Optional — QR code target URL for science fair kiosk
DEPLOY_URL = "https://your-app.streamlit.app"
```

### 3. Run locally

```bash
streamlit run app.py
```

Open `http://localhost:8501` in **Chrome** or **Edge** (best mic support).

---

## Science Fair Presenter Guide

### Before the demo

1. Open the app full-screen (F11) on a laptop connected to a large monitor.
2. Set **State** to your demo region (default: Telangana).
3. Choose **Output Voice Language** matching your audience (Hindi / Telugu / Tamil).
4. Allow microphone permission when the browser prompts.
5. Optionally set `DEPLOY_URL` in secrets so the QR code points to your live deployment.

### Demo script (2 minutes)

1. **Intro:** *"Grameen Seva AI Hub helps farmers find government subsidies using only their voice."*
2. **Speak:** Tap the mic and say:
   - *"Drip irrigation subsidy for small farmers in Telangana"*, or
   - *"ट्रैक्टर पर कितनी सब्सिडी मिलती है?"*
3. **Point to dashboard:** Show the live transcript, subsidy percentage, and max claim amount cards.
4. **Voice response:** Let the AI spoken answer play automatically.
5. **Wrap-up:** *"It searches myscheme.gov.in and gov.in in real time — no typing needed."*

### Troubleshooting at the booth

| Issue | Fix |
|-------|-----|
| Mic not working | Use Chrome/Edge; check browser mic permissions |
| Slow first response | First API call may take 10–15 s; run one demo beforehand |
| No audio playback | Check laptop volume; click **Listen Again** |
| "Missing API keys" | Verify `.streamlit/secrets.toml` exists locally or secrets are set on Cloud |

---

## Deploy to Streamlit Community Cloud

### Step 1 — Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit: Grameen Seva AI Hub"
git branch -M main
gh repo create grameen-seva-ai-hub --public --source=. --remote=origin --push
```

If `gh` is not installed, create the repo manually at [github.com/new](https://github.com/new), then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/grameen-seva-ai-hub.git
git push -u origin main
```

### Step 2 — Deploy on share.streamlit.io

1. Go to **[share.streamlit.io](https://share.streamlit.io/)** and sign in with GitHub.
2. Click **Create app** → **Deploy a public app from GitHub**.
3. Select your repository and branch (`main`).
4. Set **Main file path** to `app.py`.
5. Click **Advanced settings** → open **Secrets** and paste:

```toml
SARVAM_API_KEY = "your-sarvam-key"
GEMINI_API_KEY = "your-gemini-key"
TAVILY_API_KEY = "your-tavily-key"
FIRECRAWL_API_KEY = "your-firecrawl-key"
DEPLOY_URL = "https://YOUR-APP-NAME.streamlit.app"
```

6. Click **Deploy**. Wait 2–3 minutes for the build to finish.
7. Copy your live URL (e.g. `https://grameen-seva-ai-hub.streamlit.app`) and update `DEPLOY_URL` in Cloud secrets so the kiosk QR code works.

### Step 3 — Kiosk mode on Cloud

- Open the deployed URL in Chrome full-screen.
- Pin the tab; disable sleep on the demo laptop.
- Test mic + one full query before judges arrive.

---

## Architecture

```
Farmer taps once → browser microphone + automatic silence detection and resume
       ↓
Sarvam STT (saaras:v3) → transcript
       ↓
Gemini stable Flash alias → conversation state and one follow-up question
       ↓ (only when complete)
Tavily → one official URL → Firecrawl → one selected official page
       ↓
Structured JSON → Metric cards (%, ₹ max claim, scheme name)
       ↓
Sarvam TTS (bulbul:v3) → autoplay WAV + replay button
```

---

## API Key Sources

| Key | Provider |
|-----|----------|
| `SARVAM_API_KEY` | [sarvam.ai](https://www.sarvam.ai/) |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) |
| `TAVILY_API_KEY` | [tavily.com](https://tavily.com/) |
| `FIRECRAWL_API_KEY` | [firecrawl.dev](https://www.firecrawl.dev/) |

---

## License

MIT — free to use for educational and farmer-assistance projects.
