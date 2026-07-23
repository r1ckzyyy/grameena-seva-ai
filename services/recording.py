"""Transport-independent orchestration for one farmer voice turn."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from agents.conversation import _localized_fallback, run_conversation
from models.conversation import ConversationState
from services.knowledge import KnowledgeService
from services.performance import PerformanceReport, PerformanceTracker


@dataclass
class RecordingResult:
    """Outcome of processing one audio utterance."""

    success: bool
    response_text: str = ""
    error_message: str = ""
    detected_language: str = ""
    audio: bytes | None = None
    performance: PerformanceReport | None = None


def process_recording(
    audio_bytes: bytes,
    conversation: ConversationState,
    *,
    transcribe_fn: Callable[[bytes, str], tuple[str, str]],
    text_to_speech_fn: Callable[[str, str, str], bytes],
    sarvam_key: str,
    gemini_key: str,
    tavily_key: str,
    firecrawl_key: str,
    knowledge_service: KnowledgeService | None = None,
    memory_context: str = "",
    performance: PerformanceTracker | None = None,
) -> RecordingResult:
    """Process audio without depending on Streamlit or a specific transport."""
    tracker = performance or PerformanceTracker()
    conversation.set_state("PROCESSING")
    try:
        with tracker.stage("speech_to_text"):
            transcript, detected_language = transcribe_fn(audio_bytes, sarvam_key)
    except Exception:
        message = _localized_fallback(conversation.language_code, "temporary")
        conversation.set_state("LISTENING")
        return RecordingResult(False, error_message=message, performance=tracker.report)

    if not transcript:
        message = _localized_fallback(conversation.language_code, "repeat")
        conversation.set_state("LISTENING")
        return RecordingResult(False, error_message=message, performance=tracker.report)

    conversation.transcript = transcript
    if detected_language:
        conversation.language_code = detected_language
    conversation.add_turn("farmer", transcript)
    conversation.set_state("THINKING")
    with tracker.stage("gemini"):
        result = run_conversation(
            conversation,
            gemini_key,
            tavily_key,
            firecrawl_key,
            knowledge_service,
            memory_context=memory_context,
        )
    conversation.result = result

    response_text = (result.voice_response or "").strip() or _localized_fallback(conversation.language_code, "prompt")
    result.voice_response = response_text
    result.next_question = ""
    conversation.add_turn("assistant", response_text)
    conversation.goodbye_detected = result.goodbye_detected
    conversation.set_state("SPEAKING")
    try:
        with tracker.stage("text_to_speech"):
            audio = text_to_speech_fn(response_text, conversation.language_code or result.language or "hi-IN", sarvam_key)
    except Exception:
        audio = None
        error_message = response_text
    else:
        error_message = ""
    conversation.set_state("COMPLETED" if result.conversation_complete else "LISTENING")
    return RecordingResult(
        True,
        response_text=response_text,
        error_message=error_message,
        detected_language=detected_language,
        audio=audio,
        performance=tracker.report,
    )
