"""Build the desk demo page: the loop from a business question to curation and back, one short video
per step, and how to run it. One self-contained HTML file.

    python docs/demo/desk/build_page.py <out_desk folder> <output.html>
"""

import base64
import html
import sys
from pathlib import Path

STEPS = [
    ("Ask", "01_ask.mp4",
     "A medical economics lead asks in plain language. The answer is an exhibit computed from governed definitions: "
     "$538 per member per month in the last complete quarter, up 31%, with the quarter still receiving claims marked.",
     ["Open the desk.", "Ask: <i>How is Medicare Advantage inpatient PMPM trending in Illinois?</i>"]),
    ("Why it moved", "02_why_it_moved.mp4",
     "Volume or price: admissions per 1,000 barely moved, cost per admission rose 28%. The waterfall gives each "
     "clinical category's exact share of the change: endocrine and mental health up, oncology down.",
     ["Ask: <i>Why did it jump last quarter?</i>"]),
    ("Drill in", "03_drill_in.mp4",
     "Every bar is a way in. Clicking Endocrine and choosing admit type sends the drill to the analyst agent. There "
     "is no observation row, because medical policy MP-112 moved observation stays to outpatient.",
     ["Click the <b>Endocrine</b> bar.", "Choose <b>Admit type</b>."]),
    ("Why trust it", "04_why_trust_it.mp4",
     "One click shows the definition in plain words, the source tables down to what agents may see, and the approved "
     "change that moved the number: MP-112 took Medicare Advantage from $370 to $317, with the policy's words.",
     ["Click <b>Why trust this number?</b> on any exhibit."]),
    ("A new question", "05_a_new_question.mp4",
     "Behavioral health PMPM is not defined yet. The analyst gives no number; it sends the question to the owners "
     "of governed metrics.",
     ["Ask: <i>What is behavioral health PMPM in Illinois by quarter?</i>"]),
    ("Owners define it", "06_owners_define_it.mp4",
     "In Context Studio the actuarial lead finds the question. The curator drafts the definition with its value in the "
     "data and the generated Power BI measure. Every owner it touches approves, and it goes live.",
     ["<b>Open Context Studio</b>, sign in as <b>Actuarial lead</b>, open the question under <b>Questions</b>.",
      "Ask the Curator panel to define it (prompt below).",
      "Open the <b>Add BH PMPM</b> proposal; approve as Actuarial lead, Plains data steward and Plains medical "
      "economics lead."]),
    ("Asked again", "07_asked_again.mp4",
     "Back on the desk, the same question now has a governed answer, and its history names who defined it and when.",
     ["Back to the desk (<b>New analysis</b>), ask the same question again.", "Click <b>Why trust this number?</b>"]),
]
DRAFT = ("This came from the medical economics desk. Please define BH PMPM as a governed metric: medical claims paid "
         "for mental health claims, per member per month.")

LOOP = """<svg viewBox="0 0 1000 300" role="img" aria-label="A business question becomes curation and comes back governed" style="width:100%;height:auto">
<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#6a7a90"/></marker></defs>
<g font-family="IBM Plex Sans, -apple-system, Helvetica, sans-serif">
<rect x="20" y="20" width="460" height="260" rx="14" fill="#142338"/>
<text x="44" y="54" fill="#5fc4bf" font-size="15" font-weight="600">Medical economics desk</text>
<text x="44" y="76" fill="#92a3bb" font-size="13">business users</text>
<rect x="520" y="20" width="460" height="260" rx="14" fill="#fff" stroke="#d4dce6"/>
<text x="544" y="54" fill="#0f7b78" font-size="15" font-weight="600">Context Studio</text>
<text x="544" y="76" fill="#6a7a90" font-size="13">owners and the curator</text>
<g font-size="14" fill="#fff">
<rect x="44" y="100" width="190" height="52" rx="9" fill="#1d3150"/><text x="60" y="124">Ask, drill, trust</text><text x="60" y="142" fill="#92a3bb" font-size="12">governed exhibits</text>
<rect x="44" y="196" width="190" height="52" rx="9" fill="#1d3150"/><text x="60" y="220">Asked again</text><text x="60" y="238" fill="#92a3bb" font-size="12">now a governed answer</text>
<rect x="266" y="100" width="190" height="52" rx="9" fill="#5a3b06"/><text x="282" y="124">No definition yet</text><text x="282" y="142" fill="#f1d9a8" font-size="12">no number is invented</text>
</g>
<g font-size="14" fill="#142338">
<rect x="544" y="100" width="190" height="52" rx="9" fill="#edf1f5"/><text x="560" y="124">Question to owners</text><text x="560" y="142" fill="#6a7a90" font-size="12">with who asked</text>
<rect x="766" y="100" width="190" height="52" rx="9" fill="#edf1f5"/><text x="782" y="124">Curator drafts</text><text x="782" y="142" fill="#6a7a90" font-size="12">value and Power BI measure</text>
<rect x="766" y="196" width="190" height="52" rx="9" fill="#e2f1f0" stroke="#0f7b78"/><text x="782" y="220">Owners approve</text><text x="782" y="238" fill="#0f7b78" font-size="12">live for every agent</text>
</g>
<g stroke="#6a7a90" stroke-width="1.6" fill="none">
<path d="M234 126H264" marker-end="url(#a)"/><path d="M456 126H542" marker-end="url(#a)"/><path d="M734 126H764" marker-end="url(#a)"/>
<path d="M861 152V194" marker-end="url(#a)"/><path d="M766 222H236" marker-end="url(#a)"/></g>
</g></svg>"""

