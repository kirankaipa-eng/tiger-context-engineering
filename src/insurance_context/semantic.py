"""Semantic layer: governed metrics compiled to SQL over the ontology views.

Definitions live in ontology/metrics/<line>.yaml; a client may override or add
metrics in client.yaml under `metrics:`. Joins are never written by hand: a path
such as `claim.policy.operating_unit` is resolved by following the ontology's
class-ranged slots, so a definition runs unchanged on any mapped client.

Every query is pinned to a valuation date (`as_of`), which makes results
reproducible and lets the same metric be evaluated at successive valuations to
build development triangles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from .client import Client
from .overlay import CONTENT_ROOT, OverlayError
from .schema_utils import slot_kind, table_name
from .sqlgen import GROUP_SCHEMA, tx

METRICS_DIR = CONTENT_ROOT / "ontology" / "metrics"
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")


class MetricError(ValueError):
    pass


@dataclass
class Metric:
    name: str
    label: str
    description: str = ""
    format: str = "number"
    entity: str | None = None
    measure: str | None = None
    as_of: str | None = None
    filter: str | None = None
    ratio: list[str] | None = None
    sum_of: list[str] | None = None
    scale: float | None = None      # ratio multiplier, e.g. 12000 for annualized per-1,000 member months
    source: str = "pack"

    @property
    def is_base(self) -> bool:
        return self.entity is not None

    def definition(self) -> dict:
        d = {"name": self.name, "label": self.label, "description": self.description,
             "format": self.format, "source": self.source}
        for k in ("entity", "measure", "as_of", "filter", "ratio", "sum_of", "scale"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        return d


@dataclass
class Dimension:
    name: str
    label: str
    paths: dict[str, str]
    description: str = ""
    enum_group: dict | None = None  # {enum, annotation}: group enum values by an annotation
    aliases: dict = field(default_factory=dict)  # how people say a value -> the value in the data


@dataclass
class _Scope:
    """Join bookkeeping for one entity's CTE."""
    entity: str
    joins: dict[str, tuple[str, str, str]] = field(default_factory=dict)  # path -> (alias, table, on)


@dataclass
class Result:
    columns: list[str]
    rows: list[tuple]
    sql: str
    as_of: date
    definitions: dict[str, dict]

    def records(self) -> list[dict]:
        return [dict(zip(self.columns, r)) for r in self.rows]


