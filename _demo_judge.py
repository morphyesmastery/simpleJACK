# _demo_judge.py - local scoring endpoint for the HireHarbor seeker demo.
# POST http://127.0.0.1:8799/score  {resume, roles:[...], answers:[{q,who,a,secs}]}
# Uses the SAME GLM semantic judge family as production hh_engine.py.
# Scores: alignment (answer vs what employer wants), consistency (answer vs resume),
# plus judge notes quoting evidence. No keyword counting. Reads meaning.
import json, os, re, urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

API = "https://api.z.ai/api/coding/paas/v4/chat/completions"
MODEL = "glm-5-turbo"

# What the employer wants to hear, per unique demo question (mirrors portal want-points).
WANT = {
 "supermarket calls: their compressor is tripping": "systematic diagnosis: check amp draw vs RLA, head pressure, condenser condition and morning pull-down load; rule out electrical before parts; keep customer informed; safety and food-loss risk",
 "bill is 'insane' and refuses to pay": "stay calm, not defensive; walk the customer through the actual work and parts line by line; offer itemized breakdown; escalate to owner politely if needed; protect the relationship while protecting the company",
 "caught someone else's mistake before it became a warranty claim": "a specific first-person story with real stakes: what was wrong, how they caught it, what it would have cost, what they changed after",
 "don't know how to do a task the tech just asked": "say 'I don't know, show me' honestly and immediately; ask before guessing; safety first; willingness to learn beats faking it",
 "Why this trade and not something else": "genuine personal reason tied to the trade; concrete, not generic; shows they understand what the work actually is",
 "Two no-heat calls, one van, minus thirty": "triage by risk: elderly/children/pipe-freeze first, batch parts and calls geographically, communicate ETAs honestly, safety in extreme cold",
 "rooftop unit is short-cycling": "check controls and thermostat first, then filters/airflow, then charge and head pressure; explain WHY each check comes in that order; evidence over guessing",
 "needing a part you're out of, truck leaves in ten minutes": "act fast: check other truck or branch stock, call supplier for will-call, offer genuine interim fix, communicate with the tech, log the backorder",
 "run out of assigned work": "find useful work without being told: ask, sweep, stock, shadow a tech, study; initiative and humility",
}

def ask_glm(payload_msgs):
    key = os.environ.get("ZAI_API_KEY", "")
    body = json.dumps({"model": MODEL, "messages": payload_msgs, "temperature": 0.2}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"]

def judge(resume, roles, answers):
    want_lines = []
    for a in answers:
        w = ""
        for k, v in WANT.items():
            if k.lower() in a["q"].lower():
                w = v
                break
        want_lines.append("Q: " + a["q"] + "\nEMPLOYER WANTS: " + (w or "honest, specific, safety-aware, first-person"))
    prompt = (
        "You are the HireHarbor scoring judge. Read the resume and each pre-interview answer. "
        "Score with judgment, NOT keyword matching - meaning only.\n"
        "ROLES APPLIED: " + ", ".join(roles) + "\n\nRESUME:\n" + (resume or "(none attached)") +
        "\n\nFOR EACH answer give: alignment 0-100 (does the answer demonstrate what the employer wants), "
        "consistency 0-100 (does the answer agree with the resume's claimed experience), "
        "note (one sentence quoting their words as evidence).\n"
        "THEN give overall: verdict (ADVANCE or HOLD), summary (2 sentences a hiring manager would trust).\n"
        "Reply with STRICT JSON only:\n"
        '{"answers":[{"alignment":n,"consistency":n,"note":"..."}],"verdict":"ADVANCE|HOLD","summary":"..."}'
        "\n\nQUESTIONS AND ANSWERS:\n" + "\n\n".join(
            want_lines[i] + "\nTHEIR ANSWER (" + str(answers[i].get("secs", 0)) + "s): " + answers[i]["a"]
            for i in range(len(answers)))
    )
    raw = ask_glm([{"role": "user", "content": prompt}])
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("judge returned no JSON: " + raw[:200])
    return json.loads(m.group(0))

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()
    def do_POST(self):
        if self.path != "/score":
            self.send_response(404); self._cors(); self.end_headers(); return
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n).decode())
            out = judge(data.get("resume", ""), data.get("roles", []), data.get("answers", []))
            out["ok"] = True
            code = 200
        except Exception as e:
            out = {"ok": False, "error": str(e)}
            code = 500
        b = json.dumps(out).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

if __name__ == "__main__":
    print("demo judge on 127.0.0.1:8799", flush=True)
    HTTPServer(("127.0.0.1", 8799), H).serve_forever()
