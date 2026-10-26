"""Narrated demo: the medical economics desk, and how a business question becomes curation.

Runs live against the business demo (scripts/demo_business.sh, freshly built): a medical economics
lead asks, drills by clicking, asks why to trust a number, then asks for a metric nobody has defined.
The question reaches its owner in Context Studio, the curator drafts the definition, the owners
approve, and the same question gets a governed answer. Kokoro speaks each line; the browser acts;
agent waits are cut and marked; a process bar runs across the top.

    python record_desk.py http://localhost:3702 <out dir>
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3702"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "out_desk")
OUT.mkdir(parents=True, exist_ok=True)
W, H, BAR = 1440, 900, 64
SR = 24000
GAP = 0.45
KEEP = 1.0  # seconds kept at each end of a cut wait

os.environ.setdefault("ESPEAK_DATA_PATH", "/opt/homebrew/share/espeak-ng-data")
os.environ.setdefault("PHONEMIZER_ESPEAK_LIBRARY", "/opt/homebrew/lib/libespeak-ng.dylib")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

Q_TREND = "How is Medicare Advantage inpatient PMPM trending in Illinois?"
Q_WHY = "Why did it jump last quarter?"
Q_BH = "What is behavioral health PMPM in Illinois by quarter?"
DRAFT = ("This came from the medical economics desk. Please define BH PMPM as a governed metric: medical claims "
         "paid for mental health claims, per member per month.")

STEPS = ["Ask", "Why it moved", "Drill in", "Why trust it", "A new question", "Owners define it", "Asked again"]

SCENES = [
    (None, [
        ("This is the medical economics desk: the business view of the context layer, on Plains Health Plan, a "
         "fictional payer built on our synthetic data.", "title"),
    ]),
    (0, [
        ("A medical economics lead asks in plain language. Every answer is an exhibit, computed from the plan's "
         "governed definitions.", "desk_open"),
        ("How is Medicare Advantage inpatient cost per member trending in Illinois?", "ask_trend"),
        ("Five hundred thirty eight dollars in the last complete quarter, up thirty one percent. The newest "
         "quarter is still receiving claims, so it is marked, not compared.", "show_trend"),
    ]),
    (1, [
        ("Then, the question every finance meeting asks: why did it jump?", "ask_why"),
        ("Volume or price? Admissions per thousand barely moved. Cost per admission rose twenty eight percent. "
         "By clinical category, endocrine and mental health drove the increase, and oncology offset part of it. "
         "Each bar is that category's exact share of the change.", "show_why"),
    ]),
    (2, [
        ("Every bar is a way in. Click endocrine, choose admit type, and the analyst agent runs the drill.",
         "drill"),
        ("Elective admissions lead. And there is no observation row: under medical policy MP-112, observation "
         "stays count as outpatient.", "drill_show"),
    ]),
    (3, [
        ("Why trust this number? One click.", "trust"),
        ("The definition in plain words. The source tables, down to what agents are allowed to see. And the "
         "approved change that moved it: medical policy MP-112 took Medicare Advantage from three hundred seventy "
         "dollars to three hundred seventeen, approved by the EDW engineer, with the policy's own words.",
         "trust_show"),
    ]),
    (4, [
        ("Now a question the context does not cover yet: behavioral health PMPM.", "gap_ask"),
        ("The analyst does not improvise a number. It sends the question to the people who own the definitions.",
         "gap_show"),
    ]),
    (5, [
        ("In Context Studio, the actuarial lead, who owns governed metrics, finds the question waiting, with who "
         "asked it.", "studio_q"),
        ("They ask the curator to draft the definition. It reads the existing metrics and follows their shape.",
         "studio_draft"),
        ("The proposal carries the definition, its value in the data before anyone approves, and the Power B I "
         "measure generated from it.", "studio_open"),
        ("Every owner it touches approves: governed metrics, the plan's vocabulary, and medical economics. Now it "
         "is live, for every agent and every report.", "studio_approve"),
    ]),
    (6, [
        ("Back on the desk, the same question.", "again_ask"),
        ("This time there is a governed answer, from the definition the owners just approved.", "again_show"),
        ("And its history says who defined it, and when.", "again_trust"),
    ]),
    (None, [
        ("Every answer governed. Every gap becomes curation. The context layer gets better the more people use it.",
         "close"),
    ]),
]

TITLE_HTML = """<html><body style="margin:0;height:100vh;display:grid;place-items:center;background:#142338;
font-family:'IBM Plex Sans',Inter,-apple-system,Helvetica,Arial,sans-serif;color:#fff;overflow:hidden">
<div style="max-width:1040px;padding:0 80px">
<div style="font-size:17px;color:#5fc4bf;font-weight:600">Tiger Analytics, internal demo</div>
<div style="font-size:62px;font-weight:700;letter-spacing:-.02em;margin:14px 0 18px;line-height:1.08">{title}</div>
<div style="font-size:23px;color:#c9d4e2;line-height:1.5">{sub}</div></div></body></html>"""

OVERLAY_JS = """
(() => {
  const add = () => {
    if (document.getElementById('__cur')) return;
    const st = document.createElement('style');
    st.textContent = 'nextjs-portal{display:none!important}';
    document.head.appendChild(st);
    const c = document.createElement('div'); c.id = '__cur';
    c.style.cssText = 'position:fixed;left:0;top:0;width:22px;height:22px;z-index:2147483647;pointer-events:none;transform:translate(-100px,-100px)';
    c.innerHTML = '<svg width="22" height="22" viewBox="0 0 22 22"><path d="M3 2l14 8-6 1.6L8.5 18z" fill="#142338" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    document.body.appendChild(c);
    const w = document.createElement('div'); w.id = '__wait';
    w.style.cssText = 'position:fixed;left:50%;top:64px;transform:translateX(-50%);z-index:2147483646;pointer-events:none;background:#0f7b78;color:#fff;font:600 13px Inter,-apple-system,sans-serif;padding:6px 14px;border-radius:999px;opacity:0;transition:opacity .2s';
    document.body.appendChild(w);
    document.addEventListener('mousemove', e => { c.style.transform = `translate(${e.clientX - 3}px, ${e.clientY - 2}px)`; }, true);
    document.addEventListener('mousedown', e => {
      const r = document.createElement('div');
      r.style.cssText = `position:fixed;left:${e.clientX - 18}px;top:${e.clientY - 18}px;width:36px;height:36px;border-radius:50%;border:3px solid #c97c06;z-index:2147483646;pointer-events:none;transition:all .45s ease-out`;
      document.body.appendChild(r);
      requestAnimationFrame(() => { r.style.transform = 'scale(1.8)'; r.style.opacity = '0'; });
      setTimeout(() => r.remove(), 600);
    }, true);
  };
  if (document.body) add(); else document.addEventListener('DOMContentLoaded', add);
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
        self.p, self.t0, self.waits = page, t0, []

    def now(self):
        return time.monotonic() - self.t0

    def move(self, x, y, secs=0.5):
        self.p.mouse.move(x, y, steps=max(8, int(secs * 40)))

    def click(self, loc, pause=0.25):
        loc.scroll_into_view_if_needed()
        self.p.wait_for_timeout(150)
        b = loc.bounding_box()
        self.click_at(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, pause)

    def click_at(self, x, y, pause=0.25):
        self.move(x, y)
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

    def goto(self, path):
        self.p.goto(BASE + path)
        self.p.wait_for_load_state("networkidle")
        self.p.wait_for_timeout(1200)

    def person(self, label):
        sel = self.p.locator("header select").nth(1)
        self.hover(sel, 0.4)
        sel.select_option(label=label)
        self.p.wait_for_timeout(1500)

    def wait_until(self, check, label, timeout=300):
        start = self.now()
        self.p.evaluate("t => window.__wait && window.__wait(t)", label)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not check():
            self.p.wait_for_timeout(500)
        self.p.wait_for_timeout(700)
        self.p.evaluate("() => window.__wait && window.__wait('')")
        self.waits.append((start, self.now(), label))

    def type_into(self, box, text):
        self.click(box)
        box.press_sequentially(text, delay=18)
        self.p.wait_for_timeout(400)
        self.p.keyboard.press("Enter")
        self.p.wait_for_timeout(1200)

    def desk_ask(self, text, label):
        self.type_into(self.p.locator("aside textarea"), text)
        self.desk_wait(label)

    def desk_wait(self, label):
        ask = self.p.locator("button[aria-label='Ask']")
        self.wait_until(lambda: ask.count() > 0, label + " (sped up)", timeout=400)

    def studio_chat(self, text, label):
        box = self.p.get_by_placeholder("Ask about what you are looking at")
        self.click(box)
        box.fill(text)
        self.p.wait_for_timeout(600)
        self.p.keyboard.press("Enter")
        self.p.wait_for_timeout(1500)
        stop = self.p.locator('button[aria-label="Stop"]')
        self.wait_until(lambda: stop.count() == 0, label + " (sped up)", timeout=400)

    def inbox_item(self, text, tab="All open"):
        self.click(self.p.locator("main button").filter(has_text=tab).first)
        self.p.wait_for_timeout(900)
        self.click(self.p.locator("main button").filter(has_text=re.compile(text, re.I)).first)
        self.p.wait_for_timeout(1500)

    def approve(self):
        self.click(self.p.get_by_role("button", name="Approve", exact=True))
        self.p.wait_for_timeout(2500)


def guard(d, cond, msg):
    if not cond:
        raise SystemExit(f"guard: {msg}")


def act(d: Director, name: str):
    p = d.p
    ex = lambda n: p.locator(f"#exhibit-{n}")  # noqa: E731
    if name == "title":
        return
    if name == "desk_open":
        d.goto("/insights")
        d.hover(p.locator("aside h1"), 0.6); p.wait_for_timeout(800)
        d.hover(p.locator("aside button").filter(has_text="trending").first, 0.6)
    elif name == "ask_trend":
        d.desk_ask(Q_TREND, "The analyst computes the trend")
    elif name == "show_trend":
        guard(d, ex(1).get_by_text("$538", exact=True).count() > 0, "trend headline is not $538")
        d.hover(ex(1).locator(".num").first, 0.6); p.wait_for_timeout(1500)
        d.hover(ex(1).get_by_text("Still receiving claims").first, 0.8); p.wait_for_timeout(1200)
        d.hover(ex(1).get_by_text("Governed").first, 0.6)
    elif name == "ask_why":
        d.desk_ask(Q_WHY, "The analyst separates volume from price")
    elif name == "show_why":
        guard(d, ex(2).get_by_text("+28.3%").count() > 0, "unit cost is not +28.3%")
        d.hover(ex(2).get_by_text("Utilization").first, 0.6); p.wait_for_timeout(1500)
        d.hover(ex(2).get_by_text("Unit cost").first, 0.6); p.wait_for_timeout(1800)
        cv = ex(2).locator("canvas").first
        cv.scroll_into_view_if_needed(); p.wait_for_timeout(400)
        b = cv.bounding_box()
        for i in (1, 2, 3):
            d.move(b["x"] + 64 + (i + 0.5) * (b["width"] - 80) / 10, b["y"] + b["height"] * 0.45, 0.7)
            p.wait_for_timeout(1300)
    elif name == "drill":
        cv = ex(2).locator("canvas").first
        cv.scroll_into_view_if_needed(); p.wait_for_timeout(300)
        b = cv.bounding_box()
        d.click_at(b["x"] + 64 + 1.5 * (b["width"] - 80) / 10, b["y"] + b["height"] - 75)
        p.wait_for_timeout(1200)
        guard(d, p.locator("[role=menu]").count() > 0, "drill menu did not open")
        d.click(p.locator("[role=menu] button").filter(has_text="Admit type").first)
        d.desk_wait("The analyst runs the drill")
    elif name == "drill_show":
        guard(d, ex(3).count() > 0 and "admit type" in ex(3).locator("h2").inner_text().lower(),
              "drill did not produce an admit-type exhibit")
        cv = ex(3).locator("canvas").first
        cv.scroll_into_view_if_needed(); p.wait_for_timeout(300)
        b = cv.bounding_box()
        for f in (0.12, 0.5):
            d.move(b["x"] + b["width"] * 0.4, b["y"] + b["height"] * f + 12, 0.7); p.wait_for_timeout(1400)
    elif name == "trust":
        d.click(ex(3).get_by_role("button", name="Why trust this number?"))
        d.desk_wait("The analyst gathers the definition and its history")
    elif name == "trust_show":
        guard(d, ex(4).get_by_text("$317").count() > 0, "evidence does not show $370 -> $317")
        d.hover(ex(4).get_by_text("What it counts").first, 0.6); p.wait_for_timeout(1800)
        d.hover(ex(4).get_by_text("Where the data comes from").first, 0.6); p.wait_for_timeout(1200)
        d.hover(ex(4).get_by_text("Agents see").first, 0.6); p.wait_for_timeout(1500)
        d.hover(ex(4).get_by_text("$317").first, 0.6); p.wait_for_timeout(1800)
        d.hover(ex(4).locator("blockquote").first, 0.6)
    elif name == "gap_ask":
        d.desk_ask(Q_BH, "The analyst looks for a governed definition")
    elif name == "gap_show":
        guard(d, ex(5).get_by_text("No governed definition").count() > 0, "BH question was not recorded as a gap")
        d.hover(ex(5).get_by_text("No governed definition").first, 0.6); p.wait_for_timeout(1500)
        d.hover(ex(5).get_by_text("Sent to").first, 0.6)
    elif name == "studio_q":
        d.goto("/inbox")
        d.person("Actuarial lead")
        d.inbox_item("ehavioral|BH", tab="Questions")
        d.hover(p.locator("main").get_by_text("Plains medical economics lead").first, 0.6)
    elif name == "studio_draft":
        d.studio_chat(DRAFT, "The curator reads the metrics and drafts the definition")
    elif name == "studio_open":
        d.goto("/inbox")
        d.inbox_item(r"Add.*(BH|ehavioral)")
        approve = p.get_by_role("button", name="Approve", exact=True)
        d.wait_until(lambda: approve.count() > 0, "Checks run on the real data (sped up)", timeout=300)
        guard(d, p.locator("main").get_by_text("BH PMPM").count() > 0, "BH proposal not found")
        d.hover(p.locator("main").get_by_text("BH PMPM").first, 0.6); p.wait_for_timeout(1500)
        d.click(p.get_by_text("See exactly what changes").first); p.wait_for_timeout(1500)
        d.hover(p.get_by_text("governed_measures.tmdl").first, 0.6); p.wait_for_timeout(1200)
    elif name == "studio_approve":
        d.approve()
        for who in ("Plains data steward", "Plains medical economics lead"):
            d.person(who)
            d.inbox_item(r"Add.*(BH|ehavioral)")
            d.approve()
    elif name == "again_ask":
        d.goto("/insights")
        d.desk_ask(Q_BH, "The analyst computes the newly governed metric")
    elif name == "again_show":
        guard(d, ex(1).locator(".num").count() > 0 and p.get_by_text("No governed definition").count() == 0,
              "BH still answered as a gap")
        d.hover(ex(1).locator(".num").first, 0.6); p.wait_for_timeout(1500)
        d.hover(ex(1).get_by_text("Definition").first, 0.6)
    elif name == "again_trust":
        d.click(ex(1).get_by_role("button", name="Why trust this number?"))
        d.desk_wait("The analyst gathers the definition and its history")
        d.hover(ex(2).get_by_text("Changes that moved this number").first, 0.6); p.wait_for_timeout(800)
        d.hover(ex(2).locator("li").filter(has_text="approved by").first, 0.6)
    elif name == "close":
        p.set_content(TITLE_HTML.format(title="Every answer governed.<br>Every gap becomes curation.",
                                        sub="The context layer gets better the more people use it.<br>"
                                            "<span style='font-size:16px;color:#92a3bb'>Plains Health Plan is fictional; "
                                            "data is tigerclaw's synthetic MEE data. Agent waits are cut and marked.</span>"))


def bars():
    """One process-bar image per step (and one with none current, for the title)."""
    from playwright.sync_api import sync_playwright
    html = """<html><body style="margin:0;width:1440px;height:64px;background:#0d1a2b;font-family:Inter,-apple-system,Helvetica,sans-serif;
display:flex;align-items:center;padding:0 24px;box-sizing:border-box;gap:8px">{items}</body></html>"""

    def item(i, name, cur):
        on, done = i == cur, cur is not None and i < cur
        bg = "#0f7b78" if on else ("#1d3150" if done else "#15243a")
        fg = "#fff" if on else ("#c9d4e2" if done else "#7f91a8")
        mark = "✓ " if done else f"{i + 1}. "
        arrow = '<span style="color:#3d5372;font-size:16px">›</span>' if i < len(STEPS) - 1 else ""
        return (f'<span style="background:{bg};color:{fg};font:600 15px Inter,-apple-system,sans-serif;padding:8px 14px;'
                f'border-radius:999px;white-space:nowrap">{mark}{name}</span>{arrow}')
    with sync_playwright() as pw:
        b = pw.chromium.launch(); p = b.new_page(viewport={"width": W, "height": BAR})
        for k in [None, *range(len(STEPS))]:
            p.set_content(html.format(items="".join(item(i, n, k) for i, n in enumerate(STEPS))))
            p.screenshot(path=str(OUT / f"bar{'x' if k is None else k}.png"))
        b.close()


def main():
    from playwright.sync_api import sync_playwright
    t = time.time()
    voice = synthesize()
    print(f"voice: {sum(len(a) for r in voice for a in r) / SR:.0f}s in {time.time() - t:.0f}s", flush=True)
    log = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(OUT / "raw"),
                                  record_video_size={"width": W, "height": H})
        ctx.add_init_script(OVERLAY_JS)
        page = ctx.new_page()
        page.set_default_timeout(20000)
        page.goto(BASE + "/insights")
        page.evaluate("localStorage.setItem('studio.desk.client','plains_health'); "
                      "localStorage.setItem('studio.client','plains_health'); "
                      "localStorage.setItem('studio.user','plains-medecon-lead')")
        t0 = time.monotonic()
        d = Director(page, t0)
        page.set_content(TITLE_HTML.format(title="Ask. Drill. Trust.<br>And watch the context improve.",
                                           sub="The medical economics desk on the context layer: business questions "
                                               "answered from governed definitions, and gaps that become curation."))
        page.wait_for_timeout(1500)
        for (step, beats), audios in zip(SCENES, voice):
            for (text, action), audio in zip(beats, audios):
                start = d.now()
                log.append({"t": start, "action": action, "text": text, "dur": len(audio) / SR, "step": step})
                try:
                    act(d, action)
                except SystemExit:
                    page.screenshot(path=str(OUT / f"guard_{action}.png"))
                    raise
                except Exception as exc:
                    print(f"  ACTION FAILED {action}: {exc}", flush=True)
                    page.screenshot(path=str(OUT / f"fail_{action}.png"))
                end = d.now()
                log[-1]["acted"] = end - start
                spoken = start + len(audio) / SR + GAP
                if spoken > d.now():
                    page.wait_for_timeout(int((spoken - d.now()) * 1000))
                print(f"{action:16} {start:6.1f}s  acted {end - start:5.1f}s", flush=True)
        page.wait_for_timeout(4000)
        total = d.now()
        video = Path(page.video.path())
        ctx.close(); browser.close()
    cuts = []
    spoken = [(x["t"], x["t"] + x["dur"]) for x in log]
    for s, e, _ in d.waits:
        a, b = s + KEEP, e - KEEP
        for ts, te in spoken:
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
    # step windows on the cut timeline, for the process bar and the per-step segments
    marks = []
    for k in range(len(STEPS)):
        first = next(e for e in log if e["step"] == k)
        marks.append(shift(first["t"]))
    close_t = shift(next(e["t"] for e in log if e["action"] == "close"))
    windows = [(marks[k], marks[k + 1] if k + 1 < len(marks) else close_t) for k in range(len(STEPS))]
    (OUT / "timeline.json").write_text(json.dumps({"log": log, "waits": d.waits, "cuts": cuts, "steps": STEPS,
                                                   "windows": windows, "total": shift(total)}, indent=1))
    bars()
    sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keep)
    inputs = ["-i", str(video), "-i", str(OUT / "voice.wav"), "-i", str(OUT / "barx.png")]
    for k in range(len(STEPS)):
        inputs += ["-i", str(OUT / f"bar{k}.png")]
    chain = f"[0:v]select='{sel}',setpts=N/FRAME_RATE/TB,pad={W}:{H + BAR}:0:{BAR}[p0];[p0][2:v]overlay=0:0[q0]"
    last = "q0"
    for k, (a, b) in enumerate(windows):
        chain += f";[{last}][{k + 3}:v]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})'[q{k + 1}]"
        last = f"q{k + 1}"
    final = OUT / "Medical_Economics_Desk_demo.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *inputs,
                    "-filter_complex", chain + ";[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]",
                    "-map", f"[{last}]", "-map", "[a]", "-c:v", "libx264", "-preset", "slow", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart",
                    str(final)], check=True)
    print(final, f"{total:.0f}s recorded, {shift(total):.0f}s after cuts", f"{final.stat().st_size / 1e6:.1f} MB")
    seg = OUT / "segments"; seg.mkdir(exist_ok=True)
    for k, (a, b) in enumerate(windows):
        a = 0.0 if k == 0 else a
        out = seg / f"{k + 1:02d}_{STEPS[k].lower().replace(' ', '_')}.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", str(final),
                        "-c:v", "libx264", "-crf", "22", "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-b:a", "128k", "-movflags", "+faststart", str(out)], check=True)
        print(f"  {out.name:28} {b - a:5.1f}s")


if __name__ == "__main__":
    main()
