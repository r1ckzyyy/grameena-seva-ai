"""Configuration access for Streamlit and standalone server deployments."""

from __future__ import annotations

import os
from pathlib import Path


def secret(name: str) -> str:
    """Return a configured secret from Streamlit secrets or environment variables."""
    value = os.environ.get(name, "")
    if value:
        return str(value)
    try:
        import streamlit as st

        value = st.secrets.get(name, "")
    except (FileNotFoundError, KeyError, TypeError, ImportError, RuntimeError):
        value = ""
    return str(value or "")


def required_secrets() -> tuple[str, ...]:
    """Return the secrets required by the current voice/research workflow."""
    return ("SARVAM_API_KEY", "GEMINI_API_KEY", "TAVILY_API_KEY", "FIRECRAWL_API_KEY")


def database_path() -> Path:
    """Return the SQLite path, configurable for local and deployed environments."""
    configured = os.environ.get("GRAMEEN_SEVA_DB_PATH", "") or secret("GRAMEEN_SEVA_DB_PATH")
    return Path(configured) if configured else Path("data") / "grameen_seva.sqlite3"


def database_url() -> str:
    """Return the optional shared PostgreSQL URL for multi-service hosting."""
    return os.environ.get("DATABASE_URL", "") or secret("DATABASE_URL")


def knowledge_cache_ttl_seconds() -> int:
    """Return the configured knowledge-cache lifetime, defaulting to seven days."""
    configured = os.environ.get("KNOWLEDGE_CACHE_TTL_SECONDS", "") or secret("KNOWLEDGE_CACHE_TTL_SECONDS")
    try:
        return max(60, int(configured)) if configured else 7 * 24 * 60 * 60
    except ValueError:
        return 7 * 24 * 60 * 60


def twilio_configured() -> bool:
    """Return whether the optional Twilio transport has all required env vars."""
    return all(secret(key) for key in (
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"
    ))


def exotel_configured() -> bool:
    """Return whether the Exotel Voicebot transport has its required settings."""
    return all(secret(key) for key in (
        "EXOTEL_ACCOUNT_SID", "EXOTEL_API_KEY", "EXOTEL_API_TOKEN", "EXOTEL_EXOPHONE"
    ))


def twilio_public_base_url() -> str:
    """Return the externally reachable HTTPS base URL for Twilio callbacks."""
    return (
        os.environ.get("TWILIO_PUBLIC_BASE_URL", "")
        or os.environ.get("RENDER_EXTERNAL_URL", "")
        or secret("TWILIO_PUBLIC_BASE_URL")
    ).rstrip("/")


def twilio_port() -> int:
    """Return the optional Twilio webhook port."""
    try:
        configured = os.environ.get("TWILIO_PORT", "") or os.environ.get("PORT", "8080")
        return max(1024, int(configured))
    except ValueError:
        return 8080
