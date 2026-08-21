"""Canonical identities used by the hardened skeleton and controller."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_identity(namespace: str, value: Any) -> str:
    digest = hashlib.sha256(namespace.encode("utf-8") + b"\0" + canonical_json(value))
    return f"sha256:{digest.hexdigest()}"


def semantic_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().replace("&", " and ")
    text = re.sub(r"\b(?:a|an|the|and|of|for|in|with)\b", " ", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def permanent_module_id() -> str:
    """Generate an opaque immutable ID; titles and revisions never participate."""
    import uuid

    return f"mod_{uuid.uuid4().hex}"


def provider_fingerprint(
    *,
    provider: str,
    model: str,
    request: Any,
    prompt_hash: str,
    parameters: Any,
    schema: Any,
    run_config_hash: str,
    upstream_hashes: list[str],
) -> str:
    return sha256_identity(
        "pve-provider-operation-v1",
        {
            "provider": provider,
            "model": model,
            "request": request,
            "prompt_hash": prompt_hash,
            "parameters": parameters,
            "schema": schema,
            "run_config_hash": run_config_hash,
            "upstream_hashes": sorted(upstream_hashes),
        },
    )
