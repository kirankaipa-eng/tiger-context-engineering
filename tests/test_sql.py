"""Governed views: portability, identity, lineage and redaction."""

import duckdb
import pytest
import sqlglot

from insurance_context import sqlgen


@pytest.mark.parametrize("dialect", sqlgen.DIALECTS)
@pytest.mark.parametrize("client_name", ["summit", "acme"])
def test_views_are_portable(dialect, client_name, request):
    client = request.getfixturevalue(client_name)
    assert sqlgen.portability_issues(client, dialect) == []
    for view in sqlgen.all_views(client):
        sqlglot.parse_one(view.ddl(dialect), read=dialect)


def test_group_layer_unions_units(summit_con):
    rows = dict(summit_con.execute(
        "SELECT source_system, count(*) FROM ont.policy GROUP BY 1").fetchall())
    assert set(rows) == {"SES", "HPG"}
    assert all(n > 100 for n in rows.values())


def test_identities_are_unit_qualified_except_global(summit_con):
    ids = summit_con.execute("SELECT id, producer, operating_unit FROM ont.policy").fetchall()
    assert all(i.split(":")[0] in ("SES", "HPG") for i, _, _ in ids)
    assert all(":" not in p for _, p, _ in ids if p)          # group broker master
    assert {u for _, _, u in ids} == {"SES", "HPG"}             # global operating units
    dupes = summit_con.execute(
        "SELECT count(*) FROM (SELECT id FROM ont.policy GROUP BY id HAVING count(*) > 1)").fetchone()[0]
    assert dupes == 0


def test_every_policy_reference_resolves(summit_con):
    dangling = summit_con.execute("""
        SELECT count(*) FROM ont.commercial_claim c
        LEFT JOIN ont.policy p ON p.id = c.policy WHERE p.id IS NULL""").fetchone()[0]
    assert dangling == 0


def test_agent_layer_redacts_pii(summit_con):
    names = {r[0] for r in summit_con.execute("SELECT DISTINCT name FROM ont_agent.claimant").fetchall()}
    assert names <= {"[REDACTED]", None}
    raw = summit_con.execute("SELECT count(DISTINCT name) FROM ont.claimant").fetchone()[0]
    assert raw > 10
    fein = {r[0] for r in summit_con.execute("SELECT DISTINCT tax_id FROM ont_agent.insured").fetchall()}
    assert fein <= {"[REDACTED]", None}


def test_hidden_columns_are_absent_for_agents(acme_wh):
    con = duckdb.connect(str(acme_wh), read_only=True)
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'ont_agent' AND table_name = 'member'").fetchall()}
    assert "birth_date" not in cols and "member_number" in cols


def test_bordereau_snapshots_become_transactions(summit_con):
    # Sum of derived deltas must equal the latest cumulative snapshot for every claim.
    mismatches = summit_con.execute("""
        WITH latest AS (
          SELECT CLAIM_REF, PAID_TD, OS_RESERVE,
                 row_number() OVER (PARTITION BY CLAIM_REF ORDER BY BDX_MONTH DESC) rn
          FROM hpg.BDX_CLAIMS),
        derived AS (
          SELECT claim,
                 sum(CASE WHEN financial_type='payment' AND cost_category='loss' THEN amount ELSE 0 END) paid,
                 sum(CASE WHEN financial_type='reserve_change' AND cost_category='loss' THEN amount ELSE 0 END) os
          FROM ont.claim_financial_transaction WHERE source_system='HPG' GROUP BY 1)
        SELECT count(*) FROM latest l JOIN derived d ON d.claim = 'HPG:' || l.CLAIM_REF
        WHERE l.rn = 1 AND (abs(l.PAID_TD - d.paid) > 0.01 OR abs(l.OS_RESERVE - d.os) > 0.01)""").fetchone()[0]
    assert mismatches == 0
