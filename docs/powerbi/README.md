# Power BI metrics for agents: field guide

A client's Power BI semantic models live in a GitLab repo. CI captures their DAX measures as metric
definitions that compile to SQL, checks them, and publishes each version as a release. An MCP server
serves the newest release to agents (Claude Code, Copilot, anything else that speaks MCP) and runs
the SQL on the client's Databricks. The data stays in Databricks. Agents never send SQL: they send
metric keys, columns and filters, and every answer says which capture version it came from.

```
models repo (GitLab)            CI (pbi-capture image)                     MCP server (pbi-metrics image)
models/<name>/<name>.pbix  -->  capture: .pbix -> TMDL -> capture.yaml     serves newest capture-v<N>
  or <name>.SemanticModel/      diff vs published, verify on warehouses     (or a pinned tag)
  warehouse.yaml                MR: report kept as one MR note        -->   compute() -> SQL -> Databricks
  access.yaml                   main: release capture-v<N>                  row-level security per caller
                                nightly: drift check -> issue               audit log, limits, /healthz
```

Code: `src/insurance_context/` — `pbimodel.py` (read .pbix/TMDL), `daxcapture.py` (DAX -> metric),
`bilayer.py` (metric -> SQL), `bisecurity.py` (RLS), `biwarehouse.py` (Databricks / DuckDB),
`bici.py` (CI, publish, drift), `bi_mcp.py` (the server). Templates: `examples/pbi-models/`.
Images: `ci/pbi-capture.Dockerfile`, `ci/metrics-server.Dockerfile`.

## Try it on your laptop

From the repo root, after the setup in the [main README](../../README.md#setup)
(`uv sync --all-extras --group dev`, `.env` from `.env.example`). Nothing here needs Databricks or
GitLab: metrics run on a local DuckDB copy of the .pbix's own rows (the "stand-in").

```bash
uv run python scripts/pbi_sample.py      # Microsoft's Sales & Returns sample -> build/pbi-sample/models/sales-returns/

# 1. .pbix -> TMDL -> capture.yaml, plus the stand-in
uv run ictx pbi import build/pbi-sample/models/sales-returns/sales-returns.pbix --out build/pbi/sales-returns

# 2. what was captured, and how each metric must be asked
uv run ictx pbi metrics build/pbi/sales-returns -v

# 3. compute
uv run ictx pbi query build/pbi/sales-returns -m net_sales,store_top_n,store_sales_top_3 --by "Store[Store]" --top 5
uv run ictx pbi query build/pbi/sales-returns -m net_sales,net_sales_pm --by "Calendar[Date]:month"
uv run ictx pbi query build/pbi/sales-returns -m net_sales,wif_forecast --param pct_return_rate=25
uv run ictx pbi query build/pbi/sales-returns -m net_sales --by "Product[Category]" --dialect databricks  # + Databricks SQL

# 4. ask in plain English (needs the LLM settings in .env)
uv run ictx pbi ask build/pbi/sales-returns "Which product category has the highest return rate?"

# 5. what CI does on a models repo: capture every model folder, diff, verify, write the release folder
uv run ictx pbi ci build/pbi-sample/models --out build/pbi-sample/public
cat build/pbi-sample/public/report.md
```

Step 5 reads the sample's TMDL folder, which `pbi_sample.py` wrote with one measure added (External
Net Sales), so it reports 59 measures where the .pbix in step 1 has 58.

**Serve it to Claude Code** over stdio. The server runs as you, with no network authentication:

```bash
claude mcp add powerbi-metrics -- uv run --directory "$PWD" ictx pbi serve --dir build/pbi-sample/public
```

Start Claude Code, check `/mcp` lists `powerbi-metrics`, and ask, for example, "Top 3 stores by net
sales, and the profit forecast at a 25% return rate."

**Serve it over HTTP** with an API key, as a deployed server runs:

```bash
uv run ictx pbi serve keygen --principal you@example.com --groups finance
# prints the key once, and a line `<sha256>: {"principal": ..., "groups": [...]}`: put that line in keys.yaml
uv run ictx pbi serve --dir build/pbi-sample/public --http 8080 --keys keys.yaml --audit audit.jsonl
curl -s "http://127.0.0.1:8080/healthz?ping=0"          # no auth; a request to /mcp without the key gets 401
claude mcp add --transport http powerbi-metrics http://127.0.0.1:8080/mcp --header "Authorization: Bearer <the key>"
```

