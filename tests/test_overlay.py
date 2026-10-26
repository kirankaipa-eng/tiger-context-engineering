"""Ontology resolution: layering, IRI stability and overlay operations."""

import pytest

from insurance_context.overlay import OverlayError, resolve

CORE = "https://ontology.tigeranalytics.com/insurance/core/"
PC = "https://ontology.tigeranalytics.com/insurance/commercial-pc/"
SSG = "https://ontology.tigeranalytics.com/clients/summit-group/"


def test_base_iris_survive_client_and_unit_overlays(summit):
    for schema in (summit.resolved, *(u.resolved for u in summit.units)):
        sv = schema.view
        assert sv.get_uri("Claim", expand=True) == CORE + "Claim"
        assert sv.get_uri("CommercialClaim", expand=True) == PC + "CommercialClaim"
        assert sv.get_uri(sv.get_slot("operating_unit"), expand=True) == PC + "operating_unit"
        assert sv.get_uri(sv.get_enum("CoverageStatusEnum"), expand=True) == CORE + "CoverageStatusEnum"


def test_client_extensions_live_in_client_namespace(summit):
    sv = summit.resolved.view
    assert sv.get_uri(sv.get_slot("ssg_underwriting_region"), expand=True) == SSG + "ssg_underwriting_region"
    assert "ssg_underwriting_region" in {s.name for s in sv.class_induced_slots("Policy")}


def test_unit_extensions_stay_out_of_group_schema(summit):
    assert "ses_surplus_lines_tax_state" not in summit.resolved.view.all_slots()
    ses = summit.unit("SES").resolved.view
    assert "ses_surplus_lines_tax_state" in {s.name for s in ses.class_induced_slots("Policy")}


def test_labels_are_vocabulary_not_identity(summit):
    cls = summit.resolved.view.get_class("CommercialClaim")
    assert cls.title == "Claim File"
    assert "Claim File" in cls.aliases


def test_require_and_global_identity(summit):
    sv = summit.resolved.view
    assert sv.induced_slot("coverage_line", "CommercialClaim").required
    ann = sv.get_class("Producer").annotations["identity_scope"]
    assert getattr(ann, "value", ann) == "global"


def test_restrict_creates_subset_enum(acme):
    sv = acme.resolved.view
    rng = sv.induced_slot("claim_type", "MedicalClaim").range
    assert rng == "AcmeMedicalClaimClaimTypeEnum"
    assert set(sv.get_enum(rng).permissible_values) == {"professional", "institutional"}


def test_overlay_cannot_redefine_base_elements():
    ov = {"client": "x", "prefix": "x", "namespace": "https://example.org/x/",
          "extend": {"enums": {"CauseOfLossEnum": ["a"]}}}
    with pytest.raises(OverlayError, match="redefines"):
        resolve("commercial_pc", [ov])


def test_restrict_rejects_unknown_values():
    ov = {"client": "x", "prefix": "x", "namespace": "https://example.org/x/",
          "restrict": [{"class": "CommercialClaim", "slot": "cause_of_loss", "allow": ["meteor"]}]}
    with pytest.raises(OverlayError, match="meteor"):
        resolve("commercial_pc", [ov])


def test_enum_values_extension():
    ov = {"client": "x", "prefix": "x", "namespace": "https://example.org/x/",
          "extend": {"enum_values": {"CauseOfLossEnum": [{"value": "pfas", "description": "PFAS"}]}}}
    sv = resolve("commercial_pc", [ov]).view
    assert "pfas" in sv.get_enum("CauseOfLossEnum").permissible_values
    with pytest.raises(OverlayError, match="already has"):
        resolve("commercial_pc", [{**ov, "extend": {"enum_values": {"CauseOfLossEnum": ["fire"]}}}])


def test_both_lines_share_the_kernel(summit, acme):
    pc, hp = summit.resolved.view, acme.resolved.view
    assert "Claim" in pc.class_ancestors("CommercialClaim")
    assert "Claim" in hp.class_ancestors("MedicalClaim")
    assert pc.get_uri("Claim", expand=True) == hp.get_uri("Claim", expand=True)
