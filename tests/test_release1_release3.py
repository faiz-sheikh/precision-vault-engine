from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from pve.artifacts import ArtifactRegistry
from pve.controller import Controller, OperationBlocked
from pve.settings import Settings
from pve.store import utcnow
from pve.worker import Worker


class ReleaseOneThreeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); root=Path(self.temp.name)
        self.controller=Controller(Settings(state_dir=root/"state",artifact_dir=root/"artifacts"))
    def tearDown(self): self.temp.cleanup()

    def test_duplicate_command_is_idempotent(self):
        preview=self.controller.preview({"m":["1"]},"sha",(0,1),(0,.1))
        one,reused1=self.controller.submit_command(idempotency_key="click-1",command="run",payload={"m":"1"},preview=preview)
        two,reused2=self.controller.submit_command(idempotency_key="click-1",command="run",payload={"m":"1"},preview=preview)
        self.assertEqual(one,two); self.assertFalse(reused1); self.assertTrue(reused2)

    def test_idempotency_key_cannot_change_meaning(self):
        self.controller.submit_command(idempotency_key="k",command="run",payload={"x":1})
        with self.assertRaises(OperationBlocked): self.controller.submit_command(idempotency_key="k",command="run",payload={"x":2})

    def test_concurrent_duplicate_commands_create_one_row(self):
        results=[]; errors=[]
        def submit():
            try: results.append(self.controller.submit_command(idempotency_key="same",command="run",payload={}))
            except Exception as exc: errors.append(exc)
        threads=[threading.Thread(target=submit) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertFalse(errors); self.assertEqual(len({value[0] for value in results}),1)

    def test_artifact_dependency_reuse_and_selective_staleness(self):
        registry=ArtifactRegistry(self.controller.store)
        fp=registry.dependency_fingerprint(["up-a"],{"prompt":"v1"})
        one,reused=registry.register(module_id="mod_a",phase="note",generation_id="g1",dependency_fingerprint=fp,content_hash="h1")
        two,reused2=registry.register(module_id="mod_a",phase="note",generation_id="g2",dependency_fingerprint=fp,content_hash="h1")
        self.assertEqual(one,two); self.assertFalse(reused); self.assertTrue(reused2)
        with self.controller.store.transaction() as db:
            db.execute("INSERT INTO artifact_dependencies VALUES(?,?,?)",(one,one,"up-a"))
        self.assertEqual(registry.stale_for_upstream("up-a"),[])
        self.assertEqual(registry.stale_for_upstream("up-b"),[one])

    def test_worker_executes_job_once(self):
        with self.controller.store.transaction() as db:
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",("j",None,"local","READY","{}",None,None,0,utcnow(),utcnow()))
        calls=[]; worker=Worker(self.controller.store)
        self.assertTrue(worker.run_job("j",lambda:calls.append(1)))
        self.assertFalse(worker.run_job("j",lambda:calls.append(2))); self.assertEqual(calls,[1])

    def test_startup_reconciliation_reports_ambiguous_without_mutation(self):
        with self.controller.store.transaction() as db:
            db.execute("INSERT INTO provider_operations(id,fingerprint,provider,model,capability,state,request_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       ("op","fp","google","m","c","AMBIGUOUS","{}",utcnow(),utcnow()))
        report=self.controller.store.reconcile_startup()
        self.assertEqual(report["provider_reconciliation_required"],1)
        with self.controller.store.connect() as db: self.assertEqual(db.execute("SELECT state FROM provider_operations").fetchone()[0],"AMBIGUOUS")
