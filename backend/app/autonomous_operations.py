"""Deterministic research-only orchestration; no publishing or broker capability."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

STAGES = ("lightweight_health", "refresh_planning", "incremental_refresh", "coverage_validation",
          "model_readiness", "research_scoring_evidence", "month_end_shadow_planning",
          "matured_shadow_evaluation")


@dataclass(frozen=True)
class StageResult:
    status: str
    detail: dict[str, Any]


class ResearchOrchestrator:
    """Runs a fixed stage sequence and stops at the first non-success result."""
    def __init__(self, stages: dict[str, Callable[[], dict[str, Any]]]):
        unknown = set(stages) - set(STAGES)
        if unknown:
            raise ValueError("unknown orchestration stages")
        self.stages = stages

    def run(self) -> dict[str, Any]:
        completed: list[str] = []
        for name in STAGES:
            operation = self.stages.get(name)
            if operation is None:
                return {"status": "blocked", "failed_stage": name, "completed_stages": completed}
            try:
                result = operation()
            except Exception:
                return {"status": "blocked", "failed_stage": name, "failure_class": "stage_exception",
                        "completed_stages": completed}
            if result.get("status") not in {"ok", "completed", "ready", "no_work"}:
                return {"status": "blocked", "failed_stage": name, "failure_class": "gate_not_confirmed",
                        "completed_stages": completed}
            completed.append(name)
        return {"status": "completed", "completed_stages": completed,
                "shadow_creation_authorized": False, "production_publication_available": False}
