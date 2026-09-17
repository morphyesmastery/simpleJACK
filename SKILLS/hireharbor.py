"""HIREHARBOR — simple applicant management for business owners. ONE PRICE, OWNED.
One file, stdlib only. Wraps the verified v3 engine (score_applicant) — no rewriting.
Owner flow: post a job req -> share the /apply link -> cards land ranked -> move through pipeline.
Sold, not rented: one purchase unlocks it forever (HH-OWN-<key>), no countdown, no expiry.
Company name is owner-editable in Settings and flows to apply + interview pages.
Run: python hireharbor.py  ->  http://127.0.0.1:8712
"""
import http.server, json, os, datetime, socketserver, urllib.parse
from _score_v3 import score_applicant
from hh_interview import PAGE_INTERVIEW, INTERVIEW_CARDS  # template + default bank (unchanged)
from hh_live import live_cards, render_apply
from hh_engine import compute_rubric


def _num(v, default):
    try:
        return max(1.0, float(v))
    except (TypeError, ValueError):
        return float(default)

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "hireharbor_data.json")
PORT = 8712
OWN_PRICE = 299  # ONE PRICE, OWNED FOREVER — sold, not rented
ACTIVATE_CODE = "HH-OWN-299"  # purchase key: entered once, owned forever

DEFAULT_DATA = {"plan": "LOCKED", "company": "Your Company", "trial_started": datetime.date.today().isoformat(),
                "reqs": [], "applicants": [], "counter": 0}
STAGES = ["NEW", "INTERVIEW", "OFFER", "HIRED", "PASSED"]

def load():
    if os.path.exists(DATA):
        with open(DATA, "r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("company", "Your Company")
        d.setdefault("plan", "LOCKED")
        return d
    return json.loads(json.dumps(DEFAULT_DATA))

def save(d):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)

def plan_status(d):
    """ONE-PRICE slot: locked or owned. No trial, no countdown, no rental language."""
    if d.get("plan") == "OWNED":
        return "OWNED — paid once, yours forever. No fees, no expiry."
    return "$299 once — yours forever. No monthly fees. No expiry."

def company_name(d):
    return (d.get("company") or "Your Company").strip() or "Your Company"


PAGE_MSG_TMPL = """<!doctype html><html><head><title>HireHarbor</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a1420 radial-gradient(1200px 600px at 70% -10%,#123047 0%,#0a1420 55%) fixed;color:#dbe9f5;font-family:"Segoe UI",system-ui,sans-serif;line-height:1.55;padding:26px 20px 80px;max-width:680px;margin:0 auto}
.card{background:#0f1e2e;border:1px solid #24405a;border-radius:14px;padding:30px;text-align:center;box-shadow:0 0 30px rgba(0,0,0,.35);margin-top:10vh}
h1{font-size:22px;font-weight:800;letter-spacing:1px;color:#eaf6ff}
p{color:#8fb3cc;margin-top:10px}
a{color:#4fc3f7;font-weight:700;text-decoration:none}
a:hover{text-decoration:underline}
</style></head><body><div class="card"><h1>__MSG__</h1><p>__SUB__</p><p style="margin-top:18px"><a href="/">&larr; Back to the board</a></p></div></body></html>"""

def PAGE_MSG(title, sub):
    return PAGE_MSG_TMPL.replace("__MSG__", title).replace("__SUB__", sub)

BADGE = {"TOP": "#0a7d33", "INTERVIEW": "#1a6fb5", "MAYBE": "#8a6d1a", "PASS": "#777", "NOISE": "#a11"}

