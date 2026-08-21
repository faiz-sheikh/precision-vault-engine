#!/usr/bin/env python3
"""
dedup_engine.py — Precision Vault Engine v2  |  Phase -1
FIX (8th audit):
  - get_embedding(): 404/NOT_FOUND errors are PERMANENT (model retired or
    wrong name) — they must NOT be retried. Added permanent-error fast-fail
    that immediately returns [] and logs an actionable message instead of
    burning all 3 retry slots on an unrecoverable error.
  - If embedding is unavailable (any permanent failure), dedup gracefully
    falls back to hash-only mode (steps 1 + 2 still run; step 3 skipped).
    This is the correct degraded behavior — the pipeline continues.
"""
import gc
import re
import sys
import json
import time
import base64
import hashlib
import shutil
import subprocess
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional
import random
from google import genai
from google.genai import types
from google.genai.errors import APIError
try:
    from PIL import Image
    import numpy as np
except ImportError:
    sys.exit("Pillow + numpy required: pip install Pillow numpy")

try:
    from google import genai as google_genai
    from google.genai import types as google_genai_types
except ImportError:
    sys.exit("google-genai required: pip install google-genai")

from config import (
    log, console, get_env, safe_slug, classify_file, infer_tier,
    STAGING_DIR, QUARANTINE_DIR, HANDWRITING_DIR,
    HASH_REGISTRY_PATH, EMBEDDING_REGISTRY_PATH,
    GEMINI_FLASH_MODEL, GEMINI_EMBED_MODEL,
    MAX_RETRIES, calc_backoff,
)

SIMILARITY_THRESHOLD       = 0.92
HANDWRITING_DARK_THRESHOLD = 0.04
MARGIN_FRACTION            = 0.15
MAX_EMBEDDING_CHARS        = 10_000

TEXT_EXTENSIONS  = frozenset({".pdf", ".pptx", ".docx", ".txt", ".md"})
DOC_LEGACY_EXT   = frozenset({".doc"})
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"})
ALL_EXTENSIONS   = TEXT_EXTENSIONS | DOC_LEGACY_EXT | IMAGE_EXTENSIONS

