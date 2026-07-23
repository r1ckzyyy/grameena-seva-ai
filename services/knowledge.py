"""Knowledge retrieval, freshness, and official-source research orchestration."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Callable
from uuid import uuid4

from repositories.models import ResearchCacheRecord, SchemeRecord
from repositories.research_cache import ResearchCacheRepository
from repositories.schemes import SchemeRepository
from services.research import get_scheme_details as fetch_scheme_details
from services.research import search_schemes as search_official_schemes


class _InMemoryResearchRepository:
    """Compatibility repository for direct callers that do not inject persistence yet."""

    def __init__(self) -> None:
        self.records: dict[str, ResearchCacheRecord] = {}

    def get(self, normalized_query: str) -> ResearchCacheRecord | None:
        return next((item for item in self.records.values() if item.normalized_query == normalized_query), None)

    def list_recent(self, limit: int = 100) -> list[ResearchCacheRecord]:
        return list(self.records.values())[-limit:]

    def get_by_source(self, official_source: str) -> ResearchCacheRecord | None:
        return next((item for item in self.records.values() if item.official_source == official_source), None)

    def save(self, record: ResearchCacheRecord) -> None:
        self.records[record.id] = record


@dataclass
class KnowledgeLookup:
    """Structured result returned to the conversational tool layer."""

    knowledge_id: str
    normalized_question: str
    results: list[dict[str, str]]
    official_source: str = ""
    cached: bool = False


class KnowledgeService:
    """Decide whether official research is needed and persist structured knowledge."""

    _ELIGIBILITY_HINTS = ("eligible", "eligibility", "small farmer", "marginal", "land holding", "beneficiary")
    _DOCUMENT_HINTS = ("aadhaar", "aadhar", "land record", "bank", "passbook", "certificate", "document")
    _BENEFIT_HINTS = ("benefit", "subsidy", "assistance", "financial support", "amount")

    def __init__(
        self,
        repository: ResearchCacheRepository,
        *,
        scheme_repository: SchemeRepository | None = None,
        ttl_seconds: int = 7 * 24 * 60 * 60,
        fuzzy_threshold: float = 0.88,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.scheme_repository = scheme_repository
        self.ttl_seconds = max(60, ttl_seconds)
        self.fuzzy_threshold = min(max(fuzzy_threshold, 0.5), 1.0)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def in_memory(cls) -> "KnowledgeService":
        """Build a non-persistent service for legacy direct callers and tests."""
        return cls(_InMemoryResearchRepository())

    @staticmethod
    def normalize_question(question: str) -> str:
        """Normalize equivalent natural-language questions to a stable search key."""
        value = (question or "").casefold()
        value = re.sub(r"\b(i|we|want|need|please|tell|me|about|can|you|what|is|are)\b", " ", value)
        value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
        return " ".join(value.split())

    @staticmethod
    def infer_intent(question: str) -> str:
        """Classify the broad research intent without requiring an LLM call."""
        value = question.casefold()
        if any(term in value for term in ("eligible", "eligibility", "qualify", "qualification")):
            return "eligibility"
        return "scheme_discovery"

    @staticmethod
    def _extract_bullets(markdown: str, hints: tuple[str, ...]) -> list[str]:
        items: list[str] = []
        for line in (markdown or "").splitlines():
            stripped = line.strip(" -*\t")
            if not stripped or len(stripped) < 4:
                continue
            lower = stripped.casefold()
            if any(hint in lower for hint in hints) or line.strip().startswith(("-", "*")):
                cleaned = re.sub(r"^[-*\d.]+\s*", "", stripped)
                if cleaned and cleaned not in items:
                    items.append(cleaned[:240])
            if len(items) >= 8:
                break
        return items

    @staticmethod
    def _extract_description(markdown: str) -> str:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", markdown or "") if part.strip()]
        for paragraph in paragraphs[:6]:
            if len(paragraph) > 40 and not paragraph.startswith("#"):
                return paragraph[:800]
        return (paragraphs[0][:800] if paragraphs else "")

    def enrich_scheme_from_markdown(self, url: str, markdown: str, *, state: str = "", title: str = "") -> SchemeRecord | None:
        """Parse official markdown into a structured scheme record."""
        if not self.scheme_repository or not url:
            return None
        existing = self.scheme_repository.get_by_url(url)
        record = existing or SchemeRecord(
            id=str(uuid4()),
            name=title or "Government agricultural scheme",
            official_url=url,
        )
        if title:
            record.name = title
        description = self._extract_description(markdown)
        if description:
            record.description = description
        eligibility = self._extract_bullets(markdown, self._ELIGIBILITY_HINTS)
        if eligibility:
            record.eligibility = eligibility
        documents = self._extract_bullets(markdown, self._DOCUMENT_HINTS)
        if documents:
            record.required_documents = documents
        benefits = self._extract_bullets(markdown, self._BENEFIT_HINTS)
        if benefits:
            record.benefits = benefits
        process_paragraphs = [line.strip() for line in (markdown or "").splitlines()
                              if "apply" in line.casefold() or "application" in line.casefold()]
        if process_paragraphs:
            record.application_process = " ".join(process_paragraphs[:3])[:1000]
        if state:
            record.state = state
        if "central" in markdown.casefold() or "pm-" in url.casefold():
            record.scope = "Central"
        elif state:
            record.scope = "State"
        record.source_snapshot = markdown[:12000]
        record.confidence = 0.92 if eligibility or documents or benefits else 0.75
        self.scheme_repository.save(record)
        return record

    def _key(self, question: str, state: str) -> str:
        normalized = self.normalize_question(question)
        normalized_state = self.normalize_question(state)
        return f"{normalized} | state:{normalized_state}" if normalized_state else normalized

    def _valid(self, record: ResearchCacheRecord) -> bool:
        try:
            expires = datetime.fromisoformat(record.expires_at)
            return expires > self.clock()
        except (TypeError, ValueError):
            return False

    def _find(self, key: str) -> ResearchCacheRecord | None:
        exact = self.repository.get(key)
        if exact and self._valid(exact):
            return exact
        best: tuple[float, ResearchCacheRecord] | None = None
        for candidate in self.repository.list_recent():
            if not self._valid(candidate):
                continue
            score = SequenceMatcher(None, key, candidate.normalized_query).ratio()
            if score >= self.fuzzy_threshold and (best is None or score > best[0]):
                best = (score, candidate)
        return best[1] if best else None

    @staticmethod
    def _results(record: ResearchCacheRecord) -> list[dict[str, str]]:
        try:
            payload = json.loads(record.extracted_content)
            results = payload.get("results", []) if isinstance(payload, dict) else []
            return [dict(item) for item in results if isinstance(item, dict)]
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    def search(self, question: str, state: str, tavily_key: str) -> str:
        """Return cached knowledge or perform one official Tavily search."""
        key = self._key(question, state)
        cached = self._find(key)
        if cached:
            return json.dumps({
                "knowledge_id": cached.id,
                "normalized_question": cached.normalized_query,
                "intent": cached.intent,
                "scheme_name": cached.scheme_name,
                "official_source": cached.official_source,
                "cached": True,
                "results": self._results(cached),
            }, ensure_ascii=False)

        raw = search_official_schemes(question, state, tavily_key)
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {"results": []}
        results = [dict(item) for item in payload.get("results", []) if isinstance(item, dict)]
        source = str(results[0].get("url", "")) if results else ""
        title = str(results[0].get("title", "")) if results else ""
        now = self.clock()
        record = ResearchCacheRecord(
            id=str(uuid4()),
            question=question,
            normalized_query=key,
            extracted_content=json.dumps({"results": results}, ensure_ascii=False),
            official_source=source,
            intent=self.infer_intent(question),
            scheme_name=title,
            retrieved_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=self.ttl_seconds)).isoformat(),
            source_snapshot=raw,
            confidence=0.6 if results else 0.2,
        )
        self.repository.save(record)
        if self.scheme_repository and source:
            self.scheme_repository.save(SchemeRecord(
                id=record.id,
                name=title or "Government agricultural scheme",
                official_url=source,
                state=state,
                scope="State" if state else "Central",
                source_snapshot=record.source_snapshot,
                confidence=record.confidence,
            ))
        return json.dumps({
            "knowledge_id": record.id,
            "normalized_question": key,
            "intent": record.intent,
            "scheme_name": record.scheme_name,
            "official_source": source,
            "cached": False,
            "results": results,
        }, ensure_ascii=False)

    def get_scheme_details(self, url: str, firecrawl_key: str, *, state: str = "") -> str:
        """Return a cached official page or fetch and persist it once."""
        cached = self.repository.get_by_source(url)
        if cached and self._valid(cached) and cached.intent == "scheme_details" and len(cached.extracted_content) > 200:
            self.enrich_scheme_from_markdown(url, cached.extracted_content, state=state, title=cached.scheme_name)
            return cached.extracted_content

        markdown = fetch_scheme_details(url, firecrawl_key)
        now = self.clock()
        if cached:
            cached.extracted_content = markdown
            cached.source_snapshot = markdown
            cached.retrieved_at = now.isoformat()
            cached.expires_at = (now + timedelta(seconds=self.ttl_seconds)).isoformat()
            cached.confidence = max(cached.confidence, 0.9 if markdown else 0.3)
            cached.intent = "scheme_details"
            self.repository.save(cached)
        else:
            record = ResearchCacheRecord(
                id=str(uuid4()),
                question=url,
                normalized_query=self.normalize_question(url),
                extracted_content=markdown,
                official_source=url,
                intent="scheme_details",
                scheme_name="",
                retrieved_at=now.isoformat(),
                expires_at=(now + timedelta(seconds=self.ttl_seconds)).isoformat(),
                source_snapshot=markdown,
                confidence=0.9 if markdown else 0.3,
            )
            self.repository.save(record)
        scheme = self.enrich_scheme_from_markdown(url, markdown, state=state)
        if scheme and scheme.name:
            if cached:
                cached.scheme_name = scheme.name
                self.repository.save(cached)
        return markdown

    def scheme_for_url(self, url: str) -> SchemeRecord | None:
        """Return a structured scheme record when available."""
        if not self.scheme_repository or not url:
            return None
        return self.scheme_repository.get_by_url(url)
