"""Deterministic scheme eligibility evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from models.conversation import AgentResult
from repositories.models import FarmerRecord, SchemeRecord


@dataclass
class EligibilityDecision:
    """Explainable eligibility result independent of Gemini."""

    status: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    recommended_next_action: str = ""


class EligibilityService:
    """Evaluate explicit scheme requirements using deterministic checks."""

    _LAND_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(acre|acres|hectare|hectares|ha)\b", re.I)
    _STATE_RE = re.compile(r"(?:state|in)\s*[:=-]?\s*([a-z ]+)", re.I)
    _SMALL_RE = re.compile(r"\b(small|marginal)\b", re.I)

    @staticmethod
    def _land_acres(value: str) -> float | None:
        match = EligibilityService._LAND_RE.search(value or "")
        if not match:
            return None
        amount = float(match.group(1))
        unit = match.group(2).lower()
        if unit.startswith("hect") or unit == "ha":
            return amount * 2.471
        return amount

    def evaluate(
        self,
        farmer: FarmerRecord,
        result: AgentResult,
        *,
        scheme: SchemeRecord | None = None,
    ) -> EligibilityDecision:
        reasons: list[str] = []
        missing_requirements: list[str] = []
        state = farmer.state or result.state
        district = farmer.district or result.district
        land_size = farmer.land_size or result.land_size
        category = farmer.farmer_category or result.farmer_category

        if not state:
            missing_requirements.append("State")
        if not district:
            missing_requirements.append("District")

        requirements = list(scheme.eligibility if scheme else [])

        for requirement in requirements:
            state_match = self._STATE_RE.search(requirement)
            if state_match and state:
                required_state = state_match.group(1).strip().casefold()
                if required_state and required_state not in state.casefold():
                    reasons.append(f"State requirement not met: {requirement}")
                    return EligibilityDecision(
                        "NOT_ELIGIBLE",
                        0.9,
                        reasons,
                        [requirement],
                        "This scheme appears limited to another state.",
                    )

            max_land = re.search(r"(?:upto|up to|maximum|max\.?|less than)\s*(\d+(?:\.\d+)?)\s*(acre|acres|hectare|hectares|ha)", requirement, re.I)
            if max_land and land_size:
                farmer_acres = self._land_acres(land_size)
                limit = float(max_land.group(1))
                limit_unit = max_land.group(2).lower()
                limit_acres = limit * 2.471 if limit_unit.startswith("hect") or limit_unit == "ha" else limit
                if farmer_acres is not None and farmer_acres > limit_acres:
                    reasons.append(f"Land size exceeds scheme limit ({requirement}).")
                    return EligibilityDecision(
                        "NOT_ELIGIBLE",
                        0.88,
                        reasons,
                        [requirement],
                        "Check smaller-farmer or alternate schemes.",
                    )
            elif max_land and not land_size:
                missing_requirements.append("Land size")

            if self._SMALL_RE.search(requirement) and category:
                if not self._SMALL_RE.search(category):
                    reasons.append("Farmer category does not match small/marginal requirement.")
                    return EligibilityDecision(
                        "NOT_ELIGIBLE",
                        0.9,
                        reasons,
                        [requirement],
                        "Verify category with the local agriculture office.",
                    )
            elif self._SMALL_RE.search(requirement) and not category:
                missing_requirements.append("Farmer category")

        if missing_requirements:
            unique = list(dict.fromkeys(missing_requirements))
            return EligibilityDecision(
                "INSUFFICIENT_INFORMATION",
                0.55,
                ["Some profile details are still needed for a reliable decision."],
                unique,
                f"Provide: {', '.join(unique)}.",
            )

        if scheme and requirements:
            reasons.append("All known deterministic scheme requirements are satisfied.")
            confidence = 0.85
        elif result.scheme_name and state:
            reasons.append("Enough farmer details are available for a preliminary eligibility check.")
            confidence = 0.7
        else:
            reasons.append("Basic farmer profile is available; official scheme rules were not fully structured.")
            confidence = 0.6

        return EligibilityDecision(
            "ELIGIBLE",
            confidence,
            reasons,
            [],
            "Review the official scheme information for the next steps.",
        )
