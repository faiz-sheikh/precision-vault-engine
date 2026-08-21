"""SQLite operational authority with migrations and append-only evidence."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY, config_json TEXT NOT NULL, config_hash TEXT NOT NULL,
 manifest_checksum TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commands(
 id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, command TEXT NOT NULL,
 payload_json TEXT NOT NULL, preview_id TEXT, run_id TEXT REFERENCES runs(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_operations(
 id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, provider TEXT NOT NULL, model TEXT NOT NULL,
 capability TEXT NOT NULL, state TEXT NOT NULL, request_json TEXT NOT NULL,
 raw_path TEXT, raw_sha256 TEXT, parsed_json TEXT, provider_id TEXT,
 estimated_cost_usd REAL, actual_cost_usd REAL, attempts INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_events(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL REFERENCES provider_operations(id),
 event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS provider_events_no_update BEFORE UPDATE ON provider_events BEGIN
 SELECT RAISE(ABORT, 'provider events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS provider_events_no_delete BEFORE DELETE ON provider_events BEGIN
 SELECT RAISE(ABORT, 'provider events are append-only'); END;
CREATE TABLE IF NOT EXISTS budget_reservations(
 id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE REFERENCES provider_operations(id),
 requests INTEGER NOT NULL CHECK(requests > 0), estimated_cost_usd REAL,
 status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id), phase TEXT NOT NULL, state TEXT NOT NULL,
 payload_json TEXT NOT NULL, lease_owner TEXT, lease_expires_at TEXT, fencing_token INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts(
 id TEXT PRIMARY KEY, module_id TEXT NOT NULL, phase TEXT NOT NULL, generation_id TEXT NOT NULL,
 content_hash TEXT, dependency_fingerprint TEXT NOT NULL, status TEXT NOT NULL,
 metadata_path TEXT, created_at TEXT NOT NULL, UNIQUE(module_id, phase, dependency_fingerprint)
);
CREATE TABLE IF NOT EXISTS artifact_dependencies(
 artifact_id TEXT NOT NULL REFERENCES artifacts(id), upstream_artifact_id TEXT NOT NULL REFERENCES artifacts(id),
 upstream_hash TEXT NOT NULL, PRIMARY KEY(artifact_id, upstream_artifact_id)
);
CREATE TABLE IF NOT EXISTS sources(
 source_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, source_type TEXT NOT NULL, origin TEXT NOT NULL,
 authority_group TEXT, effective_date TEXT, supersedes_source_id TEXT REFERENCES sources(source_id),
 status TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manifest_candidates(
 id TEXT PRIMARY KEY, envelope_json TEXT NOT NULL, checksum TEXT NOT NULL UNIQUE,
 validation_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals(
 id TEXT PRIMARY KEY, action TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
 evidence_json TEXT NOT NULL, input_hashes_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS remediation_recommendations(
 id TEXT PRIMARY KEY, module_id TEXT NOT NULL, kind TEXT NOT NULL, rationale TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS failures(
 id TEXT PRIMARY KEY, scope_type TEXT NOT NULL, scope_id TEXT NOT NULL,
 classification TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS phase_state(
 run_id TEXT NOT NULL REFERENCES runs(id), module_id TEXT NOT NULL, phase TEXT NOT NULL,
 state TEXT NOT NULL, dependency_fingerprint TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(run_id,module_id,phase)
);
CREATE TABLE IF NOT EXISTS outbox(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS provider_identifiers(
 operation_id TEXT NOT NULL REFERENCES provider_operations(id), provider_id TEXT NOT NULL,
 observed_at TEXT NOT NULL, PRIMARY KEY(operation_id,provider_id)
);
CREATE TABLE IF NOT EXISTS provider_reconciliations(
 id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES provider_operations(id),
 actor TEXT NOT NULL, decision TEXT NOT NULL, evidence_json TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class StoreCorrupt(RuntimeError): pass
class FenceRejected(RuntimeError): pass


class Store:
    def __init__(self, path: Path):
        self.path = path.resolve()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        with self.connect() as db:
            db.executescript(DDL)
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (?, ?)", (SCHEMA_VERSION, utcnow()))
        os.chmod(self.path, 0o600)
        self.integrity_check()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def integrity_check(self) -> None:
        try:
            with self.connect() as db:
                result = db.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise StoreCorrupt(f"SQLite integrity unavailable: {exc}") from exc
        if result != "ok":
            raise StoreCorrupt(f"SQLite integrity check failed: {result}")

    def backup(self, destination: Path) -> dict[str, Any]:
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        with self.connect() as source, sqlite3.connect(tmp) as target:
            source.backup(target)
        os.replace(tmp, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        restored = Store.restore_check(destination)
        return {"path": str(destination), "sha256": digest, "schema_version": SCHEMA_VERSION,
                "created_at": utcnow(), "restore_test_result": "PASS" if restored else "FAIL"}

    @staticmethod
    def restore_check(backup: Path) -> bool:
        with tempfile.TemporaryDirectory(prefix="pve-restore-") as directory:
            restored = Path(directory) / "restored.sqlite3"
            shutil.copy2(backup, restored)
            try:
                Store(restored).integrity_check()
                return True
            except StoreCorrupt:
                return False

    def event(self, db: sqlite3.Connection, operation_id: str, kind: str, payload: dict[str, Any] | None = None) -> None:
        db.execute("INSERT INTO provider_events(operation_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                   (operation_id, kind, json.dumps(payload or {}, sort_keys=True), utcnow()))

    def acquire_job(self, job_id: str, owner: str, expires_at: str) -> int | None:
        now = utcnow()
        with self.transaction() as db:
            row = db.execute("SELECT lease_owner,lease_expires_at,fencing_token,state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row[3] in ("SUCCEEDED", "CANCELLED") or (row[0] and row[1] and row[1] > now and row[0] != owner):
                return None
            token = int(row[2]) + 1
            db.execute("UPDATE jobs SET lease_owner=?,lease_expires_at=?,fencing_token=?,updated_at=? WHERE id=?",
                       (owner, expires_at, token, now, job_id))
            return token

    def fenced_job_update(self, job_id: str, owner: str, token: int, state: str) -> None:
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE jobs SET state=?,updated_at=? WHERE id=? AND lease_owner=? AND fencing_token=?",
                (state, utcnow(), job_id, owner, token),
            ).rowcount
            if changed != 1:
                raise FenceRejected(f"stale worker commit rejected for {job_id}")

    def reconcile_startup(self) -> dict[str, int]:
        """Report interrupted boundaries; never guesses or resubmits provider work."""
        with self.connect() as db:
            ambiguous = db.execute("SELECT count(*) FROM provider_operations WHERE state IN ('CALLING','SUBMITTED','RUNNING','AMBIGUOUS')").fetchone()[0]
            pending_outbox = db.execute("SELECT count(*) FROM outbox WHERE status='PENDING'").fetchone()[0]
            staged_artifacts = db.execute("SELECT count(*) FROM artifacts WHERE status='STAGED'").fetchone()[0]
        return {"provider_reconciliation_required": ambiguous, "pending_outbox": pending_outbox,
                "staged_artifacts": staged_artifacts}
