"use client";

/* A thread on one chart: a conversation that stays with it, for going down the rabbit hole on that chart alone while
   the dashboard or the answer it sits in stays where it is. It opens beside the page, the chart at its head. Each
   question goes to the analyst as /ask does, told which chart it is about (its measure, columns and filters); a
   reply's own charts are drawn small, each with a thread of its own (one level deeper, the way back kept as a trail)
   and Add to dashboard. A dashboard's chart keeps its threads on its tile; an answer's, in this browser. */

import { ChevronRight, Loader2, MessagesSquare, Send, Square, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AddToDashboard } from "@/components/add-to-dashboard";
import { RATE, Visual, kindsFor, type VisualKind } from "@/components/ask-visuals";
import { periodName } from "@/components/auto-chart";
import { streamNdjson } from "@/lib/api";
import type { Chart } from "@/lib/dashboards";
import { colLabel, pbi, type Result } from "@/lib/pbi";

const PERIOD = /:(year|quarter|month|week|day)$/;
const said = (ref: string) => (PERIOD.test(ref) ? ref.match(PERIOD)![1] : colLabel(ref).replace(/ · .*$/, ""));
const shown = (ref: string, v: unknown) => (PERIOD.test(ref) ? periodName(v) : String(v ?? "(blank)"));

/** The chart a thread is about. */
export type Anchor = { id: string; title: string; metrics: string[]; dims: string[]; filters: Record<string, unknown>; kind?: VisualKind };
/** A chart the analyst computed in a reply, kept small enough to keep. */
export type Shown = { metrics: string[]; by: string[]; filters: Record<string, unknown>; columns: string[]; rows: unknown[][] };
/** One turn of a thread. */
export type Said = { who: "you" | "analyst"; text: string; at: number; results?: Shown[]; error?: string };
/** Where a page keeps its threads, by the id of the chart each is on. */
export type ThreadStore = { get: (id: string) => Said[]; set: (id: string, said: Said[]) => void };

const OPEN = "chart-thread", SAVED = "chart-thread-saved";
export function openThread(anchor: Anchor, store: ThreadStore) {
  window.dispatchEvent(new CustomEvent(OPEN, { detail: { anchor, store } }));
}

/** Threads kept in this browser, for charts that live nowhere else (an answer on the Ask page): the latest 60. */
export function browserThreads(model: string): ThreadStore {
  const key = `studio.threads.${model}`;
  const all = (): Record<string, Said[]> => { try { return JSON.parse(localStorage.getItem(key) ?? "{}"); } catch { return {}; } };
  return {
    get: (id) => all()[id] ?? [],
    set: (id, s) => {
      const next = { ...all(), [id]: s };
      const kept = Object.fromEntries(Object.entries(next).sort(([, a], [, b]) => (b.at(-1)?.at ?? 0) - (a.at(-1)?.at ?? 0)).slice(0, 60));
      try { localStorage.setItem(key, JSON.stringify(kept)); } catch { /* full or blocked: the thread lasts the visit */ }
    },
  };
}

/** The form a chart is drawn in when nothing says: what Ask offers first for its columns. */
function formFor(metrics: string[], dims: string[], labels: Record<string, string>): VisualKind {
  if (!dims.length) return "number";
  return kindsFor(dims, (d) => PERIOD.test(d), !RATE.test(labels[metrics[0]] ?? metrics[0] ?? ""))[0]?.kind ?? "leaderboard";
}
// named by the measure it draws (the first: the others an analyst computes beside it are its parts, admits beside
// admits per 1,000)
const titleOf = (s: { metrics: string[]; by: string[] }, labels: Record<string, string>) =>
  `${labels[s.metrics[0]] ?? s.metrics[0] ?? ""}${s.by.length ? ` by ${s.by.map(said).join(" and ")}` : ""}`;
/** Now, in seconds: when a turn was said. */
const clock = () => Date.now() / 1000;
const scopeOf = (filters: Record<string, unknown>) => Object.entries(filters).map(([k, v]) => `${said(k)} ${shown(k, v)}`).join(", ");

