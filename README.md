# tiger-context-engineering

Tiger's **enterprise context layer**: one governed model of the business that
every system, report and AI agent reads from, kept current by an agentic curator
that owners approve. It is built for companies that run many units on many
systems, and it keeps up as the business changes. **Insurance is where we land
first** (P&C, health, life and annuity), not where we stop.

```
             ┌──────────────────────────────────────────────────────────────┐
  L1 core    │ Party · Product · Coverage · Claim · Decision · OperatingUnit│  shared by every line
             ├──────────────────────────────────────────────────────────────┤
  L2 line    │ commercial_pc  submissions, policies, claims, IBNR           │  industry packs
             │ health_payer   commercial + MA: claims, Rx, PA, risk, Stars  │
             │ life_annuity   life + annuity: UW, crediting, A/E, reserves   │
             │ next: personal lines, group benefits, reinsurance            │
             ├──────────────────────────────────────────────────────────────┤
  L3 client  │ the carrier's vocabulary, extensions, restrictions, policy   │  a reviewable YAML diff
             ├──────────────────────────────────────────────────────────────┤
  L4 unit    │ per operating unit / source system: mappings + extensions    │  keeps its own systems
             └──────────────────────────────────────────────────────────────┘
                     │ compile
     ┌───────────────┼──────────────┬──────────────┬──────────────┬───────────────┐
  governed views   metrics      OWL / SHACL    JSON Schema /   R2RML         agent tools
  4 SQL platforms  (as-of,      (Protege, KG)  Pydantic        (virtual KG)  (LangGraph)  
                   triangles)
```

## Start here