_BOILERPLATE_RE = re.compile(
    r"Ramdeobaba\s+University|B\.Tech\s+[\w\s]+|"
    r"Slide\s+\d+\s+of\s+\d+|Page\s+\d+\s+of\s+\d+|"
    r"©\s*\d{4}[^\n]*|Prepared\s+by\s+[^\n]+|"
    r"^\s*[-=_]{3,}\s*$|^\s*\d{1,3}\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def strip_boilerplate(raw_text: str) -> str:
    text = _BOILERPLATE_RE.sub(" ", raw_text)
    text = re.sub(r'\b[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}\b', " ", text)
    text = re.sub(r'https?://\S+', " ", text)
    lines = [ln for ln in text.splitlines() if len(ln.strip()) >= 8 or not ln.strip()]
    text  = "\n".join(lines)
    return re.sub(r'[ \t]{2,}', ' ', re.sub(r'\n{3,}', '\n\n', text)).strip()


def content_fingerprint(text: str) -> str:
    clean = re.sub(r'\s+', ' ', strip_boilerplate(text).lower()).strip()
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


# ── Text Extractors ───────────────────────────────────────────────────────────
def _extract_pdf_streaming(path: Path) -> str:
    try:
        import pypdf
    except ImportError:
        return ""
    parts: list[str] = []
    try:
        with open(path, "rb") as fh:
            reader = pypdf.PdfReader(fh)
            for i in range(len(reader.pages)):
                try:
                    t = reader.pages[i].extract_text()
                    if t and t.strip():
                        parts.append(t.strip())
                except Exception:
                    continue
    except Exception as exc:
        log.warning(f"  [yellow]PDF failed {path.name}: {exc}[/yellow]")
    return "\n".join(parts)


def _extract_pptx_streaming(path: Path) -> str:
    try:
        from pptx import Presentation
    except ImportError:
        return ""
    parts: list[str] = []
    try:
        prs = Presentation(str(path))
        for slide in prs.slides:
            sp = [
                s.text.strip() for s in slide.shapes
                if hasattr(s, "text") and s.text.strip()
            ]
            if sp:
                parts.append("\n".join(sp))
    except Exception as exc:
        log.warning(f"  [yellow]PPTX failed {path.name}: {exc}[/yellow]")
    return "\n\n".join(parts)


def _extract_docx_streaming(path: Path) -> str:
    """ONLY for .docx — never call with .doc files."""
    try:
        import docx
    except ImportError:
        return ""
    parts: list[str] = []
    try:
        with open(path, "rb") as fh:
            doc = docx.Document(fh)
        for para in doc.paragraphs:
            if para.text.strip():
                parts.append(para.text.strip())
    except Exception as exc:
        log.warning(f"  [yellow]DOCX failed {path.name}: {exc}[/yellow]")
    return "\n".join(parts)


def _extract_doc_legacy(path: Path) -> str:
    """Explicit .doc (legacy binary Word) handler: antiword → catdoc → python-docx."""
    for cmd in (["antiword", str(path)], ["catdoc", str(path)]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip():
                log.info(f"  [dim].doc via {cmd[0]}: {path.name}[/dim]")
                return r.stdout.strip()
        except FileNotFoundError:
            pass
        except (subprocess.TimeoutExpired, Exception) as exc:
            log.warning(f"  [yellow]{cmd[0]} error for {path.name}: {exc}[/yellow]")
    try:
        import docx
        with open(path, "rb") as fh:
            doc = docx.Document(fh)
        parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        if parts:
            log.info(f"  [dim].doc via python-docx fallback: {path.name}[/dim]")
            return "\n".join(parts)
    except Exception:
        pass
    log.warning(
        f"  [yellow]Cannot extract .doc: {path.name} — "
        "install antiword: sudo apt install antiword[/yellow]"
    )
    return ""


def extract_text_from_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":   return _extract_pdf_streaming(path)
    if ext == ".pptx":  return _extract_pptx_streaming(path)
    if ext == ".docx":  return _extract_docx_streaming(path)
    if ext == ".doc":   return _extract_doc_legacy(path)
    if ext in (".txt", ".md"):
        parts: list[str] = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk: break
                    parts.append(chunk)
        except OSError:
            return ""
        return "".join(parts)
    return ""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    va    = np.array(a, dtype=np.float32)
    vb    = np.array(b, dtype=np.float32)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / denom) if denom != 0.0 else 0.0


def get_embedding(text: str, gemini_client: genai.Client, is_query: bool = False) -> list[float]:
    clean = strip_boilerplate(text)[:MAX_EMBEDDING_CHARS]
    if not clean.strip():
        return []

    task_type = "RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT"

    # Increase retries for bulk processing
    for attempt in range(10): 
        try:
            result = gemini_client.models.embed_content(
                model=GEMINI_EMBED_MODEL,
                contents=clean,
                config=types.EmbedContentConfig(task_type=task_type)
            )
            
            if result and result.embeddings:
                return list(result.embeddings[0].values)
            return []

        except APIError as exc:
            # 429 errors (Rate limits or Depleted Credits)
            if exc.code == 429:
                # Exponential backoff: 5, 10, 20... seconds + jitter
                wait = (5 * (2 ** attempt)) + random.uniform(1, 5)
                log.warning(f"  [yellow]Rate limited (429). Retrying in {wait:.2f}s...[/yellow]")
                time.sleep(wait)
                continue
            
            # Permanent/Auth Errors
            if exc.code in [400, 401, 403, 404]:
                log.error(f"  [red]Permanent error ({exc.code}): {exc}[/red]")
                return []
            
            # General transient retry
            time.sleep(5)
            continue

        except Exception as exc:
            log.error(f"  [red]Unexpected error: {exc}[/red]")
            return []
    return []

# ── Handwriting Detection ─────────────────────────────────────────────────────
def _detect_handwriting(image_path: Path) -> bool:
    try:
        img     = Image.open(image_path).convert("L")
        arr     = np.array(img, dtype=np.float32)
        h, w    = arr.shape
        mw      = max(int(w * MARGIN_FRACTION), 1)
        margins = np.concatenate([arr[:, :mw].ravel(), arr[:, w - mw:].ravel()])
        return (
            (float(np.sum(margins < 180)) / margins.size) > HANDWRITING_DARK_THRESHOLD
            if margins.size else False
        )
    except Exception:
        return False


def extract_handwriting_if_present(
    image_path: Path, gemini_client: google_genai.Client
) -> Optional[str]:
    if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
        return None
    if not _detect_handwriting(image_path):
        return None
    log.info(f"  [yellow]LACUNA-4[/yellow] Handwriting in {image_path.name}")
    try:
        with open(image_path, "rb") as fh:
            img_bytes = fh.read()
        if not img_bytes:
            return None
        mime_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".bmp": "image/bmp",  ".tiff": "image/tiff", ".tif": "image/tiff",
        }
        mime_type = mime_map.get(image_path.suffix.lower(), "image/jpeg")
        img_b64   = base64.b64encode(img_bytes).decode("utf-8")
        prompt = (
            "Transcribe ALL handwritten annotations.\n"
            "Mark exam hints [EXAM-HINT]. Mark derivations [DERIVATION].\n"
            "If no handwriting: reply exactly NONE"
        )
        for attempt in range(MAX_RETRIES):
            try:
                response = gemini_client.models.generate_content(
                    model=GEMINI_FLASH_MODEL,
                    contents=[{"role": "user", "parts": [
                        {"inline_data": {"mime_type": mime_type, "data": img_b64}},
                        {"text": prompt},
                    ]}],
                    config=google_genai_types.GenerateContentConfig(
                        temperature=0.0, max_output_tokens=2048,
                    ),
                )
                raw = (getattr(response, "text", "") or "").strip()
                if not raw or raw.upper() == "NONE":
                    return None
                HANDWRITING_DIR.mkdir(parents=True, exist_ok=True)
                out = HANDWRITING_DIR / f"{image_path.stem}_annotations.txt"
                out.write_text(raw, encoding="utf-8")
                return raw
            except Exception as exc:
                if attempt == MAX_RETRIES - 1:
                    return None
                time.sleep(calc_backoff(attempt))
    except Exception:
        pass
    return None


