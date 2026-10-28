"""The team space: the Scout finds what broke from its own pattern without being asked, the Skeptic's checks
are queries that ran, and the team's thread and status live in team.json beside the model. Over the same claims
as test_explain: steady months, one planted spike (Lakeview inpatient, the last month)."""

import duckdb
import pytest
from fastapi.testclient import TestClient

from context_studio import pbi_app, pbi_team
from insurance_context.bi_agent import BIToolkit
from insurance_context.bilayer import BILayer, Warehouse, build_spec
from test_explain import PROVIDERS, claims, model


class Model:
    """What the team space needs of a loaded model: its folder, layer and toolkit."""
    def __init__(self, folder, kit):
        self.folder, self.layer, self._kit, self.id = folder, kit.layer, kit, "claims"

    def kit(self):
        return self._kit


@pytest.fixture()
def team(tmp_path):
    path = str(tmp_path / "pilot.duckdb")
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
    return Model(tmp_path, BIToolkit(BILayer(build_spec(model(), "pilot", "shop")), Warehouse("local", path)))


def test_the_scout_finds_the_spike_and_the_skeptic_checks_it(team):
    data = pbi_team.scout(team)
    found = pbi_team.ranked(data)
    month = next(f for f in found if f["before"] == "2026-02")
    assert month["now"] == "2026-03" and "Lakeview" in [s["value"] for s in month["explain"]["steps"]]
    checks = {c["check"]: c for c in month["checks"]}
    assert checks["complete"]["passed"] and checks["many_rows"]["passed"] is not None
    assert checks["season"]["passed"]  # a year earlier the same months did not jump
    assert data["scouted"]["measures"] == 1 and all(c["verdict"] for c in data["scouted"]["checked"])


def test_a_thread_and_a_status_are_kept_and_survive_a_new_scout(team, monkeypatch):
    monkeypatch.setattr(pbi_app, "_MODELS", {"claims": team})
    client = TestClient(pbi_app.app)
    [first, *_] = client.get("/api/pbi/claims/team").json()["findings"]
    out = client.post(f"/api/pbi/claims/team/{first['id']}/comment", json={"who": "Priya", "text": "Real? @skeptic"}).json()
    assert out["comments"][-1]["who"] == "Fact-checker" and out["comments"][-1]["checks"]
    assert client.post(f"/api/pbi/claims/team/{first['id']}/status", json={"status": "claimed", "who": "Priya"}).json()["owner"] == "Priya"
    again = {f["id"]: f for f in client.post("/api/pbi/claims/team/scout").json()["findings"]}
    assert again[first["id"]]["owner"] == "Priya" and len(again[first["id"]]["comments"]) == 3
    assert client.post(f"/api/pbi/claims/team/{first['id']}/status", json={"status": "done"}).status_code == 400


def test_agents_on_call_and_a_persons_dashboard(team, monkeypatch):
    monkeypatch.setattr(pbi_app, "_MODELS", {"claims": team})
    client = TestClient(pbi_app.app)
    ask = {"metric": "paid", "period": "Dim_Date[Date]:month", "now": "2026-03", "before": "2026-02"}
    inv = client.post("/api/pbi/claims/explain", json=ask).json()["explain"]
    assert "Lakeview" in [s["value"] for s in inv["steps"]]
    checks = client.post("/api/pbi/claims/check", json={"explain": inv}).json()["checks"]
    assert {c["check"] for c in checks} >= {"complete", "many_rows", "season"}
    assert client.post("/api/pbi/claims/explain", json={"metric": "paid"}).status_code == 400
    tile = {"id": "t1", "title": "Paid", "kind": "explain", "explain": ask, "at": 0}
    assert client.post("/api/pbi/claims/board", json={"owner": "Priya", "tiles": [tile]}).json()["tiles"] == [tile]
    assert client.get("/api/pbi/claims/board", params={"owner": "priya"}).json()["tiles"] == [tile]  # any case
    assert client.get("/api/pbi/claims/board", params={"owner": "Sam"}).json()["tiles"] == []
    assert client.post("/api/pbi/claims/board", json={"tiles": []}).status_code == 400


def test_the_assistants_answer_in_a_thread(team, monkeypatch):
    monkeypatch.setattr(pbi_app, "_MODELS", {"claims": team})
    client = TestClient(pbi_app.app)
    [first, *_] = client.get("/api/pbi/claims/team").json()["findings"]
    out = client.post(f"/api/pbi/claims/team/{first['id']}/comment", json={"who": "Priya", "text": "@Investigator again?"}).json()
    assert out["comments"][-1]["who"] == "Investigator" and "Same answer" in out["comments"][-1]["text"]
    out = client.post(f"/api/pbi/claims/team/{first['id']}/comment", json={"who": "Priya", "text": "@Scout anything else?"}).json()
    assert out["comments"][-1]["who"] == "Scout" and "moved more than usual" in out["comments"][-1]["text"]
    out = client.post(f"/api/pbi/claims/team/{first['id']}/comment", json={"who": "Priya", "text": "@Fact-checker sure?"}).json()
    assert out["comments"][-1]["who"] == "Fact-checker"


def test_a_finding_the_store_lost_is_found_again(team, monkeypatch):
    monkeypatch.setattr(pbi_app, "_MODELS", {"claims": team})
    client = TestClient(pbi_app.app)
    [first, *_] = client.get("/api/pbi/claims/team").json()["findings"]
    (team.folder / "team.json").unlink()  # cleared while a page still shows the finding
    out = client.post(f"/api/pbi/claims/team/{first['id']}/comment", json={"who": "Priya", "text": "still there?"})
    assert out.status_code == 200 and out.json()["comments"][-1]["text"] == "still there?"
    gone = client.post("/api/pbi/claims/team/nope123/comment", json={"who": "Priya", "text": "hi"})
    assert gone.status_code == 404 and "no longer here" in gone.json()["detail"]
