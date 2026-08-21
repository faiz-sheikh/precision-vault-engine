#!/usr/bin/env python3
from __future__ import annotations

"""
ingestion_researcher.py — Phase 1

BUGS FIXED vs prior versions:
  BUG 1: Channel handle slice — was [:3] on joined STRING → "3 chars".
          Fixed: join(handles[:3]) — takes first 3 handles then joins.
  BUG 2: Manifest saved every single module iteration (500+ disk writes).
          Fixed: batch-save every 10 modules + final save at end.
  BUG 3: Missing max_output_tokens on _cfg_no_tool calls → SDK default (may be tiny).
          Fixed: explicit max_output_tokens=2048/4096 on every call.
  BUG 4: run_local_enrichment tier_filter comparison was case-sensitive.
          Fixed: .lower() normalisation on both sides.
  BUG 5: enrich_module built web_queries fallback with duplicate title+subject
          when title==subject → "Control Systems Control Systems B.Tech notes".
          Fixed: dedup query when strings match.

COMMANDS:
  python3 ingestion_researcher.py --source classroom   # Download from Google Classroom
  python3 ingestion_researcher.py --source local       # Enrich manifest with web research
  python3 ingestion_researcher.py --source all         # Both steps in sequence
"""
import re
import json
import time
import urllib.request
import urllib.parse
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    from googleapiclient.errors import HttpError
    _GCP_OK = True
except ImportError:
    _GCP_OK = False

try:
    from google import genai as google_genai
    from google.genai import types as gt
except ImportError:
    google_genai = None
    gt = None

try:
    import requests
    from bs4 import BeautifulSoup
    _REQ_OK = True
except ImportError:
    _REQ_OK = False

try:
    from youtube_transcript_api import (
        YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled, VideoUnavailable,
    )
    _YT_OK = True
except ImportError:
    _YT_OK = False

from config import (
    log, console, get_env, safe_slug, robust_json_parse, safe_gemini_text, get_google_credentials,
    MASTER_MANIFEST, CLASSROOM_DL_DIR, VAULT_ROOT,
    GOOGLE_MIME_EXPORT_MAP, classify_mime,
    GEMINI_FLASH_MODEL, MAX_RETRIES, calc_backoff, GOOGLE_SCOPES,
    ARXIV_API_URL, ARXIV_CATEGORIES, USER_AGENTS, HTML_BOILERPLATE_TAGS,
    WEB_SCRAPE_TIMEOUT_SEC, WEB_SCRAPE_MAX_RETRIES, MAX_TRANSCRIPT_CHARS,
    MAX_ARXIV_RESULTS, PRIORITY_YOUTUBE_CHANNELS,
)

DL_CHUNK  = 1024 * 1024
MAX_PAGES = 50
_MANIFEST_SAVE_INTERVAL = 10   # BUG 2 FIX: save every N modules, not every 1


# ── Gemini config helpers ──────────────────────────────────────────────────────
def _cfg_no_tool(**kw) -> gt.GenerateContentConfig:
    """Non-search calls: thinking disabled, AFC explicitly off."""
    kw.setdefault("max_output_tokens", 2048)
    return gt.GenerateContentConfig(
        thinking_config=gt.ThinkingConfig(thinking_budget=0),
        tool_config=gt.ToolConfig(
            function_calling_config=gt.FunctionCallingConfig(mode="NONE")
        ),
        **kw,
    )

def _cfg_search(**kw) -> gt.GenerateContentConfig:
    """
    Google Search grounded calls — AFC allowed for grounding.

    BUG FIX (confirmed via external research, not hypothetical): Gemini 2.5
    Flash's "thinking" tokens draw from the SAME budget as max_output_tokens
    unless thinking_budget is explicitly set to 0. This is a widely-documented
    issue (15+ open GitHub issues across Google's SDK, LangChain, LiteLLM, etc.)
    where small max_output_tokens budgets (like the 512-2048 used here) get
    silently consumed almost entirely by invisible reasoning, leaving a
    truncated or empty visible response with no error. thinking_budget and
    Google Search grounding are independent settings — disabling thinking
    does not affect grounding/tool-use capability.
    """
    kw.setdefault("max_output_tokens", 2048)
    return gt.GenerateContentConfig(
        thinking_config=gt.ThinkingConfig(thinking_budget=0),
        tools=[gt.Tool(google_search=gt.GoogleSearch())], **kw
    )

def _safe_text(response) -> str:
    return safe_gemini_text(response)

def _http_status(exc) -> int:
    if hasattr(exc, "status_code"):
        try: return int(exc.status_code)
        except Exception: pass
    try: return int(exc.resp["status"])
    except Exception: return 0

