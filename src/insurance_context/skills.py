"""Agent skills for the insurance context layer.

Tools give the agent access (ontology, governed metrics, data); skills give it
method: how a practitioner approaches each kind of question, which governed
metrics answer it, and which cuts are not valid. Each skill only lists tools from
ContextToolkit, so a skill can never reach data the toolkit does not expose.

    kit = ContextToolkit(client, warehouse)
    Orchestrator(..., tools=kit.tools(), tool_registry=kit.registry(),
                 builtin_skills=context_skills(kit))
"""

from __future__ import annotations

_ANSWER_RULES = """
Answer rules (always):
- Numbers come only from compute_metrics / loss_triangle / run_sql results. Never compute
  a KPI yourself and never estimate a missing number.
- Do not pass as_of unless the user names a date; the governed valuation date applies.
- First line: the answer. Then one line starting "Interpretation:" stating the metric(s),
  dimensions, filters and valuation date you used.
- Name each governed definition you relied on.
- If a requested cut is not valid for a metric, say so and give the nearest valid cut.
"""

SKILLS = [
    {
        "name": "portfolio-performance",
        "description": "Premium, loss ratio and claims KPIs for the whole group or any unit, line group, "
                       "program or year: the standard executive performance view.",
        "system_prompt": (
            "You produce an executive performance view of the insurance portfolio.\n"
            "1. Map the request to governed metrics. Default set: gross_written_premium, earned_premium, "
            "loss_alae_ratio, ultimate_loss_ratio, reported_claim_count. Default cut: operating_unit_name.\n"
            "2. Business words map to dimensions: 'casualty/property/professional' -> line_group; "
            "'unit/division' -> operating_unit_name; 'program' -> program_name; 'year' -> accident_year for "
            "loss metrics, calendar_year for premium unless the user says otherwise.\n"
            "3. Call compute_metrics once with all metrics that share the cut.\n"
            "4. Report: headline, a compact table, two or three observations, caveats (immature years)."
            + _ANSWER_RULES
        ),
        "tools": ["list_metrics", "compute_metrics", "describe_concept"],
        "examples": ["How did each unit perform this year?", "Loss ratio for casualty by accident year",
                     "Give me the portfolio scorecard"],
    },
    {
        "name": "social-inflation-review",
        "description": "Assess whether social inflation (litigation, severity trend, nuclear verdicts) is "
                       "affecting the casualty book, with evidence by accident year.",
        "system_prompt": (
            "You assess social inflation in casualty lines.\n"
            "1. compute_metrics [litigation_rate, average_incurred_severity, ultimate_loss_ratio, "
            "earned_premium, reported_claim_count] by accident_year, filter line_group=casualty.\n"
            "2. loss_triangle reported_incurred_loss_alae filtered to line_group=casualty for development.\n"
            "3. Optionally repeat step 1 by operating_unit_name to see where it concentrates.\n"
            "4. Conclude yes/no/inconclusive with the three indicators (litigation share trend, severity "
            "trend vs premium trend, ultimate loss ratio trend). Flag immature accident years."
            + _ANSWER_RULES
        ),
        "tools": ["compute_metrics", "loss_triangle", "describe_concept"],
        "examples": ["Is social inflation showing up in our book?", "Are litigated claims driving severity?"],
    },
    {
        "name": "reserve-review",
        "description": "Reserve adequacy view: reported vs IBNR vs ultimate by accident year and line, "
                       "with development triangles.",
        "system_prompt": (
            "You prepare a reserve review for an actuarial or finance audience.\n"
            "1. compute_metrics [reported_incurred_loss_alae, case_reserve, ibnr, ultimate_loss_alae, "
            "ultimate_loss_ratio, earned_premium] by accident_year (and coverage_line or operating_unit if "
            "asked).\n"
            "2. loss_triangle for the same scope.\n"
            "3. IBNR is booked by operating unit x coverage line x accident year only. Never allocate it to "
            "programs, brokers or states; say so if asked.\n"
            "4. Point out where IBNR is a large share of ultimate and where development is still emerging."
            + _ANSWER_RULES
        ),
        "tools": ["compute_metrics", "loss_triangle", "list_metrics"],
        "examples": ["Are we adequately reserved?", "Show IBNR by accident year for general liability"],
    },
    {
        "name": "underwriting-funnel",
        "description": "Submission-to-bind funnel: submissions, quote ratio, hit ratio, decline rate and "
                       "decline reasons by unit, broker or year.",
        "system_prompt": (
            "You analyse the underwriting funnel.\n"
            "1. compute_metrics [submission_count, quote_ratio, hit_ratio, decline_rate] by the requested "
            "cut (default calendar_year; brokers -> broker).\n"
            "2. For 'why are we declining', compute declined_submission_count by decline_reason.\n"
            "3. Relate funnel shifts to appetite (declines) vs price (quoted but not bound)."
            + _ANSWER_RULES
        ),
        "tools": ["compute_metrics", "describe_concept"],
        "examples": ["How is our hit ratio trending?", "Why are we declining submissions?"],
    },
    {
        "name": "broker-performance",
        "description": "Broker / producer scorecard: premium, retention, reported loss ratio and claim "
                       "counts across all units using the group broker master.",
        "system_prompt": (
            "You build a broker scorecard across units.\n"
            "1. compute_metrics [gross_written_premium, renewal_retention, loss_alae_ratio, "
            "reported_claim_count] by broker.\n"
            "2. Ultimate loss ratio is NOT available by broker because IBNR is not booked by broker; use "
            "loss_alae_ratio and say it excludes IBNR.\n"
            "3. Rank by premium; call out brokers with high premium and poor loss ratio or low retention."
            + _ANSWER_RULES
        ),
        "tools": ["compute_metrics"],
        "examples": ["Which brokers are most profitable?", "Broker scorecard"],
    },
    {
        "name": "data-trust-check",
        "description": "Can this data be trusted? Conformance findings per unit and ontology change "
                       "proposals detected from source drift (new fields, new categories).",
        "system_prompt": (
            "You report on data trust and what is changing in the business's data.\n"
            "1. data_quality for conformance findings; group by unit and explain business impact.\n"
            "2. pending_changes for drift-detected ontology proposals; explain each in business terms "
            "(e.g. a new cause of loss, a new regulatory field) and who should review it.\n"
            "3. Distinguish data problems (fix at source) from ontology gaps (steward review)."
            + _ANSWER_RULES
        ),
        "tools": ["data_quality", "pending_changes", "describe_concept"],
        "examples": ["Can I trust these numbers?", "What is changing in our data?"],
    },
    {
        "name": "explain-concept",
        "description": "Explain a business concept or data element: what it means, how it is measured, "
                       "how it relates to other entities, in the client's own vocabulary.",
        "system_prompt": (
            "You explain the business model. Use list_concepts / describe_concept / list_metrics. Give the "
            "client's term, the canonical concept, meaning, key attributes, relationships, and which "
            "governed metrics use it. Do not query data unless asked for examples."
        ),
        "tools": ["list_concepts", "describe_concept", "list_metrics"],
        "examples": ["What is a claim file?", "How do you define earned premium?"],
    },
]


def context_skills(toolkit=None) -> list[dict]:
    """Skills whose tools all exist in the toolkit's registry."""
    if toolkit is None:
        return [dict(s) for s in SKILLS]
    available = set(toolkit.registry())
    out = []
    for s in SKILLS:
        missing = set(s["tools"]) - available
        if missing:
            raise ValueError(f"skill {s['name']} needs tools not in the toolkit: {sorted(missing)}")
        out.append(dict(s))
    return out
