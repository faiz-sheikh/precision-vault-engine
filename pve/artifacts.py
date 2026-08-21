"""Content-addressed, path-confined artifact repository."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any


class UnsafeArtifactPath(ValueError): pass


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

    def checkpoint_raw(self, module_id: str, phase: str, artifact_id: str, raw: bytes) -> tuple[Path, str]:
        directory = self.confined(module_id, phase, artifact_id, "raw")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "response.bin"
        temporary = directory / f".{uuid.uuid4().hex}.tmp"
        with temporary.open("xb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        return path, hashlib.sha256(raw).hexdigest()

    def publish_metadata(self, module_id: str, phase: str, artifact_id: str, metadata: dict[str, Any]) -> Path:
        directory = self.confined(module_id, phase, artifact_id, "published")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "metadata.json"
        temporary = directory / f".{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(metadata, sort_keys=True, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        return path