def _safe_path(raw: str) -> Optional[Path]:
    """
    SECURITY FIX (found via adversarial testing): the previous version only
    checked "does this resolved path exist and is it a file" — with NO
    containment check. Path("../../../etc/passwd").resolve() legitimately
    resolves to a real, readable file on the system, so it was returned as
    "safe". A tampered or malformed source_files entry in the manifest could
    have caused extract_text() to read and feed arbitrary system files into
    an LLM prompt. Now requires the resolved path to actually be inside
    VAULT_ROOT before treating it as safe.
    """
    if not raw or "\x00" in raw: return None
    try:
        p = Path(raw).resolve()
        if not (p.exists() and p.is_file()): return None
        try:
            p.relative_to(VAULT_ROOT.resolve())
        except ValueError:
            log.warning(f"  [yellow]Path escapes VAULT_ROOT, rejected: {raw}[/yellow]")
            return None
        return p
    except Exception: return None


# ── Text extractors ────────────────────────────────────────────────────────────
def _extract_pdf(path: Path) -> str:
    try:
        import pypdf
        parts = []
        with open(path, "rb") as f:
            r = pypdf.PdfReader(f)
            for i in range(len(r.pages)):
                try:
                    t = r.pages[i].extract_text()
                    if t and t.strip(): parts.append(t.strip())
                except Exception: continue
        return "\n".join(parts)
    except Exception: pass
    return ""

def _extract_pptx(path: Path) -> str:
    try:
        from pptx import Presentation
        parts = []
        for slide in Presentation(str(path)).slides:
            sp = [s.text.strip() for s in slide.shapes if hasattr(s,"text") and s.text.strip()]
            if sp: parts.append("\n".join(sp))
        return "\n\n".join(parts)
    except Exception: return ""

def _extract_docx(path: Path) -> str:
    try:
        import docx
        with open(path, "rb") as f: doc = docx.Document(f)
        return "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())
    except Exception: return ""

def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":           return _extract_pdf(path)
    if ext == ".pptx":          return _extract_pptx(path)
    if ext in (".docx",".doc"): return _extract_docx(path)
    if ext in (".txt",".md"):
        try:
            with open(path,"r",encoding="utf-8",errors="replace") as f: return f.read()
        except OSError: return ""
    return ""

def extract_pyqs(text: str) -> list[str]:
    # BUG FIX (found via adversarial testing): greedy .{20,200} previously
    # consumed as many chars as possible before backtracking to find a
    # trailing '?' — on text where multiple short questions appear on one
    # unbroken line (common after PDF text extraction loses line breaks),
    # this swallowed several real questions into one garbled match. Lazy
    # .{20,200}? stops at the FIRST reachable '?', fixing this regardless
    # of whether newlines are present.
    pat = re.compile(
        r"(?:Q\.?\s*\d+[\.\)]\s*.{20,200}?\?)"
        r"|(?:\d{4}[-\u2013]\d{2,4}\s+exam\s+.{10,100}?(?=\s{2,}|\n|$))"
        r"|(?:\[\s*\d+\s*marks?\s*\].{5,100}?(?=\s{2,}|\n|$))", re.IGNORECASE)
    return list(dict.fromkeys(m.strip() for m in pat.findall(text) if len(m.strip())>20))[:25]


