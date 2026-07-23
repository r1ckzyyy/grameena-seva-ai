"""Typed state for the farmer conversation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


CONVERSATION_STATES = (
    "IDLE",
    "LISTENING",
    "PROCESSING",
    "THINKING",
    "SPEAKING",
    "SEARCHING",
    "DISPLAY_RESULTS",
    "COMPLETED",
)


@dataclass
class AgentResult:
    language: str = ""
    name: str = ""
    mobile_number: str = ""
    state: str = ""
    district: str = ""
    village: str = ""
    land_size: str = ""
    farmer_category: str = ""
    major_crop: str = ""
    equipment_or_input: str = ""
    scheme_name: str = ""
    subsidy_percent: int = 0
    max_claim_inr: int = 0
    missing_criteria: list[str] = field(default_factory=list)
    required_documents: list[str] = field(default_factory=list)
    benefits: list[str] = field(default_factory=list)
    application_process: str = ""
    conversation_complete: bool = False
    goodbye_detected: bool = False
    next_question: str = ""
    voice_response: str = ""
    source_url: str = ""
    eligibility_status: str = "UNKNOWN"
    eligibility_confidence: float = 0.0
    eligibility_reasons: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    recommended_next_action: str = ""

    @staticmethod
    def _number(value: Any) -> int:
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def from_dict(cls, data: dict[str, Any], language: str = "") -> "AgentResult":
        missing = data.get("missing_criteria") or []
        if isinstance(missing, str):
            missing = [missing] if missing else []
        documents = data.get("required_documents") or []
        if isinstance(documents, str):
            documents = [documents] if documents else []
        benefits = data.get("benefits") or []
        if isinstance(benefits, str):
            benefits = [benefits] if benefits else []
        return cls(
            language=str(data.get("language") or language),
            name=str(data.get("name") or ""),
            mobile_number=str(data.get("mobile_number") or ""),
            state=str(data.get("state") or ""),
            district=str(data.get("district") or ""),
            village=str(data.get("village") or ""),
            land_size=str(data.get("land_size") or ""),
            farmer_category=str(data.get("farmer_category") or ""),
            major_crop=str(data.get("major_crop") or ""),
            equipment_or_input=str(data.get("equipment_or_input") or ""),
            scheme_name=str(data.get("scheme_name") or ""),
            subsidy_percent=cls._number(data.get("subsidy_percent")),
            max_claim_inr=cls._number(data.get("max_claim_inr")),
            missing_criteria=[str(item) for item in missing],
            required_documents=[str(item) for item in documents],
            benefits=[str(item) for item in benefits],
            application_process=str(data.get("application_process") or ""),
            conversation_complete=bool(data.get("conversation_complete", False)),
            goodbye_detected=bool(data.get("goodbye_detected", False)),
            next_question=str(data.get("next_question") or ""),
            voice_response=str(data.get("voice_response") or ""),
            source_url=str(data.get("source_url") or ""),
            eligibility_status=str(data.get("eligibility_status") or "UNKNOWN"),
            eligibility_confidence=float(data.get("eligibility_confidence") or 0),
            eligibility_reasons=[str(item) for item in (data.get("eligibility_reasons") or [])],
            missing_requirements=[str(item) for item in (data.get("missing_requirements") or [])],
            recommended_next_action=str(data.get("recommended_next_action") or ""),
        )


@dataclass
class ConversationState:
    id: str = field(default_factory=lambda: str(uuid4()))
    farmer_id: str = ""
    state: str = "IDLE"
    language_code: str = ""
    transcript: str = ""
    turns: list[dict[str, str]] = field(default_factory=list)
    result: AgentResult = field(default_factory=AgentResult)
    audio_hash: str = ""
    farmer_profile: dict[str, Any] = field(default_factory=dict)
    eligibility_status: str = ""
    goodbye_detected: bool = False
    listening_started: bool = False
    research_search_done: bool = False
    researched_url: str = ""
    research_context: str = ""
    official_urls: list[str] = field(default_factory=list)
    summary_persisted: bool = False
    research_query: str = ""

    def add_turn(self, role: str, text: str) -> None:
        self.turns.append({"role": role, "text": text})

    def set_state(self, state: str) -> None:
        if state not in CONVERSATION_STATES:
            raise ValueError(f"Unknown conversation state: {state}")
        self.state = state
