"""The captured Power BI metrics as an MCP server: any agent asks, the warehouse answers.

    ictx pbi serve --gitlab http://localhost:8929 --project group/pbi-models [--pin capture-v3]
    ictx pbi serve --dir public                        (a folder `ictx pbi ci` wrote)
    ictx pbi serve --dir public --http 8080 --host 0.0.0.0 --keys keys.yaml --audit audit.jsonl
    ictx pbi serve keygen --principal claims-bot@corp.com --groups claims,actuarial

Tools: list_models, model_version, list_metrics, list_columns, column_values, compute, explore (BIToolkit
underneath; compute takes what-if `params`, list_metrics says what each metric needs). Every model folder of the served release is loaded; each tool takes `model`, needed
only when more than one is served. Every answer starts with the model and capture version it came
from, so a number can be traced to the release, the commit and the pipeline that verified it.

From GitLab (GITLAB_TOKEN, read_api) the server serves the newest `capture-v*` release (or the one
it is pinned to) and checks for a newer one at most every `--refresh` seconds, so a merge to main
reaches every agent without a restart; pinning an earlier tag is the rollback. A reload builds the
whole new release before swapping it in; if it fails the previous one keeps serving and
model_version says why. The server holds the only warehouse connection (biwarehouse: Databricks via
OAuth M2M or a PAT when DATABRICKS_* is set, using a SELECT-only principal, else the stand-in shipped
with the capture). Agents never send SQL; they send metric keys, columns and filters.

Who is asking. Over stdio the server is local and unauthenticated: the caller is `--principal`
(or ICTX_PRINCIPAL, default the OS user) with `--groups` (ICTX_GROUPS). Over HTTP (`--http PORT`,
streamable HTTP at /mcp) every request carries `Authorization: Bearer <token>`, else 401:
  * API keys: `--keys FILE` (ICTX_PBI_KEYS), YAML `{sha256(key): {principal, groups}}`; keygen prints
    a new key once and its line. The file is re-read when it changes, so adding or revoking a key
    needs no restart. Only hashes are stored.
  * OIDC / JWT (Entra ID, Okta, ...): `--oidc-issuer` (ICTX_OIDC_ISSUER), `--oidc-audience`
    (ICTX_OIDC_AUDIENCE, comma separated), `--oidc-jwks` (ICTX_OIDC_JWKS; default the issuer's
    discovery document), `--principal-claim` (default upn,preferred_username,email: the first present)
    and `--groups-claim` (default groups). Signature, exp, aud and iss are verified (PyJWT).
Both may be on at once. `--no-auth` serves HTTP as the `--principal` identity (development only).
Every call resolves its Principal, asks bisecurity.filters_for for the model's row filters and
runs on a toolkit bound to them; AccessDenied comes back as a tool error, never as data.

Audit: one JSON line per tool call (ts, principal, groups, model, version, tool, args, sql_sha256,
rows, ms, outcome, error) to `--audit PATH` (ICTX_AUDIT_LOG) or stderr. Tokens are never logged.
Limits: `--timeout` per statement, `--max-rows` per answer, `--max-concurrent` queries at a time
(a call waits up to `--timeout` for a slot, then is told the server is busy), `--max-top`.
GET /healthz (no auth, HTTP only) returns the version, models and warehouse kind and pings the
warehouse (cached 30s; `?ping=0` skips it, for liveness probes that should not wake a warehouse).
"""

from __future__ import annotations

import getpass
import hashlib
import io
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .bi_agent import MAX_ROWS, BIToolkit
from .bici import VERSION, counts_line, measure_counts
from .bilayer import BILayer, LayerError, Warehouse
from .bisecurity import AccessDenied, Principal, filters_for

try:  # mcp extra; tool signatures are resolved against module globals
    from mcp.server.fastmcp import Context
except ImportError:  # pragma: no cover - the sources and Served still work without it
    Context = None  # type: ignore[assignment,misc]


