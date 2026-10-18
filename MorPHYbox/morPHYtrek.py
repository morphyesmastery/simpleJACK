"""
morPHYtrek — One program. Sequential. Listen → process → narrate → repeat.

THE BEST HUMAN COMPUTER INTERFACE ON THE MARKET.

What it does:
  1. Listens to the microphone (Whisper transcription)
  2. Types what it hears at the cursor
  3. When the Conductor queue has narration jobs, it narrates them (Piper)
  4. During narration, microphone pauses (ear gate / sequential GPU)
  5. Creates raw transcript of everything heard
  6. Small on-screen presence: logo indicator (listening/muted/narrating)
  7. Spacebar = mute/unmute microphone
  8. Refresh button on screen
  9. Highlighted text auto-copied to transcript (e-reader feature, toggleable)

ONE PROCESS. ONE GPU. SEQUENTIAL. NO FIGHTING.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import sys
import time
import json
import re
import threading
import subprocess
import numpy as np
from pathlib import Path
from datetime import datetime

# ════════════════════════════════════════════════════════════
#  ROOTS & PATHS
# ════════════════════════════════════════════════════════════
# Portable root: next to the script, or user home if frozen (EXE)
if getattr(sys, 'frozen', False):
    _APP_ROOT = Path(sys.executable).parent
else:
    _APP_ROOT = Path(__file__).resolve().parent

DATA_ROOT = _APP_ROOT / "morphytrek_data"
ENGINE_DIR = DATA_ROOT / "engine"
QUEUE_DIR  = DATA_ROOT / "queue"
CONFIG_DIR = DATA_ROOT / "config"

# Self-healing: ensure queue and config dirs always exist
QUEUE_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

LIB             = DATA_ROOT / "library"
TRANSCRIPT_DIR  = DATA_ROOT / "transcripts"
LOG_DIR         = DATA_ROOT / "logs"

TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# THE ONE PYTHON — portable clone rule (2026-08-24): the interpreter that
# launched THIS program is the one python that runs everything here.
# On a customer machine that is the bundled runtime\python.exe (embedded
# Python 3.12 + tkinter); sys.executable always exists, so there is no
# hardcoded machine path and nothing to crash on at import.
MORPHYVENV_PYTHON = Path(sys.executable)
# Portable rule (2026-08-01): morPHYtrek makes ONLY its own queue next to
# itself (QUEUE_DIR above). One queue. Its own. Never looks elsewhere.
# One queue. Its own. Zero excuse to ever look elsewhere.
PIPER_DIRS = [
    DATA_ROOT / "voices",
    _APP_ROOT / "voices",
    QUEUE_DIR.parent / "piper_models",
]
PIPER_VOICE_FILE = CONFIG_DIR / "piper_voice.txt"
PIPER_EXE = _APP_ROOT / "piper" / "piper.exe"

# ── VOICE REGISTER — ONE TABLE, WHO IS TALKING (Trent's design) ──────────────
# Every narration file carries a REGISTER: its "source". The mouth looks the
# source up HERE and reads that file in that app's own voice, so the operator
# knows BY EAR which program is speaking. Add one line to give a new app a
# voice — nothing else in this file has to change. A "voice" field written
# inside the narration file always wins over this table.
VOICE_REGISTER = {
    "hermes":    "en_GB-northern_english_male-medium",  # AILA — this machine's agent
    # 2026-09-21: the "aila" alias to the northern male voice is REMOVED.
    # LAW 1 (SOUL.md / AGENTS.md): ONLY source "hermes" reads in the northern
    # male register — "aila", no source, or no voice reads in the default Alba.
    # simplejack.py writes every one of its own utterances as source "aila"
    # (portable/simplejack.py line ~426), so aliasing aila to hermes made the
    # SimpleJack app itself read in the English male voice. Removed. AILA's own
    # replies carry source "hermes", so AILA keeps her voice; SimpleJack gets
    # its own back.
    "racme":     "en_US-ryan-high",                     # RACMe — the trades tutor
    "elias":     "en_GB-jenny_dioco-medium",            # ELIAS — the lessons tutor
    "zcode":     "en_US-amy-medium",                    # ZCode — the coding face
    "morphtrek": "en_GB-alba-medium",                   # morPHYtrek itself
}
DEFAULT_VOICE = "en_GB-alba-medium"


def voice_for_register(origin, filename=""):
    """REGISTER ROUTING (Trent). Map a narration file's source to its voice.

    source field wins; if the file has no source, the filename's first token is
    used (a file named hermes_*.json is AILA's). No match -> "" and the caller
    falls through to the default voice. One table decides. Nothing else.
    """
    key = str(origin or "").strip().lower()
    if not key and filename:
        key = str(filename).split("_")[0].strip().lower()
    if not key:
        return ""
    for name, voice in VOICE_REGISTER.items():
        if key.startswith(name):
            return voice
    return ""

HEARTBEAT_FILE = CONFIG_DIR / "morPHYtrek_heartbeat.json"
STATUS_FILE    = LIB / "morPHYtrek_status.json"

# ════════════════════════════════════════════════════════════
#  DEPENDENCIES
# ════════════════════════════════════════════════════════════
def _pip(*pkgs):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", *pkgs])

for mod, pkgs in [
    ("sounddevice", ["sounddevice"]),
    ("faster_whisper", ["faster-whisper"]),
    ("pyperclip", ["pyperclip"]),
    ("pyautogui", ["pyautogui"]),
    ("pynput", ["pynput"]),
]:
    try:
        __import__(mod)
    except ImportError:
        _pip(*pkgs)

import sounddevice as sd
from faster_whisper import WhisperModel
import pyperclip
import pyautogui
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.02
from pynput import keyboard as kb

# Fix: tell Windows where to find tk/tcl DLLs for portable runtime.
# Only set these for the BUNDLED runtime (python.exe beside this script).
# MorPHYvenv has its own tcl setup — don't override it.
_rt_dir = Path(sys.executable).parent
if (_rt_dir / "tcl" / "tcl8.6").exists():
    os.add_dll_directory(str(_rt_dir))
    os.environ["TCL_LIBRARY"] = str(_rt_dir / "tcl" / "tcl8.6")
    os.environ["TK_LIBRARY"] = str(_rt_dir / "tcl" / "tk8.6")

try:
    import tkinter as tk
    from tkinter import ttk
    TK_OK = True
except ImportError:
    TK_OK = False

try:
    import win32gui
    WIN32 = True
except ImportError:
    WIN32 = False

# ════════════════════════════════════════════════════════════
#  STATE
# ════════════════════════════════════════════════════════════
WHISPER_MODEL     = "large-v3-turbo"
SAMPLE_RATE       = 16000
SILENCE_THRESHOLD = 0.01
MIN_SPEECH_SEC    = 0.8
SILENCE_PAUSE_SEC = 2.0
FLUSH_SEC         = 5.0

state = {
    "muted": False,
    "narrating": False,      # True while Piper is speaking
    "listening": True,       # True when ear is open
    "alive": True,
    "ereader_on": True,     # ALWAYS ON with morPHYtrek (operator order 2026-10-16). Abort button handles 'too much'.
    # RSVP subtitle state
    "sub_words": [],         # list of words in current sentence
    "sub_idx": 0,            # current word index for RSVP display
    "sub_active": False,     # True while subtitle is cycling
    "sub_play_start": 0.0,   # monotonic clock when audio actually starts playing
    "sub_audio_dur": 0.0,    # duration of the current audio in seconds
    "wpm": 200,              # display + voice speed — DEFAULT 200 (spec)
    "wpm_natural": 210,      # Alba natural rate measured 2026-08-02 @ length-scale 1.0
    # REFRESH = ABORT (operator order 2026-10-15): the refresh button now
    # aborts the narration currently being read and deletes its queue file.
    "current_narration_file": None,  # path of the job being spoken right now
    "abort_narration": False,        # set True by refresh() to stop mid-read
    "active_stream": None,           # live sounddevice OutputStream (for abort)
    "active_proc": None,             # live Piper process (for abort)
}

audio_buffer   = []
session_text   = []
whisper_model  = None
last_speech_ts = time.monotonic()
lock           = threading.Lock()

# Last non-morPHYtrek foreground window (for paste focus restoration)
_last_target_window = None
MORPHYTREK_HWND = None  # set after GUI creation

# ════════════════════════════════════════════════════════════
#  LOGGING
# ════════════════════════════════════════════════════════════
LOG_FILE = LOG_DIR / "morPHYtrek.log"

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except: pass

# ════════════════════════════════════════════════════════════
#  TRANSCRIPT
# ════════════════════════════════════════════════════════════
def today_transcript():
    return TRANSCRIPT_DIR / f"{datetime.now().strftime('%Y-%m-%d')}_raw.json"

def flush_to_transcript():
    global session_text
    with lock:
        chunks = list(session_text)
        session_text = []
    if not chunks: return
    text = " ".join(chunks).strip()
    if not text: return
    path = today_transcript()
    entries = []
    if path.exists():
        try: entries = json.loads(path.read_text(encoding="utf-8"))
        except: pass
    entries.append({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "text": text})
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Transcript flushed ({len(text)} chars)")

# ════════════════════════════════════════════════════════════
#  WHISPER (THE EAR)
# ════════════════════════════════════════════════════════════
def _gpu_present():
    """Detect an NVIDIA GPU WITHOUT torch. Returns True if found."""
    # nvcuda.dll ships with every NVIDIA driver — the surest sign a CUDA GPU exists.
    try:
        import ctypes
        return bool(ctypes.windll.LoadLibrary("nvcuda.dll"))
    except Exception:
        pass
    # fallback: nvidia-smi on PATH
    try:
        r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False

def init_whisper():
    global whisper_model
    log(f"Loading Whisper {WHISPER_MODEL}...")
    try:
        import torch
        if torch.cuda.is_available():
            whisper_model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
            log(f"Whisper {WHISPER_MODEL} ready (GPU)")
            return
    except Exception as e:
        log(f"GPU load failed ({e}), trying CPU")

    # torch missing AND a GPU exists → ask before falling back to CPU
    if _gpu_present():
        log("NVIDIA GPU detected but torch is not installed.")
        try:
            ans = input("GPU detected. Download and install torch for GPU transcription? (y/N): ").strip().lower()
        except Exception:
            ans = "n"
        if ans in ("y", "yes"):
            log("Installing torch (CUDA build)... this downloads ~2.5 GB once.")
            try:
                py = str(Path(sys.executable))
                subprocess.run([py, "-m", "pip", "install", "--upgrade", "pip"],
                               capture_output=True, timeout=300)
                r = subprocess.run([py, "-m", "pip", "install", "torch",
                                    "--index-url", "https://download.pytorch.org/whl/cu121"],
                                   capture_output=True, timeout=3600)
                if r.returncode != 0:
                    log(f"torch install failed: {r.stderr.decode(errors='ignore')[-300:]}")
                else:
                    log("torch installed. Retrying GPU load...")
                    import torch
                    if torch.cuda.is_available():
                        whisper_model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
                        log(f"Whisper {WHISPER_MODEL} ready (GPU)")
                        return
            except Exception as e:
                log(f"torch install error: {e}")
        else:
            log("torch skipped by user — CPU mode.")

    whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    log(f"Whisper {WHISPER_MODEL} ready (CPU fallback)")

def transcribe(audio_np):
    if not whisper_model: return ""
    try:
        segs, _ = whisper_model.transcribe(audio_np, language="en", vad_filter=True)
        return " ".join(s.text.strip() for s in segs).strip()
    except Exception as e:
        log(f"Transcribe error: {e}")
        return ""

def track_focus():
    """Track the last non-morPHYtrek foreground window so paste() can target it."""
    global _last_target_window
    while state["alive"]:
        try:
            if WIN32:
                fg = win32gui.GetForegroundWindow()
                if fg == 0 or fg == MORPHYTREK_HWND:
                    time.sleep(0.3)
                    continue
                title = win32gui.GetWindowText(fg)
                if title and title != "morPHYtrek":
                    _last_target_window = fg
        except:
            pass
        time.sleep(0.3)

def paste(text):
    try:
        # Restore focus to the target window before pasting
        global _last_target_window
        if WIN32 and _last_target_window:
            try:
                fg = win32gui.GetForegroundWindow()
                fg_title = win32gui.GetWindowText(fg)
                if fg_title == "morPHYtrek" or fg == 0:
                    win32gui.ShowWindow(_last_target_window, 9)  # SW_RESTORE
                    win32gui.SetForegroundWindow(_last_target_window)
                    time.sleep(0.05)
            except Exception as e:
                log(f"Focus restore: {e}")
        saved = pyperclip.paste()          # save what's on the clipboard now
        pyperclip.copy(text + " ")
        time.sleep(0.05)
        # VERIFY the words actually landed on the clipboard before sending Ctrl+V.
        # If the copy lost the race, retry up to 3 times — never fire Ctrl+V blind.
        for _ in range(3):
            if pyperclip.paste() == text + " ":
                break
            time.sleep(0.05)
            pyperclip.copy(text + " ")
        pyautogui.hotkey("ctrl", "v")
        # Give the target app time to process the paste BEFORE restoring the old
        # clipboard — slow apps (Electron/Hermes, Word) read the restored old
        # content otherwise. 500ms covers even slow IPC hops.
        time.sleep(0.5)
        pyperclip.copy(saved)              # restore so e-reader doesn't lose it
    except Exception as e:
        log(f"Paste error: {e}")

# ════════════════════════════════════════════════════════════
#  PIPER (THE MOUTH)
# ════════════════════════════════════════════════════════════
def piper_catalog():
    cat = {}
    for d in PIPER_DIRS:
        try:
            for p in sorted(d.glob("*.onnx")):
                cat.setdefault(p.stem, p)
        except: continue
    return cat

def get_piper_model(voice=None):
    """Pick a Piper model. Explicit voice wins; fallback is the global choice, then Alba."""
    cat = piper_catalog()
    if not cat: return None
    # 1. Per-job voice (from the narration file's "voice" field)
    if voice:
        v = str(voice).strip()
        if v in cat:
            return cat[v]
        v_path = Path(v)
        if v_path.exists() and v_path.suffix == ".onnx":
            return v_path
        v_stem = v_path.stem if v.endswith(".onnx") else v
        if v_stem in cat:
            return cat[v_stem]
        log(f"Voice '{v}' not in catalog — falling back")
    try:
        chosen = PIPER_VOICE_FILE.read_text(encoding="utf-8").strip()
        # accept stem OR full path that ends with known stem
        if chosen in cat:
            return cat[chosen]
        ch_path = Path(chosen)
        if ch_path.exists() and ch_path.suffix == ".onnx":
            return ch_path
        stem = ch_path.stem if chosen.endswith(".onnx") else chosen
        if stem in cat:
            return cat[stem]
    except Exception:
        pass
    # Prefer Alba
    for prefer in ("en_GB-alba-medium",):
        if prefer in cat:
            return cat[prefer]
    return next(iter(cat.values()))

def _piper_clean_env():
    env = os.environ.copy()
    for k in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(k, None)
    # Portable: use the discovered interpreter's Scripts dir when available.
    if MORPHYVENV_PYTHON != "python":
        vibe_scripts = str(Path(MORPHYVENV_PYTHON).parent)
        env["PATH"] = vibe_scripts + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(Path(MORPHYVENV_PYTHON).parent.parent)
    return env

def piper_generate(text, voice=None):
    """Generate audio via Piper. Returns (np_array, sample_rate) or None."""
    model = get_piper_model(voice)
    if not model:
        log("No Piper model found")
        return None
    env = _piper_clean_env()
    # WPM slider drives the VOICE too: length_scale = natural_rate / target_rate
    # Alba natural = 210 WPM at length-scale 1.0 (measured 2026-08-02).
    # 200 WPM → 210/200 = 1.05 (slightly slower). 210 → 1.0 (natural). 700 → 0.3 (fast).
    target_wpm = int(state.get("wpm", 200))
    target_wpm = max(100, min(1000, target_wpm))
    natural_wpm = float(state.get("wpm_natural", 210))
    length_scale = natural_wpm / target_wpm
    try:
        # Prefer vibe piper.exe (avoids python -m path pollution)
        if PIPER_EXE.exists():
            cmd = [str(PIPER_EXE), "--model", str(model), "--output-raw",
                   "--length-scale", f"{length_scale:.3f}"]
        else:
            python = str(MORPHYVENV_PYTHON)
            cmd = [python, "-m", "piper", "--model", str(model), "--output-raw",
                   "--length-scale", f"{length_scale:.3f}"]
        result = subprocess.run(
            cmd,
            input=text.encode("utf-8"),
            capture_output=True, timeout=60,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # 2026-09-20: piper wakes were painting console windows on every speak (operator report: 4-5 popups per Elias query). Hidden forever.
        )
        if result.returncode != 0 or not result.stdout:
            err = result.stderr.decode(errors='ignore')[:300] if result.stderr else 'no stderr (possible timeout or empty input)'
            log(f"Piper failed: code={result.returncode} stderr={err}")
            return None
        data = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        return (data, 22050)
    except Exception as e:
        log(f"Piper error: {e}")
        return None

def play_audio(audio_data, sr):
    """Play audio through speakers. BLOCKING. Sequential by construction.
    Records the REAL audio clock so the RSVP cell shows each word
    exactly when the voice says it — not when generation finished."""
    state["narrating"] = True
    state["listening"] = False
    # Audio clock: monotonic start + duration from the actual samples.
    state["sub_play_start"] = time.monotonic()
    state["sub_audio_dur"] = (len(audio_data) / float(sr)) if sr else 0.0
    update_gui()
    try:
        sd.play(audio_data, sr, device=None)
        sd.wait()
    except Exception as e:
        log(f"Playback error: {e}")
    finally:
        state["narrating"] = False
        state["listening"] = not state["muted"]
        update_gui()

# ── STREAMING VOICE (2026-09-21) ─────────────────────────────
# Operator report: "MASSIVE VOICE BREAKS BETWEEN SENTENCES, IT WAS ALL
# STREAMING AND EFFECTIVE BEFORE."  Cause: one Piper process PER SENTENCE —
# every sentence reloaded the 63 MB voice model, so audio stalled ~1-3 s
# between every sentence. Fix: ONE Piper process per job, raw PCM streamed
# straight to the speakers as it is synthesised. First word out in a few
# hundred milliseconds, zero gaps, subtitle clock starts on the first frame.

def _voice_sample_rate(model):
    """Read the voice's true sample rate from its sidecar config."""
    try:
        cfg = Path(str(model) + ".json")
        if cfg.exists():
            j = json.loads(cfg.read_text(encoding="utf-8"))
            sr = int((j.get("audio") or {}).get("sample_rate") or 0)
            if sr:
                return sr
    except Exception:
        pass
    return 22050

def piper_open(text, voice=None):
    """Spawn ONE Piper process for the whole text. Returns (proc, sample_rate)."""
    model = get_piper_model(voice)
    if not model:
        log("No Piper model found")
        return (None, 0)
    env = _piper_clean_env()
    target_wpm = int(state.get("wpm", 200))
    target_wpm = max(100, min(1000, target_wpm))
    natural_wpm = float(state.get("wpm_natural", 210))
    length_scale = natural_wpm / target_wpm
    try:
        if PIPER_EXE.exists():
            cmd = [str(PIPER_EXE), "--model", str(model), "--output-raw",
                   "--length-scale", f"{length_scale:.3f}"]
        else:
            cmd = [str(MORPHYVENV_PYTHON), "-m", "piper", "--model", str(model),
                   "--output-raw", "--length-scale", f"{length_scale:.3f}"]
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            proc.stdin.write(text.encode("utf-8"))
            proc.stdin.close()
        except Exception:
            pass
        return (proc, _voice_sample_rate(model))
    except Exception as e:
        log(f"Piper error: {e}")
        return (None, 0)

def speak_stream(text, voice=None):
    """Speak the whole text through ONE Piper process, streamed to the speakers.
    Returns True if any audio played."""
    proc, sr = piper_open(text, voice)
    if not proc:
        return False
    played = False
    frames = 0
    stream = None
    state["active_proc"] = proc
    state["narrating"] = True
    state["listening"] = False
    try:
        words = text.strip().upper().split()
        state["sub_words"] = words
        state["sub_idx"] = 0
        state["sub_active"] = True
        state["sub_play_start"] = 0.0
        state["sub_audio_dur"] = 0.0
        update_gui()
        # SUBTITLE SYNC (repair 2026-09-29): the old code timed the subtitles
        # from a WPM estimate while the real audio ran at its own speed — the
        # words ran ahead of the voice. Fix: buffer the full PCM first, then
        # time the subtitles from the REAL sample count — the same clock
        # play_audio uses. Words land exactly when the voice says them.
        chunks = []
        while True:
            chunk = proc.stdout.read(8192)
            if not chunk:
                break
            if len(chunk) % 2:
                chunk = chunk[:-1]
            if not chunk:
                continue
            chunks.append(chunk)
        if not chunks:
            return False
        audio = np.frombuffer(b"".join(chunks), dtype=np.int16).astype(np.float32) / 32768.0
        state["sub_play_start"] = time.monotonic()
        state["sub_audio_dur"] = (len(audio) / float(sr)) if sr else 0.0
        update_gui()
        stream = sd.OutputStream(samplerate=sr, channels=1, dtype="float32")
        stream.start()
        state["active_stream"] = stream
        # ABORT-AWARE PLAYBACK (operator order 2026-10-15): write in ~0.25 s
        # blocks and check the abort flag between them, so the refresh button
        # stops the voice mid-sentence instead of waiting for the whole job.
        block = max(1, int(sr * 0.25))
        for i in range(0, len(audio), block):
            if state.get("abort_narration"):
                log("Narration ABORTED by refresh")
                break
            stream.write(audio[i:i + block])
        played = True
        time.sleep(0.25)  # let the hardware buffer drain before closing
        return played
    except Exception as e:
        log(f"Stream playback error: {e}")
        return played
    finally:
        state["active_stream"] = None
        state["active_proc"] = None
        try:
            if stream is not None:
                stream.stop()
                stream.close()
        except Exception:
            pass
        try:
            proc.stdout.close()
            proc.wait(timeout=5)
        except Exception:
            pass
        state["narrating"] = False
        state["listening"] = not state["muted"]
        update_gui()

def _speak_chunks(sentences, limit=900):
    """Group sentences into jobs that hold ONE Piper model load each."""
    out, buf = [], ""
    for s in sentences:
        if buf and len(buf) + len(s) + 1 > limit:
            out.append(buf)
            buf = s
        else:
            buf = (buf + " " + s).strip()
    if buf:
        out.append(buf)
    return out

# ════════════════════════════════════════════════════════════
#  NARRATION QUEUE PROCESSOR
# ════════════════════════════════════════════════════════════
LABELS = {
    "aila": "", "claude": "", "grok": "",
    "private": "", "mirror": "",
}

def filter_text(text):
    if not text: return ""
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'```[^`]*```', '', text)
    text = re.sub(r'<NARRATION>.*?</NARRATION>', '', text, flags=re.DOTALL)
    text = re.sub(r'[#`*_~^]', '', text)
    text = re.sub(r'\n+', '. ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    # ── SIMPLEJACK SANITIZER (operator order 2026-10-16): the ears contract,
    # from simplejack.py.bak_20260721_130505. Applied to EVERY queue job so
    # ereader reads sound exactly like every other narration.
    # Windows/Unix paths → "the file"
    text = re.sub(r'[A-Za-z]:\\[^\s"\'<>|]*', 'the file', text)
    text = re.sub(r'(?:/[\w.-]+){2,}/?', 'the file', text)
    # code blocks / inline code leftovers
    text = re.sub(r'\{[^{}]*\}', ' ', text)
    text = re.sub(r'[\[\]{}()<>|]', ' ', text)
    # arrows → "then"
    text = re.sub(r'(?:->|=>|\u2192|\u21d2)', ' then ', text)
    # dashes used as separators → "then"
    text = re.sub(r'\s[-\u2013\u2014]\s', ' then ', text)
    # "hashtag" spoken as "pound" is wrong; strip the word entirely
    text = re.sub(r'\bhashtag\b', '', text, flags=re.IGNORECASE)
    # collapse whitespace and "then then then" chains
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'(?:\bthen\b\s*){2,}', 'then ', text, flags=re.IGNORECASE)
    return text.strip()

