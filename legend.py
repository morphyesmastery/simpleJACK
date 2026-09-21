"""LAYA-POWERED LEGEND BETA — the resolver, bolted into legend.py's shape.

DESIGN (operator order, 2026-09-19):
  Stage 1: Laya classifies the request against the REAL LEGEND verbs (two-stage:
           it sees all 216 verbs' descriptions as typed criteria; token budget
           means we present every verb — Laya scores each option at its own
           marker token, options defined at REQUEST time, no retraining).
           Returns verb + probabilities + confidence, ~1s CPU.
  Stage 2: The command is built from the canonical line in LEGEND.md and the
           EXISTING placeholder extractors (fill_command logic below) —
           deterministic, no generation, nothing to hallucinate.
  Fallback: if Laya can't load (no model, no RAM), falls back to the ORIGINAL
           Ollama 'legend' translator path — zero behavior lost.

Same public surface as legend.py: SKILLS, resolve(), describe_for_model(), reload().
"""
import io, json, os, re, sys, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

SIMPLEJACK_ROOT = Path(__file__).resolve().parent
SKILLS_DIR = SIMPLEJACK_ROOT / "SKILLS"
# SOURCE OF TRUTH = the operator-named original (legendpause.md).
# LEGEND.md is the MIRROR simplejack's other readers expect — kept in sync here.
LEGEND_FILE = SKILLS_DIR / "legendpause.md"
LEGEND_MIRROR = SKILLS_DIR / "LEGEND.md"


def _mirror_legend_md() -> None:
    """Keep LEGEND.md identical to legendpause.md (atomic replace)."""
    try:
        if not LEGEND_FILE.exists():
            return
        src = LEGEND_FILE.read_bytes()
        if not LEGEND_MIRROR.exists() or LEGEND_MIRROR.read_bytes() != src:
            tmp = LEGEND_MIRROR.with_suffix(".md.tmp")
            tmp.write_bytes(src)
            tmp.replace(LEGEND_MIRROR)
            _log("mirrored legendpause.md -> LEGEND.md")
    except Exception as e:
        _log(f"mirror failed: {e}")

# ── LAYA CONFIG ──────────────────────────────────────────────
_LAYA_MODEL_ID = "convaiinnovations/laya"
_LAYA_HF_HOME = SIMPLEJACK_ROOT / "modelhub" / "hf_laya_cache"
_LAYA_MIN_CONFIDENCE = 0.55   # below this = "no skill matched" (chat wins)
_LAYA_LOG = SIMPLEJACK_ROOT / "STACK" / "laya_router.log"

_laya_agent = None            # lazy singleton
_laya_failed = False          # if True, never retry (fall back to Ollama path)


