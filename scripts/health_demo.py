"""A medical economics demo: our own Power BI model for a made-up health plan (Plains Health), with the measures a
cost and utilization command center reports (PMPM by setting, admits and visits per 1,000, cost per admit, MLR,
GLP-1 spend), over synthetic claims, captured to SQL by the same pipeline a client's model goes through.

    uv run python scripts/health_demo.py              # -> build/pbi/plains-health
    uv run ctx-studio serve --pbi build/pbi/plains-health

The rows are synthetic. Members, their conditions, and the shape of their claims and fills are drawn from the
synthetic health warehouse in the tigerclaw repo (`--source`); facility names are made up here. Each member's
claims are resampled from that member's own claims, month by month from Jan 2024 to Sep 2026, so a diabetic
fills diabetic drugs and a cardiac member sees a cardiologist. Planted stories:

- trend: unit cost about +6% a year, utilization about +2%; respiratory care and the emergency room peak in
  winter; elective orthopedics dips in January and runs ahead in December (deductibles)
- GLP-1: from 2025, Commercial and Individual members aged 30 to 64 start on a GLP-1, women more often; by
  2026 it is the largest single reason Rx PMPM is up, and the main reason MLR crosses 88%
- unit cost: Lakeshore Regional's new contract from Jan 2026 makes its admissions about 24% dearer
- billing: six providers bill modifier 25 or 59 three to four times as often as their peers from Oct 2025
- behavioral health: visits among members aged 18 to 34 grow about 30% a year from 2025
- Medicare Advantage: about 15% more members from Jan 2026

Deterministic (fixed seed). Writes only into the output folder.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

MODEL = "plains-health"
CATALOG, SCHEMA = "pilot", "plains_health"
START, END = pd.Timestamp("2024-01-01"), pd.Timestamp("2026-09-30")
MONTHS = pd.date_range(START, END, freq="MS")

LOB = {"Commercial": "Commercial", "Individual": "Individual", "Medicaid": "Medicaid", "Medicare_Advantage": "Medicare Advantage"}
PLAN = {"Commercial": ["PPO", "HMO", "PPO", "HDHP"], "Individual": ["Exchange Silver", "Exchange Gold", "Exchange Bronze"],
        "Medicaid": ["Medicaid Managed Care"], "Medicare_Advantage": ["MA HMO", "MA PPO"]}
# 2024 targets, shaped like a regional plan's book: medical and Rx PMPM, the medical split by setting, admits and
# ER visits per 1,000 a year. Premiums are then set so 2024 runs at an 84% MLR, before the stories land.
MEDICAL_PMPM = {"Commercial": 410, "Individual": 440, "Medicaid": 360, "Medicare_Advantage": 980}
RX_PMPM = {"Commercial": 98, "Individual": 90, "Medicaid": 72, "Medicare_Advantage": 255}
SHARE = {"Inpatient": 0.30, "Outpatient": 0.30, "Professional": 0.32, "Emergency": 0.08}
PER_1000 = {"Inpatient": {"Commercial": 58, "Individual": 70, "Medicaid": 95, "Medicare_Advantage": 255},
            "Emergency": {"Commercial": 190, "Individual": 230, "Medicaid": 480, "Medicare_Advantage": 420}}
MLR_2024 = 0.84
FACILITIES = {  # made-up names: one per synthetic facility
    "IL01": "Lakeshore Regional Medical Center", "IL02": "Prairie View Hospital", "IL03": "North Shore Memorial",
    "IL04": "Riverbend Medical Center", "IL05": "Midway University Hospital", "IL06": "Fox Valley Medical Center",
    "IL07": "Heartland General", "IL08": "Sangamon Health", "MT01": "Big Sky Clinic Hospital",
    "MT02": "Yellowstone Medical Center", "MT03": "Bitterroot Community Hospital", "NM01": "Sandia University Hospital",
    "NM02": "Rio Grande Medical Center", "NM03": "Mesa Vista Hospital", "OK01": "Red Earth Medical Center",
    "OK02": "Canadian River Hospital", "OK03": "Arkansas Valley Medical Center", "OK04": "Twin Rivers Hospital",
    "TX01": "Bayou City Medical Center", "TX02": "Trinity Medical Center", "TX03": "Brazos Health",
    "TX04": "Lone Star Presbyterian", "TX05": "Gulf Coast Medical Center", "TX06": "Alamo Regional Hospital",
    "TX07": "Hill Country Hospital"}
CONTRACT = ("Lakeshore Regional Medical Center", pd.Timestamp("2026-01-01"), 1.24)
CATEGORY = {"cardiovascular": "Cardiovascular", "endocrine": "Endocrine", "gastrointestinal": "Gastrointestinal",
            "mental_health": "Behavioral Health", "neurological": "Neurological", "obstetric": "Maternity",
            "oncology": "Oncology", "orthopedic": "Musculoskeletal", "renal": "Renal", "respiratory": "Respiratory"}
CONDITION = {"hypertension": "Hypertension", "hyperlipidemia": "Hyperlipidemia", "anxiety": "Anxiety",
             "diabetes_t2": "Diabetes", "depression": "Depression", "hypothyroidism": "Hypothyroidism", "copd": "COPD",
             "ckd": "Chronic Kidney Disease", "chf": "Heart Failure", "asthma": "Asthma", "cad": "Coronary Artery Disease"}


def months_since(d: pd.Series | pd.Timestamp, base=START):
    return (d.year - base.year) * 12 + (d.month - base.month)


def setting(row_type: pd.Series, cpt: pd.Series) -> np.ndarray:
    cpt = cpt.fillna("")
    return np.select(
        [row_type == "institutional", cpt.str.startswith("9928"), cpt.str.match(r"^(99213|99214|99215|908)")],
        ["Inpatient", "Emergency", "Professional"], "Outpatient")


def age_band(age: pd.Series) -> pd.Series:
    return pd.cut(age, [-1, 17, 34, 49, 64, 200], labels=["0-17", "18-34", "35-49", "50-64", "65+"]).astype(str)


def risk_band(r: pd.Series) -> pd.Series:
    return pd.cut(r, [-1, 1.0, 1.5, 2.25, 99], labels=["Low", "Moderate", "High", "Very high"]).astype(str)


# ------------------------------------------------------------------ the people

def members(src: Path, rng) -> pd.DataFrame:
    with sqlite3.connect(src / "ibnr_health.db") as con:
        m = pd.read_sql("select member_id, age, gender, state, county, plan_type, chronic_conditions, risk_score "
                        "from member_enrollment", con)
    m = m[m.plan_type.isin(LOB)].reset_index(drop=True)
    conds = m.chronic_conditions.str.findall(r'"([a-z_0-9]+)"')
    m["Primary Condition"] = conds.map(lambda c: CONDITION.get(c[0], c[0].replace("_", " ").title()) if c else "None")
    m["Chronic Conditions"] = conds.map(len).clip(upper=3).map({0: "None", 1: "One", 2: "Two", 3: "Three or more"})
    # Medicare Advantage grows about 15% in Jan 2026: new members, a little healthier than the book
    ma = m[m.plan_type == "Medicare_Advantage"]
    new = ma.sample(frac=0.15, random_state=3).copy()
    new["source_id"] = new.member_id  # whose claims a new member's are resampled from
    new["member_id"] = [f"MBR-N{i:05d}" for i in range(len(new))]
    new["risk_score"] = (new.risk_score * 0.9).round(3)
    new["joined"] = 24
    m["joined"] = np.where(rng.random(len(m)) < 0.82, 0, rng.integers(1, len(MONTHS) - 3, len(m)))
    m["source_id"] = m.member_id
    m = pd.concat([m, new], ignore_index=True)
    stay = rng.geometric(0.011, len(m))  # months enrolled: about 1% leave each month
    m["left"] = np.minimum(m.joined + stay, len(MONTHS))
    plans = [PLAN[p][i % len(PLAN[p])] for i, p in enumerate(m.plan_type)]
    m["Plan"] = plans
    m["Funding"] = np.where(m.plan_type == "Commercial", np.where(rng.random(len(m)) < 0.45, "Self-funded (ASO)", "Fully insured"),
                            np.where(m.plan_type == "Medicaid", "Government", np.where(m.plan_type == "Individual", "Fully insured", "Government")))
    return m


def member_months(m: pd.DataFrame, rng) -> pd.DataFrame:
    idx = np.repeat(np.arange(len(m)), (m.left - m.joined).to_numpy())
    k = np.concatenate([np.arange(a, b) for a, b in zip(m.joined, m.left)])
    mm = pd.DataFrame({"i": idx, "k": k})
    lob = m.plan_type.to_numpy()[idx]
    years = k / 12
    # risk scores drift up a little in Medicare Advantage (coding), less elsewhere
    drift = np.where(lob == "Medicare_Advantage", 1.035, 1.01) ** years
    mm["Member ID"] = m.member_id.to_numpy()[idx]
    mm["Month"] = MONTHS[k]
    mm["Member Months"] = 1
    mm["lob"] = lob
    mm["Risk Score"] = (m.risk_score.to_numpy()[idx] * drift).round(3)
    return mm


# ------------------------------------------------------------------ the claims

def season(month: np.ndarray, category: np.ndarray, where: np.ndarray, cpt: np.ndarray) -> np.ndarray:
    w = np.ones(len(month))
    winter = np.isin(month, [12, 1, 2])
    w[winter & (category == "Respiratory")] *= 1.55
    w[winter & (where == "Emergency")] *= 1.18
    elective = np.isin(cpt, ["27447", "27130", "29881", "63047", "22612"])
    w[elective & (month == 1)] *= 0.7
    w[elective & (month == 12)] *= 1.35
    return w


def draw(pool: pd.DataFrame, mm: pd.DataFrame, m: pd.DataFrame, rate_months: int, rng) -> pd.DataFrame:
    """Rows for each member-month, resampled from that member's own source rows at that member's own rate."""
    pool = pool.sort_values("member_id").reset_index(drop=True)
    first = pool.groupby("member_id").indices
    start = {k: v[0] for k, v in first.items()}
    count = {k: len(v) for k, v in first.items()}
    src = m.source_id.to_numpy()[mm.i.to_numpy()]
    n_own = np.array([count.get(s, 0) for s in src])
    s0 = np.array([start.get(s, 0) for s in src])
    k = mm.k.to_numpy()
    lam = n_own / rate_months * 1.02 ** (k / 12)  # utilization trend
    n = rng.poisson(lam)
    rows = np.repeat(np.arange(len(mm)), n)
    pick = s0[rows] + (rng.random(len(rows)) * n_own[rows]).astype(int)
    out = pool.iloc[pick].reset_index(drop=True)
    out["mm"] = rows
    out["k"] = k[rows]
    out["Member ID"] = mm["Member ID"].to_numpy()[rows]
    return out


def thin(df: pd.DataFrame, weight: np.ndarray, rng) -> pd.DataFrame:
    """Each row kept as many times as a Poisson draw of its weight: the expected count scales by the weight."""
    times = rng.poisson(weight)
    return df.loc[df.index.repeat(times)].reset_index(drop=True)


def medical(src: Path, m: pd.DataFrame, mm: pd.DataFrame, rng) -> pd.DataFrame:
    with sqlite3.connect(src / "ibnr_health.db") as con:
        pool = pd.read_sql(
            "select c.member_id, c.provider_id, c.facility_id, c.claim_type, c.drg_code, c.drg_desc, c.cpt_code, c.cpt_desc, "
            "c.clinical_category, c.paid_amount, c.allowed_amount, c.billed_amount, c.status, c.denial_reason, "
            "a.length_of_stay, a.admit_type from medical_claims c left join adt_events a on a.event_id = c.source_adt_id", con)
    with sqlite3.connect(src / "mee_synthetic.db") as con:
        anomalies = pd.read_sql("select kind, provider_id, magnitude from anomalies", con)
    c = draw(pool, mm, m, 18, rng)
    month = MONTHS[c.k].month.to_numpy()
    c["Setting"] = setting(c.claim_type, c.cpt_code)
    c["Clinical Category"] = c.clinical_category.map(CATEGORY).fillna("Other")
    age = m.age.to_numpy()[mm.i.to_numpy()[c.mm]]
    w = season(month, c["Clinical Category"].to_numpy(), c.Setting.to_numpy(), c.cpt_code.fillna("").to_numpy())
    young_bh = (c["Clinical Category"] == "Behavioral Health").to_numpy() & (age >= 18) & (age <= 34)
    w *= np.where(young_bh, 1.30 ** np.clip((c.k.to_numpy() - 12) / 12, 0, None), 1)
    c = thin(c, w, rng)

    # utilization: admits and ER visits thinned to each line of business's 2024 rate per 1,000
    c["lob"] = mm.lob.to_numpy()[c.mm]
    months = mm[mm.k < 12].lob.value_counts()
    for where, target in PER_1000.items():
        now = c[(c.k < 12) & (c.Setting == where)].lob.value_counts() * 12000 / months
        keep = c.lob.map(lambda x: min(1.0, target[x] / now[x])).to_numpy()
        c = c[(c.Setting != where).to_numpy() | (rng.random(len(c)) < keep)].reset_index(drop=True)
    # amounts: each line of business and setting starts at its 2024 target PMPM, then unit cost trends up
    base = c[c.k < 12].groupby(["lob", "Setting"]).paid_amount.sum()
    scale = np.array([MEDICAL_PMPM[x] * SHARE[st] * months[x] / base[(x, st)] for x, st in zip(c.lob, c.Setting)])
    cost = scale * 1.06 ** (c.k.to_numpy() / 12) * rng.lognormal(0, 0.08, len(c))
    c["Facility"] = c.facility_id.fillna("").str.removeprefix("FAC-").map(FACILITIES).fillna("")
    day = MONTHS[c.k] + pd.to_timedelta(rng.integers(0, 28, len(c)), unit="D")
    contract = (c.Facility == CONTRACT[0]).to_numpy() & (c.Setting == "Inpatient").to_numpy() & (day >= CONTRACT[1])
    cost = cost * np.where(contract, CONTRACT[2], 1)
    denied = c.status.eq("denied").to_numpy()
    c["Paid"] = np.where(denied, 0, (c.paid_amount * cost).round(2))
    c["Allowed"] = (c.allowed_amount * cost).round(2)
    c["Billed"] = (c.billed_amount * cost).round(2)
    c["Service Date"] = day

    # modifiers on office and ER visits (25) and procedures (59); six providers drift from Oct 2025
    em = c.cpt_code.fillna("").str.match(r"^992").to_numpy()
    proc = (c.Setting == "Outpatient").to_numpy()
    p25, p59 = np.where(em, 0.07, 0.0), np.where(proc, 0.05, 0.0)
    late = (c["Service Date"] >= "2025-10-01").to_numpy()
    for kind, prv, mag in anomalies.itertuples(index=False):
        hit = (c.provider_id == prv).to_numpy() & late
        if kind.startswith("modifier_25"):
            p25 = np.where(hit & em, np.minimum(0.07 * mag, 0.6), np.where(hit, 0.25, p25))
        else:
            p59 = np.where(hit, np.minimum(0.05 * mag, 0.6), p59)
    u = rng.random(len(c))
    c["Modifier"] = np.where(u < p25, "25", np.where(u < p25 + p59, "59", "None"))

    # admissions: length of stay, and a readmission when the same member is back within 30 days
    ip = c.Setting == "Inpatient"
    c["Admits"] = ip.astype(int)
    c["Inpatient Days"] = np.where(ip, c.length_of_stay.fillna(3).clip(lower=1), 0).astype(int)
    c["Admit Type"] = np.where(ip, c.admit_type.fillna("emergency").str.title(), "")
    adm = c[ip].sort_values(["Member ID", "Service Date"])
    gap = adm.groupby("Member ID")["Service Date"].diff().dt.days
    c["Readmission"] = 0  # back within 30 days, and not a planned return (about half are)
    c.loc[adm.index[(gap <= 30).to_numpy() & (rng.random(len(adm)) < 0.5)], "Readmission"] = 1
    c["ER Visits"] = (c.Setting == "Emergency").astype(int)
    c["Status"] = c.status.str.title()
    c["Claim ID"] = [f"CLM-{i:07d}" for i in range(len(c))]
    return c.rename(columns={"provider_id": "Provider ID", "drg_desc": "DRG", "cpt_desc": "Procedure"})[[
        "Claim ID", "Member ID", "Provider ID", "Service Date", "Setting", "Clinical Category", "Facility", "DRG",
        "Procedure", "Admit Type", "Modifier", "Status", "Billed", "Allowed", "Paid", "Admits", "Inpatient Days",
        "Readmission", "ER Visits"]]


def pharmacy(src: Path, m: pd.DataFrame, mm: pd.DataFrame, rng) -> pd.DataFrame:
    with sqlite3.connect(src / "ibnr_health.db") as con:
        pool = pd.read_sql("select member_id, drug_name, therapeutic_class, therapeutic_subclass, is_specialty, "
                           "is_mail_order, days_supply, paid_amount from pharmacy_claims", con)
    rx = draw(pool, mm, m, 18, rng)
    glp1 = rx.therapeutic_subclass.eq("GLP-1 Agonist").to_numpy()
    # existing GLP-1 users fill a little more often each year; the new starters below are the story
    rx = thin(rx, np.where(glp1, 1.08 ** (rx.k.to_numpy() / 12), 1.0), rng)

    # GLP-1 starters: Commercial and Individual, 30 to 64, women more often; once started, they fill most months
    elig = m.plan_type.isin(["Commercial", "Individual"]) & m.age.between(30, 64)
    hazard = np.where(m.gender == "F", 1.7, 1.0)
    templates = pool[pool.therapeutic_subclass == "GLP-1 Agonist"]
    start = np.full(len(m), 10 ** 6)
    for k in range(12, len(MONTHS)):  # Jan 2025 on, uptake rising each month
        p = 0.0035 * (1 + (k - 12) * 0.12) * hazard
        new = elig.to_numpy() & (start == 10 ** 6) & (rng.random(len(m)) < p) & (m.joined.to_numpy() <= k) & (m.left.to_numpy() > k)
        start[new] = k
    on = mm[(start[mm.i.to_numpy()] <= mm.k.to_numpy()) & (rng.random(len(mm)) < 0.85)]
    t = templates.sample(len(on), replace=True, random_state=5).reset_index(drop=True)
    t["mm"], t["k"], t["Member ID"] = on.index.to_numpy(), on.k.to_numpy(), on["Member ID"].to_numpy()
    rx = pd.concat([rx, t], ignore_index=True)

    # each line of business starts at its 2024 target Rx PMPM (before the GLP-1 starters), then prices trend up
    rx["lob"] = mm.lob.to_numpy()[rx.mm]
    months = mm[mm.k < 12].lob.value_counts()
    base = rx[(rx.k < 12) & ~rx.index.isin(range(len(rx) - len(t), len(rx)))].groupby("lob").paid_amount.sum()
    scale = rx.lob.map(lambda x: RX_PMPM[x] * months[x] / base[x]).to_numpy()
    spec = rx.is_specialty.to_numpy() == 1
    glp = rx.therapeutic_subclass.eq("GLP-1 Agonist").to_numpy()
    trend = np.where(spec, 1.12, 1.04) ** (rx.k.to_numpy() / 12)  # specialty drug prices outrun the rest
    rx["Paid"] = (rx.paid_amount * trend * scale * rng.lognormal(0, 0.05, len(rx))).round(2)
    # a GLP-1 fill costs what it costs, whoever's book it lands in: about $700 net a month
    rx.loc[glp, "Paid"] = (700 * 1.03 ** (rx.k[glp] / 12) * rng.lognormal(0, 0.05, glp.sum())).round(2)
    rx["Fill Date"] = MONTHS[rx.k] + pd.to_timedelta(rng.integers(0, 28, len(rx)), unit="D")
    rx["Specialty"] = np.where(spec, "Specialty", "Non-specialty")
    rx["Channel"] = np.where(rx.is_mail_order == 1, "Mail order", "Retail")
    rx["Scripts"] = 1
    rx["Fill ID"] = [f"RX-{i:07d}" for i in range(len(rx))]
    return rx.rename(columns={"drug_name": "Drug", "therapeutic_class": "Drug Class", "therapeutic_subclass": "Drug Type",
                              "days_supply": "Days Supply"})[[
        "Fill ID", "Member ID", "Fill Date", "Drug", "Drug Class", "Drug Type", "Specialty", "Channel",
        "Days Supply", "Scripts", "Paid"]]


def providers(src: Path) -> pd.DataFrame:
    with sqlite3.connect(src / "mee_synthetic.db") as con:
        p = pd.read_sql("select provider_id, provider_name, specialty, network, primary_state, peer_cohort_id, "
                        "years_in_practice from providers", con)
    p["Network"] = np.where(p.network == "Out-of-Network", "Out of network", "In network")
    return p.rename(columns={"provider_id": "Provider ID", "provider_name": "Provider", "specialty": "Specialty",
                             "network": "Network Product", "primary_state": "Provider State",
                             "peer_cohort_id": "Peer Cohort", "years_in_practice": "Years in Practice"})


# ------------------------------------------------------------------ the Power BI model

COLUMNS = {  # table -> [(column, TMDL type)]
    "Member": [("Member ID", "string"), ("Gender", "string"), ("Age", "int64"), ("Age Band", "string"),
               ("State", "string"), ("County", "string"), ("Line of Business", "string"), ("Plan", "string"),
               ("Funding", "string"), ("Primary Condition", "string"), ("Chronic Conditions", "string"),
               ("Risk Band", "string")],
    "Member Months": [("Member ID", "string"), ("Month", "dateTime"), ("Member Months", "int64"),
                      ("Premium", "double"), ("Risk Score", "double")],
    "Medical Claims": [("Claim ID", "string"), ("Member ID", "string"), ("Provider ID", "string"),
                       ("Service Date", "dateTime"), ("Setting", "string"), ("Clinical Category", "string"),
                       ("Facility", "string"), ("DRG", "string"), ("Procedure", "string"), ("Admit Type", "string"),
                       ("Modifier", "string"), ("Status", "string"), ("Billed", "double"), ("Allowed", "double"),
                       ("Paid", "double"), ("Admits", "int64"), ("Inpatient Days", "int64"),
                       ("Readmission", "int64"), ("ER Visits", "int64")],
    "Pharmacy Claims": [("Fill ID", "string"), ("Member ID", "string"), ("Fill Date", "dateTime"), ("Drug", "string"),
                        ("Drug Class", "string"), ("Drug Type", "string"), ("Specialty", "string"),
                        ("Channel", "string"), ("Days Supply", "int64"), ("Scripts", "int64"), ("Paid", "double")],
    "Provider": [("Provider ID", "string"), ("Provider", "string"), ("Specialty", "string"), ("Network", "string"),
                 ("Network Product", "string"), ("Provider State", "string"), ("Peer Cohort", "string"),
                 ("Years in Practice", "int64")],
    "Calendar": [("Date", "dateTime")],
}
RELATIONSHIPS = [("Member Months", "Member ID", "Member", "Member ID"), ("Member Months", "Month", "Calendar", "Date"),
                 ("Medical Claims", "Member ID", "Member", "Member ID"),
                 ("Medical Claims", "Service Date", "Calendar", "Date"),
                 ("Medical Claims", "Provider ID", "Provider", "Provider ID"),
                 ("Pharmacy Claims", "Member ID", "Member", "Member ID"),
                 ("Pharmacy Claims", "Fill Date", "Calendar", "Date")]
DOLLARS, RATE, COUNT, RATIO = r"\$#,0;-\$#,0;\$#,0", "0.0%;-0.0%;0.0%", "#,0", "#,0.00"
MEASURES = [  # (name, DAX, format, folder)
    ("Member Months", "SUM('Member Months'[Member Months])", COUNT, "Membership"),
    ("Members", "DISTINCTCOUNT('Member Months'[Member ID])", COUNT, "Membership"),
    ("Premium", "SUM('Member Months'[Premium])", DOLLARS, "Membership"),
    ("Average Risk Score", "AVERAGE('Member Months'[Risk Score])", "0.000", "Membership"),
    ("Medical Paid", "SUM('Medical Claims'[Paid])", DOLLARS, "Cost"),
    ("Medical Allowed", "SUM('Medical Claims'[Allowed])", DOLLARS, "Cost"),
    ("Pharmacy Paid", "SUM('Pharmacy Claims'[Paid])", DOLLARS, "Cost"),
    ("Total Paid", "[Medical Paid] + [Pharmacy Paid]", DOLLARS, "Cost"),
    ("Total PMPM", "DIVIDE([Total Paid], [Member Months])", DOLLARS, "PMPM"),
    ("Medical PMPM", "DIVIDE([Medical Paid], [Member Months])", DOLLARS, "PMPM"),
    ("Rx PMPM", "DIVIDE([Pharmacy Paid], [Member Months])", DOLLARS, "PMPM"),
    ("Inpatient PMPM", "DIVIDE(CALCULATE([Medical Paid], 'Medical Claims'[Setting] = \"Inpatient\"), [Member Months])", DOLLARS, "PMPM"),
    ("Outpatient PMPM", "DIVIDE(CALCULATE([Medical Paid], 'Medical Claims'[Setting] = \"Outpatient\"), [Member Months])", DOLLARS, "PMPM"),
    ("Professional PMPM", "DIVIDE(CALCULATE([Medical Paid], 'Medical Claims'[Setting] = \"Professional\"), [Member Months])", DOLLARS, "PMPM"),
    ("Emergency PMPM", "DIVIDE(CALCULATE([Medical Paid], 'Medical Claims'[Setting] = \"Emergency\"), [Member Months])", DOLLARS, "PMPM"),
    ("GLP-1 PMPM", "DIVIDE(CALCULATE([Pharmacy Paid], 'Pharmacy Claims'[Drug Type] = \"GLP-1 Agonist\"), [Member Months])", DOLLARS, "PMPM"),
    ("Specialty Rx PMPM", "DIVIDE(CALCULATE([Pharmacy Paid], 'Pharmacy Claims'[Specialty] = \"Specialty\"), [Member Months])", DOLLARS, "PMPM"),
    ("Risk-Adjusted PMPM", "DIVIDE([Total PMPM], [Average Risk Score])", DOLLARS, "PMPM"),
    ("Medical Loss Ratio", "DIVIDE([Total Paid], [Premium])", RATE, "Financial"),
    ("Admits", "SUM('Medical Claims'[Admits])", COUNT, "Utilization"),
    ("Admits per 1,000", "DIVIDE([Admits] * 12000, [Member Months])", "#,0.0", "Utilization"),
    ("Inpatient Days", "SUM('Medical Claims'[Inpatient Days])", COUNT, "Utilization"),
    ("Days per 1,000", "DIVIDE([Inpatient Days] * 12000, [Member Months])", "#,0.0", "Utilization"),
    ("Average Length of Stay", "DIVIDE([Inpatient Days], [Admits])", "0.0", "Utilization"),
    ("Cost per Admit", "DIVIDE(CALCULATE([Medical Paid], 'Medical Claims'[Setting] = \"Inpatient\"), [Admits])", DOLLARS, "Unit cost"),
    ("Readmission Rate", "DIVIDE(SUM('Medical Claims'[Readmission]), [Admits])", RATE, "Quality"),
    ("ER Visits", "SUM('Medical Claims'[ER Visits])", COUNT, "Utilization"),
    ("ER Visits per 1,000", "DIVIDE([ER Visits] * 12000, [Member Months])", "#,0.0", "Utilization"),
    ("Cost per ER Visit", "DIVIDE(CALCULATE([Medical Paid], 'Medical Claims'[Setting] = \"Emergency\"), [ER Visits])", DOLLARS, "Unit cost"),
    ("Claims", "COUNTROWS('Medical Claims')", COUNT, "Utilization"),
    ("Denial Rate", "DIVIDE(CALCULATE(COUNTROWS('Medical Claims'), 'Medical Claims'[Status] = \"Denied\"), [Claims])", RATE, "Operations"),
    ("Modifier 25 Rate", "DIVIDE(CALCULATE([Claims], 'Medical Claims'[Modifier] = \"25\"), [Claims])", RATE, "Payment integrity"),
    ("Modifier 59 Rate", "DIVIDE(CALCULATE([Claims], 'Medical Claims'[Modifier] = \"59\"), [Claims])", RATE, "Payment integrity"),
    ("Scripts", "SUM('Pharmacy Claims'[Scripts])", COUNT, "Pharmacy"),
    ("Scripts per 1,000", "DIVIDE([Scripts] * 12000, [Member Months])", COUNT, "Pharmacy"),
    ("Cost per Script", "DIVIDE([Pharmacy Paid], [Scripts])", DOLLARS, "Pharmacy"),
    ("GLP-1 Members", "CALCULATE(DISTINCTCOUNT('Pharmacy Claims'[Member ID]), 'Pharmacy Claims'[Drug Type] = \"GLP-1 Agonist\")", COUNT, "Pharmacy"),
    ("Specialty Share of Rx", "DIVIDE(CALCULATE([Pharmacy Paid], 'Pharmacy Claims'[Specialty] = \"Specialty\"), [Pharmacy Paid])", RATE, "Pharmacy"),
    ("Total PMPM PY", "CALCULATE([Total PMPM], SAMEPERIODLASTYEAR('Calendar'[Date]))", DOLLARS, "Trend"),
    ("Total PMPM YoY %", "DIVIDE([Total PMPM], [Total PMPM PY]) - 1", RATE, "Trend"),
    ("Medical Loss Ratio PY", "CALCULATE([Medical Loss Ratio], SAMEPERIODLASTYEAR('Calendar'[Date]))", RATE, "Trend"),
    ("Rx PMPM PY", "CALCULATE([Rx PMPM], SAMEPERIODLASTYEAR('Calendar'[Date]))", DOLLARS, "Trend"),
    ("Rx PMPM YoY %", "DIVIDE([Rx PMPM], [Rx PMPM PY]) - 1", RATE, "Trend"),
    ("Total Paid YTD", "TOTALYTD([Total Paid], 'Calendar'[Date])", DOLLARS, "Trend"),
]


def q(name: str) -> str:
    return f"'{name}'" if not name.isidentifier() else name


def write_model(out: Path) -> Path:
    d = out / f"{MODEL}.SemanticModel" / "definition"
    shutil.rmtree(d.parent, ignore_errors=True)
    (d / "tables").mkdir(parents=True)
    (d.parent / "definition.pbism").write_text('{"version": "4.0", "settings": {}}\n')
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1601\n")
    tables = [*COLUMNS, "Medical Economics"]
    (d / "model.tmdl").write_text("model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\n"
                                  + "".join(f"ref table {q(t)}\n" for t in tables))
    (d / "relationships.tmdl").write_text("".join(
        f"relationship r{i}\n\tfromColumn: {q(a)}.{q(b)}\n\ttoColumn: {q(c)}.{q(e)}\n\n"
        for i, (a, b, c, e) in enumerate(RELATIONSHIPS, 1)))
    for t, cols in COLUMNS.items():
        body = [f"table {q(t)}\n"]
        for c, typ in cols:
            body.append(f"\tcolumn {q(c)}\n\t\tdataType: {typ}\n\t\tsourceColumn: {c}\n"
                        + ("\t\tsummarizeBy: none\n" if typ == "string" else "") + "\n")
        wh = t.lower().replace(" ", "_")
        body.append(f"\tpartition {q(t)} = m\n\t\tmode: import\n\t\tsource =\n\t\t\tlet\n"
                    f"\t\t\t    Source = Databricks.Catalogs(\"adb-plains.azuredatabricks.net\", \"/sql/1.0/warehouses/plains\", []),\n"
                    f"\t\t\t    Catalog = Source{{[Name=\"{CATALOG}\",Kind=\"Database\"]}}[Data],\n"
                    f"\t\t\t    Schema = Catalog{{[Name=\"{SCHEMA}\",Kind=\"Schema\"]}}[Data],\n"
                    f"\t\t\t    Data = Schema{{[Name=\"{wh}\",Kind=\"Table\"]}}[Data]\n\t\t\tin\n\t\t\t    Data\n\n")
        (d / "tables" / f"{t}.tmdl").write_text("".join(body))
    body = ["table 'Medical Economics'\n\n"]
    for name, dax, fmt, folder in MEASURES:
        body.append(f"\tmeasure {q(name)} = {dax}\n\t\tformatString: {fmt}\n\t\tdisplayFolder: {folder}\n\n")
    body.append("\tcolumn Value\n\t\tdataType: string\n\t\tisHidden\n\t\tsourceColumn: Value\n\n"
                "\tpartition 'Medical Economics' = calculated\n\t\tmode: import\n\t\tsource = {\"\"}\n")
    (d / "tables" / "Medical Economics.tmdl").write_text("".join(body))
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default=str(ROOT.parent / "tigerclaw" / "data" / "demo"),
                    help="the folder holding ibnr_health.db and mee_synthetic.db")
    ap.add_argument("--out", default=f"build/pbi/{MODEL}")
    a = ap.parse_args()
    src, out = Path(a.source), Path(a.out)
    rng = np.random.default_rng(11)
    out.mkdir(parents=True, exist_ok=True)

    m = members(src, rng)
    mm = member_months(m, rng)
    med = medical(src, m, mm, rng)
    rx = pharmacy(src, m, mm, rng)
    # premiums: 2024 at an 84% MLR for each line of business, renewed each January at +5.5%
    lob = m.set_index("member_id").plan_type
    paid = pd.concat([f.assign(lob=f["Member ID"].map(lob))[["lob", "Paid"]] for f in (med, rx)])
    yr = pd.concat([med["Service Date"], rx["Fill Date"]]).dt.year.to_numpy()
    cost = paid[yr == 2024].groupby("lob").Paid.sum() / mm[mm.k < 12].lob.value_counts()
    mm["Premium"] = (mm.lob.map(cost / MLR_2024) * 1.055 ** (mm.k // 12) * rng.normal(1, 0.03, len(mm))).round(2)
    member = pd.DataFrame({
        "Member ID": m.member_id, "Gender": m.gender.map({"F": "Female", "M": "Male"}).fillna("Unknown"),
        "Age": m.age, "Age Band": age_band(m.age), "State": m.state, "County": m.county,
        "Line of Business": m.plan_type.map(LOB), "Plan": m.Plan, "Funding": m.Funding,
        "Primary Condition": m["Primary Condition"], "Chronic Conditions": m["Chronic Conditions"],
        "Risk Band": risk_band(m.risk_score)})
    frames = {"Member": member, "Member Months": mm[[c for c, _ in COLUMNS["Member Months"]]],
              "Medical Claims": med, "Pharmacy Claims": rx, "Provider": providers(src),
              "Calendar": pd.DataFrame({"Date": pd.date_range(START, END, freq="D")})}

    from insurance_context.bilayer import BILayer, build_spec, save
    from insurance_context.pbimodel import read_tmdl
    model = read_tmdl(write_model(out))
    model.name = MODEL
    spec = build_spec(model, CATALOG, SCHEMA)
    save(spec, out)
    layer = BILayer(spec)

    db = out / f"{CATALOG}.duckdb"
    db.unlink(missing_ok=True)
    con = duckdb.connect(str(db))
    con.execute(f"CREATE SCHEMA {SCHEMA}")
    for name, frame in frames.items():
        frame = frame[[c for c, _ in COLUMNS[name]]]
        con.register("frame", frame)
        con.execute(f'CREATE TABLE {layer.tables[name]["table"].split(".", 1)[1]} AS SELECT * FROM frame')
        con.unregister("frame")
        print(f"  {name:<16} {len(frame):>9,} rows")
    con.close()
    (out / f"{CATALOG}.duckdb.about").write_text("generated demo data: synthetic members and claims, Jan 2024 to Sep 2026, "
                                                 "with planted stories (scripts/health_demo.py)\n")
    status = pd.Series([v["status"] for v in spec["metrics"].values() if not v.get("hidden")]).value_counts().to_dict()
    print(f"{MODEL}: {len(MEASURES)} measures, {status}")
    for k, v in spec["metrics"].items():
        if not v.get("hidden") and v["status"] != "captured":
            print(f"  not captured: {k}: {v.get('why')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