PAGE_BOARD = """<!doctype html><html><head><title>HireHarbor — Applicants</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a1420 radial-gradient(1200px 600px at 70% -10%,#123047 0%,#0a1420 55%) fixed;color:#dbe9f5;font-family:"Segoe UI",system-ui,sans-serif;line-height:1.5;padding:26px 20px 80px}
.wrap{max-width:1040px;margin:0 auto}
.mast{text-align:center;padding:10px 0 18px}
h1{font-size:30px;font-weight:800;letter-spacing:2px;color:#eaf6ff;text-shadow:0 0 24px rgba(79,195,247,.35)}
h1 b{color:#4fc3f7}
.sub{color:#8fb3cc;margin-top:6px}
.banner{background:linear-gradient(90deg,#0f5c2e,#12833f);color:#eafff2;padding:12px 18px;border-radius:12px;margin:0 0 20px;font-weight:700;box-shadow:0 0 24px rgba(18,131,63,.35);text-align:center}
.card{background:#0f1e2e;border:1px solid #24405a;border-radius:14px;padding:18px;margin-bottom:16px;box-shadow:0 0 30px rgba(0,0,0,.35)}
.req{border-left:4px solid #4fc3f7}
.row{display:flex;justify-content:space-between;align-items:center;padding:10px;border-bottom:1px solid #1a3145;gap:10px}
.row:last-child{border-bottom:0}
.badge{color:#fff;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:800;white-space:nowrap;letter-spacing:.5px}
.score{font-size:22px;font-weight:800;color:#4fc3f7;width:56px;text-align:right;font-variant-numeric:tabular-nums}
.why{color:#8fb3cc;font-size:13px;margin-top:3px}
a{color:#4fc3f7}
button{border:0;padding:7px 12px;border-radius:8px;cursor:pointer;font-weight:800}
.move{background:linear-gradient(90deg,#1b7fae,#4fc3f7);color:#04121d} .pass{background:#7a1f1f;color:#ffd9d9}
form textarea,form input{width:100%;padding:10px;margin:5px 0;box-sizing:border-box;background:#0a1420;border:1px solid #24405a;border-radius:8px;color:#eaf6ff;font-family:inherit}
form input:focus{outline:none;border-color:#4fc3f7}
.post{background:linear-gradient(90deg,#12833f,#2ab55e);color:#04140a;padding:9px 16px;border-radius:9px;font-weight:800;border:0;cursor:pointer}
.link{background:#0a1420;border:1px dashed #2c4a63;padding:5px 10px;border-radius:8px;font-family:Consolas,monospace;font-size:12.5px;color:#9fc2da}
</style></head><body>
<div class="wrap">
<div class="mast"><h1>HIRE<b>HARBOR</b></h1><div class="sub">Simple applicant management for business owners &middot; <b style="color:#eaf6ff">__COMPANY__</b> &middot; <a href="/billing" style="font-size:13px">Settings</a></div></div>
<div class="banner">__BANNER__</div>
<div class="card"><b>Post a job</b>
<form method="post" action="/api/req">
<input name="role" placeholder="Job title (e.g. Safety Training Instructor)" required>
<input name="must" placeholder="Must-haves, comma separated (e.g. instructor, safety, curriculum)" required>
<input name="nice" placeholder="Nice-to-haves, comma separated (optional)">
<input name="salary" placeholder="Pay (e.g. $35-45/hour)">
<button class="post" type="submit">Post job + get apply link</button>
</form></div>
__BOARDS__
</div>
</body></html>"""

PAGE_APPLY = """<!doctype html><html><head><title>Apply — __ROLE__</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a1420 radial-gradient(1200px 600px at 70% -10%,#123047 0%,#0a1420 55%) fixed;color:#dbe9f5;font-family:"Segoe UI",system-ui,sans-serif;line-height:1.5;padding:26px 20px 80px;max-width:680px;margin:0 auto}
h1{font-size:26px;font-weight:800;letter-spacing:1px;color:#eaf6ff}
.pay{color:#4fc3f7;font-weight:800;margin:8px 0 16px}
input,textarea{width:100%;padding:12px;margin:6px 0;box-sizing:border-box;background:#0f1e2e;border:1px solid #24405a;border-radius:9px;color:#eaf6ff;font-family:inherit}
input:focus,textarea:focus{outline:none;border-color:#4fc3f7}
button{background:linear-gradient(90deg,#1b7fae,#4fc3f7);color:#04121d;border:0;padding:14px 22px;border-radius:10px;font-size:16px;font-weight:800;cursor:pointer;box-shadow:0 0 22px rgba(79,195,247,.4)}
</style></head><body>
<h1>Apply: __ROLE__</h1><div class="pay">__SALARY__</div>
<form method="post" action="/api/apply">
<input type="hidden" name="req_id" value="__REQID__">
<input name="name" placeholder="Your full name" required>
<input name="contact" placeholder="Email or phone" required>
<textarea name="resume" rows="10" placeholder="Paste your resume or tell us about your experience, tickets, certifications..." required></textarea>
<button type="submit">Submit application</button>
</form></body></html>"""

