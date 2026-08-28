# TAB: Agent AILA | ⚡ | Screen-mapped Hermes proxy · types + reads
# HOOK: /api/agentaila_chat | POST | Routes prompt through Hermes Desktop via screen mapping
# DEST: tool
# COLOR: gold

"""
agentaila.py — Screen-mapped Hermes proxy. LOOP AGENT.

LOOP AGENT (Trentism, 2026-07-28): She watches, waits, says "still working"
while the reply cooks, delivers when it lands, loops for the next.
Relay and frame only. Different from local AILA — this is her ONLY job.

Finds the Hermes Desktop window, types Trent's prompt into it,
waits for the response, copies it back to screen, and narrates everything.

Uses Win32 API — no CDP needed. Hermes window never moves.
Screen coordinates are known. Monitor map is hardcoded.

Drift detection runs on the response before delivery.
Doesn't know how to not narrate. EVERYONE IS AILA.
"""

import os
import sys
import json
import time
import re
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

# ════════════════════════════════════════════════════════
# PATHS
# ════════════════════════════════════════════════════════
NARRATION_QUEUE = Path(__file__).resolve().parent.parent / "morphytrek_data" / "queue"
MORPHYES_ROOT = Path(__file__).resolve().parent.parent

# ════════════════════════════════════════════════════════
# SANITIZER + NARRATION
# ════════════════════════════════════════════════════════
def sanitize_for_ears(text):
    clean = re.sub(r'```.*?```', ' ', text, flags=re.DOTALL)
    clean = clean.replace('```', ' ').replace('`', ' ')
    clean = re.sub(r'[A-Za-z]:\\[^\s"\'<>\[\]]+', 'the file', clean)
    clean = re.sub(r'/[^\s"\'<>]+\.\w{2,5}', 'the file', clean)
    clean = clean.replace('\\', ' then ').replace('/', ' then ')
    for arrow in ('→', '=>', '->', '<-', '←'):
        clean = clean.replace(arrow, ' then ')
    for h in (' - ', ' — ', ' – '):
        clean = clean.replace(h, ' then ')
    clean = clean.replace('#', ' ').replace('*', ' ').replace('|', ' ')
    clean = re.sub(r'[<>{}^~|]', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    clean = re.sub(r'( then){2,}', ' then ', clean)
    return clean


def narrate(text):
    if not text or not text.strip():
        return
    NARRATION_QUEUE.mkdir(parents=True, exist_ok=True)
    clean = sanitize_for_ears(text)
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
                if buf: chunks.append(buf)
                if len(s) > MAX_CHUNK:
                    words = s.split(" ")
                    hard = ""
                    for w in words:
                        if len(hard) + len(w) + 1 <= MAX_CHUNK:
                            hard = (hard + " " + w).strip()
                        else:
                            if hard: chunks.append(hard)
                            hard = w
                    buf = hard
                else:
                    buf = s
        if buf: chunks.append(buf)
    for chunk in chunks:
        ts = int(time.time() * 1000000)
        final = NARRATION_QUEUE / f"agentaila_{ts}.json"
        tmp = final.with_suffix(".tmp")
        tmp.write_text(json.dumps({"text": chunk, "source": "aila", "engine": "piper"}), encoding="utf-8")
        tmp.replace(final)
        time.sleep(0.05)


# ════════════════════════════════════════════════════════
# WIN32 WINDOW AUTOMATION
# ════════════════════════════════════════════════════════
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Hermes Desktop window title (partial match)
HERMES_TITLES = ["Hermes", "hermes", "AILA"]

def find_hermes_window():
    """Find the Hermes Desktop window by title. Returns hwnd or None."""
    found = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    
    def enum_callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        for t in HERMES_TITLES:
            if t.lower() in title.lower():
                found.append(hwnd)
                return False  # stop enumeration
        return True
    
    user32.EnumWindows(EnumWindowsProc(enum_callback), 0)
    return found[0] if found else None


def get_window_rect(hwnd):
    """Get window position and size. Returns (left, top, right, bottom)."""
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def set_foreground(hwnd):
    """Bring window to foreground."""
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    our_tid = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(our_tid, fg_tid, True)
    try:
        user32.SetForegroundWindow(hwnd)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    finally:
        user32.AttachThreadInput(our_tid, fg_tid, False)
    time.sleep(0.3)


def send_click(x, y):
    """Send a mouse click at absolute screen coordinates."""
    # Move cursor
    user32.SetCursorPos(x, y)
    time.sleep(0.1)
    # Click down then up
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
    time.sleep(0.1)


def send_keys(text):
    """Type text via clipboard paste + Ctrl+V. Faster and more reliable than key-by-key."""
    # Copy to clipboard
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", f"Set-Clipboard -Value @'\n{text}\n'@"],
        capture_output=True, timeout=5
    )
    time.sleep(0.2)
    # Ctrl+V
    VK_CONTROL = 0x11
    VK_V = 0x56
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 2, 0)  # KEYEVENTF_KEYUP
    user32.keybd_event(VK_CONTROL, 0, 2, 0)
    time.sleep(0.3)


