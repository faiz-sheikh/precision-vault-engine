#!/usr/bin/env python3
"""
viva_command_center.py — Phase 8 — Viva Q&A Bank + Interactive Mode

Uses core_subtopics to anchor viva questions to actual syllabus content
rather than generic title-derived questions.
"""
import json
import time
import argparse
from pathlib import Path
from typing import Optional

try:
    import anthropic as ant
except ImportError:
    import sys; sys.exit("pip install anthropic")
try:
    from google import genai as google_genai
    from google.genai import types as gt
    _GENAI_OK = True
except ImportError:
    _GENAI_OK = False

from config import (
    log, console, get_env, safe_slug, safe_gemini_text, robust_json_parse,
    MASTER_MANIFEST, VAULT_DIR, GEMINI_FLASH_MODEL,
    CLAUDE_HAIKU, CLAUDE_SONNET, MAX_RETRIES, calc_backoff,
)

VIVA_DIR = VAULT_DIR.parent / "_assessment" / "viva"

_VIVA_SYS = """You are an experienced B.Tech engineering viva examiner.
Generate a comprehensive viva Q&A bank for the given topic.
Return ONLY a JSON array. No markdown. No preamble.
Schema: [{"question":"...","answer":"...","type":"conceptual|derivation|application|numerical",
          "difficulty":"Easy|Medium|Hard","marks":2,"keywords":["..."]}]"""

_EVAL_SYS = """You are a B.Tech viva examiner evaluating a student answer.
Score the answer out of 10 and give brief feedback.
Return JSON: {"score":7,"feedback":"...","missing_points":["..."],"grade":"Excellent|Good|Average|Poor"}"""

def _claude_call(prompt: str, system: str, model: str, client, max_tokens: int=4096) -> Optional[str]:
    for i in range(MAX_RETRIES):
        try:
            r = client.messages.create(
                model=model, max_tokens=max_tokens,
                system=system,
                messages=[{"role":"user","content":prompt}])
            if r.content and hasattr(r.content[0],"text"): return r.content[0].text
            return None
        except ant.RateLimitError:
            time.sleep(min(60*(i+1),300))
        except ant.APIError as e:
            if i >= MAX_RETRIES-1: log.error(f"  [red]Claude: {e}[/red]"); return None
            time.sleep(calc_backoff(i))
        except Exception as e:
            if i >= MAX_RETRIES-1: log.error(f"  [red]{e}[/red]"); return None
            time.sleep(calc_backoff(i))
    return None

def _gemini_call(prompt: str, client) -> Optional[str]:
    if not _GENAI_OK: return None
    for i in range(MAX_RETRIES):
        try:
            r = client.models.generate_content(
                model=GEMINI_FLASH_MODEL, contents=prompt,
                config=gt.GenerateContentConfig(
                    thinking_config=gt.ThinkingConfig(thinking_budget=0),
                    tool_config=gt.ToolConfig(function_calling_config=gt.FunctionCallingConfig(
                        mode=gt.FunctionCallingMode.NONE)),
                    temperature=0.3, max_output_tokens=8192))
            t = safe_gemini_text(r)
            if t: return t
            if i >= MAX_RETRIES-1: return None
            time.sleep(calc_backoff(i))
        except Exception:
            if i >= MAX_RETRIES-1: return None
            time.sleep(calc_backoff(i))
    return None

def _vault_content(mod: dict) -> str:
    tier  = mod.get("tier","Junior")
    subj  = safe_slug(mod.get("subject","unknown"))
    title = safe_slug(mod.get("title",mod.get("id","module")))
    p     = VAULT_DIR / tier / subj / f"{title}.md"
    if not p.exists(): return ""
    try: return p.read_text(encoding="utf-8")[:5000]
    except OSError: return ""

def generate_viva_bank(mod: dict, aclient, gclient=None) -> Optional[list]:
    content   = _vault_content(mod)
    subtopics = mod.get("core_subtopics",[])
    subtopic_blk = ""
    if isinstance(subtopics, list) and subtopics:
        subtopic_blk = "\nSyllabus subtopics:\n" + "\n".join(f"- {s}" for s in subtopics)
    prompt = (f"{_VIVA_SYS}\n\nTopic: {mod.get('title','')}\n"
              f"Subject: {mod.get('subject','')}\nDomain: {mod.get('domain','')}\n"
              f"Tier: {mod.get('tier','')}{subtopic_blk}\n\nContent:\n{content}\n\n"
              "Generate 15-20 viva questions covering all difficulty levels and subtopics listed.")
    tier  = mod.get("tier","Junior")
    model = CLAUDE_SONNET if tier == "Engineering" else CLAUDE_HAIKU

    raw = _claude_call(prompt, _VIVA_SYS, model, aclient, max_tokens=8192) if aclient else None
    if not raw and gclient:
        raw = _gemini_call(f"{_VIVA_SYS}\n\n{prompt}", gclient)
    if not raw: return None
    p = robust_json_parse(raw)
    return [q for q in p if isinstance(q,dict) and q.get("question")] if isinstance(p,list) else None

