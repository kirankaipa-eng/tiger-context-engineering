"""Life & annuity pack (individual life + deferred annuities) on the Northwind Life & Annuity reference client."""

import re
from datetime import date

import duckdb
import pytest
import sqlglot
import yaml

from insurance_context import sqlgen
from insurance_context.client import Client
from insurance_context.drift import run
from insurance_context.fixtures.la_warehouse import NORTHWIND_VALUATION
from insurance_context.overlay import resolve
from insurance_context.semantic import MetricError, SemanticLayer
from insurance_context.validate import validate

CORE = "https://ontology.tigeranalytics.com/insurance/core/"
LA = "https://ontology.tigeranalytics.com/insurance/life-annuity/"
NWL = "https://ontology.tigeranalytics.com/clients/northwind-life/"
CORPUS = Client("northwind_life").dir / "corpus"


@pytest.fixture(scope="module")
def con(northwind_wh):
    c = duckdb.connect(str(northwind_wh), read_only=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def sl(northwind):
    return SemanticLayer(northwind)


def _by(res, key):
    return {r[key]: r for r in res.records()}


# ------------------------------------------------------------------ the pack

def test_pack_extends_the_kernel_without_moving_iris(northwind):
    for schema in (resolve("life_annuity"), northwind.resolved, *(u.resolved for u in northwind.units)):
        sv = schema.view
        assert sv.get_uri("Claim", expand=True) == CORE + "Claim"
        # IRIs that existed in pack 0.1.0, before metrics and reference data were added.
        for cls in ("InsuredPerson", "LifeProduct", "AnnuityProduct", "CreditingStrategy", "Application",
                    "UnderwritingCase", "SuitabilityReview", "LifePolicy", "Rider", "AnnuityContract",
                    "Beneficiary", "AccountValueSnapshot", "ContractTransaction", "DeathClaim", "StatutoryReserve"):
            assert sv.get_uri(cls, expand=True) == LA + cls
        for slot in ("face_amount", "cap_rate", "reserve_method", "owner", "beneficiary_party", "contestable"):
            assert sv.get_uri(sv.get_slot(slot), expand=True) == LA + slot
        for enum in ("CreditingMethodEnum", "RiderTypeEnum", "ReserveMethodEnum", "MortalityTableEnum"):
            assert sv.get_uri(sv.get_enum(enum), expand=True) == LA + enum
        assert sv.get_uri(sv.get_enum("CoverageStatusEnum"), expand=True) == CORE + "CoverageStatusEnum"


def test_new_concepts_specialize_the_kernel():
    sv = resolve("life_annuity").view
    assert "Party" in sv.class_ancestors("ContractParty")
    assert "Entity" in sv.class_ancestors("MortalityRate")
    assert sv.get_uri("CommissionPayment", expand=True) == LA + "CommissionPayment"
    for slot in ("owner", "applicant", "beneficiary_party", "claimant"):
        assert sv.get_slot(slot).range == "ContractParty"          # a concrete party the views can resolve
    for enum in sv.all_enums().values():                           # every enum is pinned
        assert enum.enum_uri, enum.name
    ann = sv.get_class("MortalityRate").annotations["identity_scope"]
    assert getattr(ann, "value", ann) == "global"
    # Rates keep their precision: a two-place decimal would truncate 9.25% to 9%.
    assert sv.get_slot("cap_rate").range == "float" and sv.get_slot("mortality_rate").range == "float"


def test_sensitive_fields_are_marked():
    sv = resolve("life_annuity").view
    for cls, slot, level in (("DeathClaim", "cause_of_death", "phi"), ("DeathClaim", "date_of_death", "phi"),
                             ("InsuredPerson", "smoker_status", "phi"), ("ContractParty", "tax_id", "pii"),
                             ("ContractParty", "birth_date", "pii"), ("LifePolicy", "owner", "pii")):
        ann = sv.induced_slot(slot, cls).annotations["sensitivity"]
        assert getattr(ann, "value", ann) == level, (cls, slot)


# ----------------------------------------------------------------- the client

def test_northwind_vocabulary_extensions_and_restrictions(northwind):
    sv = northwind.resolved.view
    assert sv.get_class("AnnuityContract").title == "Deferred Annuity"
    assert sv.get_class("DeathClaim").title == "Mortality Claim"
    assert sv.get_class("Producer").title == "Agent"
    ann = northwind.unit("ANN").resolved.view
    assert ann.get_class("Producer").title == "Financial Professional"
    assert sv.get_uri(sv.get_slot("nwl_marketing_org"), expand=True) == NWL + "nwl_marketing_org"
    rng = sv.induced_slot("annuity_product_type", "AnnuityProduct").range
    assert set(sv.get_enum(rng).permissible_values) == {"fixed_deferred", "fixed_indexed", "registered_index_linked"}
    rng = sv.induced_slot("life_product_type", "LifeProduct").range
    assert "variable_universal_life" not in sv.get_enum(rng).permissible_values
    assert sv.induced_slot("application", "LifePolicy").required
    assert "ann_benefit_base" not in sv.all_slots()
    assert "ann_benefit_base" in {s.name for s in ann.class_induced_slots("Rider")}


@pytest.mark.parametrize("dialect", sqlgen.DIALECTS)
def test_northwind_views_and_metrics_are_portable(northwind, sl, dialect):
    assert sqlgen.portability_issues(northwind, dialect) == []
    for view in sqlgen.all_views(northwind):
        sqlglot.parse_one(view.ddl(dialect), read=dialect)
    sql = sl.compile(["placement_rate", "net_amount_at_risk", "persistency_13_month", "mortality_ae_ratio",
                      "annuity_net_flows", "average_cap_rate"], by=["product_type"],
                     as_of=NORTHWIND_VALUATION, dialect=dialect)
    sqlglot.parse_one(sql, read=dialect)


def test_units_union_and_shared_reference_data(con):
    pols = dict(con.execute("SELECT source_system, count(*) FROM ont.life_policy GROUP BY 1").fetchall())
    ctrs = dict(con.execute("SELECT source_system, count(*) FROM ont.annuity_contract GROUP BY 1").fetchall())
    assert set(pols) == {"LIFE"} and 18000 < pols["LIFE"] < 26000
    assert set(ctrs) == {"ANN"} and 9000 < ctrs["ANN"] < 15000
    # Products and producers are mastered once (global ids); both units' sales resolve to them.
    for table in ("product", "producer", "life_product", "annuity_product"):
        assert con.execute(f"SELECT count(*) FROM ont.{table} WHERE id LIKE '%:%'").fetchone()[0] == 0
    assert con.execute("""SELECT count(DISTINCT a.source_system) FROM ont.application a
                          JOIN ont.producer p ON p.id = a.producer JOIN ont.product d ON d.id = a.product""").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM ont.contract_party WHERE id NOT LIKE 'LIFE:%' AND id NOT LIKE 'ANN:%'").fetchone()[0] == 0
    dangling = con.execute("""SELECT count(*) FROM ont.death_claim c LEFT JOIN ont.contract_party p ON p.id = c.claimant
                              WHERE p.id IS NULL""").fetchone()[0]
    assert dangling == 0


def test_agent_layer_hides_and_redacts(con):
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'ont_agent' AND table_name = 'insured_person'").fetchall()}
    assert "birth_date" not in cols and "smoker_status" in cols
    assert {r[0] for r in con.execute("SELECT DISTINCT cause_of_death FROM ont_agent.death_claim").fetchall()} == {"[REDACTED]"}
    assert "tax_id" not in {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'ont_agent' AND table_name = 'contract_party'").fetchall()}


# ------------------------------------------------------------ governed metrics

def test_metric_definitions_resolve(northwind):
    assert SemanticLayer(northwind).validate() == []


def test_calibrated_ranges(sl, con):
    v = NORTHWIND_VALUATION
    lob = _by(sl.query(con, ["placement_rate", "application_count"], by=["line_of_business"], as_of=v),
              "line_of_business")
    assert 0.70 < lob["individual_life"]["placement_rate"] < 0.85
    assert 0.85 < lob["individual_annuity"]["placement_rate"] < 0.95
    uw = sl.query(con, ["uw_approval_rate", "accelerated_uw_share", "substandard_rate"], by=["year"], as_of=v).records()
    assert all(0.85 < r["uw_approval_rate"] < 0.93 and 0.07 < r["substandard_rate"] < 0.13 for r in uw)
    accel = {r["year"]: r["accelerated_uw_share"] for r in uw}
    assert accel[2019] < 0.25 and accel[2026] > 0.5                 # accelerated underwriting keeps growing
    days = {r["issue_year"]: r["average_days_to_issue"] for r in
            sl.query(con, ["average_days_to_issue"], by=["issue_year"], as_of=v).records()}
    assert 25 < days[2019] < 35 and 15 < days[2026] < days[2019]
    book = {k: float(x) for k, x in sl.query(
        con, ["inforce_policy_count", "face_amount_in_force", "inforce_contract_count", "account_value",
              "net_amount_at_risk", "statutory_reserve"], as_of=v).records()[0].items()}
    assert 14000 < book["inforce_policy_count"] < 19000 and 8000 < book["inforce_contract_count"] < 12000
    assert 300_000 < book["face_amount_in_force"] / book["inforce_policy_count"] < 700_000
    assert book["face_amount_in_force"] * 0.95 < book["net_amount_at_risk"] < book["face_amount_in_force"]
    assert 0.9 < book["statutory_reserve"] / book["account_value"] < 1.15
    pers = sl.query(con, ["persistency_13_month"], by=["issue_year"], where={"issue_year": list(range(2019, 2026))},
                    as_of=v).records()
    assert all(0.86 < r["persistency_13_month"] < 0.94 for r in pers)
    rates = _by(sl.query(con, ["lapse_rate", "surrender_rate"], by=["year"], as_of=v), "year")
    assert all(0.05 < rates[y]["lapse_rate"] < 0.08 for y in range(2021, 2027))
    assert all(0.03 < rates[y]["surrender_rate"] < 0.09 for y in range(2022, 2027))
    claims = _by(sl.query(con, ["average_days_to_pay", "contestable_claim_share"], by=["line_of_business"], as_of=v),
                 "line_of_business")
    assert 10 < claims["individual_annuity"]["average_days_to_pay"] < claims["individual_life"]["average_days_to_pay"] < 50
    assert claims["individual_annuity"]["contestable_claim_share"] == 0
    assert 0.65 < sl.query(con, ["mortality_ae_ratio"], as_of=v).records()[0]["mortality_ae_ratio"] < 0.95
    suit = sl.query(con, ["suitability_rejection_rate", "replacement_share"], as_of=v).records()[0]
    assert 0.04 < suit["suitability_rejection_rate"] < 0.09 and 0.18 < suit["replacement_share"] < 0.26


def test_caps_peak_in_2023_and_fall(sl, con):
    caps = {r["year"]: r["average_cap_rate"] for r in sl.query(
        con, ["average_cap_rate"], by=["year"], where={"product_type": "fixed_indexed"}, as_of=NORTHWIND_VALUATION).records()}
    assert caps[2021] < 0.05 and 0.095 < caps[2023] <= 0.105
    assert caps[2023] >= caps[2024] > caps[2025] > caps[2026] > 0.08
    credited = _by(sl.query(con, ["average_credited_rate"], by=["year"], where={"product_type": "fixed_deferred"}),
                   "year")
    assert credited[2021]["average_credited_rate"] < 0.03 < 0.045 < credited[2025]["average_credited_rate"]


def test_flows_reconcile_to_the_annuity_platform(sl, con):
    v = NORTHWIND_VALUATION
    r = sl.query(con, ["annuity_deposits", "annuity_withdrawals", "annuity_surrenders", "annuity_net_flows"],
                 as_of=v).records()[0]
    raw = dict(con.execute("""SELECT txn_type, sum(amount) FROM ann_src.txn WHERE txn_date <= ? GROUP BY 1""",
                           [v]).fetchall())
    assert float(r["annuity_deposits"]) == pytest.approx(float(raw["DEPOSIT"] + raw["1035_IN"]), abs=1.0)
    assert float(r["annuity_surrenders"]) == pytest.approx(float(raw["SURRENDER"] + raw["1035_OUT"]), abs=1.0)
    assert float(r["annuity_net_flows"]) == pytest.approx(
        float(r["annuity_deposits"] - r["annuity_withdrawals"] - r["annuity_surrenders"]), abs=1.0)
    face = con.execute("""SELECT sum(FACE_AMT) FROM lifepas.POLMSTR WHERE STAT_CD = 'A'""").fetchone()[0]
    assert float(sl.query(con, ["face_amount_in_force"], as_of=v).records()[0]["face_amount_in_force"]) == float(face)


def test_weighted_new_premium_and_lineage(sl, con):
    from insurance_context.lineage import explain
    r = sl.query(con, ["weighted_new_premium", "new_annualized_premium", "annuity_single_premium"],
                 by=["issue_year"], as_of=NORTHWIND_VALUATION).records()
    for x in r:
        assert float(x["weighted_new_premium"]) == pytest.approx(
            float(x["new_annualized_premium"]) + 0.1 * float(x["annuity_single_premium"]), abs=1.0)
    t = explain(sl, ["placement_rate"], ["channel"], {}, NORTHWIND_VALUATION, con)
    assert t["semantic"]["metrics"][0]["formula"] == "placed_application_count ÷ application_count"


def test_as_of_is_reproducible_and_monotonic(sl, con):
    def q(d):
        return sl.query(con, ["issued_policy_count", "annuity_deposits", "death_claim_count"], as_of=d).records()[0]
    early, late, again = q(date(2024, 12, 31)), q(NORTHWIND_VALUATION), q(date(2024, 12, 31))
    assert early == again
    assert all(early[k] < late[k] for k in early)
    # Balances are point in time: account value at an earlier valuation is a different snapshot.
    av = [sl.query(con, ["account_value"], as_of=d).records()[0]["account_value"]
          for d in (date(2022, 12, 31), NORTHWIND_VALUATION)]
    assert 0 < av[0] < av[1]


def test_invalid_cut_is_refused_not_allocated(sl, con):
    # Crediting strategies are rate sheets, not contracts: they have no tax qualification.
    with pytest.raises(MetricError, match="not defined for CreditingStrategy"):
        sl.query(con, ["average_cap_rate"], by=["tax_qualification"])
    assert sl.query(con, ["account_value"], by=["tax_qualification"]).rows


# ---------------------------------------------------------- planted signals

def test_drift_finds_the_planted_signals(northwind, con):
    findings = {(f.unit, f.entity, f.slot, f.check): f.sample for f in validate(northwind, con)}
    assert findings == {("LIFE", "Rider", "rider_type", "enum"): ["rop"],
                        ("ANN", "CreditingStrategy", "crediting_method", "enum"): ["trigger"],
                        ("ANN", "StatutoryReserve", "reserve_method", "enum"): ["vm22pbr"]}
    proposals = run(northwind, con)
    assert {(p.kind, p.target) for p in proposals} == {("new_enum_value", "RiderTypeEnum"),
                                                       ("new_enum_value", "CreditingMethodEnum"),
                                                       ("new_enum_value", "ReserveMethodEnum")}
    assert all(p.status == "verified" for p in proposals)


def test_signals_start_when_the_documents_say(sl, con):
    v = NORTHWIND_VALUATION
    by_method = {(r["year"], r["reserve_method"]) for r in sl.query(
        con, ["statutory_reserve"], by=["year", "reserve_method"], as_of=v).records()}
    assert ("vm22pbr" in {m for _, m in by_method}) and {y for y, m in by_method if m == "vm22pbr"} == {2026}
    first = con.execute("""SELECT min(rate_effective_date) FROM ont.crediting_strategy
                           WHERE crediting_method = 'trigger'""").fetchone()[0]
    assert first == date(2026, 3, 1)
    rop = con.execute("""SELECT min(r.effective_date), count(DISTINCT p.product) FROM ont.rider r
                         JOIN ont.life_policy p ON p.id = r.policy WHERE r.rider_type = 'rop'""").fetchone()
    assert rop[0] >= date(2026, 1, 1) and rop[1] >= 2


def test_mortality_selection_shows_by_cohort(sl, con):
    def ae(years):
        return sl.query(con, ["mortality_ae_ratio"], where={"issue_year": years},
                        as_of=NORTHWIND_VALUATION).records()[0]["mortality_ae_ratio"]
    assert ae([2019, 2020, 2021]) > ae([2023, 2024, 2025]) + 0.1


# ---------------------------------------------------------------- the corpus

def test_answer_key_points_at_real_pages():
    key = yaml.safe_load((CORPUS / "answer_key.yaml").read_text())
    docs = {p.name: p.read_text() for p in CORPUS.glob("*.md")}
    assert 10 <= len(docs) <= 13
    for name, text in docs.items():
        meta = yaml.safe_load(text.split("---", 2)[1])
        assert {"title", "kind", "author", "date", "source"} <= set(meta), name
        assert meta.get("fictional") is True and "fictional" in meta["title"].lower(), name
    for item in key["needles"] + key["decoys"]:
        for doc in item.get("docs") or [item["doc"]]:
            assert doc in docs, doc
            if "page" in item:
                assert re.search(rf"^## Page {item['page']}\s*$", docs[doc], re.M), (doc, item["page"])
    assert {d["doc"] for d in key["decoys"]}.isdisjoint({n.get("doc") for n in key["needles"]})
    assert {n["layer"] for n in key["needles"] if "layer" in n} == {"pack", "client"}
    corroborated = [n for n in key["needles"] if n.get("corroborated_by")]
    assert len(corroborated) >= 3
    sources = " ".join(n["corroborated_by"] for n in corroborated)
    assert "VM22PBR" in sources and "TRIGGER" in sources and "ROP" in sources
