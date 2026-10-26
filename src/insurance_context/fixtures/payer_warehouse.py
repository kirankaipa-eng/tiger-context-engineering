"""Deterministic synthetic warehouses for the health payer reference clients.

`build` - Acme Health Plan (small, single unit). Legacy table/column names on
purpose — the mapping layer is what translates them. A few rows deliberately
drift from the ontology so the drift detector and validator have something real
to find:
  * CLM_HDR.CLM_STS contains 'SUSPENDED' (not in ClaimStatusEnum)
  * CLM_HDR.CLM_FORM contains 'D' (dental — outside Acme's claim_type restriction)
  * CLM_HDR.NSA_IND is a new column nobody has mapped yet (No Surprises Act flag)
  * UM_AUTH has expedited requests decided after the 72h time frame

`build_meridian` - Meridian Health Plan (deep, calibrated; see its docstring).
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from .pc_warehouse import _insert, _poisson

FIRST = ["Maria", "James", "Aisha", "Chen", "Priya", "Robert", "Lucia", "Omar", "Grace", "Diego",
         "Hannah", "Kwame", "Sofia", "Ethan", "Mei", "Samuel", "Fatima", "Noah", "Elena", "Raj"]
LAST = ["Garcia", "Smith", "Khan", "Wong", "Patel", "Johnson", "Rossi", "Haddad", "Kim", "Lopez",
        "Brown", "Mensah", "Novak", "Miller", "Lin", "Okafor", "Ali", "Davis", "Petrov", "Shah"]
STATES = ["OH", "PA", "MI", "IN"]
DX = ["E11.9", "I10", "J45.909", "M54.5", "F32.9", "Z00.00", "I50.9", "O80", "C50.911", "K21.9"]
PROCS = [("99213", 145), ("99214", 210), ("80053", 38), ("93000", 60), ("71046", 95),
         ("97110", 85), ("70553", 1650), ("27447", 21000), ("J9355", 4800), ("90837", 180)]
PA_PROCS = ["70553", "27447", "J9355", "97110", "E0601"]
QUEUES = ["UM-IMG", "UM-ORTHO", "UM-ONC", "UM-REHAB", "UM-DME"]
CARC = ["CO-45", "CO-97", "CO-197", "CO-50", "PR-1", "PR-2"]


def build(path: Path, seed: int = 7, members: int = 400, claims: int = 2500) -> Path:
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA acme_raw")
    con.execute("SET schema = 'acme_raw'")

    con.execute("""CREATE TABLE NTWK_REF (NTWK_ID VARCHAR, NTWK_DESC VARCHAR, NTWK_TYP VARCHAR)""")
    con.executemany("INSERT INTO NTWK_REF VALUES (?,?,?)", [
        ("N-PPO1", "Acme Choice PPO", "PPO"), ("N-HMO1", "Acme Local HMO", "HMO")])

    con.execute("""CREATE TABLE PLN_BNFT_PKG (PLN_CD VARCHAR, PLN_NM VARCHAR, LOB VARCHAR, PLN_TYP VARCHAR,
        MTL_LVL VARCHAR, NTWK_ID VARCHAR, IND_DED_AMT DECIMAL(10,2), IND_OOP_AMT DECIMAL(10,2),
        EFF_DT DATE, TRM_DT DATE)""")
    plans = [
        ("P-GLD-PPO", "Acme Gold PPO", "commercial_health", "PPO", "GOLD", "N-PPO1", 1500, 6000),
        ("P-SLV-HMO", "Acme Silver HMO", "commercial_health", "HMO", "SILVER", "N-HMO1", 3500, 8700),
        ("P-HDHP", "Acme HSA Bronze", "commercial_health", "HDHP", "BRONZE", "N-PPO1", 6000, 8050),
        ("P-MA-HMO", "Acme Medicare Advantage", "medicare_advantage", "HMO", None, "N-HMO1", 0, 5900),
    ]
    con.executemany("INSERT INTO PLN_BNFT_PKG VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [p + (date(2025, 1, 1), None) for p in plans])

    con.execute("""CREATE TABLE PRV_DIM (PRV_SK VARCHAR, PRV_NM VARCHAR, NPI_NBR VARCHAR, ENTY_TYP VARCHAR,
        TXNMY_CD VARCHAR, ST_CD VARCHAR)""")
    con.execute("""CREATE TABLE PRV_NTWK_XREF (XREF_ID VARCHAR, PRV_SK VARCHAR, NTWK_ID VARCHAR,
        EFF_DT DATE, TRM_DT DATE)""")
    specialties = [("Family Medicine", "207Q00000X"), ("Cardiology", "207RC0000X"),
                   ("Orthopedic Surgery", "207X00000X"), ("Oncology", "207RX0202X"),
                   ("Radiology", "2085R0202X"), ("Physical Therapy", "225100000X"),
                   ("Behavioral Health", "103T00000X")]
    providers, xref = [], []
    for i in range(60):
        spec, tax = specialties[i % len(specialties)]
        org = i % 5 == 0
        name = f"{rng.choice(LAST)} {spec} Group" if org else f"Dr. {rng.choice(FIRST)} {rng.choice(LAST)}"
        sk = f"PRV{i:04d}"
        providers.append((sk, name, f"1{rng.randint(100000000, 999999999)}", "2" if org else "1",
                          tax, rng.choice(STATES)))
        if rng.random() < 0.8:
            xref.append((f"X{i:04d}", sk, rng.choice(["N-PPO1", "N-HMO1"]), date(2024, 1, 1), None))
    con.executemany("INSERT INTO PRV_DIM VALUES (?,?,?,?,?,?)", providers)
    con.executemany("INSERT INTO PRV_NTWK_XREF VALUES (?,?,?,?,?)", xref)
    in_network = {x[1] for x in xref}

    con.execute("""CREATE TABLE MBR_MSTR (MBR_SK VARCHAR, SUBSCR_ID VARCHAR, FRST_NM VARCHAR, LST_NM VARCHAR,
        BRTH_DT DATE, GNDR_CD VARCHAR, SUBSCR_MBR_SK VARCHAR, REL_CD VARCHAR, ST_CD VARCHAR, ZIP5 VARCHAR,
        RISK_TIER VARCHAR, CM_PGM VARCHAR)""")
    con.execute("""CREATE TABLE ELIG_SPAN (ELIG_SK VARCHAR, MBR_SK VARCHAR, PLN_CD VARCHAR,
        ELIG_EFF_DT DATE, ELIG_TRM_DT DATE)""")
    mbrs, elig, member_plan = [], [], {}
    subscriber = None
    for i in range(members):
        sk = f"M{i:05d}"
        dependent = subscriber is not None and rng.random() < 0.35
        rel = rng.choice(["01", "19"]) if dependent else "18"
        if not dependent:
            subscriber = sk
        plan = member_plan.get(subscriber) or rng.choice(plans)[0]
        member_plan[sk] = plan
        age = rng.randint(66, 88) if plan == "P-MA-HMO" else rng.randint(1 if rel == "19" else 19, 64)
        tier = rng.choices(["low", "rising", "high", "catastrophic"], [60, 25, 12, 3])[0]
        pgm = rng.choice(["diabetes", "chf", "maternity", "behavioral_health"]) if tier in ("high", "catastrophic") else "none"
        mbrs.append((sk, f"ACM{100000 + i}", rng.choice(FIRST), rng.choice(LAST),
                     date(2025 - age, rng.randint(1, 12), rng.randint(1, 28)), rng.choice("FFMMU"),
                     subscriber, rel, rng.choice(STATES), f"{rng.randint(43000, 49999)}", tier, pgm))
        termed = rng.random() < 0.1
        elig.append((f"E{i:05d}", sk, plan, date(2025, 1, 1), date(2025, 6, 30) if termed else None))
    con.executemany("INSERT INTO MBR_MSTR VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", mbrs)
    con.executemany("INSERT INTO ELIG_SPAN VALUES (?,?,?,?,?)", elig)

    con.execute("""CREATE TABLE UM_AUTH (AUTH_ID VARCHAR, MBR_SK VARCHAR, REQ_PRV_SK VARCHAR, PROC_CD VARCHAR,
        DX_CD VARCHAR, URG_IND VARCHAR, RQST_DT DATE, DCSN_DT DATE, DCSN_RSN_TXT VARCHAR, AUTH_STS VARCHAR,
        APRV_UNITS INTEGER, WRK_QUEUE VARCHAR)""")
    auths = []
    for i in range(300):
        proc = rng.choice(PA_PROCS)
        urgent = rng.random() < 0.2
        rq = date(2025, 1, 1) + timedelta(days=rng.randint(0, 330))
        sts = rng.choices(["APPROVED", "DENIED", "PARTIALLY_APPROVED", "IN_REVIEW", "PENDED_FOR_INFO", "WITHDRAWN"],
                          [62, 14, 6, 8, 7, 3])[0]
        decided = sts in ("APPROVED", "DENIED", "PARTIALLY_APPROVED")
        # Expedited should be <=3 days; some late ones on purpose.
        tat = rng.choice([1, 2, 3, 3, 5]) if urgent else rng.choice([2, 4, 6, 7, 9, 12])
        reason = {"APPROVED": "Meets medical necessity criteria",
                  "DENIED": rng.choice(["Conservative therapy not documented", "Not medically necessary",
                                        "Out-of-network provider without exception"]),
                  "PARTIALLY_APPROVED": "Approved reduced number of visits"}.get(sts)
        auths.append((f"A{i:05d}", rng.choice(mbrs)[0], rng.choice(providers)[0], proc, rng.choice(DX),
                      "E" if urgent else "S", rq, rq + timedelta(days=tat) if decided else None, reason, sts,
                      rng.randint(1, 12) if sts in ("APPROVED", "PARTIALLY_APPROVED") else None,
                      QUEUES[PA_PROCS.index(proc)]))
    con.executemany("INSERT INTO UM_AUTH VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", auths)
    approved_auth = {(a[1], a[3]): a[0] for a in auths if a[9] == "APPROVED"}

    con.execute("""CREATE TABLE CLM_HDR (CLM_ID VARCHAR, MBR_SK VARCHAR, BILL_PRV_SK VARCHAR, REND_PRV_SK VARCHAR,
        CLM_FORM VARCHAR, CLM_STS VARCHAR, RCVD_DT DATE, SVC_FROM_DT DATE, SVC_TO_DT DATE, PRIM_DX VARCHAR,
        TOT_CHRG_AMT DECIMAL(12,2), TOT_ALLW_AMT DECIMAL(12,2), TOT_PD_AMT DECIMAL(12,2),
        MBR_RESP_AMT DECIMAL(12,2), PAR_IND VARCHAR, AUTH_ID VARCHAR, NSA_IND VARCHAR)""")
    con.execute("""CREATE TABLE CLM_LN (CLM_ID VARCHAR, LN_NBR INTEGER, PROC_CD VARCHAR, MOD1 VARCHAR,
        SVC_UNITS INTEGER, CHRG_AMT DECIMAL(12,2), ALLW_AMT DECIMAL(12,2), PD_AMT DECIMAL(12,2),
        LN_STS VARCHAR, CARC_CD VARCHAR)""")
    hdrs, lines = [], []
    for i in range(claims):
        m = rng.choice(mbrs)
        bill = rng.choice(providers)
        svc = date(2025, 1, 1) + timedelta(days=rng.randint(0, 320))
        form = rng.choices(["P", "I", "D"], [78, 20, 2])[0]
        sts = rng.choices(["PAID", "DENIED", "PENDED", "ADJUDICATED", "SUSPENDED"], [74, 11, 7, 6, 2])[0]
        par = bill[0] in in_network
        n_lines = rng.randint(1, 4)
        chg = alw = pd = 0.0
        auth_id = None
        for ln in range(1, n_lines + 1):
            code, fee = rng.choice(PROCS)
            units = 1 if fee > 500 else rng.randint(1, 3)
            c = round(fee * units * rng.uniform(1.4, 2.2), 2)
            a = round(fee * units * (1.0 if par else 0.7), 2)
            line_denied = sts == "DENIED" or rng.random() < 0.06
            p = 0.0 if line_denied or sts in ("PENDED", "SUSPENDED") else round(a * rng.uniform(0.7, 0.95), 2)
            carc = rng.choice(CARC) if line_denied else ("CO-45" if c > a else None)
            lines.append((f"C{i:06d}", ln, code, rng.choice([None, None, "25", "59"]), units, c,
                          0.0 if line_denied else a, p, "DENIED" if line_denied else "PAID", carc))
            chg, alw, pd = chg + c, alw + (0.0 if line_denied else a), pd + p
            auth_id = auth_id or approved_auth.get((m[0], code))
        hdrs.append((f"C{i:06d}", m[0], bill[0], rng.choice(providers)[0], form, sts,
                     svc + timedelta(days=rng.randint(2, 40)), svc, svc, rng.choice(DX),
                     round(chg, 2), round(alw, 2), round(pd, 2), round(max(alw - pd, 0), 2),
                     "Y" if par else "N", auth_id,
                     "Y" if (not par and form == "I" and rng.random() < 0.3) else "N"))
    con.executemany("INSERT INTO CLM_HDR VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", hdrs)
    con.executemany("INSERT INTO CLM_LN VALUES (?,?,?,?,?,?,?,?,?,?)", lines)

    con.execute("""CREATE TABLE ACCUM_BAL (ACCUM_ID VARCHAR, MBR_SK VARCHAR, PLN_CD VARCHAR, PLN_YR INTEGER,
        ACCUM_TYP VARCHAR, MET_AMT DECIMAL(10,2), LMT_AMT DECIMAL(10,2))""")
    plan_limits = {p[0]: (p[6], p[7]) for p in plans}
    liab = dict(con.execute("SELECT MBR_SK, sum(MBR_RESP_AMT) FROM CLM_HDR GROUP BY 1").fetchall())
    acc = []
    for m in mbrs:
        ded, oop = plan_limits[member_plan[m[0]]]
        spent = float(liab.get(m[0], 0) or 0)
        acc.append((f"AC{m[0]}D", m[0], member_plan[m[0]], 2025, "DED", min(spent, ded), ded))
        acc.append((f"AC{m[0]}O", m[0], member_plan[m[0]], 2025, "OOP", min(spent, oop), oop))
    con.executemany("INSERT INTO ACCUM_BAL VALUES (?,?,?,?,?,?,?)", acc)
    con.close()
    return path


# =============================================================================
# Meridian Health Plan: commercial + Medicare Advantage on two platforms
# =============================================================================

MERIDIAN_VALUATION = date(2026, 6, 30)
M_START = date(2024, 7, 1)        # the warehouse keeps a rolling 24-month window
M_STATES = ["MI", "OH", "IN", "WI"]
OPEN_END = date(9999, 12, 31)     # COMM's "no termination" sentinel


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def _month_end(d: date) -> date:
    return _add_months(d, 1) - timedelta(days=1)


M_MONTHS = [_add_months(M_START, i) for i in range(24)]
_EOD = datetime.combine(MERIDIAN_VALUATION, datetime.max.time())

# NUCC taxonomy codes (public) with relative share of practitioners.
_PROF_SPECIALTIES = [("207Q00000X", 30), ("207R00000X", 20), ("207RC0000X", 8), ("207X00000X", 8),
                     ("207RH0003X", 4), ("2085R0202X", 8), ("207P00000X", 6), ("225100000X", 6),
                     ("2084P0800X", 5), ("207RN0300X", 2), ("207V00000X", 3)]
_FACILITY_KINDS = [("282N00000X", "Medical Center", 10), ("314000000X", "Skilled Nursing Center", 6),
                   ("261QA1903X", "Surgery Center", 6), ("291U00000X", "Clinical Laboratory", 4),
                   ("332B00000X", "Medical Supply", 4), ("251E00000X", "Home Health", 4)]
_PLACES = ["Kestrel Bay", "Amberlin", "Stonereach", "Larkfield", "Pinecrest Valley", "Oriel Springs",
           "Fallowmere", "Hollin Ridge", "Marrow Creek", "Elderglen", "Quillon", "Tamsworth"]
_PHARMACY_TAX = {"retail": "3336C0003X", "mail_order": "3336M0002X", "specialty": "3336S0011X",
                 "long_term_care": "3336L0003X"}

# Generic name, kind, therapeutic class (synthetic label), cost per 30 days. Brand
# products are listed by molecule only; no trademarks. NDCs are synthetic.
_DRUGS = [
    ("atorvastatin 20 mg tablet", "G", "ANTIHYPERLIPIDEMIC", 14), ("metformin 500 mg tablet", "G", "ANTIDIABETIC", 9),
    ("lisinopril 10 mg tablet", "G", "ACE INHIBITOR", 8), ("amlodipine 5 mg tablet", "G", "CALCIUM CHANNEL BLOCKER", 9),
    ("levothyroxine 50 mcg tablet", "G", "THYROID", 16), ("omeprazole 20 mg capsule", "G", "PROTON PUMP INHIBITOR", 12),
    ("sertraline 50 mg tablet", "G", "ANTIDEPRESSANT", 11), ("losartan 50 mg tablet", "G", "ARB", 12),
    ("hydrochlorothiazide 25 mg tablet", "G", "DIURETIC", 6), ("gabapentin 300 mg capsule", "G", "ANTICONVULSANT", 18),
    ("metoprolol succinate 50 mg tablet", "G", "BETA BLOCKER", 15), ("rosuvastatin 10 mg tablet", "G", "ANTIHYPERLIPIDEMIC", 17),
    ("escitalopram 10 mg tablet", "G", "ANTIDEPRESSANT", 13), ("montelukast 10 mg tablet", "G", "LEUKOTRIENE MODIFIER", 15),
    ("albuterol HFA inhaler", "G", "BRONCHODILATOR", 32), ("pantoprazole 40 mg tablet", "G", "PROTON PUMP INHIBITOR", 13),
    ("furosemide 20 mg tablet", "G", "DIURETIC", 6), ("tamsulosin 0.4 mg capsule", "G", "ALPHA BLOCKER", 14),
    ("bupropion XL 150 mg tablet", "G", "ANTIDEPRESSANT", 24), ("trazodone 50 mg tablet", "G", "ANTIDEPRESSANT", 9),
    ("amoxicillin 500 mg capsule", "G", "ANTIBIOTIC", 10), ("meloxicam 15 mg tablet", "G", "NSAID", 9),
    ("glipizide 5 mg tablet", "G", "ANTIDIABETIC", 8), ("carvedilol 12.5 mg tablet", "G", "BETA BLOCKER", 10),
    ("clopidogrel 75 mg tablet", "G", "ANTIPLATELET", 12), ("ezetimibe 10 mg tablet", "G", "ANTIHYPERLIPIDEMIC", 19),
    ("apixaban 5 mg tablet", "B", "ANTICOAGULANT", 560), ("rivaroxaban 20 mg tablet", "B", "ANTICOAGULANT", 540),
    ("empagliflozin 10 mg tablet", "B", "ANTIDIABETIC", 590), ("dapagliflozin 10 mg tablet", "B", "ANTIDIABETIC", 580),
    ("sitagliptin 100 mg tablet", "B", "ANTIDIABETIC", 520), ("semaglutide injection pen", "B", "ANTIDIABETIC", 960),
    ("dulaglutide injection pen", "B", "ANTIDIABETIC", 930), ("insulin glargine pen", "B", "INSULIN", 330),
    ("fluticasone/salmeterol inhaler", "B", "BRONCHODILATOR", 360), ("tiotropium inhaler", "B", "BRONCHODILATOR", 520),
    ("sacubitril/valsartan 49/51 mg tablet", "B", "HEART FAILURE", 640),
    ("adalimumab injection", "S", "IMMUNOLOGY", 6900), ("etanercept injection", "S", "IMMUNOLOGY", 6700),
    ("ustekinumab injection", "S", "IMMUNOLOGY", 7400), ("dupilumab injection", "S", "IMMUNOLOGY", 3600),
    ("ibrutinib 420 mg tablet", "S", "ONCOLOGY", 14800),
]

# Procedure codes by service setting (CPT/HCPCS numbers only; descriptors are licensed).
_PROCS = {"professional": ["99213", "99214", "99215", "99204", "93000", "71046", "97110", "90837", "20610", "G0439"],
          "emergency": ["99284", "99285", "99283"], "outpatient": ["70553", "72148", "74177", "29881", "45378", "66984"],
          "ancillary": ["80053", "85025", "36415", "E0601", "A4253"]}
_DX = {"inpatient": ["A41.9", "I50.9", "J44.1", "M17.11", "S72.001A", "N18.4", "O80"],
       "emergency": ["R07.9", "J45.909", "S72.001A", "I48.91", "R10.9"],
       "outpatient": ["M17.11", "Z12.11", "H25.9", "M54.50", "C50.911", "N18.4"],
       "professional": ["E11.9", "E11.22", "I10", "I50.9", "J44.1", "F33.1", "Z00.00", "E66.01", "I48.91",
                        "M54.50", "F32.9", "K21.9"],
       "ancillary": ["E11.9", "Z00.00", "G47.33", "I10"]}
_DRG = ["871", "872", "291", "292", "193", "194", "690", "392", "603", "247", "807", "470"]

# Illustrative diagnosis -> HCC (V24 number, V28 number). Factors are synthetic.
_DX_HCC = {"E11.22": (18, 37), "E11.9": (19, 38), "I50.9": (85, 226), "J44.1": (111, 280),
           "I48.91": (96, 238), "N18.4": (137, 327), "E66.01": (22, 48), "F33.1": (59, 155),
           "C50.911": (12, 23)}
_HCC_LABEL = {37: "Diabetes with chronic complications", 38: "Diabetes with glycemic or unspecified complications",
              226: "Heart failure", 280: "Chronic obstructive pulmonary disease", 238: "Specified heart arrhythmias",
              327: "Chronic kidney disease, severe (stage 4)", 48: "Morbid obesity", 155: "Major depression, moderate or severe",
              23: "Breast and other cancers", 18: "Diabetes with chronic complications", 19: "Diabetes without complication",
              85: "Congestive heart failure", 111: "Chronic obstructive pulmonary disease",
              96: "Specified heart arrhythmias", 137: "Chronic kidney disease, severe (stage 4)",
              22: "Morbid obesity", 59: "Major depressive, bipolar and paranoid disorders", 12: "Breast, prostate and other cancers"}

# Prior-auth services: code, UM delegate, typical diagnosis.
_PA_SERVICES = [("70553", "radiology_benefit_manager", "G43.909", 10), ("72148", "radiology_benefit_manager", "M54.50", 12),
                ("74177", "radiology_benefit_manager", "R10.9", 8), ("78815", "radiology_benefit_manager", "C50.911", 4),
                ("27447", "musculoskeletal_benefit_manager", "M17.11", 8), ("29881", "musculoskeletal_benefit_manager", "M23.205", 6),
                ("63030", "musculoskeletal_benefit_manager", "M51.16", 4), ("97110", "musculoskeletal_benefit_manager", "M54.50", 14),
                ("J9271", "oncology_pathway_vendor", "C50.911", 3), ("J9355", "oncology_pathway_vendor", "C50.911", 3),
                ("E0601", "in_house", "G47.33", 8), ("E1390", "in_house", "J44.1", 6), ("G0299", "in_house", "I50.9", 6),
                ("99223", "in_house", "I50.9", 8)]

_CARC = {"medical_necessity": "CO-50", "no_prior_authorization": "CO-197", "not_covered": "CO-96",
         "eligibility": "CO-27", "duplicate": "CO-18", "timely_filing": "CO-29", "coding_billing_error": "CO-16",
         "coordination_of_benefits": "CO-22", "clinical_validation": "CO-50"}
_DENIALS = [("medical_necessity", 22), ("no_prior_authorization", 14), ("not_covered", 12), ("eligibility", 10),
            ("duplicate", 14), ("timely_filing", 5), ("coding_billing_error", 18), ("coordination_of_benefits", 5)]

# Per-unit utilization: category -> (claims per member month at risk 1.0, mean allowed per claim).
_UTIL = {
    "COMM": {"inpatient": (0.0054, 28000), "emergency": (0.0160, 2300), "outpatient": (0.060, 2900),
             "professional": (0.45, 650), "ancillary": (0.08, 420)},
    "MA": {"inpatient": (0.0265, 21000), "emergency": (0.0430, 1400), "outpatient": (0.150, 2300),
           "professional": (0.62, 480), "ancillary": (0.25, 200)},
}
_PROFILE = {
    "COMM": {"los": 3.9, "deny": 0.085, "auto": 0.86, "pa_rate": 0.022, "rx_rate": 0.75,
             "rx_mix": (0.865, 0.123, 0.012), "grievance": 0.0012, "appeal_pa": 0.15},
    "MA": {"los": 4.9, "deny": 0.065, "auto": 0.89, "pa_rate": 0.12, "rx_rate": 1.50,
           "rx_mix": (0.895, 0.097, 0.008), "grievance": 0.0026, "appeal_pa": 0.25},
}


def _lognorm(rng, sigma):
    """Mean-one lognormal multiplier."""
    return rng.lognormvariate(-sigma * sigma / 2, sigma)


def _pick(rng, weighted):
    return rng.choices([w[:-1] if len(w) > 2 else w[0] for w in weighted], [w[-1] for w in weighted])[0]


def _mbi(rng) -> str:
    """A syntactically valid (synthetic) Medicare Beneficiary Identifier."""
    a, d = "ACDEFGHJKMNPQRTUVWXY", "0123456789"
    return (rng.choice("123456789") + rng.choice(a) + rng.choice(a + d) + rng.choice(d) + rng.choice(a)
            + rng.choice(a + d) + rng.choice(d) + rng.choice(a) + rng.choice(a) + rng.choice(d) + rng.choice(d))


def build_meridian(path: Path, seed: int = 23, comm_members: int = 14000, ma_members: int = 6000) -> Path:
    """Meridian Health Plan: a fictional regional payer on two platforms.

        mdm       enterprise MDM hub hosted on the commercial platform: org units,
                  legal entities, provider/pharmacy directory (NPI), drug file (NDC), PBMs
        comm_raw  COMM: legacy commercial claims & enrollment platform (UPPER_CASE
                  mainframe names, coded values, 9999-12-31 open spans, YYYYMM months)
        ma_src    MA: Medicare Advantage platform (snake_case, CMS-style codes, MMR,
                  risk scores, encounter data, Star results, bids)

    24 months of history (2024-07 .. 2026-06) valued at MERIDIAN_VALUATION. Calibrated
    to industry-typical levels: commercial medical PMPM ~$500, MA ~$1,050-1,150;
    pharmacy PMPM ~$120 / ~$175; MLR ~85%; admits/1,000 ~55 commercial, ~280 MA;
    PA approval ~88%; MA normalized risk score ~1.0. Claim lags leave the latest
    incurred months incomplete, as in any real warehouse.

    Planted signals the curator and drift loop should find:
      * ma_src.claim.denial_group starts carrying 'CLINICAL_VALIDATION' (DRG
        clinical-validation denials) from March 2026; not in DenialCategoryEnum
      * ma_src.risk_score.model_ver is 'V28' for payment year 2026 (100% V28 after
        the 2024-2025 blend); not in RiskScoreBasisEnum
      * standard MA prior auths delegated to the MSK benefit manager and received
        March-April 2026 are mostly decided in 8-16 days (CMS-0057-F: 7 days)
    """
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    for s in ("mdm", "comm_raw", "ma_src"):
        con.execute(f"CREATE SCHEMA {s}")
    ref = _m_mdm(con, rng)
    _m_comm(con, rng, ref, comm_members)
    _m_ma(con, rng, ref, ma_members)
    con.close()
    return path


build_meridian.valuation = MERIDIAN_VALUATION


# ------------------------------------------------------------------- MDM hub

def _m_mdm(con, rng) -> dict:
    con.execute("CREATE TABLE mdm.ORG_UNIT (UNIT_CD VARCHAR, UNIT_NM VARCHAR, SEGMENT_CD VARCHAR)")
    _insert(con, "mdm.ORG_UNIT", [("COMM", "Meridian Commercial", "HP"), ("MA", "Meridian Medicare Advantage", "HP")])
    con.execute("CREATE TABLE mdm.LEGAL_ENTITY (ENTITY_CD VARCHAR, ENTITY_NM VARCHAR, NAIC_CD VARCHAR, DOM_ST VARCHAR, FEIN VARCHAR)")
    _insert(con, "mdm.LEGAL_ENTITY", [("MHP-HMO", "Meridian Health Plan, Inc.", "95871", "MI", "38-5550101"),
                                       ("MHIC", "Meridian Health Insurance Company", "60412", "MI", "38-5550102")])

    con.execute("""CREATE TABLE mdm.PROVIDER (NPI VARCHAR, PRV_NAME VARCHAR, ENTITY_TYPE VARCHAR, TAXONOMY VARCHAR,
        STATE VARCHAR, PRV_CLASS VARCHAR, NCPDP_ID VARCHAR, RX_CHANNEL VARCHAR)""")
    pool = iter(rng.sample(range(100_000_000, 999_999_999), 1500))
    ncpdp = iter(rng.sample(range(1_000_000, 9_999_999), 100))
    rows, ref = [], {"prof": {}, "fac": {}, "pharm": {}}
    for _ in range(700):
        tax = _pick(rng, _PROF_SPECIALTIES)
        npi = f"1{next(pool)}"
        rows.append((npi, f"{rng.choice(FIRST)} {rng.choice(LAST)}, MD", "1", tax, rng.choice(M_STATES), "PROF", None, None))
        ref["prof"].setdefault(tax, []).append(npi)
    for tax, kind, n in _FACILITY_KINDS:
        for i in range(n):
            npi = f"1{next(pool)}"
            rows.append((npi, f"{_PLACES[(i * 5 + len(kind)) % len(_PLACES)]} {kind}", "2", tax, rng.choice(M_STATES),
                         "FAC", None, None))
            ref["fac"].setdefault(tax, []).append(npi)
    for channel, n in (("retail", 50), ("mail_order", 2), ("specialty", 3), ("long_term_care", 3)):
        for i in range(n):
            npi = f"1{next(pool)}"
            name = f"{_PLACES[i % len(_PLACES)]} Pharmacy" if channel == "retail" else f"Meridian {channel.replace('_', ' ').title()} Pharmacy {i + 1}"
            rows.append((npi, name, "2", _PHARMACY_TAX[channel], rng.choice(M_STATES), "RX", str(next(ncpdp)),
                         {"retail": "R", "mail_order": "M", "specialty": "S", "long_term_care": "L"}[channel]))
            ref["pharm"].setdefault(channel, []).append(npi)
    # Two physician organizations that hold value-based contracts.
    ref["lakeshore"] = f"1{next(pool)}"
    ref["northgate"] = f"1{next(pool)}"
    rows.append((ref["lakeshore"], "Lakeshore Physician Partners", "2", "261QP2300X", "MI", "PROF", None, None))
    rows.append((ref["northgate"], "Northgate Health Partners", "2", "261QP2300X", "OH", "PROF", None, None))
    _insert(con, "mdm.PROVIDER", rows)
    ref["pcp"] = ref["prof"]["207Q00000X"] + ref["prof"]["207R00000X"]
    ref["lakeshore_pcps"] = ref["pcp"][:40]
    ref["prof_all"] = [r[0] for r in rows if r[5] == "PROF" and r[2] == "1"]
    ref["prof_weighted"] = [(npis, w) for tax, w in _PROF_SPECIALTIES for npis in [ref["prof"][tax]]]

    con.execute("""CREATE TABLE mdm.DRUG (NDC11 VARCHAR, PRODUCT_NAME VARCHAR, GENERIC_NAME VARCHAR, MULTI_SRC VARCHAR,
        THERA_CLASS VARCHAR, SPECIALTY_IND VARCHAR)""")
    drows, ref["drugs"], seen = [], {"G": [], "B": [], "S": []}, set()
    labelers = rng.sample(range(10000, 99999), 30)
    for i, (name, kind, cls, cost) in enumerate(_DRUGS):
        for k in range(3 if kind == "G" else 1):     # generics come from several labelers
            ndc = None
            while ndc is None or ndc in seen:
                ndc = f"{labelers[(i + k * 7) % 30]:05d}{rng.randint(1000, 9999):04d}{rng.choice([1, 2, 30, 90]):02d}"
            seen.add(ndc)
            drows.append((ndc, name.upper() if kind != "G" else name, name.split(" ")[0], "B" if kind != "G" else "G",
                          cls, "Y" if kind == "S" else "N"))
            ref["drugs"][kind].append((ndc, cost))
    _insert(con, "mdm.DRUG", drows)
    ref["all_drugs"] = [(r[0], r[3], r[5]) for r in drows]

    con.execute("CREATE TABLE mdm.PBM (PBM_ID VARCHAR, PBM_NAME VARCHAR, FEIN VARCHAR, PRICING_CD VARCHAR)")
    _insert(con, "mdm.PBM", [("PBM-LKV", "Lakeview Rx Services", "41-5550188", "PT"),
                              ("PBM-BWP", "Bluewater Pharmacy Benefits", "36-5550142", "SPRD")])
    return ref


# ---------------------------------------------------- shared event generators

def _prof_npi(rng, ref) -> str:
    npis = rng.choices([n for n, _ in ref["prof_weighted"]], [w for _, w in ref["prof_weighted"]])[0]
    return rng.choice(npis)


def _medical(rng, unit, ref, m, month, network, seq) -> list[dict]:
    """Claims incurred by one member in one month (neutral layout; units format them)."""
    out = []
    prof = _PROFILE[unit]
    for cat, (rate, mean) in _UTIL[unit].items():
        for _ in range(_poisson(rng, rate * m["risk"])):
            start = month + timedelta(days=rng.randint(0, 27))
            los = 0
            if cat == "inpatient":
                los = 1 + _poisson(rng, prof["los"] - 1)
                thru = start + timedelta(days=los)
            else:
                thru = start
            received = thru + timedelta(days=2 + int(rng.expovariate(1 / 16)))
            if received > MERIDIAN_VALUATION:
                continue
            auto = rng.random() < prof["auto"]
            final = received + timedelta(days=rng.randint(1, 10) if auto else rng.randint(8, 40))
            allowed = round(mean * _lognorm(rng, 0.85 if cat != "inpatient" else 0.6), 2)
            encounter = False
            if cat == "inpatient":
                bill = rng.choice(ref["fac"]["282N00000X"])
                rend = rng.choice(ref["pcp"])
            elif cat == "emergency":
                bill, rend = rng.choice(ref["fac"]["282N00000X"]), rng.choice(ref["prof"]["207P00000X"])
            elif cat == "outpatient":
                kinds = ["282N00000X", "261QA1903X"] + (["314000000X", "251E00000X"] if unit == "MA" else [])
                bill, rend = rng.choice(ref["fac"][rng.choice(kinds)]), _prof_npi(rng, ref)
            elif cat == "ancillary":
                bill = rng.choice(ref["fac"][rng.choice(["291U00000X", "332B00000X"])])
                rend = bill
            elif m.get("capitated") and rng.random() < 0.55:
                bill, rend, encounter = ref["lakeshore"], rng.choice(ref["lakeshore_pcps"]), True
            else:
                bill = rend = _prof_npi(rng, ref)
            status, deny = "paid", None
            if final > MERIDIAN_VALUATION:
                status, final = "pended", None
            elif not encounter and rng.random() < prof["deny"]:
                status, deny = "denied", _pick(rng, _DENIALS)
            elif not encounter and rng.random() < 0.004:
                status = "voided"
            # Planted (MA): DRG clinical-validation denials begin with the March 2026 policy.
            if (unit == "MA" and cat == "inpatient" and status == "paid" and final >= date(2026, 3, 1)
                    and rng.random() < 0.2):
                status, deny = "denied", "clinical_validation"
            in_net = encounter or bill in network or rng.random() < 0.2
            liab = 0.0 if status != "paid" or encounter else round(allowed * m["liab"], 2)
            paid = 0.0 if status != "paid" or encounter else round(allowed - liab, 2)
            seq[0] += 1
            out.append({"id": seq[0], "cat": cat, "bill": bill, "rend": rend, "from": start, "thru": thru,
                        "admit": start if cat == "inpatient" else None, "disch": thru if cat == "inpatient" else None,
                        "drg": rng.choice(_DRG) if cat == "inpatient" else None, "received": received, "final": final,
                        "status": status, "deny": deny, "dx": rng.choice(_DX[cat]),
                        "billed": round(allowed * rng.uniform(1.6, 2.8), 2),
                        "allowed": 0.0 if status == "denied" else allowed, "paid": paid, "liab": liab,
                        "in_network": in_net, "auto": auto and status in ("paid", "denied"), "encounter": encounter,
                        "los": los, "auth": None})
    return out


def _lines(rng, c) -> list[tuple]:
    """(line_no, proc, modifier, units, billed, allowed, paid, status, carc)."""
    cat = c["cat"]
    n = 1 if cat == "inpatient" else rng.randint(1, 3 if cat == "professional" else 2)
    weights = [rng.random() + 0.2 for _ in range(n)]
    tot = sum(weights)
    out = []
    for i, w in enumerate(weights, 1):
        share = w / tot
        denied = c["status"] == "denied"
        proc = None if cat == "inpatient" else rng.choice(_PROCS[cat])
        out.append((i, proc, rng.choice([None, None, None, "25", "59"]) if cat == "professional" else None,
                    c["los"] if cat == "inpatient" else 1, round(c["billed"] * share, 2),
                    round(c["allowed"] * share, 2), round(c["paid"] * share, 2),
                    "denied" if denied else "paid", _CARC[c["deny"]] if denied else "CO-45"))
    return out


def _auths(rng, unit, m, month, seq) -> list[dict]:
    out = []
    for _ in range(_poisson(rng, _PROFILE[unit]["pa_rate"] * m["risk"] ** 0.5)):
        code, delegate, dx = _pick(rng, _PA_SERVICES)
        seq[0] += 1
        out.append(_auth_record(rng, unit, seq[0], code, delegate, dx,
                                datetime.combine(month + timedelta(days=rng.randint(0, 27)), datetime.min.time())
                                + timedelta(minutes=rng.randint(6 * 60, 20 * 60))))
    return out


def _auth_record(rng, unit, aid, code, delegate, dx, received, status=None) -> dict:
    expedited = rng.random() < 0.12
    if expedited:
        hours = rng.uniform(2, 70) if rng.random() < 0.96 else rng.uniform(74, 120)
    else:
        hours = rng.uniform(4, 160) if rng.random() < 0.95 else rng.uniform(170, 300)
        # Planted (MA): the MSK benefit manager's spring-2026 backlog breaches the 7-day standard.
        if (unit == "MA" and delegate == "musculoskeletal_benefit_manager"
                and date(2026, 3, 1) <= received.date() <= date(2026, 4, 30) and rng.random() < 0.7):
            hours = rng.uniform(8 * 24, 16 * 24)
    status = status or rng.choices(["approved", "partially_approved", "denied", "pended_for_info", "withdrawn"],
                                   [790, 40, 130, 10, 20])[0]
    decided = received + timedelta(hours=hours)
    if status in ("pended_for_info", "withdrawn") or decided > _EOD:
        decided = None
        if status not in ("pended_for_info", "withdrawn"):
            status = "in_review"
    reason = {"approved": "Meets medical necessity criteria",
              "partially_approved": "Approved fewer units than requested",
              "denied": rng.choice(["Not medically necessary per clinical criteria",
                                    "Conservative treatment not documented",
                                    "Out-of-network provider without exception"])}.get(status)
    units = rng.randint(1, 12) if status == "approved" else (rng.randint(1, 4) if status == "partially_approved" else None)
    return {"id": aid, "code": code, "delegate": delegate, "dx": dx, "expedited": expedited, "received": received,
            "decided": decided if status in ("approved", "partially_approved", "denied") else None,
            "status": status, "reason": reason if decided else None, "units": units}


def _scripts(rng, unit, ref, m, month, seq) -> list[dict]:
    out = []
    prof = _PROFILE[unit]
    g, b, _ = prof["rx_mix"]
    for _ in range(_poisson(rng, prof["rx_rate"] * m["rx_risk"])):
        u = rng.random()
        kind = "G" if u < g else ("B" if u < g + b else "S")
        ndc, cost30 = rng.choice(ref["drugs"][kind])
        days = 90 if kind != "S" and rng.random() < 0.2 else 30
        channel = ("specialty" if kind == "S" else "long_term_care" if m.get("institutional")
                   else "mail_order" if days == 90 else "retail")
        ingredient = round(cost30 * days / 30 * rng.uniform(0.85, 1.15), 2)
        fee = 0.0 if kind == "S" else 1.75
        if unit == "COMM":
            tier = 1 if kind == "G" else 4 if kind == "S" else (2 if rng.random() < 0.8 else 3)
            copay = {1: 10.0, 2: 40.0, 3: 80.0}.get(tier) or min(ingredient * 0.25, 250.0)
        else:
            tier = (1 if rng.random() < 0.7 else 2) if kind == "G" else 5 if kind == "S" else (3 if rng.random() < 0.75 else 4)
            copay = {1: 0.0, 2: 10.0, 3: 47.0, 4: 100.0}.get(tier) or min(ingredient * 0.25, 300.0)
        copay = round(copay * (2 if days == 90 else 1), 2)
        status = rng.choices(["paid", "rejected", "reversed"], [92, 6, 2])[0]
        plan_paid = round(max(ingredient + fee - copay, 0.0), 2) if status == "paid" else 0.0
        seq[0] += 1
        out.append({"id": seq[0], "ndc": ndc, "fill": month + timedelta(days=rng.randint(0, 27)), "days": days,
                    "qty": days * rng.choice([1, 1, 2]) if kind != "S" else rng.choice([1, 2]),
                    "fill_no": rng.randint(0, 5), "ingredient": ingredient, "fee": fee,
                    "plan_paid": plan_paid, "member_paid": copay if status == "paid" else 0.0, "tier": tier,
                    "daw": rng.choices(["0", "1", "2"], [95, 3, 2])[0], "status": status,
                    "pharmacy": rng.choice(ref["pharm"][channel]),
                    "prescriber": rng.choice(ref["prof"]["207RH0003X"] if kind == "S" else ref["pcp"])})
    return out


def _enrolled_months(start: date, end: date | None) -> list[date]:
    last = min(end or MERIDIAN_VALUATION, MERIDIAN_VALUATION)
    return [mo for mo in M_MONTHS if start <= mo <= last]


def _appeals(rng, unit, denied_auths, denied_claims, seq) -> list[dict]:
    """Appeals of adverse PA and claim decisions; MA upheld reconsiderations go to the IRE."""
    out = []

    def add(member, plan, claim, auth, decided, expedited, level):
        rcvd = decided + timedelta(days=rng.randint(3, 45))
        if rcvd > MERIDIAN_VALUATION:
            return None
        outcome = rng.choices(["overturned", "partially_overturned", "upheld", "withdrawn"], [42, 6, 47, 5])[0]
        if level == "independent_review_entity":
            outcome = rng.choices(["overturned", "upheld"], [15, 85])[0]
        dec = rcvd + timedelta(days=rng.randint(1, 3) if expedited else rng.randint(5, 28))
        if dec > MERIDIAN_VALUATION:
            outcome, dec = "pending", None
        if outcome == "withdrawn":
            dec = None
        seq[0] += 1
        a = {"id": seq[0], "member": member, "plan": plan, "claim": claim, "auth": auth, "level": level,
             "expedited": expedited, "received": rcvd, "decided": dec, "outcome": outcome,
             "reason": {"overturned": "Additional clinical documentation supports coverage",
                        "partially_overturned": "Coverage approved for a reduced service",
                        "upheld": "Original determination is consistent with coverage criteria"}.get(outcome)}
        out.append(a)
        return a

    for a, member, plan in denied_auths:
        if rng.random() < _PROFILE[unit]["appeal_pa"]:
            first = add(member, plan, None, a["id"], a["decided"].date(), a["expedited"], "internal_first_level")
            if first and first["outcome"] == "upheld":
                add(member, plan, None, a["id"], first["decided"],
                    a["expedited"], "independent_review_entity" if unit == "MA" else "external_review")
    for c, member, plan in denied_claims:
        if rng.random() < 0.03:
            add(member, plan, c["id"], None, c["final"], False, "internal_first_level")
    return out


def _grievances(rng, unit, m, month, seq) -> list[dict]:
    out = []
    for _ in range(_poisson(rng, _PROFILE[unit]["grievance"])):
        cat, text = rng.choice([
            ("access_to_care", "Member reports a long wait for a specialist appointment"),
            ("quality_of_care", "Member concerned about care received during an office visit"),
            ("customer_service", "Member was transferred several times by the call center"),
            ("billing_premium", "Member disputes a premium or cost-sharing bill"),
            ("enrollment", "Member ID card not received after enrollment"),
            ("pharmacy", "Member could not fill a prescription at a network pharmacy")])
        rcvd = month + timedelta(days=rng.randint(0, 27))
        expedited = rng.random() < 0.05
        res = rcvd + timedelta(days=rng.randint(0, 1) if expedited else rng.randint(2, 30))
        seq[0] += 1
        out.append({"id": seq[0], "cat": cat, "text": text, "received": rcvd, "expedited": expedited,
                    "resolved": res if res <= MERIDIAN_VALUATION else None})
    return out


# ---------------------------------------------------------------------- COMM

_COMM_PLANS = [  # code, name, product, metal, segment, funding, network, deductible, oop, member liability share
    ("C-IND-BRZ", "Meridian Individual Bronze HMO", "HMO", "B", "IND", "FI", "N-HMO", 7500, 9200, 0.32),
    ("C-IND-SLV", "Meridian Individual Silver HMO", "HMO", "S", "IND", "FI", "N-HMO", 4500, 9200, 0.22),
    ("C-SG-GLD", "Meridian Small Group Gold PPO", "PPO", "G", "SG", "FI", "N-PPO", 1500, 7000, 0.15),
    ("C-SG-SLV", "Meridian Small Group Silver PPO", "PPO", "S", "SG", "FI", "N-PPO", 3500, 8500, 0.21),
    ("C-LG-PPO", "Meridian Large Group PPO", "PPO", None, "LG", "FI", "N-PPO", 1000, 5000, 0.14),
    ("C-LG-HDHP", "Meridian Large Group HSA PPO", "HDHP", None, "LG", "FI", "N-PPO", 3300, 6500, 0.24),
    ("C-ASO-PPO", "Meridian Self-Funded PPO", "PPO", None, "LG", "ASO", "N-PPO", 1000, 5000, 0.15),
]


def _m_comm(con, rng, ref, target):
    S = "comm_raw"
    plans = {p[0]: p for p in _COMM_PLANS}

    con.execute(f"CREATE TABLE {S}.BRKR (BRKR_ID VARCHAR, BRKR_NM VARCHAR, BRKR_TYP VARCHAR, LIC_ST VARCHAR)")
    brokers = [(f"BR{i:03d}", f"{rng.choice(_PLACES)} Benefits Advisors {i}", rng.choice(["R", "R", "W"]),
                rng.choice(M_STATES)) for i in range(25)]
    _insert(con, f"{S}.BRKR", brokers)

    con.execute(f"""CREATE TABLE {S}.GRP_MSTR (GRP_ID VARCHAR, GRP_NBR VARCHAR, GRP_NM VARCHAR, FEIN VARCHAR,
        GRP_SEG VARCHAR, FUND_CD VARCHAR, ST_CD VARCHAR, ELIG_EE_CT INTEGER, BRKR_ID VARCHAR, EFF_DT DATE, TRM_DT DATE)""")
    groups, gid = [], 0
    for seg, fund, n, size in (("SG", "FI", 240, (2, 50)), ("LG", "FI", 60, (51, 900)), ("LG", "ASO", 20, (300, 2500))):
        for _ in range(n):
            gid += 1
            ee = rng.randint(*size)
            plan_opts = (["C-SG-GLD"] if rng.random() < 0.5 else ["C-SG-SLV"]) if seg == "SG" else \
                (["C-ASO-PPO"] if fund == "ASO" else ["C-LG-PPO", "C-LG-HDHP"])
            groups.append({"id": f"G{gid:05d}", "seg": seg, "fund": fund, "ee": ee, "plans": plan_opts,
                           "term": date(2025, 12, 31) if rng.random() < 0.05 else None})
    _insert(con, f"{S}.GRP_MSTR", [
        (g["id"], f"{100000 + i}", f"{rng.choice(_PLACES)} {rng.choice(['Manufacturing', 'Dental Group', 'Credit Union', 'Logistics', 'Schools', 'Foods', 'Engineering', 'Hospitality'])} {i}",
         f"{rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}", g["seg"], g["fund"], rng.choice(M_STATES), g["ee"],
         rng.choice(brokers)[0], date(rng.randint(2012, 2024), rng.randint(1, 12), 1), g["term"])
        for i, g in enumerate(groups)])

    con.execute(f"CREATE TABLE {S}.NTWK (NTWK_ID VARCHAR, NTWK_NM VARCHAR, NTWK_TYP VARCHAR)")
    _insert(con, f"{S}.NTWK", [("N-PPO", "Meridian Choice PPO Network", "P"), ("N-HMO", "Meridian Local HMO Network", "H")])
    all_prv = ref["prof_all"] + [n for v in ref["fac"].values() for n in v] + [ref["lakeshore"], ref["northgate"]]
    con.execute(f"CREATE TABLE {S}.PRV_NTWK (PN_ID VARCHAR, NPI VARCHAR, NTWK_ID VARCHAR, EFF_DT DATE, TRM_DT DATE)")
    pn, network = [], {"N-PPO": set(), "N-HMO": set()}
    for npi in all_prv:
        for net, p in (("N-PPO", 0.88), ("N-HMO", 0.65)):
            if rng.random() < p:
                pn.append((f"PN{len(pn):06d}", npi, net, date(2020, 1, 1), OPEN_END))
                network[net].add(npi)
    _insert(con, f"{S}.PRV_NTWK", pn)

    con.execute(f"CREATE TABLE {S}.FEE_SCHD (FS_ID VARCHAR, FS_NM VARCHAR, FS_TYP VARCHAR, BASE_YR INTEGER, EFF_DT DATE)")
    _insert(con, f"{S}.FEE_SCHD", [("FS-PRO-25", "Commercial professional (% of Medicare)", "MCR", 2025, date(2025, 1, 1)),
                                   ("FS-FAC-25", "Commercial facility DRG", "DRG", 2025, date(2025, 1, 1)),
                                   ("FS-CUST-24", "Commercial custom schedule", "CUST", 2024, date(2024, 1, 1))])
    con.execute(f"""CREATE TABLE {S}.PRV_CNTRCT (CNTRCT_ID VARCHAR, NPI VARCHAR, NTWK_ID VARCHAR, REIMB_CD VARCHAR,
        FS_ID VARCHAR, PCT_MCR DECIMAL(5,3), EFF_DT DATE, TRM_DT DATE, CNTRCT_STS VARCHAR)""")
    contracts = []
    for npi in [n for v in ref["fac"].values() for n in v] + [ref["northgate"]] + rng.sample(ref["prof_all"], 150):
        fac = npi in ref["fac"]["282N00000X"]
        contracts.append((f"CC{len(contracts):05d}", npi, "N-PPO", "DRG" if fac else "PCTM",
                          "FS-FAC-25" if fac else "FS-PRO-25", round(rng.uniform(1.35, 2.2), 3),
                          date(2023, 1, 1), OPEN_END, "A"))
    _insert(con, f"{S}.PRV_CNTRCT", contracts)
    northgate_contract = next(c[0] for c in contracts if c[1] == ref["northgate"])
    con.execute(f"""CREATE TABLE {S}.VBC_ARR (ARR_ID VARCHAR, CNTRCT_ID VARCHAR, NPI VARCHAR, ARR_TYP VARCHAR,
        CAP_PMPM DECIMAL(8,2), SS_PCT DECIMAL(5,2), MSR_PCT DECIMAL(5,2), QUAL_GATE VARCHAR, EFF_DT DATE, TRM_DT DATE)""")
    _insert(con, f"{S}.VBC_ARR", [
        ("VB001", northgate_contract, ref["northgate"], "SS", None, 50.0, 2.0, "Y", date(2025, 1, 1), OPEN_END),
        ("VB002", contracts[0][0], contracts[0][1], "P4P", None, None, None, "Y", date(2024, 1, 1), OPEN_END)])

    _formularies(con, rng, ref, S, "COMM")
    con.execute(f"""CREATE TABLE {S}.PLAN_MSTR (PLAN_CD VARCHAR, PLAN_DESC VARCHAR, PROD_TYP VARCHAR, METAL_CD VARCHAR,
        MKT_SEG VARCHAR, FUND_CD VARCHAR, NTWK_ID VARCHAR, DED_AMT DECIMAL(10,2), OOP_AMT DECIMAL(10,2), FRMLY_ID VARCHAR,
        LOB_CD VARCHAR, EFF_DT DATE, TRM_DT DATE)""")
    _insert(con, f"{S}.PLAN_MSTR", [p[:9] + ("CF-2026", "COM", date(2024, 1, 1), OPEN_END) for p in _COMM_PLANS])

    # --- members and eligibility
    members = []
    n_sub = 0
    while len(members) < target:
        n_sub += 1
        u = rng.random()
        if u < 0.14:
            grp, plan = None, rng.choice(["C-IND-BRZ", "C-IND-SLV"])
        else:
            pool = [g for g in groups if (g["fund"] == "ASO") == (u > 0.79) and (g["seg"] == "SG") == (u < 0.43)]
            grp = rng.choices(pool, [g["ee"] for g in pool])[0]
            plan = rng.choice(grp["plans"])
        start = M_START if rng.random() < 0.8 else rng.choice(M_MONTHS[1:])
        end = None
        if grp and grp["term"] and start <= grp["term"]:
            end = grp["term"]
        elif rng.random() < (0.22 if grp is None else 0.14):
            later = [mo for mo in M_MONTHS if mo > start]
            end = _month_end(rng.choice(later)) if later else None
        if grp and grp["term"] and start > grp["term"]:
            continue
        family = 1 + (_poisson(rng, 1.1) if grp else _poisson(rng, 0.5))
        sub_id = f"C{len(members) + 1:07d}"
        sub_age = rng.randint(22, 64)
        for k in range(family):
            mid = sub_id if k == 0 else f"C{len(members) + 1:07d}"
            rel = "18" if k == 0 else ("01" if k == 1 and rng.random() < 0.7 else "19")
            age = sub_age if k == 0 else (max(19, sub_age + rng.randint(-6, 6)) if rel == "01" else rng.randint(0, 25))
            sex = rng.choice("FM") if rng.random() < 0.99 else "U"
            factor = 0.45 + 1.25 * (age / 64) ** 2.2 + (0.35 if sex == "F" and 20 <= age <= 40 else 0.0)
            members.append({"id": mid, "sub": sub_id, "rel": rel, "age": age, "sex": sex, "grp": grp, "plan": plan,
                            "start": start, "end": end, "factor": factor, "liab": plans[plan][9]})
    mean_f = sum(m["factor"] for m in members) / len(members)
    for m in members:
        m["risk"] = m["factor"] / mean_f * _lognorm(rng, 1.0)
        m["rx_risk"] = (m["factor"] / mean_f) ** 0.7 * _lognorm(rng, 0.8)

    con.execute(f"""CREATE TABLE {S}.MBR (MBR_ID VARCHAR, SUB_ID VARCHAR, FNAME VARCHAR, LNAME VARCHAR, DOB DATE,
        SEX VARCHAR, SUB_MBR_ID VARCHAR, REL_CD VARCHAR, ST_CD VARCHAR, ZIP VARCHAR, CM_PGM VARCHAR)""")
    con.execute(f"""CREATE TABLE {S}.MBR_ELIG (ELIG_ID VARCHAR, MBR_ID VARCHAR, GRP_ID VARCHAR, PLAN_CD VARCHAR,
        EFF_DT DATE, TERM_DT DATE)""")
    _insert(con, f"{S}.MBR", [
        (m["id"], f"MRD{int(m['sub'][1:]):09d}" + (f"{m['rel']}" if m["rel"] != "18" else "00"), rng.choice(FIRST),
         rng.choice(LAST), date(2026 - m["age"], rng.randint(1, 12), rng.randint(1, 28)), m["sex"], m["sub"], m["rel"],
         rng.choice(M_STATES), f"{rng.randint(43000, 54999)}",
         rng.choices([None, "DM", "HF", "COPD", "MAT", "BH", "CCM"], [85, 5, 2, 2, 2, 3, 1])[0]) for m in members])
    _insert(con, f"{S}.MBR_ELIG", [(f"EL{i:07d}", m["id"], m["grp"]["id"] if m["grp"] else None, m["plan"], m["start"],
                                    m["end"] or OPEN_END) for i, m in enumerate(members)])

    # --- claims, auths, scripts, grievances
    hdr, dtl, rx, um, griev = [], [], [], [], []
    denied_auths, denied_claims = [], []
    cseq, aseq, rseq, gseq = [0], [0], [0], [0]
    cost_by_plan: dict[str, float] = {}
    mm_by_plan: dict[str, int] = {}
    deny_code = {"medical_necessity": "MN", "no_prior_authorization": "NA", "not_covered": "NC", "eligibility": "EL",
                 "duplicate": "DP", "timely_filing": "TF", "coding_billing_error": "CO", "coordination_of_benefits": "CB"}
    sts_code = {"paid": "PD", "denied": "DN", "pended": "PN", "voided": "VD"}
    for m in members:
        net = network["N-HMO" if plans[m["plan"]][2] == "HMO" else "N-PPO"]
        for month in _enrolled_months(m["start"], m["end"]):
            if month < date(2026, 1, 1):
                mm_by_plan[m["plan"]] = mm_by_plan.get(m["plan"], 0) + 1
            for c in _medical(rng, "COMM", ref, m, month, net, cseq):
                if c["cat"] == "inpatient" and rng.random() < 0.35:
                    aseq[0] += 1
                    a = _auth_record(rng, "COMM", aseq[0], rng.choice(["27447", "63030", "99223"]), "in_house",
                                     c["dx"], datetime.combine(c["from"] - timedelta(days=rng.randint(4, 12)),
                                                               datetime.min.time()) + timedelta(hours=9), "approved")
                    a["expedited"] = False
                    a["decided"] = a["received"] + timedelta(hours=rng.uniform(6, 90))
                    a["reason"] = "Meets medical necessity criteria"
                    um.append(_comm_um_row(a, m))
                    c["auth"] = a["id"]
                if month < date(2026, 1, 1):
                    cost_by_plan[m["plan"]] = cost_by_plan.get(m["plan"], 0.0) + c["paid"] + (
                        (c["allowed"] * (1 - m["liab"])) if c["status"] == "pended" else 0.0)
                cid = f"CLM{c['id']:09d}"
                ip, er = c["cat"] == "inpatient", c["cat"] == "emergency"
                inst = c["cat"] in ("inpatient", "emergency", "outpatient") and c["bill"] not in ref["fac"]["261QA1903X"]
                hdr.append((cid, m["id"], m["plan"], c["bill"], c["rend"], "I" if inst else "P",
                            ("111" if ip else "131") if inst else None,
                            None if inst else {"professional": "11", "ancillary": "81", "outpatient": "24"}.get(c["cat"], "22"),
                            "Y" if er else "N", c["admit"], c["disch"], c["drg"], c["from"], c["thru"], c["received"],
                            c["final"], sts_code[c["status"]], c["dx"], c["billed"], c["allowed"], c["paid"], c["liab"],
                            "Y" if c["in_network"] else "N", f"UM{c['auth']:08d}" if c["auth"] else None,
                            deny_code.get(c["deny"]), "Y" if c["auto"] else "N"))
                for ln in _lines(rng, c):
                    dtl.append((cid, *ln[:7], {"paid": "P", "denied": "D"}[ln[7]], ln[8]))
                if c["status"] == "denied":
                    denied_claims.append((c, m, m["plan"]))
            for a in _auths(rng, "COMM", m, month, aseq):
                um.append(_comm_um_row(a, m))
                if a["status"] == "denied":
                    denied_auths.append((a, m, m["plan"]))
            for s in _scripts(rng, "COMM", ref, m, month, rseq):
                if month < date(2026, 1, 1):
                    cost_by_plan[m["plan"]] = cost_by_plan.get(m["plan"], 0.0) + s["plan_paid"]
                rx.append((f"RX{s['id']:09d}", m["id"], m["plan"], s["pharmacy"], s["prescriber"], s["ndc"], s["fill"],
                           s["days"], s["qty"], s["fill_no"], s["ingredient"], s["fee"], s["plan_paid"], s["member_paid"],
                           s["tier"], s["daw"], {"paid": "P", "rejected": "R", "reversed": "X"}[s["status"]], "PBM-LKV"))
            for g in _grievances(rng, "COMM", m, month, gseq):
                griev.append((f"GV{g['id']:07d}", m["id"], m["plan"],
                              {"access_to_care": "ACC", "quality_of_care": "QOC", "customer_service": "SVC",
                               "billing_premium": "BIL", "enrollment": "ENR", "pharmacy": "RX"}[g["cat"]],
                              "URG" if g["expedited"] else "STD", g["received"], g["resolved"],
                              "C" if g["resolved"] else "O", g["text"]))

    con.execute(f"""CREATE TABLE {S}.CLM_HDR (CLM_ID VARCHAR, MBR_ID VARCHAR, PLAN_CD VARCHAR, BILL_NPI VARCHAR,
        REND_NPI VARCHAR, CLM_TYP VARCHAR, BILL_TYP VARCHAR, POS_CD VARCHAR, ER_REV_IND VARCHAR, ADMIT_DT DATE,
        DISCH_DT DATE, DRG_CD VARCHAR, FROM_DT DATE, THRU_DT DATE, RCVD_DT DATE, PD_DT DATE, CLM_STS VARCHAR,
        PRIM_DX VARCHAR, CHRG_AMT DECIMAL(12,2), ALLOW_AMT DECIMAL(12,2), PAID_AMT DECIMAL(12,2), MBR_LIAB_AMT DECIMAL(12,2),
        PAR_IND VARCHAR, AUTH_NBR VARCHAR, DENY_GRP VARCHAR, AUTO_ADJ_IND VARCHAR)""")
    _insert(con, f"{S}.CLM_HDR", hdr)
    con.execute(f"""CREATE TABLE {S}.CLM_DTL (CLM_ID VARCHAR, LN_NO INTEGER, PROC_CD VARCHAR, MOD_CD VARCHAR,
        UNITS INTEGER, CHRG DECIMAL(12,2), ALLOW DECIMAL(12,2), PAID DECIMAL(12,2), LN_STS VARCHAR, CARC VARCHAR)""")
    _insert(con, f"{S}.CLM_DTL", dtl)
    con.execute(f"""CREATE TABLE {S}.RX_CLM (RX_ID VARCHAR, MBR_ID VARCHAR, PLAN_CD VARCHAR, PHARM_NPI VARCHAR,
        PRSCR_NPI VARCHAR, NDC VARCHAR, FILL_DT DATE, DAYS_SUP INTEGER, QTY DECIMAL(10,3), REFILL_NO INTEGER,
        INGR_COST DECIMAL(12,2), DISP_FEE DECIMAL(8,2), PLAN_PAID DECIMAL(12,2), MBR_PAID DECIMAL(10,2), TIER_NO INTEGER,
        DAW_CD VARCHAR, RX_STS VARCHAR, PBM_ID VARCHAR)""")
    _insert(con, f"{S}.RX_CLM", rx)
    con.execute(f"""CREATE TABLE {S}.UM_CASE (CASE_ID VARCHAR, MBR_ID VARCHAR, PLAN_CD VARCHAR, REQ_NPI VARCHAR,
        SVC_CD VARCHAR, DX_CD VARCHAR, PRIORITY VARCHAR, RCVD_TS TIMESTAMP, DCSN_TS TIMESTAMP, CASE_STS VARCHAR,
        DCSN_RSN VARCHAR, APPR_UNITS INTEGER, DLGT_CD VARCHAR)""")
    for row in um:
        row[3] = row[3] or rng.choice(ref["pcp"])
    _insert(con, f"{S}.UM_CASE", [tuple(r) for r in um])
    con.execute(f"""CREATE TABLE {S}.GRIEV (GRV_ID VARCHAR, MBR_ID VARCHAR, PLAN_CD VARCHAR, CAT_CD VARCHAR,
        PRIORITY VARCHAR, RCVD_DT DATE, RSLV_DT DATE, GRV_STS VARCHAR, GRV_TXT VARCHAR)""")
    _insert(con, f"{S}.GRIEV", griev)

    aseq2 = [0]
    con.execute(f"""CREATE TABLE {S}.APPEAL (APL_ID VARCHAR, MBR_ID VARCHAR, PLAN_CD VARCHAR, CLM_ID VARCHAR,
        CASE_ID VARCHAR, APL_LVL VARCHAR, PRIORITY VARCHAR, RCVD_DT DATE, DCSN_DT DATE, OUTCOME VARCHAR, RSN_TXT VARCHAR)""")
    _insert(con, f"{S}.APPEAL", [
        (f"AP{a['id']:07d}", a["member"]["id"], a["plan"], f"CLM{a['claim']:09d}" if a["claim"] else None,
         f"UM{a['auth']:08d}" if a["auth"] else None,
         {"internal_first_level": "1", "external_review": "EXT"}[a["level"]], "URG" if a["expedited"] else "STD",
         a["received"], a["decided"],
         {"overturned": "OVT", "partially_overturned": "POVT", "upheld": "UPH", "withdrawn": "WDR"}.get(a["outcome"]),
         a["reason"]) for a in _appeals(rng, "COMM", denied_auths, denied_claims, aseq2)])

    # --- accumulators (plan year 2025) from member cost sharing
    liab: dict[str, float] = {}
    for h in hdr:
        if h[12].year == 2025 and h[16] == "PD":
            liab[h[1]] = liab.get(h[1], 0.0) + float(h[21])
    con.execute(f"""CREATE TABLE {S}.ACCUM (ACC_ID VARCHAR, MBR_ID VARCHAR, PLAN_CD VARCHAR, PLN_YR INTEGER,
        ACC_TYP VARCHAR, MET_AMT DECIMAL(10,2), LMT_AMT DECIMAL(10,2))""")
    acc = []
    for m in members:
        if m["start"] <= date(2025, 12, 1) and (m["end"] is None or m["end"] >= date(2025, 1, 1)):
            ded, oop = plans[m["plan"]][7], plans[m["plan"]][8]
            spent = liab.get(m["id"], 0.0)
            acc += [(f"AC{m['id']}D", m["id"], m["plan"], 2025, "D", round(min(spent, ded), 2), ded),
                    (f"AC{m['id']}O", m["id"], m["plan"], 2025, "O", round(min(spent, oop), 2), oop)]
    _insert(con, f"{S}.ACCUM", acc)

    # --- premium: rates priced from 2024-25 experience at a target loss ratio per plan
    target_lr = {"C-IND-BRZ": 0.855, "C-IND-SLV": 0.87, "C-SG-GLD": 0.88, "C-SG-SLV": 0.865,
                 "C-LG-PPO": 0.895, "C-LG-HDHP": 0.875}
    rate = {p: round(cost_by_plan.get(p, 0.0) / max(mm_by_plan.get(p, 1), 1) / lr, 0) for p, lr in target_lr.items()}
    bills, counts = [], {}
    for m in members:
        if plans[m["plan"]][5] == "ASO":
            continue
        payer = m["grp"]["id"] if m["grp"] else m["sub"]
        for month in _enrolled_months(m["start"], m["end"]):
            counts[(payer, m["plan"], month, m["grp"] is None)] = counts.get((payer, m["plan"], month, m["grp"] is None), 0) + 1
    seg_prem: dict[tuple, float] = {}
    for (payer, plan, month, individual), n in sorted(counts.items(), key=lambda kv: (kv[0][2], kv[0][0], kv[0][1])):
        amt = round(rate[plan] * n, 2)
        paid_dt = month + timedelta(days=rng.randint(0, 25))
        u = rng.random()
        sts, paid = ("PD", amt) if u < 0.97 else (("PP", round(amt / 2, 2)) if u < 0.99 else ("WO", 0.0))
        if paid_dt > MERIDIAN_VALUATION:
            sts, paid, paid_dt = "OP", 0.0, None
        bills.append((f"PB{len(bills):08d}", None if individual else payer, payer if individual else None, plan,
                      month.strftime("%Y%m"), month - timedelta(days=15), amt, paid, paid_dt if paid else None, sts))
        seg = plans[plan][4]
        seg_prem[(seg, month)] = seg_prem.get((seg, month), 0.0) + amt
    con.execute(f"""CREATE TABLE {S}.PREM_BILL (BILL_ID VARCHAR, GRP_ID VARCHAR, MBR_ID VARCHAR, PLAN_CD VARCHAR,
        CVG_YYYYMM VARCHAR, BILL_DT DATE, BILL_AMT DECIMAL(12,2), PAID_AMT DECIMAL(12,2), PAID_DT DATE, BILL_STS VARCHAR)""")
    _insert(con, f"{S}.PREM_BILL", bills)
    con.execute(f"""CREATE TABLE {S}.QI_EXP (QI_ID VARCHAR, MKT_SEG VARCHAR, EXP_MO DATE, QIA_CD VARCHAR, AMT DECIMAL(12,2))""")
    qi = []
    for (seg, month), prem in sorted(seg_prem.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        for code, share in (("HO", 0.5), ("RA", 0.2), ("WL", 0.2), ("IT", 0.1)):
            qi.append((f"QI{len(qi):05d}", seg, month, code, round(prem * 0.0075 * share, 2)))
    _insert(con, f"{S}.QI_EXP", qi)


def _comm_um_row(a, m) -> list:
    status = {"approved": "A", "partially_approved": "PA", "denied": "D", "pended_for_info": "P",
              "in_review": "IR", "withdrawn": "W"}[a["status"]]
    dlgt = {"in_house": "INH", "radiology_benefit_manager": "RBM", "musculoskeletal_benefit_manager": "MSK",
            "oncology_pathway_vendor": "ONC"}[a["delegate"]]
    return [f"UM{a['id']:08d}", m["id"], m["plan"], None, a["code"], a["dx"], "URG" if a["expedited"] else "STD",
            a["received"], a["decided"], status, a["reason"], a["units"], dlgt]


def _formularies(con, rng, ref, schema, unit):
    """Formulary, tiers and drug placement for plan years 2025 and 2026."""
    if unit == "COMM":
        con.execute(f"""CREATE TABLE {schema}.FRMLY (FRMLY_ID VARCHAR, FRMLY_NM VARCHAR, PLN_YR INTEGER,
            PBM_ID VARCHAR, FRMLY_TYP VARCHAR)""")
        con.execute(f"""CREATE TABLE {schema}.FRMLY_TIER (TIER_ID VARCHAR, FRMLY_ID VARCHAR, TIER_NO INTEGER,
            TIER_TYP VARCHAR, COPAY DECIMAL(8,2), COINS_PCT INTEGER)""")
        con.execute(f"""CREATE TABLE {schema}.FRMLY_DRUG (FD_ID VARCHAR, FRMLY_ID VARCHAR, NDC VARCHAR, TIER_ID VARCHAR,
            PA_IND VARCHAR, ST_IND VARCHAR, QL_IND VARCHAR, EFF_DT DATE, TRM_DT DATE)""")
        tiers = [(1, "GEN", 10, None), (2, "PB", 40, None), (3, "NPB", 80, None), (4, "SPC", None, 25)]
        f_rows, t_rows, d_rows = [], [], []
        for yr in (2025, 2026):
            fid = f"CF-{yr}"
            f_rows.append((fid, f"Meridian Commercial Drug List {yr}", yr, "PBM-LKV", "CLOSED"))
            for no, typ, copay, coins in tiers:
                t_rows.append((f"{fid}-T{no}", fid, no, typ, copay, coins))
            for ndc, ms, spec in ref["all_drugs"]:
                no = 4 if spec == "Y" else 1 if ms == "G" else 2
                d_rows.append((f"FD{len(d_rows):05d}", fid, ndc, f"{fid}-T{no}", "Y" if no >= 3 else "N",
                               "Y" if no == 4 else "N", "Y" if no >= 2 else "N", date(yr, 1, 1), date(yr, 12, 31)))
        _insert(con, f"{schema}.FRMLY", f_rows)
        _insert(con, f"{schema}.FRMLY_TIER", t_rows)
        _insert(con, f"{schema}.FRMLY_DRUG", d_rows)
    else:
        con.execute(f"""CREATE TABLE {schema}.formulary (formulary_id VARCHAR, formulary_name VARCHAR,
            contract_year INTEGER, pbm_id VARCHAR)""")
        con.execute(f"""CREATE TABLE {schema}.formulary_tier (tier_key VARCHAR, formulary_id VARCHAR, tier INTEGER,
            tier_label VARCHAR, copay DECIMAL(8,2), coinsurance DECIMAL(4,2))""")
        con.execute(f"""CREATE TABLE {schema}.formulary_drug (row_id VARCHAR, formulary_id VARCHAR, ndc VARCHAR,
            tier INTEGER, pa BOOLEAN, step BOOLEAN, ql BOOLEAN, start_date DATE, end_date DATE)""")
        tiers = [(1, "Preferred Generic", 0, None), (2, "Generic", 10, None), (3, "Preferred Brand", 47, None),
                 (4, "Non-Preferred Drug", 100, None), (5, "Specialty", None, 0.25)]
        f_rows, t_rows, d_rows = [], [], []
        for yr in (2025, 2026):
            fid = f"PD-{yr}"
            f_rows.append((fid, f"Meridian Medicare Part D Formulary {yr}", yr, "PBM-BWP"))
            for no, label, copay, coins in tiers:
                t_rows.append((f"{fid}-{no}", fid, no, label, copay, coins))
            for ndc, ms, spec in ref["all_drugs"]:
                no = 5 if spec == "Y" else 1 if ms == "G" else 3
                d_rows.append((f"{fid}-{ndc}", fid, ndc, no, no >= 4, no == 5, no >= 3, date(yr, 1, 1), None))
        _insert(con, f"{schema}.formulary", f_rows)
        _insert(con, f"{schema}.formulary_tier", t_rows)
        _insert(con, f"{schema}.formulary_drug", d_rows)


# ------------------------------------------------------------------------ MA

_MA_PBPS = [  # pbp key, contract, pbp, name, plan type, snp, network, moop, member premium, share, rebate
    ("H5521-001-000", "H5521", "001", "Meridian Medicare Advantage HMO", "HMO", None, "MA-HMO", 4900, 0, 0.50, 105),
    ("H5521-002-000", "H5521", "002", "Meridian Medicare Dual Complete (HMO D-SNP)", "HMO", "D", "MA-HMO", 9250, 0, 0.15, 140),
    ("H7710-001-000", "H7710", "001", "Meridian Medicare Choice PPO", "PPO", None, "MA-PPO", 6700, 39, 0.35, 80),
]
_NORM = {2024: 1.045, 2025: 1.05, 2026: 1.03}      # synthetic normalization factors
_V28_SHIFT = 0.965                                 # V28 raw scores run lower than the blend


def _m_ma(con, rng, ref, target):
    S = "ma_src"
    con.execute(f"CREATE TABLE {S}.contract (contract_id VARCHAR, contract_name VARCHAR, contract_kind VARCHAR)")
    _insert(con, f"{S}.contract", [("H5521", "Meridian Health Plan, Inc. (HMO)", "HMO"),
                                    ("H7710", "Meridian Health Insurance Company (PPO)", "LPPO")])
    con.execute(f"""CREATE TABLE {S}.pbp (pbp_key VARCHAR, contract_id VARCHAR, pbp_id VARCHAR, segment_id VARCHAR,
        plan_year INTEGER, snp_code VARCHAR, mapd_flag BOOLEAN, pbp_name VARCHAR, plan_type VARCHAR, network_id VARCHAR,
        deductible DECIMAL(10,2), moop DECIMAL(10,2), formulary_id VARCHAR, monthly_premium DECIMAL(8,2),
        effective_date DATE, end_date DATE)""")
    _insert(con, f"{S}.pbp", [(p[0], p[1], p[2], "000", 2026, p[5], True, p[3], p[4], p[6], 0, p[7], "PD-2026", p[8],
                               date(2024, 1, 1), None) for p in _MA_PBPS])
    con.execute(f"CREATE TABLE {S}.network (network_id VARCHAR, network_name VARCHAR, network_kind VARCHAR)")
    _insert(con, f"{S}.network", [("MA-HMO", "Meridian Medicare HMO Network", "hmo"),
                                   ("MA-PPO", "Meridian Medicare PPO Network", "ppo")])
    all_prv = ref["prof_all"] + [n for v in ref["fac"].values() for n in v] + [ref["lakeshore"]]
    con.execute(f"""CREATE TABLE {S}.network_provider (row_id VARCHAR, npi VARCHAR, network_id VARCHAR,
        start_date DATE, end_date DATE)""")
    np_rows, network = [], {"MA-HMO": set(), "MA-PPO": set()}
    for npi in all_prv:
        for net, p in (("MA-HMO", 0.7), ("MA-PPO", 0.9)):
            if rng.random() < p or npi == ref["lakeshore"]:
                np_rows.append((f"NP{len(np_rows):06d}", npi, net, date(2022, 1, 1), None))
                network[net].add(npi)
    _insert(con, f"{S}.network_provider", np_rows)

    con.execute(f"""CREATE TABLE {S}.fee_schedule (fee_schedule_id VARCHAR, description VARCHAR, basis VARCHAR,
        base_year INTEGER, start_date DATE)""")
    _insert(con, f"{S}.fee_schedule", [("MA-MPFS-26", "Medicare physician fee schedule", "medicare_based", 2026, date(2026, 1, 1)),
                                        ("MA-IPPS-26", "Medicare inpatient (MS-DRG)", "drg_based", 2026, date(2026, 1, 1))])
    con.execute(f"""CREATE TABLE {S}.provider_contract (contract_id VARCHAR, npi VARCHAR, network_id VARCHAR,
        pay_method VARCHAR, fee_schedule_id VARCHAR, pct_medicare DECIMAL(5,3), start_date DATE, end_date DATE,
        status VARCHAR)""")
    pcs = [("MPC-LAKE", ref["lakeshore"], "MA-HMO", "CAP", None, None, date(2024, 1, 1), None, "ACTIVE")]
    for npi in [n for v in ref["fac"].values() for n in v] + rng.sample(ref["prof_all"], 120):
        fac = npi in ref["fac"]["282N00000X"]
        pcs.append((f"MPC{len(pcs):05d}", npi, "MA-PPO", "DRG" if fac else "PCT_MCR",
                    "MA-IPPS-26" if fac else "MA-MPFS-26", round(rng.uniform(0.98, 1.06), 3), date(2024, 1, 1), None,
                    "ACTIVE"))
    _insert(con, f"{S}.provider_contract", pcs)
    cap_pmpm = 165.0
    con.execute(f"""CREATE TABLE {S}.vbc_arrangement (arrangement_id VARCHAR, contract_id VARCHAR, npi VARCHAR,
        kind VARCHAR, cap_pmpm DECIMAL(8,2), savings_share DECIMAL(4,2), min_savings DECIMAL(4,3), quality_gate BOOLEAN,
        start_date DATE, end_date DATE)""")
    _insert(con, f"{S}.vbc_arrangement", [
        ("MVB-LAKE", "MPC-LAKE", ref["lakeshore"], "primary_care_capitation", cap_pmpm, None, None, True,
         date(2024, 1, 1), None),
        ("MVB-P4P", pcs[1][0], pcs[1][1], "pay_for_performance", None, None, None, True, date(2025, 1, 1), None)])
    _formularies(con, rng, ref, S, "MA")

    # Star Ratings reference and results (contract level).
    measures = [("BCS", "Breast Cancer Screening", "HEDIS", 1, True, 0.74), ("COL", "Colorectal Cancer Screening", "HEDIS", 1, True, 0.71),
                ("CBP", "Controlling High Blood Pressure", "HEDIS", 3, True, 0.70),
                ("GSD", "Glycemic Status Assessment for Patients with Diabetes", "HEDIS", 3, True, 0.77),
                ("PCR", "Plan All-Cause Readmissions", "HEDIS", 3, False, 0.10),
                ("SPD", "Statin Use in Persons with Diabetes", "PDE", 1, True, 0.86),
                ("MAD", "Medication Adherence for Diabetes Medications", "PDE", 3, True, 0.87),
                ("MAH", "Medication Adherence for Hypertension (RAS Antagonists)", "PDE", 3, True, 0.88),
                ("GNC", "Getting Needed Care", "CAHPS", 2, True, 0.80), ("RHP", "Rating of Health Plan", "CAHPS", 2, True, 0.86),
                ("IPH", "Improving or Maintaining Physical Health", "HOS", 3, True, 0.68),
                ("CTM", "Complaints about the Health Plan", "ADMIN", 2, False, 0.004)]
    con.execute(f"""CREATE TABLE {S}.star_measure (measure_key VARCHAR, measure_name VARCHAR, data_source VARCHAR,
        weight DECIMAL(4,1), higher_better BOOLEAN)""")
    _insert(con, f"{S}.star_measure", [m[:5] for m in measures])
    con.execute(f"""CREATE TABLE {S}.star_result (result_id VARCHAR, contract_id VARCHAR, measure_key VARCHAR,
        measurement_year INTEGER, star_year INTEGER, num INTEGER, den INTEGER, rate DECIMAL(8,4), stars INTEGER)""")
    sr = []
    for contract, shift in (("H5521", 0.02), ("H7710", -0.01)):
        for my in (2024, 2025):
            for key, _, _, _, higher, base in measures:
                den = rng.randint(400, 3500)
                r = max(0.0, base + (shift if higher else -shift / 10) + rng.gauss(0, 0.02))
                num = round(den * r)
                rel = (r - base) / max(base, 0.01) * (1 if higher else -1)
                stars = max(1, min(5, round(3.6 + rel * 12)))
                sr.append((f"SR-{contract}-{key}-{my}", contract, key, my, my + 2, num, den, round(num / den, 4), stars))
    _insert(con, f"{S}.star_result", sr)

    # Risk adjustment reference: V24 -> V28 phase-in (PY2024 67/33, PY2025 33/67, PY2026 V28 only).
    con.execute(f"""CREATE TABLE {S}.ra_model (model_key VARCHAR, model_ver VARCHAR, payment_year INTEGER,
        blend_pct DECIMAL(5,2), norm_factor DECIMAL(6,3), coding_adj DECIMAL(5,3))""")
    _insert(con, f"{S}.ra_model", [
        ("PY2024-V24", "V24", 2024, 67, _NORM[2024], 0.059), ("PY2024-V28", "V28", 2024, 33, _NORM[2024], 0.059),
        ("PY2025-V24", "V24", 2025, 33, _NORM[2025], 0.059), ("PY2025-V28", "V28", 2025, 67, _NORM[2025], 0.059),
        ("PY2026-V28", "V28", 2026, 100, _NORM[2026], 0.059)])
    con.execute(f"""CREATE TABLE {S}.hcc_ref (hcc_key VARCHAR, model_ver VARCHAR, hcc_nbr INTEGER, hcc_label VARCHAR,
        factor DECIMAL(6,3))""")
    hcc_rows = []
    for v24, v28 in _DX_HCC.values():
        for ver, n in (("V24", v24), ("V28", v28)):
            key = f"{ver}-{n}"
            if key not in {h[0] for h in hcc_rows}:
                hcc_rows.append((key, ver, n, _HCC_LABEL[n], round(rng.uniform(0.15, 0.6), 3)))
    _insert(con, f"{S}.hcc_ref", hcc_rows)

    # --- beneficiaries and elections
    members = []
    pbp_keys = [p[0] for p in _MA_PBPS]
    for i in range(target):
        dual = rng.choices(["N", "F", "P"], [76, 18, 6])[0]
        pbp = "H5521-002-000" if dual == "F" and rng.random() < 0.75 else \
            rng.choices([pbp_keys[0], pbp_keys[2]], [50, 35])[0]
        age = min(99, 65 + int(rng.expovariate(1 / 9)))
        esrd = rng.random() < 0.01
        inst = rng.random() < 0.025
        u = rng.random()
        if u < 0.78:
            start = M_START
        elif u < 0.9:
            start = date(2025, 1, 1)
        elif u < 0.97:
            start = date(2026, 1, 1)
        else:
            start = rng.choice(M_MONTHS[1:])
        end = None
        v = rng.random()
        if v < 0.06:
            end = date(2025, 12, 31) if start <= date(2025, 12, 1) else None
        elif v < 0.12:
            later = [mo for mo in M_MONTHS if mo > start]
            end = _month_end(rng.choice(later)) if later else None
        new_enrollee = age <= 66 and start > M_START
        base = 2.3 if inst else 0.55 if new_enrollee else {"N": 0.98, "P": 1.12, "F": 1.38}[dual]
        base *= 1 + (age - 75) * 0.012
        members.append({"id": f"B{i + 1:07d}", "pbp": pbp, "plan": pbp, "age": age, "dual": dual, "esrd": esrd,
                        "institutional": inst, "start": start, "end": end, "base": base * _lognorm(rng, 0.55),
                        "new": new_enrollee, "lis": 100 if dual == "F" else rng.choice([25, 50, 75, 100]) if dual == "P"
                        else (25 if rng.random() < 0.03 else 0),
                        "capitated": pbp.startswith("H5521") and rng.random() < 0.45, "liab": 0.07})
    for m in members:
        m["risk"] = m["base"] * _lognorm(rng, 0.35) / 1.06
        m["rx_risk"] = m["base"] ** 0.6 * _lognorm(rng, 0.6)
    con.execute(f"""CREATE TABLE {S}.beneficiary (bene_id VARCHAR, mbi VARCHAR, card_id VARCHAR, first_name VARCHAR,
        last_name VARCHAR, birth_date DATE, sex_code VARCHAR, state VARCHAR, zip5 VARCHAR, care_program VARCHAR)""")
    _insert(con, f"{S}.beneficiary", [
        (m["id"], _mbi(rng), f"MHA{int(m['id'][1:]):08d}", rng.choice(FIRST), rng.choice(LAST),
         date(2026 - m["age"], rng.randint(1, 12), rng.randint(1, 28)), rng.choices(["1", "2", "0"], [45, 54, 1])[0],
         rng.choice(M_STATES), f"{rng.randint(43000, 54999)}",
         rng.choices([None, "diabetes", "heart_failure", "copd", "complex_case"], [80, 8, 5, 4, 3])[0]) for m in members])
    con.execute(f"""CREATE TABLE {S}.enrollment (enrollment_id VARCHAR, bene_id VARCHAR, pbp_key VARCHAR,
        start_date DATE, end_date DATE, dual_code VARCHAR, lis_pct INTEGER, esrd BOOLEAN, institutional BOOLEAN,
        election_type VARCHAR)""")
    _insert(con, f"{S}.enrollment", [
        (f"EN{i:07d}", m["id"], m["pbp"], m["start"], m["end"], m["dual"], m["lis"], m["esrd"], m["institutional"],
         "AEP" if m["start"].month == 1 else "IEP" if m["new"] else "SEP") for i, m in enumerate(members)])

    # --- risk scores per payment year (latest sweep on file at valuation)
    scores = []
    pay_score: dict[tuple, float] = {}
    for m in members:
        for py in (2024, 2025, 2026):
            months = [mo for mo in _enrolled_months(m["start"], m["end"]) if mo.year == py]
            if not months:
                continue
            seg = ("ESRD" if m["esrd"] else "INS" if m["institutional"] else "NE" if m["new"] and py == m["start"].year
                   else {"N": "CND", "P": "CPD", "F": "CFD"}[m["dual"]])
            ver = "ESRD" if m["esrd"] else "BLND" if py < 2026 else "V28"
            raw = m["base"] * _lognorm(rng, 0.12) * (_V28_SHIFT if ver == "V28" else 1.0) * (1 + (py - 2024) * 0.02)
            norm = raw / _NORM[py]
            pay = norm * (1 - 0.059)
            pay_score[(m["id"], py)] = pay
            scores.append((f"RS-{m['id']}-{py}", m["id"], m["pbp"], py, ver, {2024: "F", 2025: "M", 2026: "I"}[py], seg,
                           round(raw, 3), round(norm, 3), round(pay, 3)))
    con.execute(f"""CREATE TABLE {S}.risk_score (score_id VARCHAR, bene_id VARCHAR, pbp_key VARCHAR, payment_year INTEGER,
        model_ver VARCHAR, run_type VARCHAR, segment_code VARCHAR, raw_score DECIMAL(6,3), norm_score DECIMAL(6,3),
        pay_score DECIMAL(6,3))""")
    _insert(con, f"{S}.risk_score", scores)

    # --- claims, auths, scripts, grievances, capitation, diagnosis submissions
    claims, lines, pde, auths, griev, caps, dx_subs = [], [], [], [], [], [], []
    denied_auths, denied_claims = [], []
    cseq, aseq, rseq, gseq = [0], [0], [0], [0]
    cost, mm = 0.0, 0
    status_code = {"paid": "APPROVED", "denied": "DENIED", "pended": "PENDING", "voided": "VOID"}
    deny_code = {"medical_necessity": "MED_NEC", "no_prior_authorization": "NO_AUTH", "not_covered": "NOT_COVERED",
                 "eligibility": "ELIGIBILITY", "duplicate": "DUPLICATE", "timely_filing": "TIMELY_FILING",
                 "coding_billing_error": "CODING", "coordination_of_benefits": "COB",
                 "clinical_validation": "CLINICAL_VALIDATION"}
    delegate_code = {"in_house": "IN_HOUSE", "radiology_benefit_manager": "RAD_BM",
                     "musculoskeletal_benefit_manager": "MSK_BM", "oncology_pathway_vendor": "ONC_PATHWAYS"}
    outcome_code = {"approved": "APPROVED", "partially_approved": "PARTIAL", "denied": "ADVERSE",
                    "pended_for_info": "PENDING_INFO", "in_review": "IN_REVIEW", "withdrawn": "WITHDRAWN"}

    def auth_row(a, m):
        return (f"PA{a['id']:08d}", m["id"], m["pbp"], rng.choice(ref["pcp"]), a["code"], a["dx"],
                "EXPEDITED" if a["expedited"] else "STANDARD", a["received"], a["decided"], outcome_code[a["status"]],
                a["reason"], a["units"], delegate_code[a["delegate"]])

    for m in members:
        net = network["MA-HMO" if m["pbp"].startswith("H5521") else "MA-PPO"]
        for month in _enrolled_months(m["start"], m["end"]):
            experience = month < date(2026, 1, 1)
            mm += experience
            if m["capitated"]:
                caps.append((f"CP{len(caps):08d}", "MVB-LAKE", ref["lakeshore"], m["id"], m["pbp"], month, cap_pmpm))
                cost += cap_pmpm if experience else 0.0
            for c in _medical(rng, "MA", ref, m, month, net, cseq):
                if c["cat"] == "inpatient" and rng.random() < 0.35:
                    aseq[0] += 1
                    a = _auth_record(rng, "MA", aseq[0], rng.choice(["27447", "63030", "99223"]), "in_house", c["dx"],
                                     datetime.combine(c["from"] - timedelta(days=rng.randint(4, 12)),
                                                      datetime.min.time()) + timedelta(hours=10), "approved")
                    a["expedited"] = False
                    a["decided"] = a["received"] + timedelta(hours=rng.uniform(6, 90))
                    a["reason"] = "Meets medical necessity criteria"
                    auths.append(auth_row(a, m))
                    c["auth"] = a["id"]
                if experience:
                    cost += c["paid"] + ((c["allowed"] * (1 - m["liab"])) if c["status"] == "pended" else 0.0)
                cid = f"MC{c['id']:09d}"
                inst = c["cat"] in ("inpatient", "emergency", "outpatient") and c["bill"] not in ref["fac"]["261QA1903X"]
                snf_hh = c["bill"] in ref["fac"]["314000000X"] or c["bill"] in ref["fac"]["251E00000X"]
                claims.append((cid, m["id"], m["pbp"], c["bill"], c["rend"], "837I" if inst else "837P",
                               ("0111" if c["cat"] == "inpatient" else "0211" if snf_hh else "0131") if inst else None,
                               None if inst else {"professional": "11", "ancillary": "81", "outpatient": "24"}.get(c["cat"], "22"),
                               {"inpatient": "IP", "emergency": "ER", "outpatient": "OP", "professional": "PROF",
                                "ancillary": "ANC"}[c["cat"]], c["admit"], c["disch"], c["drg"], c["from"], c["thru"],
                               c["received"], c["final"], status_code[c["status"]], c["dx"], c["billed"], c["allowed"],
                               c["paid"], c["liab"], c["in_network"], f"PA{c['auth']:08d}" if c["auth"] else None,
                               deny_code.get(c["deny"]), c["auto"], c["encounter"]))
                for ln in _lines(rng, c):
                    lines.append((cid, *ln[:7], ln[7].upper(), ln[8]))
                if c["status"] == "denied":
                    denied_claims.append((c, m, m["pbp"]))
                if c["status"] in ("paid", "denied", "pended"):
                    sub = c["final"] or c["received"] + timedelta(days=20)
                    if sub <= MERIDIAN_VALUATION:
                        hcc = _DX_HCC.get(c["dx"])
                        py = c["from"].year + 1
                        dx_subs.append((f"DX{len(dx_subs):08d}", m["id"], c["rend"], c["dx"], c["from"], c["thru"], "EDS",
                                        sub + timedelta(days=rng.randint(5, 30)) if sub + timedelta(days=35) <= MERIDIAN_VALUATION else sub,
                                        "ACC" if rng.random() < 0.97 else "REJ", c["cat"] != "ancillary", cid, py,
                                        (f"V24-{hcc[0]}" if py == 2024 else f"V28-{hcc[1]}") if hcc else None))
            for a in _auths(rng, "MA", m, month, aseq):
                auths.append(auth_row(a, m))
                if a["status"] == "denied":
                    denied_auths.append((a, m, m["pbp"]))
            for s in _scripts(rng, "MA", ref, m, month, rseq):
                cost += s["plan_paid"] if experience else 0.0
                pde.append((f"PDE{s['id']:09d}", m["id"], m["pbp"], s["pharmacy"], s["prescriber"], s["ndc"], s["fill"],
                            s["days"], s["qty"], s["fill_no"], s["ingredient"], s["fee"], s["plan_paid"],
                            s["member_paid"], s["tier"], s["daw"], {"paid": "PAID", "rejected": "REJ", "reversed": "REV"}[s["status"]],
                            "PBM-BWP"))
            for g in _grievances(rng, "MA", m, month, gseq):
                griev.append((f"MG{g['id']:07d}", m["id"], m["pbp"],
                              {"access_to_care": "ACCESS", "quality_of_care": "QOC", "customer_service": "CS",
                               "billing_premium": "BILLING", "enrollment": "ENROLL", "pharmacy": "RX"}[g["cat"]],
                              g["expedited"], g["received"], g["resolved"], g["text"]))
        # Chart-review adds for members with a risk-adjusting condition.
        if rng.random() < 0.04:
            dx = rng.choice(list(_DX_HCC))
            svc = rng.choice(M_MONTHS[:18]) + timedelta(days=rng.randint(0, 27))
            if m["start"] <= svc:
                py = svc.year + 1
                dx_subs.append((f"DX{len(dx_subs):08d}", m["id"], rng.choice(ref["pcp"]), dx, svc, svc, "CRA",
                                min(svc + timedelta(days=rng.randint(60, 200)), MERIDIAN_VALUATION), "ACC", True, None,
                                py, f"V24-{_DX_HCC[dx][0]}" if py == 2024 else f"V28-{_DX_HCC[dx][1]}"))

    con.execute(f"""CREATE TABLE {S}.claim (claim_id VARCHAR, bene_id VARCHAR, pbp_key VARCHAR, billing_npi VARCHAR,
        rendering_npi VARCHAR, form_type VARCHAR, type_of_bill VARCHAR, pos VARCHAR, setting VARCHAR, admit_date DATE,
        discharge_date DATE, ms_drg VARCHAR, from_date DATE, thru_date DATE, received_date DATE, finalized_date DATE,
        status VARCHAR, primary_icd10 VARCHAR, billed DECIMAL(12,2), allowed DECIMAL(12,2), plan_paid DECIMAL(12,2),
        bene_liability DECIMAL(12,2), in_network BOOLEAN, auth_id VARCHAR, denial_group VARCHAR,
        auto_adjudicated BOOLEAN, encounter BOOLEAN)""")
    _insert(con, f"{S}.claim", claims)
    con.execute(f"""CREATE TABLE {S}.claim_line (claim_id VARCHAR, line_seq INTEGER, hcpcs VARCHAR, modifier1 VARCHAR,
        units INTEGER, billed DECIMAL(12,2), allowed DECIMAL(12,2), paid DECIMAL(12,2), line_status VARCHAR, carc VARCHAR)""")
    _insert(con, f"{S}.claim_line", lines)
    con.execute(f"""CREATE TABLE {S}.rx_pde (pde_id VARCHAR, bene_id VARCHAR, pbp_key VARCHAR, pharmacy_npi VARCHAR,
        prescriber_npi VARCHAR, ndc VARCHAR, fill_date DATE, days_supply INTEGER, quantity DECIMAL(10,3),
        fill_number INTEGER, ingredient_cost DECIMAL(12,2), dispensing_fee DECIMAL(8,2), plan_paid DECIMAL(12,2),
        patient_pay DECIMAL(10,2), tier INTEGER, daw VARCHAR, status VARCHAR, pbm_id VARCHAR)""")
    _insert(con, f"{S}.rx_pde", pde)
    con.execute(f"""CREATE TABLE {S}.auth (auth_id VARCHAR, bene_id VARCHAR, pbp_key VARCHAR, requesting_npi VARCHAR,
        service_code VARCHAR, icd10 VARCHAR, review_type VARCHAR, received_at TIMESTAMP, decided_at TIMESTAMP,
        outcome VARCHAR, outcome_reason VARCHAR, approved_units INTEGER, delegate VARCHAR)""")
    _insert(con, f"{S}.auth", auths)
    con.execute(f"""CREATE TABLE {S}.grievance (grievance_id VARCHAR, bene_id VARCHAR, pbp_key VARCHAR, category VARCHAR,
        expedited BOOLEAN, received_date DATE, resolved_date DATE, summary VARCHAR)""")
    _insert(con, f"{S}.grievance", griev)
    aseq2 = [0]
    con.execute(f"""CREATE TABLE {S}.appeal (appeal_id VARCHAR, bene_id VARCHAR, pbp_key VARCHAR, claim_id VARCHAR,
        auth_id VARCHAR, appeal_level VARCHAR, expedited BOOLEAN, received_date DATE, decision_date DATE,
        member_outcome VARCHAR, rationale VARCHAR)""")
    _insert(con, f"{S}.appeal", [
        (f"MAP{a['id']:07d}", a["member"]["id"], a["plan"], f"MC{a['claim']:09d}" if a["claim"] else None,
         f"PA{a['auth']:08d}" if a["auth"] else None,
         {"internal_first_level": "RECON", "independent_review_entity": "IRE"}[a["level"]], a["expedited"],
         a["received"], a["decided"],
         {"overturned": "FAVORABLE", "partially_overturned": "PARTIALLY_FAVORABLE", "upheld": "UNFAVORABLE",
          "withdrawn": "WITHDRAWN"}.get(a["outcome"]), a["reason"])
        for a in _appeals(rng, "MA", denied_auths, denied_claims, aseq2)])
    con.execute(f"""CREATE TABLE {S}.cap_payment (cap_id VARCHAR, arrangement_id VARCHAR, npi VARCHAR, bene_id VARCHAR,
        pbp_key VARCHAR, cap_month DATE, amount DECIMAL(10,2))""")
    _insert(con, f"{S}.cap_payment", caps)
    con.execute(f"""CREATE TABLE {S}.dx_submission (submission_id VARCHAR, bene_id VARCHAR, rendering_npi VARCHAR,
        icd10 VARCHAR, from_date DATE, thru_date DATE, source_type VARCHAR, submitted_date DATE, status VARCHAR,
        ra_eligible BOOLEAN, claim_id VARCHAR, payment_year INTEGER, hcc_key VARCHAR)""")
    _insert(con, f"{S}.dx_submission", dx_subs)

    # --- CMS revenue (MMR): Part C priced from 2024-25 experience at a target loss ratio
    part_d, target_lr = 112.0, 0.875
    rebate = {p[0]: p[10] for p in _MA_PBPS}
    exp_pay = [pay_score[(m["id"], mo.year)] for m in members for mo in _enrolled_months(m["start"], m["end"])
               if mo < date(2026, 1, 1)]
    avg_rebate = sum(rebate[m["pbp"]] for m in members for mo in _enrolled_months(m["start"], m["end"])
                     if mo < date(2026, 1, 1)) / max(mm, 1)
    base_c = (cost / max(mm, 1) / target_lr - part_d - avg_rebate) / (sum(exp_pay) / len(exp_pay))
    mmr, prem, revenue = [], [], {}
    for m in members:
        for month in _enrolled_months(m["start"], m["end"]):
            score = pay_score[(m["id"], month.year)]
            c_amt = round(base_c * score, 2)
            d_amt = round(part_d * rng.uniform(0.9, 1.1), 2)
            total = round(c_amt + d_amt + rebate[m["pbp"]], 2)
            mmr.append((f"MMR{len(mmr):08d}", m["id"], m["pbp"][:5], m["pbp"], month, round(score, 3), c_amt, d_amt,
                        rebate[m["pbp"]], total, False, None))
            revenue[month] = revenue.get(month, 0.0) + total
            if rng.random() < 0.012:      # retroactive correction posted on a later report
                adj = round(rng.uniform(-120, 120), 2)
                mmr.append((f"MMR{len(mmr):08d}", m["id"], m["pbp"][:5], m["pbp"], month, round(score, 3), adj, 0.0, 0.0,
                            adj, True, rng.choice(["25", "26", "07"])))
                revenue[month] += adj
            premium = next(p[8] for p in _MA_PBPS if p[0] == m["pbp"])
            if premium:
                paid_dt = month + timedelta(days=rng.randint(0, 20))
                ok = paid_dt <= MERIDIAN_VALUATION and rng.random() < 0.985
                prem.append((f"MP{len(prem):08d}", m["id"], m["pbp"], month, float(premium), float(premium) if ok else 0.0,
                             paid_dt if ok else None, "PAID" if ok else "OPEN"))
    con.execute(f"""CREATE TABLE {S}.mmr (mmr_id VARCHAR, bene_id VARCHAR, contract_id VARCHAR, pbp_key VARCHAR,
        payment_month DATE, risk_score DECIMAL(6,3), part_c DECIMAL(10,2), part_d DECIMAL(10,2), rebate DECIMAL(10,2),
        total DECIMAL(10,2), adjustment BOOLEAN, adj_reason VARCHAR)""")
    _insert(con, f"{S}.mmr", mmr)
    con.execute(f"""CREATE TABLE {S}.member_premium (invoice_id VARCHAR, bene_id VARCHAR, pbp_key VARCHAR,
        premium_month DATE, billed DECIMAL(8,2), collected DECIMAL(8,2), collected_date DATE, status VARCHAR)""")
    _insert(con, f"{S}.member_premium", prem)
    con.execute(f"""CREATE TABLE {S}.qi_expense (qi_id VARCHAR, expense_month DATE, activity VARCHAR,
        amount DECIMAL(12,2))""")
    _insert(con, f"{S}.qi_expense", [
        (f"MQ{i:05d}-{k}", month, act, round(rev * 0.007 * share, 2))
        for i, (month, rev) in enumerate(sorted(revenue.items()))
        for k, (act, share) in enumerate((("improve_health_outcomes", 0.45), ("prevent_readmissions", 0.25),
                                          ("patient_safety", 0.1), ("health_it", 0.2)))])

    # --- bids (contract years 2025-2027; the 2027 bid went in on the first Monday of June 2026)
    con.execute(f"""CREATE TABLE {S}.bid (bid_id VARCHAR, contract_id VARCHAR, pbp_key VARCHAR, contract_year INTEGER,
        proj_member_months INTEGER, bid_pmpm DECIMAL(10,2), benchmark_pmpm DECIMAL(10,2), rebate_pmpm DECIMAL(10,2),
        proj_mlr DECIMAL(5,3), submitted_date DATE)""")
    bids = []
    for p in _MA_PBPS:
        for cy, sub in ((2025, date(2024, 6, 3)), (2026, date(2025, 6, 2)), (2027, date(2026, 6, 1))):
            bench = round(base_c * rng.uniform(1.0, 1.06), 2)
            bid_amt = round(bench - p[10] / 0.65, 2)
            bids.append((f"BID-{p[0]}-{cy}", p[1], p[0], cy, int(target * p[9] * 12 * rng.uniform(0.95, 1.1)), bid_amt,
                         bench, float(p[10]), round(rng.uniform(0.86, 0.89), 3), sub))
    _insert(con, f"{S}.bid", bids)
