#!/usr/bin/env python3
"""
jsonl_serializer.py — DIAGNOSTIC / OFFLINE-INSPECTION TOOL ONLY.

⚠️  WARNING — DO NOT USE THIS FOR ACTUAL BATCH SUBMISSION.
    synthesis_gate.py builds and submits Claude batch requests INLINE — it
    does not call this file. This script exists ONLY so you can build a
    JSONL file locally and eyeball/validate it before trusting the pipeline.

    Running `build` here and then ALSO running `synthesis_gate.py --submit`
    does NOT double-submit (this script never calls the Anthropic API by
    itself — it only writes a local file), so there is no billing risk from
    using `build`/`validate`. The risk would only appear if you manually
    wrote a THIRD script that reads this JSONL and POSTs it to
    `client.messages.batches.create()` — don't do that; use
    `synthesis_gate.py --submit <tier>` instead, which is the single
    source of truth for what actually gets billed.

ACCURACY LIMITATION — read before trusting this file's output:
This script always builds exactly ONE request per module. synthesis_gate.py
actually splits any module with suggested_chapters > 1 into multiple
chapter-level requests (93 of 142 manifest modules need this — Claude's real
output ceiling is 8,192 tokens, not enough for dense 5-6 subtopic modules in
one shot). This file does NOT replicate that splitting. For single-chapter
modules the JSONL you get here is an accurate preview of what will be
submitted; for multi-chapter modules it is NOT — it shows one combined
request instead of the N chapter requests that synthesis_gate.py will
actually send. Treat this as a rough structural sanity-check only, never as
an exact preview for any module where suggested_chapters > 1.

COMMANDS:
  python3 jsonl_serializer.py build --tier Senior        # write a local JSONL for inspection
  python3 jsonl_serializer.py validate --file batch.jsonl # check JSONL structural validity
"""
import re
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional

from config import (
    log, console, safe_slug,
    MASTER_MANIFEST, BATCH_JSONL_DIR, VAULT_STATE,
    TIER_MODEL_MAP, CLAUDE_HAIKU, CLAUDE_SONNET,
)

_SYS = {
    "Senior": (
        "You are a senior-level educator preparing students for board exams and "
        "competitive exams (JEE/NEET). Write detailed Markdown revision notes: "
        "Overview, Theory & Derivations, Formulas (LaTeX), Solved Examples, PYQ Analysis."
    ),
    "Engineering": (
        "You are a Principal B.Tech Engineering Professor — Robotics & AI. "
        "Write complete exam-ready Markdown notes. Mandatory sections: "
        "Overview, Core Theory (full derivations), Mathematical Framework (LaTeX), "
        "Applications, Lab-Theory Bridge, Exam High-Yield Points. "
        "Display math: $$...$$  Inline: $...$"
    ),
}

_ID_PAT = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')

def _safe_cid(mid: str) -> str:
    c = re.sub(r'[^a-zA-Z0-9_-]','_', mid.strip())[:64]
    return c if _ID_PAT.match(c) else f"mod_{abs(hash(mid))%10**8}"

def _build_user_prompt(mod: dict) -> str:
    """Mirrors synthesis_gate.py's _build_prompt — kept in sync deliberately."""
    title     = mod.get("title","")
    subj      = mod.get("subject","")
    domain    = mod.get("domain","")
    content   = str(mod.get("content","")).strip()
    pyqs      = mod.get("pyqs",[])
    refs      = mod.get("references",[])
    subtopics = mod.get("core_subtopics",[])
    prereq    = mod.get("prerequisite_context",[])

    subtopic_blk = ""
    if isinstance(subtopics, list) and subtopics:
        subtopic_blk = "\n\n## Required Subtopics (cover each with full depth)\n" + \
                        "\n".join(f"- {s}" for s in subtopics)

    prereq_blk = ""
    if isinstance(prereq, list) and prereq:
        prereq_blk = "\n\n## Builds On (already covered — do not re-derive)\n" + \
                      "\n".join(f"- {p}" for p in prereq)
    else:
        prereq_blk = "\n\n## Prerequisite Context\nThis is a foundational topic — no prior coverage assumed."

    pyq_blk = ""
    if isinstance(pyqs,list) and pyqs:
        pyq_blk = "\n\nPYQs:\n" + "\n".join(f"- {q}" for q in pyqs[:8])
    ref_blk = ""
    if isinstance(refs,list) and refs:
        v = [r for r in refs[:4] if isinstance(r,dict) and r.get("url")]
        if v: ref_blk = "\n\nReferences:\n" + "\n".join(f"- {r.get('title',r['url'])}" for r in v)

    src = content[:9000] if content else "(Use domain expertise and the required subtopics above)"
    return (f"Generate complete study notes:\n**{title}** | {subj} | {domain}"
            f"{subtopic_blk}{prereq_blk}\n\nSOURCE:\n{'─'*40}\n{src}{pyq_blk}{ref_blk}")

