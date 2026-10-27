"""The Studio over a captured Power BI model (`ctx-studio serve --pbi <dir>`): any model folder, no
content repo. Overview, measures, compute, explore and values run on the claims model of the corpus."""

import shutil

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from insurance_context.bilayer import build_spec, save  # noqa: E402

import test_dax_corpus as corpus  # noqa: E402
from test_dax_corpus import layer  # noqa: E402,F401 - builds the rows


@pytest.fixture
def client(layer, tmp_path, monkeypatch):  # noqa: F811
    folder = tmp_path / "claims"
    folder.mkdir()
    save(build_spec(corpus.model(), "pilot", "shop"), folder)
    shutil.copy(layer[1].target, folder / "pilot.duckdb")
    monkeypatch.setenv("CTX_STUDIO_PBI", str(folder))
    for k in ("DATABRICKS_SERVER_HOSTNAME", "API_KEY"):
        monkeypatch.delenv(k, raising=False)
    from context_studio import pbi_app
    monkeypatch.setattr(pbi_app, "_MODELS", None)
    return TestClient(pbi_app.app)


def test_the_model_its_coverage_and_measures(client):
    assert client.get("/api/health").json()["mode"] == "pbi"
    [m] = client.get("/api/pbi/models").json()["models"]
    assert m["id"] == "claims" and m["captured"] >= 10 and "local" in m["warehouse"]
    o = client.get("/api/pbi/claims/overview").json()
    assert o["coverage"]["not_captured"] == 3 and all(g["examples"] for g in o["coverage"]["groups"])
    d = client.get("/api/pbi/claims/metrics/running_paid").json()
    assert d["status"] == "captured" and d["tables"] == ["Claims"] and "Provider[Specialty]" in d["columns"]
    assert client.get("/api/pbi/claims/metrics/nope").status_code == 404


def test_compute_explore_and_values(client):
    r = client.post("/api/pbi/claims/compute", json={"metrics": ["paid"], "by": ["Provider[Specialty]"],
                                                     "filters": {"Provider[Specialty]": "ortho"}}).json()
    assert r["rows"] == [["Ortho", 850]] and r["notes"] and "SELECT" in r["sql"]
    r = client.post("/api/pbi/claims/explore", json={"columns": ["Claims[ClaimID]"], "conditions": ["Claims[Paid] >= 300"],
                                                     "order_by": ["Claims[ClaimID]"]}).json()
    assert r["rows"] == [[2], [4]]
    assert client.get("/api/pbi/claims/values", params={"column": "Provider[Specialty]"}).json()["values"] == ["Cardio", "Ortho"]
    bad = client.post("/api/pbi/claims/explore", json={"columns": ["Claims[ClaimID]"], "conditions": ["1 = 1"]})
    assert bad.status_code == 400 and "start with a column" in bad.json()["detail"]


def test_ask_needs_an_llm(client, monkeypatch):
    from context_studio import pbi_app
    monkeypatch.setattr(pbi_app.llm, "configured", lambda: False)  # .env may hold one
    assert client.post("/api/pbi/claims/ask", json={"message": "hi"}).status_code == 503
