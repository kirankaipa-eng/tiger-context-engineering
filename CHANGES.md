# Changes from the Studio frontend work, for the agent and API owner

Range, 29 to 30 Oct 2026:
- **Public GitHub `main`:** `79e387e` to `7cbc3fd`.
- **Internal `feat/langgraph-runtime`:** `fce4c6d` to `c767996`.

The same commits have different hashes in the two repos. This file cites internal hashes; these are the public ones:

| Internal | Public | Commit |
|---|---|---|
| `fce4c6d` | `79e387e` | first in the range |
| `120eb58` | `941c03c` | `/ask` streams only its own results |
| `f3273e6` | `b2436cf` | "by demography" on the Ask page |
| `c083d6d` | `463dfa7` | `list_columns` with no metrics |
| `e071dd7` | `57b01d8` | Plains Health demo |
| `5d59a7f` | `596a5b1` | scheduled briefs |
| `c767996` | `7cbc3fd` | last in the range |
| `cdb9ba7` | none | question chips on the classic desk (`pbi-frame.tsx`); not on public yet |

No existing endpoint changed its path, request body, response shape or `/ask` event format.

Where the changes are:
- **Backend:** `120eb58`, `c083d6d`, and the backend half of `5d59a7f`.
- **Demo script:** `e071dd7` adds `scripts/health_demo.py`.
- **Frontend:** every other commit touches only `studio/web`.

## Backend changes

### 1. `/ask` streams only its own conversation's results (`120eb58`)

- **Problem.** One `BIToolkit` per model serves every conversation. The `/ask` stream emitted every `kit.results` entry newer than the one it started from, so concurrent conversations received each other's `pbi_result` events.
- **Change.**
  - `bi_agent.py` adds `ASKER: ContextVar[str | None]`.
  - `BIToolkit._keep` tags each result with `"asker": ASKER.get()`.
  - `pbi_app.ask` sets `ASKER` to `"<mid>:<session_id or 'default'>"` and emits only results with that tag.
  - The `asker` key is not sent to clients.
- **Checked by hand** with two concurrent streams. There is no automated test.
- **Still open:**
  - Clients that send no `session_id` share the tag `<mid>:default`, so they still get each other's results.
  - `kit.results` keeps 50 entries across all conversations. Under load, a result can be evicted before its stream reads it.
  - `POST /team/{fid}/comment` runs `m.kit().explain(...)` and then reads `m.kit().results[-1]` (`pbi_app.py`). That result is untagged, and while an `/ask` is running it can be another conversation's.

### 2. `list_columns` with no metrics lists every column (`c083d6d`)

- **Problem.** `BIToolkit.list_columns` (`src/insurance_context/bi_agent.py`) called `layer.columns_for(all captured metrics)`. That returns the intersection, and a what-if metric reaches no table, so the result was empty. The agent then told users the model had no Gender or Age columns.
- **Change.** With no metrics, it returns the union of each metric's columns. With metrics, behaviour is unchanged.
- **Test:** `tests/test_list_columns.py`.

### 3. Scheduled briefs (`5d59a7f`)

The new module is `src/context_studio/pbi_schedule.py`. It is wired in by the last two lines of `pbi_app.py`: `pbi_schedule.install(app, models, _model, ask)`.

| Method | Path | Body or query | Returns |
|---|---|---|---|
| GET | `/api/pbi/{mid}/schedules` | `?owner=` | `{schedules: [Schedule]}` |
| POST | `/api/pbi/{mid}/schedules` | `{owner, ask, cron, tz?, title?, id?, paused?}` | `Schedule`; 400 on a bad cron or tz, an empty `ask`, an empty `owner` or `"me"`, or an `id` owned by someone else |
| POST | `/api/pbi/{mid}/schedules/preview` | `{cron, tz?}` | `{ok: true, next: [3 ISO times]}` or `{ok: false, error}` |
| DELETE | `/api/pbi/{mid}/schedules/{sid}` | `?owner=` | `{ok: true}`, even if nothing was deleted |
| POST | `/api/pbi/{mid}/schedules/{sid}/run` | `{owner}` | `Schedule` with `running: true`, run started in the background; 404 if the brief is missing or someone else's |

