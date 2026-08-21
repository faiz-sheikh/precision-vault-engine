"""Versioned candidate manifests and governed source records."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .identity import semantic_title, sha256_identity
from .store import Store, utcnow


class ManifestInvalid(ValueError): pass
class PromotionApprovalRequired(PermissionError): pass


def validate_envelope(envelope: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("schema_version", "manifest_id", "manifest_version", "source_set_id", "modules"):
        if key not in envelope: errors.append(f"missing {key}")
    modules = envelope.get("modules")
    if not isinstance(modules, list): return [*errors, "modules must be an array"]
    ids: set[str] = set(); semantic: dict[tuple[str, str, str], str] = {}
    for index, module in enumerate(modules):
        if not isinstance(module, dict): errors.append(f"modules[{index}] must be an object"); continue
        module_id = module.get("module_id")
        if not isinstance(module_id, str) or not module_id.startswith("mod_"):
            errors.append(f"modules[{index}] has invalid permanent module_id")
        elif module_id in ids: errors.append(f"duplicate module_id {module_id}")
        ids.add(module_id)
        key = (str(module.get("domain", "")), str(module.get("grade_or_sem", "")), semantic_title(str(module.get("title", ""))))
        if key in semantic: errors.append(f"semantic duplicate {semantic[key]} and {module_id}")
        semantic[key] = str(module_id)
    expected = sha256_identity("pve-manifest-content-v1", {k: v for k, v in envelope.items() if k != "checksum"})
    if envelope.get("checksum") not in (None, expected): errors.append("checksum mismatch")
    return errors


class ManifestGovernance:
    def __init__(self, store: Store): self.store = store

    def register_source(self, *, content: bytes, source_type: str, origin: str,
                        authority_group: str | None = None, status: str = "PENDING",
                        metadata: dict[str, Any] | None = None) -> str:
        content_hash = sha256_identity("source-content-v1", content.hex())
        source_id = "src_" + content_hash.split(":", 1)[1]
        with self.store.transaction() as db:
            db.execute("INSERT OR IGNORE INTO sources(source_id,content_hash,source_type,origin,authority_group,status,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (source_id, content_hash, source_type, origin, authority_group, status,
                        json.dumps(metadata or {}, sort_keys=True), utcnow()))
        return source_id

    def create_candidate(self, envelope: dict[str, Any]) -> tuple[str, list[str]]:
        body = dict(envelope)
        body["checksum"] = sha256_identity("pve-manifest-content-v1", {k: v for k, v in body.items() if k != "checksum"})
        errors = validate_envelope(body)
        candidate_id = "cand_" + uuid.uuid4().hex
        with self.store.transaction() as db:
            db.execute("INSERT INTO manifest_candidates VALUES(?,?,?,?,?,?)",
                       (candidate_id, json.dumps(body, sort_keys=True), body["checksum"],
                        json.dumps({"errors": errors}), "VALID" if not errors else "INVALID", utcnow()))
        return candidate_id, errors

    def promote(self, *args, **kwargs):
        raise PromotionApprovalRequired("manifest promotion requires fresh user approval")


def migrate_legacy_candidate(path: Path, source_set_id: str) -> dict[str, Any]:
    legacy = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(legacy, list): raise ManifestInvalid("legacy manifest must be an array")
    modules = []
    for item in legacy:
        if not isinstance(item, dict): continue
        copy = dict(item)
        legacy_id = str(copy.pop("id", ""))
        # Migration candidates propose deterministic continuity; they do not claim final authority.
        copy["module_id"] = "mod_" + sha256_identity("legacy-module-continuity-v1", legacy_id).split(":", 1)[1]
        copy["aliases"] = [legacy_id] if legacy_id else []
        modules.append(copy)
    return {"schema_version": "1.0.0", "manifest_id": "manifest_" + uuid.uuid4().hex,
            "manifest_version": 1, "source_set_id": source_set_id, "modules": modules}
