"""
legend.py — THE LEGEND, as a Python module.

THE LEGEND IS THE PROGRAM. Not markdown the program reads. The program.
LEGEND.md is the source of truth. This module loads it on startup and
exposes every skill as a first-class object.

THREE CONSUMERS, ONE SOURCE OF TRUTH:
  1. The program  — fires skills when '&' appears in the prompt
  2. The model    — reads entries as the menu of what AILA can do
  3. The mechanic — adds skills by appending one line to LEGEND.md

THE MECHANIC'S INTERFACE (the only thing he touches):
  Add ONE line to SKILLS/LEGEND.md, in this format:
      verb :: what it does :: canonical command with <placeholders>

  That's it. No Python. No regex. No copying code blocks. The next
  time simpleJACK starts, legend.py loads the new skill automatically.

─────────────────────────────────────────────────────────────────
HOW IT WORKS
─────────────────────────────────────────────────────────────────
  LEGEND.md is parsed into Skill objects on import.
  Each Skill has: verb, desc, command (with <placeholders>), aliases.
  When '&' appears in a prompt, resolve() finds the matching verb,
  then fill() extracts values from the prompt and substitutes them
  into the command template.

  The extractor does NOT use one regex per skill. It uses smart
  extractors per PLACEHOLDER TYPE — there are ~8 types, not 107.
  Anything it can't fill stays as <placeholder> and the skill won't
  fire (so AILA can ask for the missing piece conversationally).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


# ════════════════════════════════════════════════════════════
#  PATHS
# ════════════════════════════════════════════════════════════
SIMPLEJACK_ROOT = Path(__file__).resolve().parent
SKILLS_DIR = SIMPLEJACK_ROOT / "SKILLS"
LEGEND_FILE = SKILLS_DIR / "LEGEND.md"


# ════════════════════════════════════════════════════════════
#  THE SHAPE OF A SKILL
# ════════════════════════════════════════════════════════════
@dataclass
class Skill:
    verb: str
    desc: str
    command: str
    aliases: List[str] = field(default_factory=list)


# ════════════════════════════════════════════════════════════
#  LOAD LEGEND.MD — the source of truth
# ════════════════════════════════════════════════════════════
def _load_legend_md() -> List[Skill]:
    """Parse LEGEND.md into Skill objects. One line = one skill.
    Format: verb :: description :: canonical_command"""
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
        # Auto-alias: verb with underscores also matches with hyphens / spaces
        aliases = []
        if "_" in verb:
            aliases.append(verb.replace("_", "-"))
            aliases.append(verb.replace("_", " "))
        skills.append(Skill(verb=verb, desc=desc, command=command, aliases=aliases))
    return skills


SKILLS: List[Skill] = _load_legend_md()


# ════════════════════════════════════════════════════════════
#  SMART EXTRACTORS — by placeholder TYPE, not per-skill.
#  Eight types cover all 34 placeholders used in LEGEND.md.
# ════════════════════════════════════════════════════════════
def _extract_urls(text: str) -> List[str]:
    return re.findall(r'https?://[^\s"\'<>]+', text)


def _extract_paths(text: str) -> List[str]:
    """All disk paths with extensions, in order of appearance."""
    return re.findall(r'[A-Za-z]:\\[^\s"\'<>]+\.\w{2,5}|/[^\s"\'<>]+\.\w{2,5}', text)


def _extract_dirs(text: str) -> List[str]:
    """All disk paths (may not have extension)."""
    return re.findall(r'[A-Za-z]:\\[^\s"\'<>]+', text)


def _extract_quoted(text: str, max_len: int = 50) -> List[str]:
    """All double-quoted strings (not paths, not URLs)."""
    out = []
    for m in re.finditer(r'"([^"\n]{1,%d})"' % max_len, text):
        v = m.group(1).strip()
        if "://" not in v and "\\" not in v:
            out.append(v)
    return out


def _extract_single_quoted(text: str, max_len: int = 50) -> List[str]:
    out = []
    for m in re.finditer(r"'([^'\n]{1,%d})'" % max_len, text):
        v = m.group(1).strip()
        if "://" not in v and "\\" not in v:
            out.append(v)
    return out


_STOP = {"a", "an", "the", "this", "that", "it", "and", "or", "with",
         "using", "in", "on", "to", "for", "of", "as", "is", "be",
         "please", "save", "file", "voice", "video", "audio", "name",
         "from", "into", "called", "named", "by"}


def _extract_name_like(text: str) -> Optional[str]:
    """A single word that looks like a proper name (after 'name', 'call it', 'save as')."""
    # Priority 1: explicit "name X", "call it X", "save as X", "named X"
    for pat in (r'\b(?:name|call\s+it|save\s+(?:it\s+)?as|named|save\s+by)\s+(?:the\s+)?(?:voice\s+|audio\s+|file\s+)?["\']?([A-Za-z][\w\-]{1,30})["\']?',
                r'\bvoice\s+(?:named|called|as)\s+["\']?([A-Za-z][\w\-]{1,30})["\']?'):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            v = m.group(1).strip()
            if v.lower() not in _STOP:
                return v
    # Priority 2: any quoted string that isn't a path/URL
    for q in _extract_quoted(text):
        if q.lower() not in _STOP:
            return q
    return None


def _extract_free_text_after_verb(text: str, verb: str) -> str:
    """Everything after the verb — for <args>, <prompt>, <thoughts>, etc."""
    pat = r'\b' + re.escape(verb).replace(r'\_', r'[ _-]') + r'\b\s*(.*)'
    m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    rest = m.group(1).strip()
    # Strip a trailing "and save it to desktop" type clauses for clean args
    rest = re.sub(r'\s+and\s+save\s+.*$', '', rest, flags=re.IGNORECASE)
    rest = re.sub(r'\s+and\s+save\s+it\s+.*$', '', rest, flags=re.IGNORECASE)
    return rest.strip()


def _extract_after_marker(text: str, marker: str) -> Optional[str]:
    """Text after 'in <marker>', 'for <marker>', 'with <marker>', etc."""
    pat = r'\b(?:in|for|with|using|via|from)\s+(?:the\s+|a\s+|an\s+)?' + re.escape(marker) + r'\b\s*(.*?)\s*(?:,|\.|$)'
    m = re.search(pat, text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _extract_timestamp(text: str, kind: str) -> Optional[str]:
    pat = {
        'start': r'\b(?:start(?:ing)?(?:\s+at)?|from)\s+(\d{1,2}:\d{2}(?::\d{2})?)',
        'end':   r'\b(?:end(?:ing)?(?:\s+(?:at|to))?\s+)(\d{1,2}:\d{2}(?::\d{2})?)',
    }[kind]
    m = re.search(pat, text, re.IGNORECASE)
    return m.group(1) if m else None


def _extract_location(text: str) -> Optional[str]:
    """desktop / downloads / a disk path. Portable (2026-08-01):
    resolves via Path.home() — works on ANY Windows user."""
    m = re.search(r'\b(desktop|downloads|documents)\b', text, re.IGNORECASE)
    if m:
        loc = m.group(1).lower()
        _home = Path.home()
        return {
            'desktop':   str(_home / 'Desktop'),
            'downloads': str(_home / 'Downloads'),
            'documents': str(_home / 'Documents'),
        }[loc]
    # Or a disk path mentioned
    dirs = _extract_dirs(text)
    return dirs[0] if dirs else None


# ════════════════════════════════════════════════════════════
#  PLACEHOLDER → EXTRACTOR MAPPING
#  One entry per placeholder TYPE. When fill() sees <X>, it looks
#  up X here and runs the extractor. Add a new placeholder type =
#  add one entry here. That's the only extension point.
# ════════════════════════════════════════════════════════════
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

def _fill_book_path(text, verb):
    return _fill_path(text, verb)

def _fill_video_path(text, verb):
    return _fill_path(text, verb)

def _fill_story(text, verb):
    return _fill_path(text, verb)

def _fill_file_or_folder(text, verb):
    paths = _extract_paths(text)
    if paths: return paths[0]
    dirs = _extract_dirs(text)
    return dirs[0] if dirs else None

def _fill_file_or_all(text, verb):
    paths = _extract_paths(text)
    if paths: return paths[0]
    return "all"

def _fill_root(text, verb):
    dirs = _extract_dirs(text)
    return dirs[0] if dirs else None

def _fill_name(text, verb):
    return _extract_name_like(text)

def _fill_client(text, verb):
    return _extract_name_like(text)

def _fill_client_name(text, verb):
    return _extract_name_like(text)

def _fill_site(text, verb):
    return _extract_name_like(text)

def _fill_voice(text, verb):
    """Voice name: 'in NAME voice', 'using NAME', '--voice NAME'."""
    # explicit flag
    m = re.search(r'--voice\s+["\']?([A-Za-z][\w\-]{1,20})["\']?', text, re.IGNORECASE)
    if m: return m.group(1)
    # "in NAME voice" / "using NAME voice" / "with NAME voice"
    m = re.search(r'\b(?:in|using|with)\s+(?:the\s+)?([A-Za-z][\w\-]{1,20})\s+voice\b', text, re.IGNORECASE)
    if m:
        v = m.group(1)
        if v.lower() not in _STOP: return v
    # "NAME voice" standalone
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

def _fill_mode(text, verb):
    return _extract_name_like(text)

def _fill_model(text, verb):
    return _extract_name_like(text)

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

def _fill_ministry(text, verb):
    return _extract_name_like(text)

def _fill_industry(text, verb):
    return _extract_name_like(text)

def _fill_section(text, verb):
    return _extract_name_like(text)

def _fill_tool_name(text, verb):
    return _extract_name_like(text)

def _fill_task_name(text, verb):
    return _extract_name_like(text)

def _fill_process_name(text, verb):
    return _extract_name_like(text)

def _fill_type(text, verb):
    return _extract_name_like(text)

def _fill_data(text, verb):
    return _extract_free_text_after_verb(text, verb) or None

def _fill_text_arg(text, verb):
    # Text content (could be the prompt minus verb, or a quoted block)
    q = _extract_quoted(text)
    if q: return q[0]
    return _extract_free_text_after_verb(text, verb) or None

def _fill_preview_or_sellable(text, verb):
    if re.search(r'\bsellable\b', text, re.IGNORECASE): return "sellable"
    if re.search(r'\bpreview\b', text, re.IGNORECASE): return "preview"
    return "preview"

def _fill_args(text, verb):
    """<args> = whatever was typed after the verb. Free-form."""
    return _extract_free_text_after_verb(text, verb) or ""


PLACEHOLDER_FILLERS: Dict[str, Callable] = {
    "url": _fill_url,
    "youtube_url": _fill_youtube_url,
    "path": _fill_path,
    "book_path": _fill_book_path,
    "video_path": _fill_video_path,
    "story": _fill_story,
    "file_or_folder": _fill_file_or_folder,
    "file_or_all": _fill_file_or_all,
    "root": _fill_root,
    "name": _fill_name,
    "client": _fill_client,
    "client_name": _fill_client_name,
    "site": _fill_site,
    "voice": _fill_voice,
    "query": _fill_query,
    "action": _fill_action,
    "task": _fill_task,
    "mode": _fill_mode,
    "model": _fill_model,
    "message": _fill_message,
    "prompt": _fill_prompt,
    "idea": _fill_idea,
    "thoughts": _fill_thoughts,
    "description": _fill_description,
    "content_or_brief": _fill_content_or_brief,
    "ministry": _fill_ministry,
    "industry": _fill_industry,
    "section": _fill_section,
    "tool_name": _fill_tool_name,
    "task_name": _fill_task_name,
    "process_name": _fill_process_name,
    "type": _fill_type,
    "data": _fill_data,
    "text": _fill_text_arg,
    "preview_or_sellable": _fill_preview_or_sellable,
    "args": _fill_args,
}


# ════════════════════════════════════════════════════════════
#  THE RESOLVER — what fires when '&' appears in the prompt.
#  Returns either a ready command or a "couldn't fill" note.
#  Pure function. No side effects.
# ════════════════════════════════════════════════════════════
OLLAMA_URL = "http://localhost:11434/api/chat"
LEGEND_MODEL = "legend"


def _call_legend_model(user_prompt: str, timeout: int = 600) -> Optional[str]:
    """Single-purpose LLM call. The model sees the LEGEND and the prompt.
    Its only job: output the one canonical command with placeholders
    filled from the prompt, or output exactly NO_MATCH.

    No conversation. No refusal. No opinions. Pure translation."""
    menu = "\n".join(f"  {s.verb} :: {s.desc} :: {s.command}" for s in SKILLS)
    system = (
        "You are a command translator. Single purpose. You do ONE thing:\n"
        "read the user's request, find the matching skill in the menu,\n"
        "and output the canonical command with <placeholders> filled.\n\n"
        "RULES:\n"
        "1. Output ONLY the command line. Nothing else. No preamble.\n"
        "2. If no skill matches, output exactly: NO_MATCH\n"
        "3. If a placeholder can't be filled from the prompt, output: NO_MATCH\n"
        "4. Copy the canonical command verbatim, swap <placeholders> for values\n"
        "   pulled from the user's prompt. Drop optional [bracketed] parts\n"
        "   the user didn't specify.\n"
        "5. Never refuse. Never explain. Never add JSON or markdown.\n"
        "6. The command must start with the interpreter path C:\\...\\python.exe\n"
        "   or `streamlit run`.\n"
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


def _verify_command_in_skills(cmd: str) -> bool:
    """The command must reference a .py that lives in SKILLS/.
    This is the hard guardrail: the model can't invent scripts."""
    m = re.search(r'"([^"]+\.py)"', cmd)
    if not m:
        return False
    script_name = m.group(1)
    # The script must exist in SKILLS/ (verbatim or by basename)
    target = SKILLS_DIR / script_name
    if target.exists():
        return True
    target_basename = SKILLS_DIR / Path(script_name).name
    return target_basename.exists()