def build_record(mid: str, system: str, user: str, model: str, max_tokens: int=64000) -> dict:
    return {
        "custom_id": _safe_cid(mid),
        "params": {
            "model": model, "max_tokens": max_tokens,
            "system": [{"type":"text","text":system,"cache_control":{"type":"ephemeral"}}],
            "messages": [{"role":"user","content": user}],
        }
    }

def build_jsonl(modules: list[dict], out: Path, tier: str,
                model: Optional[str]=None, skip_ids: Optional[set]=None) -> int:
    if skip_ids is None: skip_ids = set()
    model  = model or TIER_MODEL_MAP.get(tier,{}).get("synthesis_model", CLAUDE_HAIKU)
    system = _SYS.get(tier, _SYS["Engineering"])
    out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(out,'w',encoding='utf-8',newline='\n') as f:
        for mod in modules:
            if not isinstance(mod,dict): continue
            mid = mod.get("id","")
            if not mid or mid in skip_ids: continue
            user = _build_user_prompt(mod)
            if not user.strip(): continue
            rec = build_record(mid, system, user, model)
            f.write(json.dumps(rec, ensure_ascii=False, separators=(',',':')) + '\n')
            written += 1
    log.info(f"[green]JSONL written for INSPECTION ONLY: {written} records -> {out.name}[/green]")
    log.info("[yellow]Reminder: this file is NOT auto-submitted. Use synthesis_gate.py --submit for real batches.[/yellow]")
    return written

def validate_jsonl(path: Path) -> tuple[bool, list[str]]:
    if not path.exists(): return False, [f"Not found: {path}"]
    errors = []
    n = 0
    try:
        with open(path,'r',encoding='utf-8-sig') as f:
            for raw in f:
                n += 1
                raw = raw.strip()
                if not raw: continue
                try: rec = json.loads(raw)
                except json.JSONDecodeError as e:
                    errors.append(f"Line {n}: {e}"); continue
                if not isinstance(rec, dict): errors.append(f"Line {n}: not object"); continue
                if not rec.get("custom_id"):  errors.append(f"Line {n}: missing custom_id")
                if not isinstance(rec.get("params"), dict): errors.append(f"Line {n}: missing params")
    except OSError as e: return False, [str(e)]
    return len(errors)==0, errors

def main():
    p = argparse.ArgumentParser(
        prog="jsonl_serializer.py",
        description="DIAGNOSTIC TOOL ONLY — does not submit batches. Use synthesis_gate.py for that.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build")
    pb.add_argument("--tier",     required=True, choices=["Senior","Engineering"])
    pb.add_argument("--model",    default=None)
    pb.add_argument("--manifest", type=Path, default=MASTER_MANIFEST)

    pv = sub.add_parser("validate")
    pv.add_argument("--file", type=Path, required=True)

    args = p.parse_args()

    if args.cmd == "validate":
        ok, errs = validate_jsonl(args.file)
        if ok: console.print(f"[green]✓ {args.file.name} valid[/green]")
        else:
            console.print(f"[red]{len(errs)} errors:[/red]")
            for e in errs[:10]: console.print(f"  [red]• {e}[/red]")
        return

    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest, list): log.error("[red]Bad manifest[/red]"); return
    except Exception as e: log.error(f"[red]{e}[/red]"); return

    skip: set = set()
    if VAULT_STATE.exists():
        try:
            s = json.loads(VAULT_STATE.read_text(encoding="utf-8"))
            skip = set(s.get("synthesised_ids",[]))
        except Exception: pass

    target = [m for m in manifest if m.get("tier","")==args.tier]
    if not target: log.warning(f"[yellow]No {args.tier} modules[/yellow]"); return

    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = BATCH_JSONL_DIR / f"INSPECT_ONLY_batch_{args.tier.lower()}_{ts}.jsonl"
    n   = build_jsonl(target, out, args.tier, model=args.model, skip_ids=skip)
    if n: validate_jsonl(out)

if __name__ == "__main__":
    main()
