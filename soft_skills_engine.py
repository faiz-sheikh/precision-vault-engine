#!/usr/bin/env python3
from __future__ import annotations

"""
soft_skills_engine.py — Soft Skills & Communication Content Generation
Standalone, runs once. No manifest dependency.
"""
import json
import time
import argparse
from pathlib import Path
from typing import Optional

try:
    from google import genai as google_genai
    from google.genai import types as gt
except ImportError:
    google_genai = None
    gt = None

from config import (
    log, console, get_env, safe_gemini_text, robust_json_parse,
    VAULT_DIR, GEMINI_FLASH_MODEL, MAX_RETRIES, calc_backoff,
)

SS_DIR = VAULT_DIR.parent / "_assessment" / "soft_skills"

_SS_SYS = """\
You are an expert in professional communication and soft skills for engineering students.
Generate practical exercises and scenarios.
Return ONLY a JSON array. No markdown. No preamble.
"""

_SCENARIOS = {
    "communication": "Generate 5 professional email writing scenarios for engineering students. "
                     'Schema: [{"scenario":"...","task":"...","sample_email":"...","tips":["..."]}]',
    "presentation":  "Generate 5 technical presentation structure exercises. "
                     'Schema: [{"topic":"...","structure":["intro","body","conclusion"],"tips":["..."]}]',
    "interview":     "Generate 10 HR interview Q&As for B.Tech engineering students. "
                     'Schema: [{"question":"...","good_answer":"...","keywords":["..."]}]',
    "group_disc":    "Generate 3 group discussion topics with arguments for/against. "
                     'Schema: [{"topic":"...","for":["..."],"against":["..."],"neutral_points":["..."]}]',
}

def _cfg(**kw) -> gt.GenerateContentConfig:
    return gt.GenerateContentConfig(
        thinking_config=gt.ThinkingConfig(thinking_budget=0),
        tool_config=gt.ToolConfig(function_calling_config=gt.FunctionCallingConfig(
            mode=gt.FunctionCallingMode.NONE)), **kw)

def _generate(prompt: str, client) -> Optional[str]:
    for i in range(MAX_RETRIES):
        try:
            r    = client.models.generate_content(
                model=GEMINI_FLASH_MODEL,
                contents=f"{_SS_SYS}\n\n{prompt}",
                config=_cfg(temperature=0.5, max_output_tokens=8192))
            text = safe_gemini_text(r)
            if text: return text
            if i >= MAX_RETRIES-1: return None
            time.sleep(calc_backoff(i))
        except Exception as e:
            if i >= MAX_RETRIES-1: log.error(f"  [red]SS gen: {e}[/red]"); return None
            time.sleep(calc_backoff(i))
    return None

def run(client):
    SS_DIR.mkdir(parents=True, exist_ok=True)
    log.info("[bold]Soft Skills Engine[/bold]")
    ok = err = 0
    for skill, prompt in _SCENARIOS.items():
        out = SS_DIR / f"{skill}.json"
        if out.exists() and out.stat().st_size > 0:
            log.info(f"  [green]CACHED[/green] {skill}"); ok += 1; continue
        log.info(f"  Generating: {skill}")
        raw = _generate(prompt, client)
        if not raw: err += 1; continue
        parsed = robust_json_parse(raw)
        if not parsed: err += 1; continue
        tmp = out.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"skill":skill,"content":parsed},indent=2,ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(out)
            log.info(f"  [green]✓[/green] {out.name}"); ok += 1
        except OSError as e:
            tmp.unlink(missing_ok=True); log.error(f"  [red]{e}[/red]"); err += 1

    from rich.panel import Panel
    console.print(Panel(f"Done: [green]{ok}[/green]  Failed: [red]{err}[/red]",
        title="Soft Skills Complete", border_style="green"))

def main():
    from pve.safety import require_legacy_paid_execution
    require_legacy_paid_execution()
    p = argparse.ArgumentParser(prog="soft_skills_engine.py")
    p.parse_args()
    client = google_genai.Client(api_key=get_env("GEMINI_API_KEY"))
    run(client)

if __name__ == "__main__":
    main()