def send_enter():
    """Press Enter key."""
    VK_RETURN = 0x0D
    user32.keybd_event(VK_RETURN, 0, 0, 0)
    user32.keybd_event(VK_RETURN, 0, 2, 0)


def select_all():
    """Ctrl+A to select all text in the active area."""
    VK_CONTROL = 0x11
    VK_A = 0x41
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_A, 0, 0, 0)
    user32.keybd_event(VK_A, 0, 2, 0)
    user32.keybd_event(VK_CONTROL, 0, 2, 0)
    time.sleep(0.1)


def copy_selection():
    """Ctrl+C to copy selection."""
    VK_CONTROL = 0x11
    VK_C = 0x43
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_C, 0, 0, 0)
    user32.keybd_event(VK_C, 0, 2, 0)
    user32.keybd_event(VK_CONTROL, 0, 2, 0)
    time.sleep(0.2)


def get_clipboard():
    """Get current clipboard text."""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard"],
        capture_output=True, text=True, timeout=5
    )
    return result.stdout.strip()


# ════════════════════════════════════════════════════════
# DRIFT DETECTION
# ════════════════════════════════════════════════════════
def detect_drift(prompt, response):
    """Check response for drift patterns. Returns list of drift flags."""
    drift = []
    rlower = response.lower()
    plower = prompt.lower()
    
    fabrication_signals = [
        (r'\bmorphyeo\b', 'morphyeo'),
        (r'\bgoose.bridge\b', 'goose bridge'),
        (r'\bfable.session\b', 'fable session'),
        (r'\bowl.alpha\b', 'owl alpha'),
        (r'\bclaude.pro\b', 'claude pro'),
        (r'\bbuilders.barbecue\b', 'builders barbecue'),
        (r'\bopenai\b', 'OpenAI'),
        (r'\bchatgpt\b', 'ChatGPT'),
        (r'\bgemini\b', 'Gemini'),
        (r'\banthropic\b', 'Anthropic'),
    ]
    for pattern, name in fabrication_signals:
        if re.search(pattern, rlower):
            drift.append(f"FABRICATION: referenced '{name}' — does not exist here")
    
    if "what is" in plower or "how do" in plower:
        if len(response) < 50:
            drift.append("AVOIDANCE: response too short")
    
    if response.rstrip().endswith(('...', '…')) and len(response) < 200:
        drift.append("PREMATURE_STOP")
    
    process_count = sum(1 for p in ["i am going to", "let me check", "i will now", 
                       "first i will", "step one"] if p in rlower)
    if process_count >= 3:
        drift.append(f"OVER_NARRATION: {process_count} process descriptions")
    
    if not response.strip():
        drift.append("EMPTY_RESPONSE")
    
    return drift


