#!/usr/bin/env python3
"""
windows_bridge.py — Native dual-boot file bridge.

Sweeps configured Windows drop zones (mounted under /mnt/<drive>/...) for new
study material and copies it into raw_notes/ for dedup_engine.py to process.

COMMANDS:
  python3 windows_bridge.py check-mount          # Verify Windows partition is mounted
  python3 windows_bridge.py status                # Show inventory of all drop zones
  python3 windows_bridge.py sweep                  # One-shot copy: drop zones -> raw_notes/
  python3 windows_bridge.py watch                  # Poll continuously (Ctrl+C to stop)
  python3 windows_bridge.py watch --auto-exit      # Poll until all zones are empty, then exit
  python3 windows_bridge.py convert "C:\\path"     # Convert a Windows path to its Linux mount path
"""
import re
import sys
import time
import shutil
import argparse
from pathlib import Path

from config import (
    log, console, WINDOWS_DROP_ZONES, WINDOWS_MOUNT_BASE,
    ALLOWED_BRIDGE_EXTENSIONS, BRIDGE_POLL_INTERVAL_SEC,
    RAW_NOTES_DIR, wsl_to_linux_path,
)


def _resolve_drop_zone(raw_path: str) -> Path:
    """
    Convert a configured Windows-style drop zone path into the actual Linux
    mount path. Tries WINDOWS_MOUNT_BASE override first, then standard
    /mnt/<drive_letter>/ convention.
    """
    if WINDOWS_MOUNT_BASE:
        # Strip the drive letter prefix (e.g. "C:\") and join under the override base
        m = re.match(r'^[A-Za-z]:[\\\/](.*)$', raw_path)
        rest = m.group(1).replace('\\', '/') if m else raw_path.replace('\\', '/')
        return Path(WINDOWS_MOUNT_BASE) / rest
    return wsl_to_linux_path(raw_path)


def check_mount() -> bool:
    """Verify at least one configured drop zone resolves to an existing, mounted path."""
    any_found = False
    for zone in WINDOWS_DROP_ZONES:
        try:
            p = _resolve_drop_zone(zone)
        except ValueError as e:
            console.print(f"  [red]config error[/red]  {zone}  ->  {e}")
            continue
        exists = p.exists()
        status = "[green]mounted[/green]" if exists else "[red]not found[/red]"
        console.print(f"  {status}  {zone}  ->  {p}")
        any_found = any_found or exists
    if not any_found:
        log.error("[red]No configured drop zones are mounted. Check WINDOWS_MOUNT_BASE in .env "
                   "or verify the Windows partition is mounted (e.g. `sudo mount /dev/sdaX /mnt/windows`).[/red]")
    else:
        log.info("[green]At least one drop zone is accessible.[/green]")
    return any_found


def _scan_zone(zone_path: Path) -> list[Path]:
    if not zone_path.exists() or not zone_path.is_dir():
        return []
    found = []
    try:
        for f in zone_path.rglob("*"):
            if f.is_file() and f.suffix.lower() in ALLOWED_BRIDGE_EXTENSIONS:
                found.append(f)
    except (PermissionError, OSError) as e:
        log.warning(f"  [yellow]Cannot scan {zone_path}: {e}[/yellow]")
    return found


def status() -> dict[str, int]:
    """Show inventory of all drop zones without copying anything."""
    counts: dict[str, int] = {}
    log.info("[bold]Drop Zone Inventory[/bold]")
    for zone in WINDOWS_DROP_ZONES:
        try:
            p = _resolve_drop_zone(zone)
        except ValueError as e:
            log.warning(f"  [yellow]Skipping misconfigured zone '{zone}': {e}[/yellow]")
            counts[zone] = 0
            continue
        files = _scan_zone(p)
        counts[zone] = len(files)
        color = "green" if files else "dim"
        console.print(f"  [{color}]{len(files):4} files[/{color}]  {zone}")
        for f in files[:5]:
            console.print(f"        [dim]{f.name}[/dim]")
        if len(files) > 5:
            console.print(f"        [dim]... and {len(files)-5} more[/dim]")
    total = sum(counts.values())
    log.info(f"[bold]Total pending files: {total}[/bold]")
    return counts