def split_sentences(text):
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in parts if s.strip()]

def read_queue_job(path):
    """Read a narration job from the queue. Returns (text, voice, origin, engine) or None.
    REGISTER ROUTING (Trent, 2026-09-14, FINAL): the mouth determines where the
    file originated from the REGISTER written inside the file. If a voice is
    specified in the register (e.g. "en_GB-northern_english_male-medium"), it
    reads in that voice. Otherwise it reads in the default voice (Alba)."""
    try:
        data = {}
        if path.suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                data = {}
        text = str(data.get("text", "")) if data else path.read_text(encoding="utf-8").strip()
        engine = str(data.get("engine", "piper")) if data else "piper"
        origin = str(data.get("source", "")).strip() if data else ""
        voice = str(data.get("voice", "")).strip() if data else ""
        # REGISTER ROUTING (Trent): the register INSIDE the file says who is
        # talking; VOICE_REGISTER says what that app sounds like. An explicit
        # "voice" field still wins. No register match -> default voice (Alba).
        if not voice:
            voice = voice_for_register(origin, path.name)
        return (text, voice, origin, engine)
    except Exception as e:
        log(f"Queue read error for {path.name}: {e}")
        return None

def _queue_dirs():
    """Local self-heal queue — the ONLY queue. One queue. Its own."""
    dirs = []
    for d in (QUEUE_DIR,):
        try:
            d.mkdir(parents=True, exist_ok=True)
            # de-dupe if junction/same path
            rp = str(d.resolve())
            if rp not in {str(x.resolve()) for x in dirs}:
                dirs.append(d)
        except Exception:
            pass
    return dirs

