// ══════════════════════════════════════════════════════════════
// morPHYtrek Chrome Extension — Content Script
// Watches AI chat messages and sends new messages to the SimpleJack
// brain (/api/narrate) which writes to the narration queue ONLY if
// the queue folder exists — THE SWITCH. Delete the folder = no voice.
// Also: MIC capture via the Web Speech API (most devices allow it).
// ═══════════════════════════════════════════════════════════════

// The brain — morPHYtrek's own extension bridge on 8792.
// morPHYtrek.py runs this tiny server itself and writes straight to the
// narration queue. No SimpleJack, no external bridge. Standalone.
const NARRATION_URL = "http://localhost:8792/api/narrate";

let enabled = false;
let lastSpoken = "";
let observer = null;
let chunkCounter = 0;
let micRecognition = null;
let micActive = false;

const SITE_SELECTORS = {
  "chat.z.ai": "[class*='assistant'], [class*='message']",
  "chat.openai.com": "[data-message-author-role='assistant']",
  "chatgpt.com": "[data-message-role='assistant']",
  "claude.ai": "[class*='prose']",
  "chat.deepseek.com": "[class*='answer']",
  "localhost:8090": ".task-card, .message",
  "localhost:8790": ".task-card, .message",
};

function getSelector() {
  const host = location.host;
  for (const [site, sel] of Object.entries(SITE_SELECTORS)) {
    if (host.includes(site)) return sel;
  }
  return "[class*='message'], [class*='response'], [class*='assistant'], .prose";
}

function cleanText(text) {
  return text
    .replace(/```[\s\S]*?```/g, " code block ")
    .replace(/https?:\/\/\S+/g, " link ")
    .replace(/[#`*_~^]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

// NEW: Brute-force text extraction that bypasses innerText truncation
function getTextFromPage() {
  const selector = getSelector();
  let fullText = "";
  
  // If the selector finds specific message blocks, iterate through them and grab all text content
  const messageBlocks = document.querySelectorAll(selector);
  
  if (messageBlocks.length > 0) {
    messageBlocks.forEach((block, index) => {
      // Try to get the inner text of each message block
      const textContent = block.innerText || block.textContent || "";
      if (textContent.trim().length > 0) {
        fullText += textContent.trim() + "\n\n";
      }
    });
  } else {
    // Ultimate fallback: grab the entire document body, but join all block-level text nodes
    const textNodes = document.body.querySelectorAll('p, li, h1, h2, h3, h4, div.prose, span.message, div.answer');
    textNodes.forEach(node => {
      const nodeText = node.innerText || node.textContent || "";
      if (nodeText.trim().length > 10) {
        fullText += nodeText.trim() + "\n\n";
      }
    });
  }
  
  return fullText.trim();
}

async function sendChunkToQueue(chunkText, sourceName) {
  const payload = {
    text: chunkText,
    source: sourceName,
    "engine": "piper"
  };
  
  try {
    const resp = await fetch(NARRATION_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    
    if (resp.ok) {
      console.log(`[morPHYtrek] Narrated chunk ${chunkCounter} (${chunkText.substring(0, 50)}...)`);
    } else {
      console.error(`[morPHYtrek] Failed to send chunk ${chunkCounter}: HTTP ${resp.status}`);
    }
  } catch (e) {
    console.error(`[morPHYtrek] Chunk ${chunkCounter} failed:`, e.message);
  }
}

async function narrate(text, skipGate) {
  if (!text || text.length < 5) return;
  
  // THE TRANSLATOR LANE: every read-aloud passes the language gate.
  // Off by default; the popup dropdown sets it; failures read the original.
  // skipGate=true bypasses it (forced English reads, Trent 2026-09-29).
  if (window.MT_TRANSLATE && !skipGate) {
    try { text = await window.MT_TRANSLATE.maybeTranslate(text); } catch (_) {}
  }
  
  const cleaned = cleanText(text);
  if (cleaned === lastSpoken) return;
  lastSpoken = cleaned;
  
  const maxChunkSize = 4000;
  const chunks = [];
  for (let i = 0; i < cleaned.length; i += maxChunkSize) {
    chunks.push(cleaned.substring(i, i + maxChunkSize));
  }
  
  if (chunks.length === 1) {
    chunkCounter++;
    await sendChunkToQueue(chunks[0], `chrome_extension_${chunkCounter}`);
    return;
  }
  
  for (let i = 0; i < chunks.length; i++) {
    chunkCounter++;
    await sendChunkToQueue(chunks[i], `chrome_extension_${chunkCounter}`);
    await new Promise(resolve => setTimeout(resolve, 1500));
  }
}

function findNewMessages() {
  // CHANGED: Use the brute-force extractor instead of the single node text grabber
  const text = getTextFromPage();
  
  if (text.length < 10) return;
  
  // Still check if it matches what we just spoke to avoid double-narration
  if (text === lastSpoken) return;
  
  narrate(text);
}

function startObserver() {
  if (observer) observer.disconnect();
  
  observer = new MutationObserver((mutations) => {
    if (!enabled) return;
    
    let hasNewContent = false;
    for (const mutation of mutations) {
      if (mutation.addedNodes.length > 0) {
        hasNewContent = true;
        break;
      }
    }
    
    if (hasNewContent) {
      clearTimeout(window._morphyeoTimer);
      window._morphyeoTimer = setTimeout(findNewMessages, 500);
    }
  });
  
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true
  });
  
  console.log("[morPHYtrek] Observer started for", location.host);
}

function stopObserver() {
  if (observer) {
    observer.disconnect();
    observer = null;
  }
  console.log("[morPHYtrek] Observer stopped");
}

// ── MIC CAPTURE — the "most humans take mic from most things" path.
// Uses the Web Speech API (webkitSpeechRecognition) — no audio files,
// no cloud keys, works on most devices right in the browser tab.
function startMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    console.log("[morPHYtrek] SpeechRecognition not available on this browser");
    return false;
  }
  try {
    micRecognition = new SR();
    micRecognition.continuous = true;
    micRecognition.interimResults = false;
    micRecognition.lang = "en-US";
    micRecognition.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        if (transcript && transcript.trim().length > 2) {
          // Mic words → the SAME pipe → brain honors THE SWITCH.
          sendChunkToQueue(transcript.trim(), "microphone").catch(() => {});
        }
      }
    };
    micRecognition.onerror = (e) => {
      console.log("[morPHYtrek] Mic error:", e.error);
      if (e.error === "not-allowed") {
        micActive = false;
        try { chrome.runtime.sendMessage({ type: "MIC_STATE", active: false }); } catch (_) {}
      }
    };
    micRecognition.onend = () => {
      // Auto-restart unless explicitly stopped (continuous listening).
      if (micActive) {
        try { micRecognition.start(); } catch (_) {}
      }
    };
    micRecognition.start();
    micActive = true;
    console.log("[morPHYtrek] Mic listening - words go to the narration queue if the folder exists");
    return true;
  } catch (e) {
    console.log("[morPHYtrek] Mic start failed:", e);
    return false;
  }
}

