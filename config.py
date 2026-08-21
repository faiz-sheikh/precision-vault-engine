#!/usr/bin/env python3
"""config.py — Precision Vault Engine v2 — Foundation Layer

CHANGE LOG (this version):
  Added 12_NaturalSciences and 13_MechanicalEngineering domains — these were
  missing from DOMAIN_KEYWORDS/DOMAIN_DISPLAY_NAMES/infer_tier(), causing
  Chemistry/Biology/Mechanical-Engineering content to fall into
  00_Uncategorised or get mis-tiered. This was instructed verbally in an
  earlier session but never actually baked into the file — fixed now so
  curriculum_mapper.py and all downstream phases agree on the same domain set.
"""
import os
import re
import json
import pickle
import logging
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs): return False
try:
    from rich.console import Console
    from rich.logging import RichHandler
except ImportError:
    class Console:
        def print(self, *values, **kwargs): print(*values)
    RichHandler = None

_SELF_DIR = Path(__file__).parent.resolve()
for _env in [_SELF_DIR/"config"/".env", _SELF_DIR/".env", Path(".env")]:
    if _env.exists():
        load_dotenv(_env)
        break

_handlers = ([RichHandler(rich_tracebacks=True, markup=True, show_path=False)]
             if RichHandler is not None else None)
logging.basicConfig(level=logging.INFO, format="%(message)s", datefmt="[%X]", handlers=_handlers)
log     = logging.getLogger("vault_engine")
console = Console()

# ── Paths ─────────────────────────────────────────────────────────────────────
VAULT_ENGINE_ROOT = _SELF_DIR
CONFIG_DIR        = VAULT_ENGINE_ROOT / "config"
VAULT_ROOT        = Path(os.getenv("VAULT_ROOT", str(VAULT_ENGINE_ROOT))).expanduser().resolve()

RAW_NOTES_DIR    = VAULT_ROOT / "raw_notes"
STAGING_DIR      = VAULT_ROOT / "vault_staging"
VAULT_DIR        = VAULT_ROOT / "vault"
AUDIO_DIR        = VAULT_ROOT / "_audio"
VIDEO_DIR        = VAULT_ROOT / "_video_output"
BATCH_JSONL_DIR  = VAULT_ROOT / "_batch_jsonl"
QUARANTINE_DIR   = VAULT_ROOT / "_quarantine"
HANDWRITING_DIR  = VAULT_ROOT / "_handwriting_extracts"
CURRICULUM_DIR   = CONFIG_DIR / "curriculum"
CLASSROOM_DL_DIR = VAULT_ROOT / "_classroom_downloads"
MASTER_MANIFEST  = CONFIG_DIR / "master_manifest.json"
VAULT_STATE      = VAULT_ROOT / "vault_state.json"
HASH_REGISTRY_PATH      = CONFIG_DIR / "hash_registry.json"
EMBEDDING_REGISTRY_PATH = CONFIG_DIR / "embedding_registry.json"
CREDENTIALS_PATH = _SELF_DIR / os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
TOKEN_PATH       = _SELF_DIR / os.getenv("GOOGLE_TOKEN_PATH", "token.pkl")


def ensure_vault_dirs() -> None:
    dirs = [
        RAW_NOTES_DIR, STAGING_DIR, VAULT_DIR, AUDIO_DIR,
        VIDEO_DIR/"manim", VIDEO_DIR/"remotion", VIDEO_DIR/"final",
        BATCH_JSONL_DIR, QUARANTINE_DIR, HANDWRITING_DIR, CURRICULUM_DIR,
        CLASSROOM_DL_DIR, VAULT_ROOT/"_assessment"/"viva",
        VAULT_ROOT/"_assessment"/"soft_skills", VAULT_ROOT/"_assessment"/"exam_prep",
        VAULT_ROOT/"_compiled", VAULT_ROOT/"_raw_fallback", CONFIG_DIR,
    ]
    for d in dirs:
        try: d.mkdir(parents=True, exist_ok=True)
        except OSError as e: log.error(f"[red]Cannot create {d}: {e}[/red]")
    log.info(f"[green]Vault dirs verified:[/green] {VAULT_ROOT}")

