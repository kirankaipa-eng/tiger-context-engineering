"""Build the Plains demo walkthrough: one self-contained HTML page, one short video per step.

    python docs/demo/mee/build_walkthrough.py <segments folder> <output.html>
"""

import base64
import html
import sys
from pathlib import Path

import diagrams

STEPS = [
    {
        "title": "Onboard the client",
        "video": "01_onboard.mp4",
        "shows": "Onboarding Plains is a proposal like any other: vocabulary, warehouse mappings, documents and owners "
                 "arrive together. Checks run on the real data, and three owners approve.",
        "who": "Actuarial lead, then Privacy officer, then Pack steward",
        "do": ["Inbox: open <b>Onboard Plains Health</b>.",
               "Signed in as <b>Actuarial lead</b>: <b>Approve</b>. Repeat as <b>Privacy officer</b> and <b>Pack steward</b>.",
               "Reload the page. Pick <b>Plains Health Plan</b> in the client picker (top left)."],
        "prompt": None,
    },
    {
        "title": "The curator reads the documents",
        "video": "02_curate.mp4",
        "shows": "The curator reads nine documents (four real changes, five decoys). It drafts the one change it can "
                 "prove in the data (expired prior authorizations, 123 requests) and asks owners about the rest.",
        "who": "Plains data steward",
        "do": ["Signed in as <b>Plains data steward</b>: click <b>Run the curator on documents</b> (about a minute).",
               "Look at <b>Questions</b> and <b>All open</b>.",
               "Open <b>Add expired as a PA status</b>, look at <i>Proof in the data</i>, <b>Approve</b>."],
        "prompt": None,
    },
    {
        "title": "Govern a change: observation stays are outpatient",
        "video": "03_govern_a_change.mp4",
        "shows": "Medical policy MP-112 says observation stays are outpatient; the warehouse counts them as inpatient. "
                 "The steward asks the curator to draft the fix. It changes the mapping, and shows the impact before "
                 "anyone approves: MA IP PMPM $370 to $317, OP PMPM $0 to $53, admits per 1,000 294 to 251.",
        "who": "Plains data steward asks; Plains EDW engineer approves",
        "do": ["Questions: open the <b>MP-112</b> question.",
               "Paste the prompt below into the <b>Curator</b> panel on the right and send.",
               "All open: open the new proposal. Check the impact: <b>OP PMPM must rise</b> (MA $0 to $53). "
               "If it does not, ask the curator to revise instead of approving.",
               "Signed in as <b>Plains EDW engineer</b>: <b>Approve</b>."],
        "prompt": "Medical policy MP-112 says observation stays are outpatient. Please draft that change: institutional "
                  "claims for stays that began as observation should be outpatient, not inpatient; physician claims "
                  "stay professional. Show me what it does to IP PMPM, OP PMPM and admits per 1,000.",
    },
    {
        "title": "Their Power BI model stays",
        "video": "04_power_bi.mp4",
        "shows": "The curator reads Plains' medical economics Power BI model (36 measures): 8 are governed metrics "
                 "already, 11 format or compare them, 17 are gaps. Governed metrics are generated back as DAX. A gap "
                 "(BH PMPM) is added; its definition and DAX arrive in one proposal.",
        "who": "Plains medical economics lead",
        "do": ["Signed in as <b>Plains medical economics lead</b>: paste the first prompt into the Curator panel.",
               "Open the <b>Crosswalk</b> proposal; <b>See exactly what changes</b>; <b>Approve</b>.",
               "Paste the second prompt; open the <b>BH PMPM</b> proposal."],
        "prompt": "Crosswalk our Power BI model, MEE_Client_Analytics.\n\nBH PMPM is a gap. Please add it as a governed "
                  "metric: medical claims paid for mental health claims, per member per month.",
    },
    {
        "title": "Agents on governed context",
        "video": "05_agents.mp4",
        "shows": "Asked for MA IP PMPM in Illinois, the agent answers from the new definition and names it. It then "
                 "runs the PI-25 modifier review with a significance test: 7 providers flagged, all 6 planted "
                 "anomalies among them.",
        "who": "Anyone",
        "do": ["Left menu: <b>Ask the Curator</b>. Ask the two questions below, one at a time."],
        "prompt": "What was IP PMPM for Medicare Advantage members in Illinois in 2025 Q3?\n\nRun the PI-25 modifier "
                  "review: modifier 25 and modifier 59 rates per provider against their peer cohort, flag providers "
                  "above 2x their cohort in both 2025-Q4 and 2026-Q1, at least 10 claims a quarter, significant at "
                  "95% (z 1.645).",
    },
    {
        "title": "Same question, same answer",
        "video": "06_consistency.mp4",
        "shows": "The same model asks the same question five times, once through the context layer and once writing "
                 "its own SQL. The governed agent gives one answer; the raw SQL agent does not.",
        "who": "Anyone",
        "do": ["Left menu: <b>Consistency lab</b>. Click the first question (paid per inpatient admit).",
               "Runs: 5. Click <b>Run both agents</b> (about 90 seconds)."],
        "prompt": None,
    },
    {
        "title": "History",
        "video": "07_history.mp4",
        "shows": "Every change: who approved it, from which document. Each one can be undone, through another proposal.",
        "who": "Anyone",
        "do": ["Left menu: <b>History</b>."],
        "prompt": None,
    },
]

