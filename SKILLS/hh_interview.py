"""HH_INTERVIEW — the full IceTech-style cinematic role-play interview, rendered for HireHarbor.
Concept source of record: C:\\Users\\daddy\\Desktop\\icetech-site\\PREINTERVIEW.HTML (read live 2026-09-15).
Adapted: trade-generic scenario bank (10 cards), Web Speech narration, animated waveform,
particles, progress bar + dots, start overlay (name/contact), mono preview on submit.
Applicant answers POST urlencoded to /api/apply as one combined resume_text -> v3 scorer.
Placeholders: __ROLE__ __SALARY__ __REQID__
"""

PAGE_INTERVIEW = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Role-Play Interview — __ROLE__</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
:root {
  --hh-teal: #00BCD4; --hh-teal-bright: #26C6DA; --hh-teal-dim: #00838F; --hh-teal-dark: #006064;
  --bg: #070d0f; --bg-panel: #0c1619; --bg-elevated: #111f23;
  --border: #162a2e; --border-hover: #1e3a40;
  --text: #e0f2f1; --text-dim: #80cbc4; --text-muted: #4db6ac;
  --glow: rgba(0,188,212,0.12); --glow-strong: rgba(0,188,212,0.25);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text);
  min-height: 100vh; display: flex; flex-direction: column; align-items: center;
  overflow-x: hidden; position: relative;
}
body::before {
  content: ''; position: fixed; inset: 0; background:
    radial-gradient(ellipse at 15% 85%, rgba(0,188,212,0.04) 0%, transparent 50%),
    radial-gradient(ellipse at 85% 15%, rgba(0,131,143,0.03) 0%, transparent 50%),
    radial-gradient(ellipse at 50% 50%, rgba(0,188,212,0.015) 0%, transparent 60%);
  pointer-events: none; z-index: 0;
}
.particles { position: fixed; inset: 0; pointer-events: none; z-index: 1; overflow: hidden; }
.particle {
  position: absolute; width: 2px; height: 2px; background: var(--hh-teal);
  border-radius: 50%; opacity: 0; animation: floatUp 14s infinite ease-in;
}
@keyframes floatUp {
  0% { transform: translateY(100vh) scale(0); opacity: 0; }
  12% { opacity: 0.35; } 88% { opacity: 0.15; }
  100% { transform: translateY(-10vh) scale(1); opacity: 0; }
}
.header { position: relative; z-index: 10; width: 100%; max-width: 860px; padding: 36px 24px 16px; text-align: center; }
.logo-area { display: flex; align-items: center; justify-content: center; gap: 12px; margin-bottom: 10px; }
.logo-icon {
  width: 42px; height: 42px; background: linear-gradient(135deg, var(--hh-teal), var(--hh-teal-dim));
  border-radius: 10px; display: flex; align-items: center; justify-content: center;
  font-size: 20px; box-shadow: 0 0 22px var(--glow);
}
.logo-text {
  font-size: 1.5rem; font-weight: 800; letter-spacing: -0.4px;
  background: linear-gradient(135deg, #fff 0%, var(--hh-teal) 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}
.tagline { font-size: 0.78rem; color: var(--text-dim); letter-spacing: 3.5px; text-transform: uppercase; font-weight: 500; }
.divider { width: 70px; height: 2px; background: linear-gradient(90deg, transparent, var(--hh-teal), transparent); margin: 14px auto 0; opacity: 0.4; }
.rolechip {
  display: inline-block; margin-top: 10px; font-size: 0.8rem; font-weight: 600;
  color: var(--hh-teal-bright); background: rgba(0,188,212,0.07);
  border: 1px solid rgba(0,188,212,0.18); border-radius: 20px; padding: 5px 14px; letter-spacing: 1px;
}
.progress-wrap { position: relative; z-index: 10; width: 100%; max-width: 720px; padding: 0 24px; margin-bottom: 24px; }
.progress-bar { height: 3px; background: var(--border); border-radius: 3px; overflow: hidden; }
.progress-fill {
  height: 100%; background: linear-gradient(90deg, var(--hh-teal-dim), var(--hh-teal));
  border-radius: 3px; transition: width 0.6s cubic-bezier(0.22, 1, 0.36, 1); box-shadow: 0 0 10px var(--glow);
}
.progress-label { text-align: center; font-size: 0.68rem; color: var(--text-muted); margin-top: 8px; letter-spacing: 2px; text-transform: uppercase; font-weight: 500; }
.card-stage { position: relative; z-index: 10; width: 100%; max-width: 720px; padding: 0 24px; flex: 1; display: flex; flex-direction: column; justify-content: center; min-height: 420px; }
.card {
  background: var(--bg-panel); border: 1px solid var(--border); border-radius: 16px;
  padding: 32px 30px; position: relative; overflow: hidden;
  box-shadow: 0 24px 70px rgba(0,0,0,0.45), 0 0 0 1px rgba(0,188,212,0.04), inset 0 1px 0 rgba(255,255,255,0.02);
  opacity: 0; transform: translateY(16px) scale(0.985);
  transition: all 0.5s cubic-bezier(0.22, 1, 0.36, 1);
}
.card.active { opacity: 1; transform: translateY(0) scale(1); }
.card::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, var(--hh-teal), transparent); opacity: 0.25;
}
.card-category {
  display: inline-flex; align-items: center; gap: 8px; font-size: 0.68rem; font-weight: 600;
  letter-spacing: 2.5px; text-transform: uppercase; color: var(--hh-teal); margin-bottom: 18px;
  padding: 5px 12px; background: rgba(0,188,212,0.06); border: 1px solid rgba(0,188,212,0.1);
  border-radius: 6px; width: fit-content;
}
.card-title { font-size: 1.25rem; font-weight: 700; line-height: 1.3; margin-bottom: 16px; color: #fff; }
.dialogue-box {
  background: var(--bg-elevated); border: 1px solid var(--border); border-left: 3px solid var(--hh-teal);
  border-radius: 10px; padding: 18px 20px; margin-bottom: 20px; font-size: 0.97rem;
  line-height: 1.65; color: var(--text-dim); min-height: 60px; position: relative;
}
.dialogue-box .cursor {
  display: inline-block; width: 2px; height: 1.1em; background: var(--hh-teal); margin-left: 2px;
  vertical-align: text-bottom; animation: blink 1s step-end infinite; box-shadow: 0 0 6px var(--hh-teal);
}
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
.speaker-label {
  display: flex; align-items: center; gap: 8px; font-size: 0.72rem; font-weight: 600;
  letter-spacing: 1.5px; text-transform: uppercase; color: var(--hh-teal); margin-bottom: 10px;
}
.answer-label {
  font-size: 0.75rem; font-weight: 600; letter-spacing: 1.5px; text-transform: uppercase;
  color: var(--text-muted); margin-bottom: 10px; display: flex; align-items: center; gap: 8px;
}
.answer-area {
  width: 100%; min-height: 120px; background: var(--bg-elevated); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px; color: var(--text); font-family: 'Inter', sans-serif;
  font-size: 0.95rem; line-height: 1.6; resize: vertical; transition: all 0.2s ease; outline: none;
}
.answer-area:focus {
  border-color: var(--hh-teal-dim);
  box-shadow: 0 0 0 3px rgba(0,188,212,0.08), inset 0 1px 0 rgba(255,255,255,0.02);
}
.audio-bar { display: flex; align-items: center; gap: 14px; margin-top: 20px; padding-top: 18px; border-top: 1px solid var(--border); }
.audio-btn {
  width: 40px; height: 40px; border-radius: 50%; border: 1px solid var(--border); background: var(--bg);
  color: var(--hh-teal); cursor: pointer; display: flex; align-items: center; justify-content: center;
  transition: all 0.2s ease; font-size: 14px; flex-shrink: 0;
}
.audio-btn:hover:not(:disabled) { background: rgba(0,188,212,0.08); border-color: var(--hh-teal-dim); box-shadow: 0 0 14px var(--glow); transform: scale(1.05); }
.audio-btn:disabled { opacity: 0.25; cursor: not-allowed; transform: none; }
.audio-wave { flex: 1; height: 26px; display: flex; align-items: center; gap: 3px; justify-content: center; }
.audio-wave span { width: 3px; background: var(--hh-teal); border-radius: 3px; opacity: 0.25; transition: all 0.15s ease; height: 4px; }
.audio-wave.playing span { animation: wave 0.5s ease-in-out infinite alternate; }
@keyframes wave { 0% { height: 4px; opacity: 0.25; } 100% { height: 20px; opacity: 0.85; } }
.audio-status { font-size: 0.7rem; color: var(--text-muted); letter-spacing: 1px; text-transform: uppercase; min-width: 70px; text-align: right; }
.nav-bar {
  position: relative; z-index: 10; width: 100%; max-width: 720px; padding: 24px 24px 40px;
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
}
.nav-btn {
  display: flex; align-items: center; gap: 8px; padding: 11px 22px; border-radius: 10px;
  border: 1px solid var(--border); background: var(--bg-panel); color: var(--text);
  font-family: 'Inter', sans-serif; font-size: 0.88rem; font-weight: 500; cursor: pointer;
  transition: all 0.2s ease;
}
.nav-btn:hover:not(:disabled) { background: rgba(0,188,212,0.06); border-color: var(--hh-teal-dim); box-shadow: 0 0 18px var(--glow); transform: translateY(-1px); }
.nav-btn:disabled { opacity: 0.2; cursor: not-allowed; }
.nav-btn.primary {
  background: linear-gradient(135deg, var(--hh-teal-dim), var(--hh-teal)); color: var(--bg);
  border: none; font-weight: 600;
}
.nav-btn.primary:hover:not(:disabled) { box-shadow: 0 0 25px var(--glow-strong); transform: translateY(-1px); }
.nav-dots { display: flex; gap: 8px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--border); border: none; cursor: pointer; transition: all 0.3s ease; }
.dot.active { background: var(--hh-teal); box-shadow: 0 0 8px var(--glow); transform: scale(1.2); }
.dot.visited { background: var(--hh-teal-dim); }
.start-overlay {
  position: fixed; inset: 0; background: rgba(7,13,15,0.94); backdrop-filter: blur(12px);
  z-index: 100; display: flex; align-items: center; justify-content: center; flex-direction: column;
  gap: 20px; transition: opacity 0.6s ease, visibility 0.6s ease;
}
.start-overlay.hidden { opacity: 0; visibility: hidden; pointer-events: none; }
.start-overlay .logo-area { margin-bottom: 4px; }
.start-overlay h1 { font-size: 1.4rem; font-weight: 700; color: #fff; }
.start-overlay p { color: var(--text-dim); font-size: 0.92rem; max-width: 420px; text-align: center; line-height: 1.6; }
.start-btn {
  margin-top: 4px; padding: 15px 44px; border-radius: 12px; border: none;
  background: linear-gradient(135deg, var(--hh-teal-dim), var(--hh-teal)); color: var(--bg);
  font-family: 'Inter', sans-serif; font-size: 1rem; font-weight: 700; cursor: pointer;
  transition: all 0.2s ease; box-shadow: 0 4px 25px rgba(0,188,212,0.2);
}
.start-btn:hover { transform: translateY(-2px); box-shadow: 0 8px 35px rgba(0,188,212,0.35); }
.applicant-fields { display: flex; gap: 12px; margin-top: 4px; flex-wrap: wrap; justify-content: center; }
.applicant-fields input {
  background: var(--bg-elevated); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 14px; color: var(--text); font-family: 'Inter', sans-serif; font-size: 0.9rem;
  width: 220px; outline: none; transition: all 0.2s ease;
}
.applicant-fields input:focus { border-color: var(--hh-teal-dim); box-shadow: 0 0 0 3px rgba(0,188,212,0.08); }
.submit-screen { text-align: center; padding: 20px 10px; }
.submit-screen h2 {
  font-size: 1.6rem; font-weight: 800; margin-bottom: 12px;
  background: linear-gradient(135deg, #fff, var(--hh-teal));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}
.submit-screen p { color: var(--text-dim); line-height: 1.7; max-width: 480px; margin: 0 auto 24px; font-size: 0.95rem; }
.submit-btn {
  padding: 14px 40px; border-radius: 10px; border: none;
  background: linear-gradient(135deg, var(--hh-teal-dim), var(--hh-teal)); color: var(--bg);
  font-family: 'Inter', sans-serif; font-size: 1rem; font-weight: 700; cursor: pointer;
  transition: all 0.2s ease; box-shadow: 0 4px 20px rgba(0,188,212,0.2); margin-bottom: 14px;
}
.submit-btn:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 8px 30px rgba(0,188,212,0.35); }
.submit-btn:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }
.secondary-btn {
  padding: 10px 28px; border-radius: 10px; border: 1px solid var(--border); background: transparent;
  color: var(--text-dim); font-family: 'Inter', sans-serif; font-size: 0.85rem; font-weight: 500;
  cursor: pointer; transition: all 0.2s ease; margin: 0 6px;
}
.secondary-btn:hover { border-color: var(--hh-teal-dim); color: var(--hh-teal); background: rgba(0,188,212,0.04); }
.preview-panel {
  background: var(--bg-elevated); border: 1px solid var(--border); border-radius: 12px;
  padding: 24px; margin-top: 20px; text-align: left; max-height: 320px; overflow-y: auto;
  font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; line-height: 1.7;
  color: var(--text-dim); white-space: pre-wrap;
}
.preview-panel::-webkit-scrollbar { width: 6px; }
.preview-panel::-webkit-scrollbar-track { background: transparent; }
.preview-panel::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
.toast {
  position: fixed; bottom: 30px; left: 50%; transform: translateX(-50%) translateY(20px);
  background: var(--bg-elevated); border: 1px solid var(--hh-teal-dim); color: var(--hh-teal);
  padding: 12px 24px; border-radius: 10px; font-size: 0.88rem; font-weight: 500; z-index: 200;
  opacity: 0; transition: all 0.3s ease; pointer-events: none; box-shadow: 0 8px 30px rgba(0,0,0,0.4);
}
.toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
@media (max-width: 600px) {
  .card { padding: 24px 18px; } .card-title { font-size: 1.1rem; }
  .nav-bar { flex-wrap: wrap; justify-content: center; }
  .nav-dots { order: -1; width: 100%; justify-content: center; margin-bottom: 8px; }
  .applicant-fields input { width: 100%; }
}
</style>
</head>
<body>

<div class="particles" id="particles"></div>
<div class="toast" id="toast"></div>

<div class="start-overlay" id="startOverlay">
  <div class="logo-area">
    <div class="logo-icon">&#9878;&#65039;</div>
    <div class="logo-text">HireHarbor</div>
  </div>
  <h1>Role-Play Interview</h1>
  <p>You&rsquo;re applying with <b>__COMPANY__</b> for: <b>__ROLE__</b> &mdash; __SALARY__<br>
  This is an interactive walkthrough of real situations on the job.<br>Please enable your sound. Fill in your details to begin.</p>
  <div class="applicant-fields">
    <input type="text" id="applicantName" placeholder="Your Name" autocomplete="name">
    <input type="text" id="applicantContact" placeholder="Email or Phone" autocomplete="email">
  </div>
  <button class="start-btn" id="startBtn" onclick="beginExperience()">Begin Interview</button>
</div>

<div class="header">
  <div class="logo-area">
    <div class="logo-icon">&#9878;&#65039;</div>
    <div class="logo-text">HireHarbor</div>
  </div>
  <div class="tagline">Real Situations &middot; Real Answers &middot; Real People</div>
  <div class="divider"></div>
  <div class="rolechip">Applying: __ROLE__ &mdash; __SALARY__</div>
</div>

<div class="progress-wrap">
  <div class="progress-bar">
    <div class="progress-fill" id="progressFill" style="width: 0%"></div>
  </div>
  <div class="progress-label" id="progressLabel">Question 1 of __NCARDS__</div>
</div>

<div class="card-stage" id="cardStage"></div>

<div class="nav-bar" id="navBar">
  <button class="nav-btn" id="prevBtn" onclick="goPrev()" disabled>
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="15 18 9 12 15 6"/></svg>
    Previous
  </button>
  <div class="nav-dots" id="navDots"></div>
  <button class="nav-btn primary" id="nextBtn" onclick="goNext()">
    Next
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="9 18 15 12 9 6"/></svg>
  </button>
</div>

</body>
</html>

<script>
const CARDS = __CARDS_JSON__;
const N = CARDS.length;
let cur = 0, answers = new Array(N).fill(""), applicant = {name: "", contact: ""};
let times = new Array(N).fill(0), renderAt = Date.now();  /* rubric: seconds spent per card */
let typeTimer = null, currentUtterance = null;

/* particles */
(function () {
  const holder = document.getElementById("particles");
  for (let i = 0; i < 28; i++) {
    const p = document.createElement("div");
    p.className = "particle";
    p.style.left = Math.random() * 100 + "%";
    p.style.animationDelay = (Math.random() * 14) + "s";
    p.style.animationDuration = (9 + Math.random() * 10) + "s";
    holder.appendChild(p);
  }
})();

function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2600);
}

