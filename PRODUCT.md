# Product

<!-- impeccable:product-schema 1 -->

Scope: the **Power BI analysis desk**, Context Studio served over a captured Power BI model
(`ctx-studio serve --pbi DIR`, routes under `studio/web/src/app/pbi`). The Studio review app over
the content repo and the curator are out of scope here and keep their own context.

## Platform

web

## Users

- **Analysts first.** Analysts at the client (for example, a payer's cost and utilization team)
  who build and maintain the Power BI reports today. They field "why did this move?" questions,
  and they have to defend every number they hand over. They use the desk to get an answer, drill
  across several dimensions, look at the rows behind a figure, and check how a measure is built.
- **Leaders second.** Business leaders read what the analysts find and ask follow-up questions in
  plain language. They want a clear account of what happened, not a tool to operate.

## Product Purpose

The desk turns a client's existing Power BI semantic model into something people can question
and explore. It reads the .pbix/TMDL, captures each measure deterministically as SQL, and answers
three kinds of request over the client's own warehouse: metric questions, multi-dimensional
drilldowns, and ad-hoc row exploration. Success means an analyst gets to a defensible answer, and
the path that produced it, faster than they could in Power BI or Tableau. It also means a leader
trusts that answer without asking for a rebuild.

## Positioning

What Tableau or Power BI cannot truthfully copy:

- **Every number has receipts.** Each value traces to the captured measure, its DAX, the SQL it
  compiled to, the filters in effect, and the rows it read. Capture is deterministic: no LLM sits
  between the model and the number.
- **It explains, not just shows.** Click a number and the desk finds what drives it across
  dimensions on its own. *Planned, not yet built*: auto-ranked drivers and mix-vs-rate
  decomposition.
- **Ask in plain language.** An agent answers questions over the model's captured measures and
  tables. Every answer is backed by real queries it shows.
- **No migration.** The client's existing model is read as-is, and its measures become governed
  metrics and agent tools (an MCP server as well).

## Operating Context

- **Client environment.** Windows machines, no git. Files come from the GitHub repo by ZIP or by
  copying. Setup is `uv sync`, `ictx pbi import <.pbix>`, `ctx-studio serve --pbi <dir>`, then
  `npm run dev` in `studio/web`.
- **Typical model.** The real client model is large (744 measures). Not every measure is captured,
  and the Coverage screen shows each measure's status with its reason.
- **Workflow.** An analyst asks a question or starts from a measure, drills by a dimension, trends
  by month, splits again, opens the rows behind a figure, and reads how a measure is built. They
  then share the result with a leader.

## Capabilities and Constraints

**Current screens**
- Analysis desk: chat-driven exhibits, starters, and "start from a measure".
- Coverage, Drilldown (multiple levels with a split), Metrics and their detail, Explore rows, and
  Tables.

**Charts**
- The chart type follows the shape of the answer: KPI, line, bar, horizontal bar, stacked bar,
  multi-line or heatmap. A second axis is added when magnitudes differ by more than 20×, and a
  measure switcher appears for two dimensions with several metrics.
- The drill menu offers: break down by, trend by month, show rows behind it, and ask about it.

**What runs without an LLM.** Drilldowns, explore, coverage and metric detail need no LLM. Asking
questions needs LLM settings in `.env`.

**Data**
- Results are capped at 500 rows per call.
- Row-level security from the model applies to explore.
- The data can contain PHI. Client data, DAX and rows must never leave the client environment or
  appear in demos, screenshots or repo content.

**Terminology**
- *captured*: the measure compiles to SQL; *display*: a formatting-only measure; *not captured*:
  the measure comes with a reason.
- *measure / metric*, *dimension*, *exhibit*, *drill*, *rows behind it*, *how it's built*.

**Known limits**
- "Rows behind it" does not apply the measure's own filter.
- One toolkit per model is shared by everyone asking at once.

## Evidence on Hand

- A sample model for demos and verification: `build/pbi/sales-returns`, with 49 measures captured,
  9 display and 1 not captured.
- The test corpus claims model: `tests/test_dax_corpus.py`.
- Client feedback, recorded as said: they liked the graph drilldowns and are specifically
  interested in multi-dimensional drilldown.
- There are no testimonials, case studies, benchmarks or client logos. Do not fabricate them, and
  do not show the real client's model, measure names or numbers.

## Product Principles

1. **Trust before polish.** Any number on screen can be traced back to its measure, SQL and rows
   in one step.
2. **Explain the movement.** The desk exists to answer "why", so drivers, decomposition and drill
   paths are first-class, not buried in menus.
3. **Deterministic core, LLM at the edges.** Numbers come from captured SQL. The LLM only routes
   questions and narrates, and the desk still works without it.
4. **Meet the model where it is.** No rebuild and no migration. Coverage gaps are shown honestly,
   with their reasons.
5. **Analyst depth, leader clarity.** The same result serves an analyst's drill and a leader's
   read.
