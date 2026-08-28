# LEGEND: browse | <url> | Open URL in existing Chrome session reusing cookies, read page text via clipboard with anti-detection
"""
COOKIEMONSTER.py   Saves ours, eats theirs.
Browse any site using the existing logged-in Chrome session.
Uses CDP (Chrome DevTools Protocol) on port 9222 to open tabs
in the ALREADY RUNNING Chrome instance. No blind Popen launches.

Falls back to Popen only if CDP is completely unreachable.
"""
from pathlib import Path
import subprocess
import time
import random
import json
import socket
import requests

# === MORPHY DYNAMIC PORTABILITY BLOCK v1 (portable edition) ===
# Bundle-relative: everything this tool needs lives beside it.
# === END MORPHY BLOCK ===

# ── Configuration ────────────────────────────────────────────
CDP_PORT = 9222
CDP_HOST = "127.0.0.1"
CDP_BASE = f"http://{CDP_HOST}:{CDP_PORT}"

ACCOUNTS = [
    # The session account is whatever is logged into the user's own Chrome.
    # No accounts are hardcoded — this list stays empty by design.
]

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
]

# Anti-detection settings
MIN_DELAY_RELATED = 30
MIN_DELAY_UNRELATED = 5
RANDOM_DELAY_MAX = 10
MAX_PAGES_PER_SESSION = 10
SCROLL_BEFORE_READ = True


