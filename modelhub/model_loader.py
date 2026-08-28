"""
SIMPLEJACK MODEL HUB  -  MorPHYes Model Hub
============================================
One localhost OpenAI-compatible endpoint. Every app (SimpleJack, Hermes,
ZCode, Cursor, Codex) adds ONE custom provider:

    name:        simpleJACK
    base_url:    http://127.0.0.1:8123/v1
    api_key:     <leaving this blank works>  keys live HERE, offline, in the provider vault

/v1/models returns ONLY the live curated modelcards - no graveyard.
Cards are added/edited/archived through the HTML page at http://127.0.0.1:8123/
Registry hot-reloads from model_loader.json on every request.

ARCHIVE RULES (per card):
  - "archive"  pops the card OUT of /v1/models. It lands in the Archived
    section at the bottom of the page. Returning is OPTIONAL - click
    "pop back in" any time.
  - "reset_spec" is an OPTIONAL auto-return rule: once / weekly / monthly.
    When an archived card's reset time arrives, it un-archives itself.
  - "archive_until" (credit-window) is the OPPOSITE direction: the card
    stays LIVE until that date, then drops out by itself.

MORPHYES MASTER CONTROLS (per-user, applied at the source):
  - temperature = truth <-> creativity dial. LOW = stick to facts,
    HIGH = creative. The hub overrides whatever apps send, always.
  - simplicity = 0..25 (0 = OFF). 5 = child, 25 = expert. The hub
    injects the level instruction into every request's system message.
  - rate_rpm = engineered ceiling. The hub paces requests to this RPM
    so bursts slow to a reasonable rate instead of slamming the free
    tier wall (~40 RPM typical). Slide it until the wall stops hurting.

PROVIDER VAULT (bottom of page):
  Each provider is a card. chat_url + api_key (optional) are stored
  OFFLINE in model_loader.json. Blank key = local/anon, works anyway.

Keys are resolved, in order:
  1. model_loader.json  providers.<p>.api_key   (the vault)
  2. config\\keys.local.json  (zenmux / zai / openrouter ...)
  3. Hermes config.yaml  (AppData\\Local\\hermes\\config.yaml)
  4. Environment variable

WE WORK HERE.
"""

import asyncio
import calendar
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse

# ------------------------------------------------------------------ Paths
BASE_DIR        = Path(__file__).resolve().parent
REGISTRY_FILE   = BASE_DIR / "model_loader.json"
LOGO_FILE       = BASE_DIR / "logo.jpg"
SIMPLEJACK_KEYS = BASE_DIR.parent / "config" / "keys.local.json"
HERMES_YAML     = Path.home() / "AppData" / "Local" / "hermes" / "config.yaml"

app = FastAPI(title="MorPHYes Model Hub")

# ------------------------------------------------------------ Registry IO
def load_registry() -> dict:
    try:
        return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"providers": {}, "cards": [], "user_prefs": {}}

def save_registry(reg: dict) -> None:
    REGISTRY_FILE.write_text(json.dumps(reg, indent=2), encoding="utf-8")

def card_hidden(card: dict) -> bool:
    """True if the card should NOT appear in /v1/models: manually archived
    OR credit-window (archive_until) date passed."""
    if card.get("archived"):
        return True
    au = card.get("archive_until")
    if not au:
        return False
    try:
        return date.today() >= date.fromisoformat(str(au)[:10])
    except Exception:
        return False

