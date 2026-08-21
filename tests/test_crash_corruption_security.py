from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pve.artifacts import ArtifactRepository, UnsafeArtifactPath
from pve.controller import AmbiguousOperation, BudgetExceeded, Controller, OperationBlocked
from pve.providers import FailureClass, GoogleAdapter, ProviderFailure, ProviderResult
from pve.settings import Settings
from pve.store import Store, StoreCorrupt


class CrashCorruptionSecurityTests(unittest.TestCase):
    def setup_controller(self, settings=None):
        self.temp=tempfile.TemporaryDirectory(); root=Path(self.temp.name)
        return Controller(settings or Settings(state_dir=root/"state",artifact_dir=root/"artifacts"))
    def tearDown(self):
        if hasattr(self,"temp"): self.temp.cleanup()

    def execute(self, controller, adapter, parse=lambda raw:raw, estimated=.01):
        return controller.execute_provider(capability="WEB_GROUNDED_RESEARCH",request={"x":1},prompt_hash="p",
            parameters={},schema={},upstream_hashes=[],module_id="mod_x",phase="research",adapter=adapter,
            parse=parse,estimated_cost_usd=estimated)

    def test_crash_before_raw_checkpoint_blocks_resubmit(self):
        controller=self.setup_controller(); calls=[]
        adapter=GoogleAdapter(lambda req:(calls.append(1) or ProviderResult(b"paid-response")))
        with patch.object(controller.artifacts,"checkpoint_raw",side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.execute(controller,adapter)
        with self.assertRaises(AmbiguousOperation): self.execute(controller,adapter)
        self.assertEqual(calls,[1])

    def test_malformed_response_parser_does_not_recall(self):
        controller=self.setup_controller(); calls=[]
        adapter=GoogleAdapter(lambda req:(calls.append(1) or ProviderResult(b"not-json")))
        parse=lambda raw: (_ for _ in ()).throw(ValueError("malformed"))
        for _ in range(2):
            with self.assertRaises(ValueError): self.execute(controller,adapter,parse=parse)
        self.assertEqual(calls,[1])

    def test_transient_retries_are_bounded_across_restarts(self):
        controller=self.setup_controller(); calls=[]
        def transient(req): calls.append(1); raise ProviderFailure("429",FailureClass.TRANSIENT)
        for _ in range(controller.settings.max_provider_attempts):
            with self.assertRaises(ProviderFailure): self.execute(controller,GoogleAdapter(transient))
        with self.assertRaises(OperationBlocked): self.execute(controller,GoogleAdapter(transient))
        self.assertEqual(len(calls),controller.settings.max_provider_attempts)

    def test_unknown_pricing_blocked_under_cost_cap(self):
        self.temp=tempfile.TemporaryDirectory(); root=Path(self.temp.name)
        controller=Controller(Settings(state_dir=root/"state",artifact_dir=root/"artifacts",max_cost_usd=1.0))
        with self.assertRaises(BudgetExceeded): self.execute(controller,GoogleAdapter(lambda req:ProviderResult(b"x")),estimated=None)

    def test_database_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"bad.sqlite3"; path.write_bytes(b"not a sqlite database")
            with self.assertRaises(StoreCorrupt): Store(path).integrity_check()

    def test_symlink_escape_is_rejected(self):
        if not hasattr(Path,"symlink_to"): self.skipTest("symlink unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/"artifacts"; outside=Path(directory)/"outside"; outside.mkdir(); root.mkdir()
            (root/"mod").symlink_to(outside,target_is_directory=True)
            with self.assertRaises(UnsafeArtifactPath): ArtifactRepository(root).confined("mod","phase","id")

    def test_dashboard_refresh_creates_no_events(self):
        controller=self.setup_controller()
        from pve.dashboard import snapshot
        for _ in range(20): snapshot(controller.store)
        with controller.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM provider_events").fetchone()[0],0)
