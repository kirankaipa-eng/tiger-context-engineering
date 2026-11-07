"""What makes a metric findable: a catalog read from the capture, once, by plain rules.

    python -m insurance_context.findable <model folder>            write findable.yaml beside the capture, and report
    python -m insurance_context.findable <model folder> --ask "how much of the change in pmpm is utilization"
    python -m insurance_context.findable <model folder> --why <metric>      what stands between a metric and SQL

Business names are kept as code as often as words: a search on words cannot see "pmpm" in BilledPMPM. And one measure is
often four: current, prior, a change and a percent change, which read alike and mean different things. So each metric gets

    words    its name split as a reader would say it, with what its formula reads
    family   the measure it is a version of ("billed pmpm"), whatever the version
    role     current, prior, change_percent, change_amount, helper, or base

`context` is all of it as text, for a model to read and choose from: choosing is its work, and word matching is only the fallback.

and terms carry the business's own language: "utilization" finds units, "price" finds unit cost.

findable.yaml is the owner's to correct: `terms` adds to the language, and a metric's `role`, `family` and `also` stand when
the file is written again. Without the file the same catalog is built when the model loads, so nothing depends on running this.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

import yaml

NAME = "findable.yaml"
# phrases in the order they are read: "unit cost" must go before "unit" can be heard as utilization
TERMS = {
    "unitcost": ["unit cost", "cost per unit", "cost per", "price", "pricing", "average cost", "avg cost"],
    "utilization": ["utilization", "util", "volume", "unit", "visit", "admit", "script", "service count"],
    "pmpm": ["pmpm", "per member per month"],
    "pharmacy": ["pharmacy", "rx", "drug"],
    "inpatient": ["inpatient", "ip"],
    "outpatient": ["outpatient", "op"],
    "emergency": ["emergency", "er", "ed"],
}
_PRIOR = {"prior", "previous", "py", "ly", "lastyear"}
_CHANGE = {"change", "chg", "delta", "variance", "yoy", "growth"}
_PERCENT = {"percent", "pct"}
_AMOUNT = {"dollar", "amount", "amt"}
_VERSION = _PRIOR | _CHANGE | _PERCENT | _AMOUNT | {"current", "curr", "cy"}
HELPER = re.compile(r"(^|_)(id|key|flag|sort|index|title|caption|tooltip|header|candidate|staging|tmp|temp|test|debug)(_|$)|"
                    r"(day|month|period|year|date)_?id|(start|end|max|min|last|latest|current|first)_?(date|month|period|day)|"
                    r"selected|slicer", re.IGNORECASE)


def split(text: str) -> list[str]:
    """A name as words: BilledPMPM, Visits%Change_Dental and PMPMBilled all come apart, and "units" is "unit"."""
    s = re.sub(r"YoY", " yoy ", str(text)).replace("%", " percent ").replace("$", " dollar ")
    s = re.sub(r"(?<=\d),(?=\d{3})", "", s)  # 1,000 is one number, and a name writes it 1000
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=\d)", " ", s)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", s.lower())]


def heard(words: list[str], terms: dict | None = None) -> list[str]:
    """The business words these words amount to, longest phrase first, each phrase heard once."""
    said, out = f" {' '.join(words)} ", []
    phrases = sorted(((" ".join(split(p)), t) for t, ps in (terms or TERMS).items() for p in ps), key=lambda x: -len(x[0]))
    for phrase, tag in phrases:
        if f" {phrase} " in said:
            out.append(tag)
            said = said.replace(f" {phrase} ", "  ")
    return list(dict.fromkeys(out))


def role_of(key: str, words: list[str]) -> str:
    w = set(words)
    if w & _CHANGE:
        return "change_percent" if w & _PERCENT else "change_amount"
    if w & _PRIOR:
        return "prior"
    if HELPER.search(key) or HELPER.search("_".join(words)):
        return "helper"
    return "current" if w & {"current", "curr", "cy"} else "base"


def family_of(words: list[str]) -> str:
    return " ".join(sorted({w for w in words if w not in _VERSION}))


class Catalog:
    """Every metric's words, family and role, and each family's versions."""

    def __init__(self, metrics: dict, owner: dict | None = None):
        owner = owner or {}
        self.terms = {t: list(dict.fromkeys([*TERMS.get(t, []), *(owner.get("terms") or {}).get(t, [])]))
                      for t in [*TERMS, *(owner.get("terms") or {})]}
        labels = {str(m.get("label") or k).lower() for k, m in metrics.items()}
        self.metrics: dict[str, dict] = {}
        self.families: dict[str, dict[str, str]] = {}
        for k, m in metrics.items():
            mine = (owner.get("metrics") or {}).get(k) or {}
            name = split(m.get("label") or k)
            read = [r for r in re.findall(r"\[([^\]]+)\]", str(m.get("dax") or "")) if r.lower() in labels]
            more = [*split(" ".join(read)), *split(m.get("description") or ""), *split(" ".join(mine.get("also") or []))]
            tags = heard(name, self.terms)
            tags = [x for x in tags if x != "utilization"] if "unitcost" in tags else tags  # a cost per admit is a price, not a count
            entry = {"name": name, "words": list(dict.fromkeys([*name, *tags, *more])), "also": mine.get("also") or [],
                     "role": mine.get("role") or role_of(k, name), "family": mine.get("family") or family_of(name)}
            self.metrics[k] = entry
            if entry["family"]:
                # the first of a role keeps it: a second "current" of a family is a variant, and is found by its own words
                self.families.setdefault(entry["family"], {}).setdefault(entry["role"], k)

    def concepts(self, text: str) -> list[str]:
        """The business words a question names, in the order the terms know them."""
        return heard(split(text), self.terms)

    def context(self) -> str:
        """Every measure a question may use, a line to a measure with its versions, and what the business's words mean."""
        lines: dict[str, list[str]] = {}
        for k, e in self.metrics.items():
            if e["role"] != "helper":
                lines.setdefault(e["family"] or k, []).append(f"{e['role']}:{k}")
        said = "; ".join(f"{t} = {', '.join(ps)}" for t, ps in self.terms.items())
        return "\n".join(f"{f} | {' '.join(ks)}" for f, ks in lines.items()) + f"\nBusiness words: {said}"

    def query(self, text: str) -> list[str]:
        """What a question asks for, in the catalog's words."""
        words = split(text)
        return list(dict.fromkeys([*words, *heard(words, self.terms)]))

    def version(self, key: str, *, change: bool = False, prior: bool = False) -> str:
        """The member of a metric's family a question means: its current self, unless it asks for the change or the prior."""
        e = self.metrics.get(key)
        if not e or change or (prior and e["role"] == "prior") or e["role"] not in ("prior", "change_percent", "change_amount"):
            return key
        kin = self.families.get(e["family"], {})
        return kin.get("current") or kin.get("base") or key

    def is_change(self, key: str) -> bool:
        return self.metrics.get(key, {}).get("role") in ("change_percent", "change_amount")

    def data(self) -> dict:
        return {"terms": self.terms, "metrics": {k: {"role": e["role"], "family": e["family"], "words": e["words"], "also": e["also"]}
                                                 for k, e in self.metrics.items()}}


