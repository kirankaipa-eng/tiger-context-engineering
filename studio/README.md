# Context Studio

The review app over git. People keep the ontology, metrics and source mappings correct
here; the curator agent reads documents and data and drafts changes; owners approve.
Every screen reads the content repo, and every save becomes a proposal on a branch,
so the Studio keeps no data of its own. Business users never see git.

Wireframes and walkthrough: [docs/studio/](../docs/studio/README.md). Spec:
[docs/CURATOR_SPEC.md](../docs/CURATOR_SPEC.md).

## Run it

```bash
# from the repo root
uv sync --extra studio --group dev
uv run ctx-studio init          # content repo + reference warehouses (first run: ~1 min)
uv run ctx-studio serve         # API on http://127.0.0.1:8700

cd studio/web
npm install
npm run dev                     # web app on http://localhost:3700
```

Chat and the LLM curator use `API_KEY`, `API_BASE_URL` and `CHAT_MODEL` from `.env` at the
repo root (`cp .env.example .env`): any OpenAI-compatible endpoint (OpenAI, OpenRouter, a company gateway)
such as OpenRouter. The server reads them at start, so restart `ctx-studio serve` after a
change. Without them the curator falls back to rules (it still finds the PFAS needle), and
chat is unavailable.

Pick who you are with **Signed in as** (people come from `owners.yaml`; SSO replaces this
in production) and the client in the header. Start in the Inbox and press
**Run the curator on documents**.

## Over a Power BI model (any client, no content repo)

```bash
uv run ictx pbi import <model>.pbix --out build/pbi/<model>     # or a folder `ictx pbi export` wrote
uv run ctx-studio serve --pbi build/pbi/<model>                 # API on :8700; repeat --pbi for several
cd studio/web && npm run dev                                     # web app on http://localhost:3700
```

This serves only the captured model: no demo clients, no reference data, no proposals. The web app
opens on the **analysis desk**: ask in plain words on the left (the same agent as `ictx pbi ask`;
numbers only from the model's captured measures, or rows of its tables when no measure answers), and
each answer lands on the right as an exhibit with its headline, the chart that fits it (a line for a
period, ranked bars for many categories, stacked bars, lines or a heatmap for two dimensions) and how
it was built (the definition and the SQL). Click any bar, point or cell to drill in: break that value
down by another column, see its monthly trend, or open the rows behind it; each drill is a new exhibit
computed in SQL with no LLM in between. **Coverage** shows why measures were not captured, by root cause;
**Measures**, **Drilldown**, **Explore rows** and **Tables** are the model page by page. Without an LLM
configured, everything but asking still works. Numbers run on Databricks when `DATABRICKS_*` is set in
`.env`, else on the local copy of the .pbix's rows.

## How it works

| Piece | Where |
| --- | --- |
| Content repo (seeded from `ontology/` and `clients/`) | `.studio/remote.git`, served from `.studio/main` |
| Proposals: branch, checks, routing, approve, merge, undo | `src/context_studio/proposals.py` |
| Who owns what | `owners.yaml` in the content repo (seed: `src/context_studio/seed/owners.yaml`) |
| Edits (enum values, labels, definitions, metrics, mappings) | `src/context_studio/edits.py` |
| Checks on a branch (subprocess, branch content) | `src/context_studio/engine_job.py` |
| Curator: read, link, prove, route, act | `src/context_studio/curator.py` |
| Chat and the docked Curator (LangGraph) | `src/context_studio/chat.py` |
| Reviews: local records today, GitLab merge requests next | `src/context_studio/review.py` |
| API | `src/context_studio/app.py` |
| Web app | `studio/web/` (Next.js) |

Rules the code enforces: the curator never approves or merges; only an owner of a waiting
area can approve; a proposal merges when every area it touches has approved and checks pass;
undo is a new proposal; nothing writes to `main` except a merge.

### GitLab

Set `CTX_STUDIO_GIT=gitlab`, `GIT_URL`, `GIT_PROJECT` and `GIT_TOKEN` (a bot token that can
open merge requests and comment, not merge) in `.env`, and install the extra:
`uv sync --extra studio --extra gitlab`. Reviews then live on merge requests.

### Start over

`uv run ctx-studio reset` deletes `.studio/` (all proposals and history) after asking.
