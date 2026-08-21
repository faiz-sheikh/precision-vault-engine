"""Approval-gated single-request live canary runner. Preview mode is fully offline."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from typing import Any

from .controller import Controller
from .identity import provider_fingerprint
from .providers import (
    Adapter,
    google_live_adapter,
    openai_compatible_live_adapter,
    openai_responses_live_adapter,
)
from .safety import Provider
from .settings import Settings
from .store import Store

APPROVAL_TOKEN = "PVE_LIVE_CANARY_APPROVED"
CANARY_MODULE = "mod_canary"

CANARY_INPUTS = {
    "WEB_GROUNDED_RESEARCH": (
        "In at most two sentences, define a PID controller and cite one authoritative web source."
    ),
    "ROUTINE_GENERATION": (
        "Return JSON with one key named summary containing one sentence defining a PID controller."
    ),
    "ENGINEERING_REASONING": (
        "Return JSON with one key named result explaining in at most three sentences why integral "
        "action removes steady-state error for a constant disturbance."
    ),
    "FRONTIER_ESCALATION": (
        "Return JSON with one key named result containing a two-sentence explanation of derivative "
        "kick in PID control."
    ),
}


def canonical_request(capability: str, text: str, max_output_tokens: int) -> dict[str, Any]:
    if capability == "WEB_GROUNDED_RESEARCH":
        return {
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": max_output_tokens},
        }
    if capability == "FRONTIER_ESCALATION":
        return {
            "input": text,
            "reasoning": {"effort": "low"},
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
    return {
        "messages": [{"role": "user", "content": text}],
        "temperature": 0,
        "max_tokens": max_output_tokens,
        "response_format": {"type": "json_object"},
    }


def adapter_for(capability: str, settings: Settings) -> Adapter:
    route = settings.routes[capability]
    if route.provider == "google":
        return google_live_adapter()
    if route.provider == "nvidia":
        if route.endpoint is None:
            raise RuntimeError("NVIDIA endpoint is not configured")
        return openai_compatible_live_adapter(Provider.NVIDIA, route.endpoint)
    if route.provider == "openai":
        return openai_responses_live_adapter()
    raise RuntimeError(f"unsupported canary provider: {route.provider}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pve.canary")
    parser.add_argument("mode", choices=("preview", "execute", "verify"))
    parser.add_argument("--capability", choices=tuple(CANARY_INPUTS), required=True)
    parser.add_argument("--max-output-tokens", type=int, default=128)
    parser.add_argument("--budget-usd", type=float, default=0.05)
    parser.add_argument("--estimated-cost-usd", type=float, default=0.01)
    parser.add_argument("--approval-token", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_output_tokens < 1 or args.max_output_tokens > 256:
        raise SystemExit("max-output-tokens must be between 1 and 256")
    if args.budget_usd <= 0 or args.estimated_cost_usd < 0:
        raise SystemExit("budget values must be non-negative and budget must be positive")
    settings = replace(
        Settings.from_env(),
        max_provider_attempts=1,
        max_requests_per_run=1,
        max_cost_usd=args.budget_usd,
    )
    route = settings.routes[args.capability]
    text = CANARY_INPUTS[args.capability]
    request = canonical_request(args.capability, text, args.max_output_tokens)
    fingerprint = provider_fingerprint(
        provider=route.provider,
        model=route.model,
        request=request,
        prompt_hash="pre-canary-v1",
        parameters={"max_output_tokens": args.max_output_tokens},
        schema={"type": "object"},
        run_config_hash=settings.config_hash,
        upstream_hashes=[],
    )
    operation_id = "op_" + fingerprint.split(":", 1)[1]
    phase = args.capability.casefold()
    raw_path = settings.artifact_dir / CANARY_MODULE / phase / operation_id / "raw" / "response.bin"
    result = {
        "mode": args.mode,
        "provider": route.provider,
        "model": route.model,
        "capability": args.capability,
        "input": text,
        "max_requests": 1,
        "retry_ceiling": 0,
        "max_output_tokens": args.max_output_tokens,
        "budget_usd": args.budget_usd,
        "estimated_cost_usd": args.estimated_cost_usd,
        "fingerprint": fingerprint,
        "operation_id": operation_id,
        "raw_response_path": str(raw_path),
    }
    if args.mode == "preview":
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.mode == "verify":
        store = Store(settings.database_path)
        store.initialize()
        with store.connect() as db:
            operation = db.execute(
                "SELECT state,attempts,provider_id,raw_path,raw_sha256 "
                "FROM provider_operations WHERE fingerprint=?",
                (fingerprint,),
            ).fetchone()
            event_count = db.execute(
                "SELECT count(*) FROM provider_events WHERE operation_id=?", (operation_id,)
            ).fetchone()[0]
            total_operations = db.execute("SELECT count(*) FROM provider_operations").fetchone()[0]
        verification = {
            **result,
            "mode": "verify",
            "found": operation is not None,
            "state": operation[0] if operation else None,
            "attempts": operation[1] if operation else 0,
            "provider_id_present": bool(operation and operation[2]),
            "raw_path": operation[3] if operation else None,
            "raw_sha256": operation[4] if operation else None,
            "raw_exists": bool(operation and operation[3] and os.path.isfile(operation[3])),
            "event_count": event_count,
            "total_operations": total_operations,
        }
        print(json.dumps(verification, sort_keys=True))
        return 0
    if args.approval_token != APPROVAL_TOKEN or os.getenv("PVE_CANARY_APPROVAL") != APPROVAL_TOKEN:
        raise SystemExit("live canary execution requires the exact separate approval token")
    controller = Controller(settings)
    parsed, reused = controller.execute_provider(
        capability=args.capability,
        request=request,
        prompt_hash="pre-canary-v1",
        parameters={"max_output_tokens": args.max_output_tokens},
        schema={"type": "object"},
        upstream_hashes=[],
        module_id=CANARY_MODULE,
        phase=phase,
        adapter=adapter_for(args.capability, settings),
        parse=lambda raw: json.loads(raw),
        estimated_cost_usd=args.estimated_cost_usd,
    )
    parsed_directory = settings.artifact_dir / CANARY_MODULE / phase / operation_id / "parsed"
    parsed_directory.mkdir(parents=True, exist_ok=True)
    parsed_path = parsed_directory / "result.json"
    parsed_path.write_text(json.dumps(parsed, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({**result, "reused": reused, "parsed_path": str(parsed_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