function beginExperience() {
  const n = document.getElementById("applicantName").value.trim();
  const c = document.getElementById("applicantContact").value.trim();
  if (!n || !c) { showToast("Please add your name and a way to reach you."); return; }
  applicant = {name: n, contact: c};
  document.getElementById("startOverlay").classList.add("hidden");
  renderCard(0);
}

function speakerLabelFor(s) {
  return s === "customer" ? "&#128222; The Customer" :
         s === "foreman" ? "&#128119; The Foreman" :
         s === "caller"  ? "&#128222; The Caller" : "&#127970; The Situation";
}

function renderCard(i) {
  stopSpeech(); clearInterval(typeTimer);
  const card = CARDS[i];
  const stage = document.getElementById("cardStage");
  stage.innerHTML =
    '<div class="card active">' +
      '<div class="card-category">' + card.category + '</div>' +
      '<div class="card-title">' + card.title + '</div>' +
      '<div class="speaker-label">' + speakerLabelFor(card.speaker) + '</div>' +
      '<div class="dialogue-box"><span id="typed"></span><span class="cursor"></span></div>' +
      '<div class="answer-label">&#9997;&#65039; ' + card.answerLabel + '</div>' +
      '<textarea class="answer-area" id="answerArea" placeholder="Answer in your own words...">' +
        answers[i].replace(/&/g, "&amp;").replace(/</g, "&lt;") + '</textarea>' +
      '<div class="audio-bar">' +
        '<button class="audio-btn" id="replayBtn" onclick="speakCard()" title="Replay narration">&#9654;&#65039;</button>' +
        '<div class="audio-wave" id="wave">' + '<span></span>'.repeat(24) + '</div>' +
        '<div class="audio-status" id="audioStatus">READY</div>' +
      '</div>' +
    '</div>';
  document.getElementById("answerArea").addEventListener("input", function () { answers[i] = this.value; });
  typewrite(card.dialogue);
  speakCard();
  /* progress + dots */
  document.getElementById("progressFill").style.width = ((i) / N * 100) + "%";
  document.getElementById("progressLabel").textContent = "Question " + (i + 1) + " of " + N;
  const dots = document.getElementById("navDots");
  dots.innerHTML = "";
  for (let k = 0; k < N; k++) {
    const d = document.createElement("button");
    d.className = "dot" + (k === i ? " active" : (answers[k] ? " visited" : ""));
    d.onclick = function () { stampCur(); cur = k; renderCard(k); };
    dots.appendChild(d);
  }
  document.getElementById("prevBtn").disabled = (i === 0);
  document.getElementById("nextBtn").innerHTML = (i === N - 1)
    ? 'Finish <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="9 18 15 12 9 6"/></svg>'
    : 'Next <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="9 18 15 12 9 6"/></svg>';
  renderAt = Date.now();
}

