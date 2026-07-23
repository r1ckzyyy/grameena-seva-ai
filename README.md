# Grameen Seva AI Hub — Streamlit Deployment

Grameen Seva AI Hub is an AI-powered government scheme and subsidy inquiry assistant for Indian farmers. This clean deployment project contains the existing shared conversation, farmer-memory, eligibility, search, Gemini, Sarvam, SQLite, Streamlit, and Twilio implementations.

Farmers can ask questions by voice or text. The assistant uses Sarvam for speech, Gemini for conversation, Tavily and Firecrawl for official-source research, and SQLite for farmer profiles, conversations, schemes, and research cache.

## Architecture

`app.py` is the Streamlit UI. `twilio_server.py` is the standalone Flask webhook entry point. Both construct and reuse the same `ConversationService`, farmer repositories, `KnowledgeService`, eligibility service, and SQLite schema. Streamlit Community Cloud cannot expose the Flask Twilio routes, so Twilio requires the separate Render service described below.

The AI integrations are:

- Sarvam AI for speech-to-text and text-to-speech.
- Gemini API for conversational responses and tool calling.
- Tavily for official-source search.
- Firecrawl for official-page extraction.
- Twilio for phone calls and voice webhooks.

## Run locally

```powershell
pip install -r requirements.txt
streamlit run app.py
```

Configure these Streamlit secrets or environment variables:

```toml
SARVAM_API_KEY = "..."
GEMINI_API_KEY = "..."
TAVILY_API_KEY = "..."
FIRECRAWL_API_KEY = "..."
KNOWLEDGE_CACHE_TTL_SECONDS = "604800"
GRAMEEN_SEVA_DB_PATH = "data/grameen_seva.sqlite3"
```

`SARVAM_API_KEY`, `GEMINI_API_KEY`, `TAVILY_API_KEY`, and `FIRECRAWL_API_KEY` are required for the full voice-and-research workflow. The Streamlit UI starts without them and displays a warning. Never commit real credentials; use `.streamlit/secrets.toml` locally or deployment secrets.

The SQLite database defaults to `data/grameen_seva.sqlite3`; its parent directory and schema are created automatically. Set `GRAMEEN_SEVA_DB_PATH` to use another location.

## Application flow

1. Enter the farmer's mobile number. The normalized number is the cross-platform farmer identity.
2. An existing farmer profile and conversation are resumed, or a new phone-linked profile is created.
3. Ask a subsidy or scheme question by voice or text.
4. The conversation service gathers only the missing farmer details and saves them after each turn.
5. Knowledge service searches and caches official Indian government sources.
6. The assistant presents the suggested scheme, eligibility result, confidence, and official source.

The repository boundary is SQLite-backed and can be initialized independently with `create_repositories`. Farmer memory and deterministic eligibility evaluation remain separate from the Gemini conversation agent. The phone resolver is transport-neutral so Twilio, mobile, and future web adapters can use the same identity lookup.

## Deployment architecture

Streamlit Cloud runs `app.py` through Streamlit's server; it does not run or expose the Flask routes created by `create_twilio_app()` in `twilio_server.py`. The complete application therefore uses two entry points:

1. Streamlit Community Cloud for the web UI.
2. One small HTTPS Render web service for `twilio_server.py`.

Both entry points construct and reuse the same `ConversationService`; no second conversation engine is used.

## Optional Twilio voice server

The Twilio adapter reuses `ConversationService` and is inactive until the Twilio account settings are present:

```powershell
$env:TWILIO_ACCOUNT_SID = "..."
$env:TWILIO_AUTH_TOKEN = "..."
$env:TWILIO_PHONE_NUMBER = "+91..."
$env:TWILIO_PUBLIC_BASE_URL = "https://your-twilio-server.example.com"
python twilio_server.py
```

Expose the server through HTTPS, then configure the Twilio phone number's incoming voice webhook as `POST https://your-twilio-server.example.com/twilio/voice`. Configure the status callback as `POST https://your-twilio-server.example.com/twilio/status`. The server validates Twilio signatures, uses the caller's `From` number for farmer lookup, gathers speech, and loops through the shared conversation service. Sarvam audio is used when configured; Twilio `<Say>` is the fallback when Sarvam TTS is unavailable.

