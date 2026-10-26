"""The three pictures on the walkthrough page, as inline SVG: where the data comes from, the
system, and how a change travels. Plain SVG so the page stays one file that works offline."""

from html import escape

INK, MUTED = "#141414", "#475467"
FILL = {"src": "#F2F4F7", "demo": "#FFF4EB", "gov": "#E6F6FB", "out": "#ECFDF3", "ext": "#F9F5FF"}
EDGE = {"src": "#98A2B3", "demo": "#F37820", "gov": "#00A4DB", "out": "#12B76A", "ext": "#9E77ED"}


def box(x, y, w, h, title, lines=(), kind="src", mono=False):
    """A titled box; with mono, the first line is a file path."""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{FILL[kind]}" stroke="{EDGE[kind]}" stroke-width="1.5"/>',
           f'<text x="{x + 12}" y="{y + 22}" font-size="14" font-weight="700" fill="{INK}">{escape(title)}</text>']
    for i, ln in enumerate(lines):
        fam = ' font-family="ui-monospace,Menlo,monospace" font-size="12"' if mono and i == 0 else ' font-size="12.5"'
        out.append(f'<text x="{x + 12}" y="{y + 42 + i * 17}"{fam} fill="{MUTED}">{escape(ln)}</text>')
    return "\n".join(out)


def arrow(x1, y1, x2, y2, dashed=False, via=None):
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    d = "M" + " L".join(f"{a},{b}" for a, b in [(x1, y1), *(via or []), (x2, y2)])
    return f'<path d="{d}" fill="none" stroke="{MUTED}" stroke-width="1.6"{dash} marker-end="url(#ah)"/>'


def note(x, y, text, anchor="middle"):
    return (f'<text x="{x}" y="{y}" font-size="12" fill="{MUTED}" text-anchor="{anchor}" '
            f'font-style="italic">{escape(text)}</text>')


def lane(x, y, text):
    return (f'<text x="{x}" y="{y}" font-size="11" font-weight="700" letter-spacing="1.2" fill="#98A2B3">'
            f'{escape(text.upper())}</text>')


def svg(w, h, body):
    return (f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
            'font-family="-apple-system,Segoe UI,Inter,Helvetica,Arial,sans-serif" role="img">'
            '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker></defs>'
            f"{body}</svg>")


def lineage():
    b = [lane(20, 20, "1 · Generated in tigerclaw"), lane(282, 20, "2 · Snapshot"),
         lane(532, 20, "3 · Plains' warehouse"), lane(782, 20, "4 · Governed context"),
         box(20, 34, 232, 112, "ibnr_health.db", ["generate_ibnr.py", "members, eligibility, medical and",
                                                  "pharmacy claims, prior auths,", "hospital admissions (causally linked)"],
             mono=True),
         box(20, 160, 232, 112, "mee_synthetic.db", ["build_synthetic.py", "provider master, peer cohorts,",
                                                     "CPT modifiers, 6 providers with", "seeded modifier anomalies"],
             mono=True),
         box(282, 34, 220, 238, "snapshot_mee_demo.py", ["run once; tigerclaw is read-only", "",
                                                         "• no real names: HCSC → PLH,", "   hospitals renamed",
                                                         "• steady-state dates (~7%/yr", "   trend), anomalies kept",
                                                         "• Rx scaled to a typical PMPM", "",
                                                         "→ 9 Parquet files, 10 MB,", "   committed to the repo"],
             kind="demo"),
         box(532, 34, 220, 238, "DuckDB · schema plh_src", ["9 source tables, as a client EDW", "would hold them", "",
                                                             "15,000 members, 5 states", "155k medical, 210k Rx claims",
                                                             "Oct 2024 – Mar 2026", "valued 31 Mar 2026", "",
                                                             "Planted: 628 observation stays", "flagged inpatient"]),
         box(782, 34, 250, 112, "Ontology views", ["units/plh/mappings.yaml", "9 classes: Member, Enrollment,",
                                                   "MedicalClaim, Provider …", "SQL for DuckDB · Databricks · Snowflake"],
             kind="gov", mono=True),
         box(782, 160, 250, 112, "Metrics and dimensions", ["client.yaml", "IP / OP / Prof PMPM, paid per admit,",
                                                            "admits per 1,000, modifier rates;", "state, quarter, cohort …"],
             kind="gov", mono=True),
         box(20, 318, 360, 110, "Documents the curator reads", ["corpus/",
                                                                "9 written for the demo: 4 real changes (MP-112,",
                                                                "UM-021, PI-25, data dictionary) and 5 decoys;",
                                                                "answer_key.yaml says which"], kind="ext", mono=True),
         box(400, 318, 352, 110, "Their Power BI model", ["powerbi/models/MEE_Client_Analytics",
                                                          "TMDL, 36 measures (from tigerclaw's MEE demo);",
                                                          "crosswalked to the metrics, never replaced"],
             kind="ext", mono=True),
         box(782, 318, 250, 110, "Who uses it", ["agents (ContextToolkit tools),", "Power BI (generated measures),",
                                                "Consistency lab"], kind="out"),
         arrow(252, 90, 282, 90), arrow(252, 216, 282, 216), arrow(502, 153, 532, 153), arrow(752, 90, 782, 90),
         arrow(907, 146, 907, 160), arrow(907, 272, 907, 318),
         # documents and the BI model change the context only through proposals
         arrow(200, 318, 790, 280, dashed=True, via=[(200, 296), (790, 296)]),
         arrow(576, 318, 576, 296, dashed=False),
         note(400, 310, "both reach 4 only as proposals that owners approve", anchor="middle")]
    return svg(1052, 440, "\n".join(b))