function typewrite(text) {
  const el = document.getElementById("typed");
  let pos = 0;
  el.textContent = "";
  clearInterval(typeTimer);
  typeTimer = setInterval(() => {
    el.textContent = text.slice(0, ++pos);
    if (pos >= text.length) clearInterval(typeTimer);
  }, 16);
}

function stopSpeech() {
  if (window.speechSynthesis) speechSynthesis.cancel();
  currentUtterance = null;
  const w = document.getElementById("wave"), s = document.getElementById("audioStatus");
  if (w) w.classList.remove("playing");
  if (s) s.textContent = "READY";
}

function speakCard() {
  if (!window.speechSynthesis) {
    const s = document.getElementById("audioStatus");
    if (s) s.textContent = "NO VOICE";
    return;
  }
  stopSpeech();
  const card = CARDS[cur];
  const u = new SpeechSynthesisUtterance(card.dialogue);
  u.rate = 1.55; u.pitch = 1.02;
  u.onstart = function () {
    const w = document.getElementById("wave"), s = document.getElementById("audioStatus");
    if (w) w.classList.add("playing");
    if (s) s.textContent = "SPEAKING";
  };
  u.onend = u.onerror = function () {
    const w = document.getElementById("wave"), s = document.getElementById("audioStatus");
    if (w) w.classList.remove("playing");
    if (s) s.textContent = "READY";
  };
  currentUtterance = u;
  speechSynthesis.speak(u);
}