function stopMic() {
  micActive = false;
  if (micRecognition) {
    try { micRecognition.stop(); } catch (_) {}
    micRecognition = null;
  }
}

chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  if (msg.type === "SET_ENABLED") {
    enabled = msg.enabled;
    if (enabled) {
      startObserver();
    } else {
      stopObserver();
    }
    respond({ enabled: enabled });
  }

  if (msg.type === "SET_MIC") {
    if (msg.active) {
      const ok = startMic();
      respond({ active: ok, available: true });
    } else {
      stopMic();
      respond({ active: false, available: true });
    }
  }
  
  if (msg.type === "NARRATE_TEXT") {
    narrate(msg.text);
    respond({ success: true });
  }
  
  if (msg.type === "READ_SELECTION") {
    const selected = window.getSelection().toString();
    if (selected && selected.length > 5) {
      narrate(selected);
      respond({ success: true });
    } else {
      respond({ success: false, error: "No text selected" });
    }
  }
  
  if (msg.type === "READ_PAGE") {
    // CHANGED: Use brute-force extractor
    const bodyText = getTextFromPage();
    if (bodyText.length > 10) {
      narrate(bodyText);
    }
    respond({ success: true });
  }
});

console.log("[morPHYtrek] Content script loaded - narration queue via port 8792");

// â•â•â• ARTICULATE / PROMOTE â€” offline cleanup via morPHYtrek bridge â•â•â•
const MT_ARTICULATE_URL = "http://localhost:8792/api/articulate";
const MT_PROMOTE_URL = "http://localhost:8792/api/promote";
const MT_REIMAGINE_URL = "http://localhost:8792/api/reimagine";
let _mtUndo = null;

