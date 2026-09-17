"""PROMORPHYES SCORER v3 — two-sided scoring engine.
APPLICANT SIDE: rank jobs by importance TO THE APPLICANT. Needs first, income
speed first, vampire detection. Big money is a tiebreaker, never a driver.
EMPLOYER SIDE (SaaS core): score_applicant() ranks applicants by employer
needs, with real-applicant vs noise/bot detection.
Refinements borrowed from github.com/MadsLorentzen/ai-job-search:
pass/fail GATES before the graded 0-100 score.

Usage:
  python _score_v3.py rescore           # offline: re-rank existing scored.json (FAST, no CDP)
  python _score_v3.py applicant <f>     # employer side: score applicant cards vs a job req file
"""
import json, sys, datetime, re

RESULTS_JSON = "C:/Users/daddy/Desktop/simplejack-tauri-COPY/portable/SimpleJack/SKILLS/promorphyes_scored.json"
OUT_MD  = "C:/Users/daddy/Desktop/simplejack-tauri-COPY/portable/SimpleJack/SKILLS/promorphyes_v3_ranked.md"
OUT_JSON = "C:/Users/daddy/Desktop/simplejack-tauri-COPY/portable/SimpleJack/SKILLS/promorphyes_v3_ranked.json"

# ---------------- GATES (pass/fail, applied BEFORE graded score) ----------------
PHYSICAL_BLOCKS = ["lifting", "50 lbs", "50lb", "physically demanding", "warehouse",
                   "forklift", "heavy equipment", "laborer", "labourer", "field service",
                   "must be able to lift", "stand for long periods"]

# ---------------- VAMPIRE TABLES (multiplied penalties, these SINK a job) ----------------
GHOST_JOB = {          # posting exists to harvest resumes / build lists
    "talent pool": 25, "evergreen": 20, "future opportunities": 20,
    "general application": 12, "open application": 12, "we are always": 10,
    "pipeline": 8,
}
INFO_HARVEST = {       # wants your data, offers nothing concrete
    "competitive salary": 8, "salary commensurate": 8, "to be discussed": 5,
    "unpaid assessment": 20, "unpaid trial": 25, "complete profile": 6,
    "market leading": 4, "dynamic team": 3, "rockstar": 8, "ninja": 8,
    "work hard play hard": 10, "family environment": 4,
}
COMMISSION_BAIT = {    # MLM / 100% commission dressed as a job
    "unlimited earning potential": 30, "be your own boss": 25,
    "residual income": 30, "commission only": 20, "100% commission": 25,
    "no experience required": 6, "self-starter": 4, "growth opportunity": 4,
}

# ---------------- REAL EMPLOYER SIGNALS (positive, small weights) ----------------
REAL_JOB = {
    "immediate start": 12, "start immediately": 12, "asap": 6, "right away": 8,
    "per diem": 3, "overtime available": 5, "overtime pay": 5,
    "$": 3, "per hour": 8, "annual salary": 8, "per year": 8,
    "paid training": 6, "benefits": 4, "dental": 3, "extended health": 3,
    "days": 2, "shift": 2, "full-time": 5, "full time": 5, "permanent": 4,
    "apply with resume": 3, "interview": 2, "references": 2,
    "company van": 4, "tool allowance": 3, "boot allowance": 3,
    "journeyman ticket": 6, "red seal": 6, "apprentices": 4,
}
NEEDS_POSITIVE = {     # applicant-side needs (Trent Brown): instructor/L&D/safety/remote/Calgary
    "instructional design": 14, "curriculum": 10, "instructor": 8, "trainer": 8,
    "training": 6, "learning and development": 10, "facilitat": 6,
    "safety": 8, "hse": 8, "ohs": 8, "compliance": 5,
    "e-learning": 10, "elearning": 8, "lms": 6,
    "remote": 10, "work from home": 10, "hybrid": 5,
    "calgary": 10, "alberta": 4,
    "python": 6, "automation": 6, "ai": 5, "data": 3,
    "consultant": 4, "advisor": 4, "coordinator": 3,
}

