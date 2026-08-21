#!/usr/bin/env python3
from __future__ import annotations

"""
synthesis_gate.py — Phase 2 — Multi-tier Synthesis Engine (v2: chapter-aware)

KEY FIX vs previous version — three manifest fields were computed in Phase 0
but NEVER consumed here, which is the single biggest accuracy gap in the
pipeline:

  1. core_subtopics    — now injected into every synthesis prompt. Previously
                          the LLM only saw the title and had to guess depth/scope.
  2. prerequisite_context — now injected. Tells the model exactly what the
                          student already knows from earlier tiers/grades in the
                          same subject, so notes build progressively (Jr -> Sr ->
                          B.Tech) instead of re-deriving basics or assuming gaps.
  3. suggested_chapters — now ACTUALLY used. Claude's real output ceiling is
                          8,192 tokens. 93 of 142 manifest modules have
                          suggested_chapters > 1 specifically because a single
                          8,192-token response cannot cover them with full
                          derivations. Modules with suggested_chapters > 1 are
                          now split into N separate batch requests (one per
                          chapter, each covering a subset of core_subtopics),
                          then reassembled into one vault file in order.
                          Junior tier (Gemini, 65,536-token ceiling) does NOT
                          need splitting — handled as a single call regardless
                          of suggested_chapters, since there is no token-ceiling
                          risk there.
"""
import re, json, time, math, argparse
from pathlib import Path
from typing import Optional

try:
    from google import genai as google_genai
    from google.genai import types as gt
except ImportError:
    google_genai = None
    gt = None
try:
    import anthropic as ant
except ImportError:
    ant = None

from config import (
    log, console, get_env, safe_slug, safe_gemini_text,
    MASTER_MANIFEST, VAULT_DIR, VAULT_STATE,
    GEMINI_FLASH_MODEL, TIER_MODEL_MAP, MAX_RETRIES, calc_backoff,
)

_SYS = {
    "Junior": (
        "You are an engaging B.Tech-track study guide writer for school students. "
        "Write comprehensive revision notes in clean Markdown. Sections: Overview, "
        "Key Concepts, Formulas ($LaTeX$), Worked Examples, Common Questions. "
        "Inline math: $...$, display: $$...$$\n\n"
        "CONTINUITY RULE: A 'Builds on' list may be provided showing topics the "
        "student already covered in earlier classes within this same subject. "
        "Do not re-derive those basics from scratch — reference them briefly "
        "('as covered in Class 6-8 Number Systems...') and build forward from "
        "that level. If no prior topics are listed, this is foundational — start "
        "from first principles."
    ),
    "Senior": (
        "You are a senior-level educator preparing students for board exams and "
        "competitive exams (JEE/NEET). Write detailed Markdown revision notes: "
        "Overview, Theory & Derivations, Formulas (LaTeX), Solved Examples, PYQ Analysis.\n\n"
        "CONTINUITY RULE: A 'Builds on' list shows topics already covered in Junior "
        "years or earlier within this subject. Assume mastery of those — do not "
        "re-teach them. Explicitly connect new content to them where natural "
        "(e.g. 'extending the quadratic equations from Class 9-10 to...'). "
        "Calibrate depth to JEE Main/Advanced and NEET level, not just board level."
    ),
    "Engineering": (
        "You are a Principal B.Tech Engineering Professor — Robotics & AI specialisation. "
        "Write complete exam-ready Markdown notes. Mandatory sections:\n"
        "## Overview\n## Core Theory (full derivations — no steps skipped)\n"
        "## Mathematical Framework (LaTeX)\n## Applications & Industry Use\n"
        "## Lab-Theory Bridge\n## Exam High-Yield Points\n"
        "Display math: $$...$$  Inline math: $...$\n\n"
        "CONTINUITY RULE: A 'Builds on' list shows prerequisite topics from earlier "
        "semesters or from Senior-level schooling within this subject area. Assume "
        "full mastery of those — never re-derive Class 11-12 or earlier-semester "
        "material from scratch. Build directly on that foundation and cite it by "
        "name when extending it (e.g. 'building on the eigenvalue decomposition from "
        "Linear Algebra, we now...')."
    ),
}