def narration_loop():
    """Watch queue folders. When a job appears, narrate it sequentially."""
    log(f"Narration loop started queues={[str(d) for d in _queue_dirs()]}")
    while state["alive"]:
        try:
            jobs = []
            for d in _queue_dirs():
                jobs.extend(sorted(d.glob("*.json")))
                jobs.extend(sorted(d.glob("*.txt")))
            # oldest first
            try:
                jobs.sort(key=lambda p: p.stat().st_mtime)
            except Exception:
                pass
            for path in jobs:
                if not state["alive"]: break
                # skip junk names drop/frontdesk noise patterns
                if path.name.lower() in ("folder.md",):
                    continue
                result = read_queue_job(path)
                if not result:
                    try:
                        path.unlink()
                    except Exception:
                        pass
                    continue
                text, voice, source, engine = result
                text = filter_text(text)
                if not text:
                    try:
                        path.unlink()
                    except Exception:
                        pass
                    continue
                # REFRESH = ABORT (operator order 2026-10-15): the file being
                # read is tracked here so the refresh button can abort the voice
                # and delete it. The file is removed in the finally below, so a
                # crash can never leave a job looping forever.
                state["current_narration_file"] = str(path)
                state["abort_narration"] = False
                try:
                    # REGISTER ROUTING (Trent): voice was decided in read_queue_job
                    # from the register INSIDE the file (source/voice fields).
                    # hermes origin -> male. Specified voice wins. Else Alba.
                    sentences = split_sentences(text)
                    log(f"Narrating [{source}]: {len(sentences)} sentences")
                    # 2026-09-21: ONE Piper process per chunk, streamed to the
                    # speakers — no per-sentence model reloads, no gaps. Falls back
                    # to the old per-sentence path only if streaming yields nothing.
                    for chunk in _speak_chunks(sentences):
                        if not state["alive"]: break
                        if state.get("abort_narration"): break
                        if not speak_stream(chunk, voice):
                            for s in split_sentences(chunk):
                                if not state["alive"]: break
                                if state.get("abort_narration"): break
                                audio = piper_generate(s, voice)
                                if audio:
                                    play_audio(audio[0], audio[1])
                        state["sub_active"] = False
                        state["sub_words"] = []
                        state["sub_play_start"] = 0.0
                finally:
                    state["current_narration_file"] = None
                    state["abort_narration"] = False
                    try:
                        path.unlink()
                    except Exception:
                        pass
            time.sleep(0.4)
        except Exception as e:
            log(f"Narration loop error: {e}")
            time.sleep(2)