function _mtEditableFromSelection() {
  const sel = window.getSelection();
  if (sel && sel.rangeCount > 0 && !sel.isCollapsed) {
    let n = sel.anchorNode;
    if (n && n.nodeType === 3) n = n.parentNode;
    if (n && n.closest) {
      const ed = n.closest('textarea, input[type="text"], input[type="email"], input[type="search"], input[type="url"], [contenteditable="true"], [contenteditable=""]');
      if (ed) return ed;
    }
  }
  const ae = document.activeElement;
  if (ae && (ae.tagName === "TEXTAREA" || (ae.tagName === "INPUT" && /^(text|email|search|url)$/i.test(ae.type || "")) || ae.isContentEditable)) return ae;
  return null;
}

function _mtToast(msg, revertFn) {
  const old = document.getElementById("__mt_toast");
  if (old) old.remove();
  const d = document.createElement("div");
  d.id = "__mt_toast";
  d.style.cssText = "position:fixed;bottom:24px;right:24px;z-index:2147483647;background:#0f2340;color:#ece6d8;padding:10px 16px;border-radius:8px;font:13px Segoe UI,Arial,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.4);display:flex;gap:12px;align-items:center;";
  d.textContent = msg;
  if (revertFn) {
    const b = document.createElement("span");
    b.textContent = "Ctrl+Z revert";
    b.style.cssText = "color:#d97834;font-weight:700;cursor:pointer;";
    b.onclick = () => { revertFn(); d.remove(); };
    d.appendChild(b);
  }
  document.body.appendChild(d);
  setTimeout(() => d.remove(), 12000);
}

async function _mtPost(url, payload) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

function _mtReplaceSelection(ed, newText) {
  if (ed.tagName === "TEXTAREA" || ed.tagName === "INPUT") {
    const s = ed.selectionStart, e = ed.selectionEnd;
    const before = ed.value.substring(0, s);
    const after = ed.value.substring(e);
    _mtUndo = { ed: ed, value: ed.value, s: s, e: e };
    ed.value = before + newText + after;
    ed.selectionStart = s;
    ed.selectionEnd = s + newText.length;
    ed.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  }
  if (ed.isContentEditable) {
    _mtUndo = { ed: ed, html: ed.innerHTML };
    const ok = document.execCommand("insertText", false, newText);
    if (!ok) {
      const sel = window.getSelection();
      if (sel && sel.rangeCount > 0) {
        const range = sel.getRangeAt(0);
        range.deleteContents();
        range.insertNode(document.createTextNode(newText));
        return true;
      }
      return false;
    }
    return true;
  }
  return false;
}

function _mtRevert() {
  if (!_mtUndo) return;
  try {
    if (_mtUndo.value !== undefined) {
      _mtUndo.ed.value = _mtUndo.value;
      _mtUndo.ed.dispatchEvent(new Event("input", { bubbles: true }));
    } else if (_mtUndo.html !== undefined) {
      _mtUndo.ed.innerHTML = _mtUndo.html;
    }
  } catch (e) { console.warn("[morPHYtrek] revert failed", e); }
  _mtUndo = null;
}

async function _mtRun(mode) {
  const ed = _mtEditableFromSelection();
  if (!ed) {
    _mtToast(mode === "articulate" ? "Articulating..." : "Promoting...");
    return;
  }
  let text = "";
  if (ed.tagName === "TEXTAREA" || ed.tagName === "INPUT") {
    const s = ed.selectionStart, e = ed.selectionEnd;
    if (e > s) text = ed.value.substring(s, e);
    else text = ed.value;
  } else {
    const sel = window.getSelection();
    if (sel && sel.rangeCount > 0 && !sel.isCollapsed) text = sel.toString();
    else text = ed.innerText || ed.textContent || "";
  }
  if (!text || text.trim().length < 2) {
    _mtToast("Nothing to " + (mode === "articulate" ? "articulate" : "promote"));
    return;
  }
  if (mode === "reimagine") { await _mtReimagine(ed, text); return; }
  _mtToast(mode === "articulate" ? "Articulating..." : "Promoting...");
  // PROMOTE FIX (operator order 2026-10-18, THE ROOT CAUSE): the old line
  // force-translated EVERY selection to English before promote — English
  // into English. MyMemory answers that with its own error line "PLEASE
  // SELECT TWO DISTINCT LANGUAGES" (403), which got accepted, cached, and
  // rendered as the card body. Promote renders the operator's words AS
  // THEY ARE. No translation. Ever.
  try {
    const url = mode === "articulate" ? MT_ARTICULATE_URL : MT_PROMOTE_URL;
    const data = await _mtPost(url, { text: text });
    if (!data.success) throw new Error(data.error || "server error");
    let out = mode === "articulate" ? data.text : data.md;
    if (mode === "promote" && ed.isContentEditable && data.html) {
      _mtUndo = { ed: ed, html: ed.innerHTML };
      const ok = document.execCommand("insertHTML", false, data.html);
      if (ok) { _mtToast("Promoted - Ctrl+Z to revert", _mtRevert); return; }
    }
    const done = _mtReplaceSelection(ed, out);
    if (done) _mtToast(mode === "articulate" ? "Articulating..." : "Promoting...", _mtRevert);
    else _mtToast("Could not replace text in this field");
  } catch (e) {
    _mtBridgeLight(() => _mtRun(mode));
  }
}

