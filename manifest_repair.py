#!/usr/bin/env python3
"""
manifest_repair.py — One-time repair script for existing master_manifest.json

Fixes WITHOUT re-running curriculum_mapper:
  1. Semantic duplicates — "& " vs "and" variations generating separate entries
     (e.g. "Advanced sensors & Actuators" + "Advanced sensors and Actuators" → keep one)
  2. Vehicle Dynamics → 07_Control (was 00_Uncategorised)
  3. Ensures core_subtopics + suggested_chapters present on all entries (default []/1)
  4. engine=manim override for 12_NaturalSciences Junior entries (Gemini chose wrong)

Run once:
  python3 manifest_repair.py
  python3 manifest_repair.py --manifest /path/to/manifest.json
  python3 manifest_repair.py --dry-run   # show changes without writing
"""
import re
import json
import hashlib
import argparse
import shutil
from pathlib import Path
from datetime import datetime

try:
    from config import MASTER_MANIFEST
except Exception:
    MASTER_MANIFEST = Path("config/master_manifest.json")


# ── Title normalizer (must match curriculum_mapper.py) ─────────────────────────
def normalize_title(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r'\b(and|or|of|in|for|with|&|the|a|an)\b', '', t)
    t = re.sub(r'[^a-z0-9]', '', t)
    return t


# ── Domain corrections for misclassified entries ───────────────────────────────
# (title_lower_substring → correct_domain)
_DOMAIN_FIXES: list[tuple[str, str]] = [
    ("vehicle dynamics",     "07_Control"),
    ("automobile dynamics",  "07_Control"),
    ("flight dynamics",      "07_Control"),
    ("spacecraft dynamics",  "07_Control"),
]


