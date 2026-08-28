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
    "ereader_on": False,     # OFF by default - toggle with book button
    # RSVP subtitle state
    "sub_words": [],         # list of words in current sentence
    "sub_idx": 0,            # current word index for RSVP display
    "sub_active": False,     # True while subtitle is cycling
    "sub_play_start": 0.0,   # monotonic clock when audio actually starts playing
    "sub_audio_dur": 0.0,    # duration of the current audio in seconds
    "wpm": 200,              # display + voice speed — DEFAULT 200 (spec)
    "wpm_natural": 210,      # Alba natural rate measured 2026-08-02 @ length-scale 1.0
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

def get_piper_model():
    cat = piper_catalog()
    if not cat: return None
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

def piper_generate(text):
    """Generate audio via Piper. Returns (np_array, sample_rate) or None."""
    model = get_piper_model()
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
    return text

def split_sentences(text):
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in parts if s.strip()]

def read_queue_job(path):
    """Read a narration job from the queue. Returns (text, voice, source, engine) or None."""
    try:
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return (data.get("text", ""), data.get("voice"), 
                    data.get("source", ""), data.get("engine", "piper"))
        else:
            return (path.read_text(encoding="utf-8").strip(), None, "unknown", "piper")
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
                try:
                    path.unlink()
                except Exception:
                    pass
                if not result: continue
                text, voice, source, engine = result
                text = filter_text(text)
                if not text: continue
                sentences = split_sentences(text)
                log(f"Narrating [{source}]: {len(sentences)} sentences")
                for s in sentences:
                    if not state["alive"]: break
                    # Set RSVP subtitle words BEFORE generating — the cell is
                    # ready, but the words only start cycling when audio actually
                    # plays (sub_play_start). Kills the turbo-spit.
                    words = s.strip().upper().split()
                    if words:
                        state["sub_words"] = words
                        state["sub_idx"] = 0
                        state["sub_active"] = True
                        state["sub_play_start"] = 0.0
                    audio = piper_generate(s)
                    if audio:
                        play_audio(audio[0], audio[1])
                    state["sub_active"] = False
                    state["sub_words"] = []
                    state["sub_play_start"] = 0.0
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
def ereader_loop():
    """When text is highlighted, copy it to transcript and narrate.
    Version 1 - the one that worked.
    When toggled OFF, the loop sleeps long and does NOT touch clipboard at all."""
    last_clip = ""
    while state["alive"]:
        if not state["ereader_on"]:
            time.sleep(5)  # When OFF, sleep and do NOTHING with clipboard
            continue
        time.sleep(2)
        try:
            current = pyperclip.paste()
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.1)
            selected = pyperclip.paste()
            if selected != current:
                pyperclip.copy(current)
            if selected and selected != current and selected != last_clip and len(selected) > 10:
                last_clip = selected
                with lock:
                    session_text.append(f"[READ ALOUD] {selected}")
                last_speech_ts = time.monotonic()
                ts = int(time.time() * 1000)
                queue_file = QUEUE_DIR / f"ereader_{ts}.json"
                queue_file.write_text(json.dumps(
                    {"text": selected[:50000], "source": "ereader", "engine": "piper"}
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
#  STACK STATUS PROBES — feed the widget icon line (Trent 2026-08-24)
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

        self.ereader_btn = tk.Button(self.btn_frame, text="\U0001f4d6", command=self.toggle_ereader,
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

        # ── STACK STATUS LINE — Trent's design (2026-08-24): meaningful
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
        log("Refresh button pressed")
        if not whisper_model:
            init_whisper()

    def toggle_ereader(self):
        state["ereader_on"] = not state["ereader_on"]
        self.ereader_btn.config(fg="#7ac043" if state["ereader_on"] else "#555555")
        log(f"E-reader {'ON' if state['ereader_on'] else 'OFF'}")

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
        self._send_json({"error": "not found"}, 404)

    def log_message(self, *args):
        pass

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

    # Purge stale narration files (older than 2 minutes) on startup
    import time as _time
    _now = _time.time()
    _purged = 0
    for d in _queue_dirs():
        for f in d.glob("*.json"):
            try:
                if _now - f.stat().st_mtime > 120:
                    f.unlink()
                    _purged += 1
            except Exception:
                pass
    if _purged:
        log(f"Startup cleanup: purged {_purged} stale narration file(s) older than 2 min")

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