def _log(msg: str) -> None:
    try:
        _LAYA_LOG.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(_LAYA_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {msg}\n")
    except Exception:
        pass


def _get_laya():
    """Load Laya once. CPU. Returns agent or None (then caller falls back)."""
    global _laya_agent, _laya_failed
    if _laya_agent is not None:
        return _laya_agent
    if _laya_failed:
        return None
    try:
        os.environ["PYTHONPATH"] = ""
        os.environ.setdefault("HF_HOME", str(_LAYA_HF_HOME))
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        import laya as _laya_mod
        t0 = time.perf_counter()
        _laya_agent = _laya_mod.load(_LAYA_MODEL_ID, device="cpu")
        _log(f"laya loaded in {time.perf_counter()-t0:.1f}s")
        return _laya_agent
    except Exception as e:
        _laya_failed = True
        _log(f"laya load FAILED: {e}")
        return None


# ── THE SHAPE OF A SKILL (same as legend.py) ─────────────────
@dataclass
class Skill:
    verb: str
    desc: str
    command: str
    aliases: List[str] = field(default_factory=list)


def _load_legend_md() -> List[Skill]:
    skills: List[Skill] = []
    if not LEGEND_FILE.exists():
        return skills
    for line in LEGEND_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(" :: ", 2)
        if len(parts) != 3:
            continue
        verb = parts[0].strip()
        desc = parts[1].strip()
        command = parts[2].strip()
        aliases = []
        if "_" in verb:
            aliases.append(verb.replace("_", "-"))
            aliases.append(verb.replace("_", " "))
        skills.append(Skill(verb=verb, desc=desc, command=command, aliases=aliases))
    return skills


SKILLS: List[Skill] = _load_legend_md()
_mirror_legend_md()


# ── STAGE 1: LAYA CLASSIFIER ─────────────────────────────────
# ── STAGE 1: CATEGORY ROUTER (Trent's two-decision design) ───────────────
# Decision 1: Laya picks a CATEGORY (8 buckets + chat — tiny option set).
# Decision 2: Laya picks the VERB within that category (small, same-domain
# set — easier decision, better accuracy than one giant 216-way call).
#
# PRODUCTION VERBS — hand-placed by the operator's stack review 2026-09-19.
# The ~150 _underscore plumbing verbs are NOT classified (they are internal
# one-offs); they stay reachable by exact name via the explicit-verb gate.
CATEGORIES: Dict[str, List[str]] = {
    "media": [
        "studio", "clearline", "demo_voiceover", "demo_record", "demo_pipeline",
        "demo_watch", "narrate", "vibevoice", "vibe_clone", "audio_player",
        "voice_test", "clone", "queue_builds", "alberta_poster", "morphy",
    ],
    "web": [
        "browse", "post", "cdp_gmail", "cdp_scrape", "cdp_search",
        "formfill", "formfill_cdp", "browser", "bookmarks", "uihands",
    ],
    "knowledge": [
        "racme", "sovereign_library", "transcribe_140857", "transcribe_watch",
        "extract_transcript", "srt_pass", "srt_pass_any", "teeup_pass",
        "teeup_pass_any", "ocr_140857", "download_gutenberg_books",
        "gutenberg_private_library", "backfill", "factcheck",
    ],
    "work": [
        "emissary_email", "emissary_engine", "inboxreader", "promorphyes",
        "promorphyes_search", "morPHYjob_hunter", "extract_jobs", "dump_indeed",
        "opportunity_hunter", "revenue_hunter", "bounty", "cardgate",
        "prospect", "prospect_form", "forge", "forge_build", "showtime",
        "template_monument", "biomax", "quote", "brief", "docgen", "book",
        "recon_bot", "hireharbor", "hh_engine", "hh_interview",
    ],
    "build": [
        "repair", "morphyapp", "script_doctor", "swap_paths", "index",
        "cohere_tools", "patch_ship",
    ],
    "system": [
        "dispatch", "keeper", "mobile", "find", "inventory", "library",
        "search", "morningbrief", "kanban", "startup_selfcheck",
        "coolingcontrol", "awake", "session_bridge", "kiosk", "agentaila",
        "appgreeter", "appuppeteer", "hub", "want", "cut",
    ],
    "money": [
        "tax", "laser", "hw_research",
    ],
    "research": [
        "ai_news", "sov_news", "concert_news", "news", "legislation",
        "morPHYscout", "morPHYspider", "morPHYguardian", "morphy_demand",
        "zeroego", "bookmarks",
    ],
}

# keyword hints only NUDGE Laya's decision-1 wording — never decide alone
_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "media":     ["video", "audio", "voice", "narrat", "commercial", "image", "story", "mp4", "wav", "record", "demo", "sdxl", "studio", "music", "poster"],
    "web":       ["browse", "scrape", "spider", "social", "x.com", "tweet", "post", "form", "login"],
    "knowledge": ["pdf", "docx", "library", "import", "transcript", "quiz", "study", "learn", "racme", "elias", "book", "gutenberg", "ocr", "srt", "subtitle"],
    "work":      ["job", "resume", "revenue", "grant", "customer", "prospect", "card", "contract", "client", "hire", "interview", "business", "gmail", "email", "inbox", "quote", "site"],
    "build":     ["fix", "patch", "debug", "build", "test", "deploy", "website", "script", "code", "site", "repair"],
    "system":    ["stack", "queue", "dispatch", "monitor", "health", "backup", "file", "folder", "clean", "machine", "drive", "env", "venv", "kiosk", "brief"],
    "money":     ["tax", "laser", "engrave", "hardware", "mini pc", "price"],
    "research":  ["news", "scan", "research", "competitor", "report", "hunt", "scout", "guard", "watch", "legislation", "hansard"],
}

_CATEGORY_OF: Dict[str, str] = {}
for _cat, _verbs in CATEGORIES.items():
    for _v in _verbs:
        _CATEGORY_OF[_v] = _cat