def _daily_equiv(salary):
    """Extract daily-dollar-equivalent. Unit patterns FIRST, fallback second."""
    if not salary:
        return 0
    s = salary.lower().replace(",", "")
    try:
        m = re.search(r"(\d+)\s*(?:/|per\s+|an?\s+)?\s*day", s)
        if m: return int(m.group(1))
        m = re.search(r"(\d+)\s*(?:/|per\s+|an?\s+)?\s*hour", s)
        if m: return int(m.group(1)) * 8
        m = re.search(r"(\d+)\s*(?:/|per\s+|an?\s+)?\s*year", s)
        if m: return int(int(m.group(1)) / 260)
        m = re.search(r"(\d+)\s*(?:/|per\s+|an?\s+)?\s*week", s)
        if m: return int(int(m.group(1)) / 5)
        m = re.search(r"(\d+)\s*(?:/|per\s+|an?\s+)?\s*month", s)
        if m: return int(int(m.group(1)) * 12 / 260)
        nums = [int(x) for x in re.findall(r"\d+", s)]
        if not nums:
            return 0
        n = max(nums)
        if "hour" in s:  return n * 8
        if "year" in s:  return int(n / 260)
        if "day" in s:   return n
        if "week" in s:  return int(n / 5)
        if "month" in s: return int(n * 12 / 260)
        return 0
    except Exception:
        return 0

def score_job_v3(title, company, description, salary):
    """APPLICANT SIDE. Returns dict with score 0-100, tier, flags, reasons."""
    text = (description + " " + title).lower()
    flags, reasons = [], []

    # --- GATES: pass/fail before any points ---
    for block in PHYSICAL_BLOCKS:
        if block in text:
            return {"score": 0, "tier": "BLOCKED", "flags": ["physical:" + block],
                    "reasons": ["Physical requirement gate: '" + block + "'"], "daily_equiv": _daily_equiv(salary)}

    # --- VAMPIRE penalties: multiply-count, they SINK postings ---
    vampire_pts, vamp_hits = 0, []
    for table in (GHOST_JOB, INFO_HARVEST, COMMISSION_BAIT):
        for kw, pts in table.items():
            if kw in text:
                vampire_pts += pts
                vamp_hits.append(kw)
    if vampire_pts:
        flags.append("VAMPIRE")
        reasons.append("Vampire signals: " + ", ".join(vamp_hits[:6]) + " (-" + str(vampire_pts) + ")")

    # --- REAL EMPLOYER signals ---
    real_pts, real_hits = 0, []
    for kw, pts in REAL_JOB.items():
        if kw in text:
            real_pts += pts
            real_hits.append(kw)
    if real_pts >= 10:
        flags.append("REAL-EMPLOYER")
        reasons.append("Real-employer signals: " + ", ".join(real_hits[:6]) + " (+" + str(real_pts) + ")")

    # --- NEEDS scoring: what matters TO THE APPLICANT ---
    need_pts, need_hits = 0, []
    for kw, pts in NEEDS_POSITIVE.items():
        if kw in text:
            need_pts += pts
            need_hits.append(kw)

    # --- SALARY: income speed, big money CAPPED (anchor: big money nearly irrelevant) ---
    daily = _daily_equiv(salary)
    sal_pts = 0
    if daily:
        if daily >= 400:   sal_pts = 10
        elif daily >= 300: sal_pts = 8
        elif daily >= 200: sal_pts = 5
        elif daily >= 150: sal_pts = 2
        else:              sal_pts = -6
        if daily >= 300:
            flags.append("PAYS-NOW")
        reasons.append("Daily-equiv $" + str(daily) + " -> salary weight " + str(sal_pts) + " (capped)")
    else:
        flags.append("NO-SALARY-POSTED")
        reasons.append("No salary posted: hides the money, small penalty (-4)")
        sal_pts = -4

    # --- Compose: base 50, needs dominate, salary capped, vampires sink ---
    raw = 50 + need_pts * 0.9 + real_pts * 0.6 + sal_pts * 0.7 - vampire_pts * 1.5
    score = int(max(0, min(100, round(raw))))

    if score >= 80:   tier = "INTERVIEW NOW"
    elif score >= 70: tier = "APPLY TODAY"
    elif score >= 55: tier = "KEEP WARM"
    elif score >= 40: tier = "LOW"
    else:             tier = "SINK"

    if need_hits:
        reasons.insert(0, "Needs match: " + ", ".join(need_hits[:8]))
    return {"score": score, "tier": tier, "flags": flags, "reasons": reasons, "daily_equiv": daily}