CSS = """
:root{--ink:#141414;--ink2:#344054;--muted:#6B7280;--line:#E4E7EC;--bg:#F6F7F9;--card:#fff;--orange:#F37820;--blue:#00A4DB}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,"Segoe UI",Inter,Helvetica,Arial,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:0 24px}
header{background:var(--ink);color:#fff;padding:48px 0 40px}
.kicker{letter-spacing:.14em;text-transform:uppercase;font-size:12px;color:#F9B98A;font-weight:600}
header h1{font-size:38px;line-height:1.15;margin:10px 0 12px;font-weight:650;letter-spacing:-.02em;max-width:820px}
header p{font-size:18px;color:#D0D5DD;max-width:760px;margin:0}
.run{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--orange);border-radius:12px;padding:22px 26px;margin:28px 0}
.run h2{margin:0 0 10px;font-size:22px}
.run ol{margin:0;padding-left:22px}.run li{margin:6px 0}
code,pre{font-family:ui-monospace,Menlo,monospace;font-size:14px}
code{background:#F2F4F7;padding:2px 6px;border-radius:5px}
pre{background:#141414;color:#fff;padding:14px 16px;border-radius:10px;overflow-x:auto;white-space:pre-wrap;margin:10px 0}
.process{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0 6px}
.process a{text-decoration:none;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:999px;padding:7px 14px;font-weight:600;font-size:14px}
.process a:hover{border-color:var(--orange)}.process span{color:#98A2B3}
section{padding:28px 0 8px;border-top:1px solid var(--line);margin-top:24px}
section h2{font-size:24px;margin:0 0 6px}
.n{display:inline-flex;width:30px;height:30px;border-radius:8px;background:var(--orange);color:#fff;font-size:15px;align-items:center;justify-content:center;margin-right:10px;vertical-align:3px}
.shows{color:var(--ink2);max-width:860px;margin:0 0 14px}
video{width:100%;border-radius:12px;border:1px solid var(--line);background:#000;display:block}
.live{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}
@media(max-width:800px){.live{grid-template-columns:1fr}}
.box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.box h3{margin:0 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.box ol{margin:0;padding-left:20px}.box li{margin:4px 0;color:var(--ink2)}
.who{font-size:14px;color:var(--muted);margin-bottom:8px}
.copy{float:right;font:600 12px -apple-system,sans-serif;background:#F2F4F7;border:1px solid var(--line);border-radius:6px;padding:3px 9px;cursor:pointer}
.note{background:#FFFAEB;border:1px solid #FEDF89;color:#7A4A06;border-radius:10px;padding:12px 16px;font-size:14px;margin-top:28px}
.fig{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin:14px 0 6px;overflow-x:auto}
.fig svg{min-width:900px;width:100%;height:auto;display:block}
.cap{color:var(--ink2);max-width:900px;margin:6px 0 0}
table{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:14.5px}
th,td{text-align:left;vertical-align:top;padding:10px 14px;border-bottom:1px solid var(--line)}
th{background:#F9FAFB;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
td:first-child{font-weight:600;white-space:nowrap}
.tbl{overflow-x:auto;margin-top:12px}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:13px;color:var(--muted);margin-top:8px}
.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px;border:1.5px solid}
footer{padding:36px 0 56px;color:var(--muted);font-size:13px}
"""