# ── YouTube Transcript Fetcher ─────────────────────────────────────────────────
class YouTubeTranscriptFetcher:
    _PAT = [
        re.compile(r"(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})"),
        re.compile(r"^([a-zA-Z0-9_-]{11})$"),
    ]
    def __init__(self, client): self.client = client

    def _vid_id(self, url: str) -> Optional[str]:
        for p in self._PAT:
            m = p.search(url.strip())
            if m: return m.group(1)
        return None

    def _fetch(self, vid: str) -> Optional[str]:
        if not _YT_OK: return None
        try:
            for lang in [["en"],["en-US"],["en-GB"],None]:
                try:
                    entries = (
                        YouTubeTranscriptApi.get_transcript(vid, languages=lang)
                        if lang else
                        YouTubeTranscriptApi.list_transcripts(vid).find_transcript(["en"]).fetch()
                    )
                    if entries: return " ".join(e["text"] for e in entries)[:MAX_TRANSCRIPT_CHARS]
                except (NoTranscriptFound, KeyError): continue
        except (TranscriptsDisabled, VideoUnavailable): return None
        except Exception as e: log.warning(f"  [yellow]Transcript {vid}: {e}[/yellow]")
        return None

    def _fallback(self, vid: str, hint: str) -> Optional[str]:
        prompt = (f"YouTube https://youtu.be/{vid} has no transcript. "
                  f"Topic: {hint or 'engineering'}. Write B.Tech study notes.")
        for i in range(MAX_RETRIES):
            try:
                r = self.client.models.generate_content(
                    model=GEMINI_FLASH_MODEL, contents=prompt,
                    config=_cfg_no_tool(temperature=0.3, max_output_tokens=1024))
                t = _safe_text(r)
                return f"[Synthesized]\n\n{t}" if t else None
            except Exception:
                if i >= MAX_RETRIES-1: return None
                time.sleep(calc_backoff(i))
        return None

    def get(self, url: str, hint: str="") -> Optional[dict]:
        vid = self._vid_id(url)
        if not vid: return None
        t = self._fetch(vid)
        if t: return {"video_id":vid,"url":f"https://youtu.be/{vid}","text":t,"source":"transcript","wc":len(t.split())}
        fb = self._fallback(vid, hint)
        if fb: return {"video_id":vid,"url":f"https://youtu.be/{vid}","text":fb,"source":"fallback","wc":len(fb.split())}
        return None

    def search_and_fetch(self, queries: list[str], domain: str, topic: str, n: int=2) -> list[dict]:
        # BUG 1 FIX: was " ".join(...)[:3] — sliced STRING to 3 chars instead of 3 handles
        handles = [c["handle"] for c in PRIORITY_YOUTUBE_CHANNELS if c["domain"] in (domain,"all")]
        ch      = " ".join(handles[:3])
        prompt  = (
            "Find YouTube videos for:\n" + "\n".join(f"- {q}" for q in queries[:3])
            + f"\nPrefer channels: {ch}\nReturn JSON: [{{'url':'...','title':'...'}}] (max {n})"
        )
        urls: list[str] = []
        for i in range(MAX_RETRIES):
            try:
                r = self.client.models.generate_content(
                    model=GEMINI_FLASH_MODEL, contents=prompt,
                    config=_cfg_search(temperature=0.0, max_output_tokens=512))
                p = robust_json_parse(_safe_text(r))
                if isinstance(p, list):
                    urls = [str(e["url"]) for e in p if isinstance(e,dict) and e.get("url")]
                break
            except Exception as e:
                if i >= MAX_RETRIES-1: log.warning(f"  [yellow]YT search: {e}[/yellow]")
                time.sleep(calc_backoff(i))
        results = []
        for u in urls[:n]:
            r = self.get(u, topic)
            if r: results.append(r)
        return results


# ── NPTEL Scraper ──────────────────────────────────────────────────────────────
class NPTELScraper:
    def __init__(self, client): self.client = client

    def _headers(self, i: int) -> dict:
        return {"User-Agent": USER_AGENTS[i % len(USER_AGENTS)], "Accept":"text/html,*/*;q=0.8"}

    def _clean_html(self, html: str) -> str:
        if not _REQ_OK: return ""
        try: soup = BeautifulSoup(html, "lxml")
        except Exception: soup = BeautifulSoup(html, "html.parser")
        for tag in HTML_BOILERPLATE_TAGS:
            for el in soup.find_all(tag): el.decompose()
        main = soup.find("main") or soup.find("article") or soup.find("body")
        text = (main or soup).get_text(separator="\n", strip=True)
        return re.sub(r'\n{3,}','\n\n', re.sub(r'[ \t]{2,}',' ',text)).strip()

    def _fallback(self, topic: str, url: str, reason: str) -> Optional[str]:
        prompt = f"URL {url} unavailable ({reason}). Topic: {topic}. Write B.Tech engineering notes."
        for i in range(MAX_RETRIES):
            try:
                r = self.client.models.generate_content(
                    model=GEMINI_FLASH_MODEL, contents=prompt,
                    config=_cfg_no_tool(temperature=0.2, max_output_tokens=2048))
                t = _safe_text(r)
                return f"[Synthesized — {url}]\n\n{t}" if t else None
            except Exception:
                if i >= MAX_RETRIES-1: return None
                time.sleep(calc_backoff(i))
        return None

    def scrape(self, url: str, topic: str="") -> Optional[str]:
        if not _REQ_OK: return None
        for i in range(WEB_SCRAPE_MAX_RETRIES):
            try:
                resp = requests.get(url, headers=self._headers(i),
                                    timeout=WEB_SCRAPE_TIMEOUT_SEC, allow_redirects=True)
                if resp.status_code == 200:
                    c = self._clean_html(resp.text)
                    return c if len(c) > 100 else None
                if resp.status_code in (403,429):
                    return self._fallback(topic, url, f"HTTP {resp.status_code}")
                if resp.status_code in (404,410): return None
                if resp.status_code >= 500:
                    if i >= WEB_SCRAPE_MAX_RETRIES-1:
                        return self._fallback(topic, url, f"HTTP {resp.status_code}")
                    time.sleep(calc_backoff(i)); continue
            except requests.exceptions.SSLError:
                return self._fallback(topic, url, "SSL")
            except requests.exceptions.Timeout:
                if i >= WEB_SCRAPE_MAX_RETRIES-1:
                    return self._fallback(topic, url, "Timeout")
                time.sleep(calc_backoff(i))
            except Exception as e:
                return self._fallback(topic, url, str(e))
        return None

    def search_nptel(self, topic: str, domain: str, n: int=2) -> list[dict]:
        prompt = (f"Find NPTEL URLs for: {topic} ({domain}). "
                  f"Return JSON [{{'title':'...','url':'https://nptel.ac.in/...'}}] (max {n})")
        urls: list[dict] = []
        for i in range(MAX_RETRIES):
            try:
                r = self.client.models.generate_content(
                    model=GEMINI_FLASH_MODEL, contents=prompt,
                    config=_cfg_search(temperature=0.0, max_output_tokens=512))
                p = robust_json_parse(_safe_text(r))
                if isinstance(p, list):
                    urls = [e for e in p if isinstance(e,dict) and "nptel.ac.in" in e.get("url","")][:n]
                break
            except Exception as e:
                if i >= MAX_RETRIES-1: log.warning(f"  [yellow]NPTEL search: {e}[/yellow]")
                time.sleep(calc_backoff(i))
        results = []
        for e in urls:
            c = self.scrape(e.get("url",""), topic=topic)
            if c: results.append({"title":e.get("title",topic),"url":e.get("url",""),
                                   "content":c[:6000],"source":"nptel"})
        return results


