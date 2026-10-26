"""Build the business pitch for the medical economics desk: the film in seven chapters, why cost
questions are slow today, and what changes. Written for business readers; no setup steps.

    python docs/demo/desk/build_pitch.py <out_desk folder> <posters folder> <output.html> [--inline]

Without --inline the page links media/<name>.mp4 and .jpg, for publishing next to the page. With
--inline every clip and poster is embedded, for a single file to send around.
"""

import base64
import html
import json
import subprocess
import sys
from pathlib import Path

CHAPTERS = [
    ("01_ask", "Ask", "A plain question, a governed answer",
     "Inpatient cost for Medicare Advantage in Illinois: $538 per member per month, up 31%. The newest quarter is "
     "marked because its claims are still arriving, so nobody mistakes a dip for good news."),
    ("02_why_it_moved", "Why it moved", "Volume or price, in one view",
     "Admissions barely moved. Cost per admission rose 28%. Endocrine and mental health drove the increase, and "
     "oncology pulled the other way."),
    ("03_drill_in", "Drill in", "Click any bar to go deeper",
     "Endocrine by admit type: elective, emergency, urgent. There are no observation stays, because medical policy "
     "MP-112 counts them as outpatient."),
    ("04_why_trust_it", "Why trust it", "Every number shows where it came from",
     "The definition in plain words, the source data, and the approved policy change that moved the number from "
     "$370 to $317, with who approved it."),
    ("05_a_new_question", "A new question", "When nothing is defined, it says so",
     "Nobody has defined behavioral health PMPM yet. Instead of guessing, the desk sends the question to the people "
     "who own the definitions."),
    ("06_owners_define_it", "Owners define it", "The people accountable make the call",
     "The actuarial lead sees the question. An AI curator drafts the definition, checks it against the data and "
     "writes the matching Power BI measure. Three owners approve."),
    ("07_asked_again", "Asked again", "The next person gets the answer",
     "$83 per member per month, from the new definition, with its history attached. Every agent and report now "
     "uses the same one."),
]

