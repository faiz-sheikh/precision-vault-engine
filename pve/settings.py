"""Validated, secret-free runtime configuration."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .identity import sha256_identity


@dataclass(frozen=True)
class ProviderRoute:
    provider: str
    model: str
    endpoint: str | None = None


@dataclass(frozen=True)
class Settings:
    state_dir: Path = Path(".pve")
    artifact_dir: Path = Path("artifacts")
    database_name: str = "operations.sqlite3"
    routes: dict[str, ProviderRoute] = field(
        default_factory=lambda: {
            "WEB_GROUNDED_RESEARCH": ProviderRoute("google", "gemini-3.7-flash"),
            "ROUTINE_GENERATION": ProviderRoute(
                "nvidia", "nvidia/nemotron-3-super-120b-a12b", "https://integrate.api.nvidia.com/v1"
            ),
            "QUALITY_REVIEW": ProviderRoute(
                "nvidia", "nvidia/nemotron-3-super-120b-a12b", "https://integrate.api.nvidia.com/v1"
            ),
            "ENGINEERING_REASONING": ProviderRoute(
                "nvidia", "nvidia/nemotron-3-ultra-550b-a55b", "https://integrate.api.nvidia.com/v1"
            ),
            "ENGINEERING_REVIEW": ProviderRoute(
                "nvidia", "nvidia/nemotron-3-ultra-550b-a55b", "https://integrate.api.nvidia.com/v1"
            ),
            "FRONTIER_ESCALATION": ProviderRoute("openai", "gpt-5.6-sol"),
        }
    )
    max_provider_attempts: int = 3
    max_regeneration_attempts: int = 1
    max_requests_per_run: int = 100
    max_cost_usd: float | None = None

    @property
    def database_path(self) -> Path:
        return self.state_dir / self.database_name

    @property
    def config_hash(self) -> str:
        payload = asdict(self)
        payload["state_dir"] = str(self.state_dir)
        payload["artifact_dir"] = str(self.artifact_dir)
        return sha256_identity("pve-run-config-v1", payload)

    @classmethod
    def from_env(cls, root: Path | None = None) -> Settings:
        base = (root or Path.cwd()).resolve()
        return cls(
            state_dir=Path(os.getenv("PVE_STATE_DIR", str(base / ".pve"))).resolve(),
            artifact_dir=Path(os.getenv("PVE_ARTIFACT_DIR", str(base / "artifacts"))).resolve(),
        )
