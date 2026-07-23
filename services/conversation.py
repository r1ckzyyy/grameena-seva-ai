"""Application-facing conversation service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from models.conversation import AgentResult, ConversationState
from repositories.conversations import ConversationRepository
from services.knowledge import KnowledgeService
from services.farmer_profile import FarmerProfileService
from services.eligibility import EligibilityService
from services.performance import PerformanceReport, PerformanceTracker
from agents.conversation import run_conversation, summarize_conversation
from services.recording import RecordingResult, process_recording
from repositories.models import FarmerRecord
from repositories.farmers import normalize_phone
import logging


logger = logging.getLogger("grameen_seva.conversation")


@dataclass
class TurnOutcome:
    """Unified result returned by audio and text transports."""

    success: bool
    response_text: str = ""
    audio: bytes | None = None
    error_message: str = ""
    detected_language: str = ""
    performance: PerformanceReport | None = None


class ConversationService:
    """Central engine coordinating Gemini, knowledge, eligibility, and farmer memory."""

    def __init__(
        self,
        conversations: ConversationRepository,
        knowledge: KnowledgeService,
        farmer_profiles: FarmerProfileService,
        eligibility: EligibilityService,
    ) -> None:
        self.conversations = conversations
        self.knowledge = knowledge
        self.farmer_profiles = farmer_profiles
        self.eligibility = eligibility

    def start_new_farmer(self) -> str:
        """Create a new demo farmer and return its identifier."""
        return self.farmer_profiles.start_new_farmer().id

    def delete_farmer(self, farmer_id: str) -> str:
        """Delete the current farmer and return a new empty farmer identifier."""
        self.farmer_profiles.delete_farmer(farmer_id)
        return self.farmer_profiles.start_new_farmer().id

    def load_session(self, farmer_id: str) -> tuple[ConversationState, bool]:
        """Load a farmer conversation from memory, creating a fresh state when needed."""
        farmer = self.farmer_profiles.get(farmer_id)
        if farmer is None:
            farmer_id = self.start_new_farmer()
            return ConversationState(farmer_id=farmer_id), False
        conversation = self.conversations.load_for_farmer(farmer_id)
        memory_loaded = self.farmer_profiles.has_memory(farmer)
        if conversation is None:
            return ConversationState(farmer_id=farmer_id), memory_loaded
        conversation.farmer_id = farmer_id
        return conversation, memory_loaded

    def load_or_create_farmer(self, phone: str) -> tuple[FarmerRecord, ConversationState, bool]:
        """Resolve a phone identity and load its conversation or create it."""
        try:
            farmer, conversation = self.farmer_profiles.load_or_create(phone=phone)
        except ValueError:
            raise
        except Exception:
            logger.exception("Farmer lookup failed; continuing with an unsaved demo session")
            normalized = normalize_phone(phone)
            if not normalized:
                raise ValueError("Enter a valid 10-digit mobile number.")
            return FarmerRecord(id=normalized, phone=normalized), ConversationState(farmer_id=normalized), False
        if conversation is None:
            return farmer, ConversationState(farmer_id=farmer.id), False
        conversation.farmer_id = farmer.id
        return farmer, conversation, True

    def bind_phone_identity(self, conversation: ConversationState, current_farmer_id: str, phone: str) -> tuple[str, bool]:
        """Attach an anonymous browser chat to the farmer's spoken phone identity."""
        farmer, _, returning = self.load_or_create_farmer(phone)
        conversation.farmer_id = farmer.id
        self.farmer_profiles.sync_from_conversation(farmer.id, conversation)
        self.conversations.save(conversation, farmer.id)
        return farmer.id, returning

    def _apply_eligibility(self, farmer_id: str, conversation: ConversationState) -> None:
        farmer = self.farmer_profiles.get(farmer_id)
        if farmer is None:
            return
        scheme = self.knowledge.scheme_for_url(conversation.researched_url or conversation.result.source_url)
        decision = self.eligibility.evaluate(farmer, conversation.result, scheme=scheme)
        conversation.result.eligibility_status = decision.status
        conversation.result.eligibility_confidence = decision.confidence
        conversation.result.eligibility_reasons = decision.reasons
        conversation.result.missing_requirements = decision.missing_requirements
        conversation.result.recommended_next_action = decision.recommended_next_action
        self.farmer_profiles.sync_from_conversation(farmer_id, conversation, eligibility_status=decision.status)

    def _prepare_conversation(self, farmer_id: str, conversation: ConversationState) -> str:
        conversation.farmer_id = farmer_id
        try:
            farmer = self.farmer_profiles.get(farmer_id)
        except Exception:
            logger.exception("Farmer memory lookup failed; continuing without saved context")
            farmer = None
        return self.farmer_profiles.memory_context(farmer) if farmer else ""

    def persist_summary(self, farmer_id: str, conversation: ConversationState, gemini_key: str) -> None:
        """Generate and save a summary, falling back to a deterministic summary."""
        if not conversation.turns or conversation.summary_persisted:
            return
        try:
            summary = summarize_conversation(conversation, gemini_key)
        except Exception:
            result = conversation.result
            summary = (
                f"Farmer asked about {result.scheme_name or result.equipment_or_input or 'a government scheme'}. "
                f"Eligibility status: {result.eligibility_status}."
            )
        self.farmer_profiles.append_summary(farmer_id, summary)
        conversation.summary_persisted = True

    def process_audio(
        self,
        audio_bytes: bytes,
        conversation: ConversationState,
        *,
        transcribe_fn: Callable[[bytes, str], tuple[str, str]],
        text_to_speech_fn: Callable[[str, str, str], bytes],
        sarvam_key: str,
        gemini_key: str,
        tavily_key: str,
        firecrawl_key: str,
        farmer_id: str,
        summarize: bool = True,
    ) -> RecordingResult:
        """Process one audio turn using the shared knowledge and persistence services."""
        tracker = PerformanceTracker()
        memory_context = self._prepare_conversation(farmer_id, conversation)
        with tracker.stage("conversation_orchestration"):
            outcome = process_recording(
                audio_bytes,
                conversation,
                transcribe_fn=transcribe_fn,
                text_to_speech_fn=text_to_speech_fn,
                sarvam_key=sarvam_key,
                gemini_key=gemini_key,
                tavily_key=tavily_key,
                firecrawl_key=firecrawl_key,
                knowledge_service=self.knowledge,
                memory_context=memory_context,
                performance=tracker,
            )
        persistence_warning = ""
        if outcome.success:
            with tracker.stage("eligibility_and_persistence"):
                try:
                    self._apply_eligibility(farmer_id, conversation)
                    if summarize and conversation.result.conversation_complete:
                        self.persist_summary(farmer_id, conversation, gemini_key)
                    self.conversations.save(conversation, farmer_id)
                except Exception:
                    logger.exception("Unable to persist audio conversation state")
                    persistence_warning = " I could not save this turn, but you can continue speaking."
        if persistence_warning:
            outcome.error_message = (outcome.error_message or outcome.response_text) + persistence_warning
        outcome.performance = tracker.report
        return outcome

    def process_text(
        self,
        text: str,
        conversation: ConversationState,
        *,
        farmer_id: str,
        gemini_key: str,
        tavily_key: str,
        firecrawl_key: str,
        summarize: bool = True,
    ) -> TurnOutcome:
        """Process text from a non-browser transport using the same agent pipeline."""
        tracker = PerformanceTracker()
        memory_context = self._prepare_conversation(farmer_id, conversation)
        conversation.add_turn("farmer", text)
        conversation.set_state("THINKING")
        with tracker.stage("gemini"):
            result = run_conversation(
                conversation,
                gemini_key,
                tavily_key,
                firecrawl_key,
                self.knowledge,
                memory_context=memory_context,
            )
        conversation.result = result
        if result.language:
            conversation.language_code = result.language
        response = (result.voice_response or "").strip()
        conversation.add_turn("assistant", response)
        conversation.set_state("COMPLETED" if result.conversation_complete else "LISTENING")
        persistence_warning = ""
        with tracker.stage("eligibility_and_persistence"):
            try:
                self._apply_eligibility(farmer_id, conversation)
                if summarize and conversation.result.conversation_complete:
                    self.persist_summary(farmer_id, conversation, gemini_key)
                self.conversations.save(conversation, farmer_id)
            except Exception:
                logger.exception("Unable to persist text conversation state")
                persistence_warning = "I could not save this turn, but you can continue speaking."
        return TurnOutcome(True, response_text=response, error_message=persistence_warning, performance=tracker.report)
