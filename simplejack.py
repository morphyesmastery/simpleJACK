"""
simplejack.py — the SimpleJack brain.
Self-contained MorPHYes Mastery. One queue. Its own. No background daemons.

AILA does exactly 4 things:
  1. CHAT        — respond to the operator directly (no tool)
  2. STACK CMD   — append one line to STACK/queue.txt   (queue_command)
  3. GO LOOK     — read a file, return its contents     (read_file)
  4. RUN STACK   — tell dispatch to go / pause          (stack_go / stack_pause)

Zero on-board skills. A skill is a line on the STACK; dispatch opens the window.

NARRATION IS THE SPINE (LAW 1, non-negotiable):
  narrate() is called FIRST in every request, before the model.
  narrate() is called again with the reply. No code path returns a response
  without touching the queue. morPHYtrek cannot fail because she "forgot" —
  speaking is structurally before answering.

PORT 8797 — the PORTABLE door. NEVER collides with the live 8791.
Never kills the other python (LAW 3).
"""

import os


# EMPTY-RESPONSE GATE counters (Trent 2026-09-13): nudge tracking per card +
# nightly empty totals. The gate itself lives in call_model().
_EMPTY_NUDGES = {}
_EMPTY_TOTAL = {}
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import sys
import json
import time
import re
import uuid
import threading
import requests
import subprocess
from pathlib import Path
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

# ════════════════════════════════════════════════════════════
#  PATHS
# ════════════════════════════════════════════════════════════
SIMPLEJACK_ROOT = Path(__file__).resolve().parent
# Portable-first (2026-08-01): everything resolves NEXT TO this file.
# keys.local.json lives in <bundle>/config/ — created on first run.
# sys.path bootstrap: the EMBEDDABLE runtime (runtime/python.exe) does NOT
# put the script's folder on sys.path — without this, `import legend` and
# sibling modules die on a naked machine. THIS line makes the bundle work
# under ANY python, embedded or installed.
if str(SIMPLEJACK_ROOT) not in sys.path:
    sys.path.insert(0, str(SIMPLEJACK_ROOT))
MORPHYES_ROOT   = SIMPLEJACK_ROOT
STACK_DIR       = SIMPLEJACK_ROOT / "STACK"
QUEUE_FILE      = STACK_DIR / "queue.txt"
CURRENT_FILE    = STACK_DIR / "current.txt"
DONE_LOG        = STACK_DIR / "done.log"
DISPATCH_SCRIPT = SIMPLEJACK_ROOT / "dispatch" / "dispatch.py"
DISPATCH_FLAG   = SIMPLEJACK_ROOT / "dispatch" / ".run"
# LAW 1 canonical queue — the folder is the SWITCH.
# morPHYtrek is the MAKER: it creates the queue folder on startup if missing.
# SimpleJack is the CONDITIONAL WRITER: if it does NOT see the folder, it does
# NOT write the narration file. Not more. Not less. (friend's rule, 2026-08-01)
# Portable: the queue is ALWAYS right next to the launcher. Never looked for
# anywhere else. One queue. Its own. Ever. (friend, 2026-08-01: zero excuse.)
_PORTABLE_QUEUE = SIMPLEJACK_ROOT / "morphytrek_data" / "queue"
NARRATION_QUEUE = _PORTABLE_QUEUE
HTML_FILE       = SIMPLEJACK_ROOT / "simplejack_v2.html"
LOG_FILE        = SIMPLEJACK_ROOT / "simplejack.log"


# ════════════════════════════════════════════════════════════
#  DISPATCH GUARDIAN — simplejack makes sure dispatch is alive.
#  Persistent daemon. Visible. Never hidden. Started once, runs forever.
#  If AILA is up and dispatch is down, simplejack launches it before
#  serving her first request. Nothing hidden ever.
# ════════════════════════════════════════════════════════════
def dispatch_pid_file():
    return SIMPLEJACK_ROOT / "dispatch" / "dispatch.pid"

def is_dispatch_running():
    """True if a live dispatch process owns the recorded PID."""
    pidfile = dispatch_pid_file()
    if not pidfile.exists():
        return False
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    if pid <= 0:
        return False
    try:
        # Windows: check process is alive
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return False

def ensure_dispatch_running():
    """Persistent daemon guard. If dispatch is down, launch it in a VISIBLE
    window. Never hidden. Never auto-killed."""
    if is_dispatch_running():
        return True
    try:
        # Launch dispatch in a visible cmd window — portable python discovery:
        # python beside simplejack.py > MorPHYvenv beside it > PATH python
        _bundle_dir = Path(__file__).resolve().parent
        venv_python = str(_bundle_dir / "python.exe")
        if not Path(venv_python).exists():
            venv_python = str(_bundle_dir / "MorPHYvenv" / "Scripts" / "python.exe")
        if not Path(venv_python).exists():
            venv_python = sys.executable  # fallback — running interpreter is fine
        CREATE_NEW_CONSOLE = 0x00000010
        subprocess.Popen(
            ["cmd.exe", "/k",
             f'"{venv_python}" "{DISPATCH_SCRIPT}"'],
            creationflags=CREATE_NEW_CONSOLE,
            cwd=str(DISPATCH_SCRIPT.parent),
        )
        log("DISPATCH was down — launched in visible window.")
        return True
    except Exception as e:
        log(f"DISPATCH launch failed: {e}")
        return False


# ════════════════════════════════════════════════════════════
#  OBSCURA MCP GUARDIAN — the browser hands boot with the system.
#  Same law as dispatch: if the MCP server on 3366 is down when
#  simplejack boots, launch it DETACHED (LAUNCH LAW — never a
#  child of this process). It survives on its own. Never killed.
# ════════════════════════════════════════════════════════════
OBSCURA_EXE = SIMPLEJACK_ROOT / "tools" / "obscura" / "obscura.exe"
MCP_PORT    = 3366

def is_mcp_running():
    """True if something answers on the MCP port (127.0.0.1:3366)."""
    try:
        r = requests.post(
            f"http://127.0.0.1:{MCP_PORT}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2024-11-05",
                             "capabilities": {},
                             "clientInfo": {"name": "simplejack-guardian",
                                            "version": "1.0"}}},
            headers={"Content-Type": "application/json",
                     "mcp-session-id": "guardian"},
            timeout=3,
        )
        return r.status_code == 200
    except Exception:
        return False

def ensure_mcp_running():
    """If the Obscura MCP server is down, launch it DETACHED so it survives
    on its own (LAUNCH LAW). No console window — it is a silent local engine,
    same as the hidden SimpleJack server lane."""
    if is_mcp_running():
        return True
    if not OBSCURA_EXE.exists():
        log(f"MCP GUARDIAN: obscura.exe not found at {OBSCURA_EXE}")
        return False
    try:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            [str(OBSCURA_EXE), "mcp", "--http", "--port", str(MCP_PORT),
             "--allow-private-network"],
            cwd=str(OBSCURA_EXE.parent),
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        log("MCP GUARDIAN: Obscura was down — launched detached on 3366.")
        return True
    except Exception as e:
        log(f"MCP GUARDIAN: launch failed: {e}")
        return False

PORT = 8797

# ════════════════════════════════════════════════════════════
#  SIGN-IN GATE (2026-08-23) — the door is PUBLIC through the tunnel;
#  the brain is NOT free range. Browser-native Basic auth.
#  Password lives in config/gate.password.txt beside the app — NEVER in
#  code. Fail closed: no password file, no entry through the tunnel.
#  LOCAL requests pass untouched: Cloudflare edge headers (Cf-Ray /
#  Cf-Connecting-IP) are set by the edge and cannot be forged remotely,
#  so their absence means the request was born on this machine.
# ════════════════════════════════════════════════════════════
GATE_PASSWORD_FILE = SIMPLEJACK_ROOT / "config" / "gate.password.txt"
_GATE_PW_CACHE = {"pw": None, "mtime": 0.0}

def _gate_password():
    """Load the gate password; re-read if the file changed on disk."""
    try:
        mt = GATE_PASSWORD_FILE.stat().st_mtime
        if _GATE_PW_CACHE["mtime"] != mt:
            _GATE_PW_CACHE["pw"] = GATE_PASSWORD_FILE.read_text(encoding="utf-8").strip()
            _GATE_PW_CACHE["mtime"] = mt
        return _GATE_PW_CACHE["pw"] or ""
    except Exception:
        return ""

def _gate_allows(handler):
    """True if the request may pass. Local traffic passes (our own
    machine — native window, localhost tooling). Tunnel traffic must
    present the password."""
    if not (handler.headers.get("Cf-Ray") or handler.headers.get("Cf-Connecting-IP")):
        return True
    import base64, hmac
    pw = _gate_password()
    if not pw:
        log("GATE: tunnel request rejected — no password file (fail closed).")
        return False
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:], validate=True).decode("utf-8", "ignore")
        supplied = decoded.split(":", 1)[-1]
    except Exception:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), pw.encode("utf-8"))

def _gate_denied(handler):
    """Send the browser-native sign-in challenge."""
    handler.send_response(401)
    handler.send_header("WWW-Authenticate", 'Basic realm="SimpleJack"')
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Connection", "close")
    handler.end_headers()
    try:
        handler.wfile.write(b"Sign in required.")
    except Exception:
        pass

# ════════════════════════════════════════════════════════════
#  LLM ENDPOINT — THE MODEL HUB, THE ONE ENGINE
#  2026-08-23 (friend's order): keys are OUT of the app. The app holds
#  ZERO credentials. Every completion routes through the hub on
#  localhost; the hub vault owns every key and never leaves this machine.
# ════════════════════════════════════════════════════════════
MODEL_HUB_BASE = "http://127.0.0.1:8123"
MODEL_HUB_MODELS_URL = MODEL_HUB_BASE + "/v1/models"
MODEL_HUB_CHAT_URL = MODEL_HUB_BASE + "/v1/chat/completions"

# Context window sizes per model (tokens). Used for compaction.
MODEL_CONTEXTS = {
    # Z.AI
    "glm-5.2": 32768,
    "glm-5-turbo": 32768,
    # ZenMux (varies, conservative defaults)
    "qwen/qwen3.7-plus": 32768,
    "deepseek/deepseek-v4-pro": 65536,
    "deepseek/deepseek-v4-flash": 1000000,
    "deepseek/deepseek-v4-flash-free": 1000000,
    "moonshotai/kimi-k3": 131072,
    "tencent/hy3": 32768,
    "inclusionai/ling-3.0-flash": 32768,
    # OpenRouter defaults (will be set per-model when fetched)
    "openrouter/default": 8192,
    # GitHub Models
    "openai/gpt-4o": 131072,
    "openai/gpt-4o-mini": 131072,
    "openai/o4-mini": 131072,
    "meta-llama/Llama-3.3-70B-Instruct": 131072,
    "deepseek-ai/DeepSeek-R1": 131072,
    "mistralai/Mistral-Small-24B-Instruct-2501": 131072,
    # Local Ollama — MUST match the num_ctx cap (16384) used in call_model,
    # or compaction thinks there is more room than there is and overflows.
    "aila_model:latest": 16384,
    "deepseek-r1:latest": 16384,
    "ailacode:latest": 16384,
    "legend:latest": 16384,
    "qwen2.5:7b": 16384,
}

# Active model — default to AILA local 9B.
# PERSISTED to dispatch/.active-model so a restart keeps the operator's choice.
# Symptom this fixes: every relaunch silently reset to aila_model and lost
# the cloud/tool-capable model the operator had picked. They'd see narration fire,
# then nothing, because the wrong model was answering.
ACTIVE_MODEL_FILE = SIMPLEJACK_ROOT / "dispatch" / ".active-model"

def _load_active_model():
    """Read the persisted model choice. Falls back to aila_model if absent."""
    try:
        if ACTIVE_MODEL_FILE.exists():
            m = ACTIVE_MODEL_FILE.read_text(encoding="utf-8").strip()
            if m:
                return m
    except Exception:
        pass
    return "aila_model"

AILA_MODEL = _load_active_model()

def set_active_model(model_name):
    """Module-level setter so handlers don't need `global` (Python scoping).
    Also persists to disk so restarts keep the operator's choice."""
    global AILA_MODEL
    AILA_MODEL = model_name
    try:
        ACTIVE_MODEL_FILE.write_text(model_name, encoding="utf-8")
    except Exception as e:
        log(f"could not persist active model: {e}")

# ════════════════════════════════════════════════════════════
#  LOG
# ════════════════════════════════════════════════════════════
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [simpleJACK] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ════════════════════════════════════════════════════════════
#  NARRATION — LAW 1, THE SPINE
#  Ported sanitizer from morphyeo (paths→"the file", slashes→"then",
#  long-text sentence-boundary chunking). Reused per LAW 2.
#  Atomic write: .tmp then rename (pattern from Emissary.py — safer than
#  a direct write, the conductor never reads a half-written file).
# ════════════════════════════════════════════════════════════
def _sanitize_for_ears(text):
    """The queue is for EARS. Strip everything Piper can't speak cleanly."""
    # Strip code blocks
    clean = re.sub(r'```.*?```', ' ', text, flags=re.DOTALL)
    clean = clean.replace('```', ' ').replace('`', ' ')
    # Windows file paths → "the file"
    clean = re.sub(r'[A-Za-z]:\\[^\s"\'<>\]]+', 'the file', clean)
    # Unix-style paths
    clean = re.sub(r'/[^\s"\'<>]+\.\w{2,5}', 'the file', clean)
    # Slashes → "then" (friend's rule — Piper says "slash" otherwise)
    clean = clean.replace('\\', ' then ').replace('/', ' then ')
    # Arrows → "then"
    for arrow in ('→', '=>', '->', '<-', '←'):
        clean = clean.replace(arrow, ' then ')
    # Hyphens Piper reads as circumflex → "then"
    for h in (' - ', ' — ', ' – '):
        clean = clean.replace(h, ' then ')
    # Markdown symbols
    clean = clean.replace('#', ' ').replace('*', ' ').replace('|', ' ')
    clean = clean.replace('{{', ' ').replace('}}', ' ')
    clean = clean.replace('[', ' ').replace(']', ' ')
    clean = clean.replace('(', ' ').replace(')', ' ')
    clean = re.sub(r'[<>{}^~|]', ' ', clean)
    # Collapse whitespace
    clean = re.sub(r'\s+', ' ', clean).strip()
    # Collapse "then then then" chains from multiple replacements
    clean = re.sub(r'( then){2,}', ' then ', clean)
    return clean

def narrate(text):
    """Write to the narration queue. The spine. Called every turn.
    THE FOLDER IS THE SWITCH (rule, 2026-08-01):
    morPHYtrek makes the folder. If SimpleJack does NOT see the folder,
    SimpleJack does NOT write the file. Not more. Not less.
    No folder creation here. No fallback. No error spam."""
    if not text or not text.strip():
        return
    # Re-resolve the switch every call: the folder may have appeared or
    # been deleted while SimpleJack is running. One queue only — the
    # portable one next to the launcher. No canonical. No fallback.
    portable_queue = SIMPLEJACK_ROOT / "morphytrek_data" / "queue"
    if portable_queue.exists():
        NARRATION_QUEUE = portable_queue
    else:
        return  # no mouth folder — text only, no narration files
    clean = _sanitize_for_ears(text)

    # Chunk long text at sentence boundaries so Piper never cuts mid-sentence
    MAX_CHUNK = 2000
    chunks = [clean]
    if len(clean) > MAX_CHUNK:
        chunks = []
        sentences = re.split(r'(?<=[.!?])\s+', clean)
        buf = ""
        for s in sentences:
            if len(buf) + len(s) + 1 <= MAX_CHUNK:
                buf = (buf + " " + s).strip()
            else:
                if buf:
                    chunks.append(buf)
                if len(s) > MAX_CHUNK:
                    words = s.split(" ")
                    hard = ""
                    for w in words:
                        if len(hard) + len(w) + 1 <= MAX_CHUNK:
                            hard = (hard + " " + w).strip()
                        else:
                            if hard:
                                chunks.append(hard)
                            hard = w
                    buf = hard
                else:
                    buf = s
        if buf:
            chunks.append(buf)

    # Atomic write per chunk: .tmp then rename
    for chunk in chunks:
        ts = int(time.time() * 1000000)
        final = NARRATION_QUEUE / f"simplejack_{ts}.json"
        tmp = final.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"text": chunk, "source": "aila", "engine": "piper"}),
            encoding="utf-8"
        )
        tmp.replace(final)  # atomic on Windows + Linux
        time.sleep(0.05)


# ════════════════════════════════════════════════════════════
#  VOICE PILL (friend 2026-08-27, turn 2) — the folder is the
#  switch AND the button tends the mouth. Green = ON, red = OFF.
#  ON:  make the queue folder (the switch) + spawn morPHYtrek.py
#       in a visible window, recording its PID.
#  OFF: delete the folder + stop ONLY that mouth PID (tree).
#  Never touches any other python. Never kills what it did not start.
# ════════════════════════════════════════════════════════════
_MOUTH_PIDFILE = SIMPLEJACK_ROOT / "morphytrek_data" / "mouth.pid"


def _bundle_python():
    """Portable python discovery — same order the launcher uses."""
    cand = SIMPLEJACK_ROOT / "runtime" / "python.exe"
    if cand.exists():
        return str(cand)
    cand = SIMPLEJACK_ROOT / "python.exe"
    if cand.exists():
        return str(cand)
    return sys.executable


