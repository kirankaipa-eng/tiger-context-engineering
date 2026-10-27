"use client";

/* The Power BI desk: a conversation on the left, the analysis it builds on the right, one exhibit per
   answer. Questions go to the same agent as `ictx pbi ask` (numbers only from the model's captured
   measures, or rows of its tables); clicking a mark on an exhibit drills in directly, in SQL, with no
   model in between: break it down by another column, see its trend, or open the rows behind it. */

import { ArrowUp, Square } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PbiExhibit, exhibitTitle, type DeskActions, type ExhibitData } from "@/components/pbi-exhibit";
import { streamNdjson } from "@/lib/api";
import { colLabel, columnTypes, isDimension, pbi, type PbiMetric } from "@/lib/pbi";
import { useStudio } from "@/lib/studio";

/* eslint-disable @typescript-eslint/no-explicit-any */

type Turn = { role: "user" | "analyst"; text: string; steps: string[]; exhibits: number[]; busy?: boolean; error?: string; drill?: boolean };

const STEP: Record<string, string> = {
  list_metrics: "Reading the model's measures", list_columns: "Finding the columns to break it down by",
  column_values: "Matching values to the data", compute: "Computing from the Power BI measure definitions",
  explore: "Reading rows of the model's tables",
};

const newSession = () => `desk-${Math.random().toString(36).slice(2, 10)}`;

