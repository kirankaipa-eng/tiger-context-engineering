"""Bring a Studio home to the state the business demo tells: three governed changes to Plains Health
approved by their owners, with the evidence they came from. No LLM calls; safe to run again.

    CTX_STUDIO_HOME=.studio-business uv run python scripts/demo_business_state.py

    1. UM-021: "expired" becomes a prior-authorization status (123 requests already carry it)
    2. MP-112: observation stays are outpatient, a mapping change that moves IP and OP PMPM
    3. The MEE Power BI model crosswalked to governed metrics (so exhibits name the same measure)
"""

from __future__ import annotations

import time

from context_studio.cli import harvest_powerbi
from context_studio.config import bootstrap
from context_studio.proposals import CURATOR, Studio

CLIENT = "plains_health"

EXPIRED = {
    "title": "Add expired as a prior authorization status",
    "summary": "Record approvals that lapse unused after 90 days as expired, separate from withdrawn, as "
               "Utilization Management Policy UM-021 requires.",
    "kind": "enum_value", "author": CURATOR,
    "ops": [{"op": "add_enum_value", "enum": "PriorAuthStatusEnum", "value": "expired", "layer": "client",
             "client": CLIENT, "description": "An approved authorization not used within 90 days of approval."}],
    "evidence": [{"quote": "An approved authorization that is not used within 90 days of approval expires. Expired "
                           "authorizations are recorded with their own status, expired, separate from requests the "
                           "provider withdrew, so that approval rates and authorization-to-service conversion are not "
                           "distorted.", "doc": "um_policy_um021_authorization_validity.md",
                  "title": "Utilization Management Policy UM-021, authorization validity", "page": 2}],
    "data_proof": {"agrees": True, "unit": "PLH", "rows": 123, "data_value": "expired",
                   "summary": "123 PLH PriorAuthorization rows already carry pa status = expired"},
}

MP112 = {
    "title": "Classify observation-start institutional claims as outpatient (MP-112)",
    "summary": "Medical Policy MP-112 says observation stays are outpatient. Institutional claims for stays that "
               "began as observation now map to outpatient; physician claims stay professional.",
    "kind": "change", "author": {"id": "plains-data-steward", "name": "Plains data steward", "kind": "person"},
    "ops": [{"op": "set_mapping", "unit": "PLH", "entity": "MedicalClaim", "slot": "service_category", "client": CLIENT,
             "spec": {"expr": "CASE WHEN src.claim_type = 'institutional' AND src.admit_type = 'observation' THEN "
                              "'outpatient' WHEN src.claim_type = 'institutional' THEN 'inpatient' WHEN src.cpt_code IN "
                              "('99281', '99282', '99283', '99284', '99285') THEN 'emergency' ELSE 'professional' END"},
             "note": "MP-112: observation services are outpatient even when billed on an institutional claim."}],
    "evidence": [{"quote": "Observation services are outpatient services, whatever their length and even when the "
                           "hospital bills them on an institutional claim with a diagnosis-related group. A hospital "
                           "stay that began as observation and did not convert to an inpatient admission is reported "
                           "as outpatient, not as an inpatient admission, in all cost and utilization reporting.",
                  "doc": "medical_policy_mp112_observation_services.md",
                  "title": "Medical Policy MP-112, observation services", "page": 2}],
    "extra": {"watch": ["ip_pmpm", "op_pmpm", "admits_per_1000"]},
}


def approve_all(st: Studio, r: dict, run: bool = True) -> dict:
    """Each area's first owner approves, as they would in the Studio."""
    for _ in range(600):  # checks started in the background finish first
        if run or not (st.get(r["id"]).get("checks") or {}).get("running"):
            break
        time.sleep(1)
    r = st.check(r["id"]) if run else st.get(r["id"])
    if not (r.get("checks") or {}).get("passed"):
        raise SystemExit(f"checks failed on {r['title']}: {r.get('checks')}")
    for area, owners in (r.get("reviewers") or {}).items():
        if r["status"] == "merged":
            break
        if area not in st._satisfied(r):  # noqa: SLF001
            r = st.approve(r["id"], owners[0])
    return r


def main() -> None:
    st = Studio(bootstrap())
    done = {r["title"] for r in st.list(status="merged", client=CLIENT)}
    for spec in (EXPIRED, MP112):
        if spec["title"] in done:
            print(f"already approved: {spec['title']}")
            continue
        r = st.create(client=CLIENT, title=spec["title"], summary=spec["summary"], ops=spec["ops"],
                      author=spec["author"], evidence=spec["evidence"], kind=spec["kind"],
                      data_proof=spec.get("data_proof"), extra=spec.get("extra"), run_checks=False)
        r = approve_all(st, r)
        print(f"{r['status']}: {r['title']}")
    if not any(t.startswith("Crosswalk the MEE_Client_Analytics") for t in done):
        folder = st.s.main / "clients" / CLIENT / "powerbi" / "models" / "MEE_Client_Analytics"
        lead = st.owners().person("plains-medecon-lead")
        r = harvest_powerbi(st, CLIENT, str(folder), {"id": lead.id, "name": lead.name, "kind": "person"})
        r = approve_all(st, r, run=False)
        print(f"{r['status']}: {r['title']}")


if __name__ == "__main__":
    main()
