"""Power BI: harvest a model's measures, crosswalk them to governed metrics, generate TMDL back."""

import json
import sys
import types
import zipfile
from pathlib import Path

import pytest

from insurance_context import powerbi
from insurance_context.semantic import SemanticLayer

MODEL = """table Metrics
\tmeasure 'Medical PMPM' = VAR _y = MAX(Metrics[Year]) RETURN CALCULATE(SUM(Metrics[MedicalPMPM]), Metrics[Year] = _y)
\tmeasure 'Medical PMPM YoY' = VAR _y = MAX(Metrics[Year]) VAR _p = 1 RETURN FORMAT(_p, "0.0%") & " YoY"
\tmeasure 'Admits / K' = SUM(Metrics[AdmitsK])
\tmeasure 'Paid per Prof Visit' = SUM(Metrics[PaidPerProf])
\tmeasure 'BH PMPM' = SUM(Metrics[BHPMPM])
"""


@pytest.fixture
def model(tmp_path) -> Path:
    d = tmp_path / "M.SemanticModel" / "definition" / "tables"
    d.mkdir(parents=True)
    (d / "Metrics.tmdl").write_text(MODEL)
    return tmp_path


def test_crosswalk_sorts_governed_presentation_and_gaps(plains, model):
    rows = {r["name"]: r for r in powerbi.crosswalk(SemanticLayer(plains), powerbi.harvest(model))}
    assert rows["Medical PMPM"]["metric"] == "medical_pmpm"
    assert rows["Admits / K"]["metric"] == "admits_per_1000"
    assert rows["Paid per Prof Visit"]["metric"] == "paid_per_professional_visit"
    assert rows["Medical PMPM YoY"]["status"] == "presentation"
    assert rows["BH PMPM"]["status"] == "gap"


MULTILINE = """table Metrics

\tmeasure 'Inpatient PMPM' =
\t\t\tVAR _y = MAX(Metrics[Year])
\t\t\tRETURN
\t\t\t\tCALCULATE(SUM(Metrics[IPPMPM]), Metrics[Year] = _y)
\t\tformatString: \\$#,0
\t\tlineageTag: 1b2c

\tmeasure 'Fenced' = ```
\t\t\tSUM(Metrics[A])

\t\t\t+ SUM(Metrics[B])
\t\t\t```
\t\tlineageTag: 3d4e

\tmeasure 'Short' = SUM(Metrics[C])
\t\tlineageTag: 5f6a
"""


def test_harvest_reads_multiline_and_fenced_measures(tmp_path):
    (tmp_path / "Metrics.tmdl").write_text(MULTILINE)
    rows = {r["name"]: r["dax"] for r in powerbi.harvest(tmp_path)}
    assert rows == {
        "Inpatient PMPM": "VAR _y = MAX(Metrics[Year])\nRETURN\n\tCALCULATE(SUM(Metrics[IPPMPM]), Metrics[Year] = _y)",
        "Fenced": "SUM(Metrics[A])\n\n+ SUM(Metrics[B])",
        "Short": "SUM(Metrics[C])",
    }


def test_tmdl_follows_the_definitions(plains):
    tmdl, kept = powerbi.to_tmdl(SemanticLayer(plains), ["ip_pmpm", "admits_per_1000"])
    assert "measure 'IP PMPM' = DIVIDE([Inpatient paid], [Member months])" in tmdl
    assert "'medical_claim'[service_category] = \"inpatient\"" in tmdl
    assert "* 12000" in tmdl and "annotation GovernedMetric = ip_pmpm" in tmdl
    assert kept == []


BIM = {"model": {"tables": [
    {"name": "Metrics", "measures": [
        {"name": "Admits / K", "expression": "SUM(Metrics[AdmitsK])"},
        {"name": "Medical PMPM", "expression": ["VAR _y = MAX(Metrics[Year])", "RETURN SUM(Metrics[MedicalPMPM])"]}]},
    {"name": "Dates"}]}}


def test_harvest_reads_model_bim(tmp_path):
    (tmp_path / "model.bim").write_text(json.dumps(BIM))
    expected = [{"name": "Admits / K", "table": "Metrics", "dax": "SUM(Metrics[AdmitsK])"},
                {"name": "Medical PMPM", "table": "Metrics",
                 "dax": "VAR _y = MAX(Metrics[Year])\nRETURN SUM(Metrics[MedicalPMPM])"}]
    assert powerbi.harvest(tmp_path / "model.bim") == expected
    assert powerbi.harvest(tmp_path) == expected  # an older PBIP project keeps model.bim, not TMDL


def test_harvest_reads_a_template_pbit(tmp_path):
    pbit = tmp_path / "Report.pbit"
    with zipfile.ZipFile(pbit, "w") as z:
        z.writestr("DataModelSchema", json.dumps(BIM).encode("utf-16-le"))  # as Power BI writes it
    assert [m["name"] for m in powerbi.harvest(pbit)] == ["Admits / K", "Medical PMPM"]


def test_harvest_reads_a_pbix_through_pbixray(tmp_path, monkeypatch):
    records = [{"TableName": "Metrics", "Name": "Admits / K", "Expression": "\nSUM(Metrics[AdmitsK])\n",
                "DisplayFolder": None, "Description": None}]
    frame = types.SimpleNamespace(to_dict=lambda orient: records)  # stands in for pbixray's DataFrame
    fake = types.ModuleType("pbixray")
    fake.PBIXRay = lambda path: types.SimpleNamespace(dax_measures=frame)
    monkeypatch.setitem(sys.modules, "pbixray", fake)
    assert powerbi.harvest(tmp_path / "Report.pbix") == [
        {"name": "Admits / K", "table": "Metrics", "dax": "SUM(Metrics[AdmitsK])"}]


def test_harvest_says_what_to_do_without_pbixray(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pbixray", None)
    with pytest.raises(ValueError, match="PBIP"):
        powerbi.harvest(tmp_path / "Report.pbix")
