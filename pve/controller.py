"""The sole authority for provider operations and authoritative runtime state."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .artifacts import ArtifactRepository
from .identity import provider_fingerprint, sha256_identity
from .providers import Adapter, FailureClass, HTTPErrorEvidence, ProviderFailure, ProviderResult
from .settings import Settings
from .store import Store, utcnow


class OperationBlocked(RuntimeError):
    pass


class StalePreview(OperationBlocked):
    pass


class AmbiguousOperation(OperationBlocked):
    pass


class BudgetExceeded(OperationBlocked):
    pass


class Controller:
    def __init__(
        self,
        settings: Settings,
        store: Store | None = None,
        artifacts: ArtifactRepository | None = None,
    ):
        self.settings = settings
        self.store = store or Store(settings.database_path)
        self.artifacts = artifacts or ArtifactRepository(settings.artifact_dir)
        self.store.initialize()

    def preview(
        self,
        scope: dict[str, Any],
        manifest_checksum: str,
        expected_calls: tuple[int, int],
        estimated_cost: tuple[float | None, float | None],
    ) -> dict[str, Any]:
        payload = {
            "scope": scope,
            "manifest_checksum": manifest_checksum,
            "config_hash": self.settings.config_hash,
            "expected_calls": list(expected_calls),
            "estimated_cost_usd": list(estimated_cost),
            "routes": {k: asdict(v) for k, v in self.settings.routes.items()},
        }
        return {**payload, "preview_id": sha256_identity("pve-preview-v1", payload)}

    def verify_preview(self, preview: dict[str, Any]) -> None:
        claimed = preview.get("preview_id")
        payload = {k: v for k, v in preview.items() if k != "preview_id"}
        if claimed != sha256_identity("pve-preview-v1", payload):
            raise StalePreview("preview contents changed")
        if preview.get("config_hash") != self.settings.config_hash:
            raise StalePreview("configuration changed since preview")

    def submit_command(
        self,
        *,
        idempotency_key: str,
        command: str,
        payload: dict[str, Any],
        preview: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        if preview is not None:
            self.verify_preview(preview)
        command_id = "cmd_" + sha256_identity("pve-command-v1", idempotency_key).split(":", 1)[1]
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT id,command,payload_json,preview_id FROM commands WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            serialized = json.dumps(payload, sort_keys=True)
            preview_id = preview.get("preview_id") if preview else None
            if row:
                if (row["command"], row["payload_json"], row["preview_id"]) != (
                    command,
                    serialized,
                    preview_id,
                ):
                    raise OperationBlocked(
                        "idempotency key reused with different command semantics"
                    )
                return row["id"], True
            db.execute(
                "INSERT INTO commands(id,idempotency_key,command,payload_json,preview_id,created_at) VALUES(?,?,?,?,?,?)",
                (command_id, idempotency_key, command, serialized, preview_id, utcnow()),
            )
        return command_id, False

    def execute_provider(
        self,
        *,
        capability: str,
        request: dict[str, Any],
        prompt_hash: str,
        parameters: dict[str, Any],
        schema: dict[str, Any],
        upstream_hashes: list[str],
        module_id: str,
        phase: str,
        adapter: Adapter,
        parse: Callable[[bytes], Any],
        estimated_cost_usd: float | None = None,
    ) -> tuple[Any, bool]:
        route = self.settings.routes[capability]
        if adapter.provider.value != route.provider:
            raise OperationBlocked("adapter does not match configured capability route")
        fingerprint = provider_fingerprint(
            provider=route.provider,
            model=route.model,
            request=request,
            prompt_hash=prompt_hash,
            parameters=parameters,
            schema=schema,
            run_config_hash=self.settings.config_hash,
            upstream_hashes=upstream_hashes,
        )
        operation_id = "op_" + fingerprint.split(":", 1)[1]
        now = utcnow()
        attempt_id = ""
        with self.store.transaction() as db:
            existing = db.execute(
                "SELECT * FROM provider_operations WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            if existing:
                if existing["state"] == "SUCCEEDED":
                    raw = Path(existing["raw_path"]).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != existing["raw_sha256"]:
                        raise OperationBlocked("raw checkpoint checksum mismatch")
                    return parse(raw), True
                if existing["state"] in ("CALLING", "SUBMITTED", "RUNNING", "AMBIGUOUS"):
                    raise AmbiguousOperation(f"operation {operation_id} requires reconciliation")
                if existing["state"] == "FAILED_PERMANENT":
                    raise OperationBlocked("unchanged permanently failed operation cannot retry")
                if (
                    existing["state"] == "FAILED_TRANSIENT"
                    and existing["attempts"] >= self.settings.max_provider_attempts
                ):
                    raise OperationBlocked("bounded transient retry limit reached")
            # A completed operation is reusable with all live switches off. Any path that
            # could create a new external attempt must pass preflight before new evidence.
            adapter.preflight()
            if not existing:
                db.execute(
                    "INSERT INTO provider_operations(id,fingerprint,provider,model,capability,state,request_json,estimated_cost_usd,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        operation_id,
                        fingerprint,
                        route.provider,
                        route.model,
                        capability,
                        "INTENT_CREATED",
                        json.dumps(request, sort_keys=True),
                        estimated_cost_usd,
                        now,
                        now,
                    ),
                )
                self.store.event(db, operation_id, "INTENT_CREATED", {"fingerprint": fingerprint})
            attempt_count = db.execute(
                "SELECT COALESCE(SUM(attempts),0) FROM provider_operations"
            ).fetchone()[0]
            if attempt_count >= self.settings.max_requests_per_run:
                raise BudgetExceeded("request budget exhausted")
            if self.settings.max_cost_usd is not None:
                if estimated_cost_usd is None:
                    raise BudgetExceeded("unknown pricing cannot be reserved under a cost cap")
                used = db.execute(
                    "SELECT COALESCE(SUM(estimated_cost_usd),0) FROM provider_attempts "
                    "WHERE reservation_status IN "
                    "('RESERVED','CONSUMED','CONSUMED_BILLING_UNKNOWN',"
                    "'CLOSED_BILLING_UNKNOWN','HELD_AMBIGUOUS')"
                ).fetchone()[0]
                if used + estimated_cost_usd > self.settings.max_cost_usd:
                    raise BudgetExceeded("cost budget exhausted")
            attempt_number = int(existing["attempts"]) + 1 if existing else 1
            attempt_id = f"att_{operation_id.removeprefix('op_')}_{attempt_number}"
            reservation_id = "res_" + uuid.uuid4().hex
            db.execute(
                "INSERT INTO provider_attempts("
                "attempt_id,operation_id,attempt_number,state,reservation_status,"
                "estimated_cost_usd,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    operation_id,
                    attempt_number,
                    "CALLING",
                    "RESERVED",
                    estimated_cost_usd,
                    now,
                    now,
                ),
            )
            db.execute(
                "INSERT INTO budget_reservations(id,operation_id,requests,estimated_cost_usd,status,created_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(operation_id) DO UPDATE SET "
                "requests=excluded.requests,estimated_cost_usd=excluded.estimated_cost_usd,"
                "status=excluded.status,created_at=excluded.created_at",
                (reservation_id, operation_id, 1, estimated_cost_usd, "RESERVED", now),
            )
            db.execute(
                "UPDATE provider_operations SET state='CALLING',attempts=attempts+1,updated_at=? WHERE id=?",
                (now, operation_id),
            )
            self.store.event(
                db,
                operation_id,
                "RESERVED",
                {"attempt_id": attempt_id, "estimated_cost_usd": estimated_cost_usd},
            )
            self.store.event(db, operation_id, "CALLING", {"attempt_id": attempt_id})

        error_checkpoint: tuple[Path, str] | None = None

        def checkpoint_http_error(evidence: HTTPErrorEvidence) -> None:
            nonlocal error_checkpoint
            envelope = {
                "provider": route.provider,
                "model": route.model,
                "request_fingerprint": fingerprint,
                "timestamp": utcnow(),
                "http_status": evidence.status,
                "headers": evidence.headers,
                "raw_body_base64": base64.b64encode(evidence.body).decode("ascii"),
                "raw_body_text": evidence.body.decode("utf-8", errors="replace"),
                "provider_error_code": evidence.provider_error_code,
                "provider_error_message": evidence.provider_error_message,
                "attempt_id": attempt_id,
            }
            error_checkpoint = self.artifacts.checkpoint_error(
                module_id, phase, attempt_id, envelope
            )

        try:
            result: ProviderResult = adapter.execute(
                {"model": route.model, **request}, on_http_error=checkpoint_http_error
            )
        except ProviderFailure as exc:
            state = {
                FailureClass.AMBIGUOUS: "AMBIGUOUS",
                FailureClass.PERMANENT: "FAILED_PERMANENT",
            }.get(exc.classification, "FAILED_TRANSIENT")
            if exc.classification is FailureClass.AMBIGUOUS:
                reservation_status = "HELD_AMBIGUOUS"
            elif exc.request_sent is False:
                reservation_status = "RELEASED_NOT_SENT"
            else:
                reservation_status = "CLOSED_BILLING_UNKNOWN"
            error_path = str(error_checkpoint[0]) if error_checkpoint else None
            error_hash = error_checkpoint[1] if error_checkpoint else None
            http_status = exc.http_evidence.status if exc.http_evidence else None
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE provider_operations SET state=?,error_path=?,error_sha256=?,updated_at=? WHERE id=?",
                    (state, error_path, error_hash, utcnow(), operation_id),
                )
                db.execute(
                    "UPDATE provider_attempts SET state=?,reservation_status=?,billing_uncertain=?,"
                    "http_status=?,error_path=?,error_sha256=?,updated_at=? WHERE attempt_id=?",
                    (
                        state,
                        reservation_status,
                        int(reservation_status in ("CLOSED_BILLING_UNKNOWN", "HELD_AMBIGUOUS")),
                        http_status,
                        error_path,
                        error_hash,
                        utcnow(),
                        attempt_id,
                    ),
                )
                db.execute(
                    "UPDATE budget_reservations SET status=? WHERE operation_id=?",
                    (reservation_status, operation_id),
                )
                self.store.event(
                    db,
                    operation_id,
                    state,
                    {
                        "attempt_id": attempt_id,
                        "error_type": type(exc).__name__,
                        "http_status": http_status,
                        "error_path": error_path,
                        "error_sha256": error_hash,
                        "provider_error_code": (
                            exc.http_evidence.provider_error_code if exc.http_evidence else None
                        ),
                        "provider_error_message": (
                            exc.http_evidence.provider_error_message if exc.http_evidence else None
                        ),
                        "reservation_status": reservation_status,
                    },
                )
            if exc.classification is FailureClass.AMBIGUOUS:
                raise AmbiguousOperation(str(exc)) from exc
            raise
        except BaseException as exc:
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE provider_operations SET state='AMBIGUOUS',updated_at=? WHERE id=?",
                    (utcnow(), operation_id),
                )
                db.execute(
                    "UPDATE provider_attempts SET state='AMBIGUOUS',reservation_status='HELD_AMBIGUOUS',"
                    "billing_uncertain=1,updated_at=? WHERE attempt_id=?",
                    (utcnow(), attempt_id),
                )
                db.execute(
                    "UPDATE budget_reservations SET status='HELD_AMBIGUOUS' WHERE operation_id=?",
                    (operation_id,),
                )
                self.store.event(
                    db,
                    operation_id,
                    "AMBIGUOUS",
                    {
                        "attempt_id": attempt_id,
                        "error_type": type(exc).__name__,
                        "reservation_status": "HELD_AMBIGUOUS",
                    },
                )
            raise AmbiguousOperation(
                "transport outcome unknown; automatic resubmission blocked"
            ) from exc

        try:
            raw_path, raw_hash = self.artifacts.checkpoint_raw(
                module_id, phase, operation_id, result.raw
            )
        except BaseException as exc:
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE provider_operations SET state='AMBIGUOUS',updated_at=? WHERE id=?",
                    (utcnow(), operation_id),
                )
                db.execute(
                    "UPDATE provider_attempts SET state='AMBIGUOUS',reservation_status='HELD_AMBIGUOUS',"
                    "billing_uncertain=1,updated_at=? WHERE attempt_id=?",
                    (utcnow(), attempt_id),
                )
                db.execute(
                    "UPDATE budget_reservations SET status='HELD_AMBIGUOUS' WHERE operation_id=?",
                    (operation_id,),
                )
                self.store.event(
                    db,
                    operation_id,
                    "AMBIGUOUS",
                    {
                        "attempt_id": attempt_id,
                        "error_type": type(exc).__name__,
                        "boundary": "after_response_before_raw_checkpoint",
                        "reservation_status": "HELD_AMBIGUOUS",
                    },
                )
            raise AmbiguousOperation(
                "provider response received but raw checkpoint failed; resubmission blocked"
            ) from exc
        with self.store.transaction() as db:
            db.execute(
                "UPDATE provider_operations SET state='SUCCEEDED',raw_path=?,raw_sha256=?,provider_id=?,updated_at=? WHERE id=?",
                (str(raw_path), raw_hash, result.provider_id, utcnow(), operation_id),
            )
            self.store.event(
                db, operation_id, "SUCCEEDED", {"raw_sha256": raw_hash, "usage": result.usage or {}}
            )
            billing_status = "CONSUMED" if result.usage is not None else "CONSUMED_BILLING_UNKNOWN"
            db.execute(
                "UPDATE budget_reservations SET status=? WHERE operation_id=?",
                (billing_status, operation_id),
            )
            db.execute(
                "UPDATE provider_attempts SET state='SUCCEEDED',reservation_status=?,"
                "billing_uncertain=?,updated_at=? WHERE attempt_id=?",
                (billing_status, int(result.usage is None), utcnow(), attempt_id),
            )
        # Parsing deliberately occurs after durable success. A parser crash reuses raw bytes.
        return parse(result.raw), False