def _cfg(**kw) -> gt.GenerateContentConfig:
    return gt.GenerateContentConfig(
        thinking_config=gt.ThinkingConfig(thinking_budget=0),
        tool_config=gt.ToolConfig(function_calling_config=gt.FunctionCallingConfig(
            mode=gt.FunctionCallingMode.NONE)), **kw)

def _split_subtopics(core_subtopics: list[str], n_chapters: int) -> list[list[str]]:
    """Split core_subtopics into n_chapters roughly-equal, order-preserving chunks."""
    if n_chapters <= 1 or len(core_subtopics) <= 1:
        return [core_subtopics]
    chunk_size = math.ceil(len(core_subtopics) / n_chapters)
    return [core_subtopics[i:i+chunk_size] for i in range(0, len(core_subtopics), chunk_size)]

def _build_prereq_block(mod: dict) -> str:
    prereq = mod.get("prerequisite_context", [])
    if not isinstance(prereq, list) or not prereq:
        return "\n\n## Prerequisite Context\nThis is a foundational topic — no prior coverage assumed."
    lines = "\n".join(f"- {p}" for p in prereq)
    return f"\n\n## Builds On (already covered — do not re-derive)\n{lines}"

def _build_prompt(mod: dict, subtopic_subset: Optional[list[str]] = None,
                   chapter_info: Optional[tuple[int,int]] = None) -> str:
    title   = mod.get("title","")
    subject = mod.get("subject","")
    domain  = mod.get("domain","")
    content = str(mod.get("content","")).strip()
    pyqs    = mod.get("pyqs",[])
    refs    = mod.get("references",[])
    subtopics = subtopic_subset if subtopic_subset is not None else mod.get("core_subtopics",[])

    chapter_hdr = ""
    if chapter_info:
        idx, total = chapter_info
        chapter_hdr = f"\n\n**This is Chapter {idx} of {total} for this module — cover ONLY the subtopics listed below in full depth.**"

    subtopic_blk = ""
    if isinstance(subtopics, list) and subtopics:
        subtopic_blk = "\n\n## Required Subtopics (cover each with full depth)\n" + \
                        "\n".join(f"- {s}" for s in subtopics)

    prereq_blk = _build_prereq_block(mod)

    pyq_blk = ("\n\n## PYQs\n" + "\n".join(f"- {q}" for q in pyqs[:8])) if pyqs else ""
    ref_blk = ""
    if isinstance(refs,list) and refs:
        valid = [r for r in refs[:4] if isinstance(r,dict) and r.get("title") and r.get("url")]
        if valid: ref_blk = "\n\n## References\n" + "\n".join(f"- [{r['title']}]({r['url']})" for r in valid)

    src = content[:10000] if content else "(No enriched source material yet — use domain expertise and the required subtopics above)"

    return (f"Generate complete study notes for:\n"
            f"**Title**: {title}\n**Subject**: {subject}\n**Domain**: {domain}"
            f"{chapter_hdr}{subtopic_blk}{prereq_blk}\n\n"
            f"## Source Material\n{'─'*50}\n{src}{pyq_blk}{ref_blk}")

def _load_state() -> dict:
    from pve.state import load_json_state
    return load_json_state(VAULT_STATE, default_when_missing={"synthesised_ids":[],"batch_ids":{}})

def _save_state(state: dict) -> None:
    tmp = VAULT_STATE.with_suffix(".tmp")
    try:
        VAULT_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state,indent=2), encoding="utf-8")
        tmp.replace(VAULT_STATE)
    except OSError as e: log.error(f"[red]State save: {e}[/red]")
    finally: tmp.unlink(missing_ok=True)