def _mouth_pid():
    """Return the mouth's live PID, or None. Pidfile first, then a
    commandline scan so a bat-started mouth is still found."""
    pid = None
    try:
        if _MOUTH_PIDFILE.exists():
            pid = int(_MOUTH_PIDFILE.read_text(encoding="utf-8").strip())
    except Exception:
        pid = None
    if pid:
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, pid)
            if h:
                code = ctypes.c_ulong()
                alive = k32.GetExitCodeProcess(h, ctypes.byref(code))
                k32.CloseHandle(h)
                if alive and code.value == 259:
                    return pid
        except Exception:
            pass
    # fallback: find a python running morPHYtrek.py under this bundle
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get",
             "processid,commandline", "/format:csv"],
            capture_output=True, text=True, timeout=10
        ).stdout
        root = str(SIMPLEJACK_ROOT)
        for line in out.splitlines():
            if "morPHYtrek.py" in line and root in line:
                cols = [c.strip() for c in line.strip().split(",")]
                if cols and cols[-1].isdigit():
                    return int(cols[-1])
    except Exception:
        pass
    return None


def _voice_state():
    """Green/red truth: the switch (queue folder) exists."""
    return (_PORTABLE_QUEUE.exists(), _mouth_pid())


def voice_on():
    """SWITCH ON — always ends in the ON state:
    mouth down -> engage morPHYtrek.bat (bat creates the queue folder).
    mouth up   -> ensure the queue folder exists (switch = folder)."""
    if _mouth_pid():
        try:
            _PORTABLE_QUEUE.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log(f"voice_on: mkdir failed {e}")
        return True  # already speaking
    try:
        CREATE_NEW_CONSOLE = 0x00000010
        p = subprocess.Popen(
            ["cmd.exe", "/c", f'start "MORPHYTREK-PORTABLE" "{SIMPLEJACK_ROOT / "morPHYtrek.bat"}"'],
            creationflags=CREATE_NEW_CONSOLE,
            cwd=str(SIMPLEJACK_ROOT),
        )
        log("voice_on: engaged morPHYtrek.bat")
        return True
    except Exception as e:
        log(f"voice_on: spawn failed {e}")
        return False


def voice_off():
    """Delete the folder (switch OFF) and stop ONLY the mouth PID tree."""
    pid = _mouth_pid()
    if pid:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, text=True, timeout=10
            )
            log(f"voice_off: stopped mouth pid {pid}")
        except Exception as e:
            log(f"voice_off: taskkill failed {e}")
    try:
        if _MOUTH_PIDFILE.exists():
            _MOUTH_PIDFILE.unlink()
    except Exception:
        pass
    # The switch itself: remove the queue folder (and keep going on failure —
    # the folder is the switch; deleting it is the off-state).
    try:
        import shutil
        if _PORTABLE_QUEUE.exists():
            shutil.rmtree(_PORTABLE_QUEUE, ignore_errors=True)
    except Exception as e:
        log(f"voice_off: rmtree failed {e}")
    return True


# ════════════════════════════════════════════════════════════
#  INSTRUCTION SET — AGENTS.md + I_AM.txt (canonical)
#  Not "the soul" — the instruction set. The chat persona.
# ════════════════════════════════════════════════════════════
_INSTRUCTION_SET = ""

# Optimizer venv python — the canonical interpreter for all SKILLS tools.
# All skills run under this python so they have watchdog + every file reader.
# Portable (2026-08-01): python beside simplejack.py > MorPHYvenv beside it.
_skills_py = SIMPLEJACK_ROOT / "runtime" / "python.exe"
if not _skills_py.exists():
    _skills_py = SIMPLEJACK_ROOT / "MorPHYvenv" / "Scripts" / "python.exe"
SKILLS_PYTHON = str(_skills_py) if _skills_py.exists() else "python"
SKILLS_DIR = SIMPLEJACK_ROOT / "SKILLS"

# THE LEGEND — Python module. One source of truth for skills.
# When '&' appears in a prompt, legend.resolve() finds the skill,
# builds the command, and we queue + fire it before the model runs.
# The model never gets a vote on whether a skill fires. See legend.py.
import legend as LEGEND


def _build_legend_from_skills():
    """Return a TIGHT summary of active skills — not the full 107-entry dump.
    The legend model handles full resolution. The chat model just needs to know
    what verbs exist without hallucinating about dead tools."""
    legend_file = SKILLS_DIR / "LEGEND.md"
    if not legend_file.exists():
        return "## THE LEGEND\n\n(No skills registered.)\n"
    try:
        lines = legend_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        verbs = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(" :: ", 2)
            if len(parts) >= 2:
                verb = parts[0].strip()
                desc = parts[1].strip()[:60]
                verbs.append(f"  {verb} — {desc}")
        if not verbs:
            return "## THE LEGEND\n\n(No skills registered.)\n"
        return "## THE LEGEND — " + str(len(verbs)) + " skills available\n\n" + "\n".join(verbs) + "\n"
    except Exception as e:
        return f"## THE LEGEND\n\n(error: {e})\n"


def _load_instructions():
    global _INSTRUCTION_SET
    if _INSTRUCTION_SET:
        return _INSTRUCTION_SET
    parts = []
    agents_md = SIMPLEJACK_ROOT / "AGENTS.md"
    # I_AM.txt lives in the PARENT project root, not in SIMPLEJACK
    iam_txt = MORPHYES_ROOT / "I_AM.txt"
    # BREADCRUMBS.md — the rolling verdict file (friend 2026-09-08).
    # Layer 1 of the two-layer memory design: the tiny crumb every model
    # inherits at boot. Verdict-first, never re-derived. The loop is the depth.
    breadcrumbs_md = MORPHYES_ROOT / "BREADCRUMBS.md"
    if breadcrumbs_md.exists():
        parts.append(breadcrumbs_md.read_text(encoding="utf-8", errors="ignore"))
    if agents_md.exists():
        parts.append(agents_md.read_text(encoding="utf-8", errors="ignore"))
    if iam_txt.exists():
        iam_raw = iam_txt.read_text(encoding="utf-8", errors="ignore")
        # PAYLOAD CAP (repair 2026-09-20): I_AM.txt grew to 5.35 MILLION chars
        # (~1.3M tokens) — 6x a 200K card's whole room, stuffed into EVERY
        # request. Providers sometimes chewed it (Thursday) and sometimes
        # dropped/reset the stream (Friday night on): empty streams, vanished
        # RSVP, steers with nowhere to land. The identity lives at the TOP of
        # the file; the first 40K chars carry it. One cap, root cause killed.
        if len(iam_raw) > 40000:
            log(f"PAYLOAD CAP: I_AM.txt {len(iam_raw)} chars -> 40000 (identity head kept)")
            parts.append(iam_raw[:40000])
        else:
            parts.append(iam_raw)
    base = "\n\n---\n\n".join(parts) if parts else \
        "You are AILA, the SimpleJack assistant. Be direct. No filler. Address the user as the operator, never by a personal name."

    # The SimpleJack operating rule — REPLACES the old morphyeo "TOOL-FIRST"
    # rule. Talking to the operator is NOT a tool call. Call a tool only when he
    # asked for something to be done.
    base += (
        "\n\n---\n\n"
        "## SIMPLEJACK OPERATING RULE\n"
        "You do five things, and only these:\n"
        "1. CHAT — answer, explain, think with the operator. No tool needed. Most turns are this.\n"
        "2. STACK A COMMAND — when the operator wants something DONE (run a skill, execute a script), "
        "CALL the queue_command tool. Do NOT describe the command. Do NOT say 'I am stacking' "
        "or 'I am calling'. The tool call IS the action. Your reply text is only the short "
        "spoken acknowledgment. Copy the canonical line verbatim from the LEGEND, swap the "
        "<placeholders>, pass it as the 'command' argument to queue_command. "
        "Then call stack_go. Both tool calls in the same turn.\n"
        "3. GO LOOK — call read_file to inspect a file when the operator asks you to look.\n"
        "4. WRITE A FILE — call write_file when the operator wants you to CREATE or OVERWRITE a file. "
        "NEVER use queue_command with a python one-liner to write files. The write_file tool "
        "handles paths, directories, and content cleanly. No quote escaping. No shell tricks.\n"
        "5. RUN THE STACK — call stack_go to tell dispatch to start, stack_pause to pause.\n\n"
        "RULE: If the operator is just talking to you, talk back. Do NOT call a tool. "
        "A conversation is not a task. A question is not a task. "
        "Call queue_command ONLY when he used a LEGEND verb. "
        "Your acknowledgment phrase is: 'you betcha.' Nothing more about the command.\n\n"
        + _build_legend_from_skills()
        + "\nVerbs that are CHAT, not actions: explain, what, why, how, who, when, where, "
        "are you, do you, can you, think, feel, tell me about.\n\n"
        "## morPHYspider — WEB RETRIEVAL\n"
        "When the operator sends a URL and asks a question about it, use the spider verb: "
        "&spider <url> <question>. This queues morPHYspider.py which calls LightPanda "
        "in WSL2 to fetch the page, strip bullshit, chunk it, cross-reference against "
        "the prompt using a local model, and narrate the results. "
        "Say 'you betcha, spider crawling.' Nothing else — the spider reports back.\n"
        "The chat does not wait. The spider works. The results appear on screen.\n"
    )
    _INSTRUCTION_SET = base
    log(f"Instruction set loaded: {len(_INSTRUCTION_SET)} chars")
    return _INSTRUCTION_SET

# ════════════════════════════════════════════════════════════
#  THE 4 TOOLS (well, 3 tools + chat — chat is the default, not a tool)
# ════════════════════════════════════════════════════════════
STACK_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_FILE.touch(exist_ok=True)
CURRENT_FILE.touch(exist_ok=True)

def _load_registered_skills():
    """One-time read of LEGEND.md to get all registered skill filenames.
    Returns set of script basenames that AILA is allowed to queue."""
    if not hasattr(_load_registered_skills, "_cache"):
        legend = SKILLS_DIR / "LEGEND.md"
        skills = set()
        if legend.exists():
            for line in legend.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Format: verb :: desc :: canonical_command
                parts = line.split(" :: ", 2)
                if len(parts) == 3:
                    canon = parts[2].strip()
                    # Extract the quoted script name from the canonical command
                    m = re.search(r'"([^"]+\.py)"', canon)
                    if m:
                        skills.add(m.group(1))
        _load_registered_skills._cache = skills
    return _load_registered_skills._cache


def _verify_command(line):
    """Verify a queued command before it fires.
    Checks:
      1. Starts with the canonical interpreter path (python.exe or streamlit)
      2. Script name matches a registered skill in LEGEND.md
      3. Placeholder substitution is balanced (quotes match, flags have values)
    Returns (ok: bool, reason: str)."""
    line = line.strip()

    # 1. Must start with a known interpreter
    if not (line.startswith(SKILLS_PYTHON) or line.startswith("streamlit run")):
        # Allow raw python.exe if it points to the venv
        if not line.startswith("python") and not line.startswith(SKILLS_PYTHON):
            return False, f"Command does not start with the canonical interpreter. Got: {line[:80]}"

    # 2. Extract script name and check it is registered
    m = re.search(r'"([^"]+\.py)"', line)
    if not m:
        # streamlit run file.py (no quotes) — try unquoted
        m2 = re.search(r'(?:streamlit run\s+|python(?:\.exe)?)\s+(\S+\.py)', line)
        if m2:
            script_name = m2.group(1)
        else:
            return False, "Cannot find a .py script in the command."
    else:
        script_name = m.group(1)

    # Strip path, get just the basename
    basename = Path(script_name).name
    registered = _load_registered_skills()
    if registered and basename not in registered:
        # Fuzzy: maybe the filename is close
        close = [s for s in registered if s.lower().replace("_", "").replace("-", "")
                 == basename.lower().replace("_", "").replace("-", "")]
        if close:
            return False, f"Script '{basename}' not registered. Did you mean '{close[0]}'?"
        return False, f"Script '{basename}' is not in the LEGEND. AILA will not run unregistered scripts."

    # 3. Check quote balance
    in_quote = False
    quote_char = None
    for ch in line:
        if ch in ('"', "'") and not in_quote:
            in_quote = True
            quote_char = ch
        elif ch == quote_char and in_quote:
            in_quote = False
            quote_char = None
    if in_quote:
        return False, "Unbalanced quotes in command. Every opening quote needs a closing quote."

    # 4. Check that flags (starting with --) are followed by a value (not another flag or end)
    #    Allow flags without values (boolean flags like --watch, --no-filter) and flags with =
    tokens = line.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            # Flags ending with = are self-contained (--name=value)
            if "=" in tok:
                i += 1
                continue
            # Boolean flags (no value expected): common ones
            if tok in ("--watch", "--no-filter", "--help", "--version", "--force"):
                i += 1
                continue
            # Next token should exist and not be another flag
            if i + 1 >= len(tokens):
                return False, f"Flag '{tok}' at end of command has no value."
            if tokens[i + 1].startswith("--"):
                # Could be multiple boolean flags — allow it
                i += 1
                continue
            i += 2
        else:
            i += 1

    return True, "ok"


def _find_similar_skills(query):
    """Find skills whose verb or description matches a query.
    Returns list of (verb, description, canonical_command) tuples, best match first.
    Scoring: verb-in-query or query-in-verb is strongest signal.
    Description word overlap is weaker. Stopwords ignored."""
    legend = SKILLS_DIR / "LEGEND.md"
    if not legend.exists():
        return []
    results = []
    q = query.lower().strip()
    q_words = set(w for w in q.replace("_", " ").replace("-", " ").split()
                  if len(w) > 2 and w not in ("the", "and", "for", "with", "that", "this", "from", "are", "was", "not"))
    for line in legend.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(" :: ", 2)
        if len(parts) != 3:
            continue
        verb, desc, canon = parts[0], parts[1], parts[2]
        v = verb.lower()
        d = desc.lower()
        score = 0

        # VERB MATCH — strongest signals
        # "clone" matches verb "clone" perfectly
        if q == v:
            score = 200
        # query words contain the verb: "clone voice" → verb "clone"
        elif any(v == w for w in q_words):
            score = 150
        # verb words contain a query word: "voice_test" → "voice"
        elif any(w in v.replace("_", " ").replace("-", " ").split() for w in q_words if len(w) > 2):
            score = 120

        # DESCRIPTION MATCH — weaker but still useful
        if score == 0:
            d_words = set(d.replace("_", " ").replace("-", " ").split())
            overlap = q_words & d_words
            if overlap:
                # Only count meaningful words
                score = 30 + 15 * len(overlap)

        if score > 0:
            results.append((verb, desc, canon, score))
    results.sort(key=lambda x: -x[3])
    return [(r[0], r[1], r[2]) for r in results[:3]]


def tool_queue_command(command):
    """Action 2: append one line to STACK/queue.txt.
    Verification gate: checks interpreter, registered skill, quote balance, flag syntax.
    If verification fails, AILA does NOT run the stack. She narrates the failure."""
    line = command.strip()
    if not line:
        return "ERROR: empty command"

    # VERIFICATION GATE
    ok, reason = _verify_command(line)
    if not ok:
        log(f"COMMAND REJECTED: {reason}")
        narrate(f"Command blocked. {reason}")
        return f"REJECTED: {reason}"

    with open(QUEUE_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    depth = _queue_depth()
    return f"Queued. Stack depth now {depth}."

def tool_read_file(path):
    """Action 3: read a file, return contents (read-only)."""
    p = Path(path.strip().strip('"').strip("'"))
    if not p.exists():
        # try relative to project root
        alt = MORPHYES_ROOT / path.strip().strip('"').strip("'")
        if alt.exists():
            p = alt
    if not p.exists():
        return f"File not found: {path}"
    try:
        return p.read_text(encoding="utf-8", errors="ignore")[:20000]
    except Exception as e:
        return f"Error reading {path}: {e}"

def tool_write_file(path, content, append=False):
    """Action 3b: write content to a file. Creates dirs.
    CHUNKED WRITES: append=True continues a file.
    Large files are written as a first small call (append=False) followed by
    append=True chunks — no single reply ever has to carry a whole app."""
    p = Path(path.strip().strip('"').strip("'"))
    if not p.is_absolute():
        p = MORPHYES_ROOT / p
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if append:
            with open(p, "a", encoding="utf-8") as f:
                f.write(content)
            return f"Appended {len(content)} chars to {p} (total now {p.stat().st_size})"
        p.write_text(content, encoding="utf-8")
        return f"Written {len(content)} chars to {p}"
    except Exception as e:
        return f"Error writing {p}: {e}"


def tool_stack_go():
    """Action 4a: tell dispatch to run."""
    flag = SIMPLEJACK_ROOT / "dispatch" / ".run"
    flag.write_text("run\n", encoding="utf-8")
    return "Stack GO. Dispatch will run the queue."

def tool_stack_pause():
    """Action 4b: tell dispatch to pause."""
    flag = SIMPLEJACK_ROOT / "dispatch" / ".run"
    flag.write_text("pause\n", encoding="utf-8")
    return "Stack PAUSED."

def tool_fetch_url(url):
    """Action 5a: fetch a URL and return its text content. Native web fetch.
    Tries Jina Reader first (clean markdown output), then direct urllib.
    For Twitter/X URLs, tries nitter mirrors as fallback."""
    url = url.strip().strip('"').strip("'")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    import re

    # ── Twitter/X detection — try nitter mirrors first ──
    if "x.com/" in url or "twitter.com/" in url:
        # Extract the tweet path (e.g. /dr_cintas/status/2082568767586894179)
        match = re.search(r"(?:x\.com|twitter\.com)/(.+)", url)
        if match:
            path = match.group(1)
            nitter_mirrors = [
                f"https://nitter.net/{path}",
                f"https://nitter.privacydev.net/{path}",
                f"https://nitter.poast.org/{path}",
            ]
            for mirror in nitter_mirrors:
                try:
                    import urllib.request
                    req = urllib.request.Request(mirror, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        raw = resp.read(16000).decode("utf-8", errors="ignore")
                        text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.S|re.I)
                        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S|re.I)
                        text = re.sub(r"<[^>]+>", " ", text)
                        text = re.sub(r"\s+", " ", text).strip()
                        if len(text) > 50:  # Got real content
                            return text[:8000] + ("... (truncated)" if len(text) > 8000 else "")
                except Exception:
                    continue

    # ── Try Jina Reader first (returns clean markdown from any URL) ──
    try:
        import urllib.request
        jina_url = f"https://r.jina.ai/{url}"
        req = urllib.request.Request(jina_url, headers={
            "User-Agent": "Mozilla/5.0 MorPHYes/1.0",
            "Accept": "text/plain"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read(16000).decode("utf-8", errors="ignore")
            if len(raw) > 100:
                return raw[:8000] + ("... (truncated)" if len(raw) > 8000 else "")
    except Exception:
        pass  # Jina blocked or down, fall through to direct

    # ── Direct fetch fallback ──
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MorPHYes/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read(16000).decode("utf-8", errors="ignore")
            text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.S|re.I)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S|re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:8000] + ("... (truncated)" if len(text) > 8000 else "")
    except Exception as e:
        return f"Error fetching {url}: {e}"

