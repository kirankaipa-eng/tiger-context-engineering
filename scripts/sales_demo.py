"""A richer stand-in for the sales-returns sample, for live demos: the same Power BI model and tables, with
daily rows from Jan 2024 through Sep 2026 instead of 26 Sundays of 2019, and stories planted in them.

    uv run python scripts/sales_demo.py            # build/pbi/sales-returns -> build/pbi/sales-demo
    uv run ctx-studio serve --pbi build/pbi/sales-demo

Rows are resampled from the sample's own rows (so every product, store, customer and issue combination is
one the model already has), generated week by week, scaled by a trend and a season, and spread over the days. Planted stories:

- growth: Net Sales grow about 15% a year, with a holiday peak (Nov, Dec) and a summer dip (Jul, Aug)
- mix: from Jan 2026, Power BI sells far more at Internal stores, mostly on Coupon (-20%): this year
  against last, Power Platform's share of Net Sales jumps (the "why is it up" story)
- anomaly: Leo sells a lot of XBOX ONE; from Aug 2026 about half of it comes back, Material Defects (the
  "watcher" story)

Deterministic (fixed seed). Writes only into the output folder.
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

SCHEMA = "sales_returns"
FACTS = ("sales", "customer", "issues_and_promotions", "calendar")


def weeks(start: dt.date, end: dt.date) -> list[dt.date]:
    d = start  # the first day of each week
    out = []
    while d <= end:
        out.append(d)
        d += dt.timedelta(days=7)
    return out


def season(d: dt.date) -> float:
    return {11: 1.22, 12: 1.38, 7: 0.86, 8: 0.9, 1: 0.95}.get(d.month, 1.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default="build/pbi/sales-returns")
    ap.add_argument("--out", default="build/pbi/sales-demo")
    ap.add_argument("--weekly", type=int, default=900, help="rows in a typical week at the start")
    a = ap.parse_args()
    src, out = Path(a.source), Path(a.out)
    rng = np.random.default_rng(7)

    out.mkdir(parents=True, exist_ok=True)
    for p in src.iterdir():
        if p.name in ("pilot.duckdb", "agent") or p.name.startswith("."):
            continue
        dest = out / p.name
        if p.is_dir():
            shutil.copytree(p, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(p, dest)
    db = out / "pilot.duckdb"
    db.unlink(missing_ok=True)

    con = duckdb.connect(str(db))
    con.execute(f"ATTACH '{src / 'pilot.duckdb'}' AS s (READ_ONLY)")
    con.execute(f"CREATE SCHEMA {SCHEMA}")
    for (t,) in con.execute(f"SELECT table_name FROM information_schema.tables WHERE table_catalog = 's' AND table_schema = '{SCHEMA}'").fetchall():
        if t not in FACTS:
            con.execute(f'CREATE TABLE {SCHEMA}."{t}" AS SELECT * FROM s.{SCHEMA}."{t}"')

    rows = con.execute(f"""
        SELECT s.ProductID, s.StoreID, s.Status, s.Unit, s.Amount, c.Gender, c.Product, c.Category, c.Segment,
               c.Store, c.Type, c.Issue, c.Promotion, c.Age, c."Price Range"
        FROM s.{SCHEMA}.sales s JOIN s.{SCHEMA}.customer c USING (ID)""").fetchall()
    cols = ["ProductID", "StoreID", "Status", "Unit", "Amount", "Gender", "Product", "Category", "Segment", "Store",
            "Type", "Issue", "Promotion", "Age", "Price Range"]
    at = {c: i for i, c in enumerate(cols)}
    pick = lambda f: [r for r in rows if f(r)]  # noqa: E731
    power_bi = pick(lambda r: r[at["Product"]] == "Power BI" and r[at["Type"]] == "Internal" and r[at["Status"]] == "Sold")
    xbox_leo = pick(lambda r: r[at["Product"]] == "XBOX ONE" and r[at["Store"]] == "Leo")
    xbox_leo_sold = [r for r in xbox_leo if r[at["Status"]] == "Sold"]
    if not power_bi or not xbox_leo_sold:
        raise SystemExit("the sample has no rows for a planted story (Power BI at Internal stores, XBOX ONE at Leo)")
    defects = next(r for r in rows if r[at["Issue"]] == "Material Defects")

    out_rows = []
    start = dt.date(2024, 1, 1)
    for d in weeks(start, dt.date(2026, 9, 30)):
        years = (d - start).days / 365.25
        n = int(a.weekly * (1.15 ** years) * season(d) * rng.normal(1, 0.05))
        out_rows += [(d, rows[i]) for i in rng.integers(0, len(rows), n)]
        if d >= dt.date(2026, 1, 1):  # mix: Power BI at Internal stores, on coupons
            ramp = min(1.0, (d - dt.date(2026, 1, 1)).days / 120)
            for i in rng.integers(0, len(power_bi), int(n * 0.07 * ramp)):
                r = list(power_bi[i])
                r[at["Promotion"]] = "Coupon (-20%)"
                out_rows.append((d, tuple(r)))
        for i in rng.integers(0, len(xbox_leo_sold), int(n * 0.03)):  # Leo sells a lot of XBOX ONE, always
            out_rows.append((d, xbox_leo_sold[i]))
        if d >= dt.date(2026, 8, 1):  # anomaly: XBOX ONE returns at Leo, material defects
            for i in rng.integers(0, len(xbox_leo), int(n * 0.02)):
                r = list(xbox_leo[i])
                r[at["Status"]], r[at["Issue"]], r[at["Promotion"]] = "Returned", defects[at["Issue"]], "None"
                out_rows.append((d, tuple(r)))

    ids = np.unique(rng.integers(10**10, 10**12, size=len(out_rows) * 2))  # distinct, then in random order
    ids = rng.permutation(ids)[:len(out_rows)]
    f = pd.DataFrame([r for _, r in out_rows], columns=cols)
    # each week's rows spread over its seven days, so a month's total does not depend on how many Sundays it has
    days = rng.integers(0, 7, len(out_rows))
    f.insert(0, "Date", pd.to_datetime([d for d, _ in out_rows]) + pd.to_timedelta(days, unit="D"))
    f.insert(0, "ID", ids.astype("int64"))
    f = f[f["Date"] < "2026-10-01"]  # the last week stops at the end of September
    con.register("f", f)
    con.execute(f"""CREATE TABLE {SCHEMA}.sales AS SELECT ProductID, StoreID, Status, ID, Unit, Amount,
                    CAST(Date AS TIMESTAMP_NS) AS Date FROM f""")
    con.execute(f"""CREATE TABLE {SCHEMA}.customer AS SELECT ID, Unit, Gender, Product, Category, Segment, Store, Type,
                    Issue, Promotion, Amount, Age, "Price Range" FROM f""")
    con.execute(f"CREATE TABLE {SCHEMA}.issues_and_promotions AS SELECT ID, Issue, Promotion FROM f")
    con.execute(f"""CREATE TABLE {SCHEMA}.calendar AS
        SELECT CAST(d AS TIMESTAMP_NS) AS Date, strftime(d, '%b') AS "Month Name", month(d) AS MonthSort,
               CAST(strftime(d, '%U') AS BIGINT) + 1 AS Week
        FROM (SELECT CAST(range AS DATE) AS d FROM range(DATE '2024-01-01', DATE '2026-10-01', INTERVAL 1 DAY)) ORDER BY 1""")
    n, lo, hi = con.execute(f"SELECT count(*), min(Date), max(Date) FROM {SCHEMA}.sales").fetchone()
    con.close()
    Path(str(db) + ".about").write_text(f"generated demo data: the sample's rows resampled over {lo:%b %Y} to "
                                         f"{hi:%b %Y}, with planted stories")
    print(f"{out}: {n:,} sales rows, {lo:%Y-%m-%d} to {hi:%Y-%m-%d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