def _write_vault(mod: dict, text: str) -> Optional[Path]:
    tier  = mod.get("tier","Junior")
    subj  = safe_slug(mod.get("subject","unknown"))
    module_id = safe_slug(mod.get("id", ""))
    if not module_id:
        log.error("[red]Vault write blocked: module has no stable ID[/red]"); return None
    path  = VAULT_DIR / tier / subj / f"{module_id}.md"
    tmp   = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        hdr = (f"---\nid: {mod.get('id','')}\ntitle: {mod.get('title','')}\n"
               f"subject: {mod.get('subject','')}\ndomain: {mod.get('domain','')}\n"
               f"tier: {tier}\ngrade_or_sem: {mod.get('grade_or_sem','')}\n"
               f"chapters: {mod.get('suggested_chapters',1)}\n---\n\n")
        tmp.write_text(hdr + text, encoding="utf-8")
        tmp.replace(path)
        return path
    except OSError as e:
        tmp.unlink(missing_ok=True)
        log.error(f"[red]Vault write {path}: {e}[/red]"); return None

# ── Junior: single Gemini call, full subtopics (65,536-token ceiling, no split needed) ──
def _synthesise_junior(mod: dict, gclient) -> Optional[str]:
    full_prompt = f"{_SYS['Junior']}\n\n{_build_prompt(mod)}"
    for i in range(MAX_RETRIES):
        try:
            r = gclient.models.generate_content(
                model=GEMINI_FLASH_MODEL, contents=full_prompt,
                config=_cfg(temperature=0.5, max_output_tokens=65536))
            text = safe_gemini_text(r)
            if text:
                # Junior's 65,536-token ceiling makes truncation unlikely but not
                # impossible — check finish_reason rather than assume completeness.
                try:
                    fr = r.candidates[0].finish_reason
                    if str(fr).upper() in ("MAX_TOKENS", "FINISHREASON.MAX_TOKENS"):
                        log.warning(f"  [yellow]Truncated at max_output_tokens: {mod.get('title','?')}[/yellow]")
                        text += "\n\n> ⚠️ **TRUNCATED — hit the 65,536-token output ceiling.**"
                except (IndexError, AttributeError):
                    pass
                return text
            log.warning(f"  [yellow]Empty Gemini response attempt {i+1}[/yellow]")
        except Exception as e:
            es = str(e).lower()
            if "429" in es or "resource_exhausted" in es:
                wait = min(60*(i+1), 300)
                log.warning(f"  [yellow]Rate limit — waiting {wait}s[/yellow]")
                time.sleep(wait); continue
            if i >= MAX_RETRIES-1: log.error(f"  [red]Junior synth failed: {e}[/red]"); return None
            time.sleep(calc_backoff(i))
    return None

def _safe_cid(mid: str, chapter_suffix: str = "") -> str:
    base = re.sub(r'[^a-zA-Z0-9_-]','_', mid)
    max_base = 64 - len(chapter_suffix)
    c = base[:max_base] + chapter_suffix
    return c if c else f"mod_{abs(hash(mid))%10**8}{chapter_suffix}"

# ── Senior/Engineering: chapter-aware batch submission ──────────────────────────
def _safe_chapters(mod: dict) -> int:
    """Guarded int conversion — one malformed manifest entry must not abort
    an entire batch submission covering 50-90 other modules."""
    try:    return max(1, int(mod.get("suggested_chapters", 1)))
    except (TypeError, ValueError): return 1

# CORRECTION (found via verification, not assumption): the entire chapter-
# splitting architecture below was originally built on "Claude's real output
# ceiling is 8,192 tokens" — that was WRONG. Current claude-sonnet-4-6 and
# claude-haiku-4-5-20251001 both support 64,000 output tokens on the standard
# Messages/Batches API (confirmed via Anthropic's docs + a GitHub issue
# specifically about this exact 8192-vs-64000 confusion). At 64K tokens
# (~48,000-50,000 words), splitting based on subtopic count was solving a
# problem that doesn't exist — a complete module's exam notes realistically
# run 1,500-4,000 words even for dense topics. Pre-emptively splitting was
# pure added API cost (93 of 142 modules getting 2-3x the necessary requests)
# with zero benefit. max_tokens raised to the real ceiling; splitting is now
# reserved for genuinely extreme density (>15 subtopics) rather than firing
# on the routine 3-6 subtopic case it used to trigger on.
_CLAUDE_MAX_TOKENS = 64000
_CHAPTER_SPLIT_SUBTOPIC_THRESHOLD = 15

