"""Offline model-quality benchmark definitions, validation, and scoring.

This module never constructs an adapter or performs provider I/O.  Live execution
must remain in the normal controller workflow under separate authorization.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .identity import provider_fingerprint, sha256_identity
from .providers import normalize_finish_reason
from .settings import Settings

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
        "instruction_adherence",
        "factual_correctness",
        "completeness",
        "educational_clarity",
        "concise_structure",
        "format_adherence",
        "terminology_accuracy",
        "unsupported_claim_avoidance",
        "appropriate_difficulty",
        "verbosity_control",
    ),
    "QUALITY_REVIEW": (
        "defect_detection_recall",
        "false_positive_control",
        "criticism_specificity",
        "fix_usefulness",
        "factual_vs_stylistic_distinction",
        "correct_content_preservation",
        "severity_calibration",
    ),
    "ENGINEERING_REASONING": (
        "technical_correctness",
        "reasoning_coherence",
        "assumption_quality",
        "engineering_consistency",
        "constraint_handling",
        "completeness",
        "final_answer_correctness",
        "format_adherence",
        "reasoning_efficiency",
        "unsupported_inference_control",
    ),
    "ENGINEERING_REVIEW": (
        "technical_error_detection",
        "edge_case_recognition",
        "safety_correctness_sensitivity",
        "invalid_assumption_detection",
        "severity_ranking",
        "correction_quality",
        "unnecessary_criticism_control",
    ),
}

HARD_FAILURES = (
    "malformed_required_structure",
    "materially_false_engineering_statement",
    "unsafe_technical_recommendation",
    "invented_source_or_citation",
    "task_not_answered",
    "supplied_constraint_contradicted",
    "mathematically_invalid_required_result",
)

CASE_NORMALIZED_THRESHOLD = 0.75
ROUTE_MEDIAN_THRESHOLD = 0.80
ROUTE_WORST_CASE_THRESHOLD = 0.70
MAX_UNSUPPORTED_CLAIM_RATE = 0.05
VALIDATION_CONTRACT = "pve-quality-benchmark-validation-v1"
PROMPT_HASH_NAMESPACE = "pve-benchmark-prompt-v1"


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


@dataclass(frozen=True)
class CanonicalBenchmarkOperation:
    benchmark_version: str
    case_id: str
    capability: str
    provider: str
    model: str
    request: dict[str, Any]
    prompt_hash: str
    response_schema: dict[str, Any]
    parameters: dict[str, Any]
    upstream_hashes: tuple[str, ...]
    runtime_config_hash: str
    validation_contract: str
    fingerprint: str
    operation_id: str


@dataclass(frozen=True)
class BenchmarkCasePlan:
    operation: CanonicalBenchmarkOperation
    durable_state: str | None
    already_completed: bool
    safe_reuse: bool
    planned_calls: int
    blocked_reason: str | None = None


def benchmark_config_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _response_schema(case: BenchmarkCase) -> dict[str, Any]:
    properties = {name: {"type": kind} for name, kind in sorted(case.expected_structure.items())}
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
    }


def canonical_benchmark_operation(
    case: BenchmarkCase, *, benchmark_version: str, settings: Settings
) -> CanonicalBenchmarkOperation:
    """Derive every response-affecting input without consulting execution order or time."""
    schema = _response_schema(case)
    messages = ({"role": "user", "content": case.prompt},)
    prompt_hash = sha256_identity(
        PROMPT_HASH_NAMESPACE,
        {
            "benchmark_version": benchmark_version,
            "case_id": case.benchmark_id,
            "messages": messages,
        },
    )
    response_format = {"type": "json_object"}
    parameters = {
        "temperature": 0,
        "top_p": 1,
        "max_output_tokens": case.output_token_ceiling,
        "response_format": response_format,
        "validation_contract": VALIDATION_CONTRACT,
    }
    request = {
        "model": case.model,
        "messages": list(messages),
        "temperature": 0,
        "top_p": 1,
        "max_tokens": case.output_token_ceiling,
        "response_format": response_format,
    }
    upstream_hashes = tuple(
        sorted(
            (
                sha256_identity("pve-benchmark-reference-facts-v1", case.reference_facts),
                sha256_identity("pve-benchmark-constraints-v1", case.required_constraints),
                sha256_identity("pve-benchmark-deterministic-checks-v1", case.deterministic_checks),
            )
        )
    )
    fingerprint = provider_fingerprint(
        provider=case.provider,
        model=case.model,
        request=request,
        prompt_hash=prompt_hash,
        parameters=parameters,
        schema=schema,
        run_config_hash=settings.config_hash,
        upstream_hashes=list(upstream_hashes),
    )
    return CanonicalBenchmarkOperation(
        benchmark_version=benchmark_version,
        case_id=case.benchmark_id,
        capability=case.capability,
        provider=case.provider,
        model=case.model,
        request=request,
        prompt_hash=prompt_hash,
        response_schema=schema,
        parameters=parameters,
        upstream_hashes=upstream_hashes,
        runtime_config_hash=settings.config_hash,
        validation_contract=VALIDATION_CONTRACT,
        fingerprint=fingerprint,
        operation_id="op_" + fingerprint.split(":", 1)[1],
    )


def plan_benchmark_case(
    operation: CanonicalBenchmarkOperation, database_path: Path
) -> BenchmarkCasePlan:
    """Use only durable operation evidence; caller-supplied completion lists are irrelevant."""
    if not database_path.is_file():
        return BenchmarkCasePlan(operation, None, False, False, 1)
    uri = f"file:{database_path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM provider_operations WHERE fingerprint=?", (operation.fingerprint,)
        ).fetchone()
        if row is None:
            return BenchmarkCasePlan(operation, None, False, False, 1)
        state = str(row["state"])
        corrections = db.execute(
            "SELECT count(*) FROM provider_reconciliations WHERE operation_id=?",
            (row["id"],),
        ).fetchone()[0]
        if state in {"CALLING", "SUBMITTED", "RUNNING", "AMBIGUOUS"} or corrections:
            return BenchmarkCasePlan(
                operation, state, False, False, 0, "operation requires reconciliation"
            )
        if state != "SUCCEEDED":
            return BenchmarkCasePlan(operation, state, False, False, 1)
        raw_path, raw_sha256 = row["raw_path"], row["raw_sha256"]
        if not raw_path or not raw_sha256:
            return BenchmarkCasePlan(operation, state, False, False, 0, "checkpoint is absent")
        checkpoint = Path(str(raw_path))
        if (
            not checkpoint.is_file()
            or hashlib.sha256(checkpoint.read_bytes()).hexdigest() != raw_sha256
        ):
            return BenchmarkCasePlan(operation, state, False, False, 0, "checkpoint is invalid")
        return BenchmarkCasePlan(operation, state, True, True, 0)


def benchmark_plan(
    cases: tuple[BenchmarkCase, ...], *, benchmark_version: str, settings: Settings
) -> tuple[BenchmarkCasePlan, ...]:
    return tuple(
        plan_benchmark_case(
            canonical_benchmark_operation(
                case, benchmark_version=benchmark_version, settings=settings
            ),
            settings.database_path,
        )
        for case in cases
    )


def benchmark_budget_plan(
    plans: tuple[BenchmarkCasePlan, ...], *, configured_request_budget: int
) -> dict[str, Any]:
    """Summarize locally bounded calls/tokens without inventing provider pricing."""
    return {
        "logical_cases": len(plans),
        "maximum_calls": len(plans),
        "planned_calls": sum(plan.planned_calls for plan in plans),
        "maximum_output_tokens": sum(
            int(plan.operation.parameters["max_output_tokens"]) for plan in plans
        ),
        "known_input_utf8_bytes": sum(
            len(json.dumps(plan.operation.request["messages"], ensure_ascii=False).encode("utf-8"))
            for plan in plans
        ),
        "configured_request_budget": configured_request_budget,
        "configured_cost_ceiling": "UNKNOWN",
        "monetary_estimate": "UNKNOWN",
    }


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
        raise ValueError(f"{field} must be a non-empty string list")
    return tuple(value)


def parse_case(payload: dict[str, Any]) -> BenchmarkCase:
    required = {
        "benchmark_id",
        "capability",
        "prompt",
        "required_constraints",
        "expected_structure",
        "deterministic_checks",
        "reference_facts",
        "rubric_dimensions",
        "hard_fail_criteria",
        "output_token_ceiling",
        "estimated_calls",
        "provider",
        "model",
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
        benchmark_id=str(payload["benchmark_id"]),
        capability=capability,
        prompt=str(payload["prompt"]),
        required_constraints=_strings(payload["required_constraints"], "required_constraints"),
        expected_structure={str(k): str(v) for k, v in payload["expected_structure"].items()},
        deterministic_checks=tuple(checks),
        reference_facts=facts,
        rubric_dimensions=dimensions,
        hard_fail_criteria=hard_fails,
        output_token_ceiling=payload["output_token_ceiling"],
        estimated_calls=payload["estimated_calls"],
        provider=payload["provider"],
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
            evidence.append(
                CheckEvidence("required_terms", not missing, "missing=" + ",".join(missing))
            )
        elif check_type == "prohibited_terms":
            terms = [str(x).casefold() for x in check.get("terms", [])]
            found = [term for term in terms if term in searchable]
            evidence.append(
                CheckEvidence("prohibited_terms", not found, "found=" + ",".join(found))
            )
        elif check_type == "max_words":
            count = len(searchable.split())
            limit = int(check["value"])
            evidence.append(CheckEvidence("max_words", count <= limit, f"{count}/{limit}"))
        elif check_type == "array_length":
            value = output.get(str(check["field"]))
            expected = int(check["value"])
            observed = len(value) if isinstance(value, list) else -1
            evidence.append(
                CheckEvidence("array_length", observed == expected, f"{observed}/{expected}")
            )
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
    if any(
        not isinstance(score, int) or score not in SCORE_ANCHORS
        for score in dimension_scores.values()
    ):
        raise ValueError("rubric scores must be integers from 0 through 5")
    if not set(hard_failures) <= set(case.hard_fail_criteria):
        raise ValueError("hard failure is not allowed by this case")
    if not 0.0 <= unsupported_claim_rate <= 1.0:
        raise ValueError("unsupported_claim_rate must be between zero and one")
    normalized = statistics.fmean(dimension_scores.values()) / max(SCORE_ANCHORS)
    return CaseScore(
        case.benchmark_id, normalized, deterministic.passed, hard_failures, unsupported_claim_rate
    )


def route_passes(scores: tuple[CaseScore, ...]) -> bool:
    if not scores or any(not score.passed for score in scores):
        return False
    values = [score.normalized_score for score in scores]
    return (
        statistics.median(values) >= ROUTE_MEDIAN_THRESHOLD
        and min(values) >= ROUTE_WORST_CASE_THRESHOLD
    )


def review_form(case: BenchmarkCase) -> dict[str, Any]:
    return {
        "benchmark_id": case.benchmark_id,
        "dimension_scores": {dimension: None for dimension in case.rubric_dimensions},
        "dimension_evidence": {dimension: "" for dimension in case.rubric_dimensions},
        "hard_failures": [],
        "unsupported_claim_count": None,
        "total_claim_count": None,
        "scorer_comments": "",
        "scorer_id": "",
        "scored_at": "",
    }


def call_budget(cases: tuple[BenchmarkCase, ...]) -> dict[str, Any]:
    """Return the maximum initial budget; durable reuse belongs to ``benchmark_plan``."""
    by_route = {route: 0 for route in ROUTE_MODELS}
    for case in cases:
        by_route[case.capability] += case.estimated_calls
    return {
        "by_route": by_route,
        "total": sum(by_route.values()),
        "automatic_retries": 0,
        "provider_calls_now": 0,
    }


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
        "complete_finish": isinstance(choice, dict)
        and normalize_finish_reason(choice.get("finish_reason")) == "COMPLETE",
        "content_present": isinstance(content, str) and bool(content.strip()),
        "json_object": isinstance(parsed, dict),
        "required_field": isinstance(parsed, dict)
        and isinstance(parsed.get(required_field), str)
        and bool(parsed[required_field].strip()),
    }
    return {
        "deterministic_passed": all(checks.values()),
        "checks": checks,
        "rubric_scored": False,
        "provider_calls": 0,
        "limitation": "simple canary supports structural checks, not representative route-quality scoring",
    }
