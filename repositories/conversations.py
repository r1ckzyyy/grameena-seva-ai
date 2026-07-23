"""Repository for conversation sessions and turns."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from dataclasses import asdict

from models.conversation import AgentResult, ConversationState
from repositories.database import SQLiteDatabase


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationRepository:
    """Persist conversation state without exposing SQL to services."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def save(self, conversation: ConversationState, farmer_id: str | None = None) -> None:
        timestamp = _now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO conversations (id, farmer_id, state, language_code, snapshot, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET farmer_id=excluded.farmer_id,
                state=excluded.state, language_code=excluded.language_code, snapshot=excluded.snapshot, updated_at=excluded.updated_at""",
                (conversation.id, farmer_id, conversation.state, conversation.language_code, json.dumps(asdict(conversation), ensure_ascii=False), timestamp, timestamp),
            )
            connection.execute("DELETE FROM conversation_turns WHERE conversation_id = ?", (conversation.id,))
            connection.executemany(
                "INSERT INTO conversation_turns (conversation_id, role, text, created_at) VALUES (?, ?, ?, ?)",
                [(conversation.id, turn["role"], turn["text"], timestamp) for turn in conversation.turns[-40:]],
            )

    def load_for_farmer(self, farmer_id: str) -> ConversationState | None:
        """Load the most recent conversation for a returning farmer."""
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT id, farmer_id, state, language_code, snapshot
                FROM conversations WHERE farmer_id = ? ORDER BY updated_at DESC LIMIT 1""",
                (farmer_id,),
            ).fetchone()
            if row is None:
                return None
            turns = connection.execute(
                """SELECT role, text FROM conversation_turns
                WHERE conversation_id = ? ORDER BY id ASC LIMIT 40""",
                (row["id"],),
            ).fetchall()
        snapshot = row["snapshot"] or ""
        if snapshot:
            try:
                data = json.loads(snapshot)
                result = AgentResult.from_dict(data.get("result") or {})
                turns = data.get("turns", [])
                if not isinstance(turns, list):
                    turns = []
                turns = [
                    {"role": str(turn.get("role", "")), "text": str(turn.get("text", ""))}
                    for turn in turns if isinstance(turn, dict) and turn.get("role") in {"farmer", "assistant"}
                ][-40:]
                conversation = ConversationState(
                    id=data.get("id", row["id"]), farmer_id=farmer_id,
                    state=data.get("state", row["state"] or "IDLE"), language_code=data.get("language_code", row["language_code"] or ""),
                    transcript=str(data.get("transcript", "")), turns=turns, result=result,
                    audio_hash=data.get("audio_hash", ""), farmer_profile=data.get("farmer_profile", {}),
                    eligibility_status=data.get("eligibility_status", ""), goodbye_detected=bool(data.get("goodbye_detected", False)),
                    listening_started=bool(data.get("listening_started", False)), research_search_done=bool(data.get("research_search_done", False)),
                    researched_url=data.get("researched_url", ""), research_context=data.get("research_context", ""),
                    official_urls=data.get("official_urls", []), summary_persisted=bool(data.get("summary_persisted", False)),
                    research_query=data.get("research_query", ""),
                )
                return conversation
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        conversation = ConversationState(
            id=row["id"],
            farmer_id=row["farmer_id"] or farmer_id,
            state=row["state"] or "IDLE",
            language_code=row["language_code"] or "",
        )
        conversation.turns = [{"role": turn["role"], "text": turn["text"]} for turn in turns]
        return conversation

    def delete_for_farmer(self, farmer_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute("DELETE FROM conversations WHERE farmer_id = ?", (farmer_id,))
