"""Plains Health Plan: the medical economics (MEE) reference client on tigerclaw's synthetic data.

These pin what the demo relies on: the data conforms, the book is calibrated to typical
payer levels, the observation-stay needle and the seeded modifier anomalies are present,
and metrics the warehouse cannot support are refused with a reason.
"""

import duckdb
import pytest

from insurance_context.fixtures.mee_warehouse import PLAINS_VALUATION
from insurance_context.semantic import MetricError, SemanticLayer
from insurance_context.validate import validate


@pytest.fixture(scope="module")
def con(plains_wh):
    c = duckdb.connect(str(plains_wh), read_only=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def sl(plains):
    return SemanticLayer(plains)


def _q(sl, con, metrics, by=(), where=None):
    return sl.query(con, metrics, list(by), where or {}, PLAINS_VALUATION).records()


def test_definitions_resolve_and_data_conforms(plains, sl, con):
    """One planted finding: expired authorizations carry a status the ontology lacks (the UM-021 needle)."""
    assert sl.validate() == []
    findings = validate(plains, con)
    assert [(f.entity, f.slot, f.check) for f in findings] == [("PriorAuthorization", "pa_status", "enum")]
    assert findings[0].sample == ["expired"] and findings[0].violations == 123


def test_book_is_calibrated_to_typical_payer_levels(sl, con):
    (book,) = _q(sl, con, ["medical_pmpm", "pharmacy_pmpm", "admits_per_1000"])
    assert 500 < book["medical_pmpm"] < 800
    assert 100 < book["pharmacy_pmpm"] < 180
    by_lob = {r["line_of_business"]: r for r in _q(sl, con, ["medical_pmpm"], ["line_of_business"])}
    assert by_lob["medicare_advantage"]["medical_pmpm"] > 2 * by_lob["commercial_health"]["medical_pmpm"]
    assert by_lob["medicaid_managed_care"]["medical_pmpm"] < by_lob["commercial_health"]["medical_pmpm"]


def test_quarters_are_steady_until_runout(sl, con):
    q = {r["quarter"]: r["medical_pmpm"] for r in _q(sl, con, ["medical_pmpm"], ["quarter"])}
    full = [q[k] for k in ("2025-Q1", "2025-Q2", "2025-Q3")]
    assert max(full) / min(full) < 1.2                  # a steady book, not the IBNR demo's hump
    assert q["2026-Q1"] < 0.3 * q["2025-Q3"]            # the latest quarter is mostly unpaid at valuation


def test_observation_stays_are_counted_as_inpatient(sl, con):
    """The curator's needle: the pack says observation is outpatient; the warehouse says inpatient."""
    by_type = {r["admit_type"]: r for r in _q(sl, con, ["inpatient_admits", "average_length_of_stay"], ["admit_type"])}
    assert by_type["observation"]["inpatient_admits"] > 400
    assert by_type["observation"]["average_length_of_stay"] < 2
    (book,) = _q(sl, con, ["op_pmpm"])
    assert book["op_pmpm"] == 0


def test_seeded_modifier_anomalies_stand_out_in_their_cohorts(sl, con):
    seeded = con.execute("SELECT provider_id, kind FROM plh_src.anomalies").fetchall()
    rows = _q(sl, con, ["modifier_25_rate", "modifier_59_rate"], ["peer_cohort", "provider"])
    for pid, kind in seeded:
        metric = "modifier_25_rate" if "25" in kind else "modifier_59_rate"
        mine = next(r for r in rows if r["provider"].endswith(f"({pid})"))
        peers = [r[metric] for r in rows if r["peer_cohort"] == mine["peer_cohort"] and r is not mine]
        assert mine[metric] > 1.5 * (sum(peers) / len(peers)), (pid, kind)


def test_unsupported_metrics_are_refused_with_a_reason(sl):
    names = {m["name"] for m in sl.catalog()}
    assert "generic_dispensing_rate" not in names and "medical_loss_ratio" not in names
    with pytest.raises(MetricError, match="no brand/generic flag"):
        sl.compile(["generic_dispensing_rate"])
    with pytest.raises(MetricError, match="which no unit maps"):
        sl.compile(["medical_loss_ratio"])


def test_agents_can_say_state_names(plains, plains_wh, tmp_path):
    import shutil
    from insurance_context.agent_tools import ContextToolkit
    copy = tmp_path / "wh.duckdb"
    shutil.copy(plains_wh, copy)
    kit = ContextToolkit(plains, copy, as_of=PLAINS_VALUATION)
    out = kit.compute_metrics(["ip_pmpm"], filters={"state": "Illinois", "line_of_business": "Medicare Advantage"})
    assert "read state='Illinois' as 'IL'" in out and "Filter error" not in out


def test_pi25_sweep_finds_the_seeded_providers(plains, plains_wh, tmp_path):
    """PI-25: more than 2x the peer cohort in two consecutive quarters, significant at 95%."""
    import shutil
    from insurance_context.agent_tools import ContextToolkit
    copy = tmp_path / "wh.duckdb"
    shutil.copy(plains_wh, copy)
    c = duckdb.connect(str(copy), read_only=True)
    seeded = {p for (p,) in c.execute("SELECT provider_id FROM plh_src.anomalies").fetchall()}
    c.close()
    kit = ContextToolkit(plains, copy, as_of=PLAINS_VALUATION)
    flagged = []
    for metric in ("modifier_25_rate", "modifier_59_rate"):
        out = kit.compare_to_peers(metric, "provider", "peer_cohort", volume_metric="coded_professional_claims",
                                   min_volume=10, threshold=2.0, periods=["2025-Q4", "2026-Q1"], min_z=1.645)
        flagged += [line.split("|")[2].strip() for line in out.splitlines() if line.startswith("| cohort")]
    found = {p for p in seeded if any(f.endswith(f"({p})") for f in flagged)}
    assert found == seeded
    assert len(flagged) - len(found) <= 2