class CookieMonster:
    """Ride the existing Chrome session via CDP. Save ours, eat theirs."""

    def __init__(self):
        self.chrome_path = self._find_chrome()
        self._visit_count = 0
        self._last_url = None
        self._last_visit_time = 0
        self._account_index = 0

    def _find_chrome(self) -> str:
        for c in CHROME_PATHS:
            if Path(c).exists():
                return c
        return None

    def _cdp_alive(self) -> bool:
        try:
            with socket.create_connection((CDP_HOST, CDP_PORT), timeout=2):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    def _cdp_get_tabs(self) -> list:
        try:
            r = requests.get(f"{CDP_BASE}/json", timeout=3)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[CookieMonster] CDP tab list failed: {e}")
            return []

    def _cdp_new_tab_via_http(self, url: str) -> bool:
        """Open a new tab via CDP HTTP PUT endpoint (Chrome 151 requires PUT for /json/new)."""
        try:
            r = requests.put(f"{CDP_BASE}/json/new?{url}", timeout=5)
            r.raise_for_status()
            data = r.json()
            if data.get("id") or data.get("targetId"):
                print(f"[CookieMonster] CDP HTTP PUT created tab: {url}")
                return True
            else:
                print(f"[CookieMonster] CDP HTTP PUT unexpected response: {data}")
                return False
        except Exception as e:
            print(f"[CookieMonster] CDP HTTP PUT failed: {e}")
            return False

    def _cdp_new_tab_via_ws(self, url: str) -> bool:
        """Open a new tab via CDP by sending a createTarget command to an existing tab's DevTools WebSocket.
        Chrome 151 blocks browser-level WS, but tab-level WS can still create targets."""
        tabs = self._cdp_get_tabs()
        # Try any tab that has a WebSocket URL
        for tab in tabs:
            ws_url = tab.get("webSocketDebuggerUrl", "")
            if not ws_url:
                continue
            try:
                import websocket
                ws = websocket.create_connection(ws_url, timeout=5)
                cmd = {
                    "id": int(time.time() * 1000) % 100000,
                    "method": "Target.createTarget",
                    "params": {
                        "url": url,
                        "newWindow": False,
                        "background": False
                    }
                }
                ws.send(json.dumps(cmd))
                resp = ws.recv()
                ws.close()
                data = json.loads(resp)
                if "result" in data and data["result"].get("targetId"):
                    print(f"[CookieMonster] CDP tab-WS created target: {url}")
                    return True
            except Exception:
                continue
        return False

    def _cdp_navigate_existing(self, url: str) -> bool:
        """Navigate the first non-chrome-internal tab to the URL."""
        tabs = self._cdp_get_tabs()
        for tab in tabs:
            tab_url = tab.get("url", "")
            if tab_url.startswith("chrome://") or tab_url.startswith("about:"):
                continue
            ws_url = tab.get("webSocketDebuggerUrl", "")
            if not ws_url:
                continue
            try:
                import websocket
                ws = websocket.create_connection(ws_url, timeout=5)
                ws.send(json.dumps({
                    "id": 1,
                    "method": "Page.navigate",
                    "params": {"url": url}
                }))
                resp = ws.recv()
                ws.close()
                print(f"[CookieMonster] CDP navigated existing tab to: {url}")
                return True
            except Exception:
                continue
        return False

    def _fallback_popen(self, url: str):
        if self.chrome_path:
            subprocess.Popen([self.chrome_path, url])
            print(f"[CookieMonster] Fallback Popen: {url}")

    def _wait_if_related(self, url: str):
        now = time.time()
        if self._last_url and self._is_related(url, self._last_url):
            elapsed = now - self._last_visit_time
            if elapsed < MIN_DELAY_RELATED:
                wait = MIN_DELAY_RELATED - elapsed
                print(f"[CookieMonster] Related site - waiting {wait:.0f}s...")
                time.sleep(wait)
        else:
            delay = MIN_DELAY_UNRELATED + random.uniform(0, RANDOM_DELAY_MAX)
            time.sleep(delay)

    def _is_related(self, url1: str, url2: str) -> bool:
        from urllib.parse import urlparse
        d1 = urlparse(url1).netloc.lower()
        d2 = urlparse(url2).netloc.lower()
        return d1 == d2

    def _rotate_account(self):
        if len(ACCOUNTS) > 1:
            self._account_index = (self._account_index + 1) % len(ACCOUNTS)
            print(f"[CookieMonster] Using account: {ACCOUNTS[self._account_index]}")

    def visit(self, url: str, account: str = None, read: bool = True) -> str:
        """
        Open URL in the EXISTING Chrome session via CDP.
        Strategy:
          1. Try /json/new via PUT (Chrome 151 requirement)
          2. Try tab-level WebSocket Target.createTarget
          3. Navigate an existing tab
          4. Last resort: subprocess.Popen
        Returns page text if read=True.
        """
        if self._visit_count >= MAX_PAGES_PER_SESSION:
            print("[CookieMonster] Max pages reached - rotating account or stopping.")
            self._visit_count = 0
            self._rotate_account()

        self._wait_if_related(url)
        print(f"[CookieMonster] Visiting: {url}")

        opened = False

        if self._cdp_alive():
            # Strategy 1: HTTP PUT /json/new endpoint
            if not self._cdp_new_tab_via_http(url):
                # Strategy 2: Tab-level WebSocket createTarget
                if not self._cdp_new_tab_via_ws(url):
                    # Strategy 3: Navigate existing tab
                    if not self._cdp_navigate_existing(url):
                        print("[CookieMonster] All CDP methods failed, falling back to Popen")
                        self._fallback_popen(url)
            else:
                opened = True
        else:
            print(f"[CookieMonster] CDP port {CDP_PORT} not reachable, falling back to Popen")
            self._fallback_popen(url)

        time.sleep(4)
        self._last_url = url
        self._last_visit_time = time.time()
        self._visit_count += 1

        if read:
            return self.read_page()
        return ""

    def read_page(self) -> str:
        """Extract text from the current page via clipboard."""
        try:
            import pyautogui
            if SCROLL_BEFORE_READ:
                pyautogui.scroll(-3)
                time.sleep(0.5)
                pyautogui.scroll(-3)
                time.sleep(0.3)

            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.5)
            pyautogui.hotkey('ctrl', 'c')
            time.sleep(0.5)
            import pyperclip
            text = pyperclip.paste()
            pyautogui.press('escape')
            return text if text else ""
        except ImportError:
            print("[CookieMonster] pyautogui/pyperclip not available for read")
            return ""
        except Exception as e:
            print(f"[CookieMonster] Read failed: {e}")
            return ""


if __name__ == "__main__":
    import sys
    from pathlib import Path as _P

    # SimpleTool contract: --done-token required. done_<token>.json written last.
    def _write_done_token(token):
        queue = _P(__file__).resolve().parent.parent / "morphytrek_data" / "queue"
        try:
            queue.mkdir(parents=True, exist_ok=True)
            (queue / f"done_{token}.json").write_text(
                json.dumps({"done": True, "source": "cookie_monster"}), encoding="utf-8")
        except Exception as e:
            print(f"[CookieMonster] done-token write failed: {e}")

    token = None
    args = [a for a in sys.argv[1:]]
    if "--done-token" in args:
        i = args.index("--done-token")
        if i + 1 < len(args):
            token = args[i + 1]
            args = args[:i] + args[i + 2:]

    if not args:
        print("Usage: python cookie_monster.py <url> [--done-token <token>]")
        sys.exit(1)

    url = args[0]
    cm = CookieMonster()
    text = cm.visit(url, read=True)
    if text:
        print(f"\n--- Page Content ({len(text)} chars) ---")
        print(text[:3000])
        if len(text) > 3000:
            print(f"... ({len(text) - 3000} more chars)")
    else:
        print("No content extracted.")
    if token:
        _write_done_token(token)
