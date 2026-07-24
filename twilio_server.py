"""Optional Twilio webhook server for the shared farmer conversation engine."""

from __future__ import annotations

import os
import logging

from config.settings import database_path, exotel_configured, knowledge_cache_ttl_seconds, secret, twilio_configured, twilio_port, twilio_public_base_url
from repositories import create_repositories
from services.conversation import ConversationService
from services.eligibility import EligibilityService
from services.farmer_profile import FarmerProfileService
from services.knowledge import KnowledgeService
from services.performance import configure_logging
from services.sarvam import text_to_speech
from services.sarvam import transcribe
from services.exotel_transport import ExotelTransport, create_exotel_app
from services.twilio_transport import TwilioTransport, create_twilio_app


logger = logging.getLogger("grameen_seva.exotel")


def build_transport() -> TwilioTransport:
    repositories = create_repositories(database_path())
    knowledge = KnowledgeService(
        repositories.research_cache,
        scheme_repository=repositories.schemes,
        ttl_seconds=knowledge_cache_ttl_seconds(),
    )
    profiles = FarmerProfileService(repositories.farmers, repositories.conversations)
    service = ConversationService(repositories.conversations, knowledge, profiles, EligibilityService())
    return TwilioTransport(
        service,
        gemini_key=secret("GEMINI_API_KEY"),
        tavily_key=secret("TAVILY_API_KEY"),
        firecrawl_key=secret("FIRECRAWL_API_KEY"),
        sarvam_key=secret("SARVAM_API_KEY"),
        text_to_speech_fn=text_to_speech,
        public_base_url=twilio_public_base_url(),
    )


def main() -> None:
    configure_logging()
    if exotel_configured():
        repositories = create_repositories(database_path())
        knowledge = KnowledgeService(repositories.research_cache, scheme_repository=repositories.schemes, ttl_seconds=knowledge_cache_ttl_seconds())
        profiles = FarmerProfileService(repositories.farmers, repositories.conversations)
        service = ConversationService(repositories.conversations, knowledge, profiles, EligibilityService())
        def exotel_tts(text: str, language: str, api_key: str) -> bytes:
            try:
                return text_to_speech(
                    text,
                    language,
                    api_key,
                    output_audio_codec="wav",
                    speech_sample_rate=8000,
                )
            except Exception:
                # Older Sarvam SDKs do not accept the explicit codec options.
                # Fall back to their default WAV output; the Exotel adapter
                # converts that WAV to 8 kHz PCM before sending it.
                logger.warning("Sarvam SDK does not support telephony codec options; falling back to WAV")
                return text_to_speech(text, language, api_key)

        transport = ExotelTransport(
            service,
            gemini_key=secret("GEMINI_API_KEY"),
            tavily_key=secret("TAVILY_API_KEY"),
            firecrawl_key=secret("FIRECRAWL_API_KEY"),
            sarvam_key=secret("SARVAM_API_KEY"),
            transcribe_fn=transcribe,
            text_to_speech_fn=exotel_tts,
        )
        greeting = "Hello, Namaskaram. Which government subsidy or farming scheme would you like help with today?"
        thinking = "One moment, I am checking the official information. Please wait."
        try:
            logger.info("Preloading Exotel greeting audio")
            transport.preload_audio(greeting, "en-IN", exotel_tts(greeting, "en-IN", transport.sarvam_key))
            transport.preload_audio(thinking, "en-IN", exotel_tts(thinking, "en-IN", transport.sarvam_key))
        except Exception:
            logger.exception("Unable to preload Exotel greeting audio")
        app = create_exotel_app(transport)
    elif twilio_configured():
        app = create_twilio_app(build_transport())
    else:
        raise SystemExit("Configure Exotel or Twilio voice credentials first.")
    app.run(host=os.environ.get("TWILIO_HOST", "0.0.0.0"), port=twilio_port(), debug=False)


if __name__ == "__main__":
    main()
