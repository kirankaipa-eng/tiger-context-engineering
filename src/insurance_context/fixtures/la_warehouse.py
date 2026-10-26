"""Deterministic synthetic warehouse for the life & annuity reference client.

`build_northwind` - Northwind Life & Annuity (fictional): individual life on a
legacy policy administration system and deferred annuities on a newer platform,
plus an enterprise reference hub. See its docstring for the calibration targets
and the signals planted for the drift loop and the curator.

Everything here is synthetic. Product, company, producer, marketing-organization
and person names are invented; index returns are shaped like recent market
history but are not quotes; mortality rates are smooth Gompertz-Makeham curves
shaped like the published tables, not the tables themselves.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta
from pathlib import Path

import duckdb

from .pc_warehouse import _insert

NORTHWIND_VALUATION = date(2026, 6, 30)
NW_START = date(2019, 1, 1)
OPEN = "00000000"                 # LIFE's "no date" sentinel in CHAR(8) date fields

_FIRST = ["Avery", "Jordan", "Morgan", "Riley", "Casey", "Quinn", "Harper", "Rowan", "Elliot", "Sawyer",
          "Marisol", "Tobias", "Ingrid", "Desmond", "Priyanka", "Mateo", "Yuki", "Oluwaseun", "Beatrix", "Callum",
          "Fern", "Ansel", "Delphine", "Hollis", "Imani", "Jasper", "Kalinda", "Lorcan", "Maren", "Nikolai"]
_LAST = ["Ashdown", "Brightwater", "Calloway", "Dunmore", "Everly", "Fairbank", "Glenholt", "Harrowgate",
         "Ivers", "Jessop", "Kittredge", "Lindqvist", "Marchetti", "Northcote", "Okonkwo", "Pellham", "Quarles",
         "Ravensworth", "Selwyn", "Thorne", "Underhill", "Vasquez-Bell", "Whitlock", "Yardley", "Zielinski"]
_STATES = [("IA", 10), ("IL", 12), ("MN", 8), ("WI", 7), ("MO", 7), ("NE", 4), ("KS", 4), ("OH", 9),
           ("MI", 7), ("IN", 6), ("TX", 10), ("FL", 9), ("NY", 7)]
_IMOS = ["Prairie Gate Marketing", "Lakemont Financial Group", "Copperline Brokerage", "Bluewater Advisors Network",
         "Redfield Insurance Marketing", "Ironwood BGA", "Clearbrook Partners", "Tallgrass Financial"]
_CAUSES = [("CARDIO", 32), ("CANCER", 30), ("RESP", 8), ("ACCIDENT", 9), ("NEURO", 8), ("OTHER", 13)]

# ------------------------------------------------------------------ products
# Life plans (LIFE PLANDEF): code, name, type code, effective, surrender-charge years.
_LIFE_PLANS = [("T10N", "Northwind Term 10", "T", date(2015, 1, 1), 0),
               ("T20N", "Northwind Term 20", "T", date(2015, 1, 1), 0),
               ("T30N", "Northwind Term 30", "T", date(2015, 1, 1), 0),
               ("UL01", "Northwind Guaranteed UL", "U", date(2016, 7, 1), 10),
               ("IUL1", "Northwind IUL Accumulator", "I", date(2017, 1, 1), 10),
               ("IUL2", "Northwind IUL Protector", "I", date(2023, 1, 1), 10),
               ("WL01", "Northwind Whole Life", "W", date(2014, 1, 1), 0)]
# Annuity products (ANN product): id, name, kind, surrender years, launch.
_ANN_PRODUCTS = [("MYGA3", "Northwind Secure Rate 3", "MYGA", 3, date(2018, 1, 1)),
                 ("MYGA5", "Northwind Secure Rate 5", "MYGA", 5, date(2018, 1, 1)),
                 ("FIA7", "Northwind Index Advantage 7", "FIA", 7, date(2017, 6, 1)),
                 ("FIA10", "Northwind Index Advantage 10", "FIA", 10, date(2017, 6, 1)),
                 ("RILA6", "Northwind Shield 6", "RILA", 6, date(2018, 9, 1))]
_SC = {"MYGA3": [7, 6, 5], "MYGA5": [8, 7, 6, 5, 4], "FIA7": [9, 8, 7, 6, 5, 4, 3],
       "FIA10": [10, 9, 8, 7, 6, 5, 4, 3, 2, 1], "RILA6": [8, 7, 6, 5, 4, 3]}
_LIFE_SC = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]

# Rate sheets every January and July. Indexed caps fall from the 2023 peak.
_PERIODS = [date(y, m, 1) for y in range(2019, 2027) for m in (1, 7) if date(y, m, 1) <= NORTHWIND_VALUATION]
_IUL_CAP = [10.0, 9.75, 9.5, 9.0, 8.75, 8.75, 9.0, 9.5, 10.25, 10.5, 10.25, 10.0, 9.75, 9.5, 9.25]
_FIA_CAP = [5.5, 5.25, 4.75, 4.25, 4.0, 4.0, 4.5, 6.0, 9.75, 10.0, 9.75, 9.5, 9.0, 8.75, 8.25]
_FIA_PAR = [100, 100, 95, 90, 90, 95, 105, 120, 140, 145, 140, 135, 130, 128, 125]
_FIA_FIXED = [2.0, 1.9, 1.75, 1.5, 1.5, 1.5, 2.0, 2.75, 3.75, 4.0, 4.0, 3.9, 3.85, 3.8, 3.75]
_MYGA3 = [3.0, 2.8, 2.4, 2.1, 2.0, 2.1, 2.8, 4.0, 5.25, 5.5, 5.5, 5.3, 5.1, 4.9, 4.75]
_RILA_CAP = [11.0, 10.5, 9.5, 9.0, 9.5, 10.0, 12.0, 15.0, 18.0, 19.0, 17.5, 17.0, 16.0, 15.0, 14.5]
TRIGGER_START = date(2026, 3, 1)      # planted: performance-trigger strategy on the FIAs
ROP_START = date(2026, 1, 1)          # planted: return-of-premium rider on term
PBR_START = date(2026, 1, 1)          # planted: VM-22 PBR reserves on new fixed annuities

# Calendar-year price returns of a synthetic large-cap index (shaped like recent history).
_INDEX_YEAR = {2017: 0.19, 2018: -0.06, 2019: 0.29, 2020: 0.16, 2021: 0.27, 2022: -0.19, 2023: 0.24,
               2024: 0.23, 2025: 0.16, 2026: 0.10}


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def _add_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:          # Feb 29
        return d.replace(year=d.year + n, day=28)


def _ymd(d: date | None) -> str:
    return d.strftime("%Y%m%d") if d else OPEN


def _cents(x: float) -> int:
    return int(round(x * 100))


def _pick(rng, weighted):
    return rng.choices([w[0] for w in weighted], [w[1] for w in weighted])[0]


def _rate_period(d: date) -> int:
    i = 0
    for k, p in enumerate(_PERIODS):
        if p <= d:
            i = k
    return i


def _index_level(d: date) -> float:
    """Synthetic index level, compounding each calendar year's return evenly by day."""
    level = 1.0
    for y in range(2017, d.year):
        level *= 1 + _INDEX_YEAR[y]
    frac = (d - date(d.year, 1, 1)).days / 365.0
    return level * (1 + _INDEX_YEAR[d.year]) ** frac


def _index_return(end: date) -> float:
    return _index_level(end) / _index_level(_add_years(end, -1)) - 1


