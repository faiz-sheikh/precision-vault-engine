"""Controller-owned DAG planning; execution is intentionally serial by default."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .artifacts import ArtifactRegistry
from .controller import Controller
from .providers import Adapter

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


PROVIDER_PHASES = {
    "research":"WEB_GROUNDED_RESEARCH", "synthesis":"ROUTINE_GENERATION",
    "quality":"QUALITY_REVIEW", "visual":"ROUTINE_GENERATION", "audio":"ROUTINE_GENERATION",
    "assessment":"ROUTINE_GENERATION", "viva":"ROUTINE_GENERATION",
}


class PipelineRunner:
    """Serial single-module runner. All provider work crosses Controller."""
    def __init__(self, controller: Controller, adapters: dict[str, Adapter]):
        self.controller=controller; self.adapters=adapters; self.registry=ArtifactRegistry(controller.store)

    def run_module(self, module_id: str, source_hash: str) -> dict[str, Any]:
        outputs: dict[str, Any]={}; upstream=[source_hash]; provider_calls=0
        for phase in PHASES:
            dependency=self.registry.dependency_fingerprint(upstream,{"phase":phase,"config":self.controller.settings.config_hash})
            capability=PROVIDER_PHASES.get(phase)
            if capability:
                adapter=self.adapters[capability]
                parsed,reused=self.controller.execute_provider(
                    capability=capability,request={"phase":phase,"module_id":module_id,"dependency":dependency},
                    prompt_hash=f"{phase}-v1",parameters={"temperature":0},schema={"type":"object"},
                    upstream_hashes=upstream,module_id=module_id,phase=phase,adapter=adapter,
                    parse=lambda raw:json.loads(raw),estimated_cost_usd=0.0)
                provider_calls += 0 if reused else 1; content=json.dumps(parsed,sort_keys=True).encode()
            else:
                content=json.dumps({"phase":phase,"module_id":module_id,"upstream":upstream},sort_keys=True).encode()
            content_hash="sha256:"+hashlib.sha256(content).hexdigest()
            artifact_id,reused_artifact=self.registry.register(module_id=module_id,phase=phase,
                generation_id=dependency,dependency_fingerprint=dependency,content_hash=content_hash)
            outputs[phase]={"artifact_id":artifact_id,"content_hash":content_hash,"reused":reused_artifact}
            upstream=[content_hash]
        outputs["provider_calls"]=provider_calls
        return outputs