// BRIDGE LIGHT — replaces the "is the app running?" toast (Trent 2026-09-30).
// A light: red = bridge offline, green = online. Tap it to refresh the
// check; if the bridge is back the last action retries automatically.
function _mtBridgeLight(onBackOnline) {
  const old = document.getElementById("__mt_toast");
  if (old) old.remove();
  const d = document.createElement("div");
  d.id = "__mt_toast";
  d.style.cssText = "position:fixed;bottom:24px;right:24px;z-index:2147483647;background:#0f2340;color:#ece6d8;padding:10px 16px;border-radius:8px;font:13px Segoe UI,Arial,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.4);display:flex;gap:12px;align-items:center;";
  const light = document.createElement("span");
  light.style.cssText = "width:14px;height:14px;border-radius:50%;background:#ef4444;box-shadow:0 0 8px #ef4444;cursor:pointer;flex:none;";
  const label = document.createElement("span");
  label.textContent = "Bridge offline — tap the light to refresh";
  light.onclick = async () => {
    let ok = false;
    try { const r = await fetch(MT_ARTICULATE_URL, { method: "OPTIONS" }); ok = r.ok; } catch (_) {}
    light.style.background = ok ? "#4ade80" : "#ef4444";
    light.style.boxShadow = "0 0 8px " + (ok ? "#4ade80" : "#ef4444");
    label.textContent = ok ? "Bridge online" : "Bridge offline — tap the light to refresh";
    if (ok && onBackOnline) { d.remove(); onBackOnline(); }
  };
  d.appendChild(light);
  d.appendChild(label);
  document.body.appendChild(d);
  setTimeout(() => d.remove(), 12000);
}

// REIMAGINE — Tina's lane (operator order 2026-10-18): Tina ships with
// the tool and does the reimagine work. Model: josie-2b-tools via the
// local Ollama engine. The bridge does the model call; content just
// posts text and swaps the result into the field.
async function _mtReimagine(ed, text) {
  _mtToast("Reimagining...");
  try {
    const data = await _mtPost(MT_REIMAGINE_URL, { text: text, tone: "clear" });
    if (!data.success) throw new Error(data.error || "reimagine failed");
    const out = data.text || "";
    if (!out) throw new Error("empty response");
    const done = _mtReplaceSelection(ed, out);
    _mtToast(done ? "Reimagined - Ctrl+Z to revert" : "Could not replace text in this field");
  } catch (e) {
    _mtToast("Reimagine: " + (e.message || "failed"));
  }
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "ARTICULATE_SELECTION") _mtRun("articulate");
  if (msg.type === "PROMOTE_FIELD") _mtRun("promote");
  if (msg.type === "REIMAGINE_SELECTION") _mtRun("reimagine");
  if (msg.type === "TRANSLATE_READ") _mtTranslateRead(msg.text);
});

// TRANSLATE WITH MORPHYTREK — turn the writing to English and read it
// aloud (Trent 2026-09-29). Forced English via the MyMemory lane,
// regardless of the popup dropdown. Failure reads the original.
// 2026-09-30: the highlighted text on screen is ALSO swapped for the
// English — written and spoken in one step, no Google two-step.
async function _mtTranslateRead(text) {
  if (!text || text.trim().length < 2) return;
  let out = text;
  if (window.MT_TRANSLATE) {
    try { out = await window.MT_TRANSLATE.forceTranslate(text, "en"); }
    catch (_) { out = text; /* SCREEN SWAP GUARD (2026-10-18): on translation
                 failure the original text is kept — the old code appended
                 "Translation unavailable" AND swapped it into the page,
                 replacing the operator's highlighted words with an error
                 message. Read the original; change nothing on screen. */ }
  }
  try {
    const sel = window.getSelection();
    if (sel && sel.rangeCount > 0 && !sel.isCollapsed) {
      const range = sel.getRangeAt(0);
      range.deleteContents();
      range.insertNode(document.createTextNode(out));
    }
  } catch (_) {}
  lastSpoken = ""; // forced read: never dedup against what was just spoken
  await narrate(out, true);
}