def _q_vbt(age: int, sex: str, smoker: bool) -> float:
    """Synthetic ultimate mortality shaped like an experience table (male nonsmoker base)."""
    q = 0.0004 + 0.000035 * math.exp(0.092 * age)
    q *= 0.72 if sex == "F" else 1.0
    q *= 2.2 if smoker else 1.0
    return min(q, 0.5)


def _q_iam(age: int, sex: str) -> float:
    q = 0.0003 * math.exp(0.095 * (age - 30))
    return min(q * (0.8 if sex == "F" else 1.0), 0.5)


_CLASS_MORT = {"PP": 0.62, "PF": 0.78, "SP": 0.95, "ST": 1.15, "SS": 1.0}   # SS: x (1 + 25% per table)


def _select_factor(duration: int) -> float:
    """Underwriting selection wears off: low mortality in the first policy years."""
    return 0.6 if duration < 2 else (0.85 if duration < 5 else 1.0)


def build_northwind(path: Path, seed: int = 31, life_apps: int = 27000, ann_apps: int = 13500) -> Path:
    """Northwind Life & Annuity: a fictional individual life and annuity carrier.

        ref      enterprise reference hub (hosted on the LIFE platform): org units, legal
                 entities, the product catalog, the producer master, mortality tables
        lifepas  LIFE: legacy individual-life policy administration system (UPPER_CASE
                 mainframe names, CHAR(8) YYYYMMDD dates with 00000000 for none,
                 amounts in cents, rates in basis points, one- and two-letter codes)
        ann_src  ANN: deferred annuity platform (snake_case, real dates, fractions,
                 its own codes)

    Policies and contracts issued 2019-01 .. 2026-06, valued at NORTHWIND_VALUATION.
    Calibrated to industry-typical levels: ~22k life policies (term ~55%, IUL ~25%,
    UL, whole life), ~12k deferred annuities (MYGA / FIA / RILA); life placement ~78%,
    underwriting approval ~89%, accelerated share rising from ~20% to ~50%; 13-month
    persistency ~0.89 and life lapse ~5-7% a year; FIA caps peaking ~10% in 2023 and
    falling to ~8.25%; annuity surrenders ~5-9% a year with MYGA shock lapses at the
    end of the guarantee; life deaths drawn from the same mortality curve the
    actual-to-expected metric uses, by risk class, with select-period mortality in the
    first five policy years (A/E ~0.75-0.85 overall, rising with duration).

    Planted signals the curator and drift loop should find:
      * ann_src.crediting_option.method = 'TRIGGER' (performance-trigger strategy on
        the FIAs) from 2026-03-01; not in CreditingMethodEnum
      * lifepas.RIDER.RDR_CD = 'ROP' (return-of-premium rider on term) from 2026-01-01;
        not in RiderTypeEnum
      * ann_src.reserve.method = 'VM22PBR' (VM-22 principle-based reserves) for fixed
        and indexed annuities issued from 2026-01-01; not in ReserveMethodEnum
    """
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    for s in ("ref", "lifepas", "ann_src"):
        con.execute(f"CREATE SCHEMA {s}")
    ref = _ref(con, rng)
    _life(con, rng, ref, life_apps)
    _ann(con, rng, ref, ann_apps)
    con.close()
    return path


build_northwind.valuation = NORTHWIND_VALUATION


# ------------------------------------------------------------- reference hub

def _ref(con, rng) -> dict:
    con.execute("CREATE TABLE ref.ORG_UNIT (UNIT_CD VARCHAR, UNIT_NM VARCHAR, SEG_CD VARCHAR)")
    _insert(con, "ref.ORG_UNIT", [("LIFE", "Northwind Individual Life", "INS"),
                                  ("ANN", "Northwind Annuities", "INS")])
    con.execute("CREATE TABLE ref.LEGAL_ENTITY (CO_CD VARCHAR, CO_NM VARCHAR, NAIC_CD VARCHAR, DOM_ST VARCHAR, FEIN VARCHAR)")
    _insert(con, "ref.LEGAL_ENTITY", [("NWL", "Northwind Life Insurance Company", "99871", "IA", "42-5550117"),
                                      ("NWNY", "Northwind Life & Annuity Company of New York", "99872", "NY", "13-5550142")])
    con.execute("""CREATE TABLE ref.PRODUCT_CAT (PROD_ID VARCHAR, PROD_NM VARCHAR, LOB_CD VARCHAR,
        EFF_DT DATE, END_DT DATE)""")
    _insert(con, "ref.PRODUCT_CAT", [(p[0], p[1], "IL", p[3], None) for p in _LIFE_PLANS]
            + [(p[0], p[1], "IA", p[4], None) for p in _ANN_PRODUCTS])

    # Producer master: one writing producer per row, with channel and marketing org.
    con.execute("""CREATE TABLE ref.PRODUCER (AGT_ID VARCHAR, AGT_NM VARCHAR, AGT_TYP VARCHAR, CHNL_CD VARCHAR,
        RES_ST VARCHAR, IMO_NM VARCHAR, CONTRACT_DT DATE)""")
    producers, by_channel = [], {}
    plan = [("IBGA", "IA", 260), ("CAREER", "CA", 70), ("BD", "BD", 90), ("BANK", "BK", 60)]
    n = 0
    for chnl, typ, count in plan:
        for _ in range(count):
            n += 1
            aid = f"A{n:05d}"
            imo = rng.choice(_IMOS) if chnl == "IBGA" else ("Northwind Career Agency" if chnl == "CAREER" else None)
            producers.append((aid, f"{rng.choice(_FIRST)} {rng.choice(_LAST)}", typ, chnl, _pick(rng, _STATES), imo,
                              date(rng.randint(2008, 2018), rng.randint(1, 12), 1)))
            by_channel.setdefault(chnl, []).append(aid)
    producers.append(("A99999", "Northwind Direct", "DR", "DTC", "IA", None, date(2015, 1, 1)))
    by_channel["DTC"] = ["A99999"]
    _insert(con, "ref.PRODUCER", producers)

    # Mortality tables. Synthetic curves; the experience basis (VBT15) feeds A/E.
    con.execute("""CREATE TABLE ref.MORT_BASIS (TBL_CD VARCHAR, SEX_CD VARCHAR, SMK_CD VARCHAR, ATT_AGE INTEGER,
        QX DECIMAL(9,6))""")
    rows = []
    for age in range(0, 121):
        for sex in ("M", "F"):
            for smk in ("N", "S"):
                rows.append(("VBT15", sex, smk, age, round(_q_vbt(age, sex, smk == "S"), 6)))
                rows.append(("17CSO", sex, smk, age, round(min(_q_vbt(age, sex, smk == "S") * 1.45, 1.0), 6)))
            rows.append(("12IAM", sex, "U", age, round(_q_iam(age, sex), 6)))
    _insert(con, "ref.MORT_BASIS", rows)
    return {"by_channel": by_channel}


# ----------------------------------------------------------------------- LIFE