# ════════════════════════════════════════════════════════════
#  MICROPHONE LISTENING (VAD)
# ════════════════════════════════════════════════════════════
def process_chunk(chunk):
    global last_speech_ts
    audio_np = np.concatenate(chunk, axis=0).flatten()
    if len(audio_np) / SAMPLE_RATE < MIN_SPEECH_SEC: return
    
    # Ear gate — don't transcribe while narrating
    if state["narrating"] or state["muted"]:
        return
    
    text = transcribe(audio_np)
    if not text: return
    
    # Echo guard — don't transcribe what Piper just said
    try:
        ns_file = LIB / "now_speaking.json"
        if ns_file.exists():
            spoken = json.loads(ns_file.read_text(encoding="utf-8-sig"))
            recent_words = set()
            now_t = time.time()
            for e in spoken:
                if now_t - e.get("ts", 0) < 180:
                    recent_words.update(w.lower().strip(".,!?\"'") for w in e.get("text", "").split())
            heard = [w.lower().strip(".,!?\"'") for w in text.split()]
            if len(heard) >= 4 and recent_words:
                overlap = sum(1 for w in heard if w in recent_words) / len(heard)
                if overlap > 0.7:
                    log(f"ECHO DROPPED ({overlap:.0%}): {text[:60]}")
                    return
    except: pass
    
    log(f"Heard: {text[:80]}")
    paste(text)
    with lock:
        session_text.append(text)
    last_speech_ts = time.monotonic()

def vad_loop():
    """Voice Activity Detection — listens for speech, transcribes it."""
    silence_start = None
    speaking = False
    
    def callback(indata, frames, time_info, status):
        nonlocal silence_start, speaking
        if state["muted"] or state["narrating"]:
            return
        rms = float(np.sqrt(np.mean(indata ** 2)))
        if rms > SILENCE_THRESHOLD:
            speaking = True
            silence_start = None
            with lock:
                audio_buffer.append(indata.copy())
        else:
            if speaking:
                if silence_start is None:
                    silence_start = time.monotonic()
                elif time.monotonic() - silence_start >= SILENCE_PAUSE_SEC:
                    with lock:
                        chunk = list(audio_buffer)
                        audio_buffer.clear()
                    speaking = False
                    silence_start = None
                    if chunk:
                        threading.Thread(target=process_chunk, args=(chunk,), daemon=True).start()
    
    log("Microphone listening")
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        callback=callback, blocksize=int(SAMPLE_RATE * 0.1)):
        while state["alive"]:
            time.sleep(0.1)

# ════════════════════════════════════════════════════════════
#  FLUSH WATCHDOG
# ════════════════════════════════════════════════════════════
def flush_watchdog():
    while state["alive"]:
        time.sleep(1)
        with lock:
            has_text = bool(session_text)
        if has_text:
            idle = time.monotonic() - last_speech_ts
            if idle >= FLUSH_SEC:
                flush_to_transcript()

# ════════════════════════════════════════════════════════════
#  E-READER: Highlighted text → narration
# ════════════════════════════════════════════════════════════
def _mouse_left_down():
    """TRUE QUERY (operator order 2026-10-16): does the user currently hold
    the left mouse button? Windows GetAsyncKeyState — presses NOTHING.
    pyautogui.mouseDown() PRESSES the button; that bug glued the widget to
    the cursor. This replaces it."""
    try:
        import ctypes
        VK_LBUTTON = 0x01
        return bool(ctypes.windll.user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
    except Exception:
        return False

def ereader_loop():
    """When text is highlighted, copy it to transcript and narrate.
    Version 3 (operator order 2026-10-16):
    - ALWAYS ON when morPHYtrek is on. No toggle. The abort button handles
      'too much'.
    - Wait for the mouse button to be RELEASED, then 1 full second of
      mouse-idle, THEN read. No more partial reads mid-drag.
    - No length cap. The whole selection reads.
    - Audio is regular morPHYtrek JSON per chunk — the mouth already chunks
      at 900 chars and applies the SimpleJack filter to every job."""
    last_clip = ""
    prev_down = False
    while state["alive"]:
        if not state.get("ereader_on", True):
            time.sleep(5)
            continue
        time.sleep(0.1)
        try:
            # ONLY act on a real drag: mouse was down last tick and is up now.
            # This is what preserves normal copy/cut/paste — with no drag there
            # is NO Ctrl+C fired at all, so the user's clipboard is never touched.
            down = _mouse_left_down()
            dragged = prev_down and not down
            prev_down = down
            if not dragged:
                continue
            # Drag finished. Wait 2 full seconds of mouse-idle before firing
            # Ctrl+C (operator order 2026-10-17: 2s, was 1s). A new drag in the
            # window cancels the read.
            stable = False
            for _ in range(8):   # 8 x 0.25s = 2 full seconds of mouse-idle
                time.sleep(0.25)
                if _mouse_left_down():
                    stable = False
                    break
                stable = True
            if not stable:
                continue
            current = pyperclip.paste()
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.15)
            selected = pyperclip.paste()
            # Restore the user's clipboard ONLY if we are still the last writer.
            # If the user copied/cut something in our 0.15s window, their content
            # wins — we never clobber a real copy.
            if selected != current and pyperclip.paste() == selected:
                pyperclip.copy(current)
            if selected and selected != current and selected != last_clip and len(selected) > 10:
                # LOOP GUARD (operator order 2026-10-16): if the foreground
                # window is the ReadPlayer drawer (Edge app window titled
                # "MorPHYes Read Player"), do NOT queue — that is the reader
                # narrating its own text through the highlight reader. The
                # reader speaks through the bridge itself.
                try:
                    if WIN32:
                        import win32gui as _w32g
                        _fg = _w32g.GetForegroundWindow()
                        _fg_title = _w32g.GetWindowText(_fg) or ""
                        if "Read Player" in _fg_title or "MorPHYes Read" in _fg_title:
                            last_clip = selected  # remember so we don't recheck
                            log("E-reader: skipped reader window self-read")
                            continue
                except Exception:
                    pass
                last_clip = selected
                with lock:
                    session_text.append(f"[READ ALOUD] {selected}")
                last_speech_ts = time.monotonic()
                ts = int(time.time() * 1000)
                queue_file = QUEUE_DIR / f"ereader_{ts}.json"
                queue_file.write_text(json.dumps(
                    {"text": selected, "source": "ereader", "engine": "piper"}
                ), encoding="utf-8")
                log(f"E-reader: queued {len(selected)} chars")
        except Exception:
            pass

# ════════════════════════════════════════════════════════════
#  HEARTBEAT
# ════════════════════════════════════════════════════════════
def heartbeat_loop():
    while state["alive"]:
        try:
            HB_FILE = CONFIG_DIR / "morPHYtrek_heartbeat.json"
            HB_FILE.write_text(json.dumps({
                "ts": datetime.now().isoformat(),
                "status": "listening" if state["listening"] else ("muted" if state["muted"] else "narrating"),
                "model": WHISPER_MODEL,
                "alive": True
            }), encoding="utf-8")
        except: pass
        time.sleep(5)

# ════════════════════════════════════════════════════════════
#  STACK STATUS PROBES — feed the widget icon line (friend 2026-08-24)
#  Grey = dead/missing. Green = alive. Yellow = working hard.
#  PURE STDLIB (bundled runtime has no psutil). Cheap + cached:
#  port connects, pid file + ctypes OpenProcess, one Ollama API call.
# ════════════════════════════════════════════════════════════
import socket
import ctypes

# Portable: probe the bundle's own dispatch pidfile. Falls back to the
# developer's live install if present (dual-run testing), else dead.
_SIMPLEJACK_PIDFILE = _APP_ROOT / "dispatch" / "dispatch.pid"
_PROC_CACHE = {}


def _port_up(port):
    t = time.time()
    c = _PROC_CACHE.get(("port", port))
    if c and t - c[0] < 3.0:
        return c[1]
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=0.4)
        s.close()
        ok = True
    except Exception:
        ok = False
    _PROC_CACHE[("port", port)] = (t, ok)
    return ok


def _pid_alive(pid):
    """stdlib pid liveness via kernel32.OpenProcess (PROCESS_QUERY_LIMITED_INFORMATION)."""
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, 0, int(pid))
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    except Exception:
        return False


