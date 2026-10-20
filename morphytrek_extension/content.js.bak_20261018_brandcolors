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

async function narrate(text) {
  if (!text || text.length < 5) return;
  
  const cleaned = cleanText(text);
  if (cleaned === lastSpoken) return;
  lastSpoken = cleaned;
  
  // SINGLE SEND - no chunking, no limit. The whole selection goes as one narration.
  chunkCounter++;
  await sendChunkToQueue(cleaned, `chrome_extension_${chunkCounter}`);
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
  _mtToast(mode === "articulate" ? "Articulating..." : "Promoting...");
  try {
    const url = mode === "articulate" ? MT_ARTICULATE_URL : MT_PROMOTE_URL;
    const data = await _mtPost(url, { text: text });
    if (!data.success) {
      // REIMAGINE-CLASS GATE (operator order 2026-10-16): the server marks
      // upgrade-gated features with upgrade:true — small notification only.
      if (data.upgrade) { _mtToast("Requires upgrade"); return; }
      throw new Error(data.error || "server error");
    }
    let out = mode === "articulate" ? data.text : data.md;
    if (mode === "promote" && ed.isContentEditable && data.html) {
      _mtUndo = { ed: ed, html: ed.innerHTML };
      const ok = document.execCommand("insertHTML", false, data.html);
      if (ok) { _mtToast("Promoted - Ctrl+Z to revert", _mtRevert); return; }
    }
    const done = _mtReplaceSelection(ed, out);
    if (done) _mtToast(mode === "articulate" ? "Articulated - Ctrl+Z reverts" : "Promoted - Ctrl+Z reverts", _mtRevert);
    else _mtToast("Could not replace text in this field");
  } catch (e) {
    _mtToast("morPHYtrek bridge offline - is the app running?");
  }
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "ARTICULATE_SELECTION") _mtRun("articulate");
  if (msg.type === "PROMOTE_FIELD") _mtRun("promote");
});