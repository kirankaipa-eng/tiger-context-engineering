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
    t = y["tables"]["Member Months"]
    t["table"] = "other_catalog." + t["table"].split(".", 1)[1]
    (model / "capture.yaml").write_text(yaml.safe_dump(y, sort_keys=False))
    desk_doctor.main([str(model), "medical_pmpm"])
    out = capsys.readouterr().out
    assert "FAIL  its tables are all in one warehouse catalog" in out and "other_catalog: Member Months" in out


def test_the_scope_value_that_empties_a_measure_is_named(model, capsys):
    (model / "analysis_default.yaml").write_text("filters:\n  Member[Line of Business]: Nowhere\n")
    desk_doctor.main([str(model), "medical_pmpm"])
    assert "is the value that empties it" in capsys.readouterr().out


def test_set_replaces_a_scope_value_for_one_run(model, capsys):
    import re

    (model / "analysis_default.yaml").write_text("filters:\n  Member[Line of Business]: Nowhere\n")
    desk_doctor.main([str(model), "medical_pmpm"])
    line = re.search(r"a number with ([^:]+):", capsys.readouterr().out).group(1)
    assert desk_doctor.main([str(model), "medical_pmpm", "--set", f"Member[Line of Business]={line}"]) == 0
    assert "ALL PASSED" in capsys.readouterr().out


def test_every_measure_is_run_and_summed_up(model, capsys):
    import json

    (model / "analysis_default.yaml").write_text("filters:\n  Member[Line of Business]: Nowhere\n")
    desk_doctor.main([str(model), "-", "--all", "--workers", "2"])
    out = capsys.readouterr().out
    got = json.loads((model / "doctor.json").read_text())
    assert got and all(e["result"] in ("answers", "empty", "refused") for e in got.values())
    assert "captured measures under the owner's scope" in out and "refused, by reason" in out
    assert not any("value" in e for e in got.values())  # what happened to each, never a number


def test_the_tables_its_sql_reads_are_named(model, capsys):
    desk_doctor.main([str(model), "medical_pmpm"])
    assert "the tables its SQL reads are in one catalog" in capsys.readouterr().out
