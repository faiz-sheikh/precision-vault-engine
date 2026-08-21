"""Fail-closed legacy state loading retained for migration safety."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StateMissing(RuntimeError): pass
class StateCorrupt(RuntimeError): pass


def load_json_state(path: Path, *, default_when_missing: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        if default_when_missing is not None:
            return default_when_missing.copy()
        raise StateMissing(f"critical state is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateCorrupt(f"critical state is unreadable: {path}: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise StateCorrupt(f"critical state must be a JSON object: {path}")
    return value