def _find_skill(prompt: str) -> Optional[Skill]:
    """Legacy stub. Real resolution now goes through resolve() → the model."""
    return None


def _fill_command(skill: Skill, prompt: str) -> Tuple[str, List[str]]:
    """Legacy stub. The model fills the command now."""
    return skill.command, []


def resolve(prompt: str) -> Dict:
    """The legend. Calls the single-purpose model.
    Returns:
      {"ok": True,  "verb": ..., "command": ...}
      {"ok": False, "verb": ..., "reason": ...}
      {"ok": False, "verb": None, "reason": ...}

    The model sees the whole LEGEND as the menu. It picks the skill,
    fills the placeholders, returns the command. We verify the script
    exists in SKILLS/ — model can't invent scripts.
    """
    # Strip the '&' trigger
    user_prompt = prompt.replace("&", " ").strip()

    raw = _call_legend_model(user_prompt)
    if not raw:
        return {"ok": False, "verb": None, "reason": "legend model unreachable"}

    text = raw.strip()

    # Strip code fences if model added them anyway
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()

    if text == "NO_MATCH" or not text:
        return {"ok": False, "verb": None, "reason": "no skill matched"}

    # Hard guardrail: command must reference a real script in SKILLS/
    if not _verify_command_in_skills(text):
        return {
            "ok": False,
            "verb": None,
            "reason": f"model returned unknown script (not in SKILLS/): {text[:80]}",
        }

    # Try to identify which verb it matched (for the log line)
    cmd = text.split("\n")[0].strip()
    verb = "?"
    for s in SKILLS:
        m = re.search(r'"([^"]+\.py)"', s.command)
        if m and m.group(1) in cmd:
            verb = s.verb
            break

    return {"ok": True, "verb": verb, "command": cmd}


