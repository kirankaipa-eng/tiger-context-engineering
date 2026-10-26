"""Conformance validation and the drift -> verified proposal loop."""

import duckdb

from insurance_context import sqlgen
from insurance_context.drift import ChangeProposal, detect, run, verify
from insurance_context.validate import validate


def test_conformance_finds_emerging_cause_of_loss(summit, summit_con):
    findings = validate(summit, summit_con)
    assert [(f.unit, f.slot, f.check) for f in findings] == [("HPG", "cause_of_loss", "enum")]
    assert findings[0].sample == ["pfas"]


def test_conformance_catches_broken_source_rows(summit, summit_copy):
    con = duckdb.connect(str(summit_copy))
    con.execute("UPDATE ses.cc_claim SET LOSS_DT = NULL WHERE CLM_ID = (SELECT min(CLM_ID) FROM ses.cc_claim)")
    con.execute("UPDATE ses.cc_claim SET POL_ID = 'P999999' WHERE CLM_ID = (SELECT max(CLM_ID) FROM ses.cc_claim)")
    sqlgen.install(summit, con)
    checks = {(f.unit, f.entity, f.slot, f.check): f.violations for f in validate(summit, con)}
    assert checks[("SES", "CommercialClaim", "loss_date", "required")] == 1
    assert checks[("SES", "CommercialClaim", "policy", "reference")] == 1


def test_drift_detects_planted_industry_signals(summit, summit_con):
    by_source = {p.evidence.get("source"): p for p in run(summit, summit_con)}
    lit = by_source["ses.cc_claim.LITIGATION_FUNDING_IND"]
    assert lit.kind == "new_attribute" and lit.target == "CommercialClaim"
    tria = by_source["hpg.BDX_PREMIUM.TRIA_PREM"]
    assert tria.target == "PolicyTransaction"               # row grain, not an aggregate
    pfas = [p for p in by_source.values() if p.kind == "new_enum_value"]
    assert pfas and pfas[0].target == "CauseOfLossEnum"
    for p in (lit, tria, *pfas):
        assert p.status == "verified", p.verification


def test_restriction_breach_routes_to_data_owner(acme, acme_wh):
    con = duckdb.connect(str(acme_wh), read_only=True)
    kinds = {(p.kind, p.target) for p in detect(acme, con)}
    assert ("restriction_violation", "AcmeMedicalClaimClaimTypeEnum") in kinds
    assert ("new_enum_value", "ClaimStatusEnum") in kinds


def test_bad_suggestion_is_marked_invalid(summit):
    p = ChangeProposal(kind="new_attribute", layer="client:summit_group", target="Policy", title="t",
                       rationale="r", evidence={"source": "x"},
                       overlay_change={"extend": {"add_slots": {"NoSuchClass": {"x": {"range": "string"}}}}})
    assert verify(summit, p).status == "invalid"


def test_llm_enrichment_is_reverified(summit, summit_con):
    def fake_llm(prompt):
        return '{"rationale": "Industry-wide.", "layer": "pack", "name": "litigation_funding_disclosed", "range": "boolean"}'
    from insurance_context.drift import enrich
    ps = [p for p in detect(summit, summit_con) if "LITIGATION" in p.evidence.get("source", "")]
    enrich(summit, ps, fake_llm)
    p = verify(summit, ps[0])
    assert p.layer == "pack:commercial_pc" and p.enriched_by == "llm"
    assert "litigation_funding_disclosed" in p.overlay_change["extend"]["add_slots"]["CommercialClaim"]
    assert p.status == "verified"


def test_lineage_explains_every_layer(summit, summit_con, as_of):
    from insurance_context.lineage import explain
    from insurance_context.semantic import SemanticLayer
    t = explain(SemanticLayer(summit), ["ultimate_loss_ratio"], ["accident_year"],
                {"line_group": "casualty", "operating_unit": "HPG"}, as_of, summit_con)
    ents = {e["name"]: e for e in t["ontology"]["entities"]}
    assert ents["CommercialClaim"]["label"] == "Claim File"
    assert ents["CommercialClaim"]["curie"] == "pc:CommercialClaim"
    assert "ins:Claim" in [a["curie"] for a in ents["CommercialClaim"]["is_a"]]
    hops = {(h["from"], h["slot"], h["to"]) for h in t["ontology"]["relationships"]}
    assert {("ClaimFinancialTransaction", "claim", "CommercialClaim"), ("CommercialClaim", "policy", "Policy"),
            ("Policy", "operating_unit", "OperatingUnit")} <= hops
    assert "general_liability" in t["ontology"]["groupings"][0]["groups"]["casualty"]
    tree = t["semantic"]["metrics"][0]
    assert tree["kind"] == "ratio" and tree["children"][0]["formula"] == "reported_incurred_loss_alae + ibnr"
    cft = next(c for c in t["context"] if c["entity"] == "ClaimFinancialTransaction")
    hpg = next(u for u in cft["units"] if u["unit"] == "HPG")
    assert hpg["tables"] == ["hpg.BDX_CLAIMS"] and hpg["transform"] == ["snapshot deltas"] and hpg["rows"] > 0
    assert "SELECT" in t["sql"]


def test_ontology_model_shows_layers_sources_and_customization(summit, acme, summit_con):
    from insurance_context.catalog import ontology_model
    m = ontology_model(summit, summit_con)
    cls = {c["name"]: c for c in m["classes"]}
    claim = cls["CommercialClaim"]
    assert claim["label"] == "Claim File" and claim["curie"] == "pc:CommercialClaim" and claim["layer"] == "line"
    assert cls["Claim"]["layer"] == "core"
    assert "relabelled 'Claim File'" in claim["customized"] and "coverage_line: made required" in claim["customized"]
    assert {s["unit"] for s in claim["sources"]} == {"SES", "HPG"}
    assert "reported_claim_count" in claim["metrics"]
    policy_attrs = {a["name"]: a for a in cls["Policy"]["attributes"]}
    assert policy_attrs["ssg_underwriting_region"]["layer"] == "client"
    assert policy_attrs["ses_surplus_lines_tax_state"]["layer"] == "unit:SES"
    assert ("CommercialClaim", "policy", "Policy") in {(r["from"], r["slot"], r["to"]) for r in m["relationships"]}
    member = {c["name"]: c for c in ontology_model(acme)["classes"]}["Member"]
    assert "birth_date: hidden from agents" in member["customized"]