def submit_batch(modules: list[dict], tier: str, aclient) -> Optional[str]:
    """
    Submits one batch request per module in the normal case. Only splits into
    multiple chapter-level requests when core_subtopics genuinely exceeds
    _CHAPTER_SPLIT_SUBTOPIC_THRESHOLD — a real overflow risk, not a routine one.
    custom_id encodes module_id + chapter index so retrieve_batch can reassemble.
    """
    model  = TIER_MODEL_MAP[tier]["synthesis_model"]
    system = _SYS[tier]
    reqs   = []
    chapter_plan = 0
    for mod in modules:
        mid = mod.get("id","")
        if not mid: continue
        cst = mod.get("core_subtopics",[])
        needs_split = isinstance(cst, list) and len(cst) > _CHAPTER_SPLIT_SUBTOPIC_THRESHOLD
        n_chapters  = _safe_chapters(mod) if needs_split else 1
        chunks = _split_subtopics(cst, n_chapters)

        if len(chunks) == 1:
            cid = _safe_cid(mid)
            reqs.append({
                "custom_id": cid,
                "params": {
                    "model": model, "max_tokens": _CLAUDE_MAX_TOKENS,
                    "system": [{"type":"text","text":system,"cache_control":{"type":"ephemeral"}}],
                    "messages": [{"role":"user","content": _build_prompt(mod)}],
                }
            })
        else:
            chapter_plan += len(chunks)
            for idx, chunk in enumerate(chunks, start=1):
                cid = _safe_cid(mid, chapter_suffix=f"__c{idx}")
                prompt = _build_prompt(mod, subtopic_subset=chunk, chapter_info=(idx, len(chunks)))
                reqs.append({
                    "custom_id": cid,
                    "params": {
                        "model": model, "max_tokens": _CLAUDE_MAX_TOKENS,
                        "system": [{"type":"text","text":system,"cache_control":{"type":"ephemeral"}}],
                        "messages": [{"role":"user","content": prompt}],
                    }
                })
    if not reqs: log.warning("[yellow]No requests to submit[/yellow]"); return None
    multi_chapter_modules = sum(
        1 for m in modules
        if isinstance(m.get("core_subtopics"), list) and len(m["core_subtopics"]) > _CHAPTER_SPLIT_SUBTOPIC_THRESHOLD
    )
    log.info(f"[cyan]Batch plan: {len(modules)} modules -> {len(reqs)} requests "

             f"({multi_chapter_modules} modules split into chapters)[/cyan]")
    try:
        batch = aclient.messages.batches.create(requests=reqs)
        log.info(f"[green]Batch submitted:[/green] {batch.id} ({len(reqs)} reqs, tier={tier})")
        return batch.id
    except Exception as e: log.error(f"[red]Submit failed: {e}[/red]"); return None

