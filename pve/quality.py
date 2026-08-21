"""Deterministic quality, grounding, and remediation policy."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...]
    metrics: dict[str, float | int]


def validate_markdown(
    text: str, required_sections: tuple[str, ...] = ("Overview",)
) -> ValidationResult:
    errors: list[str] = []
    if not text.strip():
        errors.append("empty output")
    for section in required_sections:
        if not re.search(rf"(?im)^#+\s+{re.escape(section)}\s*$", text):
            errors.append(f"missing section: {section}")
    if text.count("$$") % 2:
        errors.append("unbalanced display math")
    if text.rstrip().endswith(("…", "...", "[truncated]")):
        errors.append("probable truncation")
    headings = re.findall(r"(?m)^#+\s+(.+)$", text)
    if len(headings) != len(set(h.casefold() for h in headings)):
        errors.append("duplicate headings")
    citations = len(re.findall(r"\[[^]]+\]\(https?://[^)]+\)", text))
    formulas = text.count("$$") // 2 + len(re.findall(r"(?<!\$)\$[^$]+\$(?!\$)", text))
    return ValidationResult(
        not errors,
        tuple(errors),
        {"characters": len(text), "citations": citations, "formulas": formulas},
    )


def grounding_map(claims: list[str], evidence: dict[str, str]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for claim in claims:
        tokens = {w for w in re.findall(r"[a-z0-9]+", claim.casefold()) if len(w) > 3}
        mapping[claim] = [
            eid
            for eid, text in evidence.items()
            if tokens
            and len(tokens & set(re.findall(r"[a-z0-9]+", text.casefold()))) / len(tokens) >= 0.5
        ]
    return mapping


def technical_risk(text: str) -> str:
    formulas = text.count("$$") // 2
    signals = sum(
        word in text.casefold()
        for word in ("derive", "proof", "jacobian", "eigenvalue", "stability", "tensor")
    )
    return "HIGH" if formulas >= 3 or signals >= 3 else "MEDIUM" if formulas or signals else "LOW"


def remediation(score: float, validation: ValidationResult) -> str:
    if not validation.valid:
        return "NOTE_REPAIR"
    if score < 0.4:
        return "PREREQUISITE_REVISION_AND_TARGETED_PRACTICE"
    if score < 0.7:
        return "TARGETED_EXAMPLES_AND_FLASHCARDS"
    return "NO_ACTION"
