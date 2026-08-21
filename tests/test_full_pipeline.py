from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pve.controller import Controller
from pve.pipeline import PHASES, PipelineRunner
from pve.providers import GoogleAdapter, NvidiaAdapter, ProviderResult
from pve.settings import Settings


class FullPipelineTests(unittest.TestCase):
    def test_all_phases_and_three_run_zero_call_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(
                Settings(
                    state_dir=Path(directory) / "state", artifact_dir=Path(directory) / "artifacts"
                )
            )
            calls = []

            def transport(request):
                calls.append(request["phase"])
                payload = {
                    "phase": request["phase"],
                    "content": "# Overview\nValidated mocked content",
                }
                return ProviderResult(json.dumps(payload).encode())

            runner = PipelineRunner(
                controller,
                {
                    "WEB_GROUNDED_RESEARCH": GoogleAdapter(transport),
                    "ROUTINE_GENERATION": NvidiaAdapter(transport),
                    "QUALITY_REVIEW": NvidiaAdapter(transport),
                },
            )
            first = runner.run_module("mod_pipeline", "source-v1")
            second = runner.run_module("mod_pipeline", "source-v1")
            third = runner.run_module("mod_pipeline", "source-v1")
            self.assertEqual(set(PHASES), set(first) - {"provider_calls"})
            self.assertEqual(first["provider_calls"], 7)
            self.assertEqual(second["provider_calls"], 0)
            self.assertEqual(third["provider_calls"], 0)
            self.assertEqual(len(calls), 7)
            self.assertTrue(all(second[p]["reused"] for p in PHASES))

    def test_changed_source_selectively_creates_new_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(
                Settings(
                    state_dir=Path(directory) / "state", artifact_dir=Path(directory) / "artifacts"
                )
            )
            calls = []

            def transport(request):
                calls.append(request)
                return ProviderResult(
                    json.dumps(
                        {
                            "phase": request["phase"],
                            "dependency": request["dependency"],
                            "content": "# Overview\nValidated mocked content",
                        }
                    ).encode()
                )

            runner = PipelineRunner(
                controller,
                {
                    "WEB_GROUNDED_RESEARCH": GoogleAdapter(transport),
                    "ROUTINE_GENERATION": NvidiaAdapter(transport),
                    "QUALITY_REVIEW": NvidiaAdapter(transport),
                },
            )
            runner.run_module("mod_a", "source-a")
            runner.run_module("mod_b", "source-b")
            before = len(calls)
            runner.run_module("mod_a", "source-a2")
            self.assertEqual(len(calls) - before, 7)
            runner.run_module("mod_b", "source-b")
            self.assertEqual(len(calls) - before, 7)
