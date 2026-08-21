#!/usr/bin/env python3
"""
overnight_batch_renderer.py — Phase 9 — Video Rendering
"""
import json
import subprocess
import argparse
from pathlib import Path
from typing import Optional

from config import (
    log, console, safe_slug,
    MASTER_MANIFEST, VAULT_DIR, AUDIO_DIR, VIDEO_DIR, VAULT_STATE,
)

FINAL_OUT = VIDEO_DIR / "final"
_BATCH_STATE_FILE = VAULT_STATE.parent / "render_state.json"

def _load_render_state() -> dict:
    from pve.state import load_json_state
    return load_json_state(_BATCH_STATE_FILE, default_when_missing={"rendered_ids":[]})

def _save_render_state(state: dict) -> None:
    tmp = _BATCH_STATE_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state,indent=2), encoding="utf-8")
        tmp.replace(_BATCH_STATE_FILE)
    except OSError as e: log.error(f"[red]Render state save: {e}[/red]")
    finally: tmp.unlink(missing_ok=True)

def _find_audio(mod: dict) -> Optional[Path]:
    tier  = mod.get("tier","Junior")
    subj  = safe_slug(mod.get("subject","unknown"))
    title = safe_slug(mod.get("title",mod.get("id","module")))
    p     = AUDIO_DIR / tier / subj / f"{title}.mp3"
    return p if p.exists() and p.stat().st_size > 0 else None

def _find_vault(mod: dict) -> Optional[Path]:
    tier  = mod.get("tier","Junior")
    subj  = safe_slug(mod.get("subject","unknown"))
    title = safe_slug(mod.get("title",mod.get("id","module")))
    p     = VAULT_DIR / tier / subj / f"{title}.md"
    return p if p.exists() else None

def _ffmpeg_available() -> bool:
    try: subprocess.run(["ffmpeg","-version"],capture_output=True,timeout=5); return True
    except Exception: return False

def render_simple_video(mod: dict, audio_path: Optional[Path]) -> Optional[Path]:
    """Title-card video with audio (if available). Duration scales with suggested_chapters."""
    if not _ffmpeg_available():
        log.warning("  [yellow]ffmpeg not available — skipping video[/yellow]"); return None

    tier   = mod.get("tier","Junior")
    subj   = safe_slug(mod.get("subject","unknown"))
    title  = safe_slug(mod.get("title",mod.get("id","module")))
    FINAL_OUT.mkdir(parents=True, exist_ok=True)
    out    = FINAL_OUT / f"{tier}_{subj}_{title}.mp4"

    if out.exists() and out.stat().st_size > 0: return out

    title_text = mod.get("title","").replace("'","")[:60]
    # BUG FIX (found via adversarial testing): unguarded int() on a malformed
    # suggested_chapters value would raise ValueError and crash the entire
    # unattended overnight batch run over one bad manifest entry.
    try:    chapters = max(1, int(mod.get("suggested_chapters", 1)))
    except (TypeError, ValueError): chapters = 1
    duration   = 90 * chapters   # 90s per chapter as a baseline placeholder duration

    if audio_path and audio_path.exists():
        cmd = [
            "ffmpeg","-y",
            "-f","lavfi","-i",f"color=c=black:s=1280x720:d={duration}:r=24",
            "-i", str(audio_path),
            "-vf", f"drawtext=text='{title_text}':fontsize=40:fontcolor=white:"
                   "x=(w-text_w)/2:y=(h-text_h)/2",
            "-shortest","-c:v","libx264","-c:a","aac",str(out),"-loglevel","error"
        ]
    else:
        cmd = [
            "ffmpeg","-y",
            "-f","lavfi","-i",f"color=c=black:s=1280x720:d={duration}:r=24",
            "-vf", f"drawtext=text='{title_text}':fontsize=40:fontcolor=white:"
                   "x=(w-text_w)/2:y=(h-text_h)/2",
            "-c:v","libx264",str(out),"-loglevel","error"
        ]
    try:
        # ROBUSTNESS FIX: flat 300s timeout was tight for 3-chapter modules
        # (duration=270s) on slower machines. Scale with duration so the
        # margin stays comfortable regardless of chapter count.
        render_timeout = max(300, duration * 2)
        result = subprocess.run(cmd, capture_output=True, timeout=render_timeout)
        if result.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return out
        log.error(f"  [red]ffmpeg error: {result.stderr.decode()[:200]}[/red]")
    except subprocess.TimeoutExpired:
        log.error(f"  [red]ffmpeg timeout (>{render_timeout}s)[/red]")
    except Exception as e:
        log.error(f"  [red]Render error: {e}[/red]")
    return None

def run(manifest_path=MASTER_MANIFEST, tier_filter=None, limit=None, dry_run=False):
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, list): log.error("[red]Bad manifest[/red]"); return
    except Exception as e: log.error(f"[red]{e}[/red]"); return

    state    = _load_render_state()
    done_ids = set(state.get("rendered_ids",[]))

    target = [m for m in manifest
              if m.get("id") not in done_ids
              and (not tier_filter or m.get("tier")==tier_filter)]
    if limit: target = target[:limit]

    log.info(f"[bold]Phase 9: Batch Renderer[/bold] — {len(target)} modules")
    if dry_run:
        for mod in target: log.info(f"  [dim][DRY] {mod.get('title','?')}[/dim]")
        return

    ok = err = skip = 0
    for i, mod in enumerate(target):
        mid   = mod.get("id","?")
        title = mod.get("title","?")
        log.info(f"  [{i+1}/{len(target)}] {title}")

        vault = _find_vault(mod)
        if not vault:
            log.warning(f"  [yellow]No vault file — skipping {mid}[/yellow]"); skip += 1; continue

        audio = _find_audio(mod)
        if not audio:
            log.info(f"  [dim]No audio for {mid} — text-only video[/dim]")

        out = render_simple_video(mod, audio)
        if out:
            state["rendered_ids"].append(mid)
            _save_render_state(state)
            ok += 1
            log.info(f"  [green]✓[/green] {out.name}")
        else:
            err += 1

    from rich.panel import Panel
    console.print(Panel(
        f"Rendered: [green]{ok}[/green]  Failed: [red]{err}[/red]  Skipped: {skip}\n"
        f"Output: {FINAL_OUT}",
        title="Phase 9 Complete", border_style="green" if not err else "yellow"))

def main():
    p = argparse.ArgumentParser(prog="overnight_batch_renderer.py")
    p.add_argument("--manifest", type=Path, default=MASTER_MANIFEST)
    p.add_argument("--tier",     default=None)
    p.add_argument("--limit",    type=int, default=None)
    p.add_argument("--dry-run",  action="store_true")
    args = p.parse_args()
    run(args.manifest, args.tier, args.limit, args.dry_run)

if __name__ == "__main__":
    main()
