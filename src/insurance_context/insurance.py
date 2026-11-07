"""How a health payer reads cost: the knowledge an analyst brings to a question before any number is asked for.

Cost trend is PMPM, dollars per member per month. It moves for two reasons only, more services per member (utilization, often
per 1,000 members) or a higher cost per service (unit cost), and it is read by service category: inpatient, outpatient,
professional, emergency and pharmacy. So "what is driving cost" has a known shape, and the harness works it out in code:

    plan(catalog, key)    the measures that break a PMPM down: per category its PMPM, its utilization and its unit cost
    read(plan, now, then) that breakdown in words, from numbers the warehouse returned for two periods

Nothing here names a client's measure: a measure is placed by the words of its own name (findable), so the same plan is found
in any model that names its measures the way the business talks.
"""

from __future__ import annotations

from . import findable

PRIMER = """How a health payer reads cost: cost trend is PMPM, dollars per member per month. PMPM moves only through utilization
(services per member, often per 1,000 members) and unit cost (cost per service), and it is read by service category: inpatient,
outpatient, professional, emergency and pharmacy. "What is driving cost" asks which categories moved most, and whether more services or
a higher cost per service moved them. The most recent months may not be complete, because claims arrive after the service (runout)."""

SAY = """Say a cost trend the way an actuary would: the categories that moved it most, and for each whether more services (utilization)
or a higher cost per service (unit cost) moved it, when the results say. When a period has no number, say its claims may not be complete
yet; never tell the reader to widen a date range or a population."""

CATEGORIES = {
    "inpatient": ["inpatient", "ip", "admit", "admission", "acute"],
    "outpatient": ["outpatient", "op"],
    "professional": ["professional", "prof", "physician"],
    "emergency": ["emergency", "er", "ed"],
    "pharmacy": ["pharmacy", "rx", "drug", "script", "prescription"],
}
MEDICAL = ("inpatient", "outpatient", "professional", "emergency")
PARTS = 5  # the categories one breakdown reads
_RATE = {"1000", "k", "per", "rate"}
_MONEY = {"total", "overall", "all", "allowed", "paid", "billed", "net", "gross", "claim", "amount", "amt"}  # what a total may say
_HEARD = {w for ps in findable.TERMS.values() for p in ps for w in findable.split(p)}


def category(words: list[str]) -> str | None:
    """The level a name is about: a service category, "medical" for all of medical, or None for a total."""
    hits = findable.heard(words, CATEGORIES)
    if len(hits) == 1:
        return hits[0]
    return "medical" if not hits and {"medical", "med"} & set(words) else None


def _kind(entry: dict, terms: dict) -> str | None:
    tags = findable.heard(entry["name"], terms)
    if "pmpm" in tags:
        return "pmpm"
    if "unitcost" in tags:
        return "cost"
    return "util" if "utilization" in tags else None


_CATS = {w for ps in CATEGORIES.values() for p in ps for w in findable.split(p)} | {"medical", "med"}


def _plain(words: list[str]) -> set[str]:
    """What a name says beyond its kind and level: allowed or paid, for one."""
    return set(words) - _CATS - _HEARD - findable._VERSION - _RATE


def plan(catalog, key: str) -> dict | None:
    """The breakdown of a PMPM measure: its own utilization and unit cost, and the PMPMs one level under it with theirs.
    None when the measure is no PMPM, or nothing in the catalog breaks it down."""
    me = catalog.metrics.get(key)
    if not me or _kind(me, catalog.terms) != "pmpm":
        return None
    usable = {k: e for k, e in catalog.metrics.items() if k != key and e["role"] == me["role"] and _kind(e, catalog.terms)}
    level, words = category(me["name"]), _plain(me["name"])

    def best(kind: str, at: str | None) -> str | None:
        found = [(len(words & _plain(e["name"])), bool(_RATE & set(e["name"])), -len(e["name"]), k) for k, e in usable.items()
                 if _kind(e, catalog.terms) == kind and category(e["name"]) == at and words - _MONEY <= _plain(e["name"])]
        return max(found)[-1] if found else None

    def part(at: str | None, pmpm: str) -> dict:
        return {"category": at, "pmpm": pmpm, "util": best("util", at), "cost": best("cost", at)}

    head = {"category": level, "pmpm": key, "util": best("util", level), "cost": best("cost", level)}
    if level in CATEGORIES:
        under = []
    elif level == "medical":
        under = list(MEDICAL)
    elif words - _MONEY:
        under = []  # a slice of its own (one drug class, one program): no category sits under it
    else:
        under = ["medical", "pharmacy"] if best("pmpm", "medical") and best("pmpm", "pharmacy") else [*MEDICAL, "pharmacy"]
    parts = [part(at, k) for at in under if (k := best("pmpm", at))][:PARTS]
    if not parts and not (head["util"] or head["cost"]):
        return None
    return {"head": head, "parts": parts}


def keys(p: dict) -> list[str]:
    return list(dict.fromkeys(k for x in [p["head"], *p["parts"]] for k in (x["pmpm"], x["util"], x["cost"]) if k))


def _pct(now, then) -> float | None:
    return (now / then - 1) * 100 if isinstance(now, (int, float)) and isinstance(then, (int, float)) and then else None


def _drivers(x: dict, now: dict, then: dict, label) -> str:
    said = [f"{label(x[k])} {v:+.1f}%" for k in ("util", "cost") if x[k] and (v := _pct(now.get(x[k]), then.get(x[k]))) is not None]
    return f" ({', '.join(said)})" if said else ""


def lean(x: dict, now: dict, then: dict) -> str | None:
    """"utilization" or "unit cost": which moved a PMPM more, when both are known."""
    u, c = (_pct(now.get(x[k]), then.get(x[k])) if x[k] else None for k in ("util", "cost"))
    if u is None or c is None:
        return None
    return "utilization" if abs(u) > abs(c) else "unit cost"


def read(p: dict, now: dict, then: dict, label, show, when: str) -> str:
    """The breakdown in words, from the numbers of two periods. Every number here came from the warehouse."""
    h = p["head"]
    a, b = now.get(h["pmpm"]), then.get(h["pmpm"])
    if not isinstance(a, (int, float)):
        return ""
    lines = [f"{label(h['pmpm'])}: {show(h['pmpm'], a)}" + (f" against {show(h['pmpm'], b)}, {_pct(a, b):+.1f}%, {when}"
                                                            if _pct(a, b) is not None else f", {when}") + _drivers(h, now, then, label) + "."]
    moved = sorted(((now.get(x["pmpm"]) - then.get(x["pmpm"]), x) for x in p["parts"]
                    if isinstance(now.get(x["pmpm"]), (int, float)) and isinstance(then.get(x["pmpm"]), (int, float))),
                   key=lambda m: -abs(m[0]))
    if moved:
        lines.append("By service category, the change in PMPM: " + "; ".join(
            f"{label(x['pmpm'])} {'+' if d >= 0 else '-'}{show(x['pmpm'], abs(d))}{_drivers(x, now, then, label)}" for d, x in moved) + ".")
        d, top = moved[0]
        why = lean(top, now, then)
        lines.append(f"The largest mover is {label(top['pmpm'])}" + (f", moved more by {why}" if why else "") + ".")
    elif why := lean(h, now, then):
        lines.append(f"It moved more by {why}.")
    return "\n".join(lines)