class LocalCaptures:
    """A folder `ictx pbi ci` wrote: version.json and one folder per model."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)

    def current(self) -> tuple[str, Path]:
        v = json.loads((self.folder / VERSION).read_text()) if (self.folder / VERSION).exists() else {}
        return v.get("version", "local"), self.folder


class GitLabCaptures:
    """Releases `capture-v<N>` of a models repo; each links the capture job's files."""

    def __init__(self, url: str, project: str, token: str | None = None, pin: str | None = None,
                 cache: str | Path | None = None):
        self.api = url.rstrip("/") + "/api/v4/projects/" + urllib.parse.quote(project, safe="")
        self.token, self.pin = token or os.environ.get("GITLAB_TOKEN"), pin
        self.cache = Path(cache or Path.home() / ".cache" / "ictx-captures" / project.replace("/", "__"))

    def _get(self, url: str) -> bytes:
        headers = {"PRIVATE-TOKEN": self.token} if self.token else {}
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:  # noqa: S310
            return r.read()

    def latest_tag(self) -> str:
        if self.pin:
            return self.pin
        rels = json.loads(self._get(self.api + "/releases?order_by=released_at&sort=desc&per_page=20"))
        tags = [r["tag_name"] for r in rels if r["tag_name"].startswith("capture-")]
        if not tags:
            raise LayerError("no capture-v* release yet; merge to main so CI publishes one")
        return tags[0]

    def current(self) -> tuple[str, Path]:
        tag = self.latest_tag()
        folder = self.cache / tag
        if not (folder / "done").exists():
            rel = json.loads(self._get(f"{self.api}/releases/{urllib.parse.quote(tag)}"))
            link = next(x["url"] for x in rel["assets"]["links"] if x["name"] == "capture")
            with zipfile.ZipFile(io.BytesIO(self._get(link))) as zf:
                zf.extractall(folder)
            (folder / "done").touch()
        top = next((p for p in folder.iterdir() if p.is_dir() and (p / VERSION).exists()), folder)
        return tag.removeprefix("capture-"), top


@dataclass(frozen=True)
class Release:
    """One loaded capture release: every model's layer and warehouse, swapped in as a whole."""
    version: str
    info: dict
    key: tuple
    models: dict = field(default_factory=dict)  # name -> (BILayer, Warehouse)


def model_counts(layer: BILayer) -> dict:
    """Measures by status (presentation = display-only), with a line a person reads."""
    c = measure_counts(layer.spec)
    return {**c, "summary": counts_line(c)}


class Served:
    """The release being served, reloaded atomically when the source has a newer version.
    `model`: serve only these model folders (a name or a list); default all of them."""

    def __init__(self, source, model: str | list[str] | None = None, refresh: float = 60, prefer: str = "auto",
                 timeout: float | None = None):
        self.source, self.refresh, self.prefer, self.timeout = source, refresh, prefer, timeout
        self.only = [model] if isinstance(model, str) else model
        self.release: Release | None = None
        self.error, self.checked = None, 0.0
        self._lock, self._shared = threading.Lock(), {}
        self.reload(force=True)

    def _key(self, version: str, folder: Path) -> tuple:
        v = folder / VERSION
        return version, str(folder), v.stat().st_mtime_ns if v.exists() else None

    def _warehouse(self, folder: Path, layer: BILayer) -> Warehouse:
        wh = Warehouse.open(folder, layer, prefer=self.prefer, timeout=self.timeout)
        if wh.kind == "local":
            return wh
        return self._shared.setdefault((wh.kind, wh.target), wh)  # one pool across models and reloads

    def _build(self, version: str, folder: Path, key: tuple) -> Release:
        names = sorted(p.name for p in folder.iterdir() if (p / "capture.yaml").exists())
        if not names:
            raise LayerError(f"no model captures in {folder}")
        missing = [n for n in self.only or [] if n not in names]
        if missing:
            raise LayerError(f"no model {', '.join(map(repr, missing))} in {version}; it has {', '.join(names)}")
        models = {}
        for name in self.only or names:
            layer = BILayer.load(folder / name)
            models[name] = (layer, self._warehouse(folder / name, layer))
        info = json.loads((folder / VERSION).read_text()) if (folder / VERSION).exists() else {}
        return Release(version, info, key, models)

    def reload(self, force: bool = False) -> None:
        """Check the source (at most every `refresh` s). One caller reloads; the rest keep serving."""
        if not force and time.monotonic() - self.checked < self.refresh:
            return
        if not self._lock.acquire(blocking=force):
            return
        try:
            self.checked = time.monotonic()
            try:
                version, folder = self.source.current()
                key = self._key(version, folder)
                if self.release is not None and key == self.release.key:
                    self.error = None
                    return
                new = self._build(version, folder, key)
            except Exception as exc:
                if self.release is None:
                    raise
                why = " ".join(str(exc).split())[:300]
                self.error = f"could not load the newest capture ({why}); still serving {self.release.version}"
                print(f"reload failed: {self.error}", file=sys.stderr)
                return
            self.release, self.error = new, None
        finally:
            self._lock.release()

    def pick(self, model: str | None = None) -> tuple[Release, str]:
        rel = self.release
        if model is None:
            if len(rel.models) == 1:
                return rel, next(iter(rel.models))
            raise LayerError(f"{len(rel.models)} models are served; pass model= one of: {', '.join(rel.models)}")
        if model not in rel.models:
            raise LayerError(f"no model {model!r} in capture {rel.version}; served: {', '.join(rel.models)}")
        return rel, model

    def stamp(self, model: str | None = None, rel: Release | None = None) -> str:
        rel, name = (rel, model) if rel is not None else self.pick(model)
        i = rel.info
        return (f"[{name} capture {rel.version}" + (f" · commit {i['commit']}" if i.get("commit") else "")
                + f" · {rel.models[name][1].kind}]")

    @property
    def version(self) -> str:
        return self.release.version

    @property
    def kit(self) -> BIToolkit:
        """The single served model's toolkit with no row filters: for local scripts and tests only."""
        layer, wh = self.release.models[self.pick()[1]]
        return BIToolkit(layer, wh)

    def call(self, fn, *args) -> str:
        try:
            self.reload()
        except Exception as exc:  # noqa: BLE001 - keep serving the version already loaded
            self.error = str(exc)
        return f"{self.stamp()}\n" + (f"({self.error})\n" if self.error else "") + f"{fn(*args)}"


