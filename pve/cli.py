from __future__ import annotations

import argparse
import json
from pathlib import Path

from .controller import Controller
from .manifest import ManifestGovernance, migrate_legacy_candidate, validate_envelope
from .settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pve")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status"); sub.add_parser("doctor"); sub.add_parser("providers")
    validate = sub.add_parser("validate"); validate.add_argument("path", type=Path)
    preview = sub.add_parser("preview"); preview.add_argument("--manifest-checksum", required=True)
    backup = sub.add_parser("backup"); backup.add_argument("destination", type=Path)
    restore = sub.add_parser("restore-check"); restore.add_argument("backup", type=Path)
    candidate = sub.add_parser("manifest-candidate"); candidate.add_argument("legacy", type=Path); candidate.add_argument("--source-set-id", required=True)
    export = sub.add_parser("export"); export.add_argument("destination", type=Path)
    for name in ("run", "resume", "rerun-failed", "rerun-stale", "jobs", "failures", "cost", "sources", "artifacts", "manifest", "settings"):
        sub.add_parser(name)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env(args.root)
    controller = Controller(settings)
    if args.command in ("status", "doctor"):
        controller.store.integrity_check(); print(json.dumps({"database": "ok", "live_providers": "disabled_by_default", "config_hash": settings.config_hash})); return 0
    if args.command == "providers":
        print(json.dumps({key: route.__dict__ for key, route in settings.routes.items()}, sort_keys=True)); return 0
    if args.command == "validate":
        value = json.loads(args.path.read_text(encoding="utf-8")); errors = validate_envelope(value)
        print(json.dumps({"valid": not errors, "errors": errors})); return 0 if not errors else 2
    if args.command == "preview":
        print(json.dumps(controller.preview({"all": True}, args.manifest_checksum, (0, 0), (0, 0)), sort_keys=True)); return 0
    if args.command == "backup":
        print(json.dumps(controller.store.backup(args.destination), sort_keys=True)); return 0
    if args.command == "restore-check":
        ok = controller.store.restore_check(args.backup); print(json.dumps({"restore_check": "PASS" if ok else "FAIL"})); return 0 if ok else 2
    if args.command == "manifest-candidate":
        envelope = migrate_legacy_candidate(args.legacy, args.source_set_id)
        cid, errors = ManifestGovernance(controller.store).create_candidate(envelope)
        print(json.dumps({"candidate_id": cid, "errors": errors})); return 0 if not errors else 2
    if args.command == "export":
        from .dashboard import snapshot
        payload = snapshot(controller.store); args.destination.parent.mkdir(parents=True, exist_ok=True)
        args.destination.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        print(json.dumps({"exported": str(args.destination), "provider_calls": 0})); return 0
    query_map={"jobs":"SELECT id,phase,state,lease_owner,fencing_token FROM jobs ORDER BY created_at DESC",
               "failures":"SELECT id,scope_type,scope_id,classification,detail,created_at FROM failures ORDER BY created_at DESC",
               "sources":"SELECT source_id,content_hash,source_type,origin,status FROM sources ORDER BY created_at DESC",
               "artifacts":"SELECT id,module_id,phase,content_hash,status FROM artifacts ORDER BY created_at DESC",
               "manifest":"SELECT id,checksum,status,created_at FROM manifest_candidates ORDER BY created_at DESC"}
    if args.command in query_map:
        with controller.store.connect() as db: rows=[dict(row) for row in db.execute(query_map[args.command])]
        print(json.dumps({"items":rows,"provider_calls":0},sort_keys=True)); return 0
    if args.command == "cost":
        with controller.store.connect() as db:
            row=db.execute("SELECT count(*),COALESCE(SUM(estimated_cost_usd),0),COALESCE(SUM(actual_cost_usd),0) FROM provider_operations").fetchone()
        print(json.dumps({"operations":row[0],"estimated_cost_usd":row[1],"actual_cost_usd":row[2],"provider_calls":0})); return 0
    if args.command == "settings":
        print(json.dumps({"config_hash":settings.config_hash,"routes":{k:v.__dict__ for k,v in settings.routes.items()},"secrets":"not_exposed","provider_calls":0},sort_keys=True)); return 0
    # Mutating execution commands are scaffolded behind controller state and do not invoke providers.
    print(json.dumps({"command": args.command, "status": "NO_ELIGIBLE_WORK", "provider_calls": 0})); return 0


if __name__ == "__main__": raise SystemExit(main())
