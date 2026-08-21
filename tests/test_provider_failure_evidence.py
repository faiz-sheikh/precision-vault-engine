from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from pve.controller import AmbiguousOperation, Controller, OperationBlocked
from pve.providers import (
    Adapter,
    GoogleAdapter,
    LegacyAnthropicAdapter,
    NvidiaAdapter,
    OpenAIAdapter,
    ProviderFailure,
    _post_json,
    classify_status,
)
from pve.settings import ProviderRoute, Settings


class ProviderFailureEvidenceTests(unittest.TestCase):
    PROVIDERS: ClassVar[dict[str, tuple[type[Adapter], str, str]]] = {
        "google": (GoogleAdapter, "WEB_GROUNDED_RESEARCH", "gemini-test"),
        "nvidia": (NvidiaAdapter, "ROUTINE_GENERATION", "nemotron-test"),
        "openai": (OpenAIAdapter, "FRONTIER_ESCALATION", "openai-test"),
        "anthropic": (LegacyAnthropicAdapter, "LEGACY_COMPATIBILITY", "anthropic-test"),
    }

    def controller(
        self, root: Path, provider: str, capability: str, model: str, attempts: int = 1
    ) -> Controller:
        return Controller(
            Settings(
                state_dir=root / "state",
                artifact_dir=root / "artifacts",
                routes={capability: ProviderRoute(provider, model)},
                max_provider_attempts=attempts,
                max_requests_per_run=attempts,
            )
        )

    @staticmethod
    def execute(controller: Controller, capability: str, adapter: Adapter):
        return controller.execute_provider(
            capability=capability,
            request={"input": "bounded failure test"},
            prompt_hash="failure-evidence-v1",
            parameters={},
            schema={"type": "object"},
            upstream_hashes=[],
            module_id="mod_failure",
            phase="provider_test",
            adapter=adapter,
            parse=json.loads,
            estimated_cost_usd=0.01,
        )

    @staticmethod
    def http_error(status: int, body: bytes) -> urllib.error.HTTPError:
        headers = Message()
        headers["Content-Type"] = "application/json"
        headers["Retry-After"] = "5"
        headers["Set-Cookie"] = "must-not-persist"
        headers["Authorization"] = "must-not-persist"
        return urllib.error.HTTPError(
            "https://provider.invalid/v1", status, "mock failure", headers, io.BytesIO(body)
        )

    def test_http_failure_matrix_is_durable_bounded_and_provider_neutral(self):
        error_codes = {
            400: "invalid_argument",
            401: "unauthenticated",
            403: "permission_denied",
            404: "not_found",
            408: "deadline_exceeded",
            429: "RESOURCE_EXHAUSTED",
            500: "internal",
            503: "unavailable",
        }
        for provider, (adapter_class, capability, model) in self.PROVIDERS.items():
            for status, error_code in error_codes.items():
                with (
                    self.subTest(provider=provider, status=status),
                    tempfile.TemporaryDirectory() as d,
                ):
                    root = Path(d)
                    controller = self.controller(root, provider, capability, model)
                    secret = "fake-provider-credential-value-123456789"
                    body = json.dumps(
                        {"error": {"status": error_code, "message": f"failure {secret}"}}
                    ).encode()

                    def transport(request, credential=secret):
                        return _post_json(
                            "https://provider.invalid/v1",
                            request,
                            {"Authorization": f"Bearer {credential}"},
                        )

                    adapter = adapter_class(transport)
                    with patch(
                        "urllib.request.urlopen", side_effect=self.http_error(status, body)
                    ) as network:
                        with self.assertRaises(ProviderFailure) as caught:
                            self.execute(controller, capability, adapter)
                        self.assertEqual(caught.exception.classification, classify_status(status))
                        with self.assertRaises(OperationBlocked):
                            self.execute(controller, capability, adapter)
                    self.assertEqual(network.call_count, 1)
                    with controller.store.connect() as db:
                        operation = db.execute("SELECT * FROM provider_operations").fetchone()
                        attempt = db.execute("SELECT * FROM provider_attempts").fetchone()
                        reservation = db.execute("SELECT * FROM budget_reservations").fetchone()
                        self.assertEqual(
                            db.execute(
                                "SELECT count(*) FROM provider_operations WHERE provider<>?",
                                (provider,),
                            ).fetchone()[0],
                            0,
                        )
                    self.assertEqual(attempt["http_status"], status)
                    self.assertEqual(attempt["reservation_status"], "CLOSED_BILLING_UNKNOWN")
                    self.assertEqual(attempt["billing_uncertain"], 1)
                    self.assertEqual(reservation["status"], "CLOSED_BILLING_UNKNOWN")
                    self.assertEqual(operation["error_path"], attempt["error_path"])
                    envelope_bytes = Path(attempt["error_path"]).read_bytes()
                    self.assertNotIn(secret.encode(), envelope_bytes)
                    envelope = json.loads(envelope_bytes)
                    self.assertEqual(envelope["provider"], provider)
                    self.assertEqual(envelope["model"], model)
                    self.assertEqual(envelope["http_status"], status)
                    self.assertEqual(envelope["attempt_id"], attempt["attempt_id"])
                    self.assertTrue(envelope["request_fingerprint"].startswith("sha256:"))
                    self.assertEqual(envelope["provider_error_code"], error_code)
                    self.assertEqual(envelope["provider_error_message"], "failure [REDACTED]")
                    self.assertNotIn("set-cookie", envelope["headers"])
                    self.assertNotIn("authorization", envelope["headers"])

    def test_empty_and_malformed_429_bodies_are_still_checkpointed(self):
        for body in (b"", b"not-json"):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                controller = self.controller(root, "google", "WEB_GROUNDED_RESEARCH", "gemini-test")
                adapter = GoogleAdapter(
                    lambda request: _post_json("https://google.invalid", request, {})
                )
                with (
                    patch("urllib.request.urlopen", side_effect=self.http_error(429, body)),
                    self.assertRaises(ProviderFailure),
                ):
                    self.execute(controller, "WEB_GROUNDED_RESEARCH", adapter)
                with controller.store.connect() as db:
                    path = Path(
                        db.execute("SELECT error_path FROM provider_attempts").fetchone()[0]
                    )
                envelope = json.loads(path.read_bytes())
                self.assertEqual(envelope["raw_body_text"], body.decode())
                self.assertIsNone(envelope["provider_error_code"])
                self.assertIsNone(envelope["provider_error_message"])

    def test_network_failure_has_no_fake_response_and_is_held_ambiguous(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            controller = self.controller(root, "google", "WEB_GROUNDED_RESEARCH", "gemini-test")
            adapter = GoogleAdapter(
                lambda request: _post_json("https://google.invalid", request, {})
            )
            with patch(
                "urllib.request.urlopen", side_effect=urllib.error.URLError("offline")
            ) as network:
                with self.assertRaises(AmbiguousOperation):
                    self.execute(controller, "WEB_GROUNDED_RESEARCH", adapter)
                with self.assertRaises(AmbiguousOperation):
                    self.execute(controller, "WEB_GROUNDED_RESEARCH", adapter)
            self.assertEqual(network.call_count, 1)
            with controller.store.connect() as db:
                attempt = db.execute("SELECT * FROM provider_attempts").fetchone()
                reservation = db.execute("SELECT status FROM budget_reservations").fetchone()[0]
            self.assertIsNone(attempt["error_path"])
            self.assertEqual(attempt["reservation_status"], "HELD_AMBIGUOUS")
            self.assertEqual(reservation, "HELD_AMBIGUOUS")

    def test_checkpoint_failure_after_http_response_blocks_resubmission(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            controller = self.controller(root, "google", "WEB_GROUNDED_RESEARCH", "gemini-test")
            adapter = GoogleAdapter(
                lambda request: _post_json("https://google.invalid", request, {})
            )
            with (
                patch("urllib.request.urlopen", side_effect=self.http_error(429, b"{}")) as network,
                patch.object(
                    controller.artifacts, "checkpoint_error", side_effect=OSError("disk full")
                ),
            ):
                with self.assertRaises(AmbiguousOperation):
                    self.execute(controller, "WEB_GROUNDED_RESEARCH", adapter)
                with self.assertRaises(AmbiguousOperation):
                    self.execute(controller, "WEB_GROUNDED_RESEARCH", adapter)
            self.assertEqual(network.call_count, 1)
            with controller.store.connect() as db:
                self.assertEqual(
                    db.execute("SELECT state FROM provider_operations").fetchone()[0], "AMBIGUOUS"
                )
                self.assertEqual(
                    db.execute("SELECT reservation_status FROM provider_attempts").fetchone()[0],
                    "HELD_AMBIGUOUS",
                )

    def test_repeated_known_failures_close_each_attempt_without_active_leak(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            controller = self.controller(
                root, "google", "WEB_GROUNDED_RESEARCH", "gemini-test", attempts=3
            )
            adapter = GoogleAdapter(
                lambda request: _post_json("https://google.invalid", request, {})
            )
            with patch(
                "urllib.request.urlopen",
                side_effect=lambda *args, **kwargs: (_ for _ in ()).throw(
                    self.http_error(503, b'{"error":{"message":"unavailable"}}')
                ),
            ) as network:
                for _ in range(3):
                    with self.assertRaises(ProviderFailure):
                        self.execute(controller, "WEB_GROUNDED_RESEARCH", adapter)
                with self.assertRaises(OperationBlocked):
                    self.execute(controller, "WEB_GROUNDED_RESEARCH", adapter)
            self.assertEqual(network.call_count, 3)
            with controller.store.connect() as db:
                attempts = db.execute(
                    "SELECT reservation_status,billing_uncertain FROM provider_attempts"
                ).fetchall()
                active = db.execute(
                    "SELECT count(*) FROM provider_attempts WHERE reservation_status='RESERVED'"
                ).fetchone()[0]
            self.assertEqual(len(attempts), 3)
            self.assertEqual(active, 0)
            self.assertTrue(all(row[0] == "CLOSED_BILLING_UNKNOWN" for row in attempts))
            self.assertTrue(all(row[1] == 1 for row in attempts))
