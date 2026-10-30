"""list_columns with no metrics lists what any metric can be grouped by, not only what all of them share."""

from insurance_context.bi_agent import BIToolkit

REACH = {"sales": ["Shop[City]", "Customer[Age]"], "returns": ["Shop[City]"], "what_if": []}


class Layer:
    tables: dict = {}

    def captured(self):
        return dict.fromkeys(REACH)

    def columns_for(self, metrics):
        shared = None
        for k in metrics:
            shared = REACH[k] if shared is None else [c for c in shared if c in REACH[k]]
        return shared or []


def test_without_metrics_every_reachable_column_is_listed():
    out = BIToolkit(Layer(), None).list_columns()
    assert "Shop[City]" in out and "Customer[Age]" in out  # a what-if metric reaching nothing hides none


def test_with_metrics_only_what_they_share():
    out = BIToolkit(Layer(), None).list_columns(["sales", "returns"])
    assert "Shop[City]" in out and "Customer[Age]" not in out