Field details:
- **Defaults.** `tz` defaults to `UTC`. An `id` updates that brief.
- **`Schedule`.** `{id, owner, title, ask, cron, tz, paused, created, last_run, editions, next_run, running}`.
- **Edition.** `{at, seconds, answer, results, error?}`. `results` holds the last six `pbi_result` payloads.
- **Retention.** Up to 12 editions are kept, newest first.
- **Times.** `at`, `created` and `last_run` are epoch seconds; `next_run` is ISO.

How a run works:
- **Storage.** `schedules.json` in the model folder, written under a lock.
- **The run itself.**
  - It calls `ask(mid, {message, who, session_id: f"brief-{sid}-{epoch}"})` and parses the NDJSON.
  - `/ask` at this commit ignores `who`.
  - Each edition is a new conversation.
  - If no LLM is configured, `/ask` raises 503 and the edition stores it as `error`.
- **The message.** It is `PROMPT` in the module: a user message, not a system prompt. It asks for the latest complete month in the data, not the run date. Edit `PROMPT` to change it.
- **Startup.**
  - A loop started with `@app.on_event("startup")` checks every 20 seconds.
  - Each due brief runs once.
  - After downtime, a brief catches up once, not once per missed time.

Limits:
- **Serial loop.** The loop runs due briefs one at a time, across all models, so a slow brief delays the rest. `POST …/run` runs alongside the loop.
- **Single process.** `_RUNNING` and `_LOCK` are per process. With several uvicorn workers, a brief runs once per worker, and writes to `schedules.json` can be lost.
- **Deprecated hook.** FastAPI deprecates `on_event` in favour of `lifespan`.
- **Cron syntax.** See the `pbi_schedule.py` docstring.
- **Tests:** `tests/test_schedule.py`.

## What the frontend relies on

The Ask page (`studio/web/src/app/pbi/ask/page.tsx`) calls:
- `/metrics`, `/metrics/{key}`, `/tables` and `/values`.
- `/compute` for each view. The exceptions are totals the agent already computed (below) and the agent's row listings, which are drawn from its own rows.
- `GET /board?owner=` and `POST /board {owner, tiles}`.
- `/ask`.

From `/ask` it reads:
- `chunk`, `result.answer` and `error.message`.
- `agent_tool_call`: `data.status` and `data.tool`. On `started`, the answer text resets and a step label shows for `compute`, `explore`, `explain` or `analyze`.
- `pbi_result`: `kind`, `columns`, `rows`, `metrics`, `by`, `filters`, `sql`, `ms`, `warehouse`, `labels`, `explain.steps[].by` and `explain.period`.
- `pbi_result.analysis`, when present, for the method, numbers and code. At `c767996`, `/ask` never sends it. Sending it needs:
  - the `analyze` tool, which is uncommitted;
  - `analyze` added to the tool names after which `pbi_app.ask` emits a `pbi_result`;
  - the `analysis` field forwarded in the payload.

Two more dependencies:
- **Agent rows.** The page reuses an agent `compute` result only when `by` is empty. It re-runs breakdowns through `/compute`, because the agent's `shown` rows can be only the top rows.
- **Schedules.** My briefs (`studio/web/src/app/pbi/board/page.tsx`) calls all five schedule endpoints, through `studio/web/src/lib/schedules.ts` and `studio/web/src/components/brief-schedules.tsx`.

## Agent behaviour seen in testing (not changed here)

1. **Memory is lost on restart.** Conversation memory lives in the orchestrator cached on `Loaded._orch` (`_orchestrator()` in `pbi_app.py`). After an API restart, a follow-up has no context: a "was that statistically significant?" follow-up was answered without the earlier turns. A persistent LangGraph checkpointer would fix this.
2. **"Why is our medical loss ratio up this year?"** (on Plains Health, below)
   - The agent named a small member subgroup: female, risk band "Very high", age 18–34.
   - It did not mention the planted drivers: GLP-1 pharmacy spend, and inpatient unit cost at one facility.
   - Its driver search went down member columns and did not split by `Medical Claims[Setting]` or `Pharmacy Claims[Drug Type]`.
