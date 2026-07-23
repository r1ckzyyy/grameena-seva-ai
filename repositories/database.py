"""SQLite connection and schema management."""

from __future__ import annotations

import sqlite3
import logging
import os
import re
from contextlib import contextmanager
from typing import Iterator
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS farmers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    phone_normalized TEXT NOT NULL DEFAULT '',
    district TEXT NOT NULL DEFAULT '',
    village TEXT NOT NULL DEFAULT '',
    mandal TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    land_size TEXT NOT NULL DEFAULT '',
    farmer_category TEXT NOT NULL DEFAULT '',
    soil_type TEXT NOT NULL DEFAULT '',
    irrigation_source TEXT NOT NULL DEFAULT '',
    current_crops TEXT NOT NULL DEFAULT '[]',
    previous_crops TEXT NOT NULL DEFAULT '[]',
    owned_equipment TEXT NOT NULL DEFAULT '[]',
    eligible_schemes TEXT NOT NULL DEFAULT '[]',
    recommendations TEXT NOT NULL DEFAULT '[]',
    conversation_summaries TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    farmer_id TEXT REFERENCES farmers(id) ON DELETE SET NULL,
    state TEXT NOT NULL DEFAULT 'IDLE',
    language_code TEXT NOT NULL DEFAULT '',
    snapshot TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schemes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    benefits TEXT NOT NULL DEFAULT '[]',
    eligibility TEXT NOT NULL DEFAULT '[]',
    required_documents TEXT NOT NULL DEFAULT '[]',
    application_process TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    official_url TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    last_updated TEXT NOT NULL,
    source_snapshot TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS research_cache (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    normalized_query TEXT NOT NULL UNIQUE,
    extracted_content TEXT NOT NULL DEFAULT '',
    official_source TEXT NOT NULL DEFAULT '',
    intent TEXT NOT NULL DEFAULT '',
    scheme_name TEXT NOT NULL DEFAULT '',
    retrieved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    source_snapshot TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_conversation_turns_conversation
    ON conversation_turns(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_research_cache_expiry
    ON research_cache(expires_at);
"""

logger = logging.getLogger("grameen_seva.database")


class PostgresConnection:
    """Small compatibility wrapper for the repository layer's SQLite-style SQL."""

    def __init__(self, connection) -> None:
        self.connection = connection

    @staticmethod
    def _sql(statement: str) -> str:
        return re.sub(r"\?", "%s", statement)

    def execute(self, statement: str, parameters=()):
        return self.connection.execute(self._sql(statement), parameters)

    def executemany(self, statement: str, parameters):
        return self.connection.executemany(self._sql(statement), parameters)


class PostgresDatabase:
    """PostgreSQL backend used when DATABASE_URL is configured for shared hosting."""

    def __init__(self, url: str) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary] to use DATABASE_URL.") from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.url = url
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[PostgresConnection]:
        connection = self._psycopg.connect(self.url, row_factory=self._dict_row)
        try:
            yield PostgresConnection(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        schema = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
        with self.connect() as connection:
            for statement in schema.split(";"):
                if statement.strip():
                    connection.execute(statement)
            self._migrate_columns(connection, "farmers", {
                "phone_normalized": "TEXT NOT NULL DEFAULT ''",
                "eligible_schemes": "TEXT NOT NULL DEFAULT '[]'",
                "recommendations": "TEXT NOT NULL DEFAULT '[]'",
                "conversation_summaries": "TEXT NOT NULL DEFAULT '[]'",
            })
            self._migrate_columns(connection, "conversations", {"snapshot": "TEXT NOT NULL DEFAULT ''"})
            self._migrate_columns(connection, "research_cache", {
                "intent": "TEXT NOT NULL DEFAULT ''",
                "scheme_name": "TEXT NOT NULL DEFAULT ''",
                "confidence": "REAL NOT NULL DEFAULT 0",
            })
            self._migrate_columns(connection, "schemes", {
                "confidence": "REAL NOT NULL DEFAULT 0",
                "required_documents": "TEXT NOT NULL DEFAULT '[]'",
                "application_process": "TEXT NOT NULL DEFAULT ''",
            })
            connection.execute("CREATE INDEX IF NOT EXISTS idx_farmers_phone_normalized ON farmers(phone_normalized) WHERE phone_normalized != ''")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_farmers_phone_normalized ON farmers(phone_normalized) WHERE phone_normalized != ''")

    @staticmethod
    def _migrate_columns(connection: PostgresConnection, table: str, columns_to_add: dict[str, str]) -> None:
        rows = connection.execute(
            "SELECT column_name AS name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
        existing = {row["name"] for row in rows}
        for column, definition in columns_to_add.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def create_database(path: str | Path, url: str = "") -> SQLiteDatabase | PostgresDatabase:
    """Create the configured shared database backend.

    DATABASE_URL takes precedence so Streamlit and Twilio can use the same
    Render PostgreSQL database. Without it, the original SQLite path remains.
    """
    database_url = (url or os.environ.get("DATABASE_URL", "")).strip()
    if database_url:
        return PostgresDatabase(database_url)
    return SQLiteDatabase(path)


class SQLiteDatabase:
    """Own SQLite connections and initialize the application schema."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open and reliably close a connection for one repository operation."""
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 30000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create missing tables and indexes without altering existing data."""
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_research_cache(connection)
            self._migrate_columns(connection, "farmers", {
                "phone_normalized": "TEXT NOT NULL DEFAULT ''",
                "eligible_schemes": "TEXT NOT NULL DEFAULT '[]'",
                "recommendations": "TEXT NOT NULL DEFAULT '[]'",
                "conversation_summaries": "TEXT NOT NULL DEFAULT '[]'",
            })
            self._migrate_columns(connection, "conversations", {
                "snapshot": "TEXT NOT NULL DEFAULT ''",
            })
            connection.execute("CREATE INDEX IF NOT EXISTS idx_farmers_phone_normalized ON farmers(phone_normalized) WHERE phone_normalized != ''")
            # A partial unique index is safe for clean databases. Older databases
            # may contain formatting duplicates; the repository's normalized lookup
            # still prevents new duplicates while preserving those legacy rows.
            try:
                connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_farmers_phone_normalized ON farmers(phone_normalized) WHERE phone_normalized != ''")
            except sqlite3.IntegrityError:
                logger.warning("Legacy farmer phone duplicates detected; preserving records and using normalized lookup safeguards")
            self._migrate_columns(connection, "schemes", {
                "confidence": "REAL NOT NULL DEFAULT 0",
                "required_documents": "TEXT NOT NULL DEFAULT '[]'",
                "application_process": "TEXT NOT NULL DEFAULT ''",
            })

    @staticmethod
    def _migrate_columns(connection: sqlite3.Connection, table: str, columns_to_add: dict[str, str]) -> None:
        existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        for column, definition in columns_to_add.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _migrate_research_cache(connection: sqlite3.Connection) -> None:
        """Add Knowledge Repository fields to databases created by earlier versions."""
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(research_cache)")}
        migrations = {
            "intent": "ALTER TABLE research_cache ADD COLUMN intent TEXT NOT NULL DEFAULT ''",
            "scheme_name": "ALTER TABLE research_cache ADD COLUMN scheme_name TEXT NOT NULL DEFAULT ''",
            "confidence": "ALTER TABLE research_cache ADD COLUMN confidence REAL NOT NULL DEFAULT 0",
        }
        for column, statement in migrations.items():
            if column not in columns:
                connection.execute(statement)
