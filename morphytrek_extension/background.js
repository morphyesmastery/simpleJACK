// ============================================================
// morPHYtrek Chrome Extension — Background Service Worker
// ============================================================

// Register menus on EVERY service worker wake (removeAll first) —
// onInstalled alone left Articulate/Promote missing from the running
// Chrome because the menus were only created at install time.
function registerMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "morphytrek-read-selection",
      title: "Read aloud (morPHYtrek)",
      contexts: ["selection"]
    });

    chrome.contextMenus.create({
      id: "morphytrek-read-page",
      title: "Read page (morPHYtrek)",
      contexts: ["page"]
    });

    // Translate the writing to English and read it aloud (Trent 2026-09-29)
    chrome.contextMenus.create({
      id: "morphytrek-translate-read",
      title: "Translate with morPHYtrek",
      contexts: ["selection"]
    });

    // Articulate / Promote - offline text cleanup via morPHYtrek bridge
    chrome.contextMenus.create({
      id: "morphytrek-articulate",
      title: "Articulate with morPHYtrek",
      contexts: ["selection"]
    });
    chrome.contextMenus.create({
      id: "morphytrek-promote",
      title: "Promote with morPHYtrek",
      contexts: ["editable"]
    });

    // Reimagine - the model sees the dump and conveys it into attractive
    // language (Trent 2026-10-01). Fires as many times as the user wants.
    chrome.contextMenus.create({
      id: "morphytrek-reimagine",
      title: "Reimagine with morPHYtrek",
      contexts: ["selection"]
    });
  });
  console.log("[morPHYtrek] Context menus registered");
}
registerMenus();

// Create right-click context menu on install
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({ morphyeo_enabled: false });
  registerMenus();
});

// SELF-HEALING SEND (operator order 2026-10-18): after an extension reload,
// every tab opened BEFORE the reload holds orphaned content scripts — their
// messaging line is dead, so menu clicks vanished silently into
// .catch(()=>{}). Now: on failure, inject the fresh scripts into the tab
// and retry once. Old tabs heal on first click. No more dead menus.
async function mtSend(tabId, msg) {
  try {
    await chrome.tabs.sendMessage(tabId, msg);
  } catch (_) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tabId },
        files: ["translate.js", "content.js"]
      });
      await chrome.tabs.sendMessage(tabId, msg);
    } catch (_) {} // chrome:// pages and the Web Store can't be injected
  }
}

// Handle context menu clicks
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "morphytrek-read-selection") {
    mtSend(tab.id, { type: "NARRATE_TEXT", text: info.selectionText });
  }
  if (info.menuItemId === "morphytrek-read-page") {
    mtSend(tab.id, { type: "READ_PAGE" });
  }
  if (info.menuItemId === "morphytrek-translate-read") {
    mtSend(tab.id, { type: "TRANSLATE_READ", text: info.selectionText });
  }
  if (info.menuItemId === "morphytrek-articulate") {
    mtSend(tab.id, { type: "ARTICULATE_SELECTION" });
  }
  if (info.menuItemId === "morphytrek-promote") {
    mtSend(tab.id, { type: "PROMOTE_FIELD" });
  }
  if (info.menuItemId === "morphytrek-reimagine") {
    mtSend(tab.id, { type: "REIMAGINE_SELECTION" });
  }
});

// Toggle on icon click
chrome.action.onClicked.addListener((tab) => {
  chrome.storage.local.get(["morphyeo_enabled"], (result) => {
    const newState = !result.morphyeo_enabled;
    chrome.storage.local.set({ morphyeo_enabled: newState });
    
    // Notify active tab
    chrome.tabs.query({active: true, currentWindow: true}, (tabs) => {
      if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, {
          type: "SET_ENABLED",
          enabled: newState
        }).catch(() => {});
      }
    });
    
    // Update badge
    chrome.action.setBadgeText({ text: newState ? "ON" : "" });
    chrome.action.setBadgeBackgroundColor({ color: "#4ade80" });
  });
});