def _quarter_ends(start: date, end: date) -> list[date]:
    out, d = [], date(start.year, ((start.month - 1) // 3) * 3 + 1, 1)
    while True:
        qe = _add_months(d, 3) - timedelta(days=1)
        if qe > end:
            break
        if qe >= start:
            out.append(qe)
        d = _add_months(d, 3)
    return out


_RESERVE_DATES = [date(y, 12, 31) for y in range(2019, 2026)] + [date(2026, 3, 31), date(2026, 6, 30)]


def _life(con, rng, ref, n_apps):
    S = "lifepas"
    V = NORTHWIND_VALUATION
    con.execute(f"""CREATE TABLE {S}.PLANDEF (PLAN_CD VARCHAR, PLAN_NM VARCHAR, PLAN_TYP VARCHAR, CO_CD VARCHAR,
        SC_YRS INTEGER, EFF_DT CHAR(8), END_DT CHAR(8))""")
    _insert(con, f"{S}.PLANDEF", [(p[0], p[1], p[2], "NWL", p[4], _ymd(p[3]), OPEN) for p in _LIFE_PLANS])

    # IUL strategies: one row per strategy per rate sheet.
    con.execute(f"""CREATE TABLE {S}.IDXSTRAT (STRAT_ID VARCHAR, PLAN_CD VARCHAR, IDX_NM VARCHAR, METH_CD VARCHAR,
        TERM_YRS INTEGER, CAP_BPS INTEGER, PART_BPS INTEGER, FLR_BPS INTEGER, DECL_BPS INTEGER, RATE_EFF_DT CHAR(8))""")
    strats = []
    for plan in ("IUL1", "IUL2"):
        for i, p in enumerate(_PERIODS):
            if plan == "IUL2" and p < date(2023, 1, 1):
                continue
            tag = p.strftime("%Y%m")
            strats.append((f"{plan}-A1-{tag}", plan, "US Large Cap 500 Index", "A1", 1,
                           int(_IUL_CAP[i] * 100) + (25 if plan == "IUL2" else 0), 10000, 0, None, _ymd(p)))
            strats.append((f"{plan}-MA-{tag}", plan, "US Large Cap 500 Index", "MA", 1,
                           int(_IUL_CAP[i] * 100) + 150, 10000, 0, None, _ymd(p)))
            strats.append((f"{plan}-FX-{tag}", plan, None, "FX", 1, None, None, None,
                           int((_FIA_FIXED[i] + 1.5) * 100), _ymd(p)))
    _insert(con, f"{S}.IDXSTRAT", strats)

    clients, apps, uw, pols, riders, benefs, snaps, trans, claims, reserves, comms = ([] for _ in range(11))
    nclient = [0]

    def new_client(kind, sex=None, birth=None, state=None, last=None, smk=""):
        nclient[0] += 1
        cid = f"C{nclient[0]:07d}"
        first = rng.choice(_FIRST) if kind == "I" else None
        last = last or rng.choice(_LAST)
        ent = None
        if kind == "T":
            ent = f"{last} Family Irrevocable Trust"
        elif kind == "B":
            ent = f"{last} {rng.choice(['Manufacturing', 'Holdings', 'Dental Group', 'Logistics'])} LLC"
        elif kind == "E":
            ent = f"Estate of {rng.choice(_FIRST)} {last}"
        clients.append((cid, kind, first, last if kind == "I" else None, ent,
                        _ymd(birth) if birth else OPEN, sex or "", smk, state or _pick(rng, _STATES),
                        f"{rng.randint(100, 899)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"))
        return cid

    app_seq = pol_seq = clm_seq = trn_seq = cms_seq = 0
    strat_by = {(s[1], s[3], s[9]): s[0] for s in strats}
    cap_by = {s[0]: s[5] for s in strats}

    # Applications spread over 2019-01 .. 2026-06 with ~5% annual growth.
    months = [_add_months(NW_START, i) for i in range(90)]
    weights = [1.05 ** (i / 12) for i in range(90)]
    for _ in range(n_apps):
        m = rng.choices(months, weights)[0]
        app_dt = m + timedelta(days=rng.randint(0, 27))
        yr = app_dt.year
        ptyp = rng.choices(["T", "I", "U", "W"], [55, 20 + (yr - 2019) * 1.2, 15 - (yr - 2019) * 0.8, 8])[0]
        if ptyp == "T":
            plan = rng.choices(["T10N", "T20N", "T30N"], [20, 55, 25])[0]
        elif ptyp == "I":
            plan = "IUL2" if app_dt >= date(2023, 1, 1) and rng.random() < 0.45 else "IUL1"
        else:
            plan = {"U": "UL01", "W": "WL01"}[ptyp]
        age = int(max(18, min(80, rng.gauss({"T": 41, "I": 45, "U": 58, "W": 52}[ptyp], 9))))
        if ptyp == "T" and plan == "T30N":
            age = min(age, 50)
        sex = rng.choice("MF")
        smoker = rng.random() < 0.09
        state = _pick(rng, _STATES)
        median = {"T": 500_000, "I": 400_000, "U": 250_000, "W": 75_000}[ptyp]
        face = max(25_000, min(5_000_000, round(median * math.exp(rng.gauss(0, 0.6)) / 25_000) * 25_000))
        chnl = rng.choices(["IBGA", "CAREER", "BD", "BANK", "DTC"],
                           [55, 25, 8, 5, 7 if ptyp == "T" and face <= 1_000_000 else 0])[0]
        agent = rng.choice(ref["by_channel"][chnl])
        birth = date(app_dt.year - age, rng.randint(1, 12), rng.randint(1, 28))
        insured = new_client("I", sex, birth, state, smk="S" if smoker else "N")
        owner_kind = rng.choices(["SELF", "SPOUSE", "T", "B"], [83, 9, 5 if face >= 1_000_000 else 1, 3])[0]
        if owner_kind == "SELF":
            owner = insured
        elif owner_kind == "SPOUSE":
            owner = new_client("I", "F" if sex == "M" else "M", birth + timedelta(days=rng.randint(-1500, 1500)),
                               state, clients[-1][3])
        else:
            owner = new_client(owner_kind, state=state)
        app_seq += 1
        app_no = f"L{app_seq:07d}"
        replacement = rng.random() < 0.05

        # Underwriting.
        accel_share = min(0.85, 0.30 + 0.08 * (yr - 2019))
        if ptyp == "W" and face <= 50_000:
            path = "SI"
        elif age <= 60 and face <= 1_000_000 and ptyp in ("T", "I") and rng.random() < accel_share:
            path = "AU"
        else:
            path = "FU"
        dec = rng.choices(["AP", "AO", "DC", "PP", "WD", "IC"], [72, 12, 8, 2, 4, 2])[0]
        if smoker and dec == "AP":
            dec = "AO" if rng.random() < 0.3 else "AP"
        tat = rng.randint(1, 6) if path == "AU" else (rng.randint(3, 12) if path == "SI" else int(rng.gauss(32, 9)))
        tat = max(1, tat)
        dcsn_dt = app_dt + timedelta(days=tat)
        decided = dcsn_dt <= V
        risk, table, flat = None, 0, 0.0
        if dec in ("AP", "AO"):
            if dec == "AP":
                risk = rng.choices(["PP", "PF", "SP", "ST"], [20, 34, 22, 24])[0]
            else:
                risk = rng.choices(["ST", "SS"], [40, 60])[0]
            if risk == "SS":
                table = rng.choice([2, 2, 3, 4, 4, 6, 8])
                flat = rng.choice([0, 0, 0, 2.5, 5.0])
            if path == "AU" and risk in ("SS",):
                path = "FU"      # substandard is never decided on data alone
        reason = {"AP": "Meets class criteria", "AO": "Rated: build / medical history",
                  "DC": rng.choice(["Medical history", "Motor vehicle record", "Avocation"]),
                  "PP": "Postponed pending treatment", "WD": "Applicant withdrew",
                  "IC": "Closed incomplete: requirements outstanding"}[dec]
        uw.append((f"U{app_seq:07d}", app_no, path, risk or "", table, flat,
                   dec if decided else "OP", _ymd(dcsn_dt if decided else None), reason if decided else None))

        issue_dt = dcsn_dt + timedelta(days=rng.randint(2, 9))
        issued = decided and dec in ("AP", "AO") and issue_dt <= V
        if not decided:
            app_stat = "UW"
        elif dec in ("DC", "PP"):
            app_stat = "DC"
        elif dec == "WD":
            app_stat = "WD"
        elif dec == "IC":
            app_stat = "IC"
        elif not issued:
            app_stat = "AP"
        else:
            app_stat = "PL"      # updated below for not-taken
        app_row = [app_no, plan, owner, agent, _ymd(app_dt), state, "Y" if replacement else "N", app_stat, chnl]
        if not issued:
            apps.append(tuple(app_row))
            continue

        # Policy.
        pol_seq += 1
        pol_no = f"{'7' if ptyp == 'T' else '8'}{pol_seq:08d}"
        rate = {"T": 0.45 * math.exp(0.08 * (age - 30)) * {"T10N": 0.75, "T20N": 1.0, "T30N": 1.45}.get(plan, 1),
                "I": 6 + 0.4 * (age - 30), "U": 8 + 0.6 * (age - 45), "W": 14 + 0.7 * (age - 35)}[ptyp]
        rate = max(rate, 0.3) * {"PP": 0.75, "PF": 0.9, "SP": 1.05, "ST": 1.25, "SS": 1.25 * (1 + 0.25 * table)}[risk]
        rate *= 2.5 if smoker else 1.0
        ann_prem = round(face / 1000 * rate + 60 + flat * face / 1000, 2)
        mode = rng.choices(["A", "S", "Q", "M"], [25, 5, 10, 60])[0]
        freq = {"A": 1, "S": 2, "Q": 4, "M": 12}[mode]
        modal = round(ann_prem / freq, 2)
        ann_prem = round(modal * freq, 2)
        co = "NWNY" if state == "NY" else "NWL"
        strat = None
        if ptyp == "I":
            meth = rng.choices(["A1", "MA", "FX"], [70, 15, 15])[0]
            strat = strat_by[(plan, meth, _ymd(_PERIODS[_rate_period(issue_dt)]))]

        # Life events, one policy year at a time.
        term_dt, term_rsn = None, None
        if rng.random() < 0.06:
            term_dt, term_rsn = issue_dt + timedelta(days=rng.randint(25, 60)), "N"
        elif rng.random() < 0.01:
            term_dt, term_rsn = issue_dt + timedelta(days=rng.randint(10, 30)), "F"
        q_mult = _CLASS_MORT[risk] * (1 + 0.25 * table if risk == "SS" else 1)
        k = 0
        while term_dt is None:
            start = _add_years(issue_dt, k)
            if start > V:
                break
            att = age + k
            lapse = {"T": 0.10, "I": 0.08, "U": 0.06, "W": 0.06}[ptyp] if k == 0 else \
                {"T": 0.055, "I": 0.045, "U": 0.03, "W": 0.025}[ptyp]
            hz = [("D", _q_vbt(att, sex, smoker) * q_mult * _select_factor(k)), ("L", lapse)]
            if ptyp != "T" and k >= 2:
                hz.append(("S", 0.015))
            if ptyp == "T" and k >= 3:
                hz.append(("C", 0.006))
            for code, p in hz:
                if rng.random() < p:
                    t = start + timedelta(days=rng.randint(0, 364))
                    if t <= V:
                        term_dt, term_rsn = t, code
                    break
            k += 1
        if term_rsn == "N":
            app_row[7] = "NT"
        apps.append(tuple(app_row))
        stat = {None: "A", "N": "N", "F": "F", "L": "L", "S": "S", "D": "D", "C": "C"}[term_rsn]
        months_paid = None
        end = term_dt or V
        if term_rsn in ("N", "F"):
            paid_to = issue_dt
        elif term_rsn == "L":
            paid_to = term_dt - timedelta(days=61)
        else:
            months_paid = (end.year - issue_dt.year) * 12 + end.month - issue_dt.month + 1
            step = 12 // freq
            paid_to = _add_months(date(issue_dt.year, issue_dt.month, 1), ((months_paid + step - 1) // step) * step)
        paid_to = max(paid_to, issue_dt)
        pols.append((pol_no, plan, co, app_no, owner, insured, agent, _ymd(issue_dt), age, state, face, risk,
                     table, mode, _cents(modal), _cents(ann_prem), _ymd(paid_to), stat, _ymd(term_dt), chnl, strat))

        # Riders.
        rseq = 0
        def rider(code, amt, chg):
            nonlocal rseq
            rseq += 1
            riders.append((pol_no, rseq, code, amt, int(chg * 10000) if chg is not None else None, _ymd(issue_dt)))
        if ptyp in ("T", "U") and rng.random() < 0.28:
            rider("WP", face, 0.06)
        if rng.random() < 0.14:
            rider("ADB", min(face, 500_000), 0.0008)
        if ptyp == "T" and rng.random() < 0.07:
            rider("CTR", 10_000, 0.0005)
        if ptyp == "I" and rng.random() < 0.6:
            rider("CIR", face, None)
        if ptyp == "U" and rng.random() < 0.12:
            rider("LTC", face, 0.0012)
        if ptyp == "T" and issue_dt >= ROP_START and rng.random() < 0.25:
            rider("ROP", ann_prem * {"T10N": 10, "T20N": 20, "T30N": 30}[plan], 0.35)

        # Beneficiaries.
        nb = rng.choices([1, 2, 3], [55, 35, 10])[0]
        shares = [100] if nb == 1 else ([50, 50] if nb == 2 else [34, 33, 33])
        prim = []
        for b in range(nb):
            cid = new_client("I", rng.choice("MF"), date(rng.randint(1950, 2010), rng.randint(1, 12), rng.randint(1, 28)), state)
            benefs.append((pol_no, b + 1, cid, "P", shares[b]))
            prim.append(cid)
        if rng.random() < 0.4:
            benefs.append((pol_no, nb + 1, new_client("E" if rng.random() < 0.2 else "I",
                                                       state=state), "C", 100))

        # Premiums (one PRM summary row per policy year) and commissions.
        cms_rate = {"T": 0.85, "I": 0.95, "U": 0.80, "W": 0.75}[ptyp]
        if term_rsn not in ("N", "F"):
            cms_seq += 1
            comms.append((f"M{cms_seq:08d}", pol_no, agent, "FY", _ymd(issue_dt), _cents(ann_prem * cms_rate)))
        k = 0
        while True:
            start = _add_years(issue_dt, k)
            if start > end or (term_rsn in ("N", "F")):
                break
            nxt = _add_years(issue_dt, k + 1)
            stop = min(end, nxt)
            mths = max(1, (stop.year - start.year) * 12 + stop.month - start.month + (1 if stop.day >= start.day else 0))
            if term_rsn == "L" and stop == end:
                mths = max(1, mths - 2)
            step = 12 // freq
            pays = min(freq, (min(mths, 12) + step - 1) // step)
            trn_seq += 1
            trans.append((f"T{trn_seq:09d}", pol_no, "PRM", _ymd(start), _cents(modal * pays)))
            if 1 <= k <= 9:
                cms_seq += 1
                comms.append((f"M{cms_seq:08d}", pol_no, agent, "RN", _ymd(start), _cents(modal * pays * 0.03)))
            k += 1
        if term_rsn == "L" and term_dt < _add_years(issue_dt, 1):
            cms_seq += 1
            comms.append((f"M{cms_seq:08d}", pol_no, agent, "CB", _ymd(term_dt), -_cents(ann_prem * cms_rate * 0.5)))

        # Values on permanent plans (quarter-end snapshots) and loans / surrenders.
        av, loan, k_done = 0.0, 0.0, -1
        perm = ptyp != "T"
        last_cred = None
        def credit_for(anniv):
            if ptyp == "I":
                if strat and "-FX-" in strat:
                    return (_FIA_FIXED[_rate_period(issue_dt)] + 1.5) / 100
                cap = cap_by[strat] / 10000
                r = _index_return(anniv) * (0.85 if "-MA-" in strat else 1.0)
                return min(cap, max(0.0, r))
            return {"U": 0.035, "W": 0.045}[ptyp]
        load = {"I": 0.85, "U": 0.55, "W": 0.6}.get(ptyp, 0)
        if perm and term_rsn not in ("N", "F"):
            for qe in _quarter_ends(issue_dt, end if term_dt else V):
                while _add_years(issue_dt, k_done + 1) <= qe:
                    k_done += 1
                    anniv = _add_years(issue_dt, k_done)
                    if k_done > 0:
                        last_cred = credit_for(anniv)
                        coi = max(face - av, 0) * _q_vbt(age + k_done, sex, smoker) * q_mult * 1.3
                        av = max(0.0, av * (1 + last_cred) - coi)
                    av += ann_prem * load
                    if ptyp == "I" and k_done >= 3 and rng.random() < 0.03:
                        amt = round(av * 0.2, 2)
                        loan += amt
                        trn_seq += 1
                        trans.append((f"T{trn_seq:09d}", pol_no, "LON", _ymd(anniv), _cents(amt)))
                    loan *= 1.05 if k_done > 0 else 1
                sc = (_LIFE_SC[k_done] / 100 if ptyp in ("I", "U") and k_done < 10 else 0) * av
                csv = max(0.0, av - sc - loan)
                snaps.append((pol_no, _ymd(qe), _cents(av), _cents(csv), _cents(sc), _cents(loan),
                              int(round(last_cred * 10000)) if last_cred is not None else None))
            if term_rsn == "S":
                trn_seq += 1
                sc = (_LIFE_SC[max(k_done, 0)] / 100 if ptyp in ("I", "U") and k_done < 10 else 0) * av
                trans.append((f"T{trn_seq:09d}", pol_no, "SUR", _ymd(term_dt), _cents(max(0.0, av - sc - loan))))

        # Statutory reserves at year-ends and the 2026 quarter-ends.
        if term_rsn not in ("N", "F"):
            meth, tbl = ("CRVM", "01CSO") if issue_dt.year == 2019 else ("VM20", "17CSO")
            for vd in _RESERVE_DATES:
                if vd < issue_dt or (term_dt and term_dt <= vd):
                    continue
                dur = (vd - issue_dt).days / 365.25
                if ptyp == "T":
                    rsv = face / 1000 * (0.6 + 0.35 * dur) * rate / 2.0
                elif ptyp == "W":
                    rsv = ann_prem * dur * 0.75
                else:
                    rsv = ann_prem * load * dur * (1.08 if ptyp == "U" else 0.95) + face * 0.002
                reserves.append((pol_no, _ymd(vd), meth, tbl, _cents(rsv)))

        # Death claim.
        if term_rsn == "D":
            clm_seq += 1
            notif = term_dt + timedelta(days=max(2, int(rng.expovariate(1 / 16))))
            contest = term_dt < _add_years(issue_dt, 2)
            if notif > V:
                notif = V
            days = int(rng.gauss(70, 20)) if contest else max(3, int(rng.gauss(13, 6)))
            paid_dt = notif + timedelta(days=max(3, days))
            status = "PD"
            if contest and rng.random() < 0.09:
                status = "DN"
            if paid_dt > V:
                status = "CR" if contest else rng.choice(["RP", "IR"])
            interest = face * 0.03 * max((paid_dt - term_dt).days, 0) / 365 if status == "PD" else 0
            claims.append((f"D{clm_seq:06d}", pol_no, _ymd(term_dt), _pick(rng, _CAUSES), _ymd(notif), status,
                           "Y" if contest else "N", _cents(face), _cents(face + interest) if status == "PD" else 0,
                           _ymd(paid_dt) if status in ("PD", "DN") else OPEN, prim[0]))

    con.execute(f"""CREATE TABLE {S}.CLIENT (CLNT_ID VARCHAR, CLNT_TYP VARCHAR, FIRST_NM VARCHAR, LAST_NM VARCHAR,
        ENTITY_NM VARCHAR, BIRTH_DT CHAR(8), SEX_CD VARCHAR, SMK_CD VARCHAR, RES_ST VARCHAR, TIN VARCHAR)""")
    _insert(con, f"{S}.CLIENT", clients)
    con.execute(f"""CREATE TABLE {S}.APPL (APP_NO VARCHAR, PLAN_CD VARCHAR, APPLCNT_CLNT_ID VARCHAR, AGT_ID VARCHAR,
        APP_SIGN_DT CHAR(8), APP_ST VARCHAR, RPLC_IND VARCHAR, APP_STAT_CD VARCHAR, DIST_CHNL VARCHAR)""")
    _insert(con, f"{S}.APPL", apps)
    con.execute(f"""CREATE TABLE {S}.UWCASE (UW_CASE_ID VARCHAR, APP_NO VARCHAR, UW_PATH VARCHAR, RISK_CL VARCHAR,
        TBL_RT INTEGER, FLAT_XTRA DECIMAL(6,2), UW_DCSN VARCHAR, DCSN_DT CHAR(8), DCSN_RSN VARCHAR)""")
    _insert(con, f"{S}.UWCASE", uw)
    con.execute(f"""CREATE TABLE {S}.POLMSTR (POL_NO VARCHAR, PLAN_CD VARCHAR, CO_CD VARCHAR, APP_NO VARCHAR,
        OWNR_CLNT_ID VARCHAR, INSD_CLNT_ID VARCHAR, AGT_ID VARCHAR, ISS_DT CHAR(8), ISS_AGE INTEGER, ISS_ST VARCHAR,
        FACE_AMT BIGINT, RISK_CL VARCHAR, TBL_RT INTEGER, MODE_CD VARCHAR, MODAL_PREM_CTS BIGINT, ANN_PREM_CTS BIGINT,
        PD_TO_DT CHAR(8), STAT_CD VARCHAR, STAT_DT CHAR(8), DIST_CHNL VARCHAR, STRAT_ID VARCHAR)""")
    _insert(con, f"{S}.POLMSTR", pols)
    con.execute(f"""CREATE TABLE {S}.RIDER (POL_NO VARCHAR, RDR_SEQ INTEGER, RDR_CD VARCHAR, RDR_AMT BIGINT,
        RDR_CHG_BPS INTEGER, RDR_EFF_DT CHAR(8))""")
    _insert(con, f"{S}.RIDER", riders)
    con.execute(f"""CREATE TABLE {S}.BENEF (POL_NO VARCHAR, BNF_SEQ INTEGER, CLNT_ID VARCHAR, BNF_TYP VARCHAR,
        BNF_PCT DECIMAL(5,2))""")
    _insert(con, f"{S}.BENEF", benefs)
    con.execute(f"""CREATE TABLE {S}.CVSNAP (POL_NO VARCHAR, VAL_DT CHAR(8), ACCT_VAL_CTS BIGINT, CSV_CTS BIGINT,
        SURR_CHG_CTS BIGINT, LOAN_BAL_CTS BIGINT, CRED_RT_BPS INTEGER)""")
    _insert(con, f"{S}.CVSNAP", snaps)
    con.execute(f"""CREATE TABLE {S}.FINTRAN (TRN_ID VARCHAR, POL_NO VARCHAR, TRN_CD VARCHAR, TRN_DT CHAR(8),
        TRN_AMT_CTS BIGINT)""")
    _insert(con, f"{S}.FINTRAN", trans)
    con.execute(f"""CREATE TABLE {S}.CLMHDR (CLM_NO VARCHAR, POL_NO VARCHAR, DOD CHAR(8), COD_CD VARCHAR,
        NOTIF_DT CHAR(8), CLM_STAT VARCHAR, CONTEST_IND VARCHAR, DB_AMT_CTS BIGINT, PD_AMT_CTS BIGINT, PD_DT CHAR(8),
        CLMNT_CLNT_ID VARCHAR)""")
    _insert(con, f"{S}.CLMHDR", claims)
    con.execute(f"""CREATE TABLE {S}.STATRSV (POL_NO VARCHAR, VAL_DT CHAR(8), RSV_METH VARCHAR, MORT_TBL VARCHAR,
        RSV_AMT_CTS BIGINT)""")
    _insert(con, f"{S}.STATRSV", reserves)
    con.execute(f"""CREATE TABLE {S}.COMMPAY (CMSN_ID VARCHAR, POL_NO VARCHAR, AGT_ID VARCHAR, CMSN_TYP VARCHAR,
        CMSN_DT CHAR(8), CMSN_AMT_CTS BIGINT)""")
    _insert(con, f"{S}.COMMPAY", comms)


# ------------------------------------------------------------------------ ANN

def _ann(con, rng, ref, n_apps):
    S = "ann_src"
    V = NORTHWIND_VALUATION
    con.execute(f"""CREATE TABLE {S}.product (product_id VARCHAR, product_name VARCHAR, product_kind VARCHAR,
        company_code VARCHAR, surrender_years INTEGER, launch_date DATE, close_date DATE)""")
    _insert(con, f"{S}.product", [(p[0], p[1], p[2], "NWL", p[3], p[4], None) for p in _ANN_PRODUCTS])

    con.execute(f"""CREATE TABLE {S}.crediting_option (option_id VARCHAR, product_id VARCHAR, index_name VARCHAR,
        method VARCHAR, term_years INTEGER, cap DECIMAL(6,4), participation DECIMAL(6,4), spread DECIMAL(6,4),
        floor DECIMAL(6,4), buffer DECIMAL(6,4), declared_rate DECIMAL(6,4), effective_date DATE)""")
    opts = []
    for i, p in enumerate(_PERIODS):
        tag = p.strftime("%Y%m")
        for pid, bump in (("MYGA3", 0.0), ("MYGA5", 0.25)):
            opts.append((f"{pid}-FIXED-{tag}", pid, None, "FIXED", int(pid[-1]), None, None, None, None, None,
                         round((_MYGA3[i] + bump) / 100, 4), p))
        for pid, bump in (("FIA7", 0.0), ("FIA10", 0.5)):
            opts.append((f"{pid}-APTP-{tag}", pid, "US Large Cap 500 Index", "APTP", 1, round((_FIA_CAP[i] + bump) / 100, 4),
                         1.0, None, 0.0, None, None, p))
            opts.append((f"{pid}-PAR-{tag}", pid, "Northwind Balanced 5% Risk Control Index", "APTP", 1, None,
                         round((_FIA_PAR[i] + bump * 10) / 100, 4), None, 0.0, None, None, p))
            opts.append((f"{pid}-MPTP-{tag}", pid, "US Large Cap 500 Index", "MPTP", 1, round((_FIA_CAP[i] + bump) / 600, 4),
                         1.0, None, 0.0, None, None, p))
            opts.append((f"{pid}-FIXED-{tag}", pid, None, "FIXED", 1, None, None, None, None, None,
                         round((_FIA_FIXED[i] + bump / 2) / 100, 4), p))
        opts.append((f"RILA6-APTP-{tag}", "RILA6", "US Large Cap 500 Index", "APTP", 1, round(_RILA_CAP[i] / 100, 4),
                     1.0, None, None, 0.10, None, p))
    for pid, rate in (("FIA7", 0.0625), ("FIA10", 0.0675)):       # planted: performance trigger
        opts.append((f"{pid}-TRIGGER-202603", pid, "US Large Cap 500 Index", "TRIGGER", 1, None, None, None, 0.0,
                     None, rate, TRIGGER_START))
    _insert(con, f"{S}.crediting_option", opts)
    opt_rows = {o[0]: o for o in opts}

    customers, apps, reviews, contracts, riders, benefs, values, txns, claims, reserves, comms = ([] for _ in range(11))
    ncust = [0]

    def customer(kind, sex=None, birth=None, state=None, last=None):
        ncust[0] += 1
        cid = f"CU{ncust[0]:07d}"
        last = last or rng.choice(_LAST)
        customers.append((cid, kind, rng.choice(_FIRST) if kind == "PERSON" else None,
                          last if kind == "PERSON" else None,
                          f"{last} Living Trust" if kind == "TRUST" else None, birth,
                          sex if kind == "PERSON" else None, state or _pick(rng, _STATES),
                          f"{rng.randint(100, 899)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"))
        return cid

    months = [_add_months(NW_START, i) for i in range(90)]
    # Sales follow the rate environment: fixed annuity sales surge in 2023-2024.
    year_w = {2019: 1.0, 2020: 0.8, 2021: 0.85, 2022: 1.2, 2023: 1.9, 2024: 1.8, 2025: 1.55, 2026: 1.45}
    weights = [year_w[m.year] for m in months]
    seq = {"app": 0, "ctr": 0, "txn": 0, "clm": 0, "cms": 0, "rev": 0}

    def nxt(k):
        seq[k] += 1
        return seq[k]

    for _ in range(n_apps):
        m = rng.choices(months, weights)[0]
        signed = m + timedelta(days=rng.randint(0, 27))
        yr = signed.year
        kind = rng.choices(["MYGA", "FIA", "RILA"],
                           [{2023: 55, 2024: 52}.get(yr, 35 if yr < 2023 else 42), 50 if yr < 2023 else 38,
                            8 + max(0, yr - 2021) * 2.5])[0]
        pid = {"MYGA": rng.choice(["MYGA3", "MYGA3", "MYGA5"]), "FIA": rng.choice(["FIA7", "FIA10"]),
               "RILA": "RILA6"}[kind]
        age = int(max(45, min(85, rng.gauss(64, 8))))
        sex = rng.choice("MF")
        state = _pick(rng, _STATES)
        birth = date(yr - age, rng.randint(1, 12), rng.randint(1, 28))
        annuitant = customer("PERSON", sex, birth, state)
        owner = annuitant if rng.random() < 0.9 else customer("TRUST", state=state)
        chnl = "BD" if kind == "RILA" else rng.choices(["IBGA", "BANK", "BD", "CAREER"], [48, 30, 16, 6])[0]
        advisor = rng.choice(ref["by_channel"][chnl])
        replacement = rng.random() < 0.22
        app_id = f"AP-{nxt('app'):07d}"
        prem = {"MYGA": 135_000, "FIA": 95_000, "RILA": 110_000}[kind] * math.exp(rng.gauss(-0.25, 0.75))
        prem = round(max(10_000 if kind != "MYGA" else 25_000, min(1_000_000, prem)), -2)

        # Best-interest review: before issue, on every sale.
        outcome = rng.choices(["APPROVED", "CONDITIONAL", "REJECTED"],
                              [80, 6, 14] if replacement else [91, 5, 4])[0]
        profile = rng.random() < (0.93 if outcome == "REJECTED" else 0.995)
        reviewed = signed + timedelta(days=rng.randint(1, 5))
        if reviewed > V:
            outcome, reviewed = "PENDING", None
        note = {"APPROVED": "Recommendation consistent with stated objectives",
                "CONDITIONAL": "Approved after advisor documented liquidity needs",
                "REJECTED": rng.choice(["Replacement not in consumer's interest: surrender charge on existing contract",
                                        "Insufficient liquid assets outside the annuity",
                                        "Time horizon shorter than surrender period"]),
                "PENDING": None}[outcome]
        reviews.append((f"SR-{nxt('rev'):07d}", app_id, outcome, replacement, profile, reviewed, note))

        transfer = replacement or rng.random() < 0.08            # funded by a 1035 exchange / transfer
        issue = (reviewed or V) + timedelta(days=rng.randint(20, 45) if transfer else rng.randint(3, 12))
        withdrawn = rng.random() < 0.04
        if outcome == "PENDING":
            status = "SUBMITTED"
        elif outcome == "REJECTED":
            status = "DECLINED"
        elif withdrawn:
            status = "WITHDRAWN"
        elif issue > V:
            status = "APPROVED"
        else:
            status = "ISSUED"
        apps.append((app_id, pid, owner, advisor, signed, state, replacement, status, chnl))
        if status != "ISSUED":
            continue

        # Contract.
        cno = f"NW{nxt('ctr'):08d}"
        period = _PERIODS[_rate_period(issue)]
        tag = period.strftime("%Y%m")
        if kind == "MYGA":
            opt = f"{pid}-FIXED-{tag}"
        elif kind == "RILA":
            opt = f"RILA6-APTP-{tag}"
        else:
            choice = rng.choices(["APTP", "PAR", "MPTP", "FIXED", "TRIGGER"],
                                 [45, 22, 10, 18, 30 if issue >= TRIGGER_START else 0])[0]
            opt = f"{pid}-TRIGGER-202603" if choice == "TRIGGER" else f"{pid}-{choice}-{tag}"
        o = opt_rows[opt]
        tax = rng.choices(["NQ", "IRA", "ROTH", "401K"], [55, 38, 5, 2])[0]
        sc = _SC[pid]
        lock = signed - timedelta(days=rng.randint(0, 30)) if kind == "MYGA" else None

        av = prem
        phase, term_dt, term_rsn = "ACCUM", None, None
        n_txn = nxt("txn")
        txns.append((f"TX{n_txn:09d}", cno, "1035_IN" if transfer else "DEPOSIT", issue, prem))
        cms = prem * {"MYGA": 0.02, "FIA": 0.06 if pid == "FIA7" else 0.07, "RILA": 0.05}[kind]
        comms.append((f"CM{nxt('cms'):08d}", cno, advisor, "UPFRONT", issue, round(cms, 2)))
        guar_rate = o[10]
        last_cred, glwb_base = None, None
        if kind == "FIA" and pid == "FIA10" and rng.random() < 0.4:
            glwb_base = prem
            riders.append((f"RD-{cno}-1", cno, "GLWB", 0.011, prem, issue))
        elif kind == "FIA" and rng.random() < 0.1:
            riders.append((f"RD-{cno}-1", cno, "EDB", 0.004, prem, issue))
        elif kind == "RILA":
            riders.append((f"RD-{cno}-1", cno, "RDB", 0.0015, prem, issue))
        net_prem = prem

        def credited(anniv, k):
            if o[3] == "FIXED":
                if kind == "MYGA" and k > int(pid[-1]):
                    return max(0.01, _MYGA3[_rate_period(anniv)] / 100 - 0.0125)
                return float(guar_rate) if kind == "MYGA" else _FIA_FIXED[_rate_period(anniv)] / 100
            r = _index_return(anniv)
            if o[3] == "TRIGGER":
                return float(o[10]) if r >= 0 else 0.0
            if o[3] == "MPTP":
                return max(0.0, min(float(o[5]) * 12, r * 0.7))
            if o[5] is None:                                   # participation strategy
                return max(0.0, float(o[6]) * r * 0.45)
            if kind == "RILA":
                return min(float(o[5]), r) if r >= 0 else min(0.0, r + 0.10)
            return min(float(o[5]), max(0.0, r))

        k = 0
        snap_dates = _quarter_ends(issue, V)
        my_vals = []

        def snapshot(upto, start, k):
            """Quarter-end values in [start, upto) of contract year k."""
            while snap_dates and snap_dates[0] < upto:
                qe = snap_dates.pop(0)
                if qe < start:
                    continue
                accr = av * (last_cred or float(guar_rate or 0)) * (qe - start).days / 365 if o[3] == "FIXED" else 0.0
                val = av + accr
                sc_pct = sc[k] / 100 if k < len(sc) else 0.0
                chg = round(max(0.0, val * 0.9) * sc_pct, 2)
                shown = last_cred if last_cred is not None else (float(guar_rate) if o[3] == "FIXED" else None)
                row = (cno, qe, round(val, 2), round(val - chg, 2), chg, round(shown, 4) if shown is not None else None)
                values.append(row)
                my_vals.append(row)

        while True:
            start = _add_years(issue, k)
            end = _add_years(issue, k + 1)
            if start > V:
                break
            att = age + k
            sc_pct = sc[k] / 100 if k < len(sc) else 0.0
            shock = k == len(sc)
            surr = (0.55 if kind == "MYGA" else 0.25) if shock else (0.025 if sc_pct > 0 else 0.12)
            t = start + timedelta(days=rng.randint(0, 35) if shock else rng.randint(0, 364))
            event = None
            if t <= V:
                if rng.random() < _q_iam(att, sex):
                    event = "DEATH"
                elif rng.random() < surr:
                    event = "1035_OUT" if rng.random() < 0.3 else "SURRENDER"
                elif att >= 70 and sc_pct == 0 and rng.random() < 0.02:
                    event = "ANNUITIZE"
            snapshot(min(end, t if event else end, V + timedelta(days=1)), start, k)
            if event == "DEATH":
                term_dt, term_rsn = t, "DEATH"
                break
            if event in ("SURRENDER", "1035_OUT"):
                csv = av * (1 - sc_pct * 0.9)
                txns.append((f"TX{nxt('txn'):09d}", cno, event, t, round(csv, 2)))
                term_dt, term_rsn = t, event
                if t < _add_years(issue, 1):
                    comms.append((f"CM{nxt('cms'):08d}", cno, advisor, "CHARGEBACK", t, round(-cms * 0.5, 2)))
                break
            if event == "ANNUITIZE":
                phase = "PAYOUT"
                txns.append((f"TX{nxt('txn'):09d}", cno, "ANNUITIZE", t, round(av, 2)))
                income = av / max(8, 90 - att)
                y = 1
                while _add_years(t, y) <= V:
                    txns.append((f"TX{nxt('txn'):09d}", cno, "INCOME", _add_years(t, y), round(income, 2)))
                    y += 1
                break
            # Withdrawals: required minimum distributions on qualified money, occasional free withdrawals.
            wd_dt = start + timedelta(days=rng.randint(30, 330))
            if wd_dt <= V:
                wd = 0.0
                if tax in ("IRA", "401K") and att >= 73:
                    wd = av / max(10, 100 - att)
                elif rng.random() < 0.07:
                    wd = av * 0.08
                if wd:
                    txns.append((f"TX{nxt('txn'):09d}", cno, "WITHDRAWAL", wd_dt, round(wd, 2)))
                    av -= wd
                    net_prem -= wd
            if end > V:
                break
            if glwb_base:
                fee = round(glwb_base * 0.011, 2)
                txns.append((f"TX{nxt('txn'):09d}", cno, "RIDER_FEE", end, fee))
                av -= fee
            if chnl == "BANK":
                comms.append((f"CM{nxt('cms'):08d}", cno, advisor, "TRAIL", end, round(av * 0.0025, 2)))
            last_cred = credited(end, k + 1)
            av = max(0.0, av * (1 + last_cred))
            k += 1

        # Death claim on the annuitant.
        if term_rsn == "DEATH":
            notif = min(V, term_dt + timedelta(days=max(3, int(rng.expovariate(1 / 21)))))
            paid = notif + timedelta(days=max(4, int(rng.gauss(16, 7))))
            db = max(av, net_prem) if kind == "RILA" else av
            status = "PAID" if paid <= V else rng.choice(["OPEN", "REVIEW"])
            bene = customer("PERSON", rng.choice("MF"), date(rng.randint(1950, 2000), rng.randint(1, 12), 1), state)
            benefs.append((cno, 1, bene, "PRIMARY", 100))
            claims.append((f"DC-{nxt('clm'):06d}", cno, term_dt, _pick(rng, _CAUSES), notif, status, False,
                           round(db, 2), round(db, 2) if status == "PAID" else 0.0, paid if status == "PAID" else None, bene))
        else:
            nb = rng.choices([1, 2], [65, 35])[0]
            for b in range(nb):
                bene = customer("PERSON", rng.choice("MF"), date(rng.randint(1950, 2005), rng.randint(1, 12), 1), state)
                benefs.append((cno, b + 1, bene, "PRIMARY", 100 if nb == 1 else 50))

        status_c = {None: "ACTIVE", "DEATH": "DEATH_CLAIM", "SURRENDER": "SURRENDERED", "1035_OUT": "EXCHANGED"}[term_rsn]
        contracts.append((cno, pid, "NWNY" if state == "NY" else "NWL", app_id, owner, annuitant, advisor, issue, age,
                          state, tax, phase, prem, opt, status_c, term_dt, chnl, lock))

        # Reserves: CARVM; legacy VM-22 rates once annuitized; VM-22 PBR for new fixed / indexed issues.
        for vd in _RESERVE_DATES:
            if vd < issue or (term_dt and term_dt <= vd):
                continue
            snap = [v for v in my_vals if v[1] <= vd]
            base = snap[-1][2] if snap else prem
            if phase == "PAYOUT":
                meth = "VM22"
            elif issue >= PBR_START and kind in ("MYGA", "FIA"):
                meth = "VM22PBR"
            else:
                meth = "CARVM"
            reserves.append((cno, vd, meth, "12IAM", round(base * rng.uniform(0.95, 1.01), 2)))

    con.execute(f"""CREATE TABLE {S}.customer (customer_id VARCHAR, customer_type VARCHAR, first_name VARCHAR,
        last_name VARCHAR, org_name VARCHAR, dob DATE, gender VARCHAR, state VARCHAR, ssn_tin VARCHAR)""")
    _insert(con, f"{S}.customer", customers)
    con.execute(f"""CREATE TABLE {S}.application (app_id VARCHAR, product_id VARCHAR, applicant_id VARCHAR,
        advisor_id VARCHAR, signed_date DATE, state VARCHAR, replacement BOOLEAN, status VARCHAR, channel VARCHAR)""")
    _insert(con, f"{S}.application", apps)
    con.execute(f"""CREATE TABLE {S}.suitability (review_id VARCHAR, app_id VARCHAR, outcome VARCHAR,
        replacement BOOLEAN, profile_complete BOOLEAN, reviewed_on DATE, reviewer_note VARCHAR)""")
    _insert(con, f"{S}.suitability", reviews)
    con.execute(f"""CREATE TABLE {S}.contract (contract_id VARCHAR, product_id VARCHAR, company_code VARCHAR,
        app_id VARCHAR, owner_id VARCHAR, annuitant_id VARCHAR, advisor_id VARCHAR, issue_date DATE, issue_age INTEGER,
        issue_state VARCHAR, tax_type VARCHAR, phase VARCHAR, single_premium DECIMAL(14,2), option_id VARCHAR,
        status VARCHAR, status_date DATE, channel VARCHAR, rate_lock_date DATE)""")
    _insert(con, f"{S}.contract", contracts)
    con.execute(f"""CREATE TABLE {S}.rider (rider_id VARCHAR, contract_id VARCHAR, rider_code VARCHAR,
        fee_rate DECIMAL(6,4), benefit_base DECIMAL(14,2), start_date DATE)""")
    _insert(con, f"{S}.rider", riders)
    con.execute(f"""CREATE TABLE {S}.beneficiary (contract_id VARCHAR, seq INTEGER, customer_id VARCHAR,
        designation VARCHAR, pct DECIMAL(5,2))""")
    _insert(con, f"{S}.beneficiary", benefs)
    con.execute(f"""CREATE TABLE {S}.account_value (contract_id VARCHAR, as_of_date DATE, account_value DECIMAL(14,2),
        surrender_value DECIMAL(14,2), surrender_charge DECIMAL(14,2), credited_rate DECIMAL(7,4))""")
    _insert(con, f"{S}.account_value", values)
    con.execute(f"""CREATE TABLE {S}.txn (txn_id VARCHAR, contract_id VARCHAR, txn_type VARCHAR, txn_date DATE,
        amount DECIMAL(14,2))""")
    _insert(con, f"{S}.txn", txns)
    con.execute(f"""CREATE TABLE {S}.death_claim (claim_id VARCHAR, contract_id VARCHAR, date_of_death DATE,
        cause VARCHAR, notified_on DATE, status VARCHAR, contestable BOOLEAN, benefit_amount DECIMAL(14,2),
        paid_amount DECIMAL(14,2), paid_on DATE, claimant_id VARCHAR)""")
    _insert(con, f"{S}.death_claim", claims)
    con.execute(f"""CREATE TABLE {S}.reserve (contract_id VARCHAR, valuation_date DATE, method VARCHAR,
        mortality_basis VARCHAR, reserve DECIMAL(14,2))""")
    _insert(con, f"{S}.reserve", reserves)
    con.execute(f"""CREATE TABLE {S}.commission (comm_id VARCHAR, contract_id VARCHAR, advisor_id VARCHAR,
        comm_type VARCHAR, paid_on DATE, amount DECIMAL(12,2))""")
    _insert(con, f"{S}.commission", comms)
