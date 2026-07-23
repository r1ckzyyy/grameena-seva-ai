"""Repository for cached official research results."""

from __future__ import annotations

from repositories.database import SQLiteDatabase
from repositories.models import ResearchCacheRecord


class ResearchCacheRepository:
    """Store complete research results keyed by normalized query."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def get(self, normalized_query: str) -> ResearchCacheRecord | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM research_cache WHERE normalized_query = ?", (normalized_query,)).fetchone()
        if row is None:
            return None
        return ResearchCacheRecord(
            id=row["id"], question=row["question"], normalized_query=row["normalized_query"],
            extracted_content=row["extracted_content"], official_source=row["official_source"],
            intent=row["intent"], scheme_name=row["scheme_name"], retrieved_at=row["retrieved_at"],
            expires_at=row["expires_at"], source_snapshot=row["source_snapshot"], confidence=float(row["confidence"] or 0),
        )

    def list_recent(self, limit: int = 100) -> list[ResearchCacheRecord]:
        """Return recent records for service-level fuzzy matching."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_cache ORDER BY retrieved_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            ResearchCacheRecord(
                id=row["id"], question=row["question"], normalized_query=row["normalized_query"],
                extracted_content=row["extracted_content"], official_source=row["official_source"],
                intent=row["intent"], scheme_name=row["scheme_name"], retrieved_at=row["retrieved_at"],
                expires_at=row["expires_at"], source_snapshot=row["source_snapshot"], confidence=float(row["confidence"] or 0),
            )
            for row in rows
        ]

    def get_by_source(self, official_source: str) -> ResearchCacheRecord | None:
        """Find the knowledge record associated with one official page."""
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM research_cache WHERE official_source = ?", (official_source,)).fetchone()
        if row is None:
            return None
        return ResearchCacheRecord(
            id=row["id"], question=row["question"], normalized_query=row["normalized_query"],
            extracted_content=row["extracted_content"], official_source=row["official_source"],
            intent=row["intent"], scheme_name=row["scheme_name"], retrieved_at=row["retrieved_at"],
            expires_at=row["expires_at"], source_snapshot=row["source_snapshot"], confidence=float(row["confidence"] or 0),
        )

    def save(self, record: ResearchCacheRecord) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO research_cache
                (id, question, normalized_query, extracted_content, official_source,
                 intent, scheme_name, retrieved_at, expires_at, source_snapshot, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(normalized_query) DO UPDATE SET question=excluded.question,
                extracted_content=excluded.extracted_content, official_source=excluded.official_source,
                intent=excluded.intent, scheme_name=excluded.scheme_name,
                retrieved_at=excluded.retrieved_at, expires_at=excluded.expires_at,
                source_snapshot=excluded.source_snapshot, confidence=excluded.confidence""",
                (record.id, record.question, record.normalized_query, record.extracted_content,
                 record.official_source, record.intent, record.scheme_name, record.retrieved_at,
                 record.expires_at, record.source_snapshot, record.confidence),
            )
