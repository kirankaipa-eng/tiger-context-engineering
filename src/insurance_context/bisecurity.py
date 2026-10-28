"""Row-level security for captured Power BI models: the model's roles, enforced on every query.

    capture_roles(model)            {role: {tables: {T: condition}, status, why}} from the model's RLS roles
    load_access(path, roles=None)   {role: [members]} from an owner-maintained access.yaml
    filters_for(spec, principal)    {table: SQL condition} for one caller, for BILayer.compile(row_filters=)

Interface contract (other modules build against it):
  * spec["security"] = {"roles": {name: {"status": "captured"|"not_captured", "why": str?,
                                          "tables": {Table: "<condition over {Table[Column]} placeholders>"},
                                          "dax": {Table: "<the role's DAX filter>"}, "dynamic": True?,
                                          "note": str?, "members_in_model": [...]?}},
                        "members": {name: [principal ids or "group:<name>"]}}   # members from access.yaml
  * Principal(id, groups, object_id): who is asking (an email / UPN, their groups, their Entra object id),
    from the MCP server's auth.
  * filters_for returns None when the model defines no roles (no RLS: every row is visible), else the
    OR of the caller's roles per table. A caller in no role, or in a role that was not captured,
    raises AccessDenied: fail closed, never return rows a role would hide. There is no bypass.

A role filter is DAX over the permission's own table ([Type] means Store[Type] in Store's permission);
it is parsed by daxcapture and written as a row condition the layer applies to every metric whose rows
reach that table. Dynamic RLS reads the caller: USERPRINCIPALNAME() and USERNAME() become
{USERPRINCIPALNAME} / {USERNAME}, USEROBJECTID() becomes {USEROBJECTID}, each substituted at
filters_for time with the caller's value as a quoted SQL literal. Identity comparisons are made
case-insensitively (lower() on both sides), as DAX compares text. Members of the roles come from
access.yaml, not the model: the model's own role memberships (kept as members_in_model) name Entra
accounts in the Power BI service, which is not who calls the MCP server.

Several roles combine the way Power BI combines them: per table, the OR of the roles' filters, and a
table one of the caller's roles leaves unfiltered is unfiltered for that caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DYNAMIC = ("USERPRINCIPALNAME", "USERNAME", "USEROBJECTID")
_SENTINEL = "\x00{}\x00"  # a string literal that stands for the caller until _row has written the SQL
_PLACEHOLDER = re.compile(r"\{(" + "|".join(DYNAMIC) + r")\}")


class AccessDenied(PermissionError):
    pass


@dataclass(frozen=True)
class Principal:
    id: str
    groups: tuple[str, ...] = field(default_factory=tuple)
    object_id: str | None = None  # for USEROBJECTID(); a role that needs it denies a caller without one


# ------------------------------------------------------------------ capture

def capture_roles(model) -> dict:
    """The model's roles as row conditions, each captured or not_captured with why."""
    from .daxcapture import Unsupported, _Ctx
    ctx = _Ctx(model, {}, {}, {}, set())
    out = {}
    for role in getattr(model, "roles", None) or []:
        spec: dict = {"status": "captured", "tables": {}, "dax": {t: " ".join(d.split()) for t, d in role.tables.items()}}
        perm = (role.model_permission or "read").strip()
        try:
            if perm.lower() == "none":
                raise Unsupported("permission", "modelPermission none: the role grants no read access")
            for table, dax in role.tables.items():
                cond, dynamic = _filter(ctx, table, dax)
                spec["tables"][table] = cond
                if dynamic:
                    spec["dynamic"] = True
        except Unsupported as u:
            spec = {"status": "not_captured", "why": u.why, "tables": {}, "dax": spec["dax"]}
        if perm.lower() not in ("read", "none"):
            spec["note"] = f"modelPermission {perm}: only reading is served here; row filters still apply"
        if role.members:
            spec["members_in_model"] = list(role.members)
        out[role.name] = spec
    return out


def _filter(ctx, table: str, dax: str) -> tuple[str, bool]:
    """One table permission's DAX filter as a SQL condition, and whether it reads the caller."""
    from .daxcapture import Unsupported, _row, parse
    if table not in ctx.model.tables:
        raise Unsupported("reference", f"filters table {table!r}, which is not in the model")
    used: set[str] = set()
    sql = _row(ctx, _resolve(ctx.model, table, parse(dax), used))
    for name in used:
        sql = sql.replace("'" + _SENTINEL.format(name) + "'", "{" + name + "}")
    if "\x00" in sql:
        raise Unsupported("dynamic", f"uses the caller's identity in a way that is not captured: {dax}")
    return sql, bool(used)