PAGE_BILLING = """<!doctype html><html><head><title>HireHarbor — Own It</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a1420 radial-gradient(1200px 600px at 70% -10%,#123047 0%,#0a1420 55%) fixed;color:#dbe9f5;font-family:"Segoe UI",system-ui,sans-serif;line-height:1.6;padding:26px 20px 80px;max-width:680px;margin:0 auto}
h1{font-size:26px;font-weight:800;letter-spacing:1px;color:#eaf6ff;margin-bottom:6px}
.st{background:linear-gradient(90deg,#0f5c2e,#12833f);color:#eafff2;padding:12px 18px;border-radius:12px;font-weight:700;margin:16px 0;box-shadow:0 0 24px rgba(18,131,63,.35)}
a{color:#4fc3f7}
input{width:100%;padding:12px;margin:6px 0;box-sizing:border-box;background:#0f1e2e;border:1px solid #24405a;border-radius:9px;color:#eaf6ff;font-family:inherit}
input:focus{outline:none;border-color:#4fc3f7}
button{background:linear-gradient(90deg,#12833f,#2ab55e);color:#04140a;border:0;padding:13px 20px;border-radius:10px;font-size:15px;font-weight:800;cursor:pointer}
</style></head><body>
<h1>HireHarbor — Own It</h1><div class="st">__STATUS__</div>
<p>One price. $299 once, and it is yours forever — no monthly fees, no expiry, no countdown. Sold, not rented.
After payment (e-transfer to morphyesmastery@gmail.com), enter your purchase key below one time.</p>
<form method="post" action="/api/activate">
<input name="code" placeholder="Purchase key (e.g. HH-OWN-299)" required>
<button type="submit">Unlock forever</button>
</form>
<h1 style="margin-top:34px">Settings</h1>
<form method="post" action="/api/settings">
<label style="color:#8fb3cc;font-size:13px">Company name — shown to applicants on the apply + interview pages</label>
<input name="company" placeholder="Your Company" value="__COMPANY__" required>
<button type="submit" style="background:linear-gradient(90deg,#1b7fae,#4fc3f7);color:#04121d">Save settings</button>
</form>
<p><a href="/">&larr; Back to applicant board</a></p>
</body></html>"""


