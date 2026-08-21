#!/usr/bin/env python3
"""
assessment_bank.py — Phase 6 — MCQ + Formula + Short Notes Banks

Uses core_subtopics from the manifest to anchor question generation to
concrete syllabus content rather than relying purely on vault text + title.
"""
import json
import time
import math
import argparse
from pathlib import Path
from typing import Optional

from google import genai as google_genai
from google.genai import types as gt

from config import (
    log, console, get_env, safe_slug, safe_gemini_text, robust_json_parse,
    MASTER_MANIFEST, VAULT_DIR, GEMINI_FLASH_MODEL, MAX_RETRIES, calc_backoff,
)

ASSESS_DIR = VAULT_DIR.parent / "_assessment" / "exam_prep"

_MCQ_SYS   = "Output ONLY a JSON array of MCQ objects. No markdown. No preamble.\nSchema: [{\"question\":\"...\",\"options\":{\"A\":\"...\",\"B\":\"...\",\"C\":\"...\",\"D\":\"...\"},\"correct\":\"A|B|C|D\",\"explanation\":\"...\",\"difficulty\":\"Easy|Medium|Hard\",\"bloom\":\"Remember|Understand|Apply|Analyze\"}]"
_FORM_SYS  = "Output ONLY a JSON array of formula objects. No markdown.\nSchema: [{\"name\":\"...\",\"latex\":\"$$...$$\",\"variables\":{\"sym\":\"meaning\"},\"units\":\"SI\",\"priority\":\"High|Medium|Low\"}]"
_SHORT_SYS = "Output ONLY a JSON array of short-answer Q&As. No markdown.\nSchema: [{\"question\":\"...\",\"answer\":\"...\",\"key_terms\":[\"...\"],\"marks\":5}]"

def _cfg(**kw) -> gt.GenerateContentConfig:
    return gt.GenerateContentConfig(
        thinking_config=gt.ThinkingConfig(thinking_budget=0),
        tool_config=gt.ToolConfig(function_calling_config=gt.FunctionCallingConfig(
            mode=gt.FunctionCallingMode.NONE)), **kw)

def _call(client, prompt: str, temperature=0.3, max_tokens=4096, label="") -> Optional[str]:
    for i in range(MAX_RETRIES):
        try:
            r    = client.models.generate_content(
                model=GEMINI_FLASH_MODEL, contents=prompt,
                config=_cfg(temperature=temperature, max_output_tokens=max_tokens))
            text = safe_gemini_text(r)
            if text: return text
            if i >= MAX_RETRIES-1: return None
            time.sleep(calc_backoff(i))
        except Exception as e:
            es = str(e).lower()
            if "429" in es or "resource_exhausted" in es:
                time.sleep(min(60*(i+1),300)); continue
            if i >= MAX_RETRIES-1: log.warning(f"  [yellow]{label}: {e}[/yellow]"); return None
            time.sleep(calc_backoff(i))
    return None

def _vault_content(mod: dict) -> str:
    tier  = mod.get("tier","Junior")
    subj  = safe_slug(mod.get("subject","unknown"))
    title = safe_slug(mod.get("title",mod.get("id","module")))
    p     = VAULT_DIR / tier / subj / f"{title}.md"
    if not p.exists(): return ""
    try: return p.read_text(encoding="utf-8")
    except OSError: return ""

def _subtopic_block(mod: dict) -> str:
    cst = mod.get("core_subtopics",[])
    if not isinstance(cst, list) or not cst: return ""
    return "\nSyllabus subtopics to draw questions from:\n" + "\n".join(f"- {s}" for s in cst)

def gen_mcq(mod: dict, content: str, client, n: int=20) -> Optional[list]:
    if not content.strip() and not mod.get("core_subtopics"): return None
    all_q: list = []
    batch = 5
    no_prog = 0
    for _ in range(math.ceil(n/batch)+3):
        if len(all_q) >= n: break
        rem  = min(batch, n - len(all_q))
        prompt = (f"{_MCQ_SYS}\n\nModule: {mod.get('title','')} | {mod.get('subject','')}"
                  f"{_subtopic_block(mod)}\nContent:\n{content[:6000]}\n\n"
                  f"Generate EXACTLY {rem} MCQ(s). Have {len(all_q)}/{n}.")
        raw  = _call(client, prompt, label=f"MCQ:{mod.get('id','')[:20]}")
        if not raw: break
        parsed = robust_json_parse(raw)
        prev = len(all_q)
        if isinstance(parsed, list):
            all_q.extend(q for q in parsed if isinstance(q,dict) and q.get("question"))
        if len(all_q) == prev:
            no_prog += 1
            if no_prog >= 2: break
        else: no_prog = 0
    return all_q[:n] if all_q else None