# ── arXiv Searcher ─────────────────────────────────────────────────────────────
class ArxivSearcher:
    def search(self, query: str, domain: str, n: int=MAX_ARXIV_RESULTS) -> list[dict]:
        cats = ARXIV_CATEGORIES.get(domain, ["cs.LG"])
        cf   = "+OR+".join(f"cat:{urllib.parse.quote(c)}" for c in cats[:3])
        url  = (f"{ARXIV_API_URL}?search_query=all:{urllib.parse.quote(query)}"
                f"+AND+({cf})&max_results={n}&sortBy=relevance")
        for i in range(WEB_SCRAPE_MAX_RETRIES):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENTS[0]})
                with urllib.request.urlopen(req, timeout=WEB_SCRAPE_TIMEOUT_SEC) as resp:
                    root = ET.fromstring(resp.read())
                ns = {"a":"http://www.w3.org/2005/Atom"}
                papers = []
                for entry in root.findall("a:entry",ns):
                    ti  = entry.find("a:title",ns)
                    id_ = entry.find("a:id",ns)
                    su  = entry.find("a:summary",ns)
                    if not ti or not id_: continue
                    title    = " ".join((ti.text or "").split())
                    abs_url  = (id_.text or "").strip().replace("http://","https://")
                    abstract = " ".join((su.text or "").split())[:400] if su is not None else ""
                    if title and abs_url:
                        papers.append({"title":title,"url":abs_url,"abstract":abstract,"source":"arxiv"})
                return papers
            except urllib.error.HTTPError as e:
                if e.code in (403,404): return []
                if i >= WEB_SCRAPE_MAX_RETRIES-1: return []
                time.sleep(calc_backoff(i))
            except Exception as e:
                log.warning(f"  [yellow]arXiv: {e}[/yellow]"); return []
        return []


