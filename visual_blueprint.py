#!/usr/bin/env python3
from __future__ import annotations

"""
visual_blueprint.py — Phase 3 — Mermaid Diagram Injection

For each synthesized vault markdown file, generates a Mermaid diagram via
Gemini and injects it into the file. Uses core_subtopics from the manifest
(when available) to give the diagram generator concrete structural content
instead of guessing from the title alone.
"""
import re
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
    log, console, get_env, safe_slug, safe_gemini_text,
    MASTER_MANIFEST, VAULT_DIR, GEMINI_FLASH_MODEL, MAX_RETRIES, calc_backoff,
)

_MERMAID_PROMPT = """\
Generate a Mermaid diagram for the topic below. Choose the most appropriate type:
- flowchart TD for processes/algorithms
- classDiagram for object relationships
- sequenceDiagram for step sequences
- stateDiagram-v2 for state machines
- graph LR for concept maps

Rules:
- Return ONLY the mermaid code block: ```mermaid ... ```
- Valid Mermaid syntax only — no explanation
- Max 20 nodes for readability
- Use the listed subtopics as the actual diagram nodes/stages where applicable

Topic: {title}
Subject: {subject}
Key subtopics: {subtopics}
Content summary: {summary}
"""

def _cfg(**kw) -> gt.GenerateContentConfig:
    return gt.GenerateContentConfig(
        thinking_config=gt.ThinkingConfig(thinking_budget=0),
        tool_config=gt.ToolConfig(function_calling_config=gt.FunctionCallingConfig(
            mode=gt.FunctionCallingMode.NONE)), **kw)

def _extract_mermaid(raw: str) -> Optional[str]:
    m = re.search(r'```mermaid\s*\n(.*?)\n```', raw, re.DOTALL)
    if m: return m.group(1).strip()
    for kw in ("flowchart","graph","sequenceDiagram","classDiagram","stateDiagram","pie"):
        if kw in raw:
            lines = raw.strip().splitlines()
            start = next((i for i, line in enumerate(lines) if kw in line), None)
            if start is not None: return "\n".join(lines[start:]).strip()
    return None

def generate_mermaid(title: str, subject: str, subtopics: list[str], content: str, client) -> Optional[str]:
    summary = content[:800].replace("\n"," ")
    subtopic_str = ", ".join(subtopics[:7]) if subtopics else "(none listed)"
    prompt = _MERMAID_PROMPT.format(title=title, subject=subject, subtopics=subtopic_str, summary=summary)
    for i in range(MAX_RETRIES):
        try:
            r    = client.models.generate_content(
                model=GEMINI_FLASH_MODEL, contents=prompt,
                config=_cfg(temperature=0.2, max_output_tokens=2048))
            raw  = safe_gemini_text(r)
            if not raw:
                if i >= MAX_RETRIES-1: return None
                time.sleep(calc_backoff(i)); continue
            diag = _extract_mermaid(raw)
            if diag: return diag
            log.warning(f"  [yellow]No mermaid block in response for {title}[/yellow]")
            if i >= MAX_RETRIES-1: return None
            time.sleep(calc_backoff(i))
        except Exception as e:
            if i >= MAX_RETRIES-1: log.error(f"  [red]Mermaid {title}: {e}[/red]"); return None
            time.sleep(calc_backoff(i))
    return None

def inject_diagram(vault_path: Path, diagram: str) -> bool:
    tmp = vault_path.with_suffix(".tmp")
    try:
        text  = vault_path.read_text(encoding="utf-8")
        block = f"\n\n## Diagram\n\n```mermaid\n{diagram}\n```\n"
        if "```mermaid" in text:
            text = re.sub(r'\n\n## Diagram\n\n```mermaid.*?```\n', block, text, flags=re.DOTALL)
        else:
            text = text + block
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(vault_path)
        return True
    except OSError as e:
        tmp.unlink(missing_ok=True)
        log.error(f"  [red]Inject diagram {vault_path}: {e}[/red]"); return False

def run(manifest_path=MASTER_MANIFEST, tier_filter=None, client=None, force=False):
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, list): log.error("[red]Bad manifest[/red]"); return
    except Exception as e: log.error(f"[red]{e}[/red]"); return

    target = manifest
    if tier_filter: target = [m for m in manifest if m.get("tier")==tier_filter]

    log.info(f"[bold]Phase 3: Visual Blueprint[/bold] — {len(target)} modules")
    ok = err = skip = cached = 0
    for i, mod in enumerate(target):
        tier   = mod.get("tier","Junior")
        subj   = safe_slug(mod.get("subject","unknown"))
        title  = safe_slug(mod.get("title",mod.get("id","module")))
        vpath  = VAULT_DIR / tier / subj / f"{title}.md"
        if not vpath.exists():
            skip += 1; continue
        try:
            content = vpath.read_text(encoding="utf-8")
        except OSError:
            skip += 1; continue

        # API-BLEED FIX: skip modules already diagrammed unless --force is given.
        # Without this check, every re-run of Phase 3 re-calls Gemini for ALL
        # modules, including ones that already have a mermaid block injected.
        if not force and "```mermaid" in content:
            cached += 1; continue

        log.info(f"  [{i+1}/{len(target)}] {mod.get('title','?')}")
        diag = generate_mermaid(
            mod.get("title",""), mod.get("subject",""),
            mod.get("core_subtopics",[]), content, client,
        )
        if diag:
            if inject_diagram(vpath, diag): ok += 1
            else: err += 1
        else:
            log.warning(f"  [yellow]No diagram for {mod.get('title')}[/yellow]"); err += 1

    from rich.panel import Panel
    console.print(Panel(
        f"Diagrams: [green]{ok}[/green]  Failed: [red]{err}[/red]  "
        f"Skipped (no vault file): {skip}  Already diagrammed (cached): [cyan]{cached}[/cyan]",
        title="Phase 3 Complete", border_style="green" if not err else "yellow"))

def main():
    from pve.safety import require_legacy_paid_execution
    require_legacy_paid_execution()
    p = argparse.ArgumentParser(prog="visual_blueprint.py")
    p.add_argument("--manifest", type=Path, default=MASTER_MANIFEST)
    p.add_argument("--tier",     default=None)
    p.add_argument("--force",    action="store_true",
                   help="Regenerate diagrams even for modules that already have one (costs extra Gemini calls)")
    args   = p.parse_args()
    client = google_genai.Client(api_key=get_env("GEMINI_API_KEY"))
    run(args.manifest, args.tier, client, force=args.force)

if __name__ == "__main__":
    main()
