"""Boru hatti: durum, varliklar ve orkestrasyon.

`Orchestrator` tembel dislanir (PEP 562): araclar `pipeline.models` icindeki
varliklari kullanir, orkestrator ise araclara bagimlidir. Hemen ice aktarmak
tools -> pipeline -> agents -> tools dongusunu olusturur.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import (
    Artifact,
    Decision,
    Gap,
    Phase,
    PhaseState,
    Priority,
    Requirement,
    ResearchNote,
    Severity,
    Status,
    Task,
)
from .state import ProjectState

if TYPE_CHECKING:  # pragma: no cover
    from .orchestrator import PHASE_ROLE, Orchestrator, PhaseResult, RunReport

_LAZY = {"Orchestrator", "PhaseResult", "RunReport", "PHASE_ROLE"}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        from . import orchestrator

        return getattr(orchestrator, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__))


__all__ = [
    "PHASE_ROLE",
    "Artifact",
    "Decision",
    "Gap",
    "Orchestrator",
    "Phase",
    "PhaseResult",
    "PhaseState",
    "Priority",
    "ProjectState",
    "Requirement",
    "ResearchNote",
    "RunReport",
    "Severity",
    "Status",
    "Task",
]