# --------------------------------------------------------- Key resolution
def _keys_local() -> dict:
    try:
        return json.loads(SIMPLEJACK_KEYS.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _ensure_keys_dir() -> None:
    """Bundle-relative config/ may not exist on a fresh install — create it
    before the first key write so save_keys_local never fails on a new
    machine. Idempotent; no-op when the folder is already there."""
    try:
        SIMPLEJACK_KEYS.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

def _hermes_yaml_blocks() -> dict:
    """Parse Hermes config.yaml blocks at ANY nesting depth.

    Hermes stores providers under `providers:` (children at indent 2, their
    keys at indent 4). The old parser only read keys directly under a
    top-level block, so nested provider api_keys were invisible -> cards
    fired with no Authorization header -> upstream 401. Fix: track the
    indent stack; when we hit `base_url`/`api_key` under a provider-looking
    block, record it under the innermost provider name.
    """
    blocks: dict = {}
    try:
        text = HERMES_YAML.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return blocks
    stack: list = []  # (indent, name)
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        # pop stack entries deeper-or-equal to this line's indent
        while stack and stack[-1][0] >= indent:
            stack.pop()
        m = re.match(r"^([A-Za-z0-9_.-]+):\s*(?:#.*)?$", line.strip())
        if m:
            stack.append((indent, m.group(1)))
            continue
        m2 = re.match(r"^(base_url|api_key):\s*(.+)$", line.strip())
        if m2 and stack:
            # attribute belongs to the innermost named block
            owner = stack[-1][1]
            blocks.setdefault(owner, {})[m2.group(1)] = m2.group(2).strip().strip("'\"")
    return blocks

def resolve_key_src(provider: str):
    """Return (key, source). Source tells the vault where the real key
    actually lives so the UI never lies about keys being absent."""
    reg = load_registry()
    p = reg.get("providers", {}).get(provider, {})
    if p.get("api_key"):
        return p["api_key"], "vault"
    kl = _keys_local().get(provider)
    if isinstance(kl, dict) and kl.get("api_key"):
        return kl["api_key"], "keys.local.json"
    if isinstance(kl, str) and kl:
        return kl, "keys.local.json"
    hb = _hermes_yaml_blocks().get(provider, {})
    if hb.get("api_key"):
        return hb["api_key"], "hermes yaml"
    ek = os.environ.get(provider.upper() + "_API_KEY", "")
    return ek, ("env" if ek else "none")

def resolve_key(provider: str) -> str:
    key, _ = resolve_key_src(provider)
    return key

def save_keys_local(provider: str, key: str) -> bool:
    """Write a key into the canonical offline store every agent reads.
    Bare-string shape, matching the existing keys.local.json file."""
    try:
        _ensure_keys_dir()
        kl = _keys_local()
        if not isinstance(kl, dict):
            kl = {}
        kl[provider] = str(key).strip()
        SIMPLEJACK_KEYS.write_text(json.dumps(kl, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        print(f"[MODEL HUB] save_keys_local({provider}) failed: {e}")
        return False

# ------------------------------------------------------------- Card utils
def find_card(reg: dict, alias: str) -> Optional[dict]:
    for c in reg.get("cards", []):
        if c.get("id", "").lower() == alias.lower():
            return c
    return None

def chat_url_for(provider: str) -> str:
    reg = load_registry()
    p = reg.get("providers", {}).get(provider, {})
    return p.get("chat_url", "")

# --------------------------------------------- Archive / auto-return clock
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}

def next_occurrence(spec: dict, after: Optional[datetime] = None) -> Optional[datetime]:
    """Next datetime when an archived card should revive itself, from a
    reset_spec: {"freq":"once|weekly|monthly", "time":"HH:MM",
                 "dow":"friday", "dom":1}."""
    if not spec or not isinstance(spec, dict):
        return None
    freq = str(spec.get("freq") or "none").lower()
    if freq not in ("once", "weekly", "monthly"):
        return None
    now = after or datetime.now()
    try:
        hh, mm = (int(x) for x in str(spec.get("time") or "17:00").split(":"))
    except Exception:
        hh, mm = 17, 0

    if freq == "once":
        d = spec.get("date")
        if not d:
            return None
        try:
            return datetime.fromisoformat(str(d))
        except Exception:
            return None

    if freq == "weekly":
        target = WEEKDAYS.get(str(spec.get("dow") or "friday").lower(), 4)
        base = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        nxt = base + timedelta(days=(target - base.weekday()) % 7)
        if nxt <= now:
            nxt += timedelta(days=7)
        return nxt

    # monthly
    dom = int(spec.get("dom") or 1)
    y, m = now.year, now.month
    maxd = calendar.monthrange(y, m)[1]
    nxt = datetime(y, m, min(dom, maxd), hh, mm)
    if nxt <= now:
        m += 1
        if m > 12:
            m, y = 1, y + 1
        maxd = calendar.monthrange(y, m)[1]
        nxt = datetime(y, m, min(dom, maxd), hh, mm)
    return nxt

def run_archive_clock(reg: dict) -> bool:
    """Un-archive any card whose reset time has arrived. Returns True when
    the registry changed and must be saved."""
    changed = False
    now = datetime.now()
    for c in reg.get("cards", []):
        if not c.get("archived"):
            continue
        rn = c.get("reset_next")
        if not rn:
            continue
        try:
            due = datetime.fromisoformat(str(rn))
        except Exception:
            continue
        if now >= due:
            c["archived"] = False
            c["reset_next"] = None
            spec = c.get("reset_spec") or {}
            if str(spec.get("freq") or "").lower() in ("weekly", "monthly"):
                nxt = next_occurrence(spec, after=now)
                if nxt:
                    c["reset_next"] = nxt.isoformat(timespec="minutes")
            changed = True
            print(f"[MODEL HUB] auto-return: {c.get('id')} came back")
    return changed

# ----------------------------------------------- MorPHYes Master Controls
RATING_BANDS = {
    1: ("family safe / G-PG: no violence, gore, swearing, sexual themes, or illegal how-to", "G/PG"),
    2: ("PG-13: mild conflict ok, no graphic violence, no explicit sex, no hard-drug detail", "PG-13"),
    3: ("R: adult themes allowed, no gratuitous gore, no graphic sexual detail", "R"),
    4: ("NC-17: adults only", "NC-17"),
}

DEFAULT_PREFS = {"temperature": 0.7, "simplicity": 12, "rate_rpm": 40,
                 "impose_temp": True, "impose_simple": True, "impose_rate": True,
                 "content_rating": 0, "impose_content_rating": False,
                 "gpu_util_max": 60, "gpu_vram_max_mb": 6000}

def load_prefs(reg: dict) -> dict:
    p = reg.get("user_prefs") or {}
    merged = dict(DEFAULT_PREFS)
    for k in p:
        merged[k] = p[k]
    try:
        merged["temperature"] = max(0.0, min(2.0, float(merged.get("temperature", 0.7))))
    except Exception:
        merged["temperature"] = 0.7
    try:
        merged["simplicity"] = max(0, min(25, int(merged.get("simplicity", 12))))
    except Exception:
        merged["simplicity"] = 12
    try:
        merged["rate_rpm"] = max(0, min(120, int(merged.get("rate_rpm", 40))))
    except Exception:
        merged["rate_rpm"] = 40
    for k in ("impose_temp", "impose_simple", "impose_rate"):
        merged[k] = bool(merged.get(k, True))
    merged["impose_content_rating"] = bool(merged.get("impose_content_rating", False))
    try:
        merged["content_rating"] = max(0, min(18, int(merged.get("content_rating", 0))))
    except Exception:
        merged["content_rating"] = 0
    try:
        merged["gpu_util_max"] = max(10, min(99, int(merged.get("gpu_util_max", 60))))
    except Exception:
        merged["gpu_util_max"] = 60
    try:
        merged["gpu_vram_max_mb"] = max(500, min(8192, int(merged.get("gpu_vram_max_mb", 6000))))
    except Exception:
        merged["gpu_vram_max_mb"] = 6000
    return merged

def apply_master_prefs(payload: dict, prefs: dict) -> None:
    """Inject the user's master settings into an upstream request.
    Each master is a TOGGLE first: imposed = applied, off = the model
    keeps its own default and this hub stays out of the way."""
    if prefs.get("impose_temp", True):
        payload["temperature"] = prefs.get("temperature", 0.7)
    else:
        payload.pop("temperature", None)
    if prefs.get("impose_simple", True):
        simp = int(prefs.get("simplicity") or 0)
        if 5 <= simp <= 25:
            rule = (f"[MorPHYes simplicity level {simp} of 25. 5 = explain like I am a "
                    f"small child, no jargon. 25 = dense expert language. Match this "
                    f"clarity level exactly for THIS reply.]")
            _inject_system_rule(payload, rule)
    # Content rating (parental control): motion-picture scale, toggle-gated.
    if prefs.get("impose_content_rating", False):
        rating = int(prefs.get("content_rating") or 0)
        if rating >= 1:
            band, label = RATING_BANDS.get(min(rating // 6 + 1, 4), RATING_BANDS[4])
            rule = (f"[MorPHYes content rating: {label}. "
                    f"Filter this reply to this rating: {band}.]")
            _inject_system_rule(payload, rule)


def _inject_system_rule(payload: dict, rule: str) -> None:
    msgs = payload.get("messages")
    if isinstance(msgs, list) and msgs:
        first = msgs[0]
        if isinstance(first, dict) and first.get("role") == "system":
            msgs[0] = {**first, "content": str(first.get("content", "")) + "\n" + rule}
        else:
            msgs.insert(0, {"role": "system", "content": rule})

# ------------------------------------------- Engineered rate limiter (RPM)
class TokenBucket:
    """Leaky-bucket RPM ceiling. Bursts slow to the ceiling instead of
    slamming the free-tier wall. 0 = unlimited."""

    def __init__(self):
        self._tokens = 1
        self._last = time.monotonic()
        self._rpm = 40
        self._lock = asyncio.Lock()

    def set_rpm(self, rpm: int) -> None:
        try:
            self._rpm = max(0, min(120, int(rpm)))
        except Exception:
            self._rpm = 40

    def _refill(self) -> None:
        now = time.monotonic()
        if self._rpm > 0:
            self._tokens = min(self._rpm, self._tokens + (now - self._last) * self._rpm / 60.0)
        else:
            self._tokens = 1
        self._last = now

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1.0 - self._tokens) * 60.0 / max(1, self._rpm)
            await asyncio.sleep(min(wait, 30.0))


_bucket = TokenBucket()

# ------------------------------------------- GPU GUARD (Ollama-only gate)
# The tools are the ONLY things allowed to talk to Ollama, and only when
# the GPU has headroom. When GPU utilisation or VRAM crosses the busy
# line, ollama/ollama_cloud requests through this hub WAIT (up to a
# patience window) then bounce with HTTP 429 "GPU busy". Cloud providers
# are untouched - they never touch the GPU.
GPU_GUARD_POLL = 1.0        # seconds between nvidia-smi polls while waiting
GPU_GUARD_TIMEOUT = 20.0    # seconds an ollama request waits before 429
_gpu_state = {"busy": False, "util": 0, "vram": 0, "checked": 0.0}

def gpu_busy() -> dict:
    """Poll nvidia-smi (max once per second, cached) for util + VRAM."""
    now = time.monotonic()
    if now - _gpu_state["checked"] < GPU_GUARD_POLL:
        return _gpu_state
    util, vram = 0, 0
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip().splitlines()
        if out:
            parts = out[0].split(",")
            util = int(re.sub(r"[^0-9]", "", parts[0]) or 0)
            vram = int(re.sub(r"[^0-9]", "", parts[1]) or 0)
    except Exception:
        util, vram = 0, 0   # no GPU / no nvidia-smi -> never block
    prefs = load_prefs(load_registry())
    util_max = int(prefs.get("gpu_util_max", 60))
    vram_max = int(prefs.get("gpu_vram_max_mb", 6000))
    _gpu_state.update(busy=(util >= util_max or vram >= vram_max),
                      util=util, vram=vram, checked=now)
    return _gpu_state

async def gpu_gate(provider: str):
    """Hold ollama-bound requests while the GPU is busy. Returns a
    JSONResponse(429) if patience runs out, else None (clear to go)."""
    if provider not in ("ollama", "ollama_cloud"):
        return None
    deadline = time.monotonic() + GPU_GUARD_TIMEOUT
    while gpu_busy()["busy"]:
        if time.monotonic() >= deadline:
            st = gpu_busy()
            print(f"[MODEL HUB] GPU GUARD: {provider} bounced - GPU busy "
                  f"(util={st['util']}% vram={st['vram']}MB)")
            return JSONResponse(
                {"error": {"message": (
                    f"GPU busy (util {st['util']}%, VRAM {st['vram']}MB). "
                    f"Ollama is held back so the GPU owner keeps it. "
                    f"Retry shortly or pick a cloud card.")}},
                status_code=429)
        await asyncio.sleep(GPU_GUARD_POLL)
    return None

# -------------------------------------------------------- OpenAI-compat API
@app.get("/v1/models")
def list_models():
    reg = load_registry()
    if run_archive_clock(reg):
        save_registry(reg)
    data = [
        {
            "id": c["id"],
            "object": "model",
            "created": int(c.get("created", 0)),
            "owned_by": c.get("provider", ""),
            "context_length": c.get("context"),
            "cost": c.get("cost", ""),
            "label": c.get("label", ""),
        }
        for c in reg.get("cards", [])
        if c.get("enabled", True) and not card_hidden(c)
    ]
    return {"object": "list", "data": data}

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "invalid JSON body"}}, status_code=400)

    model_alias = body.get("model", "")
    reg = load_registry()
    card = find_card(reg, model_alias)
    if not card:
        return JSONResponse(
            {"error": {"message": f"unknown model '{model_alias}'. Cards: "
                                   + ", ".join(c["id"] for c in reg.get("cards", []))}},
            status_code=404,
        )
    if card_hidden(card):
        return JSONResponse(
            {"error": {"message": f"model '{model_alias}' is archived. "
                                  f"Pop it back in from the hub to use it."}},
            status_code=404,
        )

    provider = card["provider"]
    upstream = card.get("upstream", model_alias)
    api_key  = resolve_key(provider)
    url      = chat_url_for(provider)
    if not url:
        return JSONResponse({"error": {"message": f"no chat_url for provider '{provider}'"}},
                            status_code=500)

    prefs = load_prefs(reg)
    # ── WEIGHT GATE (Trent 2026-08-25) — THE BRIDLE ──────────────────
    # The hub is the one choke point every model passes through. It does
    # not care what a payload says, only what it weighs. LAW 16/17: one
    # flat working room — 100K. Any request heavier is refused honestly:
    # "compress first." No app ships a stuffed window through this hub.
    # Compact estimate: ~4 chars/token on message text.
    _est_tokens = 0
    for _m in body.get("messages", []):
        _role = _m.get("role", "")
        # The bridle weighs the REAL conversation only. The system prompt
        # and tool schemas are scaffolding, not conversation — Trent 2026-08-26.
        if _role == "system":
            continue
        _c = _m.get("content") or ""
        if isinstance(_c, str):
            _est_tokens += len(_c) // 4
        elif isinstance(_c, list):
            for _part in _c:
                if isinstance(_part, dict):
                    _est_tokens += len(str(_part.get("text", ""))) // 4
    _ctx_cap = int(card.get("context") or 200000)
    _room = min(100000, _ctx_cap)
    if _est_tokens > _room:
        return JSONResponse(
            {"error": {"message": (
                f"WEIGHT GATE: request is ~{_est_tokens} tokens, over the "
                f"{_room} flat room (LAW 16/17). The hub refuses to deliver a "
                f"stuffed window. COMPRESS FIRST, then resend. Any model can "
                f"take over for any model only if the payload fits the room."
            ), "code": "payload_too_heavy", "estimated_tokens": _est_tokens,
               "room": _room}}, status_code=413)
    # GPU GUARD: ollama traffic yields to whoever owns the GPU right now
    gate = await gpu_gate(provider)
    if gate is not None:
        return gate
    # engineered ceiling: pace ourselves under the wall (toggleable switch)
    if prefs.get("impose_rate", True) and prefs.get("rate_rpm"):
        _bucket.set_rpm(prefs.get("rate_rpm", 40))
        await _bucket.acquire()

    payload = dict(body)
    payload["model"] = upstream
    apply_master_prefs(payload, prefs)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        print(f"[MODEL HUB] {model_alias} -> {provider}/{upstream} no key (local/anon)")
    is_stream = bool(body.get("stream", False))
    print(f"[MODEL HUB] {model_alias} -> {provider}/{upstream} stream={is_stream} "
          f"temp={payload.get('temperature')} rpm={prefs.get('rate_rpm')}")

    try:
        client = httpx.AsyncClient(timeout=600.0)
        req = client.build_request("POST", url, json=payload, headers=headers)

        if is_stream:
            async def stream_pass():
                # 2026-08-23 FIX: client.stream() takes (method, url), not a
                # pre-built Request. The old client.stream(req) form raised
                # TypeError on EVERY streamed call — 36/36 crashes in
                # model_hub.log. The hub has never streamed a single token.
                #
                # 2026-08-24 — THE OX FIX. An upstream drop mid-stream used to
                # kill this generator, cutting the SSE response with no
                # terminator; every client saw "Response ended prematurely" as
                # a raw CLOUD ERROR (log 16:35/17:04 oxalpha, 17:06 v4pro —
                # two providers, same signature). Now: one retry if nothing
                # was sent yet; if bytes already went out, terminate the SSE
                # stream CLEANLY with an error event so the client can react
                # honestly instead of reading a broken pipe.
                sent_any = False
                try:
                    for attempt in (1, 2):
                        try:
                            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                                if resp.status_code != 200:
                                    detail = (await resp.aread()).decode("utf-8", errors="ignore")[:200]
                                    print(f"[MODEL HUB] upstream {resp.status_code} attempt {attempt}: {detail}")
                                    if attempt == 1:
                                        continue
                                    yield ("data: " + json.dumps(
                                        {"error": {"message": f"upstream {resp.status_code}: {detail}"}}
                                    ) + "\n\n").encode()
                                    return
                                async for chunk in resp.aiter_bytes():
                                    sent_any = True
                                    yield chunk
                            return
                        except Exception as e:
                            print(f"[MODEL HUB] stream drop attempt {attempt} "
                                  f"(sent_any={sent_any}): {e!r}")
                            if sent_any or attempt == 2:
                                yield ("data: " + json.dumps(
                                    {"error": {"message": f"upstream stream dropped: {str(e)[:160]}"}}
                                ) + "\n\n").encode()
                                return
                finally:
                    await client.aclose()
            return StreamingResponse(stream_pass(), media_type="text/event-stream")

        resp = await client.send(req)
        content = await resp.aread()
        await client.aclose()
        return JSONResponse(
            json.loads(content.decode("utf-8", errors="ignore")),
            status_code=resp.status_code,
        )
    except Exception as e:
        return JSONResponse({"error": {"message": f"upstream failure: {e}"}}, status_code=502)

