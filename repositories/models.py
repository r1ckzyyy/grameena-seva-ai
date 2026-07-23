"""Typed records exchanged by repositories."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FarmerRecord:
    id: str
    name: str = ""
    phone: str = ""
    district: str = ""
    village: str = ""
    mandal: str = ""
    state: str = ""
    land_size: str = ""
    farmer_category: str = ""
    soil_type: str = ""
    irrigation_source: str = ""
    current_crops: list[str] = field(default_factory=list)
    previous_crops: list[str] = field(default_factory=list)
    owned_equipment: list[str] = field(default_factory=list)
    eligible_schemes: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    conversation_summaries: list[str] = field(default_factory=list)


@dataclass
class SchemeRecord:
    id: str
    name: str
    official_url: str
    description: str = ""
    benefits: list[str] = field(default_factory=list)
    eligibility: list[str] = field(default_factory=list)
    required_documents: list[str] = field(default_factory=list)
    application_process: str = ""
    department: str = ""
    state: str = ""
    scope: str = ""
    source_snapshot: str = ""
    confidence: float = 0.0


@dataclass
class ResearchCacheRecord:
    id: str
    question: str
    normalized_query: str
    extracted_content: str
    official_source: str
    intent: str
    scheme_name: str
    retrieved_at: str
    expires_at: str
    source_snapshot: str = ""
    confidence: float = 0.0
