"""Governed metrics: definitions resolve, and numbers reconcile to raw sources."""

from datetime import date

import pytest

from insurance_context.semantic import MetricError, SemanticLayer


@pytest.fixture(scope="module")
def sl(summit):
    return SemanticLayer(summit)


def _one(res, name):
    return float(res.records()[0][name])


def test_all_definitions_resolve(sl):
    assert sl.validate() == []


def test_written_premium_reconciles_to_sources(sl, summit_con, as_of):
    gwp = _one(sl.query(summit_con, ["gross_written_premium"], as_of=as_of), "gross_written_premium")
    ses = summit_con.execute("SELECT sum(PREM_CHG) FROM ses.pc_transaction WHERE BOOK_DT <= ?", [as_of]).fetchone()[0]
    hpg = summit_con.execute("SELECT sum(GROSS_PREM) FROM hpg.BDX_PREMIUM WHERE last_day(BDX_MONTH) <= ?",
                             [as_of]).fetchone()[0]
    assert gwp == pytest.approx(float(ses) + float(hpg), abs=1.0)


def test_paid_loss_reconciles_across_system_types(sl, summit_con, as_of):
    res = sl.query(summit_con, ["paid_loss"], by=["operating_unit"], as_of=as_of).records()
    paid = {r["operating_unit"]: float(r["paid_loss"]) for r in res}
    ses = summit_con.execute("""SELECT sum(AMT) FROM ses.cc_transaction
                                WHERE TXN_TYP='PMT' AND COST_TYP='LOSS' AND TXN_DT <= ?""", [as_of]).fetchone()[0]
    hpg = summit_con.execute("""SELECT sum(PAID_TD) FROM (
        SELECT PAID_TD, row_number() OVER (PARTITION BY CLAIM_REF ORDER BY BDX_MONTH DESC) rn
        FROM hpg.BDX_CLAIMS WHERE last_day(BDX_MONTH) <= ?) WHERE rn = 1""", [as_of]).fetchone()[0]
    assert paid["SES"] == pytest.approx(float(ses), abs=1.0)
    assert paid["HPG"] == pytest.approx(float(hpg), abs=1.0)


def test_earned_never_exceeds_written(sl, summit_con, as_of):
    r = sl.query(summit_con, ["gross_written_premium", "earned_premium"], by=["operating_unit"], as_of=as_of)
    for row in r.records():
        assert 0 < row["earned_premium"] <= float(row["gross_written_premium"]) * 1.0001


def test_dimension_partitions_are_additive(sl, summit_con, as_of):
    total = _one(sl.query(summit_con, ["earned_premium"], as_of=as_of), "earned_premium")
    by_group = sl.query(summit_con, ["earned_premium"], by=["line_group"], as_of=as_of).records()
    assert sum(r["earned_premium"] for r in by_group) == pytest.approx(total, rel=1e-9)
    assert "unassigned" not in {r["line_group"] for r in by_group}


def test_as_of_is_reproducible_and_monotonic(sl, summit_con):
    early = _one(sl.query(summit_con, ["paid_loss"], as_of=date(2024, 12, 31)), "paid_loss")
    late = _one(sl.query(summit_con, ["paid_loss"], as_of=date(2026, 6, 30)), "paid_loss")
    again = _one(sl.query(summit_con, ["paid_loss"], as_of=date(2024, 12, 31)), "paid_loss")
    assert early == again and early < late


def test_ratio_composes_from_leaves(sl, summit_con, as_of):
    r = sl.query(summit_con, ["ultimate_loss_ratio", "reported_incurred_loss_alae", "ibnr", "earned_premium"],
                 as_of=as_of).records()[0]
    expected = (float(r["reported_incurred_loss_alae"]) + float(r["ibnr"])) / float(r["earned_premium"])
    assert r["ultimate_loss_ratio"] == pytest.approx(expected)
    assert 0.3 < r["ultimate_loss_ratio"] < 1.5


def test_invalid_dimension_is_refused_not_allocated(sl, summit_con):
    with pytest.raises(MetricError, match="not defined for ReserveEstimate"):
        sl.query(summit_con, ["ultimate_loss_ratio"], by=["program"])


def test_triangle_is_upper_left(sl, summit_con, as_of):
    tri = sl.triangle(summit_con, "reported_incurred_loss_alae", as_of=as_of)
    for ay, *cells in tri.rows:
        filled = [c is not None for c in cells]
        assert filled == sorted(filled, reverse=True)       # no gaps after the diagonal
        assert (as_of.year - ay) + 1 >= sum(filled)


def test_client_metric_override(summit):
    summit.config.setdefault("metrics", {})["reported_loss_ratio"] = {
        "description": "Summit reports loss ratio including ALAE.",
        "ratio": ["reported_incurred_loss_alae", "earned_premium"]}
    try:
        m = SemanticLayer(summit).metrics["reported_loss_ratio"]
        assert m.source == "client:summit_group" and m.ratio[0] == "reported_incurred_loss_alae"
    finally:
        summit.config["metrics"].pop("reported_loss_ratio")
        if not summit.config["metrics"]:
            summit.config.pop("metrics")