export default function PbiDesk() {
  const { pbiModel, pbiModels, setPbiModel, llm } = useStudio();
  const model = pbiModels.find((m) => m.id === pbiModel);
  const [session, setSession] = useState(newSession);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [cards, setCards] = useState<ExhibitData[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [metrics, setMetrics] = useState<PbiMetric[]>([]);
  const [cols, setCols] = useState<{ dates: string[]; types: Record<string, string> }>({ dates: [], types: {} });
  const [starters, setStarters] = useState<string[]>([]);
  const [pick, setPick] = useState({ metric: "", by: "" });
  const [pickCols, setPickCols] = useState<string[]>([]);
  const abort = useRef<AbortController | null>(null);
  const railEnd = useRef<HTMLDivElement>(null);
  const count = useRef(0);

  useEffect(() => { railEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns]);
  useEffect(() => { if (cards.length) document.getElementById(`exhibit-${cards.length}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); }, [cards.length]);

  useEffect(() => {
    if (!pbiModel) return;
    Promise.all([pbi.metrics(pbiModel), pbi.tables(pbiModel)]).then(async ([r, t]) => {
      const ct = columnTypes(t.tables);
      setCols(ct);
      const ok = r.metrics.filter((m) => m.status === "captured").sort((a, b) => a.label.localeCompare(b.label));
      setMetrics(ok);
      const plain = r.metrics.filter((m) => m.status === "captured" && !m.needs?.by?.length && !Object.keys(m.needs?.params ?? {}).length);
      const a = plain[0], b = plain[1];
      const out: string[] = [];
      if (a) {
        const d = await pbi.metric(pbiModel, a.key).catch(() => null);
        const own = new Set(d?.tables ?? []);
        const dims = (d?.columns ?? []).filter((c) => !ct.dates.includes(c) && isDimension(c, ct.types[c]));
        const dim = dims.find((c) => !own.has(c.replace(/\[.*$/, "").replace(/^'|'$/g, ""))) ?? dims[0];
        const date = (d?.columns ?? []).find((c) => ct.dates.includes(c));
        if (date) out.push(`How is ${a.label} trending by month?`);
        if (dim) out.push(`Which ${colLabel(dim).replace(/ · .*$/, "").toLowerCase()} has the highest ${a.label}${b ? `, and what is its ${b.label}` : ""}?`);
        const fact = d?.tables?.[0];
        if (fact) out.push(`Show the 10 largest rows of ${fact}`);
        setPick({ metric: a.key, by: dim ?? "" });
        setPickCols(d?.columns ?? []);
      }
      out.push("What can I ask about this model?");
      setStarters(out);
    }).catch(() => {});
  }, [pbiModel]);

  const last = (fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)));
  /** Posts an exhibit and returns its number. */
  const post = (c: ExhibitData) => {
    count.current += 1;
    setCards((cs) => [...cs, c]);
    return count.current;
  };

  const ask = async (text: string) => {
    const message = text.trim();
    if (!message || busy || !pbiModel) return;
    if (!llm) { setTurns((ts) => [...ts, { role: "user", text: message, steps: [], exhibits: [] }, { role: "analyst", text: "", steps: [], exhibits: [], error: "No LLM is configured (API_KEY, API_BASE_URL, CHAT_MODEL in .env). Drill into exhibits, or start one from a metric below." }]); return; }
    setInput(""); setBusy(true);
    setTurns((ts) => [...ts, { role: "user", text: message, steps: [], exhibits: [] }, { role: "analyst", text: "", steps: [], exhibits: [], busy: true }]);
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      await streamNdjson(pbi.askPath(pbiModel), { message, session_id: session }, (e) => {
        if (e.type === "chunk") last((t) => ({ ...t, text: t.text + (e.data.text ?? "") }));
        else if (e.type === "agent_tool_call" && e.data.status === "started") last((t) => ({ ...t, text: "", steps: STEP[e.data.tool] && !t.steps.includes(STEP[e.data.tool]) ? [...t.steps, STEP[e.data.tool]] : t.steps }));
        else if (e.type === "pbi_result") {
          const d = e.data as any;
          const n = post({ ...d, filters: d.filters ?? {}, origin: "agent", question: message });
          last((t) => ({ ...t, exhibits: [...t.exhibits, n] }));
        } else if (e.type === "error") last((t) => ({ ...t, error: e.data.message }));
        else if (e.type === "result") last((t) => ({ ...t, text: t.text || e.data.answer || "" }));
      }, ctrl.signal);
    } catch (err: any) {
      if (err?.name !== "AbortError") last((t) => ({ ...t, error: String(err?.message ?? err) }));
    } finally {
      last((t) => ({ ...t, busy: false }));
      setBusy(false); abort.current = null;
    }
  };

  const labels = Object.fromEntries(metrics.map((m) => [m.key, m.label]));
  /** A drill or a start: computed directly, posted as an exhibit and noted in the conversation. */
  const direct = async (note: string, run: () => Promise<ExhibitData>) => {
    setTurns((ts) => [...ts, { role: "user", text: note, steps: [], exhibits: [], drill: true }, { role: "analyst", text: "", steps: ["Computing in SQL"], exhibits: [], busy: true }]);
    try {
      const c = await run();
      const n = post(c);
      last((t) => ({ ...t, busy: false, steps: [], exhibits: [n], text: c.rows.length ? "" : "No rows match." }));
    } catch (err: any) {
      last((t) => ({ ...t, busy: false, error: String(err?.message ?? err) }));
    }
  };

  const keep = (e: ExhibitData, values: unknown[]) => {
    const f: Record<string, unknown> = { ...e.filters };
    e.by.forEach((d, i) => { if (values[i] !== undefined) f[d] = values[i]; });
    return f;
  };
  const actions: DeskActions = {
    drill: (e, values, by) => {
      const filters = keep(e, values);
      const within = Object.entries(filters).map(([k, v]) => `${colLabel(k).replace(/ · .*$/, "")} = ${v ?? "(blank)"}`).join(", ");
      direct(`${exhibitTitle({ ...e, by: [by] })}, within ${within}`, async () => {
        const r = await pbi.compute(pbiModel, { metrics: e.metrics, by: [by], filters });
        return { kind: "compute", columns: r.columns, rows: r.rows, metrics: e.metrics, by: [by], filters, sql: r.sql, ms: r.ms, warehouse: r.warehouse, labels: e.labels, origin: "drill" };
      });
    },
    rows: (e, values) => {
      const filters = keep(e, values);
      direct(`The rows behind ${exhibitTitle(e).toLowerCase()}, for ${Object.values(filters).map((v) => v ?? "(blank)").join(", ")}`, async () => {
        const d = e.kind === "compute" && e.metrics[0] ? await pbi.metric(pbiModel, e.metrics[0]).catch(() => null) : null;
        const table = d?.tables?.[0] ?? e.table;
        const t = table ? (await pbi.tables(pbiModel)).tables.find((x) => x.name === table) : undefined;
        const ref = (c: string) => `${/^[A-Za-z_][A-Za-z0-9_]*$/.test(table!) ? table : `'${table!.replace(/'/g, "''")}'`}[${c}]`;
        const own = t ? t.columns.map((c) => ref(c.name)).filter((c) => !/image|sort|colou?r|url/i.test(c)).slice(0, 8) : [];
        const columns = [...Object.keys(filters), ...own].filter((c, i, a) => a.indexOf(c) === i);
        const r = await pbi.explore(pbiModel, { columns, filters, limit: 50, table });
        return { kind: "explore", columns: r.columns, rows: r.rows, metrics: [], by: columns, filters, sql: r.sql, ms: r.ms, warehouse: r.warehouse, labels: {}, origin: "drill", table };
      });
    },
    ask: (text) => ask(text),
  };
  const start = () => {
    if (!pick.metric || !pick.by) return;
    const label = labels[pick.metric] ?? pick.metric;
    direct(`${label} by ${colLabel(pick.by).replace(/ · .*$/, "")}`, async () => {
      const r = await pbi.compute(pbiModel, { metrics: [pick.metric], by: [pick.by] });
      return { kind: "compute", columns: r.columns, rows: r.rows, metrics: [pick.metric], by: [pick.by], filters: {}, sql: r.sql, ms: r.ms, warehouse: r.warehouse, labels, origin: "drill" };
    });
  };
  const reset = () => { abort.current?.abort(); setTurns([]); setCards([]); count.current = 0; setSession(newSession()); };

  return (
    <div className="desk flex h-screen flex-col bg-[var(--d-paper)]">
      <header className="flex h-14 shrink-0 items-center gap-5 bg-[var(--d-navy)] px-6 text-[var(--d-rail-ink)]">
        <div className="flex items-baseline gap-3">
          <span className="cond text-[19px] font-semibold text-white">{model?.name ?? "…"}</span>
          <span className="text-[14px] text-[var(--d-rail-muted)]">Power BI analysis desk</span>
        </div>
        {pbiModels.length > 1 && (
          <select value={pbiModel} onChange={(e) => { setPbiModel(e.target.value); reset(); }} className="rounded-md border border-[var(--d-navy-2)] bg-[var(--d-navy-2)] px-2 py-1.5 text-[13px] text-white">
            {pbiModels.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        )}
        <nav className="ml-auto flex items-center gap-1 text-[13px]">
          {[["/pbi/model", "Coverage"], ["/pbi/metrics", "Measures"], ["/pbi/drill", "Drilldown"], ["/pbi/explore", "Explore rows"], ["/pbi/tables", "Tables"]].map(([href, label]) => (
            <Link key={href} href={href} className="rounded-md px-3 py-1.5 text-[var(--d-rail-ink)] hover:bg-[var(--d-navy-2)]">{label}</Link>
          ))}
          <button onClick={reset} className="ml-2 rounded-md border border-[var(--d-navy-2)] px-3 py-1.5 text-[var(--d-rail-ink)] hover:bg-[var(--d-navy-2)]">New analysis</button>
        </nav>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-[420px] shrink-0 flex-col bg-[var(--d-navy)]">
          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-6">
            {turns.length === 0 && (
              <div>
                <h1 className="cond text-[30px] font-semibold leading-tight text-white">Ask about {model?.name ?? "the model"}.</h1>
                <p className="mt-3 text-[14px] leading-relaxed text-[var(--d-rail-muted)]">
                  Every number is computed from the Power BI model&rsquo;s own measure definitions ({model ? `${model.captured} of ${model.measures} captured` : "…"}), or from rows of its tables when no measure answers. Click anything on an exhibit to drill in.
                </p>
                <div className="mt-6 space-y-2">
                  {starters.map((s) => (
                    <button key={s} onClick={() => ask(s)} className="block w-full rounded-lg border border-[var(--d-navy-2)] bg-[var(--d-navy-2)] px-4 py-3 text-left text-[14px] text-[var(--d-rail-ink)] hover:border-[var(--d-teal)]">{s}</button>
                  ))}
                </div>
                <div className="mt-6 rounded-lg border border-[var(--d-navy-2)] p-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[.08em] text-[var(--d-rail-muted)]">Or start from a measure</div>
                  <select value={pick.metric} onChange={async (e) => {
                    const k = e.target.value;
                    const d = await pbi.metric(pbiModel, k).catch(() => null);
                    const dims = (d?.columns ?? []).filter((c) => !cols.dates.includes(c) && isDimension(c, cols.types[c]));
                    setPickCols(d?.columns ?? []);
                    setPick({ metric: k, by: d?.needs?.by?.[0] ?? dims[0] ?? "" });
                  }} className="mt-2 w-full rounded-md bg-[var(--d-navy-2)] px-2 py-2 text-[13px] text-white">
                    {metrics.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
                  </select>
                  <div className="mt-2 flex gap-2">
                    <select value={pick.by} onChange={(e) => setPick({ ...pick, by: e.target.value })} className="min-w-0 flex-1 rounded-md bg-[var(--d-navy-2)] px-2 py-2 text-[13px] text-white">
                      {pickCols.filter((c) => !cols.dates.includes(c) && isDimension(c, cols.types[c])).map((c) => <option key={c} value={c}>by {colLabel(c)}</option>)}
                      {pickCols.filter((c) => cols.dates.includes(c)).map((c) => <option key={c} value={`${c}:month`}>by {colLabel(c)} (month)</option>)}
                    </select>
                    <button onClick={start} disabled={!pick.by} className="rounded-md bg-[var(--d-teal)] px-3 text-[13px] font-semibold text-white disabled:opacity-40">Show</button>
                  </div>
                </div>
              </div>
            )}
            {turns.map((t, i) => t.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="max-w-[88%] rounded-2xl rounded-br-sm bg-white px-4 py-2.5 text-[14px] text-[var(--d-navy)]">
                  {t.drill && <span className="mb-0.5 block text-[11px] font-semibold text-[var(--d-teal)]">Drill-down</span>}
                  {t.text}
                </div>
              </div>
            ) : (
              <div key={i}>
                {t.steps.map((s, j) => (
                  <div key={j} className="mb-1 flex items-center gap-2 text-[12px] text-[var(--d-rail-muted)]">
                    <span className={`inline-block h-1.5 w-1.5 rounded-full bg-[#5fc4bf] ${t.busy && j === t.steps.length - 1 ? "pulse-soft" : ""}`} />{s}
                  </div>
                ))}
                {t.exhibits.length > 0 && (
                  <div className="mb-2 mt-2 flex flex-wrap gap-1.5">
                    {t.exhibits.map((n) => <a key={n} href={`#exhibit-${n}`} className="rounded-full bg-[#0f7b78] px-2.5 py-0.5 text-[12px] font-semibold text-white hover:bg-[#13918d]">Exhibit {n}</a>)}
                  </div>
                )}
                {t.text && <div className="rail-prose"><ReactMarkdown remarkPlugins={[remarkGfm]}>{t.text}</ReactMarkdown></div>}
                {t.busy && !t.text && t.steps.length === 0 && <div className="pulse-soft text-[13px] text-[var(--d-rail-muted)]">Reading the question…</div>}
                {t.error && <div className="mt-2 rounded-md bg-[#4a1f24] px-3 py-2 text-[13px] text-[#ffd9dc]">{t.error}</div>}
              </div>
            ))}
            <div ref={railEnd} />
          </div>
          <form onSubmit={(e) => { e.preventDefault(); ask(input); }} className="border-t border-[var(--d-navy-2)] p-4">
            <div className="flex items-end gap-2 rounded-xl bg-[var(--d-navy-2)] p-1.5 focus-within:ring-2 focus-within:ring-[var(--d-teal)]">
              <label className="min-w-0 flex-1">
                <span className="sr-only">Ask a question</span>
                <textarea value={input} onChange={(e) => setInput(e.target.value)} rows={1}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(input); } }}
                  placeholder={turns.length ? "Ask a follow-up" : "Ask about the numbers, or for rows"} className="block max-h-36 min-h-10 w-full resize-none bg-transparent px-2.5 py-2.5 text-[14px] text-white outline-none placeholder:text-[var(--d-rail-muted)]" />
              </label>
              {busy ? (
                <button type="button" onClick={() => abort.current?.abort()} aria-label="Stop" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white text-[var(--d-navy)]"><Square size={14} /></button>
              ) : (
                <button type="submit" aria-label="Ask" disabled={!input.trim()} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--d-teal)] text-white disabled:opacity-40"><ArrowUp size={17} /></button>
              )}
            </div>
            <div className="mt-2 truncate text-[11px] text-[var(--d-rail-muted)]">{model?.warehouse ? `Data: ${model.warehouse}` : `No data: ${model?.data_error ?? "…"}`}</div>
          </form>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto">
          {cards.length === 0 ? (
            <div className="flex h-full items-center justify-center px-10">
              <div className="max-w-md text-center">
                <svg width="64" height="64" viewBox="0 0 64 64" aria-hidden className="mx-auto">
                  <path d="M10 50h44" stroke="#9aabbf" strokeWidth="2" /><path d="M14 44 26 32l8 6 16-18" stroke="#0f7b78" strokeWidth="3" fill="none" /><circle cx="50" cy="20" r="4" fill="#c97c06" />
                </svg>
                <p className="cond mt-4 text-[22px] font-semibold text-[var(--d-navy)]">Your analysis builds here, one exhibit per answer.</p>
                <p className="mt-2 text-[14px] text-[var(--d-muted)]">Each exhibit says which Power BI measure it used and shows the SQL behind it, so it matches the report.</p>
              </div>
            </div>
          ) : (
            <div className="relative mx-auto max-w-[1180px] px-8 py-8">
              <div className="absolute bottom-8 left-[52px] top-12 w-0.5 bg-[var(--d-rule)]" aria-hidden />
              {cards.map((c, i) => (
                <div key={i} className="relative mb-8 grid grid-cols-[48px_1fr] gap-4">
                  <div className="relative pt-5">
                    <span className="num relative z-10 flex h-9 w-9 items-center justify-center rounded-full border-2 border-[var(--d-teal)] bg-white text-[15px] font-semibold text-[var(--d-teal)]">{i + 1}</span>
                  </div>
                  <PbiExhibit e={c} n={i + 1} model={pbiModel} actions={actions} landed={i === cards.length - 1} dates={cols.dates} types={cols.types} />
                </div>
              ))}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
