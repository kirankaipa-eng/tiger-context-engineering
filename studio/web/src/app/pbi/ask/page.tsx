"use client";

/* Ask once, get a dashboard. The question goes to the same agent as the desk (the `/ask` stream); its
   answer leads the page, and the measure it settled on is laid out as linked tiles: the number, its
   trend, what it breaks down by, and the detail. The tiles past the agent's own are planned here from the
   measure's columns and run directly, in SQL, through `/compute`; no model in between. Clicking a mark
   focuses every tile on it (the trail above says where you are), and Keep pins the tiles to My dashboard. */

import { ArrowUp, Bookmark, Check, Repeat, Share2, Square, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AutoChart, DESK_PALETTE, isRatio, pctText, periodName } from "@/components/auto-chart";
import type { ExhibitData } from "@/components/pbi-exhibit";
import { ResultTable, SqlBlock, num } from "@/components/pbi";
import { streamNdjson } from "@/lib/api";
import { owner, readAsked, tileOf, writeAsked } from "@/lib/desk";
import { colLabel, columnTypes, isDimension, pbi, type PbiMetric, type Result } from "@/lib/pbi";
import { useStudio } from "@/lib/studio";
import { useLook } from "@/lib/theme";

/* eslint-disable @typescript-eslint/no-explicit-any */

type Role = "number" | "trend" | "breakdown" | "detail";
/** `latest`: measures that only exist month by month (a change on the month before): the number tile
    shows them at the month in focus, or the last one, from the trend's rows. */
type Spec = { id: string; role: Role; metrics: string[]; by: string[]; filters: Record<string, unknown>; latest?: string[] };
type Run = { result?: Result; error?: string; loading?: boolean };

const PERIOD = /:(year|quarter|month|week|day)$/;
const STEP: Record<string, string> = {
  list_metrics: "Reading the measures", list_columns: "Finding what it breaks down by", column_values: "Matching your words to the data",
  compute: "Computing from the Power BI measures", explore: "Reading rows", explain: "Working out what moved",
};
/** "Store[Store]" -> "Store", "Calendar[Date]:month" -> "month": as a person says it. */
const said = (ref: string) => (PERIOD.test(ref) ? ref.match(PERIOD)![1] : colLabel(ref).replace(/ · .*$/, ""));
const shown = (ref: string, v: unknown) => (PERIOD.test(ref) ? periodName(v) : v === null || v === undefined || v === "" ? "(blank)" : String(v));
const key = (metrics: string[], by: string[], filters: Record<string, unknown>) => JSON.stringify([metrics, by, Object.entries(filters).sort()]);
const newSession = () => `ask-${Math.random().toString(36).slice(2, 10)}`;

/** The tiles for the measures an answer settled on: their number, their trend by month, two breakdowns
    (the answer's own first, then columns of other tables before its own), and the detail of the first two.
    A measure that needs a month (a change on the month before) goes on the trend and, at its latest
    month, on the number; one that needs anything else (a column to rank over, a what-if value) is left out. */