def _keyword_category(user_prompt: str) -> Optional[str]:
    low = user_prompt.lower()
    best, score = None, 0
    for cat, kws in _CATEGORY_KEYWORDS.items():
        s = sum(1 for kw in kws if kw in low)
        if s > score:
            best, score = cat, s
    return best  # may be None — Laya decides from scratch then


def _verbs_in_category(cat: str) -> List[str]:
    """Curated bucket first; fallback = keyword-extended (catches new verbs
    added to LEGEND.md after today that mention the category's words)."""
    curated = CATEGORIES.get(cat, [])
    have = {s.verb for s in SKILLS}
    curated = [v for v in curated if v in have]
    if curated:
        return curated
    kws = _CATEGORY_KEYWORDS.get(cat, [])
    out = []
    for s in SKILLS:
        blob = (s.verb + " " + s.desc).lower()
        if any(kw in blob for kw in kws):
            out.append(s.verb)
    return out or [s.verb for s in SKILLS[:12]]


def _tokenize(text: str) -> set:
    return set(re.findall(r"[a-z]{3,}", text.lower())) - _STOP_WORDS


_STOP_WORDS = {"the", "and", "for", "with", "this", "that", "from", "into",
               "what", "how", "why", "when", "where", "who", "you", "your",
               "can", "could", "would", "should", "make", "made", "get",
               "need", "want", "have", "has", "are", "was", "were", "will"}


def _laya_pick_verb(user_prompt: str) -> Tuple[Optional[str], float, str]:
    """Two Laya decisions (Trent's design, 2026-09-19):
    1) category (9 options), 2) verb inside the category (<=14 options).
    Both fit the 192-token head budget. Falls back cleanly."""
    # 0. exact verb name mentioned? trust it (the '&' contract) — INSTANT,
    #    before any model load, works even when RAM is too tight for Laya.
    low = user_prompt.lower()
    for s in SKILLS:
        for alias in [s.verb] + s.aliases:
            if re.search(r"\b" + re.escape(alias).replace(r"\_", r"[ _-]") + r"\b", low):
                return s.verb, 1.0, "explicit verb"
    agent = _get_laya()
    if agent is None:
        return None, 0.0, "laya unavailable"
    try:
        # ── DECISION 1: category ──
        kw_cat = _keyword_category(user_prompt)
        cat_desc = {
            "media":     "Make or edit video, audio, voice, narration, commercials, images, posters",
            "web":       "Browse, scrape, fill forms, post to social media, read web pages",
            "knowledge": "Documents, PDFs, libraries, transcripts, quizzes, study, subtitles, books",
            "work":      "Jobs, resumes, revenue, grants, customers, prospect cards, contracts, client sites, email, quotes",
            "build":     "Fix, patch, debug, build or test code, scripts, deployment",
            "system":    "Stack health, queue, dispatch, monitors, backups, files, kiosk, machine upkeep",
            "money":     "Taxes, hardware prices, laser work",
            "research":  "News, X feeds, legislation, scouting, security scans, reports",
        }
        cat_criteria = {c: d for c, d in cat_desc.items()}
        cat_criteria["chat"] = "Plain conversation, explanation or thinking. No tool, no command, no skill."
        cat_q = {
            "category": {
                "type": "choice",
                "instructions": "Which category of work is this request?" + (f" The words suggest '{kw_cat}' but judge for yourself." if kw_cat else ""),
                "criteria": cat_criteria,
            }
        }
        t0 = time.perf_counter()
        r1 = agent.predict({"request": user_prompt[:700]}, cat_q)
        dt1 = (time.perf_counter() - t0) * 1000
        cat = r1["answers"]["category"]["choice"]
        cat_conf = float(r1["answers"]["category"].get("confidence", 0))
        _log(f"laya d1: category={cat} conf={cat_conf:.2f} {dt1:.0f}ms (keyword_hint={kw_cat})")
        if cat == "chat":
            return None, cat_conf, "chat"

        # ── DECISION 2: verb within category (cap 14 by keyword closeness) ──
        cat_verbs = _verbs_in_category(cat)
        words = _tokenize(user_prompt)
        if len(cat_verbs) > 14:
            def closeness(v: str) -> int:
                s = next(x for x in SKILLS if x.verb == v)
                return len(words & _tokenize(s.verb + " " + s.desc))
            cat_verbs = sorted(cat_verbs, key=lambda v: -closeness(v))[:14]
        desc_map = {s.verb: s.desc for s in SKILLS}
        verb_criteria = {v: desc_map[v] for v in cat_verbs}
        verb_criteria["none_of_these"] = "None of these skills actually fits — do not force a pick."
        verb_q = {
            "skill": {
                "type": "choice",
                "instructions": "Which exact skill should run for this request?",
                "criteria": verb_criteria,
            }
        }
        t0 = time.perf_counter()
        r2 = agent.predict({"request": user_prompt[:900]}, verb_q)
        dt2 = (time.perf_counter() - t0) * 1000
        a = r2["answers"]["skill"]
        top = a["choice"]
        conf = float(a.get("confidence", 0))
        probs = a.get("probabilities", {})
        top3 = sorted(probs.items(), key=lambda kv: -kv[1])[:3]
        _log(f"laya d2: {top} conf={conf:.2f} {dt2:.0f}ms in[{cat}] options={cat_verbs} top3={top3}")
        if top == "none_of_these" or conf < _LAYA_MIN_CONFIDENCE:
            return None, conf, "no verb in category cleared the bar"
        return top, conf, "laya classified"
    except Exception as e:
        _log(f"laya classify error: {e}")
        return None, 0.0, f"laya error: {e}"


