"""
dispatch.py — THE STACK RUNNER.
Watches STACK/queue.txt. Pastes the next command into a VISIBLE command
prompt window. Writes results to STACK/done.log or dispatch/failures.log.

NEVER touches the GPU. Burns CPU all day. Trent sees every command run.

THREE THINGS THAT MATTER (and only these):
  1. ATOMIC CUT — queue.txt → rename → read → write remainder back. No race.
  2. VISIBLE WINDOW — each command runs in a REAL cmd.exe (CREATE_NEW_CONSOLE).
     Trent watches it run. Nothing executes hidden. Transparency.
  3. CALL OUT FAILURE — non-zero exit → failures.log with command + error + time.
     No silent failures. No drift disguised as bugs.

The flag file dispatch/.run is read each loop:
  "run"   → process the queue
  "pause" → idle, do nothing
Default is "run" so the stack works out of the box.

AILA talks to dispatch ONLY through files:
  - STACK/queue.txt   (she appends commands)
  - dispatch/.run     (she writes "go"/"pause")
Dispatch never chats. It runs.
"""
import os
import sys
import re
import time
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

# Paths — STACK lives one level up from this file
DISPATCH_DIR = Path(__file__).resolve().parent
SIMPLEJACK_ROOT = DISPATCH_DIR.parent
STACK_DIR = SIMPLEJACK_ROOT / "STACK"
QUEUE        = STACK_DIR / "queue.txt"
PROCESSING   = STACK_DIR / "queue.processing"   # temp name during atomic cut
CURRENT      = STACK_DIR / "current.txt"        # what's running RIGHT NOW
DONE         = STACK_DIR / "done.log"
FAIL         = DISPATCH_DIR / "failures.log"
RUN_FLAG     = DISPATCH_DIR / ".run"            # "run" / "pause"

# Narration queue — THE BUILDERS BARBECUE. Skills narrate completion here.
# Portable (2026-08-01): the queue is next to the launcher — DISPATCH_DIR.parent
# is the bundle root. One queue. Its own. Never looked for anywhere else.
NARRATION_QUEUE = DISPATCH_DIR.parent / "morphytrek_data" / "queue"

# --- REGISTRY HOOK -------------------------------------------------------
# The daemon stays dumb. It executes, observes, writes the registry.
# After each command: identify the folder it touched, refresh FOLDER.md there,
# and if that folder is a venv, append a tool-change line to VENV.md.
SIMPLEJACK_ROOT_FD = DISPATCH_DIR.parent  # for importing frontdesk
if str(SIMPLEJACK_ROOT_FD) not in sys.path:
    sys.path.insert(0, str(SIMPLEJACK_ROOT_FD))

# Guarded import — if frontdesk isn't there, dispatch still works, just no refresh.
_FRONTDESK = None
try:
    import frontdesk as _FRONTDESK  # the canonical library
except Exception as e:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [dispatch] frontdesk import failed (registry hook disabled): {e}")

# Venv folders where tool changes matter and VENV.md must be kept current.
# Portable (2026-08-01): dispatch lives NEXT TO simplejack.py — its root is
# its own parent. Any venv inside the bundle is the only one it tracks.
_DISPATCH_ROOT = Path(__file__).resolve().parent.parent
VENV_FOLDERS = set()
for _v in (_DISPATCH_ROOT / "MorPHYvenv", _DISPATCH_ROOT):
    if _v.exists():
        VENV_FOLDERS.add(_v)
if not VENV_FOLDERS:
    VENV_FOLDERS = {_DISPATCH_ROOT}


def parse_cwd_hint(cmd):
    """AILA may prepend a hint line '# cwd: C:\\path' to the queued command.
    Strips and returns (clean_cmd, cwd_or_None). If no hint, returns (cmd, None)."""
    m = re.match(r"^\s*#\s*cwd:\s*(.+?)\s*[\r\n]+(.+)", cmd, flags=re.IGNORECASE)
    if m:
        return m.group(2).strip(), Path(m.group(1).strip().strip('"').strip("'"))
    return cmd, None