PAGE_EDIT = """<!doctype html><html><head><title>Interview Editor — __ROLE__</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a1420 radial-gradient(1200px 600px at 70% -10%,#123047 0%,#0a1420 55%) fixed;color:#dbe9f5;font-family:"Segoe UI",system-ui,sans-serif;line-height:1.55;padding:26px 20px 80px;max-width:940px;margin:0 auto}
h1{font-size:26px;font-weight:800;letter-spacing:1px;color:#eaf6ff;margin-bottom:6px}
.sub{color:#8fb3cc;margin-bottom:16px;line-height:1.6}
.sub b{color:#4fc3f7}
a{color:#4fc3f7}
.q{background:#0f1e2e;border:1px solid #24405a;border-radius:13px;padding:16px;margin-bottom:14px;box-shadow:0 0 24px rgba(0,0,0,.35)}
.q label{display:block;font-size:11.5px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#6f93ad;margin:9px 0 3px}
.q input,.q textarea,.q select{width:100%;padding:10px;box-sizing:border-box;background:#0a1420;border:1px solid #24405a;border-radius:8px;color:#eaf6ff;font-family:inherit}
.q input:focus,.q textarea:focus,.q select:focus{outline:none;border-color:#4fc3f7}
.q textarea{min-height:56px}
.two{display:flex;gap:12px} .two>div{flex:1}
button{border:0;padding:9px 15px;border-radius:9px;cursor:pointer;font-weight:800}
.add{background:linear-gradient(90deg,#1b7fae,#4fc3f7);color:#04121d}
.save{background:linear-gradient(90deg,#12833f,#2ab55e);color:#04140a;font-size:15px;padding:13px 24px}
.del{background:#7a1f1f;color:#ffd9d9;margin-top:10px}
.top{display:flex;gap:10px;align-items:center;margin-bottom:14px}
</style></head><body>
<h1>Interview editor — __ROLE__</h1>
<div class="sub">These questions and your wanted answers feed the live interview page instantly —
no redeploy, no duplicate copy. Scoring: <b>alignment</b> = applicant answers vs your wanted key
points; <b>timing</b> = seconds spent per question inside your min/max window (quick AND correct);
<b>consistency</b> = an AI judge READS your requirements, the resume, and every answer together
and scores real understanding - never keyword matching. Leave "wanted answer" empty to exclude a question from alignment scoring.</div>
<div id="rows"></div>
<button class="add" onclick="addRow()">+ Add question</button>
<form method="post" action="/api/interview" onsubmit="prep()">
<input type="hidden" name="id" value="__RID__">
<input type="hidden" name="questions" id="qjson">
<button class="save" type="submit">Save interview</button>
</form>
<p><a href="/">&larr; Back to board</a> &middot; <a href="/apply?id=__RID__" target="_blank">Preview live interview</a></p>
<script>
const ROWS = __ROWS__;
function esc(s){return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;")}
function render(){
  const el=document.getElementById("rows");el.innerHTML="";
  ROWS.forEach((r,i)=>{
    const d=document.createElement("div");d.className="q";
    d.innerHTML=
      '<label>Short title</label><input value="'+esc(r.title)+'" oninput="upd('+i+',\'title\',this.value)">'+
      '<div class="two"><div><label>Category</label><input value="'+esc(r.category)+'" oninput="upd('+i+',\'category\',this.value)"></div>'+
      '<div><label>Voice / speaker</label><select onchange="upd('+i+',\'speaker\',this.value)">'+
      ["narrator","customer","caller","foreman"].map(s=>'<option'+(r.speaker===s?" selected":"")+'>'+s+'</option>').join("")+
      '</select></div></div>'+
      '<label>Question (spoken/typed to the applicant)</label><textarea oninput="upd('+i+',\'question\',this.value)">'+esc(r.question)+'</textarea>'+
      '<label>Wanted answer — key points you hope to hear (comma or sentence form)</label><textarea oninput="upd('+i+',\'want\',this.value)">'+esc(r.want)+'</textarea>'+
      '<div class="two"><div><label>Min seconds (faster than this smells canned)</label><input type="number" value="'+(r.lo||20)+'" oninput="upd('+i+',\'lo\',this.value)"></div>'+
      '<div><label>Max seconds (slower still counts, decays)</label><input type="number" value="'+(r.hi||120)+'" oninput="upd('+i+',\'hi\',this.value)"></div></div>'+
      '<button class="del" onclick="delRow('+i+')">Remove question</button>';
    el.appendChild(d);
  });
}
function upd(i,k,v){ROWS[i][k]=v}
function addRow(){ROWS.push({title:"",category:"",speaker:"narrator",question:"",want:"",lo:20,hi:120});render()}
function delRow(i){ROWS.splice(i,1);render()}
function prep(){document.getElementById("qjson").value=JSON.stringify(ROWS)}
render();
</script></body></html>"""


