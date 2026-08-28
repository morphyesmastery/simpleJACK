// ════════════════════════════════════════════════════════════
// morPHYtrek Chrome Extension — Background Service Worker
// ════════════════════════════════════════════════════════════

// Create right-click context menu on install
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({ morphyeo_enabled: false });
  
  // Create context menu for selected text
  chrome.contextMenus.create({
    id: "morphytrek-read-selection",
    title: "🔊 Read aloud (morPHYtrek)",
    contexts: ["selection"]
  });
  
  // Context menu for page
  chrome.contextMenus.create({
    id: "morphytrek-read-page",
    title: "🔊 Read page (morPHYtrek)",
    contexts: ["page"]
  });
  
  console.log("[morPHYtrek] Extension installed, context menus created");
});

// Handle context menu clicks
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "morphytrek-read-selection") {
    chrome.tabs.sendMessage(tab.id, { type: "NARRATE_TEXT", text: info.selectionText });
  }
  if (info.menuItemId === "morphytrek-read-page") {
    chrome.tabs.sendMessage(tab.id, { type: "READ_PAGE" });
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
