"""Deterministic synthetic warehouse for the Summit Specialty Group reference client.

A fictional decentralized specialty carrier with three units on different systems:

  ses   Summit Excess & Surplus  modern policy/claims platform, transactional
  hpg   Harbor Program Group     program business reported ONLY via monthly
                                 bordereaux (claims as cumulative snapshots)
  corp  Group corporate          org, legal entities, broker master, cat events,
                                 reinsurance treaties, actuarial IBNR selections

Industry dynamics are built in so the metrics and the drift detector have real
signal: casualty severity trends up ~9%/yr with a rising litigated share (social
inflation), rare nuclear verdicts pierce umbrella/excess layers, and several
things the ontology does not model yet arrive in the data:
  * ses.cc_claim.LITIGATION_FUNDING_IND   third-party litigation funding disclosure
  * hpg.BDX_PREMIUM.TRIA_PREM             terrorism (TRIA) premium split
  * hpg.BDX_CLAIMS.CAUSE_CD = 'PFAS'       emerging PFAS contamination claims
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta
from pathlib import Path

import duckdb

VALUATION = date(2026, 6, 30)
FIRST_EFF = date(2022, 1, 1)

STATES = ["TX", "FL", "CA", "NY", "IL", "GA", "LA", "PA", "OH", "AZ", "NJ", "CO"]
BIZ = [
    ("238220", "Plumbing & HVAC contractor"), ("238160", "Roofing contractor"),
    ("236220", "Commercial general contractor"), ("531110", "Apartment building owner"),
    ("722511", "Full-service restaurant"), ("484121", "Long-haul trucking"),
    ("562112", "Hazardous waste collection"), ("325998", "Specialty chemical manufacturer"),
    ("541330", "Engineering services"), ("621111", "Physician practice"),
    ("713940", "Fitness center"), ("493110", "General warehousing"),
    ("332710", "Machine shop"), ("561612", "Security guard services"),
]
NAMES_A = ["Apex", "Bluewater", "Cedar", "Delta", "Evergreen", "Frontier", "Granite", "Harbor",
           "Ironwood", "Juniper", "Keystone", "Lakeside", "Meridian", "Northgate", "Oakridge",
           "Pinnacle", "Quarry", "Redstone", "Summit Ridge", "Trident", "Union", "Vantage",
           "Westfield", "Yellowstone"]
NAMES_B = ["Builders", "Holdings", "Logistics", "Industries", "Partners", "Services", "Group",
           "Enterprises", "Contracting", "Properties", "Manufacturing", "Solutions"]
PEOPLE_F = ["Maria", "James", "Aisha", "Chen", "Priya", "Robert", "Lucia", "Omar", "Grace", "Diego"]
PEOPLE_L = ["Garcia", "Smith", "Khan", "Wong", "Patel", "Johnson", "Rossi", "Haddad", "Kim", "Lopez"]

# (ses code, ontology line, share of SES book, base rate per $1k revenue,
#  target loss & ALAE ratio at 2022 rate adequacy, severity median)
SES_LINES = {
    "GL":   ("general_liability", 0.38, 4.2, 0.50, 14_000),
    "UMB":  ("umbrella_excess", 0.14, 1.1, 0.44, 350_000),
    "XS":   ("excess_casualty", 0.08, 0.7, 0.40, 700_000),
    "PROP": ("commercial_property", 0.22, 0.0, 0.44, 28_000),
    "PL":   ("professional_liability", 0.10, 2.1, 0.50, 70_000),
    "ENV":  ("environmental", 0.08, 1.5, 0.40, 120_000),
}
RATE_TREND = 1.08          # hard-market written rate change per year
CASUALTY_SEV_TREND = 1.09  # plus a rising litigated share -> social inflation outpaces rate
CAUSES = {
    "general_liability": ["BI", "PD", "PROD"],
    "umbrella_excess": ["BI", "PROD"],
    "excess_casualty": ["BI", "PROD"],
    "commercial_property": ["FIRE", "WIND", "WATER", "THEFT", "HURR", "SCS"],
    "professional_liability": ["E&O"],
    "environmental": ["POLL"],
}


def _insert(con, table: str, rows: list[tuple]) -> None:
    """Bulk load through a temp CSV; orders of magnitude faster than executemany."""
    if not rows:
        return
    import csv
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            for r in rows:
                w.writerow(["" if v is None else v.isoformat() if isinstance(v, date) else v for v in r])
        con.execute(f"COPY {table} FROM '{tmp}' (HEADER false, NULLSTR '')")
    finally:
        os.unlink(tmp)


def _money(x: float) -> float:
    return round(x, 2)


def _days(a: date, b: date) -> int:
    return (b - a).days


def build(path: Path, seed: int = 11) -> Path:
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    for s in ("corp", "ses", "hpg"):
        con.execute(f"CREATE SCHEMA {s}")

    brokers = _corp(con, rng)
    cats = [r[0] for r in con.execute("SELECT CAT_CD FROM corp.CAT_EVENT").fetchall()]
    ses_stats = _ses(con, rng, brokers, cats)
    hpg_stats = _hpg(con, rng, brokers, cats)
    _ibnr(con, rng, ses_stats, hpg_stats)
    con.close()
    return path


# ---------------------------------------------------------------- corporate

def _corp(con, rng):
    con.execute("CREATE TABLE corp.ORG_UNIT (UNIT_CD VARCHAR, UNIT_NM VARCHAR, PARENT_CD VARCHAR, SEGMENT VARCHAR)")
    _insert(con, "corp.ORG_UNIT", [
        ("SSG", "Summit Specialty Group", None, "CORPORATE"),
        ("SES", "Summit Excess & Surplus", "SSG", "INSURANCE"),
        ("HPG", "Harbor Program Group", "SSG", "INSURANCE"),
    ])
    con.execute("CREATE TABLE corp.LEGAL_ENTITY (CO_CD VARCHAR, CO_NM VARCHAR, NAIC_CD VARCHAR, DOM_ST VARCHAR, ADMITTED VARCHAR)")
    _insert(con, "corp.LEGAL_ENTITY", [
        ("SSIC", "Summit Specialty Insurance Company", "91234", "DE", "N"),
        ("SNIC", "Summit National Insurance Company", "91235", "IA", "Y"),
    ])
    con.execute("CREATE TABLE corp.BROKER_MASTER (GRP_PRODUCER_ID VARCHAR, BROKER_NM VARCHAR, BROKER_TYPE VARCHAR, HQ_ST VARCHAR)")
    brokers = []
    wholesale = ["Atlas Wholesale", "Crestline E&S Brokers", "Keel Specialty", "Northstar Excess",
                 "Pioneer Wholesale", "Ridgeway Surplus"]
    pas = ["Contractors Risk Programs LLC", "HabPro Underwriting", "Roadway Program Managers"]
    for i, n in enumerate(wholesale):
        brokers.append((f"B{i + 1:03d}", n, "WHS", rng.choice(STATES)))
    for i, n in enumerate(pas):
        brokers.append((f"B{i + 101:03d}", n, "PA", rng.choice(STATES)))
    _insert(con, "corp.BROKER_MASTER", brokers)

    con.execute("CREATE TABLE corp.CAT_EVENT (CAT_CD VARCHAR, CAT_NM VARCHAR, PERIL VARCHAR, START_DT DATE, END_DT DATE, STATES VARCHAR)")
    _insert(con, "corp.CAT_EVENT", [
        ("CAT-22-07", "2022 Gulf Coast Hurricane", "HU", date(2022, 9, 27), date(2022, 10, 2), "FL,GA"),
        ("CAT-23-11", "2023 Plains Hail Outbreak", "SCS", date(2023, 6, 13), date(2023, 6, 18), "TX,CO,IL"),
        ("CAT-24-05", "2024 Southeast Hurricane", "HU", date(2024, 9, 26), date(2024, 10, 1), "FL,GA,LA"),
        ("CAT-25-01", "2025 Southern California Wildfire", "WF", date(2025, 1, 7), date(2025, 1, 31), "CA"),
        ("CAT-25-09", "2025 Midwest Derecho", "SCS", date(2025, 5, 16), date(2025, 5, 17), "IL,OH"),
    ])

    con.execute("""CREATE TABLE corp.RI_TREATY (TREATY_ID VARCHAR, TREATY_NM VARCHAR, TREATY_TYP VARCHAR,
        UNIT_CD VARCHAR, INCEPT DATE, EXPIRY DATE, RETENTION DECIMAL(14,2), LMT DECIMAL(14,2),
        CEDE_PCT DECIMAL(5,4), LOBS VARCHAR, PANEL VARCHAR)""")
    rows = []
    for yr in range(2022, 2027):
        rows += [
            (f"T-{yr}-CAS", f"{yr} Casualty Excess of Loss", "PER_OCC_XOL", "SSG", date(yr, 1, 1), date(yr, 12, 31),
             2_000_000, 18_000_000, None, "general_liability,umbrella_excess,excess_casualty,environmental",
             "Reinsurer A;Reinsurer B;Reinsurer C"),
            (f"T-{yr}-CAT", f"{yr} Property Catastrophe XOL", "CAT_XOL", "SSG", date(yr, 6, 1), date(yr + 1, 5, 31),
             15_000_000, 85_000_000, None, "commercial_property", "Reinsurer B;Reinsurer D"),
            (f"T-{yr}-HPGQS", f"{yr} Harbor Programs Quota Share", "QS", "HPG", date(yr, 1, 1), date(yr, 12, 31),
             None, None, 0.40, "general_liability,commercial_property,commercial_auto", "Reinsurer E"),
        ]
    _insert(con, "corp.RI_TREATY", rows)
    return brokers


# ----------------------------------------------------------------------- SES

def _ses(con, rng, brokers, cats):
    whs = [b[0] for b in brokers if b[2] == "WHS"]
    con.execute("""CREATE TABLE ses.ab_account (ACCT_ID VARCHAR, LEGAL_NM VARCHAR, DBA VARCHAR, FEIN VARCHAR,
        NAICS VARCHAR, YRS_BUS INTEGER, REVENUE DECIMAL(16,2), EMP_CT INTEGER, OPS_DESC VARCHAR, MAIL_ST VARCHAR)""")
    con.execute("""CREATE TABLE ses.uw_submission (SUB_ID VARCHAR, ACCT_ID VARCHAR, BRKR_GRP_ID VARCHAR,
        RCVD_DT DATE, REQ_EFF_DT DATE, LOBS VARCHAR, SUB_STS VARCHAR, CLEARANCE VARCHAR, APPETITE VARCHAR,
        DECL_RSN VARCHAR, TIV DECIMAL(16,2), TGT_PREM DECIMAL(14,2), PRIOR_CARRIER VARCHAR, RENEWAL_OF_POL VARCHAR)""")
    con.execute("""CREATE TABLE ses.uw_quote (QUOTE_ID VARCHAR, SUB_ID VARCHAR, QUOTE_DT DATE, LOB_CD VARCHAR,
        QUOTE_PREM DECIMAL(14,2), QUOTE_STS VARCHAR)""")
    con.execute("""CREATE TABLE ses.uw_decision (DEC_ID VARCHAR, SUB_ID VARCHAR, DEC_TYP VARCHAR, OUTCOME VARCHAR,
        RULE_ID VARCHAR, PACK_VER VARCHAR, AUTH_LVL INTEGER, DEC_ROLE VARCHAR, DEC_DT DATE, RSN_TXT VARCHAR)""")
    con.execute("""CREATE TABLE ses.uw_loss_run (LR_ID VARCHAR, ACCT_ID VARCHAR, SUB_ID VARCHAR, POL_YR INTEGER,
        CARRIER VARCHAR, CLM_CT INTEGER, PAID DECIMAL(14,2), RESERVED DECIMAL(14,2), VAL_DT DATE)""")
    con.execute("""CREATE TABLE ses.pc_policy (POL_ID VARCHAR, POL_NBR VARCHAR, ACCT_ID VARCHAR, BRKR_GRP_ID VARCHAR,
        WRT_CO VARCHAR, EFF_DT DATE, EXP_DT DATE, POL_STS VARCHAR, POL_FORM VARCHAR, RETRO_DT DATE, NEW_REN VARCHAR,
        PRIOR_POL_ID VARCHAR, AUDIT_FREQ VARCHAR, CXL_RSN VARCHAR, SL_TAX_ST VARCHAR, TOT_PREM DECIMAL(14,2))""")
    con.execute("""CREATE TABLE ses.pc_policy_line (LINE_ID VARCHAR, POL_ID VARCHAR, LOB_CD VARCHAR, CLASS_CD VARCHAR,
        EXPO_BASIS VARCHAR, EXPO_AMT DECIMAL(16,2), RATE DECIMAL(10,4), OCC_LMT DECIMAL(14,2), AGG_LMT DECIMAL(14,2),
        DED DECIMAL(12,2), SIR DECIMAL(12,2), ATTACH_PT DECIMAL(14,2), LINE_PREM DECIMAL(14,2), RATE_ST VARCHAR)""")
    con.execute("""CREATE TABLE ses.pc_transaction (TXN_ID VARCHAR, POL_ID VARCHAR, LINE_ID VARCHAR, TXN_TYP VARCHAR,
        TXN_EFF_DT DATE, BOOK_DT DATE, PREM_CHG DECIMAL(14,2))""")
    con.execute("""CREATE TABLE ses.pc_location (LOC_ID VARCHAR, ACCT_ID VARCHAR, POL_ID VARCHAR, LOC_NBR INTEGER,
        ST VARCHAR, ZIP VARCHAR, CONSTR_CD VARCHAR, OCC_DESC VARCHAR, YR_BLT INTEGER, SPRK VARCHAR, PPC VARCHAR,
        BLDG_VAL DECIMAL(16,2), CNTS_VAL DECIMAL(16,2), BI_VAL DECIMAL(16,2), CAT_ZONES VARCHAR)""")
    con.execute("""CREATE TABLE ses.cc_claim (CLM_ID VARCHAR, CLM_NBR VARCHAR, POL_ID VARCHAR, LOB_CD VARCHAR,
        LOSS_DT DATE, RPT_DT DATE, LOSS_DESC VARCHAR, CAUSE_CD VARCHAR, LOSS_ST VARCHAR, CAT_CD VARCHAR,
        IN_SUIT VARCHAR, COV_POS VARCHAR, ADJ_NM VARCHAR, CLM_STS VARCHAR, CLOSE_DT DATE, LITIGATION_FUNDING_IND VARCHAR)""")
    con.execute("""CREATE TABLE ses.cc_claimant (CLMT_ID VARCHAR, CLM_ID VARCHAR, CLMT_NM VARCHAR, CLMT_TYP VARCHAR,
        ATTY_IND VARCHAR)""")
    con.execute("""CREATE TABLE ses.cc_exposure (EXP_ID VARCHAR, CLM_ID VARCHAR, CLMT_ID VARCHAR, LOB_CD VARCHAR,
        EXP_TYP VARCHAR, EXP_STS VARCHAR)""")
    con.execute("""CREATE TABLE ses.cc_transaction (TXN_ID VARCHAR, CLM_ID VARCHAR, EXP_ID VARCHAR, TXN_DT DATE,
        TXN_TYP VARCHAR, COST_TYP VARCHAR, RECOV_TYP VARCHAR, AMT DECIMAL(14,2))""")

    accts, subs, quotes, decs, lruns = [], [], [], [], []
    pols, lines, txns, locs = [], [], [], []
    claims, claimants, exps, ctx = [], [], [], []
    earned_by_seg: dict[tuple[str, int], float] = {}

    n_acct = 3200
    for a in range(n_acct):
        naics, ops = rng.choice(BIZ)
        rev = _money(math.exp(rng.gauss(math.log(12_000_000), 1.0)))
        accts.append((f"A{a:05d}", f"{rng.choice(NAMES_A)} {rng.choice(NAMES_B)} {a}", None,
                      f"{rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}", naics, rng.randint(1, 60), rev,
                      max(3, int(rev / rng.uniform(120_000, 300_000))), ops, rng.choice(STATES)))
    _insert(con, "ses.ab_account", accts)

    sub_n = pol_n = line_n = txn_n = loc_n = clm_n = ctx_n = dec_n = 0
    last_policy: dict[str, str] = {}

    def new_submission(acct, eff, renewal_of=None):
        nonlocal sub_n, dec_n
        sub_n += 1
        sid = f"S{sub_n:06d}"
        rcvd = eff - timedelta(days=rng.randint(20, 75))
        wanted = ["GL"] + [c for c in ("UMB", "PROP", "PL", "ENV", "XS") if rng.random() < SES_LINES[c][1] * 1.6]
        naics = acct[4]
        clearance = "CLEAR" if renewal_of or rng.random() > 0.07 else rng.choice(["BOR_BLOCK", "DUP"])
        hazardous = naics in ("562112", "325998", "238160")
        appetite = ("DECLINE" if (rng.random() < (0.30 if hazardous else 0.12)) else
                    "REFER" if rng.random() < 0.25 else "ACCEPT")
        if renewal_of:
            appetite = "ACCEPT" if rng.random() > 0.1 else "REFER"
        decl = None
        if clearance != "CLEAR":
            sts = "DECLINED"
            decl = "Blocked by clearance - broker of record"
        elif appetite == "DECLINE":
            sts = "DECLINED"
            decl = rng.choice(["Class outside appetite", "Loss history exceeds guideline",
                               "Inadequate underlying limits", "Wildfire exposure outside appetite"])
        else:
            quoted = rng.random() < (0.9 if renewal_of else 0.72)
            if not quoted:
                sts = "DECLINED"
                decl = "Pricing adequacy not achievable"
            else:
                bound = rng.random() < (0.78 if renewal_of else 0.36)
                sts = "BOUND" if bound else "NTU"
        subs.append((sid, acct[0], rng.choice(whs), rcvd, eff, ",".join(wanted), sts, clearance, appetite, decl,
                     _money(float(acct[6]) * rng.uniform(0.2, 0.6)) if "PROP" in wanted else None,
                     _money(float(acct[6]) * 0.004 * rng.uniform(0.7, 1.3)), rng.choice(
                         ["Incumbent Mutual", "Coastal Specialty", "Granite E&S", "Unknown"]),
                     renewal_of))
        for dtyp, outcome, rule in (("CLEARANCE", "ACCEPT" if clearance == "CLEAR" else "DECLINE", "CLR-001"),
                                    ("APPETITE", appetite, f"APP-{naics[:3]}")):
            dec_n += 1
            decs.append((f"D{dec_n:07d}", sid, dtyp, outcome, rule, f"ses-appetite-{2022 + (eff.year - 2022)}.{1 + eff.month // 7}",
                         1 if outcome != "REFER" else rng.choice([2, 3]), "underwriter" if outcome != "REFER" else "senior_underwriter",
                         rcvd + timedelta(days=rng.randint(0, 6)), decl if outcome == "DECLINE" else None))
            if clearance != "CLEAR":
                break
        if not renewal_of:
            for k in range(5):
                lruns.append((f"LR{sub_n:06d}{k}", acct[0], sid, eff.year - 5 + k, "Incumbent Mutual",
                              rng.randint(0, 4), _money(rng.uniform(0, 250_000)), _money(rng.uniform(0, 80_000)),
                              rcvd - timedelta(days=30)))
        return sid, sts, wanted

    def quote_and_bind(sid, sts, wanted, acct, eff, renewal_of=None):
        nonlocal pol_n, line_n, txn_n, loc_n
        if sts not in ("BOUND", "NTU"):
            return None
        for lob in wanted:
            quotes.append((f"Q{sid[1:]}{lob}", sid, eff - timedelta(days=rng.randint(3, 15)), lob,
                           None, "ACCEPTED" if sts == "BOUND" else "NTU"))
        if sts != "BOUND":
            return None
        pol_n += 1
        pid = f"P{pol_n:06d}"
        exp = date(eff.year + 1, eff.month, min(eff.day, 28))
        rev = float(acct[6])
        trend = RATE_TREND ** (eff.year - 2022)
        total = 0.0
        pline_ids = []
        for lob in wanted:
            line_n += 1
            lid = f"L{line_n:07d}"
            base = SES_LINES[lob]
            if lob == "PROP":
                tiv = rev * rng.uniform(0.25, 0.6)
                prem = tiv * rng.uniform(0.0025, 0.0060) * trend
                expo_basis, expo_amt, rate = "TIV", tiv, prem / tiv * 100
                occ, agg, ded, sir, att = min(tiv, 50_000_000), None, rng.choice([10_000, 25_000, 50_000]), None, None
            else:
                prem = rev / 1000 * base[2] * rng.uniform(0.7, 1.4) * trend
                expo_basis, expo_amt, rate = "SALES", rev, prem / (rev / 1000)
                occ = {"GL": 1_000_000, "UMB": 5_000_000, "XS": 10_000_000, "PL": 2_000_000, "ENV": 5_000_000}[lob]
                agg = occ * 2 if lob in ("GL", "PL", "ENV") else occ
                ded = None
                sir = rng.choice([None, 25_000, 50_000, 100_000]) if lob in ("GL", "PL", "ENV") else None
                att = {"UMB": 1_000_000, "XS": 5_000_000}.get(lob)
            prem = _money(max(prem, 2_500))
            total += prem
            state = acct[9]
            lines.append((lid, pid, lob, acct[4] if lob != "PROP" else "0311", expo_basis, _money(expo_amt),
                          round(rate, 4), occ, agg, ded, sir, att, prem, state))
            txn_n += 1
            txns.append((f"T{txn_n:07d}", pid, lid, "RN" if renewal_of else "NB", eff,
                         eff + timedelta(days=rng.randint(-5, 20)), prem))
            pline_ids.append((lid, lob, prem))
        cancelled = rng.random() < 0.05
        cxl_date = eff + timedelta(days=rng.randint(40, 300)) if cancelled else None
        status = ("CANCELLED" if cancelled and cxl_date <= VALUATION else
                  "IN_FORCE" if eff <= VALUATION < exp else "EXPIRED" if exp <= VALUATION else "PENDING")
        form = "CM" if any(l in ("PL", "ENV") for l in wanted) and rng.random() < 0.8 else "OCC"
        pols.append((pid, f"SES-{eff.year}-{pol_n:06d}", acct[0], subs[-1][2], "SSIC", eff, exp, status, form,
                      eff - timedelta(days=365 * rng.randint(1, 5)) if form == "CM" else None,
                      "R" if renewal_of else "N", renewal_of, "ANNUAL" if rng.random() < 0.5 else "NONE",
                      rng.choice(["Non-payment", "Insured request", "Underwriting"]) if cancelled else None,
                      acct[9], _money(total)))
        # endorsements, audits, cancellations
        for lid, lob, prem in pline_ids:
            if rng.random() < 0.25:
                d = eff + timedelta(days=rng.randint(30, 300))
                if d <= VALUATION:
                    txn_n += 1
                    txns.append((f"T{txn_n:07d}", pid, lid, "END", d, d + timedelta(days=rng.randint(0, 25)),
                                 _money(prem * rng.uniform(-0.05, 0.15))))
            if cancelled and cxl_date <= VALUATION:
                txn_n += 1
                txns.append((f"T{txn_n:07d}", pid, lid, "CXL", cxl_date, cxl_date + timedelta(days=5),
                             _money(-prem * _days(cxl_date, exp) / _days(eff, exp))))
            elif exp + timedelta(days=90) <= VALUATION and lob in ("GL",) and rng.random() < 0.5:
                txn_n += 1
                txns.append((f"T{txn_n:07d}", pid, lid, "AUD", exp, exp + timedelta(days=rng.randint(60, 120)),
                             _money(prem * rng.uniform(-0.08, 0.20))))
        if "PROP" in wanted:
            for k in range(rng.randint(1, 4)):
                loc_n += 1
                st = rng.choice(STATES)
                zones = []
                if st in ("FL", "LA", "TX") and rng.random() < 0.6:
                    zones.append("NS1")
                if st == "CA" and rng.random() < 0.5:
                    zones.append(rng.choice(["EQ", "WF"]))
                if st in ("TX", "CO", "IL", "OH") and rng.random() < 0.5:
                    zones.append("SCS")
                bldg = rev * rng.uniform(0.05, 0.3)
                locs.append((f"LOC{loc_n:07d}", acct[0], pid, k + 1, st, f"{rng.randint(10000, 99999)}",
                             str(rng.randint(1, 6)), rng.choice(["Office", "Warehouse", "Retail", "Manufacturing",
                                                                   "Habitational"]),
                             rng.randint(1950, 2022), rng.choice("YN"), str(rng.randint(1, 10)),
                             _money(bldg), _money(bldg * 0.4), _money(bldg * 0.25), ",".join(zones) or None))
        return pid, eff, exp, pline_ids, cancelled, cxl_date

    bound = []
    for acct in accts:
        eff = FIRST_EFF + timedelta(days=rng.randint(0, 365 * 4 + 150))
        if eff > date(2026, 9, 30):
            continue
        sid, sts, wanted = new_submission(acct, eff)
        res = quote_and_bind(sid, sts, wanted, acct, eff)
        while res is not None:
            bound.append(res)
            pid, eff0, exp0, *_ = res
            last_policy[acct[0]] = pid
            if exp0 > date(2026, 9, 30) or res[4]:
                break
            sid, sts, wanted = new_submission(acct, exp0, renewal_of=pid)
            res = quote_and_bind(sid, sts, wanted, acct, exp0, renewal_of=pid)

    # ---- claims: frequency is back-solved so each line lands on its target
    # loss & ALAE ratio at 2022 rate adequacy; severity trend and litigation then
    # push later accident years above target (social inflation).
    adjusters = ["J. Alvarez", "K. Brennan", "S. Okoye", "M. Tanaka", "R. Feldman", "TPA - Crawford-style"]
    cal = random.Random(99)
    mean_cost = {lob: sum(sum(_ses_claim(cal, lob, 2022)[:2]) for _ in range(6000)) / 6000 for lob in SES_LINES}
    for pid, eff, exp, pline_ids, cancelled, cxl_date in bound:
        end = min(exp, cxl_date or exp, VALUATION)
        if end <= eff:
            continue
        exposure_years = _days(eff, end) / 365.0
        for lid, lob, prem in pline_ids:
            line_name, _, _, target_lr, _ = SES_LINES[lob]
            prem_at_2022_rates = prem / RATE_TREND ** (eff.year - 2022)
            n = _poisson(rng, target_lr * prem_at_2022_rates * exposure_years / mean_cost[lob])
            for _ in range(n):
                loss_dt = eff + timedelta(days=rng.randint(0, max(_days(eff, end) - 1, 0)))
                casualty = line_name != "commercial_property"
                lag = int(rng.expovariate(1 / (160 if casualty else 25)))
                rpt = loss_dt + timedelta(days=lag)
                if rpt > VALUATION:
                    continue  # IBNR: happened, not yet reported
                clm_n += 1
                cid = f"C{clm_n:06d}"
                cause = rng.choice(CAUSES[line_name])
                cat = None
                if cause in ("HURR", "SCS"):
                    cand = [c for c in cats if (c.startswith("CAT-2" + str(loss_dt.year)[3]))]
                    cat = rng.choice(cand) if cand and rng.random() < 0.7 else None
                ult, alae, in_suit, funded, cwp = _ses_claim(rng, lob, loss_dt.year)
                age = _days(rpt, VALUATION)
                p_closed = 1 - math.exp(-age / (900 if in_suit else (420 if casualty else 120)))
                closed = rng.random() < p_closed
                close_dt = rpt + timedelta(days=rng.randint(30, max(31, age))) if closed else None
                reopened = closed and rng.random() < 0.03
                sts = ("RO" if reopened else ("CWP" if cwp else "C") if closed else "O")
                claims.append((cid, f"SES-CL-{loss_dt.year}-{clm_n:06d}", pid, lob, loss_dt, rpt,
                               _loss_desc(rng, cause), cause, rng.choice(STATES), cat,
                               "Y" if in_suit else "N",
                               rng.choice(["ACC", "ACC", "ACC", "ROR"]) if not cwp else rng.choice(["DEN", "ACC"]),
                               rng.choice(adjusters), sts, close_dt if sts != "RO" else None,
                               "Y" if funded else ("N" if in_suit else None)))
                clmt = f"{cid}-1"
                claimants.append((clmt, cid, f"{rng.choice(PEOPLE_F)} {rng.choice(PEOPLE_L)}",
                                  "3P" if casualty else "1P", "Y" if in_suit else rng.choice("NNNY")))
                exp_id = f"{cid}-E1"
                exps.append((exp_id, cid, clmt, lob,
                             {"BI": "BI", "PD": "PD", "PROD": "BI", "E&O": "DEF", "POLL": "PD"}.get(cause, "FPP"),
                             "C" if closed and sts != "RO" else "O"))
                ctx_n = _claim_financials(rng, ctx, ctx_n, cid, exp_id, rpt, close_dt, ult, alae, closed and sts != "RO",
                                          subro=(not casualty and not cwp and rng.random() < 0.15))

    _insert(con, "ses.uw_submission", subs)
    _insert(con, "ses.uw_quote", quotes)
    _insert(con, "ses.uw_decision", decs)
    _insert(con, "ses.uw_loss_run", lruns)
    _insert(con, "ses.pc_policy", pols)
    _insert(con, "ses.pc_policy_line", lines)
    _insert(con, "ses.pc_transaction", txns)
    _insert(con, "ses.pc_location", locs)
    _insert(con, "ses.cc_claim", claims)
    _insert(con, "ses.cc_claimant", claimants)
    _insert(con, "ses.cc_exposure", exps)
    _insert(con, "ses.cc_transaction", ctx)
    # Quote premium = the line premium it became (or a lost quote's price)
    con.execute("""UPDATE ses.uw_quote q SET QUOTE_PREM = coalesce(
        (SELECT l.LINE_PREM FROM ses.pc_policy p JOIN ses.pc_policy_line l ON l.POL_ID = p.POL_ID
         JOIN ses.uw_submission s ON s.ACCT_ID = p.ACCT_ID AND s.REQ_EFF_DT = p.EFF_DT
         WHERE s.SUB_ID = q.SUB_ID AND l.LOB_CD = q.LOB_CD LIMIT 1),
        (SELECT s.TGT_PREM * 1.15 FROM ses.uw_submission s WHERE s.SUB_ID = q.SUB_ID))""")
    return {"lines": {v[0] for v in SES_LINES.values()}}


def _ses_claim(rng, lob, loss_year):
    """(ultimate loss, ALAE, in_suit, litigation_funded, closed_without_payment) for one SES claim."""
    line_name, _, _, _, sev = SES_LINES[lob]
    casualty = line_name != "commercial_property"
    yrs = loss_year - 2022
    trend = (CASUALTY_SEV_TREND if casualty else 1.04) ** yrs
    in_suit = rng.random() < ((0.12 + 0.035 * yrs) if casualty else 0.02)
    funded = in_suit and rng.random() < 0.18 + 0.04 * yrs
    ult = math.exp(rng.gauss(math.log(sev * trend), 0.95)) * (2.8 if in_suit else 1.0)
    if lob in ("UMB", "XS") and in_suit and rng.random() < 0.08:
        ult *= rng.uniform(4, 9)  # nuclear verdict
    limit = {"GL": 1_000_000, "UMB": 5_000_000, "XS": 10_000_000, "PL": 2_000_000,
             "ENV": 5_000_000}.get(lob, 25_000_000)
    ult = min(ult, limit)
    cwp = rng.random() < (0.25 if casualty else 0.12)
    if cwp:
        ult = 0.0
    alae = ult * rng.uniform(0.15, 0.35) + (rng.uniform(25_000, 120_000) if in_suit else rng.uniform(300, 4_000))
    return ult, alae, in_suit, funded, cwp


def _claim_financials(rng, out, n, cid, exp_id, rpt, close_dt, ult, alae, closed, subro=False):
    """Emit reserve/payment/recovery transactions that reconcile to the ultimate."""
    horizon = close_dt or VALUATION
    span = max(_days(rpt, horizon), 1)

    def add(d, typ, cost, amt, recov=None):
        nonlocal n
        n += 1
        out.append((f"CT{n:08d}", cid, exp_id, d, typ, cost, recov, _money(amt)))

    for cost, total in (("LOSS", ult), ("EXP", alae)):
        initial = total * rng.uniform(0.5, 1.2) if total > 0 else rng.uniform(5_000, 25_000)
        add(rpt, "RSV", cost, initial)
        reserve = initial
        paid_target = total if closed else total * rng.uniform(0.1, 0.7)
        k = rng.randint(1, 3) if paid_target > 0 else 0
        paid = 0.0
        for i in range(k):
            d = rpt + timedelta(days=int(span * (i + 1) / (k + 1)))
            amt = paid_target / k
            add(d, "PMT", cost, amt)
            take = min(amt, reserve)
            add(d, "RSV", cost, -take)
            reserve -= take
            paid += amt
        if closed:
            if abs(reserve) > 0.005:
                add(horizon, "RSV", cost, -reserve)
        else:
            target_os = max(total - paid, 0) * rng.uniform(0.85, 1.15)
            add(VALUATION - timedelta(days=rng.randint(0, 60)), "RSV", cost, target_os - reserve)
    if subro and closed:
        add(horizon + timedelta(days=rng.randint(30, 200)) if horizon + timedelta(days=200) <= VALUATION else horizon,
            "REC", "LOSS", ult * rng.uniform(0.1, 0.4), recov="SUBRO")
    return n


def _loss_desc(rng, cause):
    return {
        "BI": "Third party slipped and fell at insured premises; alleges back injury.",
        "PD": "Insured's crew damaged adjacent property during operations.",
        "PROD": "Claimant alleges injury from insured's product.",
        "FIRE": "Kitchen fire spread to storage area.",
        "WIND": "Roof damage from high winds.",
        "WATER": "Sprinkler line burst, water damage to inventory.",
        "THEFT": "Break-in, equipment stolen.",
        "HURR": "Hurricane wind and surge damage.",
        "SCS": "Hail damage to roof and HVAC units.",
        "E&O": "Client alleges negligent engineering design.",
        "POLL": "Release of contaminants during transport; cleanup costs.",
        "PFAS": "Groundwater contamination alleged from PFAS-containing materials.",
    }.get(cause, "Loss reported.")


def _poisson(rng, lam):
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


# ----------------------------------------------------------------------- HPG

def _hpg(con, rng, brokers, cats):
    con.execute("""CREATE TABLE hpg.PGM_MASTER (PGM_CD VARCHAR, PGM_NM VARCHAR, PA_ID VARCHAR, CLASS_FOCUS VARCHAR,
        AUTH VARCHAR, COMM_PCT DECIMAL(5,4), START_DT DATE, END_DT DATE)""")
    programs = [
        ("CRP", "Artisan Contractors Program", "B101", "Artisan & trade contractors under $5M revenue", "LTD", 0.22,
         "GL", ("238220", "238160", "236220"), 0.50, 9_500),
        ("HAB", "Habitational Property Program", "B102", "Garden-style apartments", "FULL", 0.18,
         "PROP", ("531110",), 0.46, 18_000),
        ("TRN", "Regional Trucking Program", "B103", "Intermediate-radius trucking fleets", "LTD", 0.15,
         "AUTO", ("484121",), 0.58, 24_000),
    ]
    _insert(con, "hpg.PGM_MASTER", [p[:6] + (date(2021, 1, 1), None) for p in programs])

    con.execute("""CREATE TABLE hpg.BDX_PREMIUM (BDX_MONTH DATE, PGM_CD VARCHAR, CERT_NO VARCHAR, INSURED_NM VARCHAR,
        INSURED_ST VARCHAR, NAICS VARCHAR, TRANS_TYPE VARCHAR, TRANS_EFF DATE, POL_EFF DATE, POL_EXP DATE,
        COVERAGE VARCHAR, LIMIT_OCC DECIMAL(14,2), LIMIT_AGG DECIMAL(14,2), DED DECIMAL(12,2),
        GROSS_PREM DECIMAL(14,2), TRIA_PREM DECIMAL(12,2), COMMISSION DECIMAL(12,2))""")
    con.execute("""CREATE TABLE hpg.BDX_CLAIMS (BDX_MONTH DATE, PGM_CD VARCHAR, CERT_NO VARCHAR, POL_EFF DATE,
        CLAIM_REF VARCHAR, LOSS_DT DATE, REPORTED_DT DATE, CLAIMANT VARCHAR, CAUSE_CD VARCHAR, LOSS_ST VARCHAR,
        STATUS VARCHAR, PAID_TD DECIMAL(14,2), OS_RESERVE DECIMAL(14,2), EXP_PAID_TD DECIMAL(14,2),
        EXP_OS DECIMAL(14,2), RECOVERY_TD DECIMAL(14,2), IN_LITIGATION VARCHAR, CAT_CD VARCHAR)""")

    prem_rows, clm_rows = [], []
    cert_n = clm_n = 0
    cal = random.Random(98)
    mean_cost = {}
    for p in programs:
        cov = p[6]
        draws = [_hpg_claim(cal, cov, 2022) for _ in range(6000)]
        mean_cost[cov] = sum(d[0] + d[1] for d in draws) / len(draws)
    for code, _, _, _, _, comm, cov, naics_set, target_lr, avg_prem in programs:
        for _ in range(650):
            cert_n += 1
            eff = FIRST_EFF + timedelta(days=rng.randint(0, 365 * 4 + 170))
            if eff > VALUATION:
                continue
            renewals = 0
            insured = f"{rng.choice(NAMES_A)} {rng.choice(NAMES_B)} H{cert_n}"
            st = rng.choice(STATES)
            naics = rng.choice(naics_set)
            while eff <= VALUATION:
                cert = f"{code}-{cert_n:05d}-{renewals}"
                exp = date(eff.year + 1, eff.month, min(eff.day, 28))
                prem = _money(avg_prem * rng.uniform(0.5, 1.8) * (1.07 ** (eff.year - 2022)))
                tria = _money(prem * 0.01) if cov != "AUTO" else 0.0
                lim = {"GL": (1_000_000, 2_000_000), "PROP": (5_000_000, None), "AUTO": (1_000_000, None)}[cov]
                bdx = date(eff.year, eff.month, 1)
                prem_rows.append((bdx, code, cert, insured, st, naics, "REN" if renewals else "NEW", eff, eff, exp,
                                  cov, lim[0], lim[1], rng.choice([1000, 2500, 5000]), prem, tria, _money(prem * comm)))
                if rng.random() < 0.15:
                    d = eff + timedelta(days=rng.randint(30, 300))
                    if d <= VALUATION:
                        chg = _money(prem * rng.uniform(-0.1, 0.12))
                        prem_rows.append((date(d.year, d.month, 1), code, cert, insured, st, naics, "MTA", d, eff, exp,
                                          cov, lim[0], lim[1], None, chg, 0.0, _money(chg * comm)))
                # claims for this term
                end = min(exp, VALUATION)
                ey = _days(eff, end) / 365.0
                prem_2022 = prem / (1.07 ** (eff.year - 2022))
                for _ in range(_poisson(rng, target_lr * prem_2022 * ey / mean_cost[cov])):
                    loss_dt = eff + timedelta(days=rng.randint(0, max(_days(eff, end) - 1, 0)))
                    rpt = loss_dt + timedelta(days=int(rng.expovariate(1 / (90 if cov != "PROP" else 20))))
                    if rpt > VALUATION:
                        continue
                    clm_n += 1
                    ult, alae, lit, cause = _hpg_claim(rng, cov, loss_dt.year)
                    age = _days(rpt, VALUATION)
                    close_after = int(rng.expovariate(1 / (700 if lit else 200)))
                    cat = None
                    if cause in ("SCS", "WIND", "HAIL"):
                        cand = [c for c in cats if c.startswith("CAT-2" + str(loss_dt.year)[3])]
                        cat = rng.choice(cand) if cand and rng.random() < 0.5 else None
                    claim_ref = f"HPG{code}{clm_n:05d}"
                    claimant = f"{rng.choice(PEOPLE_F)} {rng.choice(PEOPLE_L)}"
                    m = date(rpt.year, rpt.month, 1)
                    months = []
                    while m <= VALUATION:
                        months.append(m)
                        m = date(m.year + (m.month // 12), m.month % 12 + 1, 1)
                    n_m = len(months)
                    for i, mo in enumerate(months):
                        t = min(1.0, (i + 1) * 30 / max(close_after, 30))
                        closed = t >= 1.0
                        paid = ult * t ** 1.5
                        os_ = 0.0 if closed else max(ult * rng.uniform(0.9, 1.1) - paid, 0)
                        epaid = alae * t ** 1.2
                        eos = 0.0 if closed else max(alae - epaid, 0) * 1.1
                        rec = ult * 0.2 if closed and cause in ("WATER", "FIRE") and rng.random() < 0.3 else 0.0
                        clm_rows.append((mo, code, cert, eff, claim_ref, loss_dt, rpt, claimant, cause, st,
                                         "CLOSED" if closed else "OPEN", _money(paid), _money(os_), _money(epaid),
                                         _money(eos), _money(rec), "Y" if lit else "N", cat))
                        if closed:
                            break
                renewals += 1
                if rng.random() > 0.72:
                    break
                eff = exp
    _insert(con, "hpg.BDX_PREMIUM", prem_rows)
    _insert(con, "hpg.BDX_CLAIMS", clm_rows)
    return {}


def _hpg_claim(rng, cov, loss_year):
    """(ultimate loss, ALAE, litigated, cause code) for one program claim."""
    yrs = loss_year - 2022
    sev = {"GL": 12_000, "PROP": 16_000, "AUTO": 15_000}[cov]
    limit = {"GL": 1_000_000, "PROP": 5_000_000, "AUTO": 1_000_000}[cov]
    cause = {"GL": rng.choice(["BI", "BI", "PD", "PFAS" if loss_year >= 2024 and rng.random() < 0.08 else "PD"]),
             "PROP": rng.choice(["FIRE", "WATER", "WIND", "SCS", "HAIL"]),
             "AUTO": rng.choice(["COLL", "COLL", "BI"])}[cov]
    lit = cov != "PROP" and rng.random() < (0.1 + 0.03 * yrs)
    ult = math.exp(rng.gauss(math.log(sev * (1.08 ** yrs)), 1.0)) * (2.5 if lit else 1)
    if cause == "PFAS":
        ult *= 6
    ult = min(ult, limit)
    alae = ult * rng.uniform(0.08, 0.25) + (rng.uniform(20_000, 60_000) if lit else 0)
    return ult, alae, lit, cause


# ---------------------------------------------------------------- actuarial

def _ibnr(con, rng, *_):
    """Selected IBNR by unit x line x accident year at mid-year valuations.

    Longer-tailed lines and immature accident years carry more IBNR; casualty
    AYs 2023+ get a social-inflation load.
    """
    con.execute("""CREATE TABLE corp.ACT_IBNR (UNIT_CD VARCHAR, LOB VARCHAR, AY INTEGER, VAL_DT DATE,
        IBNR DECIMAL(16,2), METHOD VARCHAR, SELECTED VARCHAR)""")
    # Years until ~63% of ultimate is reported, by line.
    tau = {"general_liability": 1.1, "umbrella_excess": 2.4, "excess_casualty": 2.8, "commercial_property": 0.15,
           "professional_liability": 1.4, "environmental": 2.0, "commercial_auto": 0.7}
    elr = {"commercial_property": 0.46, "commercial_auto": 0.60}
    # Accident-year earned premium straight from source (pro rata, capped at valuation).
    ep_rows = con.execute(f"""
        WITH tx AS (
          SELECT 'SES' AS u,
                 CASE l.LOB_CD WHEN 'GL' THEN 'general_liability' WHEN 'UMB' THEN 'umbrella_excess'
                      WHEN 'XS' THEN 'excess_casualty' WHEN 'PROP' THEN 'commercial_property'
                      WHEN 'PL' THEN 'professional_liability' ELSE 'environmental' END AS lob,
                 t.PREM_CHG AS wp, t.TXN_EFF_DT AS s, p.EXP_DT AS e
          FROM ses.pc_transaction t JOIN ses.pc_policy p USING (POL_ID) JOIN ses.pc_policy_line l USING (LINE_ID)
          UNION ALL
          SELECT 'HPG', CASE COVERAGE WHEN 'GL' THEN 'general_liability' WHEN 'PROP' THEN 'commercial_property'
                 ELSE 'commercial_auto' END, GROSS_PREM, TRANS_EFF, POL_EXP
          FROM hpg.BDX_PREMIUM
        ), yrs AS (SELECT unnest(range(2022, 2027)) AS ay)
        SELECT u, lob, ay, sum(wp * greatest(0, datediff('day', greatest(s, make_date(ay, 1, 1)),
                                  least(e, make_date(ay + 1, 1, 1), DATE '{VALUATION}' + 1)))
                         / nullif(datediff('day', s, e), 0)) AS ep
        FROM tx JOIN yrs ON ay BETWEEN year(s) AND year(e)
        GROUP BY ALL""").fetchall()
    rows = []
    for unit, lob, ay, ep in ep_rows:
        if not ep:
            continue
        for val in (date(2025, 12, 31), VALUATION):
            if val.year < ay:
                continue
            # Average accident date of the exposure earned so far in the year.
            exposure_end = min(val, date(ay, 12, 31))
            mid = date(ay, 1, 1) + (exposure_end - date(ay, 1, 1)) / 2
            maturity = max((val - mid).days / 365.0, 0.05)
            earned_to_val = float(ep) if val >= VALUATION else float(ep) * min(1.0, (exposure_end - date(ay, 1, 1)).days / 365.0)
            # BF: expected loss ratio x earned premium x share not yet reported;
            # casualty ELR picks rise with social inflation.
            loss_ratio = elr.get(lob, 0.52 + 0.03 * max(ay - 2022, 0))
            ibnr = earned_to_val * loss_ratio * math.exp(-maturity / tau[lob])
            for method, sel, adj in (("BF", "Y", 1.0), ("CL", "N", rng.uniform(0.8, 1.3))):
                rows.append((unit, lob, ay, val, _money(ibnr * adj), method, sel))
    _insert(con, "corp.ACT_IBNR", rows)
