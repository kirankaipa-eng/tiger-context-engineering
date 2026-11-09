# The analyst

The analyst answers a business question from governed Power BI measures. This spec replaces `desk_harness.py`
(2,461 lines: seven readers, ten intent regexes, three "why" engines) with one loop, built the way Claude Code is:
a model that reads, calls a few plain tools, sees what they return, and answers. Code never guesses what a question
means. It looks things up, runs what the model asks for, and says exactly what it did.

## Principles

1. **One loop.** The model decides everything a question means. There is no pre-reader, no ledger and no word pattern.
   The loop is the runtime's own (`agent_runtime.Orchestrator`: model, then tools, then model, until it answers). Its
   memory of the conversation is the message history, so "sure", "the second one" and "what about Texas" are read the
   way any follow-up is.
2. **Few tools that each do one thing.** There are five. Each returns plain text the model reads and a status it can
   trust (`ok`, `empty`, `refused`), never a message to be pattern-matched.
3. **Nothing hidden.** Code adds only the owner's scope, and every result says the scope it ran under. Code never
   carries a period over, never drops a measure and never retries quietly.
4. **The facts the model needs are in its prompt.** The prompt holds the measures, the columns and their values, the
   owner's scope, the latest month with data, and how a payer reads cost. A lookup the model makes is a fallback.
5. **The page draws what the answer states.** The step whose rows the answer is about is marked `show`, and it is the
   result the page draws.
6. **The same events.** `chunk`, `agent_tool_call`, `pbi_result` and `result` are unchanged, so neither the page nor
   the API changes.

## The loop

```
question ─▶ model ──tool calls──▶ tools ──results──▶ model ──▶ … ──▶ answer
              ▲                                                      │
              └──────── conversation history (per chat) ◀────────────┘
```

A typical question takes two model calls: one plans and calls `compute`, the other writes the answer from the rows.
A "why" question takes three: `compute` for the levels, `explain` for the drivers, then the answer.

**Budget.** A question gets at most 8 tool calls and 120 s. Past either limit, a tool returns "Answer now from what
you have." and the model answers.

**Steady reading.** The model is called at temperature 0. `ANALYST_TEMPERATURE=none` leaves the setting out for a model
that refuses it.

## Tools

| Tool | Takes | Does | Returns |
|---|---|---|---|
| `compute` | `steps`: each with `metrics`, and as needed `by`, `filters`, `top`, `order_by`, `ascending`, `params`, `show` | Runs every step together, each under the owner's scope | Per step: `R<n> ok\|empty\|refused`, the scope applied, the rows (at most 30), or why it was refused |
| `explain` | `metric`, `period` (date column with grain), `now`, `before`, `filters` | One layer of what drove the change: each column's split, the value that carries most of it | The layer, with the lead to dig into |
| `describe` | `metric` | What a measure is made of, what it needs, the columns it can be cut by, its service-category parts | Text |
| `values` | `column`, `filters` | The values a column holds | Text |
| `find_metrics` | `query` | Measures matching business words, for a catalog too big to read whole | Keys with labels |

"Why is IL the highest?" needs no separate tool: it is `compute` by state, plus `compute` of the categories for IL
and for everyone. "Why did it go up?" is `compute` of the two months, plus `explain`.

## The prompt

In this order, so the unchanging part is cached:

1. **Role and method** (fixed). Answer from numbers you computed. Plan every number the answer needs and send them as
   steps of one `compute`. Mark `show` on the step whose rows the answer states, and keep that step to
   measures in one unit. Answer in one to three sentences with the period named. End with at most one offer, of one
   next step, so a reply like "sure" always means it.
2. **How a payer reads cost** (`insurance.PRIMER` and `insurance.SAY`).
3. **The catalog** (fixed per model): every measure as `role:key`, its label and what it needs; each PMPM's breakdown
   into categories, utilization and unit cost; the columns to cut by with their values; the date column.
4. **This turn's facts** (per turn): the owner's scope ("applied to every number; never add these"), the latest month
   with data, and today's date.

## What the harness keeps

Nothing per conversation beyond the runtime's message history. Per model, it keeps the latest month of each date
column and the owner's scope with "latest" resolved, both read once.

## Switching

`DESK_HARNESS=on` with `ANALYST=on` runs the analyst (`src/insurance_context/analyst.py`); `DESK_HARNESS=on` alone runs
the old harness. The switch keeps its old name because the AVD's launch command sets it. Once the analyst answers the
demo questions as well, the old harness is deleted.

## Done when

- The six demo questions give the same read and the same chart in two runs. They are: where Total PMPM is highest and
  how it moved; why IL is the highest; why total PMPM in IL went up; "sure" after an offer; and how the business is doing.
- Each answer's chart is a result its text states.
- The trace of each question is its tool calls, in order, with what each returned.
- `analyst.py` is under 500 lines.