def repair_manifest(manifest_path: Path, dry_run: bool = False) -> dict:
    """Load, repair, and save the manifest. Returns a stats dict."""

    if not manifest_path.exists():
        print(f"ERROR: {manifest_path} not found.")
        return {}

    # Load
    try:
        manifest: list[dict] = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR: Cannot read manifest — {e}")
        return {}

    if not isinstance(manifest, list):
        print("ERROR: Manifest is not a JSON array.")
        return {}

    print(f"Loaded {len(manifest)} entries from {manifest_path}")

    # Backup before modifying
    if not dry_run:
        backup = manifest_path.with_suffix(
            f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        shutil.copy2(str(manifest_path), str(backup))
        print(f"Backup created: {backup.name}")

    stats = {
        "total_input":        len(manifest),
        "schema_fields_added": 0,
        "domain_fixes":       0,
        "engine_fixes":       0,
        "semantic_dupes_removed": 0,
        "total_output":       0,
    }

    # ── Pass 1: Per-entry fixes ────────────────────────────────────────────────
    for entry in manifest:
        if not isinstance(entry, dict):
            continue

        title  = entry.get("title", "").lower()
        domain = entry.get("domain", "")
        tier   = entry.get("tier", "")

        # Fix 1: Ensure schema completeness — add missing fields with safe defaults
        if "core_subtopics" not in entry:
            entry["core_subtopics"] = []
            stats["schema_fields_added"] += 1
        if "suggested_chapters" not in entry:
            entry["suggested_chapters"] = 1
            stats["schema_fields_added"] += 1
        if not isinstance(entry.get("references"), list):
            entry["references"] = []
        if not isinstance(entry.get("pyqs"), list):
            entry["pyqs"] = []
        if not isinstance(entry.get("source_files"), list):
            entry["source_files"] = []

        # Fix 2: Domain corrections for known misclassified entries
        for kw, correct_domain in _DOMAIN_FIXES:
            if kw in title:
                old_domain = domain
                entry["domain"] = correct_domain
                # Regenerate ID prefix to reflect new domain
                # (keep hash suffix the same — just update prefix in ID)
                if old_domain != correct_domain:
                    old_id = entry.get("id", "")
                    if old_id.startswith(old_domain):
                        entry["id"] = correct_domain + old_id[len(old_domain):]
                    stats["domain_fixes"] += 1
                break

        # Fix 3: NaturalSciences Junior tier → engine must be remotion
        # (Gemini sometimes assigns manim to junior chemistry/biology)
        if entry.get("domain") == "12_NaturalSciences" and tier == "Junior":
            if entry.get("engine") == "manim":
                entry["engine"] = "remotion"
                stats["engine_fixes"] += 1

    # ── Pass 2: Semantic deduplication ────────────────────────────────────────
    # "Advanced sensors & Actuators" and "Advanced sensors and Actuators" produce
    # different slug prefixes even with the same hash suffix. Remove the lower-
    # priority duplicate (keep the one with more core_subtopics or the first seen).
    seen_semantic: dict[str, int] = {}   # semantic_key → index in output list
    output: list[dict] = []

    for entry in manifest:
        if not isinstance(entry, dict) or not entry.get("title"):
            output.append(entry)
            continue

        semantic_key = (
            entry.get("domain", "")
            + "|"
            + entry.get("grade_or_sem", "")
            + "|"
            + normalize_title(entry.get("title", ""))
        )

        if semantic_key not in seen_semantic:
            seen_semantic[semantic_key] = len(output)
            output.append(entry)
        else:
            # Duplicate found — merge source_docx, keep entry with more subtopics
            existing_idx = seen_semantic[semantic_key]
            existing     = output[existing_idx]

            # Merge source_docx tracking
            existing_src = existing.get("source_docx", "")
            new_src      = entry.get("source_docx", "")
            if new_src and new_src not in existing_src:
                existing["source_docx"] = existing_src + " | " + new_src

            # Keep the richer core_subtopics
            existing_st = existing.get("core_subtopics", [])
            new_st      = entry.get("core_subtopics", [])
            if len(new_st) > len(existing_st):
                existing["core_subtopics"] = new_st

            # Keep the non-empty subject_code
            if not existing.get("subject_code") and entry.get("subject_code"):
                existing["subject_code"] = entry["subject_code"]

            stats["semantic_dupes_removed"] += 1
            if dry_run:
                print(
                    f"  [DUP] '{entry.get('title')}' ({entry.get('grade_or_sem','')})"
                    f"\n         kept: {existing.get('id')}"
                    f"\n         drop: {entry.get('id')}"
                )

    stats["total_output"] = len(output)

    # ── Write ──────────────────────────────────────────────────────────────────
    if not dry_run:
        tmp = manifest_path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(output, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(manifest_path)
            print(f"\nManifest saved: {manifest_path}")
        except OSError as e:
            tmp.unlink(missing_ok=True)
            print(f"ERROR: Write failed — {e}")
            return stats

    # ── Report ─────────────────────────────────────────────────────────────────
    print(f"\n{'─'*50}")
    print(f"{'DRY RUN — no changes written' if dry_run else 'Repair complete'}")
    print(f"  Input entries        : {stats['total_input']}")
    print(f"  Schema fields added  : {stats['schema_fields_added']}")
    print(f"  Domain fixes         : {stats['domain_fixes']}")
    print(f"  Engine fixes         : {stats['engine_fixes']}")
    print(f"  Semantic dupes removed: {stats['semantic_dupes_removed']}")
    print(f"  Output entries       : {stats['total_output']}")
    print(f"{'─'*50}\n")
    return stats


def main() -> None:
    p = argparse.ArgumentParser(
        prog="manifest_repair.py",
        description=(
            "One-time repair of master_manifest.json — "
            "fixes semantic dupes, domain errors, schema gaps."
        ),
    )
    p.add_argument(
        "--manifest",
        type=Path,
        default=MASTER_MANIFEST,
        help=f"Path to manifest (default: {MASTER_MANIFEST})",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing",
    )
    args = p.parse_args()
    repair_manifest(args.manifest, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
