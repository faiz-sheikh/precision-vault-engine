from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Prompt:
    prompt_id: str; version: int; hash: str; text: str; path: Path


def load_prompt(prompt_id: str, version: int = 1) -> Prompt:
    path = Path(__file__).parent / "prompts" / f"{prompt_id}_v{version}.txt"
    text = path.read_text(encoding="utf-8")
    return Prompt(prompt_id, version, "sha256:" + hashlib.sha256(text.encode()).hexdigest(), text, path)
