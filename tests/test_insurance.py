"""The payer's reading of cost: which measures break a PMPM down, and the breakdown said from two periods' numbers."""

from insurance_context import findable, insurance

# made-up measures named the way a warehouse names them: code-style, with a current version beside each
NAMES = ["Current_PMPMPaid_Medical", "Current_PMPMPaid_Pharmacy", "Current_PMPMPaid_Inpatient", "Current_PMPMPaid_Outpatient",
         "Current_PMPMPaid_Professional", "Current_UtilPer1000_Inpatient", "Current_UnitCostPaid_Inpatient",
         "Current_ScriptsPer1000_Pharmacy", "Current_UnitCostPaid_Pharmacy", "Current_PMPMPaid_Total", "Current_PMPMPaid_Dental",
         "Current_PMPMPaid_SpecialtyRx", "Prior_PMPMPaid_Medical", "PMPMPaid%Change_Medical"]


def catalog():
    return findable.Catalog({n.lower(): {"label": n} for n in NAMES})


def test_a_medical_pmpm_breaks_down_into_the_medical_categories_with_their_utilization_and_unit_cost():
    p = insurance.plan(catalog(), "current_pmpmpaid_medical")
    assert p["head"]["pmpm"] == "current_pmpmpaid_medical"
    cats = {x["category"]: x for x in p["parts"]}
    assert set(cats) == {"inpatient", "outpatient", "professional"}
    assert cats["inpatient"]["util"] == "current_utilper1000_inpatient"
    assert cats["inpatient"]["cost"] == "current_unitcostpaid_inpatient"
    assert cats["outpatient"]["util"] is None


def test_a_total_breaks_into_medical_and_pharmacy_and_a_category_into_its_own_two_drivers():
    c = catalog()
    assert [x["category"] for x in insurance.plan(c, "current_pmpmpaid_total")["parts"]] == ["medical", "pharmacy"]
    rx = insurance.plan(c, "current_pmpmpaid_pharmacy")
    assert rx["parts"] == [] and rx["head"]["util"] == "current_scriptsper1000_pharmacy"


def test_a_slice_of_its_own_and_a_measure_that_is_no_pmpm_have_no_breakdown():
    c = catalog()
    assert insurance.plan(c, "current_pmpmpaid_dental") is None
    assert insurance.plan(c, "current_unitcostpaid_inpatient") is None
    assert insurance.plan(c, "current_pmpmpaid_specialtyrx") is None  # pharmacy-wide drivers are not a specialty drug's


def test_the_breakdown_names_the_largest_category_and_what_moved_it():
    p = insurance.plan(catalog(), "current_pmpmpaid_medical")
    now = {"current_pmpmpaid_medical": 500.0, "current_pmpmpaid_inpatient": 220.0, "current_pmpmpaid_outpatient": 150.0,
           "current_pmpmpaid_professional": 130.0, "current_utilper1000_inpatient": 66.0, "current_unitcostpaid_inpatient": 40000.0}
    then = {"current_pmpmpaid_medical": 450.0, "current_pmpmpaid_inpatient": 180.0, "current_pmpmpaid_outpatient": 145.0,
            "current_pmpmpaid_professional": 125.0, "current_utilper1000_inpatient": 55.0, "current_unitcostpaid_inpatient": 39000.0}
    said = insurance.read(p, now, then, lambda k: k, lambda k, v: f"${v:,.0f}", "against a year before")
    assert said.splitlines()[0].startswith("current_pmpmpaid_medical: $500 against $450, +11.1%")
    assert "current_pmpmpaid_inpatient +$40 (current_utilper1000_inpatient +20.0%, current_unitcostpaid_inpatient +2.6%)" in said
    assert said.splitlines()[-1] == "The largest mover is current_pmpmpaid_inpatient, moved more by utilization."


def test_with_no_number_now_there_is_nothing_to_say():
    p = insurance.plan(catalog(), "current_pmpmpaid_medical")
    assert insurance.read(p, {}, {"current_pmpmpaid_medical": 1.0}, str, lambda k, v: str(v), "") == ""
