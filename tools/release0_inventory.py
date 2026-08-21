#!/usr/bin/env python3
"""Create deterministic, offline Release 0 inventories without mutating inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARTIFACT_ROOTS = (
    "vault", "vault_staging", "_assessment", "_audio", "_compiled",
    "_video_output", "_quarantine", "_raw_fallback", "raw_notes",
    "_classroom_downloads", "_handwriting_extracts",
)
STATE_FILES = (
    "config/embedding_registry.json", "config/hash_registry.json",
)
MANIFESTS = ("master_manifest.json", "config/master_manifest.json")
EXCLUDED_PARTS = {"venv", "node_modules", ".git", "_protected_credentials", "_migration_backups"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_record(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    result: dict[str, Any] = {"path": relative, "sha256": sha256(path)}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {**result, "valid_json": False, "error_type": type(exc).__name__}
    modules = value if isinstance(value, list) else value.get("modules") if isinstance(value, dict) else None
    result.update({
        "valid_json": True,
        "module_count": len(modules) if isinstance(modules, list) else None,
        "legacy_shape": "array" if isinstance(value, list) else type(value).__name__,
        "authority_status": "blocked_pending_source_governance_and_promotion",
    })
    if isinstance(modules, list):
        required = ("id", "title")
        result["entries_missing_identity"] = sum(
            1 for item in modules
            if not isinstance(item, dict) or any(not item.get(key) for key in required)
        )
        result["entries_with_source_provenance"] = sum(
            1 for item in modules
            if isinstance(item, dict) and any(item.get(k) for k in ("source", "sources", "provenance", "source_file"))
        )
    return result


def artifact_inventory(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for base_name in ARTIFACT_ROOTS:
        base = root / base_name
        if not base.exists():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            stat = path.stat()
            record = {
                "path": path.relative_to(root).as_posix(),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": sha256(path),
            }
            records.append(record)
            by_name[path.name.casefold()].append(record)
    collisions = []
    for normalized_name, items in sorted(by_name.items()):
        if len(items) > 1:
            collisions.append({
                "normalized_filename": normalized_name,
                "count": len(items),
                "distinct_content_count": len({item["sha256"] for item in items}),
                "items": items,
                "resolution": "manual_review",
            })
    return records, collisions


def batch_and_state_inventory(root: Path) -> dict[str, Any]:
    states = []
    for relative in STATE_FILES:
        path = root / relative
        if path.exists():
            states.append({"path": relative, "size": path.stat().st_size, "sha256": sha256(path)})
    batches = []
    batch_root = root / "_batch_jsonl"
    if batch_root.exists():
        for path in sorted(p for p in batch_root.rglob("*") if p.is_file()):
            line_count = None
            if path.suffix.casefold() == ".jsonl":
                with path.open("rb") as stream:
                    line_count = sum(1 for line in stream if line.strip())
            batches.append({
                "path": path.relative_to(root).as_posix(), "size": path.stat().st_size,
                "sha256": sha256(path), "request_line_count": line_count,
                "acceptance_status": "manual_review",
            })
    return {"state_files": states, "batch_files": batches, "active_batch_status": "manual_review" if batches else "none_discovered"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    artifacts, collisions = artifact_inventory(root)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "offline_only": True,
        "manifests": [manifest_record(root, path) for path in MANIFESTS],
        "authority_decision": "blocked_pending_source_registry_validation_and_fresh_promotion_approval",
        "artifact_summary": {"file_count": len(artifacts), "collision_group_count": len(collisions)},
        "artifacts": artifacts,
        "collisions": collisions,
        "operations": batch_and_state_inventory(root),
        "migration_map": {
            "schema_version": 1,
            "status": "skeleton",
            "rules": [],
            "ambiguous_items_policy": "quarantine_and_manual_review",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