# ---------------------------------------------------------- Management API
@app.get("/api/cards")
def api_cards():
    reg = load_registry()
    if run_archive_clock(reg):
        save_registry(reg)
    for c in reg.get("cards", []):
        c["hidden"] = card_hidden(c)
    base = {name: p.get("chat_url", "") for name, p in reg.get("providers", {}).items()}
    return {"providers": list(reg.get("providers", {}).keys()), "cards": reg.get("cards", []),
            "_base": base, "prefs": load_prefs(reg)}

@app.post("/api/cards")
async def api_add_card(request: Request):
    try:
        card = await request.json()
    except Exception:
        return JSONResponse({"error": "bad JSON"}, status_code=400)
    card.setdefault("created", int(time.time()))
    card.setdefault("label", "")
    card.setdefault("cost", "")
    card.setdefault("context", None)
    card.setdefault("enabled", True)
    card.setdefault("note", "")
    card.setdefault("archive_until", None)
    card.setdefault("archived", False)
    card.setdefault("reset_spec", None)
    card.setdefault("reset_next", None)
    if not card.get("id") or not card.get("provider"):
        return JSONResponse({"error": "id and provider required"}, status_code=400)
    if not card.get("upstream"):
        return JSONResponse({"error": "upstream model required"}, status_code=400)
    reg = load_registry()
    if find_card(reg, card["id"]):
        return JSONResponse({"error": f"card '{card['id']}' already exists"}, status_code=409)
    reg.setdefault("cards", []).append(card)
    save_registry(reg)
    return JSONResponse({"ok": True, "card": card})