def _ollama_busy():
    """True while a local model is loaded (Ollama /api/ps) — VRAM held = heavy."""
    t = time.time()
    c = _PROC_CACHE.get(("ollama",))
    if c and t - c[0] < 5.0:
        return c[1]
    busy = False
    ol_up = _port_up(11434)
    if ol_up:
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=0.5) as r:
                data = json.loads(r.read().decode("utf-8"))
                busy = bool(data.get("models"))
        except Exception:
            busy = False
    _PROC_CACHE[("ollama",)] = (t, busy)
    return busy


def _dispatch_alive():
    try:
        with open(_SIMPLEJACK_PIDFILE, "r") as f:
            dpid = int(f.read().strip())
        return _pid_alive(dpid)
    except Exception:
        return False


def stack_status_symbols():
    """[(symbol, name, color)] — grey dead, green alive, yellow heavy."""
    ol_up = _port_up(11434)
    ol_busy = _ollama_busy()
    out = []

    sj_up = _port_up(8797)
    out.append(("\u25c6", "SimpleJack", "#7ac043" if sj_up else "#555555"))

    hub_up = _port_up(8123)
    out.append(("\u2b24", "ModelHub", "#7ac043" if hub_up else "#555555"))

    rtr_up = _port_up(8123)
    out.append(("\u2301", "Router", "#7ac043" if rtr_up else "#555555"))

    out.append(("\U0001F3A7", "morPHYtrek", "#7ac043"))  # we ARE it

    out.append(("\u26c1", "Ollama",
                "#FFD700" if (ol_up and ol_busy) else
                ("#7ac043" if ol_up else "#555555")))

    out.append(("\U0001F5B4", "GPU",
                "#FFD700" if (ol_up and ol_busy) else "#7ac043"))

    out.append(("\u2709", "dispatch",
                "#7ac043" if _dispatch_alive() else "#555555"))

    return out