def evaluate_answer(question: str, student_ans: str, correct_ans: str,
                    gclient, aclient=None) -> dict:
    prompt = (f"Question: {question}\nCorrect answer: {correct_ans}\n"
              f"Student answer: {student_ans}\n\nEvaluate and score.")
    raw = None
    if gclient: raw = _gemini_call(f"{_EVAL_SYS}\n\n{prompt}", gclient)
    if not raw and aclient:
        raw = _claude_call(prompt, _EVAL_SYS, CLAUDE_HAIKU, aclient, max_tokens=512)
    if not raw: return {"score":5,"feedback":"Could not evaluate","grade":"Average","missing_points":[]}
    p = robust_json_parse(raw)
    if isinstance(p,dict): return p
    return {"score":5,"feedback":raw[:200],"grade":"Average","missing_points":[]}

def run_mode_a(manifest: list[dict], aclient, gclient=None):
    log.info(f"[bold]Phase 8: Viva Bank Generation[/bold] — {len(manifest)} modules")
    VIVA_DIR.mkdir(parents=True, exist_ok=True)
    ok = err = 0
    for i, mod in enumerate(manifest):
        mid  = mod.get("id","?")
        slug = safe_slug(f"{mod.get('tier','')[:3]}_{mid[:30]}")
        out  = VIVA_DIR / f"viva_{slug}.json"
        if out.exists() and out.stat().st_size > 0:
            log.info(f"  [green]CACHED[/green] {mid}"); ok += 1; continue
        log.info(f"  [{i+1}/{len(manifest)}] {mod.get('title',mid)}")
        bank = generate_viva_bank(mod, aclient, gclient)
        if not bank: log.error(f"  [red]Failed: {mid}[/red]"); err += 1; continue
        payload = {"_meta":{"module_id":mid,"title":mod.get("title",""),
                            "tier":mod.get("tier","")}, "questions":bank}
        tmp = out.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload,indent=2,ensure_ascii=False), encoding="utf-8")
            tmp.replace(out)
            log.info(f"  [green]✓[/green] {len(bank)} questions"); ok += 1
        except OSError as e:
            tmp.unlink(missing_ok=True); log.error(f"  [red]{e}[/red]"); err += 1

    from rich.panel import Panel
    console.print(Panel(f"Generated: [green]{ok}[/green]  Failed: [red]{err}[/red]",
        title="Phase 8 Complete", border_style="green"))

def run_interactive(module_id: str, manifest: list[dict], aclient, gclient=None):
    mod  = next((m for m in manifest if m.get("id")==module_id), None)
    if not mod: log.error(f"[red]Module {module_id} not found[/red]"); return

    slug = safe_slug(f"{mod.get('tier','')[:3]}_{module_id[:30]}")
    vf   = VIVA_DIR / f"viva_{slug}.json"
    if vf.exists():
        try:
            data  = json.loads(vf.read_text(encoding="utf-8"))
            banks = data.get("questions",[])
        except Exception: banks = []
    else:
        log.info("[cyan]Generating viva bank...[/cyan]")
        banks = generate_viva_bank(mod, aclient, gclient) or []

    if not banks: log.error("[red]No questions available[/red]"); return

    console.print(f"\n[bold cyan]Viva: {mod.get('title','')}[/bold cyan]")
    console.print(f"[dim]{len(banks)} questions | type 'quit' to exit[/dim]\n")

    score_total = attempts = 0
    for q in banks[:10]:
        question = q.get("question","")
        correct  = q.get("answer","")
        console.print(f"[bold yellow]Q: {question}[/bold yellow]")
        try: ans = input("Your answer: ").strip()
        except (EOFError, KeyboardInterrupt): break
        if ans.lower() == "quit": break
        if not ans: continue
        result = evaluate_answer(question, ans, correct, gclient, aclient)
        score_total += result.get("score",0)
        attempts    += 1
        grade = result.get("grade","?")
        color = {"Excellent":"green","Good":"cyan","Average":"yellow","Poor":"red"}.get(grade,"white")
        console.print(f"[{color}]Score: {result.get('score',0)}/10 — {grade}[/{color}]")
        console.print(f"[dim]Feedback: {result.get('feedback','')}[/dim]\n")

    if attempts:
        avg = score_total / attempts
        console.print(f"\n[bold]Session: {score_total}/{attempts*10} ({avg:.1f}/10 avg)[/bold]")

def main():
    from pve.safety import require_legacy_paid_execution
    require_legacy_paid_execution()
    p = argparse.ArgumentParser(prog="viva_command_center.py")
    p.add_argument("--manifest",    type=Path, default=MASTER_MANIFEST)
    p.add_argument("--generate",    action="store_true", help="Generate all viva banks")
    p.add_argument("--interactive", metavar="MODULE_ID", help="Start interactive viva session")
    p.add_argument("--tier",        default=None)
    args = p.parse_args()

    aclient  = gclient = None
    try:    aclient = ant.Anthropic(api_key=get_env("ANTHROPIC_API_KEY"))
    except Exception as e: log.warning(f"[yellow]Anthropic: {e}[/yellow]")
    if _GENAI_OK:
        try: gclient = google_genai.Client(api_key=get_env("GEMINI_API_KEY"))
        except Exception: pass

    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest, list): log.error("[red]Bad manifest[/red]"); return
    except Exception as e: log.error(f"[red]{e}[/red]"); return

    if args.tier: manifest = [m for m in manifest if m.get("tier")==args.tier]

    if args.interactive: run_interactive(args.interactive, manifest, aclient, gclient)
    elif args.generate:  run_mode_a(manifest, aclient, gclient)
    else:                run_mode_a(manifest, aclient, gclient)

if __name__ == "__main__":
    main()
