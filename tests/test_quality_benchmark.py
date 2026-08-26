from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from pve.quality_benchmark import (
    HARD_FAILURES,
    ROUTE_MODELS,
    BenchmarkCase,
    call_budget,
    deterministic_validate,
    load_cases,
    parse_case,
    review_form,
    route_passes,
    score_case,
    score_chat_checkpoint,
)

ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = ROOT / "config" / "quality_benchmark_cases.json"


class QualityBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_cases(CASE_PATH)

    def test_case_schema_routes_and_balanced_call_budget(self) -> None:
        self.assertEqual(len(self.cases), 12)
        self.assertEqual({case.capability for case in self.cases}, set(ROUTE_MODELS))
        plan = call_budget(self.cases)
        self.assertEqual(plan["by_route"], {route: 3 for route in ROUTE_MODELS})
        self.assertEqual((plan["total"], plan["automatic_retries"], plan["provider_calls_now"]), (12, 0, 0))

    def test_malformed_case_and_missing_reference_are_rejected(self) -> None:
        payload = json.loads(CASE_PATH.read_text())[0]
        malformed = copy.deepcopy(payload)
        malformed["model"] = "wrong-model"
        with self.assertRaisesRegex(ValueError, "route mismatch"):
            parse_case(malformed)
        missing = copy.deepcopy(payload)
        missing["reference_facts"] = []
        with self.assertRaisesRegex(ValueError, "reference_facts"):
            parse_case(missing)

    def test_deterministic_validator_emits_stable_evidence(self) -> None:
        case = self.cases[0]
        output = {
            "title": "PID feedback routine",
            "steps": ["Learn feedback and PID terms", "Compare P, I, D", "Practice tuning"],
        }
        first = deterministic_validate(case, output)
        second = deterministic_validate(case, output)
        self.assertEqual(first, second)
        self.assertTrue(first.passed)
        self.assertTrue(all(item.observed for item in first.evidence))
        failure = deterministic_validate(case, {"title": "PID", "steps": []})
        self.assertFalse(failure.passed)
        self.assertTrue(any(not item.passed for item in failure.evidence))

    def _score(self, case: BenchmarkCase, value: int, **kwargs):
        deterministic = deterministic_validate(
            case,
            {
                "title": "PID feedback routine",
                "steps": ["PID feedback", "PID feedback", "PID feedback"],
            },
        )
        return score_case(
            case,
            deterministic,
            {dimension: value for dimension in case.rubric_dimensions},
            **kwargs,
        )

    def test_score_bounds_hard_fail_override_and_thresholds(self) -> None:
        case = self.cases[0]
        with self.assertRaisesRegex(ValueError, "0 through 5"):
            self._score(case, 6)
        strong = self._score(case, 4)
        self.assertTrue(strong.passed)
        hard = self._score(case, 5, hard_failures=("materially_false_engineering_statement",))
        self.assertFalse(hard.passed)
        self.assertEqual(hard.normalized_score, 1.0)
        weak = self._score(case, 3)
        self.assertFalse(weak.passed)
        self.assertTrue(route_passes((strong, strong, strong)))
        self.assertFalse(route_passes((strong, strong, weak)))

    def test_manual_review_form_is_complete_and_unscored(self) -> None:
        case = self.cases[-1]
        form = review_form(case)
        self.assertEqual(set(form["dimension_scores"]), set(case.rubric_dimensions))
        self.assertTrue(all(value is None for value in form["dimension_scores"].values()))
        self.assertEqual(form["hard_failures"], [])

    def test_duplicate_benchmark_plan_has_zero_call_reuse_semantics(self) -> None:
        completed = {case.benchmark_id for case in self.cases}
        plan = call_budget(self.cases, completed)
        self.assertEqual(plan["total"], 0)
        self.assertEqual(plan["provider_calls_now"], 0)

    def test_preserved_checkpoints_score_offline_without_provider_calls(self) -> None:
        fixtures = (
            (
                ROOT / ".pve/canary/ROUTINE_GENERATION/operations.sqlite3",
                ROOT / "artifacts/canary/ROUTINE_GENERATION/mod_canary/routine_generation/op_e7a1cabf522414486bcb5a2437ae6a31dfecd4d86d2c405c82274c6e3b1233bf/raw/response.bin",
                "e3f32d9af36e473ccdda86031b57d5523d0faaacfcf9b65d579b0fbb6bce9171",
                "summary",
            ),
            (
                ROOT / ".pve/canary/ENGINEERING_REASONING/operations.sqlite3",
                ROOT / "artifacts/canary/ENGINEERING_REASONING/mod_canary/engineering_reasoning/op_32a4a44f4d862086605f0a274e744a5be6e18d9b01c003fc209665a7417f6bb4/raw/response.bin",
                "66319144a7bc0c1d78ea93f212383da9a4e5cab4f9f52e3a748b913cea94448f",
                "result",
            ),
        )
        for database, raw_path, expected_hash, field in fixtures:
            if not database.is_file() or not raw_path.is_file():
                self.skipTest("preserved local provider checkpoints are unavailable")
            raw = raw_path.read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected_hash)
            score = score_chat_checkpoint(raw, required_field=field)
            self.assertTrue(score["deterministic_passed"])
            self.assertFalse(score["rubric_scored"])
            self.assertEqual(score["provider_calls"], 0)

    def test_unknown_hard_failure_is_rejected(self) -> None:
        self.assertNotIn("made_up_failure", HARD_FAILURES)
        with self.assertRaisesRegex(ValueError, "not allowed"):
            self._score(self.cases[0], 4, hard_failures=("made_up_failure",))


if __name__ == "__main__":
    unittest.main()