def extract_working_folder(cmd):
    """Best-effort: which folder did this command touch?
    Priority: explicit # cwd: hint > quoted path > longest existing path-like
    prefix > dispatch's own cwd. Returns (Path|None, clean_cmd)."""
    clean, hinted = parse_cwd_hint(cmd)
    if hinted and hinted.exists():
        return hinted, clean
    # Quoted path first (handles "C:\some path with spaces\...")
    m = re.search(r'"([A-Za-z]:\\[^"]+|[A-Za-z]:/[^"]+)"', clean)
    if m:
        p = Path(m.group(1))
        if p.exists():
            return (p.parent if p.is_file() else p), clean
    # Unquoted path: greedily extend while the cumulative path exists on disk.
    # Handles "C:\...\SIMPLEJACK\..." style commands with spaces.
    m = re.search(r'([A-Za-z]:\\[^\s"]*)', clean)
    if m:
        start = m.start(1)
        # Token by token, extend the match while the path exists
        tail = m.group(1)
        rest = clean[m.end(1):]
        # try to extend by consuming following space-prefixed tokens
        best = None
        candidate = tail
        if Path(candidate).exists():
            best = Path(candidate)
        for chunk in re.finditer(r'\s+(\S+)', rest):
            candidate = candidate + ' ' + chunk.group(1)
            if Path(candidate).exists():
                best = Path(candidate)
            else:
                break
        if best is not None:
            return (best.parent if best.is_file() else best), clean
    return None, clean


def registry_hook(cmd, cwd_hint=None):
    """THE HOOK. Called after a command finishes. Dumb observer:
    refresh FOLDER.md in the touched folder, append a change line to VENV.md
    if it's a venv. Never crashes dispatch."""
    if not _FRONTDESK:
        return  # no library, no refresh
    try:
        folder = cwd_hint
        if folder is None:
            folder, _ = extract_working_folder(cmd)
        if folder is None or not folder.exists():
            return
        # 1. Refresh FOLDER.md in the touched folder (in-process, no boot tax)
        data = _FRONTDESK.scan_folder(folder)
        md = _FRONTDESK.render_md(data)
        (folder / "FOLDER.md").write_text(md, encoding="utf-8")
        # 2. If it's a venv, append a pip-vs-VENV.md change line
        if folder in VENV_FOLDERS or any(folder == v or v in folder.parents
                                          for v in VENV_FOLDERS):
            append_venv_diff(folder)
        log(f"HOOK: refreshed FOLDER.md in {folder.name}")
    except Exception as e:
        log(f"HOOK error (non-fatal): {e}")


def append_venv_diff(venv_folder):
    """Diff current pip list against VENV.md, append a change line if anything
    differs. Honest — no fabrication, no silent write."""
    try:
        venv_python = venv_folder / "Scripts" / "python.exe"
        if not venv_python.exists():
            return
        out = subprocess.run(
            [str(venv_python), "-m", "pip", "list", "--format=freeze"],
            capture_output=True, text=True, timeout=60
        ).stdout
        live = {}
        for line in out.splitlines():
            if "==" in line:
                n, _, v = line.partition("==")
                live[n.strip().lower()] = v.strip()
        # Parse what VENV.md currently records (backtick-enclosed name==ver)
        venv_md = venv_folder / "VENV.md"
        recorded = {}
        if venv_md.exists():
            for m in re.finditer(r"`([^`]+==[^`]+)`", venv_md.read_text(encoding="utf-8")):
                spec = m.group(1)
                n, _, v = spec.partition("==")
                recorded[n.strip().lower()] = v.strip()
        added = [n for n in live if n not in recorded]
        removed = [n for n in recorded if n not in live]
        changed = [n for n in live if n in recorded and live[n] != recorded[n]]
        if not (added or removed or changed):
            return  # no change, no line
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        parts = []
        if added:   parts.append("added: " + ", ".join(sorted(added)[:20]))
        if removed: parts.append("removed: " + ", ".join(sorted(removed)[:20]))
        if changed:
            parts.append("changed: " + ", ".join(
                f"{n} {recorded[n]}->{live[n]}" for n in sorted(changed)[:20]))
        line = f"\n_{ts} — " + " | ".join(parts) + "_\n"
        with venv_md.open("a", encoding="utf-8") as f:
            f.write(line)
        log(f"HOOK: appended VENV.md change line to {venv_folder.name}")
    except Exception as e:
        log(f"VENV diff error (non-fatal): {e}")