- **[docs/CURATOR_SPEC.md](docs/CURATOR_SPEC.md)**: what we are building, why, and how
  the agentic curator works. Also a [living Claude doc](https://claude.ai/code/artifact/83fa8fad-c725-4034-af3d-a36533f946ed) for comments.
- **[docs/studio/](docs/studio/README.md)**: Context Studio wireframes (the review app
  over git), with a 30-minute walkthrough.
- **[examples/curator/](examples/curator/)**: five hello-world scripts. Each is the
  starting point for a real component.
- **[studio/](studio/README.md)**: run Context Studio (API + web app) locally.
- **[docs/powerbi/](docs/powerbi/README.md)**: Power BI models as governed metrics for agents:
  the field guide (try it on a laptop, onboard a client model, CI, the MCP server).

### How we develop: ten rules

1. **Product, not demo.** If it only works on the laptop that built it, it isn't done. Every feature ships with a test and runs on a reference client.
2. **Start from the hello world.** Each component grows out of its example script. The script stays in the repo and keeps passing.
3. **The answer key is the spec.** Every needle we plant has an answer key. A change that finds fewer needles, or stops holding back on a decoy, is a regression.
4. **Kernel, pack or client, in that order of caution.** A concept goes in the kernel only if every line needs it, in a line pack if every client in that line needs it, and otherwise in the client overlay. When in doubt, go lower.
5. **Never move an IRI.** Overlays add and restrict; they never rename or redefine. Client dashboards and agents depend on base concepts staying put.
6. **Governed beats clever.** Agents query governed views and metrics, never raw tables. If an answer needs a new metric, add the metric, not a longer prompt.
7. **Show the work.** Every answer carries its trace: which concept, which metric, which mapping. If a layer helped, the user can see it helped.
8. **Evidence or it didn't happen.** Every proposal cites the page it came from and is checked by building the ontology with the change applied. The curator drafts; owners decide.
9. **Small and reversible.** One proposal, one change. Anything merged can be reverted without side effects.
10. **Safe by default.** Synthetic data and fictional names only. Secrets live in `.env`, never in git. Business users never see git, and the curator never merges.

## What it does

| Capability | What you get |
|---|---|
| **Layered ontology** | Core kernel → line pack → client overlay → unit overlay. Base concepts keep canonical IRIs through every layer, so a client can call a claim a "Claim File" while it stays `ins:Claim` in every export. |
| **Many units, one answer** | Each unit maps its own source system. The engine unions them into one group layer with unit-qualified identities and row-level lineage (`source_system`). |
| **Any warehouse** | Mappings are authored once and compiled per platform: DuckDB, Snowflake, Databricks, Postgres. A portability lint means no view ships silently broken. |
| **Governed metrics** | Written/earned premium, incurred, IBNR, loss ratios, severity, litigation rate, hit ratio, retention; PMPM and MLR; placement, persistency, lapse, net flows and mortality A/E. Joins are derived from the ontology, never hand-written. Every result is pinned to a valuation date and carries its definition, and clients can override definitions explicitly. |
| **Data conformance** | Required fields, enum domains, patterns, bounds, referential integrity and id uniqueness, all generated from the ontology and run inside the warehouse, per unit. |
| **Evolution loop** | Drift in source data becomes **change proposals**. Each has evidence, a concrete overlay change and a scope (pack/client/unit), and is dry-run verified before a human sees it. An optional LLM pass adds industry context and escalates industry-wide trends to the pack. |
| **Agent harness** | LangGraph agent tools generated from the context layer. Agents get governed KPIs, concept lookup in the client's vocabulary, triangles, and read-only SQL confined to a PII/PHI-redacted schema (enforced by parser and connection, not prompt). |
| **Standard exports** | OWL 2, SHACL, JSON Schema, Pydantic, W3C R2RML, LLM-ready data dictionary, metric catalog, and a manifest with content hashes. |
| **Power BI metrics for agents** | A client's Power BI models (.pbix or TMDL) in Git become versioned metric definitions: CI captures every DAX measure that produces a number as SQL for the warehouse, verifies it and releases it; one MCP server serves the newest release to any agent, with the model's row-level security. See [docs/powerbi/](docs/powerbi/README.md). |

## Setup

You need:

| | for | check |
|---|---|---|
| Python 3.11+ and [uv](https://docs.astral.sh/uv/) | everything | `uv --version` |
| An OpenAI-compatible LLM endpoint (`API_KEY`, `API_BASE_URL`, `CHAT_MODEL` in `.env`) | Q&A (`ask`) and the Studio chat; the agents run on LangGraph | `uv run python -c "from insurance_context import llm; print(llm.complete('say pong'))"` |
| Node.js 20.9+ | the Context Studio web app only | `node --version` |
| Docker | building the Power BI images and the local GitLab demo only | `docker version` |

```bash
git clone https://github.com/kirankaipa-eng/tiger-context-engineering.git
cd tiger-context-engineering
uv sync --all-extras --group dev          # or only what you use, e.g. --extra agent --extra powerbi
cp .env.example .env                      # then fill in the LLM key; everything else is optional
uv run python scripts/pbi_sample.py       # Microsoft's Power BI sample, used by the Power BI tests
uv run pytest -q                          # about a minute; all pass
```

`.env` holds secrets and is git-ignored. [`.env.example`](.env.example) lists every variable:
the LLM endpoint (any OpenAI-compatible one: OpenAI, OpenRouter, a company gateway), and the
optional Databricks, GitLab and MCP server settings. Without an LLM key, everything runs except
chat, `ask` and the LLM pass of the curator and drift. Without the Power BI sample, the tests that
need it skip and say how to get it.

## Quickstart

```bash
uv run ictx clients
uv run ictx demo summit_group              # synthetic warehouse -> views -> exports -> validate -> drift
uv run ictx query summit_group -m earned_premium,ultimate_loss_ratio,litigation_rate --by accident_year \
    --where line_group=casualty
uv run ictx triangle summit_group --where line_group=casualty
uv run ictx drift summit_group --llm       # LLM-enriched proposals (needs API_KEY/API_BASE_URL/CHAT_MODEL in .env)
uv run ictx ask summit_group --trace "How is our casualty book developing, and is social inflation showing up?"
uv run ictx sql summit_group --dialect snowflake > views.sql
```

### Power BI: from a .pbix to an agent's answer

```bash
uv run python scripts/pbi_sample.py                       # build/pbi-sample/models/sales-returns/
uv run ictx pbi import build/pbi-sample/models/sales-returns/sales-returns.pbix --out build/pbi/sales-returns
uv run ictx pbi metrics build/pbi/sales-returns            # 48 captured, 9 display-only, 1 not captured
uv run ictx pbi coverage build/pbi/sales-returns           # what was not captured, grouped by root cause
uv run ictx pbi query build/pbi/sales-returns -m net_sales,store_top_n --by "Store[Store]" --top 3
uv run ictx pbi query build/pbi/sales-returns -m wif_forecast --param pct_return_rate=25
uv run ictx pbi ask build/pbi/sales-returns "Which product category has the highest return rate?"
uv run ictx pbi export build/pbi-sample/models/sales-returns/sales-returns.pbix --out build/metric-views/sales-returns  # Databricks metric views
```

`import` writes a capture folder: `capture.yaml` (the metrics) and `pilot.duckdb` (a local copy of
the .pbix's rows, so questions run on a laptop). `metrics`, `query` and `ask` take that folder.
`export` writes the Databricks metric views and, in its `capture/` subfolder, the same capture; those
commands accept the export folder too. With your own model, swap the paths:

```bash
uv run ictx pbi import path/to/model.pbix --out build/pbi/model
uv run ictx pbi ask build/pbi/model "Total net paid by month for the last 12 months?"
```

A TMDL folder or a DirectQuery .pbix carries no rows: answers then need Databricks
(`DATABRICKS_*` in `.env`, `--target databricks`).

The field guide continues from here: serving the metrics over MCP to Claude Code or Copilot, CI on
a models repo, Databricks, security: [docs/powerbi/README.md](docs/powerbi/README.md).

## Reference clients (synthetic, fictional)

**Summit Specialty Group** (`clients/summit_group`, commercial P&C) is a decentralized specialty
carrier with three units on deliberately different systems:

| Unit | System | What the mapping has to do |
|---|---|---|
| `CORP` | Group finance & actuarial warehouse | Global reference data (units, legal entities, broker master, cat events), treaties, IBNR selections |
| `SES` | Transactional policy / claims platform | Straight mappings with code decodes and list columns |
| `HPG` | Program business, **bordereaux only** | Derives policies, coverage parts and transactions from premium bordereaux; turns cumulative monthly claim snapshots into financial transactions with window functions |

The data has current industry dynamics built in: casualty severity trend and a rising
litigated share (social inflation), nuclear verdicts in umbrella/excess, and fields the
ontology does not model yet (third-party litigation funding flags, TRIA premium, PFAS
claims). These are what the drift loop is expected to find.

**Meridian Health Plan** (`clients/meridian_health`, health payer) is a regional payer with
commercial and Medicare Advantage business on two platforms:

| Unit | System | What the mapping has to do |
|---|---|---|
| `COMM` | Legacy commercial claims & enrollment platform (+ enterprise MDM hub) | Decode mainframe codes, 9999-12-31 open spans and YYYYMM months; derive service category from bill type / POS / ER revenue lines; map the shared provider, pharmacy and drug masters once (NPI / NDC global identity) |
| `MA` | Medicare Advantage platform | CMS-style codes (sex 1/2, dual and LIS codes, MMR, risk score basis), PBPs that double as plans, member-perspective appeal outcomes, capitated encounters |

24 months of calibrated data (commercial medical PMPM ~$490, MA ~$1,100, MLR ~85%,
admits/1,000 ~55 vs ~285) with planted signals for the drift loop and curator: a new
MA denial group (DRG clinical validation), the CMS-HCC V28-only risk score basis for
payment year 2026, and a cluster of standard prior auths decided past the 7-day
CMS-0057-F time frame. A practice corpus with an answer key lives in
`clients/meridian_health/corpus/`.

**Northwind Life & Annuity** (`clients/northwind_life`, life & annuity) is an individual
life and deferred annuity carrier on two platforms:

| Unit | System | What the mapping has to do |
|---|---|---|
| `LIFE` | Legacy individual-life policy administration system (+ enterprise reference hub) | Decode CHAR(8) YYYYMMDD dates with 00000000 for none, cents and basis points, one- and two-letter codes; one client file for every role (insured, owner, beneficiary); map the shared product catalog, producer master and mortality tables once (global identity) |
| `ANN` | Deferred annuity platform (MYGA, fixed indexed, RILA) | Its own product and crediting codes, quarter-end account values, best-interest reviews, 1035 exchanges, annuitization; annuity riders carry a benefit base (unit extension) |

Policies and contracts issued 2019-2026 (~22k life, ~12k annuities), calibrated to
industry-typical levels: life placement ~78%, accelerated underwriting rising from ~20%
to ~55% of decisions, 13-month persistency ~0.90, life lapse ~6% a year, FIA caps
peaking ~10% in 2023 and falling to ~8.25%, MYGA shock surrenders at the end of the
guarantee, and mortality A/E ~0.8 that rises with duration (select period). Planted
signals: a performance-trigger crediting method on the FIAs, a return-of-premium term
rider, and VM-22 principle-based reserves for 2026 fixed annuity issues. A practice
corpus with an answer key lives in `clients/northwind_life/corpus/`.

**Acme Health Plan** (`clients/acme_health`, health payer) is a single-unit plan. It
exercises restrictions (dental carved out), hidden fields (birth date never reaches
agents), and drift (No Surprises Act flag, new claim statuses).

## Onboarding a client

```
clients/<client>/
  client.yaml                 line + pinned version, prefix/namespace, units, overlay ops, agent policy, metric overrides
  units/<unit>/mappings.yaml  source table/query -> ontology entity, slot by slot
  units/<unit>/overlay.yaml   optional unit-only extensions
```

1. `client.yaml`: pick the line pack, declare the units, add vocabulary (`labels`) and
   carrier-specific rules (`extend`, `restrict`, `require`, `hide`, `global_identity`).
2. Per unit, write `mappings.yaml`. `column`, `expr` (DuckDB SQL over `src`) or `value`
   per slot, a `table` or a `query` per entity, plus optional per-dialect overrides.
3. `ictx check` resolves everything, lints metric definitions and SQL portability.
4. `ictx install` / `ictx sql --dialect …` deploys the views; `ictx validate` shows
   conformance per unit; `ictx drift` shows what the mapping still leaves behind.

Overlay operations never redefine base elements. They label, extend, restrict, require or
hide, so every client stays on the upgrade path of the line pack.

## The evolution loop

```
 source drift ─┐                                                    ┌─ pack    (industry-wide)
 regulation   ─┼─► detect ─► draft ─► enrich ─► verify ─► review ───┼─ client  (this carrier's practice)
 standards    ─┤   (signal)  (change)  (LLM,     (resolve   (steward)└─ unit    (one system's quirk)
 agent misses ─┘                       optional)  with it)
```

The ontology **does not rewrite itself**. In regulated insurance an unreviewed schema
change can alter adjudication or statutory reporting. It detects, drafts, verifies and
asks, and accepted changes ship as a new pack/overlay version. Business-rule breaches
(e.g. records violating a client restriction) are routed to the data owner, not proposed
as ontology changes.

Signal sources today: data drift (unmapped columns/tables, out-of-domain values, coverage
gaps). The proposal format is shared, so regulatory and standards feeds plug into the same review flow.

## Layout

```
ontology/core/core.yaml            L1 kernel
ontology/lines/commercial_pc.yaml  L2 commercial & specialty P&C
ontology/lines/health_payer.yaml   L2 healthcare insurance (payer): commercial + Medicare Advantage
ontology/lines/life_annuity.yaml   L2 individual life & annuity: underwriting, best interest, crediting, reserves
ontology/metrics/<line>.yaml       governed metrics, conformed dimensions, derived entities
                                   (commercial_pc: premium, loss, IBNR; health_payer: PMPM, MLR,
                                   per-1,000 utilization, PA timeliness, risk scores, Stars, pharmacy;
                                   life_annuity: placement, weighted new premium, persistency, lapse,
                                   annuity flows, caps, mortality A/E, reserves, best-interest reviews)
clients/<client>/…                 L3/L4 overlays and mappings (summit_group, meridian_health,
                                   northwind_life, acme_health)
clients/<client>/corpus/           curator practice corpora and answer keys (meridian_health, northwind_life)
src/insurance_context/
  overlay.py      layer resolution with IRI pinning
  client.py       client / unit model
  sqlgen.py       per-dialect view compiler, identity qualification, redaction, portability lint
  semantic.py     metric compiler: ontology-path joins, as-of, ratios, triangles
  validate.py     warehouse-side conformance checks
  drift.py        drift detection, proposals, LLM enrichment, verification
  exports.py      OWL, SHACL, JSON Schema, Pydantic, R2RML, context doc, manifest
  agent_tools.py  agent toolkit with structural guardrails; agent_runtime.py: the LangGraph runtime
  cli.py          `ictx` (and `ictx pbi ...`)
  pbimodel.py     Power BI: read .pbix / TMDL, write TMDL, RLS roles
  daxcapture.py   Power BI: DAX measures -> metric definitions (or the reason not)
  bilayer.py      Power BI: metric definitions -> warehouse SQL (sqlglot), import
  bisecurity.py   Power BI: row-level security per caller
  biwarehouse.py  Power BI: Databricks connection, or the local stand-in
  bici.py         Power BI: CI capture, diff, verify, release, drift
  bi_agent.py     Power BI: the agent toolkit; bi_mcp.py: the metrics MCP server
  bimetricview.py Power BI: captured metrics as Databricks metric views (+ a context file)
src/context_studio/               Context Studio API: proposals, owners, curator, chat, insights desk
studio/web/                       Context Studio web app (Next.js)
tests/            reconciliation, portability, guardrail, evolution and Power BI tests
docs/CURATOR_SPEC.md              the build spec for the agentic curator
docs/powerbi/                     Power BI field guide
examples/curator/                 curator hello world: 5 scripts, practice corpus, answer key
examples/pbi-models/              template for a client's Power BI models repo (CI files)
ci/                               Dockerfiles: Power BI capture image, metrics MCP server image
scripts/                          pbi_sample.py (the Power BI sample), demo_pbi_gitlab.py (local GitLab demo)
```

## Standards and licensing

FHIR R4 (open) mappings are recorded as `exact/close_mappings`. ACORD (P&C forms and
models) and X12 (health transactions) are **licensed**: the packs reference form and
transaction identifiers as annotations only and do not reproduce their content. CPT
descriptors are AMA-licensed and are not shipped. Clients load their own licensed code
sets.

## Roadmap

- **Payer depth:** Medicaid managed care, commercial ACA risk adjustment, incurred-basis
  MLR with IBNR (MA Stars, HCC risk adjustment and CMS-0057-F timeliness are in the pack).
- **More signal sources:** regulatory feeds (Federal Register / CMS, state DOI bulletins,
  NAIC), standards releases (FHIR IGs, code-set updates), agent questions the layer could not answer.
- **Mapping studio:** profile a new unit's warehouse and propose mappings with confidence,
  for human approval (the drift profiler is the foundation).
- **Native governance:** Snowflake masking policies / Databricks column masks in place of
  redacting views, and semantic-layer exports (Snowflake semantic views, Databricks metric
  views, dbt MetricFlow, OSI).
- **Entity resolution:** shared insureds and brokers across units beyond a group master.
- **Steward UI:** browse the ontology, review proposals, approve mappings.
