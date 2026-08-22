from __future__ import annotations

import unittest
from unittest.mock import patch

from pve.providers import FailureClass, LegacyAnthropicAdapter, ProviderResult, classify_status
from pve.safety import PaidExecutionBlocked
from pve.settings import Settings


class ProviderFailureMatrixTests(unittest.TestCase):
    def test_status_matrix(self):
        cases = {
            400: FailureClass.PERMANENT,
            401: FailureClass.AUTH_BLOCKED,
            403: FailureClass.ACCESS_BLOCKED,
            404: FailureClass.PERMANENT,
            408: FailureClass.TRANSIENT,
            409: FailureClass.TRANSIENT,
            425: FailureClass.TRANSIENT,
            429: FailureClass.RATE_LIMIT_TRANSIENT,
            500: FailureClass.TRANSIENT,
            502: FailureClass.TRANSIENT,
            503: FailureClass.TRANSIENT,
            504: FailureClass.TRANSIENT,
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.assertEqual(classify_status(status), expected)

    def test_unknown_acceptance_is_always_ambiguous(self):
        for status in (None, 200, 400, 429, 500):
            self.assertEqual(classify_status(status, accepted_unknown=True), FailureClass.AMBIGUOUS)

    def test_capability_routes_use_exact_pre_canary_models(self):
        routes = Settings().routes
        expected = {
            "WEB_GROUNDED_RESEARCH": ("google", "gemini-3.7-flash"),
            "ROUTINE_GENERATION": ("nvidia", "nvidia/nemotron-3-super-120b-a12b"),
            "QUALITY_REVIEW": ("nvidia", "nvidia/nemotron-3-super-120b-a12b"),
            "ENGINEERING_REASONING": ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
            "ENGINEERING_REVIEW": ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
            "FRONTIER_ESCALATION": ("openai", "gpt-5.6-sol"),
        }
        self.assertEqual(
            {capability: (route.provider, route.model) for capability, route in routes.items()},
            expected,
        )

    def test_legacy_anthropic_adapter_is_compatibility_only_and_kill_switched(self):
        calls = []
        adapter = LegacyAnthropicAdapter(
            lambda request: (calls.append(request), ProviderResult(b"{}"))[1], live=True
        )
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(PaidExecutionBlocked):
            adapter.execute({"model": "legacy-test"})
        self.assertEqual(calls, [])

        offline = LegacyAnthropicAdapter(
            lambda request: (calls.append(request), ProviderResult(b'{"ok":true}'))[1]
        )
        self.assertEqual(offline.execute({"model": "legacy-test"}).raw, b'{"ok":true}')
        self.assertEqual(len(calls), 1)
