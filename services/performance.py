"""Lightweight latency profiling for conversation pipeline stages."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

logger = logging.getLogger("grameen_seva.performance")


@dataclass
class StageTiming:
    """One measured pipeline stage."""

    name: str
    duration_ms: float


@dataclass
class PerformanceReport:
    """Aggregated timings for one conversation turn."""

    stages: list[StageTiming] = field(default_factory=list)

    def add(self, name: str, duration_ms: float) -> None:
        self.stages.append(StageTiming(name=name, duration_ms=duration_ms))
        logger.info("stage=%s duration_ms=%.1f", name, duration_ms)

    @property
    def total_ms(self) -> float:
        return sum(stage.duration_ms for stage in self.stages)

    def summary(self) -> dict[str, float]:
        return {stage.name: stage.duration_ms for stage in self.stages}


class PerformanceTracker:
    """Collect stage timings for one turn."""

    def __init__(self) -> None:
        self.report = PerformanceReport()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.report.add(name, (time.perf_counter() - started) * 1000)


def configure_logging() -> None:
    """Configure application logging once."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