# ── SMART EXTRACTORS (verbatim behavior preserved from legend.py) ──
def _extract_urls(text): return re.findall(r'https?://[^\s"\'<>]+', text)
def _extract_paths(text): return re.findall(r'[A-Za-z]:\\[^\s"\'<>]+\.\w{2,5}|/[^\s"\'<>]+\.\w{2,5}', text)
def _extract_dirs(text): return re.findall(r'[A-Za-z]:\\[^\s"\'<>]+', text)

def _extract_quoted(text, max_len=50):
    out = []
    for m in re.finditer(r'"([^"\n]{1,%d})"' % max_len, text):
        v = m.group(1).strip()
        if "://" not in v and "\\" not in v:
            out.append(v)
    return out

_STOP = {"a", "an", "the", "this", "that", "it", "and", "or", "with",
         "using", "in", "on", "to", "for", "of", "as", "is", "be",
         "please", "save", "file", "voice", "video", "audio", "name",
         "from", "into", "called", "named", "by"}

def _extract_name_like(text):
    for pat in (r'\b(?:name|call\s+it|save\s+(?:it\s+)?as|named|save\s+by)\s+(?:the\s+)?(?:voice\s+|audio\s+|file\s+)?["\']?([A-Za-z][\w\-]{1,30})["\']?',
                r'\bvoice\s+(?:named|called|as)\s+["\']?([A-Za-z][\w\-]{1,30})["\']?'):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            v = m.group(1).strip()
            if v.lower() not in _STOP:
                return v
    for q in _extract_quoted(text):
        if q.lower() not in _STOP:
            return q
    return None

def _extract_free_text_after_verb(text, verb):
    pat = r'\b' + re.escape(verb).replace(r'\_', r'[ _-]') + r'\b\s*(.*)'
    m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    rest = m.group(1).strip()
    rest = re.sub(r'\s+and\s+save\s+.*$', '', rest, flags=re.IGNORECASE)
    return rest.strip()

def _fill_url(text, verb):
    urls = _extract_urls(text)
    return urls[0] if urls else None

def _fill_youtube_url(text, verb):
    urls = _extract_urls(text)
    for u in urls:
        if 'youtu' in u:
            return u
    return urls[0] if urls else None

def _fill_path(text, verb):
    paths = _extract_paths(text)
    return paths[0] if paths else None

def _fill_file_or_folder(text, verb):
    paths = _extract_paths(text)
    if paths: return paths[0]
    dirs = _extract_dirs(text)
    return dirs[0] if dirs else None

def _fill_file_or_all(text, verb):
    paths = _extract_paths(text)
    if paths: return paths[0]
    return "all"

def _fill_name(text, verb):
    return _extract_name_like(text)

