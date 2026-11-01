"""The living brief over test_explain's claims (steady months, one planted spike: Lakeview inpatient, March 2026),
with a small claim on each month's last day so every month is complete. The brief must find the spike as new in
March and nothing in February, say where the quarter lands against plan or last year, and let someone take a
claim, which becomes the team's finding."""

import calendar

import duckdb
import pytest
from fastapi.testclient import TestClient
from test_explain import MONTHS, PROVIDERS, claims, model
from test_team import Model

from context_studio import pbi_app, pbi_brief
from insurance_context.bi_agent import BIToolkit
from insurance_context.bilayer import BILayer, Warehouse, build_spec


@pytest.fixture()
def team(tmp_path):
    path = str(tmp_path / "pilot.duckdb")
    rows = claims()
    n = len(rows)
    for month in MONTHS:  # the month is over: a small claim on its last day
        n += 1
        y, mo = int(month[:4]), int(month[5:])
        rows.append((n, 6, f"{month}-{calendar.monthrange(y, mo)[1]}", "OP", 10.0))
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA shop")
    values = ", ".join(f"({i}, {p}, DATE '{d}', '{s}', {v})" for i, p, d, s, v in rows)
    con.execute(f"CREATE TABLE shop.claims AS SELECT * FROM (VALUES {values}) t(ClaimID, ProviderID, ServiceDate, "
                "Setting, Paid)")
    con.execute("CREATE TABLE shop.provider AS SELECT * FROM (VALUES "
                + ", ".join(f"({i}, '{n}', '{s}')" for i, n, s in PROVIDERS) + ") t(ProviderID, Provider, Specialty)")
    con.execute("CREATE TABLE shop.dim_date AS SELECT CAST(range AS DATE) AS Date "
                "FROM range(DATE '2025-01-01', DATE '2026-04-01', INTERVAL 1 DAY)")
    con.close()
    pbi_brief._CACHE.clear()
    return Model(tmp_path, BIToolkit(BILayer(build_spec(model(), "pilot", "shop")), Warehouse("local", path)))


def test_the_brief_finds_the_spike_as_new_and_nothing_the_month_before(team):
    b = pbi_brief.brief(team)
    assert b["as_of"] == "2026-03" and b["data_through"] == "2026-03-31" and b["partial"] is None
    [c] = b["claims"]
    assert c["status"] == "new" and c["since"] == "2026-03" and "Lakeview" in [w["value"] for w in c["where"]]
    assert c["value"] > 4 * c["usual"] and 0 < c["share"] < 1 and b["settled"] == []
    before = pbi_brief.brief(team, "2026-02")
    assert before["as_of"] == "2026-02" and before["claims"] == [] and before["settled"] == []


def test_the_quarter_lands_against_last_year_or_a_plan(team):
    [land] = pbi_brief.brief(team)["landing"]
    assert land["closed"]["quarter"] == "2026Q1" and land["quarter"] == "2026Q2" and land["months_done"] == 0
    assert land["low"] <= land["landing"] <= land["high"] and land["last_year"]["quarter"] == "2025Q2"
    assert land["target"] is None and len(land["ahead"]) == 3
    pbi_brief.set_target(team.folder, "paid", "2026Q2", 50000, "Priya")
    [land] = pbi_brief.brief(team)["landing"]  # the plan shows at once; the numbers stay cached
    assert land["target"]["value"] == 50000 and land["target"]["by"] == "Priya"


def test_someone_takes_a_claim_and_it_is_the_teams_finding(team, monkeypatch):
    monkeypatch.setattr(pbi_app, "_MODELS", {"claims": team})
    client = TestClient(pbi_app.app)
    b = client.get("/api/pbi/claims/brief").json()
    c = b["claims"][0]
    f = client.post("/api/pbi/claims/brief/take", json={"who": "Priya", "metric": c["metric"],
                                                       "values": [w["value"] for w in c["where"]]}).json()
    assert f["owner"] == "Priya" and f["status"] == "claimed" and f["checks"]
    again = client.get("/api/pbi/claims/brief").json()["claims"][0]
    assert again["finding"]["owner"] == "Priya"
    assert client.post("/api/pbi/claims/brief/take", json={"metric": "paid", "values": ["nowhere"]}).status_code == 404
    assert client.post("/api/pbi/claims/targets", json={"metric": "paid", "quarter": "Q2"}).status_code == 400
    out = client.post("/api/pbi/claims/targets", json={"metric": "paid", "quarter": "2026Q2", "value": 1}).json()
    assert out["paid"]["2026Q2"]["value"] == 1.0


def test_the_map_lights_only_what_chance_would_not_put_there(team):
    p = pbi_brief.pattern(team)
    lit = [(g["name"], c["name"], p["months"][i]) for g in p["groups"] for c in g["children"]
           for i, o in enumerate(c["out"]) if o]
    assert p["levels"][0] == "Provider[Provider]" and p["months"][-1] == "2026-03"
    assert lit == [("Lakeview", "IP", "2026-03")]  # the planted spike, and nothing the wiggle made
    assert p["disturbed"][-1] == 1 and sum(p["disturbed"]) == 1 and p["claim"][0] == "Lakeview"


def test_the_t_tail_matches_the_tables():
    from insurance_context.bistats import surprise, t_tail
    assert abs(t_tail(2.201, 11) - 0.05) < 0.001 and abs(t_tail(3.106, 11) - 0.01) < 0.0005
    assert surprise([10.0] * 12, 10.0) == 1.0 and surprise([10.0] * 12, 11.0) == 0.0


def test_a_brief_is_the_persons_own(team, monkeypatch):
    monkeypatch.setattr(pbi_app, "_MODELS", {"claims": team})
    client = TestClient(pbi_app.app)
    assert client.post("/api/pbi/claims/brief/prefs", json={"who": "me"}).status_code == 400
    assert client.post("/api/pbi/claims/brief/prefs", json={"who": "Sam", "scope": {"Nowhere[X]": ["a"]}}).status_code == 400
    # Sam answers for Riverside: the Lakeview spike is not his, and his landing is Riverside's alone
    client.post("/api/pbi/claims/brief/prefs", json={"who": "Sam", "scope": {"Provider[Provider]": ["Riverside"]}})
    sam = client.get("/api/pbi/claims/brief", params={"me": "sam"}).json()
    everyone = client.get("/api/pbi/claims/brief").json()
    assert sam["claims"] == [] and sam["prefs"]["scope"] == {"Provider[Provider]": ["Riverside"]}
    assert sam["landing"][0]["closed"]["actual"] < everyone["landing"][0]["closed"]["actual"] / 4
    # Priya answers for Lakeview: the spike is hers to see, and once she takes it, it is marked as hers
    client.post("/api/pbi/claims/brief/prefs", json={"who": "Priya", "scope": {"Provider[Provider]": ["Lakeview"]}})
    [c] = client.get("/api/pbi/claims/brief", params={"me": "Priya"}).json()["claims"]
    assert c["status"] == "new" and not c["mine"]
    client.post("/api/pbi/claims/brief/take", json={"who": "Priya", "metric": c["metric"],
                                                   "values": [w["value"] for w in c["where"]]})
    assert client.get("/api/pbi/claims/brief", params={"me": "Priya"}).json()["claims"][0]["mine"]
    p = client.get("/api/pbi/claims/pattern", params={"me": "Sam"}).json()  # Sam's map is Riverside's rows only
    tiles = sum(c["values"][-1] for g in p["groups"] for c in g["children"])
    assert round(tiles, 2) == round(sam["landing"][0]["history"][-1][1], 2)