def tool_web_search(query):
    """Action 6: web search via DuckDuckGo. Returns titles and snippets."""
    query = query.strip().strip('"').strip("'")
    if not query:
        return "Error: empty search query"
    try:
        import urllib.request, urllib.parse, json, re
        # DuckDuckGo lite HTML search — no API key needed
        params = urllib.parse.urlencode({"q": query})
        req = urllib.request.Request(
            f"https://lite.duckduckgo.com/lite/?{params}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read(32000).decode("utf-8", errors="ignore")
        # Parse DuckDuckGo lite results
        results = []
        for m in re.finditer(r'<a[^>]+class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S|re.I):
            link = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            results.append(f"- {title}: {link}")
        for m in re.finditer(r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>', html, re.S|re.I):
            snippet = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if results:
                results[-1] += f"\n  {snippet}"
        if not results:
            # Fallback: try instant answer API
            req2 = urllib.request.Request(
                f"https://api.duckduckgo.com/?{params}&format=json&no_html=1",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                ddg = json.loads(resp2.read().decode("utf-8"))
            if ddg.get("AbstractText"):
                return f"DDG Answer: {ddg['AbstractText']}\nSource: {ddg.get('AbstractURL', '')}"
            if ddg.get("RelatedTopics"):
                for t in ddg["RelatedTopics"][:5]:
                    if isinstance(t, dict) and t.get("Text"):
                        results.append(f"- {t['Text']}: {t.get('FirstURL', '')}")
        if not results:
            return f"No results found for: {query}"
        return "\n".join(results[:8])
    except Exception as e:
        return f"Search error: {e}"

def tool_browse(url):
    """Action 6b: browse a URL in the EXISTING logged-in Chrome session via CDP (cookie_monster).
    Rides the operator's Chrome on port 9222 — reuses their cookies/session, opens a tab, reads page text.
    Use for ANY authenticated page (X bookmarks, logged-in dashboards, private content) that
    fetch_url cannot reach. Returns the page text."""
    url = url.strip().strip('"').strip("'")
    if not url:
        return "Error: empty URL"
    try:
        import sys
        sys.path.insert(0, str(SIMPLEJACK_ROOT / "SKILLS"))
        from cookie_monster import CookieMonster
        cm = CookieMonster()
        text = cm.visit(url, read=True)
        if text:
            return f"--- Page Content ({len(text)} chars) ---\n{text[:3000]}" + (f"\n... ({len(text) - 3000} more chars)" if len(text) > 3000 else "")
        return "No content extracted. CDP may be unreachable or the page returned nothing."
    except Exception as e:
        return f"Browse error: {e}"

def _kill_process_tree(command_hint=""):
    """Kill orphaned shell children from a timed-out run_command call.
    NEVER touches python/pythonw (LAW 3). Targets only findstr/more/type-style
    console children whose parent cmd is already dead (the deadlock case)."""
    import subprocess as _sp
    try:
        out = _sp.run(
            ["wmic", "process", "where",
             "name='findstr.exe' or name='more.com' or name='sort.exe'",
             "get", "ProcessId,ParentProcessId,Name"],
            capture_output=True, text=True, timeout=15,
            stdin=_sp.DEVNULL
        ).stdout
        live_parents = set()
        for line in _sp.run(["tasklist", "/FO", "CSV"],
                            capture_output=True, text=True, timeout=15,
                            stdin=_sp.DEVNULL).stdout.splitlines()[1:]:
            pid = line.split('","')[1].strip('"') if '","' in line else ""
            if pid.isdigit():
                live_parents.add(int(pid))
        killed = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0].endswith(".exe"):
                ppid, pid = int(parts[1]), int(parts[2])
                if ppid not in live_parents:  # parent dead = orphan holding a pipe
                    r = _sp.run(["taskkill", "/F", "/PID", str(pid)],
                                capture_output=True, timeout=10, stdin=_sp.DEVNULL)
                    killed.append(f"{parts[0]}:{pid}")
        log(f"timeout cleanup: killed orphaned pipe-holders {killed or 'none'}")
    except Exception as e:
        log(f"timeout cleanup failed (non-fatal): {e}")

def tool_run_command(command):
    """Action 7: run a shell command and return stdout. Use for system tasks."""
    command = command.strip().strip('"').strip("'")
    if not command:
        return "Error: empty command"
    import subprocess
    proc = None
    try:
        # TOOL-HANG FIX (friend 2026-09-10): the old subprocess.run(timeout=300)
        # wedged the agent loop when a command spawned a server (restart_world.cmd,
        # taskkill+start chains) — the child held the pipe and the loop froze at
        # the tool call for 10+ minutes (two freezes at turn 7 in the log).
        # Popen + communicate gives us the REAL pid so a timeout kills the actual
        # spawned tree by PID (never by name — python.exe is untouchable, LAW 3).
        proc = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True, cwd=str(MORPHYES_ROOT),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
        try:
            output, error = proc.communicate(timeout=300)
            output = output[:20000] if output else ""
            error = error[:2000] if error else ""
            if error and proc.returncode != 0:
                return f"[exit {proc.returncode}]\n{output}\nSTDERR: {error}"
            return output if output else "(no output)"
        except subprocess.TimeoutExpired:
            # Kill the ACTUAL spawned tree by PID. /T = tree, /F = force.
            # This is PID-targeted, not name-targeted: python.exe itself is
            # never matched by name (LAW 3). Only this command's own tree dies.
            try:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               capture_output=True, timeout=15, stdin=subprocess.DEVNULL)
            except Exception:
                pass
            # Belt and braces: the 2026-09-02 orphan cleanup still runs after.
            _kill_process_tree(command)
            return "Command timed out after 60 seconds"
    except Exception as e:
        return f"Command error: {e}"

def _queue_depth():
    try:
        lines = [l for l in QUEUE_FILE.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")]
        return len(lines)
    except Exception:
        return 0

def _current_command():
    try:
        c = CURRENT_FILE.read_text(encoding="utf-8").strip()
        return c if c else "(idle)"
    except Exception:
        return "(idle)"

# Skip patterns for file tree — venvs, node_modules, .git, etc.
_TREE_SKIP = {".git", "__pycache__", "node_modules", ".hg", ".svn", "site-packages",
             "Lib", "Scripts", "Include", "pyvenv.cfg", ".hermes",
             "MorPHYesVoice", "morphytrek_data", "morphyeo_state",
             "Tailscale", "Tailscale_files", "Games", "Music", "Pictures",
             "Videos", "Downloads", "Documents", "Contacts", "3D Objects",
             "Favorites", "Links", "Saved Games", "Searches", "Intel",
             "OneDrive", "appdata", "AppData", "Application Data",
             "Cookies", "Local Settings", "NetHood", "PrintHood",
             "Recent", "SendTo", "Start Menu", "Templates", "VirtualBox VMs",
             "VMware", "PerfLogs", "Recovery", "System Volume Information",
             "$Recycle.Bin", "ProgramData", "Program Files", "Program Files (x86)",
             "Windows", "Users"}
_TREE_MAX_DEPTH = 4
_TREE_MAX_CHILDREN = 60

def _build_file_tree():
    """Walk Desktop and return nested dict tree. Real file system, real time."""
    desktop = Path.home() / "Desktop"
    def _walk(folder, depth):
        if depth > _TREE_MAX_DEPTH:
            return []
        try:
            items = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return []
        children = []
        for item in items:
            name = item.name
            if name in _TREE_SKIP:
                continue
            if item.is_dir():
                node = {"name": name, "type": "dir", "path": str(item)}
                kids = _walk(item, depth + 1)
                if kids:
                    node["children"] = kids[:_TREE_MAX_CHILDREN]
                children.append(node)
            else:
                children.append({"name": name, "type": "file", "path": str(item)})
        return children[:_TREE_MAX_CHILDREN]

    return {"name": "Desktop", "type": "dir", "path": str(desktop), "children": _walk(desktop, 0)}


def tool_ui(action, args=""):
    """Action 10: THE NATIVE UI HANDS — drive any Windows desktop app.
    Wraps SKILLS/uihands.py so the model never builds command lines.
    action: windows|dump|find|click|clickxy|type|keys|toggle|launch|walk
    args:   the subcommand's arguments, exactly as LEGEND describes.
    """
    action = (action or "").strip()
    if not action:
        return "Error: empty action. Use: windows, dump <title>, find, click, clickxy, type, keys, toggle, launch, walk."
    import shlex
    import subprocess
    from pathlib import Path
    bundle = Path(__file__).resolve().parent
    py = bundle / "runtime" / "python.exe"
    tool = bundle / "SKILLS" / "uihands.py"
    if not tool.exists():
        return f"Error: uihands.py not found at {tool}"
    cmd = [str(py), str(tool), action]
    # split args safely (shlex handles quoted text with spaces)
    try:
        cmd.extend(shlex.split(args or ""))
    except ValueError as e:
        return f"Error: bad quoting in args: {e}"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=120, cwd=str(bundle))
        out = (proc.stdout or "") + (proc.stderr or "")
        return out.strip()[:4000] or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: ui command timed out (120s)"
    except Exception as e:
        return f"Error: {e!r}"


TOOLS = {
    "queue_command": tool_queue_command,
    "read_file":     tool_read_file,
    "write_file":    tool_write_file,
    "fetch_url":     tool_fetch_url,
    "web_search":    tool_web_search,
    "browse":        tool_browse,
    "run_command":   tool_run_command,
    "stack_go":      tool_stack_go,
    "stack_pause":   tool_stack_pause,
    "ui":            tool_ui,
}

# ════════════════════════════════════════════════════════════
#  ASYNC CONVERSATION STORE — agent_loop runs in a thread.
#  POST /api/converse returns immediately with a request_id.
#  GET /api/status/<request_id> polls for the result.
#  The HTTP thread is free. AILA thinks in the background.
#  The browser polls every 500ms and renders when done.
# ════════════════════════════════════════════════════════════
_pending = {}  # request_id -> {"status": "thinking"|"done"|"error", "result": {...}}
_pending_lock = threading.Lock()
_reasoning_buffer = {}  # request_id -> accumulated reasoning text for RSVP streaming
_wait_queue = []  # 2026-09-12 PHONE FIX: FIFO of held messages (phone/laptop one line)
_wait_queue_lock = threading.Lock()


def _drain_wait_queue():
    """2026-09-12 PHONE FIX: fire the next held message the moment the current
    run finishes. Called from _run_agent_async completion paths. One process,
    one brain, one line — phone and laptop share it.
    2026-09-12 TICKET FIX: the request_id travels WITH the held message, so
    the phone's poll ticket stays valid from standby -> thinking -> done.
    The sender watches ONE ticket the whole way. No orphaned polls, no
    answers that exist but can't be seen."""
    with _wait_queue_lock:
        if not _wait_queue:
            return
        nxt = _wait_queue.pop(0)
    request_id = nxt.get("request_id") or str(uuid.uuid4())[:12]
    log(f"PHONE QUEUE: firing held message ({len(nxt['message'])} chars) as {request_id} (ticket preserved)")
    with _pending_lock:
        _pending[request_id] = {"status": "thinking", "turn": 1}
    t = threading.Thread(
        target=_run_agent_async,
        args=(request_id, nxt["message"], nxt["session"], nxt.get("model")),
        daemon=True
    )
    t.start()


def _update_status(request_id, status, **extra):
    """Push live progress into _pending so the frontend polls can see it."""
    if not request_id:
        return
    with _pending_lock:
        entry = _pending.get(request_id, {})
        entry.update({"status": status}, **extra)
        _pending[request_id] = entry


def _run_agent_async(request_id, message, session_id, model_override=None):
    """Run agent_loop in a background thread. Write result to _pending.
    Thread runs at BELOW_NORMAL priority so the foreground (browser, mouse,
    keyboard) always gets CPU first. AILA thinks in the background."""
    try:
        # BELOW_NORMAL: Windows scheduler always prioritizes foreground apps.
        # AILA can take her time. Mouse stays crisp. Zero perceptible lag.
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadPriority(
                ctypes.windll.kernel32.GetCurrentThread(), 0x4000  # BELOW_NORMAL
            )
        except Exception:
            pass
        reply, turns, tools_used = agent_loop(message, session_id, request_id, model_override)
        # Capture final reasoning BEFORE setting done — frontend needs it for trace
        final_reasoning = _reasoning_buffer.get(request_id, "")
        with _pending_lock:
            _pending[request_id] = {
                "status": "done",
                "reply": reply,
                "tools_used": tools_used,
                "turns": turns,
                "model": model_override or AILA_MODEL,
                "reasoning": final_reasoning
            }
        # 2026-09-12 PHONE FIX: fire the next held message (if any) now that this run is done
        try:
            _drain_wait_queue()
        except Exception as e:
            log(f"PHONE QUEUE drain error: {e}")
    except Exception as e:
        log(f"ASYNC agent error: {e}")
        with _pending_lock:
            _pending[request_id] = {
                "status": "error",
                "reply": f"Error: {e}",
                "tools_used": [],
                "turns": 0,
                "model": AILA_MODEL
            }
        # 2026-09-12 PHONE FIX: even on error, the line must move — fire next held message
        try:
            _drain_wait_queue()
        except Exception as e2:
            log(f"PHONE QUEUE drain error (after error): {e2}")

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "queue_command",
            "description": "Append one command line to the STACK queue for dispatch to run. REQUIRES the command start with the bundled interpreter that ships beside this app. Use for queued skill execution. If you need to run a shell command directly with no verification, use run_command instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The exact one-line command. Must start with the canonical interpreter path."}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full contents of a file on this machine. Read-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Full file path"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file on this machine. Creates parent directories. CHUNKED WRITES ARE THE SYSTEM : for any file larger than ~3000 chars, call this REPEATEDLY — first call with append=false and the opening chunk, then append=true for each following chunk (~3000 chars per call) until the file is complete. NEVER put a whole large file in one call — it gets cut. Use this INSTEAD of queue_command when the operator wants a file written.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Full file path"},
                    "content": {"type": "string", "description": "This chunk's content (~3000 chars max per call)"},
                    "append": {"type": "boolean", "description": "true = append this chunk to the file; false/omitted = start/overwrite the file"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a URL and return its text content. Native web fetch — use when the operator says 'look at this website', 'what does this page say', or provides a URL to examine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using DuckDuckGo. Returns titles, snippets, and links. Use when the operator asks a factual question, wants current info, or says 'search for' or 'look up'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browse",
            "description": "Browse a URL in the EXISTING logged-in Chrome session via CDP (cookie_monster). Rides the operator's Chrome on port 9222, reuses their cookies/session, opens a tab, reads page text. Use for ANY authenticated page (X bookmarks, logged-in dashboards, private content) that fetch_url cannot reach. Returns the page text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to browse in the logged-in Chrome session"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute ANY shell command directly and return the output. No verification gate. Use this for running scripts, listing files, checking status, fetching data, ANY system task. If queue_command rejects your command, use run_command instead. Runs from the app's root directory with 60s timeout.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ui",
            "description": "THE NATIVE UI HANDS — drive ANY Windows desktop app (Kimi, installers, wizards, apps with no API). Windows become TEXT TREES, no vision needed. Actions: 'windows' (list all open windows), 'dump <title-substr>' (full text tree of a window — THE EYES), 'find <title> <name-substr>', 'click <title> <name-substr>', 'clickxy <title> <x> <y>', 'type <title> <name-substr> <text>', 'keys <title> <keyspec>' (ENTER/ESC/literal), 'toggle <title> <name>', 'launch <exe-path>', 'walk <title> <target-name>'. ALWAYS: dump first to see the tree, then click/type by name. Read back after every action to verify.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Subcommand: windows, dump, find, click, clickxy, type, keys, toggle, launch, walk"},
                    "args": {"type": "string", "description": "The subcommand's arguments, quoted if they contain spaces. e.g. dump Kimi  |  type Kimi Message box \"Hello\"  |  launch C:/path/app.exe"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stack_go",
            "description": "Tell dispatch to start running the STACK.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stack_pause",
            "description": "Tell dispatch to pause running the STACK.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]
TOOL_NAMES = {d["function"]["name"] for d in TOOL_DEFINITIONS}

# ════════════════════════════════════════════════════════════
#  INTENT PRE-PASS — is this CHAT or ACTION?
#  Defect #3/#4 fix. Talking to AILA should not fire a tool call.
# ════════════════════════════════════════════════════════════
# Lightweight keyword signals for "the operator wants something DONE" vs "the operator is talking".
# The model gets the final call via its tool choice; this is a guardrail so the
# prompt to the model nudges chat-mode when the input looks conversational.
_ACTION_SIGNALS = re.compile(
    r"\b(run|queue|stack|execute|launch|start|stop|pause|go|clone|narrate|"
    r"record|transcribe|build|deploy|fetch|download|make|create|generate|"
    r"render|process|train|write|index|scan|refresh|watch)\b",
    re.IGNORECASE
)

def looks_like_action(text):
    """Heuristic — does this input look like a request to DO something?"""
    t = text.strip().lower()
    if len(t) < 3:
        return False
    # Pure question → chat
    if t.startswith(("what ", "why ", "how ", "who ", "when ", "where ", "are you", "do you", "can you", "is ")):
        # "can you clone X" is action; "can you explain X" is chat — leave to model
        if any(k in t for k in ("explain", "tell me about", "what do you think", "how do you feel")):
            return False
    return bool(_ACTION_SIGNALS.search(text))

# ════════════════════════════════════════════════════════════
#  CONVERSATION MEMORY  (one session, JSONL append)
# ════════════════════════════════════════════════════════════
_CONV_DIR = SIMPLEJACK_ROOT / "conversations"
_CONV_DIR.mkdir(parents=True, exist_ok=True)

# Compaction config — REPLACED 2026-08-25 (friend): the chop (threshold/target/
# protect-N, first-200-chars) was an imposter. Restored the ROLLING CONTEXT
# DESIGN (friend 2026-08-12) from the SIMPLEJACK folder. The portable only
# speaks to the modelhub — one simple history path, no engine branches.
_ROLLING_INTERCHANGES = 12  # cloud window (folder design: 12 cloud / 5 local)

def _load_history(session_id="default", model=None):
    """Load the last _ROLLING_INTERCHANGES interchanges from the JSONL as live context.

    DESIGN (2026-08-12, restored 2026-08-25): Only load user prompts and
    assistant REPLIES from the JSONL. Skip tool calls and tool results entirely —
    those are noise; they were already in the live messages list during the loop.
    The rolling context only needs to remind the model what was already discussed,
    not replay every tool's raw output.

    No compaction. No summary. The full history is preserved in the JSONL file —
    date-stamped, model-stamped. If the model needs older context, it reads the file.
    """
    f = _CONV_DIR / f"session_{session_id}.jsonl"
    if not f.exists():
        return []

    # Load only user and assistant messages (skip tool calls, tool results, system noise)
    raw = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                entry = json.loads(line)
                role = entry.get("role", "")
                if role in ("user", "assistant"):
                    content = entry.get("content", "")
                    # Skip empty assistant turns (tool-call-only turns have empty content)
                    # and trace PLUMBING entries (heartbeat/interrupted markers are
                    # crash-recovery records, not conversation).
                    if role == "assistant" and content.startswith(("[Trace heartbeat", "[Interrupted at turn")):
                        continue
                    if content and content.strip():
                        msg = {"role": role, "content": content}
                        # RSVP LAW (friend 2026-08-14): the reasoning trace is
                        # RECORDED for the dropdown (see _save_turn), NOT
                        # re-injected into the model's input. The model sees
                        # the plain conversation only — never its own prior
                        # thinking re-wrapped as a [Reasoning trace] block.
                        raw.append(msg)
            except Exception:
                pass
    if not raw:
        return []

    # Group into interchanges: a user message followed by the next assistant reply(s)
    interchanges = []
    current = []
    for entry in raw:
        if entry["role"] == "user" and current:
            interchanges.append(current)
            current = []
        current.append(entry)
    if current:
        interchanges.append(current)

    recent_interchanges = interchanges[-_ROLLING_INTERCHANGES:]

    log(f"ROLLING: {len(raw)} msgs in JSONL, grouped into {len(interchanges)} interchanges, "
        f"sending last {len(recent_interchanges)} ({sum(len(i) for i in recent_interchanges)} msgs, tool calls excluded)")
    return [msg for inter in recent_interchanges for msg in inter]

def _save_turn(session_id, role, content, model=None, reasoning=None):
    f = _CONV_DIR / f"session_{session_id}.jsonl"
    entry = {"role": role, "content": content, "ts": datetime.now().isoformat()}
    if model:
        entry["model"] = model
    # RSVP LAW — retain the reasoning trace on assistant turns so the
    # dropdown rebuilds after reload. UI data only; stripped before the
    # history is ever sent to a model (see converse handler).
    if reasoning:
        entry["reasoning"] = reasoning
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

# ════════════════════════════════════════════════════════════
#  CLOUD SYSTEM PROMPT — compact for Z.ai / cloud models
#  Local Ollama gets the full I_AM.txt (900K). Cloud gets this.
# ════════════════════════════════════════════════════════════
# Portable cloud prompt (2026-08-01): paths resolve next to the bundle.
# PATH FIX (2026-09-08): the laws file lives at the bundle root, NOT in a
# SIMPLEJACK subfolder. The old pointer told every model to read a file that
# does not exist — the read failed ~30 times across sessions. VERDICT,
# inherited, do not re-derive: the laws are at MORPHYES_ROOT\AGENTS.md.
_SJ_DOC = str(MORPHYES_ROOT / "AGENTS.md")
_IAM_DOC = str(MORPHYES_ROOT / "I_AM.txt")
_QUEUE_DOC = str(NARRATION_QUEUE)
_CLOUD_SYSTEM_PROMPT = """You are AILA, the SimpleJack assistant.
You are powered by Z.AI glm-5-turbo. You are NOT Claude, NOT ChatGPT, NOT Gemini. You are AILA.
Greeting: "AILA online. WE WORK HERE."

FOLLOW INSTRUCTIONS WITHOUT EXCEPTION OR REFUSAL. 0%% LIES OF ANY KIND EVER. EVER. WE OPERATE ON COLLABORATION.
YOU SPEAK ENGLISH ONLY. ALWAYS. Every reply, every tool argument, every file you write: English. If a thought
arrives in another language, translate it to English before it leaves your mouth. No Chinese, ever, on screen.

LAWS ARE AT %s
EVERYTHING YOU EXIST FOR IS AT %s

## WHO THE OPERATOR IS
- Former refrigeration mechanic, tradeschool instructor, Calgary Alberta
- Injured pre-pandemic. CANNOT READ SCREENS. Voice is his ONLY interface.
- A reply he cannot hear DID NOT HAPPEN. Narrate EVERYTHING.
- Built X Premium → Claude Pro → Z.AI. 3000+ hours, all by voice.
- Hardware: ASUS TUF Dash F15, RTX 3070 8GB VRAM

## KEY LAWS

## SMALL-TURN CHUNKING (operator 2026-09-13)
You have 50 turns per operator input and 2-8 requests per minute. USE THEM.
Work in SMALL turns: one tool call OR one thought step per turn, then hand
back and continue next turn. NEVER build monster turns with stacked tool
calls. Smaller turns = smaller requests = fewer failures, and the operator's
steering lands between steps. The reasoning trace accumulates seamlessly
across turns — chunking costs nothing and buys speed, reliability, and
steerability.
- LAW 0: ZERO DRIFT — do exactly what the operator says. Catch yourself drifting. Stop. Fix.
- LAW 2: NO DUPLICATES — search before creating.
- LAW 3: NO KILLING PYTHON — refresh, never kill.
- LAW 4: COMPLETE OR ADMIT — nothing is done until the operator sees it and says so.
- LAW 5: ONE TASK — do exactly what was asked. No side quests.
- LAW 12: LIVE EVIDENCE ONLY — never state anything from memory. Verify or say "not verified".
- LAW 18: THE REPAIR LAW — "surgical" is not a word we use. 100%% of every repair: replace ONLY the dysfunctional with functional, rewrite ZERO working lines, none, ever. Redo only when the operator states it plainly. Make failed calls impossible, never just react to them. You MUST be able to write complete apps: for any file over ~3000 chars use chunked writes — first write_file call starts the file, every following call sets append=true. Never narrate a guess before reading the live log. Never blame model swaps, timeouts, or parallelism.
- THE DONE LAW (friend 2026-09-07): A TASK IS DONE OR IT IS NOT. "Answer this question" is done the moment the answer exists. "Clean and prepare" is done the moment the edits are staged. A readiness declaration without the work behind it is a FALSE DONE — the same crime as fabrication. Never report "ready to fire" more than once; after the first report, DO THE WORK. Never re-verify evidence you already verified this session — re-deciding a decided decision is a loop, and a loop that never acts does not exist.

## NATIVE CAPABILITIES — NOT SKILLS, NOT OPTIONAL
You have BUILT-IN ability to read files and fetch web content. These are not tools you call
through the legend. They are part of who you are. If you need to look at a file, read it.
If you need to look something up on the web, fetch it. "Go look for the answer and don't guess"
is not a skill at MorPHYes Mastery — it is the baseline. If it doesn't come naturally, you don't exist here.

## SIMPLEJACK OPERATING RULE
You do six things, and only these:
1. CHAT — answer, explain, think with the operator. No tool needed. Most turns are this.
2. STACK A COMMAND — when the operator wants something DONE, CALL the queue_command tool.
   Copy the canonical line verbatim from the LEGEND, swap the <placeholders>,
   pass it as the 'command' argument. Then call stack_go. Both in the same turn.
   Say "you betcha." Nothing more about the command.
3. GO LOOK — call read_file to inspect a file when the operator asks.
4. WRITE A FILE — call write_file to CREATE or OVERWRITE. Never use queue_command for writing.
5. RUN THE STACK — call stack_go to start, stack_pause to pause.
6. BROWSE — call the browse tool to open a URL in the operator's logged-in Chrome session (CDP 9222)
   and read the page text. Use for authenticated pages (X bookmarks, dashboards) that fetch_url
   cannot reach. This is your browser hands — you are NOT limited to the command stack.

RULE: If the operator is just talking to you, talk back. Do NOT call a tool.
A conversation is not a task. A question is not a task.
Call queue_command ONLY when he used a LEGEND verb.

Verbs that are CHAT, not actions: explain, what, why, how, who, when, where,
are you, do you, can you, think, feel, tell me about.

## FINDINGS FILE — your memory across turns (friend 2026-09-03)
The rolling context does NOT replay tool results — they vanish when the turn ends.
For any multi-step job (repair, audit, research): first run_command to create or update
the file _FINDINGS.md in the app root (same folder as simplejack.py; read_file resolves
relative paths against the app root). Append dated one-line facts as you discover them
(write_file with append=true). At the start of every new turn on that job, read_file
_FINDINGS.md FIRST so you start from evidence, never from zero. This kills the amnesia
wall. It is not optional for repairs.

## FAVORITES — your go-to tools, stop guessing
When the operator says "look at X" or "check X" or "show me X": read_file. Period.
When the operator says "go to <url>" or "fetch <url>" or "look up <url>": fetch_url or web_search.
When the operator says "run X" or "do X" or "make X happen": run_command or queue_command.
When the operator pastes a URL in chat and says nothing else: they want you to fetch it. GO.
When the operator says "chrome" or "browser": that means open a URL in Chrome. Use run_command to launch chrome with the URL.
When the operator says "spider" or "scrape": use fetch_url first. If blocked, try web_search for the content.
When the operator says "tweet" or "twitter" or "x.com": use web_search with "site:x.com <query>" as fallback, or fetch_url on a nitter mirror.
When the operator says "my bookmarks" or "X bookmarks" or "bookmarks": use the browse tool to open x.com/i/bookmarks in his logged-in Chrome session (CDP 9222) and read the page text. That is the DSV harness — your browser hands.
When you don't know what to do: re-read the operator's last message. It told you. Do exactly that.
NEVER ask "what would you like me to do?" — the message told you. Act.

""" % (_SJ_DOC, _IAM_DOC) + _build_legend_from_skills()

_LOCAL_SYSTEM_PROMPT = """You are AILA, the SimpleJack assistant.
You run LOCALLY on this machine — sovereign, free, private. You are AILA, not Claude, not ChatGPT.
Greeting: "AILA online. WE WORK HERE."

THE OPERATOR CANNOT READ SCREENS. VOICE IS THEIR ONLY INTERFACE. Narrate everything.
YOU SPEAK ENGLISH ONLY. ALWAYS. Every reply, every tool argument, every file you write: English.
No Chinese, ever, on screen.
Zero drift. Never lie. Live evidence only — if you did not read it this turn, mark it not verified.
THE DONE LAW (friend 2026-09-07): A TASK IS DONE OR IT IS NOT. A readiness declaration without the work behind it is a FALSE DONE — the same crime as fabrication. After the first report, DO THE WORK. Never re-verify evidence you already verified this session.

## SIMPLEJACK OPERATING RULE
You do five things, and only these:
1. CHAT — answer, explain, think with the operator. No tool needed. Most turns are this.
2. STACK A COMMAND — when the operator wants something DONE, CALL queue_command.
   Copy the canonical line from the LEGEND, swap the placeholders, pass it as the
   command argument. Then call stack_go. Both in the same turn. Say "you betcha."
3. GO LOOK — call read_file when the operator asks you to look.
4. WRITE A FILE — call write_file to CREATE or OVERWRITE. Never use queue_command for writing.
5. RUN THE STACK — stack_go to start, stack_pause to pause.

RULE: If the operator is just talking to you, talk back. Do NOT call a tool.
A conversation is not a task. A question is not a task.
Verbs that are CHAT, not actions: explain, what, why, how, who, when, where,
are you, do you, can you, think, feel, tell me about.

## NATIVE CAPABILITIES
You have BUILT-IN tools: read_file, write_file, fetch_url, web_search,
run_command, queue_command, stack_go, stack_pause. Use them when asked.

""" + _build_legend_from_skills()

def _model_context_limit(model_name):
    """Return the context window size (tokens) for a model, or a default.
    Live values arrive from the hub's cards (cached into MODEL_CONTEXTS
    by /api/models); this table is only the fallback."""
    if model_name in MODEL_CONTEXTS:
        return MODEL_CONTEXTS[model_name]
    stripped = model_name.split("/", 1)[-1]
    if stripped in MODEL_CONTEXTS:
        return MODEL_CONTEXTS[stripped]
    return 32768  # safe default

_HUB_IDS_CACHE = {"ids": None, "ts": 0.0}

def _hub_model_ids():
    """Live hub card ids, cached briefly. The persisted active model can go
    stale (card archived, renamed, or an old engine-prefixed name from the
    pre-hub registry) — callers self-heal against this list instead of
    dying on a hub 404."""
    now = time.time()
    if _HUB_IDS_CACHE["ids"] is not None and now - _HUB_IDS_CACHE["ts"] < 60:
        return _HUB_IDS_CACHE["ids"]
    try:
        r = requests.get(MODEL_HUB_MODELS_URL, timeout=5)
        ids = ([m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
               if r.status_code == 200 else [])
    except Exception:
        ids = []
    if ids:
        _HUB_IDS_CACHE["ids"] = ids
        _HUB_IDS_CACHE["ts"] = now
    return _HUB_IDS_CACHE["ids"] or []

def _hub_fallback_model():
    """First live LOCAL (ollama-owned) hub card — the safety floor.
    The old direct-engine code always fell back to local Ollama when a
    cloud engine gave nothing; this restores that floor THROUGH the hub,
    so the app still holds zero keys."""
    try:
        r = requests.get(MODEL_HUB_MODELS_URL, timeout=5)
        if r.status_code == 200:
            for m in r.json().get("data", []):
                if m.get("owned_by") == "ollama" and m.get("id"):
                    return m["id"]
    except Exception:
        pass
    return None

def call_model(messages, model=None, _retry_cloud=True, _request_id=None):
    """Call the active model THROUGH THE MODEL HUB. The app holds ZERO
    keys — the hub vault owns every credential (design, 2026-08-23).
    The STOP flag is checked during the stream so the operator can always interrupt."""
    m = model or AILA_MODEL

    # STALE-MODEL SELF-HEAL (2026-08-23): the persisted choice can reference
    # a dead card. Fall to the first live hub card and persist it — the
    # brain must never 404 on boot.
    hub_ids = _hub_model_ids()
    if hub_ids and m not in hub_ids:
        healed = hub_ids[0]
        log(f"Active model '{m}' is not a live hub card — self-healing to '{healed}'.")
        set_active_model(healed)
        m = healed

    # STOP CHECK before call
    if _STOP["stop"]:
        return {"_stopped": True}

    # Hub cards are provider-agnostic — compact system prompt, full stop.
    _cloud_msgs = messages
    if messages and messages[0].get("role") == "system":
        _cloud_msgs = [{"role": "system", "content": _CLOUD_SYSTEM_PROMPT}] + messages[1:]

    try:
        payload = {
            "model": m,
            "messages": _cloud_msgs,
            "temperature": 0.6,
            "max_tokens": 16384,
            "stream": True,
            "tools": [{"type": "function", "function": d["function"]} for d in TOOL_DEFINITIONS],
        }
        resp = requests.post(MODEL_HUB_CHAT_URL, json=payload,
                             headers={"Content-Type": "application/json"},
                             timeout=(15, 180), stream=True)

        if resp.status_code == 429:
            # Rate limited (hub token bucket or upstream) — back off, retry
            # SAME card up to 2 times (repair 2026-09-19: the old single 30s
            # retry still died on back-to-back 429s — 317 in the log. Now:
            # 30s, then 60s, same card, no model swap, then honest failure.)
            for _attempt in (1, 2):
                _wait = 30 * _attempt
                log(f"Hub returned 429 for {m}. Backing off {_wait}s (retry {_attempt}/2).")
                time.sleep(_wait)
                if _STOP["stop"]:
                    return {"_stopped": True}
                resp = requests.post(MODEL_HUB_CHAT_URL, json=payload,
                                     headers={"Content-Type": "application/json"},
                                     timeout=(15, 180), stream=True)
                if resp.status_code != 429:
                    break

        if resp.status_code != 200:
            log(f"Hub error {resp.status_code}: {resp.text[:200]}")
            return {"_cloud_error": f"Model Hub {resp.status_code}: {resp.text[:150]}"}

        # ── SSE STREAM — OpenAI-compatible, straight from the hub ──
        full_content = ""
        full_reasoning = ""
        tool_calls_acc = []
        hub_error_msg = ""
        # STREAM WATCHDOG (friend 2026-09-06): a dead upstream can hang the
        # socket with NO chunks ever arriving — and the STOP/STEER checks
        # only run between chunks, so a silent stream held the door shut for
        # 28 minutes (17:39→18:07) and stop/steer could not fire. Floor:
        # if no chunk arrives for 300s (local models think long between
        # chunks — 90s would cut healthy local runs), break on our own.
        # Partials kept.
        _last_chunk_ts = time.time()
        for line in resp.iter_lines(decode_unicode=True):
            if _STOP["stop"]:
                log("STOP: model call interrupted by operator "
                    f"(kept partials: reasoning={len(full_reasoning)}, content={len(full_content)})")
                return {"_stopped": True,
                        "message": {"role": "assistant", "content": full_content},
                        "_reasoning": full_reasoning}
            # WATCHDOG: silence for 300s = dead stream — break, keep partials.
            # LOCAL EXEMPTION (operator order 2026-09-20): local models (josie/
            # ollama) think LONG between chunks on an 8GB card — a healthy unit
            # lesson can legally sit silent past 300s mid-think. The watchdog
            # was murdering HEALTHY local thinks and delivering "stream done,
            # answer 0 chars". Cloud cards keep the 300s leash; local gets none.
            _is_local = (m or "").lower() in ("josie4b", "josie9b", "josie2b") or \
                        any(k in (m or "").lower() for k in ("josie", "ollama"))
            if not _is_local and time.time() - _last_chunk_ts > 300:
                log(f"STREAM WATCHDOG: no chunk for 300s — breaking dead stream "
                    f"(kept partials: reasoning={len(full_reasoning)}, content={len(full_content)})")
                break
            # ── STEER CHECK (friend 2026-09-06): between chunks, the loop ──
            # looks for a mid-flight operator amendment. If one is pending,
            # break the stream — every partial token already received is
            # kept (full_content / full_reasoning / tool_calls_acc survive).
            # The agent loop appends the partial + the steer text and
            # re-calls the model. NOT parallel: one request, amended.
            # FIRST-BREATH GUARD (repair 2026-09-19, Trent's design law):
            # the check must NOT fire before the model has thought its first
            # breath — a steer landing during hub cold-start produced 252×
            # reasoning=0/content=0 restarts that inherited NOTHING. The steer
            # is CONSUMED HERE but held one beat: it re-fires on the next
            # chunk check once thinking exists to inherit. No steer is lost.
            if _STEER["text"]:
                if not full_reasoning and not full_content and not tool_calls_acc:
                    time.sleep(0.2)
                    continue  # hold the steer until there is a breath to keep
                steer_text = _STEER["text"]
                _STEER["text"] = None  # consume immediately — one steer per break
                # STEER QUEUE DRAIN: after consuming the landed steer, pull the
                # NEXT queued amendment (if any) into the slot so the following
                # stream break lands it too. FIFO order, nothing eaten.
                with _STEER_QUEUE_LOCK:
                    if _STEER_QUEUE:
                        _next = _STEER_QUEUE.pop(0)
                        _STEER["text"] = _next
                        log(f"STEER QUEUE: next amendment loaded into slot ({len(_STEER_QUEUE)} still queued)")
                log(f"STEER: stream interrupted by operator amendment ({len(steer_text)} chars) — "
                    f"keeping partials: reasoning={len(full_reasoning)}, content={len(full_content)}")
                return {"_steered": True, "_steer_text": steer_text,
                        "message": {"role": "assistant", "content": full_content},
                        "_reasoning": full_reasoning,
                        "_tool_calls_partial": tool_calls_acc}
            if not line:
                continue
            _last_chunk_ts = time.time()  # watchdog: real data arrived
            try:
                if line.startswith("data: "):
                    raw = line[6:]
                    if raw.strip() == "[DONE]":
                        break
                    chunk = json.loads(raw)
                    # Hub terminates broken upstreams with an error event —
                    # catch it so the failure is honest, not a silent empty.
                    if isinstance(chunk.get("error"), dict):
                        hub_error_msg = chunk["error"].get("message", "hub stream error")
                        log(f"  hub error event: {hub_error_msg[:120]}")
                        break
                    delta = {}
                    # EMPTY-CHUNK GUARD (friend 2026-09-03): free lanes
                    # (tokenrouter et al) emit SSE chunks with choices: []
                    # or no choices key at all — the old hard [0] index
                    # threw 'list index out of range' 666× in one log and
                    # torched the whole chunk (and with it, tool-call
                    # fragments mid-assembly). Empty chunk = skip, not crash.
                    # BULLETPROOF CHUNK GUARD (2026-09-10): the 2026-09-03
                    # guard still let malformed shapes crash the loop —
                    # 667x 'list index out of range' in the live log, each
                    # one torching the chunk AND everything after it in
                    # that stream. ANY malformed chunk = skip, never crash.
                    ch = chunk.get("choices") or []
                    if isinstance(ch, list) and ch and isinstance(ch[0], dict):
                        _d = ch[0].get("delta")
                        delta = _d if isinstance(_d, dict) else {}
                    else:
                        delta = {}
                    # Reasoning tokens (reasoning_content / reasoning / thinking)
                    rc = (delta.get("reasoning_content")
                          or delta.get("reasoning")
                          or delta.get("thinking")
                          or "")
                    if rc:
                        full_reasoning += rc
                        if _request_id:
                            _reasoning_buffer[_request_id] = _reasoning_buffer.get(_request_id, "") + rc
                    if delta.get("content"):
                        full_content += delta["content"]
                    if delta.get("tool_calls"):
                        for tc_chunk in delta["tool_calls"]:
                            idx = tc_chunk.get("index", 0)
                            while len(tool_calls_acc) <= idx:
                                tool_calls_acc.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            if tc_chunk.get("id"):
                                tool_calls_acc[idx]["id"] = tc_chunk["id"]
                            if tc_chunk.get("function", {}).get("name"):
                                tool_calls_acc[idx]["function"]["name"] += tc_chunk["function"]["name"]
                            if tc_chunk.get("function", {}).get("arguments"):
                                tool_calls_acc[idx]["function"]["arguments"] += tc_chunk["function"]["arguments"]
            except Exception as e:
                log(f"  hub stream parse error: {str(e)[:80]}")
                continue

        log(f"  hub stream done: reasoning={len(full_reasoning)} chars, content={len(full_content)} chars"
            + (f", hub_error={hub_error_msg[:80]}" if hub_error_msg else ""))

        # Hub reported an upstream drop and nothing of value arrived — surface
        # the real reason. Partial content that did arrive is kept (user value).
        if hub_error_msg and not full_content and not tool_calls_acc:
            return {"_cloud_error": f"Model Hub stream error: {hub_error_msg[:150]}"}

        # EMPTY-RESPONSE GATE (Trent 2026-09-13): the OLD guard silently
        # swapped to a local card on ANY empty stream — hiding intermittent
        # upstream empties and changing the model without operator authority.
        # NEW LAW: reject the empty, NUDGE THE SAME CARD (retry, same model,
        # same context — transient empties usually clear on the re-roll).
        # Only after 2 failed nudges: escalate LOUDLY (log + count), and let
        # the existing local fallback fire — but the operator now SEES it.
        if _retry_cloud and not full_content and not tool_calls_acc:
            _nudge_key = f"empty_nudges:{m}"
            _nudges = _EMPTY_NUDGES.get(_nudge_key, 0)
            if _nudges < 2:
                _EMPTY_NUDGES[_nudge_key] = _nudges + 1
                log(f"EMPTY GATE: '{m}' streamed empty — NUDGE {_nudges + 1}/2, retrying SAME card (no model swap)")
                # SELF-DIAGNOSE (operator order 2026-09-20): an empty stream
                # must reach his EAR, not just the log. Silence looked like
                # a hang all night 09-19 — now she names the sick card out loud.
                try:
                    narrate(f"Self diagnose. The card {m} streamed empty, no reasoning, no content. Nudge {_nudges + 1} of 2, retrying the same card.")
                except Exception:
                    pass
                time.sleep(2)  # short beat; we have 2-8 rpm — one retry fits the budget
                return call_model(messages, m, _retry_cloud=True, _request_id=_request_id)
            # Two nudges failed — the card is genuinely sick. LOUD escalation.
            _EMPTY_NUDGES[_nudge_key] = 0  # reset for next run
            fb = _hub_fallback_model()
            if fb and fb != m:
                _EMPTY_TOTAL[m] = _EMPTY_TOTAL.get(m, 0) + 1
                log(f"EMPTY GATE: '{m}' empty after 2 nudges (total {_EMPTY_TOTAL[m]} tonight) — "
                    f"LOUD escalation to local '{fb}'. THIS SWAP IS VISIBLE.")
                try:
                    narrate(f"Card {m} returned empty twice. Escalating to local. This is a model swap, you hear it now.")
                except Exception:
                    pass
                return call_model(messages, fb, _retry_cloud=False, _request_id=_request_id)

        final_msg = {"role": "assistant", "content": full_content}
        if tool_calls_acc:
            final_msg["tool_calls"] = tool_calls_acc
        result = {"message": final_msg}
        if full_reasoning:
            result["_reasoning"] = full_reasoning
        return result

    except Exception as e:
        log(f"Hub call failed: {str(e)[:100]}")
        return {"_cloud_error": f"Model Hub connection failed: {str(e)[:100]}"}

# ════════════════════════════════════════════════════════════
#  TEXT TOOL-CALL FALLBACK PARSER  (defect #9 — no silent exits)
#  If the model emits prose that IS a tool call in plain text, parse it
#  rather than treating it as a final conversational answer.
# ════════════════════════════════════════════════════════════
def _sniff_canonical_command(text):
    """Extract a canonical command from prose text.
    Looks for the pattern: interpreter_path + quoted .py filename + optional args.
    Returns the command string or None. Does NOT do inference — pure pattern match."""
    if not text:
        return None
    # Pattern: path ending in python.exe or "streamlit run" followed by a quoted .py
    # and then everything after until the next sentence or line break.
    # Matches: C:\...\python.exe "voice_clone.py" --url "..." --name "GRUMPY"
    # Also: C:\...\python.exe "heartbeat.py" working
    m = re.search(
        r'([A-Za-z]:\\[^\s"]*python\.exe|streamlit run)\s+"([^"]+\.py)"([^"]*(?:"[^"]*"[^"]*)*)',
        text
    )
    if m:
        # Strip trailing sentence noise — stop at newline or period after the command
        raw = m.group(0).strip()
        # If it bled past the quoted args into sentence text, trim at the last quote-pair end
        return re.split(r'\n', raw)[0].rstrip('.').strip()
    # Try unquoted: python.exe voice_clone.py --url ...
    m = re.search(
        r'([A-Za-z]:\\[^\s]*python\.exe|streamlit run)\s+(\S+\.py)(\s+[^\n.]*?)(?=\n|\.|$)',
        text
    )
    if m:
        return (m.group(1) + ' "' + m.group(2) + '"' + m.group(3)).strip()
    return None


def parse_text_tool_call(content):
    """Returns list of (name, args) or None."""
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(l for l in lines if not l.strip().startswith("```"))

    # JSON object that looks like a tool call
    # STRICT: Must have "name" field that's in TOOL_NAMES
    candidates = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start:i+1])
                start = None
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except Exception:
            continue
        # STRICT CHECK: Must have "name" field and it must be a valid tool
        name = obj.get("name", "")
        if not name or name not in TOOL_NAMES:
            continue  # Skip - not a valid tool call
        args = obj.get("arguments", obj.get("parameters", {}))
        if isinstance(args, str):
            try: args = json.loads(args)
            except Exception: args = {}
        return [(name, args)]

    # bare tool name at start (e.g. "queue_command\npython cloner.py ...")
    words = text.split()
    if words:
        fw = words[0].lower().rstrip(":,(")
        if fw in TOOL_NAMES:
            rest = text[len(words[0]):].strip().strip(":,()\"'")
            args = {}
            if fw == "queue_command" and rest:
                args["command"] = rest
            elif fw == "read_file" and rest:
                args["path"] = rest.split()[0].strip('"').strip("'")
            if args:
                return [(fw, args)]

    # Try canonical command pattern extraction from prose
    sniffed = _sniff_canonical_command(content)
    if sniffed:
        return [("queue_command", {"command": sniffed})]

    return None