class SemanticLayer:
    def __init__(self, client: Client, metrics_file: Path | None = None):
        self.client = client
        self.sv = client.resolved.view
        path = metrics_file or METRICS_DIR / f"{client.line}.yaml"
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.version = spec.get("version", "0")
        self.derived: dict[str, dict] = spec.get("derived") or {}
        self.dimensions = {
            n: Dimension(n, d.get("label", n), d["paths"], d.get("description", ""), d.get("enum_group"),
                         d.get("aliases") or {})
            for n, d in (spec.get("dimensions") or {}).items()
        }
        self.metrics: dict[str, Metric] = {}
        for n, m in (spec.get("metrics") or {}).items():
            self.metrics[n] = Metric(name=n, label=m.get("label", n), **_metric_fields(m))
        for n, m in (client.config.get("metrics") or {}).items():
            base = self.metrics.get(n)
            fields_ = {**(base.definition() if base else {}), **m}
            fields_.pop("name", None)
            fields_.pop("source", None)
            label = fields_.pop("label", n)
            self.metrics[n] = Metric(name=n, label=label, source=f"client:{client.name}",
                                     **_metric_fields(fields_))
        for dim_name, d in (client.config.get("dimensions") or {}).items():
            self.dimensions[dim_name] = Dimension(dim_name, d.get("label", dim_name), d["paths"],
                                                  d.get("description", ""), d.get("enum_group"),
                                                  d.get("aliases") or {})

    # ------------------------------------------------------------ validation

    def validate(self) -> list[str]:
        """Resolve every path in every definition; return problems (empty = healthy)."""
        problems = []
        for m in self.metrics.values():
            try:
                self._leaves(m.name)
                if m.is_base:
                    scope = _Scope(m.entity)
                    self._render(m.measure, scope, date.today())
                    if m.filter:
                        self._render(m.filter, scope, date.today())
                    if m.as_of:
                        self._ref(m.as_of, scope)
            except (MetricError, OverlayError) as exc:
                problems.append(f"metric {m.name}: {exc}")
        for d in self.dimensions.values():
            for entity, path in d.paths.items():
                try:
                    self._dim_expr(d, _Scope(entity), date.today())
                except (MetricError, OverlayError) as exc:
                    problems.append(f"dimension {d.name} on {entity}: {exc}")
        return problems

    # ------------------------------------------------------------ availability

    def mapped_entities(self) -> set[str]:
        """Entities this client can query: classes some unit maps, plus derived entities
        whose every source table is one of those."""
        mapped = {cls for u in self.client.units for cls in (u.mappings.get("classes") or {})}
        tables = {table_name(c): c for c in mapped}
        for name, spec in self.derived.items():
            used = set(re.findall(r"\{ont\}\.([a-z_]+)", spec.get("sql", "")))
            if used <= set(tables):
                mapped.add(name)
        return mapped

    def unavailable(self) -> dict[str, list[str]]:
        """Metric -> why this client cannot answer it: the entities it needs that no unit maps
        (a client need not map the whole pack), or the client's own `withhold:` reason for a
        metric its data cannot support (e.g. no brand/generic flag behind a generic rate)."""
        have = self.mapped_entities()
        withheld = self.client.config.get("withhold") or {}
        out = {n: [str(reason)] for n, reason in withheld.items() if n in self.metrics}
        for n in self.metrics:
            if n in out:
                continue
            try:
                leaves = self._leaves(n)
            except MetricError:
                continue
            missing = sorted({self.metrics[b].entity for b in leaves} - have)
            if missing:
                out[n] = [f"{', '.join(missing)}, which no unit maps"]
            elif any(b in withheld for b in leaves):
                out[n] = [f"it is built on {', '.join(b for b in leaves if b in withheld)}, which is withheld"]
        return out

    def catalog(self) -> list[dict]:
        out = []
        unavailable = self.unavailable()
        for m in self.metrics.values():
            if m.name in unavailable:
                continue
            d = m.definition()
            d["dimensions"] = sorted(self.available_dimensions([m.name]))
            out.append(d)
        return out

    def available_dimensions(self, metrics: list[str]) -> set[str]:
        entities = {self.metrics[b].entity for n in metrics for b in self._leaves(n)}
        return {d.name for d in self.dimensions.values() if entities <= set(d.paths)}

    # ------------------------------------------------------------ compilation

    def compile(self, metrics: list[str], by: list[str] | None = None, where: dict | None = None,
                as_of: date | None = None, dialect: str = "duckdb") -> str:
        by = list(by or [])
        where = dict(where or {})
        as_of = as_of or date.today()
        unavailable = self.unavailable()
        for n in metrics:
            if n not in self.metrics:
                raise MetricError(f"unknown metric {n!r}; known: {sorted(self.metrics)}")
            if n in unavailable:
                raise MetricError(f"metric {n!r} is not available for {self.client.title}: "
                                  f"{'; '.join(unavailable[n])}")
        for d in [*by, *where]:
            if d not in self.dimensions:
                raise MetricError(f"unknown dimension {d!r}; known: {sorted(self.dimensions)}")

        leaves = []
        for n in metrics:
            leaves += [x for x in self._leaves(n) if x not in leaves]
        # One CTE per (entity, as_of, filter): metrics sharing them share a scan.
        groups: dict[tuple, list[Metric]] = {}
        for n in leaves:
            m = self.metrics[n]
            groups.setdefault((m.entity, m.as_of, m.filter), []).append(m)

        ctes, derived_used = [], []
        cte_names = []
        for i, ((entity, as_of_path, filt), ms) in enumerate(groups.items()):
            for d in [*by, *where]:
                if entity not in self.dimensions[d].paths:
                    raise MetricError(
                        f"dimension {d!r} is not defined for {entity} (needed by "
                        f"{', '.join(m.name for m in ms)}); available: "
                        f"{sorted(self.available_dimensions([m.name for m in ms]))}")
            if entity in self.derived and entity not in derived_used:
                derived_used.append(entity)
            scope = _Scope(entity)
            dim_cols = [f"{self._dim_expr(self.dimensions[d], scope, as_of)} AS {d}" for d in by]
            measures = [f"{self._render(m.measure, scope, as_of)} AS {m.name}" for m in ms]
            conds = []
            if as_of_path:
                conds.append(f"{self._ref(as_of_path, scope)} <= {_date(as_of)}")
            if filt:
                conds.append(f"({self._render(filt, scope, as_of)})")
            for d, v in where.items():
                expr = self._dim_expr(self.dimensions[d], scope, as_of)
                conds.append(_condition(expr, v))
            base = f"d_{table_name(entity)}" if entity in self.derived else f"{GROUP_SCHEMA}.{table_name(entity)}"
            joins = "".join(f"\n  LEFT JOIN {t} {a} ON {on}" for a, t, on in scope.joins.values())
            sql = f"SELECT {', '.join(dim_cols + measures)}\n  FROM {base} b{joins}"
            if conds:
                sql += "\n  WHERE " + "\n    AND ".join(conds)
            if by:
                sql += f"\n  GROUP BY {', '.join(str(k + 1) for k in range(len(by)))}"
            name = f"m{i}"
            cte_names.append((name, ms))
            ctes.append(f"{name} AS (\n  {sql}\n)")

        derived_ctes = [
            f"d_{table_name(e)} AS (\n{self._render_derived(e, as_of)}\n)" for e in derived_used
        ]
        metric_src = {m.name: name for name, ms in cte_names for m in ms}

        if by:
            keys = "\n  UNION\n  ".join(f"SELECT {', '.join(by)} FROM {n}" for n, _ in cte_names)
            ctes.append(f"k AS (\n  {keys}\n)")
            joins = "".join(
                f"\nLEFT JOIN {n} ON " + " AND ".join(f"{n}.{d} IS NOT DISTINCT FROM k.{d}" for d in by)
                for n, _ in cte_names)
            select = [f"k.{d}" for d in by]
            from_ = "k" + joins
        else:
            select, from_ = [], " CROSS JOIN ".join(n for n, _ in cte_names)
        select += [f"{self._expr(n, metric_src)} AS {n}" for n in metrics]
        sql = "WITH " + ",\n".join(derived_ctes + ctes) + f"\nSELECT {', '.join(select)}\nFROM {from_}"
        if by:
            sql += f"\nORDER BY {', '.join(str(i + 1) for i in range(len(by)))}"
        return tx(sql, dialect)

    def query(self, con, metrics: list[str], by: list[str] | None = None, where: dict | None = None,
              as_of: date | None = None) -> Result:
        as_of = as_of or date.today()
        sql = self.compile(metrics, by, where, as_of)
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        defs = {}
        for n in metrics:
            for x in [n, *self._leaves(n)]:
                defs[x] = self.metrics[x].definition()
        return Result(cols, cur.fetchall(), sql, as_of, defs)

    def dimension_values(self, con, metrics: list[str], dim: str, as_of: date | None = None) -> list:
        """Distinct values a dimension takes for these metrics' data (what a filter can match)."""
        res = self.query(con, metrics, by=[dim], as_of=as_of)
        return [r[0] for r in res.rows if r[0] is not None]

    def triangle(self, con, metric: str, ages_months: tuple[int, ...] = (12, 24, 36, 48, 60),
                 where: dict | None = None, as_of: date | None = None) -> Result:
        """Accident-year development triangle: metric evaluated at successive valuations."""
        as_of = as_of or date.today()
        rows: dict[int, dict[int, float]] = {}
        for age in ages_months:
            # Valuation at `age` months of development = year-end of AY + (age - 12) months.
            res_by_val = {}
            for ay in range(as_of.year - 10, as_of.year + 1):
                val = date(ay + age // 12 - 1, 12, 31)
                if val > as_of:
                    continue
                res_by_val.setdefault(val, []).append(ay)
            for val, ays in res_by_val.items():
                r = self.query(con, [metric], by=["accident_year"],
                               where={**(where or {}), "accident_year": ays}, as_of=val)
                for ay_value, v in r.rows:
                    if ay_value is not None:
                        rows.setdefault(int(ay_value), {})[age] = v
        cols = ["accident_year", *[f"{a}m" for a in ages_months]]
        out = [(ay, *[rows[ay].get(a) for a in ages_months]) for ay in sorted(rows)]
        return Result(cols, out, "-- triangle: one query per valuation", as_of,
                      {metric: self.metrics[metric].definition()})

    # ------------------------------------------------------------ internals

    def _leaves(self, name: str, seen: tuple = ()) -> list[str]:
        if name in seen:
            raise MetricError(f"metric cycle: {' -> '.join((*seen, name))}")
        m = self.metrics.get(name)
        if m is None:
            raise MetricError(f"unknown metric {name!r}")
        if m.is_base:
            return [name]
        parts = m.ratio or m.sum_of
        if m.scale and not m.ratio:
            raise MetricError(f"metric {name}: scale applies to ratio metrics only")
        if not parts:
            raise MetricError(f"metric {name} needs entity+measure, ratio or sum_of")
        out = []
        for p in parts:
            out += [x for x in self._leaves(p, (*seen, name)) if x not in out]
        return out

    def _expr(self, name: str, src: dict[str, str]) -> str:
        m = self.metrics[name]
        if m.is_base:
            return f"coalesce({src[name]}.{name}, 0)"
        if m.ratio:
            num, den = (self._expr(p, src) for p in m.ratio)
            scale = f" * {m.scale}" if m.scale else ""
            return f"CAST({num} AS DOUBLE){scale} / nullif({den}, 0)"
        return "(" + " + ".join(self._expr(p, src) for p in m.sum_of) + ")"

    def _render_derived(self, entity: str, as_of: date) -> str:
        sql = self.derived[entity]["sql"]
        return sql.replace("{ont}", GROUP_SCHEMA).replace("{as_of}", _date(as_of)).strip()

    def _dim_expr(self, dim: Dimension, scope: _Scope, as_of: date) -> str:
        spec = dim.paths.get(scope.entity)
        if spec is None:
            raise MetricError(f"dimension {dim.name} has no path for {scope.entity}")
        expr = self._render(spec, scope, as_of) if "{" in spec else self._ref(spec, scope)
        if dim.enum_group:
            expr = self._enum_group_case(expr, dim.enum_group["enum"], dim.enum_group["annotation"])
        return expr

    def _enum_group_case(self, expr: str, enum_name: str, tag: str) -> str:
        """CASE expression mapping enum values to the group named by an annotation on each value."""
        from .schema_utils import annotation
        enum = self.sv.get_enum(enum_name)
        if enum is None:
            raise MetricError(f"unknown enum {enum_name!r}")
        whens = []
        for value, pv in enum.permissible_values.items():
            group = annotation(pv, tag)
            if group:
                whens.append(f"WHEN '{value}' THEN '{group}'")
        if not whens:
            raise MetricError(f"no {tag!r} annotations on {enum_name}")
        return f"CASE {expr} {' '.join(whens)} ELSE 'unassigned' END"

    def _render(self, template: str, scope: _Scope, as_of: date) -> str:
        def sub(m):
            key = m.group(1)
            if key == "as_of":
                return _date(as_of)
            if key == "ont":
                return GROUP_SCHEMA
            return self._ref(key, scope)
        return _PLACEHOLDER.sub(sub, template)

    def _columns(self, entity: str) -> dict[str, str | None]:
        """column -> class range (or None) for an entity, real or derived."""
        if entity in self.derived:
            return {c: (spec or {}).get("range") for c, spec in self.derived[entity]["columns"].items()}
        if entity not in self.sv.all_classes():
            raise MetricError(f"unknown entity {entity!r}")
        cols = {}
        for s in self.sv.class_induced_slots(entity):
            cols[s.name] = s.range if slot_kind(self.sv, s) == "class" and not s.multivalued else None
        return cols

    def _ref(self, path: str, scope: _Scope) -> str:
        parts = path.split(".")
        entity, alias = scope.entity, "b"
        for i, hop in enumerate(parts):
            cols = self._columns(entity)
            if hop not in cols:
                raise MetricError(f"{entity} has no attribute {hop!r} (in path {path!r})")
            if i == len(parts) - 1:
                return f"{alias}.{hop}"
            target = cols[hop]
            if target is None:
                raise MetricError(f"{entity}.{hop} is not a relationship (in path {path!r})")
            key = ".".join(parts[: i + 1])
            if key not in scope.joins:
                j = f"j{len(scope.joins) + 1}"
                scope.joins[key] = (j, f"{GROUP_SCHEMA}.{table_name(target)}", f"{j}.id = {alias}.{hop}")
            alias, entity = scope.joins[key][0], target
        raise MetricError(f"empty path {path!r}")


def _metric_fields(m: dict) -> dict:
    allowed = {"description", "format", "entity", "measure", "as_of", "filter", "ratio", "sum_of", "scale"}
    unknown = set(m) - allowed - {"label"}
    if unknown:
        raise MetricError(f"unknown metric keys {sorted(unknown)}")
    return {k: v for k, v in m.items() if k in allowed}


def _date(d: date) -> str:
    return f"DATE '{d.isoformat()}'"


def _condition(expr: str, value) -> str:
    def lit(v):
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return str(v)
        return "'" + str(v).replace("'", "''") + "'"
    if isinstance(value, (list, tuple, set)):
        return f"{expr} IN ({', '.join(lit(v) for v in value)})"
    if value is None:
        return f"{expr} IS NULL"
    return f"{expr} = {lit(value)}"