# ── Windows Bridge ─────────────────────────────────────────────────────────────
WINDOWS_DROP_ZONES: list[str] = [
    r"C:\Users\Faiz\Downloads\Telegram Desktop",
    r"C:\Users\Faiz\Documents\WhatsApp Materials",
    r"C:\Users\Faiz\pipeline_staging",
]
WINDOWS_MOUNT_BASE: str        = os.getenv("WINDOWS_MOUNT_BASE", "")
ALLOWED_BRIDGE_EXTENSIONS      = frozenset({".pdf",".pptx",".docx",".png",".jpg",".jpeg",".heic",".txt",".md"})
BRIDGE_POLL_INTERVAL_SEC: int  = 30

# ── Research ───────────────────────────────────────────────────────────────────
PRIORITY_YOUTUBE_CHANNELS: list[dict] = [
    {"name":"3Blue1Brown",       "handle":"@3blue1brown",       "domain":"01_Math"},
    {"name":"NPTEL",             "handle":"@nptel",             "domain":"all"},
    {"name":"MIT OCW",           "handle":"@mitocw",            "domain":"all"},
    {"name":"Brian Douglas",     "handle":"@BrianBDouglas",     "domain":"07_Control"},
    {"name":"Sentdex",           "handle":"@sentdex",           "domain":"06_AI_ML"},
    {"name":"Two Minute Papers", "handle":"@TwoMinutePapers",   "domain":"06_AI_ML"},
    {"name":"Yannic Kilcher",    "handle":"@YannicKilcher",     "domain":"06_AI_ML"},
    {"name":"Articulated Rob.",  "handle":"@ArticulatedRobotics","domain":"05_Robotics"},
    {"name":"The Construct",     "handle":"@TheConstructSim",   "domain":"05_Robotics"},
    {"name":"Khan Academy",      "handle":"@khanacademy",       "domain":"01_Math"},
    {"name":"StatQuest",         "handle":"@statquest",         "domain":"06_AI_ML"},
]
ARXIV_CATEGORIES: dict[str,list[str]] = {
    "01_Math":["math.NA","math.OC","math.ST"],
    "02_Physics":["physics.app-ph","cond-mat.mtrl-sci"],
    "03_Programming":["cs.DS","cs.PL","cs.SE"],
    "04_Electronics":["eess.SP","cs.ET","eess.SY"],
    "05_Robotics":["cs.RO","cs.SY","eess.SY"],
    "06_AI_ML":["cs.LG","cs.AI","stat.ML","cs.CV","cs.CL"],
    "07_Control":["eess.SY","cs.SY","math.OC"],
    "08_Design":["cs.GR","cs.HC"],
    "09_Defence":["cs.RO","eess.SY","cs.AI"],
    "10_HSSM":["cs.HC","cs.CY"],
    "11_Projects":["cs.SE","cs.RO"],
    "12_NaturalSciences":["q-bio.BM","q-bio.MN","physics.chem-ph"],
    "13_MechanicalEngineering":["physics.flu-dyn","physics.app-ph","cs.RO"],
    "00_Uncategorised":["cs.LG","cs.RO"],
}
NPTEL_BASE_URL   = "https://nptel.ac.in"
ARXIV_API_URL    = "https://export.arxiv.org/api/query"
WEB_SCRAPE_TIMEOUT_SEC = 15
WEB_SCRAPE_MAX_RETRIES = 3
MAX_TRANSCRIPT_CHARS   = 12_000
MAX_ARXIV_RESULTS      = 3
USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
]
HTML_BOILERPLATE_TAGS = ["script","style","nav","header","footer","aside","noscript","iframe"]

