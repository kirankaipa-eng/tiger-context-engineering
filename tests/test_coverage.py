"""`ictx pbi coverage`: measures not captured, grouped by the reason their root failed."""

from insurance_context.daxcapture import coverage

METRICS = {
    "paid": {"label": "Paid", "status": "captured"},
    "paid_part": {"label": "Paid part", "status": "captured", "hidden": True},
    "a": {"label": "A", "status": "not_captured", "gap": "function", "why": "uses PATHCONTAINS, which is not captured yet",
          "dax": "PATHCONTAINS(T[p], 1)"},
    "b": {"label": "B", "status": "not_captured", "gap": "dependency",
          "why": "built on [A], which is not captured: uses PATHCONTAINS, which is not captured yet", "dax": "[A] * 2"},
    "c": {"label": "C", "status": "not_captured", "gap": "dependency",
          "why": "built on [B], which is not captured: built on [A], ...", "dax": "[B] + 1"},
    "d": {"label": "D", "status": "not_captured", "gap": "context",
          "why": "reads Claims[State] outside an aggregation (the visual's current value)", "dax": "Claims[State]"},
    "e": {"label": "E", "status": "not_captured", "gap": "context",
          "why": "reads 'Member Months'[Month] outside an aggregation (the visual's current value)", "dax": "x"},
    "f": {"label": "F", "status": "presentation", "why": "formats a value as text"},
}


def test_dependents_count_under_their_root_and_like_reasons_group():
    out = coverage(METRICS)
    assert out.startswith("7 measures: 1 captured, 1 display-only, 5 not captured")  # hidden parts left out
    rows = [line.split(None, 3) for line in out.splitlines() if line[:6].strip().isdigit()]
    assert rows[0][:3] == ["3", "1", "function"]  # A, and B and C built on it
    assert rows[1][:3] == ["2", "2", "context"]   # D and E: the same reason with different columns
    assert "[...]" in rows[1][3] and "e.g. [A] = PATHCONTAINS" in out
