"""HH_LIVE — feeds the EXISTING cinematic interview template from the employer's
portal config, LIVE on every load (anchor: "changed in the employer portal,
which feeds the app live each time. i dont like duplicating work").
No page code here: PAGE_INTERVIEW is imported unchanged from hh_interview.
Cards carry the employer's wanted-answer key points + timing windows so the
server rubric (hh_engine) scores alignment without any second source of truth.
No questions configured -> default 10-card scenario bank, untouched.
"""
import json
from hh_interview import PAGE_INTERVIEW, INTERVIEW_CARDS

def live_cards(req):
    """Employer-configured questions -> card deck. Empty config -> default bank."""
    qs = ((req.get("interview") or {}).get("questions")) or []
    cards = []
    for i, q in enumerate(qs, 1):
        cards.append({
            "category": q.get("category") or ("Interview Question " + str(i)),
            "title": q.get("title") or ("Question " + str(i)),
            "speaker": q.get("speaker") or "narrator",
            "dialogue": q.get("question") or "",
            "answerLabel": q.get("answerLabel") or "Type your answer:",
            # scoring payload — the page ignores these; the rubric reads them:
            "want": q.get("want") or "",
            "lo": q.get("lo"), "hi": q.get("hi"),
        })
    return cards if cards else [dict(c) for c in INTERVIEW_CARDS]

def render_apply(req, company="Your Company"):
    """LIVE each load — questions come from the stored req, never a hardcoded deck."""
    cards = live_cards(req)
    return (PAGE_INTERVIEW
            .replace("__ROLE__", req["role"])
            .replace("__SALARY__", req.get("salary", ""))
            .replace("__COMPANY__", company)
            .replace("__REQID__", str(req["id"]))
            .replace("__NCARDS__", str(len(cards)))
            .replace("__CARDS_JSON__",
                     json.dumps(cards, ensure_ascii=False).replace("</", "<\\/")))