# ── Google OAuth ───────────────────────────────────────────────────────────────
GOOGLE_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
]

def get_google_credentials(scopes=None, credentials_path=None, token_path=None):
    if scopes is None:           scopes           = GOOGLE_SCOPES
    if credentials_path is None: credentials_path = CREDENTIALS_PATH
    if token_path is None:       token_path       = TOKEN_PATH
    if not credentials_path.exists():
        raise FileNotFoundError(f"credentials.json not found: {credentials_path}")
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    creds = None
    if token_path.exists():
        try:
            with open(token_path,"rb") as f: loaded = pickle.load(f)
            if isinstance(loaded, Credentials) and hasattr(loaded,"token"): creds = loaded
            else: token_path.unlink(missing_ok=True)
        except Exception: token_path.unlink(missing_ok=True)
    if creds and creds.expired and creds.refresh_token:
        try: creds.refresh(Request())
        except Exception: creds = None; token_path.unlink(missing_ok=True)
    elif creds and not creds.valid:
        creds = None; token_path.unlink(missing_ok=True)
    if not creds or not creds.valid:
        flow  = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
        try:    creds = flow.run_local_server(port=0, open_browser=True)
        except Exception: creds = flow.run_console()
        with open(token_path,"wb") as f: pickle.dump(creds, f)
        token_path.chmod(0o600)
    return creds