CSS = """
/* Screening room up top, a quiet paper document below. Display: Plex Sans Condensed; reading: Source Serif 4. */
:root{
  --paper:#eef1f4;--ink:#142338;--ink2:#3e4c60;--muted:#66758a;--rule:#d3dae3;--card:#ffffff;
  --stage:#142338;--stage2:#1c2f4a;--stage-ink:#e9eef5;--stage-muted:#93a4ba;
  --teal:#0f7b78;--teal-hi:#5fc4bf;--amber:#c97c06;
  --display:"IBM Plex Sans Condensed","Arial Narrow",Arial,sans-serif;
  --body:"Source Serif 4",Georgia,"Times New Roman",serif;
  --ui:"IBM Plex Sans",-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --paper:#0f1a2a;--ink:#e6ecf3;--ink2:#b9c5d4;--muted:#8796aa;--rule:#253650;--card:#15243a;
  --stage:#0a1320;--stage2:#15243a;--teal:#4fb8b3;--amber:#e3a23a;color-scheme:dark}}
:root[data-theme="dark"]{
  --paper:#0f1a2a;--ink:#e6ecf3;--ink2:#b9c5d4;--muted:#8796aa;--rule:#253650;--card:#15243a;
  --stage:#0a1320;--stage2:#15243a;--teal:#4fb8b3;--amber:#e3a23a;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:18px/1.62 var(--body)}
.wrap{max-width:1180px;margin:0 auto;padding-inline:24px}
h1,h2,h3{font-family:var(--display);text-wrap:balance;margin:0}
.stage{background:var(--stage);color:var(--stage-ink);padding-block:56px 64px}
.who{font:600 15px/1.3 var(--ui);color:var(--teal-hi);margin:0 0 18px}
h1{font-size:clamp(40px,6vw,74px);line-height:1.02;font-weight:700;letter-spacing:-.015em;max-width:15ch}
.stand{font-size:20px;color:#c5d1df;max-width:60ch;margin:20px 0 0}
.room{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:22px;margin-top:42px;align-items:start}
.screen{min-width:0}
video{width:100%;max-width:100%;aspect-ratio:1440/964;display:block;border-radius:10px;background:#000;border:1px solid var(--stage2)}
.now{margin-top:16px;max-width:66ch}
.now h3{font-size:24px;font-weight:600;color:#fff}
.now p{margin:6px 0 0;color:#c5d1df;font-size:17px}
.chapters{list-style:none;margin:0;padding:0;display:grid;gap:4px;counter-reset:c}
.chapters button{all:unset;box-sizing:border-box;cursor:pointer;display:grid;grid-template-columns:30px 1fr auto;gap:10px;align-items:center;
  width:100%;padding:11px 12px;border-radius:8px;font:500 16px/1.25 var(--ui);color:var(--stage-muted)}
.chapters button:hover{background:var(--stage2);color:#fff}
.chapters button:focus-visible{outline:2px solid var(--teal-hi);outline-offset:1px}
.chapters .k{width:26px;height:26px;border-radius:50%;border:1.5px solid currentColor;display:grid;place-items:center;font:600 13px var(--ui)}
.chapters .t{font:400 13px var(--ui);font-variant-numeric:tabular-nums;opacity:.8}
.chapters button[aria-current="true"]{background:var(--stage2);color:#fff}
.chapters button[aria-current="true"] .k{background:var(--teal-hi);border-color:var(--teal-hi);color:var(--stage)}
.chapters .gap-step .k{border-style:dashed}
.total{font:400 13px/1.4 var(--ui);color:var(--stage-muted);margin:12px 12px 0}
section{padding-block:72px 8px}
.kicker{font:600 15px/1.3 var(--ui);color:var(--teal);margin:0 0 10px}
h2{font-size:clamp(30px,4vw,46px);line-height:1.08;font-weight:700;max-width:22ch}
.intro{color:var(--ink2);max-width:62ch;margin:16px 0 0}
.three{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:36px;margin-top:36px}
.three h3,.roles h3,.fits h3{font-size:22px;font-weight:600;line-height:1.2}
.three p,.roles p,.fits p{margin:8px 0 0;color:var(--ink2);font-size:17px}
.three > div{border-top:3px solid var(--ink);padding-top:14px;min-width:0}
.loop{margin-top:36px;display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:0;counter-reset:l;list-style:none;padding:0}
.loop li{position:relative;padding:18px 18px 18px 0;border-top:3px solid var(--teal);min-width:0}
.loop li.hold{border-top-color:var(--amber)}
.loop b{display:block;font:600 19px/1.2 var(--display)}
.loop span{display:block;color:var(--muted);font:400 15px/1.4 var(--ui);margin-top:6px}
.loop li::before{counter-increment:l;content:counter(l);position:absolute;top:-15px;left:0;width:26px;height:26px;border-radius:50%;
  background:var(--paper);border:3px solid var(--teal);display:grid;place-items:center;font:700 12px var(--ui);color:var(--ink)}
.loop li.hold::before{border-color:var(--amber)}
.back{font:400 15px/1.4 var(--ui);color:var(--muted);margin:6px 0 0}
.quote{margin:40px 0 0;padding:26px 30px;background:var(--card);border:1px solid var(--rule);border-radius:12px;max-width:820px}
.quote p{margin:0;font-size:21px;line-height:1.5}
.quote small{display:block;margin-top:10px;font:400 14px var(--ui);color:var(--muted)}
.fits{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:30px 48px;margin-top:34px}
.fits > div{min-width:0}
.roles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:28px;margin-top:34px}
.roles > div{min-width:0}
.roles .r{font:600 14px var(--ui);color:var(--teal);margin:0 0 6px}
.close{margin-top:80px;background:var(--stage);color:var(--stage-ink);padding-block:60px}
.close h2{color:#fff}
.close p{color:#c5d1df;max-width:62ch;margin:16px 0 0}
.close ol{color:#c5d1df;margin:22px 0 0;padding-left:22px;max-width:62ch}
.close li{margin:6px 0}
.close li::marker{color:var(--teal-hi);font-family:var(--ui);font-weight:600}
footer{padding-block:28px 46px;font:400 14px/1.5 var(--ui);color:var(--muted)}
@media (max-width:900px){
  .room{grid-template-columns:minmax(0,1fr)}
  .chapters{grid-template-columns:repeat(2,minmax(0,1fr))}
  .three,.fits{grid-template-columns:minmax(0,1fr)}
  .roles{grid-template-columns:repeat(2,minmax(0,1fr))}
  .loop{grid-template-columns:repeat(2,minmax(0,1fr));row-gap:28px}
}
@media (max-width:520px){.chapters,.roles{grid-template-columns:minmax(0,1fr)}.wrap{padding-inline:16px}}
"""

