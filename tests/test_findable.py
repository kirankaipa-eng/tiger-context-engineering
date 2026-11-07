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


CHAIN = {
    "effect": {"label": "Current_VisitEffect_Dental", "status": "captured", "dax": "[Visits%Change_Dental] * [Base]"},
    "pct": {"label": "Visits%Change_Dental", "status": "not_captured", "dax": "[Rate]", "why": "built on [Rate], which is not captured: x"},
    "rate": {"label": "Rate", "status": "not_captured", "dax": "x", "why": "uses PATHITEM, which is not captured yet"},
    "base": {"label": "Base", "status": "captured", "dax": "SUM(T[A])"},
}


def test_why_goes_down_to_what_could_not_be_translated_and_shows_no_formula():
    got = f.blocked(CHAIN, "current_visiteffect_dental") or f.blocked(CHAIN, "effect")
    assert got[0].startswith("Current_VisitEffect_Dental [captured]")
    assert got[1].startswith("  Visits%Change_Dental [not_captured]: built on [Rate]")
    assert got[2] == "    Rate [not_captured]: uses PATHITEM, which is not captured yet"
    assert not any("SUM" in x or "Base" in x for x in got)


def test_why_is_quiet_when_nothing_is_in_the_way():
    assert f.blocked(CHAIN, "base") == [] and f.blocked(CHAIN, "nothing") == ["no metric 'nothing'"]


def test_a_model_reads_every_measure_with_its_versions_and_no_helpers():
    text = f.Catalog(METRICS).context()
    assert 'current:current_billedpmpm_dental "Current_BilledPMPM_Dental" | prior:prior_billedpmpm_dental' in text
    assert "lastdayid" not in text and "Business words: unitcost = unit cost" in text


def test_a_label_that_says_more_than_its_key_is_what_the_model_reads():
    c = f.Catalog({"visits_q": {"label": "Dental visits in the quarter", "description": "Counted  at discharge", "dax": "x"},
                   "total_pmpm": {"label": "Total PMPM", "dax": "x"}})
    text = c.context()
    assert 'base:visits_q "Dental visits in the quarter" (Counted at discharge)' in text
    assert 'base:total_pmpm\n' in text


def test_a_preview_pinned_to_one_period_is_no_measure_to_answer_with():
    c = f.Catalog({"sql_candidate_visits_dental": {"label": "SQL preview dental visits (May 2025 R12, 0M runout)", "dax": "x"},
                   "sql_preview_x": {"label": "SQL preview dental visits (June 2025)", "dax": "x"}})
    assert {e["role"] for e in c.metrics.values()} == {"helper"}
    assert "2025" not in c.metrics["sql_candidate_visits_dental"]["family"]
    assert "sql_candidate" not in c.context()
