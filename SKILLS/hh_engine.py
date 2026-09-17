"""HH_ENGINE — semantic alignment rubric (anchor 2026-09-15; amended same day by
operator ruling: 'must have words' are a terrible idea and inconsistent with
advanced intelligence. ALL keyword counting removed. Alignment and consistency
are now judged by a GLM semantic judge that READS the ad's requirements, the
resume, and the answers together, scoring meaning and understanding — never
word overlap. Timing stays deterministic math: quick AND correct.)

Machine side: resume in -> capability consistency vs the ad's requirements;
interview answers vs how the employer wants it answered; per-question response
timing. The employer provides questions + wanted answers in the portal; the
page does the rest. Stdlib only (urllib). Consumed by hireharbor.py /api/apply.
Signature of compute_rubric UNCHANGED — zero caller lines touched.

Honesty law: if the semantic judge is unreachable or returns garbage, those
components score None — never invented, never a silent fallback to counting.
"""
import json, os, re, urllib.request

ZAI_ENDPOINT = "https://api.z.ai/api/coding/paas/v4/chat/completions"
ZAI_MODEL = os.environ.get("HH_JUDGE_MODEL", "glm-5-turbo")

_JLOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hh_judge.log")

def _jlog_line(msg):
    try:
        with open(_JLOG, "a", encoding="utf-8") as f:
            import datetime as _dt
            f.write(_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass

SYSTEM_PROMPT = (
    "You are a hiring rubric judge for skilled-trade applicants. You judge MEANING, "
    "never keyword matching. A paraphrase in the applicant's own words that shows real "
    "understanding scores HIGH. A string of buzzwords with no substance scores LOW. "
    "You reply with ONLY valid JSON. No markdown, no prose outside the JSON."
)

def _judge_prompt(cards, answers, must_list, resume_text):
    rows = []
    for i, c in enumerate(cards):
        q = ((c.get("title") or "") + " - " + (c.get("text") or ""))[:400]
        want = (c.get("want") or "").strip()
        ans = answers[i].strip() if i < len(answers) and isinstance(answers[i], str) else ""
        if not ans:
            ans = "(no answer given)"
        rows.append("QUESTION %d:\n  SCENARIO: %s\n  EMPLOYER WANTED TO HEAR: %s\n  APPLICANT ANSWERED: %s"
                    % (i + 1, q, want if want else "(employer gave no key points)", ans[:1500]))
    req = "; ".join((m or "").strip() for m in (must_list or []) if (m or "").strip()) or "(none listed)"
    n = len(cards)
    return (
        "Judge this applicant for a skilled-trade job.\n\n"
        "AD REQUIREMENTS: %s\n\nAPPLICANT RESUME:\n%s\n\nINTERVIEW:\n%s\n\n"
        "TASK 1 - ALIGNMENT: for each of the %d questions, score 0-100 how well the "
        "answer delivers what the employer wanted to hear, judging comprehension, "
        "specificity, and judgment. If the employer gave no key points for a question, "
        "score it null.\n\n"
        "TASK 2 - CONSISTENCY: one score 0-100 for how well the applicant's demonstrated "
        "understanding across ALL answers matches the capabilities the resume claims and "
        "the ad requires. Someone whose resume claims expertise their answers cannot "
        "support scores LOW even if every word matches. Someone who proves real "
        "understanding in plain language scores HIGH.\n\n"
        "Reply with ONLY this JSON shape:\n"
        '{"alignment": [<int 0-100 or null> x %d], "consistency": <int 0-100 or null>, '
        '"note": "<one sentence, plain language, for the employer>"}'
        % (req, (resume_text or "")[:3000], "\n".join(rows), n, n)
    )

def glm_semantic(cards, answers, must_list, resume_text, timeout=60):
    """One call, whole picture. Returns dict with per-question alignment ints,
    consistency int, and a note - or None on ANY failure. Never invents scores."""
    key = os.environ.get("ZAI_API_KEY") or os.environ.get("CUSTOM_Z_AI_API_KEY") or ""
    if not key:
        _jlog_line("FAIL no API key in server environment")
        return None
    if not cards:
        _jlog_line("FAIL no cards")
        return None
    payload = {"model": ZAI_MODEL,
               "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": _judge_prompt(cards, answers, must_list, resume_text)}],
               "temperature": 0.2, "max_tokens": 1024}
    req = urllib.request.Request(
        ZAI_ENDPOINT, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        txt = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            _jlog_line("FAIL no JSON in reply, raw head: " + txt[:160].replace("\n", " "))
            return None
        out = json.loads(m.group(0))
    except Exception as e:
        _jlog_line("FAIL " + type(e).__name__ + ": " + str(e)[:200])
        return None
    n = len(cards)
    def clamp(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return max(0, min(100, int(round(v))))
    raw_align = out.get("alignment")
    align = [clamp(raw_align[i]) if isinstance(raw_align, list) and i < len(raw_align) else None
             for i in range(n)]
    cons = clamp(out.get("consistency"))
    note = out.get("note") if isinstance(out.get("note"), str) else ""
    return {"alignment": align, "consistency": cons, "note": note[:300]}



def timing_pts(secs, lo, hi):
    """Quick AND correct: inside window = 100. Suspiciously fast = canned/spam smell,
    capped 55 sliding to 30 as it approaches zero reading time. Late = still counts,
    decays toward floor 30 — slow thinking is not no thinking. None secs -> None.
    (Kept VERBATIM from the verified engine — deterministic math, not word counting.)"""
    if secs is None:
        return None
    lo = 8 if lo is None else max(1.0, float(lo))
    hi = 300 if hi is None else max(float(lo) + 1.0, float(hi))
    if secs < lo:
        return max(30, int(round(55 * (secs / lo))))
    if secs <= hi:
        return 100
    decay = min(1.0, (secs - hi) / hi)   # at 2x the window -> floor
    return int(round(100 - (70 * decay)))


def compute_rubric(cards, answers, times, must_list, resume_text):
    """THE SEMANTIC RUBRIC (operator ruling 2026-09-15: keyword counting is dead).
    alignment = GLM judge reads each answer against what the employer wanted to hear;
    timing = deterministic quick-AND-correct math (kept verbatim);
    consistency = GLM judge reads the WHOLE interview against the resume's claimed
    capabilities and the ad's requirements — meaning, never word overlap.
    Signature UNCHANGED. Output keys UNCHANGED (per_question/alignment/timing/
    consistency/caps/score) so hireharbor.py pages need zero edits. 'caps' is
    kept as an always-empty list purely for shape compatibility — the concept
    of extracted keywords is retired. Judge down -> those components are None;
    timing still scores. Missing components are never invented."""
    judge = glm_semantic(cards, answers, must_list, resume_text)
    judge_align = judge["alignment"] if judge else [None] * len(cards)
    per = []
    for i, c in enumerate(cards):
        t = times[i] if i < len(times) and isinstance(times[i], (int, float)) else None
        per.append({"q": i + 1, "title": (c.get("title") or "")[:60],
                    "alignment": judge_align[i],
                    "timing": timing_pts(t, c.get("lo"), c.get("hi")),
                    "secs": round(t, 1) if t is not None else None})
    av = [p["alignment"] for p in per if p["alignment"] is not None]
    align = int(round(sum(av) / len(av))) if av else None
    tv = [p["timing"] for p in per if p["timing"] is not None]
    tim = int(round(sum(tv) / len(tv))) if tv else None
    cons = judge["consistency"] if judge else None
    note = judge["note"] if judge else ""
    comp = [x for x in (align, tim, cons) if x is not None]
    total = int(round(sum(comp) / len(comp))) if comp else None
    return {"per_question": per, "alignment": align, "timing": tim,
            "consistency": cons, "caps": [], "note": note, "score": total}
