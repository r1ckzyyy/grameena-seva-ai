"""Construction of the application's repository boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from repositories.conversations import ConversationRepository
from repositories.database import SQLiteDatabase
from repositories.farmers import FarmerRepository
from repositories.research_cache import ResearchCacheRepository
from repositories.schemes import SchemeRepository


@dataclass
class RepositoryBundle:
    """All persistence capabilities used by application services."""

    database: SQLiteDatabase
    farmers: FarmerRepository
    conversations: ConversationRepository
    schemes: SchemeRepository
    research_cache: ResearchCacheRepository


def create_repositories(path: str | Path) -> RepositoryBundle:
    """Create one repository bundle backed by the supplied SQLite database."""
    database = SQLiteDatabase(path)
    return RepositoryBundle(
        database=database,
        farmers=FarmerRepository(database),
        conversations=ConversationRepository(database),
        schemes=SchemeRepository(database),
        research_cache=ResearchCacheRepository(database),
    )