def retrieve_batch(batch_id: str, aclient, poll_sec: int=60, max_wait_hours: float=30.0) -> dict[str,str]:
    """
    Returns dict: custom_id -> text. Caller (SynthesisGate.retrieve) is
    responsible for reassembling chapter-split custom_ids back into one
    module before writing the vault file.

    TRUNCATION DETECTION: result.type == "succeeded" only means the API call
    didn't error — it does NOT mean generation finished naturally. A response
    that hit the 8,192-token ceiling mid-derivation still reports "succeeded".
    Without checking stop_reason, a truncated chapter would be silently
    written to the vault as if complete. Truncated responses are now flagged
    with a visible marker so they surface in review rather than passing
    silently as finished notes.

    TIMEOUT (found via failure-injection testing): the polling loop previously
    had no exit condition other than processing_status == "ended". A stuck
    batch, a stale/wrong batch_id, or an API anomaly that never transitions
    out of "in_progress" would hang this forever with zero indication anything
    was wrong. Anthropic's documented batch SLA is up to 24 hours; max_wait_hours
    defaults to 30 to give margin beyond that, after which this gives up and
    returns whatever was retrievable rather than hanging indefinitely.
    """
    log.info(f"[cyan]Polling {batch_id}... (will give up after {max_wait_hours}h if stuck)[/cyan]")
    deadline = time.time() + max_wait_hours * 3600
    timed_out = False
    while True:
        if time.time() > deadline:
            log.error(f"[red]Batch {batch_id} did not reach 'ended' within {max_wait_hours}h — "
                      f"giving up. Check this batch_id manually; it may still complete later.[/red]")
            timed_out = True
            break
        try:
            b  = aclient.messages.batches.retrieve(batch_id)
            rc = b.request_counts
            log.info(f"  {b.processing_status} | proc={rc.processing} ok={rc.succeeded} err={rc.errored}")
            if b.processing_status == "ended": break
            time.sleep(poll_sec)
        except ant.RateLimitError: time.sleep(60)
        except Exception as e: log.error(f"[red]Poll error: {e}[/red]"); time.sleep(30)
    if timed_out:
        return {}
    results: dict[str,str] = {}
    truncated: list[str] = []
    try:
        for res in aclient.messages.batches.results(batch_id):
            if res.result.type == "succeeded":
                msg = res.result.message
                c   = msg.content
                if c and hasattr(c[0],"text"):
                    text = c[0].text
                    if getattr(msg, "stop_reason", None) == "max_tokens":
                        truncated.append(res.custom_id)
                        text += ("\n\n> ⚠️ **TRUNCATED — hit the 8,192-token output ceiling.** "
                                 "This chapter is incomplete. Consider increasing "
                                 "suggested_chapters for this module and re-running synthesis.")
                    results[res.custom_id] = text
    except Exception as e: log.error(f"[red]Results fetch: {e}[/red]")
    log.info(f"[green]{len(results)} results from {batch_id}[/green]")
    if truncated:
        log.warning(f"[yellow]{len(truncated)} response(s) hit max_tokens and were truncated: "
                    f"{', '.join(truncated[:5])}{'...' if len(truncated)>5 else ''}[/yellow]")
    return results

def _reassemble_chapters(results: dict[str,str]) -> dict[str,str]:
    """
    Groups custom_ids like 'abc123__c1', 'abc123__c2' by base id, sorts by
    chapter index, concatenates into one document with chapter headers.
    Plain (non-chaptered) custom_ids pass through unchanged.
    """
    groups: dict[str, list[tuple[int,str]]] = {}
    plain: dict[str,str] = {}
    for cid, text in results.items():
        m = re.match(r'^(.+)__c(\d+)$', cid)
        if m:
            base, idx = m.group(1), int(m.group(2))
            groups.setdefault(base, []).append((idx, text))
        else:
            plain[cid] = text

    reassembled: dict[str,str] = dict(plain)
    for base, parts in groups.items():
        parts.sort(key=lambda p: p[0])
        merged = "\n\n---\n\n".join(
            f"## Chapter {idx}\n\n{text}" for idx, text in parts
        )
        reassembled[base] = merged
    return reassembled

