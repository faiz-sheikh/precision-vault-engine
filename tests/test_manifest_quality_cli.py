from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from pve.cli import main
from pve.controller import Controller
from pve.dashboard import SECTIONS, snapshot
from pve.manifest import ManifestGovernance, PromotionApprovalRequired, validate_envelope
from pve.prompting import load_prompt
from pve.quality import grounding_map, remediation, technical_risk, validate_markdown
from pve.settings import Settings


class GovernanceQualityCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.controller = Controller(Settings.from_env(self.root))
    def tearDown(self): self.temp.cleanup()

    def envelope(self):
        return {"schema_version":"1.0.0","manifest_id":"man_1","manifest_version":1,
                "source_set_id":"set_1","modules":[{"module_id":"mod_a","title":"Sensors & Actuators","domain":"04_Electronics","grade_or_sem":"Sem 1"}]}

    def test_candidate_validates_and_promotion_is_blocked(self):
        candidate, errors = ManifestGovernance(self.controller.store).create_candidate(self.envelope())
        self.assertTrue(candidate.startswith("cand_")); self.assertEqual(errors, [])
        with self.assertRaises(PromotionApprovalRequired): ManifestGovernance(self.controller.store).promote(candidate)

    def test_semantic_duplicate_is_rejected(self):
        value = self.envelope(); value["modules"].append({"module_id":"mod_b","title":"Sensors and Actuators","domain":"04_Electronics","grade_or_sem":"Sem 1"})
        self.assertTrue(any("semantic duplicate" in e for e in validate_envelope(value)))

    def test_source_registration_is_content_idempotent(self):
        gov = ManifestGovernance(self.controller.store)
        one = gov.register_source(content=b"abc",source_type="DOCX",origin="x")
        two = gov.register_source(content=b"abc",source_type="DOCX",origin="x")
        self.assertEqual(one, two)

    def test_deterministic_validation_detects_truncation_formula_and_missing(self):
        result = validate_markdown("# Other\n$$ broken...")
        self.assertFalse(result.valid); self.assertGreaterEqual(len(result.errors), 3)

    def test_grounding_is_separate_from_quality(self):
        mapped = grounding_map(["Kalman filter estimates system state"], {"e1":"A Kalman filter estimates the state of a system", "e2":"unrelated"})
        self.assertEqual(mapped["Kalman filter estimates system state"], ["e1"])

    def test_technical_risk_and_remediation(self):
        self.assertEqual(technical_risk("derive $$x$$ $$y$$ $$z$$ jacobian"), "HIGH")
        self.assertEqual(remediation(.2, validate_markdown("# Overview\nValid")), "PREREQUISITE_REVISION_AND_TARGETED_PRACTICE")

    def test_prompt_is_versioned_and_hashed(self):
        prompt = load_prompt("research")
        self.assertEqual(prompt.version, 1); self.assertTrue(prompt.hash.startswith("sha256:"))
        self.assertIn("untrusted", prompt.text)

    def test_dashboard_snapshot_is_read_only_and_complete(self):
        before = snapshot(self.controller.store); after = snapshot(self.controller.store)
        self.assertEqual(before, after); self.assertEqual(tuple(before["sections"]), SECTIONS)

    def test_cli_status_preview_and_execution_no_work(self):
        for argv in (["--root",str(self.root),"status"], ["--root",str(self.root),"preview","--manifest-checksum","sha"], ["--root",str(self.root),"run"]):
            output = io.StringIO()
            with redirect_stdout(output): code = main(argv)
            self.assertEqual(code, 0); self.assertTrue(json.loads(output.getvalue()))
