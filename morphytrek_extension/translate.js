// ============================================================
// morPHYtrek Translator — the read-aloud lane's OWN translator.
// Free MyMemory lane (api.mymemory.translated.net) — CORS open,
// no key, proven live 2026-09-10 (autodetect TR->EN verified).
// Loads BEFORE content.js in the same isolated world, so
// window.MT_TRANSLATE is available to the narration code and
// to the popup. Law: on any failure, read the original text
// and SAY translation failed. Never silent, never fake.
// ============================================================
(function () {
  const MM = "https://api.mymemory.translated.net/get?q=";
  const LANGS = {
    "en": "English", "zh-CN": "Chinese", "es": "Spanish", "fr": "French",
    "de": "German", "it": "Italian", "pt": "Portuguese", "ru": "Russian",
    "ar": "Arabic", "hi": "Hindi", "ja": "Japanese", "ko": "Korean",
    "tr": "Turkish", "pl": "Polish", "uk": "Ukrainian", "vi": "Vietnamese",
    "fa": "Persian", "tl": "Filipino"
  };
  const cache = new Map();

  function isForeign(text) {
    const s = (text || "").slice(0, 600);
    const foreign = (s.match(/[\u0400-\u04FF\u0370-\u03FF\u0600-\u06FF\u0900-\u097F\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF\u0E00-\u0E7F]/g) || []).length;
    return foreign > Math.max(3, s.length * 0.04);
  }

  async function translateChunk(text, target) {
    const key = target + "|" + text;
    if (cache.has(key)) return cache.get(key);
    const q = encodeURIComponent(text.slice(0, 480));
    const r = await fetch(MM + q + "&langpair=Autodetect|" + encodeURIComponent(target));
    if (!r.ok) throw new Error("HTTP " + r.status);
    const j = await r.json();
    // JUNK GUARD (operator order 2026-10-18): MyMemory returns its own error
    // line ("PLEASE SELECT TWO DISTINCT LANGUAGES") as responseData on a 403
    // — e.g. English→English. That error text was being accepted as a
    // "translation", cached, narrated, and even PROMOTED. An error is never
    // a translation: refuse before it can be cached or spoken.
    if (j && j.responseStatus && String(j.responseStatus) !== "200") {
      throw new Error("MyMemory " + j.responseStatus + ": " + (j.responseDetails || "error"));
    }
    const out = j && j.responseData && j.responseData.translatedText
      ? String(j.responseData.translatedText) : "";
    if (!out) throw new Error("empty translation");
    if (/please\s+select|distinct\s+languages|INVALID\s+(SOURCE|TARGET)/i.test(out)) {
      throw new Error("MyMemory returned an error line, not a translation");
    }
    cache.set(key, out);
    return out;
  }

  async function forceTranslate(text, target) {
    const chunks = [];
    for (let i = 0; i < text.length; i += 480) chunks.push(text.slice(i, i + 480));
    const outs = [];
    for (const c of chunks) {
      outs.push(await translateChunk(c, target));
      await new Promise(res => setTimeout(res, 350));
    }
    return outs.join(" ");
  }

  async function maybeTranslate(text) {
    try {
      const st = await chrome.storage.local.get(["morphyeo_translate", "morphyeo_lang"]);
      if (!st.morphyeo_translate) return text;
      const target = st.morphyeo_lang || "en";
      if (target === "en" && !isForeign(text)) return text;
      return await forceTranslate(text, target);
    } catch (e) {
      console.log("[morPHYtrek] translate failed, reading original:", e.message);
      return text + " Translation unavailable, reading original.";
    }
  }

  window.MT_TRANSLATE = { translateChunk, forceTranslate, maybeTranslate, isForeign, LANGS };
  console.log("[morPHYtrek] translator loaded");
})();