async function plan(model: string, lead: ExhibitData, dates: string[], types: Record<string, string>, all: PbiMetric[]): Promise<Spec[]> {
  const filters = lead.filters;
  const needs = (k: string) => all.find((x) => x.key === k)?.needs;
  const bare = (k: string) => !needs(k)?.by?.length && !Object.values(needs(k)?.params ?? {}).some((x) => x.default == null);
  const monthly = lead.metrics.filter((k) => !bare(k) && needs(k)!.by.length === 1 && PERIOD.test(needs(k)!.by[0]) && !Object.keys(needs(k)!.params ?? {}).length);
  const metrics = lead.metrics.filter(bare).slice(0, 3);
  const first = metrics[0] ?? monthly[0];
  if (!first) return [];
  const d = await pbi.metric(model, first).catch(() => null);
  const columns = d?.columns ?? [];
  const own = new Set(d?.tables ?? []);
  const date = columns.find((c) => dates.includes(c));
  const table = (c: string) => c.replace(/\[.*$/, "").replace(/^'|'$/g, "");
  const field = (c: string) => c.replace(/^.*\[|\].*$/g, "");
  // a column of the calendar (Month, Year) would be the trend again, not a breakdown
  const calendar = new Set(columns.filter((c) => dates.includes(c)).map(table));
  const dims = columns.filter((c) => !dates.includes(c) && isDimension(c, types[c]) && !(c in filters) && !calendar.has(table(c)));
  const outside = (c: string) => !own.has(table(c));
  const breakdowns = [...lead.by.filter((b) => !PERIOD.test(b)), ...dims.filter(outside), ...dims.filter((c) => !outside(c))]
    // two tables' Product columns are the same question twice: one breakdown per column name
    .filter((c, i, a) => a.findIndex((x) => field(x) === field(c)) === i).slice(0, 2);
  const trend = monthly.length ? needs(monthly[0])!.by[0] : lead.by.find((b) => PERIOD.test(b)) ?? (date ? `${date}:month` : undefined);
  const latest = trend ? monthly.filter((k) => needs(k)!.by[0] === trend) : [];
  const specs: Spec[] = [{ id: "number", role: "number", metrics, by: [], filters, latest }];
  if (trend) specs.push({ id: "trend", role: "trend", metrics: [...metrics.slice(0, 1), ...latest], by: [trend], filters });
  if (!metrics.length) return specs;
  breakdowns.forEach((b, i) => specs.push({ id: `by${i}`, role: "breakdown", metrics: metrics.slice(0, 1), by: [b], filters }));
  if (breakdowns.length === 2) specs.push({ id: "detail", role: "detail", metrics, by: breakdowns, filters });
  return specs;
}

export default function AskPage() {
  const { pbiModel, pbiModels, llm } = useStudio();
  const look = useLook();
  const model = pbiModels.find((m) => m.id === pbiModel);
  const [metrics, setMetrics] = useState<PbiMetric[]>([]);
  const [cols, setCols] = useState<{ dates: string[]; types: Record<string, string> }>({ dates: [], types: {} });
  const [session, setSession] = useState(newSession);
  const [input, setInput] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [step, setStep] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [specs, setSpecs] = useState<Spec[]>([]);
  const [runs, setRuns] = useState<Record<string, Run>>({});
  const [focus, setFocus] = useState<[string, unknown][]>([]);  // the trail: each click narrows every tile
  const [kept, setKept] = useState(false);
  const cache = useRef(new Map<string, Result>());
  const abort = useRef<AbortController | null>(null);
  const box = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!pbiModel) return;
    Promise.all([pbi.metrics(pbiModel), pbi.tables(pbiModel)]).then(([r, t]) => {
      setMetrics(r.metrics.filter((m) => m.status === "captured"));
      setCols(columnTypes(t.tables));
    }).catch(() => {});
  }, [pbiModel]);
  const labels = useMemo(() => Object.fromEntries(metrics.map((m) => [m.key, m.label])), [metrics]);
  const starters = useMemo(() => {
    const plain = metrics.filter((m) => !m.needs?.by?.length && !Object.keys(m.needs?.params ?? {}).length).slice(0, 2);
    return plain.length ? [`How is ${plain[0].label} doing, and what drives it?`, ...(plain[1] ? [`Where is ${plain[1].label} highest, and how has it moved?`] : [])] : [];
  }, [metrics]);

  // every tile, re-run whenever the trail changes: a chart ignores the focus on its own breakdown, so it
  // keeps showing every value and the one picked is named on it; the detail narrows to it
  useEffect(() => {
    if (!specs.length) return;
    for (const s of specs) {
      const filters = { ...s.filters, ...Object.fromEntries(focus.filter(([k]) => s.role === "detail" || !s.by.includes(k))) };
      if (!s.metrics.length) continue;  // the number of a measure that needs a month: read off the trend
      const k = key(s.metrics, s.by, filters);
      const hit = cache.current.get(k);
      if (hit) { setRuns((r) => ({ ...r, [s.id]: { result: hit } })); continue; }
      setRuns((r) => ({ ...r, [s.id]: { result: r[s.id]?.result, loading: true } }));
      pbi.compute(pbiModel, { metrics: s.metrics, by: s.by, filters })
        .then((res) => { cache.current.set(k, res); setRuns((r) => ({ ...r, [s.id]: { result: res } })); })
        .catch((err) => setRuns((r) => ({ ...r, [s.id]: { error: String(err?.message ?? err) } })));
    }
  }, [specs, focus, pbiModel]);

  const ask = async (text: string) => {
    const message = text.trim();
    if (!message || busy || !pbiModel) return;
    if (!llm) { setError("No language model is configured (API_KEY, API_BASE_URL, CHAT_MODEL in .env)."); return; }
    setInput(""); setQuestion(message); setAnswer(""); setError(""); setStep("Reading the question");
    setSpecs([]); setRuns({}); setFocus([]); setKept(false); setBusy(true);
    const results: ExhibitData[] = [];
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      await streamNdjson(pbi.askPath(pbiModel), { message, session_id: session }, (e) => {
        if (e.type === "chunk") setAnswer((a) => a + (e.data.text ?? ""));
        else if (e.type === "agent_tool_call" && e.data.status === "started") { setAnswer(""); if (STEP[e.data.tool]) setStep(STEP[e.data.tool]); }
        else if (e.type === "pbi_result") {
          const d = e.data as any, r: ExhibitData = { ...d, filters: d.filters ?? {}, origin: "agent" };
          results.push(r);
          // the agent's own rows stand in for the same question, so its tiles need not run twice
          if (r.kind === "compute") cache.current.set(key(r.metrics, r.by, r.filters), { columns: r.columns, rows: r.rows, sql: r.sql ?? "", ms: r.ms, warehouse: r.warehouse ?? "", truncated: false, notes: [] });
        } else if (e.type === "error") setError(e.data.message);
        else if (e.type === "result") setAnswer((a) => a || e.data.answer || "");
      }, ctrl.signal);
      // a "why" answer (an investigation) lays out the same way: its measure over its period, broken down
      // first by the columns it walked down
      const last = [...results].reverse().find((r) => (r.kind === "compute" || r.kind === "explain") && r.metrics.length);
      const lead = last?.explain ? { ...last, by: [last.explain.period, ...last.explain.steps.map((s) => s.by)] } : last;
      if (lead) {
        // what the answer narrowed to (Product = Excel) becomes the focus, not a fixed filter: the trail says
        // so, its own chart shows it against the rest, and one click on "Everything" widens it
        const narrowed = Object.keys(lead.filters).filter((k) => !PERIOD.test(k));
        setStep("Laying out the dashboard");
        setSpecs(await plan(pbiModel, { ...lead, by: [...narrowed, ...lead.by], filters: {} }, cols.dates, cols.types, metrics));
        setFocus(Object.entries(lead.filters));
      }
    } catch (err: any) {
      if (err?.name !== "AbortError") setError(String(err?.message ?? err));
    } finally { setStep(""); setBusy(false); abort.current = null; }
  };
  const fresh = () => { abort.current?.abort(); setSession(newSession()); setQuestion(""); setAnswer(""); setSpecs([]); setRuns({}); setFocus([]); setError(""); setKept(false); setTimeout(() => box.current?.focus()); };
  /** A question kept in this browser, reopened where it left off: its answer, tiles and focus, and its
      conversation, so a follow-up still knows what came before. */
  const reopen = (id: string) => {
    const a = readAsked(pbiModel).find((x) => x.id === id);
    if (!a) return fresh();
    abort.current?.abort();
    setSession(a.id); setQuestion(a.question); setAnswer(a.answer); setSpecs(a.specs as Spec[]); setFocus(a.focus); setRuns({}); setError(""); setKept(false);
  };
  // the sidebar opens a question (or a new one); arriving with ?c= opens it once the model is known
  useEffect(() => {
    if (!pbiModel) return;
    const c = new URLSearchParams(window.location.search).get("c");
    if (c) { window.history.replaceState(null, "", "/pbi/ask"); reopen(c); }  // eslint-disable-line react-hooks/set-state-in-effect
    const on = (e: Event) => { const id = (e as CustomEvent).detail; if (id) reopen(id); else fresh(); };
    window.addEventListener("ask:open", on);
    return () => window.removeEventListener("ask:open", on);
  }, [pbiModel]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { window.dispatchEvent(new CustomEvent("ask:showing", { detail: question ? session : null })); }, [question, session]);
  // each answered question kept, titled by the conversation's first; a new question moves it to the top
  useEffect(() => {
    if (busy || !question || !pbiModel) return;
    const list = readAsked(pbiModel), prev = list.find((x) => x.id === session);
    const entry = { id: session, title: prev?.title ?? question, question, answer, specs, focus, at: Date.now() / 1000 };
    writeAsked(pbiModel, prev?.question === question ? list.map((x) => (x.id === session ? entry : x)) : [entry, ...list.filter((x) => x.id !== session)]);
  }, [busy, question, answer, specs, focus, session, pbiModel]);

  const pick = (s: Spec, values: unknown[]) => setFocus((f) => {
    const next = f.filter(([k]) => !s.by.includes(k));
    s.by.forEach((b, i) => { if (values[i] !== undefined) next.push([b, values[i]]); });
    return next;
  });
  const keep = async () => {
    const board = await pbi.board(pbiModel, owner()).catch(() => ({ tiles: [] as ReturnType<typeof tileOf>[] }));
    const mine = specs.map((s) => tileOf({ kind: "compute", columns: [], rows: [], metrics: s.metrics, by: s.by, filters: { ...s.filters, ...Object.fromEntries(focus) }, labels, origin: "agent" }));
    const tiles = [...board.tiles, ...mine.filter((t) => !board.tiles.some((x) => x.id === t.id))];
    await pbi.saveBoard(pbiModel, owner(), tiles).then(() => setKept(true)).catch((err) => setError(`Could not keep it: ${err?.message ?? err}`));
  };

  const name = (k: string) => labels[k] ?? k;
  /** A month-by-month measure at the month in focus, or the last month: [value, month]. */
  const atMonth = (m: string): [unknown, unknown] | null => {
    const t = specs.find((x) => x.role === "trend"), r = runs.trend?.result;
    if (!t || !r?.rows.length) return null;
    const i = r.columns.indexOf(t.by[0]), want = focus.find(([k]) => k === t.by[0])?.[1];
    const row = want !== undefined ? r.rows.find((x) => x[i] === want) : [...r.rows].sort((a, b) => String(a[i]).localeCompare(String(b[i]))).at(-1);
    return row ? [row[r.columns.indexOf(m)], row[i]] : null;
  };
  const tile = (s: Spec) => {
    const run = s.role === "number" && !s.metrics.length ? runs.trend : runs[s.id], r = run?.result;
    const mine = s.role === "detail" ? undefined : focus.find(([k]) => s.by.includes(k));
    const title = s.role === "number" ? [...s.metrics, ...(s.latest ?? [])].map(name).join(" · ") : s.role === "detail" ? `${name(s.metrics[0])}${s.metrics.length > 1 ? " and more" : ""}, by ${s.by.map(said).join(" and ")}`
      : `${name(s.metrics[0])} by ${said(s.by[0])}`;
    const span = { number: "lg:col-span-2", trend: "lg:col-span-4", breakdown: "lg:col-span-3", detail: "lg:col-span-6" }[s.role];
    return (
      <section key={s.id} className={`flex min-w-0 flex-col rounded-2xl bg-[var(--d-sheet)] p-5 shadow-[0_1px_2px_rgba(20,35,56,.06)] ${span}`}>
        <header className="flex items-baseline gap-3">
          <h2 className="cond text-[15px] font-semibold text-[var(--d-ink)]">{title}</h2>
          {s.role !== "number" && s.role !== "detail" && !mine && <span className="text-[12px] text-[var(--d-muted)]">Click {s.role === "trend" ? "a month" : "a bar"} to focus on it</span>}
          {mine && <span className="rounded-full bg-[var(--d-teal-soft)] px-2 py-0.5 text-[12px] font-semibold text-[var(--d-teal)]">{shown(mine[0], mine[1])} picked</span>}
          {(!run || run.loading) && <span className="pulse-soft ml-auto text-[12px] text-[var(--d-muted)]">Computing…</span>}
        </header>
        <div className={`mt-3 min-h-0 flex-1 transition-opacity ${run?.loading && r ? "opacity-40" : ""}`}>
          {run?.error ? <p className="text-[13px] text-[var(--d-rise-ink)]">{run.error}</p>
            : !r ? <div className="h-40 rounded-xl bg-[var(--d-paper)]" />
            : !r.rows.length ? <p className="py-6 text-[13px] text-[var(--d-muted)]">Nothing here for this focus.</p>
            : s.role === "number" ? (
              <div className="flex h-full flex-col justify-center gap-4">
                {s.metrics.map((m) => {
                  const v = r.rows[0]?.[r.columns.indexOf(m)];
                  return (
                    <div key={m}>
                      {s.metrics.length + (s.latest?.length ?? 0) > 1 && <div className="text-[13px] text-[var(--d-muted)]">{name(m)}</div>}
                      <div className="num text-[44px] font-semibold leading-none text-[var(--d-ink)]">{isRatio(name(m), [v]) ? pctText(v) : num(v)}</div>
                    </div>
                  );
                })}
                {s.metrics.length > 0 && (
                  <div className="text-[13px] text-[var(--d-muted)]">
                    {focus.length ? `For ${focus.map(([k, v]) => shown(k, v)).join(", ")}` : Object.keys(s.filters).length ? `For ${Object.entries(s.filters).map(([k, v]) => shown(k, v)).join(", ")}` : "Every month, everything"}
                  </div>
                )}
                {(s.latest ?? []).map((m) => {
                  const at = atMonth(m);
                  return (
                    <div key={m}>
                      <div className="text-[13px] text-[var(--d-muted)]">{name(m)}{at ? `, ${periodName(at[1])}` : ""}</div>
                      <div className="num text-[30px] font-semibold leading-none text-[var(--d-ink)]">{!at ? "…" : isRatio(name(m), [at[0]]) ? pctText(at[0]) : num(at[0])}</div>
                    </div>
                  );
                })}
              </div>
            ) : s.role === "detail" ? (
              <div className="max-h-80 overflow-auto"><ResultTable columns={r.columns} rows={r.rows} numeric={s.metrics} labels={{ ...labels, ...Object.fromEntries(s.by.map((b) => [b, said(b)])) }} /></div>
            ) : (
              <AutoChart columns={r.columns} rows={r.rows} dims={s.by} metrics={s.metrics} labels={labels} palette={DESK_PALETTE} height={s.role === "trend" ? 240 : 260} hideKinds
                onPick={(p) => pick(s, p.values)} />
            )}
        </div>
        {r?.sql && (
          <details className="mt-3 text-[12px] text-[var(--d-muted)]">
            <summary className="cursor-pointer select-none hover:text-[var(--d-ink)]">How this was computed</summary>
            <div className="mt-2"><SqlBlock sql={r.sql} warehouse={r.warehouse} ms={r.ms} /></div>
          </details>
        )}
      </section>
    );
  };

  const composer = (big: boolean) => (
    <form onSubmit={(e) => { e.preventDefault(); ask(input); }}
      className={`flex items-center gap-2 rounded-2xl bg-[var(--d-sheet)] p-1.5 pl-5 shadow-[0_12px_40px_-24px_rgba(20,35,56,.5)] ring-1 ring-[var(--d-rule)] focus-within:ring-2 focus-within:ring-[var(--d-teal)] ${big ? "py-2" : ""}`}>
      <label className="min-w-0 flex-1">
        <span className="sr-only">Ask a question</span>
        {/* the box's ring shows the focus; the desk's outline on the field itself would draw a box inside it */}
        <input ref={box} value={input} onChange={(e) => setInput(e.target.value)} autoFocus={big} style={{ outline: "none" }}
          placeholder={question ? "Ask a follow-up, or something new" : "Ask a question about your numbers"}
          className={`w-full bg-transparent text-[var(--d-ink)] outline-none placeholder:text-[var(--d-muted)] ${big ? "text-[18px]" : "text-[15px]"}`} />
      </label>
      {busy ? (
        <button type="button" onClick={() => abort.current?.abort()} aria-label="Stop" className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--d-navy)] text-white"><Square size={14} /></button>
      ) : (
        <button type="submit" disabled={!input.trim()} aria-label="Ask" className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--d-teal)] text-white disabled:opacity-30"><ArrowUp size={18} /></button>
      )}
    </form>
  );

  return (
    <div className={`desk ${look.look} flex h-screen flex-col bg-[var(--d-paper)] text-[var(--d-ink)]`}>
      <main className="min-h-0 flex-1 overflow-y-auto">
        {!question ? (
          <div className="mx-auto flex min-h-full max-w-2xl flex-col justify-center px-6 py-16">
            <h1 className="cond text-[34px] font-semibold leading-tight">Ask a question. Get a dashboard.</h1>
            <p className="mt-2 text-[15px] leading-relaxed text-[var(--d-muted)]">
              The answer comes first, then the number, its trend and what drives it, all linked: click anything to focus every view on it.
              Every number comes from your Power BI measures, translated to SQL{model ? ` (${model.captured} of ${model.measures} so far)` : ""}.
            </p>
            <div className="mt-8">{composer(true)}</div>
            {error && <p className="mt-3 text-[13px] text-[var(--d-rise-ink)]">{error}</p>}
            <div className="mt-5 flex flex-wrap gap-2">
              {starters.map((s) => (
                <button key={s} onClick={() => ask(s)} className="rounded-full bg-[var(--d-sheet)] px-4 py-2 text-[13.5px] text-[var(--d-ink-2)] ring-1 ring-[var(--d-rule)] hover:ring-[var(--d-teal)]">{s}</button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-[1180px] px-8 pb-16 pt-8">
            <p className="text-[13px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">You asked</p>
            <h1 className="cond mt-1 text-[28px] font-semibold leading-tight">{question}</h1>
            <div className="mt-4 max-w-3xl text-[15.5px] leading-relaxed text-[var(--d-ink-2)] [&_p]:mb-2 [&_strong]:text-[var(--d-ink)] [&_ul]:ml-5 [&_ul]:list-disc">
              {answer ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{answer}</ReactMarkdown> : busy ? <p className="pulse-soft text-[var(--d-muted)]">{step}…</p> : null}
              {busy && answer && step && <p className="pulse-soft text-[13px] text-[var(--d-muted)]">{step}…</p>}
            </div>
            {error && <p className="mt-3 rounded-lg bg-[var(--d-rise-soft)] px-3 py-2 text-[13px] text-[var(--d-rise-ink)]">{error}</p>}
            {!busy && !specs.length && !error && <p className="mt-3 text-[13px] text-[var(--d-muted)]">This answer did not come from a measure, so there is no dashboard for it. Try asking about a number.</p>}

            {specs.length > 0 && (
              <>
                <div className="sticky top-0 z-10 -mx-8 mt-6 flex flex-wrap items-center gap-2 bg-[var(--d-paper)]/95 px-8 py-3 backdrop-blur">
                  <span className="text-[13px] text-[var(--d-muted)]">Showing</span>
                  <button onClick={() => setFocus([])} className={`rounded-full px-3 py-1 text-[13px] ${focus.length ? "text-[var(--d-teal)] hover:underline" : "bg-[var(--d-navy)] font-semibold text-white"}`}>Everything</button>
                  {focus.map(([k, v], i) => (
                    <span key={k} className="flex items-center gap-1.5">
                      <span className="text-[var(--d-muted)]" aria-hidden>›</span>
                      <span className={`flex items-center gap-1 rounded-full py-1 pl-3 pr-1.5 text-[13px] ${i === focus.length - 1 ? "bg-[var(--d-navy)] font-semibold text-white" : "bg-[var(--d-sheet)] text-[var(--d-ink)] ring-1 ring-[var(--d-rule)]"}`}>
                        <button onClick={() => setFocus(focus.slice(0, i + 1))}>{said(k)}: {shown(k, v)}</button>
                        <button aria-label={`Stop focusing on ${shown(k, v)}`} onClick={() => setFocus(focus.filter(([x]) => x !== k))} className="rounded-full p-0.5 opacity-70 hover:opacity-100"><X size={12} /></button>
                      </span>
                    </span>
                  ))}
                  <span className="ml-auto flex items-center gap-1">
                    <button onClick={keep} disabled={kept} className="flex items-center gap-1.5 rounded-full bg-[var(--d-teal)] px-3.5 py-1.5 text-[13px] font-semibold text-white disabled:bg-[var(--d-teal-soft)] disabled:text-[var(--d-teal)]">
                      {kept ? <Check size={14} /> : <Bookmark size={14} />}{kept ? "Kept in My dashboard" : "Keep"}
                    </button>
                    {[["Share", Share2], ["Repeat", Repeat]].map(([label, Icon]: any) => (
                      <button key={label} disabled title={`${label}: not built yet (needs the server side)`} className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] text-[var(--d-muted)] opacity-60"><Icon size={14} />{label}</button>
                    ))}
                  </span>
                </div>
                <div className="mt-2 grid grid-cols-1 gap-4 lg:grid-cols-6">{specs.map(tile)}</div>
              </>
            )}
          </div>
        )}
      </main>
      {/* the follow-up box stays at the foot of the window, under the answer however long it is */}
      {question && <div className="shrink-0 px-8 pb-6 pt-3"><div className="mx-auto max-w-2xl">{composer(false)}</div></div>}
    </div>
  );
}