class SynthesisGate:
    def __init__(self, gemini_client=None, anthropic_client=None):
        self.gc = gemini_client
        self.ac = anthropic_client

    def run_junior(self, manifest: list[dict], state: dict) -> int:
        done  = set(state.get("synthesised_ids",[]))
        mods  = [m for m in manifest if m.get("tier")=="Junior" and m.get("id") not in done]
        if not mods: log.info("[dim]No Junior modules pending[/dim]"); return 0
        log.info(f"[bold]Junior Synthesis[/bold] — {len(mods)} modules")
        ok = 0
        for i,mod in enumerate(mods):
            log.info(f"  [{i+1}/{len(mods)}] {mod.get('title','?')}")
            text = _synthesise_junior(mod, self.gc)
            if not text: log.error(f"  [red]Failed: {mod.get('id')}[/red]"); continue
            p = _write_vault(mod, text)
            if p:
                state["synthesised_ids"].append(mod["id"])
                _save_state(state); ok += 1
                log.info(f"  [green]✓[/green] {p.name}")
        return ok

    def submit(self, manifest: list[dict], tier: str, state: dict) -> Optional[str]:
        done = set(state.get("synthesised_ids",[]))
        mods = [m for m in manifest if m.get("tier")==tier and m.get("id") not in done]
        if not mods: log.info(f"[dim]No {tier} modules pending[/dim]"); return None
        log.info(f"[bold]{tier} Submit[/bold] — {len(mods)} modules")
        bid = submit_batch(mods, tier, self.ac)
        if bid:
            state.setdefault("batch_ids",{})[bid] = {"tier":tier,"count":len(mods)}
            _save_state(state)
        return bid

    def retrieve(self, batch_id: str, manifest: list[dict], state: dict) -> int:
        raw_results = retrieve_batch(batch_id, self.ac)
        results     = _reassemble_chapters(raw_results)
        by_cid      = {_safe_cid(m.get("id","")): m for m in manifest if m.get("id")}
        ok = 0
        for cid, text in results.items():
            mod = by_cid.get(cid)
            if not mod: continue
            p = _write_vault(mod, text)
            if p:
                state["synthesised_ids"].append(mod["id"]); ok += 1
        _save_state(state)
        log.info(f"[green]{ok} vault files written from {batch_id}[/green]")
        return ok

    def run(self, manifest_path=MASTER_MANIFEST, tier=None, canary=False,
            submit_tier=None, retrieve_id=None):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, list): log.error("[red]Bad manifest[/red]"); return
        except Exception as e: log.error(f"[red]Load manifest: {e}[/red]"); return

        state = _load_state()

        if canary:
            mods = [m for m in manifest if m.get("tier")=="Junior"][:3]
            log.info(f"[yellow bold]Canary: {len(mods)} Junior modules[/yellow bold]")
            if mods and self.gc: self.run_junior(mods, state)
            return

        if retrieve_id: self.retrieve(retrieve_id, manifest, state); return
        if submit_tier: self.submit(manifest, submit_tier, state); return

        if self.gc: self.run_junior(manifest, state)
        if self.ac:
            for t in ["Senior","Engineering"]:
                if not tier or tier in (t,"all"): self.submit(manifest, t, state)

def main():
    from pve.safety import require_legacy_paid_execution
    require_legacy_paid_execution()
    p = argparse.ArgumentParser(prog="synthesis_gate.py")
    p.add_argument("--manifest", type=Path, default=MASTER_MANIFEST)
    p.add_argument("--tier",     default=None)
    p.add_argument("--canary",   action="store_true")
    p.add_argument("--submit",   metavar="TIER")
    p.add_argument("--retrieve", metavar="BATCH_ID")
    args = p.parse_args()

    gc = ac = None
    try: gc = google_genai.Client(api_key=get_env("GEMINI_API_KEY"))
    except Exception as e: log.warning(f"[yellow]Gemini: {e}[/yellow]")
    try: ac = ant.Anthropic(api_key=get_env("ANTHROPIC_API_KEY"))
    except Exception as e: log.warning(f"[yellow]Anthropic: {e}[/yellow]")

    sg = SynthesisGate(gc, ac)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except Exception as e: log.error(f"[red]{e}[/red]"); return
    state = _load_state()

    if args.retrieve: sg.retrieve(args.retrieve, manifest, state)
    elif args.submit: sg.submit(manifest, args.submit, state)
    else: sg.run(args.manifest, args.tier, args.canary)

if __name__ == "__main__":
    main()