function stampCur() { times[cur] += (Date.now() - renderAt) / 1000; renderAt = Date.now(); }

function goPrev() { if (cur > 0) { stampCur(); cur--; renderCard(cur); } }

function goNext() {
  if (cur < N - 1) { stampCur(); cur++; renderCard(cur); }
  else { finishInterview(); }
}

function finishInterview() {
  const missing = answers.filter(a => !a.trim()).length;
  if (missing > 0) { showToast(missing + " question" + (missing > 1 ? "s" : "") + " still need an answer."); return; }
  stopSpeech();
  let txt = "ROLE-PLAY INTERVIEW — " + document.title.replace("Role-Play Interview — ", "") + "\n";
  txt += "Applicant: " + applicant.name + " <" + applicant.contact + ">\n";
  CARDS.forEach((c, i) => {
    txt += "\n=== Q" + (i + 1) + " [" + c.category + "] " + c.title + " ===\n";
    txt += "Q: " + c.dialogue + "\n";
    txt += "A: " + answers[i].trim() + "\n";
  });
  stampCur();
  const body = new URLSearchParams({
    req_id: "__REQID__", name: applicant.name, contact: applicant.contact, resume: txt,
    qa: JSON.stringify({answers: answers, times: times})
  });
  const btn = document.getElementById("nextBtn");
  if (btn) { btn.disabled = true; }
  fetch("/api/apply", { method: "POST", headers: {"Content-Type": "application/x-www-form-urlencoded"}, body: body.toString() })
    .then(r => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      const stage = document.getElementById("cardStage");
      stage.innerHTML =
        '<div class="card active"><div class="submit-screen">' +
        '<h2>Application Received</h2>' +
        '<p>Thank you, ' + applicant.name.replace(/</g, "&lt;") + '. Your role-play answers are in. The owner reviews ranked applicants and will reach you at <b>' + applicant.contact.replace(/</g, "&lt;") + '</b>.</p>' +
        '<div class="preview-panel">' + txt.replace(/&/g, "&amp;").replace(/</g, "&lt;") + '</div>' +
        '</div></div>';
      document.getElementById("navBar").style.display = "none";
      document.getElementById("progressFill").style.width = "100%";
      document.getElementById("progressLabel").textContent = "COMPLETE";
    })
    .catch(e => {
      showToast("Submission failed — check your connection and try again.");
      if (btn) { btn.disabled = false; }
    });
}
</script>
</body>
</html>
"""

# ==================== SCENARIO BANK ====================
# Modeled on the IceTech PREINTERVIEW.HTML card patterns (read live 2026-09-15),
# generalized so any trade can use them. Speaker: customer | caller | foreman | narrator.

INTERVIEW_CARDS = [
    {"category": "Customer Service", "title": "The Angry Customer", "speaker": "customer",
     "dialogue": "I called you guys this morning and was told someone would be here before noon. It is now two-thirty, nobody has shown up or even called, and this problem is costing me business every hour. Tell me exactly what you are going to do about it.",
     "answerLabel": "Type your response:"},
    {"category": "Customer Service", "title": "The Broken Promise", "speaker": "caller",
     "dialogue": "Someone from your company promised me a callback by the end of the day yesterday. I waited, I called twice, and I still know nothing about when my repair is happening. Why should I keep waiting?",
     "answerLabel": "Type your response:"},
    {"category": "Priorities & Triage", "title": "Two Jobs, One Morning", "speaker": "narrator",
     "dialogue": "A customer calls in a panic: something critical just failed and product or property is at risk. At the same time, your schedule shows three routine jobs already lined up for the morning. Walk me through how you prioritize, who gets bumped, and what you tell everyone.",
     "answerLabel": "Type your plan:"},
    {"category": "Communication", "title": "The Unreachable Coworker", "speaker": "narrator",
     "dialogue": "You need a coworker to switch to an urgent job, but they are not answering their phone, radio, or messages. The affected customer is calling every ten minutes. What concrete steps do you take to reach your coworker and keep the customer calm in the meantime?",
     "answerLabel": "Type your step-by-step response:"},
    {"category": "Multitasking", "title": "The Busy Hour", "speaker": "narrator",
     "dialogue": "You are mid-conversation with a customer about a billing problem. At the same moment a coworker texts you about an urgent breakdown, and a new customer walks in the door needing help. Walk me through the exact order you handle these in, and why.",
     "answerLabel": "Type your answer:"},
    {"category": "Safety", "title": "The Shortcut", "speaker": "foreman",
     "dialogue": "We are behind schedule and the customer is pushing hard. The safe way to finish this job takes two more hours. Someone suggests skipping the lockout step to save time. What do you do, and what do you say?",
     "answerLabel": "Type your response:"},
    {"category": "Honesty", "title": "The Mistake", "speaker": "foreman",
     "dialogue": "You damage a part on a job. Nobody saw it happen, and it would probably go unnoticed until after you have left the site. What do you do, and what exactly do you say to me about it?",
     "answerLabel": "Type your answer:"},
    {"category": "Reliability", "title": "The Morning You Cannot Make", "speaker": "foreman",
     "dialogue": "It is one hour before your shift and your ride has fallen through. The crew is counting on you today and there is no one to cover. What do you do, in what order, and what do you tell me?",
     "answerLabel": "Type your answer:"},
    {"category": "Learning", "title": "The Task You Have Never Done", "speaker": "narrator",
     "dialogue": "You are asked to do a task you have watched others do but have never done yourself. There is no one free to walk you through it right now, and the customer is waiting. What is your approach, step by step?",
     "answerLabel": "Type your approach:"},
    {"category": "Pride of Work", "title": "Why This Work", "speaker": "narrator",
     "dialogue": "Tell me why you want to work in this trade. What does a genuinely good day at work look like for you, and how do you know at the end of the day that you did it right?",
     "answerLabel": "Type your answer:"},
]