# ── Deep Web Researcher ────────────────────────────────────────────────────────
class DeepWebResearcher:
    def __init__(self, client):
        self.client = client
        self.yt     = YouTubeTranscriptFetcher(client)
        self.nptel  = NPTELScraper(client)
        self.arxiv  = ArxivSearcher()

    def _web_search(self, topic: str, queries: list[str], domain: str, n: int) -> list[dict]:
        # BUG 1 FIX: join handles[:3] not join(...)[:3]
        handles = [c["handle"] for c in PRIORITY_YOUTUBE_CHANNELS if c["domain"] in (domain,"all")]
        ch      = " ".join(handles[:3])
        qblock  = "\n".join(f"- {q}" for q in queries[:4])
        prompt  = (
            f"Find educational resources for:\n{qblock}\n"
            f"Prefer NPTEL, {ch}, MIT OCW, arXiv.\n"
            f"Return JSON (max {n}): [{{'title':'...','url':'https://...','snippet':'...',"
            f"'source_type':'nptel|youtube|arxiv|university'}}]"
        )
        for i in range(MAX_RETRIES):
            try:
                r = self.client.models.generate_content(
                    model=GEMINI_FLASH_MODEL, contents=prompt,
                    config=_cfg_search(temperature=0.0, max_output_tokens=2048))
                raw = _safe_text(r)
                if not raw:
                    if i >= MAX_RETRIES-1: return []
                    time.sleep(calc_backoff(i)); continue
                p = robust_json_parse(raw)
                if isinstance(p, dict):
                    for k in ("results","references","urls","items","data"):
                        if isinstance(p.get(k), list): p = p[k]; break
                if isinstance(p, list):
                    return [e for e in p if isinstance(e,dict) and e.get("title")
                            and str(e.get("url","")).startswith("http")][:n]
            except Exception as e:
                if i >= MAX_RETRIES-1: log.warning(f"  [yellow]Web search: {e}[/yellow]")
                time.sleep(calc_backoff(i))
        return []

    def research(self, topic: str, queries: list[str], domain: str, tier: str, n: int=6) -> dict:
        result = {"references":[],"yt_transcripts":[],"nptel":[],"arxiv":[],"combined":""}
        result["references"].extend(self._web_search(topic, queries, domain, n))

        if tier in ("Engineering","Senior"):
            try:
                yt = self.yt.search_and_fetch(queries[:2], domain, topic, n=2)
                result["yt_transcripts"].extend(yt)
                result["references"].extend([
                    {"title":f"YouTube: {topic}","url":y["url"],
                     "snippet":f"Transcript ({y['wc']} words)","source_type":"youtube"}
                    for y in yt
                ])
            except Exception as e: log.warning(f"  [yellow]YT: {e}[/yellow]")

        try:
            nr = self.nptel.search_nptel(topic, domain, n=1)
            result["nptel"].extend(nr)
            result["references"].extend([
                {"title":n2["title"],"url":n2["url"],
                 "snippet":n2["content"][:150]+"...","source_type":"nptel"}
                for n2 in nr
            ])
        except Exception as e: log.warning(f"  [yellow]NPTEL: {e}[/yellow]")

        if tier == "Engineering" and domain in ARXIV_CATEGORIES:
            try:
                ap = self.arxiv.search(topic, domain)
                result["arxiv"].extend(ap)
                result["references"].extend([
                    {"title":p["title"],"url":p["url"],
                     "snippet":p["abstract"],"source_type":"arxiv"}
                    for p in ap
                ])
            except Exception as e: log.warning(f"  [yellow]arXiv: {e}[/yellow]")

        parts = []
        for y in result["yt_transcripts"][:2]:
            parts.append(f"## YouTube: {topic}\nSource: {y['url']}\n\n{y['text'][:3000]}")
        for n2 in result["nptel"]:
            parts.append(f"## NPTEL: {n2['title']}\nSource: {n2['url']}\n\n{n2['content'][:3000]}")
        for p in result["arxiv"][:2]:
            parts.append(f"## Paper: {p['title']}\nSource: {p['url']}\n\nAbstract: {p['abstract']}")
        result["combined"]   = "\n\n---\n\n".join(parts)
        result["references"] = result["references"][:n]
        return result