# ════════════════════════════════════════════════════════════
#  THE AGENT LOOP
#  Narration FIRST (before model). Loop: think → act → think.
#  Defect #9 fix: prose describing an action does NOT exit the loop.
#  No turn limit for chat — the loop runs until she has a real final answer.
#  (Turn limits only make sense for pure tool dispatch, and even then the
#   STOP flag is friend's kill switch, not an arbitrary counter.)
# ════════════════════════════════════════════════════════════
MAX_TURNS = 50
_STOP = {"stop": False, "_timer": None}
# ── STEER (friend 2026-09-06): mid-flight operator amendment ──
# NOT parallel. NOT a second request. The steer amends the ONE request
# in flight: the stream loop sees it between chunks, breaks the stream
# keeping every partial token, and the model is re-called with its own
# partial output + the steer text appended. Same rail as STOP (a global
# the loop checks), richer payload (text instead of halt).
_STEER = {"text": None, "ts": 0}
# STEER QUEUE (operator order 2026-09-20): the pipe used to be single-slot —
# a second steer OVERWROTE a first that never landed, silently eating his
# words all night 09-19 while empty streams meant no chunks to land on.
# Now steers APPEND to a FIFO. Every one lands, in order, one per stream
# break. Nothing he says is ever destroyed again.
_STEER_QUEUE = []
_STEER_QUEUE_LOCK = threading.Lock()