# ================= EMPLOYER SIDE (SaaS core): rank APPLICANTS by needs =================
def score_applicant(card, job_req):
    """card: dict with name, resume_text, contact, applied_role.
    job_req: dict with must_have[], nice_to_have[], location, salary_band.
    Returns dict with score, tier, flags. REAL APPLICANT vs NOISE separation."""
    text = (card.get("resume_text", "") + " " + card.get("name", "")).lower()
    flags, reasons = [], []

    # --- NOISE / BOT gates (fail = rejected before points) ---
    if len(text.strip()) < 120:
        return {"score": 0, "tier": "NOISE", "flags": ["empty/thin submission"], "reasons": ["Resume under 120 chars"]}
    if card.get("contact", "").count("@") != 1 and "://" not in card.get("contact", ""):
        flags.append("NO-CONTACT")   # no real email/link = harvest-shaped submission
        reasons.append("No valid contact info")
    if any(b in text for b in ["crypto", "forex", "seo services", "backlinks", "web traffic"]):
        return {"score": 0, "tier": "NOISE", "flags": ["SPAM"], "reasons": ["Spam keywords in submission"]}

    # --- REAL APPLICANT signals ---
    real_hits = [k for k in ("certification", "ticket", "diploma", "years experience",
                             "references", "portfolio", "linkedin", "available") if k in text]
    if len(real_hits) >= 3:
        flags.append("REAL-APPLICANT")
        reasons.append("Real-applicant signals: " + ", ".join(real_hits))

    # --- NEEDS matching: employer's must-haves dominate ---
    must, nice = 0, []
    for kw in job_req.get("must_have", []):
        if kw.lower() in text:
            must += 18; nice.append(kw)
        else:
            must -= 10
    missing = [k for k in job_req.get("must_have", []) if k.lower() not in text]
    if missing:
        flags.append("GAPS:" + ",".join(missing[:3]))
    have_pts = 0
    for kw in job_req.get("nice_to_have", []):
        if kw.lower() in text:
            have_pts += 6; nice.append(kw)
    if nice:
        reasons.insert(0, "Needs match: " + ", ".join(nice[:8]))

    raw = 40 + must + have_pts + len(real_hits) * 3
    score = int(max(0, min(100, raw)))
    tier = ("TOP" if score >= 75 else "INTERVIEW" if score >= 55
            else "MAYBE" if score >= 35 else "PASS")
    return {"score": score, "tier": tier, "flags": flags, "reasons": reasons}

# ================= COMMANDS =================
def cmd_rescore():
    """Offline re-rank of existing scored results with v3 (no CDP needed)."""
    with open(RESULTS_JSON, "r", encoding="utf-8") as f:
        jobs = json.load(f)
    for j in jobs:
        r = score_job_v3(j.get("title", ""), j.get("company", ""),
                         j.get("description", ""), j.get("salary", ""))
        j.update(r)
    jobs.sort(key=lambda x: x["score"], reverse=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2, ensure_ascii=False)
    lines = ["# PROMORPHYES v3 — RANKED BY IMPORTANCE TO THE APPLICANT",
             "# " + datetime.datetime.now().isoformat(timespec="minutes"), ""]
    tiers = ["INTERVIEW NOW", "APPLY TODAY", "KEEP WARM", "LOW", "SINK", "BLOCKED"]
    for t in tiers:
        grp = [j for j in jobs if j["tier"] == t]
        if not grp:
            continue
        lines.append("## " + t + " (" + str(len(grp)) + ")")
        lines.append("")
        for j in grp:
            lines.append("### " + str(j["score"]) + "% — " + j.get("title", "?"))
            lines.append("- " + j.get("company", "?") + " | " + j.get("location", "?") + " | " + j.get("salary", "?"))
            if j.get("url"):
                lines.append("- " + j["url"])
            for r in j["reasons"]:
                lines.append("- " + r)
            lines.append("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("Ranked " + str(len(jobs)) + " jobs -> " + OUT_MD)
    for j in jobs[:10]:
        print(str(j["score"]) + "% [" + j["tier"] + "] " + j.get("title", "?")[:50])

def cmd_applicant(req_file, cards_file):
    with open(req_file, "r", encoding="utf-8") as f:
        req = json.load(f)
    with open(cards_file, "r", encoding="utf-8") as f:
        cards = json.load(f)
    out = []
    for c in cards:
        r = score_applicant(c, req)
        r["name"] = c.get("name", "?")
        out.append(r)
    out.sort(key=lambda x: x["score"], reverse=True)
    for r in out:
        print(str(r["score"]) + "% [" + r["tier"] + "] " + r["name"] + " | " + "; ".join(r["reasons"])[:100])
    with open(req_file.replace(".json", "_applicants_ranked.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "rescore":
        cmd_rescore()
    elif len(sys.argv) >= 4 and sys.argv[1] == "applicant":
        cmd_applicant(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