# ── MIME Maps ──────────────────────────────────────────────────────────────────
GOOGLE_MIME_EXPORT_MAP: dict[str,dict] = {
    "application/vnd.google-apps.document":     {"export_mime":"application/pdf","extension":".pdf"},
    "application/vnd.google-apps.presentation": {"export_mime":"application/vnd.openxmlformats-officedocument.presentationml.presentation","extension":".pptx"},
    "application/vnd.google-apps.spreadsheet":  {"export_mime":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","extension":".xlsx"},
    "application/vnd.google-apps.drawing":      {"export_mime":"image/png","extension":".png"},
}
DIRECT_DOWNLOAD_MIMES = frozenset({
    "application/pdf","application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain","text/markdown","image/jpeg","image/jpg","image/png",
})
SKIP_MIMES = frozenset({
    "application/vnd.google-apps.site","application/vnd.google-apps.script",
    "application/vnd.google-apps.folder","video/mp4","audio/mpeg","application/zip",
})

def classify_mime(mime: str) -> str:
    if not mime or mime in SKIP_MIMES: return "skip"
    if mime in GOOGLE_MIME_EXPORT_MAP:  return "export"
    if mime in DIRECT_DOWNLOAD_MIMES:   return "download"
    return "unknown"

# ── Model Routing ──────────────────────────────────────────────────────────────
GEMINI_FLASH_MODEL = "gemini-2.5-flash"
GEMINI_EMBED_MODEL = "models/gemini-embedding-2"
CLAUDE_HAIKU   = "claude-haiku-4-5-20251001"
CLAUDE_SONNET  = "claude-sonnet-4-6"

TIER_MODEL_MAP: dict[str,dict] = {
    "Junior": {
        "synthesis_model": GEMINI_FLASH_MODEL,
        "use_batch": False,
        "est_cost_usd": 0.00,
    },
    "Senior": {
        "synthesis_model": CLAUDE_HAIKU,
        "use_batch": True,
        "est_cost_usd": 2.52,
    },
    "Engineering": {
        "synthesis_model": CLAUDE_SONNET,
        "use_batch": True,
        "est_cost_usd": 8.75,
    },
}

# ── Retry ──────────────────────────────────────────────────────────────────────
MAX_RETRIES      = 3
BASE_BACKOFF_SEC = 2.0
MAX_BACKOFF_SEC  = 60.0

def calc_backoff(attempt: int) -> float:
    return min(BASE_BACKOFF_SEC ** (attempt + 1), MAX_BACKOFF_SEC)

# ── Domain Taxonomy ────────────────────────────────────────────────────────────
# FIX: 12_NaturalSciences and 13_MechanicalEngineering added — previously
# missing, causing Chemistry/Biology/Mechanical content to fall into
# 00_Uncategorised (confirmed bug from manifest audit).
DOMAIN_KEYWORDS: dict[str,list[str]] = {
    "01_Math":["calculus","algebra","statistics","probability","differential","matrix","linear","integral","fourier","laplace","markov","stochastic"],
    "02_Physics":["physics","laser","ultrasonic","sensor","fluid","electromagnetic","optics","thermodynamics","quantum","semiconductor"],
    "03_Programming":["python","c programming","dsa","data structure","oops","algorithm","programming","sorting","linked list","binary tree","web development","database","networking","cybersecurity","operating system","compiler"],
    "04_Electronics":["electrical","digital logic","microcontroller","raspberry","plc","iot","arduino","arm","gpio","mechatronics","actuator","signal","embedded","fpga"],
    "05_Robotics":["robotics","manipulator","kinematics","end effector","ros","mobile robot","navigation","trajectory","denavit","jacobian","slam","path planning"],
    "06_AI_ML":["artificial intelligence","machine learning","deep learning","reinforcement","neural network","cnn","rnn","transformer","computer vision","nlp","pytorch","tensorflow","yolo","q-learning"],
    "07_Control":["control system","pid","transfer function","bode","hydraulic","pneumatic","state space","nyquist","root locus","feedback","stability","servo","routh"],
    "08_Design":["design thinking","engineering graphics","fabrication","cad","ansys","solidworks","additive manufacturing","3d printing","fea"],
    "09_Defence":["defence","warfare","weapon","platform","mdm","defense","missile","drdo","military","armament","ballistic"],
    "10_HSSM":["communication","english","human values","business","environmental","sustainability","ethics","professional"],
    "11_Projects":["project","internship","viva","research methodology","capstone","dissertation"],
    "12_NaturalSciences":["biology","chemistry","biochemistry","botany","zoology","ecology","genetics","life science","organic chemistry","inorganic chemistry","environmental science","microbiology","physiology","cell biology","acids","bases"],
    "13_MechanicalEngineering":["mechanisms","theory of machines","kinematics of machine","elasticity","fluid mechanics","heat transfer","manufacturing process","strength of material","machine design","turbomachinery","industrial engineering","thermodynamics of machines"],
    "00_Uncategorised":[],
}
DOMAIN_DISPLAY_NAMES: dict[str,str] = {
    "01_Math":"Mathematics","02_Physics":"Physics & Materials","03_Programming":"Programming",
    "04_Electronics":"Electronics & Hardware","05_Robotics":"Robotics Core",
    "06_AI_ML":"AI / ML / DL","07_Control":"Control Systems","08_Design":"Design & Fabrication",
    "09_Defence":"Defence — MDM","10_HSSM":"Communication & Values","11_Projects":"Projects",
    "12_NaturalSciences":"Natural Sciences","13_MechanicalEngineering":"Mechanical Engineering",
}

# ── Utility Functions ──────────────────────────────────────────────────────────
def get_env(key: str, required: bool = True, default: str = "") -> str:
    v = os.getenv(key, default)
    if required and not v:
        raise EnvironmentError(f"Missing env var: {key} — add to config/.env")
    return v

def classify_file(filename_or_path: str) -> str:
    low = str(filename_or_path).lower().replace("\\","/")
    for domain, kws in DOMAIN_KEYWORDS.items():
        if any(kw in low for kw in kws): return domain
    return "00_Uncategorised"

def infer_tier(title: str, domain: str, grade: Optional[int] = None) -> str:
    """
    Map content to synthesis tier.
    grade>=13 → Engineering (B.Tech sem N maps to grade 13+N).
    grade<=10 → Junior. grade 11-12 → Senior.
    No grade (domain fallback): strictly-engineering domains → Engineering;
    school-level domains (incl. 12_NaturalSciences for Chem/Bio) → Senior;
    everything else defaults Engineering (RBU B.Tech context).
    """
    if grade is not None:
        if grade <= 10:  return "Junior"
        if grade <= 12:  return "Senior"
        return "Engineering"
    eng = {"05_Robotics","06_AI_ML","07_Control","09_Defence","08_Design",
           "11_Projects","04_Electronics","13_MechanicalEngineering"}
    if domain in eng: return "Engineering"
    sr  = {"01_Math","02_Physics","03_Programming","10_HSSM","12_NaturalSciences"}
    if domain in sr:  return "Senior"
    return "Engineering"

def normalize_grade_or_sem(raw: str) -> str:
    if not raw or not raw.strip(): return ""
    text = raw.strip().lower()
    for pat in [r'(?:semester|sem)[\s\-_]*(\d{1,2})',r's(\d{1,2})(?:\b|$)']:
        m = re.search(pat, text)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 12: return f"Sem {n}"
    for pat in [r'(?:class|grade|std)[\s\-_]*(\d{1,2})']:
        m = re.search(pat, text)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 12: return f"Class {n}"
    return raw.strip()

def safe_slug(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_-]','_', name.strip()).strip('_')[:80]

def strip_json_fences(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```json"): s = s[7:]
    elif s.startswith("```"):   s = s[3:]
    if s.endswith("```"):       s = s[:-3]
    return s.strip()

def robust_json_parse(raw: str):
    if not raw: return None
    clean = strip_json_fences(raw)
    try: return json.loads(clean)
    except (json.JSONDecodeError, ValueError): pass
    for start_ch, end_ch in [("[","]"),("{","}")]:
        si = clean.find(start_ch)
        ei = clean.rfind(end_ch)
        if si != -1 and ei > si:
            try: return json.loads(clean[si:ei+1])
            except (json.JSONDecodeError, ValueError): pass
    return None

def safe_gemini_text(response) -> str:
    try:
        t = response.text
        if t and isinstance(t, str): return t.strip()
    except (ValueError, AttributeError): pass
    try:
        for cand in (getattr(response,"candidates",None) or []):
            content = getattr(cand,"content",None)
            if not content: continue
            pieces: list[str] = []
            for part in (getattr(content,"parts",None) or []):
                if getattr(part,"function_call",None) is not None:         continue
                if getattr(part,"executable_code",None) is not None:       continue
                if getattr(part,"code_execution_result",None) is not None: continue
                pt = getattr(part,"text",None)
                if pt and isinstance(pt,str) and pt.strip(): pieces.append(pt)
            if pieces: return "".join(pieces).strip()
    except Exception: pass
    return ""

def wsl_to_linux_path(raw: str) -> "Path":
    """
    BUG FIX (found via adversarial testing): empty input previously fell
    through to `Path.home() / ""`, which resolves to the home directory
    itself. If WINDOWS_DROP_ZONES or WINDOWS_MOUNT_BASE were ever misconfigured
    to an empty string, windows_bridge.py's sweep() would silently scan the
    user's entire home directory. Now raises clearly instead of resolving
    to a plausible-looking but unintended path.
    """
    if not raw or not raw.strip():
        raise ValueError("wsl_to_linux_path() received an empty path — check WINDOWS_DROP_ZONES/WINDOWS_MOUNT_BASE config")
    raw = raw.strip().strip('"').strip("'")
    if raw.startswith("/") or raw.startswith("~"): return Path(raw).expanduser().resolve()
    if len(raw) >= 2 and raw[1] == ":":
        drive = raw[0].lower(); rest = raw[2:].replace("\\","/").lstrip("/")
        return Path(f"/mnt/{drive}/{rest}").resolve()
    return (Path.home() / raw).resolve()
