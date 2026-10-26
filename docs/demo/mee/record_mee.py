"""Narrated demo: medical economics, made agent-ready (Context Studio on Plains Health Plan).

Runs the whole flow live against a Studio (web + API): onboarding, the curator, governed changes,
the Power BI crosswalk, agents, and the consistency lab. Kokoro speaks each line first; the
browser performs the line's actions; waits on agents are cut afterwards and marked on screen.

    python record_mee.py http://localhost:3701
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).parent
OUT = HERE / "out_mee"
OUT.mkdir(exist_ok=True)
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3701"
W, H = 1440, 900
SR = 24000
GAP = 0.45
KEEP = 1.2  # seconds kept at each end of a cut wait

os.environ.setdefault("ESPEAK_DATA_PATH", "/opt/homebrew/share/espeak-ng-data")
os.environ.setdefault("PHONEMIZER_ESPEAK_LIBRARY", "/opt/homebrew/lib/libespeak-ng.dylib")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

OBS_Q = ("Medical policy MP-112 says observation stays are outpatient. Please draft that change: institutional "
         "claims for stays that began as observation should be outpatient, not inpatient; physician claims stay "
         "professional. Show me what it does to IP PMPM, "
         "OP PMPM and admits per 1,000.")
BH_Q = ("BH PMPM is a gap. Please add it as a governed metric: medical claims paid for mental health "
        "claims, per member per month.")
PI25_Q = ("Run the PI-25 modifier review: modifier 25 and modifier 59 rates per provider against their peer "
          "cohort, flag providers above 2x their cohort in both 2025-Q4 and 2026-Q1, at least 10 claims a "
          "quarter, significant at 95% (z 1.645).")
IP_Q = "What was IP PMPM for Medicare Advantage members in Illinois in 2025 Q3?"

SCENES = [
    ("", [
        ("This is Context Studio, running on Plains Health Plan: a fictional regional payer built on our "
         "synthetic medical economics data. Fifteen thousand members in five states, and three hundred sixty "
         "thousand claims.", "title"),
    ]),
    ("Onboard a client", [
        ("Onboarding a client is a proposal like any other. Its vocabulary, warehouse mappings, documents "
         "and owners arrive together, and nothing is live until the owners approve.", "onboard_open"),
        ("The checks ran on the real data: the ontology builds, every metric resolves, the SQL compiles for "
         "every platform, and the data's one known issue is recorded, not hidden.", "onboard_checks"),
        ("Three people approve: the actuarial lead for metrics, the privacy officer for redaction, and the "
         "pack steward for documents and ownership.", "onboard_approve"),
    ]),
    ("The curator reads the documents", [
        ("Now the curator reads Plains' nine documents: medical, utilization and payment integrity policies, "
         "the medical economics data dictionary, and five decoys, from trade press to a rate sheet.",
         "scan_start"),
        ("All four real changes found. One it proves in the data; the rest go to owners as questions. "
         "A news article raised one question too, which an owner can close.", "scan_done"),
    ]),
    ("A change the data proves", [
        ("Policy UM-021 says expired authorizations need their own status. The data agrees: one hundred "
         "twenty three requests already carry it. The data steward approves.", "expired"),
    ]),
    ("Observation stays are outpatient", [
        ("Medical policy MP-112 says observation stays are outpatient. Today the warehouse counts them as "
         "inpatient admissions, and outpatient PMPM reads zero.", "obs_question"),
        ("The steward asks the curator to draft the fix.", "obs_chat"),
        ("It read the current mapping first, and changed the mapping, not the metrics, so every measure built "
         "on service category follows.", "obs_open"),
        ("Before anyone approves, the impact. Medicare Advantage IP PMPM falls from three hundred seventy "
         "dollars to three hundred seventeen. OP PMPM rises from zero to fifty three. Admits per thousand fall "
         "from two hundred ninety four to two hundred fifty one. Total cost does not move; only the split.",
         "obs_impact"),
        ("The engineer who owns the warehouse mapping approves it.", "obs_approve"),
    ]),
    ("Their Power BI model stays", [
        ("Plains' medical economics Power B I model stays the source of truth for reports. The curator reads "
         "its thirty six measures.", "pbi_chat"),
        ("Eight are governed metrics already. Eleven only format or compare them over time. Seventeen are "
         "gaps. The governed ones are generated back as DAX measures.", "pbi_open"),
        ("The medical economics lead approves, and asks for one gap: behavioral health PMPM.", "bh_chat"),
        ("The definition and its generated DAX measure arrive together, in one proposal, and the new number is "
         "shown before approval.", "bh_open"),
    ]),
    ("Agents on governed context", [
        ("Now the agents. Asked for Medicare Advantage IP PMPM in Illinois, the agent answers from the new "
         "definition, and names it.", "ip_chat"),
        ("Policy PI-25 compares each provider's modifier use with its peer cohort, two quarters running. The "
         "agent runs that review, with a significance test.", "pi25_chat"),
        ("Seven providers are flagged. Six are the anomalies planted in the data. The seventh is worth a look.",
         "pi25_show"),
    ]),
    ("Same question, same answer", [
        ("Finally, consistency. The same model asks the same question five times: paid per inpatient admit "
         "for Commercial members in 2025. One agent goes through the context layer; one writes its own SQL.",
         "lab_run"),
        ("The governed agent gives one answer, every time. The agent writing its own SQL does not.", "lab_show"),
    ]),
    ("History", [
        ("Every change is in the history: who approved it, from which document, and each one can be undone, "
         "through another proposal.", "history"),
    ]),
    ("", [
        ("Their semantic layer stays the source of truth. Context Studio makes it agent-ready: governed words, "
         "governed numbers, owners who approve every change, and the same answer every time.", "close"),
    ]),
]

TITLE_HTML = """<html><body style="margin:0;height:100vh;display:grid;place-items:center;background:#141414;
font-family:Inter,-apple-system,Helvetica,Arial,sans-serif;color:#fff;overflow:hidden">
<div style="position:absolute;right:-90px;top:-90px;width:380px;height:380px;border-radius:56px;transform:rotate(18deg);
background:linear-gradient(135deg,#F37820 50%,#00A4DB 50%)"></div>
<div style="max-width:980px;padding:0 70px">
<div style="letter-spacing:.16em;text-transform:uppercase;font-size:15px;color:#F9B98A;font-weight:600">Tiger Analytics · internal demo</div>
<div style="font-size:60px;font-weight:700;letter-spacing:-.02em;margin:14px 0 18px">{title}</div>
<div style="font-size:23px;color:#D0D5DD;line-height:1.5">{sub}</div></div></body></html>"""

OVERLAY_JS = """
(() => {
  if (!['3700', '3701'].includes(location.port)) return;
  const add = () => {
    if (document.getElementById('__cur')) return;
    const st = document.createElement('style');
    st.textContent = 'nextjs-portal{display:none!important}';
    document.head.appendChild(st);
    const c = document.createElement('div'); c.id = '__cur';
    c.style.cssText = 'position:fixed;left:0;top:0;width:22px;height:22px;z-index:2147483647;pointer-events:none;transform:translate(-100px,-100px)';
    c.innerHTML = '<svg width="22" height="22" viewBox="0 0 22 22"><path d="M3 2l14 8-6 1.6L8.5 18z" fill="#141414" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    document.body.appendChild(c);
    const l = document.createElement('div'); l.id = '__chap';
    l.style.cssText = 'position:fixed;left:24px;bottom:22px;z-index:2147483646;pointer-events:none;background:#141414;color:#fff;font:600 14px Inter,-apple-system,sans-serif;padding:8px 14px;border-radius:8px;border-left:4px solid #F37820;box-shadow:0 6px 18px rgba(0,0,0,.18);opacity:0;transition:opacity .3s';
    document.body.appendChild(l);
    const w = document.createElement('div'); w.id = '__wait';
    w.style.cssText = 'position:fixed;left:50%;top:14px;transform:translateX(-50%);z-index:2147483646;pointer-events:none;background:#00A4DB;color:#fff;font:600 13px Inter,-apple-system,sans-serif;padding:6px 14px;border-radius:999px;opacity:0;transition:opacity .2s';
    document.body.appendChild(w);
    document.addEventListener('mousemove', e => { c.style.transform = `translate(${e.clientX - 3}px, ${e.clientY - 2}px)`; }, true);
    document.addEventListener('mousedown', e => {
      const r = document.createElement('div');
      r.style.cssText = `position:fixed;left:${e.clientX - 18}px;top:${e.clientY - 18}px;width:36px;height:36px;border-radius:50%;border:3px solid #F37820;z-index:2147483646;pointer-events:none;transition:all .45s ease-out`;
      document.body.appendChild(r);
      requestAnimationFrame(() => { r.style.transform = 'scale(1.8)'; r.style.opacity = '0'; });
      setTimeout(() => r.remove(), 600);
    }, true);
    if (window.__chapText) window.__chapter(window.__chapText);
  };
  if (document.body) add(); else document.addEventListener('DOMContentLoaded', add);
  window.__chapter = (t) => { window.__chapText = t; const l = document.getElementById('__chap'); if (l) { l.textContent = t; l.style.opacity = t ? '1' : '0'; } };
  window.__wait = (t) => { const w = document.getElementById('__wait'); if (w) { w.textContent = t; w.style.opacity = t ? '1' : '0'; } };
})();
"""


def synthesize():
    from kokoro import KPipeline
    pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    return [[np.concatenate([np.asarray(a) for _, _, a in pipe(t, voice="af_heart", speed=1.0)]).astype(np.float32)
             for t, _ in beats] for _, beats in SCENES]


class Director:
    def __init__(self, page, t0):
        self.p, self.t0, self.waits, self.chapter_text = page, t0, [], ""

    def now(self):
        return time.monotonic() - self.t0

    def move(self, x, y, secs=0.5):
        self.p.mouse.move(x, y, steps=max(8, int(secs * 40)))

    def click(self, loc, pause=0.25):
        loc.scroll_into_view_if_needed()
        self.p.wait_for_timeout(150)
        b = loc.bounding_box()
        self.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
        self.p.wait_for_timeout(int(pause * 1000))
        self.p.mouse.down(); self.p.mouse.up()
        self.p.wait_for_timeout(300)

    def hover(self, loc, secs=0.6):
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
            b = loc.bounding_box(timeout=3000)
        except Exception:
            return
        if b:
            self.move(b["x"] + min(b["width"] / 2, 240), b["y"] + b["height"] / 2, secs)

    def chapter(self, text):
        self.chapter_text = text
        self.p.evaluate("t => window.__chapter && window.__chapter(t)", text)

    def goto(self, path):
        self.p.goto(BASE + path)
        self.p.wait_for_load_state("networkidle")
        self.p.wait_for_timeout(800)
        self.chapter(self.chapter_text)

    def person(self, label):
        sel = self.p.locator("header select").nth(1)
        self.hover(sel, 0.4)
        sel.select_option(label=label)
        self.p.wait_for_timeout(1800)

    def client(self, label_start):
        sel = self.p.locator("header select").nth(0)
        opt = sel.locator("option").filter(has_text=label_start).first.get_attribute("value")
        self.hover(sel, 0.4)
        sel.select_option(value=opt)
        self.p.wait_for_timeout(2000)

    def wait_until(self, check, label, timeout=300):
        """Wait on the app; the waiting stretch is cut from the video (with a marker kept on screen)."""
        start = self.now()
        self.p.evaluate("t => window.__wait && window.__wait(t)", label)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not check():
            self.p.wait_for_timeout(500)
        self.p.wait_for_timeout(600)
        self.p.evaluate("() => window.__wait && window.__wait('')")
        self.waits.append((start, self.now(), label))

    def chat(self, text, label="The curator is working"):
        box = self.p.get_by_placeholder("Ask about what you are looking at")
        self.click(box)
        box.fill(text)
        self.p.wait_for_timeout(500)
        self.p.keyboard.press("Enter")
        self.p.wait_for_timeout(1500)
        stop = self.p.locator('button[aria-label="Stop"]')
        self.wait_until(lambda: stop.count() == 0, label + " (sped up)", timeout=400)

    def inbox_item(self, text, tab="All open"):
        import re
        self.click(self.p.locator("main button").filter(has_text=tab).first)
        self.p.wait_for_timeout(800)
        pattern = re.compile(text, re.I) if any(ch in text for ch in "|()") else text
        item = self.p.locator("main button").filter(has_text=pattern).first
        self.click(item)
        self.p.wait_for_timeout(1500)

    def approve(self):
        btn = self.p.get_by_role("button", name="Approve", exact=True)
        self.click(btn)
        self.p.wait_for_timeout(2500)


def act(d: Director, name: str, state: dict):
    p = d.p
    if name == "title":
        return
    if name == "onboard_open":
        d.goto("/inbox"); d.chapter("Onboard a client")
        d.person("Actuarial lead")
        d.inbox_item("Onboard Plains")
        d.hover(p.locator("main h1, main h2").filter(has_text="Onboard").first)
    elif name == "onboard_checks":
        d.hover(p.get_by_text("Ontology builds").first); p.wait_for_timeout(700)
        d.hover(p.get_by_text("Data-quality findings recorded").first); p.wait_for_timeout(1500)
        d.hover(p.get_by_text("WHO DECIDES").first)
    elif name == "onboard_approve":
        for who in ("Actuarial lead", "Privacy officer", "Pack steward"):
            d.person(who)
            d.inbox_item("Onboard Plains")
            d.approve()
        p.reload(); p.wait_for_timeout(2500); d.chapter(d.chapter_text)
    elif name == "scan_start":
        d.client("Plains")
        d.person("Plains data steward")
        btn = p.get_by_role("button", name="Run the curator on documents")
        d.click(btn)
        d.wait_until(lambda: p.get_by_role("button", name="Run the curator on documents").count() > 0,
                     "The curator is reading nine documents (sped up)", timeout=400)
    elif name == "scan_done":
        d.click(p.locator("main button").filter(has_text="Questions").first)
        p.wait_for_timeout(1200)
        for t in ("Observation", "Modifier", "Data Dictionary"):
            d.hover(p.locator("main button").filter(has_text=t).first, 0.5); p.wait_for_timeout(700)
        d.click(p.locator("main button").filter(has_text="All open").first)
        d.hover(p.locator("main button").filter(has_text="expired").first, 0.5)
    elif name == "expired":
        d.inbox_item("expired")
        d.hover(p.get_by_text("PROOF IN THE DATA").first); p.wait_for_timeout(1500)
        d.approve()
    elif name == "obs_question":
        d.inbox_item("MP-112|observation", tab="Questions")
        d.hover(p.locator("main").get_by_text("outpatient").first)
    elif name == "obs_chat":
        d.chat(OBS_Q, "The curator reads the mapping and drafts the change")
    elif name == "obs_open":
        d.goto("/inbox")
        d.inbox_item("bservation|MP-112")
        d.click(p.get_by_text("See exactly what changes").first); p.wait_for_timeout(1500)
        d.hover(p.get_by_text("service_category").first)
    elif name == "obs_impact":
        if p.locator("main").get_by_text("$53", exact=True).count() == 0:
            raise SystemExit("guard: OP PMPM does not show $53 in the impact; not recording a false narration")
        imp = p.get_by_text("IP PMPM").first
        d.hover(imp); p.wait_for_timeout(1500)
        d.hover(p.get_by_text("OP PMPM").first); p.wait_for_timeout(1500)
        d.hover(p.get_by_text("admits per 1,000").first)
    elif name == "obs_approve":
        d.person("Plains EDW engineer")
        d.inbox_item("bservation|MP-112")
        d.approve()
    elif name == "pbi_chat":
        d.person("Plains medical economics lead")
        d.chat("Crosswalk our Power BI model, MEE_Client_Analytics.", "The curator reads the Power BI model")
    elif name == "pbi_open":
        d.goto("/inbox")
        d.inbox_item("Crosswalk")
        d.hover(p.locator("main").get_by_text("governed metrics already").first); p.wait_for_timeout(1200)
        d.click(p.get_by_text("See exactly what changes").first); p.wait_for_timeout(1500)
        d.hover(p.get_by_text("governed_measures.tmdl").first)
    elif name == "bh_chat":
        d.approve()
        d.chat(BH_Q, "The curator drafts the metric")
    elif name == "bh_open":
        d.goto("/inbox")
        d.inbox_item("BH")
        d.hover(p.get_by_text("governed_measures.tmdl").first); p.wait_for_timeout(1200)
        d.hover(p.get_by_text("BH PMPM").first)
        state["bh"] = True
    elif name == "ip_chat":
        d.goto("/chat"); d.chapter("Agents on governed context")
        box = p.get_by_placeholder("Ask the Curator")
        d.click(box); box.fill(IP_Q); p.keyboard.press("Enter"); p.wait_for_timeout(1500)
        stop = p.locator('button[aria-label="Stop"]')
        d.wait_until(lambda: stop.count() == 0, "The agent computes the governed metric (sped up)", timeout=300)
        d.hover(p.locator(".prose-studio").last)
    elif name == "pi25_chat":
        box = p.get_by_placeholder("Ask the Curator")
        d.click(box); box.fill(PI25_Q); p.keyboard.press("Enter"); p.wait_for_timeout(1500)
        stop = p.locator('button[aria-label="Stop"]')
        d.wait_until(lambda: stop.count() == 0, "The agent compares 500 providers with their cohorts (sped up)",
                     timeout=400)
    elif name == "pi25_show":
        text = p.locator(".prose-studio").last.inner_text()
        planted = ("Patel", "Khan", "Sato", "Yamamoto", "Wang", "Kumar")
        if not all(n in text for n in planted):
            raise SystemExit(f"guard: PI-25 answer misses planted providers: {[n for n in planted if n not in text]}")
        tbl = p.locator(".prose-studio table").last
        d.hover(tbl if tbl.count() else p.locator(".prose-studio").last); p.wait_for_timeout(1200)
        p.mouse.wheel(0, 400); p.wait_for_timeout(1200)
    elif name == "lab_run":
        d.goto("/consistency")
        d.click(p.locator("main button").filter(has_text="paid per inpatient admit").first)
        d.click(p.get_by_role("button", name="Run both agents"))
        d.wait_until(lambda: p.get_by_role("button", name="Run both agents").count() > 0,
                     "Ten agent runs in parallel (sped up)", timeout=500)
    elif name == "lab_show":
        d.hover(p.get_by_text("Governed agent").first); p.wait_for_timeout(1500)
        d.hover(p.get_by_text("Raw-data agent").first)
    elif name == "history":
        d.goto("/history")
        d.hover(p.locator("main").get_by_text("bservation").first); p.wait_for_timeout(1200)
        d.hover(p.locator("main").get_by_text("Undo").first)
    elif name == "close":
        p.set_content(TITLE_HTML.format(title="Agent-ready medical economics",
                                        sub="Their semantic layer stays the source of truth.<br>"
                                            "<span style='font-size:16px;color:#98A2B3'>Plains Health Plan is fictional; "
                                            "data is tigerclaw's synthetic MEE data. Agent waits are cut and marked.</span>"))


def main():
    from playwright.sync_api import sync_playwright
    t = time.time()
    voice = synthesize()
    print(f"voice: {sum(len(a) for r in voice for a in r) / SR:.0f}s in {time.time() - t:.0f}s", flush=True)
    log, state = [], {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(OUT / "raw"),
                                  record_video_size={"width": W, "height": H})
        ctx.add_init_script(OVERLAY_JS)
        page = ctx.new_page()
        page.set_default_timeout(20000)
        t0 = time.monotonic()
        d = Director(page, t0)
        page.set_content(TITLE_HTML.format(title="Medical economics, agent-ready",
                                           sub="Context Studio on a synthetic regional payer: ontology, governed "
                                               "metrics, owners, and agents on LangGraph"))
        page.wait_for_timeout(1500)
        for (chap, beats), audios in zip(SCENES, voice):
            for i, ((text, action), audio) in enumerate(zip(beats, audios)):
                start = d.now()
                log.append({"t": start, "action": action, "text": text, "dur": len(audio) / SR})
                if chap and i == 0:
                    d.chapter(chap)
                try:
                    act(d, action, state)
                except SystemExit:
                    page.screenshot(path=str(OUT / f"guard_{action}.png"))
                    raise
                except Exception as exc:
                    print(f"  ACTION FAILED {action}: {exc}", flush=True)
                    page.screenshot(path=str(OUT / f"fail_{action}.png"))
                end = d.now()
                log[-1]["acted"] = end - start
                # speak while acting: the line must finish before the next starts
                spoken = start + len(audio) / SR + GAP
                if spoken > d.now():
                    page.wait_for_timeout(int((spoken - d.now()) * 1000))
                print(f"{action:16} {start:6.1f}s  acted {end - start:5.1f}s", flush=True)
        page.wait_for_timeout(4000)
        total = d.now()
        video = Path(page.video.path())
        ctx.close(); browser.close()
    # ---- cuts: each wait keeps KEEP seconds at both ends; narration never falls inside a cut
    cuts = []
    for s, e, _ in d.waits:
        a, b = s + KEEP, e - KEEP
        spoken = [(x["t"], x["t"] + x["dur"]) for x in log]
        for ts, te in spoken:  # do not cut speech
            if ts < b and te > a:
                if ts <= a < te:
                    a = te + 0.2
                elif ts < b <= te:
                    b = ts - 0.2
        if b - a > 2.0:
            cuts.append((a, b))
    cuts.sort()
    keep, prev = [], 0.0
    for a, b in cuts:
        keep.append((prev, a)); prev = b
    keep.append((prev, total))
    shift = lambda t: t - sum(min(b, t) - a for a, b in cuts if t > a)  # noqa: E731
    track = np.zeros(int((shift(total) + 1) * SR), dtype=np.float32)
    for entry, audio in zip(log, [a for row in voice for a in row]):
        i = int((shift(entry["t"]) + 0.15) * SR)
        track[i:i + len(audio)] += audio[: max(0, len(track) - i)]
    sf.write(OUT / "voice.wav", track, SR)
    (OUT / "timeline.json").write_text(json.dumps({"log": log, "waits": d.waits, "cuts": cuts}, indent=1))
    sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keep)
    final = OUT / "Context_Studio_MEE_demo.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-i", str(OUT / "voice.wav"),
                    "-filter_complex", f"[0:v]select='{sel}',setpts=N/FRAME_RATE/TB[v];"
                                       "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "slow", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart",
                    str(final)], check=True)
    print(final, f"{total:.0f}s recorded, {shift(total):.0f}s after cuts", f"{final.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