# ════════════════════════════════════════════════════════════
#  GUI — Small on-screen presence
# ════════════════════════════════════════════════════════════
class MorPHYtrekGUI:
    """Small always-on-top widget. When narrating, shows RSVP words
    with ORP focus letter in rust — same technique as SimpleJack.
    Mute only dims the status dot; it NEVER stops or greys out narration."""

    def __init__(self):
        if not TK_OK:
            log("Tkinter not available — running headless")
            self.root = None
            return
        self.root = tk.Tk()
        self.root.title("morPHYtrek")
        self.root.geometry("480x160")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#0a0b0f")
        self.root.overrideredirect(True)

        # Position bottom-right
        self.root.geometry("+{}+{}".format(
            self.root.winfo_screenwidth() - 500,
            self.root.winfo_screenheight() - 180
        ))

        # Top row: status dot + buttons (compact)
        self.top_frame = tk.Frame(self.root, bg="#0a0b0f")
        self.top_frame.pack(fill=tk.X, padx=6, pady=(3, 0))

        self.status_label = tk.Label(self.top_frame, text="\u25cf LISTENING",
                                     fg="#7ac043", bg="#0a0b0f",
                                     font=("Segoe UI", 9, "bold"), anchor="w")
        self.status_label.pack(side=tk.LEFT)

        self.btn_frame = tk.Frame(self.top_frame, bg="#0a0b0f")
        self.btn_frame.pack(side=tk.RIGHT)

        self.refresh_btn = tk.Button(self.btn_frame, text="\u21bb", command=self.refresh,
                                     bg="#1a1d28", fg="#FFD700", bd=0, font=("Segoe UI", 9),
                                     width=3, cursor="hand2", takefocus=0)
        self.refresh_btn.pack(side=tk.LEFT, padx=1)

        self.ereader_btn = tk.Button(self.btn_frame, text="\U0001f4d6", command=self.toggle_reader_drawer,
                                      bg="#1a1d28", fg="#7ac043", bd=0, font=("Segoe UI", 9),
                                      width=3, cursor="hand2", takefocus=0)
        self.ereader_btn.pack(side=tk.LEFT, padx=1)

        # WPM row: one slider drives BOTH the display speed AND Piper's voice.
        self.wpm_frame = tk.Frame(self.root, bg="#0a0b0f")
        self.wpm_frame.pack(fill=tk.X, padx=6, pady=(2, 0))

        self.wpm_label = tk.Label(self.wpm_frame, text="WPM", fg="#8a92a6",
                                  bg="#0a0b0f", font=("Segoe UI", 8, "bold"))
        self.wpm_label.pack(side=tk.LEFT, padx=(2, 4))

        self.wpm_scale = tk.Scale(self.wpm_frame, from_=100, to=1000, orient=tk.HORIZONTAL,
                                  resolution=50, showvalue=False, bg="#0a0b0f",
                                  fg="#d97834", troughcolor="#1a1d28",
                                  highlightthickness=0, bd=0, takefocus=0,
                                  command=self._on_wpm)
        self.wpm_scale.set(int(state.get("wpm", 200)))
        self.wpm_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.wpm_value = tk.Label(self.wpm_frame, text=str(state.get("wpm", 200)),
                                  fg="#d97834", bg="#0a0b0f",
                                  font=("Consolas", 10, "bold"), width=4, anchor="e")
        self.wpm_value.pack(side=tk.LEFT, padx=(4, 2))

        # ── THE EXACT SimpleJack CELL — just as big, under the original widget ──
        # SimpleJack .rsvp-word: font-size 2.2rem ≈ 35px ≈ Consolas 26pt bold.
        # One word at a time. Centered. White letters. ORP focus in rust.
        self.cell_frame = tk.Frame(self.root, bg="#0a1a2e",
                                   highlightbackground="#d97834",
                                   highlightthickness=1)
        self.cell_frame.pack(fill=tk.X, padx=6, pady=(6, 3))

        self.rsvp_text = tk.Text(self.cell_frame, height=1, width=48,
                                 bg="#0a1a2e", fg="#ece6d8",
                                 font=("Consolas", 26, "bold"),
                                 bd=0, highlightthickness=0,
                                 cursor="arrow", takefocus=0,
                                 wrap=tk.WORD)
        self.rsvp_text.pack(padx=8, pady=8, anchor="center")
        self.rsvp_text.config(state=tk.DISABLED)

        # Text tags for ORP coloring (same as SimpleJack rsvp-pre / rsvp-orp / rsvp-post)
        self.rsvp_text.tag_configure("orp", foreground="#b8401e",
                                     font=("Consolas", 26, "bold"))   # RUST focus letter
        self.rsvp_text.tag_configure("pre", foreground="#8a8478")      # dim
        self.rsvp_text.tag_configure("post", foreground="#6b6459")     # dimmer

        # RSVP timer
        self._rsvp_after_id = None

        # ── STACK STATUS LINE — friend's design (2026-08-24): meaningful
        # symbols, grey/green, yellow when that part is working hard.
        # Legend button (?) cycles: hidden → icons+names → icons only.
        self.stack_frame = tk.Frame(self.root, bg="#0a0b0f")
        self.stack_frame.pack(fill=tk.X, padx=6, pady=(4, 2))

        self.stack_label = tk.Label(self.stack_frame, text="", fg="#8a92a6",
                                    bg="#0a0b0f", font=("Segoe UI", 9),
                                    anchor="w", justify=tk.LEFT)
        self.stack_label.pack(side=tk.LEFT)

        self.legend_btn = tk.Button(self.stack_frame, text="?", command=self.cycle_legend,
                                    bg="#1a1d28", fg="#8a92a6", bd=0,
                                    font=("Segoe UI", 8, "bold"), width=2,
                                    cursor="hand2", takefocus=0)
        self.legend_btn.pack(side=tk.RIGHT, padx=(2, 0))
        self._legend_mode = 0   # 0=off, 1=icons+names, 2=icons only

        # widget height grows for the new line
        self.root.geometry("480x184")

        # Allow dragging
        self.root.bind("<Button-1>", self.start_drag)
        self.root.bind("<B1-Motion>", self.do_drag)
        self._drag_x = 0
        self._drag_y = 0

    # ── ORP calculation (identical to SimpleJack _rsvpOrpIndex) ──
    @staticmethod
    def _orp_index(word):
        n = len(word)
        if n <= 1: return 0
        if n <= 5: return 1
        if n <= 9: return 2
        if n <= 13: return 3
        return 4

    # ── Delay multiplier (identical to SimpleJack _rsvpDelayMultiplier) ──
    @staticmethod
    def _delay_mult(word):
        if not word: return 1.0
        last = word[-1]
        if last in ".!?": return 2.0
        if last in ",;:\u2014": return 1.3
        if len(word) > 10: return 1.1
        return 1.0

    # ── Show one word with ORP coloring ──
    def _rsvp_show_word(self, word):
        """Render one word with pre=dim, orp_char=rust bold, post=dimmer.
        Exactly the same visual as SimpleJack RSVP."""
        self.rsvp_text.config(state=tk.NORMAL)
        self.rsvp_text.delete("1.0", tk.END)

        oi = self._orp_index(word)
        pre = word[:oi]
        orp_ch = word[oi] if oi < len(word) else ""
        post = word[oi+1:]

        if pre:
            self.rsvp_text.insert(tk.END, pre, "pre")
        if orp_ch:
            self.rsvp_text.insert(tk.END, orp_ch, "orp")
        if post:
            self.rsvp_text.insert(tk.END, post, "post")

        self.rsvp_text.config(state=tk.DISABLED)

    # ── WPM slider handler — one slider drives display AND voice ──
    def _on_wpm(self, val):
        try:
            wpm = int(float(val))
        except Exception:
            wpm = 200
        state["wpm"] = wpm
        self.wpm_value.config(text=str(wpm))
        log(f"WPM set to {wpm}")

    # ── RSVP tick loop — synced to the REAL audio clock ──
    def _rsvp_tick(self):
        """Advance words on the real audio clock.

        The tick is scheduled only while audio is playing. Elapsed audio time
        picks which word is spoken NOW — not a fixed 130ms timer. When audio
        finishes, the cell goes CLEAN (no pale tail)."""
        if not self.root:
            return
        words = state.get("sub_words", [])
        if not words or not state.get("sub_active"):
            self._rsvp_clear()
            return

        start = state.get("sub_play_start", 0.0)
        dur = state.get("sub_audio_dur", 0.0)

        # Not playing yet (Piper still generating) — hold. No turbo-spit.
        if start <= 0.0:
            self._rsvp_after_id = self.root.after(200, self._rsvp_tick)
            return

        elapsed = time.monotonic() - start

        # Audio finished — every word was revealed one at a time. Cell goes
        # clean, dot returns to LISTENING. No greyed tail. Ever.
        if dur > 0 and elapsed >= dur:
            self.rsvp_text.config(state=tk.NORMAL)
            self.rsvp_text.delete("1.0", tk.END)
            self.rsvp_text.config(state=tk.DISABLED)
            self._rsvp_after_id = None
            state["sub_active"] = False
            return

        # PROPORTIONAL mapping: the trace takes EXACTLY as long as the audio.
        # Word N is shown during the Nth slice of the real audio duration.
        # The last word is scheduled to land right as the audio ends — it can
        # never be cut, because the cell only clears after the audio is done.
        if dur <= 0:
            dur = 1.0
        n = len(words)
        frac = elapsed / dur
        idx = min(int(frac * n), n - 1)
        state["sub_idx"] = idx

        word = words[idx]
        self._rsvp_show_word(word)

        # Next tick ~40ms later — smooth enough, cheap enough.
        self._rsvp_after_id = self.root.after(40, self._rsvp_tick)

    def _rsvp_clear(self):
        """Stop RSVP timer and clear the word display."""
        if self._rsvp_after_id is not None and self.root:
            self.root.after_cancel(self._rsvp_after_id)
            self._rsvp_after_id = None
        self.rsvp_text.config(state=tk.NORMAL)
        self.rsvp_text.delete("1.0", tk.END)
        self.rsvp_text.config(state=tk.DISABLED)

    def cycle_legend(self):
        self._legend_mode = (self._legend_mode + 1) % 3
        self.draw_stack_line(force=True)
        log(f"Legend mode: {['hidden', 'icons+names', 'icons only'][self._legend_mode]}")

    def draw_stack_line(self, force=False):
        """Render the status icon line beneath the trace.
        Modes: 0=icons only (default view), 1=icons+names, 2=hidden."""
        if not self.root: return
        syms = stack_status_symbols()
        if self._legend_mode == 2:
            self.stack_label.config(text="")
            return
        if self._legend_mode == 1:
            # named legend view — symbol + short name, one line
            parts = [f"{s} {n}" for s, n, _ in syms]
            self.stack_label.config(text="  ".join(parts), fg="#8a92a6")
            return
        # mode 0: colored icons only
        for lbl in getattr(self, "_stack_sym_labels", []):
            lbl.destroy()
        labels = []
        for i, (s, n, col) in enumerate(syms):
            if i:
                sep = tk.Label(self.stack_frame, text="·", fg="#333845", bg="#0a0b0f",
                               font=("Segoe UI", 9))
                sep.pack(side=tk.LEFT, padx=(0, 1))
                labels.append(sep)
            lbl = tk.Label(self.stack_frame, text=s, fg=col, bg="#0a0b0f",
                           font=("Segoe UI", 11, "bold"))
            lbl.pack(side=tk.LEFT, padx=(0, 2))
            # tooltip-ish: click an icon to speak its name
            lbl.bind("<Button-1>", lambda e, name=n: self._speak_icon(name))
            labels.append(lbl)
        self._stack_sym_labels = labels

    def _speak_icon(self, name):
        try:
            qd = _queue_dirs()[0]
            qd.mkdir(parents=True, exist_ok=True)
            fn = qd / f"aila_icon_{int(time.time())}.json"
            fn.write_text(json.dumps({"text": name, "source": "aila",
                                      "engine": "piper"}), encoding="utf-8")
        except Exception:
            pass

    def start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def do_drag(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def refresh(self):
        """REFRESH = ABORT (operator order 2026-10-15).
        Aborts the narration currently being read and deletes its queue file.
        If nothing is narrating, falls back to the old self-heal (whisper init)."""
        if state.get("narrating") or state.get("current_narration_file"):
            state["abort_narration"] = True
            log("Refresh pressed — ABORTING current narration")
            # 1. stop the speaker stream immediately
            try:
                sd.stop()
            except Exception:
                pass
            st = state.get("active_stream")
            if st is not None:
                try:
                    st.abort()
                except Exception:
                    pass
            # 2. kill the live Piper process so it stops synthesising
            pr = state.get("active_proc")
            if pr is not None:
                try:
                    pr.kill()
                except Exception:
                    pass
            # 3. delete the queue file being read
            cur = state.get("current_narration_file")
            if cur:
                try:
                    Path(cur).unlink()
                    log(f"Refresh deleted queue file: {Path(cur).name}")
                except Exception as e:
                    log(f"Refresh could not delete {cur}: {e}")
            return
        log("Refresh button pressed (nothing narrating)")
        if not whisper_model:
            init_whisper()

    def toggle_ereader(self):
        # Kept for compatibility — ereader is ALWAYS ON now (operator order
        # 2026-10-16). The abort button handles 'too much'.
        state["ereader_on"] = True
        log("E-reader: always on with morPHYtrek (abort button stops any read)")

    def toggle_reader_drawer(self):
        """READER DRAWER (operator order 2026-10-16):
        Book icon opens the branded ReadPlayer v3 in a borderless Edge app
        window anchored directly below the widget. Click again to close it.
        Pure automation, no model."""
        # ── CLOSE PATH ──
        proc = getattr(self, "reader_proc", None)
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
            self.reader_proc = None
            log("Reader drawer CLOSED")
            return
        # ── OPEN PATH ──
        reader_url = (_APP_ROOT / "reader" / "ReadPlayer_v3.html").as_uri()
        W, H = 960, 560
        x = self.root.winfo_x() + (self.root.winfo_width() - W) // 2
        max_x = self.root.winfo_screenwidth() - W - 8
        x = max(8, min(x, max_x))
        y = self.root.winfo_y() + self.root.winfo_height() + 8
        max_y = self.root.winfo_screenheight() - H - 60
        y = max(8, min(y, max_y))
        try:
            import subprocess
            edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
            if not Path(edge).exists():
                edge = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
            if Path(edge).exists():
                self.reader_proc = subprocess.Popen([
                    edge,
                    f"--app={reader_url}",
                    f"--window-size={W},{H}",
                    f"--window-position={x},{y}",
                ])
                log(f"Reader drawer OPENED (Edge app window at {x},{y})")
                return
        except Exception as e:
            log(f"Reader drawer Edge path failed: {e}")
        # Fallback: default browser (no close tracking possible)
        import webbrowser
        webbrowser.open(reader_url)
        log("Reader drawer OPENED (default browser — close it by hand)")

    def update(self):
        if not self.root: return

        # ── Status dot (top-left) ──
        if state["muted"]:
            self.status_label.config(text="\u25cf MUTED", fg="#555555")
        elif state["narrating"]:
            self.status_label.config(text="\u25cf", fg="#FFD700")
        else:
            self.status_label.config(text="\u25cf LISTENING", fg="#7ac043")

        # ── STACK STATUS LINE — beneath the trace, every 2s ──
        t = time.time()
        if t - getattr(self, "_last_stack_draw", 0) >= 2.0:
            self._last_stack_draw = t
            try:
                self.draw_stack_line()
            except Exception as e:
                self.stack_label.config(text="[status line err: %s]" % str(e)[:40])

        # ── RSVP word display — COMPLETELY INDEPENDENT OF MUTE ──
        # Mute does NOT stop, grey out, or interfere with narration words. Ever.
        if state.get("sub_active") and state.get("sub_words"):
            if self._rsvp_after_id is None:
                self._rsvp_tick()
        else:
            if self._rsvp_after_id is not None:
                self._rsvp_clear()

    def run(self):
        if not self.root:
            return
        global MORPHYTREK_HWND
        MORPHYTREK_HWND = win32gui.GetForegroundWindow() if WIN32 else None
        if _last_target_window and WIN32:
            try:
                win32gui.SetForegroundWindow(_last_target_window)
            except: pass
        def tick():
            self.update()
            self.root.after(500, tick)
        tick()
        self.root.mainloop()

def update_gui():
    """Called from worker threads — GUI updates itself on timer."""
    pass  # GUI polls state every 500ms via tick()

# ════════════════════════════════════════════════════════════
#  SPACEBAR MUTE
# ════════════════════════════════════════════════════════════
def on_space(key):
    if key == kb.Key.space:
        # Only toggle on spacebar press, not repeat
        state["muted"] = not state["muted"]
        state["listening"] = not state["muted"]
        # MUTE FLUSH (2026-08-18): stale audio captured before the mute must NOT
        # survive it — that half-utterance is the hallucination+puke on unmute.
        with lock:
            audio_buffer.clear()
        log("MIC MUTED" if state["muted"] else "MIC LISTENING")

def start_hotkeys():
    try:
        listener = kb.Listener(on_press=on_space)
        listener.start()
        log("Spacebar mute active")
    except Exception as e:
        log(f"Hotkey init failed: {e}")

# ════════════════════════════════════════════════════════════
#  STATUS FILE
# ════════════════════════════════════════════════════════════
def write_status():
    try:
        STATUS_FILE.write_text(json.dumps({
            "state": "muted" if state["muted"] else ("narrating" if state["narrating"] else "listening"),
            "ts": datetime.now().isoformat(),
            "model": WHISPER_MODEL
        }), encoding="utf-8")
    except: pass

# ════════════════════════════════════════════════════════════
#  SINGLE INSTANCE GUARD
# ════════════════════════════════════════════════════════════
import socket as _sock
_lock_sock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
if hasattr(_sock, "SO_EXCLUSIVEADDRUSE"):
    _lock_sock.setsockopt(_sock.SOL_SOCKET, _sock.SO_EXCLUSIVEADDRUSE, 1)
try:
    # Portable clone rule (2026-08-24): own lock port. The live release
    # mouth locks 47839; the portable clone locks 47841 so both can sit
    # on the same machine during testing. On a customer machine the
    # single-instance guard works exactly as before — one port, one trek.
    _lock_sock.bind(("127.0.0.1", 47841))
except OSError:
    print("[morPHYtrek] Already running. Exiting.")
    sys.exit(0)

# ════════════════════════════════════════════════════════════
#  EXTENSION BRIDGE — tiny HTTP listener for Chrome "Read Aloud"
#  Right-click → Read Aloud → POST to localhost:8792/api/narrate
#  morPHYtrek writes the queue file itself. No external bridge.
# ════════════════════════════════════════════════════════════
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as _THTTP
from urllib.parse import urlparse as _urlparse

EXTENSION_PORT = 8792

class _ExtHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

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
        path = _urlparse(self.path).path
        if path == "/api/health":
            self._send_json({"status": "alive", "service": "morPHYtrek"})
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = _urlparse(self.path).path
        if path == "/api/narrate":
            body = self._read_body()
            text = body.get("text", "")
            source = body.get("source", "ereader")
            engine = body.get("engine", "piper")
            if text:
                ts = int(time.time() * 1000)
                qf = QUEUE_DIR / f"ereader_{ts}.json"
                qf.write_text(json.dumps(
                    {"text": text[:50000], "source": source, "engine": engine}
                ), encoding="utf-8")
                log(f"Extension: queued {len(text)} chars")
                self._send_json({"success": True, "queued": text[:80]})
            else:
                self._send_json({"error": "no text"}, 400)
            return
        if path in ("/api/articulate", "/api/promote", "/api/reimagine"):
            body = self._read_body()
            raw_text = body.get("text", "")
            if not raw_text:
                self._send_json({"error": "no text"}, 400)
                return
            try:
                # PROMOTE (operator order 2026-10-16): promote renders the
                # branded HTML + footer from the text AS-IS. NO model call —
                # the old code ran _mt_clean() first, which needs Josie; when
                # Josie wasn't loaded the endpoint hung and the extension
                # reported "bridge offline" / returned the identical text.
                # Promote's job is the beautiful HTML with the footer, every
                # time, instantly, on any machine — model or no model.
                if path == "/api/promote":
                    md, html = _mt_promote(raw_text.strip())
                    self._send_json({"success": True, "md": md, "html": html})
                elif path == "/api/reimagine":
                    # REIMAGINE (operator order 2026-10-16): the one feature
                    # that truly needs a language model. In the sale build it
                    # is the upgrade door — small notification, no hang, no
                    # silent fallback, no model shipped.
                    self._send_json({"success": False,
                                     "error": "requires upgrade",
                                     "upgrade": True}, 200)
                else:
                    cleaned = _mt_clean(raw_text.strip())
                    if path == "/api/articulate":
                        self._send_json({"success": True, "text": cleaned})
            except Exception as e:
                self._send_json({"error": str(e)}, 503)
            return
        self._send_json({"error": "not found"}, 404)

    def log_message(self, *args):
        pass

# ── ARTICULATE / PROMOTE helpers (OFFLINE, via LOCAL OLLAMA — friend 2026-09-03) ──
# DESIGN LAW FIX: the comment always said "offline, via local Ollama" but the
# code called OpenRouter FIRST. OpenRouter free lane 429s when the daily quota
# dies → handler returns 503 → the extension wrongly says "bridge offline".
# Local Josie answers in ~1s on this machine (live-verified). LOCAL IS PRIMARY.
# OpenRouter stays ONLY as fallback for the rare case Ollama is down.
_MT_OL_URL = "http://127.0.0.1:11434/api/chat"
# MODEL ORDER (operator order 2026-10-16): 2b EVERY TIME — smallest, fastest,
# loads in seconds. 4b and 9b-tools stay as fallbacks only if 2b is missing.
_MT_OL_MODELS = ("josie-2b-tools:latest", "josie-4b-tools:latest", "josie-9b-tools:latest")
_MT_OR_URL = "https://openrouter.ai/api/v1/chat/completions"
_MT_OR_KEY = os.environ.get("OPENROUTER_API_KEY", "")  # BYO: no key ships; buyer sets their own
_MT_OR_MODEL = "google/gemma-4-31b-it:free"
_MT_ACRO = {"i","ai","app","gpu","cpu","ok","pm","am","html","md","os","tts","api","ucp","ceo","usa","llc","ps","po","kc","bc","us","morphyes","morphytrek","morphyesmastery","josie"}

def _mt_decaps(t):
    letters = [c for c in t if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.7:
        t = t.lower()
        t = re.sub(r"(^|[.!?]\s+|\n)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
        t = re.sub(r"\bi\b", "I", t)
        return t
    def _w(m):
        w = m.group(0)
        if w.lower() in _MT_ACRO:
            return w
        return w[0] + w[1:].lower()
    return re.sub(r"\b[A-Z]{2,}\b", _w, t)

def _mt_ol_call(sys_p, user_t, temp):
    """LOCAL FIRST (2026-09-03): Josie via Ollama — offline, sovereign, ~1s.
    NO CAP, NO TIMER (2026-09-22): the model decides when it is done. The old
    num_predict 800 was an agent-written budget, not a property of the model."""
    import urllib.request as _ur
    last_err = None
    for model in _MT_OL_MODELS:
        try:
            payload = json.dumps({"model": model, "stream": False, "think": False,
                                  "options": {"temperature": temp, "num_ctx": 8192},
                                  "messages": [{"role": "system", "content": sys_p},
                                               {"role": "user", "content": user_t}]})
            req = _ur.Request(_MT_OL_URL, data=payload.encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
            with _ur.urlopen(req, timeout=None) as r:   # NO TIMER (2026-09-22) — local model reply
                data = json.loads(r.read().decode("utf-8"))
            msg = (data.get("messages") or [{}])[-1]
            out = (data.get("message", {}) or {}).get("content", "") or msg.get("content", "")
            out = (out or "").strip()
            if out:
                return out
            last_err = "empty local output"
        except Exception as e:
            last_err = str(e)[:120]
    raise RuntimeError(f"local Ollama failed: {last_err}")

def _mt_or_call(sys_p, user_t, temp=0.3):
    """CLOUD FIRST (friend's order 2026-09-03: cloud provider for SPEED).
    OpenRouter gemma free is the primary brain for Articulate/Promote.
    Local Josie is the FALLBACK ONLY for the daily 50-request ration window
    (resets 18:00 server time) — so the extension never reports 'bridge down'
    just because the day's ration died."""
    if not _MT_OR_KEY:
        # BYO product ships keyless - local Josie takes it, auth never fails.
        return _mt_ol_call(sys_p, user_t, temp)
    import urllib.request as _ur
    payload = json.dumps({"model": _MT_OR_MODEL, "temperature": temp, "messages": [
        {"role": "system", "content": sys_p},
        {"role": "user", "content": user_t}]})
    req = _ur.Request(_MT_OR_URL, data=payload.encode("utf-8"),
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + _MT_OR_KEY},
                      method="POST")
    with _ur.urlopen(req, timeout=None) as r:   # NO TIMER (2026-09-22) — model reply
        data = json.loads(r.read().decode("utf-8"))
    out = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    if not out:
        # Cloud returned nothing usable (ration dead / empty) → local Josie
        # takes it so the operator still gets his Promote instead of an error.
        return _mt_ol_call(sys_p, user_t, temp)
    return out

def _mt_brain(sys_p, user_t, temp):
    """LOCAL IS PRIMARY (design law 2026-09-03, applied 2026-09-04):
    Josie via local Ollama first - offline, sovereign, ~1s (live-verified).
    Cloud OpenRouter ONLY if local Ollama is down. The old cloud-first order
    died on a placeholder key (401) and the exception escaped past the
    empty-only fallback - articulate returned 503 "bridge offline"."""
    try:
        return _mt_ol_call(sys_p, user_t, temp)
    except Exception:
        return _mt_or_call(sys_p, user_t, temp)

def _mt_clean(t):
    """ARTICULATE (operator order 2026-10-16): grammar-and-spelling cleanup is
    a RULES job, not a model job — the same thing Windows has done for years.
    No model, no Ollama, no download: instant, offline, works on any machine.
    The model belongs to reimagine (rewording), never to articulate (cleanup).
    Rules: de-SHOUT all caps, sentence-start capitals, I capitalization,
    space collapse, spacing after punctuation."""
    # 1. De-shout: if the text is mostly caps, lowercase it and re-capitalize
    #    sentence starts (keeps known acronyms via _mt_decaps).
    t = _mt_decaps(t)
    # 2. Sentence-start capitals (after . ! ? or newline or start).
    t = re.sub(r"(^|[.!?]\s+|\n)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
    # 3. Standalone i → I.
    t = re.sub(r"\bi\b", "I", t)
    # 4. Collapse runs of spaces/tabs (keep newlines — paragraph structure).
    t = re.sub(r"[ \t]{2,}", " ", t)
    # 5. One space after , ; : (never before).
    t = re.sub(r"\s+([,;:])", r"\1", t)
    t = re.sub(r"([,;:])(?=[A-Za-z])", r"\1 ", t)
    # 6. Space before . ! ? only when it's clearly a sentence end (word before).
    t = re.sub(r"(\w)\s+([.!?])(?=\s|$)", r"\1\2", t)
    # 7. Trim each line; drop trailing blank runs.
    lines = [ln.strip() for ln in t.split("\n")]
    t = "\n".join(lines)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

def _mt_brand():
    _root = Path(__file__).resolve().parent
    for p in (_root / "branding" / "brand.json",
              Path(__file__).resolve().parent / "branding" / "brand.json"):
        try:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _mt_promote(t):
    b = _mt_brand()
    pal = b.get("palette", {})
    name = b.get("name", "MorPHYes Mastery")
    site = b.get("website", "www.morphyesmastery.com")
    cta = b.get("footer_cta", "Get your MorPHYes masterpiece today")
    furl = b.get("footer_url", "https://" + site)

    # PROMOTE (operator order 2026-10-01, flat): promote renders the agreed
    # HTML conversion and that is ALL it does. The copywriter pass that rewrote
    # the words toward selling is GONE — that was the "windows style" output.
    # The words belong to articulate (cleanup) and reimagine (attractive
    # language), never to promote.
    polished = t

    paras = [pp.strip() for pp in polished.split("\n") if pp.strip()]
    md = "\n\n".join(paras) + "\n\n---\n**[" + cta + "](" + furl + ")** \u2014 " + name
    html = ('<div style="font-family:Segoe UI,Arial,sans-serif;max-width:640px;margin:0 auto;'
            'padding:20px 24px;border:1px solid rgba(0,0,0,0.08);border-radius:10px;'
            'background:' + pal.get("bg", "#0f2340") + ';color:' + pal.get("ink", "#ece6d8") + '">'
            + "".join('<p style="margin:0 0 12px 0;line-height:1.6;font-size:15px;">' + pp + "</p>" for pp in paras)
            + '<hr style="border:none;border-top:1px solid rgba(255,255,255,0.15);margin:18px 0;">'
            + '<p style="margin:0;font-size:14px;"><a href="' + furl + '" style="color:'
            + pal.get("orange", "#d97834") + ';font-weight:700;text-decoration:none;text-transform:uppercase;'
            'letter-spacing:0.5px;">' + cta + '</a> &nbsp;\u2022&nbsp; <span style="color:'
            + pal.get("mut", "#8a92a6") + ';">' + name + "</span></p></div>")
    return md, html

def _mt_reimagine(t):
    """REIMAGINE WITH MORPHYTREK (operator order 2026-10-01, Trent's design).
    The model sees the information dump and conveys it precisely into
    attractive language. Every fact kept, his voice kept. NO markdown headers,
    NO bullet points — plain attractive prose, because most emails have no
    headers. Leans toward the content-brief-generator style, far away from
    promote's selling. STATELESS: every call is fresh, so the user can fire it
    as many times as they want if the first result is terrible."""
    sys_p = (
        "You are a world-class writer. The user gives you a rough information "
        "dump — their thoughts, half-ordered. Reimagine it: convey precisely "
        "the same information in attractive, natural language. Keep EVERY fact, "
        "every number, every name, and the writer's own voice. Do not add "
        "claims, do not invent, do not sell. NO markdown headers, NO bullet "
        "points, NO lists — plain flowing prose, the way a person writes an "
        "email. Fix grammar, punctuation, and capitalization. "
        "Return ONLY the reimagined text, no subject line, no signature, no commentary."
    )
    return _mt_brain(sys_p, t, temp=0.6)

def extension_server_loop():
    """Tiny HTTP server for Chrome extension Read Aloud. Runs in a thread."""
    try:
        srv = _THTTP(("127.0.0.1", EXTENSION_PORT), _ExtHandler)
        log(f"Extension bridge on port {EXTENSION_PORT}")
        srv.serve_forever()
    except Exception as e:
        log(f"Extension bridge error: {e}")

# ════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════
def main():
    log("=" * 50)
    log("morPHYtrek — THE BEST HUMAN COMPUTER INTERFACE")
    log(f"Whisper model: {WHISPER_MODEL}")
    log(f"Queue: {QUEUE_DIR}")
    log(f"Transcript: {TRANSCRIPT_DIR}")
    log("Sequential operation: listen -> process -> narrate -> repeat")
    log("Spacebar = mute/unmute microphone")
    log("=" * 50)

    # Stale-file cleanup (2026-09-21 — CHANGED, operator law: a reply he cannot
    # hear DID NOT HAPPEN). The old 2-minute purge was DELETING real narration
    # files that were still waiting behind a long job or a refresh — words died
    # silently and the operator heard nothing. Now: 30 minutes, and every purge
    # is LOGGED with its text so nothing can ever vanish without a trace.
    import time as _time
    _now = _time.time()
    _purged = 0
    for d in _queue_dirs():
        for f in d.glob("*.json"):
            try:
                if f.name.lower() == "folder.md":
                    continue
                if _now - f.stat().st_mtime > 1800:
                    try:
                        _snip = f.read_text(encoding="utf-8-sig", errors="ignore")[:80].replace("\n", " ")
                    except Exception:
                        _snip = "(unreadable)"
                    f.unlink()
                    _purged += 1
                    log(f"Purged stale (>30 min) narration file {f.name}: {_snip}")
            except Exception:
                pass
    if _purged:
        log(f"Startup cleanup: purged {_purged} stale narration file(s) older than 30 min")

    # Init Whisper (the ear)
    init_whisper()
    
    # Start worker threads
    start_hotkeys()
    # Start focus tracker BEFORE GUI so we catch the initial target window
    threading.Thread(target=track_focus, daemon=True).start()
    threading.Thread(target=vad_loop, daemon=True).start()
    threading.Thread(target=flush_watchdog, daemon=True).start()
    threading.Thread(target=narration_loop, daemon=True).start()
    threading.Thread(target=ereader_loop, daemon=True).start()
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=extension_server_loop, daemon=True).start()
    
    # Start memory daemon (tracks wants + task completions only)
    # VISIBLE LABELLED WINDOW — zero hidden processes
    # Optional sidecar daemons (developer machine only): each is
    # existence-checked and skips silently when absent — a customer bundle
    # simply runs without them.
    MEMORY_SCRIPT = _APP_ROOT / "morPHYmemory" / "morPHYmemory.py"
    if MEMORY_SCRIPT.exists():
        def _start_memory():
            try:
                subprocess.Popen(
                    [str(MORPHYVENV_PYTHON), str(MEMORY_SCRIPT)],
                    cwd=str(MEMORY_SCRIPT.parent),
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
                log("morPHYmemory daemon launched (visible window)")
            except Exception as e:
                log(f"morPHYmemory launch failed: {e}")
        threading.Thread(target=_start_memory, daemon=True).start()
    else:
        log(f"morPHYmemory.py not found at {MEMORY_SCRIPT}")

    # Start session bridge — gathers intel into I_AM.txt when INACTIVE.
    BRIDGE_SCRIPT = _APP_ROOT / "SKILLS" / "session_bridge.py"
    if BRIDGE_SCRIPT.exists():
        def _start_bridge():
            try:
                subprocess.Popen(
                    [str(MORPHYVENV_PYTHON), str(BRIDGE_SCRIPT), "watch", "300"],
                    cwd=str(BRIDGE_SCRIPT.parent),
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
                log("session_bridge daemon launched (INACTIVITY MODE, 300s threshold)")
            except Exception as e:
                log(f"session_bridge launch failed: {e}")
        threading.Thread(target=_start_bridge, daemon=True).start()
    else:
        log(f"session_bridge.py not found at {BRIDGE_SCRIPT} — I_AM will go stale!")
    
    log("All systems running. GUI starting.")
    
    # Start GUI (main thread — Tkinter needs main thread)
    gui = MorPHYtrekGUI()
    gui.run()
    
    # If GUI closes, stop everything
    state["alive"] = False
    log("morPHYtrek shutting down")

if __name__ == "__main__":
    main()