def system():
    b = [lane(20, 20, "People"), lane(250, 20, "Context Studio"), lane(760, 20, "Shared with every consumer"),
         box(20, 34, 200, 84, "Browser", ["web app (Next.js) :3700", "Inbox, Documents, Metrics,",
                                          "Chat, Consistency, History"]),
         box(20, 132, 200, 96, "Owners", ["'Signed in as' today, SSO later.", "Who approves what:",
                                          "seed/owners.yaml areas"]),
         box(250, 34, 240, 194, "Studio API (FastAPI) :8700", ["proposals: draft, check, route,",
                                                                "   approve, merge, undo",
                                                                "curator: reads documents,",
                                                                "   corroborates in data,",
                                                                "   drafts or asks",
                                                                "curator chat (LangGraph)",
                                                                "Power BI harvest + crosswalk",
                                                                "consistency lab"], kind="demo"),
         box(520, 34, 210, 108, "Content repo (git)", ["main/ — what everything serves", "work/<id>/ — one branch",
                                                         "   per proposal", "every change is a commit"], kind="gov"),
         box(520, 156, 210, 72, "Check job", ["subprocess on the branch;", "checks + impact on a copy"], kind="demo"),
         box(250, 250, 240, 84, "LLM gateway", ["Tiger AI gateway (.env);", "curator and chat only.",
                                                "Numbers come from SQL."], kind="ext"),
         box(520, 250, 210, 84, "Reference warehouse", ["DuckDB per client;", "views installed for main",
                                                         "or a proposal branch"]),
         box(760, 34, 272, 108, "Engine (insurance_context)", ["ontology + mappings → SQL views",
                                                                "semantic layer → metric SQL",
                                                                "ContextToolkit: agent tools",
                                                                "TMDL generator for Power BI"], kind="gov"),
         box(760, 156, 272, 72, "Agents", ["ask for metrics by name; get the", "governed SQL, never raw tables"],
             kind="out"),
         box(760, 250, 272, 84, "Power BI", ["their model keeps its measures;", "governed ones generated as DAX",
                                             "with each approved change"], kind="out"),
         arrow(220, 76, 250, 76), arrow(220, 180, 250, 180), arrow(490, 76, 520, 76), arrow(490, 192, 520, 192),
         arrow(370, 228, 370, 250), arrow(625, 228, 625, 250), arrow(730, 88, 760, 88), arrow(896, 142, 896, 156),
         arrow(1032, 100, 1032, 292, via=[(1046, 100), (1046, 292)])]
    return svg(1056, 348, "\n".join(b))


def process():
    steps = [("Something changes", ["a policy page, an owner's", "question, a Power BI gap,", "a chat request"], "ext"),
             ("Curator drafts", ["a proposal: a git branch", "with the edit and the", "source it came from"], "demo"),
             ("Checks run", ["on real rows, on the", "branch: does it compile,", "what breaks"], "demo"),
             ("Impact shown", ["metrics before → after,", "e.g. MA IP PMPM", "$370 → $317"], "demo"),
             ("Owners approve", ["everyone who owns a", "touched area; routed", "from the diff"], "gov"),
             ("Served", ["merged to main: views,", "metrics, Power BI DAX;", "the next question uses it"], "out")]
    w, gap, y = 158, 20, 40
    b = []
    for i, (t, lines, kind) in enumerate(steps):
        x = 20 + i * (w + gap)
        b.append(f'<circle cx="{x + 16}" cy="{y - 14}" r="11" fill="{EDGE[kind]}"/>'
                 f'<text x="{x + 16}" y="{y - 10}" font-size="12" font-weight="700" fill="#fff" '
                 f'text-anchor="middle">{i + 1}</text>')
        b.append(box(x, y, w, 96, t, lines, kind))
        if i:
            b.append(arrow(x - gap, y + 48, x, y + 48))
    ix, ox = 20 + 3 * (w + gap) + w / 2, 20 + 1 * (w + gap) + w / 2
    b.append(arrow(ix, y + 96, ox, y + 96, dashed=True, via=[(ix, y + 126), (ox, y + 126)]))
    b.append(note((ix + ox) / 2, y + 144, "not right? ask the curator to revise"))
    sx = 20 + 5 * (w + gap) + w / 2
    b.append(arrow(sx, y + 96, sx, y + 160))
    b.append(box(sx - 190, y + 160, 268, 56, "History", ["who approved what, from which page;",
                                                         "undo is another proposal"], "out"))
    return svg(1070, 280, "\n".join(b))
