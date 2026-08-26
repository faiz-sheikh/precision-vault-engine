"""Offline model-quality benchmark definitions, validation, and scoring.

This module never constructs an adapter or performs provider I/O.  Live execution
must remain in the normal controller workflow under separate authorization.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .providers import normalize_finish_reason

SCORE_ANCHORS = {
    0: "unusable or fundamentally wrong",
    1: "major defects requiring replacement",
    2: "partially correct but materially deficient",
    3: "acceptable with limited corrections",
    4: "strong, correct, and useful",
    5: "excellent with no meaningful defect",
}

ROUTE_MODELS = {
    "ROUTINE_GENERATION": "nvidia/nemotron-3-super-120b-a12b",
    "QUALITY_REVIEW": "nvidia/nemotron-3-super-120b-a12b",
    "ENGINEERING_REASONING": "nvidia/nemotron-3-ultra-550b-a55b",
    "ENGINEERING_REVIEW": "nvidia/nemotron-3-ultra-550b-a55b",
}

RUBRICS = {
    "ROUTINE_GENERATION": (
        "instruction_adherence", "factual_correctness", "completeness",
        "educational_clarity", "concise_structure", "format_adherence",
        "terminology_accuracy", "unsupported_claim_avoidance",
        "appropriate_difficulty", "verbosity_control",
    ),
    "QUALITY_REVIEW": (
        "defect_detection_recall", "false_positive_control", "criticism_specificity",
        "fix_usefulness", "factual_vs_stylistic_distinction",
        "correct_content_preservation", "severity_calibration",
    ),
    "ENGINEERING_REASONING": (
        "technical_correctness", "reasoning_coherence", "assumption_quality",
        "engineering_consistency", "constraint_handling", "completeness",
        "final_answer_correctness", "format_adherence", "reasoning_efficiency",
        "unsupported_inference_control",
    ),
    "ENGINEERING_REVIEW": (
        "technical_error_detection", "edge_case_recognition",
        "safety_correctness_sensitivity", "invalid_assumption_detection",
        "severity_ranking", "correction_quality", "unnecessary_criticism_control",
    ),
}

HARD_FAILURES = (
    "malformed_required_structure", "materially_false_engineering_statement",
    "unsafe_technical_recommendation", "invented_source_or_citation",
    "task_not_answered", "supplied_constraint_contradicted",
    "mathematically_invalid_required_result",
)

CASE_NORMALIZED_THRESHOLD = 0.75
ROUTE_MEDIAN_THRESHOLD = 0.80
ROUTE_WORST_CASE_THRESHOLD = 0.70
MAX_UNSUPPORTED_CLAIM_RATE = 0.05


@dataclass(frozen=True)
class BenchmarkCase:
    benchmark_id: str
    capability: str
    prompt: str
    required_constraints: tuple[str, ...]
    expected_structure: dict[str, str]
    deterministic_checks: tuple[dict[str, Any], ...]
    reference_facts: tuple[str, ...]
    rubric_dimensions: tuple[str, ...]
    hard_fail_criteria: tuple[str, ...]
    output_token_ceiling: int
    estimated_calls: int
    provider: str
    model: str


@dataclass(frozen=True)
class CheckEvidence:
    check: str
    passed: bool
    observed: str


@dataclass(frozen=True)
class DeterministicResult:
    passed: bool
    evidence: tuple[CheckEvidence, ...]


@dataclass(frozen=True)
class CaseScore:
    benchmark_id: str
    normalized_score: float
    deterministic_passed: bool
    hard_failures: tuple[str, ...]
    unsupported_claim_rate: float

    @property
    def passed(self) -> bool:
        return (
            self.deterministic_passed
            and not self.hard_failures
            and self.normalized_score >= CASE_NORMALIZED_THRESHOLD
            and self.unsupported_claim_rate <= MAX_UNSUPPORTED_CLAIM_RATE
        )


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
        raise ValueError(f"{field} must be a non-empty string list")
    return tuple(value)


def parse_case(payload: dict[str, Any]) -> BenchmarkCase:
    required = {
        "benchmark_id", "capability", "prompt", "required_constraints",
        "expected_structure", "deterministic_checks", "reference_facts",
        "rubric_dimensions", "hard_fail_criteria", "output_token_ceiling",
        "estimated_calls", "provider", "model",
    }
    if set(payload) != required:
        raise ValueError(f"benchmark case fields differ: {sorted(set(payload) ^ required)}")
    capability = payload["capability"]
    if capability not in ROUTE_MODELS or payload["model"] != ROUTE_MODELS[capability]:
        raise ValueError("benchmark provider route mismatch")
    if payload["provider"] != "nvidia":
        raise ValueError("benchmark provider must be nvidia")
    if not isinstance(payload["expected_structure"], dict) or not payload["expected_structure"]:
        raise ValueError("expected_structure must define required fields")
    checks = payload["deterministic_checks"]
    if not isinstance(checks, list) or not checks or not all(isinstance(x, dict) for x in checks):
        raise ValueError("deterministic_checks must be a non-empty object list")
    facts = _strings(payload["reference_facts"], "reference_facts")
    dimensions = _strings(payload["rubric_dimensions"], "rubric_dimensions")
    if dimensions != RUBRICS[capability]:
        raise ValueError("case rubric does not match its route")
    hard_fails = _strings(payload["hard_fail_criteria"], "hard_fail_criteria")
    if not set(hard_fails) <= set(HARD_FAILURES):
        raise ValueError("unknown hard-fail criterion")
    if not isinstance(payload["output_token_ceiling"], int) or payload["output_token_ceiling"] < 1:
        raise ValueError("output_token_ceiling must be positive")
    if payload["estimated_calls"] != 1:
        raise ValueError("each initial benchmark case must reserve exactly one call")
    return BenchmarkCase(
        benchmark_id=str(payload["benchmark_id"]), capability=capability,
        prompt=str(payload["prompt"]),
        required_constraints=_strings(payload["required_constraints"], "required_constraints"),
        expected_structure={str(k): str(v) for k, v in payload["expected_structure"].items()},
        deterministic_checks=tuple(checks), reference_facts=facts,
        rubric_dimensions=dimensions, hard_fail_criteria=hard_fails,
        output_token_ceiling=payload["output_token_ceiling"],
        estimated_calls=payload["estimated_calls"], provider=payload["provider"],
        model=payload["model"],
    )


def load_cases(path: Path) -> tuple[BenchmarkCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("benchmark case file must be a non-empty list")
    cases = tuple(parse_case(item) for item in payload if isinstance(item, dict))
    if len(cases) != len(payload) or len({case.benchmark_id for case in cases}) != len(cases):
        raise ValueError("benchmark cases must be objects with unique IDs")
    return cases


def deterministic_validate(case: BenchmarkCase, output: object) -> DeterministicResult:
    evidence: list[CheckEvidence] = []
    is_object = isinstance(output, dict)
    evidence.append(CheckEvidence("json_object", is_object, type(output).__name__))
    if not is_object:
        return DeterministicResult(False, tuple(evidence))
    assert isinstance(output, dict)
    for key, kind in case.expected_structure.items():
        value = output.get(key)
        passed = (kind == "string" and isinstance(value, str) and bool(value.strip())) or (
            kind == "array" and isinstance(value, list)
        )
        evidence.append(CheckEvidence(f"required_field:{key}:{kind}", passed, type(value).__name__))
    searchable = json.dumps(output, sort_keys=True).casefold()
    for check in case.deterministic_checks:
        check_type = check.get("type")
        if check_type == "required_terms":
            terms = [str(x).casefold() for x in check.get("terms", [])]
            missing = [term for term in terms if term not in searchable]
            evidence.append(CheckEvidence("required_terms", not missing, "missing=" + ",".join(missing)))
        elif check_type == "prohibited_terms":
            terms = [str(x).casefold() for x in check.get("terms", [])]
            found = [term for term in terms if term in searchable]
            evidence.append(CheckEvidence("prohibited_terms", not found, "found=" + ",".join(found)))
        elif check_type == "max_words":
            count = len(searchable.split())
            limit = int(check["value"])
            evidence.append(CheckEvidence("max_words", count <= limit, f"{count}/{limit}"))
        elif check_type == "array_length":
            value = output.get(str(check["field"]))
            expected = int(check["value"])
            observed = len(value) if isinstance(value, list) else -1
            evidence.append(CheckEvidence("array_length", observed == expected, f"{observed}/{expected}"))
        else:
            raise ValueError(f"unsupported deterministic check: {check_type}")
    return DeterministicResult(all(item.passed for item in evidence), tuple(evidence))


def score_case(
    case: BenchmarkCase,
    deterministic: DeterministicResult,
    dimension_scores: dict[str, int],
    *,
    hard_failures: tuple[str, ...] = (),
    unsupported_claim_rate: float = 0.0,
) -> CaseScore:
    if set(dimension_scores) != set(case.rubric_dimensions):
        raise ValueError("all and only route rubric dimensions must be scored")
    if any(not isinstance(score, int) or score not in SCORE_ANCHORS for score in dimension_scores.values()):
        raise ValueError("rubric scores must be integers from 0 through 5")
    if not set(hard_failures) <= set(case.hard_fail_criteria):
        raise ValueError("hard failure is not allowed by this case")
    if not 0.0 <= unsupported_claim_rate <= 1.0:
        raise ValueError("unsupported_claim_rate must be between zero and one")
    normalized = statistics.fmean(dimension_scores.values()) / max(SCORE_ANCHORS)
    return CaseScore(case.benchmark_id, normalized, deterministic.passed, hard_failures, unsupported_claim_rate)


def route_passes(scores: tuple[CaseScore, ...]) -> bool:
    if not scores or any(not score.passed for score in scores):
        return False
    values = [score.normalized_score for score in scores]
    return statistics.median(values) >= ROUTE_MEDIAN_THRESHOLD and min(values) >= ROUTE_WORST_CASE_THRESHOLD


def review_form(case: BenchmarkCase) -> dict[str, Any]:
    return {
        "benchmark_id": case.benchmark_id,
        "dimension_scores": {dimension: None for dimension in case.rubric_dimensions},
        "dimension_evidence": {dimension: "" for dimension in case.rubric_dimensions},
        "hard_failures": [], "unsupported_claim_count": None, "total_claim_count": None,
        "scorer_comments": "", "scorer_id": "", "scored_at": "",
    }


def call_budget(
    cases: tuple[BenchmarkCase, ...], completed_case_ids: set[str] | None = None
) -> dict[str, Any]:
    completed = completed_case_ids or set()
    by_route = {route: 0 for route in ROUTE_MODELS}
    for case in cases:
        if case.benchmark_id not in completed:
            by_route[case.capability] += case.estimated_calls
    return {"by_route": by_route, "total": sum(by_route.values()), "automatic_retries": 0, "provider_calls_now": 0}


def score_chat_checkpoint(raw: bytes, *, required_field: str) -> dict[str, Any]:
    envelope = json.loads(raw)
    choices = envelope.get("choices") if isinstance(envelope, dict) else None
    choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
    message = choice.get("message") if isinstance(choice, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    try:
        parsed = json.loads(content) if isinstance(content, str) else None
    except json.JSONDecodeError:
        parsed = None
    checks = {
        "single_choice": isinstance(choice, dict),
        "complete_finish": isinstance(choice, dict) and normalize_finish_reason(choice.get("finish_reason")) == "COMPLETE",
        "content_present": isinstance(content, str) and bool(content.strip()),
        "json_object": isinstance(parsed, dict),
        "required_field": isinstance(parsed, dict) and isinstance(parsed.get(required_field), str) and bool(parsed[required_field].strip()),
    }
    return {
        "deterministic_passed": all(checks.values()), "checks": checks,
        "rubric_scored": False, "provider_calls": 0,
        "limitation": "simple canary supports structural checks, not representative route-quality scoring",
    }
