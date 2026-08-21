from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from pve.canary import APPROVAL_TOKEN, CANARY_INPUTS, canonical_request, main
from pve.safety import PaidExecutionBlocked


class CanaryOfflineTests(unittest.TestCase):
    def test_every_canary_preview_is_deterministic_and_network_free(self) -> None:
        for capability in CANARY_INPUTS:
            arguments = ["preview", "--capability", capability]
            first = io.StringIO()
            second = io.StringIO()
            with patch("urllib.request.urlopen") as network:
                with redirect_stdout(first):
                    self.assertEqual(main(arguments), 0)
                with redirect_stdout(second):
                    self.assertEqual(main(arguments), 0)
                network.assert_not_called()
            self.assertEqual(json.loads(first.getvalue()), json.loads(second.getvalue()))
            self.assertEqual(json.loads(first.getvalue())["max_requests"], 1)

    def test_execute_requires_separate_approval_even_if_provider_switches_are_on(self) -> None:
        env = {
            "PVE_ALLOW_LIVE_PROVIDERS": "1",
            "PVE_ALLOW_GOOGLE_LIVE": "1",
            "GEMINI_API_KEY": "fake-test-value",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("urllib.request.urlopen") as network,
            self.assertRaises(SystemExit),
        ):
            main(["execute", "--capability", "WEB_GROUNDED_RESEARCH"])
        network.assert_not_called()

    def test_approval_token_still_cannot_bypass_provider_kill_switch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {
                "PVE_STATE_DIR": directory + "/state",
                "PVE_ARTIFACT_DIR": directory + "/artifacts",
                "PVE_CANARY_APPROVAL": APPROVAL_TOKEN,
                "GEMINI_API_KEY": "fake-test-value",
            }
            with (
                patch.dict(os.environ, env, clear=True),
                patch("urllib.request.urlopen") as network,
                self.assertRaises(PaidExecutionBlocked),
            ):
                main(
                    [
                        "execute",
                        "--capability",
                        "WEB_GROUNDED_RESEARCH",
                        "--approval-token",
                        APPROVAL_TOKEN,
                    ]
                )
            network.assert_not_called()

    def test_request_shapes_are_provider_specific_and_bounded(self) -> None:
        google = canonical_request("WEB_GROUNDED_RESEARCH", "x", 128)
        nvidia = canonical_request("ROUTINE_GENERATION", "x", 128)
        openai = canonical_request("FRONTIER_ESCALATION", "x", 128)
        self.assertEqual(google["tools"], [{"google_search": {}}])
        self.assertEqual(nvidia["response_format"], {"type": "json_object"})
        self.assertEqual(openai["reasoning"], {"effort": "low"})
        self.assertFalse(openai["store"])

    def test_verify_mode_is_network_free_when_operation_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {
                "PVE_STATE_DIR": directory + "/state",
                "PVE_ARTIFACT_DIR": directory + "/artifacts",
            }
            output = io.StringIO()
            with (
                patch.dict(os.environ, env, clear=True),
                patch("urllib.request.urlopen") as network,
                redirect_stdout(output),
            ):
                self.assertEqual(main(["verify", "--capability", "FRONTIER_ESCALATION"]), 0)
                network.assert_not_called()
            self.assertFalse(json.loads(output.getvalue())["found"])
