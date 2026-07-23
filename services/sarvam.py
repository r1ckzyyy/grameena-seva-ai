"""Sarvam speech services with automatic language detection."""

from __future__ import annotations

import base64
import io
from typing import Any

import streamlit as st


def _client(api_key: str):
    from sarvamai import SarvamAI

    return SarvamAI(api_subscription_key=api_key)


def transcribe(audio_bytes: bytes, api_key: str) -> tuple[str, str]:
    """Return transcript and Sarvam's detected language code."""
    result = _client(api_key).speech_to_text.transcribe(
        file=io.BytesIO(audio_bytes),
        model="saaras:v3",
        language_code="unknown",
    )
    transcript = str(getattr(result, "transcript", "") or "").strip()
    language = str(
        getattr(result, "language_code", "")
        or getattr(result, "language", "")
        or ""
    )
    return transcript, language


def text_to_speech(text: str, language_code: str, api_key: str) -> bytes:
    result = _client(api_key).text_to_speech.convert(
        text=text[:2500],
        target_language_code=language_code or "hi-IN",
        model="bulbul:v3",
        speaker="shubh",
    )
    raw: Any = result.audios[0]
    return base64.b64decode(raw) if isinstance(raw, str) else raw
