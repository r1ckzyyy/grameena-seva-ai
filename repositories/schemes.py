"""Repository for structured government schemes."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from repositories.database import SQLiteDatabase
from repositories.models import SchemeRecord


class SchemeRepository:
    """Store and retrieve structured schemes by identity or official URL."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def save(self, scheme: SchemeRecord) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO schemes
                (id, name, description, benefits, eligibility, required_documents, application_process,
                 department, official_url, state, scope,
                 last_updated, source_snapshot, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description,
                benefits=excluded.benefits, eligibility=excluded.eligibility,
                required_documents=excluded.required_documents, application_process=excluded.application_process,
                department=excluded.department, official_url=excluded.official_url, state=excluded.state,
                scope=excluded.scope, last_updated=excluded.last_updated, source_snapshot=excluded.source_snapshot,
                confidence=excluded.confidence""",
                (scheme.id, scheme.name, scheme.description, json.dumps(scheme.benefits),
                 json.dumps(scheme.eligibility), json.dumps(scheme.required_documents), scheme.application_process,
                 scheme.department, scheme.official_url, scheme.state, scheme.scope, timestamp,
                 scheme.source_snapshot, scheme.confidence),
            )

    def get_by_url(self, official_url: str) -> SchemeRecord | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM schemes WHERE official_url = ?", (official_url,)).fetchone()
        if row is None:
            return None
        return SchemeRecord(
            id=row["id"], name=row["name"], official_url=row["official_url"], description=row["description"],
            benefits=json.loads(row["benefits"]), eligibility=json.loads(row["eligibility"]),
            required_documents=json.loads(row["required_documents"]), application_process=row["application_process"],
            department=row["department"], state=row["state"], scope=row["scope"], source_snapshot=row["source_snapshot"],
            confidence=float(row["confidence"] or 0),
        )