`TWILIO_PUBLIC_BASE_URL` is optional when the service is reached directly at its public URL, but should be set whenever a proxy or hosting platform changes the externally visible URL. It is used for generated callback URLs and signature validation.

## GitHub and Streamlit Community Cloud deployment

1. Create an empty GitHub repository.
2. From this project directory, run:

   ```powershell
   git init
   git add .
   git commit -m "Prepare Grameen Seva AI demo"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
   git push -u origin main
   ```

3. Open Streamlit Community Cloud and create an app from the repository's `main` branch.
4. Set the main file to `app.py`.
5. Add these Streamlit secrets in the app settings:

   ```toml
   SARVAM_API_KEY = "..."
   GEMINI_API_KEY = "..."
   TAVILY_API_KEY = "..."
   FIRECRAWL_API_KEY = "..."
   KNOWLEDGE_CACHE_TTL_SECONDS = "604800"
   GRAMEEN_SEVA_DB_PATH = "data/grameen_seva.sqlite3"
   ```

6. Deploy and open the Streamlit URL. The app creates its SQLite directory and schema automatically.

For a live demo, farmer memory is persisted within the SQLite file used by each running service. Streamlit Cloud and Render do not share local files; use the Twilio service as the source of truth for phone-call demonstrations, or run both entry points on a host with a shared filesystem when cross-interface memory is required.

## Standalone Twilio deployment on Render

1. In Render, choose **New → Blueprint** and select the GitHub repository. The included `render.yaml` sets the build command, start command, port behavior, and non-secret defaults.
2. In the Render service environment settings, provide the four AI keys plus:

   ```text
   TWILIO_ACCOUNT_SID
   TWILIO_AUTH_TOKEN
   TWILIO_PHONE_NUMBER
   GRAMEEN_SEVA_DB_PATH=data/grameen_seva.sqlite3
   ```

3. Render automatically supplies the public URL through `RENDER_EXTERNAL_URL`; no manual public URL value is needed. If deploying elsewhere, set `TWILIO_PUBLIC_BASE_URL` to the service's HTTPS base URL.
4. The Blueprint starts the service with `python twilio_server.py`.
5. Configure the Twilio number's incoming voice webhook to `POST https://YOUR-SERVICE.onrender.com/twilio/voice` and status callback to `POST https://YOUR-SERVICE.onrender.com/twilio/status`.
6. Confirm the service is reachable over HTTPS, then place a test call.

The server honors the hosting platform's `PORT` variable automatically; set `TWILIO_PORT` only when you need a custom port.

## First live call

1. Confirm the Render service is live over HTTPS.
2. Set the Twilio phone number's incoming voice webhook to `POST https://YOUR-SERVICE.onrender.com/twilio/voice`.
3. Set the status callback to `POST https://YOUR-SERVICE.onrender.com/twilio/status`.
4. Call the Twilio number from a phone.
5. Enter the caller's phone number when using Streamlit, or speak directly through the Twilio call; the shared farmer lookup and conversation service handle the interaction.

## Deployment and troubleshooting

- Run Streamlit with `streamlit run app.py`; run the standalone webhook with `python twilio_server.py`.
- Set `GRAMEEN_SEVA_DB_PATH` to a persistent writable volume in deployment. SQLite uses WAL mode and creates missing tables/indexes safely on startup.
- Twilio must have `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, and a reachable HTTPS base URL; otherwise the webhook server exits with a configuration message and Streamlit remains usable.
- The Twilio webhook must be HTTPS and publicly reachable. A `403` indicates a missing or invalid Twilio signature.
- If Gemini, Tavily, Firecrawl, or Sarvam is unavailable, the conversation returns a localized fallback; verify the corresponding key and service quota before retrying.
- Generated Twilio audio is served once and removed from the in-memory cache; restart the process after changing deployment secrets.

## Architecture notes

Streamlit and Twilio are adapters only. Both call the single `ConversationService`, which coordinates farmer memory, the KnowledgeService search pipeline, deterministic eligibility, and the single Gemini agent. Conversation snapshots and compact completed summaries are persisted so returning farmers can resume without replaying the full business workflow.
