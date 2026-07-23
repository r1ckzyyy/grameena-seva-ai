"""Repository for persistent farmer profiles."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from repositories.database import SQLiteDatabase
from repositories.models import FarmerRecord


logger = logging.getLogger("grameen_seva.farmers")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_phone(phone: str) -> str:
    """Normalize an Indian mobile number to its ten-digit identity key."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    return digits if len(digits) == 10 else ""


_SPOKEN_DIGITS = {
    "zero": "0", "oh": "0", "o": "0",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
    # Common Hindi forms, which Sarvam may preserve in a Hindi transcript.
    "शून्य": "0", "जीरो": "0", "एक": "1", "दो": "2", "तीन": "3", "चार": "4",
    "पांच": "5", "पाँच": "5", "छह": "6", "छः": "6", "सात": "7", "आठ": "8", "नौ": "9",
}


def normalize_spoken_phone(transcript: str) -> str:
    """Extract a ten-digit Indian mobile identity from spoken-number text.

    Sarvam commonly returns either digits or English number words. The caller
    identity path still uses ``normalize_phone``; this helper is for browser
    microphone onboarding only.
    """
    text = str(transcript or "").strip().lower()
    direct = normalize_phone(text)
    if direct:
        return direct
    tokens = re.findall(r"[\u0900-\u097f]+|[a-z]+|\d", text)
    digits: list[str] = []
    for token in tokens:
        if token in _SPOKEN_DIGITS:
            digits.append(_SPOKEN_DIGITS[token])
        elif token.isdigit():
            digits.append(token)
    candidate = "".join(digits)
    if candidate.startswith("91") and len(candidate) == 12:
        candidate = candidate[2:]
    return candidate if len(candidate) == 10 else ""


class FarmerRepository:
    """Persist and retrieve farmer profile records."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def save(self, farmer: FarmerRecord) -> None:
        timestamp = _now()
        normalized_phone = normalize_phone(farmer.phone)
        farmer.phone = normalized_phone or farmer.phone
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO farmers
                (id, name, phone, phone_normalized, district, village, mandal, state, land_size,
                 farmer_category, soil_type, irrigation_source, current_crops,
                 previous_crops, owned_equipment, eligible_schemes, recommendations,
                 conversation_summaries, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, phone=excluded.phone, phone_normalized=excluded.phone_normalized, district=excluded.district,
                 village=excluded.village, mandal=excluded.mandal, state=excluded.state,
                 land_size=excluded.land_size, farmer_category=excluded.farmer_category,
                 soil_type=excluded.soil_type, irrigation_source=excluded.irrigation_source,
                 current_crops=excluded.current_crops, previous_crops=excluded.previous_crops,
                 owned_equipment=excluded.owned_equipment, eligible_schemes=excluded.eligible_schemes,
                 recommendations=excluded.recommendations, conversation_summaries=excluded.conversation_summaries,
                 updated_at=excluded.updated_at""",
                (farmer.id, farmer.name, farmer.phone, normalized_phone, farmer.district, farmer.village,
                 farmer.mandal, farmer.state, farmer.land_size, farmer.farmer_category,
                 farmer.soil_type, farmer.irrigation_source, json.dumps(farmer.current_crops),
                 json.dumps(farmer.previous_crops), json.dumps(farmer.owned_equipment),
                 json.dumps(farmer.eligible_schemes), json.dumps(farmer.recommendations),
                 json.dumps(farmer.conversation_summaries), timestamp, timestamp),
            )

    def find_by_phone(self, phone: str) -> FarmerRecord | None:
        """Find a farmer by the normalized phone identity."""
        normalized = normalize_phone(phone)
        if not normalized:
            return None
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM farmers WHERE phone_normalized = ? ORDER BY updated_at DESC LIMIT 1",
                (normalized,),
            ).fetchone()
            if row is not None:
                return self._row_to_record(row)
            # One-time compatibility fallback for records created before the
            # normalized column existed; repair the row after finding it.
            rows = connection.execute("SELECT * FROM farmers WHERE phone != '' ORDER BY updated_at DESC").fetchall()
        for row in rows:
            stored = normalize_phone(row["phone"])
            if stored == normalized:
                with self.database.connect() as repair:
                    repair.execute("UPDATE farmers SET phone_normalized = ?, phone = ? WHERE id = ? AND phone_normalized = ''", (normalized, normalized, row["id"]))
                return self._row_to_record(row)
        return None

    def get(self, farmer_id: str) -> FarmerRecord | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM farmers WHERE id = ?", (farmer_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    @staticmethod
    def _row_to_record(row) -> FarmerRecord:
        def text(column: str, default: str = "") -> str:
            try:
                return str(row[column] or default)
            except (IndexError, KeyError):
                return default

        def list_value(column: str) -> list[str]:
            try:
                value = json.loads(row[column] or "[]")
                return [str(item) for item in value] if isinstance(value, list) else []
            except (TypeError, ValueError, json.JSONDecodeError, IndexError, KeyError):
                logger.warning("Ignoring malformed farmer field: %s", column)
                return []

        return FarmerRecord(
            id=text("id"), name=text("name"),
            phone=text("phone") or text("phone_normalized"),
            district=text("district"), village=text("village"), mandal=text("mandal"),
            state=text("state"), land_size=text("land_size"), farmer_category=text("farmer_category"),
            soil_type=text("soil_type"), irrigation_source=text("irrigation_source"),
            current_crops=list_value("current_crops"), previous_crops=list_value("previous_crops"),
            owned_equipment=list_value("owned_equipment"), eligible_schemes=list_value("eligible_schemes"),
            recommendations=list_value("recommendations"), conversation_summaries=list_value("conversation_summaries"),
        )

    def delete(self, farmer_id: str) -> None:
        """Delete one farmer profile after dependent records are removed."""
        with self.database.connect() as connection:
            connection.execute("DELETE FROM farmers WHERE id = ?", (farmer_id,))
