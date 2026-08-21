from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pve.controller import Controller
from pve.providers import GoogleAdapter, NvidiaAdapter, OpenAIAdapter, ProviderResult
from pve.settings import Settings


class MockedEndToEndZeroCallTests(unittest.TestCase):
    def test_three_identical_runs_call_each_provider_only_on_first(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(state_dir=Path(directory)/"state", artifact_dir=Path(directory)/"artifacts")
            controller = Controller(settings); calls = {"google":0,"nvidia":0,"openai":0}
            def adapter(kind, cls):
                def transport(request):
                    calls[kind] += 1
                    return ProviderResult(json.dumps({"provider":kind,"phase":request.get("phase")}).encode())
                return cls(transport)
            adapters = {"WEB_GROUNDED_RESEARCH":adapter("google",GoogleAdapter),
                        "ROUTINE_GENERATION":adapter("nvidia",NvidiaAdapter),
                        "FRONTIER_ESCALATION":adapter("openai",OpenAIAdapter)}
            def run():
                reused = []
                for phase, capability in (("research","WEB_GROUNDED_RESEARCH"),("synthesis","ROUTINE_GENERATION"),("escalation","FRONTIER_ESCALATION")):
                    result, was_reused = controller.execute_provider(
                        capability=capability, request={"phase":phase,"input":"unchanged"}, prompt_hash="prompt-v1",
                        parameters={}, schema={"type":"object"}, upstream_hashes=["source-v1"], module_id="mod_e2e",
                        phase=phase, adapter=adapters[capability], parse=lambda raw:json.loads(raw), estimated_cost_usd=0.01)
                    reused.append(was_reused)
                return reused
            self.assertEqual(run(), [False,False,False])
            self.assertEqual(run(), [True,True,True])
            self.assertEqual(run(), [True,True,True])
            self.assertEqual(calls, {"google":1,"nvidia":1,"openai":1})