# ════════════════════════════════════════════════════════════
#  FOR THE MODEL — render skills as a menu AILA can read.
# ════════════════════════════════════════════════════════════
def describe_for_model() -> str:
    lines = [f"# LEGEND — {len(SKILLS)} skills available via the '&' trigger:", ""]
    for s in SKILLS:
        lines.append(f"  {s.verb} — {s.desc}")
    lines.append("")
    lines.append("To fire a skill, prefix your prompt with '&' and name the verb.")
    return "\n".join(lines)


def reload():
    """Re-parse LEGEND.md. Use after the mechanic adds a skill."""
    global SKILLS
    SKILLS = _load_legend_md()


# ════════════════════════════════════════════════════════════
#  SELF-TEST — run `python legend.py` to verify.
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print(f"LEGEND MODULE — {len(SKILLS)} skill(s) loaded from LEGEND.md")
    print("=" * 60)
    print()
    tests = [
        r'& clone the full length https://youtu.be/dVATgQfpeYY?si=ORFU9PKEXgfLkZXw with chatterbox and save it as "grumpy"',
        r'& provide a narration recording of "C:\Users\Public\Documents\sample_speech.md" in trudope voice and save it to desktop',
        r'& narrate "C:\Users\Public\Documents\sample_speech.md" in the trudope voice',
        r'& voice_test "Hello world this is a test"',
        r'random conversation with no skill verb',
    ]
    for prompt in tests:
        result = resolve(prompt)
        mark = "✓" if result.get("ok") else "✗"
        print(f"{mark} {prompt[:80]}")
        if result.get("ok"):
            print(f"   → {result['command'][:120]}")
        else:
            print(f"   → {result.get('verb')}: {result.get('reason')}")
        print()