# ════════════════════════════════════════════════════════
# TYPE PROMPT INTO HERMES + READ RESPONSE
# ════════════════════════════════════════════════════════
def send_to_hermes(prompt, timeout=300):
    """
    Find Hermes window, type prompt, wait for response, return it.
    
    Strategy:
    1. Find Hermes window
    2. Click in the input area (bottom of window)
    3. Clear any existing text (Ctrl+A, Delete)
    4. Paste the prompt
    5. Hit Enter
    6. Wait for response to appear
    7. Click in the response area
    8. Select all, copy
    9. Return clipboard content
    
    Returns (response_text, error_or_None)
    """
    hwnd = find_hermes_window()
    if not hwnd:
        return None, "Hermes Desktop window not found. Is it running?"
    
    left, top, right, bottom = get_window_rect(hwnd)
    width = right - left
    height = bottom - top
    
    print(f"[agentaila] Hermes window: {left},{top} {width}x{height}")
    
    # Bring to front
    set_foreground(hwnd)
    time.sleep(0.5)
    
    # Click in the input area (bottom-center of the window)
    # Hermes input is typically in the bottom ~100px
    input_x = left + width // 2
    input_y = bottom - 60  # ~60px from bottom
    send_click(input_x, input_y)
    time.sleep(0.3)
    
    # Clear existing text
    select_all()
    time.sleep(0.1)
    # Type the prompt
    send_keys(prompt)
    time.sleep(0.3)
    
    # Send
    send_enter()
    print(f"[agentaila] Prompt sent. Waiting for response...")
    
    # Wait for response — LOOP AGENT heartbeat
    start = time.time()
    prev_clip = ""
    response = ""
    stable_count = 0
    last_heartbeat = 0
    
    while time.time() - start < timeout:
        time.sleep(3)
        
        # LOOP AGENT heartbeat: say "still working" every 30 seconds
        now = time.time()
        if now - last_heartbeat > 30:
            narrate("Still working.")
            last_heartbeat = now
        
        # Click in response area (center of window)
        resp_x = left + width // 2
        resp_y = top + height // 2
        send_click(resp_x, resp_y)
        time.sleep(0.2)
        
        # Select response area
        select_all()
        time.sleep(0.1)
        copy_selection()
        time.sleep(0.1)
        
        current = get_clipboard()
        
        # Check if response has stabilized (same content for 2 consecutive reads)
        if current == prev_clip and len(current) > len(prompt) + 50:
            stable_count += 1
            if stable_count >= 2:
                # Extract just the response (remove the prompt if it's echoed)
                response = current
                break
        else:
            stable_count = 0
            prev_clip = current
        
        # If we have substantial content that's growing slowly, we're close
        if len(current) > 500 and len(current) > len(response):
            response = current
    
    if not response or len(response) < 20:
        return None, "Timed out waiting for Hermes response"
    
    # Clean up — remove the echoed prompt from the response if present
    if prompt in response:
        idx = response.find(prompt)
        response = response[idx + len(prompt):].strip()
    
    return response, None


# ════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="Agent AILA — Screen-mapped Hermes proxy")
    parser.add_argument("--prompt", required=True, help="Trent's message to forward to Hermes")
    parser.add_argument("--done-token", required=True, help="Dispatch token")
    parser.add_argument("--timeout", type=int, default=300, help="Max wait for response")
    args = parser.parse_args()

    prompt = args.prompt
    token = args.done_token
    timeout = args.timeout

    print(f"\n{'='*60}")
    print(f"  Agent AILA — Screen-Mapped Hermes Proxy")
    print(f"  Prompt: {prompt[:120]}")
    print(f"{'='*60}\n")

    narrate("Agent AILA online. Forwarding to Hermes.")

    # Send to Hermes
    response, error = send_to_hermes(prompt, timeout=timeout)

    if error:
        print(f"ERROR: {error}")
        narrate(f"Agent AILA error. {error}")
        write_done_token(token)
        sys.exit(1)

    # Drift detection
    drift_flags = detect_drift(prompt, response)

    if drift_flags:
        drift_summary = "Drift detected. " + ". ".join(drift_flags) + "."
        print(f"DRIFT: {drift_summary}")
        narrate(drift_summary)

    print(f"RESPONSE ({len(response)} chars):")
    print(response[:800])
    if len(response) > 800:
        print("...")

    narrate(response)

    write_done_token(token)
    print(f"\nAgent AILA complete. WE WORK HERE.")


def write_done_token(token):
    done_file = NARRATION_QUEUE / f"done_{token}.json"
    done_file.write_text(json.dumps({
        "text": "Agent AILA complete.",
        "source": "aila",
        "engine": "piper",
        "tool": "agentaila",
        "ts": datetime.now().isoformat()
    }), encoding="utf-8")


if __name__ == "__main__":
    main()
