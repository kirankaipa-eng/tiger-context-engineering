"""Cut the finished take into one video per step, each with a process bar across the top."""
import json, subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
OUT = HERE / "out_mee"
SEG = OUT / "segments"; SEG.mkdir(exist_ok=True)
STEPS = [("Onboard", ["title", "onboard_open"]), ("Curate", ["scan_start"]), ("Govern a change", ["obs_question"]),
         ("Power BI", ["pbi_chat"]), ("Agents", ["ip_chat"]), ("Consistency", ["lab_run"]), ("History", ["history"])]
tl = json.loads((OUT / "timeline.json").read_text()); log, cuts = tl["log"], tl["cuts"]
shift = lambda t: t - sum(min(b, t) - a for a, b in cuts if t > a)
dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(OUT / "Context_Studio_MEE_demo.mp4")], capture_output=True, text=True).stdout)
starts = [shift(next(e["t"] for e in log if e["action"] == acts[0])) for _, acts in STEPS]
starts[0] = 0.0
close_t = shift(next(e["t"] for e in log if e["action"] == "close"))
ends = starts[1:] + [close_t]

BAR = """<html><body style="margin:0;width:1440px;height:64px;background:#141414;font-family:Inter,-apple-system,Helvetica,sans-serif;
display:flex;align-items:center;padding:0 24px;box-sizing:border-box;gap:8px">{items}</body></html>"""
def item(i, name, cur):
    on = i == cur; done = i < cur
    bg = "#F37820" if on else ("#2a2a2a" if not done else "#3a3a3a")
    fg = "#fff" if on else ("#bbb" if done else "#888")
    mark = "✓ " if done else f"{i + 1}. "
    arrow = '<span style="color:#555;font-size:16px">›</span>' if i < len(STEPS) - 1 else ""
    return (f'<span style="background:{bg};color:{fg};font:600 15px Inter,-apple-system,sans-serif;padding:8px 14px;'
            f'border-radius:999px;white-space:nowrap">{mark}{name}</span>{arrow}')

with sync_playwright() as pw:
    b = pw.chromium.launch(); p = b.new_page(viewport={"width": 1440, "height": 64})
    for k in range(len(STEPS)):
        p.set_content(BAR.format(items="".join(item(i, n, k) for i, (n, _) in enumerate(STEPS))))
        p.screenshot(path=str(SEG / f"bar{k}.png"))
    b.close()

for k, (name, _) in enumerate(STEPS):
    out = SEG / f"{k + 1:02d}_{name.lower().replace(' ', '_')}.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{starts[k]:.2f}", "-to", f"{ends[k]:.2f}",
                    "-i", str(OUT / "Context_Studio_MEE_demo.mp4"), "-i", str(SEG / f"bar{k}.png"),
                    "-filter_complex", "[0:v]pad=1440:964:0:64[p];[p][1:v]overlay=0:0[v]",
                    "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-crf", "22", "-preset", "slow",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)], check=True)
    print(f"{out.name:32} {ends[k] - starts[k]:5.1f}s {out.stat().st_size / 1e6:4.1f} MB")
