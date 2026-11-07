import yaml

from insurance_context import findable as f

METRICS = {
    "current_billedpmpm_dental": {"label": "Current_BilledPMPM_Dental", "dax": "DIVIDE([Billed],[Months])"},
    "prior_billedpmpm_dental": {"label": "Prior_BilledPMPM_Dental", "dax": "x"},
    "billedpmpm_pct_change_dental": {"label": "BilledPMPM%Change_Dental", "dax": "x"},
    "billedpmpm_change_dental": {"label": "BilledPMPM$Change_Dental", "dax": "x"},
    "current_visits_dental": {"label": "Current_Visits_Dental", "dax": "x"},
    "current_visitcost_dental": {"label": "Current_UnitCostBilled_Dental", "dax": "x"},
    "lastdayid": {"label": "Current_EndDayId", "dax": "x"},
    "billed": {"label": "Billed", "dax": "x"}, "months": {"label": "Months", "dax": "x"},
}


def test_a_name_kept_as_code_comes_apart():
    assert f.split("Current_UnitCostBilled_Dental") == ["current", "unit", "cost", "billed", "dental"]
    assert f.split("BilledPMPM%Change_Dental") == ["billed", "pmpm", "percent", "change", "dental"]
    assert f.split("PMPMBilled") == ["pmpm", "billed"]
    assert f.split("VisitsPer1000") == f.split("Visits per 1,000") == ["visit", "per", "1000"]
    assert f.split("Total PMPM YoY %") == ["total", "pmpm", "yoy", "percent"]


def test_unit_cost_is_not_heard_as_utilization():
    assert f.heard(f.split("unit cost")) == ["unitcost"]
    assert f.heard(f.split("Visits")) == ["utilization"]
    assert set(f.heard(f.split("price and units"))) == {"unitcost", "utilization"}


def test_versions_of_a_measure_are_one_family_with_roles():
    c = f.Catalog(METRICS)
    assert c.families["billed dental pmpm"] == {"current": "current_billedpmpm_dental", "prior": "prior_billedpmpm_dental",
                                                "change_percent": "billedpmpm_pct_change_dental", "change_amount": "billedpmpm_change_dental"}
    assert c.metrics["lastdayid"]["role"] == "helper"


def test_a_question_gets_the_version_it_means():
    c = f.Catalog(METRICS)
    assert c.version("billedpmpm_pct_change_dental") == "current_billedpmpm_dental"
    assert c.version("prior_billedpmpm_dental") == "current_billedpmpm_dental"
    assert c.version("prior_billedpmpm_dental", prior=True) == "prior_billedpmpm_dental"
    assert c.version("billedpmpm_pct_change_dental", change=True) == "billedpmpm_pct_change_dental"


def test_the_words_a_formula_reads_find_it():
    assert "month" in f.Catalog(METRICS).metrics["current_billedpmpm_dental"]["words"]


def test_an_owners_corrections_stand(tmp_path):
    (tmp_path / f.NAME).write_text(yaml.safe_dump({"terms": {"utilization": ["encounter"]},
                                                   "metrics": {"billed": {"role": "helper", "also": ["charges"]}}}))
    c = f.load(tmp_path, METRICS)
    assert c.metrics["billed"]["role"] == "helper" and "charge" in c.metrics["billed"]["words"]
    assert "utilization" in c.query("encounters")
    assert "visit" in c.terms["utilization"]


def test_it_writes_what_it_loads(tmp_path):
    c = f.Catalog(METRICS)
    (tmp_path / f.NAME).write_text(yaml.safe_dump(c.data()))
    again = f.load(tmp_path, METRICS)
    assert again.metrics["current_visits_dental"]["role"] == "current" and again.families == c.families


def test_a_price_is_not_a_count():
    c = f.Catalog({"cost_per_visit": {"label": "CostPerVisit", "dax": "x"}, "visits": {"label": "Visits", "dax": "x"}})
    assert "unitcost" in c.metrics["cost_per_visit"]["words"] and "utilization" not in c.metrics["cost_per_visit"]["words"]
    assert "utilization" in c.metrics["visits"]["words"]
