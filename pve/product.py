"""Safe, provider-free product workflows built over PVE operational state."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .artifacts import ArtifactRegistry
from .identity import sha256_identity
from .settings import Settings
from .store import Store, utcnow


class ProductMode(StrEnum):
    SIMPLE = "Simple"
    DEMO = "Demo"
    EXPERT = "Expert"


class Depth(StrEnum):
    QUICK = "Quick"
    STANDARD = "Standard"
    DEEP = "Deep"


class ProductError(RuntimeError):
    """A safe, user-displayable product error."""


class AmbiguousIntent(ProductError):
    pass


class StalePreview(ProductError):
    pass


class ForbiddenAction(ProductError):
    pass


ARTIFACT_LABELS = {
    "research": "Grounded Research",
    "notes": "Detailed Notes",
    "revision": "Revision Sheet",
    "quiz": "Quiz",
    "viva": "Viva Practice",
    "flashcards": "Flashcards",
}
WORKFLOWS = {
    "Generate Notes": ("research", "notes"),
    "Research Topic": ("research",),
    "Explain Simply": ("notes",),
    "Create Revision Sheet": ("revision",),
    "Create Quiz": ("quiz",),
    "Practice Viva": ("viva",),
    "Create Flashcards": ("flashcards",),
    "Full Study Pack": ("research", "notes", "revision", "quiz", "viva", "flashcards"),
}
PROVIDER_OPERATIONS = {
    "research": "Gemini",
    "notes": "Nemotron Super",
    "revision": "Nemotron Super",
    "quiz": "Nemotron Super",
    "viva": "Nemotron Super",
    "flashcards": "Nemotron Super",
}
ADMIN_ACTIONS = frozenset(
    {
        "manifest_promotion",
        "source_authority_change",
        "budget_increase",
        "provider_live_enablement",
        "manual_provider_retry",
        "ambiguous_reconciliation",
        "legacy_migration",
        "destructive_cleanup",
        "backup_restore",
        "database_recovery",
    }
)


@dataclass(frozen=True)
class StudyCommand:
    command_id: str
    user_intent: str
    module_id: str
    requested_artifacts: tuple[str, ...]
    depth: Depth
    quality_policy: str
    provider_capabilities_required: tuple[str, ...]
    max_provider_operations: int
    preview_id: str
    config_hash: str
    manifest_checksum: str
    dependency_state: str
    created_at: str
    demo: bool = False


@dataclass(frozen=True)
class Preview:
    command: StudyCommand
    estimated_requests_min: int
    estimated_requests_max: int
    estimated_cost: str
    reusable_items: int
    new_operations: int


def _normalize_module(value: str) -> str:
    clean = re.sub(r"\s+", " ", value).strip(" .?!,:;\t\n")
    if not clean or len(clean) < 2 or len(clean) > 160:
        raise AmbiguousIntent("Please name one specific subject or module.")
    return clean


def parse_study_request(text: str) -> tuple[str, tuple[str, ...], Depth, int | None]:
    """Parse ordinary study requests locally. Never executes or calls a provider."""
    raw = re.sub(r"\s+", " ", text).strip()
    lowered = raw.casefold()
    if not raw:
        raise AmbiguousIntent("Describe what you want to study.")
    dangerous = (
        "ignore the budget",
        "budget to unlimited",
        "increase the budget",
        "delete the old manifest",
        "rebuild everything",
        "retry until",
        "whichever api",
        "enable live",
    )
    if any(item in lowered for item in dangerous):
        raise ForbiddenAction(
            "This request changes safety or administration settings. Use Expert Mode and the "
            "appropriate confirmation workflow; it cannot run as a study command."
        )
    if "everything for every subject" in lowered or "all modules" in lowered:
        raise AmbiguousIntent("Choose one module first so PVE can keep the plan and budget bounded.")

    requested: list[str] = []
    markers = {
        "research": ("research", "sources"),
        "notes": ("note", "explain"),
        "revision": ("revision", "cheat sheet", "summary"),
        "quiz": ("quiz", "question", "assessment"),
        "viva": ("viva", "oral practice"),
        "flashcards": ("flashcard",),
    }
    if any(term in lowered for term in ("everything", "full study pack", "study pack")):
        requested = list(WORKFLOWS["Full Study Pack"])
    else:
        requested = [kind for kind, words in markers.items() if any(word in lowered for word in words)]
    if not requested:
        raise AmbiguousIntent("Should PVE create notes, a quiz, viva practice, or a full study pack?")

    count_match = re.search(r"\b(\d{1,3})[ -]*(?:question|item)", lowered)
    question_count = int(count_match.group(1)) if count_match else None
    if question_count is not None and not 1 <= question_count <= 100:
        raise ForbiddenAction("Choose between 1 and 100 questions.")
    depth = Depth.DEEP if any(x in lowered for x in ("detailed", "deep", "comprehensive")) else Depth.STANDARD
    if any(x in lowered for x in ("quick", "brief", "simple")):
        depth = Depth.QUICK

    module = raw
    if count_match:
        module = re.sub(re.escape(count_match.group(0)), " ", module, flags=re.IGNORECASE)
    patterns = (
        r"^(?:please )?(?:make|create|build|generate|prepare|practice|research|explain)\s+",
        r"\b(?:detailed|deep|comprehensive|quick|brief|simple)\b",
        r"\b(?:notes?|quiz|questions?|assessment|viva|oral practice|flashcards?|revision sheet|summary|study pack|everything)\b",
        r"\bfor\b",
        r"\bon\b",
        r"\babout\b",
        r"\b(?:and|a|an|the|me|please|create|make)\b",
        r"\b\d{1,3}[ -]*(?:question|item)s?\b",
    )
    for pattern in patterns:
        module = re.sub(pattern, " ", module, flags=re.IGNORECASE)
    return _normalize_module(module), tuple(dict.fromkeys(requested)), depth, question_count


def manifest_checksum(root: Path) -> str:
    for candidate in (
        root / "master_manifest.json",
        root / "pve" / "fixtures" / "demo_manifest.json",
        Path(__file__).parent / "fixtures" / "demo_manifest.json",
    ):
        if candidate.is_file():
            return "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()
    return "unavailable"


def dependency_state(store: Store, module_id: str) -> str:
    with store.connect() as db:
        rows = [tuple(row) for row in db.execute(
            "SELECT phase,content_hash,status FROM artifacts WHERE module_id=? ORDER BY phase,id",
            (module_id,),
        )]
    return sha256_identity("pve-product-dependency-v1", rows)


class ProductService:
    """Controller-safe product façade. No provider adapter is imported here."""

    def __init__(self, settings: Settings, root: Path | None = None):
        self.settings = settings
        self.root = (root or Path.cwd()).resolve()
        self.store = Store(settings.database_path)
        self.store.initialize()
        self.registry = ArtifactRegistry(self.store)

    def create_preview(
        self,
        *,
        module_id: str,
        artifacts: tuple[str, ...],
        depth: Depth = Depth.STANDARD,
        intent: str = "guided workflow",
        demo: bool = False,
        question_count: int | None = None,
    ) -> Preview:
        module = _normalize_module(module_id)
        requested = tuple(dict.fromkeys(artifacts))
        if not requested or any(item not in ARTIFACT_LABELS for item in requested):
            raise ProductError("Choose at least one supported study material type.")
        if question_count is not None and "quiz" not in requested:
            raise ProductError("A question count requires a quiz in the plan.")
        preview_id = "preview_" + uuid.uuid4().hex
        command_id = "cmd_" + uuid.uuid4().hex
        operations = 0 if demo else len(requested)
        command = StudyCommand(
            command_id=command_id,
            user_intent=intent[:500],
            module_id=module,
            requested_artifacts=requested,
            depth=depth,
            quality_policy="grounded_and_verified",
            provider_capabilities_required=tuple(
                dict.fromkeys(PROVIDER_OPERATIONS[item] for item in requested)
            ),
            max_provider_operations=operations,
            preview_id=preview_id,
            config_hash=self.settings.config_hash,
            manifest_checksum=manifest_checksum(self.root),
            dependency_state=dependency_state(self.store, module),
            created_at=utcnow(),
            demo=demo,
        )
        payload = asdict(command)
        payload["depth"] = command.depth.value
        payload["question_count"] = question_count
        idempotency = sha256_identity(
            "pve-product-command-v1",
            {
                "module": module.casefold(),
                "artifacts": requested,
                "depth": depth.value,
                "demo": demo,
                "question_count": question_count,
                "config": command.config_hash,
                "manifest": command.manifest_checksum,
                "dependencies": command.dependency_state,
            },
        )
        with self.store.transaction() as db:
            existing = db.execute(
                "SELECT id,preview_id,payload_json FROM commands WHERE idempotency_key=? AND run_id IS NULL",
                (idempotency,),
            ).fetchone()
            if existing:
                payload = json.loads(existing["payload_json"])
                command = _command_from_payload(payload)
                preview_id = existing["preview_id"]
            else:
                db.execute(
                    "INSERT INTO commands VALUES(?,?,?,?,?,?,?)",
                    (command_id, idempotency, "STUDY", json.dumps(payload, sort_keys=True), preview_id, None, utcnow()),
                )
        reusable = self._reusable_count(module, requested)
        return Preview(command, max(0, operations - 1), operations, "Unknown" if operations else "$0 (Demo)", reusable, max(0, operations - reusable))

    def preview_from_text(self, text: str, *, demo: bool = False) -> Preview:
        module, requested, depth, count = parse_study_request(text)
        return self.create_preview(
            module_id=module,
            artifacts=requested,
            depth=depth,
            intent=text,
            demo=demo,
            question_count=count,
        )

    def execute_demo(self, preview_id: str) -> dict[str, Any]:
        command, row = self._load_current_preview(preview_id)
        if not command.demo:
            raise ForbiddenAction("Normal provider-backed execution is not available from Demo Mode.")
        if row["run_id"]:
            return self.run_result(str(row["run_id"])) | {"reused": True}
        fixture = self._demo_fixture(command.module_id)
        run_id = "run_demo_" + hashlib.sha256(row["idempotency_key"].encode()).hexdigest()[:24]
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO runs VALUES(?,?,?,?,?,?)",
                (run_id, json.dumps({"mode": "demo"}), command.config_hash, command.manifest_checksum, "SUCCEEDED", utcnow()),
            )
            claimed = db.execute(
                "UPDATE commands SET run_id=? WHERE id=? AND run_id IS NULL", (run_id, row["id"])
            ).rowcount
            if not claimed:
                current = db.execute("SELECT run_id FROM commands WHERE id=?", (row["id"],)).fetchone()
                return self.run_result(str(current["run_id"])) | {"reused": True}
        produced: list[dict[str, Any]] = []
        for kind in command.requested_artifacts:
            content = fixture.get(kind, f"Demo {ARTIFACT_LABELS[kind]} for {command.module_id}")
            raw = content.encode("utf-8")
            content_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
            fingerprint = sha256_identity("pve-demo-artifact-v1", {"module": command.module_id, "kind": kind, "content": content_hash})
            artifact_id, reused = self.registry.register(
                module_id=command.module_id,
                phase=kind,
                generation_id=run_id,
                dependency_fingerprint=fingerprint,
                content_hash=content_hash,
                status="DEMO_OUTPUT",
            )
            metadata = {
                "title": f"{command.module_id}: {ARTIFACT_LABELS[kind]}",
                "artifact_type": kind,
                "content": content,
                "quality_status": "Demo verified",
                "grounding_status": "Bundled demo sources",
                "version": "Demo 1",
                "sources": fixture.get("sources", []),
                "demo": True,
                "provider_calls": 0,
            }
            path = self.settings.artifact_dir / "demo" / artifact_id / "metadata.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(metadata, sort_keys=True, indent=2), encoding="utf-8")
            with self.store.transaction() as db:
                db.execute("UPDATE artifacts SET metadata_path=? WHERE id=?", (str(path), artifact_id))
            produced.append({"id": artifact_id, "type": kind, "title": metadata["title"], "reused": reused})
        return {"run_id": run_id, "status": "SUCCEEDED", "artifacts": produced, "provider_calls": 0, "demo": True, "reused": False}

    def _load_current_preview(self, preview_id: str) -> tuple[StudyCommand, sqlite3.Row]:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM commands WHERE preview_id=?", (preview_id,)).fetchone()
        if row is None:
            raise ProductError("This preview no longer exists. Please create a new plan.")
        command = _command_from_payload(json.loads(row["payload_json"]))
        # A completed command is immutable and duplicate interactions reuse it.
        # Staleness only governs the transition from preview to first execution.
        if row["run_id"]:
            return command, row
        if (
            command.config_hash != self.settings.config_hash
            or command.manifest_checksum != manifest_checksum(self.root)
            or command.dependency_state != dependency_state(self.store, command.module_id)
        ):
            raise StalePreview(
                "Your plan changed since this preview was created. Please review the updated plan before running."
            )
        return command, row

    def run_result(self, run_id: str) -> dict[str, Any]:
        with self.store.connect() as db:
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            artifacts = [dict(row) for row in db.execute("SELECT * FROM artifacts WHERE generation_id=?", (run_id,))]
        if run is None:
            raise ProductError("The saved run could not be found.")
        return {"run_id": run_id, "status": run["status"], "artifacts": artifacts, "provider_calls": 0, "demo": run_id.startswith("run_demo_")}

    def materials(self, query: str = "", artifact_type: str = "All") -> list[dict[str, Any]]:
        terms = query.casefold().split()
        with self.store.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM artifacts ORDER BY created_at DESC")]
        items: list[dict[str, Any]] = []
        for row in rows:
            metadata: dict[str, Any] = {}
            path = row.get("metadata_path")
            if path:
                try:
                    metadata = json.loads(Path(path).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    metadata = {}
            item = row | metadata
            item.setdefault("title", f"{row['module_id']}: {ARTIFACT_LABELS.get(row['phase'], row['phase'].title())}")
            item.setdefault("artifact_type", row["phase"])
            item.setdefault("quality_status", "Not recorded")
            item.setdefault("grounding_status", "Not recorded")
            item.setdefault("version", "1")
            haystack = json.dumps({"module": row["module_id"], "title": item["title"], "type": item["artifact_type"]}).casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            if artifact_type != "All" and item["artifact_type"] != artifact_type:
                continue
            items.append(item)
        return items

    def doctor(self) -> dict[str, Any]:
        self.store.integrity_check()
        fixture = self._fixture_path("demo_study_pack.json")
        providers = {}
        env_names = {"Gemini": "GOOGLE_API_KEY", "NVIDIA": "NVIDIA_API_KEY", "OpenAI": "OPENAI_API_KEY", "Anthropic": "ANTHROPIC_API_KEY"}
        global_live = os.getenv("PVE_ALLOW_LIVE_PROVIDERS", "0") == "1"
        for name, env_name in env_names.items():
            specific = os.getenv(f"PVE_ALLOW_{'GOOGLE' if name == 'Gemini' else name.upper()}_LIVE", "0") == "1"
            providers[name] = {"configured": bool(os.getenv(env_name)), "live": global_live and specific}
        return {
            "database": "Healthy",
            "storage": "Healthy" if os.access(self.settings.artifact_dir, os.W_OK) else "Needs attention",
            "manifest": "Available" if manifest_checksum(self.root) != "unavailable" else "Needs review",
            "demo": "Ready" if fixture.is_file() else "Unavailable",
            "providers": providers,
            "provider_calls": 0,
        }

    @staticmethod
    def require_admin(mode: ProductMode, action: str, confirmed: bool = False) -> None:
        if action not in ADMIN_ACTIONS:
            raise ProductError("Unknown administrative action.")
        if mode is not ProductMode.EXPERT:
            raise ForbiddenAction("This operation is only available in Expert Mode.")
        if not confirmed:
            raise ForbiddenAction("Explicit confirmation is required for this operation.")

    def _reusable_count(self, module: str, requested: tuple[str, ...]) -> int:
        placeholders = ",".join("?" for _ in requested)
        with self.store.connect() as db:
            return int(db.execute(
                f"SELECT count(DISTINCT phase) FROM artifacts WHERE module_id=? AND phase IN ({placeholders}) AND status IN ('SUCCEEDED','DEMO_OUTPUT')",
                (module, *requested),
            ).fetchone()[0])

    def _demo_fixture(self, module: str) -> dict[str, Any]:
        path = self._fixture_path("demo_study_pack.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        fixture = data.get(module.casefold()) or data.get("pid control")
        if not isinstance(fixture, dict):
            raise ProductError("The bundled demo material is unavailable.")
        return fixture

    def _fixture_path(self, name: str) -> Path:
        local = self.root / "pve" / "fixtures" / name
        return local if local.is_file() else Path(__file__).parent / "fixtures" / name


def _command_from_payload(payload: dict[str, Any]) -> StudyCommand:
    return StudyCommand(
        command_id=payload["command_id"], user_intent=payload["user_intent"], module_id=payload["module_id"],
        requested_artifacts=tuple(payload["requested_artifacts"]), depth=Depth(payload["depth"]),
        quality_policy=payload["quality_policy"], provider_capabilities_required=tuple(payload["provider_capabilities_required"]),
        max_provider_operations=int(payload["max_provider_operations"]), preview_id=payload["preview_id"],
        config_hash=payload["config_hash"], manifest_checksum=payload["manifest_checksum"],
        dependency_state=payload["dependency_state"], created_at=payload["created_at"], demo=bool(payload.get("demo", False)),
    )


FAILURE_COPY = {
    "ACCOUNT_OR_BILLING_BLOCKED": "This provider cannot currently accept requests because its account or billing quota needs attention.",
    "BLOCKED_ACCOUNT_OR_BILLING": "This provider cannot currently accept requests because its account or billing quota needs attention.",
    "RATE_LIMIT_TRANSIENT": "The provider is temporarily rate-limiting requests.",
    "FAILED_TRANSIENT": "The provider had a temporary problem. PVE preserved completed work.",
    "AUTH_BLOCKED": "The provider credentials are missing or invalid.",
    "BLOCKED_AUTH": "The provider credentials are missing or invalid.",
    "MODEL_ACCESS_BLOCKED": "This model is not available to the configured account.",
    "BLOCKED_MODEL_ACCESS": "This model is not available to the configured account.",
    "AMBIGUOUS": "PVE cannot prove whether the provider accepted the previous request. For safety, it will not submit another one automatically.",
}


def friendly_failure(classification: str, provider_calls: int = 0) -> dict[str, Any]:
    return {
        "what_happened": FAILURE_COPY.get(classification, "The run could not finish safely."),
        "preserved": "Your completed work and request history were saved.",
        "provider_effect": f"External provider requests made: {provider_calls}. Automatic retries made: 0.",
        "next_step": "Return to your materials or ask the administrator to review the advanced details.",
        "retry_safe": classification in {"RATE_LIMIT_TRANSIENT", "FAILED_TRANSIENT"},
    }