CSS = """
:root{--navy:#142338;--paper:#edf1f5;--rule:#d4dce6;--ink2:#3d4d63;--muted:#6a7a90;--teal:#0f7b78;--teal-soft:#e2f1f0}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--navy);font:16px/1.6 "IBM Plex Sans",-apple-system,"Segoe UI",Helvetica,Arial,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:0 24px}
header{background:var(--navy);color:#fff;padding:52px 0 44px}
header h1{font-size:42px;line-height:1.12;margin:0 0 14px;font-weight:700;letter-spacing:-.02em;max-width:860px}
header p{font-size:18px;color:#c9d4e2;max-width:760px;margin:0}
.lead{color:#5fc4bf;font-weight:600;font-size:15px;margin-bottom:10px}
.fig{background:#fff;border:1px solid var(--rule);border-radius:14px;padding:18px;margin:28px 0 8px}
.cap{color:var(--ink2);max-width:860px}
.run{background:#fff;border:1px solid var(--rule);border-left:5px solid var(--teal);border-radius:12px;padding:20px 26px;margin:28px 0}
.run h2{margin:0 0 8px;font-size:22px}.run ol{margin:0;padding-left:22px}.run li{margin:6px 0}
code{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:14px;background:var(--paper);padding:2px 6px;border-radius:5px}
pre{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:14px;background:var(--navy);color:#fff;padding:12px 16px;border-radius:10px;white-space:pre-wrap;margin:8px 0}
.steps{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.steps a{text-decoration:none;color:var(--navy);background:#fff;border:1px solid var(--rule);border-radius:999px;padding:6px 14px;font-weight:600;font-size:14px}
.steps a:hover{border-color:var(--teal)}
section{padding:26px 0 6px;border-top:1px solid var(--rule);margin-top:22px}
section h2{font-size:24px;margin:0 0 6px}
.n{display:inline-flex;width:30px;height:30px;border-radius:50%;border:2px solid var(--teal);color:var(--teal);font-size:15px;align-items:center;justify-content:center;margin-right:10px;vertical-align:2px;background:#fff}
video{width:100%;border-radius:12px;border:1px solid var(--rule);background:#000;display:block;margin-top:12px}
.do{background:#fff;border:1px solid var(--rule);border-radius:12px;padding:14px 18px;margin-top:14px}
.do h3{margin:0 0 4px;font-size:14px;color:var(--muted);font-weight:600}.do ol{margin:0;padding-left:20px}.do li{margin:3px 0;color:var(--ink2)}
.note{font-size:14px;color:var(--muted);margin-top:30px}
footer{padding:32px 0 50px;color:var(--muted);font-size:13px}
"""


def build(out: Path, dest: Path) -> None:
    seg = out / "segments"
    nav = '<div class="steps">' + "".join(f'<a href="#s{i + 1}">{i + 1}. {html.escape(s[0])}</a>' for i, s in enumerate(STEPS)) + "</div>"
    parts = []
    for i, (name, video, shows, do) in enumerate(STEPS):
        data = base64.b64encode((seg / video).read_bytes()).decode()
        extra = f"<pre>{html.escape(DRAFT)}</pre>" if name == "Owners define it" else ""
        parts.append(f"""<section id="s{i + 1}"><h2><span class="n">{i + 1}</span>{html.escape(name)}</h2>
<p class="cap">{html.escape(shows)}</p>
<video src="data:video/mp4;base64,{data}" controls playsinline preload="metadata"></video>
<div class="do"><h3>Do it yourself</h3><ol>{''.join(f'<li>{x}</li>' for x in do)}</ol>{extra}</div></section>""")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Medical economics desk</title><style>{CSS}</style></head><body>
<header><div class="wrap"><div class="lead">Tiger Analytics, internal demo</div>
<h1>Ask, drill, trust, and watch the context improve</h1>
<p>Business users get answers computed from governed definitions. When they ask for something nobody has defined,
the question goes to its owners, the curator drafts the definition, and the next person gets a governed answer.</p></div></header>
<main class="wrap">
<div class="fig">{LOOP}</div>
<p class="cap">The desk never invents a number. A gap becomes an owner's question; the owner's approval makes the answer
available to every agent and report, with its history.</p>
<div class="run"><h2>Run it yourself</h2><ol>
<li>In <code>tiger-context-engineering</code>, with <code>.env</code> holding the Tiger AI gateway key: <pre>scripts/demo_business.sh --fresh</pre>
It builds its own demo Studio (three approved changes) and serves it on ports 8702 and 3702, apart from the Studio demo.</li>
<li>Open <a href="http://localhost:3702/insights">http://localhost:3702/insights</a>.</li>
<li>Follow the seven steps. Answers take 15 to 30 seconds; the videos cut those waits and mark them.</li></ol></div>
<h2 style="margin:6px 0 0">The steps</h2>{nav}
{''.join(parts)}
<p class="note">Plains Health Plan is fictional; the data is tigerclaw's synthetic medical economics data. The full
video is <code>Medical_Economics_Desk_demo.mp4</code>, alongside this page.</p>
</main><footer><div class="wrap">Tiger Analytics, Context Studio, medical economics desk</div></footer></body></html>"""
    dest.write_text(page)
    print(dest, f"{dest.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
