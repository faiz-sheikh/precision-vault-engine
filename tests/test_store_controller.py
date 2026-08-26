from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from pve.artifacts import ArtifactRepository, UnsafeArtifactPath
from pve.controller import AmbiguousOperation, Controller, OperationBlocked, StalePreview
from pve.providers import FailureClass, GoogleAdapter, ProviderFailure, ProviderResult
from pve.safety import PaidExecutionBlocked
from pve.settings import Settings
from pve.store import FenceRejected, Store, utcnow


class StoreControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(state_dir=root / "state", artifact_dir=root / "artifacts")
        self.controller = Controller(self.settings)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def call(self, adapter, parse=lambda raw: json.loads(raw)):
        return self.controller.execute_provider(
            capability="WEB_GROUNDED_RESEARCH",
            request={"contents": "tiny"},
            prompt_hash="p1",
            parameters={"temperature": 0},
            schema={"type": "object"},
            upstream_hashes=["s1"],
            module_id="mod_1",
            phase="research",
            adapter=adapter,
            parse=parse,
        )

    def test_success_is_checkpointed_and_reused_without_second_call(self) -> None:
        calls = []
        adapter = GoogleAdapter(
            lambda request: (
                calls.append(request) or ProviderResult(b'{"ok":true}', "pid", {"tokens": 2})
            )
        )
        self.assertEqual(self.call(adapter), ({"ok": True}, False))
        self.assertEqual(self.call(adapter), ({"ok": True}, True))
        self.assertEqual(len(calls), 1)
        with self.controller.store.connect() as db:
            events = [
                r[0] for r in db.execute("SELECT event_type FROM provider_events ORDER BY sequence")
            ]
        self.assertEqual(events, ["INTENT_CREATED", "RESERVED", "CALLING", "SUCCEEDED"])

    def test_parser_crash_reuses_raw_without_provider_recall(self) -> None:
        calls = []
        adapter = GoogleAdapter(lambda request: (calls.append(1) or ProviderResult(b'{"ok":true}')))
        with self.assertRaises(ValueError):
            self.call(adapter, lambda raw: (_ for _ in ()).throw(ValueError("parser crash")))
        result, reused = self.call(adapter)
        self.assertTrue(result["ok"])
        self.assertTrue(reused)
        self.assertEqual(len(calls), 1)

    def test_unknown_transport_error_becomes_ambiguous_and_never_resubmits(self) -> None:
        calls = []
        adapter = GoogleAdapter(
            lambda request: (calls.append(1) or (_ for _ in ()).throw(TimeoutError()))
        )
        with self.assertRaises(AmbiguousOperation):
            self.call(adapter)
        with self.assertRaises(AmbiguousOperation):
            self.call(adapter)
        self.assertEqual(len(calls), 1)

    def test_permanent_failure_does_not_retry_unchanged(self) -> None:
        calls = []

        def fail(request):
            calls.append(1)
            raise ProviderFailure("bad request", FailureClass.PERMANENT)

        with self.assertRaises(ProviderFailure):
            self.call(GoogleAdapter(fail))
        with self.assertRaises(OperationBlocked):
            self.call(GoogleAdapter(fail))
        self.assertEqual(len(calls), 1)

    def test_live_adapter_is_blocked_before_transport(self) -> None:
        calls = []
        adapter = GoogleAdapter(lambda request: calls.append(1), live=True)
        with self.assertRaises(PaidExecutionBlocked):
            self.call(adapter)
        self.assertEqual(calls, [])
        with self.controller.store.connect() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM provider_operations").fetchone()[0], 0
            )

    def test_completed_live_operation_reuses_with_every_switch_off(self) -> None:
        calls = []
        adapter = GoogleAdapter(
            lambda request: (calls.append(request) or ProviderResult(b'{"ok":true}')),
            live=True,
        )
        enabled = {"PVE_ALLOW_LIVE_PROVIDERS": "1", "PVE_ALLOW_GOOGLE_LIVE": "1"}
        with patch.dict(os.environ, enabled, clear=True):
            self.assertEqual(self.call(adapter), ({"ok": True}, False))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.call(adapter), ({"ok": True}, True))
        self.assertEqual(len(calls), 1)

    def test_events_are_append_only(self) -> None:
        self.call(GoogleAdapter(lambda request: ProviderResult(b"{}")))
        with self.assertRaises(sqlite3.IntegrityError), self.controller.store.transaction() as db:
            db.execute("DELETE FROM provider_events")

    def test_backup_is_verified(self) -> None:
        destination = Path(self.temp.name) / "backup.sqlite3"
        result = self.controller.store.backup(destination)
        self.assertEqual(result["restore_test_result"], "PASS")
        self.assertTrue(Store.restore_check(destination))
        restored = Store(destination)
        restored.integrity_check()
        with restored.connect() as db:
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            self.assertEqual(
                db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0], 4
            )

    def test_migrations_are_idempotent_and_foreign_keys_reject_orphans(self) -> None:
        self.controller.store.initialize()
        self.controller.store.initialize()
        with self.controller.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], 4)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO provider_events(operation_id,event_type,payload_json,created_at) "
                    "VALUES('missing','CALLING','{}',?)",
                    (utcnow(),),
                )

    def test_version_one_provider_operations_migrate_without_losing_evidence(self) -> None:
        legacy_path = Path(self.temp.name) / "legacy-v1.sqlite3"
        with sqlite3.connect(legacy_path) as db:
            db.executescript(
                "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);"
                "INSERT INTO schema_migrations VALUES(1,'historical');"
                "CREATE TABLE provider_operations("
                "id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL UNIQUE,provider TEXT NOT NULL,"
                "model TEXT NOT NULL,capability TEXT NOT NULL,state TEXT NOT NULL,"
                "request_json TEXT NOT NULL,raw_path TEXT,raw_sha256 TEXT,parsed_json TEXT,"
                "provider_id TEXT,estimated_cost_usd REAL,actual_cost_usd REAL,"
                "attempts INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);"
                "CREATE TABLE budget_reservations("
                "id TEXT PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE REFERENCES provider_operations(id),"
                "requests INTEGER NOT NULL,estimated_cost_usd REAL,status TEXT NOT NULL,created_at TEXT NOT NULL);"
                "INSERT INTO provider_operations(id,fingerprint,provider,model,capability,state,"
                "request_json,attempts,created_at,updated_at) VALUES("
                "'historical-op','historical-fp','google','legacy-model','research',"
                "'FAILED_TRANSIENT','{}',1,'historical','historical');"
                "INSERT INTO budget_reservations VALUES("
                "'historical-res','historical-op',1,0.01,'RESERVED','historical');"
            )
        migrated = Store(legacy_path)
        migrated.initialize()
        with migrated.connect() as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(provider_operations)")}
            self.assertTrue({"error_path", "error_sha256"}.issubset(columns))
            self.assertEqual(
                db.execute(
                    "SELECT state FROM provider_operations WHERE id='historical-op'"
                ).fetchone()[0],
                "FAILED_TRANSIENT",
            )
            self.assertEqual(
                [
                    row[0]
                    for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")
                ],
                [1, 2, 3, 4],
            )
            attempt = db.execute("SELECT * FROM provider_attempts").fetchone()
            self.assertEqual(attempt["attempt_number"], 1)
            self.assertEqual(attempt["state"], "FAILED_TRANSIENT")
            self.assertEqual(attempt["reservation_status"], "HISTORICAL_RESERVED_UNRECONCILED")
            self.assertEqual(
                db.execute("SELECT status FROM budget_reservations").fetchone()[0], "RESERVED"
            )

    def test_fencing_rejects_stale_worker(self) -> None:
        with self.controller.store.transaction() as db:
            db.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("j1", None, "research", "READY", "{}", None, None, 0, utcnow(), utcnow()),
            )
        past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        future = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        token_a = self.controller.store.acquire_job("j1", "a", past)
        token_b = self.controller.store.acquire_job("j1", "b", future)
        self.assertGreater(token_b, token_a)
        with self.assertRaises(FenceRejected):
            self.controller.store.fenced_job_update("j1", "a", token_a, "DONE")
        self.controller.store.fenced_job_update("j1", "b", token_b, "DONE")

    def test_path_traversal_and_symlink_component_rejected(self) -> None:
        repo = ArtifactRepository(Path(self.temp.name) / "repo")
        for bad in ("..", "a/b", "a\\b", ""):
            with self.assertRaises(UnsafeArtifactPath):
                repo.confined(bad)

    def test_stale_preview_is_rejected(self) -> None:
        preview = self.controller.preview({"modules": ["m"]}, "sha", (1, 1), (None, None))
        self.controller.verify_preview(preview)
        preview["scope"] = {"modules": ["other"]}
        with self.assertRaises(StalePreview):
            self.controller.verify_preview(preview)