# ── Google Classroom Client ────────────────────────────────────────────────────
class GoogleClassroomClient:
    def __init__(self):
        if not _GCP_OK:
            raise ImportError("pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        creds     = get_google_credentials(scopes=GOOGLE_SCOPES)
        self._cls = build("classroom","v1",credentials=creds)
        self._drv = build("drive","v3",credentials=creds)
        log.info("[green]Classroom + Drive clients ready.[/green]")

    def list_courses(self, active=True, archived=True) -> list[dict]:
        states  = (["ACTIVE"] if active else []) + (["ARCHIVED"] if archived else [])
        all_c: list[dict] = []
        for state in states:
            pt = None
            while True:
                try:
                    kw: dict = {"courseStates":[state],"pageSize":50}
                    if pt: kw["pageToken"] = pt
                    resp = self._cls.courses().list(**kw).execute()
                    for c in resp.get("courses",[]): c["_state"] = state
                    all_c.extend(resp.get("courses",[]))
                    pt = resp.get("nextPageToken")
                    if not pt: break
                except HttpError as e:
                    log.warning(f"[yellow]{state} list error {_http_status(e)}[/yellow]"); break
                except Exception as e:
                    log.error(f"[red]Course list: {e}[/red]"); break
            log.info(f"  [cyan]{state}[/cyan]: {sum(1 for c in all_c if c.get('_state')==state)}")
        log.info(f"[green]Total: {len(all_c)} courses[/green]")
        return all_c

    def list_materials(self, course_id: str, state: str="ACTIVE") -> list[dict]:
        mats: list[dict] = []
        is_arch = state == "ARCHIVED"
        for endpoint, key in [
            (lambda **kw: self._cls.courses().courseWorkMaterials().list(**kw), "courseWorkMaterial"),
            (lambda **kw: self._cls.courses().announcements().list(**kw),       "announcements"),
            (lambda **kw: self._cls.courses().courseWork().list(**kw),           "courseWork"),
        ]:
            pt, n = None, 0
            while n < MAX_PAGES:
                try:
                    kw = {"courseId":course_id,"pageSize":30}
                    if pt: kw["pageToken"] = pt
                    resp = endpoint(**kw).execute()
                    items = resp.get(key, [])
                    for item in items:
                        att = [m.get("driveFile",m.get("link",{}))
                               for m in item.get("materials",[])
                               if m.get("driveFile") or m.get("link")]
                        if att or key == "courseWorkMaterial":
                            item["_att"] = att; mats.append(item)
                    pt = resp.get("nextPageToken"); n += 1
                    if not pt: break
                except HttpError as e:
                    if _http_status(e) == 403 and is_arch:
                        log.warning(f"  [yellow]Archived {key} 403[/yellow]")
                    break
                except Exception: break
        return mats

    def file_meta(self, fid: str) -> Optional[dict]:
        try: return self._drv.files().get(fileId=fid, fields="id,name,mimeType,size").execute()
        except Exception: return None

    def download(self, fid: str, mime: str, dest: Path, fname: str) -> Optional[Path]:
        cat = classify_mime(mime)
        if cat == "skip": return None
        dest.mkdir(parents=True, exist_ok=True)
        if cat == "export":
            ei    = GOOGLE_MIME_EXPORT_MAP[mime]
            dname = safe_slug(Path(fname).stem) + ei["extension"]
            dp    = dest / dname
            req   = self._drv.files().export_media(fileId=fid, mimeType=ei["export_mime"])
        else:
            ext   = Path(fname).suffix.lower() or ".bin"
            dname = safe_slug(Path(fname).stem) + ext
            dp    = dest / dname
            req   = self._drv.files().get_media(fileId=fid)

        if dp.exists() and dp.stat().st_size > 0:
            log.info(f"  [green]CACHED[/green] {dp.name}"); return dp

        for i in range(MAX_RETRIES):
            try:
                with open(dp,"wb") as f:
                    dl = MediaIoBaseDownload(f, req, chunksize=DL_CHUNK)
                    done = False
                    while not done: _, done = dl.next_chunk()
                if not dp.exists() or dp.stat().st_size == 0:
                    dp.unlink(missing_ok=True); return None
                log.info(f"  [green]✓[/green] {dp.name} ({dp.stat().st_size//1024} KB)")
                return dp
            except HttpError as e:
                dp.unlink(missing_ok=True)
                s = _http_status(e)
                if s in (403,404): log.warning(f"  [yellow]{s} {fname}[/yellow]"); return None
                if i >= MAX_RETRIES-1: return None
                time.sleep(calc_backoff(i))
            except Exception as e:
                dp.unlink(missing_ok=True)
                if i >= MAX_RETRIES-1: log.error(f"  [red]Download {fname}: {e}[/red]"); return None
                time.sleep(calc_backoff(i))
        return None

    def ingest_course(self, course: dict, dedup_engine, semester: int=0) -> list[Path]:
        cid    = course["id"]
        cname  = course.get("name","Unknown")
        ccode  = course.get("courseCode","")
        state  = course.get("_state","ACTIVE")
        tier   = "Engineering"   # RBU B.Tech Classroom = always Engineering

        rsem = semester
        if rsem == 0:
            m = re.search(r'\bsem(?:ester)?\s*(\d)', cname, re.IGNORECASE)
            if m: rsem = int(m.group(1))

        is_arch = state == "ARCHIVED"
        label   = "[ARCHIVED]" if is_arch else "[ACTIVE]"
        color   = "cyan" if is_arch else "green"
        log.info(f"  [{color}]{label}[/{color}] [cyan]{cname}[/cyan] [Engineering | Sem {rsem}]")

        mats = self.list_materials(cid, state=state)
        if not mats: log.info(f"  [dim]No materials: {cname}[/dim]"); return []

        dest_dir = CLASSROOM_DL_DIR / (safe_slug(cname) + ("_archived" if is_arch else ""))
        staged: list[Path] = []

        for mat in mats:
            for att in mat.get("_att",[]):
                df  = att if "id" in att else att.get("driveFile",{})
                fid = df.get("id","")
                ttl = df.get("title", df.get("name","untitled"))
                if not fid: continue
                meta = self.file_meta(fid)
                if not meta: continue
                dl = self.download(fid, meta.get("mimeType",""), dest_dir, meta.get("name",ttl))
                if dl is None: continue
                sp = dedup_engine.process_incoming_file(
                    path=dl, subject=cname, tier=tier, semester=rsem, course_code=ccode)
                if sp: staged.append(sp)

        log.info(f"  [green]Done:[/green] {cname} — {len(staged)} staged")
        return staged


# ── IngestionResearcher ────────────────────────────────────────────────────────
class IngestionResearcher:
    def __init__(self, gemini_client, dedup_engine=None):
        self.client   = gemini_client
        self.dedup    = dedup_engine
        self.research = DeepWebResearcher(gemini_client)

    def _save_manifest(self, manifest: list[dict], path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(manifest,indent=2,ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as e:
            log.error(f"[red]Manifest save failed: {e}[/red]")
        finally:
            tmp.unlink(missing_ok=True)

    def run_classroom(self, sem=0, manifest_path=MASTER_MANIFEST, archived=True) -> dict:
        stats = {"courses":0,"active":0,"archived":0,"files":0}
        if not _GCP_OK: log.error("[red]google-api-python-client not installed[/red]"); return stats
        if not self.dedup: log.error("[red]DedupEngine required for classroom ingestion[/red]"); return stats
        try:   gcs = GoogleClassroomClient()
        except Exception as e: log.error(f"[red]Classroom init: {e}[/red]"); return stats

        courses = gcs.list_courses(active=True, archived=archived)
        if not courses: log.warning("[yellow]No courses found[/yellow]"); return stats

        manifest: list[dict] = []
        if manifest_path.exists():
            try:
                m = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(m, list): manifest = m
            except Exception: pass

        lookup: dict[str,list[int]] = {}
        for i, e in enumerate(manifest):
            if not isinstance(e, dict): continue
            for f in ("subject","title"):
                s = safe_slug(str(e.get(f,"")))
                if s: lookup.setdefault(s,[]).append(i)

        dirty = False
        for c in courses:
            cname = c.get("name","")
            state = c.get("_state","ACTIVE")
            if sem > 0:
                m2 = re.search(r'\bsem(?:ester)?\s*(\d)', cname, re.IGNORECASE)
                cs = int(m2.group(1)) if m2 else 0
                if cs != 0 and cs != sem: continue
            try:
                staged = gcs.ingest_course(c, self.dedup, semester=sem)
                stats["files"]  += len(staged)
                stats["courses"] += 1
                stats["active" if state=="ACTIVE" else "archived"] += 1
                if staged and manifest:
                    seen: set[int] = set()
                    for key in {safe_slug(cname), safe_slug(c.get("courseCode",""))}:
                        for idx in lookup.get(key,[]):
                            if idx >= len(manifest) or idx in seen: continue
                            seen.add(idx)
                            exist = set(manifest[idx].get("source_files",[]))
                            new   = [str(p) for p in staged if str(p) not in exist]
                            if new:
                                manifest[idx].setdefault("source_files",[]).extend(new)
                                dirty = True
            except Exception as e:
                log.error(f"  [red]Ingestion error {cname}: {e}[/red]")

        if dirty: self._save_manifest(manifest, manifest_path)

        from rich.panel import Panel
        console.print(Panel(
            f"Courses : {stats['courses']} (active:{stats['active']} archived:{stats['archived']})\n"
            f"Files   : [green]{stats['files']}[/green]",
            title="Classroom Ingestion Complete", border_style="green"))
        return stats

    def enrich_module(self, module: dict, force=False) -> dict:
        if not force and str(module.get("content","")).strip():
            return module

        title  = module.get("title","")
        subj   = module.get("subject","")
        domain = module.get("domain","00_Uncategorised")
        tier   = module.get("tier","Engineering")
        wq     = module.get("web_queries",[])

        # BUG 5 FIX: don't repeat title+subject if they're the same string
        if not wq:
            if title.lower().strip() == subj.lower().strip():
                wq = [f"{title} lecture notes", f"{title} engineering tutorial"]
            else:
                wq = [f"{title} {subj} B.Tech notes", f"{title} NPTEL"]

        local = ""
        for sf in module.get("source_files",[]):
            p = _safe_path(sf)
            if p:
                t = extract_text(p)
                if t.strip(): local += f"\n=== {p.name} ===\n{t.strip()}"

        pyqs = extract_pyqs(local) if local else []
        res  = self.research.research(
            topic=f"{title} — {subj}", queries=wq, domain=domain, tier=tier, n=6)

        parts = []
        if local:            parts.append(f"## LOCAL NOTES\n\n{local[:12000]}")
        if pyqs:             parts.append("## PYQs\n\n" + "\n".join(f"- {q}" for q in pyqs))
        if res["combined"]:  parts.append(f"## RESEARCH\n\n{res['combined'][:8000]}")
        if not parts:        parts.append(f"## {title}\n\nSubject: {subj}\nDomain: {domain}")

        module["content"]    = "\n\n".join(parts)
        module["references"] = res["references"]
        module["pyqs"]       = pyqs
        return module

    def run_local_enrichment(
        self, manifest_path=MASTER_MANIFEST, tier_filter=None, force=False
    ) -> list[dict]:
        if not manifest_path.exists():
            log.error(f"[red]Manifest not found: {manifest_path}[/red]"); return []
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, list):
                log.error("[red]Manifest not a list[/red]"); return []
        except Exception as e:
            log.error(f"[red]Cannot read manifest: {e}[/red]"); return []

        # BUG 4 FIX: case-insensitive tier filter comparison
        if tier_filter and tier_filter.lower() != "all":
            target = [m for m in manifest if m.get("tier","").lower() == tier_filter.lower()]
        else:
            target = manifest

        id_idx = {m["id"]:i for i,m in enumerate(manifest) if isinstance(m,dict) and m.get("id")}

        ok = err = 0
        log.info(f"[bold]Phase 1: Enrichment[/bold] — {len(target)} modules")

        for mod in target:
            if not isinstance(mod,dict) or not mod.get("id"): err += 1; continue
            try:
                updated = self.enrich_module(mod, force=force)
                idx     = id_idx.get(updated["id"])
                if idx is not None: manifest[idx] = updated
                ok += 1
            except Exception as e:
                log.error(f"  [red]Enrich {mod.get('id')}: {e}[/red]"); err += 1; continue

            # BUG 2 FIX: batch save every N modules instead of every 1
            if ok % _MANIFEST_SAVE_INTERVAL == 0:
                self._save_manifest(manifest, manifest_path)
                log.info(f"  [dim]Saved ({ok} done)[/dim]")

        # Final save
        self._save_manifest(manifest, manifest_path)

        from rich.panel import Panel
        console.print(Panel(
            f"Enriched: [green]{ok}[/green]\nFailed: [red]{err}[/red]",
            title="Phase 1 Complete",
            border_style="green" if not err else "yellow"))
        return manifest


# ── CLI ────────────────────────────────────────────────────────────────────────
def main():
    from pve.safety import require_legacy_paid_execution
    require_legacy_paid_execution()
    p = argparse.ArgumentParser(
        prog="ingestion_researcher.py",
        description="""Phase 1 Ingestion.
COMMANDS:
  --source classroom   Download all Google Classroom materials + dedup
  --source local       Enrich manifest modules with web research
  --source all         Both steps in sequence

EXAMPLES:
  python3 ingestion_researcher.py --source classroom
  python3 ingestion_researcher.py --source local --tier Engineering
  python3 ingestion_researcher.py --source local --force
  python3 ingestion_researcher.py --source all --sem 3
  python3 ingestion_researcher.py --research-only "Forward Kinematics" --domain 05_Robotics
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--source",       choices=["classroom","local","all"], default="local")
    p.add_argument("--sem",          type=int, default=0,       help="Filter by semester (0=all)")
    p.add_argument("--tier",         default="all",             help="Filter tier for enrichment")
    p.add_argument("--force",        action="store_true",       help="Re-enrich already-enriched modules")
    p.add_argument("--manifest",     type=Path, default=MASTER_MANIFEST)
    p.add_argument("--no-archived",  action="store_true",       help="Skip archived Classroom courses")
    p.add_argument("--research-only",metavar="TOPIC",           help="Test web research for one topic")
    p.add_argument("--domain",       default="05_Robotics",     help="Domain for --research-only")
    args = p.parse_args()

    client = google_genai.Client(api_key=get_env("GEMINI_API_KEY"))

    if args.research_only:
        r = DeepWebResearcher(client).research(
            topic=args.research_only,
            queries=[f"{args.research_only} NPTEL", f"{args.research_only} notes"],
            domain=args.domain, tier="Engineering")
        for ref in r["references"]:
            console.print(f"  [{ref.get('source_type','web')}] {ref['title'][:60]} → {ref['url']}")
        return

    dedup = None
    if args.source in ("classroom","all"):
        try:
            from dedup_engine import DedupEngine
            dedup = DedupEngine(gemini_client=client)
        except Exception as e:
            log.error(f"[red]DedupEngine: {e}[/red]")

    researcher = IngestionResearcher(client, dedup_engine=dedup)

    if args.source in ("classroom","all") and dedup is not None:
        researcher.run_classroom(
            sem=args.sem,
            manifest_path=args.manifest,
            archived=not args.no_archived,
        )

    if args.source in ("local","all"):
        researcher.run_local_enrichment(
            manifest_path=args.manifest,
            tier_filter=args.tier if args.tier != "all" else None,
            force=args.force,
        )

if __name__ == "__main__":
    main()