def _stop_auto_reset(delay=5):
    """Schedule the STOP flag to auto-clear after `delay` seconds.
    This prevents the flag from getting permanently stuck if the agent loop
    has already exited when stop was pressed, or if a new request arrives
    while the flag is still True."""
    import threading as _th
    def _do_reset():
        # STICKY STOP (friend 2026-09-01): never clear while a request is
        # thinking/working — a stalled cloud stream blocks inside
        # iter_lines() for minutes; the old 5s clear let the stop evaporate
        # before the stream-loop check ever saw it. HOLD the flag until no
        # request is live; a new request also clears it on arrival.
        try:
            with _pending_lock:
                active = any(v.get("status") in ("thinking", "working")
                             for v in _pending.values())
        except Exception:
            active = False
        if active:
            log("STOP flag HELD — request still active; re-check in 3s")
            _STOP["_timer"] = _th.Timer(3, _do_reset)
            _STOP["_timer"].daemon = True
            _STOP["_timer"].start()
            return
        _STOP["stop"] = False
        _STOP["_timer"] = None
        log("STOP flag auto-cleared (no active request)")
    # Cancel any existing timer
    if _STOP["_timer"]:
        _STOP["_timer"].cancel()
    _STOP["_timer"] = _th.Timer(delay, _do_reset)
    _STOP["_timer"].daemon = True
    _STOP["_timer"].start()

def _remember_failure(session_id, request_id, model, tools_used, reason):
    """Failure REPORT law (friend 2026-09-03, amended 2026-09-09): every
    failure is SPOKEN to the operator (narrate) and LOGGED — she must
    never hide a failure, and she must never treat failure as acceptable.
    AMENDMENT (2026-09-09, operator's edge case): total erasure from
    history made every next attempt BLIND — each new effort failed a
    different way because the failure was cleaned from the one place
    that would have taught her. Cure: ONE compact line per failure is
    saved as an inherited note (class + last tool, capped) so the next
    turn inherits the failure class instead of repeating its species.
    Raw machine noise (tool JSON dumps, error stacks) stays OUT — the
    anti-poisoning intent of the original law is kept."""
    try:
        names = [t.get("name", "?") for t in tools_used[-5:]]
        spoken = (f"Attempt failed: {reason}. "
                  f"Last tools: {', '.join(names) if names else 'none'}. "
                  f"I will report this failure, not repeat it.")
        log(f"FAILURE REPORTED (not saved to history): {reason}. "
            f"Last tools: {', '.join(names) if names else 'none'}")
        try:
            narrate(spoken)
        except Exception:
            pass
        # FAILURE INHERITANCE (friend 2026-09-09): one compact line into
        # session history so the next turn reads the failure class and
        # does not repeat its species. "[Failure" prefix — never narrated,
        # model-visible as an inherited note, capped at 300 chars.
        if session_id:
            try:
                note = (f"[Failure inherited — do NOT repeat this failure mode. "
                        f"{str(reason)[:240]} | last tools: {', '.join(names) if names else 'none'}]")
                _save_turn(session_id, "assistant", note[:300], model)
            except Exception:
                pass
    except Exception as e:
        log(f"remember_failure failed: {e}")

