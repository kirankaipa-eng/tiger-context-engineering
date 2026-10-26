"""Life & annuity pack: kernel inheritance, IRI stability, overlays and exports."""

import pytest

from insurance_context.overlay import resolve

CORE = "https://ontology.tigeranalytics.com/insurance/core/"
LA = "https://ontology.tigeranalytics.com/insurance/life-annuity/"

# A fictional life & annuity carrier: its words, one extra field, one restriction.
NORTHWIND = {
    "client": "northwind_life",
    "prefix": "nwl",
    "namespace": "https://ontology.tigeranalytics.com/clients/northwind-life/",
    "labels": {"classes": {"AnnuityContract": "Deferred Annuity", "DeathClaim": "Mortality Claim"}},
    "extend": {"add_slots": {"AnnuityContract": {"nwl_block_code": {"description": "Reinsured block id."}}}},
    "restrict": [{"class": "LifeProduct", "slot": "life_product_type",
                  "allow": ["term", "indexed_universal_life"],
                  "reason": "Northwind only sells term and IUL."}],
}


@pytest.fixture(scope="module")
def la():
    return resolve("life_annuity")


def test_pack_specializes_the_kernel(la):
    sv = la.view
    assert "Coverage" in sv.class_ancestors("LifePolicy")
    assert "Coverage" in sv.class_ancestors("AnnuityContract")
    assert "Claim" in sv.class_ancestors("DeathClaim")
    assert "Decision" in sv.class_ancestors("SuitabilityReview")
    assert "Person" in sv.class_ancestors("InsuredPerson")


def test_kernel_iris_are_shared_across_lines(la):
    sv = la.view
    assert sv.get_uri("Claim", expand=True) == CORE + "Claim"
    assert sv.get_uri("DeathClaim", expand=True) == LA + "DeathClaim"
    for other in ("health_payer", "commercial_pc"):
        assert resolve(other).view.get_uri("Claim", expand=True) == CORE + "Claim"


def test_kernel_knows_the_new_lines(la):
    lob = la.view.get_enum("LineOfBusinessEnum").permissible_values
    assert {"individual_life", "individual_annuity", "group_benefits"} <= set(lob)


def test_sensitive_fields_are_marked(la):
    sv = la.view
    for cls, slot in (("DeathClaim", "cause_of_death"), ("InsuredPerson", "smoker_status"),
                      ("Beneficiary", "beneficiary_party")):
        ann = sv.induced_slot(slot, cls).annotations["sensitivity"]
        assert getattr(ann, "value", ann) in {"phi", "pii"}


def test_client_overlay(la):
    sv = resolve("life_annuity", [NORTHWIND]).view
    assert sv.get_class("AnnuityContract").title == "Deferred Annuity"
    assert "nwl_block_code" in {s.name for s in sv.class_induced_slots("AnnuityContract")}
    rng = sv.induced_slot("life_product_type", "LifeProduct").range
    assert set(sv.get_enum(rng).permissible_values) == {"term", "indexed_universal_life"}
    assert sv.get_uri("AnnuityContract", expand=True) == LA + "AnnuityContract"


def test_curator_style_change_new_mortality_table():
    # The spec's life needle: an actuarial memo moves reserves to a new table.
    ov = {**NORTHWIND, "extend": {"enum_values": {"MortalityTableEnum": ["vm20_2026_improvement"]}}}
    sv = resolve("life_annuity", [ov]).view
    assert "vm20_2026_improvement" in sv.get_enum("MortalityTableEnum").permissible_values


def test_exports_owl_and_jsonschema(la, tmp_path):
    pytest.importorskip("linkml.generators.owlgen")
    from linkml.generators.jsonschemagen import JsonSchemaGenerator
    from linkml.generators.owlgen import OwlSchemaGenerator
    from rdflib import OWL, RDF, Graph, URIRef

    path = la.dump(tmp_path / "life_annuity.yaml")
    ttl = OwlSchemaGenerator(str(path), use_native_uris=False, consolidate_cardinality_axioms=True,
                             skip_vacuous_min_zero_cardinality_axioms=True,
                             skip_vacuous_local_range_axioms=True).serialize()
    g = Graph().parse(data=ttl, format="turtle")
    assert (URIRef(LA + "AnnuityContract"), RDF.type, OWL.Class) in g
    assert (URIRef(CORE + "Coverage"), RDF.type, OWL.Class) in g
    assert "AnnuityContract" in JsonSchemaGenerator(str(path)).serialize()