def render_applicant(a):
    """Rubric detail page (anchor 2026-09-15). House pattern: one template + slots."""
    rub = a.get("rubric") or {}
    per = rub.get("per_question") or []

    def cell(v, pct=True):
        if v is None:
            return "&mdash;"
        return (str(v) + "%") if pct else str(v)

    rows = ""
    for p in per:
        rows += ("<tr><td>Q" + str(p["q"]) + "</td><td>" + str(p.get("title", "")) +
                 "</td><td>" + cell(p.get("alignment")) + "</td><td>" + cell(p.get("timing")) +
                 "</td><td>" + cell(p.get("secs"), False) + "</td></tr>")
    caps = ", ".join(rub.get("caps") or []) or "&mdash;"
    html = """<!doctype html><html><head><title>Applicant — __NAME__</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a1420 radial-gradient(1200px 600px at 70% -10%,#123047 0%,#0a1420 55%) fixed;color:#dbe9f5;font-family:"Segoe UI",system-ui,sans-serif;line-height:1.55;padding:26px 20px 80px;max-width:900px;margin:0 auto}
h1{font-size:28px;font-weight:800;letter-spacing:1px;color:#eaf6ff;margin-bottom:12px}
a{color:#4fc3f7}
.card{background:#0f1e2e;border:1px solid #24405a;border-radius:14px;padding:18px;margin-bottom:16px;box-shadow:0 0 30px rgba(0,0,0,.35)}
.big{font-size:36px;font-weight:800;color:#4fc3f7;text-shadow:0 0 18px rgba(79,195,247,.4)}
table{border-collapse:collapse;width:100%;margin-top:10px}
td,th{border:1px solid #1a3145;padding:8px 10px;text-align:left;font-size:13.5px;color:#c9dcec}
th{background:#0a1420;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#6f93ad}
tr:nth-child(even) td{background:#0c1928}
pre{white-space:pre-wrap;font-family:Consolas,monospace;font-size:13px;background:#0a1420;border:1px solid #1a3145;color:#b9d2e4;padding:14px;border-radius:10px}
.k{color:#8fb3cc}
i{color:#bfe3f7}
b{color:#eaf6ff}
</style></head><body>
<h1>__NAME__</h1>
<div class="card"><span class="big">__SCORE__</span>
<span class="k"> engine tier: <b>__TIER__</b> &middot; stage: __STAGE__ &middot; contact: __CONTACT__</span><br>
<span class="k">Engine reasons: __REASONS__</span></div>
<div class="card"><b>Alignment rubric</b> &mdash; total __TOTAL__<br>
Alignment vs employer's wanted answers: <b>__ALIGN__</b> &middot;
Timing (quick and correct): <b>__TIMING__</b> &middot;
Consistency (understanding vs resume claims and requirements): <b>__CONS__</b><br>
Judge's read: <i>__NOTE__</i>
<table><tr><th>#</th><th>Question</th><th>Alignment</th><th>Timing</th><th>Seconds</th></tr>__ROWS__</table></div>
<div class="card"><b>Full interview transcript</b><pre>__QA__</pre></div>
<p><a href="/">&larr; Back to board</a></p></body></html>"""
    qa = str(a.get("resume_text", "")).replace("&", "&amp;").replace("<", "&lt;")
    slots = {"__NAME__": a.get("name", "?"), "__SCORE__": str(a.get("score", 0)) + "%",
             "__TIER__": a.get("tier", "?"), "__STAGE__": a.get("stage", "NEW"),
             "__CONTACT__": str(a.get("contact", "")),
             "__REASONS__": "; ".join(a.get("reasons", [])),
             "__TOTAL__": cell(rub.get("score")), "__ALIGN__": cell(rub.get("alignment")),
             "__TIMING__": cell(rub.get("timing")), "__CONS__": cell(rub.get("consistency")),
             "__CAPS__": caps, "__NOTE__": (rub.get("note") or "&mdash;"),
             "__ROWS__": rows, "__QA__": qa}
    for k, v in slots.items():
        html = html.replace(k, v)
    return html