REAL = [
    ("Source data", "Synthetic tables generated in tigerclaw, snapshotted to 9 Parquet files",
     "The client's own claims, eligibility, authorization and provider tables, where they already are (Databricks). "
     "The context layer is views over their tables, not a copy."),
    ("Warehouse", "DuckDB, built from the Parquet on first start",
     "Their SQL warehouse. The view DDL is already generated per platform: "
     "<code>ictx sql plains_health --dialect databricks</code>."),
    ("Mappings", "units/plh/mappings.yaml, written by us from the synthetic schema",
     "Written with their data engineers from their data dictionary, then checked against real rows by the Studio. "
     "This is the main onboarding effort."),
    ("Metrics", "client.yaml: PMPM, admits per 1,000, modifier rates",
     "Their existing definitions (the MEE semantic layer): the Power BI crosswalk matches what is already governed, and each gap is added as a proposal their owners approve."),
    ("Documents", "9 short documents written for the demo, with an answer key",
     "Their medical policies, UM and payment-integrity policies, data dictionary: read from their system of record, "
     "read-only."),
    ("Power BI", "A copy of tigerclaw's demo model (TMDL)", "Their PBIP/TMDL from source control; generated measures go "
     "back as a reviewed change, their own measures are not touched."),
    ("Owners", "Seeded roles, switched with 'Signed in as'", "Named people from their medical economics, data and EDW "
     "teams, signed in through SSO."),
    ("LLM", "Tiger AI gateway", "Their approved endpoint. The model only reads, drafts and calls tools; every number "
     "comes from governed SQL."),
]

NOT_YET = ("Not built yet for a real client: running checks on Databricks (the Studio checks on its DuckDB copy today), "
           "SSO, and connectors to their document store and Power BI repository. The mapping and metric formats, the "
           "SQL for Databricks and the review process are the same as in the demo.")


def fits() -> str:
    legend = "".join(f'<span><i style="background:{diagrams.FILL[k]};border-color:{diagrams.EDGE[k]}"></i>{t}</span>'
                     for k, t in [("src", "synthetic source data"), ("demo", "Studio and demo tooling"),
                                  ("gov", "governed context"), ("ext", "client inputs"), ("out", "consumers")])
    rows = "".join(f"<tr><td>{a}</td><td>{html.escape(b)}</td><td>{c}</td></tr>" for a, b, c in REAL)
    return f"""
<section id="data"><h2>Where the data comes from</h2>
<p class="cap">Plains Health Plan is fictional. Its data is tigerclaw's synthetic payer data, generated by script, with
the medical economics additions (providers, peer cohorts, modifiers, planted anomalies). One script snapshots it into
the repo with real names removed; the Studio loads that into a DuckDB warehouse shaped like a client's EDW. Everything
after that, the views, metrics and Power BI measures, is governed context, and it changes only through approved
proposals.</p>
<div class="fig">{diagrams.lineage()}</div><div class="legend">{legend}</div></section>
<section id="system"><h2>The system</h2>
<p class="cap">The Studio is a web app and an API over a git repository of context. Each proposal is a branch; checks
run in a separate process against a copy of the warehouse with the branch's views installed. The engine reads only
main, so agents and Power BI see a change only after its owners approve it. The LLM drafts and reads; it never
produces the numbers.</p>
<div class="fig">{diagrams.system()}</div></section>
<section id="process"><h2>How a change travels</h2>
<p class="cap">The same path for every change: a client onboarding, a policy the curator found, a Power BI gap, or a
request typed in chat. Steps 1 to 4 of the demo show it five times: onboarding, the expired status, observation stays, the Power BI crosswalk, BH PMPM.</p>
<div class="fig">{diagrams.process()}</div></section>
<section id="real"><h2>From demo to a real client</h2>
<p class="cap">What each demo piece becomes on an engagement.</p>
<div class="tbl"><table><tr><th></th><th>In this demo</th><th>On a real client</th></tr>{rows}</table></div>
<div class="note">{NOT_YET}</div></section>"""


