from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .store import Store


class Worker:
    def __init__(self, store: Store, owner: str | None = None, lease_seconds: int = 60):
        self.store = store
        self.owner = owner or "worker_" + uuid.uuid4().hex
        self.lease_seconds = lease_seconds

    def run_job(self, job_id: str, function: Callable[[], None]) -> bool:
        expires = (datetime.now(UTC) + timedelta(seconds=self.lease_seconds)).isoformat()
        token = self.store.acquire_job(job_id, self.owner, expires)
        if token is None:
            return False
        self.store.fenced_job_update(job_id, self.owner, token, "RUNNING")
        try:
            function()
        except BaseException:
            self.store.fenced_job_update(job_id, self.owner, token, "FAILED")
            raise
        self.store.fenced_job_update(job_id, self.owner, token, "SUCCEEDED")
        return True