def gen_formulas(mod: dict, content: str, client) -> Optional[list]:
    if not content.strip() and not mod.get("core_subtopics"): return None
    prompt = (f"{_FORM_SYS}\n\nModule: {mod.get('title','')} | {mod.get('domain','')}"
              f"{_subtopic_block(mod)}\nContent:\n{content[:6000]}\n\nExtract ALL formulas.")
    # max_tokens raised to 8192 — "ALL formulas" on a dense module (e.g. Control
    # Systems with 10-15 formulas + variable tables) can exceed the 4096 default.
    raw = _call(client, prompt, temperature=0.1, max_tokens=8192, label=f"FORM:{mod.get('id','')[:20]}")
    if not raw: return None
    p = robust_json_parse(raw)
    return [f for f in p if isinstance(f,dict) and f.get("name")] if isinstance(p,list) else None

def gen_short_notes(mod: dict, content: str, client) -> Optional[list]:
    if not content.strip() and not mod.get("core_subtopics"): return None
    prompt = (f"{_SHORT_SYS}\n\nModule: {mod.get('title','')} | Complexity: {mod.get('complexity','Mixed')}"
              f"{_subtopic_block(mod)}\nContent:\n{content[:6000]}\n\nGenerate 10 short-answer Q&As.")
    raw = _call(client, prompt, max_tokens=6144, label=f"SHORT:{mod.get('id','')[:20]}")
    if not raw: return None
    p = robust_json_parse(raw)
    return [n for n in p if isinstance(n,dict) and n.get("question")] if isinstance(p,list) else None

def run_module(mod: dict, client) -> bool:
    mid  = mod.get("id","unknown")
    slug = safe_slug(f"{mod.get('tier','')[:3]}_{mid[:30]}")
    out  = ASSESS_DIR / f"assessment_{slug}.json"
    if out.exists() and out.stat().st_size > 0:
        log.info(f"  [green]CACHED[/green] {mid}"); return True

    content  = _vault_content(mod)
    if not content and not mod.get("core_subtopics"):
        log.warning(f"  [yellow]No vault content or subtopics: {mid}[/yellow]")

    log.info(f"  Generating: {mod.get('title',mid)}")
    mcqs     = gen_mcq(mod, content, client)
    formulas = gen_formulas(mod, content, client)
    shorts   = gen_short_notes(mod, content, client)

    if not any([mcqs, formulas, shorts]):
        log.error(f"  [red]All generators failed: {mid}[/red]"); return False

    payload = {
        "_meta": {"module_id":mid,"title":mod.get("title",""),"tier":mod.get("tier",""),
                  "domain":mod.get("domain","")},
        "mcq_bank":      mcqs     or [],
        "formula_cards": formulas or [],
        "short_notes":   shorts   or [],
    }
    ASSESS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload,indent=2,ensure_ascii=False), encoding="utf-8")
        tmp.replace(out)
        log.info(f"  [green]✓[/green] MCQ:{len(payload['mcq_bank'])} "
                 f"Formula:{len(payload['formula_cards'])} Short:{len(payload['short_notes'])}")
        return True
    except OSError as e:
        tmp.unlink(missing_ok=True); log.error(f"  [red]Write failed: {e}[/red]"); return False

def run(manifest_path=MASTER_MANIFEST, tier_filter=None, domain_filter=None, client=None):
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, list): log.error("[red]Bad manifest[/red]"); return
    except Exception as e: log.error(f"[red]{e}[/red]"); return

    target = manifest
    if tier_filter:   target = [m for m in target if m.get("tier")==tier_filter]
    if domain_filter: target = [m for m in target if m.get("domain")==domain_filter]

    log.info(f"[bold]Phase 6: Assessment Bank[/bold] — {len(target)} modules")
    ok = err = 0
    for i,mod in enumerate(target):
        log.info(f"  [{i+1}/{len(target)}] {mod.get('title',mod.get('id','?'))}")
        try:
            if run_module(mod, client): ok += 1
            else: err += 1
        except Exception as e: log.error(f"  [red]{e}[/red]"); err += 1

    from rich.panel import Panel
    console.print(Panel(f"Done: [green]{ok}[/green]  Failed: [red]{err}[/red]",
        title="Phase 6 Complete", border_style="green" if not err else "yellow"))

def main():
    from pve.safety import require_legacy_paid_execution
    require_legacy_paid_execution()
    p = argparse.ArgumentParser(prog="assessment_bank.py")
    p.add_argument("--manifest", type=Path, default=MASTER_MANIFEST)
    p.add_argument("--tier",     default=None)
    p.add_argument("--domain",   default=None)
    p.add_argument("--all",      action="store_true", help="Process all modules (default behavior)")
    args   = p.parse_args()
    client = google_genai.Client(api_key=get_env("GEMINI_API_KEY"))
    run(args.manifest, args.tier, args.domain, client)

if __name__ == "__main__":
    main()
