from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from pve.providers import (
    FailureClass,
    ProviderFailure,
    google_live_adapter,
    openai_compatible_live_adapter,
    openai_responses_live_adapter,
)
from pve.safety import PaidExecutionBlocked, Provider


class ConcreteProviderOfflineTests(unittest.TestCase):
    def test_every_live_adapter_kill_switch_prevents_urlopen(self):
        adapters = (
            google_live_adapter(),
            openai_compatible_live_adapter(Provider.NVIDIA, "https://nvidia.invalid/v1"),
            openai_responses_live_adapter("https://openai.invalid/v1"),
        )
        with patch.dict(os.environ, {}, clear=True), patch("urllib.request.urlopen") as network:
            for adapter in adapters:
                with (
                    self.subTest(provider=adapter.provider),
                    self.assertRaises(PaidExecutionBlocked),
                ):
                    adapter.execute({"model": "m", "messages": []})
            network.assert_not_called()

    def test_missing_credentials_fail_before_network_even_when_switches_enabled(self):
        cases = (
            (google_live_adapter(), "PVE_ALLOW_GOOGLE_LIVE"),
            (
                openai_compatible_live_adapter(Provider.NVIDIA, "https://n.invalid/v1"),
                "PVE_ALLOW_NVIDIA_LIVE",
            ),
            (
                openai_compatible_live_adapter(Provider.OPENAI, "https://o.invalid/v1"),
                "PVE_ALLOW_OPENAI_LIVE",
            ),
            (openai_responses_live_adapter("https://o.invalid/v1"), "PVE_ALLOW_OPENAI_LIVE"),
        )
        with patch("urllib.request.urlopen") as network:
            for adapter, switch in cases:
                with patch.dict(
                    os.environ, {"PVE_ALLOW_LIVE_PROVIDERS": "1", switch: "1"}, clear=True
                ):
                    with self.assertRaises(ProviderFailure) as caught:
                        adapter.execute({"model": "m", "messages": []})
                    self.assertEqual(caught.exception.classification, FailureClass.PERMANENT)
            network.assert_not_called()

    def test_request_construction_and_secret_not_in_body(self):
        response = MagicMock()
        response.read.return_value = (
            b'{"id":"provider-1","ok":true,"usage":{"prompt_tokens":2,"completion_tokens":3}}'
        )
        response.headers = {}
        response.__enter__.return_value = response
        env = {
            "PVE_ALLOW_LIVE_PROVIDERS": "1",
            "PVE_ALLOW_NVIDIA_LIVE": "1",
            "NVIDIA_API_KEY": "secret-test-value",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("urllib.request.urlopen", return_value=response) as network,
        ):
            result = openai_compatible_live_adapter(
                Provider.NVIDIA, "https://n.invalid/v1"
            ).execute({"model": "nemotron", "messages": []})
        request = network.call_args.args[0]
        self.assertEqual(request.full_url, "https://n.invalid/v1/chat/completions")
        self.assertNotIn(b"secret-test-value", request.data)
        self.assertEqual(result.provider_id, "provider-1")
        self.assertEqual(result.usage, {"prompt_tokens": 2, "completion_tokens": 3})

    def test_google_and_openai_responses_request_construction(self):
        response = MagicMock()
        response.read.return_value = b'{"id":"response-1","usageMetadata":{"totalTokenCount":4}}'
        response.headers = {}
        response.__enter__.return_value = response
        cases = (
            (
                google_live_adapter("https://google.invalid/v1beta"),
                {
                    "PVE_ALLOW_LIVE_PROVIDERS": "1",
                    "PVE_ALLOW_GOOGLE_LIVE": "1",
                    "GEMINI_API_KEY": "fake-google-test-value",
                },
                {"model": "gemini-test", "contents": []},
                "https://google.invalid/v1beta/models/gemini-test:generateContent",
                b"fake-google-test-value",
            ),
            (
                openai_responses_live_adapter("https://openai.invalid/v1"),
                {
                    "PVE_ALLOW_LIVE_PROVIDERS": "1",
                    "PVE_ALLOW_OPENAI_LIVE": "1",
                    "OPENAI_API_KEY": "fake-openai-test-value",
                },
                {"model": "gpt-5.6-sol", "input": "bounded test", "store": False},
                "https://openai.invalid/v1/responses",
                b"fake-openai-test-value",
            ),
        )
        for adapter, env, payload, expected_url, secret in cases:
            with (
                self.subTest(provider=adapter.provider),
                patch.dict(os.environ, env, clear=True),
                patch("urllib.request.urlopen", return_value=response) as network,
            ):
                result = adapter.execute(payload)
            request = network.call_args.args[0]
            self.assertEqual(request.full_url, expected_url)
            self.assertNotIn(secret, request.data)
            self.assertEqual(result.provider_id, "response-1")
            self.assertEqual(result.usage, {"totalTokenCount": 4})

    def test_malformed_envelope_is_preserved_without_usage_guessing(self):
        response = MagicMock()
        response.read.return_value = b"not-json"
        response.headers = {}
        response.__enter__.return_value = response
        env = {
            "PVE_ALLOW_LIVE_PROVIDERS": "1",
            "PVE_ALLOW_OPENAI_LIVE": "1",
            "OPENAI_API_KEY": "fake-test-value",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("urllib.request.urlopen", return_value=response),
        ):
            result = openai_compatible_live_adapter(
                Provider.OPENAI, "https://openai.invalid/v1"
            ).execute({"model": "gpt-5.6-sol", "messages": []})
        self.assertEqual(result.raw, b"not-json")
        self.assertIsNone(result.usage)
