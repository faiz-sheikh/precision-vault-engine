#!/usr/bin/env python3
"""
domain_compiler.py — Phase 5 — Compile domain-grouped markdown references
"""
import re
import json
import argparse
from pathlib import Path

from config import (
    log, console, safe_slug, MASTER_MANIFEST, VAULT_DIR,
    DOMAIN_DISPLAY_NAMES,
)

COMPILED_DIR = VAULT_DIR.parent / "_compiled"

def _demote_heading(text: str, levels: int=1) -> str:
    def bump(m):
        return '#' * min(len(m.group(1)) + levels, 6) + m.group(2)
    return re.sub(r'^(#{1,6})([ \t].*)$', bump, text, flags=re.MULTILINE)

def compile_domain(domain: str, modules: list[dict]) -> str:
    display = DOMAIN_DISPLAY_NAMES.get(domain, domain)
    lines   = [f"# {display}\n\n*{len(modules)} modules*\n"]
    for mod in sorted(modules, key=lambda m: (-m.get("priority",3), m.get("grade_or_sem",""), m.get("title",""))):
        tier   = mod.get("tier","Junior")
        subj   = safe_slug(mod.get("subject","unknown"))
        title  = safe_slug(mod.get("title",mod.get("id","module")))
        vpath  = VAULT_DIR / tier / subj / f"{title}.md"
        if not vpath.exists(): continue
        try:
            content = vpath.read_text(encoding="utf-8")
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1: content = content[end+3:].lstrip()
            lines.append(f"\n---\n\n## {mod.get('title','')} *(Sem/Class {mod.get('grade_or_sem','')} — {tier})*\n")
            lines.append(_demote_heading(content, levels=2))
        except OSError: continue
    return "\n".join(lines)

def run(manifest_path=MASTER_MANIFEST):
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, list): log.error("[red]Bad manifest[/red]"); return
    except Exception as e: log.error(f"[red]{e}[/red]"); return

    COMPILED_DIR.mkdir(parents=True, exist_ok=True)
    by_domain: dict[str, list[dict]] = {}
    for mod in manifest:
        d = mod.get("domain","00_Uncategorised")
        by_domain.setdefault(d,[]).append(mod)

    log.info(f"[bold]Phase 5: Domain Compiler[/bold] — {len(by_domain)} domains")
    ok = 0
    for domain, mods in sorted(by_domain.items()):
        text = compile_domain(domain, mods)
        out  = COMPILED_DIR / f"{safe_slug(domain)}.md"
        tmp  = out.with_suffix(".tmp")
        try:
            tmp.write_text(text, encoding="utf-8"); tmp.replace(out)
            log.info(f"  [green]✓[/green] {out.name} ({len(mods)} modules)")
            ok += 1
        except OSError as e:
            tmp.unlink(missing_ok=True); log.error(f"  [red]{domain}: {e}[/red]")

    from rich.panel import Panel
    console.print(Panel(f"Compiled: [green]{ok}[/green] domain files -> {COMPILED_DIR}",
        title="Phase 5 Complete", border_style="green"))

def main():
    p = argparse.ArgumentParser(prog="domain_compiler.py")
    p.add_argument("--manifest", type=Path, default=MASTER_MANIFEST)
    args = p.parse_args()
    run(args.manifest)

if __name__ == "__main__":
    main()