def quality_score(path: Path) -> int:
    ext = path.suffix.lower()
    if ext == ".pdf":  return 10 if len(_extract_pdf_streaming(path).strip()) > 200 else 3
    if ext == ".pptx": return 8
    if ext == ".docx": return 6
    if ext == ".doc":  return 4
    if ext in (".txt", ".md"):    return 6
    if ext in IMAGE_EXTENSIONS:   return 3 if _detect_handwriting(path) else 1
    return 2


# ── DedupEngine ───────────────────────────────────────────────────────────────
class DedupEngine:
    def __init__(self, gemini_client: google_genai.Client, dry_run: bool = False) -> None:
        self.gemini_client = gemini_client
        self.dry_run       = dry_run
        self._byte_hashes: dict[str, str]                  = {}
        self._text_hashes: dict[str, str]                  = {}
        self._embeddings:  list[tuple[list[float], Path]]  = []
        self.stats = {
            "total": 0, "exact_dupes": 0, "near_dupes": 0,
            "survivors": 0, "handwriting_extracted": 0,
        }
        self._load_hash_registry()
        self._load_embedding_registry()

    # ── Registry I/O ──────────────────────────────────────────────────────────
    def _load_hash_registry(self) -> None:
        if not HASH_REGISTRY_PATH.exists():
            return
        try:
            data = json.loads(HASH_REGISTRY_PATH.read_text(encoding="utf-8"))
            raw_byte = data.get("byte_hashes", {})
            raw_text = data.get("text_hashes", {})
            self._byte_hashes = {k: v for k, v in raw_byte.items() if Path(v).exists()}
            self._text_hashes = {k: v for k, v in raw_text.items() if Path(v).exists()}
            log.info(
                f"[green]Hash registry:[/green] {len(self._byte_hashes)} byte "
                f"/ {len(self._text_hashes)} content hashes"
            )
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            log.warning(f"[yellow]hash_registry corrupt ({exc}). Resetting.[/yellow]")

    def _save_hash_registry(self) -> None:
        try:
            HASH_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = HASH_REGISTRY_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "byte_hashes": self._byte_hashes,
                "text_hashes": self._text_hashes,
                "updated_at":  datetime.now().isoformat(),
            }, indent=2), encoding="utf-8")
            tmp.replace(HASH_REGISTRY_PATH)
        except OSError as exc:
            log.warning(f"[yellow]Hash registry save failed: {exc}[/yellow]")

    def _load_embedding_registry(self) -> None:
        if not EMBEDDING_REGISTRY_PATH.exists():
            return
        try:
            data = json.loads(EMBEDDING_REGISTRY_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            loaded = 0
            for path_str, vec in data.items():
                p = Path(path_str)
                if p.exists() and isinstance(vec, list) and vec:
                    self._embeddings.append((vec, p))
                    loaded += 1
            if loaded:
                log.info(f"[green]Embedding registry:[/green] {loaded} vectors restored.")
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            log.warning(
                f"[yellow]embedding_registry corrupt ({exc}). Starting fresh.[/yellow]"
            )

    def _save_embedding_registry(self) -> None:
        try:
            EMBEDDING_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {str(p): vec for vec, p in self._embeddings if vec and p.exists()}
            tmp  = EMBEDDING_REGISTRY_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            tmp.replace(EMBEDDING_REGISTRY_PATH)
        except OSError as exc:
            log.warning(f"[yellow]Embedding registry save failed: {exc}[/yellow]")

    # ── Internal helpers ───────────────────────────────────────────────────────
    def _quarantine(self, path: Path, reason: str) -> None:
        if self.dry_run:
            log.info(f"  [dim][DRY-RUN] quarantine {path.name} ({reason})[/dim]")
            return
        QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
        dest    = QUARANTINE_DIR / path.name
        counter = 0
        while dest.exists():
            counter += 1
            dest = QUARANTINE_DIR / f"{path.stem}_{counter}{path.suffix}"
        try:
            shutil.move(str(path), str(dest))
        except OSError as exc:
            log.error(f"  [red]Quarantine failed {path.name}: {exc}[/red]")

    def _stage(self, path: Path, subject_slug: str, tier: str) -> Optional[Path]:
        if self.dry_run:
            return None
        dest_dir = STAGING_DIR / tier / subject_slug
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest    = dest_dir / path.name
        counter = 0
        while dest.exists():
            counter += 1
            dest = dest_dir / f"{path.stem}_{counter}{path.suffix}"
        try:
            shutil.copy2(str(path), str(dest))
            return dest
        except OSError as exc:
            log.error(f"  [red]Stage failed {path.name}: {exc}[/red]")
            return None

    def _run_dedup_checks(self, path: Path, raw_text: str) -> bool:
        """
        Three-stage dedup:
        1. Byte hash (exact duplicate)
        2. Content fingerprint (same content, different encoding/formatting)
        3. Embedding cosine similarity (near-duplicate, semantic match)

        If embedding is unavailable (model retired, no API key, etc.),
        step 3 is silently skipped — steps 1+2 still protect against dupes.
        """
        _hashes_dirty     = False
        _embeddings_dirty = False

        # ── Step 1: byte hash ──────────────────────────────────────────────────
        fh = sha256_file(path)
        if fh:
            if fh in self._byte_hashes:
                log.info(f"  [red]Exact dup[/red] {path.name}")
                self.stats["exact_dupes"] += 1
                self._quarantine(path, "exact-byte-dup")
                return False
            self._byte_hashes[fh] = str(path)
            _hashes_dirty = True

        # ── Step 2: content fingerprint ───────────────────────────────────────
        if raw_text.strip():
            fp = content_fingerprint(raw_text)
            if fp in self._text_hashes:
                log.info(f"  [red]Content dup[/red] {path.name}")
                self.stats["exact_dupes"] += 1
                self._quarantine(path, "content-dup")
                if _hashes_dirty:
                    self._save_hash_registry()
                return False
            self._text_hashes[fp] = str(path)
            _hashes_dirty = True

        # ── Step 3: embedding similarity ──────────────────────────────────────
        embed_text  = raw_text if raw_text.strip() else path.stem.replace("_", " ")
        embedding   = get_embedding(embed_text, self.gemini_client)
        quarantined = False

        if embedding:
            for existing_vec, existing_path in self._embeddings:
                if not existing_vec:
                    continue
                sim = cosine_similarity(embedding, existing_vec)
                if sim >= SIMILARITY_THRESHOLD:
                    self.stats["near_dupes"] += 1
                    si     = quality_score(path)
                    se     = quality_score(existing_path)
                    winner = path if si > se else existing_path
                    loser  = existing_path if si > se else path
                    if loser.suffix.lower() in IMAGE_EXTENSIONS:
                        ann = extract_handwriting_if_present(loser, self.gemini_client)
                        if ann:
                            self.stats["handwriting_extracted"] += 1
                    log.info(
                        f"  [yellow]Near-dup (sim={sim:.3f}):[/yellow] "
                        f"keeping {winner.name}"
                    )
                    if winner == path:
                        self._embeddings = [
                            (v, p) for v, p in self._embeddings if p != existing_path
                        ]
                        self._embeddings.append((embedding, path))
                        _embeddings_dirty = True
                        self._quarantine(existing_path, f"near-dup sim={sim:.3f}")
                    else:
                        self._quarantine(path, f"near-dup sim={sim:.3f}")
                        quarantined = True
                    break

        if not quarantined:
            self._embeddings.append((embedding if embedding else [], path))
            if embedding:
                _embeddings_dirty = True

        if _hashes_dirty:
            self._save_hash_registry()
        if _embeddings_dirty:
            self._save_embedding_registry()

        return not quarantined

    # ── Public APIs ───────────────────────────────────────────────────────────
    def process_file(self, path: Path) -> bool:
        if path.suffix.lower() not in ALL_EXTENSIONS:
            return False
        self.stats["total"] += 1
        is_image = path.suffix.lower() in IMAGE_EXTENSIONS
        raw_text = "" if is_image else extract_text_from_file(path)
        if self._run_dedup_checks(path, raw_text):
            domain  = classify_file(str(path))
            tier    = infer_tier(path.stem, domain)
            subject = safe_slug(path.parent.name or path.stem[:30])
            staged  = self._stage(path, subject, tier)
            if staged:
                self.stats["survivors"] += 1
                return True
        return False

    def process_incoming_file(
        self, path: Path, subject: str = "", tier: str = "",
        semester: int = 0, course_code: str = "",
    ) -> Optional[Path]:
        if not path.exists() or path.suffix.lower() not in ALL_EXTENSIONS:
            return None
        self.stats["total"] += 1
        is_image = path.suffix.lower() in IMAGE_EXTENSIONS
        raw_text = "" if is_image else extract_text_from_file(path)
        if not self._run_dedup_checks(path, raw_text):
            return None
        resolved_tier = (
            tier if tier in ("Junior", "Senior", "Engineering")
            else infer_tier(path.stem, classify_file(f"{subject} {path.stem}"))
        )
        subject_slug = safe_slug(subject or path.parent.name or path.stem[:30])
        staged       = self._stage(path, subject_slug, resolved_tier)
        if staged:
            self.stats["survivors"] += 1
        return staged

    def run(self, input_dir: Path) -> dict:
        if not input_dir.exists():
            log.error(f"[red]Input dir not found: {input_dir}[/red]")
            return self.stats
        all_files = [
            f for f in input_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in ALL_EXTENSIONS
        ]
        if not all_files:
            log.warning(f"[yellow]No files in {input_dir}[/yellow]")
            return self.stats
        log.info(
            f"[bold]Phase -1: Dedup[/bold] — {len(all_files)} files, "
            f"{len(self._embeddings)} embeddings loaded"
        )
        for idx, fpath in enumerate(all_files):
            log.info(f"[{idx+1}/{len(all_files)}] {fpath.name}")
            try:
                self.process_file(fpath)
            except Exception as exc:
                log.error(f"[red]Error {fpath.name}: {exc}[/red]")

        from rich.panel import Panel
        console.print(Panel(
            f"Total: {self.stats['total']}  "
            f"Exact dups: [red]{self.stats['exact_dupes']}[/red]  "
            f"Near-dups: [yellow]{self.stats['near_dupes']}[/yellow]  "
            f"Survivors: [green]{self.stats['survivors']}[/green]",
            title="Phase -1 Complete", border_style="green",
        ))
        return self.stats


def main() -> None:
    from pve.safety import require_legacy_paid_execution
    require_legacy_paid_execution()
    parser = argparse.ArgumentParser(prog="dedup_engine.py")
    parser.add_argument("--input",   "-i", type=Path, default=Path("raw_notes"))
    parser.add_argument("--dry-run", "-n", action="store_true")
    args   = parser.parse_args()
    client = google_genai.Client(api_key=get_env("GEMINI_API_KEY"))
    DedupEngine(
        gemini_client=client, dry_run=args.dry_run
    ).run(args.input.resolve())


if __name__ == "__main__":
    main()
