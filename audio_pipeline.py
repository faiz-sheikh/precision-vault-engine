#!/usr/bin/env python3
"""
audio_pipeline.py — Phase 4 — Dialogue Script + edge-tts Audio
"""
import re
import json
import time
import asyncio
import subprocess
import argparse
from pathlib import Path
from typing import Optional

from google import genai as google_genai
from google.genai import types as gt

from config import (
    log, console, get_env, safe_slug, safe_gemini_text,
    MASTER_MANIFEST, VAULT_DIR, AUDIO_DIR,
    GEMINI_FLASH_MODEL, MAX_RETRIES, calc_backoff,
)

VOICES = {"host": "en-IN-NeerjaNeural", "expert": "en-IN-PrabhatNeural"}

_SCRIPT_PROMPT = """\
Write a 3-minute educational podcast dialogue between HOST and EXPERT about this topic.
FORMAT:
HOST: [engaging question or intro]
EXPERT: [clear explanation]
HOST: [follow-up or summary]
EXPERT: [key exam points]
...

Keep it exam-focused, natural, and under 400 words total.
Topic: {title}
Subject: {subject}
Key content: {content}
"""

def _cfg(**kw) -> gt.GenerateContentConfig:
    return gt.GenerateContentConfig(
        thinking_config=gt.ThinkingConfig(thinking_budget=0),
        tool_config=gt.ToolConfig(function_calling_config=gt.FunctionCallingConfig(
            mode=gt.FunctionCallingMode.NONE)), **kw)

def generate_script(title: str, subject: str, content: str, client) -> Optional[str]:
    prompt = _SCRIPT_PROMPT.format(title=title, subject=subject, content=content[:1500])
    for i in range(MAX_RETRIES):
        try:
            r    = client.models.generate_content(
                model=GEMINI_FLASH_MODEL, contents=prompt,
                config=_cfg(temperature=0.6, max_output_tokens=4096))
            text = safe_gemini_text(r)
            if text and ("HOST:" in text or "EXPERT:" in text): return text
            if i >= MAX_RETRIES-1: return None
            time.sleep(calc_backoff(i))
        except Exception as e:
            if i >= MAX_RETRIES-1: log.error(f"  [red]Script gen {title}: {e}[/red]"); return None
            time.sleep(calc_backoff(i))
    return None

def parse_script(script: str) -> list[tuple[str,str]]:
    lines   = []
    pattern = re.compile(r'^(HOST|EXPERT)\s*:\s*(.+)$', re.IGNORECASE)
    for line in script.strip().splitlines():
        m = pattern.match(line.strip())
        if m: lines.append((m.group(1).upper(), m.group(2).strip()))
    return lines

async def _tts_line(text: str, voice: str, out: Path) -> bool:
    try:
        import edge_tts
        tts  = edge_tts.Communicate(text, voice)
        await tts.save(str(out))
        return out.exists() and out.stat().st_size > 0
    except ImportError:
        log.error("[red]pip install edge-tts[/red]"); return False
    except Exception as e:
        log.error(f"  [red]TTS: {e}[/red]"); return False

async def generate_audio_async(lines: list[tuple[str,str]], out_dir: Path, stem: str) -> list[Path]:
    parts = []
    for i, (speaker, text) in enumerate(lines):
        voice = VOICES["host"] if speaker == "HOST" else VOICES["expert"]
        part  = out_dir / f"{stem}_part{i:03d}.mp3"
        ok    = await _tts_line(text, voice, part)
        if ok: parts.append(part)
    return parts

def _ffmpeg_available() -> bool:
    try: subprocess.run(["ffmpeg","-version"],capture_output=True,timeout=5); return True
    except Exception: return False

def merge_audio(parts: list[Path], out_path: Path) -> bool:
    if not parts: return False
    if not _ffmpeg_available():
        import shutil
        shutil.copy2(str(parts[0]), str(out_path)); return True
    concat_file = out_path.parent / f"{out_path.stem}_concat.txt"
    try:
        concat_file.write_text("\n".join(f"file '{p.resolve()}'" for p in parts), encoding="utf-8")
        result = subprocess.run(
            ["ffmpeg","-f","concat","-safe","0","-i",str(concat_file),
             "-c","copy",str(out_path),"-y"],
            capture_output=True, timeout=120)
        concat_file.unlink(missing_ok=True)
        if result.returncode != 0:
            log.error(f"  [red]ffmpeg: {result.stderr.decode()[:200]}[/red]"); return False
        for p in parts: p.unlink(missing_ok=True)
        return True
    except Exception as e:
        concat_file.unlink(missing_ok=True); log.error(f"  [red]Merge: {e}[/red]"); return False

def process_module(mod: dict, client) -> Optional[Path]:
    tier  = mod.get("tier","Junior")
    subj  = safe_slug(mod.get("subject","unknown"))
    title = safe_slug(mod.get("title",mod.get("id","module")))
    audio_dir = AUDIO_DIR / tier / subj
    out_path  = audio_dir / f"{title}.mp3"
    if out_path.exists() and out_path.stat().st_size > 0:
        log.info(f"  [green]CACHED[/green] {out_path.name}"); return out_path

    vault_path = VAULT_DIR / tier / subj / f"{title}.md"
    content    = ""
    if vault_path.exists():
        try: content = vault_path.read_text(encoding="utf-8")[:2000]
        except OSError: pass

    script = generate_script(mod.get("title",""), mod.get("subject",""), content, client)
    if not script: return None

    lines = parse_script(script)
    if not lines: return None

    audio_dir.mkdir(parents=True, exist_ok=True)
    parts = asyncio.run(generate_audio_async(lines, audio_dir, title))
    if not parts: return None

    if len(parts) == 1:
        import shutil; shutil.move(str(parts[0]), str(out_path))
    else:
        if not merge_audio(parts, out_path): return None

    log.info(f"  [green]✓[/green] {out_path.name}")
    return out_path

def run(manifest_path=MASTER_MANIFEST, tier_filter=None, client=None):
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, list): log.error("[red]Bad manifest[/red]"); return
    except Exception as e: log.error(f"[red]{e}[/red]"); return

    target = [m for m in manifest if not tier_filter or m.get("tier")==tier_filter]
    log.info(f"[bold]Phase 4: Audio Pipeline[/bold] — {len(target)} modules")
    ok = err = 0
    for i, mod in enumerate(target):
        log.info(f"  [{i+1}/{len(target)}] {mod.get('title','?')}")
        p = process_module(mod, client)
        if p: ok += 1
        else: err += 1
    from rich.panel import Panel
    console.print(Panel(f"Audio: [green]{ok}[/green]  Failed: [red]{err}[/red]",
        title="Phase 4 Complete", border_style="green"))

def main():
    from pve.safety import require_legacy_paid_execution
    require_legacy_paid_execution()
    p = argparse.ArgumentParser(prog="audio_pipeline.py")
    p.add_argument("--manifest", type=Path, default=MASTER_MANIFEST)
    p.add_argument("--tier",     default=None)
    args   = p.parse_args()
    client = google_genai.Client(api_key=get_env("GEMINI_API_KEY"))
    run(args.manifest, args.tier, client)

if __name__ == "__main__":
    main()