/** The button on a chart: opens its thread, and says how many replies it has. */
export function ThreadButton({ anchor, store }: { anchor: () => Anchor; store: ThreadStore }) {
  const id = anchor().id;
  const [n, setN] = useState(0);
  useEffect(() => {
    const count = () => setN(store.get(id).filter((s) => s.who === "analyst").length);
    count();
    window.addEventListener(SAVED, count);
    return () => window.removeEventListener(SAVED, count);
  }, [id, store]);
  return (
    <button onClick={() => openThread(anchor(), store)} title={n ? `Open its thread (${n} ${n === 1 ? "reply" : "replies"})` : "Start a thread on this chart"}
      aria-label={n ? `Open its thread, ${n} ${n === 1 ? "reply" : "replies"}` : "Start a thread on this chart"}
      className={`flex h-7 items-center gap-1 rounded-lg px-1.5 text-[12px] ring-1 ${n ? "font-semibold text-[var(--d-teal)] ring-[var(--d-teal)]" : "text-[var(--d-ink-2)] ring-[var(--d-rule)] hover:text-[var(--d-teal)] hover:ring-[var(--d-teal)]"}`}>
      <MessagesSquare size={14} />{n > 0 && n}
    </button>
  );
}

/** The panel, mounted once on a page: opens on any chart's thread button. */
export function ThreadPanel({ model, labels }: { model: string; labels: Record<string, string> }) {
  const [trail, setTrail] = useState<{ anchor: Anchor; store: ThreadStore }[]>([]);
  const here = trail.at(-1);
  const [talk, setTalk] = useState<Said[]>([]);
  const [chart, setChart] = useState<Result | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const session = useRef("");  // a fresh conversation each time a thread opens, told what was said before
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const open = (e: Event) => setTrail([(e as CustomEvent).detail]);
    window.addEventListener(OPEN, open);
    return () => window.removeEventListener(OPEN, open);
  }, []);
  useEffect(() => {
    if (!here) return;
    abort.current?.abort();
    setTalk(here.store.get(here.anchor.id)); setInput(""); setChart(null); session.current = "";  // eslint-disable-line react-hooks/set-state-in-effect -- a thread opened
    pbi.compute(model, { metrics: here.anchor.metrics, by: here.anchor.dims, filters: here.anchor.filters }).then(setChart).catch(() => {});
  }, [here, model]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && here && !busy) setTrail([]); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [here, busy]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [talk]);

  const keep = useCallback((next: Said[]) => {
    if (!here) return;
    here.store.set(here.anchor.id, next);
    window.dispatchEvent(new Event(SAVED));
  }, [here]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || busy || !here) return;
    const a = here.anchor;
    const first = !session.current;
    if (first) session.current = `thread-${a.id}-${Math.round(clock())}`.replace(/[^\w-]/g, "-");
    // the chart the question is about, every time; and, when the thread reopens, what was said in it so far
    const about = `This question is about one chart, "${a.title}": ${a.metrics.map((m) => labels[m] ?? m).join(" and ")}`
      + `${a.dims.length ? ` by ${a.dims.map(said).join(" and ")}` : ", in total"}${Object.keys(a.filters).length ? `, for ${scopeOf(a.filters)}` : ""}.`
      + " Keep to what it shows unless asked to widen; compute what you need.";
    const before = first && talk.length ? `\nEarlier in this thread:\n${talk.slice(-6).map((s) => `${s.who === "you" ? "Q" : "A"}: ${s.text.slice(0, 500)}`).join("\n")}` : "";
    const mine: Said = { who: "you", text: q, at: clock() };
    let reply: Said = { who: "analyst", text: "", at: clock(), results: [] };
    const base = [...talk, mine];
    setTalk([...base, reply]); setInput(""); setBusy(true);
    const ctrl = new AbortController();
    abort.current = ctrl;
    const show = (r: Said) => { reply = r; setTalk([...base, r]); };
    try {
      await streamNdjson(pbi.askPath(model), { message: `${about}${before}\n\nThe question: ${q}`, session_id: session.current }, (e) => {
        if (e.type === "chunk") show({ ...reply, text: reply.text + (e.data.text ?? "") });
        else if (e.type === "agent_tool_call" && e.data.status === "started") show({ ...reply, text: "" });
        else if (e.type === "result") show({ ...reply, text: reply.text || e.data.answer || "" });
        else if (e.type === "error") show({ ...reply, error: e.data.message });
        else if (e.type === "pbi_result" && e.data.kind === "compute" && e.data.metrics?.length && e.data.rows?.length) {
          const d = e.data;
          show({ ...reply, results: [...(reply.results ?? []), { metrics: d.metrics, by: d.by ?? [], filters: d.filters ?? {}, columns: d.columns, rows: d.rows.slice(0, 60) }] });
        }
      }, ctrl.signal);
    } catch (err: any) {  // eslint-disable-line @typescript-eslint/no-explicit-any
      if (err?.name !== "AbortError") reply = { ...reply, error: String(err?.message ?? err) };
    } finally {
      // the charts that say something: a total of one row is in the words already; the last of each shape is kept
      const results = (reply.results ?? []).filter((r) => r.by.length && r.rows.length > 1);
      const done = { ...reply, results: results.filter((r, i) => !results.slice(i + 1).some((x) => titleOf(x, labels) === titleOf(r, labels))).slice(-3) };
      const next = [...base, ...(done.text || done.error || done.results.length ? [done] : [])];
      setTalk(next); keep(next); setBusy(false); abort.current = null;
    }
  };

  if (!here) return null;
  const a = here.anchor;
  const deeper = (r: Shown, i: number, at: number) => setTrail((t) => [...t, { anchor: {
    id: `${a.id}>${Math.round(at)}-${i}`, title: titleOf(r, labels), metrics: r.metrics.slice(0, 1), dims: r.by, filters: r.filters }, store: here.store }]);
  const tileOf = (r: Shown, at: number, i: number): Chart => ({ id: `th${Math.round(at)}${i}`, title: titleOf(r, labels), kind: "compute", at: clock(),
    metrics: r.metrics, by: r.by, filters: r.filters, chart: formFor(r.metrics, r.by, labels), dims: r.by });
  const starters = a.dims.length
    ? [`What's behind the highest one?`, `How has this moved over the last year?`, `Is anything here unusual for us?`]
    : [`What's behind the latest change?`, `Which part of it drives it most?`, `Is this unusual for us?`];

  return (
    <aside aria-label={`Thread on ${a.title}`} className="fixed inset-y-0 right-0 z-40 flex w-[min(480px,100vw)] flex-col border-l border-[var(--d-rule)] bg-[var(--d-sheet)] text-[var(--d-ink)] shadow-[-24px_0_60px_-30px_rgba(20,35,56,.45)]">
      <header className="shrink-0 border-b border-[var(--d-rule)] px-5 pb-3 pt-4">
        <div className="flex items-center gap-2">
          <MessagesSquare size={16} className="text-[var(--d-teal)]" />
          <span className="text-[12px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">Thread</span>
          <span className="flex-1" />
          <button onClick={() => { abort.current?.abort(); setTrail([]); }} aria-label="Close the thread" className="rounded-lg p-1.5 text-[var(--d-muted)] hover:bg-[var(--d-paper)] hover:text-[var(--d-ink)]"><X size={16} /></button>
        </div>
        {trail.length > 1 && (
          // the way back up, one chart at a time
          <nav aria-label="Threads above this one" className="mt-1.5 flex flex-wrap items-center gap-1 text-[12px] text-[var(--d-muted)]">
            {trail.slice(0, -1).map((t, i) => (
              <span key={t.anchor.id} className="flex items-center gap-1">
                <button onClick={() => setTrail((x) => x.slice(0, i + 1))} disabled={busy} className="max-w-[180px] truncate text-[var(--d-teal)] hover:underline disabled:opacity-50">{t.anchor.title}</button>
                <ChevronRight size={12} />
              </span>
            ))}
          </nav>
        )}
        <h2 className="cond mt-1 text-[17px] font-semibold leading-snug">{a.title}</h2>
        {Object.keys(a.filters).length > 0 && <p className="mt-0.5 text-[12.5px] text-[var(--d-muted)]">{scopeOf(a.filters)}</p>}
        <div className="mt-2 h-[150px]">
          {chart ? <Visual kind={a.kind ?? formFor(a.metrics, a.dims, labels)} data={[{ columns: chart.columns, rows: chart.rows }]} metrics={a.metrics} dims={a.dims} labels={labels} height={150} exact />
            : <div className="h-full animate-pulse rounded-xl bg-[var(--d-paper)]" />}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {!talk.length && (
          <div>
            <p className="text-[14px] text-[var(--d-ink-2)]">Ask anything about this chart. The analyst keeps to it, and each chart it brings back can be followed further.</p>
            <div className="mt-3 flex flex-col items-start gap-2">
              {starters.map((s) => <button key={s} onClick={() => send(s)} className="rounded-full bg-[var(--d-paper)] px-3.5 py-1.5 text-left text-[13.5px] ring-1 ring-[var(--d-rule)] hover:ring-[var(--d-teal)]">{s}</button>)}
            </div>
          </div>
        )}
        <ol className="space-y-5">
          {talk.map((s, k) => s.who === "you" ? (
            <li key={k} className="ml-10 rounded-2xl rounded-br-md bg-[var(--d-teal-soft)] px-3.5 py-2 text-[14px]">{s.text}</li>
          ) : (
            <li key={k}>
              <div className="text-[14.5px] leading-relaxed text-[var(--d-ink-2)] [&_p]:mb-2 [&_p:last-child]:mb-0 [&_strong]:font-semibold [&_strong]:text-[var(--d-ink)] [&_ul]:ml-5 [&_ul]:list-disc">
                {s.text ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{s.text}</ReactMarkdown>
                  : busy && k === talk.length - 1 ? <p className="pulse-soft flex items-center gap-2 text-[var(--d-muted)]"><Loader2 size={13} className="animate-spin" />Working on it…</p> : null}
              </div>
              {s.error && <p className="mt-2 rounded-lg bg-[var(--d-rise-soft)] px-3 py-2 text-[13px] text-[var(--d-rise-ink)]">{s.error}</p>}
              {(!busy || k < talk.length - 1) && s.results?.map((r, i) => (
                <figure key={i} className="mt-3 rounded-xl bg-[var(--d-paper)] p-3 ring-1 ring-[var(--d-rule)]">
                  <figcaption className="flex items-center gap-2">
                    <span className="cond min-w-0 flex-1 truncate text-[13.5px] font-semibold" title={titleOf(r, labels)}>{titleOf(r, labels)}</span>
                    <button onClick={() => deeper(r, i, s.at)} title="Follow this chart in a thread of its own"
                      className="flex h-7 items-center gap-1 rounded-lg px-2 text-[12px] font-medium text-[var(--d-teal)] ring-1 ring-[var(--d-rule)] hover:ring-[var(--d-teal)]">
                      <MessagesSquare size={13} />Follow</button>
                    <AddToDashboard model={model} charts={() => [tileOf(r, s.at, i)]} label="Add this chart to a dashboard" compact />
                  </figcaption>
                  {/* its scope, which may be narrower than the chart the thread is on (one month of it) */}
                  {Object.keys(r.filters).length > 0 && <p className="mt-0.5 text-[12px] text-[var(--d-muted)]">{scopeOf(r.filters)}</p>}
                  <Visual kind={formFor(r.metrics, r.by, labels)} data={[{ columns: r.columns, rows: r.rows }]} metrics={r.metrics} dims={r.by} labels={labels} height={190} exact />
                </figure>
              ))}
            </li>
          ))}
        </ol>
        <div ref={end} />
      </div>

      <form onSubmit={(e) => { e.preventDefault(); send(input); }} className="shrink-0 border-t border-[var(--d-rule)] p-3">
        <div className="flex items-center gap-2 rounded-2xl bg-[var(--d-paper)] px-3 py-2 ring-1 ring-[var(--d-rule)] focus-within:ring-[var(--d-teal)]">
          {/* the box's ring shows the focus; the desk's outline on the field itself would draw a box inside it */}
          <label className="min-w-0 flex-1"><span className="sr-only">Ask about this chart</span>
            <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about this chart" disabled={busy} style={{ outline: "none" }}
              className="w-full bg-transparent text-[14px] outline-none placeholder:text-[var(--d-muted)]" /></label>
          {busy ? <button type="button" onClick={() => abort.current?.abort()} aria-label="Stop" className="grid h-8 w-8 place-items-center rounded-full bg-[var(--d-ink)] text-[var(--d-sheet)]"><Square size={12} /></button>
            : <button type="submit" disabled={!input.trim()} aria-label="Send" className="grid h-8 w-8 place-items-center rounded-full bg-[var(--d-teal)] text-white disabled:opacity-40"><Send size={14} /></button>}
        </div>
      </form>
    </aside>
  );
}
