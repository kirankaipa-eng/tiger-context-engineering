"use client";

/* An exhibit on the Power BI desk: one computed answer, with its headline (the latest period and its
   change, or the top value), the chart that fits its shape, the table, and how it was built (the
   captured definition and the SQL). Clicking a mark opens the drill menu: break that value down by
   another column, see its trend, open the rows behind it, or ask about it. */

import { Check, Pin, TriangleAlert, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { AutoChart, DESK_PALETTE, blank, isRatio, pctText, periodName, type ChartKind } from "./auto-chart";
import { ResultTable, num } from "./pbi";
import { colLabel, isDimension, pbi, splitRef, type PbiMetricDetail } from "@/lib/pbi";

export type ExhibitData = {
  kind: "compute" | "explore" | "explain";
  /** An investigation (kind explain): why a measure changed, see pbi-investigation. */
  explain?: import("./pbi-investigation").Investigation; columns: string[]; rows: unknown[][]; metrics: string[]; by: string[];
  filters: Record<string, unknown>; conditions?: string[]; sql?: string; ms?: number; warehouse?: string;
  labels: Record<string, string>; origin: "agent" | "drill"; question?: string; table?: string;
  /** The rows behind a measure's value: how many, what they add up to, and the value they explain. */
  receipt?: Receipt;
  /** A drill-down: the value it breaks down. */
  parent?: { label: string; value: unknown; within: string };
};

export type Receipt = {
  metric: string; label: string; row_count: number | null; totals: { key: string; label: string; value: number | null }[];
  exact: boolean; why: string | null; conditions: string[]; shown: number; expected?: unknown; within: string;
};

/** The exhibit's filters with the clicked mark's values added. */
export function narrowed(e: ExhibitData, values: unknown[]) {
  const f: Record<string, unknown> = { ...e.filters };
  e.by.forEach((d, i) => { if (values[i] !== undefined) f[d] = values[i]; });
  return f;
}

/** The first measure's value at a mark (the row whose columns are its values). */
export function valueAt(e: ExhibitData, values: unknown[], metric = e.metrics[0]) {
  const iM = e.columns.indexOf(metric);
  const idx = e.by.map((d) => e.columns.indexOf(d));
  const row = e.rows.find((r) => idx.every((j, i) => values[i] === undefined || r[j] === values[i]));
  return row && iM >= 0 ? row[iM] : undefined;
}

const close = (a: number, b: number) => Math.abs(a - b) <= Math.max(1e-6, Math.abs(b) * 0.005);

export type DeskActions = {
  drill: (e: ExhibitData, values: unknown[], by: string) => void;
  rows: (e: ExhibitData, values: unknown[]) => void;
  ask: (text: string) => void;
  /** Pin the exhibit's question to the person's dashboard (it is re-run there, live). */
  pin?: (e: ExhibitData) => void;
  pinned?: (e: ExhibitData) => boolean;
};

const PERIOD = /:(year|quarter|month|week|day)$/;
const pct = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(1)}%`;

export function exhibitTitle(e: ExhibitData) {
  const names = e.metrics.map((m) => e.labels[m] ?? m);
  const what = !names.length ? "Transactions" : names.length <= 2 ? names.join(" and ") : `${names[0]} and ${names.length - 1} more`;
  const by = (d: string) => PERIOD.test(d) ? d.match(PERIOD)![1] : colLabel(d).replace(/ · .*$/, "").toLowerCase();
  return e.by.length ? `${what} by ${e.by.map(by).join(" and ")}` : what;
}

/** One row of numbers as figures, each in its own units. */
function Tiles({ e }: { e: ExhibitData }) {
  const row = e.rows[0];
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-3">
      {e.metrics.map((k) => {
        const v = row[e.columns.indexOf(k)], label = e.labels[k] ?? k;
        return (
          <div key={k} className="rounded-xl border border-[var(--d-rule)] px-4 py-3.5">
            <div className="text-[13px] leading-snug text-[var(--d-muted)]">{label}</div>
            <div className="num cond mt-1.5 text-[28px] font-semibold leading-none text-[var(--d-ink)]">{isRatio(label, [v]) ? pctText(v) : num(v)}</div>
          </div>
        );
      })}
    </div>
  );
}

function Headline({ e }: { e: ExhibitData }) {
  const m = e.metrics[0];
  if (!m || e.kind === "explore" && !e.metrics.length) return null;
  const iM = e.columns.indexOf(m);
  if (!e.by.length) {
    return (
      <div className="self-center">
        {e.metrics.map((k) => (
          <div key={k} className="mb-3">
            <div className="text-[13px] text-[var(--d-muted)]">{e.labels[k] ?? k}</div>
            <div className="num text-[52px] font-semibold leading-none text-[var(--d-ink)]">{num(e.rows[0]?.[e.columns.indexOf(k)])}</div>
          </div>
        ))}
      </div>
    );
  }
  const i0 = e.columns.indexOf(e.by[0]);
  if (e.by.length === 1 && PERIOD.test(e.by[0]) && e.rows.length) {
    const rows = [...e.rows].sort((a, b) => String(a[i0]).localeCompare(String(b[i0])));
    const lastRow = rows[rows.length - 1], prev = rows[rows.length - 2];
    const v = Number(lastRow[iM] ?? 0), p = prev ? Number(prev[iM] ?? 0) : null;
    const ratio = isRatio(e.labels[m] ?? m, rows.map((r) => r[iM]));
    return (
      <div className="self-center">
        <div className="text-[13px] text-[var(--d-muted)]">{e.labels[m] ?? m}, {periodName(lastRow[i0])}</div>
        <div className="num text-[52px] font-semibold leading-none text-[var(--d-ink)]">{ratio ? pctText(v) : num(v)}</div>
        {p !== null && (ratio ? <div className="mt-3 text-[15px] text-[var(--d-muted)]">{pctText(p)} in {periodName(prev![i0])}</div>
          : p !== 0 && <div className="mt-3 text-[15px]"><span className="num font-semibold" style={{ color: v >= p ? "var(--d-teal)" : "var(--d-rise)" }}>{pct(v / p - 1)}</span> <span className="text-[var(--d-muted)]">vs {periodName(prev![i0])}</span></div>)}
      </div>
    );
  }
  if (e.by.length === 1 && e.rows.length) {
    const rows = [...e.rows].sort((a, b) => Number(b[iM] ?? 0) - Number(a[iM] ?? 0));
    const top = rows[0], low = rows[rows.length - 1];
    return (
      <div className="self-center">
        <div className="text-[13px] text-[var(--d-muted)]">Highest {e.labels[m] ?? m}</div>
        <div className="cond mt-0.5 truncate text-[22px] font-semibold text-[var(--d-ink)]" title={blank(top[i0])}>{blank(top[i0])}</div>
        <div className="num text-[40px] font-semibold leading-none text-[var(--d-ink)]">{num(top[iM])}</div>
        {rows.length > 1 && <div className="mt-3 text-[13px] text-[var(--d-muted)]">Lowest: <span className="font-semibold text-[var(--d-ink-2)]">{blank(low[i0])}</span> {num(low[iM])}<br />{rows.length} {colLabel(e.by[0]).replace(/ · .*$/, "").toLowerCase()} values</div>}
      </div>
    );
  }
  return (
    <div className="self-center text-[13px] text-[var(--d-muted)]">
      <div className="num text-[40px] font-semibold leading-none text-[var(--d-ink)]">{e.rows.length}</div>
      combinations of {e.by.map((d) => colLabel(d).replace(/ · .*$/, "").toLowerCase()).join(" and ")}
    </div>
  );
}

export function PbiExhibit({ e, n, model, actions, landed, dates, types }: { e: ExhibitData; n: number; model: string; actions: DeskActions; landed?: boolean; dates: string[]; types: Record<string, string> }) {
  const [tab, setTab] = useState<"chart" | "table" | "how">(e.metrics.length && e.rows.length ? "chart" : "table");
  const [kind, setKind] = useState<ChartKind | undefined>(undefined);
  const [menu, setMenu] = useState<{ x: number; y: number; values: unknown[] } | null>(null);
  const [detail, setDetail] = useState<PbiMetricDetail | null>(null);
  const box = useRef<HTMLDivElement | null>(null);
  const m = e.metrics[0];
  useEffect(() => { if (e.kind === "compute" && m) pbi.metric(model, m).then(setDetail).catch(() => {}); }, [model, m, e.kind]);

  const used = useMemo(() => [...e.by, ...Object.keys(e.filters)].map((d) => d.replace(/:[a-z]+$/, "")), [e]);
  const calendars = new Set(dates.map((d) => splitRef(d).table));  // its months and weeks are the trend, not a split
  const next = (detail?.columns ?? []).filter((c) => !calendars.has(splitRef(c).table) && isDimension(c, types[c]) && !used.includes(c)).slice(0, 12);
  const trend = (detail?.columns ?? []).filter((c) => dates.includes(c) && !used.includes(c)).slice(0, 1);
  const chartable = e.metrics.length > 0 && e.by.length <= 2 && e.rows.length > 0;
  const single = chartable && e.rows.length === 1;  // one row of numbers: figures, never a chart
  const showHead = chartable && (e.by.length <= 1);
  const filters = Object.entries(e.filters);

  return (
    <section id={`exhibit-${n}`} className={`rounded-xl border border-[var(--d-rule)] bg-[var(--d-sheet)] shadow-[0_1px_2px_rgba(20,35,56,.06)] ${landed ? "landed" : ""}`}>
      <div className="flex flex-wrap items-start gap-3 border-b border-[var(--d-rule)] px-6 pb-3 pt-4">
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">{n ? `Exhibit ${n} · ` : ""}{e.origin === "drill" ? (e.receipt ? "the transactions" : Object.keys(e.filters).length ? "drill-down" : "from the measure") : "answer"}</div>
          <h2 className="cond text-[21px] font-semibold leading-snug text-[var(--d-ink)]">{e.receipt ? `The rows behind ${e.receipt.label}` : exhibitTitle(e)}</h2>
          {e.parent && <div className="mt-0.5 text-[13px] text-[var(--d-ink-2)]">Breaking down {e.parent.label} for {e.parent.within}: <span className="num font-semibold text-[var(--d-ink)]">{num(e.parent.value)}</span></div>}
          {(filters.length > 0 || (e.conditions?.length ?? 0) > 0) && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {filters.map(([k, v]) => <span key={k} className="rounded-full bg-[var(--d-teal-soft)] px-2.5 py-0.5 text-[12px] text-[var(--d-teal)]">{PERIOD.test(k) ? k.match(PERIOD)![1][0].toUpperCase() + k.match(PERIOD)![1].slice(1) : colLabel(k).replace(/ · .*$/, "")}: <b>{Array.isArray(v) ? v.map((x) => periodName(x)).join(", ") : periodName(v)}</b></span>)}
              {e.conditions?.map((c) => <span key={c} className="rounded-full bg-[var(--d-paper)] px-2.5 py-0.5 font-mono text-[11px] text-[var(--d-ink-2)]">{c}</span>)}
            </div>
          )}
        </div>
        <span className={`rounded-full px-2.5 py-0.5 text-[12px] font-semibold ${e.kind === "compute" ? "bg-[var(--d-teal-soft)] text-[var(--d-teal)]" : "bg-[var(--d-paper)] text-[var(--d-ink-2)]"}`}
          title={e.kind === "compute" ? "Computed from the Power BI model's own measure definitions" : "Rows of the model's tables, not a Power BI measure"}>
          {e.kind === "compute" ? "From your Power BI report" : e.receipt ? "Checked against the transactions" : "Transactions"}
        </span>
        {actions.pin && e.kind === "compute" && (
          <button onClick={() => actions.pin!(e)} aria-pressed={actions.pinned?.(e)} title="Pin to my dashboard"
            className={`flex items-center gap-1 rounded-md px-2 py-1 text-[12px] ${actions.pinned?.(e) ? "text-[var(--d-teal)]" : "text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]"}`}>
            <Pin size={13} className={actions.pinned?.(e) ? "fill-current" : ""} />{actions.pinned?.(e) ? "Pinned" : "Pin"}</button>
        )}
        <div className="flex gap-1">
          {(chartable ? ["chart", "table", "how"] as const : ["table", "how"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`rounded-md px-2.5 py-1 text-[12px] ${tab === t ? "bg-[var(--d-navy)] text-white" : "text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]"}`}>
              {t === "how" ? "How it's built" : t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {e.receipt && <ReceiptBand r={e.receipt} />}
      <div ref={box} className="relative px-6 pb-5 pt-4">
        {tab === "chart" && single && <Tiles e={e} />}
        {tab === "chart" && chartable && !single && (
          <div className={showHead ? "grid gap-6 md:grid-cols-[230px_minmax(0,1fr)]" : ""}>
            {showHead && <Headline e={e} />}
            {e.by.length > 0 ? (
              <AutoChart columns={e.columns} rows={e.rows} dims={e.by} metrics={e.metrics} labels={e.labels} kind={kind} onKind={setKind} palette={DESK_PALETTE} height={300}
                onPick={e.kind === "compute" || e.metrics.length ? (p) => setMenu({ x: Math.max(8, Math.min(p.x + (showHead ? 250 : 0), (box.current?.clientWidth ?? 800) - 300)), y: p.y + 36, values: p.values }) : undefined} />
            ) : <div />}
          </div>
        )}
        {tab === "table" && <ResultTable columns={e.columns} rows={e.rows} numeric={e.metrics} labels={e.labels} />}
        {tab === "how" && (
          <div className="space-y-3 text-[13px] text-[var(--d-ink-2)]">
            {e.kind === "compute" ? e.metrics.map((k) => <MetricHow key={k} model={model} k={k} label={e.labels[k] ?? k} />)
              : <p>Rows read from {e.table ?? "the table whose rows reach every column asked for"}, joined to related tables the way Power BI filters. These are not Power BI measures.</p>}
            <details open><summary className="cursor-pointer text-[var(--d-muted)]">SQL that ran{e.warehouse ? ` on ${e.warehouse}` : ""}{e.ms !== undefined ? ` · ${e.ms} ms` : ""}</summary>
              <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-[var(--d-paper)] p-3 font-mono text-[11px]">{e.sql}</pre></details>
          </div>
        )}

        {menu && <DrillMenu e={e} at={menu} model={model} detail={detail} next={next} trend={trend} actions={actions} onClose={() => setMenu(null)} />}
      </div>
      <div className="border-t border-[var(--d-rule)] px-6 py-2 text-[12px] text-[var(--d-muted)]">
        {tab === "chart" && e.kind === "compute" && e.by.length ? "Click any point to see what's behind it"
          : e.receipt?.row_count != null && e.receipt.row_count > e.rows.length ? `Showing ${e.rows.length} of ${num(e.receipt.row_count)} transactions`
          : `${e.rows.length} ${e.receipt || e.kind === "explore" ? "transaction" : "line"}${e.rows.length === 1 ? "" : "s"}`}{tab === "how" ? ` · ${e.warehouse ?? ""}` : ""}
      </div>
    </section>
  );
}

function MetricHow({ model, k, label }: { model: string; k: string; label: string }) {
  const [d, setD] = useState<PbiMetricDetail | null>(null);
  useEffect(() => { pbi.metric(model, k).then(setD).catch(() => {}); }, [model, k]);
  return (
    <div>
      <div className="font-semibold text-[var(--d-ink)]">{label}</div>
      {d?.definition && <div className="font-mono text-[12px]">{d.definition}</div>}
      {d?.parts?.map((p) => <div key={p.key} className="ml-3 font-mono text-[11px] text-[var(--d-muted)]">{p.key} = {p.definition}</div>)}
      {d?.dax && <details className="mt-1"><summary className="cursor-pointer text-[var(--d-muted)]">The DAX in Power BI</summary><pre className="mt-1 whitespace-pre-wrap font-mono text-[11px]">{d.dax}</pre></details>}
    </div>
  );
}

function ReceiptBand({ r }: { r: Receipt }) {
  const t = r.totals[0];
  const matches = r.exact && t && typeof r.expected === "number" && t.value !== null ? close(t.value, r.expected) : null;
  const ok = r.exact && matches !== false;
  return (
    <div className={`flex items-start gap-3 border-b px-6 py-3 text-[13px] ${ok ? "border-[var(--d-rule)] bg-[var(--d-teal-soft)] text-[var(--d-ink)]" : "border-[var(--d-rule)] bg-[var(--d-rise-soft)] text-[var(--d-ink)]"}`} role="status">
      {ok ? <Check size={16} className="mt-0.5 shrink-0 text-[var(--d-teal)]" /> : <TriangleAlert size={16} className="mt-0.5 shrink-0 text-[var(--d-rise-ink)]" />}
      <div className="min-w-0">
        {r.exact ? (
          <>
            <b className="num">{num(r.row_count)}</b> row{r.row_count === 1 ? "" : "s"} make up {r.label} for {r.within}
            {r.conditions.length > 0 && <> (only rows where <span className="font-mono text-[12px]">{r.conditions.map((c) => c.replace(/^\((.*)\)$/, "$1")).join(" and ")}</span>)</>}.{" "}
            {t && <>Added up, they come to <b className="num">{num(t.value)}</b>{matches === true ? <>, the same as the exhibit.</> : matches === false ? <>, but the exhibit shows <b className="num">{num(r.expected)}</b>. Something filters differently; check How it&rsquo;s built.</> : "."}</>}
          </>
        ) : (
          <>These are the rows of the table for {r.within}, not exactly the rows {r.label} adds up: {r.why}. Use them to inspect, not to reconcile.</>
        )}
      </div>
    </div>
  );
}

type Split = { by: string; n: number; top: unknown; topValue: number; share: number | null; rows: [unknown, number][] };

/** The drill menu: the clicked value and its share, then the columns that split it, ranked by how
    far their largest value is above an even split (columns with one value only are set aside), its trend, the
    rows behind it, and a question about it. Each split is computed for real, in SQL, when the menu opens. */
function DrillMenu({ e, at, model, detail, next, trend, actions, onClose }: {
  e: ExhibitData; at: { x: number; y: number; values: unknown[] }; model: string; detail: PbiMetricDetail | null;
  next: string[]; trend: string[]; actions: DeskActions; onClose: () => void;
}) {
  const m = e.metrics[0];
  const label = e.labels[m] ?? m ?? "";
  const value = valueAt(e, at.values);
  const [splits, setSplits] = useState<Split[] | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [more, setMore] = useState(false);
  const first = useRef<HTMLButtonElement | null>(null);
  const filters = useMemo(() => narrowed(e, at.values), [e, at.values]);

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => { if (ev.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  useEffect(() => { first.current?.focus(); }, [splits]);
  useEffect(() => {
    if (e.kind !== "compute" || !m || !e.by.length) return;
    let live = true;
    // the whole the exhibit is a part of: its value with the exhibit's own filters only
    pbi.compute(model, { metrics: [m], filters: e.filters }).then((r) => {
      const all = Number(r.rows[0]?.[r.columns.indexOf(m)]);
      const iM = e.columns.indexOf(m);
      const sum = e.rows.reduce((a, row) => a + Number(row[iM] ?? 0), 0);
      if (live && Number.isFinite(all) && e.by.length === 1 && close(sum, all)) setTotal(all);
    }).catch(() => {});
    return () => { live = false; };
  }, [e, m, model]);
  useEffect(() => {
    if (e.kind !== "compute" || !m || !detail) return;
    let live = true;
    Promise.all(next.map((c) => pbi.compute(model, { metrics: [m], by: [c], filters }).then((r): Split => {
      const iM = r.columns.indexOf(m), i0 = r.columns.indexOf(c);
      const rows = r.rows.map((x) => [x[i0], Number(x[iM] ?? 0)] as [unknown, number]).filter(([, v]) => Number.isFinite(v)).sort((a, b) => b[1] - a[1]);
      const sum = rows.reduce((a, [, v]) => a + v, 0);
      const additive = typeof value === "number" && rows.length > 0 && close(sum, value);
      return { by: c, n: rows.length, top: rows[0]?.[0], topValue: rows[0]?.[1] ?? 0, share: additive && sum ? rows[0][1] / sum : null, rows: rows.slice(0, 3) };
    }).catch(() => null))).then((xs) => {
      if (!live) return;
      const ok = xs.filter((x): x is Split => !!x);
      const spread = (x: Split) => x.share !== null ? x.share - 1 / x.n : (x.rows.length > 1 && x.topValue ? 1 - x.rows[x.rows.length - 1][1] / x.topValue : 0);
      setSplits([...ok.filter((x) => x.n > 1).sort((a, b) => spread(b) - spread(a)), ...ok.filter((x) => x.n <= 1)]);
    });
    return () => { live = false; };
  }, [e, m, model, detail, next, filters, value]);

  const within = e.by.map((d, i) => (at.values[i] !== undefined ? blank(at.values[i]) : "")).filter(Boolean).join(", ");
  const useful = splits?.filter((x) => x.n > 1) ?? [];
  const flat = splits?.filter((x) => x.n <= 1) ?? [];
  const shown = more ? useful : useful.slice(0, 4);
  const item = "block w-full rounded-md px-2 py-1.5 text-left hover:bg-[var(--d-teal-soft)] focus:bg-[var(--d-teal-soft)] focus:outline-none";

  return (
    <div role="menu" aria-label={`Drill into ${within}`} className="absolute z-20 max-h-[460px] w-[340px] overflow-y-auto rounded-xl border border-[var(--d-rule)] bg-[var(--d-sheet)] p-2 text-[13px] shadow-xl"
      style={{ left: Math.min(at.x, Math.max(8, at.x - 40)), top: at.y }}>
      <div className="flex items-start gap-2 px-2 pb-2">
        <div className="min-w-0 flex-1">
          <div className="cond truncate text-[17px] font-semibold text-[var(--d-ink)]">{within || label}</div>
          {value !== undefined && (
            <div className="text-[13px] text-[var(--d-ink-2)]">{label} <span className="num font-semibold text-[var(--d-ink)]">{num(value)}</span>
              {total && typeof value === "number" ? <> · <span className="num font-semibold">{(100 * value / total).toFixed(0)}%</span> of {num(total)}</> : null}</div>
          )}
        </div>
        <button onClick={onClose} aria-label="Close" className="text-[var(--d-muted)] hover:text-[var(--d-ink)]"><X size={14} /></button>
      </div>
      {e.kind === "compute" && (
        <>
          <div className="px-2 pb-1 text-[12px] font-semibold text-[var(--d-ink-2)]">Where it comes from</div>
          {!splits && <div className="px-2 py-1.5 text-[12px] text-[var(--d-muted)]">Splitting {within} by {next.length} columns…</div>}
          {splits && !useful.length && <div className="px-2 py-1.5 text-[12px] text-[var(--d-muted)]">No column splits it further.</div>}
          {shown.map((x, i) => (
            <button key={x.by} ref={i === 0 ? first : undefined} role="menuitem" onClick={() => { onClose(); actions.drill(e, at.values, x.by); }} className={item}>
              <div className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1 truncate font-semibold text-[var(--d-ink)]">{colLabel(x.by).replace(/ · .*$/, "")}</span>
                <span className="shrink-0 text-[12px] text-[var(--d-ink-2)]">{x.n} values</span>
              </div>
              <div className="mt-0.5 flex items-center gap-2 text-[12px] text-[var(--d-ink-2)]">
                <span className="min-w-0 flex-1 truncate">{x.share !== null ? <>{blank(x.top)} holds <b className="num text-[var(--d-ink)]">{(100 * x.share).toFixed(0)}%</b></> : <>highest {blank(x.top)} <b className="num text-[var(--d-ink)]">{num(x.topValue)}</b></>}</span>
                {x.share !== null && <span className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-[var(--d-paper)]"><span className="block h-full bg-[var(--d-teal)]" style={{ width: `${Math.round(100 * x.share)}%` }} /></span>}
              </div>
            </button>
          ))}
          {(useful.length > 4 || flat.length > 0) && (
            <button role="menuitem" onClick={() => setMore(!more)} className={`${item} text-[12px] text-[var(--d-ink-2)]`}>
              {more ? "Fewer" : `${useful.length - shown.length} more way${useful.length - shown.length === 1 ? "" : "s"} to split it`}
            </button>
          )}
          {more && flat.map((x) => <div key={x.by} className="px-2 py-1 text-[12px] text-[var(--d-ink-2)]">{colLabel(x.by).replace(/ · .*$/, "")}: only {blank(x.top)}</div>)}
          {trend.map((d) => <button key={d} role="menuitem" onClick={() => { onClose(); actions.drill(e, at.values, `${d}:month`); }} className={`${item} mt-1 border-t border-[var(--d-rule)] pt-2`}>See it month by month</button>)}
          {!detail && <div className="px-2 py-1 text-[12px] text-[var(--d-muted)]">Reading the columns…</div>}
        </>
      )}
      <div className="mt-1 border-t border-[var(--d-rule)] pt-1">
        <button role="menuitem" onClick={() => { onClose(); actions.rows(e, at.values); }} className={item}>See the transactions</button>
        <button role="menuitem" onClick={() => {
          onClose();
          const where = e.by.map((d, i) => (at.values[i] !== undefined ? `${colLabel(d).replace(/ · .*$/, "")} = ${blank(at.values[i])}` : "")).filter(Boolean).join(", ");
          actions.ask(`Why is ${label || "this"} what it is for ${where}? Compare it with the others.`);
        }} className={item}>Ask about it</button>
      </div>
    </div>
  );
}
