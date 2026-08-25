from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from pve.controller import Controller, OperationBlocked
from pve.providers import (
    Adapter,
    FailureClass,
    GoogleAdapter,
    LegacyAnthropicAdapter,
    NvidiaAdapter,
    OpenAIAdapter,
    ProviderFailure,
    ProviderResult,
    _post_json,
)
from pve.settings import ProviderRoute, Settings


class BodyAwareProviderClassificationTests(unittest.TestCase):
    CASES = (
        ("openai", OpenAIAdapter, 429, "insufficient_quota", "You exceeded your current quota; check your plan and billing details", FailureClass.ACCOUNT_OR_BILLING_BLOCKED, "BLOCKED_ACCOUNT_OR_BILLING"),
        ("openai", OpenAIAdapter, 429, "rate_limit_exceeded", "Requests per minute rate limit exceeded", FailureClass.RATE_LIMIT_TRANSIENT, "FAILED_TRANSIENT"),
        ("openai", OpenAIAdapter, 401, "invalid_api_key", "Incorrect API key", FailureClass.AUTH_BLOCKED, "BLOCKED_AUTH"),
        ("openai", OpenAIAdapter, 403, "project_access_denied", "Project access denied", FailureClass.ACCESS_BLOCKED, "BLOCKED_ACCESS"),
        ("openai", OpenAIAdapter, 404, "model_not_found", "Model not found or unavailable", FailureClass.MODEL_ACCESS_BLOCKED, "BLOCKED_MODEL_ACCESS"),
        ("openai", OpenAIAdapter, 400, "invalid_request_error", "Invalid request", FailureClass.PERMANENT_REQUEST, "FAILED_PERMANENT"),
        ("openai", OpenAIAdapter, 500, "internal_error", "Internal error", FailureClass.TRANSIENT, "FAILED_TRANSIENT"),
        ("openai", OpenAIAdapter, 503, "server_overloaded", "Temporarily unavailable", FailureClass.TRANSIENT, "FAILED_TRANSIENT"),
        ("google", GoogleAdapter, 429, "RESOURCE_EXHAUSTED", "Current quota exhausted; check your billing plan", FailureClass.ACCOUNT_OR_BILLING_BLOCKED, "BLOCKED_ACCOUNT_OR_BILLING"),
        ("google", GoogleAdapter, 429, "RESOURCE_EXHAUSTED", "Requests per minute rate limit exceeded", FailureClass.RATE_LIMIT_TRANSIENT, "FAILED_TRANSIENT"),
        ("google", GoogleAdapter, 401, "UNAUTHENTICATED", "Invalid credential", FailureClass.AUTH_BLOCKED, "BLOCKED_AUTH"),
        ("google", GoogleAdapter, 404, "NOT_FOUND", "Model not found", FailureClass.MODEL_ACCESS_BLOCKED, "BLOCKED_MODEL_ACCESS"),
        ("nvidia", NvidiaAdapter, 429, "insufficient_quota", "Credit balance exhausted", FailureClass.ACCOUNT_OR_BILLING_BLOCKED, "BLOCKED_ACCOUNT_OR_BILLING"),
        ("nvidia", NvidiaAdapter, 429, "rate_limit_exceeded", "Tokens per minute exceeded", FailureClass.RATE_LIMIT_TRANSIENT, "FAILED_TRANSIENT"),
        ("nvidia", NvidiaAdapter, 401, "invalid_api_key", "Authentication failed", FailureClass.AUTH_BLOCKED, "BLOCKED_AUTH"),
        ("nvidia", NvidiaAdapter, 404, "model_not_found", "Unknown model", FailureClass.MODEL_ACCESS_BLOCKED, "BLOCKED_MODEL_ACCESS"),
        ("anthropic", LegacyAnthropicAdapter, 400, "billing_error", "Credit balance is too low", FailureClass.ACCOUNT_OR_BILLING_BLOCKED, "BLOCKED_ACCOUNT_OR_BILLING"),
        ("anthropic", LegacyAnthropicAdapter, 429, "rate_limit_error", "Requests per minute exceeded", FailureClass.RATE_LIMIT_TRANSIENT, "FAILED_TRANSIENT"),
        ("anthropic", LegacyAnthropicAdapter, 401, "authentication_error", "Invalid x-api-key", FailureClass.AUTH_BLOCKED, "BLOCKED_AUTH"),
    )

    @staticmethod
    def http_error(status: int, body: bytes) -> urllib.error.HTTPError:
        headers = Message()
        headers["Content-Type"] = "application/json"
        headers["Authorization"] = "never-persist"
        return urllib.error.HTTPError(
            "https://provider.invalid", status, "mock", headers, io.BytesIO(body)
        )

    def test_body_aware_matrix_is_durable_non_fallback_and_nonduplicating(self) -> None:
        for provider, adapter_type, status, code, message, classification, state in self.CASES:
            with self.subTest(provider=provider, status=status, code=code), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                capability = "TEST_CAPABILITY"
                settings = Settings(
                    state_dir=root / "state",
                    artifact_dir=root / "artifacts",
                    routes={capability: ProviderRoute(provider, "model-test")},
                    max_provider_attempts=1,
                    max_requests_per_run=10,
                )
                controller = Controller(settings)
                secret = "test-credential-that-must-never-persist"
                body = json.dumps({"error": {"type": code, "message": f"{message} {secret}"}}).encode()
                adapter: Adapter = adapter_type(
                    lambda request, payload=body, credential=secret: _post_json(
                        "https://provider.invalid",
                        request,
                        {"Authorization": f"Bearer {credential}"},
                    )
                )

                def execute(
                    approval_id: str | None = None,
                    *,
                    bound_controller: Controller = controller,
                    bound_capability: str = capability,
                    bound_adapter: Adapter = adapter,
                ):
                    return bound_controller.execute_provider(
                        capability=bound_capability,
                        request={"input": "same"},
                        prompt_hash="body-aware-v1",
                        parameters={},
                        schema={},
                        upstream_hashes=[],
                        module_id="module",
                        phase="test",
                        adapter=bound_adapter,
                        parse=json.loads,
                        estimated_cost_usd=0.01,
                        manual_retry_approval_id=approval_id,
                    )

                with patch("urllib.request.urlopen", side_effect=self.http_error(status, body)) as network:
                    with self.assertRaises(ProviderFailure) as caught:
                        execute()
                    self.assertEqual(caught.exception.classification, classification)
                    with self.assertRaises(OperationBlocked):
                        execute()
                self.assertEqual(network.call_count, 1)
                with controller.store.connect() as db:
                    operation = db.execute("SELECT * FROM provider_operations").fetchone()
                    attempt = db.execute("SELECT * FROM provider_attempts").fetchone()
                    reservation = db.execute("SELECT * FROM provider_attempt_reservations").fetchone()
                    event = db.execute("SELECT payload_json FROM provider_events ORDER BY sequence DESC LIMIT 1").fetchone()
                    self.assertEqual(db.execute("SELECT count(*) FROM provider_operations WHERE provider<>?", (provider,)).fetchone()[0], 0)
                self.assertEqual(operation["state"], state)
                self.assertEqual(attempt["reservation_status"], "CLOSED_BILLING_UNKNOWN")
                self.assertEqual(reservation["status"], "CLOSED_BILLING_UNKNOWN")
                self.assertEqual(json.loads(event[0])["semantic_classification"], classification.value)
                raw_evidence = Path(operation["error_path"]).read_bytes()
                self.assertNotIn(secret.encode(), raw_evidence)
                self.assertEqual(json.loads(raw_evidence)["provider_error_code"], code)

    def test_historical_correction_is_append_only_and_requires_manual_approval(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            settings = Settings(
                state_dir=root / "state",
                artifact_dir=root / "artifacts",
                routes={"CAP": ProviderRoute("openai", "gpt-test")},
                max_provider_attempts=3,
                max_requests_per_run=10,
            )
            controller = Controller(settings)
            calls: list[str] = []

            def historical_failure(request):
                calls.append("attempt")
                raise ProviderFailure("historical 429", FailureClass.TRANSIENT, request_sent=True)

            def execute(adapter: Adapter, approval_id: str | None = None):
                return controller.execute_provider(
                    capability="CAP", request={"input": "same"}, prompt_hash="v1",
                    parameters={}, schema={}, upstream_hashes=[], module_id="m", phase="p",
                    adapter=adapter, parse=json.loads, estimated_cost_usd=0.01,
                    manual_retry_approval_id=approval_id,
                )

            with self.assertRaises(ProviderFailure):
                execute(OpenAIAdapter(historical_failure))
            with controller.store.connect() as db:
                operation = db.execute("SELECT * FROM provider_operations").fetchone()
                attempt = db.execute("SELECT * FROM provider_attempts").fetchone()
                operation_id, fingerprint, attempt_id = operation["id"], operation["fingerprint"], attempt["attempt_id"]
            error_path, evidence_hash = controller.artifacts.checkpoint_error(
                "m", "p", attempt_id, {"sanitized": "insufficient_quota"}
            )
            with controller.store.connect() as db:
                db.execute("UPDATE provider_attempts SET error_path=?,error_sha256=? WHERE attempt_id=?", (str(error_path), evidence_hash, attempt_id))
                db.execute("UPDATE provider_operations SET error_path=?,error_sha256=? WHERE id=?", (str(error_path), evidence_hash, operation_id))
            correction_id = controller.append_classification_correction(
                operation_id=operation_id,
                attempt_id=attempt_id,
                evidence_type="ERROR",
                original_classification="FAILED_TRANSIENT",
                corrected_classification=FailureClass.ACCOUNT_OR_BILLING_BLOCKED,
                reason="sanitized insufficient_quota evidence",
                evidence_sha256=evidence_hash,
                actor="test-system",
                system_version="test-version",
            )
            with self.assertRaises(OperationBlocked):
                execute(OpenAIAdapter(lambda request: self.fail("must not call")))
            controller.approve_manual_provider_retry(
                approval_id="approval-1", operation_id=operation_id, fingerprint=fingerprint,
                actor="operator", reason="billing state externally resolved",
                expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                additional_attempts=1, max_additional_requests=1, max_additional_cost_usd=0.02,
            )
            success = OpenAIAdapter(lambda request: ProviderResult(b'{"ok":true}', usage={"total_tokens": 1}))
            self.assertEqual(execute(success, "approval-1"), ({"ok": True}, False))
            self.assertEqual(execute(success), ({"ok": True}, True))
            self.assertEqual(calls, ["attempt"])
            with controller.store.connect() as db:
                historical = db.execute("SELECT * FROM provider_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
                correction = db.execute("SELECT * FROM provider_reconciliations WHERE id=?", (correction_id,)).fetchone()
                self.assertEqual(historical["state"], "FAILED_TRANSIENT")
                self.assertEqual(correction["decision"], FailureClass.ACCOUNT_OR_BILLING_BLOCKED.value)
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("DELETE FROM provider_reconciliations WHERE id=?", (correction_id,))


if __name__ == "__main__":
    unittest.main()
