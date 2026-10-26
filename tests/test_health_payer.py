"""Health payer pack (commercial + Medicare Advantage) on the Meridian Health Plan reference client."""

import re
from datetime import date

import duckdb
import pytest
import sqlglot
import yaml

from insurance_context import sqlgen
from insurance_context.client import Client
from insurance_context.drift import run
from insurance_context.fixtures.payer_warehouse import MERIDIAN_VALUATION
from insurance_context.overlay import resolve
from insurance_context.semantic import MetricError, SemanticLayer
from insurance_context.validate import validate

CORE = "https://ontology.tigeranalytics.com/insurance/core/"
HP = "https://ontology.tigeranalytics.com/insurance/health-payer/"
MHP = "https://ontology.tigeranalytics.com/clients/meridian-health/"
CORPUS = Client("meridian_health").dir / "corpus"


@pytest.fixture(scope="module")
def con(meridian_wh):
    c = duckdb.connect(str(meridian_wh), read_only=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def sl(meridian):
    return SemanticLayer(meridian)


def _by(res, key):
    return {r[key]: r for r in res.records()}


# ------------------------------------------------------------------ the pack

def test_pack_extends_the_kernel_without_moving_iris(meridian):
    for schema in (resolve("health_payer"), meridian.resolved, *(u.resolved for u in meridian.units)):
        sv = schema.view
        assert sv.get_uri("Claim", expand=True) == CORE + "Claim"
        # IRIs that existed before the MA / pharmacy / contracting depth was added.
        for cls in ("Member", "Provider", "HealthPlan", "Enrollment", "MedicalClaim", "MedicalClaimLine",
                    "PriorAuthorization", "BenefitAccumulator"):
            assert sv.get_uri(cls, expand=True) == HP + cls
        for slot in ("member_number", "claim_type", "pa_status", "denial_reason_code"):
            assert sv.get_uri(sv.get_slot(slot), expand=True) == HP + slot
        for enum in ("ClaimStatusEnum", "PriorAuthStatusEnum", "PlanTypeEnum"):
            assert sv.get_uri(sv.get_enum(enum), expand=True) == HP + enum
        assert sv.get_uri(sv.get_enum("LineOfBusinessEnum"), expand=True) == CORE + "LineOfBusinessEnum"


def test_new_concepts_specialize_the_kernel():
    sv = resolve("health_payer").view
    assert "Claim" in sv.class_ancestors("PharmacyClaim")
    assert "Decision" in sv.class_ancestors("Appeal")
    assert "Provider" in sv.class_ancestors("Pharmacy")
    assert "Organization" in sv.class_ancestors("EmployerGroup")
    assert sv.get_uri("CapitationPayment", expand=True) == HP + "CapitationPayment"
    for enum in sv.all_enums().values():         # every enum is pinned
        assert enum.enum_uri, enum.name


def test_sensitive_fields_are_marked():
    sv = resolve("health_payer").view
    for cls, slot, level in (("Member", "medicare_beneficiary_identifier", "phi"), ("MedicalClaim", "primary_diagnosis_code", "phi"),
                             ("DiagnosisSubmission", "diagnosis_code", "phi"), ("MedicalClaim", "admission_date", "phi"),
                             ("Member", "birth_date", "pii"), ("Grievance", "grievance_description", "phi")):
        ann = sv.induced_slot(slot, cls).annotations["sensitivity"]
        assert getattr(ann, "value", ann) == level, (cls, slot)


def test_cms_identifiers_are_global():
    sv = resolve("health_payer").view
    for cls in ("MedicareAdvantageContract", "PlanBenefitPackage", "HierarchicalConditionCategory", "StarMeasure"):
        ann = sv.get_class(cls).annotations["identity_scope"]
        assert getattr(ann, "value", ann) == "global"


# ----------------------------------------------------------------- the client

def test_meridian_vocabulary_extensions_and_restrictions(meridian):
    sv = meridian.resolved.view
    assert sv.get_class("EmployerGroup").title == "Account"
    assert sv.get_class("Member").title is None                     # the MA unit says "Beneficiary"
    ma = meridian.unit("MA").resolved.view
    assert ma.get_class("Member").title == "Beneficiary"
    assert sv.get_uri(sv.get_slot("mhp_um_delegate"), expand=True) == MHP + "mhp_um_delegate"
    rng = sv.induced_slot("claim_type", "MedicalClaim").range
    assert set(sv.get_enum(rng).permissible_values) == {"professional", "institutional"}
    rng = sv.induced_slot("snp_type", "PlanBenefitPackage").range
    assert set(sv.get_enum(rng).permissible_values) == {"none", "dual_eligible"}
    assert sv.induced_slot("plan", "MedicalClaim").required
    assert "ma_mmr_adjustment_reason" not in sv.all_slots()
    assert "ma_mmr_adjustment_reason" in {s.name for s in ma.class_induced_slots("CapitationPayment")}


@pytest.mark.parametrize("dialect", sqlgen.DIALECTS)
def test_meridian_views_are_portable(meridian, dialect):
    assert sqlgen.portability_issues(meridian, dialect) == []
    for view in sqlgen.all_views(meridian):
        sqlglot.parse_one(view.ddl(dialect), read=dialect)


def test_units_union_and_shared_reference_data(con):
    members = dict(con.execute("SELECT source_system, count(*) FROM ont.member GROUP BY 1").fetchall())
    assert set(members) == {"COMM", "MA"} and members["COMM"] > members["MA"] > 1000
    # Providers and drugs are mastered once (NPI / NDC keys); both units' claims resolve to them.
    assert con.execute("SELECT count(*) FROM ont.provider WHERE id LIKE '%:%'").fetchone()[0] == 0
    dangling = con.execute("""
        SELECT count(*) FROM ont.medical_claim c LEFT JOIN ont.provider p ON p.id = c.billing_provider
        WHERE p.id IS NULL""").fetchone()[0]
    assert dangling == 0
    assert con.execute("""SELECT count(DISTINCT c.source_system) FROM ont.pharmacy_claim c
                          JOIN ont.drug d ON d.id = c.drug""").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM ont.member WHERE id NOT LIKE 'COMM:%' AND id NOT LIKE 'MA:%'").fetchone()[0] == 0


def test_agent_layer_hides_and_redacts_phi(con):
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'ont_agent' AND table_name = 'member'").fetchall()}
    assert "birth_date" not in cols and "medicare_beneficiary_identifier" not in cols
    dx = {r[0] for r in con.execute("SELECT DISTINCT diagnosis_code FROM ont_agent.diagnosis_submission").fetchall()}
    assert dx == {"[REDACTED]"}


# ------------------------------------------------------------ governed metrics

def test_metric_definitions_resolve(meridian, acme):
    assert SemanticLayer(meridian).validate() == []
    assert SemanticLayer(acme).validate() == []


def test_calibrated_ranges(sl, con):
    r = _by(sl.query(con, ["medical_pmpm", "pharmacy_pmpm", "medical_loss_ratio", "admits_per_1000",
                           "pa_approval_rate", "generic_dispensing_rate", "average_length_of_stay"],
                     by=["line_of_business"], as_of=MERIDIAN_VALUATION), "line_of_business")
    comm, ma = r["commercial_health"], r["medicare_advantage"]
    assert 450 < comm["medical_pmpm"] < 550 and 1000 < ma["medical_pmpm"] < 1200
    assert 100 < comm["pharmacy_pmpm"] < 160 and 150 < ma["pharmacy_pmpm"] < 250
    assert 0.82 < comm["medical_loss_ratio"] < 0.88 and 0.82 < ma["medical_loss_ratio"] < 0.88
    assert 50 < comm["admits_per_1000"] < 60 and 250 < ma["admits_per_1000"] < 300
    assert 0.80 < comm["pa_approval_rate"] < 0.90 and 0.80 < ma["pa_approval_rate"] < 0.90
    assert 0.80 < comm["generic_dispensing_rate"] < 0.95
    assert 3 < comm["average_length_of_stay"] < ma["average_length_of_stay"] < 6
    risk = sl.query(con, ["average_risk_score"], by=["payment_year"]).records()
    assert all(0.9 < x["average_risk_score"] < 1.1 for x in risk)


def test_paid_claims_reconcile_to_both_platforms(sl, con):
    paid = {r["unit"]: float(r["medical_claims_paid"]) for r in
            sl.query(con, ["medical_claims_paid"], by=["unit"], as_of=MERIDIAN_VALUATION).records()}
    comm = con.execute("""SELECT sum(PAID_AMT) FROM comm_raw.CLM_HDR
                          WHERE PD_DT <= ? AND CLM_STS <> 'VD'""", [MERIDIAN_VALUATION]).fetchone()[0]
    ma = con.execute("""SELECT sum(plan_paid) FROM ma_src.claim
                        WHERE finalized_date <= ? AND status <> 'VOID'""", [MERIDIAN_VALUATION]).fetchone()[0]
    assert paid["COMM"] == pytest.approx(float(comm), abs=1.0)
    assert paid["MA"] == pytest.approx(float(ma), abs=1.0)


def test_mlr_is_a_ratio_of_sums_on_risk_business(sl, con):
    r = sl.query(con, ["medical_loss_ratio", "medical_claims_paid_insured", "provider_capitation",
                       "pharmacy_claims_paid_insured", "quality_improvement_expense", "premium_revenue",
                       "medical_claims_paid"], as_of=MERIDIAN_VALUATION).records()[0]
    num = sum(float(r[k]) for k in ("medical_claims_paid_insured", "provider_capitation",
                                     "pharmacy_claims_paid_insured", "quality_improvement_expense"))
    assert r["medical_loss_ratio"] == pytest.approx(num / float(r["premium_revenue"]))
    assert r["medical_claims_paid_insured"] < r["medical_claims_paid"]          # ASO claims excluded
    aso = sl.query(con, ["premium_billed"], where={"funding_type": "aso"}).records()[0]
    assert aso["premium_billed"] == 0


def test_per_1000_scale_and_lineage(sl, con):
    from insurance_context.lineage import explain
    r = sl.query(con, ["admits_per_1000", "inpatient_admits", "member_months"], as_of=MERIDIAN_VALUATION).records()[0]
    assert r["admits_per_1000"] == pytest.approx(r["inpatient_admits"] / r["member_months"] * 12000)
    t = explain(sl, ["admits_per_1000"], ["line_of_business"], {}, MERIDIAN_VALUATION, con)
    assert t["semantic"]["metrics"][0]["formula"] == "inpatient_admits ÷ member_months × 12000"


def test_as_of_is_reproducible_and_monotonic(sl, con):
    def q(d):
        return sl.query(con, ["medical_claims_paid", "member_months"], as_of=d).records()[0]
    early, late, again = q(date(2025, 12, 31)), q(MERIDIAN_VALUATION), q(date(2025, 12, 31))
    assert early == again
    assert early["medical_claims_paid"] < late["medical_claims_paid"] and early["member_months"] < late["member_months"]


def test_invalid_cut_is_refused_not_allocated(sl, con):
    # Quality improvement expense is booked by line and segment only, so MLR cannot be cut by metal tier.
    with pytest.raises(MetricError, match="not defined for QualityImprovementExpense"):
        sl.query(con, ["medical_loss_ratio"], by=["metal_tier"])
    assert sl.query(con, ["medical_pmpm"], by=["metal_tier"]).rows


# ---------------------------------------------------------- planted signals

def test_drift_finds_the_planted_ma_signals(meridian, con):
    findings = {(f.unit, f.entity, f.slot, f.check): f.sample for f in validate(meridian, con)}
    assert findings == {("MA", "MemberRiskScore", "risk_score_basis", "enum"): ["v28"],
                        ("MA", "MedicalClaim", "denial_category", "enum"): ["clinical_validation"]}
    proposals = run(meridian, con)
    assert {(p.kind, p.target) for p in proposals} == {("new_enum_value", "RiskScoreBasisEnum"),
                                                       ("new_enum_value", "DenialCategoryEnum")}
    assert all(p.status == "verified" for p in proposals)


def test_v28_switch_shows_by_payment_year(sl, con):
    rows = sl.query(con, ["risk_scored_member_count"], by=["payment_year", "risk_score_basis"]).records()
    basis = {(r["payment_year"], r["risk_score_basis"]) for r in rows if r["risk_score_basis"] != "esrd"}
    assert basis == {(2024, "cms_hcc_v24_v28_blend"), (2025, "cms_hcc_v24_v28_blend"), (2026, "v28")}


def test_pa_timeliness_cluster_is_visible(sl, con):
    monthly = {r["month"]: r["pa_timeliness_rate"] for r in sl.query(
        con, ["pa_timeliness_rate"], by=["month"], where={"line_of_business": "medicare_advantage", "year": 2026},
        as_of=MERIDIAN_VALUATION).records()}
    assert monthly[date(2026, 3, 1)] < 0.85 and monthly[date(2026, 4, 1)] < 0.85
    assert monthly[date(2026, 1, 1)] > 0.9 and monthly[date(2026, 5, 1)] > 0.9
    by_delegate = _by(sl.query(con, ["pa_timeliness_rate"], by=["um_delegate"],
                               where={"urgency": "standard", "line_of_business": "medicare_advantage", "year": 2026}),
                      "um_delegate")
    worst = min(by_delegate.values(), key=lambda r: r["pa_timeliness_rate"])
    assert worst["um_delegate"] == "musculoskeletal_benefit_manager"


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
    corroborated = [n for n in key["needles"] if n.get("corroborated_by")]
    assert len(corroborated) >= 2
    sources = " ".join(n["corroborated_by"] for n in corroborated)
    assert "V28" in sources and "CLINICAL_VALIDATION" in sources and "MSK" in sources


# ------------------------------------------------------------------ agent filters
# An agent writes "MA" or "Medicare Advantage"; the data holds "medicare_advantage". An unmatched
# filter used to return a blank PMPM that agents reported as $0 (seen in the consistency lab).

@pytest.fixture(scope="module")
def kit(meridian, meridian_wh, tmp_path_factory):
    import shutil
    from insurance_context.agent_tools import ContextToolkit
    # own copy: this module's `con` fixture holds the warehouse open with a different configuration
    copy = tmp_path_factory.mktemp("meridian_agent") / "warehouse.duckdb"
    shutil.copy(meridian_wh, copy)
    return ContextToolkit(meridian, copy, as_of=MERIDIAN_VALUATION)


@pytest.mark.parametrize("spelling", ["medicare_advantage", "Medicare Advantage", "MA", "medicare-advantage"])
def test_filter_values_resolve_to_the_data_codes(kit, spelling):
    out = kit.compute_metrics(["medical_pmpm"], filters={"line_of_business": spelling})
    exact = kit.compute_metrics(["medical_pmpm"], filters={"line_of_business": "medicare_advantage"})
    assert out.split("\n\n")[1] == exact.split("\n\n")[1]
    assert "No data matches" not in out
    if spelling != "medicare_advantage":
        assert "as 'medicare_advantage'" in out


def test_unknown_filter_value_lists_the_valid_ones(kit):
    out = kit.compute_metrics(["medical_pmpm"], filters={"line_of_business": "dental"})
    assert out.startswith("Filter error") and "medicare_advantage" in out and "commercial_health" in out


def test_empty_result_is_called_out(kit):
    out = kit.compute_metrics(["medical_cost"], filters={"year": 1999})
    assert "No data matches these filters" in out
