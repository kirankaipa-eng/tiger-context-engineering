"""The doctor: one measure through the desk's steps, and the first that failed."""

import shutil
from pathlib import Path

import pytest

from insurance_context import desk_doctor

DEMO = Path(__file__).resolve().parents[1] / "build" / "pbi" / "plains-health"


@pytest.fixture
def model(tmp_path):
    if not (DEMO / "pilot.duckdb").exists():
        pytest.skip("the demo model is not built here")
    for name in ("capture.yaml", "pilot.duckdb", "pilot.duckdb.about"):
        if (DEMO / name).exists():
            shutil.copy(DEMO / name, tmp_path / name)
    return tmp_path


def test_a_measure_that_answers_passes_every_step(model, capsys):
    assert desk_doctor.main([str(model), "Medical PMPM"]) == 0
    assert "ALL PASSED" in capsys.readouterr().out


def test_the_filter_that_empties_a_measure_is_named(model, capsys):
    (model / "analysis_default.yaml").write_text("filters:\n  Member[Line of Business]: Nowhere\n")
    assert desk_doctor.main([str(model), "medical_pmpm"]) == 1
    out = capsys.readouterr().out
    assert "FIRST FAILURE: step 4" in out and "<-- this filter empties it" in out


def test_a_measure_reading_two_catalogs_is_named(model, capsys):
    import yaml

    y = yaml.safe_load((model / "capture.yaml").read_text())
    t = y["tables"]["Member"]
    t["table"] = "other_catalog." + t["table"].split(".", 1)[1]
    (model / "capture.yaml").write_text(yaml.safe_dump(y, sort_keys=False))
    desk_doctor.main([str(model), "medical_pmpm"])
    out = capsys.readouterr().out
    assert "FAIL  its tables are all in one warehouse catalog" in out and "other_catalog: Member" in out
