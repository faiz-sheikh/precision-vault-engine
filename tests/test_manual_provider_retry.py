from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from pve.controller import AmbiguousOperation, BudgetExceeded, Controller, OperationBlocked
from pve.providers import (
    Adapter,
    FailureClass,
    GoogleAdapter,
    LegacyAnthropicAdapter,
    NvidiaAdapter,
    OpenAIAdapter,
    ProviderFailure,
    ProviderResult,
)
from pve.safety import PaidExecutionBlocked
from pve.settings import ProviderRoute, Settings


class ManualProviderRetryTests(unittest.TestCase):
    PROVIDERS: ClassVar[dict[str, type[Adapter]]] = {
        "google": GoogleAdapter,
        "nvidia": NvidiaAdapter,
        "openai": OpenAIAdapter,
        "anthropic": LegacyAnthropicAdapter,
    }

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def controller(self, provider: str = "google", model: str = "model-test") -> Controller:
        return Controller(
            Settings(
                state_dir=self.root / "state",
                artifact_dir=self.root / "artifacts",
                routes={"CAPABILITY": ProviderRoute(provider, model)},
                max_provider_attempts=1,
                max_requests_per_run=1,
                max_cost_usd=0.10,
            )
        )

    @staticmethod
    def execute(
        controller: Controller,
        adapter: Adapter,
        *,
        approval_id: str | None = None,
        estimated_cost: float = 0.01,
    ):
        return controller.execute_provider(
            capability="CAPABILITY",
            request={"input": "same logical request"},
            prompt_hash="manual-retry-v1",
            parameters={"temperature": 0},
            schema={"type": "object"},
            upstream_hashes=["source-v1"],
            module_id="mod_retry",
            phase="research",
            adapter=adapter,
            parse=json.loads,
            estimated_cost_usd=estimated_cost,
            manual_retry_approval_id=approval_id,
        )

    @staticmethod
    def transient_adapter(adapter_class: type[Adapter], calls: list[str]) -> Adapter:
        def fail(request):
            calls.append("failed")
            raise ProviderFailure("mock 429", FailureClass.TRANSIENT, request_sent=True)

        return adapter_class(fail)

    def operation(self, controller: Controller):
        with controller.store.connect() as db:
            return db.execute("SELECT * FROM provider_operations").fetchone()

    def approve(
        self,
        controller: Controller,
        approval_id: str = "approval-1",
        *,
        max_cost: float = 0.02,
    ) -> str:
        operation = self.operation(controller)
        controller.approve_manual_provider_retry(
            approval_id=approval_id,
            operation_id=operation["id"],
            fingerprint=operation["fingerprint"],
            actor="test-operator",
            reason="explicit bounded retry test",
            expires_at=(datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            additional_attempts=1,
            max_additional_requests=1,
            max_additional_cost_usd=max_cost,
        )
        return approval_id

    def test_provider_neutral_failed_approved_success_and_zero_call_reuse(self) -> None:
        for provider, adapter_class in self.PROVIDERS.items():
            with self.subTest(provider=provider):
                self.tearDown()
                self.setUp()
                controller = self.controller(provider)
                calls: list[str] = []
                with self.assertRaises(ProviderFailure):
                    self.execute(controller, self.transient_adapter(adapter_class, calls))
                operation_before = self.operation(controller)
                fingerprint = operation_before["fingerprint"]
                with controller.store.connect() as db:
                    historical_reservation = dict(
                        db.execute("SELECT * FROM budget_reservations").fetchone()
                    )
                approval_id = self.approve(controller)

                def succeed(request, provider_calls=calls):
                    provider_calls.append("succeeded")
                    return ProviderResult(b'{"result":"ok"}', usage={"total_tokens": 3})

                adapter = adapter_class(succeed)
                self.assertEqual(
                    self.execute(controller, adapter, approval_id=approval_id),
                    ({"result": "ok"}, False),
                )
                self.assertEqual(self.execute(controller, adapter), ({"result": "ok"}, True))
                self.assertEqual(calls, ["failed", "succeeded"])
                with controller.store.connect() as db:
                    operation = db.execute("SELECT * FROM provider_operations").fetchone()
                    attempts = db.execute(
                        "SELECT * FROM provider_attempts ORDER BY attempt_number"
                    ).fetchall()
                    consumption = db.execute(
                        "SELECT * FROM provider_retry_approval_consumptions"
                    ).fetchone()
                    current_historical_reservation = dict(
                        db.execute("SELECT * FROM budget_reservations").fetchone()
                    )
                    retry_reservation = db.execute(
                        "SELECT * FROM provider_attempt_reservations WHERE attempt_id=?",
                        (attempts[1]["attempt_id"],),
                    ).fetchone()
                self.assertEqual(operation["fingerprint"], fingerprint)
                self.assertEqual(operation["attempts"], 2)
                self.assertEqual([row["attempt_number"] for row in attempts], [1, 2])
                self.assertNotEqual(attempts[0]["attempt_id"], attempts[1]["attempt_id"])
                self.assertEqual(attempts[1]["previous_attempt_id"], attempts[0]["attempt_id"])
                self.assertEqual(attempts[1]["approval_id"], approval_id)
                self.assertEqual(attempts[1]["reservation_status"], "CONSUMED")
                self.assertEqual(consumption["attempt_id"], attempts[1]["attempt_id"])
                self.assertEqual(current_historical_reservation, historical_reservation)
                self.assertNotEqual(retry_reservation["reservation_id"], historical_reservation["id"])
                self.assertEqual(retry_reservation["status"], "CONSUMED")

    def test_no_approval_is_blocked_after_automatic_limit(self) -> None:
        controller = self.controller()
        calls: list[str] = []
        adapter = self.transient_adapter(GoogleAdapter, calls)
        with self.assertRaises(ProviderFailure):
            self.execute(controller, adapter)
        with self.assertRaises(OperationBlocked):
            self.execute(controller, adapter)
        self.assertEqual(calls, ["failed"])

    def insert_bound_approval(self, controller: Controller, approval_id: str, **changes) -> None:
        operation = self.operation(controller)
        values = {
            "operation_id": operation["id"],
            "fingerprint": operation["fingerprint"],
            "provider": operation["provider"],
            "model": operation["model"],
            "capability": operation["capability"],
            "config_hash": controller.settings.config_hash,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        }
        values.update(changes)
        with controller.store.transaction() as db:
            db.execute(
                "INSERT INTO provider_retry_approvals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    approval_id,
                    values["operation_id"],
                    values["fingerprint"],
                    "MANUAL_PROVIDER_RETRY",
                    "test-operator",
                    "binding test",
                    datetime.now(UTC).isoformat(),
                    values["expires_at"],
                    1,
                    values["provider"],
                    values["model"],
                    values["capability"],
                    1,
                    0.02,
                    values["config_hash"],
                ),
            )

    def test_expired_and_wrong_bindings_are_blocked_without_calls(self) -> None:
        mismatches = {
            "expired": {"expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
            "fingerprint": {"fingerprint": "sha256:wrong"},
            "provider": {"provider": "openai"},
            "model": {"model": "wrong-model"},
            "capability": {"capability": "WRONG"},
            "configuration": {"config_hash": "sha256:wrong"},
        }
        for label, changes in mismatches.items():
            with self.subTest(label=label):
                self.tearDown()
                self.setUp()
                controller = self.controller()
                calls: list[str] = []
                with self.assertRaises(ProviderFailure):
                    self.execute(controller, self.transient_adapter(GoogleAdapter, calls))
                self.insert_bound_approval(controller, "approval-wrong", **changes)
                with self.assertRaises(OperationBlocked):
                    self.execute(
                        controller,
                        GoogleAdapter(
                            lambda request, provider_calls=calls: provider_calls.append(
                                "unexpected"
                            )
                        ),
                        approval_id="approval-wrong",
                    )
                self.assertEqual(calls, ["failed"])

    def test_consumed_approval_cannot_authorize_another_failed_attempt(self) -> None:
        controller = self.controller()
        calls: list[str] = []
        adapter = self.transient_adapter(GoogleAdapter, calls)
        with self.assertRaises(ProviderFailure):
            self.execute(controller, adapter)
        approval_id = self.approve(controller)
        with self.assertRaises(ProviderFailure):
            self.execute(controller, adapter, approval_id=approval_id)
        with self.assertRaises(OperationBlocked):
            self.execute(controller, adapter, approval_id=approval_id)
        self.assertEqual(calls, ["failed", "failed"])

    def test_concurrent_consumption_allows_exactly_one_provider_attempt(self) -> None:
        controller = self.controller()
        with self.assertRaises(ProviderFailure):
            self.execute(controller, self.transient_adapter(GoogleAdapter, []))
        approval_id = self.approve(controller)
        calls: list[str] = []
        outcomes: list[str] = []
        barrier = threading.Barrier(2)

        def succeed(request):
            calls.append("network")
            time.sleep(0.05)
            return ProviderResult(b'{"result":"ok"}', usage={"tokens": 1})

        def invoke() -> None:
            barrier.wait()
            try:
                self.execute(controller, GoogleAdapter(succeed), approval_id=approval_id)
                outcomes.append("success")
            except OperationBlocked:
                outcomes.append("blocked")

        threads = [threading.Thread(target=invoke) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(calls, ["network"])
        self.assertEqual(sorted(outcomes), ["blocked", "success"])

    def test_succeeded_and_ambiguous_operations_block_manual_retry(self) -> None:
        controller = self.controller()
        self.execute(
            controller,
            GoogleAdapter(lambda request: ProviderResult(b"{}", usage={"tokens": 1})),
        )
        with self.assertRaises(OperationBlocked):
            self.execute(
                controller,
                GoogleAdapter(lambda request: ProviderResult(b"{}")),
                approval_id="not-applicable",
            )

        self.tearDown()
        self.setUp()
        controller = self.controller()
        with self.assertRaises(AmbiguousOperation):
            self.execute(
                controller,
                GoogleAdapter(
                    lambda request: (_ for _ in ()).throw(
                        ProviderFailure("unknown", FailureClass.AMBIGUOUS, request_sent=True)
                    )
                ),
            )
        with self.assertRaises(OperationBlocked):
            self.approve(controller)

    def test_budget_and_disabled_live_switch_block_before_approval_consumption(self) -> None:
        controller = self.controller()
        with self.assertRaises(ProviderFailure):
            self.execute(controller, self.transient_adapter(GoogleAdapter, []))
        approval_id = self.approve(controller, max_cost=0.005)
        with self.assertRaises(BudgetExceeded):
            self.execute(
                controller,
                GoogleAdapter(lambda request: ProviderResult(b"{}")),
                approval_id=approval_id,
                estimated_cost=0.01,
            )
        with controller.store.connect() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM provider_retry_approval_consumptions").fetchone()[
                    0
                ],
                0,
            )

        with patch.dict(os.environ, {}, clear=True), self.assertRaises(PaidExecutionBlocked):
            self.execute(
                controller,
                GoogleAdapter(lambda request: ProviderResult(b"{}"), live=True),
                approval_id=approval_id,
                estimated_cost=0.001,
            )
        with controller.store.connect() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM provider_retry_approval_consumptions").fetchone()[
                    0
                ],
                0,
            )

    def test_crash_boundaries_consume_once_and_block_resubmission(self) -> None:
        class CrashBeforeTransportAdapter(GoogleAdapter):
            def execute(self, request, on_http_error=None):
                raise RuntimeError("crash after durable approval consumption")

        controller = self.controller()
        with self.assertRaises(ProviderFailure):
            self.execute(controller, self.transient_adapter(GoogleAdapter, []))
        approval_id = self.approve(controller)
        with self.assertRaises(AmbiguousOperation):
            self.execute(
                controller,
                CrashBeforeTransportAdapter(lambda request: ProviderResult(b"{}")),
                approval_id=approval_id,
            )
        with self.assertRaises(AmbiguousOperation):
            self.execute(
                controller,
                GoogleAdapter(lambda request: ProviderResult(b"{}")),
                approval_id=approval_id,
            )
        with controller.store.connect() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM provider_retry_approval_consumptions").fetchone()[
                    0
                ],
                1,
            )

    def test_ambiguous_submission_after_manual_consumption_is_never_resubmitted(self) -> None:
        controller = self.controller()
        with self.assertRaises(ProviderFailure):
            self.execute(controller, self.transient_adapter(GoogleAdapter, []))
        approval_id = self.approve(controller)
        calls: list[str] = []

        def ambiguous(request):
            calls.append("submitted")
            raise ProviderFailure("unknown acceptance", FailureClass.AMBIGUOUS, request_sent=True)

        with self.assertRaises(AmbiguousOperation):
            self.execute(controller, GoogleAdapter(ambiguous), approval_id=approval_id)
        with self.assertRaises(AmbiguousOperation):
            self.execute(controller, GoogleAdapter(ambiguous), approval_id=approval_id)
        self.assertEqual(calls, ["submitted"])
        with controller.store.connect() as db:
            attempt = db.execute(
                "SELECT * FROM provider_attempts WHERE approval_id=?", (approval_id,)
            ).fetchone()
        self.assertEqual(attempt["state"], "AMBIGUOUS")
        self.assertEqual(attempt["reservation_status"], "HELD_AMBIGUOUS")

    def test_response_checkpoint_and_parser_crash_boundaries_are_recoverable(self) -> None:
        controller = self.controller()
        with self.assertRaises(ProviderFailure):
            self.execute(controller, self.transient_adapter(GoogleAdapter, []))
        approval_id = self.approve(controller)
        calls: list[str] = []
        adapter = GoogleAdapter(
            lambda request: (calls.append("response") or ProviderResult(b'{"result":"ok"}'))
        )
        with (
            patch.object(controller.artifacts, "checkpoint_raw", side_effect=OSError("disk full")),
            self.assertRaises(AmbiguousOperation),
        ):
            self.execute(controller, adapter, approval_id=approval_id)
        with self.assertRaises(AmbiguousOperation):
            self.execute(controller, adapter, approval_id=approval_id)
        self.assertEqual(calls, ["response"])

        self.tearDown()
        self.setUp()
        controller = self.controller()
        with self.assertRaises(ProviderFailure):
            self.execute(controller, self.transient_adapter(GoogleAdapter, []))
        approval_id = self.approve(controller)
        calls = []
        adapter = GoogleAdapter(
            lambda request: (calls.append("response") or ProviderResult(b'{"result":"ok"}'))
        )
        with self.assertRaises(ValueError):
            controller.execute_provider(
                capability="CAPABILITY",
                request={"input": "same logical request"},
                prompt_hash="manual-retry-v1",
                parameters={"temperature": 0},
                schema={"type": "object"},
                upstream_hashes=["source-v1"],
                module_id="mod_retry",
                phase="research",
                adapter=adapter,
                parse=lambda raw: (_ for _ in ()).throw(ValueError("parser crash")),
                estimated_cost_usd=0.01,
                manual_retry_approval_id=approval_id,
            )
        self.assertEqual(self.execute(controller, adapter), ({"result": "ok"}, True))
        self.assertEqual(calls, ["response"])

    def test_approval_and_consumption_are_immutable(self) -> None:
        controller = self.controller()
        with self.assertRaises(ProviderFailure):
            self.execute(controller, self.transient_adapter(GoogleAdapter, []))
        approval_id = self.approve(controller)
        self.execute(
            controller,
            GoogleAdapter(lambda request: ProviderResult(b"{}", usage={"tokens": 1})),
            approval_id=approval_id,
        )
        for statement in (
            "UPDATE provider_retry_approvals SET reason='changed'",
            "DELETE FROM provider_retry_approvals",
            "UPDATE provider_retry_approval_consumptions SET consumed_at='changed'",
            "DELETE FROM provider_retry_approval_consumptions",
        ):
            with (
                self.subTest(statement=statement),
                self.assertRaises(sqlite3.IntegrityError),
                controller.store.transaction() as db,
            ):
                db.execute(statement)
