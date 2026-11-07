"""The grid: each captured measure against the columns it can be cut by, compiled under the owner's scope, never run."""

import shutil
from pathlib import Path

import pytest
import yaml

from insurance_context import grid
from insurance_context.bilayer import BILayer

DEMO = Path(__file__).resolve().parents[1] / "build" / "pbi" / "plains-health"


@pytest.fixture
def model(tmp_path):
    if not (DEMO / "capture.yaml").exists():
        pytest.skip("the demo model is not built here")
    shutil.copy(DEMO / "capture.yaml", tmp_path / "capture.yaml")
    return tmp_path


def test_a_formula_reads_as_a_reader_writes_it():
    assert grid.plain("({medical_paid} / nullif({member_months}, 0))") == "medical_paid / member_months"
    assert grid.plain("(({a} * 12000) / nullif({b}, 0))") == "(a * 12000) / b"
    assert grid.plain("({a} / {b}) - 1") == "(a / b) - 1"


def test_each_measure_has_its_parts_and_the_columns_it_can_be_cut_by(model):
    g = grid.build(BILayer.load(model), model)
    e = g["metrics"]["medical_pmpm"]
    assert e["formula"] == "medical_paid / member_months"
    assert e["parts"]["member_months"].startswith("sum of ")
    assert "Member[Line of Business]" in grid.slices(g, "medical_pmpm")
    assert not any("ID" in c.split("[")[1] for c in grid.slices(g, "medical_pmpm"))  # a key is no slice


def test_a_measure_that_needs_a_grain_is_compiled_with_it_not_refused(model):
    g = grid.build(BILayer.load(model), model)
    e = g["metrics"]["total_pmpm_yoy_pct"]
    assert e["needs"]["by"] and "slices" in e


def test_the_owner_scope_is_compiled_with_and_a_table_the_rows_never_reach_is_left_out(model):
    (model / "analysis_default.yaml").write_text("filters:\n  Member[Line of Business]: Commercial\n  Nowhere[Basis]: ADP\n")
    layer = BILayer.load(model)
    seen = []
    real = layer.compile

    def compile(metrics, by=None, where=None, *a, **k):
        seen.append(dict(where or {}))
        if "Nowhere[Basis]" in (where or {}):
            raise ValueError("Nowhere is not related to Medical Claims")
        return real(metrics, by, where, *a, **k)

    layer.compile = compile
    g = grid.build(layer, model)
    assert "slices" in g["metrics"]["medical_pmpm"]
    assert {"Member[Line of Business]": "Commercial"} in seen


def test_a_grid_of_another_capture_is_not_read(model):
    (model / grid.NAME).write_text(yaml.safe_dump(grid.build(BILayer.load(model), model)))
    assert grid.load(model) is not None
    (model / "capture.yaml").write_text((model / "capture.yaml").read_text() + "\n# changed\n")
    assert grid.load(model) is None


def test_with_the_toolkit_the_grid_compiles_as_the_desk_does_resolved_and_with_the_real_latest(model):
    (model / "analysis_default.yaml").write_text("filters:\n  Member[Line of Business]: Commercial\n  Member[Plan]: latest\n")
    layer = BILayer.load(model)
    real = layer.compile

    def compile(metrics, by=None, where=None, *a, **k):  # a model that wants its reporting context settled first
        if not (where or {}).get("settled"):
            raise ValueError("This measure requires query-context resolution; use the PBI toolkit")
        return real(metrics, by, {c: v for c, v in where.items() if c != "settled"})

    layer.compile = compile

    class Kit:
        row_filters = None

        class wh:
            dialect = "duckdb"

            @staticmethod
            def query(sql, limit=None):
                return [], [("Bronze",), ("Gold",), (None,)]

        @staticmethod
        def _values_sql(col, where=None):
            return "values"

        @staticmethod
        def resolve(filters):
            return {**filters, "settled": True}, []

    g = grid.build(layer, model, Kit())
    assert g["scope"]["Member[Plan]"] == "Gold"
    assert "slices" in g["metrics"]["medical_pmpm"]
    assert "slices" not in grid.build(layer, model)["metrics"]["medical_pmpm"]  # offline it is refused, as on the AVD