def _resolve(model, table: str, node, used: set):
    """The parsed filter with [Column] read as the permission table's column and the caller's identity
    as a sentinel literal; a comparison with the identity lowers the other side."""
    from .daxcapture import Unsupported
    if isinstance(node, list):
        return [_resolve(model, table, x, used) for x in node]
    if not isinstance(node, tuple) or not node:
        return node
    if node[0] == "ref":
        if model.tables[table].column(node[1]) is None:
            raise Unsupported("reference", f"refers to [{node[1]}], which is not a column of {table} (a measure?)")
        return ("col", table, node[1])
    if node[0] == "call" and node[1] in DYNAMIC:
        used.add(node[1])
        return ("str", _SENTINEL.format(node[1]))
    if node[0] == "bin" and node[1] in ("=", "<>", "IN"):
        a, b = _resolve(model, table, node[2], used), _resolve(model, table, node[3], used)
        if _is_identity(b) and not _is_identity(a):
            a = ("call", "LOWER", [a])
        elif _is_identity(a) and not _is_identity(b):
            b = ("call", "LOWER", [b])
        return ("bin", node[1], a, b)
    return tuple(_resolve(model, table, x, used) for x in node)


def _is_identity(node) -> bool:
    if node[0] == "set":
        return any(_is_identity(x) for x in node[1])
    return node[0] == "str" and node[1].startswith("\x00")


# ------------------------------------------------------------------ members

def load_access(path: str | Path, roles=None) -> dict[str, list[str]]:
    """The members of each role from an owner-maintained access.yaml, validated; this is what goes
    in spec["security"]["members"]. The file:

        roles:
          Store Managers: [ana@shop.com, group:Retail Ops]
          Finance: [group:Finance]

    A member is a principal id (email / UPN, matched case-insensitively) or group:<name>. Pass the
    captured roles (spec["security"]["roles"]) to reject a role the model does not define. Raises
    ValueError naming the file and the problem."""
    import yaml
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"{path}: cannot read ({e})") from None
    if not isinstance(data, dict) or not isinstance(data.get("roles", {}), dict) or set(data) - {"roles"}:
        raise ValueError(f"{path}: expected one key, roles: {{RoleName: [user@x.com, group:Name, ...]}}")
    out = {}
    for name, members in (data.get("roles") or {}).items():
        members = [] if members is None else members
        if not isinstance(members, list) or not all(isinstance(m, str) and m.strip() for m in members):
            raise ValueError(f"{path}: role {name!r} needs a list of member ids or group:<name>")
        members = [m.strip() for m in members]
        bad = [m for m in members if m.lower().startswith("group:") and not m[6:].strip()]
        if bad:
            raise ValueError(f"{path}: role {name!r} has a group member with no name")
        if roles is not None and str(name) not in roles:
            raise ValueError(f"{path}: role {name!r} is not defined in the model (roles: {', '.join(roles) or 'none'})")
        out[str(name)] = members
    return out


def _member(principal: Principal, entry: str) -> bool:
    if entry.lower().startswith("group:"):
        g = entry[6:].strip().casefold()
        return any(x.casefold().removeprefix("group:").strip() == g for x in principal.groups)
    return entry.casefold() == principal.id.strip().casefold()


# ------------------------------------------------------------------ per caller

def filters_for(spec: dict, principal: Principal | None) -> dict[str, str] | None:
    """{table: condition} for this caller (None when the model has no roles); AccessDenied otherwise."""
    sec = spec.get("security") or {}
    roles = sec.get("roles") or {}
    if not roles:
        return None
    if principal is None or not (principal.id or "").strip():
        raise AccessDenied("this model has row-level security; the caller's identity is required")
    members = sec.get("members") or {}
    mine = [n for n in roles if any(_member(principal, e) for e in members.get(n) or [])]
    if not mine:
        raise AccessDenied(f"{principal.id} is in none of this model's roles ({', '.join(roles)})")
    for n in mine:
        if roles[n].get("status") != "captured":
            raise AccessDenied(f"{principal.id} is in role {n!r}, which was not captured "
                               f"({roles[n].get('why', 'no reason recorded')}); refusing rather than guess its rows")
    out: dict[str, str] = {}
    for t in dict.fromkeys(t for n in mine for t in roles[n].get("tables") or {}):
        conds = [roles[n]["tables"].get(t) for n in mine]
        if any(c is None for c in conds):
            continue  # a role that leaves the table unfiltered shows the caller all of it
        conds = [_identity(c, principal) for c in dict.fromkeys(conds)]
        out[t] = conds[0] if len(conds) == 1 else " OR ".join(f"({c})" for c in conds)
    return out


def _identity(cond: str, principal: Principal) -> str:
    def value(m):
        v = principal.object_id if m.group(1) == "USEROBJECTID" else principal.id
        if not v:
            raise AccessDenied(f"a role filter reads {m.group(1)}(), which is not known for {principal.id}")
        return "'" + v.strip().lower().replace("'", "''") + "'"
    return _PLACEHOLDER.sub(value, cond)