# ------------------------------------------------------------------ who is asking

def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def keygen(principal: str, groups: list[str]) -> tuple[str, str]:
    """A new API key and the keys-file line that admits it (only the hash is kept)."""
    key = "pbik_" + secrets.token_urlsafe(32)
    return key, f"{hash_key(key)}: {json.dumps({'principal': principal, 'groups': groups})}"


class ApiKeys:
    """A YAML keys file {sha256(key): {principal, groups}}, re-read when it changes."""

    def __init__(self, path: str | Path):
        self.path, self._mtime, self._keys = Path(path), None, {}
        self._load()

    def _load(self) -> None:
        import yaml
        mtime = self.path.stat().st_mtime_ns
        if mtime != self._mtime:
            raw = yaml.safe_load(self.path.read_text()) or {}
            self._keys = {str(h).lower(): Principal(str(v["principal"]), tuple(v.get("groups") or ()))
                          for h, v in raw.items()}
            self._mtime = mtime

    def lookup(self, key: str) -> Principal | None:
        try:
            self._load()
        except Exception as exc:  # noqa: BLE001 - a broken edit keeps the keys already loaded
            print(f"keys file {self.path}: {exc}", file=sys.stderr)
        return self._keys.get(hash_key(key))


class OIDC:
    """Bearer JWTs from an OpenID Connect issuer (Entra ID: issuer https://login.microsoftonline.com/
    <tenant>/v2.0, audience the app's client id or api://... URI)."""

    def __init__(self, issuer: str, audience: str | list[str], jwks_url: str | None = None,
                 principal_claims: tuple[str, ...] = ("upn", "preferred_username", "email"),
                 groups_claims: tuple[str, ...] = ("groups",), algorithms: tuple[str, ...] = ("RS256", "ES256")):
        self.issuer, self.audience = issuer, [audience] if isinstance(audience, str) else list(audience)
        self.principal_claims, self.groups_claims, self.algorithms = principal_claims, groups_claims, list(algorithms)
        self.jwks_url, self._jwks = jwks_url, None

    def _client(self):
        import jwt  # pip install 'insurance-context[mcp]' (PyJWT[crypto])
        if self._jwks is None:
            url = self.jwks_url
            if not url:
                conf = self.issuer.rstrip("/") + "/.well-known/openid-configuration"
                with urllib.request.urlopen(conf, timeout=30) as r:  # noqa: S310
                    url = json.loads(r.read())["jwks_uri"]
            self._jwks = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=30)
        return self._jwks

    def verify(self, token: str) -> tuple[Principal, dict] | None:
        import jwt
        try:
            key = self._client().get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=self.algorithms, audience=self.audience,
                                issuer=self.issuer, leeway=30, options={"require": ["exp", "iss", "aud"]})
        except jwt.PyJWTError as exc:
            print(f"bearer token rejected: {type(exc).__name__}", file=sys.stderr)
            return None
        who = next((claims[c] for c in self.principal_claims if claims.get(c)), None)
        if not who:
            print(f"bearer token has none of {', '.join(self.principal_claims)}", file=sys.stderr)
            return None
        groups = []
        for c in self.groups_claims:
            v = claims.get(c) or []
            groups += [v] if isinstance(v, str) else list(v)
        return Principal(str(who), tuple(dict.fromkeys(map(str, groups)))), claims


