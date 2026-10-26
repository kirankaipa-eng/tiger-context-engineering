"""Snapshot tigerclaw's synthetic MEE demo data for the Plains Health Plan reference client.

Reads tigerclaw/data/demo/{ibnr_health,mee_synthetic}.db (read-only) and writes Parquet
files under clients/plains_health/data/, which the Plains warehouse fixture loads. Run it
again to refresh; tigerclaw is never written to.

Edits on the way in:
  * no real names: plan ids lose their client-specific prefix (``HCSC-`` becomes ``PLH-``)
    and real hospital names become neutral facility names
  * a steady-state book: the source was generated for an IBNR demo, so incurred volume
    rises and falls while membership is flat, which makes PMPM trend meaningless. Each
    claim's service (or fill) date is re-sampled inside its member's eligibility window
    (Oct 2024 - Mar 2026) with a ~7%/yr trend, using a hash of the claim id (deterministic).
    Each claim keeps its own submission and payment lags, so recent months still show
    runout; a claim submitted after the cut-off becomes 'ibnr'. Admissions move with their
    claim. Claims of the six seeded anomaly providers inside the anomaly window keep their
    dates, so the answer key holds. Eligibility is clipped to the claims window (spans
    that ended before it are treated as open), so every member month has claims behind it.
  * pharmacy costs scaled by RX_SCALE to an industry-typical pharmacy PMPM (copays unchanged)

    uv run python scripts/snapshot_mee_demo.py [--tigerclaw ../tigerclaw]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "clients" / "plains_health" / "data"

PLAN = "replace(plan_id, 'HCSC-', 'PLH-') AS plan_id"
FACILITY = "CASE WHEN facility_id IS NULL THEN NULL ELSE 'Plains Network Facility ' || facility_id END AS facility_name"

START, CUT = "DATE '2024-10-01'", "DATE '2026-03-31'"
TREND = 0.07 / 365  # relative growth per day of incurred volume
RX_SCALE = 0.3       # source Rx costs run ~3x industry levels; brings pharmacy PMPM to ~$135

# Eligibility window per member, clipped to the data window (spans that ended before it
# are treated as open, so no member's claims fall outside eligibility).
WINDOW = f"""
  win AS (
    SELECT member_id,
           greatest(CAST(enrollment_start AS DATE), {START}) AS ws,
           CASE WHEN CAST(enrollment_end AS DATE) < {START} OR enrollment_end IS NULL THEN {CUT}
                ELSE least(CAST(enrollment_end AS DATE), {CUT}) END AS we
    FROM ibnr.member_enrollment)"""


def _resample(key: str, start: str = "w.ws", end: str = "w.we") -> str:
    """A date in [start, end] with density rising TREND per day: inverse CDF of a linear density."""
    u = f"((hash({key} || 'svc') % 1000000) / 1000000.0)"
    L = f"greatest(date_diff('day', {start}, {end}), 0)"
    z = f"({L} + {TREND} * {L} * {L} / 2)"
    off = f"CAST(floor((-1 + sqrt(1 + 2 * {TREND} * {u} * {z})) / {TREND}) AS INTEGER)"
    return f"({start} + {off})"


MEDICAL = f"""
WITH {WINDOW},
  keep AS (  -- seeded anomaly claims keep their dates
    SELECT DISTINCT c.claim_id FROM ibnr.medical_claims c JOIN mee.anomalies a ON a.provider_id = c.provider_id
    WHERE CAST(c.service_date AS DATE) BETWEEN CAST(a.time_window_start AS DATE) AND CAST(a.time_window_end AS DATE)),
  base AS (
    SELECT c.*, CAST(c.service_date AS DATE) AS svc0,
           CASE WHEN k.claim_id IS NOT NULL THEN CAST(c.service_date AS DATE) ELSE {_resample('c.claim_id')} END AS svc,
           CAST(date_diff('day', CAST(c.service_date AS DATE), CAST(c.submission_date AS DATE)) AS INTEGER) AS sub_lag,
           CAST(date_diff('day', CAST(c.submission_date AS DATE), CAST(c.adjudication_date AS DATE)) AS INTEGER) AS adj_lag,
           CAST(date_diff('day', CAST(c.submission_date AS DATE), CAST(c.paid_date AS DATE)) AS INTEGER) AS paid_lag
    FROM ibnr.medical_claims c JOIN win w USING (member_id) LEFT JOIN keep k USING (claim_id)),
  dated AS (
    SELECT *, svc + sub_lag AS sub,
           CASE WHEN svc + sub_lag > {CUT} THEN 'ibnr' WHEN status = 'ibnr' THEN 'paid' ELSE status END AS st
    FROM base)
