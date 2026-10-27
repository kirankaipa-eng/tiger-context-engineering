"""Ad-hoc rows (`explore`): columns, conditions, filters, aggregates over the model's tables, read from the
table whose rows reach every table named, under row-level security. Values are literals, never SQL."""

import pytest
import sqlglot

from insurance_context.bi_agent import BIToolkit
from insurance_context.bilayer import LayerError

from test_dax_corpus import layer  # noqa: F401 - the claims fixture


def rows(layer, **kw):  # noqa: F811
    lay, wh = layer
    sqlglot.parse_one(lay.explore_sql(**kw, dialect="databricks"), read="databricks")
    return wh.query(lay.explore_sql(**kw))[1]


def test_rows_with_conditions_and_order(layer):  # noqa: F811
    assert rows(layer, columns=["Claims[ClaimID]", "Provider[Specialty]", "Claims[Paid]"],
                conditions=["Claims[Paid] > 150"], order_by=["Claims[Paid]"], descending=True) == [
        (4, "Ortho", 400), (2, "Ortho", 300), (3, "Cardio", 200)]
    assert rows(layer, columns=["Claims[ClaimID]"], order_by=["Claims[ClaimID]"],
                conditions=["Provider[Specialty] like '%ARD%'",
                            "Claims[ServiceDate] between '2025-01-01' and '2025-02-28'"]) == [(1,), (3,)]
    assert rows(layer, columns=["Claims[ClaimID]"], conditions=["Claims[ClaimID] not in (1, 2, 3, 4)"],
                order_by=["Claims[ClaimID]"]) == [(5,), (6,)]


def test_aggregates_group_by_the_columns(layer):  # noqa: F811
    assert rows(layer, columns=["Provider[Specialty]"], aggregates=["sum(Claims[Paid])", "count(*)"],
                order_by=["Provider[Specialty]"]) == [("Cardio", 350, 3), ("Ortho", 850, 3)]
    assert rows(layer, columns=["Claims[ServiceDate]:month"], aggregates=["count_distinct(Claims[ClaimID])"],
                where={"Provider[Specialty]": "Ortho"}, order_by=["Claims[ServiceDate]:month"]) == [
        ("2025-01", 1), ("2025-03", 1), ("2025-04", 1)]
    assert rows(layer, aggregates=["avg(Claims[Paid])"]) == [(200,)]


def test_the_rows_read_are_the_table_that_reaches_every_table_named(layer):  # noqa: F811
    # Dim_Date and Provider are not related to each other: Claims links them
    assert len(rows(layer, columns=["Dim_Date[Date]", "Provider[Specialty]"])) == 6
    assert len(rows(layer, columns=["Dim_Date[Date]"])) == 100  # alone: the date table's own rows (limit 100)


def test_conditions_take_values_not_sql(layer):  # noqa: F811
    for bad in ["Claims[Paid] > 1; DROP TABLE claims", "Claims[Paid] > (SELECT 1)", "Claims[Paid] = Claims[ClaimID]",
                "1 = 1", "Claims[Paid] like '%' OR 1=1 --'"]:
        with pytest.raises(LayerError):
            layer[0].explore_sql(columns=["Claims[ClaimID]"], conditions=[bad])
    with pytest.raises(LayerError, match="no table"):
        layer[0].explore_sql(columns=["Secret[Thing]"])
    with pytest.raises(LayerError, match="order_by"):
        layer[0].explore_sql(columns=["Claims[ClaimID]"], order_by=["Claims[Paid]"])


def test_the_toolkit_matches_values_honours_security_and_shows_the_sql(layer):  # noqa: F811
    lay, wh = layer
    kit = BIToolkit(lay, wh, row_filters={"Provider": "{Provider[Specialty]} = 'Cardio'"})
    out = kit.explore(columns=["Provider[Specialty]"], aggregates=["sum(Claims[Paid])"])
    assert "| Cardio | 350 |" in out and "Ortho" not in out.split("SQL")[0] and "SELECT" in out
    out = BIToolkit(lay, wh).explore(columns=["Claims[ClaimID]"], filters={"Provider[Specialty]": "ortho"})
    assert "read Provider[Specialty]='ortho' as 'Ortho'" in out and "(3 rows)" in out
    assert BIToolkit(lay, wh).explore(columns=["Nope[X]"]).startswith("Error:")