HEARTBEAT_SEC = 2        # idle wait between queue checks
SETTLE_SEC    = 1        # pause between commands (GPU/CPU settle)
HOLD_WINDOW_SEC = 4      # how long the visible window stays open after a command

# Windows process creation flag: open a NEW console window Trent can see.
# 0x00000010 = CREATE_NEW_CONSOLE. Falls back gracefully on non-Windows.
IS_WINDOWS = sys.platform.startswith("win")
NEW_CONSOLE_FLAG = 0x00000010 if IS_WINDOWS else 0


def now():
    return datetime.now().strftime("%H:%M:%S")


def log(msg, target="dispatch"):
    line = f"[{now()}] [{target}] {msg}"
    print(line, flush=True)


def narrate_completion(text):
    """THE BUILDERS BARBECUE. Every skill is AILA. One mouth, one voice.
    Writes '{text} the builders barbecue is complete' to the narration queue."""
    try:
        NARRATION_QUEUE.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000000)
        final = NARRATION_QUEUE / f"dispatch_{ts}.json"
        tmp = final.with_suffix(".tmp")
        import json
        tmp.write_text(json.dumps(
            {"text": f"{text} the builders barbecue is complete",
             "source": "aila", "engine": "piper"}),
            encoding="utf-8")
        tmp.replace(final)
    except Exception as e:
        log(f"narrate error (non-fatal): {e}")


def read_run_flag():
    """Return 'run' or 'pause'. Default run so the stack works out of the box."""
    if not RUN_FLAG.exists():
        return "run"
    try:
        return RUN_FLAG.read_text(encoding="utf-8").strip().lower() or "run"
    except Exception:
        return "run"


def get_next_command():
    """ATOMIC CUT. rename queue → processing, read first line, write remainder back.
    Returns the command string, or None if queue empty / another worker owns it."""
    if not QUEUE.exists():
        return None
    try:
        QUEUE.rename(PROCESSING)   # atomic on Windows + Linux
    except OSError:
        # another dispatch worker has it, or queue vanished mid-call
        return None
    content = PROCESSING.read_text(encoding="utf-8", errors="ignore")
    lines = [l for l in content.splitlines() if l.strip() and not l.startswith("#")]
    if lines:
        cmd = lines[0]
        rest = ("\n".join(lines[1:]) + "\n") if len(lines) > 1 else ""
        QUEUE.write_text(rest, encoding="utf-8")
    else:
        QUEUE.write_text("", encoding="utf-8")
    PROCESSING.unlink(missing_ok=True)
    return lines[0] if lines else None


# Portable (2026-08-01): the persistent window uses the bundle's own
# command interpreter — a python -c console via the resolved python.
# No Trent paths. Falls back to plain cmd if anything is missing.
VENV_BAT = None

# ───────────────────────────────────────────────────────────────────────
# PERSISTENT LIVE WINDOW — one VENV.BAT cmd window for the whole session.
# Opened once, reused for every command. Same design as morPHYtrek.
# ───────────────────────────────────────────────────────────────────────
_LIVE_WINDOW = {"hwnd": None}
_LIVE_WINDOW_TITLE = "DISPATCH"