def load(folder, metrics: dict) -> Catalog:
    """The catalog of these metrics, with what an owner has corrected in findable.yaml."""
    base = Path(folder or ".")
    p = next((x for x in (base / NAME, base / "capture" / NAME) if x.exists()), None)
    return Catalog(metrics, (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p else {})


def blocked(metrics: dict, name: str) -> list[str]:
    """Down a metric's formula to what could not be translated: each measure in the way, and why, with what it is built on.
    Names and reasons only, never a formula. [] when every measure under it is captured."""
    by_label = {str(m.get("label") or k).lower(): k for k, m in metrics.items()}
    start = name if name in metrics else by_label.get(name.lower())
    if start is None:
        return [f"no metric {name!r}"]

    def walk(key: str, depth: int, seen: tuple) -> list[str]:
        m = metrics[key]
        text = f"{m.get('dax') or ''} {m.get('why') or ''}"
        under = [by_label[r.lower()] for r in dict.fromkeys(re.findall(r"\[([^\]]+)\]", text)) if r.lower() in by_label]
        below = [x for k in under if k not in seen and k != key for x in walk(k, depth + 1, (*seen, key))]
        stuck = m.get("status") not in ("captured", None) or m.get("why")
        if not stuck and not below:
            return []
        said = f": {m.get('why')}" if m.get("why") else ""
        return [f"{'  ' * depth}{m.get('label') or key} [{m.get('status') or 'part'}]{said}", *below]

    return walk(start, 0, ())


def main(argv=None) -> None:
    from .bilayer import BILayer

    ap = argparse.ArgumentParser(description="Make a captured model's metrics findable.")
    ap.add_argument("folder")
    ap.add_argument("--ask", help="show what a question finds, and write nothing")
    ap.add_argument("--why", help="show what stands between a metric and SQL, and write nothing")
    ap.add_argument("--top", type=int, default=10, help="how many measures --ask lists")
    a = ap.parse_args(argv)
    layer = BILayer.load(a.folder)
    if a.why:
        print("\n".join(blocked(layer.metrics, a.why)) or f"{a.why}: every measure under it is captured")
        return
    metrics = layer.captured()
    cat = load(a.folder, metrics)
    if a.ask:
        quiet = {"the", "of", "in", "is", "by", "how", "much", "what", "change", "versu", "v", "drive", "driven", "driving", "cost", "unit"}
        asked = set(cat.query(a.ask)) - quiet
        wants_change = bool(re.search(r"%|\bpercent\b|\bpct\b", a.ask))
        print("reads as:", ", ".join(sorted(asked)))
        hits = sorted(((len(asked & set(e["words"])), k) for k, e in cat.metrics.items()
                       if e["role"] != "helper" and (wants_change or not cat.is_change(k))), key=lambda x: (-x[0], x[1]))
        for n, k in [h for h in hits if h[0]][:a.top]:
            print(f"  {n}  {k}  [{cat.metrics[k]['role']}]  family: {cat.metrics[k]['family']}")
        return
    out = Path(a.folder) / NAME
    out.write_text(yaml.safe_dump(cat.data(), sort_keys=False, allow_unicode=True), encoding="utf-8")
    roles = Counter(e["role"] for e in cat.metrics.values())
    whole = sum(1 for f in cat.families.values() if len(f) > 1)
    print(f"{out}: {len(cat.metrics)} metrics, {len(cat.families)} families, {whole} with more than one version")
    print("roles: " + ", ".join(f"{r} {n}" for r, n in roles.most_common()))
    print(f"read whole by a model: {len(cat.context()):,} characters, about {len(cat.context()) // 4:,} tokens")


if __name__ == "__main__":
    main()