@app.patch("/api/cards/{card_id}")
async def api_patch_card(card_id: str, request: Request):
    try:
        patch = await request.json()
    except Exception:
        return JSONResponse({"error": "bad JSON"}, status_code=400)
    reg = load_registry()
    card = find_card(reg, card_id)
    if not card:
        return JSONResponse({"error": f"card '{card_id}' not found"}, status_code=404)
    for k, v in patch.items():
        card[k] = v
    save_registry(reg)
    return JSONResponse({"ok": True, "card": card})

@app.delete("/api/cards/{card_id}")
def api_del_card(card_id: str):
    reg = load_registry()
    before = len(reg.get("cards", []))
    reg["cards"] = [c for c in reg.get("cards", []) if c.get("id", "").lower() != card_id.lower()]
    if len(reg["cards"]) == before:
        return JSONResponse({"error": f"card '{card_id}' not found"}, status_code=404)
    save_registry(reg)
    return JSONResponse({"ok": True})

@app.post("/api/cards/{card_id}/archive")
async def api_archive_card(card_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    reg = load_registry()
    card = find_card(reg, card_id)
    if not card:
        return JSONResponse({"error": f"card '{card_id}' not found"}, status_code=404)
    card["archived"] = True
    spec = body.get("reset_spec")
    if spec is None or spec == "none" or not isinstance(spec, dict):
        card["reset_spec"] = None
        card["reset_next"] = None
    else:
        card["reset_spec"] = spec
        nxt = next_occurrence(spec)
        card["reset_next"] = nxt.isoformat(timespec="minutes") if nxt else None
    save_registry(reg)
    return JSONResponse({"ok": True, "card": card,
                         "reset_next": card.get("reset_next")})

@app.post("/api/cards/{card_id}/unarchive")
def api_unarchive_card(card_id: str):
    reg = load_registry()
    card = find_card(reg, card_id)
    if not card:
        return JSONResponse({"error": f"card '{card_id}' not found"}, status_code=404)
    card["archived"] = False
    card["reset_next"] = None
    # credit-window cards come fully back: clear the window date
    if card.get("archive_until"):
        card["archive_until"] = None
    save_registry(reg)
    return JSONResponse({"ok": True, "card": card})

# ------------------------------------------------------------ Provider vault
@app.get("/api/providers")
def api_providers():
    reg = load_registry()
    out = []
    for name, p in reg.get("providers", {}).items():
        key, src = resolve_key_src(name)
        prow = {"name": name, "chat_url": p.get("chat_url", ""),
                "has_key": bool(key),
                "key_source": src if key else "none",
                "key_tail": str(key)[-4:] if key else ""}
        out.append(prow)
    return {"providers": out}

@app.get("/api/providers/{provider}/key")
def api_provider_key(provider: str):
    reg = load_registry()
    if provider not in reg.get("providers", {}):
        return JSONResponse({"error": f"provider '{provider}' not found"}, status_code=404)
    key, src = resolve_key_src(provider)
    return {"key": key, "key_source": src}

@app.patch("/api/providers/{provider}")
async def api_patch_provider(provider: str, request: Request):
    try:
        patch = await request.json()
    except Exception:
        return JSONResponse({"error": "bad JSON"}, status_code=400)
    reg = load_registry()
    if provider not in reg.get("providers", {}):
        return JSONResponse({"error": f"provider '{provider}' not found"}, status_code=404)
    p = reg["providers"][provider]
    if "chat_url" in patch and patch["chat_url"] is not None:
        p["chat_url"] = str(patch["chat_url"]).strip()
    if "api_key" in patch:
        key = (patch["api_key"] or "").strip()
        if key:
            # canonical store: keys.local.json (every agent reads from here)
            if not save_keys_local(provider, key):
                return JSONResponse({"error": "could not write keys.local.json"}, status_code=500)
            p.pop("api_key", None)  # single source of truth
        else:
            # deleting a key: remove it from both places
            p.pop("api_key", None)
            try:
                _ensure_keys_dir()
                kl = _keys_local()
                if isinstance(kl, dict):
                    kl.pop(provider, None)
                    SIMPLEJACK_KEYS.write_text(json.dumps(kl, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                print(f"[MODEL HUB] key delete failed: {e}")
    save_registry(reg)
    return JSONResponse({"ok": True, "provider": p})

@app.post("/api/providers")
async def api_add_provider(request: Request):
    try:
        prov = await request.json()
    except Exception:
        return JSONResponse({"error": "bad JSON"}, status_code=400)
    name = (prov.get("name") or "").strip()
    chat_url = (prov.get("chat_url") or "").strip()
    if not name or not chat_url:
        return JSONResponse({"error": "name and chat_url required"}, status_code=400)
    reg = load_registry()
    if name in reg.get("providers", {}):
        return JSONResponse({"error": f"provider '{name}' already exists"}, status_code=409)
    entry = {"chat_url": chat_url}
    if prov.get("api_key"):
        entry["api_key"] = str(prov["api_key"]).strip()
    reg.setdefault("providers", {})[name] = entry
    save_registry(reg)
    return JSONResponse({"ok": True, "provider": name, "chat_url": chat_url})

@app.delete("/api/providers/{provider}")
def api_del_provider(provider: str):
    reg = load_registry()
    if provider not in reg.get("providers", {}):
        return JSONResponse({"error": f"provider '{provider}' not found"}, status_code=404)
    cards_using = [c["id"] for c in reg.get("cards", []) if c.get("provider") == provider]
    if cards_using:
        return JSONResponse(
            {"error": f"provider in use by cards: {', '.join(cards_using)}"}, status_code=409)
    del reg["providers"][provider]
    save_registry(reg)
    return JSONResponse({"ok": True})

# ------------------------------------------------- MorPHYes Master Controls
@app.get("/api/prefs")
def api_get_prefs():
    reg = load_registry()
    if run_archive_clock(reg):
        save_registry(reg)
    return {"prefs": load_prefs(reg)}

@app.put("/api/prefs")
async def api_put_prefs(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad JSON"}, status_code=400)
    reg = load_registry()
    cur = dict(reg.get("user_prefs") or {})
    for k in ("temperature", "simplicity", "rate_rpm",
              "impose_temp", "impose_simple", "impose_rate",
              "content_rating", "impose_content_rating",
              "gpu_util_max", "gpu_vram_max_mb"):
        if k in body:
            cur[k] = body[k]
    reg["user_prefs"] = load_prefs({**reg, "user_prefs": cur})
    save_registry(reg)
    return {"ok": True, "prefs": reg["user_prefs"]}

# ------------------------------------------------------- Logo
@app.get("/api/gpu")
def api_gpu():
    """Live GPU state + the guard's current verdict. For the hub UI."""
    st = gpu_busy()
    prefs = load_prefs(load_registry())
    return {"util": st["util"], "vram": st["vram"],
            "busy": st["busy"],
            "util_max": prefs.get("gpu_util_max", 60),
            "vram_max": prefs.get("gpu_vram_max_mb", 6000)}

@app.get("/logo.jpg")
def logo():
    if LOGO_FILE.exists():
        return FileResponse(LOGO_FILE, media_type="image/jpeg")
    return HTMLResponse("", status_code=404)

# ------------------------------------------------------- HTML interface
HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MorPHYes Model Hub</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;500;600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root { --navy:#0f2340; --navy-panel:#0a1a2e; --navy-lift:#112338; --orange:#d97834;
          --orange-hover:#e88a4a; --orange-glow:rgba(217,120,52,.22); --rust:#b8401e;
          --text:#ece6d8; --muted:#8a92a6; --green:#4ade80; --red:#f87171;
          --border:rgba(255,255,255,.08); --border-strong:rgba(255,255,255,.14);
          --bg:#060e18; --font:'Inter',system-ui,sans-serif; --display:'Oswald',sans-serif; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font:15px/1.55 var(--font);
         padding:24px; max-width:1400px; margin:0 auto; }
  header { display:flex; align-items:center; gap:16px; flex-wrap:wrap; margin-bottom:10px; }
  .logo { height:52px; border-radius:8px; border:1px solid var(--border-strong); }
  h1 { font-family:var(--display); font-size:26px; font-weight:700; letter-spacing:.05em;
       text-transform:uppercase; color:var(--orange); }
  .brand-bar { background:var(--navy-panel); border:1px solid var(--border-strong);
               border-left:3px solid var(--orange); border-radius:10px; padding:12px 16px;
               margin-bottom:18px; font-size:13px; color:var(--muted);
               display:grid; grid-template-columns:auto 1fr; gap:4px 18px; max-width:720px; }
  .brand-bar b { color:var(--orange); font-family:var(--display); letter-spacing:.08em;
                 text-transform:uppercase; font-size:11px; display:block; margin-bottom:8px; }
  .brand-bar code { color:var(--text); font-family:ui-monospace,Consolas,monospace; font-size:12px; }
  .brand-bar .k { color:var(--muted); }
  h2.sec { font-family:var(--display); font-size:13px; letter-spacing:.12em; text-transform:uppercase;
           color:var(--orange); margin:26px 0 10px; border-bottom:1px solid var(--border-strong);
           padding-bottom:6px; }
  .sub { color:var(--muted); margin-bottom:14px; font-size:13px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:14px; }
  .card { background:var(--navy-panel); border:1px solid var(--border-strong); border-radius:10px; padding:16px;
          cursor:pointer; transition:border-color .15s; }
  .card:hover { border-color:var(--orange); }
  .card.archcard { opacity:.72; border-style:dashed; }
  .card.archcard:hover { border-color:var(--orange); opacity:1; }
  .card h3 { font-family:var(--display); font-size:19px; letter-spacing:.03em; color:var(--orange-hover); word-break:break-all; }
  .meta { color:var(--muted); font-size:13px; margin:6px 0 10px; word-break:break-all; }
  .badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:11px; font-weight:700; margin-right:6px; }
  .free { background:rgba(74,222,128,.12); color:var(--green); border:1px solid rgba(74,222,128,.4); }
  .paid { background:rgba(217,120,52,.12); color:var(--orange); border:1px solid rgba(217,120,52,.4); }
  .local{ background:rgba(88,166,255,.12); color:#58a6ff; border:1px solid rgba(88,166,255,.4); }
  .cloud{ background:rgba(217,120,52,.12); color:var(--orange-hover); border:1px solid rgba(217,120,52,.4); }
  .arch { background:rgba(138,146,166,.12); color:var(--muted); border:1px solid var(--border-strong); }
  .row { display:flex; gap:8px; align-items:center; margin-top:10px; flex-wrap:wrap; }
  .btn { background:transparent; border:1px solid var(--border-strong); color:var(--text); border-radius:6px;
         padding:4px 12px; cursor:pointer; font-size:13px; }
  .btn:hover { border-color:var(--orange); color:var(--orange); }
  .del { border-color:rgba(248,113,113,.4); color:var(--red); }
  .del:hover { background:rgba(248,113,113,.12); }
  .archbtn { border-color:var(--border-strong); color:var(--muted); }
  .archbtn:hover { border-color:var(--orange); color:var(--orange); }
  .pop { border-color:var(--green); color:var(--green); }
  .pop:hover { background:rgba(74,222,128,.12); }
  .rsel { background:var(--navy); border:1px solid var(--border-strong); color:var(--muted); border-radius:6px;
          padding:4px 6px; font-size:12px; max-width:178px; }
  .expanded { display:none; margin-top:12px; border-top:1px solid var(--border); padding-top:12px; }
  .expanded.open { display:block; }
  .field { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
  .field label { font-size:11px; color:var(--muted); width:110px; flex-shrink:0; }
  .field input, .field select { flex:1; background:var(--navy); border:1px solid var(--border-strong); color:var(--text);
                                 border-radius:6px; padding:6px 8px; font-size:13px; font-family:ui-monospace,Consolas,monospace; }
  .copy { cursor:pointer; font-size:11px; padding:3px 10px; border:1px solid var(--orange); color:var(--orange);
          background:transparent; border-radius:4px; }
  .copy:hover { background:var(--orange-glow); }
  form { background:var(--navy-panel); border:1px solid var(--border-strong); border-radius:10px;
         padding:16px; margin-top:20px; display:flex; flex-wrap:wrap; gap:10px; align-items:end; }
  form h2 { width:100%; font-family:var(--display); font-size:14px; letter-spacing:.08em; text-transform:uppercase;
            color:var(--orange); margin-bottom:2px; }
  label { font-size:12px; color:var(--muted); display:block; margin-bottom:2px; }
  input,select { background:var(--navy); border:1px solid var(--border-strong); color:var(--text);
                 border-radius:6px; padding:8px 10px; font-size:14px; }
  input { width:150px; } input.wide { width:220px; }
  button.add { background:var(--orange); color:#060e18; border:none; font-weight:700; font-family:var(--display);
               letter-spacing:.05em; text-transform:uppercase; border-radius:6px; padding:10px 18px; cursor:pointer; font-size:13px; }
  button.add:hover { background:var(--orange-hover); }
  .err { color:var(--red); margin-top:10px; }
  .ok  { color:var(--green); margin-top:10px; }
  .panel { background:var(--navy-panel); border:1px solid var(--border-strong); border-radius:10px;
           padding:16px; margin-top:20px; }
  .master { border:1px solid var(--orange-border, rgba(217,120,52,.35)); background:var(--navy-panel); }
  .master h2 { color:var(--orange); font-family:var(--display); font-size:15px; letter-spacing:.1em;
               text-transform:uppercase; margin-bottom:2px; }
  .sliderdeck { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:22px; margin-top:14px; }
  .shead { display:flex; align-items:center; justify-content:space-between; gap:10px; }
  .sw { display:inline-flex; align-items:center; gap:7px; cursor:pointer; user-select:none; flex:0 0 auto; }
  .sw input { display:none; }
  .swsl { width:36px; height:19px; border-radius:11px; background:#223047; position:relative; transition:background .2s; flex:0 0 auto; box-shadow:inset 0 1px 2px rgba(0,0,0,.5); }
  .swsl::after { content:""; position:absolute; top:2.5px; left:3px; width:14px; height:14px; border-radius:50%; background:#8a92a6; transition:all .2s; }
  .sw input:checked + .swsl { background:var(--orange); box-shadow:0 0 10px var(--orange-glow); }
  .sw input:checked + .swsl::after { left:19px; background:#fff; }
  .swtxt { font:600 9px/1 Oswald, sans-serif; letter-spacing:.09em; text-transform:uppercase; color:var(--muted); }
  .sw input:checked ~ .swtxt { color:var(--orange); }
  .slidercol.off input[type=range] { opacity:.22; pointer-events:none; }
  .slidercol.off .sval, .slidercol.off .snote, .slidercol.off .ends { opacity:.35; }
  .slidercol.off { border:1px dashed rgba(255,255,255,.06); }
  .slidercol { display:flex; flex-direction:column; }
  .slidercol .shead { display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; }
  .slidercol .sname { font-family:var(--display); font-size:12px; letter-spacing:.1em; text-transform:uppercase; color:var(--text); }
  .slidercol .sval { font-family:ui-monospace,Consolas,monospace; color:var(--orange); font-size:15px; }
  .slidercol .ends { display:flex; justify-content:space-between; color:var(--muted); font-size:11px; margin-bottom:2px; }
  input[type=range] { -webkit-appearance:none; width:100%; height:12px; border-radius:999px;
                      background:linear-gradient(90deg, var(--navy), var(--navy-lift)); border:1px solid var(--border-strong);
                      outline:none; padding:0; }
  input[type=range]::-webkit-slider-thumb { -webkit-appearance:none; width:26px; height:26px; border-radius:50%;
                      background:var(--orange); border:3px solid var(--bg); cursor:pointer;
                      box-shadow:0 0 10px var(--orange-glow); }
  input[type=range]::-moz-range-thumb { width:26px; height:26px; border-radius:50%; background:var(--orange);
                      border:3px solid var(--bg); cursor:pointer; }
  input[type=range]::-moz-range-track { height:12px; border-radius:999px; background:var(--navy); border:1px solid var(--border-strong); }
  .slidercol .snote { color:var(--muted); font-size:11px; margin-top:4px; }
  .vault { display:grid; grid-template-columns:repeat(auto-fill,minmax(380px,1fr)); gap:12px; }
  .vaultcard { background:var(--navy-panel); border:1px solid var(--border-strong); border-radius:10px; padding:14px 16px; }
  .vaultcard h4 { font-family:var(--display); font-size:13px; letter-spacing:.08em; text-transform:uppercase;
                  color:var(--orange-hover); margin-bottom:10px; word-break:break-all; }
  .vaultcard .vrow { display:flex; gap:8px; align-items:center; margin-bottom:8px; }
  .vaultcard .vrow label { width:78px; flex-shrink:0; font-size:10px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; }
  .vaultcard .vrow input { flex:1; min-width:0; font-family:ui-monospace,Consolas,monospace; font-size:12px; }
  .vaultcard .keysrc { font-size:11px; color:var(--muted); margin-bottom:8px; }
  .vaultcard .keysrc b { color:var(--green); }
  .archnote { color:var(--orange); font-size:12px; margin-top:8px; }
  .empty { color:var(--muted); font-style:italic; padding:10px 2px; }
</style>
</head>
<body>
<header>
  <img class="logo" src="/logo.jpg" alt="MorPHYes">
  <h1>MorPHYes Model Hub</h1>
</header>
<div class="brand-bar">
  <div>
    <b>Custom provider setup</b>
    <span class="k">base_url&nbsp;</span><code>http://127.0.0.1:8123/v1</code><br>
    <span class="k">api_key&nbsp;</span><code>&lt;blank works — the key vault is here, offline&gt;</code><br>
    <span class="k">models&nbsp;</span><code>GET /v1/models (live list only)</code>
  </div>
  <div>
    <span class="k">provider name&nbsp;</span><code>simpleJACK</code><br>
    <span class="k">one endpoint, every app</span><code> — SimpleJack looks for this first</code><br>
    <span class="k">WE WORK HERE</span>
  </div>
</div>
<div class="sub">One offline engine. Every app serves from here. Cards autopop the graveyard on schedule; keys live in the vault below; your truth / simplicity / pace are set here and obeyed everywhere. Each master has its own switch — OFF means this hub stays out of the way and the model keeps its own defaults.</div>

<h2 class="sec">&#9679; Live cards</h2>
<div class="grid" id="grid"></div>

<div class="panel master" id="masterPanel">
  <h2>&#10022; MorPHYes Master Controls</h2>
  <div style="color:var(--muted); font-size:12px; margin-top:2px;">Set once here. Every app that points at this hub obeys. No app settings fight this.</div>
  <div class="sliderdeck">
    <div class="slidercol" id="col_temp">
      <div class="shead"><span class="sname">&#127919; TRUTH &harr; CREATIVITY &#127912;</span><label class="sw"><input type="checkbox" id="sw_temp" checked><span class="swsl"></span><span class="swtxt">imposed</span></label></div>
      <div class="ends"><span>exact facts</span><span>wild ideas</span></div>
      <input type="range" id="mp_temp" min="0" max="2" step="0.05" value="0.7">
      <div class="snote">low = stick to the truth. high = let it fly.<span class="sval" id="mp_temp_lbl" style="float:right;">0.70</span></div>
    </div>
    <div class="slidercol" id="col_simp">
      <div class="shead"><span class="sname">&#127868; SIMPLE &harr; EXPERT &#128640;</span><label class="sw"><input type="checkbox" id="sw_simp" checked><span class="swsl"></span><span class="swtxt">imposed</span></label></div>
      <div class="ends"><span>explain like I am 5</span><span>dense expert jargon</span></div>
      <input type="range" id="mp_simp" min="0" max="25" step="1" value="12">
      <div class="snote">5 = small child. 25 = technician talk.<span class="sval" id="mp_simp_lbl" style="float:right;">12</span></div>
    </div>
    <div class="slidercol" id="col_rate">
      <div class="shead"><span class="sname">&#9201; RATE PER MINUTE</span><label class="sw"><input type="checkbox" id="sw_rate" checked><span class="swsl"></span><span class="swtxt">imposed</span></label></div>
      <div class="ends"><span>patient 1</span><span>wall 40</span></div>
      <input type="range" id="mp_rpm" min="1" max="60" step="1" value="40">
      <div class="snote">engineered ceiling. bursts slow to this pace.<span class="sval" id="mp_rpm_lbl" style="float:right;">40</span></div>
    </div>
    <div class="slidercol" id="col_content">
      <div class="shead"><span class="sname">&#127909; CONTENT RATING &#128118;</span><label class="sw"><input type="checkbox" id="sw_content"><span class="swsl"></span><span class="swtxt">off</span></label></div>
      <div class="ends"><span>uncensored</span><span>NC-17</span></div>
      <input type="range" id="mp_content" min="0" max="18" step="1" value="0">
      <div class="snote">motion-picture filter. off = uncensored.<span class="sval" id="mp_content_lbl" style="float:right;">OFF</span></div>
    </div>
    <div class="slidercol" id="col_gpu">
      <div class="shead"><span class="sname">&#128187; GPU BUSY LINE (ollama gate)</span></div>
      <div class="ends"><span>util %</span><span>VRAM MB</span></div>
      <div style="display:flex; gap:8px;">
        <input type="range" id="mp_gpuutil" min="10" max="99" step="1" value="60" style="flex:1;">
        <input type="range" id="mp_gpuvram" min="500" max="8192" step="100" value="6000" style="flex:1;">
      </div>
      <div class="snote">when the GPU crosses this line, ollama cards wait then bounce. cloud cards never touch the GPU.<span class="sval" id="mp_gpu_lbl" style="float:right;">60% / 6000MB</span></div>
    </div>
  </div>
  <div><span class="ok" id="mp_msg"></span></div>
</div>

<form id="addForm">
  <h2>&#10010; Add model card</h2>
  <div><label>ALIAS (id)</label><input id="f_id" required placeholder="zaiglm53"></div>
  <div><label>PROVIDER</label><select id="f_provider"></select></div>
  <div><label>UPSTREAM MODEL</label><input id="f_up" class="wide" required placeholder="glm-5.3-free"></div>
  <div><label>LABEL</label><input id="f_label" placeholder="GLM 5.3 free"></div>
  <div><label>CONTEXT</label><input id="f_ctx" placeholder="1000000"></div>
  <div><label>COST</label><select id="f_cost"><option value="FREE">FREE</option><option value="PAID">PAID</option><option value="LOCAL">LOCAL</option><option value="CLOUD">CLOUD</option></select></div>
  <div><label>ARCHIVE DATE (stays live until this date, then hides)</label><input id="f_arch" type="date" title="Credit-window: live until this date, then drops itself into Archived"></div>
  <button class="add" type="submit">ADD CARD</button>
</form>

<div class="panel">
  <h2 style="font-family:var(--display); font-size:14px; letter-spacing:.08em; text-transform:uppercase; color:var(--orange); margin-bottom:10px;">&#10010; Add provider</h2>
  <form id="provForm" style="margin-top:0; background:transparent; border:none; padding:0;">
    <div><label>PROVIDER NAME</label><input id="p_name" required placeholder="deepseek"></div>
    <div><label>CHAT URL</label><input id="p_url" class="wide" required placeholder="https://api.deepseek.com/v1/chat/completions"></div>
    <div><label>API KEY (optional)</label><input id="p_key" class="wide" placeholder="sk-...  blank = local/anon"></div>
    <button class="add" type="submit">ADD PROVIDER</button>
  </form>
  <div class="err" id="perr"></div>
</div>

<h2 class="sec">&#9724; Archived (graveyard is pickable)</h2>
<div class="grid" id="gridArch"></div>
<div class="empty" id="archEmpty">Nothing in the graveyard. Archive a card and it lands here. Returning is always optional - pop it back in whenever you want.</div>

<h2 class="sec">&#128273; Provider vault (offline key storage)</h2>
<div class="sub">Keys live HERE, in model_loader.json on this machine. App side: blank key. If a key is missing the card runs local/anon - the key call has nothing to do with the call.</div>
<div class="vault" id="vault"></div>

<div class="err" id="err"></div>

<script>
let providers = [];
let expanded = null;

function specToSelect(spec){
  if(!spec || !spec.freq) return 'none';
  if(spec.freq==='weekly') return 'weekly:'+(spec.dow||'friday')+':'+(spec.time||'17:00');
  if(spec.freq==='monthly') return 'monthly:'+(spec.dom||1)+':'+(spec.time||'09:00');
  return 'none';
}
function selectToSpec(v){
  if(v==='none') return null;
  const p = v.split(':');
  if(p[0]==='weekly') return {freq:'weekly', dow:p[1], time:p[2]};
  if(p[0]==='monthly') return {freq:'monthly', dom:parseInt(p[1]||1,10), time:p[2]};
  return null;
}
const RESET_OPTIONS = `
  <option value="none">no auto-return</option>
  <option value="weekly:friday:17:00">weekly Fri 5pm</option>
  <option value="weekly:friday:09:00">weekly Fri 9am</option>
  <option value="weekly:monday:09:00">weekly Mon 9am</option>
  <option value="weekly:saturday:09:00">weekly Sat 9am</option>
  <option value="monthly:1:09:00">monthly 1st 9am</option>
  <option value="monthly:15:09:00">monthly 15th 9am</option>`;

function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function cardHtml(c, j, arch){
  const cost = (c.cost||'').toUpperCase();
  const cls = cost==='FREE'?'free':(cost==='LOCAL'?'local':(cost==='CLOUD'?'cloud':'paid'));
  const archBadge = arch ? ' <span class="badge arch">ARCHIVED</span>' : '';
  const retNote = c.reset_next ? `<div class="archnote">auto-return armed: ${esc(c.reset_next)}</div>` : '';
  const popBtn = arch ? '<button class="btn pop" data-act="pop">pop back in</button>' : '';
  const selVal = esc(specToSelect(c.reset_spec));
  return `<div class="card ${arch?'archcard':''}" data-id="${c.id}">
    <h3>${esc(c.id)}</h3>
    <div class="meta">${esc(c.provider)} &#183; ${esc(c.upstream)}${c.context?(' &#183; '+Number(c.context).toLocaleString()+' ctx'):''}</div>
    <div><span class="badge ${cls}">${arch?'ARCHIVED':(c.cost||'PAID')}</span>${c.label?' &#183; <span class="meta">'+esc(c.label)+'</span>':''}${archBadge}</div>
    <div class="row">
      <button class="btn" data-act="config">config</button>
      ${arch?popBtn:'<button class="btn archbtn" data-act="archive">archive</button>'}
      <select class="rsel" data-rsel="${c.id}" title="auto-return if you archive this (optional)">${RESET_OPTIONS}</select>
      <button class="btn del" data-act="del">remove</button>
    </div>
    ${retNote}
    <div class="expanded" id="exp-${c.id}">
      <div class="field"><label>ALIAS</label><input readonly value="${esc(c.id)}"><button class="copy" data-copy="${esc(c.id)}">copy</button></div>
      <div class="field"><label>PROVIDER</label><input readonly value="${esc(c.provider)}"><button class="copy" data-copy="${esc(c.provider)}">copy</button></div>
      <div class="field"><label>UPSTREAM</label><input readonly value="${esc(c.upstream)}"><button class="copy" data-copy="${esc(c.upstream)}">copy</button></div>
      <div class="field"><label>LABEL</label><input readonly value="${esc(c.label||'')}"><button class="copy" data-copy="${esc(c.label||'')}">copy</button></div>
      <div class="field"><label>CONTEXT</label><input readonly value="${esc(c.context||'')}"><button class="copy" data-copy="${esc(c.context||'')}">copy</button></div>
      <div class="field"><label>COST</label><input readonly value="${esc(c.cost||'')}"><button class="copy" data-copy="${esc(c.cost||'')}">copy</button></div>
      <div class="field"><label>NOTE</label><input readonly value="${esc(c.note||'')}"><button class="copy" data-copy="${esc(c.note||'')}">copy</button></div>
      <div class="field"><label>ARCHIVE DATE</label><input readonly value="${esc(c.archive_until||'')}"><button class="copy" data-copy="${esc(c.archive_until||'')}">copy</button></div>
      <div class="field"><label>AUTO-RETURN</label><input readonly value="${esc(c.reset_next||'')}"><button class="copy" data-copy="${esc(c.reset_next||'')}">copy</button></div>
      <div class="field"><label>BASE URL</label><input readonly value="${esc(((j._base||{})[ c.provider ])||'')}"><button class="copy" data-copy="${esc(((j._base||{})[ c.provider ])||'')}">copy</button></div>
    </div>
  </div>`;
}

function wireCard(card){
  const id = card.dataset.id;
  const exp = document.getElementById('exp-'+id);
  const sel = card.querySelector('.rsel');
  if(sel) sel.value = card.dataset.selval || 'none';
  card.addEventListener('click', e=>{
    if (e.target.closest('button') || e.target.closest('select')) return;
    const wasOpen = exp.classList.contains('open');
    document.querySelectorAll('.expanded').forEach(x=>x.classList.remove('open'));
    if (!wasOpen) exp.classList.add('open');
  });
  card.querySelectorAll('button[data-act]').forEach(b=>{
    b.onclick = async e=>{
      e.stopPropagation();
      const act = b.dataset.act;
      if (act==='del') { await fetch('/api/cards/'+id, {method:'DELETE'}); load(true); }
      else if (act==='archive') {
        const rs = sel ? selectToSpec(sel.value) : null;
        await fetch('/api/cards/'+id+'/archive', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({reset_spec: rs})});
        load(true);
      }
      else if (act==='pop') {
        await fetch('/api/cards/'+id+'/unarchive', {method:'POST'});
        load(true);
      }
    };
  });
  card.querySelectorAll('button[data-copy]').forEach(b=>{
    b.onclick = e=>{
      e.stopPropagation();
      navigator.clipboard.writeText(b.dataset.copy).then(()=>{ b.textContent='copied'; setTimeout(()=>b.textContent='copy', 1200); });
    };
  });
}

function ratingLabel(v){
  v = parseInt(v)||0;
  if(v<=0) return 'OFF';
  if(v<=6) return 'G/PG';
  if(v<=12) return 'PG-13';
  if(v<=16) return 'R';
  return 'NC-17';
}

function setSliders(prefs){
  const t = document.getElementById('mp_temp'), s = document.getElementById('mp_simp'), r = document.getElementById('mp_rpm'), c = document.getElementById('mp_content');
  const gu = document.getElementById('mp_gpuutil'), gv = document.getElementById('mp_gpuvram');
  if(prefs){
    t.value = prefs.temperature;
    s.value = prefs.simplicity;
    r.value = prefs.rate_rpm;
    c.value = (prefs.content_rating !== undefined) ? prefs.content_rating : 0;
    gu.value = (prefs.gpu_util_max !== undefined) ? prefs.gpu_util_max : 60;
    gv.value = (prefs.gpu_vram_max_mb !== undefined) ? prefs.gpu_vram_max_mb : 6000;
    const cols = {temp:'sw_temp', simp:'sw_simp', rate:'sw_rate', content:'sw_content'};
    ['temp','simp','rate','content'].forEach(k=>{
      const sw = document.getElementById(cols[k]);
      sw.checked = !!prefs['impose_'+k];
      document.getElementById('col_'+k).classList.toggle('off', !sw.checked);
      const txt = sw.parentElement.querySelector('.swtxt');
      if (txt) txt.textContent = sw.checked ? 'imposed' : 'off';
    });
  }
  document.getElementById('mp_temp_lbl').textContent = parseFloat(t.value).toFixed(2);
  document.getElementById('mp_simp_lbl').textContent = s.value + (s.value==='0' ? ' OFF' : '');
  document.getElementById('mp_rpm_lbl').textContent = r.value;
  document.getElementById('mp_content_lbl').textContent = ratingLabel(c.value);
  document.getElementById('mp_gpu_lbl').textContent = gu.value + '% / ' + gv.value + 'MB';
}

async function savePrefs(){
  const body = { temperature: parseFloat(document.getElementById('mp_temp').value),
                 simplicity: parseInt(document.getElementById('mp_simp').value, 10),
                 rate_rpm: parseInt(document.getElementById('mp_rpm').value, 10),
                 content_rating: parseInt(document.getElementById('mp_content').value, 10),
                 gpu_util_max: parseInt(document.getElementById('mp_gpuutil').value, 10),
                 gpu_vram_max_mb: parseInt(document.getElementById('mp_gpuvram').value, 10),
                 impose_temp: document.getElementById('sw_temp').checked,
                 impose_simple: document.getElementById('sw_simp').checked,
                 impose_rate: document.getElementById('sw_rate').checked,
                 impose_content_rating: document.getElementById('sw_content').checked };
  document.getElementById('mp_msg').textContent = 'saving...';
  const r = await fetch('/api/prefs', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const j = await r.json();
  document.getElementById('mp_msg').textContent = j.ok ? 'saved - every app using this hub obeys now' : 'save failed';
}

['temp','simp','rate','content'].forEach(k=>{
  document.getElementById('sw_'+k).addEventListener('change', async e=>{
    const col = document.getElementById('col_'+k);
    col.classList.toggle('off', !e.target.checked);
    const txt = e.target.parentElement.querySelector('.swtxt');
    if (txt) txt.textContent = e.target.checked ? 'imposed' : 'off';
    const body = { ['impose_'+k]: e.target.checked };
    await fetch('/api/prefs', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    document.getElementById('mp_msg').textContent = e.target.checked
      ? (k==='content' ? 'content filter imposed - replies filtered to the rating below' : 'imposed - this hub applies it to every app again')
      : (k==='content' ? 'content filter off - uncensored' : 'off - model keeps its own default. switch back anytime.');
  });
});

async function loadVault(){
  const r = await fetch('/api/providers'); const j = await r.json();
  const v = document.getElementById('vault');
  v.innerHTML = (j.providers||[]).map(p=>{
    const statusTxt = p.has_key
      ? `key <b>present</b> &mdash; ${esc(p.key_source)}${p.key_tail?' <span class="tail">(&hellip;'+esc(p.key_tail)+')</span>':''}`
      : 'key <b>absent</b> &mdash; card runs local/anon';
    return `<div class="vaultcard" data-name="${esc(p.name)}">
      <h4>${esc(p.name)}</h4>
      <div class="keysrc">${statusTxt}</div>
      <div class="vrow"><label>CHAT URL</label><input class="vurl" value="${esc(p.chat_url)}" placeholder="https://.../v1/chat/completions"></div>
      <div class="vrow"><label>API KEY</label><input class="vkey" type="password" value="" placeholder="type a new key, or reveal"></div>
      <div class="row">
        <button class="btn" data-vreveal>reveal</button>
        <button class="btn pop" data-vsave>save key</button>
        <span class="ok vsavemsg"></span>
      </div>
    </div>`;
  }).join('');
  v.querySelectorAll('.vaultcard').forEach(card=>{
    const name = card.dataset.name;
    card.querySelector('button[data-vreveal]').onclick = async e=>{
      const r = await fetch('/api/providers/'+name+'/key');
      const j = await r.json();
      const inp = card.querySelector('.vkey');
      inp.value = j.key || '';
      inp.type = 'text';
      card.querySelector('.vsavemsg').textContent = j.key ? 'shown - save overwrites' : 'no key on file';
      setTimeout(()=>{ inp.type='password'; card.querySelector('.vsavemsg').textContent=''; }, 4000);
    };
    card.querySelector('button[data-vsave]').onclick = async e=>{
      const body = { chat_url: card.querySelector('.vurl').value.trim(),
                     api_key: card.querySelector('.vkey').value };
      const r = await fetch('/api/providers/'+name, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      const j = await r.json();
      const msg = card.querySelector('.vsavemsg');
      msg.textContent = j.ok ? 'saved to keys.local.json (the real file)' : ('error: '+(j.error||''));
      setTimeout(()=>{msg.textContent='';}, 2500);
      loadVault();
    };
  });
}

async function load(keepSelection){
  const r = await fetch('/api/cards'); const j = await r.json();
  const sel = document.getElementById('f_provider');
  const prev = sel.value;
  providers = j.providers;
  sel.innerHTML = providers.map(p=>`<option value="${p}">${p}</option>`).join('');
  if (prev && providers.includes(prev)) sel.value = prev;
  else if (providers.includes('zenmux')) sel.value = 'zenmux';
  else if (providers.length) sel.value = providers[0];

  const live = j.cards.filter(c=>!c.hidden);
  const arch = j.cards.filter(c=>c.hidden);

  const grid = document.getElementById('grid');
  grid.innerHTML = live.map(c=>cardHtml(c, j, false)).join('');
  grid.querySelectorAll('.card').forEach(card=>{
    card.dataset.selval = specToSelect(live.find(c=>c.id===card.dataset.id).reset_spec);
    wireCard(card);
  });

  const ga = document.getElementById('gridArch');
  ga.innerHTML = arch.map(c=>cardHtml(c, j, true)).join('');
  ga.querySelectorAll('.card').forEach(card=>{
    card.dataset.selval = specToSelect(arch.find(c=>c.id===card.dataset.id).reset_spec);
    wireCard(card);
  });
  document.getElementById('archEmpty').style.display = arch.length ? 'none' : 'block';

  setSliders(j.prefs);
  loadVault();
}

document.getElementById('addForm').onsubmit = async e=>{
  e.preventDefault();
  const body = {
    id: f_id.value.trim(), provider: f_provider.value, upstream: f_up.value.trim(),
    label: f_label.value.trim(), cost: f_cost.value,
    context: f_ctx.value? parseInt(f_ctx.value): null,
    archive_until: f_arch.value || null
  };
  const r = await fetch('/api/cards', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const j = await r.json();
  if(j.error){ document.getElementById('err').textContent = j.error; }
  else {
    document.getElementById('err').textContent = '';
    f_id.value=''; f_up.value=''; f_label.value=''; f_ctx.value=''; f_arch.value='';
    load(true);
  }
};

document.getElementById('provForm').onsubmit = async e=>{
  e.preventDefault();
  const body = { name: p_name.value.trim(), chat_url: p_url.value.trim(), api_key: p_key.value.trim() };
  const r = await fetch('/api/providers', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const j = await r.json();
  if(j.error){ document.getElementById('perr').textContent = j.error; }
  else {
    document.getElementById('perr').textContent = 'added: '+j.provider;
    p_name.value=''; p_url.value=''; p_key.value='';
    load(true);
  }
};

['mp_temp','mp_simp','mp_rpm','mp_content','mp_gpuutil','mp_gpuvram'].forEach(id=>{
  document.getElementById(id).addEventListener('input', e=>{
    const v = e.target.value;
    const lbl = document.getElementById(id==='mp_temp'?'mp_temp_lbl':(id==='mp_simp'?'mp_simp_lbl':(id==='mp_rpm'?'mp_rpm_lbl':(id==='mp_content'?'mp_content_lbl':'mp_gpu_lbl'))));
    lbl.textContent = id==='mp_temp' ? parseFloat(v).toFixed(2)
                    : (id==='mp_content' ? ratingLabel(v)
                    : (id==='mp_gpuutil' ? v+'% / '+document.getElementById('mp_gpuvram').value+'MB'
                    : (id==='mp_gpuvram' ? document.getElementById('mp_gpuutil').value+'% / '+v+'MB'
                    : (v + (id==='mp_simp'&&v==='0' ? ' OFF' : '')))));
  });
  document.getElementById(id).addEventListener('change', savePrefs);
});

load();
setInterval(()=>load(true), 5000);
</script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def index():
    reg = load_registry()
    base = {}
    for name, p in reg.get("providers", {}).items():
        base[name] = p.get("chat_url", "")
    page = HTML_PAGE.replace('let providers = [];',
                             'let providers = []; const __BASE__ = ' + json.dumps(base) + ';')
    return page

if __name__ == "__main__":
    print("=" * 60)
    print("  MORPHYES MODEL HUB")
    print("  UI:   http://127.0.0.1:8123/")
    print("  API:  http://127.0.0.1:8123/v1")
    print("  Master Controls: truth | simplicity | rate, applied at source")
    print("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=8123, log_level="warning")