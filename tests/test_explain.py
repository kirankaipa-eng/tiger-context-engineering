"""explain: why a metric changed between two periods, worked out level by level in SQL. Fifteen months of
claims, steady with a small wiggle, and one planted spike: Lakeview's inpatient claims five times over in
the last month. explain must find that path, not dress up ordinary variation as a cause, and look under a
total that hardly moved when one part of it broke out."""

import duckdb
import pytest

from insurance_context.bi_agent import BIToolkit
from insurance_context.bilayer import BILayer, Warehouse, build_spec
from insurance_context.pbimodel import Column, Measure, Model, Relationship, Table

PROVIDERS = [(1, "Lakeview", "Ortho"), (2, "Riverside", "Ortho"), (3, "Hillcrest", "Cardio"),
             (4, "Brookside", "Cardio"), (5, "Northgate", "Primary"), (6, "Eastwood", "Primary")]
BASE = {"IP": 900, "OP": 300}
MONTHS = [f"{2025 + (m // 12)}-{m % 12 + 1:02d}" for m in range(15)]  # 2025-01 .. 2026-03
MONTH = "Dim_Date[Date]:month"


def claims():
    out, n = [], 0
    for m, month in enumerate(MONTHS):
        for p, _, _ in PROVIDERS:
            for k, (setting, base) in enumerate(BASE.items()):
                wiggle = 1 + 0.03 * ((m * 7 + p * 3 + k) % 5 - 2)
                spike = 5 if (p == 1 and setting == "IP" and month == MONTHS[-1]) else 1
                for c in range(3):
                    n += 1
                    out.append((n, p, f"{month}-{10 + c}", setting, round(base * wiggle * spike, 2)))
    return out


def model() -> Model:
    cols = lambda *cs: [Column(c, t, c) for c, t in cs]  # noqa: E731
    fact = Table("Claims", cols(("ClaimID", "int64"), ("ProviderID", "int64"), ("ServiceDate", "dateTime"),
                                ("Setting", "string"), ("Paid", "double")), [Measure("Paid", "SUM(Claims[Paid])")],
                 "x", "m")
    provider = Table("Provider", cols(("ProviderID", "int64"), ("Provider", "string"), ("Specialty", "string")),
                     source="x", source_kind="m")
    dates = Table("Dim_Date", cols(("Date", "dateTime")), source="x", source_kind="m")
    return Model("Claims", {t.name: t for t in (fact, provider, dates)}, [
        Relationship("Claims", "ProviderID", "Provider", "ProviderID"),
        Relationship("Claims", "ServiceDate", "Dim_Date", "Date")])


@pytest.fixture(scope="module")
def kit(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("explain") / "pilot.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA shop")
    rows = ", ".join(f"({i}, {p}, DATE '{d}', '{s}', {v})" for i, p, d, s, v in claims())
    con.execute(f"CREATE TABLE shop.claims AS SELECT * FROM (VALUES {rows}) t(ClaimID, ProviderID, ServiceDate, "
                "Setting, Paid)")
    con.execute("CREATE TABLE shop.provider AS SELECT * FROM (VALUES "
                + ", ".join(f"({i}, '{n}', '{s}')" for i, n, s in PROVIDERS) + ") t(ProviderID, Provider, Specialty)")
    con.execute("CREATE TABLE shop.dim_date AS SELECT CAST(range AS DATE) AS Date "
                "FROM range(DATE '2025-01-01', DATE '2026-04-01', INTERVAL 1 DAY)")
    con.close()
    return BIToolkit(BILayer(build_spec(model(), "pilot", "shop")), Warehouse("local", path))


def test_it_finds_the_planted_path(kit):
    out = kit.explain("paid", MONTH, "2026-03", "2026-02")
    path = next(x for x in out.splitlines() if x.startswith("Where it comes from"))
    assert "Provider[Provider] = Lakeview" in path and "Claims[Setting] = IP" in path
    assert "Provider[Specialty]" not in path  # Ortho is only Lakeview's share of it: the more specific column wins
    inv = kit.results[-1]["explain"]  # the investigation as one exhibit: its path, what moved, what was ruled out
    assert inv["verdict"] == "path" and [s["value"] for s in inv["steps"]] == ["Lakeview", "IP"]
    first = inv["steps"][0]
    assert first["values"][0][0] == "Lakeview" and {t["status"] for t in first["tested"]} >= {"driver"}


def test_ordinary_variation_is_not_dressed_up_as_a_cause(kit):
    out = kit.explain("paid", MONTH, "2025-09", "2025-08")
    assert "usual swing" in out and "no driver to find" in out.lower()
    assert "Where it comes from" not in out
    assert kit.results[-1]["explain"]["verdict"] == "quiet" and not kit.results[-1]["explain"]["steps"]


def test_a_part_that_broke_out_under_a_quiet_total(kit):
    # Lakeview's spike, less everyone else, so the total hardly moves: it is still found
    out = kit.explain("paid", MONTH, "2026-03", "2026-02", {"Provider[Specialty]": ["Ortho", "Cardio", "Primary"]})
    assert "Lakeview" in out


def test_bad_input_is_an_error_not_a_story(kit):
    assert kit.explain("nope", MONTH, "2026-03", "2026-02").startswith("Error")
