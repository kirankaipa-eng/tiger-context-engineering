"""Plains Health Plan: a fictional regional payer built for the medical economics (MEE) demo.

The source data is tigerclaw's synthetic payer warehouse plus its MEE augmentation
(provider master, peer cohorts, CPT modifiers, seeded anomalies), snapshotted to Parquet
under clients/plains_health/data/ by scripts/snapshot_mee_demo.py. This module loads that
snapshot into one source schema, the way a client's enterprise data warehouse would look:

    plh_src   member_enrollment, medical_claims, pharmacy_claims, prior_authorizations,
              adt_events, providers, peer_cohorts, claim_modifiers, anomalies

15,000 members in five states, 155k medical and 210k pharmacy claims, services from
October 2024 to March 2026, valued at PLAINS_VALUATION. Claims paid after the valuation
date (and the 'ibnr' rows, submitted later) are outside the paid-basis numbers, as in any
real warehouse at month end.

Planted for the demo:
  * every institutional claim is flagged inpatient, including 628 observation stays that
    the pack's service-category definition counts as outpatient (the curator's needle)
  * six providers with modifier 25 / 59 use seeded at 2.7x to 4.2x their peer cohort
    (plh_src.anomalies is the answer key; agents never see it)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

DATA = Path(__file__).resolve().parents[3] / "clients" / "plains_health" / "data"
PLAINS_VALUATION = date(2026, 3, 31)

TABLES = ("member_enrollment", "medical_claims", "pharmacy_claims", "prior_authorizations", "adt_events",
          "providers", "peer_cohorts", "claim_modifiers", "anomalies")
DATE_COLUMNS = {"date_of_birth", "enrollment_start", "enrollment_end", "admit_date"}


def _select(con, parquet: Path) -> str:
    """SELECT list that types date columns (the snapshot keeps SQLite's text dates)."""
    cols = con.execute(f"DESCRIBE SELECT * FROM '{parquet}'").fetchall()
    out = []
    for name, ctype, *_ in cols:
        if ctype.startswith("VARCHAR") and (name.endswith("_date") or name in DATE_COLUMNS):
            out.append(f'TRY_CAST("{name}" AS DATE) AS "{name}"')
        else:
            out.append(f'"{name}"')
    return ", ".join(out)


def build_plains(path: Path, data: Path = DATA) -> Path:
    missing = [t for t in TABLES if not (data / f"{t}.parquet").exists()]
    if missing:
        raise FileNotFoundError(f"Plains snapshot missing {missing}; run scripts/snapshot_mee_demo.py")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA plh_src")
    for t in TABLES:
        parquet = data / f"{t}.parquet"
        con.execute(f"CREATE TABLE plh_src.{t} AS SELECT {_select(con, parquet)} FROM '{parquet}'")
    con.close()
    return path


build_plains.valuation = PLAINS_VALUATION