def _fill_voice(text, verb):
    m = re.search(r'--voice\s+["\']?([A-Za-z][\w\-]{1,20})["\']?', text, re.IGNORECASE)
    if m: return m.group(1)
    m = re.search(r'\b(?:in|using|with)\s+(?:the\s+)?([A-Za-z][\w\-]{1,20})\s+voice\b', text, re.IGNORECASE)
    if m:
        v = m.group(1)
        if v.lower() not in _STOP: return v
    m = re.search(r'\b([A-Za-z][\w\-]{1,20})\s+voice\b', text, re.IGNORECASE)
    if m:
        v = m.group(1)
        if v.lower() not in _STOP: return v
    return None

def _fill_query(text, verb):
    return _extract_free_text_after_verb(text, verb) or None

def _fill_action(text, verb):
    return _extract_free_text_after_verb(text, verb) or None

def _fill_task(text, verb):
    return _extract_free_text_after_verb(text, verb) or None

def _fill_message(text, verb):
    return _extract_free_text_after_verb(text, verb) or None

def _fill_prompt(text, verb):
    return _extract_free_text_after_verb(text, verb) or None

def _fill_idea(text, verb):
    return _extract_free_text_after_verb(text, verb) or None

def _fill_thoughts(text, verb):
    return _extract_free_text_after_verb(text, verb) or None

def _fill_description(text, verb):
    return _extract_free_text_after_verb(text, verb) or None

def _fill_content_or_brief(text, verb):
    return _extract_free_text_after_verb(text, verb) or None

def _fill_args(text, verb):
    return _extract_free_text_after_verb(text, verb) or ""

def _fill_preview_or_sellable(text, verb):
    if re.search(r'\bsellable\b', text, re.IGNORECASE): return "sellable"
    if re.search(r'\bpreview\b', text, re.IGNORECASE): return "preview"
    return "preview"

PLACEHOLDER_FILLERS: Dict[str, Callable] = {
    "url": _fill_url,
    "youtube_url": _fill_youtube_url,
    "path": _fill_path,
    "video_path": _fill_path,
    "file_or_folder": _fill_file_or_folder,
    "file_or_all": _fill_file_or_all,
    "name": _fill_name,
    "voice": _fill_voice,
    "query": _fill_query,
    "action": _fill_action,
    "task": _fill_task,
    "message": _fill_message,
    "prompt": _fill_prompt,
    "idea": _fill_idea,
    "thoughts": _fill_thoughts,
    "description": _fill_description,
    "content_or_brief": _fill_content_or_brief,
    "text": _fill_query,
    "data": _fill_query,
    "args": _fill_args,
    "preview_or_sellable": _fill_preview_or_sellable,
    "section": _fill_name,
    "ministry": _fill_name,
    "industry": _fill_name,
    "site": _fill_name,
    "tool_name": _fill_name,
    "task_name": _fill_name,
    "process_name": _fill_name,
    "type": _fill_name,
    "model": _fill_name,
    "mode": _fill_name,
    "client": _fill_name,
    "client_name": _fill_name,
    "root": _fill_file_or_folder,
    "story": _fill_path,
    "book_path": _fill_path,
    "script_md": _fill_path,
}


def _build_command(skill: Skill, user_prompt: str) -> Tuple[Optional[str], List[str]]:
    """Stage 2: deterministic fill of the canonical command. No generation."""
    cmd = skill.command
    missing: List[str] = []
    for ph in set(re.findall(r"<([a-z_]+)>", cmd)):
        filler = PLACEHOLDER_FILLERS.get(ph)
        val = filler(user_prompt, skill.verb) if filler else None
        if val is None or val == "":
            # optional placeholder: leave for skill to default; required ones gate
            missing.append(ph)
            continue
        val = str(val).replace('"', '\\"') if '"' in str(val) else str(val)
        cmd = cmd.replace(f"<{ph}>", val)
    if missing:
        return None, missing
    return cmd, []


def _verify_command_in_skills(cmd: str) -> bool:
    m = re.search(r'"([^"]+\.py)"', cmd)
    if not m:
        return False
    script_name = m.group(1)
    target = SKILLS_DIR / script_name
    if target.exists():
        return True
    return (SKILLS_DIR / Path(script_name).name).exists()


# ── FALLBACK: original Ollama translator (kept intact) ───────
OLLAMA_URL = "http://localhost:11434/api/chat"
LEGEND_MODEL = "legend"


