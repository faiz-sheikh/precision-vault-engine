from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from pve.controller import AmbiguousOperation, BudgetExceeded, Controller, OperationBlocked
from pve.identity import provider_fingerprint
from pve.providers import (
    FailureClass,
    HTTPErrorEvidence,
    NvidiaAdapter,
    ProviderFailure,
    ProviderResult,
    parse_structured_message,
)
from pve.safety import PaidExecutionBlocked
from pve.settings import ProviderRoute, Settings


class ReconciliationSupersessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            state_dir=root / "state",
            artifact_dir=root / "artifacts",
            max_provider_attempts=1,
            max_requests_per_run=1,
        )
        self.controller = Controller(self.settings)
        self.request128 = {
            "messages": [{"role": "user", "content": "bounded reasoning"}],
            "temperature": 0,
            "max_tokens": 128,
            "response_format": {"type": "json_object"},
        }
        self.request256 = {**self.request128, "max_tokens": 256}
        self.parameters128 = {"max_output_tokens": 128}
        self.parameters256 = {"max_output_tokens": 256}
        self.schema = {"type": "object", "required": ["result"]}
        self.expiry = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def execute(
        self,
        adapter: NvidiaAdapter,
        *,
        request: dict[str, object] | None = None,
        parameters: dict[str, object] | None = None,
        retry: str | None = None,
        supersession: str | None = None,
    ):
        return self.controller.execute_provider(
            capability="ENGINEERING_REASONING",
            request=request or self.request128,
            prompt_hash="pre-canary-v1",
            parameters=parameters or self.parameters128,
            schema=self.schema,
            upstream_hashes=[],
            module_id="mod_canary",
            phase="engineering_reasoning",
            adapter=adapter,
            parse=lambda raw: parse_structured_message(raw, required_string_fields=("result",)),
            estimated_cost_usd=0.01,
            manual_retry_approval_id=retry,
            supersession_approval_id=supersession,
        )

    @staticmethod
    def truncated_raw() -> bytes:
        return json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"result":"incomplete'},
                    }
                ],
                "usage": {"completion_tokens": 128},
            }
        ).encode()

    @staticmethod
    def valid_raw() -> bytes:
        return json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"result":"complete"}'},
                    }
                ],
                "usage": {"completion_tokens": 20},
            }
        ).encode()

    def historical_operation(self) -> tuple[str, str, str, str]:
        evidence = HTTPErrorEvidence(
            status=503,
            headers={},
            body=b'{"error":{"message":"overloaded"}}',
            provider_error_code="503",
            provider_error_message="overloaded",
        )

        def fail(request: dict[str, object]) -> ProviderResult:
            raise ProviderFailure(
                "503", FailureClass.TRANSIENT, http_evidence=evidence, request_sent=True
            )

        with self.assertRaises(ProviderFailure):
            self.execute(NvidiaAdapter(fail))
        with self.controller.store.connect() as db:
            operation = db.execute("SELECT * FROM provider_operations").fetchone()
        approval = "same-request-retry"
        self.controller.approve_manual_provider_retry(
            approval_id=approval,
            operation_id=operation["id"],
            fingerprint=operation["fingerprint"],
            actor="operator",
            reason="retry transient overload",
            expires_at=self.expiry,
            additional_attempts=1,
            max_additional_requests=1,
            max_additional_cost_usd=0.01,
        )
        # Emulate the historical pre-fix outer-envelope parser.
        self.controller.execute_provider(
            capability="ENGINEERING_REASONING",
            request=self.request128,
            prompt_hash="pre-canary-v1",
            parameters=self.parameters128,
            schema=self.schema,
            upstream_hashes=[],
            module_id="mod_canary",
            phase="engineering_reasoning",
            adapter=NvidiaAdapter(
                lambda request: ProviderResult(self.truncated_raw(), usage={"total_tokens": 169})
            ),
            parse=json.loads,
            estimated_cost_usd=0.01,
            manual_retry_approval_id=approval,
        )
        with self.controller.store.connect() as db:
            operation = db.execute("SELECT * FROM provider_operations").fetchone()
            attempt1 = db.execute(
                "SELECT * FROM provider_attempts WHERE attempt_number=1"
            ).fetchone()
            attempt2 = db.execute(
                "SELECT * FROM provider_attempts WHERE attempt_number=2"
            ).fetchone()
        return (
            operation["id"],
            operation["raw_sha256"],
            attempt1["attempt_id"],
            attempt2["attempt_id"],
        )

    def reconcile(self) -> tuple[str, str, str, str, str]:
        operation_id, raw_sha, attempt1, attempt2 = self.historical_operation()
        correction = self.controller.append_classification_correction(
            operation_id=operation_id,
            attempt_id=attempt2,
            evidence_type="RAW",
            original_classification="SUCCEEDED",
            corrected_classification=FailureClass.VALIDATION,
            reason="finish reason length is truncated",
            evidence_sha256=raw_sha,
            actor="operator",
            system_version="validator-v2",
        )
        return operation_id, raw_sha, attempt1, attempt2, correction

    def approve_for_parent(self, operation_id: str, approval_id: str = "supersession-1"):
        child, fingerprint, reused = self.controller.approve_superseding_operation(
            approval_id=approval_id,
            parent_operation_id=operation_id,
            request=self.request256,
            prompt_hash="pre-canary-v1",
            parameters=self.parameters256,
            schema=self.schema,
            upstream_hashes=[],
            max_output_tokens=256,
            max_cost_usd=0.01,
            actor="operator",
            reason="increase bounded ceiling after truncation",
            expires_at=self.expiry,
        )
        return child, fingerprint, reused

    def approve_supersession(self, approval_id: str = "supersession-1"):
        operation_id, _, _, attempt2, correction = self.reconcile()
        child, fingerprint, reused = self.approve_for_parent(operation_id, approval_id)
        return operation_id, child, fingerprint, reused, attempt2, correction

    def test_attempt_scoped_raw_correction_ignores_earlier_error(self) -> None:
        operation_id, raw_sha, attempt1, attempt2, correction = self.reconcile()
        with self.controller.store.connect() as db:
            op = db.execute(
                "SELECT * FROM provider_operations WHERE id=?", (operation_id,)
            ).fetchone()
            rec = db.execute(
                "SELECT * FROM provider_reconciliations WHERE id=?", (correction,)
            ).fetchone()
            attempts = [
                dict(row)
                for row in db.execute("SELECT * FROM provider_attempts ORDER BY attempt_number")
            ]
            events = [
                row[0]
                for row in db.execute("SELECT event_type FROM provider_events ORDER BY sequence")
            ]
            reservation = db.execute(
                "SELECT status FROM provider_attempt_reservations WHERE attempt_id=?", (attempt2,)
            ).fetchone()[0]
            self.assertEqual(
                self.controller._effective_operation_state(db, op), "FAILED_VALIDATION"
            )
        self.assertEqual(rec["attempt_id"], attempt2)
        self.assertEqual(rec["evidence_type"], "RAW")
        self.assertEqual(rec["evidence_sha256"], raw_sha)
        self.assertTrue(attempts[0]["error_sha256"])
        self.assertEqual(attempts[1]["state"], "SUCCEEDED")
        self.assertEqual(reservation, "CONSUMED")
        self.assertEqual(events[-1], "CLASSIFICATION_CORRECTED")
        self.assertEqual(attempt1, attempts[0]["attempt_id"])

    def test_raw_hash_mismatch_and_absent_evidence_fail_closed(self) -> None:
        operation_id, raw_sha, _, attempt2 = self.historical_operation()
        with self.assertRaises(OperationBlocked):
            self.controller.append_classification_correction(
                operation_id=operation_id,
                attempt_id=attempt2,
                evidence_type="RAW",
                original_classification="SUCCEEDED",
                corrected_classification=FailureClass.VALIDATION,
                reason="bad hash",
                evidence_sha256="0" * 64,
                actor="operator",
                system_version="v2",
            )
        with self.controller.store.transaction() as db:
            db.execute(
                "UPDATE provider_operations SET raw_path=NULL,raw_sha256=NULL WHERE id=?",
                (operation_id,),
            )
        with self.assertRaises(OperationBlocked):
            self.controller.append_classification_correction(
                operation_id=operation_id,
                attempt_id=attempt2,
                evidence_type="RAW",
                original_classification="SUCCEEDED",
                corrected_classification=FailureClass.VALIDATION,
                reason="missing",
                evidence_sha256=raw_sha,
                actor="operator",
                system_version="v2",
            )

    def test_supersession_is_idempotent_and_preserves_lineage(self) -> None:
        parent, child, fingerprint, reused, _, _ = self.approve_supersession()
        self.assertFalse(reused)
        expected = provider_fingerprint(
            provider="nvidia",
            model="nvidia/nemotron-3-ultra-550b-a55b",
            request=self.request256,
            prompt_hash="pre-canary-v1",
            parameters=self.parameters256,
            schema=self.schema,
            run_config_hash=self.settings.config_hash,
            upstream_hashes=[],
        )
        self.assertEqual(fingerprint, expected)
        child2, fingerprint2, reused2 = self.approve_for_parent(parent)
        self.assertEqual((child2, fingerprint2, reused2), (child, fingerprint, True))
        with self.controller.store.connect() as db:
            lineage = db.execute("SELECT * FROM provider_operation_lineage").fetchone()
            self.assertEqual(
                (lineage["parent_operation_id"], lineage["child_operation_id"]), (parent, child)
            )
            self.assertEqual(
                db.execute("SELECT count(*) FROM provider_operations").fetchone()[0], 2
            )

    def test_changed_or_expired_approval_is_rejected(self) -> None:
        parent, _, _, _, _, _ = self.approve_supersession()
        with self.assertRaises(OperationBlocked):
            self.controller.approve_superseding_operation(
                approval_id="supersession-1",
                parent_operation_id=parent,
                request={**self.request256, "max_tokens": 300},
                prompt_hash="pre-canary-v1",
                parameters={"max_output_tokens": 300},
                schema=self.schema,
                upstream_hashes=[],
                max_output_tokens=300,
                max_cost_usd=0.01,
                actor="operator",
                reason="increase bounded ceiling after truncation",
                expires_at=(datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            )

    def test_expired_and_changed_provider_approval_are_rejected(self) -> None:
        parent, *_ = self.reconcile()
        with self.assertRaises(OperationBlocked):
            self.controller.approve_superseding_operation(
                approval_id="expired",
                parent_operation_id=parent,
                request=self.request256,
                prompt_hash="pre-canary-v1",
                parameters=self.parameters256,
                schema=self.schema,
                upstream_hashes=[],
                max_output_tokens=256,
                max_cost_usd=0.01,
                actor="operator",
                reason="expired",
                expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            )
        changed = Settings(
            state_dir=self.settings.state_dir,
            artifact_dir=self.settings.artifact_dir,
            max_provider_attempts=1,
            routes={
                **self.settings.routes,
                "ENGINEERING_REASONING": ProviderRoute(
                    "nvidia", "different-model", "https://invalid"
                ),
            },
        )
        with self.assertRaises(OperationBlocked):
            Controller(changed).approve_superseding_operation(
                approval_id="changed-provider",
                parent_operation_id=parent,
                request=self.request256,
                prompt_hash="pre-canary-v1",
                parameters=self.parameters256,
                schema=self.schema,
                upstream_hashes=[],
                max_output_tokens=256,
                max_cost_usd=0.01,
                actor="operator",
                reason="changed provider",
                expires_at=self.expiry,
            )

    def test_approved_child_requires_bound_approval_and_has_independent_reservation(self) -> None:
        parent, child, _, _, _, _ = self.approve_supersession()
        adapter = NvidiaAdapter(
            lambda request: ProviderResult(self.valid_raw(), usage={"total_tokens": 30})
        )
        with self.assertRaises(OperationBlocked):
            self.execute(adapter, request=self.request256, parameters=self.parameters256)
        with self.assertRaises(OperationBlocked):
            self.execute(
                adapter,
                request={**self.request256, "max_tokens": 300},
                parameters={"max_output_tokens": 300},
                supersession="supersession-1",
            )
        result = self.execute(
            adapter,
            request=self.request256,
            parameters=self.parameters256,
            supersession="supersession-1",
        )
        self.assertEqual(result, ({"result": "complete"}, False))
        self.assertEqual(
            self.execute(adapter, request=self.request256, parameters=self.parameters256),
            ({"result": "complete"}, True),
        )
        with self.controller.store.connect() as db:
            reservations = list(
                db.execute("SELECT operation_id,status FROM provider_attempt_reservations")
            )
        self.assertTrue(any(row[0] == child and row[1] == "CONSUMED" for row in reservations))
        self.assertTrue(any(row[0] == parent and row[1] == "CONSUMED" for row in reservations))

    def test_child_attempt_limit_is_independent_of_parent_attempts(self) -> None:
        parent, child, _, _, _, _ = self.approve_supersession()
        with self.controller.store.connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT attempts FROM provider_operations WHERE id=?", (parent,)
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                db.execute(
                    "SELECT attempts FROM provider_operations WHERE id=?", (child,)
                ).fetchone()[0],
                0,
            )
        calls: list[str] = []
        result = self.execute(
            NvidiaAdapter(
                lambda request: (
                    calls.append("transport")
                    or ProviderResult(self.valid_raw(), usage={"total_tokens": 30})
                )
            ),
            request=self.request256,
            parameters=self.parameters256,
            supersession="supersession-1",
        )
        self.assertEqual(result, ({"result": "complete"}, False))
        self.assertEqual(calls, ["transport"])

    def test_child_exhausted_allowance_blocks_without_consuming_approval(self) -> None:
        _, child, _, _, _, _ = self.approve_supersession()
        with self.controller.store.transaction() as db:
            db.execute(
                "UPDATE provider_operations SET attempts=1 WHERE id=?",
                (child,),
            )
        calls: list[str] = []
        with self.assertRaisesRegex(OperationBlocked, "initial attempt allowance exhausted"):
            self.execute(
                NvidiaAdapter(lambda request: calls.append("transport")),
                request=self.request256,
                parameters=self.parameters256,
                supersession="supersession-1",
            )
        with self.controller.store.connect() as db:
            consumptions = db.execute(
                "SELECT count(*) FROM provider_supersession_consumptions"
            ).fetchone()[0]
            reservations = db.execute(
                "SELECT count(*) FROM provider_attempt_reservations WHERE operation_id=?",
                (child,),
            ).fetchone()[0]
        self.assertEqual((calls, consumptions, reservations), ([], 0, 0))

    def test_global_cost_budget_still_blocks_superseding_child(self) -> None:
        cost_root = self.settings.state_dir.parent / "cost-capped"
        self.settings = Settings(
            state_dir=cost_root / "state",
            artifact_dir=cost_root / "artifacts",
            max_provider_attempts=1,
            max_requests_per_run=1,
            max_cost_usd=0.02,
        )
        self.controller = Controller(self.settings)
        _, child, _, _, _, _ = self.approve_supersession()
        calls: list[str] = []
        with self.assertRaisesRegex(BudgetExceeded, "cost budget exhausted"):
            self.execute(
                NvidiaAdapter(lambda request: calls.append("transport")),
                request=self.request256,
                parameters=self.parameters256,
                supersession="supersession-1",
            )
        with self.controller.store.connect() as db:
            consumptions = db.execute(
                "SELECT count(*) FROM provider_supersession_consumptions"
            ).fetchone()[0]
            attempts = db.execute(
                "SELECT attempts FROM provider_operations WHERE id=?", (child,)
            ).fetchone()[0]
        self.assertEqual((calls, consumptions, attempts), ([], 0, 0))

    def test_wrong_expired_drifted_and_consumed_approvals_never_transport(self) -> None:
        _, child, _, _, _, _ = self.approve_supersession()
        calls: list[str] = []
        adapter = NvidiaAdapter(lambda request: calls.append("transport"))

        with self.assertRaisesRegex(OperationBlocked, "binding mismatch"):
            self.execute(
                adapter,
                request=self.request256,
                parameters=self.parameters256,
                supersession="missing-or-wrong-approval",
            )

        changed = Settings(
            state_dir=self.settings.state_dir,
            artifact_dir=self.settings.artifact_dir,
            max_provider_attempts=1,
            max_requests_per_run=1,
            routes={
                **self.settings.routes,
                "ENGINEERING_REASONING": ProviderRoute(
                    "nvidia", "drifted-model", "https://invalid"
                ),
            },
        )
        drifted = Controller(changed)
        with self.assertRaisesRegex(OperationBlocked, "approved target operation"):
            drifted.execute_provider(
                capability="ENGINEERING_REASONING",
                request=self.request256,
                prompt_hash="pre-canary-v1",
                parameters=self.parameters256,
                schema=self.schema,
                upstream_hashes=[],
                module_id="mod_canary",
                phase="engineering_reasoning",
                adapter=adapter,
                parse=json.loads,
                estimated_cost_usd=0.01,
                supersession_approval_id="supersession-1",
            )

        class FutureDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(UTC) + timedelta(hours=1)

        with patch("pve.controller.datetime", FutureDateTime), self.assertRaisesRegex(
            OperationBlocked, "expired"
        ):
            self.execute(
                adapter,
                request=self.request256,
                parameters=self.parameters256,
                supersession="supersession-1",
            )

        result = self.execute(
            NvidiaAdapter(lambda request: ProviderResult(self.valid_raw())),
            request=self.request256,
            parameters=self.parameters256,
            supersession="supersession-1",
        )
        self.assertEqual(result, ({"result": "complete"}, False))
        self.assertEqual(
            self.execute(
                adapter,
                request=self.request256,
                parameters=self.parameters256,
                supersession="supersession-1",
            ),
            ({"result": "complete"}, True),
        )
        with self.controller.store.connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM provider_supersession_consumptions"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM provider_attempts WHERE operation_id=?", (child,)
                ).fetchone()[0],
                1,
            )
        self.assertEqual(calls, [])

    def test_disabled_live_circuit_blocks_before_child_reservation_or_consumption(self) -> None:
        _, child, _, _, _, _ = self.approve_supersession()
        calls: list[str] = []
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(PaidExecutionBlocked):
            self.execute(
                NvidiaAdapter(lambda request: calls.append("transport"), live=True),
                request=self.request256,
                parameters=self.parameters256,
                supersession="supersession-1",
            )
        with self.controller.store.connect() as db:
            attempts = db.execute(
                "SELECT attempts FROM provider_operations WHERE id=?", (child,)
            ).fetchone()[0]
            reservations = db.execute(
                "SELECT count(*) FROM provider_attempt_reservations WHERE operation_id=?", (child,)
            ).fetchone()[0]
            consumptions = db.execute(
                "SELECT count(*) FROM provider_supersession_consumptions"
            ).fetchone()[0]
        self.assertEqual((calls, attempts, reservations, consumptions), ([], 0, 0, 0))

    def test_parent_reservations_are_immutable_during_child_execution(self) -> None:
        parent, child, _, _, _, _ = self.approve_supersession()
        with self.controller.store.connect() as db:
            before = [
                tuple(row)
                for row in db.execute(
                    "SELECT * FROM provider_attempt_reservations WHERE operation_id=? "
                    "ORDER BY reservation_id",
                    (parent,),
                )
            ]
        self.execute(
            NvidiaAdapter(lambda request: ProviderResult(self.valid_raw())),
            request=self.request256,
            parameters=self.parameters256,
            supersession="supersession-1",
        )
        with self.controller.store.connect() as db:
            after = [
                tuple(row)
                for row in db.execute(
                    "SELECT * FROM provider_attempt_reservations WHERE operation_id=? "
                    "ORDER BY reservation_id",
                    (parent,),
                )
            ]
            child_reservations = db.execute(
                "SELECT count(*) FROM provider_attempt_reservations WHERE operation_id=?", (child,)
            ).fetchone()[0]
        self.assertEqual(after, before)
        self.assertEqual(child_reservations, 1)

    def test_concurrent_child_execution_consumes_one_shot_once(self) -> None:
        _, child, _, _, _, _ = self.approve_supersession()
        entered = threading.Event()
        release = threading.Event()
        calls: list[str] = []
        outcomes: list[str] = []

        def transport(request: dict[str, object]) -> ProviderResult:
            calls.append("transport")
            entered.set()
            release.wait(timeout=5)
            return ProviderResult(self.valid_raw(), usage={"total_tokens": 30})

        def execute() -> None:
            try:
                self.execute(
                    NvidiaAdapter(transport),
                    request=self.request256,
                    parameters=self.parameters256,
                    supersession="supersession-1",
                )
                outcomes.append("success")
            except (AmbiguousOperation, OperationBlocked):
                outcomes.append("blocked")

        first = threading.Thread(target=execute)
        first.start()
        self.assertTrue(entered.wait(timeout=5))
        second = threading.Thread(target=execute)
        second.start()
        second.join(timeout=5)
        release.set()
        first.join(timeout=5)
        with self.controller.store.connect() as db:
            attempts = db.execute(
                "SELECT count(*) FROM provider_attempts WHERE operation_id=?", (child,)
            ).fetchone()[0]
            reservations = db.execute(
                "SELECT count(*) FROM provider_attempt_reservations WHERE operation_id=?",
                (child,),
            ).fetchone()[0]
            consumptions = db.execute(
                "SELECT count(*) FROM provider_supersession_consumptions"
            ).fetchone()[0]
        self.assertEqual(calls, ["transport"])
        self.assertEqual(sorted(outcomes), ["blocked", "success"])
        self.assertEqual((attempts, reservations, consumptions), (1, 1, 1))

    def test_concurrent_duplicate_creation_produces_one_child(self) -> None:
        operation_id, *_ = self.reconcile()
        results: list[tuple[str, str, bool]] = []
        errors: list[BaseException] = []

        def create() -> None:
            try:
                results.append(
                    self.controller.approve_superseding_operation(
                        approval_id="concurrent",
                        parent_operation_id=operation_id,
                        request=self.request256,
                        prompt_hash="pre-canary-v1",
                        parameters=self.parameters256,
                        schema=self.schema,
                        upstream_hashes=[],
                        max_output_tokens=256,
                        max_cost_usd=0.01,
                        actor="operator",
                        reason="bounded concurrent",
                        expires_at=self.expiry,
                    )
                )
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=create) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len({result[0] for result in results}), 1)
        with self.controller.store.connect() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM provider_operation_lineage").fetchone()[0], 1
            )
