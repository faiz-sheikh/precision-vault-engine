from __future__ import annotations

import dataclasses
import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pve.controller import AmbiguousOperation, Controller, OperationBlocked
from pve.providers import NvidiaAdapter, ProviderResult
from pve.quality_benchmark import (
    benchmark_budget_plan,
    benchmark_config_hash,
    benchmark_plan,
    canonical_benchmark_operation,
    load_cases,
    plan_benchmark_case,
)
from pve.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = ROOT / "config" / "quality_benchmark_cases.json"


class BenchmarkExecutionPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = dataclasses.replace(
            Settings(),
            state_dir=root / "state",
            artifact_dir=root / "artifacts",
            max_provider_attempts=1,
            max_requests_per_run=12,
        )
        self.controller = Controller(self.settings)
        self.cases = load_cases(CASE_PATH)
        self.version = benchmark_config_hash(CASE_PATH)
        self.expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def operation(self, case_index: int = 0):
        return canonical_benchmark_operation(
            self.cases[case_index], benchmark_version=self.version, settings=self.settings
        )

    def approve(self, operation=None, *, approval_id="benchmark-approval-1", **changes):
        operation = operation or self.operation()
        values = {
            "approval_id": approval_id,
            "benchmark_version": operation.benchmark_version,
            "case_id": operation.case_id,
            "capability": operation.capability,
            "request": operation.request,
            "prompt_hash": operation.prompt_hash,
            "parameters": operation.parameters,
            "schema": operation.response_schema,
            "upstream_hashes": list(operation.upstream_hashes),
            "max_output_tokens": operation.parameters["max_output_tokens"],
            "request_budget": 1,
            "token_budget": operation.parameters["max_output_tokens"],
            "max_cost_usd": 1.0,
            "actor": "benchmark-owner",
            "purpose": "initial model quality benchmark",
            "reason": "authorized exact case",
            "expires_at": self.expiry,
        }
        values.update(changes)
        return self.controller.approve_initial_benchmark_operation(**values)

    def execute(self, operation, adapter, approval_id="benchmark-approval-1"):
        return self.controller.execute_provider(
            capability=operation.capability,
            request=operation.request,
            prompt_hash=operation.prompt_hash,
            parameters=operation.parameters,
            schema=operation.response_schema,
            upstream_hashes=list(operation.upstream_hashes),
            module_id="mod_benchmark",
            phase="quality_benchmark",
            adapter=adapter,
            parse=json.loads,
            estimated_cost_usd=0.1,
            initial_benchmark_approval_id=approval_id,
        )

    def test_identity_changes_for_every_material_contract_input(self) -> None:
        base_case = self.cases[0]
        base = self.operation()
        self.assertEqual(base, self.operation())
        variants = (
            dataclasses.replace(base_case, prompt=base_case.prompt + " Changed."),
            dataclasses.replace(
                base_case, expected_structure={**base_case.expected_structure, "note": "string"}
            ),
            dataclasses.replace(base_case, output_token_ceiling=767),
        )
        for case in variants:
            changed = canonical_benchmark_operation(
                case, benchmark_version=self.version, settings=self.settings
            )
            self.assertNotEqual(base.fingerprint, changed.fingerprint)
        changed_settings = dataclasses.replace(self.settings, max_requests_per_run=13)
        changed_runtime = canonical_benchmark_operation(
            base_case, benchmark_version=self.version, settings=changed_settings
        )
        self.assertNotEqual(base.fingerprint, changed_runtime.fingerprint)

    def test_all_twelve_fingerprints_are_unique_and_preview_has_no_calls(self) -> None:
        plans = benchmark_plan(self.cases, benchmark_version=self.version, settings=self.settings)
        self.assertEqual(len({plan.operation.fingerprint for plan in plans}), 12)
        self.assertEqual(sum(plan.planned_calls for plan in plans), 12)
        self.assertTrue(all(plan.durable_state is None for plan in plans))
        budget = benchmark_budget_plan(
            plans, configured_request_budget=self.settings.max_requests_per_run
        )
        self.assertEqual((budget["maximum_calls"], budget["maximum_output_tokens"]), (12, 8960))
        self.assertGreater(budget["known_input_utf8_bytes"], 0)
        self.assertEqual(budget["monetary_estimate"], "UNKNOWN")

    def test_durable_states_and_checkpoint_control_reuse(self) -> None:
        operation = self.operation()
        with self.controller.store.transaction() as db:
            db.execute(
                "INSERT INTO provider_operations(id,fingerprint,provider,model,capability,state,"
                "request_json,attempts,created_at,updated_at) VALUES(?,?,?,?,?,'FAILED_VALIDATION',?,1,'x','x')",
                (
                    operation.operation_id,
                    operation.fingerprint,
                    operation.provider,
                    operation.model,
                    operation.capability,
                    json.dumps(operation.request),
                ),
            )
        failed = plan_benchmark_case(operation, self.settings.database_path)
        self.assertEqual((failed.safe_reuse, failed.planned_calls), (False, 1))
        with self.controller.store.transaction() as db:
            db.execute(
                "UPDATE provider_operations SET state='AMBIGUOUS' WHERE id=?",
                (operation.operation_id,),
            )
        ambiguous = plan_benchmark_case(operation, self.settings.database_path)
        self.assertEqual(
            (ambiguous.planned_calls, ambiguous.blocked_reason),
            (0, "operation requires reconciliation"),
        )
        with self.controller.store.transaction() as db:
            db.execute(
                "UPDATE provider_operations SET state='SUCCEEDED',raw_path='missing',raw_sha256='bad' WHERE id=?",
                (operation.operation_id,),
            )
        invalid = plan_benchmark_case(operation, self.settings.database_path)
        self.assertEqual(
            (invalid.planned_calls, invalid.blocked_reason), (0, "checkpoint is invalid")
        )

    def test_completed_checkpoint_produces_zero_call_rerun(self) -> None:
        operation = self.operation()
        raw = b'{"title":"PID","steps":[]}'
        path = self.settings.artifact_dir / "checkpoint.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        with self.controller.store.transaction() as db:
            db.execute(
                "INSERT INTO provider_operations(id,fingerprint,provider,model,capability,state,"
                "request_json,raw_path,raw_sha256,attempts,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'SUCCEEDED',?,?,?,1,'x','x')",
                (
                    operation.operation_id,
                    operation.fingerprint,
                    operation.provider,
                    operation.model,
                    operation.capability,
                    json.dumps(operation.request),
                    str(path),
                    hashlib.sha256(raw).hexdigest(),
                ),
            )
        plan = plan_benchmark_case(operation, self.settings.database_path)
        self.assertEqual(
            (plan.already_completed, plan.safe_reuse, plan.planned_calls), (True, True, 0)
        )

    def test_approval_exact_binding_duplicate_expiry_drift_and_one_shot(self) -> None:
        operation = self.operation()
        first = self.approve(operation)
        duplicate = self.approve(operation)
        self.assertFalse(first[2])
        self.assertTrue(duplicate[2])
        with self.assertRaisesRegex(OperationBlocked, "binding mismatch"):
            self.approve(operation, reason="drifted")
        with self.assertRaisesRegex(OperationBlocked, "expired"):
            self.approve(
                self.operation(1),
                approval_id="expired",
                expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            )
        drift_calls: list[object] = []
        with self.assertRaisesRegex(OperationBlocked, "exact operation"):
            self.controller.execute_provider(
                capability=operation.capability,
                request={**operation.request, "temperature": 0.1},
                prompt_hash=operation.prompt_hash,
                parameters={**operation.parameters, "temperature": 0.1},
                schema=operation.response_schema,
                upstream_hashes=list(operation.upstream_hashes),
                module_id="mod_benchmark",
                phase="quality_benchmark",
                adapter=NvidiaAdapter(lambda request: drift_calls.append(request)),
                parse=json.loads,
                estimated_cost_usd=0.1,
                initial_benchmark_approval_id="benchmark-approval-1",
            )
        self.assertEqual(drift_calls, [])
        calls: list[dict[str, object]] = []
        adapter = NvidiaAdapter(
            lambda request: (calls.append(request) or ProviderResult(b'{"ok":true}'))
        )
        self.assertEqual(self.execute(operation, adapter), ({"ok": True}, False))
        self.assertEqual(len(calls), 1)
        reused = self.execute(operation, NvidiaAdapter(lambda request: self.fail("transport")))
        self.assertEqual(reused, ({"ok": True}, True))
        with self.controller.store.connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM benchmark_initial_approval_consumptions"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(db.execute("SELECT count(*) FROM provider_attempts").fetchone()[0], 1)
            self.assertEqual(
                db.execute("SELECT count(*) FROM provider_attempt_reservations").fetchone()[0], 1
            )

    def test_concurrent_approval_and_execution_allow_one_operation_and_transport(self) -> None:
        operation = self.operation()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.approve(operation), range(2)))
        self.assertEqual(sorted(result[2] for result in results), [False, True])
        entered = threading.Event()
        release = threading.Event()
        calls = 0
        lock = threading.Lock()

        def transport(request):
            nonlocal calls
            with lock:
                calls += 1
            entered.set()
            release.wait(2)
            return ProviderResult(b'{"ok":true}')

        errors: list[BaseException] = []

        def worker():
            try:
                self.execute(operation, NvidiaAdapter(transport))
            except BaseException as exc:
                errors.append(exc)

        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        second.join(2)
        release.set()
        first.join(2)
        self.assertEqual(calls, 1)
        self.assertTrue(any(isinstance(error, AmbiguousOperation) for error in errors))

    def test_case_id_list_cannot_authorize_completion(self) -> None:
        plans = benchmark_plan(self.cases, benchmark_version=self.version, settings=self.settings)
        pretend_completed_case_ids = {case.benchmark_id for case in self.cases}
        self.assertEqual(len(pretend_completed_case_ids), 12)
        self.assertEqual(sum(plan.planned_calls for plan in plans), 12)


if __name__ == "__main__":
    unittest.main()