def _send_input_paste_and_enter():
    """Send Ctrl+V (paste) then Enter via SendInput. Console windows read
    from the OS input queue — PostMessage can't reach them, SendInput can."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL = 0x11
    VK_V = 0x56
    VK_RETURN = 0x0D

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]
    class INPUT(ctypes.Structure):
        class _INPUT(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
        _anonymous_ = ("_input",)
        _fields_ = [("type", wintypes.DWORD), ("_input", _INPUT)]

    # Build the INPUT structs inside a typed ctypes array so the memory
    # layout matches what SendInput expects. The old code used byref(*list)
    # which unpacks the list into separate byref() args — but byref takes
    # at most 2 args. That broke EVERY skills command with the error:
    # "byref expected at most 2 arguments, got 4". Fixed 2026-07-21.
    def _send_input_seq(keys):
        n = len(keys)
        arr = (INPUT * n)()
        for i, k in enumerate(keys):
            arr[i] = k
        sent = user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT))
        return sent == n

    def _key(vk, up=False):
        i = INPUT()
        i.type = INPUT_KEYBOARD
        i.ki.wVk = vk
        i.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
        return i

    # Ctrl down, V down, V up, Ctrl up = paste
    paste = [_key(VK_CONTROL), _key(VK_V), _key(VK_V, True), _key(VK_CONTROL, True)]
    if not _send_input_seq(paste):
        return False, "paste SendInput failed"
    time.sleep(0.3)
    enter = [_key(VK_RETURN), _key(VK_RETURN, True)]
    if not _send_input_seq(enter):
        return False, "enter SendInput failed"
    return True, "ok"


def _find_live_window():
    """Find the DISPATCH cmd window by its title."""
    import ctypes
    user32 = ctypes.windll.user32
    found = [None]
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n > 0:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if _LIVE_WINDOW_TITLE in buf.value:
                found[0] = hwnd
                return False
        return True
    user32.EnumWindows(EnumWindowsProc(_enum), 0)
    return found[0]


def _open_live_window():
    """Open the one live cmd window. Title is fixed so we can find it.
    Portable (2026-08-01): no VENV.BAT — plain cmd, cwd = bundle root."""
    import ctypes
    user32 = ctypes.windll.user32
    _root = Path(__file__).resolve().parent.parent
    launch_bat = Path(tempfile.gettempdir()) / "sj_live.bat"
    launch_bat.write_text(
        f"@echo off\n"
        f"title {_LIVE_WINDOW_TITLE}\n"
        f"cd /d \"{_root}\"\n"
        f"title {_LIVE_WINDOW_TITLE}\n",
        encoding="utf-8"
    )
    subprocess.Popen(["cmd.exe", "/c", str(launch_bat)],
                     creationflags=NEW_CONSOLE_FLAG)
    deadline = time.time() + 10
    hwnd = None
    while time.time() < deadline:
        time.sleep(0.5)
        hwnd = _find_live_window()
        if hwnd:
            _LIVE_WINDOW["hwnd"] = hwnd
            return hwnd
    return None


def _ensure_live_window():
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = _LIVE_WINDOW.get("hwnd")
    if hwnd and user32.IsWindow(hwnd):
        return hwnd
    return _open_live_window()


def _bring_to_foreground(hwnd):
    """Attach thread input so SetForegroundWindow works reliably."""
    import ctypes
    user32 = ctypes.windll.user32
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    our_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(our_tid, fg_tid, True)
    try:
        user32.SetForegroundWindow(hwnd)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    finally:
        user32.AttachThreadInput(our_tid, fg_tid, False)


def run_command_visible(cmd, cwd=None):
    """Paste the command to the persistent live VENV.BAT cmd window.

    ONE window, opened once, reused. Same design as morPHYtrek. Command's
    real output streams live in that window. Window stays open via cmd /k.
    """
    try:
        hwnd = _ensure_live_window()
        if not hwnd:
            return False, "DISPATCH: live window failed to open"
        # Set clipboard, bring window forward, paste + Enter via SendInput
        full_cmd = f'cd /d "{cwd}"\n{cmd}' if cwd else cmd
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"Set-Clipboard -Value @'\n{full_cmd}\n'@"],
            capture_output=True, timeout=5
        )
        _bring_to_foreground(hwnd)
        time.sleep(0.4)
        ok, msg = _send_input_paste_and_enter()
        if not ok:
            return False, f"DISPATCH paste failed: {msg}"
        time.sleep(1.0)
        return True, f"Pasted to live cursor: {cmd[:80]}"
    except Exception as e:
        return False, f"DISPATCH RUN ERROR: {e}"


def main():
    log("=" * 60)
    log("dispatch — THE STACK RUNNER")
    log(f"STACK:    {STACK_DIR}")
    log(f"Queue:    {QUEUE}")
    log(f"Done:     {DONE}")
    log(f"Failures: {FAIL}")
    log(f"Visible window: {'YES (CREATE_NEW_CONSOLE)' if IS_WINDOWS else 'NO (non-Windows)'}")
    log("=" * 60)

    STACK_DIR.mkdir(parents=True, exist_ok=True)
    QUEUE.touch(exist_ok=True)
    DONE.touch(exist_ok=True)
    FAIL.touch(exist_ok=True)
    # Default run flag so the stack works without AILA having to say go first
    if not RUN_FLAG.exists():
        RUN_FLAG.write_text("run", encoding="utf-8")

    # Write our PID so simplejack's guardian can detect if we go down.
    PID_FILE = DISPATCH_DIR / "dispatch.pid"
    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass

    log("DISPATCH ONLINE. Watching STACK/queue.txt. Visible window. Atomic cut.")

    # Portable: done-marker queue = the bundle's own queue. One queue. Its own.
    DONE_MARKER_DIR = DISPATCH_DIR.parent / "morphytrek_data" / "queue"

    while True:
        # Respect the pause flag
        if read_run_flag() == "pause":
            time.sleep(HEARTBEAT_SEC)
            continue

        cmd = get_next_command()
        if not cmd:
            time.sleep(HEARTBEAT_SEC)
            continue

        # Pull the cwd hint (if AILA provided one) and pass it to the runner.
        clean_cmd, cwd_hint = parse_cwd_hint(cmd)

        # SEQUENTIAL EXECUTION via skill-done-signal:
        # If this is a SKILLS command, append --done-token <token>. The skill
        # writes done_<token>.json when its output is shipped. dispatch waits
        # for that file before pulling the next command from the queue.
        # Sequential. One finishes before the next starts.
        run_cmd = clean_cmd
        done_token = None
        if "SKILLS" in clean_cmd and "python.exe" in clean_cmd:
            done_token = f"{int(time.time()*1000)}_{os.getpid()}"
            run_cmd = f'{clean_cmd} --done-token {done_token}'
            # Clean any stale marker from a previous run with this token
            marker = DONE_MARKER_DIR / f"done_{done_token}.json"
            try: marker.unlink(missing_ok=True)
            except Exception: pass

        # Mark what's running RIGHT NOW (AILA reads this via current.txt)
        CURRENT.write_text(clean_cmd, encoding="utf-8")
        log(f"RUNNING: {clean_cmd[:120]}")

        success, output = run_command_visible(run_cmd, cwd=cwd_hint)

        # Wait for the skill's done-marker (sequential gate).
        # For non-SKILLS commands (no token), skip the wait — they're instant.
        if done_token:
            marker = DONE_MARKER_DIR / f"done_{done_token}.json"
            deadline = time.time() + 86400  # 24h cap (long-running indexes)
            log(f"WAIT: polling for done marker {marker.name}")
            while time.time() < deadline:
                if marker.exists():
                    log(f"WAIT: done marker received")
                    break
                if _STOP.get("stop"):
                    break
                time.sleep(2)
            # Cleanup the marker
            try: marker.unlink(missing_ok=True)
            except Exception: pass

        # Clear the current marker
        CURRENT.write_text("(idle)", encoding="utf-8")

        # === REGISTRY HOOK === Dumb observer. After the command finishes,
        # refresh FOLDER.md in the folder it touched, and diff VENV.md if venv.
        registry_hook(clean_cmd, cwd_hint=cwd_hint)

        ts_full = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if success:
            entry = (
                f"[{ts_full}] OK | {cmd[:200]}\n"
                f"{output[:2000]}\n"
                f"---\n"
            )
            with DONE.open("a", encoding="utf-8") as f:
                f.write(entry)
            log(f"DONE: {cmd[:80]}")
            narrate_completion(f"Command complete. {cmd[:80]}")
        else:
            entry = (
                f"[{ts_full}] FAIL | CMD: {cmd[:200]}\n"
                f"OUTPUT:\n{output[:2000]}\n"
                f"---\n"
            )
            with FAIL.open("a", encoding="utf-8") as f:
                f.write(entry)
            log(f"FAILED: {cmd[:80]}")
            # Failed commands are NOT retried. Trent or AILA reads failures.log.
            # Surgery happens. Then it gets queued again manually.

        time.sleep(SETTLE_SEC)


if __name__ == "__main__":
    main()