SELECT * EXCLUDE (svc0, svc, sub, st, sub_lag, adj_lag, paid_lag, service_date, submission_date, adjudication_date,
                  paid_date, status, facility_name),
       CAST(svc AS VARCHAR) AS service_date,
       CAST(sub AS VARCHAR) AS submission_date,
       CASE WHEN st IN ('paid', 'denied') THEN CAST(sub + coalesce(adj_lag, paid_lag, 14) AS VARCHAR) END AS adjudication_date,
       CASE WHEN st = 'paid' THEN CAST(sub + coalesce(paid_lag, 21) AS VARCHAR) END AS paid_date,
       st AS status,
       {FACILITY},
       CAST(date_diff('day', svc0, svc) AS INTEGER) AS _shift
FROM dated"""

PHARMACY = f"""
WITH {WINDOW}
SELECT r.* EXCLUDE (fill_date, submission_date, paid_amount, ingredient_cost, dispensing_fee),
       round(r.paid_amount * {RX_SCALE}, 2) AS paid_amount,
       round(r.ingredient_cost * {RX_SCALE}, 2) AS ingredient_cost,
       round(r.dispensing_fee * {RX_SCALE}, 2) AS dispensing_fee,
       CAST({_resample('r.rx_claim_id')} AS VARCHAR) AS fill_date,
       CAST({_resample('r.rx_claim_id')} + CAST(coalesce(r.submission_lag_days, 0) AS INTEGER) AS VARCHAR) AS submission_date
FROM ibnr.pharmacy_claims r JOIN win w USING (member_id)"""

# Admissions move with the institutional claim that bills them.
ADT = f"""
SELECT a.* EXCLUDE (admit_date, discharge_date, facility_name),
       CAST(CAST(a.admit_date AS DATE) + coalesce(m._shift, 0) AS VARCHAR) AS admit_date,
       CAST(CAST(a.discharge_date AS DATE) + coalesce(m._shift, 0) AS VARCHAR) AS discharge_date,
       {FACILITY}
FROM ibnr.adt_events a
LEFT JOIN (SELECT source_adt_id, any_value(_shift) AS _shift FROM medical WHERE source_adt_id IS NOT NULL
           GROUP BY source_adt_id) m ON m.source_adt_id = a.event_id"""

# table -> SELECT list (columns renamed or rewritten on the way in; * EXCLUDE keeps the rest),
# or a full query
TABLES = {
    ("ibnr", "member_enrollment"): f"""* EXCLUDE (plan_id, enrollment_start, enrollment_end), {PLAN},
        CAST(greatest(CAST(enrollment_start AS DATE), {START}) AS VARCHAR) AS enrollment_start,
        CASE WHEN CAST(enrollment_end AS DATE) < {START} THEN NULL ELSE enrollment_end END AS enrollment_end""",
    ("query", "medical_claims"): "SELECT * EXCLUDE (_shift) FROM medical",
    ("query", "pharmacy_claims"): PHARMACY,
    ("ibnr", "prior_authorizations"): "*",
    ("query", "adt_events"): ADT,
    ("mee", "providers"): "*",
    ("mee", "peer_cohorts"): "*",
    ("mee", "claim_modifiers"): "*",
    ("mee", "anomalies"): "*",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tigerclaw", type=Path, default=ROOT.parent / "tigerclaw")
    demo = ap.parse_args().tigerclaw / "data" / "demo"
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{demo / 'ibnr_health.db'}' AS ibnr (TYPE sqlite, READ_ONLY)")
    con.execute(f"ATTACH '{demo / 'mee_synthetic.db'}' AS mee (TYPE sqlite, READ_ONLY)")
    con.execute(f"CREATE TEMP TABLE medical AS {MEDICAL}")
    for (db, table), select in TABLES.items():
        target = OUT / f"{table}.parquet"
        sql = select if db == "query" else f"SELECT {select} FROM {db}.{table}"
        con.execute(f"COPY ({sql}) TO '{target}' (FORMAT parquet, COMPRESSION zstd)")
        n = con.execute(f"SELECT count(*) FROM '{target}'").fetchone()[0]
        print(f"{table:22} {n:>8,} rows  {target.stat().st_size / 1e6:6.1f} MB")
    leftover = con.execute(f"SELECT count(*) FROM '{OUT / 'member_enrollment.parquet'}' WHERE plan_id LIKE 'HCSC%'").fetchone()[0]
    assert leftover == 0, "plan ids still carry the client prefix"


if __name__ == "__main__":
    main()
