"""Plains Health at the size of a real payer model: the same synthetic data as scripts/health_demo.py, with about 800
measures, grown the way a large model grows. Each measure is repeated for every line of business, funding and state;
each PMPM for every plan, age band and risk band; each measure has prior-year, change, prior-month and year-to-date
versions; and there are legacy copies and display text. All of it goes through the same capture pipeline.

    uv run python scripts/health_demo.py                  # the data, once -> build/pbi/plains-health
    uv run python scripts/health_demo_large.py            # -> build/pbi/plains-large
    uv run ctx-studio serve --pbi build/pbi/plains-large

Names and values are made up. Writes only into the output folder.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import health_demo as hd  # noqa: E402

SLICES = {  # column -> values, from the generated data
    ("Member", "Line of Business"): ["Commercial", "Individual", "Medicaid", "Medicare Advantage"],
    ("Member", "Funding"): ["Fully insured", "Government", "Self-funded (ASO)"],
    ("Member", "State"): ["IL", "MT", "NM", "OK", "TX"],
}
PMPM_SLICES = {
    ("Member", "Plan"): ["Exchange Bronze", "Exchange Gold", "Exchange Silver", "HDHP", "HMO", "MA HMO", "MA PPO",
                         "Medicaid Managed Care", "PPO"],
    ("Member", "Age Band"): ["0-17", "18-34", "35-49", "50-64", "65+"],
    ("Member", "Risk Band"): ["High", "Low", "Moderate", "Very high"],
}
ADDITIVE = ("Member Months", "Premium", "Medical Paid", "Medical Allowed", "Pharmacy Paid", "Total Paid", "Admits",
            "Inpatient Days", "ER Visits", "Claims", "Scripts")


def measures() -> list[tuple[str, str, str, str]]:
    base = [m for m in hd.MEASURES if not m[0].endswith((" PY", " YoY %", " YTD"))]
    out = list(base)
    names = {m[0] for m in base}
    for name, _, fmt, folder in base:
        out.append((f"{name} PY", f"CALCULATE([{name}], SAMEPERIODLASTYEAR('Calendar'[Date]))", fmt, f"{folder} trend"))
        out.append((f"{name} YoY %", f"DIVIDE([{name}], [{name} PY]) - 1", hd.RATE, f"{folder} trend"))
        out.append((f"{name} PM", f"CALCULATE([{name}], DATEADD('Calendar'[Date], -1, MONTH))", fmt, f"{folder} trend"))
        if name in ADDITIVE:
            out.append((f"{name} YTD", f"TOTALYTD([{name}], 'Calendar'[Date])", fmt, f"{folder} trend"))
        for (t, c), vals in SLICES.items():
            for v in vals:
                out.append((f"{name} - {v}", f"CALCULATE([{name}], {hd.q(t)}[{c}] = \"{v}\")", fmt, f"{folder} by {c}"))
        if "PMPM" in name:
            for (t, c), vals in PMPM_SLICES.items():
                for v in vals:
                    out.append((f"{name} - {v}", f"CALCULATE([{name}], {hd.q(t)}[{c}] = \"{v}\")", fmt, f"{folder} by {c}"))
    for name, _, fmt, folder in base[:20]:  # what a model keeps after a rework
        out.append((f"zz_{name} (old)", f"[{name}]", fmt, "zz Archive"))
    for name in [n for n in names if "PMPM" in n]:  # text for a card's title
        out.append((f"{name} Title", f"\"{name} \" & FORMAT([{name}], \"$#,0\")", "", "Display"))
    seen, unique = set(), []
    for m in out:
        if m[0] not in seen:
            seen.add(m[0])
            unique.append(m)
    return unique


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default="build/pbi/plains-health", help="the folder health_demo.py wrote")
    ap.add_argument("--out", default="build/pbi/plains-large")
    a = ap.parse_args()
    data, out = Path(a.data), Path(a.out)
    if not (data / f"{hd.CATALOG}.duckdb").exists():
        print(f"no data in {data}: run scripts/health_demo.py first", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    hd.MODEL, hd.MEASURES = "plains-large", measures()

    from insurance_context.bilayer import build_spec, save
    from insurance_context.pbimodel import read_tmdl
    model = read_tmdl(hd.write_model(out))
    model.name = hd.MODEL
    spec = build_spec(model, hd.CATALOG, hd.SCHEMA)
    save(spec, out)
    for name in (f"{hd.CATALOG}.duckdb", f"{hd.CATALOG}.duckdb.about"):
        shutil.copy(data / name, out / name)
    shown = [v for v in spec["metrics"].values() if not v.get("hidden")]
    status: dict[str, int] = {}
    for v in shown:
        status[v["status"]] = status.get(v["status"], 0) + 1
    print(f"{hd.MODEL}: {len(hd.MEASURES)} measures, {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