def build(segments: Path, out: Path) -> None:
    proc = '<div class="process">' + '<span>›</span>'.join(
        f'<a href="#s{i + 1}">{i + 1}. {html.escape(s["title"].split(":")[0])}</a>' for i, s in enumerate(STEPS)) + "</div>"
    parts = []
    for i, s in enumerate(STEPS):
        data = base64.b64encode((segments / s["video"]).read_bytes()).decode()
        prompt = ""
        if s["prompt"]:
            prompt = ('<div class="box"><h3>Type this <button class="copy" onclick="navigator.clipboard.writeText('
                      f'this.parentNode.nextElementSibling.innerText)">Copy</button></h3><pre>{html.escape(s["prompt"])}</pre></div>')
        parts.append(f"""
<section id="s{i + 1}"><h2><span class="n">{i + 1}</span>{html.escape(s['title'])}</h2>
<p class="shows">{html.escape(s['shows'])}</p>
<video src="data:video/mp4;base64,{data}" controls playsinline preload="metadata"></video>
<div class="live"><div class="box"><h3>Do it live</h3><div class="who">Signed in as: {html.escape(s['who'])}</div>
<ol>{''.join(f'<li>{d}</li>' for d in s['do'])}</ol></div>{prompt}</div></section>""")
    FITS = fits()
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Plains demo: medical economics, agent-ready</title><style>{CSS}</style></head><body>
<header><div class="wrap"><div class="kicker">Tiger Analytics · internal demo</div>
<h1>Medical economics, agent-ready: Context Studio on Plains Health Plan</h1>
<p>A fictional regional payer on our synthetic medical economics data. Seven short steps: each has a video, what it
shows, and exactly what to click and type to run it yourself.</p></div></header>
<main class="wrap">
<div class="run"><h2>How to run it</h2><ol>
<li>In <code>tiger-context-engineering</code> (branch <code>feat/context-studio</code>), with <code>.env</code> holding the Tiger AI gateway key, run:
<pre>scripts/demo_mee.sh</pre>It starts the Studio API (:8700) and web app (:3700), and puts <b>Onboard Plains Health</b> in the inbox. Safe to run again.</li>
<li>Open <a href="http://localhost:3700">http://localhost:3700</a>.</li>
<li>Follow the steps below. Switch people with <b>Signed in as</b> (top right): each step says who.</li>
<li>Agent steps take 20 to 120 seconds; the videos cut those waits (marked "sped up").</li></ol></div>
{FITS}
<section id="steps"><h2>The demo, step by step</h2>{proc}</section>
{''.join(parts)}
<div class="note">Plains Health Plan is fictional; the data is tigerclaw's synthetic MEE data (15,000 members, 365k
claims, six planted modifier anomalies). Agent answers can vary between runs: always read the impact before approving.
The recording used the same steps on a scratch copy of the Studio.</div>
</main><footer><div class="wrap">Tiger Analytics · Context Studio · Plains demo walkthrough</div></footer>
</body></html>"""
    out.write_text(page)
    print(out, f"{out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
