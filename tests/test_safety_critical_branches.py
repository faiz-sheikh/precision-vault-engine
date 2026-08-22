from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from pve.artifacts import ArtifactRepository
from pve.controller import (
    AmbiguousOperation,
    BudgetExceeded,
    Controller,
    OperationBlocked,
)
from pve.manifest import (
    ManifestGovernance,
    ManifestInvalid,
    PromotionApprovalRequired,
    validate_envelope,
)
from pve.pipeline import PipelineRunner
from pve.providers import (
    FailureClass,
    GoogleAdapter,
    NvidiaAdapter,
    ProviderFailure,
    ProviderResult,
    _post_json,
)
from pve.settings import Settings
from pve.state import StateCorrupt, StateMissing, load_json_state
from pve.store import Store, utcnow
from pve.worker import Worker


class SafetyCriticalBranchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(state_dir=root / "state", artifact_dir=root / "artifacts")
        self.controller = Controller(self.settings)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def execute(self, adapter: GoogleAdapter, **changes):
        arguments = {
            "capability": "WEB_GROUNDED_RESEARCH",
            "request": {"x": 1},
            "prompt_hash": "p",
            "parameters": {},
            "schema": {},
            "upstream_hashes": [],
            "module_id": "mod_x",
            "phase": "research",
            "adapter": adapter,
            "parse": lambda raw: raw,
            "estimated_cost_usd": 0.01,
        }
        arguments.update(changes)
        return self.controller.execute_provider(**arguments)

    def test_adapter_route_mismatch_is_blocked_before_intent(self) -> None:
        with self.assertRaises(OperationBlocked):
            self.controller.execute_provider(
                capability="ROUTINE_GENERATION",
                request={},
                prompt_hash="p",
                parameters={},
                schema={},
                upstream_hashes=[],
                module_id="mod_x",
                phase="synthesis",
                adapter=GoogleAdapter(lambda request: ProviderResult(b"{}")),
                parse=json.loads,
            )
        with self.controller.store.connect() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM provider_operations").fetchone()[0], 0
            )

    def test_raw_checkpoint_checksum_mismatch_fails_closed(self) -> None:
        adapter = GoogleAdapter(lambda request: ProviderResult(b"original"))
        self.execute(adapter)
        with self.controller.store.connect() as db:
            path = Path(db.execute("SELECT raw_path FROM provider_operations").fetchone()[0])
        path.write_bytes(b"tampered")
        with self.assertRaises(OperationBlocked):
            self.execute(adapter)

    def test_cost_ceiling_rejects_second_operation(self) -> None:
        root = Path(self.temp.name)
        controller = Controller(
            Settings(
                state_dir=root / "cost-state",
                artifact_dir=root / "cost-artifacts",
                max_cost_usd=0.01,
            )
        )
        adapter = GoogleAdapter(lambda request: ProviderResult(b"ok"))
        base = dict(
            capability="WEB_GROUNDED_RESEARCH",
            prompt_hash="p",
            parameters={},
            schema={},
            upstream_hashes=[],
            phase="research",
            adapter=adapter,
            parse=lambda raw: raw,
            estimated_cost_usd=0.01,
        )
        controller.execute_provider(request={"x": 1}, module_id="mod_1", **base)
        with self.assertRaises(BudgetExceeded):
            controller.execute_provider(request={"x": 2}, module_id="mod_2", **base)

    def test_explicit_ambiguous_failure_never_resubmits(self) -> None:
        calls = []

        def ambiguous(request):
            calls.append(1)
            raise ProviderFailure("unknown acceptance", FailureClass.AMBIGUOUS)

        with self.assertRaises(AmbiguousOperation):
            self.execute(GoogleAdapter(ambiguous))
        with self.assertRaises(AmbiguousOperation):
            self.execute(GoogleAdapter(ambiguous))
        self.assertEqual(calls, [1])

    def test_http_error_and_timeout_classification(self) -> None:
        for status, expected in (
            (400, FailureClass.PERMANENT),
            (429, FailureClass.RATE_LIMIT_TRANSIENT),
            (503, FailureClass.TRANSIENT),
        ):
            error = urllib.error.HTTPError("https://invalid", status, "error", {}, io.BytesIO(b""))
            with patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaises(ProviderFailure) as caught:
                    GoogleAdapter(
                        lambda request: _post_json("https://invalid", request, {})
                    ).execute({})
                self.assertEqual(caught.exception.classification, expected)
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            with self.assertRaises(ProviderFailure) as caught:
                GoogleAdapter(lambda request: _post_json("https://invalid", request, {})).execute(
                    {}
                )
            self.assertEqual(caught.exception.classification, FailureClass.AMBIGUOUS)

    def test_artifact_metadata_atomic_publish(self) -> None:
        repository = ArtifactRepository(Path(self.temp.name) / "metadata")
        path = repository.publish_metadata("mod_x", "note", "art_x", {"status": "valid"})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"status": "valid"})
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_transaction_rolls_back_and_invalid_backup_fails_restore(self) -> None:
        with self.assertRaises(RuntimeError), self.controller.store.transaction() as db:
            db.execute(
                "INSERT INTO failures VALUES(?,?,?,?,?,?)",
                ("failure", "test", "x", "LOCAL", "rollback", utcnow()),
            )
            raise RuntimeError("inject rollback")
        with self.controller.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM failures").fetchone()[0], 0)
        invalid = Path(self.temp.name) / "invalid.sqlite3"
        invalid.write_bytes(b"invalid")
        self.assertFalse(Store.restore_check(invalid))

    def test_worker_failure_is_durable_and_rerunnable_only_by_policy(self) -> None:
        with self.controller.store.transaction() as db:
            db.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("job", None, "local", "READY", "{}", None, None, 0, utcnow(), utcnow()),
            )
        worker = Worker(self.controller.store)
        with self.assertRaisesRegex(RuntimeError, "local failure"):
            worker.run_job("job", lambda: (_ for _ in ()).throw(RuntimeError("local failure")))
        with self.controller.store.connect() as db:
            self.assertEqual(
                db.execute("SELECT state FROM jobs WHERE id='job'").fetchone()[0], "FAILED"
            )

    def test_state_missing_default_and_non_object_corruption(self) -> None:
        missing = Path(self.temp.name) / "missing.json"
        self.assertEqual(
            load_json_state(missing, default_when_missing={"safe": True}), {"safe": True}
        )
        with self.assertRaises(StateMissing):
            load_json_state(missing)
        non_object = Path(self.temp.name) / "list.json"
        non_object.write_text("[]", encoding="utf-8")
        with self.assertRaises(StateCorrupt):
            load_json_state(non_object)

    def test_manifest_validation_and_approval_fail_closed_branches(self) -> None:
        self.assertIn("modules must be an array", validate_envelope({"modules": {}}))
        errors = validate_envelope(
            {
                "schema_version": "1",
                "manifest_id": "m",
                "manifest_version": 1,
                "source_set_id": "s",
                "modules": [None, {"module_id": "bad", "title": "x"}],
                "checksum": "wrong",
            }
        )
        self.assertTrue(any("invalid permanent" in error for error in errors))
        self.assertIn("checksum mismatch", errors)

        governance = ManifestGovernance(self.controller.store)
        with self.assertRaises(ValueError):
            governance.record_approval(
                action="SOURCE_AUTHORITY", actor="", reason="", evidence={}, input_hashes=[]
            )
        with self.assertRaises(ValueError):
            governance.promote("missing", None, approval_id="approval")
        approval = governance.record_approval(
            action="MANIFEST_PROMOTION",
            actor="operator",
            reason="test",
            evidence={},
            input_hashes=[],
        )
        with self.assertRaises(ManifestInvalid):
            governance.promote(
                "missing", Path(self.temp.name) / "manifest.json", approval_id=approval
            )

    def test_source_authorization_requires_hash_bound_approval(self) -> None:
        governance = ManifestGovernance(self.controller.store)
        source_id = governance.register_source(content=b"source", source_type="TXT", origin="test")
        with self.assertRaises(PromotionApprovalRequired):
            governance.authorize_source(source_id, approval_id="missing")
        with self.controller.store.connect() as db:
            content_hash = db.execute(
                "SELECT content_hash FROM sources WHERE source_id=?", (source_id,)
            ).fetchone()[0]
        approval = governance.record_approval(
            action="SOURCE_AUTHORITY",
            actor="operator",
            reason="verified fixture",
            evidence={},
            input_hashes=[content_hash],
        )
        governance.authorize_source(source_id, approval_id=approval)
        with self.controller.store.connect() as db:
            self.assertEqual(
                db.execute("SELECT status FROM sources WHERE source_id=?", (source_id,)).fetchone()[
                    0
                ],
                "APPROVED",
            )

    def test_truncated_synthesis_never_creates_downstream_artifact_or_recalls(self) -> None:
        calls = []

        def transport(request):
            calls.append(request["phase"])
            content = (
                "# Overview\nDraft..." if request["phase"] == "synthesis" else "# Overview\nOK"
            )
            return ProviderResult(json.dumps({"content": content}).encode())

        runner = PipelineRunner(
            self.controller,
            {
                "WEB_GROUNDED_RESEARCH": GoogleAdapter(transport),
                "ROUTINE_GENERATION": NvidiaAdapter(transport),
                "QUALITY_REVIEW": NvidiaAdapter(transport),
            },
        )
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "probable truncation"):
                runner.run_module("mod_truncated", "source")
        self.assertEqual(calls, ["research", "synthesis"])
        with self.controller.store.connect() as db:
            phases = [row[0] for row in db.execute("SELECT phase FROM artifacts")]
        self.assertEqual(sorted(phases), ["curriculum", "research", "sources"])
