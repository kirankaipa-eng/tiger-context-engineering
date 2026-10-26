"""Agent guardrails and export artifacts."""

import importlib.util
import json

import pytest
from rdflib import Graph, Namespace, RDFS, URIRef

from insurance_context.agent_tools import ContextToolkit, SQLRejected, check_sql
from insurance_context.exports import build


@pytest.mark.parametrize("sql", [
    "SELECT * FROM ses.cc_claim",
    "SELECT * FROM ont.claimant",
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM ont_agent.policy WHERE id IN (SELECT id FROM ont.policy)",
    "DROP VIEW ont_agent.policy",
    "COPY ont_agent.policy TO '/tmp/x.csv'",
    "SELECT 1; SELECT 2",
    "ATTACH 'other.duckdb'",
])
def test_sql_guard_rejects(sql):
    with pytest.raises(SQLRejected):
        check_sql(sql)


def test_sql_guard_allows_ctes_over_agent_schema():
    check_sql("WITH x AS (SELECT source_system, count(*) n FROM ont_agent.policy GROUP BY 1) SELECT * FROM x")


def test_toolkit_answers_with_definitions_and_valuation(summit, summit_wh, as_of):
    kit = ContextToolkit(summit, summit_wh, as_of=as_of)
    out = kit.compute_metrics(["ultimate_loss_ratio"], by=["line_group"])
    assert "Valuation date: 2026-06-30" in out and "casualty" in out and "Definitions used" in out
    assert "not defined for ReserveEstimate" in kit.compute_metrics(["ibnr"], by=["program"])
    assert "Claim File" in kit.describe_concept("claim")
    assert "[REDACTED]" in kit.run_sql("SELECT name FROM ont_agent.claimant LIMIT 1")


def test_build_exports_every_target(summit, tmp_path):
    m = build(summit, tmp_path)
    for key in ("owl", "shacl", "jsonschema", "pydantic", "r2rml", "context", "metrics",
                "sql:duckdb", "sql:snowflake", "sql:databricks", "sql:postgres"):
        assert (tmp_path / m["artifacts"][key]["path"]).stat().st_size > 0
    assert m["line_version"] == "0.1.0"

    g = Graph().parse(tmp_path / "owl" / "summit_group.ttl")
    ins = Namespace("https://ontology.tigeranalytics.com/insurance/core/")
    pc = Namespace("https://ontology.tigeranalytics.com/insurance/commercial-pc/")
    assert (pc.CommercialClaim, RDFS.subClassOf, ins.Claim) in g
    assert (pc.Policy, RDFS.subClassOf, ins.Coverage) in g

    rr = Graph().parse(tmp_path / "r2rml" / "summit_group.ttl")
    assert len(set(rr.subjects(URIRef("http://www.w3.org/ns/r2rml#logicalTable"), None))) == 20

    catalog = json.loads((tmp_path / "metrics" / "catalog.json").read_text())
    assert {"ultimate_loss_ratio", "hit_ratio", "renewal_retention"} <= {x["name"] for x in catalog["metrics"]}

    spec = importlib.util.spec_from_file_location("models", tmp_path / "pydantic" / "summit_group.py")
    models = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(models)
    with pytest.raises(Exception):
        models.CommercialClaim(id="x", claim_status="not_a_status", received_date="2026-01-01",
                               policy="p", loss_date="2026-01-01", coverage_line="general_liability")
