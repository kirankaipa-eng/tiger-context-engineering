"""Guardrails on the sweeps (the Living Brief, the Scout): what they cost the warehouse depends on what is watched,
not on how many measures the model has; a run stops at its budget and says so; one run at a time per brief; no
more than PARALLEL sweep queries on the warehouse at once."""

import threading
import time

from test_brief import team  # noqa: F401 - the fixture

from context_studio import pbi_brief, pbi_team, pbi_watch


class Layer:
    def __init__(self, n):
        self.metrics = {f"m{i}": {} for i in range(n)}


class Big:
    """A model with `n` measures that add up by month, and no data."""
    def __init__(self, folder, n):
        self.folder, self.layer = folder, Layer(n)


def test_a_model_of_700_measures_watches_ten_the_headline_first(tmp_path, monkeypatch):
    monkeypatch.setattr(pbi_team, "_additive", lambda layer: list(layer.metrics))
    monkeypatch.setattr(pbi_team, "_period", lambda layer, k: "Calendar[Date]:month")
    big = Big(tmp_path, 700)
    assert pbi_watch.watchlist(big) == ["m0", "m1"]  # nothing chosen: the brief's two headline measures
    (tmp_path / "brief.yaml").write_text("watch: [m5, m6, nope]\n")
    assert pbi_watch.watchlist(big, headline=["m1"]) == ["m1", "m5", "m6"]
    assert len(pbi_watch.watchlist(big, mine=[f"m{i}" for i in range(50)])) == pbi_watch.WATCH
    assert len(pbi_watch.watchlist(Big(tmp_path, 12))) == 12  # a small model: every measure


def test_a_brief_stops_at_its_budget_and_says_it_is_partial(team):  # noqa: F811
    (team.folder / "brief.yaml").write_text("budget: 25\n")
    b = pbi_brief.brief(team)
    assert b["spent"] == {"queries": 25, "budget": 25, "partial": True}
    (team.folder / "brief.yaml").unlink()
    pbi_brief._CACHE.clear()
    full = pbi_brief.brief(team)
    assert not full["spent"]["partial"] and full["spent"]["queries"] < pbi_watch.BUDGET and full["claims"]


def test_a_run_has_its_own_results_and_leaves_the_shared_toolkit_alone(team):  # noqa: F811
    shared = team.kit()
    pbi_brief.brief(team)
    assert shared.results == []


def test_two_people_opening_one_brief_run_it_once():
    calls, gate = [], threading.Event()

    def slow():
        calls.append(1)
        gate.wait(2)
        return "the brief"

    got = []
    threads = [threading.Thread(target=lambda: got.append(pbi_watch.single(("t",), slow))) for _ in range(3)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join()
    assert calls == [1] and got == ["the brief"] * 3


def test_no_more_than_parallel_sweep_queries_at_once():
    lock, now, most = threading.Lock(), [0], [0]

    class Slow:
        def query(self, sql, limit=None):
            with lock:
                now[0] += 1
                most[0] = max(most[0], now[0])
            time.sleep(0.05)
            with lock:
                now[0] -= 1
            return [], []

    runs = [pbi_watch.Budgeted(Slow(), 100) for _ in range(6)]
    threads = [threading.Thread(target=lambda w=w: [w.query("select 1") for _ in range(3)]) for w in runs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most[0] == pbi_watch.PARALLEL