3. **"Is the jump in cost per admit at Lakeshore Regional statistically significant?"**
   - The agent compared September 2025 with September 2026, with n=12 admits each.
   - The planted change starts in January 2026, so the test that answers the question compares before that date against after it.
4. **"Demography".** Before `c083d6d` the agent said Gender and Age did not exist. Since `f3273e6`, the Ask page maps "demography" to gender and age columns (`PEOPLE` in `page.tsx`). The agent has no such mapping.

## Plains Health: a model to test the agent against

`scripts/health_demo.py` writes a made-up health plan:
- **Model:** TMDL with 44 measures (PMPM by setting, admits and ER visits per 1,000, cost per admit, MLR, GLP-1 PMPM and members, modifier 25/59 rates, year over year).
- **Data:** synthetic, Jan 2024 to Sep 2026.
- **Capture:** with `build_spec`, 44 of 44.
- **Input:** `--source` (default `../tigerclaw/data/demo`) must hold `ibnr_health.db` and `mee_synthetic.db`.
- **Output:** `--out`, default `build/pbi/plains-health`.

```
uv run python scripts/health_demo.py
uv run ctx-studio serve --pbi build/pbi/plains-health --port 8701
uv run ictx pbi query build/pbi/plains-health -m medical_loss_ratio,total_pmpm_yoy_pct --by "Calendar[Date]:year"
```

To serve the sales demo beside it, add `--pbi build/pbi/sales-demo`. You build that with `uv run python scripts/sales_demo.py`, from `build/pbi/sales-returns`.

| Planted | Query | Result |
|---|---|---|
| Cost trend | `total_pmpm_yoy_pct` by `Calendar[Date]:year` | +7.2% in 2025, +16.6% in 2026 (Jan to Sep against Jan to Sep) |
| MLR rising | `medical_loss_ratio` by `Calendar[Date]:year` | 84.0%, 85.4%, 88.8% |
| GLP-1 uptake in Commercial and Individual, ages 30–64, women more often | `rx_pmpm_yoy_pct` by year; `glp_1_members` by `Calendar[Date]:quarter` | Rx PMPM +16.1% in 2025, +30.4% in 2026; GLP-1 members 271 in 2024-Q1, 896 in 2026-Q3 |
| Facility contract from Jan 2026, +24% on its inpatient paid | `cost_per_admit` by quarter, where `Medical Claims[Facility]` = "Lakeshore Regional Medical Center" | 16.4k to 20.6k a quarter in 2026; 5.4k to 55k in 2025. With 2 to 26 admits a quarter, it is too noisy to show the step |
| Behavioral health, ages 18–34 | `professional_pmpm` by year and `Member[Age Band]`, where `Medical Claims[Clinical Category]` = "Behavioral Health" | 18–34: $15.7, $20.0, $25.9; 35–49: $18.2, $19.5, $19.7 |
| Medicare Advantage growth from Jan 2026 | `members` by quarter, where `Member[Line of Business]` = "Medicare Advantage" | 3,308 in 2025-Q4, 3,930 in 2026-Q1 |
| Billing drift from Oct 2025 | `modifier_25_rate`, `modifier_59_rate` by `Provider[Provider]` | Seeded, not queried here; details below |

How the billing drift was seeded:
- **Providers.** Six, listed in tigerclaw's `mee_synthetic.db` table `anomalies`, with magnitudes 2.68 to 4.45.
- **Each provider drifts on one modifier,** set by the anomaly's `kind`.
- **A `modifier_25_spike` provider.** On E/M claims the modifier 25 rate is `min(0.07 × magnitude, 0.6)`; on their other claims it is 0.25.
- **A `modifier_59_spike` provider.** The modifier 59 rate is `min(0.05 × magnitude, 0.6)` on all of their claims.

## Verify

```
uv run pytest -q tests/test_schedule.py tests/test_list_columns.py
uv run pytest -q
cd studio/web && npx tsc --noEmit && npx eslint src
```

At `5d59a7f`, the full suite gave 301 passed and 20 skipped.