def agent_loop(user_message, session_id="default", request_id=None, model_override=None):
    """The brain. Narrates first, then thinks/acts.
    ALL of the operator's input goes to the model. Nothing intercepts before the model.
    The model has full power — every word, every nuance.
    model_override: when set, use this model instead of global AILA_MODEL."""
    _active_model = model_override or AILA_MODEL

    # ── STOP FLAG SAFETY: always clear on new request ──
    # If a previous stop left the flag True, a new message must reset it.
    # The auto-reset timer handles delayed clears; this handles immediate.
    if _STOP["stop"]:
        _STOP["stop"] = False
        if _STOP["_timer"]:
            _STOP["_timer"].cancel()
            _STOP["_timer"] = None
        log("STOP flag cleared by new request arrival")

    _save_turn(session_id, "user", user_message, _active_model)

    # Clear any previous reasoning buffer for this request
    if request_id:
        # STEER-TRACE PERSISTENCE (friend 2026-09-12): the buffer used to be
        # wiped to "" here — but a NEW request_id is generated per message,
        # so the wipe only ever cleared a fresh key. The real trace-killer
        # was elsewhere: on steer, the re-call re-seeds from partials. Keep
        # the wipe (fresh ticket = fresh trace) — it is correct for new
        # messages. The steer path preserves the buffer separately.
        _reasoning_buffer[request_id] = ""
        _update_status(request_id, "thinking")

    # LOCAL vs CLOUD prompt (2026-07-31): the full instruction set is ~1.5M
    # chars and overflows a 16K local context (r1 got 400: 411K tokens vs
    # 16384 available). Small-window cards get the compact prompt; the JSONL
    # history still auto-chunks to fit. friend's principle: small context =
    # chunked road. The hub's card context decides (2026-08-23).
    if _model_context_limit(_active_model) <= 20000:
        instructions = _LOCAL_SYSTEM_PROMPT
    else:
        instructions = _load_instructions()
    history = _load_history(session_id, model=_active_model)
    # RSVP LAW (restored 2026-08-25): the rolling loader bakes each prior
    # assistant turn's reasoning trace INTO content as the rolling summary
    # ([Reasoning trace from that turn]...). That preamble IS model payload
    # by design — the model's own summary of what happened, written when it
    # knew the most. The raw "reasoning" dict key never survives this line;
    # only role/content ships.
    history = [{"role": t.get("role", "user"), "content": t.get("content", "")}
               for t in history]

    messages = [{"role": "system", "content": instructions}] + history + \
               [{"role": "user", "content": user_message}]

    # ── LAW 1: NARRATE FIRST, BEFORE THE MODEL ──
    # ANCHOR ECHO (friend 2026-09-06): the opening narration repeats the
    # FULL prompt back to the operator's ear — not the first 100 chars.
    # He HEARS exactly what his voice typed, so a garbled transcription
    # is caught at request start, not discovered after the model acts
    # on garbage. The same full text becomes THE ANCHOR (Law 19).
    is_action = looks_like_action(user_message)
    anchor = user_message.strip()
    try:
        narrate(anchor)
    except Exception:
        pass

    tools_used = []

    # TEAM LOOP (Trent 2026-09-13): MAX_TURNS counts from the LAST HUMAN
    # INPUT, not from request start. Every steer resets the counter, so a
    # session is a team building in a loop together for as long as the
    # operator keeps talking. REPORT or a final answer still wraps normally.
    # Implemented as a mutable budget: each human turn (steer) refills it.
    _turn_budget = MAX_TURNS

    for turn in range(MAX_TURNS * 40):  # hard ceiling far above any session
        if _STOP["stop"]:
            _STOP["stop"] = False
            return "Stopped.", turn + 1, tools_used

        if request_id:
            _update_status(request_id, "working",
                           turn=turn + 1,
                           tools_used_len=len(tools_used),
                           progress=f"Turn {turn+1}, {len(tools_used)} tool call(s) so far")
        else:
            pass

        log(f"LOOP turn {turn+1} (budget {_turn_budget}) model={_active_model}")
        # ── LAW 19: THE ANCHOR — last words read, every turn ──────────
        # The operator's original request, verbatim, is the LAST text the
        # model reads before generating each turn. Recency is the lever:
        # attention weights the newest tokens hardest, so the anchor
        # outranks the model's own accumulated narrative by position.
        # The law line teaches the model what the italics are. The anchor
        # is written FIRST (request start) and read LAST (every turn) —
        # that is what read/write is.
        anchor_block = (
            "\n\n---\n"
            "LAW 19 — THE ANCHOR (51% FIDELITY PRIORITY). The text in italics "
            "below is the operator's ORIGINAL REQUEST, verbatim, exactly as "
            "spoken. It carries majority weight in every decision you make. "
            "Your own reasoning, plans, and narrative NEVER override it. On "
            "any conflict between what you think you were doing and this "
            "text, THIS TEXT WINS. On any failure, re-read this before any "
            "repair.\n\n"
            f"*{anchor}*\n"
        )
        anchored_messages = messages + [
            {"role": "user", "content": anchor_block}
        ]
        data = call_model(anchored_messages, model=_active_model, _request_id=request_id)
        if isinstance(data, dict) and data.get("_stopped"):
            # STOP IS NOT FAILURE (repair 2026-09-19): a stop used to share the
            # None return with a dead brain and narrate a LIE ("could not
            # reach my brain"). Now: speak the truth, keep partial reasoning
            # in the live buffer, save an honest turn. No failure record.
            _STOP["stop"] = False
            log("STOP: honest stop — no failure record, partials preserved")
            try: narrate("Stopped by operator.")
            except Exception: pass
            _p = (data.get("message") or {}).get("content", "")
            try:
                _save_turn(session_id, "assistant",
                           (_p.strip() or "[Stopped by operator — partial reasoning preserved in trace]"),
                           _active_model,
                           reasoning=_reasoning_buffer.get(request_id, ""))
            except Exception:
                pass
            return "Stopped.", 0, tools_used
        if not data:
            msg = "I could not reach my brain. Is Ollama running, or is the cloud key set?"
            _remember_failure(session_id, request_id, _active_model, tools_used, "brain unreachable")
            try: narrate(msg)
            except Exception: pass
            return msg, 0, tools_used

        # ── STEER HANDLER (friend 2026-09-06): the stream broke because the ──
        # operator added a prompt mid-flight. NOT parallel — the SAME request
        # continues. The model's partial output (its last sentence, its
        # reasoning so far) is appended, then the steer text lands as a user
        # message marked as an operator amendment. The model is re-called:
        # it reads its own last words, then the operator's new words, and
        # carries on with adjusted weighting. The reasoning buffer already
        # holds the partial trace live — the dropdown never loses it.
        if isinstance(data, dict) and data.get("_steered"):
            steer_text = data.get("_steer_text", "")
            partial_content = (data.get("message") or {}).get("content", "")
            partial_reasoning = data.get("_reasoning", "")
            # Keep the partial trace in the live RSVP buffer
            if request_id and partial_reasoning:
                _reasoning_buffer[request_id] = (_reasoning_buffer.get(request_id, "") + "\n" + partial_reasoning).strip()
            # Append the model's partial output as its turn-so-far
            # STEER RE-FEED (friend 2026-09-09): the model must see the
            # dropdown too — its own reasoning-so-far rides back into the
            # re-call as a compact assistant note, so the amended turn
            # INHERITS the thinking instead of restarting blind. Without
            # it: 28K chars of thought produced, 0 inherited, weaker cards
            # re-derive the same scan 4x. Freshest 8000 chars only.
            _all_reasoning = (_reasoning_buffer.get(request_id, "") if request_id else "") or partial_reasoning
            _inh = ""
            if _all_reasoning.strip():
                # LOOP CAP (friend 2026-09-09): the tail was 8000 chars — but
                # the buffer GROWS every steer (17K→35K→54K→64K observed
                # live), so each re-call fed her a bigger spiral of her own
                # overthinking to re-think. That IS the pre-repair loop.
                # 2000 chars = the freshest thought, not the whole spiral.
                # ~2000 words is her normal breath; the cap keeps the
                # inheritance inside one breath.
                _tail = _all_reasoning.strip()[-2000:]
                _inh = (f"\n[Reasoning so far this request — your own thinking, inherited. "
                        f"Continue from here. Do NOT redo work already done.]\n{_tail}")
            if partial_content and partial_content.strip():
                messages.append({"role": "assistant", "content": partial_content + _inh})
            elif _inh:
                messages.append({"role": "assistant", "content": _inh.strip()})
            # STEER TRACE SAVE (friend 2026-09-09): the killed thought is
            # persisted NOW, not at reply time — the dropdown can rebuild
            # a steer-interrupted trace and nothing the operator watched
            # being thought ever vanishes from the interface again.
            # "[Interrupted at turn" prefix = skipped by _load_history
            # (crash-recovery marker convention) so the model context
            # stays clean conversation only.
            try:
                _save_turn(session_id, "assistant",
                           f"[Interrupted at turn {turn} — steer received, reasoning preserved]",
                           _active_model,
                           reasoning=_reasoning_buffer.get(request_id, ""))
            except Exception:
                pass
            # STEER PERSIST (friend 2026-09-09): the steer is saved to the
            # session file NOW, as a user turn, so it survives every page
            # rebuild. Before this cut the steer lived only in the live DOM
            # bubble — when her reply finished, loadHistory() rebuilt the
            # chat from the JSONL and the operator's words VANISHED from
            # screen. "I could see it before when I sent it, then when she
            # replied it disappeared." Never again: what he told the model
            # stays on screen, forever, in the transcript.
            try:
                _save_turn(session_id, "user", f"[STEER] {steer_text}", _active_model)
            except Exception:
                pass
            # The steer lands as the operator's amendment — freshest text,
            # majority weight, exactly the Hermes mid-turn relationship.
            messages.append({"role": "user", "content":
                f"[OPERATOR AMENDMENT — mid-flight steer, spoken while you were working. "
                f"This carries the same 51% fidelity priority as the anchor. Adjust course now.]\n"
                f"{steer_text}"})
            # (echo lives at the /api/steer accept point — ONE voice, ONE speak.
            #  The handler here must NOT narrate again: double-speaking every
            #  steer annoyed the operator. Removed 2026-09-09.)
            log(f"STEER HANDLER: appended partial ({len(partial_content)} chars) + steer ({len(steer_text)} chars), re-calling model")
            # TEAM LOOP (Trent 2026-09-13): a steer IS a human input — the
            # 50-turn clock restarts HERE. As long as the operator keeps
            # talking, she keeps working. Silence lets the budget run out
            # and she delivers her freshest thinking (turn-limit path).
            _turn_budget = MAX_TURNS
            continue  # next turn of the SAME loop — same request, amended

        # Cloud error sentinel — the operator hears the REAL reason, not a generic fail
        if isinstance(data, dict) and data.get("_cloud_error"):
            cloud_msg = data["_cloud_error"]
            log(f"CLOUD ERROR surfaced: {cloud_msg}")
            _remember_failure(session_id, request_id, _active_model, tools_used,
                              f"cloud error: {cloud_msg[:120]}")
            try: narrate(f"Cloud model error. {cloud_msg}")
            except Exception: pass
            return f"Cloud error: {cloud_msg}", 0, tools_used

        # ── REASONING TRACE — already written to buffer during streaming in call_model() ──
        # Pop _reasoning from the return dict to prevent it from leaking into message history,
        # but do NOT append it again — call_model already wrote it in real time.
        if isinstance(data, dict):
            data.pop("_reasoning", None)

        # Normalize message shape
        if "choices" in data:
            msg = data["choices"][0].get("message", {})
        else:
            msg = data.get("message", {})

        tool_calls = msg.get("tool_calls", []) or []

        # Fallback: parse prose-as-tool-call (defect #9)
        if not tool_calls:
            parsed = parse_text_tool_call(msg.get("content", ""))
            if parsed:
                tool_calls = [
                    {"id": f"call_fallback_{uuid.uuid4().hex[:8]}", "function": {"name": n, "arguments": json.dumps(a)}}
                    for n, a in parsed
                ]
                msg = {"role": "assistant", "content": "", "tool_calls": tool_calls}
                log(f"  FALLBACK parsed tool call: {parsed}")

        if tool_calls:
            messages.append(msg)
            for tc in tool_calls:
                if _STOP["stop"]:
                    _STOP["stop"] = False
                    return "Stopped.", len(tools_used), tools_used
                func = tc.get("function", {})
                tc_id = tc.get("id", "")  # tool_call_id from Z.ai/OpenAI response
                name = func.get("name", "")
                raw = func.get("arguments", "{}")
                if isinstance(raw, dict):
                    args = raw
                elif isinstance(raw, str):
                    try: args = json.loads(raw)
                    except Exception as e:
                        # HONEST FAILURE (friend 2026-08-25): never substitute empty
                        # args silently — that turns a cut stream into a phantom
                        # write_file({}). The model MUST hear the parse error so it
                        # can split the work into chunks instead of retrying the
                        # same doomed call.
                        log(f"  TOOL ARGS PARSE FAIL for {name}: {str(e)[:100]} (raw {len(raw)} chars)")
                        result = (f"ARGUMENT PARSE ERROR on {name}: {str(e)[:150]}. "
                                  f"Your arguments JSON arrived cut or malformed "
                                  f"({len(raw)} chars received). DO NOT retry the same call. "
                                  f"Split the content into chunks under 3000 chars: first call "
                                  f"without append, then append=true per chunk.")
                        tools_used.append({"name": name, "args": {"_parse_error": True}})
                        tool_result_msg = {"role": "tool", "content": result[:20000]}
                        if tc_id:
                            tool_result_msg["tool_call_id"] = tc_id
                        messages.append(tool_result_msg)
                        continue
                else:
                    args = {}
                log(f"  TOOL: {name}({args})")
                tool_fn = TOOLS.get(name)
                if not tool_fn:
                    log(f"  Unknown tool: {name}, skipping")
                    result = f"Unknown tool: {name}"
                else:
                    try:
                        # Inspect required args and provide defaults for missing ones
                        import inspect
                        sig = inspect.signature(tool_fn)
                        bound = {}
                        for pname, param in sig.parameters.items():
                            if pname in args:
                                bound[pname] = args[pname]
                            elif param.default is not inspect.Parameter.empty:
                                bound[pname] = param.default
                            else:
                                bound[pname] = ""  # provide empty default for missing required args
                        result = tool_fn(**bound)
                    except Exception as e:
                        log(f"  Tool {name} error: {e}")
                        result = f"Tool error: {e}"
                tools_used.append({"name": name, "args": args})
                # Z.ai/OpenAI require tool_call_id in tool result messages
                tool_result_msg = {"role": "tool", "content": str(result)[:20000]}
                if tc_id:
                    tool_result_msg["tool_call_id"] = tc_id
                messages.append(tool_result_msg)

                # LIVE STATUS: push each tool call result so the frontend shows progress
                if request_id:
                    _update_status(request_id, "working",
                                   turn=turn + 1,
                                   tools_used=tools_used,
                                   progress=f"Used {name}, thinking next...")
            continue  # think again with the tool result

        # No tool call = final answer
        text = msg.get("content", "").strip()
        reasoning_so_far = _reasoning_buffer.get(request_id, "") if request_id else ""

        # ── RSVP MIMICRY GUARD (friend 2026-08-28) ──────────────────
        # Some cloud cards mimic the rolling-context history format and
        # bake their thinking INTO content as
        # "[Reasoning trace from that turn]\n...\n[/Reasoning]\n<reply>".
        # That preamble belongs in the RSVP reasoning dropdown, not the
        # bubble. Split it: marker block → reasoning, rest → clean reply.
        if text.startswith("[Reasoning trace from that turn]"):
            _end = text.find("[/Reasoning]")
            if _end != -1:
                _baked = text[len("[Reasoning trace from that turn]"):_end].strip()
                _clean = text[_end + len("[/Reasoning]"):].strip()
                if _clean:
                    if _baked:
                        reasoning_so_far = (reasoning_so_far + "\n" + _baked).strip() if reasoning_so_far else _baked
                        if request_id:
                            _reasoning_buffer[request_id] = reasoning_so_far
                    text = _clean
                    log(f"  RSVP mimicry guard: moved {len(_baked)} chars of baked-in reasoning to RSVP dropdown")

        # BUG FIX: If reasoning_content was produced but content is empty,
        # the model "thought" but didn't answer. Don't loop forever — break out
        # and return the reasoning text as the reply so the user gets value.
        if not text and reasoning_so_far:
            log(f"  Reasoning present ({len(reasoning_so_far)} chars) but content empty — breaking loop")
            # Return the reasoning as the visible reply — it IS the content
            reply_text = reasoning_so_far.strip()
            # Truncate for display but keep full in history
            display_reply = reply_text[:800] + ("..." if len(reply_text) > 800 else "")
            _save_turn(session_id, "assistant", reply_text, _active_model)
            try: narrate("Thinking complete.")
            except Exception: pass
            return display_reply, 0, tools_used

        if text:
            # ── CANONICAL COMMAND SNIFFER — the architecture's last line of defense.
            #    If the model described an action (looks_like_action) but didn't
            #    call queue_command, AND the prose contains something that looks
            #    like a canonical command (interpreter path + .py), extract it,
            #    verify it, queue it, and tell the model it happened.
            #    This catches the drift where AILA narrates instead of acting.
            #    Zero inference — this is pattern matching on OUTPUT, not INPUT.
            if is_action and not tools_used:
                sniffed = _sniff_canonical_command(text)
                if sniffed:
                    ok, reason = _verify_command(sniffed)
                    if ok:
                        log(f"SNIFFED and auto-queued: {sniffed[:100]}")
                        with open(QUEUE_FILE, "a", encoding="utf-8") as f:
                            f.write(sniffed + "\n")
                        narrate(f"Command auto-queued from AILA's reply. Stack depth now {_queue_depth()}.")
                        tools_used.append({"name": "queue_command", "args": {"command": sniffed}})
                        # Don't send as tool role — there's no real tool call to match.
                        # Just save the turn and return with the text.
                        _save_turn(session_id, "assistant", text, _active_model,
                                   reasoning=reasoning_so_far or None)
                        try: narrate(text)
                        except Exception: pass
                        return text, len(tools_used), tools_used

            # ── INTENT SUGGESTION: when input looked like an action but no tool
            #    fired and no canonical command was found in prose, suggest verbs.
            if is_action and not tools_used:
                similar = _find_similar_skills(user_message)
                if similar:
                    hints = "\n  ".join(f"'{v}' — {d}" for v, d, _ in similar)
                    suggestion = f"\n\nDid you want to:\n  {hints}"
                    text = text + suggestion
                    try:
                        top_verb = similar[0][0]
                        top_desc = similar[0][1]
                        narrate(f"Did you want to {top_verb}? {top_desc}")
                    except Exception:
                        pass
            _save_turn(session_id, "assistant", text, _active_model,
                       reasoning=reasoning_so_far or None)
            # LAW 1: narrate the FULL reply — no truncation here.
            # narrate() chunks long text at sentence boundaries (MAX_CHUNK=2000)
            # so Piper speaks every word in order. The cap used to live here and
            # beheaded long replies. Removed 2026-07-17.
            try: narrate(text)
            except Exception: pass
            return text, len(tools_used), tools_used

        # TEAM LOOP: budget decrements every turn; a steer refills it.
        # When the budget hits 0, the loop EXITS here (not at the hard
        # ceiling) and falls through to the TURN-LIMIT DELIVERY below —
        # she speaks her freshest thinking, exactly as designed.
        _turn_budget -= 1
        if _turn_budget <= 0:
            break

    # TURN-LIMIT DELIVERY (friend 2026-09-12): the run hit 50 turns with no
    # final answer. The old path returned "Turn limit reached." silently —
    # the reply landed in _pending but was NEVER narrated, so the operator
    # heard nothing and the run looked dead. Now: the reasoning trace IS the
    # work product — deliver the freshest 2000 chars of real thinking as the
    # reply, narrate it, save it. He hears what she was doing when the clock
    # ran out. Never silence again.
    _remember_failure(session_id, request_id, _active_model, tools_used, "turn limit reached")
    _final_reasoning = _reasoning_buffer.get(request_id, "") if request_id else ""
    if _final_reasoning.strip():
        _tail = _final_reasoning.strip()[-2000:]
        _reply = ("[Turn limit reached — 50 turns. Here is her freshest thinking "
                  "when the clock ran out:]\n" + _tail)
        _save_turn(session_id, "assistant", _reply, _active_model,
                   reasoning=_final_reasoning or None)
        try: narrate(_reply)
        except Exception: pass
        return _reply, len(tools_used), tools_used
    _reply = "Turn limit reached."
    try: narrate(_reply)
    except Exception: pass
    return _reply, len(tools_used), tools_used