JS = """
const C = JSON.parse(document.getElementById('chapters-data').textContent);
const v = document.getElementById('film'), h = document.getElementById('now-h'), p = document.getElementById('now-p');
const btns = [...document.querySelectorAll('.chapters button')];
let cur = -1;
function mark(i) {
  if (i === cur) return; cur = i; const c = C[i];
  h.textContent = (i + 1) + '. ' + c.headline; p.textContent = c.text;
  btns.forEach((b, j) => b.setAttribute('aria-current', j === i ? 'true' : 'false'));
}
btns.forEach((b, i) => b.addEventListener('click', () => {
  v.currentTime = C[i].start + (i ? 0.05 : 0); mark(i); v.play().catch(() => {});
}));
v.addEventListener('timeupdate', () => {
  let i = 0; C.forEach((c, j) => { if (v.currentTime >= c.start) i = j; }); mark(i);
});
mark(0);
"""


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


def body(out: Path, posters: Path, inline: bool) -> str:
    film = out / "Medical_Economics_Desk_demo.mp4"
    windows = json.loads((out / "timeline.json").read_text())["windows"]
    total = duration(film)

    def src(path: Path, mime: str, published: str) -> str:
        return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}" if inline else published

    data, items = [], []
    for i, ((name, short, headline, text), (start, _)) in enumerate(zip(CHAPTERS, windows)):
        start = 0.0 if i == 0 else start  # the opening title belongs to chapter 1
        data.append({"start": round(start, 2), "headline": headline, "text": text})
        cls = ' class="gap-step"' if name in ("05_a_new_question", "06_owners_define_it") else ""
        items.append(f'<li{cls}><button type="button" aria-current="false"><span class="k">{i + 1}</span>'
                     f'<span>{html.escape(short)}</span><span class="t">{int(start // 60)}:{int(start % 60):02d}</span></button></li>')
    video = src(film, "video/mp4", "media/film.mp4")
    poster = src(posters / "01_ask.jpg", "image/jpeg", "media/01_ask.jpg")
    payload = json.dumps(data).replace("</", "<\\/")
    return f"""<title>Medical Economics Desk</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Condensed:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap">
<style>{CSS}</style>
<header class="stage"><div class="wrap">
<p class="who">Tiger Analytics, for health plan medical economics, actuarial and finance teams</p>
<h1>Ask about cost. Get a number you can defend.</h1>
<p class="stand">The medical economics desk answers in plain language, shows what moved the number and where it came
from. When you ask for something your plan has never defined, it doesn't guess. It gets it defined.</p>
<div class="room">
<div class="screen"><video id="film" src="{video}" poster="{poster}" controls playsinline preload="metadata"></video>
<div class="now" aria-live="polite"><h3 id="now-h"></h3><p id="now-p"></p></div></div>
<div><ol class="chapters">{''.join(items)}</ol>
<p class="total">The whole story in {int(total // 60)} minutes {int(total % 60)} seconds. Pick a chapter to jump to it.</p></div>
</div></div></header>

<main>
<section class="wrap">
<p class="kicker">Why cost questions are slow today</p>
<h2>Everyone has a PMPM. Nobody has the same one.</h2>
<div class="three">
<div><h3>Two reports, two numbers</h3><p>Finance and medical economics define inpatient cost slightly differently.
The meeting turns into a reconciliation instead of a decision.</p></div>
<div><h3>Every why is a ticket</h3><p>A question about a jump goes into an analyst queue. The answer comes back days
later, and the next question starts the wait again.</p></div>
<div><h3>Chat tools guess</h3><p>A general AI assistant will give a confident number for a metric your plan has never
defined. Nobody can tell where it came from.</p></div>
</div>
</section>

<section class="wrap">
<p class="kicker">What makes it different</p>
<h2>It never makes up a number</h2>
<p class="intro">Every answer is computed from definitions your own people approved. A question nobody can answer
yet goes to them, and once they agree, every user, agent and report gets the same answer.</p>
<ol class="loop">
<li><b>Someone asks</b><span>in plain language, on the desk</span></li>
<li class="hold"><b>No definition yet</b><span>the desk says so and gives no number</span></li>
<li><b>Owners see the question</b><span>with who asked and why</span></li>
<li><b>The curator drafts it</b><span>checked against your data, with its Power BI measure</span></li>
<li><b>Owners approve</b><span>each area signs off on its part</span></li>
<li><b>Asked again</b><span>a governed answer, for everyone</span></li>
</ol>
<p class="back">Then the loop starts over with the next question. Each one makes the next answer faster.</p>
<div class="quote"><p>"No governed definition answers this yet, so no number was given."</p><small>What the desk
shows when asked for behavioral health PMPM, chapter 5 of the film</small></div>
</section>

<section class="wrap">
<p class="kicker">Fits what you already run</p>
<h2>Nothing to rip out</h2>
<div class="fits">
<div><h3>Your Power BI stays</h3><p>Every answer names the Power BI measure it matches. When owners approve a new
definition, its Power BI measure comes with it.</p></div>
<div><h3>Your data stays where it is</h3><p>The desk reads your claims and membership data in the platform you
already use, such as Databricks or Snowflake. Nothing is copied into a new warehouse.</p></div>
<div><h3>Your policies become evidence</h3><p>Medical policies and contracts are quoted word for word behind the
definitions they change, like MP-112 moving observation stays to outpatient.</p></div>
<div><h3>Your people decide</h3><p>Owners approve changes in their own area. Every change has a name, a date and the
reason behind it.</p></div>
</div>
</section>

<section class="wrap">
<p class="kicker">Who gets what</p>
<h2>One desk, four kinds of relief</h2>
<div class="roles">
<div><p class="r">Medical economics</p><h3>Answers and drivers in minutes</h3><p>Ask, see what moved it, and click
deeper without filing a request.</p></div>
<div><p class="r">Actuarial and finance</p><h3>One definition of each metric</h3><p>The same PMPM in every report,
with a history you can audit.</p></div>
<div><p class="r">Data teams</p><h3>Fewer one-off requests</h3><p>A definition is reviewed once and used everywhere,
instead of rebuilt per dashboard.</p></div>
<div><p class="r">Leadership</p><h3>AI that can say "not yet"</h3><p>Assistants that answer from approved definitions
and say plainly when one doesn't exist.</p></div>
</div>
</section>
</main>

<div class="close"><div class="wrap">
<h2>See it on your own data</h2>
<p>A pilot starts small and runs in your environment. Tiger Analytics sets it up with your team in three parts:</p>
<ol><li>One business area to start, such as medical economics for one line of business.</li>
<li>Your existing Power BI model and the claims and membership data behind it.</li>
<li>The people who own the definitions, who approve what the desk is allowed to say.</li></ol>
</div></div>
<footer><div class="wrap">The film uses Plains Health Plan, a fictional health plan with synthetic data. Every step ran
live; waits for answers are shortened in the film and marked.</div></footer>
<script type="application/json" id="chapters-data">{payload}</script>
<script>{JS}</script>"""


def main() -> None:
    out, posters, dest = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    inline = "--inline" in sys.argv
    page = body(out, posters, inline)
    if inline:
        page = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">' + page + "</html>")
    dest.write_text(page)
    print(dest, f"{dest.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
