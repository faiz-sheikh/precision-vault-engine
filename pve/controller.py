"""The sole authority for provider operations and authoritative runtime state."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifacts import ArtifactRepository
from .identity import provider_fingerprint, sha256_identity
from .providers import (
    Adapter,
    FailureClass,
    HTTPErrorEvidence,
    ProviderFailure,
    ProviderResult,
)
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


_FAILURE_STATE = {
    FailureClass.PERMANENT_REQUEST: "FAILED_PERMANENT",
    FailureClass.ACCOUNT_OR_BILLING_BLOCKED: "BLOCKED_ACCOUNT_OR_BILLING",
    FailureClass.AUTH_BLOCKED: "BLOCKED_AUTH",
    FailureClass.ACCESS_BLOCKED: "BLOCKED_ACCESS",
    FailureClass.MODEL_ACCESS_BLOCKED: "BLOCKED_MODEL_ACCESS",
    FailureClass.RATE_LIMIT_TRANSIENT: "FAILED_TRANSIENT",
    FailureClass.TRANSIENT: "FAILED_TRANSIENT",
    FailureClass.AMBIGUOUS: "AMBIGUOUS",
    FailureClass.VALIDATION: "FAILED_VALIDATION",
    FailureClass.LOCAL_PROCESSING: "FAILED_LOCAL_PROCESSING",
}
_MANUAL_RETRY_STATES = {
    "FAILED_TRANSIENT",
    "BLOCKED_ACCOUNT_OR_BILLING",
    "BLOCKED_AUTH",
    "BLOCKED_ACCESS",
    "BLOCKED_MODEL_ACCESS",
    "FAILED_VALIDATION",
}


def _serialized_parsed(value: Any) -> str | None:
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return None


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

    @staticmethod
    def _effective_operation_state(db: sqlite3.Connection, operation: sqlite3.Row) -> str:
        correction = db.execute(
            "SELECT decision,evidence_json FROM provider_reconciliations WHERE operation_id=? "
            "ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (operation["id"],),
        ).fetchone()
        if correction is None:
            return str(operation["state"])
        try:
            evidence = json.loads(correction["evidence_json"])
            if evidence.get("original_classification") != operation["state"]:
                return str(operation["state"])
            return _FAILURE_STATE[FailureClass(correction["decision"])]
        except (json.JSONDecodeError, AttributeError, KeyError, ValueError) as exc:
            raise OperationBlocked("provider reconciliation classification is invalid") from exc

    def append_classification_correction(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        evidence_type: str,
        original_classification: str,
        corrected_classification: FailureClass,
        reason: str,
        evidence_sha256: str,
        actor: str,
        system_version: str,
    ) -> str:
        """Append semantic correction evidence without rewriting provider history."""
        if not all(value.strip() for value in (reason, evidence_sha256, actor, system_version)):
            raise OperationBlocked("classification correction evidence is incomplete")
        correction_id = "rec_" + uuid.uuid4().hex
        created_at = utcnow()
        with self.store.transaction() as db:
            operation = db.execute(
                "SELECT * FROM provider_operations WHERE id=?", (operation_id,)
            ).fetchone()
            if operation is None or operation["state"] != original_classification:
                raise OperationBlocked("historical provider classification does not match")
            attempt = db.execute(
                "SELECT * FROM provider_attempts WHERE attempt_id=? AND operation_id=?",
                (attempt_id, operation_id),
            ).fetchone()
            if attempt is None:
                raise OperationBlocked("target reconciliation attempt does not exist")
            normalized_evidence_type = evidence_type.strip().upper()
            if normalized_evidence_type == "ERROR":
                durable_evidence_sha256 = attempt["error_sha256"]
                evidence_path = attempt["error_path"]
            elif normalized_evidence_type == "RAW":
                latest_attempt = db.execute(
                    "SELECT attempt_id FROM provider_attempts WHERE operation_id=? "
                    "ORDER BY attempt_number DESC LIMIT 1",
                    (operation_id,),
                ).fetchone()
                if latest_attempt is None or latest_attempt["attempt_id"] != attempt_id:
                    raise OperationBlocked("raw projection evidence must target the latest attempt")
                durable_evidence_sha256 = operation["raw_sha256"]
                evidence_path = operation["raw_path"]
            else:
                raise OperationBlocked("unsupported reconciliation evidence type")
            if not durable_evidence_sha256 or not evidence_path:
                raise OperationBlocked("target attempt evidence is absent")
            path = Path(evidence_path)
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != evidence_sha256
            ):
                raise OperationBlocked(
                    "target attempt evidence checkpoint is unavailable or corrupt"
                )
            if durable_evidence_sha256 != evidence_sha256:
                raise OperationBlocked("classification correction evidence hash does not match")
            evidence = {
                "operation_id": operation_id,
                "fingerprint": operation["fingerprint"],
                "attempt_id": attempt_id,
                "evidence_type": normalized_evidence_type,
                "original_classification": original_classification,
                "corrected_semantic_classification": corrected_classification.value,
                "corrected_effective_state": _FAILURE_STATE[corrected_classification],
                "reason": reason.strip(),
                "evidence_sha256": evidence_sha256,
                "actor": actor.strip(),
                "validator_version": system_version.strip(),
                "timestamp": created_at,
            }
            db.execute(
                "INSERT INTO provider_reconciliations("
                "id,operation_id,actor,decision,evidence_json,created_at,attempt_id,"
                "evidence_type,evidence_sha256) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    correction_id,
                    operation_id,
                    actor.strip(),
                    corrected_classification.value,
                    json.dumps(evidence, sort_keys=True),
                    created_at,
                    attempt_id,
                    normalized_evidence_type,
                    evidence_sha256,
                ),
            )
            self.store.event(db, operation_id, "CLASSIFICATION_CORRECTED", evidence)
        return correction_id

    def approve_superseding_operation(
        self,
        *,
        approval_id: str,
        parent_operation_id: str,
        request: dict[str, Any],
        prompt_hash: str,
        parameters: dict[str, Any],
        schema: dict[str, Any],
        upstream_hashes: list[str],
        max_output_tokens: int,
        max_cost_usd: float | None,
        actor: str,
        reason: str,
        expires_at: str,
    ) -> tuple[str, str, bool]:
        if not approval_id or not actor.strip() or not reason.strip() or max_output_tokens < 1:
            raise OperationBlocked("supersession approval fields are incomplete")
        expiry = datetime.fromisoformat(expires_at)
        if expiry.tzinfo is None or expiry <= datetime.now(UTC):
            raise OperationBlocked("supersession approval is expired or lacks a timezone")
        now = utcnow()
        with self.store.transaction() as db:
            parent = db.execute(
                "SELECT * FROM provider_operations WHERE id=?", (parent_operation_id,)
            ).fetchone()
            if parent is None or self._effective_operation_state(db, parent) != "FAILED_VALIDATION":
                raise OperationBlocked("supersession parent must project FAILED_VALIDATION")
            route = self.settings.routes.get(parent["capability"])
            if route is None or (route.provider, route.model) != (
                parent["provider"],
                parent["model"],
            ):
                raise OperationBlocked("supersession provider route changed")
            target_fingerprint = provider_fingerprint(
                provider=route.provider,
                model=route.model,
                request=request,
                prompt_hash=prompt_hash,
                parameters=parameters,
                schema=schema,
                run_config_hash=self.settings.config_hash,
                upstream_hashes=upstream_hashes,
            )
            if target_fingerprint == parent["fingerprint"]:
                raise OperationBlocked("supersession must materially change the fingerprint")
            child_operation_id = "op_" + target_fingerprint.split(":", 1)[1]
            old_request = json.loads(parent["request_json"])
            old_limit = old_request.get("max_tokens") or old_request.get(
                "generationConfig", {}
            ).get("maxOutputTokens")
            config_diff = {"max_output_tokens": {"old": old_limit, "new": max_output_tokens}}
            binding = (
                parent_operation_id,
                parent["fingerprint"],
                child_operation_id,
                target_fingerprint,
                route.provider,
                route.model,
                parent["capability"],
                json.dumps(request, sort_keys=True),
                prompt_hash,
                json.dumps(parameters, sort_keys=True),
                json.dumps(schema, sort_keys=True),
                json.dumps(upstream_hashes, sort_keys=True),
                json.dumps(config_diff, sort_keys=True),
                max_output_tokens,
                1,
                max_cost_usd,
                actor.strip(),
                reason.strip(),
                expiry.isoformat(),
                self.settings.config_hash,
            )
            existing = db.execute(
                "SELECT * FROM provider_supersession_approvals WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            if existing:
                columns = (
                    "parent_operation_id",
                    "parent_fingerprint",
                    "child_operation_id",
                    "target_fingerprint",
                    "provider",
                    "model",
                    "capability",
                    "request_json",
                    "prompt_hash",
                    "parameters_json",
                    "schema_json",
                    "upstream_hashes_json",
                    "config_diff_json",
                    "max_output_tokens",
                    "max_requests",
                    "max_cost_usd",
                    "actor",
                    "reason",
                    "expires_at",
                    "config_hash",
                )
                if tuple(existing[name] for name in columns) != binding:
                    raise OperationBlocked("supersession approval ID binding mismatch")
                return child_operation_id, target_fingerprint, True
            if db.execute(
                "SELECT 1 FROM provider_operations WHERE fingerprint=?", (target_fingerprint,)
            ).fetchone():
                raise OperationBlocked("target superseding operation already exists")
            db.execute(
                "INSERT INTO provider_operations("
                "id,fingerprint,provider,model,capability,state,request_json,estimated_cost_usd,"
                "created_at,updated_at) VALUES(?,?,?,?,?,'APPROVED',?,?,?,?)",
                (
                    child_operation_id,
                    target_fingerprint,
                    route.provider,
                    route.model,
                    parent["capability"],
                    json.dumps(request, sort_keys=True),
                    max_cost_usd,
                    now,
                    now,
                ),
            )
            db.execute(
                "INSERT INTO provider_supersession_approvals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (approval_id, *binding, now),
            )
            db.execute(
                "INSERT INTO provider_operation_lineage VALUES(?,?,?,?,?)",
                (child_operation_id, parent_operation_id, approval_id, reason.strip(), now),
            )
            self.store.event(
                db,
                parent_operation_id,
                "SUPERSESSION_APPROVED",
                {
                    "approval_id": approval_id,
                    "child_operation_id": child_operation_id,
                    "target_fingerprint": target_fingerprint,
                    "config_diff": config_diff,
                },
            )
            self.store.event(
                db,
                child_operation_id,
                "INTENT_APPROVED",
                {
                    "approval_id": approval_id,
                    "parent_operation_id": parent_operation_id,
                    "fingerprint": target_fingerprint,
                },
            )
            return child_operation_id, target_fingerprint, False

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

    def approve_manual_provider_retry(
        self,
        *,
        approval_id: str,
        operation_id: str,
        fingerprint: str,
        actor: str,
        reason: str,
        expires_at: str,
        additional_attempts: int,
        max_additional_requests: int,
        max_additional_cost_usd: float | None,
    ) -> None:
        if not approval_id or not actor.strip() or not reason.strip():
            raise OperationBlocked("manual retry approval identity, actor, and reason are required")
        if additional_attempts != 1 or max_additional_requests != 1:
            raise OperationBlocked("manual retry approvals are one-shot and authorize one request")
        if max_additional_cost_usd is not None and max_additional_cost_usd < 0:
            raise OperationBlocked("manual retry budget cannot be negative")
        try:
            expiry = datetime.fromisoformat(expires_at)
        except ValueError as exc:
            raise OperationBlocked("manual retry expiry must be ISO-8601") from exc
        if expiry.tzinfo is None or expiry <= datetime.now(UTC):
            raise OperationBlocked("manual retry approval is expired or lacks a timezone")
        now = utcnow()
        with self.store.transaction() as db:
            operation = db.execute(
                "SELECT * FROM provider_operations WHERE id=?", (operation_id,)
            ).fetchone()
            if operation is None or operation["fingerprint"] != fingerprint:
                raise OperationBlocked(
                    "manual retry approval does not match the operation fingerprint"
                )
            effective_state = self._effective_operation_state(db, operation)
            if effective_state not in _MANUAL_RETRY_STATES:
                raise OperationBlocked(
                    "operation state does not permit an approval-bound manual retry"
                )
            if effective_state == "FAILED_TRANSIENT" and (
                int(operation["attempts"]) < self.settings.max_provider_attempts
            ):
                raise OperationBlocked("automatic retry allowance is not exhausted")
            route = self.settings.routes.get(operation["capability"])
            if (
                route is None
                or route.provider != operation["provider"]
                or route.model != operation["model"]
            ):
                raise OperationBlocked("current provider route differs from the failed operation")
            try:
                db.execute(
                    "INSERT INTO provider_retry_approvals("
                    "approval_id,operation_id,fingerprint,approval_type,actor,reason,created_at,"
                    "expires_at,additional_attempts,provider,model,capability,"
                    "max_additional_requests,max_additional_cost_usd,config_hash) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        approval_id,
                        operation_id,
                        fingerprint,
                        "MANUAL_PROVIDER_RETRY",
                        actor.strip(),
                        reason.strip(),
                        now,
                        expiry.isoformat(),
                        additional_attempts,
                        operation["provider"],
                        operation["model"],
                        operation["capability"],
                        max_additional_requests,
                        max_additional_cost_usd,
                        self.settings.config_hash,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise OperationBlocked("manual retry approval ID already exists") from exc
            self.store.event(
                db,
                operation_id,
                "MANUAL_RETRY_APPROVED",
                {
                    "approval_id": approval_id,
                    "actor": actor.strip(),
                    "reason": reason.strip(),
                    "expires_at": expiry.isoformat(),
                    "additional_attempts": additional_attempts,
                    "max_additional_requests": max_additional_requests,
                    "max_additional_cost_usd": max_additional_cost_usd,
                    "config_hash": self.settings.config_hash,
                },
            )

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
        manual_retry_approval_id: str | None = None,
        supersession_approval_id: str | None = None,
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
            if supersession_approval_id is not None and existing is None:
                raise OperationBlocked(
                    "supersession approval does not match an approved target operation"
                )
            if existing:
                effective_state = self._effective_operation_state(db, existing)
                if effective_state == "APPROVED":
                    if supersession_approval_id is None:
                        raise OperationBlocked(
                            "approved superseding operation requires its approval"
                        )
                    supersession = db.execute(
                        "SELECT * FROM provider_supersession_approvals WHERE approval_id=?",
                        (supersession_approval_id,),
                    ).fetchone()
                    supersession_expected = (
                        operation_id,
                        fingerprint,
                        route.provider,
                        route.model,
                        capability,
                        json.dumps(request, sort_keys=True),
                        prompt_hash,
                        json.dumps(parameters, sort_keys=True),
                        json.dumps(schema, sort_keys=True),
                        json.dumps(upstream_hashes, sort_keys=True),
                        self.settings.config_hash,
                    )
                    supersession_actual = (
                        None
                        if supersession is None
                        else (
                            supersession["child_operation_id"],
                            supersession["target_fingerprint"],
                            supersession["provider"],
                            supersession["model"],
                            supersession["capability"],
                            supersession["request_json"],
                            supersession["prompt_hash"],
                            supersession["parameters_json"],
                            supersession["schema_json"],
                            supersession["upstream_hashes_json"],
                            supersession["config_hash"],
                        )
                    )
                    if supersession_actual != supersession_expected:
                        raise OperationBlocked("supersession approval binding mismatch")
                    if datetime.fromisoformat(supersession["expires_at"]) <= datetime.now(UTC):
                        raise OperationBlocked("supersession approval expired")
                    if db.execute(
                        "SELECT 1 FROM provider_supersession_consumptions WHERE approval_id=?",
                        (supersession_approval_id,),
                    ).fetchone():
                        raise OperationBlocked("supersession approval already consumed")
                if effective_state == "SUCCEEDED":
                    if manual_retry_approval_id is not None:
                        raise OperationBlocked(
                            "a succeeded operation cannot consume retry approval"
                        )
                    raw = Path(existing["raw_path"]).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != existing["raw_sha256"]:
                        raise OperationBlocked("raw checkpoint checksum mismatch")
                    return parse(raw), True
                if effective_state in ("CALLING", "SUBMITTED", "RUNNING", "AMBIGUOUS"):
                    raise AmbiguousOperation(f"operation {operation_id} requires reconciliation")
                if effective_state == "FAILED_PERMANENT":
                    raise OperationBlocked("unchanged permanently failed operation cannot retry")
                if (
                    effective_state in ("FAILED_VALIDATION", "FAILED_LOCAL_PROCESSING")
                    and manual_retry_approval_id is None
                ):
                    raw_path = existing["raw_path"]
                    raw_sha256 = existing["raw_sha256"]
                    if not raw_path or not raw_sha256:
                        raise OperationBlocked("local failure is missing its raw checkpoint")
                    raw = Path(raw_path).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != raw_sha256:
                        raise OperationBlocked("raw checkpoint checksum mismatch")
                    try:
                        parsed = parse(raw)
                    except ValueError:
                        raise
                    db.execute(
                        "UPDATE provider_operations SET state='SUCCEEDED',parsed_json=?,updated_at=? WHERE id=?",
                        (_serialized_parsed(parsed), utcnow(), operation_id),
                    )
                    self.store.event(
                        db,
                        operation_id,
                        "LOCAL_REVALIDATION_SUCCEEDED",
                        {"raw_sha256": raw_sha256},
                    )
                    return parsed, True
                if effective_state in _MANUAL_RETRY_STATES:
                    automatic_exhausted = (
                        int(existing["attempts"]) >= self.settings.max_provider_attempts
                    )
                    approval_required = effective_state != "FAILED_TRANSIENT" or automatic_exhausted
                    if manual_retry_approval_id is None and approval_required:
                        raise OperationBlocked("explicit manual retry approval is required")
                    if (
                        manual_retry_approval_id is not None
                        and effective_state == "FAILED_TRANSIENT"
                        and not automatic_exhausted
                    ):
                        raise OperationBlocked(
                            "manual retry approval cannot replace remaining automatic allowance"
                        )
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
            approval = None
            if manual_retry_approval_id is not None:
                if existing is None or self._effective_operation_state(db, existing) not in (
                    _MANUAL_RETRY_STATES
                ):
                    raise OperationBlocked("manual retry requires an eligible failed operation")
                approval = db.execute(
                    "SELECT * FROM provider_retry_approvals WHERE approval_id=?",
                    (manual_retry_approval_id,),
                ).fetchone()
                if approval is None:
                    raise OperationBlocked("manual retry approval not found")
                if db.execute(
                    "SELECT 1 FROM provider_retry_approval_consumptions WHERE approval_id=?",
                    (manual_retry_approval_id,),
                ).fetchone():
                    raise OperationBlocked("manual retry approval already consumed")
                expected = (
                    operation_id,
                    fingerprint,
                    route.provider,
                    route.model,
                    capability,
                    self.settings.config_hash,
                )
                actual = (
                    approval["operation_id"],
                    approval["fingerprint"],
                    approval["provider"],
                    approval["model"],
                    approval["capability"],
                    approval["config_hash"],
                )
                if actual != expected:
                    raise OperationBlocked("manual retry approval binding mismatch")
                if approval["approval_type"] != "MANUAL_PROVIDER_RETRY":
                    raise OperationBlocked("invalid manual retry approval type")
                if datetime.fromisoformat(approval["expires_at"]) <= datetime.now(UTC):
                    raise OperationBlocked("manual retry approval expired")
                if approval["additional_attempts"] != 1 or approval["max_additional_requests"] != 1:
                    raise OperationBlocked("manual retry approval is not one-shot")
                if approval["max_additional_cost_usd"] is not None and (
                    estimated_cost_usd is None
                    or estimated_cost_usd > approval["max_additional_cost_usd"]
                ):
                    raise BudgetExceeded("manual retry estimate exceeds approved additional budget")
            attempt_count = db.execute(
                "SELECT COALESCE(SUM(attempts),0) FROM provider_operations"
            ).fetchone()[0]
            if attempt_count >= self.settings.max_requests_per_run and approval is None:
                raise BudgetExceeded("request budget exhausted")
            if self.settings.max_cost_usd is not None:
                if estimated_cost_usd is None:
                    raise BudgetExceeded("unknown pricing cannot be reserved under a cost cap")
                used = db.execute(
                    "SELECT COALESCE(SUM(estimated_cost_usd),0) FROM provider_attempts "
                    "WHERE reservation_status IN "
                    "('RESERVED','CONSUMED','CONSUMED_BILLING_UNKNOWN',"
                    "'CLOSED_BILLING_UNKNOWN','HELD_AMBIGUOUS',"
                    "'HISTORICAL_RESERVED_UNRECONCILED')"
                ).fetchone()[0]
                if used + estimated_cost_usd > self.settings.max_cost_usd:
                    raise BudgetExceeded("cost budget exhausted")
            attempt_number = int(existing["attempts"]) + 1 if existing else 1
            attempt_id = f"att_{operation_id.removeprefix('op_')}_{attempt_number}"
            previous_attempt_id = (
                f"att_{operation_id.removeprefix('op_')}_{attempt_number - 1}"
                if attempt_number > 1
                else None
            )
            reservation_id = "res_" + uuid.uuid4().hex
            db.execute(
                "INSERT INTO provider_attempts("
                "attempt_id,operation_id,attempt_number,state,reservation_status,"
                "estimated_cost_usd,approval_id,previous_attempt_id,reservation_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    operation_id,
                    attempt_number,
                    "CALLING",
                    "RESERVED",
                    estimated_cost_usd,
                    manual_retry_approval_id,
                    previous_attempt_id,
                    reservation_id,
                    now,
                    now,
                ),
            )
            db.execute(
                "INSERT INTO provider_attempt_reservations("
                "reservation_id,attempt_id,operation_id,requests,estimated_cost_usd,status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    reservation_id,
                    attempt_id,
                    operation_id,
                    1,
                    estimated_cost_usd,
                    "RESERVED",
                    now,
                    now,
                ),
            )
            if manual_retry_approval_id is not None:
                try:
                    db.execute(
                        "INSERT INTO provider_retry_approval_consumptions VALUES(?,?,?)",
                        (manual_retry_approval_id, attempt_id, now),
                    )
                except sqlite3.IntegrityError as exc:
                    raise OperationBlocked(
                        "manual retry approval was concurrently consumed"
                    ) from exc
                self.store.event(
                    db,
                    operation_id,
                    "MANUAL_RETRY_APPROVAL_CONSUMED",
                    {
                        "approval_id": manual_retry_approval_id,
                        "attempt_id": attempt_id,
                        "previous_attempt_id": previous_attempt_id,
                    },
                )
            if supersession_approval_id is not None:
                try:
                    db.execute(
                        "INSERT INTO provider_supersession_consumptions VALUES(?,?,?)",
                        (supersession_approval_id, attempt_id, now),
                    )
                except sqlite3.IntegrityError as exc:
                    raise OperationBlocked(
                        "supersession approval was concurrently consumed"
                    ) from exc
                self.store.event(
                    db,
                    operation_id,
                    "SUPERSESSION_APPROVAL_CONSUMED",
                    {"approval_id": supersession_approval_id, "attempt_id": attempt_id},
                )
            if manual_retry_approval_id is None:
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
                {
                    "attempt_id": attempt_id,
                    "approval_id": manual_retry_approval_id,
                    "estimated_cost_usd": estimated_cost_usd,
                },
            )
            self.store.event(
                db,
                operation_id,
                "CALLING",
                {"attempt_id": attempt_id, "approval_id": manual_retry_approval_id},
            )

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
            state = _FAILURE_STATE[exc.classification]
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
                    "UPDATE provider_attempt_reservations SET status=?,billing_uncertain=?,"
                    "updated_at=? WHERE reservation_id=?",
                    (
                        reservation_status,
                        int(reservation_status in ("CLOSED_BILLING_UNKNOWN", "HELD_AMBIGUOUS")),
                        utcnow(),
                        reservation_id,
                    ),
                )
                if manual_retry_approval_id is None:
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
                        "semantic_classification": exc.classification.value,
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
                    "UPDATE provider_attempt_reservations SET status='HELD_AMBIGUOUS',"
                    "billing_uncertain=1,updated_at=? WHERE reservation_id=?",
                    (utcnow(), reservation_id),
                )
                if manual_retry_approval_id is None:
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
                    "UPDATE provider_attempt_reservations SET status='HELD_AMBIGUOUS',"
                    "billing_uncertain=1,updated_at=? WHERE reservation_id=?",
                    (utcnow(), reservation_id),
                )
                if manual_retry_approval_id is None:
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
        billing_status = "CONSUMED" if result.usage is not None else "CONSUMED_BILLING_UNKNOWN"
        try:
            parsed = parse(result.raw)
        except ValueError as exc:
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE provider_operations SET state='FAILED_VALIDATION',raw_path=?,raw_sha256=?,"
                    "provider_id=?,updated_at=? WHERE id=?",
                    (str(raw_path), raw_hash, result.provider_id, utcnow(), operation_id),
                )
                db.execute(
                    "UPDATE provider_attempt_reservations SET status=?,billing_uncertain=?,"
                    "updated_at=? WHERE reservation_id=?",
                    (billing_status, int(result.usage is None), utcnow(), reservation_id),
                )
                if manual_retry_approval_id is None:
                    db.execute(
                        "UPDATE budget_reservations SET status=? WHERE operation_id=?",
                        (billing_status, operation_id),
                    )
                db.execute(
                    "UPDATE provider_attempts SET state='FAILED_VALIDATION',reservation_status=?,"
                    "billing_uncertain=?,updated_at=? WHERE attempt_id=?",
                    (billing_status, int(result.usage is None), utcnow(), attempt_id),
                )
                self.store.event(
                    db,
                    operation_id,
                    "FAILED_VALIDATION",
                    {
                        "attempt_id": attempt_id,
                        "raw_sha256": raw_hash,
                        "error_type": type(exc).__name__,
                        "reservation_status": billing_status,
                        "usage": result.usage or {},
                    },
                )
            raise
        with self.store.transaction() as db:
            db.execute(
                "UPDATE provider_operations SET state='SUCCEEDED',raw_path=?,raw_sha256=?,parsed_json=?,"
                "provider_id=?,updated_at=? WHERE id=?",
                (
                    str(raw_path),
                    raw_hash,
                    _serialized_parsed(parsed),
                    result.provider_id,
                    utcnow(),
                    operation_id,
                ),
            )
            self.store.event(
                db, operation_id, "SUCCEEDED", {"raw_sha256": raw_hash, "usage": result.usage or {}}
            )
            db.execute(
                "UPDATE provider_attempt_reservations SET status=?,billing_uncertain=?,"
                "updated_at=? WHERE reservation_id=?",
                (billing_status, int(result.usage is None), utcnow(), reservation_id),
            )
            if manual_retry_approval_id is None:
                db.execute(
                    "UPDATE budget_reservations SET status=? WHERE operation_id=?",
                    (billing_status, operation_id),
                )
            db.execute(
                "UPDATE provider_attempts SET state='SUCCEEDED',reservation_status=?,"
                "billing_uncertain=?,updated_at=? WHERE attempt_id=?",
                (billing_status, int(result.usage is None), utcnow(), attempt_id),
            )
        return parsed, False