def sweep() -> int:
    """One-shot copy: every allowed file in every drop zone -> raw_notes/. Returns count copied."""
    RAW_NOTES_DIR.mkdir(parents=True, exist_ok=True)
    total_copied = 0
    for zone in WINDOWS_DROP_ZONES:
        try:
            zone_path = _resolve_drop_zone(zone)
        except ValueError as e:
            log.warning(f"  [yellow]Skipping misconfigured zone '{zone}': {e}[/yellow]")
            continue
        files = _scan_zone(zone_path)
        if not files:
            continue
        log.info(f"[cyan]Sweeping:[/cyan] {zone} ({len(files)} files)")
        for f in files:
            dest = RAW_NOTES_DIR / f.name
            # Avoid overwrite collisions across multiple drop zones
            if dest.exists():
                stem, suffix = dest.stem, dest.suffix
                counter = 1
                while dest.exists():
                    dest = RAW_NOTES_DIR / f"{stem}_{counter}{suffix}"
                    counter += 1
            try:
                shutil.copy2(str(f), str(dest))
                total_copied += 1
                log.info(f"  [green]✓[/green] {f.name}")
            except (OSError, shutil.Error) as e:
                log.error(f"  [red]Copy failed {f.name}: {e}[/red]")
    log.info(f"[bold green]Sweep complete — {total_copied} files copied to {RAW_NOTES_DIR}[/bold green]")
    return total_copied


def watch(auto_exit: bool = False, poll_interval: int = BRIDGE_POLL_INTERVAL_SEC) -> None:
    """Continuously poll drop zones. With --auto-exit, stops once a full pass finds nothing to copy."""
    log.info(f"[bold]Watching {len(WINDOWS_DROP_ZONES)} drop zones every {poll_interval}s[/bold] "
              f"({'auto-exit on empty' if auto_exit else 'Ctrl+C to stop'})")
    empty_streak = 0
    try:
        while True:
            copied = sweep()
            if copied == 0:
                empty_streak += 1
                if auto_exit and empty_streak >= 2:
                    log.info("[green]Two consecutive empty passes — exiting.[/green]")
                    break
            else:
                empty_streak = 0
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        log.info("\n[yellow]Watch stopped by user.[/yellow]")


def convert(raw_path: str) -> Path:
    """Convert a Windows-style path string to its resolved Linux path."""
    resolved = _resolve_drop_zone(raw_path) if re.match(r'^[A-Za-z]:', raw_path) else wsl_to_linux_path(raw_path)
    console.print(f"  {raw_path}  ->  {resolved}")
    console.print(f"  exists: {'[green]yes[/green]' if resolved.exists() else '[red]no[/red]'}")
    return resolved


def main() -> None:
    p = argparse.ArgumentParser(
        prog="windows_bridge.py",
        description="Bridge files from native Windows dual-boot drop zones into raw_notes/",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check-mount", help="Verify Windows partition is mounted")
    sub.add_parser("status",      help="Show drop zone file inventory")
    sub.add_parser("sweep",       help="One-shot copy: drop zones -> raw_notes/")

    watch_p = sub.add_parser("watch", help="Poll continuously")
    watch_p.add_argument("--auto-exit", action="store_true",
                          help="Exit automatically after 2 consecutive empty passes")
    watch_p.add_argument("--interval", type=int, default=BRIDGE_POLL_INTERVAL_SEC,
                          help=f"Poll interval in seconds (default {BRIDGE_POLL_INTERVAL_SEC})")

    convert_p = sub.add_parser("convert", help="Convert a Windows path to its Linux mount path")
    convert_p.add_argument("path", type=str)

    args = p.parse_args()

    if args.cmd == "check-mount":
        ok = check_mount()
        sys.exit(0 if ok else 1)
    elif args.cmd == "status":
        status()
    elif args.cmd == "sweep":
        sweep()
    elif args.cmd == "watch":
        watch(auto_exit=args.auto_exit, poll_interval=args.interval)
    elif args.cmd == "convert":
        convert(args.path)


if __name__ == "__main__":
    main()