Each tool call adds one line to `audit.jsonl` (who, which tool, a hash of the SQL, rows, outcome).

## Databricks metric views

`ictx pbi export` writes the captured metrics as
[Databricks metric views](https://learn.microsoft.com/en-us/azure/databricks/uc-semantics/metric-views/yaml-reference)
(YAML specification 1.1), from a `.pbix`, a TMDL folder or a capture folder:

```bash
uv run ictx pbi export build/pbi-sample/models/sales-returns/sales-returns.pbix --out build/metric-views/sales-returns
uv run ictx pbi export path/to/Model.SemanticModel --schema sales --out build/metric-views/model   # TMDL
```

One metric view per fact table the measures aggregate, with the model's relationships as `joins`,
every reachable column as a `field` (plus year, quarter and month fields for dates), and three files:

| file | what |
|---|---|
| `<view>.yaml` | the metric view; only the specification's keys |
| `<view>.sql` | `CREATE OR REPLACE VIEW <catalog>.<schema>.<view>_metrics WITH METRICS LANGUAGE YAML AS $$ ... $$` |
| `<view>.context.yaml` | our context the specification has no place for, keyed by measure name: Power BI name and DAX, how exact the translation is, how it must be asked; the metrics the specification cannot express (ranks, ISINSCOPE switches, ALL / ALLSELECTED, latest-period variables) with their captured definitions; display-only measures and the number each shows; what-if ranges; RLS roles; which field is which Power BI column |
| `export.md` | per view: counts, and every metric left out with why |
| `capture/` | the capture (`capture.yaml`, and from a .pbix the local copy of its rows): `ictx pbi metrics / query / ask` take the export folder |

| captured as | in the metric view | fidelity |
|---|---|---|
| aggregation + filters | `SUM(x) FILTER (WHERE ...)` | exact |
| formula over metrics | `COALESCE(MEASURE(a), 0) / NULLIF(COALESCE(MEASURE(b), 0), 0)` | exact |
| year/quarter/month to date | `window`: cumulative on the date, current on the period (the specification's documented pattern) | not yet run on Databricks |
| previous month/quarter/year, DATEADD, SAMEPERIODLASTYEAR | `window`: current on the period with `offset` (`-1 month`, `-12 month`, ...) | not yet run on Databricks |
| what-if parameter | `parameters:` (the slider's range is in the context file) | exact |
| rank, ISINSCOPE, ALL / ALLSELECTED, latest-period VAR, measures over two fact tables | left out; in the context file and the report | answered by our engine |

On the Sales & Returns sample: the Sales view holds 23 measures (20 Power BI measures, 3 parts of
them), 25 are left out with the reason. Exported measures without a window compute what our compiler
computes (`tests/test_metricview.py` emulates the view on the stand-in). Creating the views on
Databricks needs Databricks Runtime 17.3+ (18.1+ for windows with offsets).

## What is proven, and what is not

| | status |
|---|---|
| .pbix and TMDL read; every number-producing measure captured | Sales & Returns 49 of 59, Plains MEE 25 of 36; the rest display-only, except one text sort key |
| Captured numbers | match expectations worked out independently from DAX's rules (`tests/test_capture_parity.py`); **not yet compared with Power BI's own engine** (planned: a Windows runner that opens the .pbix in Power BI Desktop and compares its DAX answers with our SQL) |
| CI, releases, MR note, drift issue, MCP server | run end to end on a local GitLab (scripts/demo_pbi_gitlab.py) |
| Databricks | connection, OAuth and SQL dialect built and unit-tested; **not yet run against a real workspace** |
| Azure DevOps template | **untested** |

## The local GitLab demo

The whole loop on a GitLab on your machine: a models repo, a merge request with the pipeline's
note, releases, the server picking each one up. It needs a GitLab at `http://localhost:8929` with a
Docker runner whose job containers join the `gitlab-ci` network (they reach the API at
`http://gitlab:8929`) and use `pull_policy: if-not-present` for local images, and a token with `api`
scope (an admin's: the script creates the project `root/pbi-models`).

```bash
GITLAB_TOKEN=<token> uv run python scripts/demo_pbi_gitlab.py all     # --pbix other.pbix to use your own
uv run ictx pbi serve --gitlab http://localhost:8929 --project root/pbi-models   # with GITLAB_TOKEN set
```

`all` runs the steps in order; each also runs alone: `image` (build `pbi-capture:dev` and
`pbi-metrics:dev`; needs no token), `setup` (create the repo, push the .pbix: capture-v1), `change`
(an owner's merge request: TMDL, one measure added, two tables confirmed; prints the pipeline's
note), `merge` (capture-v2), `ci` (push the latest CI template: a new release; play the nightly
drift schedule). The local clone of the models repo is kept in `build/pbi-models`.

## What is captured, and what is not

Every measure of a model ends up in one of three groups, and the CI report counts them for a
business reader, e.g. "59 measures: 49 captured, 9 display-only (7 show a captured number),
1 not captured":

- **captured**: a metric agents can compute (the number is worked out in SQL on the warehouse);
- **display-only**: text for a visual, such as a `FORMAT` label ("$1.2M"), a ▲/▼ indicator, a
  tooltip or an image URL. It is not served as such; when it shows a number, the report and
  `list_metrics` name the captured metric behind it ("Net Sales Label: shows net_sales as text"),
  and agents compute that. When the number is an expression (the ▲/▼ of a variance), it is captured
  as its own metric `<key>_value`;
- **not captured**: with the reason. Only an agreed short list stays here (in the Sales & Returns
  sample, `Association`: a sort key glued together from text, which measures nothing).

| DAX | captured as |
|---|---|
| `SUM / AVERAGE / MIN / MAX / COUNT / COUNTA / DISTINCTCOUNT(T[c])`, `COUNTROWS(T)` | aggregation |
| `SUMX / AVERAGEX / MINX / MAXX / COUNTX(T or FILTER(T, ...), expr)`, `RELATED(...)` | aggregation over a row expression |
| `CALCULATE(m, T[c] = "x", ...)` | the filter applies to every part of `m` |
| `CALCULATE(m, ALL / ALLSELECTED / REMOVEFILTERS / ALLEXCEPT(...))` | `context`: the question's grouping (and, for ALL, its filters) on that table or column is ignored for `m` (a share of total) |
| `VAR _y = MAX(T[Year]) RETURN CALCULATE(m, T[Year] = _y)` | `latest`: only the latest (or latest - n) value of the column in the question's rows |
| `DIVIDE(a, b)`, `[a] - [b]`, `IF`, `SWITCH`, `ISBLANK` | formula over other metrics (conditionals become `CASE WHEN`) |
| `PREVIOUSMONTH/QUARTER/YEAR/DAY`, `NEXT...`, `PARALLELPERIOD` | a period shift; asked at that grain |
| `DATEADD`, `SAMEPERIODLASTYEAR` | a period shift at any grain |
| `DATESYTD/QTD/MTD`, `TOTALYTD/QTD/MTD` | a running total from the start of the year/quarter/month |
| `RANKX(ALL(T[c]), [m], , DESC/ASC, Skip/Dense)` | `rank`: each value of `T[c]` ranked by `m`; asked by `T[c]` |
| `SWITCH(TRUE(), ISINSCOPE(c1), e1, ISINSCOPE(c2), e2, ..., else)` | `by_scope`: the branch for the column the question groups by |
| `GENERATESERIES` table + `SELECTEDVALUE(P[P])` | a what-if parameter (`parameters:` in capture.yaml: label, min, max, step, default) |
| `FORMAT(...)`, `&`, `UNICHAR`, text constants | display-only, with `value:` naming the number shown |

Calculated columns and calculated tables (other than what-if parameters) exist only inside Power BI
and have no warehouse source. Power Query reshaping (`Table.AddColumn`, merges, unpivots, ...) is
flagged per table (`reshaped_in_power_query`): the warehouse table must already hold the reshaped
rows, or a `sql:` override in warehouse.yaml must produce them.

### How a metric must be asked

`list_metrics` (and `ictx pbi metrics <dir> -v`) puts a hint after a metric that cannot be asked
for any which way. The server says how to ask when a question misses one.

| hint | meaning | ask like |
|---|---|---|
| `[needs by=Calendar[Date]:month]` | a period metric: the previous month needs months | `by=["Calendar[Date]:month"]` |
| `[needs by=Calendar[Date]:month or finer]` | a running total or shift: that grain or a finer one | `by=["Calendar[Date]:month"]` or `:day` |
| `[needs by=Store[Store]]` | a rank (or a metric built on one, "Top 3 Sales") ranks stores | `by=["Store[Store]"]` |
| `depends on the grouping: by Product[Product]: ...; otherwise ...` | a scope switch (ISINSCOPE) | group by the column whose answer you want |
| `[what-if % Return Rate (pct_return_rate): 0 to 60 step 1, default 5]` | reads a what-if slider | `params={"pct_return_rate": 10}` |

- **Ranks and top N.** "Top 3 stores by net sales" is `compute(["net_sales"], by=["Store[Store]"],
  top=3)`; add the rank metric (`store_top_n`) to show the rank itself. "Where does Contoso rank" is
  the rank metric by `Store[Store]` filtered to Contoso. Rank 1 is the highest unless the definition
  says lowest. A rank counts only the rows the question's filters leave (as a visual with slicers).
- **Scope switches.** The same measure answers with a different metric per grouping, as in the
  report's matrix (by product: product rank; by segment: segment rank). Group by the column whose
  answer you want; without any of them the `otherwise` branch answers, or the server asks for one.
- **What-if values.** `compute(..., params={key: value})`, also by label (`"% Return Rate"`); MCP
  tool argument `params`, CLI `ictx pbi query <dir> -m m --param pct_return_rate=10`. A value outside
  the slider's range is refused with the range. Left out, the parameter's default applies (the
  `SELECTEDVALUE` alternate result); a parameter with no default must be given, and the error says
  its range. Every answer says which value it used.

Deliberate differences from DAX: a CALCULATE filter is ANDed with the question's filters (DAX
would replace a filter on the same column); text comparison is exact (DAX is case-insensitive); a
rank over `ALL(T[c])` ranks among the rows the question's filters leave. Grouping and top-N ("by
month", "top 3 stores") are answered by the question, not stored.

Row-level security roles are read from the model (`definition/roles/*.tmdl`), including dynamic RLS
(`USERPRINCIPALNAME()`, `USERNAME()`, `USEROBJECTID()`). A role whose filter cannot be translated is
`not_captured`, and its members are refused, not shown unfiltered rows. Object-level (column / table)
security is not read.

## The models repo

```
.gitlab-ci.yml                     from examples/pbi-models/.gitlab-ci.yml
README.md
models/
  sales-returns/
    sales-returns.pbix             first import, as Power BI Desktop saves it
    sales-returns.SemanticModel/   the same model saved as PBIP (TMDL); once present it is the source
    warehouse.yaml                 where each table and column lives in Databricks
    access.yaml                    who is in each RLS role (required when the model has roles)
```

One folder per model; the folder name is the model's name in the server. Add `*.pbix binary` to
`.gitattributes` (or Git LFS). Prefer TMDL once onboarded: reviewers can read a measure's diff.

## Onboarding a client model

1. **Repo and runner.** Create the models repo from `examples/pbi-models/` (`.gitlab-ci.yml`,
   README). Build the capture image (`uv run python scripts/demo_pbi_gitlab.py image` builds
   `pbi-capture:dev` and `pbi-metrics:dev`), push it to a registry the runners pull from, and set
   `CAPTURE_IMAGE` to that tag. Settings: "Pipelines must succeed" on merge requests; protect `main`.
2. **Token.** A project access token, role Developer or above, `api` scope, as masked CI variable
   `GITLAB_TOKEN` (MR note, releases, drift issue). Without it MR pipelines still run but post no note.
3. **First import.** Commit `models/<name>/<name>.pbix` to main. The pipeline converts it, captures
   it and publishes `capture-v1`. Read the job's `report.md`: captured / display-only / not captured,
   and which tables still have an **assumed** warehouse name (`<catalog>.<schema>.<snake_table>`).
4. **Map the warehouse.** In a merge request add `warehouse.yaml` confirming each table (`tables:`),
   renaming columns that differ (`columns:`), and `sql:` for tables Power Query reshapes. Set the
   Databricks CI variables so the MR pipeline runs every metric's total on Databricks; compare the
   totals with the Power BI report before merging.
5. **Security.** If the model has RLS roles, add `access.yaml`. A model with roles and no access.yaml
   is published, but the server denies every caller; the report says so in bold.
6. **Switch to TMDL** (optional, recommended): save as PBIP in Power BI Desktop and commit the
   `.SemanticModel` folder; from then on measure changes are reviewed as text.
7. **Serve.** Run the metrics server in the client environment (below), register it in the agents'
   MCP clients, and create the nightly drift schedule (CI/CD > Schedules, target `main`).

Changing a metric afterwards: edit its measure (TMDL, or re-save the .pbix), open a merge request,
read the pipeline's note (what changed, verified totals), merge. The server picks up the new release
within its refresh interval (default 30 s).

## warehouse.yaml

```yaml
catalog: pilot              # catalog of assumed table names (default pilot)
schema: sales_returns       # schema of assumed table names (default: the folder name, snake_case)
tables:                     # Power BI table -> catalog.schema.table, confirmed by the owner
  Sales: pilot.sales_returns.fact_sales
  Store: pilot.sales_returns.store
columns:                    # Power BI table -> {model column: warehouse column}, where they differ
  Sales:
    Amount: net_amount
    Date: order_date
sql:                        # the table's rows come from this query instead (Databricks SQL)
  Product: >
    SELECT p.ProductID, p.Category, c.Price
    FROM pilot.sales_returns.product p JOIN pilot.sales_returns.price c USING (ProductID)
```

Every name is checked: an unknown key, a table not in the model, a column not in that table, a table
that is calculated in Power BI, a `tables:` value that is not `catalog.schema.table`, or `sql:` that
does not parse as Databricks SQL fails that model's capture with a message naming the file and the
name. Mapped column names apply to the stand-in too. A `sql:` query must return the warehouse column
names (after `columns:` renames); it is also run on the local stand-in, so keep it portable SQL.
Tables read from a Databricks Power Query source (`Databricks.Catalogs`, `Value.NativeQuery`) are
mapped automatically; warehouse.yaml overrides them.

## access.yaml

```yaml
roles:
  Store Managers: [ana@client.com, group:Retail Ops]
  Finance: [group:Finance]
```

Keys are the model's role names; members are principal ids (email / UPN, compared case-insensitively)
or `group:<name>` matched against the caller's groups (the OIDC `groups` claim, or an API key's
groups). A role the model does not define fails the capture. Members recorded inside the .pbix
(Power BI service accounts) are kept as `members_in_model` for reference but grant nothing: who may
call the MCP server is decided here, in review. A caller in several roles sees the union of their
rows, per table, as in Power BI; a caller in no role, or in a not-captured role, is refused.

## CI

Jobs (`examples/pbi-models/.gitlab-ci.yml`):

| job | when | what |
|---|---|---|
| `capture-mr` | merge requests | `ictx pbi ci models --out public`; report kept as **one** MR note, edited on every pipeline; artifacts 30 days |
| `capture` | main | the same, versioned `v<N>` (one past the newest release), then `ictx pbi publish public` in the same job: release `capture-v<N>` linking this job's artifacts, which never expire |
| `drift` | schedules, or a run with `PBI_DRIFT=true` | `ictx pbi drift --release latest --issue` |

On main, capturing and releasing are one job in `resource_group: capture-release`, so the jobs of
two merges run one after the other and each numbers its version after the previous release exists.
`publish` still refuses a tag that exists (only possible if someone releases by hand), and it refuses a preview capture and one that did not verify.

The report per model: source (converted .pbix or TMDL), counts, changes since the published version
(metrics added / removed / redefined, table and column mapping changes, relationships), **which
warehouses were checked** (local stand-in, Databricks, or why not), each captured metric's value per
warehouse, the what-if parameters, the display-only measures with the metric each shows, the
measures not captured with why, RLS status, and still-assumed tables. A metric that fails to compile
or run on any checked warehouse fails the pipeline. Totals come from one query per model per
warehouse; a metric runs on its own only to locate a failure, or when it must be asked by a column
(a period metric at its grain: the latest period's value is shown; a rank by the column it ranks:
the first row's value is shown) and the row is named ("latest Calendar[Date]:month = 2019-06", "at
Store[Store] = Abbas"). A what-if parameter is set to its default, or the midpoint of its range
(on a slider step) when it has none, and the value is shown ("% Return Rate = 30 (midpoint, no
default)"). The diff marks a measure that moved between groups ("not captured -> display-only,
shows `net_sales`") and what-if parameters added, removed or changed. No `EXPLAIN`.

### CI/CD variables

| variable | needed for | notes |
|---|---|---|
| `GITLAB_TOKEN` | MR note, publish, drift issue | project access token, `api`, Developer+; masked |
| `CAPTURE_IMAGE` | every job | default `pbi-capture:dev` (a local image; with `pull_policy: if-not-present` runners) |
| `GITLAB_API` | when jobs reach GitLab at another address | default `CI_API_V4_URL`; e.g. `http://gitlab:8929/api/v4` when the external URL is localhost |
| `DATABRICKS_SERVER_HOSTNAME` | verify / drift on Databricks | `adb-....azuredatabricks.net` |
| `DATABRICKS_HTTP_PATH` | same | `/sql/1.0/warehouses/<id>` |
| `DATABRICKS_CLIENT_ID` + `DATABRICKS_CLIENT_SECRET` | same (preferred) | OAuth M2M as a service principal; masked |
| `DATABRICKS_TOKEN` | same (fallback) | a PAT, used only without client id/secret; masked |
| `PBI_DRIFT` | manual drift run | "Run pipeline" with `PBI_DRIFT=true` runs only the drift job |

Without any Databricks variable, CI checks the local stand-in only and the report says
"Databricks: not configured". With the hostname set but the HTTP path or credentials missing, the
report says **misconfigured** and the pipeline fails: it never claims a Databricks check it did not
make. Give the CI principal `SELECT` (and `USE CATALOG` / `USE SCHEMA`) on the mapped tables only.

### Azure DevOps

`examples/pbi-models/azure-pipelines.yml` is an **untested** template: capture on PRs and main
(diffing against the last main build's artifact), nightly drift. There is no release or PR comment
there: off GitLab, `ictx pbi ci` and `ictx pbi drift` work on folders and make no GitLab call, and
`ictx pbi publish` refuses to run. The main build's `capture` artifact is the release; serve it with
`ictx pbi serve --dir`.

## Running the MCP server in a client environment

Image: `ci/metrics-server.Dockerfile` (`pbi-metrics:dev`): non-root user `ictx`, extras
`powerbi,databricks,mcp`, entrypoint `ictx pbi serve --http 8080 --host 0.0.0.0`, healthcheck on
`/healthz?ping=0`. Arguments after the image name are appended:

```sh
docker run -d -p 8080:8080 \
  -e GITLAB_TOKEN \                                   # read_api on the models repo
  -e DATABRICKS_SERVER_HOSTNAME -e DATABRICKS_HTTP_PATH \
  -e DATABRICKS_CLIENT_ID -e DATABRICKS_CLIENT_SECRET \
  -e ICTX_OIDC_ISSUER=https://login.microsoftonline.com/<tenant>/v2.0 \
  -e ICTX_OIDC_AUDIENCE=api://pbi-metrics \
  -v pbi-audit:/home/ictx/audit \
  pbi-metrics:dev --gitlab https://gitlab.client.com --project analytics/pbi-models \
    --audit /home/ictx/audit/audit.jsonl --public-url https://pbi-metrics.client.com/mcp
```

- **Source**: `--gitlab URL --project P` serves the newest `capture-v*` release, checked every
  `--refresh` seconds (default 30); a failed reload keeps the previous release serving and
  `model_version` says why. `--dir D` serves a folder `ictx pbi ci` wrote. `--model a,b` limits models.
  Releases are cached under `~/.cache/ictx-captures`.
- **Warehouse**: Databricks when `DATABRICKS_SERVER_HOSTNAME` is set (OAuth M2M with client
  id/secret, else `DATABRICKS_TOKEN`), else the stand-in shipped in the release (the .pbix's rows:
  development only). `--target databricks` refuses to fall back. Use a SELECT-only principal.
- **Auth** (HTTP): every request needs `Authorization: Bearer <token>`, else 401.
  OIDC: `--oidc-issuer` / `ICTX_OIDC_ISSUER`, `--oidc-audience` / `ICTX_OIDC_AUDIENCE` (required with
  an issuer), `--oidc-jwks`, `--principal-claim` (default `upn,preferred_username,email`),
  `--groups-claim` (default `groups`). API keys: `ictx pbi serve keygen --principal bot@client.com
  --groups finance` prints a key once and the line for the keys file (`--keys FILE` /
  `ICTX_PBI_KEYS`, only hashes stored, re-read on change). Both can be on. Without either, every
  request is refused; `--no-auth` is for development only.
- **Limits**: `--timeout` (s per statement, default 120), `--max-rows` (200), `--max-top` (1000),
  `--max-concurrent` (4 warehouse queries).
- **Audit**: one JSON line per tool call (principal, groups, model, version, tool, args, SQL hash,
  rows, ms, outcome) to `--audit PATH` / `ICTX_AUDIT_LOG`, else stderr. Tokens are never logged.
- **Health**: `GET /healthz` (no auth) returns version, models, warehouse kind and pings the
  warehouse (cached 30 s); `?ping=0` skips the ping, for liveness probes that should not wake a
  stopped SQL warehouse.

Tools: `list_models`, `model_version`, `list_metrics`, `list_columns`, `column_values`,
`compute(metrics, by, filters, top, order_by, ascending, params, model)`, and
`explore(columns, aggregates, filters, conditions, order_by, descending, limit, distinct, table, model)`
for ad-hoc rows no captured metric answers ("claims over $10,000 in March", "count of claims by place
of service"): conditions like `Claims[Paid] > 10000`, `T[C] in ('a', 'b')`, `T[C] between 1 and 9`,
`T[C] like '%x%'`, `T[C] is null`; aggregates like `sum(T[C])`, `count(*)`, `count_distinct(T[C])`
grouped by the columns. The rows are read from the table whose rows reach every table named, joined as
Power BI filters, under row-level security; values are literals, never SQL. Each takes `model` when more
than one is served. `list_models` / `model_version` counts carry `captured`, `presentation`
(display-only), `presentation_with_value`, `not_captured` and a `summary` line.

## In the Studio

`uv run ctx-studio serve --pbi build/pbi/<model>` and `npm run dev` in `studio/web` give the analysis
desk over the captured model: questions, exhibits with drilldowns, coverage, measures, rows and tables.
See [studio/README.md](../../studio/README.md#over-a-power-bi-model-any-client-no-content-repo).

## Registering the server in MCP clients

HTTP endpoint: `https://<host>/mcp` (streamable HTTP).

**Claude Code**

```sh
# a deployed server
claude mcp add --transport http powerbi-metrics https://pbi-metrics.client.com/mcp \
  --header "Authorization: Bearer $PBI_METRICS_KEY"
# or local over stdio, as the signed-in OS user (no network auth; RLS uses --principal/--groups)
claude mcp add powerbi-metrics -- uv run --directory /path/to/tiger-context-engineering \
  ictx pbi serve --gitlab https://gitlab.client.com --project analytics/pbi-models \
  --principal ana@client.com --groups "Retail Ops"
```

For stdio, launch through a small script that reads `GITLAB_TOKEN` from a secret store at start, so
the token is not written into `~/.claude.json`.

**VS Code / GitHub Copilot** (`.vscode/mcp.json`):

```json
{
  "inputs": [{"id": "pbi-key", "type": "promptString", "description": "Power BI metrics API key", "password": true}],
  "servers": {
    "powerbi-metrics": {"type": "http", "url": "https://pbi-metrics.client.com/mcp",
                        "headers": {"Authorization": "Bearer ${input:pbi-key}"}}
  }
}
```

**Other clients** (Cursor, Claude Desktop, agent SDKs): any streamable-HTTP MCP client with a bearer
header, or the stdio command above. With OIDC, the client obtains an access token for the configured
audience; the server publishes protected-resource metadata when `--public-url` is set.

## Rollback and pinning

Every merge to main is a release `capture-v<N>`, whose artifacts never expire. To roll back, run
the server with `--pin capture-v<N-1>` (restart with the flag); it serves that release until unpinned.
Then fix forward with a merge request (revert the measure change); removing the pin returns to the
newest release. Do not delete releases: the server and audit lines refer to them by version.
`model_version` and every answer's first line name the version, commit and pipeline.

## Drift

`ictx pbi drift [public | <capture> | models] [--release latest|capture-vN] [--target ...]
[--out drift.md] [--issue]` checks, per model, that every mapped table exists in the warehouse
(Databricks `DESCRIBE TABLE`, `DESCRIBE QUERY` for `sql:` tables; DuckDB `DESCRIBE` locally), has every
mapped column, and that each column's type is of the kind the model reads (number, text, date/time,
boolean, binary: `int64` vs `DECIMAL` is fine, `int64` vs `STRING` is drift). No rows are read. Exit 1
and a markdown report on drift, or when a model has no warehouse to check on; exit 2 when there is no
capture to check.

In CI the scheduled `drift` job checks the latest release and, with `GITLAB_TOKEN`, keeps one open
issue labelled `warehouse-drift`: opened on the first failure, its description replaced and a note
added on each later one, closed by the next clean run. Without Databricks variables it checks the
release's stand-in, which cannot drift: configure Databricks for this job to mean anything.
Fix by correcting the warehouse, or by mapping the model to it in warehouse.yaml and merging.

## Troubleshooting

| symptom | cause / fix |
|---|---|
| `GitLab API ... is unreachable from here` in a job | jobs reach GitLab at another address: set `GITLAB_API` |
| capture-mr passes but no MR note | `GITLAB_TOKEN` not set (or not available to MR pipelines: unprotect the variable) |
| `capture-vN already exists` in publish | a release was made outside the CI job (by hand); re-run the pipeline |
| `<model>: Not captured: warehouse.yaml: ...` | a name in warehouse.yaml does not exist in the model; the message names it |
| `Databricks ...: **misconfigured**` | hostname set without HTTP path or credentials |
| a metric fails `databricks: ... TABLE_OR_VIEW_NOT_FOUND` | the table is still assumed or misnamed; confirm it in `tables:` |
| "No access.yaml: every caller will be denied" | the model has RLS roles; add access.yaml |
| agent gets `AccessDenied` | caller in no role, or in a not-captured role; check the principal / groups claim and access.yaml |
| server keeps an old version | `--pin` set, or the reload failed: `model_version` shows the error |
| image pull fails on the runner | `CAPTURE_IMAGE` not in a registry the runner can pull; local images need `pull_policy: if-not-present` |
| server crashes with `mcp.server.fastmcp` missing | mcp 2.x installed; the extra pins `mcp>=1.30,<2` |

## Security model and its limits

- The MCP server holds the only warehouse credential; agents send metric keys, columns and filters,
  never SQL. Column and filter names are validated against the capture, values are quoted literals.
- RLS is enforced by the server, in the SQL it writes, from the roles captured from the model and the
  members in access.yaml. Databricks itself does not see the end user: the service principal can read
  every row of the mapped tables. Keep that principal SELECT-only on exactly those tables, and add
  Unity Catalog row filters if the warehouse must enforce RLS independently.
- Fail closed: a model with roles denies callers in no role, callers in a not-captured role, and
  everyone when access.yaml is missing. There is no bypass flag.
- Identity comes from the bearer token (OIDC signature, exp, aud, iss verified; or a hashed API key).
  Over stdio and with `--no-auth`, identity is whatever `--principal` says: trust is the local machine.
- Object-level security, Power BI service workspace permissions, and the model's own role members are
  not applied. Calculated columns and display-only text are not available to agents.
- Who can change a metric or access.yaml is whoever can merge to main: protect main and require review.
- The local stand-in holds the .pbix's imported rows and ships inside the release artifacts; anyone
  with `read_api` on the repo can download it. If the .pbix holds sensitive data, restrict the repo
  or commit TMDL without the .pbix after the first import.