class Verifier:
    """The mcp SDK's TokenVerifier over API keys and/or OIDC: a token either is a known key or a JWT
    the issuer signed; anything else is a 401 from the SDK's bearer middleware."""

    def __init__(self, keys: ApiKeys | None = None, oidc: OIDC | None = None):
        if not keys and not oidc:
            raise ValueError("HTTP serving needs --keys and/or --oidc-issuer (or --no-auth for development)")
        self.keys, self.oidc = keys, oidc

    async def verify_token(self, token: str):
        import anyio
        from mcp.server.auth.provider import AccessToken
        p, via, exp, iss = (self.keys.lookup(token) if self.keys else None), "api_key", None, None
        if p is None and self.oidc and token.count(".") == 2:
            got = await anyio.to_thread.run_sync(self.oidc.verify, token)
            if got:
                (p, claims), via = got, "oidc"
                exp, iss = claims.get("exp"), claims.get("iss")
        if p is None:
            return None
        return AccessToken(token="sha256:" + hash_key(token)[:16], client_id=p.id, subject=p.id, scopes=[],
                           expires_at=exp, claims={"groups": list(p.groups), "via": via, "iss": iss})


def local_principal(principal: str | None = None, groups: str | list[str] | None = None) -> Principal:
    """The stdio caller: --principal / ICTX_PRINCIPAL, else the OS user; groups from ICTX_GROUPS."""
    groups = groups if groups is not None else os.environ.get("ICTX_GROUPS", "")
    if isinstance(groups, str):
        groups = [g.strip() for g in groups.split(",") if g.strip()]
    return Principal(principal or os.environ.get("ICTX_PRINCIPAL") or getpass.getuser(), tuple(groups))


