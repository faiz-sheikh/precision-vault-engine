from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pve.controller import Controller
from pve.providers import (
    ContentValidationError,
    FailureClass,
    NvidiaAdapter,
    ProviderResult,
    parse_structured_message,
)
from pve.settings import Settings


def response(content: str, finish_reason: str = "stop") -> bytes:
    return json.dumps(
        {
            "id": "provider-id",
            "model": "nvidia/nemotron-3-ultra-550b-a55b",
            "choices": [{"finish_reason": finish_reason, "message": {"content": content}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 8, "total_tokens": 12},
        }
    ).encode()


class StructuredProviderValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.controller = Controller(
            Settings(state_dir=root / "state", artifact_dir=root / "artifacts")
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def parse(raw: bytes) -> dict[str, object]:
        return parse_structured_message(raw, required_string_fields=("result",))

    def execute(self, adapter: NvidiaAdapter):
        return self.controller.execute_provider(
            capability="ENGINEERING_REASONING",
            request={"messages": [{"role": "user", "content": "bounded"}]},
            prompt_hash="structured-v1",
            parameters={"max_tokens": 128},
            schema={"type": "object", "required": ["result"]},
            upstream_hashes=[],
            module_id="mod_structured",
            phase="engineering_reasoning",
            adapter=adapter,
            parse=self.parse,
            estimated_cost_usd=0.01,
        )

    def ledger(self) -> tuple[str, str, str, str | None, int]:
        with self.controller.store.connect() as db:
            operation = db.execute("SELECT state,raw_path FROM provider_operations").fetchone()
            attempt = db.execute(
                "SELECT state,reservation_status FROM provider_attempts"
            ).fetchone()
            events = db.execute("SELECT count(*) FROM provider_events").fetchone()[0]
        return operation[0], attempt[0], attempt[1], operation[1], events

    def test_complete_valid_structured_content_succeeds(self) -> None:
        result, reused = self.execute(
            NvidiaAdapter(
                lambda request: ProviderResult(
                    response('{"result":"Integral action accumulates error."}'),
                    "provider-id",
                    {"total_tokens": 12},
                )
            )
        )
        self.assertEqual(result, {"result": "Integral action accumulates error."})
        self.assertFalse(reused)
        self.assertEqual(self.ledger()[:3], ("SUCCEEDED", "SUCCEEDED", "CONSUMED"))

    def test_truncated_inner_json_is_failed_validation_and_checkpointed(self) -> None:
        calls: list[int] = []
        adapter = NvidiaAdapter(
            lambda request: (
                calls.append(1)
                or ProviderResult(
                    response('{"result":"unfinished', "length"), usage={"total_tokens": 12}
                )
            )
        )
        with self.assertRaises(ContentValidationError):
            self.execute(adapter)
        state, attempt_state, reservation, raw_path, _ = self.ledger()
        self.assertEqual(
            (state, attempt_state, reservation),
            ("FAILED_VALIDATION", "FAILED_VALIDATION", "CONSUMED"),
        )
        self.assertTrue(raw_path and Path(raw_path).is_file())
        with self.assertRaises(ContentValidationError):
            self.execute(adapter)
        self.assertEqual(calls, [1])

    def test_length_is_rejected_even_when_inner_json_is_valid(self) -> None:
        with self.assertRaises(ContentValidationError):
            self.execute(
                NvidiaAdapter(
                    lambda request: ProviderResult(response('{"result":"valid"}', "length"))
                )
            )
        self.assertEqual(self.ledger()[0], "FAILED_VALIDATION")

    def test_complete_malformed_inner_json_is_rejected(self) -> None:
        with self.assertRaises(ContentValidationError):
            self.execute(NvidiaAdapter(lambda request: ProviderResult(response("not-json"))))
        self.assertEqual(self.ledger()[0], "FAILED_VALIDATION")

    def test_schema_invalid_inner_json_is_rejected(self) -> None:
        with self.assertRaises(ContentValidationError):
            self.execute(NvidiaAdapter(lambda request: ProviderResult(response('{"other":"x"}'))))
        self.assertEqual(self.ledger()[0], "FAILED_VALIDATION")

    def test_unknown_safety_and_tool_finish_reasons_fail_closed(self) -> None:
        for reason in ("mystery", "content_filter", "tool_calls"):
            with self.subTest(reason=reason), self.assertRaises(ContentValidationError):
                self.parse(response('{"result":"otherwise valid"}', reason))

    def test_local_parser_recovery_reuses_raw_without_provider_recall(self) -> None:
        calls: list[int] = []
        adapter = NvidiaAdapter(
            lambda request: (
                calls.append(1)
                or ProviderResult(response('{"result":"valid after parser repair"}'))
            )
        )
        with self.assertRaises(ValueError):
            self.controller.execute_provider(
                capability="ENGINEERING_REASONING",
                request={"messages": [{"role": "user", "content": "bounded"}]},
                prompt_hash="structured-v1",
                parameters={"max_tokens": 128},
                schema={"type": "object", "required": ["result"]},
                upstream_hashes=[],
                module_id="mod_structured",
                phase="engineering_reasoning",
                adapter=adapter,
                parse=lambda raw: (_ for _ in ()).throw(ValueError("old parser crash")),
                estimated_cost_usd=0.01,
            )
        self.assertEqual(self.execute(adapter), ({"result": "valid after parser repair"}, True))
        self.assertEqual(calls, [1])
        self.assertEqual(self.ledger()[0], "SUCCEEDED")

    def test_historical_super_shape_remains_valid(self) -> None:
        parsed = parse_structured_message(
            response('{"summary":"A PID controller combines three actions."}'),
            required_string_fields=("summary",),
        )
        self.assertIn("summary", parsed)

    def test_append_only_correction_can_reclassify_historical_success_projection(self) -> None:
        self.execute(
            NvidiaAdapter(
                lambda request: ProviderResult(
                    response('{"result":"transport-era parser accepted this"}')
                )
            )
        )
        with self.controller.store.connect() as db:
            operation = db.execute("SELECT * FROM provider_operations").fetchone()
            operation_id = operation["id"]
            raw_sha256 = operation["raw_sha256"]
        self.controller.append_classification_correction(
            operation_id=operation_id,
            original_classification="SUCCEEDED",
            corrected_classification=FailureClass.VALIDATION,
            reason="offline re-evaluation found invalid inner content",
            evidence_sha256=raw_sha256,
            actor="test-operator",
            system_version="test-version",
        )
        with self.controller.store.connect() as db:
            operation = db.execute("SELECT * FROM provider_operations").fetchone()
            self.assertEqual(operation["state"], "SUCCEEDED")
            self.assertEqual(
                self.controller._effective_operation_state(db, operation), "FAILED_VALIDATION"
            )
        self.controller.approve_manual_provider_retry(
            approval_id="validation-retry-1",
            operation_id=operation_id,
            fingerprint=operation["fingerprint"],
            actor="test-operator",
            reason="explicit regeneration after validation correction",
            expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            additional_attempts=1,
            max_additional_requests=1,
            max_additional_cost_usd=0.01,
        )
