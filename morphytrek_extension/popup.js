
  const btn = document.getElementById("toggleBtn");
  const status = document.getElementById("status");
  
  function updateUI(enabled) {
    btn.textContent = enabled ? "Disable" : "Enable";
    status.textContent = enabled ? "✅ Listening for new messages" : "Disabled";
  }
  
  chrome.storage.local.get(["morphyeo_enabled"], (result) => {
    updateUI(result.morphyeo_enabled || false);
  });
  
  btn.addEventListener("click", () => {
    chrome.storage.local.get(["morphyeo_enabled"], (result) => {
      const newState = !result.morphyeo_enabled;
      chrome.storage.local.set({ morphyeo_enabled: newState });
      
      chrome.tabs.query({active: true, currentWindow: true}, (tabs) => {
        if (tabs[0]) {
          chrome.tabs.sendMessage(tabs[0].id, { type: "SET_ENABLED", enabled: newState });
        }
      });
      
      updateUI(newState);
    });
  });
  
  document.getElementById("readSelection").addEventListener("click", () => {
    chrome.tabs.query({active: true, currentWindow: true}, (tabs) => {
      if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, { type: "READ_SELECTION" });
      }
    });
  });
  
  document.getElementById("readPage").addEventListener("click", () => {
    chrome.tabs.query({active: true, currentWindow: true}, (tabs) => {
      if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, { type: "READ_PAGE" });
      }
    });
  });
  
  // ── MIC TOGGLE ──────────────────────────────────────────────
  const micBtn = document.getElementById("micBtn");
  const micStatus = document.getElementById("micStatus");
  let micOn = false;
  
  function updateMicUI(active) {
    micOn = active;
    micBtn.textContent = active ? "Stop mic" : "Start mic";
    micStatus.textContent = active
      ? "🎤 Listening — speak, words go to the narration queue (if the folder exists)"
      : "Mic off — speak to send words to the narration queue";
  }
  
  micBtn.addEventListener("click", () => {
    chrome.tabs.query({active: true, currentWindow: true}, (tabs) => {
      if (!tabs[0]) return;
      const next = !micOn;
      chrome.tabs.sendMessage(tabs[0].id, { type: "SET_MIC", active: next }, (resp) => {
        if (chrome.runtime.lastError) {
          micStatus.textContent = "Reload this tab, then try again";
          return;
        }
        updateMicUI(resp && resp.active !== undefined ? resp.active : next);
      });
    });
  });

  // ── TRANSLATOR ROW — own translator, language dropdown ──────
  const transBtn = document.getElementById("transBtn");
  const langSel = document.getElementById("langSel");
  const transStatus = document.getElementById("transStatus");

  function transUI(on) {
    transBtn.textContent = on ? "On" : "Off";
    const langName = langSel.options[langSel.selectedIndex].text;
    transStatus.textContent = on
      ? "🌐 On — reads aloud in " + langName + " (free lane, no key)"
      : "Off — reads the original language";
  }

  chrome.storage.local.get(["morphyeo_translate", "morphyeo_lang"], (r) => {
    langSel.value = r.morphyeo_lang || "en";
    transUI(!!r.morphyeo_translate);
  });

  transBtn.addEventListener("click", () => {
    chrome.storage.local.get(["morphyeo_translate"], (r) => {
      const next = !r.morphyeo_translate;
      chrome.storage.local.set({ morphyeo_translate: next });
      transUI(next);
    });
  });

  langSel.addEventListener("change", () => {
    chrome.storage.local.set({ morphyeo_lang: langSel.value });
    chrome.storage.local.get(["morphyeo_translate"], (r) => transUI(!!r.morphyeo_translate));
  });

  // ── PROMOTE CARD COLORS — three choosable colors; ink/muted are LAW ──
  // Defaults = operator brand law: navy #0f2340, orange #d97834, rust #b8401e.
  // Saved to chrome.storage.local AND pushed to the bridge (POST
  // /api/brand_colors) so _mt_promote renders the card with them instantly.
  // "Brand law" button returns to the law colors.
  // HARDENED: listeners attach FIRST; the colors fetch runs LAST in its own
  // catch — a dead bridge can never leave the buttons unwired again.
  const pcBg = document.getElementById("pcBg");
  const pcAction = document.getElementById("pcAction");
  const pcRust = document.getElementById("pcRust");
  const pcStatus = document.getElementById("pcStatus");
  const PC_LAW = { bg: "#0f2340", action: "#d97834", rust: "#b8401e" };
  const pcEls = { bg: pcBg, action: pcAction, rust: pcRust };

  function pcUI(c) { pcEls.bg.value = c.bg; pcEls.action.value = c.action; pcEls.rust.value = c.rust; }

  function pcSave() {
    const c = { bg: pcEls.bg.value, action: pcEls.action.value, rust: pcEls.rust.value };
    chrome.storage.local.set({ promote_bg: c.bg, promote_action: c.action, promote_rust: c.rust });
    pcStatus.textContent = "Saving...";
    fetch("http://localhost:8792/api/brand_colors", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(c)
    }).then(r => r.json()).then(j => {
      pcStatus.textContent = j.success
        ? "Saved — every promote card now uses these colors."
        : "Saved locally — bridge will pick them up on next start.";
    }).catch(() => { pcStatus.textContent = "Saved locally — bridge offline, colors apply on next start."; });
  }

  document.getElementById("pcSave").addEventListener("click", pcSave);
  document.getElementById("pcReset").addEventListener("click", () => { pcUI(PC_LAW); pcSave(); });

  // Load LAST: bridge is truth if alive (it serves what promote currently
  // uses); storage is the offline fallback; law is the floor.
  fetch("http://localhost:8792/api/brand_colors")
    .then(r => r.ok ? r.json() : Promise.reject())
    .then(c => pcUI({ bg: c.card_bg || c.bg || PC_LAW.bg, action: c.card_action || c.action || PC_LAW.action, rust: c.card_rust || c.rust || PC_LAW.rust }))
    .catch(() => chrome.storage.local.get(["promote_bg", "promote_action", "promote_rust"], (s) =>
      pcUI({ bg: s.promote_bg || PC_LAW.bg, action: s.promote_action || PC_LAW.action, rust: s.promote_rust || PC_LAW.rust })));
