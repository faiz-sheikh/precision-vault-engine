"""Controller-owned DAG planning; execution is intentionally serial by default."""

from __future__ import annotations

from dataclasses import dataclass

PHASES = ("sources", "curriculum", "research", "synthesis", "quality", "grounding",
          "visual", "audio", "assessment", "viva", "compilation", "render")
DEPENDENCIES = {
    "curriculum": ("sources",), "research": ("curriculum",), "synthesis": ("research",),
    "quality": ("synthesis",), "grounding": ("quality",), "visual": ("grounding",),
    "audio": ("grounding",), "assessment": ("grounding",), "viva": ("grounding",),
    "compilation": ("visual", "audio", "assessment", "viva"), "render": ("compilation",),
}


@dataclass(frozen=True)
class PlannedJob:
    module_id: str; phase: str; dependencies: tuple[str, ...]


def plan(module_ids: list[str], phases: tuple[str, ...] = PHASES) -> list[PlannedJob]:
    unknown = set(phases) - set(PHASES)
    if unknown: raise ValueError(f"unknown phases: {sorted(unknown)}")
    return [PlannedJob(module_id, phase, DEPENDENCIES.get(phase, ()))
            for module_id in module_ids for phase in PHASES if phase in phases]