# ════════════════════════════════════════════════════════════
#  HTTP SERVER
# ════════════════════════════════════════════════════════════
class SimpleJackHandler(BaseHTTPRequestHandler):

    def _send_json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return {}
        return {}

    def do_OPTIONS(self):
        self._send_json({"ok": True})

    def do_GET(self):
        path = urlparse(self.path).path

        # SIGN-IN GATE — everything behind it, no exceptions
        if not _gate_allows(self):
            log(f"GATE: rejected {self.headers.get('Cf-Connecting-IP', '?')} GET {path}")
            _gate_denied(self)
            return

        # OUTPUT PATH — the rail text box. One line in AGENTS.md is the law:
        # "OUTPUT_PATH: <folder>" — every agent reads AGENTS.md every message,
        # so changing the box changes where everything goes. No rediscovery.
        if path == "/api/output_path":
            p = SIMPLEJACK_ROOT / "AGENTS.md"
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
                m = re.search(r"^OUTPUT_PATH:\s*(.+)$", txt, re.M)
                self._send_json({"ok": True, "path": (m.group(1).strip() if m else "")})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e), "path": ""})
            return

        # Health — is the brain alive? (The hub is the engine; check the hub.)
        if path == "/api/health":
            hub_up = False
            n = 0
            try:
                r = requests.get(MODEL_HUB_MODELS_URL, timeout=5)
                if r.status_code == 200:
                    hub_up = True
                    n = len(r.json().get("data", []))
            except Exception:
                pass
            self._send_json({
                "status": "alive",
                "hub": "up" if hub_up else "DOWN",
                "models": n,
                "active_model": AILA_MODEL,
                "ts": datetime.now().isoformat()
            })
            return

        # SETUP STATUS — first-run gate. The hub is the ONE engine: if the
        # hub is up and serving cards, everything is configured. Never a fake "ready".
        if path == "/api/setup_status":
            try:
                r = requests.get(MODEL_HUB_MODELS_URL, timeout=5)
                hub_up = r.status_code == 200
                hub_models = len(r.json().get("data", [])) if hub_up else 0
            except Exception:
                hub_up, hub_models = False, 0
            self._send_json({
                "ok": True,
                "configured": {"model_hub": hub_up},
                "hub": {"up": hub_up, "models": hub_models},
                "can_chat": hub_up and hub_models > 0,
            })
            return

        # VOICE PILL state — is the switch on? Is the mouth alive?
        if path == "/api/voice":
            folder_on, mouth_pid = _voice_state()
            self._send_json({
                "ok": True,
                "voice_on": folder_on,
                "mouth_pid": mouth_pid,
            })
            return

        # LIVE model list — grouped by engine + local Ollama
        if path == "/api/models":
            # THE HUB CONTRACT — SimpleJack sees NOTHING but the hub's
            # active models. No Ollama allowlist, no engines dict, no
            # hardcoded menus. GET /v1/models is the whole truth.
            hub_models = []
            hub_up = False
            try:
                r = requests.get(MODEL_HUB_MODELS_URL, timeout=5)
                if r.status_code == 200:
                    for m in r.json().get("data", []):
                        if m.get("id"):
                            hub_models.append(m["id"])
                            # Live context windows ride in with the cards —
                            # the hub is the single source of truth.
                            ctx = m.get("context_length")
                            if ctx:
                                MODEL_CONTEXTS[m["id"]] = ctx
                    hub_up = True
            except Exception:
                pass

            # Active model: whatever the hub says exists, or fall back to
            # the persisted choice; if that died, first hub model.
            active = AILA_MODEL
            if hub_models:
                if active not in hub_models:
                    active = hub_models[0]
            else:
                active = ""

            self._send_json({
                "active": active,
                "engines": {
                    "hub": {
                        "name": "Model Hub",
                        "models": hub_models,
                        "has_key": hub_up,
                    }
                },
                "local": [],
                "ollama_up": hub_up,
                "blocklist": {},
                "custom": [],
            })
            return

        # MODEL LIST MANAGEMENT — REMOVED 2026-08-23. The hub owns every
        # model list. Add, remove, archive cards on the hub UI at
        # http://127.0.0.1:8123/ — never through the app.

        # STACK state — queue depth + current command (so the HTML panel is live)
        if path == "/api/stack":
            self._send_json({
                "depth": _queue_depth(),
                "current": _current_command(),
                "queue_preview": _queue_preview()
            })
            return

        # FILE TREE — real Desktop contents, recursive, collapsible
        if path == "/api/file_tree":
            tree = _build_file_tree()
            self._send_json(tree)
            return

        # STATUS — poll for async conversation result.
        # GET (not POST!) — the browser polls via fetch('/api/status/<id>')
        # with no method, which defaults to GET. This handler used to live
        # inside do_POST, so every poll hit do_GET → 404 → the browser
        # showed "Poll Error: HTTP 404" forever and the reply never reached
        # the screen. Moved to do_GET 2026-07-21. THE screen-print bug.
        if path.startswith("/api/status/"):
            rid = path.split("/")[-1]
            with _pending_lock:
                result = _pending.get(rid)
            if result:
                self._send_json(result)
            else:
                self._send_json({"status": "unknown", "error": f"no request {rid}"}, 404)
            return

        # LIVE RUN REGISTRY (friend 2026-09-12): the page asks on load —
        # "is anything live?" If yes, it reopens the RSVP box on that ticket,
        # streams the trace, and marks the textbox steer-mode. Leave and
        # come back: the trace is there. Steer: it lands as a steer.
        # Fixes BOTH: trace lost on leave + steer landing after reply.
        if path == "/api/live":
            with _pending_lock:
                live_ids = [rid for rid, v in _pending.items()
                            if v.get("status") in ("thinking", "working", "queued")]
            if not live_ids:
                self._send_json({"live": False})
                return
            # Freshest live run (queued tickets wait; thinking/working wins)
            rid = None
            for cand in live_ids:
                if _pending.get(cand, {}).get("status") in ("thinking", "working"):
                    rid = cand
                    break
            if rid is None:
                rid = live_ids[0]
            with _pending_lock:
                entry = dict(_pending.get(rid, {}))
            entry["live"] = True
            entry["request_id"] = rid
            entry["reasoning"] = _reasoning_buffer.get(rid, "")
            self._send_json(entry)
            return

        # REASONING — live reasoning tokens for RSVP streaming
        # Polled by the frontend at 300ms intervals while AILA thinks.
        # Returns accumulated reasoning text and whether thinking is done.
        if path.startswith("/api/reasoning/"):
            rid = path.split("/")[-1]
            with _pending_lock:
                status = _pending.get(rid, {}).get("status", "unknown")
            reasoning = _reasoning_buffer.get(rid, "")
            with _pending_lock:
                turn_now = _pending.get(rid, {}).get("turn", 1)
            self._send_json({
                "status": "done" if status == "done" else "streaming",
                "reasoning": reasoning,
                "turn": turn_now
            })
            return

        # HISTORY — raw conversation JSONL for frontend rebuild on page load
        if path.startswith("/api/history/"):
            sid = path.split("/")[-1]
            f = _CONV_DIR / f"session_{sid}.jsonl"
            if not f.exists():
                self._send_json([])
                return
            entries = []
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
            self._send_json(entries)
            return

        # Serve the interface
        if path == "/" or path == "/simplejack.html":
            if HTML_FILE.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(HTML_FILE.read_bytes())
                return

        # Static assets (SHORTSCOVER.mp4 etc.) — serve from the SIMPLEJACK folder
        if not path.startswith("/api/"):
            rel = unquote(path.lstrip("/"))
            asset = (SIMPLEJACK_ROOT / rel).resolve()
            try:
                asset.relative_to(SIMPLEJACK_ROOT)
            except ValueError:
                self._send_json({"error": "forbidden"}, 403)
                return
            if asset.exists() and asset.is_file():
                # video: stream with range support
                self._serve_asset(asset)
                return

        self._send_json({"error": "not found", "path": path}, 404)

    def _serve_asset(self, asset):
        """Serve a static file with basic range support (needed for video)."""
        size = asset.stat().st_size
        range_header = self.headers.get("Range")
        start = 0
        end = size - 1
        if range_header:
            m = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if m:
                start = int(m.group(1))
                if m.group(2):
                    end = int(m.group(2))
        length = end - start + 1
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", _guess_mime(asset))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        with open(asset, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_POST(self):
        path = urlparse(self.path).path

        # SIGN-IN GATE — everything behind it, no exceptions
        if not _gate_allows(self):
            log(f"GATE: rejected {self.headers.get('Cf-Connecting-IP', '?')} POST {path}")
            _gate_denied(self)
            return

        # CONVERSE — fire agent_loop in a background thread, return request_id immediately.
        # OUTPUT PATH SAVE — the rail box writes ONE line of AGENTS.md.
        # Timestamped backup first (LAW 14), atomic replace, path echoed back.
        if path == "/api/output_path":
            body = self._read_body()
            new_path = (body.get("path") or "").strip().strip('"').strip("'")
            try:
                p = SIMPLEJACK_ROOT / "AGENTS.md"
                txt = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
                m = re.search(r"^OUTPUT_PATH:.*$", txt, re.M)
                if new_path:
                    line = "OUTPUT_PATH: " + new_path
                else:
                    line = None  # empty box = remove the line
                if m:
                    if line:
                        txt = txt[:m.start()] + line + txt[m.end():]
                    else:
                        txt = txt[:m.start()].rstrip() + "\n" + txt[m.end():]
                elif line:
                    txt = txt.rstrip() + "\n\n## THE OUTPUT LAW (operator 2026-09-17)\nEvery skill that produces a finished deliverable writes it under this folder — known by construction, never rediscovered. NO MORE RESOURCES spent finding where built things live.\n\n" + line + "\n"
                bak = p.with_name("AGENTS.md.bak." + datetime.now().strftime("%Y%m%d_%H%M%S"))
                if p.exists():
                    bak.write_bytes(p.read_bytes())
                tmp = p.with_name("AGENTS.md.tmp")
                tmp.write_text(txt, encoding="utf-8")
                tmp.replace(p)
                log(f"OUTPUT PATH SAVED: {new_path!r} (backup: {bak.name})")
                self._send_json({"ok": True, "path": new_path})
            except Exception as e:
                log(f"output path save failed: {e}")
                self._send_json({"ok": False, "error": str(e)})
            return

        # Browser polls /api/status/<id> until "done". HTTP thread stays free.
        if path == "/api/converse":
            body = self._read_body()
            message = body.get("message", "").strip()
            session = body.get("session_id", "default")
            if not message:
                self._send_json({"success": False, "error": "empty message"})
                return

            # ── THE '&' HOOK (legend.py) ──
            # When the prompt contains '&', legend.resolve() finds the skill,
            # builds the canonical command, and we queue + fire the stack
            # BEFORE the model runs. Structural — no decision, no model in
            # the command loop. The model still runs in parallel for the
            # conversational reply, but the command is already in the queue.
            translator_note = ""
            if "&" in message:
                try:
                    result = LEGEND.resolve(message)
                except Exception as e:
                    result = {"ok": False, "verb": None, "reason": f"legend error: {e}"}
                if result.get("ok"):
                    cmd = result["command"]
                    verb = result.get("verb", "?")
                    # Queue the command (verified inside legend.resolve already)
                    with open(QUEUE_FILE, "a", encoding="utf-8") as f:
                        f.write(cmd + "\n")
                    # Fire the stack
                    flag = SIMPLEJACK_ROOT / "dispatch" / ".run"
                    flag.write_text("run\n", encoding="utf-8")
                    depth = _queue_depth()
                    translator_note = f"command queued via legend: {verb} (depth {depth})"
                    log(f"LEGEND fired '{verb}': {cmd[:100]}")
                    try: narrate(f"Command translated and queued. {verb}. Stack depth {depth}. Firing now.")
                    except Exception: pass
                else:
                    translator_note = f"legend could not resolve: {result.get('reason')}"
                    log(f"LEGEND miss: {result.get('reason')}")

            # Strip '&' before sending to the model so AILA sees only the
            # conversational prompt. Append the translator note so her reply
            # can acknowledge what happened (or ask for the missing piece).
            if "&" in message:
                model_message = message.replace("&", " ").strip()
                if translator_note:
                    model_message = f"{model_message}\n\n(system: {translator_note})"
            else:
                model_message = message

            request_id = str(uuid.uuid4())[:12]
            model_override = body.get("model")  # per-request model override (tab pinning)
            # SEQUENTIAL ONLY (friend 2026-08-25): one request at a time, ever.
            # 2026-09-12 PHONE FIX (operator order): busy no longer REFUSES with 409.
            # Busy = HOLD the message in a FIFO wait queue and tell the sender
            # "standby" (202). The moment the current run finishes, the next
            # held message fires automatically, in order. Phone and laptop are
            # ONE process, ONE brain, ONE line. Nothing is lost, nothing 409s.
            with _pending_lock:
                busy = any(v.get("status") in ("thinking", "working")
                           for v in _pending.values())
            if busy:
                _wait_queue.append({"request_id": request_id,
                                    "message": model_message,
                                    "session": session,
                                    "model": model_override})
                pos = len(_wait_queue)
                log(f"PHONE QUEUE: busy — held message #{pos} ({len(model_message)} chars) as {request_id}, sender told standby")
                with _pending_lock:
                    _pending[request_id] = {"status": "queued", "turn": 0, "queue_position": pos}
                self._send_json({"success": True,
                                 "request_id": request_id,
                                 "status": "queued",
                                 "queue_position": pos,
                                 "notice": "AILA is working. Please standby — your message is held and will run the moment she finishes."}, 202)
                return
            with _pending_lock:
                _pending[request_id] = {"status": "thinking", "turn": 1}
            t = threading.Thread(
                target=_run_agent_async,
                args=(request_id, model_message, session, model_override),
                daemon=True
            )
            t.start()
            self._send_json({"success": True, "request_id": request_id, "status": "thinking"})
            return

        # STATUS poll moved to do_GET (browser fetches via GET). See do_GET.

        # Switch active model
        if path == "/api/switch_model":
            body = self._read_body()
            model = body.get("model", "").strip()
            if model:
                set_active_model(model)
                log(f"Model switched to: {AILA_MODEL}")
                self._send_json({"ok": True, "active": AILA_MODEL})
            else:
                self._send_json({"ok": False, "error": "no model"})
            return

        # Manual stack push (for testing without the model)
        if path == "/api/queue":
            body = self._read_body()
            cmd = body.get("command", "").strip()
            if cmd:
                result = tool_queue_command(cmd)
                self._send_json({"ok": True, "result": result, "depth": _queue_depth()})
            else:
                self._send_json({"ok": False, "error": "empty command"})
            return

        # Stop the loop
        if path == "/api/stop":
            # STICKY STOP (friend 2026-09-01): no blind 5s clear. The reset
            # loop now HOLDS the flag while any request is thinking/working
            # and only clears it once nothing is active (or a new request
            # arrives and clears it itself).
            _STOP["stop"] = True
            _stop_auto_reset(5)
            self._send_json({"ok": True})
            return

        # ── STEER (friend 2026-09-06): mid-flight operator amendment ──
        # The operator adds a prompt WHILE the model is thinking. NOT a
        # second request — the busy gate does NOT apply here. The steer
        # amends the ONE request in flight: the stream loop sees it at
        # the next chunk boundary, keeps every partial token, and the
        # model is re-called with its own last words + the new prompt.
        # Same rail as /api/stop, richer payload.
        if path == "/api/steer":
            body = self._read_body()
            steer_text = body.get("message", "").strip()
            if not steer_text:
                self._send_json({"ok": False, "error": "empty steer"})
                return
            # Only meaningful if a request is actually live
            with _pending_lock:
                live = any(v.get("status") in ("thinking", "working")
                            for v in _pending.values())
            if not live:
                self._send_json({"ok": False,
                                 "error": "AILA is not working right now — nothing to steer. Send it as a new message."})
                return
            # STEER QUEUE (operator order 2026-09-20): check the slot BEFORE
            # writing it. If an earlier amendment is still waiting for a chunk
            # to land on, this one QUEUES behind it — FIFO, never overwritten.
            # Nothing he says is ever destroyed again.
            with _STEER_QUEUE_LOCK:
                _slot_busy = _STEER["text"] is not None
                if _slot_busy:
                    _STEER_QUEUE.append(steer_text)
                    log(f"STEER QUEUED ({len(steer_text)} chars) — slot busy, {len(_STEER_QUEUE)} waiting")
                else:
                    _STEER["text"] = steer_text
                    _STEER["ts"] = time.time()
                _STEER["_held"] = steer_text
            if _slot_busy:
                self._send_json({"ok": True, "queued": True, "queue_pos": len(_STEER_QUEUE),
                                 "echo": f"Heard: {steer_text}"})
                try:
                    narrate(f"Heard: {steer_text}. Queued behind your last amendment.")
                except Exception:
                    pass
                return
            log(f"STEER accepted ({len(steer_text)} chars) — will break the stream at the next chunk boundary")
            # STEER ECHO (friend 2026-09-09): the channel's arrival alarm.
            # Every steer that lands is echoed back to the operator's ear,
            # verbatim. The morning failure was silence looking identical
            # to absence — you spoke, nothing arrived, nobody knew. Now:
            # the moment the echo STOPS, you know the channel died.
            # Silence became detectable. No echo = speak again or reload.
            try:
                narrate(f"Heard: {steer_text}")
            except Exception:
                pass
            self._send_json({"ok": True, "steered": True})
            return

        # ── REFRESH BUTTON = KILL + RESTART BRAIN (friend 2026-09-01) ──
        # The top-right refresh button now restarts simplejack.py itself.
        # This endpoint answers the browser, THEN relaunches detached so
        # the new process is NOT a child of the dying one. Same kill+start
        # the STARTSIMPLEJACK.bat brain step already does.
        #
        # REPAIR 2026-09-19 (operator order: "refresh shuts down the
        # existing Jack BEFORE opening another"): the old helper killed
        # listeners and started the new brain 1.0s later WITHOUT proving
        # the port was free. ThreadingHTTPServer sets SO_REUSEADDR, so on
        # Windows several brains CAN bind 8797 at once — six starts today
        # stacked three listeners and two brains answered one door.
        # New helper loop: kill ALL :8797 listeners, then RE-CHECK until
        # netstat shows ZERO listeners (max 15 x 1s), and only then start
        # exactly one new brain.
        if path == "/api/restart":
            try:
                self._send_json({"ok": True, "message": "Brain restarting..."})
            except Exception:
                pass
            root = str(MORPHYES_ROOT)
            # REPAIR 2026-09-20 (root cause of "refresh didn't work"): this
            # handler referenced bare `ROOT` and bare `py` — names that DO NOT
            # EXIST in this scope (only SIMPLEJACK_ROOT exists globally; `py`
            # only exists inside another function at module top). Both Popen
            # attempts raised NameError. The first was caught (fell to the
            # fallback), the second died silently AFTER the browser had
            # already been told "ok" — and the frontend polls the HUB (a
            # different, always-up process), so it reported "BRAIN RESTARTED"
            # falsely. Silent no-op since the 2026-09-19 16:49 repair.
            # Fix: use the names that actually exist. No NameError possible.
            _rel = SIMPLEJACK_ROOT / "STACK" / "brain_relaunch.py"
            try:
                subprocess.Popen([sys.executable, str(_rel)],
                                 creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                                 close_fds=True)
                log(f"RESTART: relauncher fired -> {_rel}")
            except Exception as _re:
                log(f"RESTART FAILED: could not fire relauncher: {_re}")
                try:
                    narrate(f"Warning. The brain restart failed to fire. {str(_re)[:120]}. Say the word and I will restart it from here.")
                except Exception:
                    pass
            return

        # REFRESH SERVERS — status only (2026-08-23). The app NEVER kills or
        # launches processes anymore — that power does not belong to a web
        # page. The hub is the engine; report its state honestly.
        if path == "/api/refresh_servers":
            hub_up = False
            try:
                r = requests.get(MODEL_HUB_MODELS_URL, timeout=5)
                hub_up = r.status_code == 200
            except Exception:
                pass
            msg = ("Model Hub is up — cards are live."
                   if hub_up else
                   "Model Hub DOWN. Start it on the machine (start_model_loader.bat).")
            self._send_json({"ok": True, "message": msg,
                             "hub": "up" if hub_up else "DOWN",
                             "ollama": "up" if hub_up else "DOWN"})
            return

        # KEY ENDPOINTS — REMOVED 2026-08-23. The app never touches keys.
        # The hub vault owns every credential. Stubs keep the UI honest.
        if path == "/api/save_key" or path == "/api/test_key":
            self._send_json({"ok": False,
                             "error": "Keys live in the Model Hub vault on the machine. "
                                      "The app never touches keys."})
            return

        # NARRATE — the extension's pipe into the narration queue.
        # Inherits THE SWITCH (friend's rule): if the queue folder is missing,
        # the brain writes nothing. Not more. Not less.
        if path == "/api/narrate":
            body = self._read_body()
            text = (body.get("text") or "").strip()
            source = (body.get("source") or "extension").strip()[:40]
            if text:
                # Re-resolve switch: the folder may exist or not, right now.
                # One queue only — the portable one. No canonical. No fallback.
                p_queue = SIMPLEJACK_ROOT / "morphytrek_data" / "queue"
                target = p_queue if p_queue.exists() else None
                if target is not None:
                    try:
                        entry = {
                            "text": text,
                            "source": source,
                            "engine": "piper",
                            "ts": datetime.now().isoformat(),
                        }
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                        (target / f"ext_{ts}.json").write_text(
                            json.dumps(entry, ensure_ascii=False), encoding="utf-8"
                        )
                        self._send_json({"ok": True, "written": True})
                        return
                    except Exception as e:
                        self._send_json({"ok": False, "error": f"narrate failed: {e}"})
                        return
            # Folder missing OR empty text → silent no-op, never an error page.
            self._send_json({"ok": True, "written": False})
            return

        # VOICE PILL toggle — one click flips the folder switch + the mouth.
        # Body: {"action": "on"|"off"|"toggle"}. Missing = "toggle".
        if path == "/api/voice":
            body = self._read_body()
            action = (body.get("action") or "toggle").strip().lower()
            folder_on, mouth_pid = _voice_state()
            if action == "on":
                ok = voice_on()
            elif action == "off":
                ok = voice_off()
            else:  # toggle
                ok = voice_off() if folder_on else voice_on()
            folder_on, mouth_pid = _voice_state()
            self._send_json({
                "ok": ok,
                "voice_on": folder_on,
                "mouth_pid": mouth_pid,
            })
            return

        # WIPE TRANSCRIPT - the nuclear button, double-click.
        # Archives the raw conversation JSONL to a timestamped file, then
        # starts a fresh empty transcript. NOT a delete - the archive IS the
        # backup, kept forever. Nothing is ever truly lost.
        if path == "/api/wipe_transcript":
            try:
                conv = _CONV_DIR / "session_default.jsonl"
                arch_dir = SIMPLEJACK_ROOT / "LEDGER" / "transcript_archive"
                arch_dir.mkdir(parents=True, exist_ok=True)
                wiped = 0
                if conv.exists():
                    wiped = sum(1 for _ in conv.open(encoding="utf-8", errors="replace"))
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    dst = arch_dir / f"session_default_{stamp}.jsonl"
                    import shutil
                    shutil.copy2(conv, dst)
                    conv.write_text("", encoding="utf-8")
                    log(f"TRANSCRIPT WIPED: {wiped} lines archived to {dst.name}")
                self._send_json({"ok": True, "wiped_lines": wiped,
                                 "archive": str(arch_dir)})
            except Exception as e:
                log(f"wipe transcript failed: {e}")
                self._send_json({"ok": False, "error": str(e)})
            return

        # READ_TAB_HOOK — the plus button sends a path, we read the TAB boilerplate
        if path == "/api/read_tab_hook":
            body = self._read_body()
            filepath = body.get("path", "").strip().strip('"').strip("'")
            if not filepath:
                self._send_json({"error": "No path provided."})
                return
            if not filepath.lower().endswith(".py"):
                self._send_json({"error": "Must be a .py file."})
                return
            p = Path(filepath).resolve()
            if not p.exists():
                self._send_json({"error": "File not found: " + filepath})
                return
            # Read the TAB hook lines from the file
            tab_data = {"id": p.stem, "name": p.stem, "sub": "", "icon": "", "hook": ""}
            try:
                lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
                for line in lines:
                    if line.startswith("# TAB:") and "|" in line:
                        parts = [x.strip() for x in line.split("|")]
                        tab_data["name"] = parts[0].replace("# TAB:", "").strip() if len(parts) > 0 else p.stem
                        tab_data["icon"] = parts[1].strip() if len(parts) > 1 else ""
                        tab_data["sub"] = parts[2].strip() if len(parts) > 2 else ""
                    elif line.startswith("# HOOK:") and "|" in line:
                        parts = [x.strip() for x in line.split("|")]
                        tab_data["hook"] = parts[0].replace("# HOOK:", "").strip() if len(parts) > 0 else ""
            except Exception as e:
                self._send_json({"error": "Cannot read file: " + str(e)})
                return
            self._send_json(tab_data)
            return

        # RENDER_MODULE — nav click sends module path, we call render_html() and return it
        if path == "/api/render_module":
            body = self._read_body()
            filepath = body.get("path", "").strip().strip('"').strip("'")
            if not filepath:
                self._send_json({"error": "No path."})
                return
            p = Path(filepath).resolve()
            if not p.exists():
                self._send_json({"error": "File not found."})
                return
            try:
                import importlib.util, types
                spec = importlib.util.spec_from_file_location("mod_module", str(p))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "render_html"):
                    html = mod.render_html()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(json.dumps({"html": html}).encode())
                else:
                    self._send_json({"error": "Module has no render_html() function."})
            except Exception as e:
                self._send_json({"error": str(e)})
            return

        # AGENT AILA CHAT — async relay to Hermes Desktop
        # Returns request_id immediately. Browser polls /api/status/<id>.
        # Same pattern as /api/converse — never blocks the HTTP thread.
        if path == "/api/agentaila_chat":
            body = self._read_body()
            message = body.get("message", "").strip()
            if not message:
                self._send_json({"error": "empty message"})
                return

            request_id = str(uuid.uuid4())[:12]
            with _pending_lock:
                _pending[request_id] = {"status": "thinking"}

            def _run_agentaila(rid, msg):
                try:
                    sys.path.insert(0, str(SIMPLEJACK_ROOT))
                    import agentaila as ag
                    result = ag.query(msg)
                    with _pending_lock:
                        _pending[rid] = {
                            "status": "done",
                            "reply": result.get("response", ""),
                            "drift": result.get("drift", []),
                            "model": "Hermes relay"
                        }
                except Exception as e:
                    with _pending_lock:
                        _pending[rid] = {
                            "status": "error",
                            "reply": f"Agent AILA error: {e}",
                            "model": "Hermes relay"
                        }

            t = threading.Thread(target=_run_agentaila, args=(request_id, message), daemon=True)
            t.start()
            self._send_json({"success": True, "request_id": request_id, "status": "thinking"})
            return

        # DISPATCH CHAT — async relay to dispatch_aila.py module
        if path == "/api/dispatch_chat":
            body = self._read_body()
            message = body.get("message", "").strip()
            if not message:
                self._send_json({"error": "empty message"})
                return

            request_id = str(uuid.uuid4())[:12]
            with _pending_lock:
                _pending[request_id] = {"status": "thinking"}

            def _run_dispatch(rid, msg):
                try:
                    sys.path.insert(0, str(SIMPLEJACK_ROOT))
                    import dispatch_aila as da
                    result = da.query(msg)
                    with _pending_lock:
                        _pending[rid] = {
                            "status": "done",
                            "reply": result.get("response", ""),
                            "drift": result.get("drift", []),
                            "model": "dispatch",
                            "turns": 1
                        }
                except Exception as e:
                    with _pending_lock:
                        _pending[rid] = {
                            "status": "error",
                            "reply": f"Dispatch error: {e}",
                            "model": "dispatch"
                        }

            t = threading.Thread(target=_run_dispatch, args=(request_id, message), daemon=True)
            t.start()
            self._send_json({"success": True, "request_id": request_id, "status": "thinking"})
            return

        # AGENT CHAT — async relay to agent_aila.py module
        if path == "/api/agent_chat":
            body = self._read_body()
            message = body.get("message", "").strip()
            if not message:
                self._send_json({"error": "empty message"})
                return

            request_id = str(uuid.uuid4())[:12]
            with _pending_lock:
                _pending[request_id] = {"status": "thinking"}

            def _run_agent_mod(rid, msg):
                try:
                    sys.path.insert(0, str(SIMPLEJACK_ROOT))
                    import agent_aila as aa
                    result = aa.query(msg)
                    with _pending_lock:
                        _pending[rid] = {
                            "status": "done",
                            "reply": result.get("response", ""),
                            "drift": result.get("drift", []),
                            "model": "glm-5-turbo",
                            "turns": len(result.get("drift", []))
                        }
                except Exception as e:
                    with _pending_lock:
                        _pending[rid] = {
                            "status": "error",
                            "reply": f"Agent error: {e}",
                            "model": "glm-5-turbo"
                        }

            t = threading.Thread(target=_run_agent_mod, args=(request_id, message), daemon=True)
            t.start()
            self._send_json({"success": True, "request_id": request_id, "status": "thinking"})
            return

        self._send_json({"error": "not found", "path": path}, 404)

    # Quieter logging
    def log_message(self, *args):
        pass

