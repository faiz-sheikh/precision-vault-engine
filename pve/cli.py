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
    for name in ("run", "resume", "rerun-failed", "rerun-stale", "jobs", "failures", "cost", "sources", "artifacts"):
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
    # Mutating execution commands are scaffolded behind controller state and do not invoke providers.
    print(json.dumps({"command": args.command, "status": "NO_ELIGIBLE_WORK", "provider_calls": 0})); return 0


if __name__ == "__main__": raise SystemExit(main())
