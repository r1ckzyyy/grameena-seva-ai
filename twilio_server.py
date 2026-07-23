"""Optional Twilio webhook server for the shared farmer conversation engine."""

from __future__ import annotations

import os

from config.settings import database_path, knowledge_cache_ttl_seconds, secret, twilio_configured, twilio_port, twilio_public_base_url
from repositories import create_repositories
from services.conversation import ConversationService
from services.eligibility import EligibilityService
from services.farmer_profile import FarmerProfileService
from services.knowledge import KnowledgeService
from services.performance import configure_logging
from services.sarvam import text_to_speech
from services.twilio_transport import TwilioTransport, create_twilio_app


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
    if not twilio_configured():
        raise SystemExit("Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER first.")
    app = create_twilio_app(build_transport())
    app.run(host=os.environ.get("TWILIO_HOST", "0.0.0.0"), port=twilio_port(), debug=False)


if __name__ == "__main__":
    main()
