"use client";

/* A person's dashboards. Tabs for each, made, renamed and removed in place; the charts of the one open, laid out on a
   twelve-column grid, each re-run against today's numbers. "Arrange for me" lays them out as a reader takes them in:
   headline numbers first, then each measure's movement, ranking, drivers and detail, in full rows that share the
   screen's height, so the whole dashboard is seen at once (it scrolls only when its charts cannot fit). The
   charts are linked: a click on a bar or a cell filters every chart on the dashboard by it (a chart drawn by that
   column keeps every value and marks the picked one), and the filters stay with the dashboard. */

import { ArrowUpRight, GripVertical, LayoutDashboard, Loader2, MoreHorizontal, Pencil, Plus, RefreshCw, Sparkles, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { KIND_NAME, RATE, Visual, kindsFor, noteOf } from "@/components/ask-visuals";
import { ThreadButton, ThreadPanel, openThread, type Anchor, type ThreadStore } from "@/components/chart-thread";
import { periodName } from "@/components/auto-chart";
import { InvestigationExhibit } from "@/components/pbi-investigation";
import type { ExhibitData } from "@/components/pbi-exhibit";
import { LIMIT, MAIN, arrange, chartsOf, dashboardsOf, formOf, load, newDash, save, widthOf, type Chart, type Dash } from "@/lib/dashboards";
import { owner as me } from "@/lib/desk";
import { colLabel, columnTypes, pbi, type Result } from "@/lib/pbi";
import { useStudio } from "@/lib/studio";
import { SettingsMenu, useLook } from "@/lib/theme";

const PERIOD = /:(year|quarter|month|week|day)$/;
const said = (ref: string) => (PERIOD.test(ref) ? ref.match(PERIOD)![1] : colLabel(ref).replace(/ · .*$/, ""));
const shown = (ref: string, v: unknown) => (PERIOD.test(ref) ? periodName(v) : String(v ?? "(blank)"));
type Saved = Chart | Dash;

export function Dashboards() {
  const { pbiModel } = useStudio();
  const look = useLook();
  const router = useRouter();
  const params = useSearchParams();
  const [who, setWho] = useState("me");
  const [tiles, setTiles] = useState<Saved[] | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [dates, setDates] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [stamp, setStamp] = useState(0);  // a refresh re-runs every chart
  const [naming, setNaming] = useState<"new" | "rename" | null>(null);
  const [name, setName] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);

  useEffect(() => { setWho(me()); }, []);  // eslint-disable-line react-hooks/set-state-in-effect -- the browser's own name
  useEffect(() => {
    if (!pbiModel || who === "me") return;
    load(pbiModel, who).then(setTiles).catch((e) => setError(String(e?.message ?? e)));
    pbi.metrics(pbiModel).then((r) => setLabels(Object.fromEntries(r.metrics.map((m) => [m.key, m.label])))).catch(() => {});
    pbi.tables(pbiModel).then((t) => setDates(columnTypes(t.tables).dates)).catch(() => {});
  }, [pbiModel, who]);

  const dashes = useMemo(() => (tiles ? dashboardsOf(tiles) : []), [tiles]);
  const active = dashes.find((d) => d.id === params.get("d")) ?? dashes[0];
  const charts = useMemo(() => (tiles && active ? chartsOf(tiles, active.id) : []), [tiles, active]);
  const filters = active?.filters ?? {};
  // an arranged dashboard is a grid that fits the screen: its rows (by the widths, twelve columns to a row) share the
  // height, a row of headline numbers no taller than it needs, rows of charts the rest, each kept tall enough to read
  const fit = useMemo(() => {
    if (!active?.arranged || !charts.length) return "";
    const rows: boolean[] = []; let used = 12;
    for (const c of charts) {
      if (used + widthOf(c) > 12) { rows.push(true); used = 0; }
      used += widthOf(c); if (c.chart !== "number") rows[rows.length - 1] = false;
    }
    return rows.map((number) => (number ? "minmax(158px, 174px)" : "minmax(200px, 1fr)")).join(" ");
  }, [active?.arranged, charts]);

  const commit = useCallback(async (next: Saved[]) => {
    setTiles(next); setError("");
    try { await save(pbiModel, who, next); } catch (e: any) { setError(String(e?.message ?? e)); }  // eslint-disable-line @typescript-eslint/no-explicit-any
  }, [pbiModel, who]);
  const go = (id: string) => router.replace(`/pbi/board?d=${encodeURIComponent(id)}`);
  /** The dashboard's own record, changed (made when "My dashboard" has none yet). */
  const setDash = (change: Partial<Dash>) => {
    if (!tiles || !active) return;
    const has = tiles.some((t) => t.kind === "dashboard" && (t as Dash).id === active.id);
    commit(has ? tiles.map((t) => (t.kind === "dashboard" && (t as Dash).id === active.id ? { ...t, ...change } : t)) : [...tiles, { ...active, ...change }]);
  };
  const setChart = (id: string, change: Partial<Chart>) => tiles && commit(tiles.map((t) => (t.id === id ? { ...t, ...change } as Saved : t)));
  // a chart's threads live on its tile; written to the tiles as they are when a reply lands, not as they were when
  // the thread opened (the dashboard may have changed since)
  const latest = useRef(tiles);
  useEffect(() => { latest.current = tiles; }, [tiles]);
  const threadsOf = useCallback((chartId: string): ThreadStore => ({
    get: (id) => (latest.current?.find((t) => t.id === chartId) as Chart | undefined)?.threads?.[id] ?? [],
    set: (id, said) => {
      const now = latest.current;
      if (now) commit(now.map((t) => (t.id === chartId ? { ...t, threads: { ...(t as Chart).threads, [id]: said } } as Saved : t)));
    },
  }), [commit]);
  const remove = (id: string) => tiles && commit(tiles.filter((t) => t.id !== id));
  const pick = (dim: string, value: unknown) => setDash({ filters: { ...filters, [dim]: value } });
  const unpick = (dim: string) => { const f = { ...filters }; delete f[dim]; setDash({ filters: f }); };

  const create = () => {
    if (!tiles || !name.trim()) return;
    const d = newDash(tiles, name);
    commit([...tiles, d]); setNaming(null); setName(""); go(d.id);
  };
  const rename = () => { if (name.trim()) setDash({ title: name.trim() }); setNaming(null); setName(""); };
  const drop = () => {
    if (!tiles || !active) return;
    commit(tiles.filter((t) => !(t.kind === "dashboard" && (t as Dash).id === active.id) && !((t as Chart).dash === active.id || (active.id === MAIN && !(t as Chart).dash && t.kind !== "dashboard"))));
    setDeleting(false); go(MAIN);
  };
  const tidy = () => {
    if (!tiles || !active) return;
    const room = LIMIT - tiles.length;
    const laid = arrange(charts, labels, room);
    const others = tiles.filter((t) => !charts.some((c) => c.id === t.id));
    // one save: the charts laid out, and the dashboard marked as arranged (its record made if it had none)
    const has = others.some((t) => t.kind === "dashboard" && (t as Dash).id === active.id);
    const marked = has ? others.map((t) => (t.kind === "dashboard" && (t as Dash).id === active.id ? { ...t, arranged: true } : t)) : [...others, { ...active, arranged: true }];
    commit([...marked, ...laid]);
  };
  const move = (from: string, to: string) => {
    if (!tiles || from === to) return;
    const order = charts.map((c) => c.id), i = order.indexOf(from), j = order.indexOf(to);
    order.splice(i, 1); order.splice(j, 0, from);
    commit(tiles.map((t) => (order.includes(t.id) ? { ...t, pos: order.indexOf(t.id) + 1 } as Saved : t)));
  };

  return (
    <div className={`desk ${look.look} flex h-screen flex-col bg-[var(--d-ground)] text-[var(--d-ink)]`}>
      <header className="shrink-0 border-b border-[var(--d-rule)] bg-[var(--d-sheet)] px-6">
        <div className="flex h-14 items-center gap-3">
          <LayoutDashboard size={18} className="text-[var(--d-teal)]" />
          <span className="cond text-[19px] font-semibold">Dashboards</span>
          <span className="flex-1" />
          <button onClick={() => setStamp(Date.now())} className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px] text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]">
            <RefreshCw size={14} />Today&rsquo;s numbers</button>
          <SettingsMenu look={look} />
        </div>
        {/* the dashboards, as tabs: one open, a new one named in place */}
        <nav aria-label="Dashboards" className="-mb-px flex flex-wrap items-end gap-1">
          {dashes.map((d) => (
            <button key={d.id} onClick={() => go(d.id)} aria-current={d.id === active?.id ? "page" : undefined}
              className={`border-b-2 px-3.5 pb-2.5 pt-1 text-[14px] ${d.id === active?.id ? "border-[var(--d-teal)] font-semibold text-[var(--d-ink)]" : "border-transparent text-[var(--d-ink-2)] hover:text-[var(--d-ink)]"}`}>
              {d.title}<span className="ml-1.5 text-[12px] font-normal text-[var(--d-muted)]">{tiles ? chartsOf(tiles, d.id).length : ""}</span>
            </button>
          ))}
          {naming === "new" ? (
            <form onSubmit={(e) => { e.preventDefault(); create(); }} className="flex items-center gap-1.5 pb-1.5">
              <label><span className="sr-only">Name the new dashboard</span>
                <input autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Escape" && setNaming(null)}
                  placeholder="Name it" className="w-44 rounded-lg bg-[var(--d-paper)] px-2.5 py-1 text-[13.5px] outline-none ring-1 ring-[var(--d-rule)] focus:ring-[var(--d-teal)]" /></label>
              <button type="submit" disabled={!name.trim()} className="rounded-lg bg-[var(--d-teal)] px-2.5 py-1 text-[13px] font-semibold text-white disabled:opacity-40">Create</button>
            </form>
          ) : (
            <button onClick={() => { setNaming("new"); setName(""); }} className="flex items-center gap-1 px-3 pb-2.5 pt-1 text-[13.5px] text-[var(--d-teal)] hover:underline"><Plus size={14} />New dashboard</button>
          )}
        </nav>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className={`mx-auto max-w-[1280px] px-8 py-6 ${fit ? "flex min-h-full flex-col" : ""}`}>
          {error && <p role="alert" className="mb-4 rounded-lg bg-[var(--d-rise-soft)] px-4 py-2 text-[13px] text-[var(--d-rise-ink)]">{error}</p>}
          {!tiles ? (
            <p className="flex items-center gap-2 text-[14px] text-[var(--d-muted)]"><Loader2 size={15} className="animate-spin" />Opening your dashboards…</p>
          ) : active && (
            <>
              <div className="mb-5 flex flex-wrap items-center gap-2">
                {naming === "rename" ? (
                  <form onSubmit={(e) => { e.preventDefault(); rename(); }} className="flex items-center gap-2">
                    <label><span className="sr-only">Rename the dashboard</span>
                      <input autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Escape" && setNaming(null)}
                        className="cond w-72 rounded-lg bg-[var(--d-paper)] px-3 py-1.5 text-[22px] font-semibold outline-none ring-1 ring-[var(--d-teal)]" /></label>
                    <button type="submit" className="rounded-lg bg-[var(--d-teal)] px-3 py-1.5 text-[13px] font-semibold text-white">Save</button>
                  </form>
                ) : (
                  <h1 className="cond text-[24px] font-semibold">{active.title}
                    <button onClick={() => { setNaming("rename"); setName(active.title); }} aria-label="Rename this dashboard" className="ml-2 align-middle text-[var(--d-muted)] hover:text-[var(--d-ink)]"><Pencil size={15} /></button>
                  </h1>
                )}
                <span className="flex-1" />
                {charts.length > 1 && (
                  <button onClick={tidy} className="flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[13px] font-semibold text-[var(--d-teal)] ring-1 ring-[var(--d-teal)] hover:bg-[var(--d-teal-soft)]">
                    <Sparkles size={14} />Arrange for me</button>
                )}
                {active.id !== MAIN && (deleting ? (
                  <span className="flex items-center gap-2 text-[13px]">Delete {active.title} and its charts?
                    <button onClick={drop} className="rounded-full bg-[var(--d-rise-ink)] px-3 py-1 font-semibold text-white">Delete</button>
                    <button onClick={() => setDeleting(false)} className="text-[var(--d-ink-2)] hover:underline">Keep it</button></span>
                ) : (
                  <button onClick={() => setDeleting(true)} aria-label="Delete this dashboard" className="rounded-full p-2 text-[var(--d-muted)] hover:bg-[var(--d-paper)] hover:text-[var(--d-rise-ink)]"><Trash2 size={15} /></button>
                ))}
              </div>

              {/* the filters on the whole dashboard: picked on any chart, applied to all */}
              {Object.keys(filters).length > 0 && (
                <div className="mb-5 flex flex-wrap items-center gap-2 text-[13px]">
                  <span className="text-[var(--d-muted)]">Every chart filtered to</span>
                  {Object.entries(filters).map(([k, v]) => (
                    <span key={k} className="flex items-center gap-1 rounded-full bg-[var(--d-navy)] py-1 pl-3 pr-1.5 font-semibold text-white">
                      {said(k)}: {shown(k, v)}
                      <button onClick={() => unpick(k)} aria-label={`Stop filtering on ${said(k)}`} className="rounded-full p-0.5 opacity-75 hover:opacity-100"><X size={12} /></button>
                    </span>
                  ))}
                  <button onClick={() => setDash({ filters: {} })} className="text-[var(--d-teal)] hover:underline">Clear</button>
                </div>
              )}

              {!charts.length ? (
                <div className="rounded-2xl border border-dashed border-[var(--d-rule)] px-8 py-14 text-center">
                  <LayoutDashboard size={28} className="mx-auto text-[var(--d-muted)]" />
                  <p className="cond mt-3 text-[18px] font-semibold">Nothing on {active.title} yet</p>
                  <p className="mx-auto mt-1 max-w-md text-[14px] text-[var(--d-muted)]">Every chart on the Ask page has <b>Add to dashboard</b>. Pick this one, or start a new dashboard from there.</p>
                  <Link href="/pbi/ask" className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-[var(--d-teal)] px-4 py-2 text-[13.5px] font-semibold text-white">Ask a question<ArrowUpRight size={14} /></Link>
                </div>
              ) : (
                <div className={`grid grid-cols-1 gap-4 lg:grid-cols-12 ${fit ? "flex-1 lg:[grid-template-rows:var(--rows)]" : ""}`}
                  style={fit ? { "--rows": fit } as React.CSSProperties : undefined}>
                  {charts.map((c) => (
                    <Card key={c.id} chart={c} model={pbiModel} labels={labels} dates={dates} filters={filters} stamp={stamp} dashes={dashes} activeId={active.id}
                      fit={!!fit} threads={threadsOf(c.id)} onPick={pick} onChange={(x) => setChart(c.id, x)} onRemove={() => remove(c.id)} onAsk={(q) => router.push(`/pbi/ask?q=${encodeURIComponent(q)}`)}
                      drag={{ dragging, start: () => setDragging(c.id), end: () => setDragging(null), over: () => dragging && move(dragging, c.id) }} />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </main>
      <ThreadPanel model={pbiModel} labels={labels} />
    </div>
  );
}

type Drag = { dragging: string | null; start: () => void; end: () => void; over: () => void };

/** One chart on a dashboard: re-run under the dashboard's filters, its kind switchable, its size and place its own. */
function Card({ chart: c, model, labels, dates, filters, stamp, dashes, activeId, fit, threads, onPick, onChange, onRemove, onAsk, drag }: {
  chart: Chart; model: string; labels: Record<string, string>; dates: string[]; filters: Record<string, unknown>; stamp: number; dashes: Dash[]; activeId: string;
  /** drawn to the height of its place in a fitted grid, not its own */
  fit: boolean;
  threads: ThreadStore;
  onPick: (dim: string, value: unknown) => void; onChange: (x: Partial<Chart>) => void; onRemove: () => void; onAsk: (q: string) => void; drag: Drag;
}) {
  const [run, setRun] = useState<{ data?: Result; series?: Result; month?: string; exhibit?: ExhibitData; error?: string; loose?: string[] } | null>(null);
  const [menu, setMenu] = useState(false);
  const menuBox = useRef<HTMLDivElement>(null);
  const plot = useRef<HTMLDivElement>(null);
  const [tall, setTall] = useState(0);
  const [spill, setSpill] = useState(0);  // what a tile shows beside its chart (a key, a legend, a number), taken off its height
  useEffect(() => {
    if (!fit || !plot.current) return;
    const room = plot.current, box = room.firstElementChild as HTMLElement;
    // measured, not added up: the tile's content less the chart's own canvas, the same at any height it is drawn; again
    // whenever the room changes or the chart (loaded after the tile) appears
    const measure = () => {
      setTall(Math.floor(room.clientHeight));
      const canvas = box.querySelector<HTMLElement>("[_echarts_instance_]");
      if (!canvas) return;
      const drawn = Math.max(0, ...[...box.children].map((e) => (e as HTMLElement).offsetTop + (e as HTMLElement).offsetHeight));
      const extra = Math.max(0, drawn - canvas.offsetHeight);
      setSpill((s) => (Math.abs(extra - s) > 2 && extra < room.clientHeight / 2 ? extra : s));
    };
    const ro = new ResizeObserver(measure), mo = new MutationObserver(measure);
    ro.observe(room); mo.observe(box, { childList: true, subtree: true });
    return () => { ro.disconnect(); mo.disconnect(); };
  }, [fit]);
  const form = formOf(c, labels), dims = form.dims;
  const own = new Set(dims.map((d) => d.split(":")[0]));
  // the dashboard's filters, but for those on a column the chart is drawn by: it keeps every value and marks the pick
  const theirs = Object.fromEntries(Object.entries(filters).filter(([k]) => !own.has(k.split(":")[0])));
  const picked = Object.fromEntries(Object.entries(filters).filter(([k]) => own.has(k.split(":")[0])));
  const key = JSON.stringify([c.metrics, dims, c.filters, theirs, c.explain, stamp, dates.length]);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        if (c.kind === "explain" && c.explain) {
          const r = await pbi.explain(model, { ...c.explain, filters: { ...(c.explain.filters ?? {}), ...theirs } });
          if (live) setRun({ exhibit: { kind: "explain", explain: r.explain, columns: [], rows: [], metrics: [c.explain.metric], by: [], filters: c.explain.filters ?? {}, labels: {}, origin: "agent" } });
          return;
        }
        const body = { metrics: c.metrics ?? [], by: dims, filters: { ...(c.filters ?? {}), ...theirs } };
        // a headline is the latest month (with its months behind it, and the change on the one before), not an
        // average of every month there is
        if (c.chart === "number" && !dims.length && c.metrics?.[0]) {
          const cols = await pbi.metric(model, c.metrics[0]).then((d) => d.columns ?? []).catch(() => [] as string[]);
          const date = cols.find((x) => dates.includes(x));
          if (date) {
            const P = `${date}:month`, period = Object.keys(body.filters).find((k) => k.startsWith(`${date}:`));
            const series = await pbi.compute(model, { ...body, by: [P], filters: Object.fromEntries(Object.entries(body.filters).filter(([k]) => k !== period)) });
            const rows = [...series.rows].sort((a, b) => String(a[0]).localeCompare(String(b[0])));
            const last = rows.at(-1);
            if (last && live) {
              setRun({ data: { ...series, columns: series.columns.slice(1), rows: [last.slice(1)] }, series: { ...series, rows: rows.slice(-13) }, month: String(last[0]) });
              return;
            }
          }
        }
        try {
          const r = await pbi.compute(model, body);
          if (live) setRun({ data: r });
        } catch {  // a filter on a column this measure does not reach: drawn without it, and said so
          const r = await pbi.compute(model, { ...body, filters: c.filters ?? {} });
          if (live) setRun({ data: r, loose: Object.keys(theirs) });
        }
      } catch (e: any) { if (live) setRun({ error: String(e?.message ?? e) }); }  // eslint-disable-line @typescript-eslint/no-explicit-any
    })();
    return () => { live = false; };
  }, [key]);  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!menu) return;
    const away = (e: MouseEvent) => { if (menuBox.current && !menuBox.current.contains(e.target as Node)) setMenu(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [menu]);

  const kind = form.chart;
  // what its thread is about: the chart as drawn now, under the dashboard's filters
  const anchor = (): Anchor => c.kind === "explain" && c.explain
    ? { id: c.id, title: c.title, metrics: [c.explain.metric], dims: [], filters: { ...(c.explain.filters ?? {}), ...filters } }
    : { id: c.id, title: c.title, metrics: c.metrics ?? [], dims, filters: { ...(c.filters ?? {}), ...filters }, kind };
  const additive = !RATE.test(labels[c.metrics?.[0] ?? ""] ?? c.metrics?.[0] ?? "");
  const kinds = c.kind === "explain" ? [] : kindsFor(dims, (d) => PERIOD.test(d), additive);
  const w = widthOf({ ...c, ...form }), span = { 3: "lg:col-span-3", 4: "lg:col-span-4", 6: "lg:col-span-6", 8: "lg:col-span-8", 12: "lg:col-span-12" }[w] ?? "lg:col-span-6";
  const data = run?.data ? [{ columns: run.data.columns, rows: run.data.rows }, ...(run.series ? [{ columns: run.series.columns, rows: run.series.rows }] : [])] : null;
  const shownDims = run?.series ? [run.series.columns[0]] : dims;
  const note = run?.month ? `Latest month, ${periodName(run.month)}` : data && c.kind !== "explain" ? noteOf(kind, data, c.metrics ?? [], dims, labels) : null;

  return (
    <section draggable onDragStart={drag.start} onDragEnd={drag.end} onDragOver={(e) => { e.preventDefault(); drag.over(); }}
      className={`group relative flex min-w-0 flex-col rounded-2xl bg-[var(--d-sheet)] p-4 ring-1 ring-[var(--d-rule)] transition-opacity ${span} ${drag.dragging === c.id ? "opacity-40" : ""}`}>
      <header className="flex items-center gap-2">
        <GripVertical size={14} aria-hidden className="shrink-0 cursor-grab text-[var(--d-muted)] opacity-0 group-hover:opacity-100" />
        <h3 className="cond min-w-0 flex-1 truncate text-[15px] font-semibold" title={c.title}>{c.title}</h3>
        {kinds.length > 1 && (
          <span role="group" aria-label="Show as" className="hidden overflow-hidden rounded-lg ring-1 ring-[var(--d-rule)] group-hover:flex">
            {kinds.map((k) => (
              <button key={k.kind} aria-pressed={k.kind === kind} onClick={() => k.kind !== kind && onChange({ chart: k.kind, dims: k.dims, by: k.dims })}
                className={`px-2 py-0.5 text-[11.5px] ${k.kind === kind ? "bg-[var(--d-navy)] font-semibold text-white" : "text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]"}`}>{KIND_NAME[k.kind]}</button>
            ))}
          </span>
        )}
        <ThreadButton anchor={anchor} store={threads} />
        <div ref={menuBox} className="relative">
          <button onClick={() => setMenu((m) => !m)} aria-label="Chart options" aria-expanded={menu} className="grid h-7 w-7 place-items-center rounded-lg text-[var(--d-muted)] hover:bg-[var(--d-paper)] hover:text-[var(--d-ink)]"><MoreHorizontal size={16} /></button>
          {menu && (
            <div role="menu" className="absolute right-0 top-full z-20 mt-1 w-56 rounded-xl bg-[var(--d-sheet)] p-1.5 text-[13.5px] shadow-[0_20px_50px_-20px_rgba(20,35,56,.45)] ring-1 ring-[var(--d-rule)]">
              <p className="px-2.5 pb-1 pt-1 text-[11.5px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">Size</p>
              <div className="flex gap-1 px-1.5 pb-1.5">
                {[["Small", 4], ["Half", 6], ["Wide", 8], ["Full", 12]].map(([l, n]) => (
                  <button key={l} onClick={() => { onChange({ w: n as number }); setMenu(false); }}
                    className={`flex-1 rounded-md px-1 py-1 text-[12px] ${w === n ? "bg-[var(--d-navy)] text-white" : "ring-1 ring-[var(--d-rule)] hover:bg-[var(--d-paper)]"}`}>{l}</button>
                ))}
              </div>
              {dashes.filter((d) => d.id !== activeId).length > 0 && <p className="px-2.5 pb-1 pt-1.5 text-[11.5px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">Move to</p>}
              {dashes.filter((d) => d.id !== activeId).map((d) => (
                <button key={d.id} role="menuitem" onClick={() => { onChange({ dash: d.id, pos: 1e6 }); setMenu(false); }} className="block w-full truncate rounded-lg px-2.5 py-1.5 text-left hover:bg-[var(--d-paper)]">{d.title}</button>
              ))}
              <div className="my-1 border-t border-[var(--d-rule)]" />
              <button role="menuitem" onClick={() => { openThread(anchor(), threads); setMenu(false); }} className="block w-full rounded-lg px-2.5 py-1.5 text-left hover:bg-[var(--d-paper)]">Open its thread</button>
              <button role="menuitem" onClick={onRemove} className="block w-full rounded-lg px-2.5 py-1.5 text-left text-[var(--d-rise-ink)] hover:bg-[var(--d-rise-soft)]">Remove from dashboard</button>
            </div>
          )}
        </div>
      </header>
      {note && <p className={`mt-2 text-[13px] leading-snug text-[var(--d-ink-2)] ${fit ? "line-clamp-1" : ""}`} title={fit ? note : undefined}>{note}</p>}
      {run?.loose?.length ? <p className="mt-1 text-[12px] text-[var(--d-muted)]">Not filtered by {run.loose.map(said).join(", ")}: this measure does not reach it.</p> : null}
      {/* in a fitted grid the chart is drawn into the room its row leaves, and never pushes the row taller */}
      <div ref={plot} className={`mt-2 min-h-0 flex-1 ${fit ? "relative" : ""}`}>
        <div className={fit ? `absolute inset-0 ${run?.exhibit ? "overflow-auto" : "overflow-hidden"}` : ""}>
        {!run ? <div className="h-48 animate-pulse rounded-xl bg-[var(--d-paper)]" />
          : run.error ? <p className="py-6 text-[13px] text-[var(--d-rise-ink)]">Could not run it today: {run.error}</p>
          : run.exhibit ? <InvestigationExhibit e={run.exhibit} n={0} lean actions={{ drill: () => {}, rows: () => {}, ask: onAsk }} />
          : data && !data[0].rows.length ? <p className="py-6 text-[13px] text-[var(--d-muted)]">Nothing here for these filters.</p>
          : data && <Visual kind={kind} data={data} metrics={c.metrics ?? []} dims={shownDims} labels={labels} picked={picked} height={fit && tall > 60 ? tall - spill : kind === "number" ? 120 : 260} exact={fit}
              onPick={onPick} />}
        </div>
      </div>
    </section>
  );
}