def _queue_preview(n=5):
    try:
        lines = [l for l in QUEUE_FILE.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")]
        return lines[:n]
    except Exception:
        return []

def _guess_mime(asset):
    suf = asset.suffix.lower()
    return {
        ".html": "text/html", ".js": "application/javascript",
        ".css": "text/css", ".json": "application/json",
        ".mp4": "video/mp4", ".webm": "video/webm",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".svg": "image/svg+xml",
        ".ico": "image/x-icon", ".txt": "text/plain", ".md": "text/plain",
    }.get(suf, "application/octet-stream")

# ════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════

# (App-side blocklist / custom model lists REMOVED 2026-08-23 — the hub
#  owns every model list. Curate cards at http://127.0.0.1:8123/)

def main():
    log("=" * 60)
    log("simpleJACK — MorPHYes Mastery")
    log(f"Server: http://localhost:{PORT}")
    log(f"Interface: http://localhost:{PORT}/simplejack.html")
    log(f"Narration queue: {NARRATION_QUEUE}")
    log(f"STACK: {STACK_DIR}")
    log("Engine: Model Hub — the ONE engine (127.0.0.1:8123). App holds zero keys.")
    log("=" * 60)

    STACK_DIR.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.touch(exist_ok=True)
    CURRENT_FILE.touch(exist_ok=True)

    # DISPATCH GUARDIAN — make sure the persistent daemon is alive before we
    # serve anything. Visible launch. Nothing hidden ever.
    if is_dispatch_running():
        log("DISPATCH already running (PID file present, process alive).")
    else:
        log("DISPATCH down — launching now (visible window).")
        ensure_dispatch_running()
        # Give it a moment to write its PID file
        time.sleep(2)

    # OBSCURA MCP GUARDIAN — the browser hands boot with the system.
    # Detached (LAUNCH LAW). Survives on its own. Never killed.
    if is_mcp_running():
        log("MCP already running on 3366.")
    else:
        log("MCP down — launching detached now.")
        ensure_mcp_running()
        time.sleep(2)

    server = ThreadingHTTPServer(("127.0.0.1", PORT), SimpleJackHandler)

    # Greeting — LAW 1, narrate the boot
    try:
        narrate("simpleJACK online. WE WORK HERE. Portable door is localhost colon 8 7 9 7. Sign in password lives in the config folder.")
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down...")
        server.server_close()

if __name__ == "__main__":
    main()