class Audit:
    """One JSON line per tool call, appended to `path` (or stderr)."""

    def __init__(self, path: str | Path | None = None):
        self.path, self._lock = Path(path) if path else None, threading.Lock()

    def write(self, **rec) -> None:
        line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **rec},
                          default=str, ensure_ascii=False)
        with self._lock:
            if self.path:
                with self.path.open("a") as f:
                    f.write(line + "\n")
            else:
                print(line, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ the server

def build_server(served: Served, *, verifier: Verifier | None = None, principal: Principal | None = None,
                 audit: Audit | None = None, max_rows: int = MAX_ROWS, max_top: int = 1000,
                 max_concurrent: int = 4, timeout: float = 120, host: str = "127.0.0.1", port: int = 8000,
                 public_url: str | None = None):
    """`verifier` (HTTP) authenticates each request; without it every call is `principal`."""
    import anyio
    from mcp.server.auth.middleware.auth_context import get_access_token
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError

    auth = None
    if verifier is not None:
        from mcp.server.auth.settings import AuthSettings
        url = public_url or f"http://{host}:{port}/mcp"
        oidc = verifier.oidc
        auth = AuthSettings(issuer_url=oidc.issuer if oidc else url, resource_server_url=url if oidc else None,
                            validate_token_resource=False)  # the verifier checks aud itself
    principal = principal or (None if verifier else local_principal())
    audit, slots = audit or Audit(), anyio.Semaphore(max_concurrent)
    mcp = FastMCP("powerbi-metrics", host=host, port=port, token_verifier=verifier, auth=auth, instructions=(
        "Business metrics captured from Power BI models and computed as SQL on the warehouse. "
        "Call list_models when you need to choose a model, list_metrics first, then compute. "
        "A metric marked [needs by=...] must be grouped by those columns (a period grain, or the column a "
        "rank ranks); one marked [what-if ...] takes params={key: value} in compute, else its default. "
        "Display-only measures are text for a visual: compute the metric they show. "
        "Numbers come only from compute; quote the capture version line each answer starts with "
        "when you report a number."))

    def caller(ctx: Context) -> Principal:
        tok = get_access_token()
        if tok is None:
            req = getattr(ctx.request_context, "request", None)
            user = req.scope.get("user") if req is not None else None
            tok = user.access_token if isinstance(user, AuthenticatedUser) else None
        if tok is not None:
            return Principal(tok.client_id, tuple((tok.claims or {}).get("groups") or ()))
        if principal is None:
            raise ToolError("unauthenticated")  # unreachable behind the bearer middleware; fail closed
        return principal

    async def run(tool: str, ctx: Context, model: str | None, args: dict, fn, warehouse: bool = True,
                  stamp: bool = True) -> str:
        """Resolve the caller, the model and its row filters, run `fn(kit)` off the event loop, audit it."""
        t0, kit = time.perf_counter(), None
        rec = {"principal": None, "groups": None, "model": model, "version": None, "tool": tool, "args": args}
        outcome, error = "ok", None
        try:
            who = caller(ctx)
            rec |= {"principal": who.id, "groups": list(who.groups)}
            await anyio.to_thread.run_sync(served.reload)
            rel, name = served.pick(model)
            rec |= {"model": name, "version": rel.version}
            layer, wh = rel.models[name]
            kit = BIToolkit(layer, wh, row_filters=filters_for(layer.spec, who), max_rows=max_rows, max_top=max_top)
            if warehouse:
                with anyio.fail_after(timeout):
                    await slots.acquire()
                try:
                    text = await anyio.to_thread.run_sync(fn, kit)
                finally:
                    slots.release()
            else:
                text = fn(kit)
            if text.startswith("Error:"):
                outcome, error = "error", text[6:].strip()[:500]
            if not stamp:
                return text
            return f"{served.stamp(name, rel)}\n" + (f"({served.error})\n" if served.error else "") + text
        except AccessDenied as exc:
            outcome, error = "denied", str(exc)
            raise ToolError(f"Access denied: {exc}") from None
        except TimeoutError:
            outcome, error = "busy", f"no query slot within {timeout:g}s"
            raise ToolError(f"The server is busy ({max_concurrent} queries running); try again shortly.") from None
        except (LayerError, ToolError) as exc:
            outcome, error = "error", str(exc)
            raise ToolError(str(exc)) from None
        except Exception as exc:
            outcome, error = "error", f"{type(exc).__name__}: {exc}"
            raise
        finally:
            sql = (kit.last.get("sql") if kit else None)
            audit.write(**rec, sql_sha256=hashlib.sha256(sql.encode()).hexdigest() if sql else None,
                        rows=kit.last.get("rows") if kit else None, ms=round((time.perf_counter() - t0) * 1000, 1),
                        outcome=outcome, error=error)

    @mcp.tool()
    async def list_models(ctx: Context) -> str:
        """The models served (name, captured / not captured measure counts, Power BI source) and the capture version."""
        t0 = time.perf_counter()
        who = caller(ctx)
        await anyio.to_thread.run_sync(served.reload)
        rel = served.release
        out = {"version": rel.version, "commit": rel.info.get("commit"), "models": [
            {"name": n, **model_counts(layer), "source": layer.spec.get("source"), "warehouse": wh.kind}
            for n, (layer, wh) in rel.models.items()]}
        if served.error:
            out["reload_error"] = served.error
        audit.write(principal=who.id, groups=list(who.groups), model=None, version=rel.version,
                    tool="list_models", args={}, sql_sha256=None, rows=None, ms=round((time.perf_counter() - t0) * 1000, 1),
                    outcome="ok", error=None)
        return json.dumps(out, indent=2)

    @mcp.tool()
    async def model_version(ctx: Context, model: str | None = None) -> str:
        """Which model and capture version is served, the commit and pipeline that verified it, and the warehouse."""
        def info(kit: BIToolkit) -> str:
            rel = served.release
            name = next(n for n, (layer, _) in rel.models.items() if layer is kit.layer)
            i = rel.info
            return json.dumps({"model": name, "version": rel.version, "commit": i.get("commit"),
                               "pipeline": i.get("pipeline_url"), "built_at": i.get("built_at"),
                               "warehouse": kit.wh.describe(), "counts": model_counts(kit.layer),
                               "models_served": list(rel.models), "reload_error": served.error}, indent=2)
        return await run("model_version", ctx, model, {}, info, warehouse=False, stamp=False)

    @mcp.tool()
    async def list_metrics(ctx: Context, model: str | None = None) -> str:
        """Captured metrics (keys, labels, definitions, what each needs: `by` columns and what-if parameters
        with their range and default), display-only measures with the metric they show, and the measures
        not captured, with why."""
        return await run("list_metrics", ctx, model, {}, lambda kit: kit.list_metrics(), warehouse=False)

    @mcp.tool()
    async def list_columns(ctx: Context, metrics: list[str] | None = None, model: str | None = None) -> str:
        """Columns the given metrics (default all) can be grouped or filtered by, written Table[Column]."""
        return await run("list_columns", ctx, model, {"metrics": metrics}, lambda kit: kit.list_columns(metrics),
                         warehouse=False)

    @mcp.tool()
    async def column_values(ctx: Context, column: str, filters: dict[str, str | int | float | list[str]] | None = None,
                            model: str | None = None) -> str:
        """Distinct values of a column (Table[Column], a date column may add :year/:quarter/:month/:week/:day),
        narrowed by `filters` on its table or tables it reaches. Use it to list things; compute for numbers."""
        return await run("column_values", ctx, model, {"column": column, "filters": filters},
                         lambda kit: kit.column_values(column, filters))

    @mcp.tool()
    async def compute(ctx: Context, metrics: list[str], by: list[str] | None = None,
                      filters: dict[str, str | int | float | list[str]] | None = None, top: int | None = None,
                      order_by: str | None = None, ascending: bool = False,
                      params: dict[str, float] | None = None, model: str | None = None) -> str:
        """Compute captured metrics grouped `by` columns (Table[Column]; dates may add :month etc.),
        filtered ({Table[Column]: value or [values]}; loose values like "online" are matched to the data),
        optionally the `top` N rows by `order_by` (default the first metric; ascending=true for bottom N).
        `params` sets what-if parameters ({key: value}, within the range list_metrics shows; the report's
        default when left out). Returns the table, the what-if values used, the definitions and the SQL."""
        args = {"metrics": metrics, "by": by, "filters": filters, "top": top, "order_by": order_by,
                "ascending": ascending, **({"params": params} if params else {})}
        return await run("compute", ctx, model, args,
                         lambda kit: kit.compute(metrics, by, filters, top, order_by, ascending, params))

    @mcp.tool()
    async def explore(ctx: Context, columns: list[str] | None = None, aggregates: list[str] | None = None,
                      filters: dict[str, str | int | float | list[str]] | None = None,
                      conditions: list[str] | None = None, order_by: list[str] | None = None,
                      descending: bool = False, limit: int | None = None, distinct: bool = False,
                      table: str | None = None, model: str | None = None) -> str:
        """Ad-hoc rows of the model's tables, for questions no captured metric answers: `columns`
        (Table[Column]), `conditions` ("T[C] > 100", "T[C] in ('a', 'b')", "T[C] between 1 and 9",
        "T[C] like '%x%'", "T[C] is null"), `filters` ({Table[Column]: value}, loosely matched), `aggregates`
        ("sum(T[C])", "count(*)", "count_distinct(T[C])", avg/min/max) grouped by the columns, `order_by`,
        `limit`, `distinct`, `table`. Row-level security applies; the agent never sends SQL."""
        args = {k: v for k, v in {"columns": columns, "aggregates": aggregates, "filters": filters,
                                  "conditions": conditions, "order_by": order_by, "descending": descending,
                                  "limit": limit, "distinct": distinct, "table": table}.items() if v}
        return await run("explore", ctx, model, args,
                         lambda kit: kit.explore(columns, aggregates, filters, conditions, order_by, descending,
                                                 limit, distinct, table))

    health: dict = {}

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request):
        from starlette.responses import JSONResponse
        rel = served.release
        whs = {id(wh): wh for _, wh in rel.models.values()}
        body = {"status": "ok", "version": rel.version, "models": list(rel.models),
                "warehouse": sorted({w.kind for w in whs.values()}), "reload_error": served.error}
        if request.query_params.get("ping", "1") != "0":
            if time.monotonic() - health.get("at", -1e9) > 30 or health.get("version") != rel.version:
                pings = await anyio.to_thread.run_sync(lambda: [w.ping() for w in whs.values()])
                health.update(at=time.monotonic(), version=rel.version, ok=all(ok for ok, _ in pings),
                              detail=[m for ok, m in pings if not ok])
            body["warehouse_ok"] = health["ok"]
            if not health["ok"]:
                body |= {"status": "degraded", "warehouse_error": health["detail"][0]}
        return JSONResponse(body, status_code=200 if body["status"] == "ok" else 503)

    return mcp