def _call_legend_model(user_prompt: str, timeout: int = 600) -> Optional[str]:
    menu = "\n".join(f"  {s.verb} :: {s.desc} :: {s.command}" for s in SKILLS)
    system = (
        "You are a command translator. Single purpose. You do ONE thing:\n"
        "read the user's request, find the matching skill in the menu,\n"
        "and output the canonical command with <placeholders> filled.\n\n"
        "RULES:\n1. Output ONLY the command line. Nothing else.\n"
        "2. If no skill matches, output exactly: NO_MATCH\n"
        "3. If a placeholder can't be filled from the prompt, output: NO_MATCH\n"
        "4. Copy the canonical command verbatim, swap <placeholders>.\n"
        "5. Never refuse. Never explain. Never add JSON or markdown.\n"
    )
    payload = {
        "model": LEGEND_MODEL,
        "messages": [
            {"role": "system", "content": system + "\nMENU OF SKILLS:\n" + menu},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 512},
    }
    try:
        import requests
        r = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        if r.status_code != 200:
            return None
        msg = r.json().get("message", {}).get("content", "").strip()
        return msg if msg else None
    except Exception:
        return None


# ════════════════════════════════════════════════════════════
#  RESOLVE — the '&' entry point (same signature as legend.py)
#  Laya classifies → deterministic fill → verify → fire.
# ════════════════════════════════════════════════════════════
def resolve(prompt: str) -> Dict:
    user_prompt = prompt.replace("&", " ").strip()

    verb, conf, note = _laya_pick_verb(user_prompt)

    if verb is None:
        if "laya" in note:  # laya unavailable/errored → original path
            raw = _call_legend_model(user_prompt)
            if not raw:
                return {"ok": False, "verb": None, "reason": "legend model unreachable (laya fallback also failed)"}
            text = raw.strip()
            if text.startswith("```"):
                text = "\n".join(l for l in text.split("\n") if not l.strip().startswith("```")).strip()
            if text == "NO_MATCH" or not text:
                return {"ok": False, "verb": None, "reason": "no skill matched"}
            if not _verify_command_in_skills(text):
                return {"ok": False, "verb": None, "reason": f"model returned unknown script: {text[:80]}"}
            cmd = text.split("\n")[0].strip()
            v = "?"
            for s in SKILLS:
                m = re.search(r'"([^"]+\.py)"', s.command)
                if m and m.group(1) in cmd:
                    v = s.verb
                    break
            return {"ok": True, "verb": v, "command": cmd}
        # Laya spoke: chat / low confidence / explicit non-match
        return {"ok": False, "verb": None, "reason": f"no skill matched ({note}, conf={conf:.2f})"}

    skill = next((s for s in SKILLS if s.verb == verb), None)
    if skill is None:
        return {"ok": False, "verb": verb, "reason": "verb vanished from LEGEND"}

    cmd, missing = _build_command(skill, user_prompt)
    if cmd is None:
        return {"ok": False, "verb": verb, "reason": f"missing {', '.join('<'+m+'>' for m in missing)}"}
    if not _verify_command_in_skills(cmd):
        return {"ok": False, "verb": verb, "reason": "resolved script missing from SKILLS/"}

    return {"ok": True, "verb": verb, "command": cmd}


def describe_for_model() -> str:
    lines = [f"# LEGEND — {len(SKILLS)} skills available via the '&' trigger:", ""]
    for s in SKILLS:
        lines.append(f"  {s.verb} — {s.desc}")
    lines.append("")
    lines.append("To fire a skill, prefix your prompt with '&' and name the verb.")
    return "\n".join(lines)


def reload():
    global SKILLS
    SKILLS = _load_legend_md()
    _mirror_legend_md()


if __name__ == "__main__":
    print("=" * 60)
    print(f"LEGEND BETA (Laya) — {len(SKILLS)} skills loaded from LEGEND.md")
    print("=" * 60)
    tests = [
        "& racme boiler fundamentals quiz",
        "& post this announcement everywhere: AI sovereignty now",
        "& cardgate all",
        "just chatting, no skill needed here friend",
    ]
    for prompt in tests:
        t0 = time.perf_counter()
        result = resolve(prompt)
        dt = (time.perf_counter() - t0) * 1000
        mark = "✓" if result.get("ok") else "✗"
        print(f"{mark} ({dt:.0f}ms) {prompt[:70]}")
        if result.get("ok"):
            print(f"   → {result['command'][:130]}")
        else:
            print(f"   → {result.get('reason')}")
        print()