class HH(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _form(self):
        n = int(self.headers.get("Content-Length", 0))
        return {k: v[0] for k, v in urllib.parse.parse_qs(
            self.rfile.read(n).decode("utf-8")).items()}

    def _redirect(self, to="/"):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def do_GET(self):
        d = load()
        u = urllib.parse.urlparse(self.path)
        if u.path == "/":
            body = PAGE_BOARD.replace("__BANNER__", plan_status(d)).replace("__COMPANY__", company_name(d))
            parts = []
            for r in sorted(d["reqs"], key=lambda x: -x["id"]):
                apps = sorted([a for a in d["applicants"] if a["req_id"] == r["id"]],
                              key=lambda a: -a.get("score", 0))
                rows = []
                for a in apps:
                    b = BADGE.get(a.get("tier", "PASS"), "#777")
                    rows.append(
                        '<div class="row"><div class="score">' + str(a.get("score", 0)) +
                        '%</div><div style="flex:1"><b><a href="/applicant?id=' + str(a["id"]) + '">' + a["name"] + '</a></b> ' +
                        '<span class="badge" style="background:' + b + '">' + a.get("tier", "?") +
                        "</span> " + (('<small style="background:#0a7d33;color:#fff;padding:2px 8px;border-radius:10px;font-weight:bold">ALIGNMENT ' + str(a["rubric"]["score"]) + "%</small>") if a.get("rubric") and a["rubric"].get("score") is not None else "") +
                        " <small>[" + a.get("stage", "NEW") + "]</small>" +
                        '<div class="why">' + "; ".join(a.get("reasons", []))[:220] + "</div></div>" +
                        "<form method=post action=/api/move style=display:flex;gap:4px>" +
                        '<input type=hidden name=id value="' + str(str(a["id"])) + '">' +
                        '<input type=hidden name=stage value="' + a.get("stage", "NEW") + '">' +
                        '<button class=move name=go value=next>Next &rarr;</button>' +
                        '<button class=pass name=go value=pass>Pass</button></form></div>')
                link = "http://" + self.headers.get("Host", "127.0.0.1:" + str(PORT)) + "/apply?id=" + str(r["id"])
                parts.append('<div class="card req"><b>' + r["role"] + "</b> &mdash; " + r.get("salary", "")
                             + " <small>(" + str(len(apps)) + " applicants)</small>"
                             + '<div class="link">Share apply link: ' + link
                             + ' &nbsp; <a href="/interview?id=' + str(r["id"]) + '"><b>Edit interview</b></a></div>'
                             + ("".join(rows) if rows else '<div class="why">No applicants yet.</div>')
                             + "</div>")
            html = body.replace("__BOARDS__", "\n".join(parts) or "<p>No jobs posted yet.</p>")
            self._html(html)
        elif u.path == "/billing":
            self._html(PAGE_BILLING.replace("__STATUS__", plan_status(d)).replace("__COMPANY__", company_name(d)))
        elif u.path == "/interview":
            q = urllib.parse.parse_qs(u.query)
            rid = int(q.get("id", ["0"])[0])
            req = next((r for r in d["reqs"] if r["id"] == rid), None)
            if not req:
                self._html(PAGE_MSG("Job not found", "That posting is not on the board — it may have been removed."))
                return
            rows = json.dumps((req.get("interview") or {}).get("questions") or [], ensure_ascii=False)
            self._html(PAGE_EDIT.replace("__ROLE__", req["role"]).replace("__RID__", str(rid))
                       .replace("__ROWS__", rows.replace("</", "<\\/")))
        elif u.path == "/applicant":
            q = urllib.parse.parse_qs(u.query)
            aid = int(q.get("id", ["0"])[0])
            a = next((x for x in d["applicants"] if x["id"] == aid), None)
            if not a:
                self._html(PAGE_MSG("Applicant not found", "This record is not on the board — it may have been removed."))
                return
            self._html(render_applicant(a))
        elif u.path == "/apply":
            q = urllib.parse.parse_qs(u.query)
            rid = int(q.get("id", ["0"])[0])
            req = next((r for r in d["reqs"] if r["id"] == rid), None)
            if not req:
                self._html(PAGE_MSG("Job not found", "That posting is not on the board — it may have been removed."))
                return
            if q.get("form", ["0"])[0] == "1":  # plain-form fallback kept intact
                html = (PAGE_APPLY.replace("__ROLE__", req["role"]).replace("__SALARY__", req.get("salary", ""))
                        .replace("__COMPANY__", company_name(d))
                        .replace("__REQID__", str(rid)))
            else:  # cinematic interview, fed LIVE from the employer's portal config
                html = render_apply(req, company_name(d))
            self._html(html)
        else:
            self._html(PAGE_MSG("Page not found", "That address does not exist here."))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_POST(self):
        # public careers page — JSON applicant from the seeker page
        if self.path == "/api/public_apply":
            try:
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(n).decode())
                d = load()
                name = (data.get("name") or "Applicant").strip()[:80]
                roles = [str(r).strip()[:80] for r in (data.get("roles") or []) if str(r).strip()]
                resume = str(data.get("resume") or "")
                answers = data.get("answers") or []
                qa_text = resume + "\n\n[Roles applied: " + ", ".join(roles) + "]\n\n" + "\n\n".join(
                    "Q: " + str(a.get("q", "")) + "\nA: " + str(a.get("a", "")) for a in answers)
                scored = []
                for role in roles or ["General Application"]:
                    req = next((r for r in d["reqs"] if r["role"].lower() == role.lower()), None)
                    if not req:
                        d["counter"] += 1
                        req = {"id": d["counter"], "role": role, "must": [], "nice": [],
                               "salary": "", "created": datetime.date.today().isoformat()}
                        d["reqs"].append(req)
                    res = score_applicant({"name": name, "contact": "via careers page",
                                           "resume_text": qa_text},
                                          {"must_have": req["must"], "nice_to_have": req["nice"]})
                    d["counter"] += 1
                    d["applicants"].append({"name": name, "contact": "via careers page",
                                            "resume_text": qa_text, "id": d["counter"],
                                            "req_id": req["id"], "stage": "NEW",
                                            "applied": datetime.date.today().isoformat(),
                                            "rubric": {}, **res})
                    scored.append(res.get("score", 0))
                save(d)
                b = json.dumps({"ok": True, "scores": scored}).encode()
                self.send_response(200)
            except Exception as e:
                b = json.dumps({"ok": False, "error": str(e)}).encode()
                self.send_response(500)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        d = load()
        f = self._form()
        if self.path == "/api/req":
            d["counter"] += 1
            d["reqs"].append({"id": d["counter"], "role": f.get("role", "").strip(),
                              "must": [x.strip() for x in f.get("must", "").split(",") if x.strip()],
                              "nice": [x.strip() for x in f.get("nice", "").split(",") if x.strip()],
                              "salary": f.get("salary", "").strip(),
                              "created": datetime.date.today().isoformat()})
            save(d)
            self._redirect("/")
        elif self.path == "/api/apply":
            req = next((r for r in d["reqs"] if r["id"] == int(f.get("req_id", 0))), None)
            if req:
                d["counter"] += 1
                card = {"name": f.get("name", "?"), "contact": f.get("contact", ""),
                        "resume_text": f.get("resume", "")}
                res = score_applicant(card, {"must_have": req["must"], "nice_to_have": req["nice"]})  # VERIFIED engine — real vs noise
                try:
                    qa = json.loads(f.get("qa", "") or "{}")
                    rub = compute_rubric(live_cards(req), qa.get("answers", []), qa.get("times", []),
                                         req["must"], f.get("resume", ""))
                except Exception:
                    rub = {}
                d["applicants"].append(dict(card, id=d["counter"], req_id=req["id"],
                                            stage="NEW", applied=datetime.date.today().isoformat(),
                                            rubric=rub, **res))
                save(d)
                self._html(PAGE_MSG("Application received.", "The owner reviews ranked applicants daily."))
            else:
                self._html(PAGE_MSG("Job not found", "That posting is not on the board — it may have been removed."))
        elif self.path == "/api/interview":
            req = next((r for r in d["reqs"] if r["id"] == int(f.get("id", 0))), None)
            if req:
                try:
                    clean = []
                    for x in json.loads(f.get("questions", "[]")):
                        if not (x.get("question") or "").strip():
                            continue
                        clean.append({"title": (x.get("title") or "").strip()[:80],
                                      "category": (x.get("category") or "").strip()[:40],
                                      "speaker": (x.get("speaker") or "narrator").strip()[:12],
                                      "question": (x.get("question") or "").strip(),
                                      "want": (x.get("want") or "").strip(),
                                      "lo": _num(x.get("lo"), 20), "hi": _num(x.get("hi"), 120)})
                    req["interview"] = {"questions": clean, "updated": datetime.date.today().isoformat()}
                    save(d)
                except Exception:
                    pass
            self._redirect("/interview?id=" + f.get("id", "0"))
        elif self.path == "/api/activate":
            if f.get("code", "").strip() == ACTIVATE_CODE:
                d["plan"] = "OWNED"
                d.pop("paid_until", None)
                save(d)
                self._html("<h1>Owned.</h1><p>One price, yours forever — no fees, no expiry. <a href=&quot;/&quot;>Back to board</a></p>")
            else:
                self._html("<h1>Key not recognized.</h1><p><a href=&quot;/billing&quot;>Try again</a></p>")
        elif self.path == "/api/settings":
            d["company"] = (f.get("company", "").strip() or "Your Company")[:80]
            save(d)
            self._redirect("/billing")
        elif self.path == "/api/move":
            a = next((x for x in d["applicants"] if x["id"] == int(f.get("id", 0))), None)
            if a:
                if f.get("go") == "pass":
                    a["stage"] = "PASSED"
                else:
                    i = STAGES.index(a.get("stage", "NEW")) if a.get("stage", "NEW") in STAGES else 0
                    a["stage"] = STAGES[min(i + 1, 3)]
                save(d)
            self._redirect("/")

    def _html(self, s):
        b = s.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

if __name__ == "__main__":
    with Server(("127.0.0.1", PORT), HH) as httpd:
        print("HireHarbor running -> http://127.0.0.1:" + str(PORT))
        httpd.serve_forever()
