"""Re-voice one line of a finished take (same video, same cuts), checking it fits its slot."""
import json, subprocess, sys
from pathlib import Path
import numpy as np, soundfile as sf
sys.path.insert(0, str(Path(__file__).parent))
import record_mee as R
action, text = sys.argv[1], sys.argv[2]
out = R.OUT
tl = json.loads((out / "timeline.json").read_text()); log, cuts = tl["log"], tl["cuts"]
for chap, beats in R.SCENES:
    for i, (t, a) in enumerate(beats):
        if a == action:
            beats[i] = (text, a)
voice = [a for row in R.synthesize() for a in row]
idx = [e["action"] for e in log].index(action)
slot = log[idx + 1]["t"] - log[idx]["t"] - 0.2
print(f"new line {len(voice[idx]) / R.SR:.1f}s, slot {slot:.1f}s")
assert len(voice[idx]) / R.SR <= slot, "does not fit"
shift = lambda t: t - sum(min(b, t) - a for a, b in cuts if t > a)
total = log[-1]["t"] + 10
track = np.zeros(int((shift(total) + 1) * R.SR), dtype=np.float32)
for entry, audio in zip(log, voice):
    i = int((shift(entry["t"]) + 0.15) * R.SR)
    track[i:i + len(audio)] += audio[: max(0, len(track) - i)]
sf.write(out / "voice.wav", track, R.SR)
raw = sorted((out / "raw").glob("*.webm"))[-1]
keep, prev = [], 0.0
for a, b in cuts:
    keep.append((prev, a)); prev = b
keep.append((prev, 10_000))
sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keep)
final = out / "Context_Studio_MEE_demo.mp4"
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-i", str(out / "voice.wav"),
                "-filter_complex", f"[0:v]select='{sel}',setpts=N/FRAME_RATE/TB[v];[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]",
                "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(final)], check=True)
print(final, f"{final.stat().st_size / 1e6:.1f} MB")
