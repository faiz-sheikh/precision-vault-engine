"""Content-addressed, path-confined artifact repository."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from .identity import sha256_identity
from .store import Store, utcnow


class UnsafeArtifactPath(ValueError):
    pass


class ArtifactRepository:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def confined(self, *parts: str) -> Path:
        if any(not part or part in (".", "..") or "/" in part or "\\" in part for part in parts):
            raise UnsafeArtifactPath("artifact path components must be opaque names")
        candidate = self.root.joinpath(*parts)
        parent = candidate.parent.resolve()
        if self.root != parent and self.root not in parent.parents:
            raise UnsafeArtifactPath("artifact path escapes repository")
        for ancestor in [self.root, *[p for p in parent.parents if p != parent]]:
            if ancestor.exists() and ancestor.is_symlink():
                raise UnsafeArtifactPath("symlink ancestor rejected")
        return candidate

    def checkpoint_raw(
        self, module_id: str, phase: str, artifact_id: str, raw: bytes
    ) -> tuple[Path, str]:
        directory = self.confined(module_id, phase, artifact_id, "raw")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "response.bin"
        temporary = directory / f".{uuid.uuid4().hex}.tmp"
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return path, hashlib.sha256(raw).hexdigest()

    def publish_metadata(
        self, module_id: str, phase: str, artifact_id: str, metadata: dict[str, Any]
    ) -> Path:
        directory = self.confined(module_id, phase, artifact_id, "published")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "metadata.json"
        temporary = directory / f".{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(metadata, sort_keys=True, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        return path


class ArtifactRegistry:
    def __init__(self, store: Store):
        self.store = store

    def dependency_fingerprint(self, upstream_hashes: list[str], config: dict[str, Any]) -> str:
        return sha256_identity(
            "pve-artifact-dependency-v1", {"upstream": sorted(upstream_hashes), "config": config}
        )

    def register(
        self,
        *,
        module_id: str,
        phase: str,
        generation_id: str,
        dependency_fingerprint: str,
        content_hash: str,
        status: str = "SUCCEEDED",
        metadata_path: str | None = None,
    ) -> tuple[str, bool]:
        artifact_id = (
            "art_"
            + sha256_identity(
                "pve-artifact-id-v1",
                {"module_id": module_id, "phase": phase, "generation_id": generation_id},
            ).split(":", 1)[1]
        )
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT id FROM artifacts WHERE module_id=? AND phase=? AND dependency_fingerprint=?",
                (module_id, phase, dependency_fingerprint),
            ).fetchone()
            if row:
                return row[0], True
            db.execute(
                "INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    artifact_id,
                    module_id,
                    phase,
                    generation_id,
                    content_hash,
                    dependency_fingerprint,
                    status,
                    metadata_path,
                    utcnow(),
                ),
            )
        return artifact_id, False

    def stale_for_upstream(self, upstream_hash: str) -> list[str]:
        with self.store.connect() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT artifact_id FROM artifact_dependencies WHERE upstream_hash<>?",
                    (upstream_hash,),
                )
            ]
