"use client";

/* An exhibit on the Power BI desk: one computed answer, with its headline (the latest period and its
   change, or the top value), the chart that fits its shape, the table, and how it was built (the
   captured definition and the SQL). Clicking a mark opens the drill menu: break that value down by
   another column, see its trend, open the rows behind it, or ask about it. */

import { X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { AutoChart, DESK_PALETTE, blank, type ChartKind } from "./auto-chart";
import { ResultTable, num } from "./pbi";
import { colLabel, isDimension, pbi, type PbiMetricDetail } from "@/lib/pbi";

export type ExhibitData = {
  kind: "compute" | "explore"; columns: string[]; rows: unknown[][]; metrics: string[]; by: string[];
  filters: Record<string, unknown>; conditions?: string[]; sql?: string; ms?: number; warehouse?: string;
  labels: Record<string, string>; origin: "agent" | "drill"; question?: string; table?: string;
};

export type DeskActions = {
  drill: (e: ExhibitData, values: unknown[], by: string) => void;
  rows: (e: ExhibitData, values: unknown[]) => void;
  ask: (text: string) => void;
};

const PERIOD = /:(year|quarter|month|week|day)$/;
const pct = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(1)}%`;

export function exhibitTitle(e: ExhibitData) {
  const what = e.metrics.length ? e.metrics.map((m) => e.labels[m] ?? m).join(" and ") : "Rows";
  return e.by.length ? `${what} by ${e.by.map((d) => colLabel(d).replace(/ · .*$/, "")).join(" and ")}` : what;
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
            <div className="num text-[52px] font-semibold leading-none text-[var(--d-navy)]">{num(e.rows[0]?.[e.columns.indexOf(k)])}</div>
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
    return (
      <div className="self-center">
        <div className="text-[13px] text-[var(--d-muted)]">{e.labels[m] ?? m}, {blank(lastRow[i0])}</div>
        <div className="num text-[52px] font-semibold leading-none text-[var(--d-navy)]">{num(v)}</div>
        {p !== null && p !== 0 && <div className="mt-3 text-[15px]"><span className="num font-semibold" style={{ color: v >= p ? "var(--d-teal)" : "var(--d-rise)" }}>{pct(v / p - 1)}</span> <span className="text-[var(--d-muted)]">vs {blank(prev![i0])}</span></div>}
      </div>
    );
  }
  if (e.by.length === 1 && e.rows.length) {
    const rows = [...e.rows].sort((a, b) => Number(b[iM] ?? 0) - Number(a[iM] ?? 0));
    const top = rows[0], low = rows[rows.length - 1];
    return (
      <div className="self-center">
        <div className="text-[13px] text-[var(--d-muted)]">Highest {e.labels[m] ?? m}</div>
        <div className="cond mt-0.5 truncate text-[22px] font-semibold text-[var(--d-navy)]" title={blank(top[i0])}>{blank(top[i0])}</div>
        <div className="num text-[40px] font-semibold leading-none text-[var(--d-navy)]">{num(top[iM])}</div>
        {rows.length > 1 && <div className="mt-3 text-[13px] text-[var(--d-muted)]">Lowest: <span className="font-semibold text-[var(--d-ink-2)]">{blank(low[i0])}</span> {num(low[iM])}<br />{rows.length} {colLabel(e.by[0]).replace(/ · .*$/, "").toLowerCase()} values</div>}
      </div>
    );
  }
  return (
    <div className="self-center text-[13px] text-[var(--d-muted)]">
      <div className="num text-[40px] font-semibold leading-none text-[var(--d-navy)]">{e.rows.length}</div>
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
  const next = (detail?.columns ?? []).filter((c) => !dates.includes(c) && isDimension(c, types[c]) && !used.includes(c)).slice(0, 9);
  const trend = (detail?.columns ?? []).filter((c) => dates.includes(c) && !used.includes(c)).slice(0, 1);
  const chartable = e.metrics.length > 0 && e.by.length <= 2 && e.rows.length > 0;
  const showHead = chartable && (e.by.length <= 1);
  const filters = Object.entries(e.filters);

  return (
    <section id={`exhibit-${n}`} className={`rounded-xl border border-[var(--d-rule)] bg-[var(--d-sheet)] shadow-[0_1px_2px_rgba(20,35,56,.06)] ${landed ? "landed" : ""}`}>
      <div className="flex flex-wrap items-start gap-3 border-b border-[var(--d-rule)] px-6 pb-3 pt-4">
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">Exhibit {n}{e.origin === "drill" ? (Object.keys(e.filters).length ? " · drill-down" : " · from the measure") : " · answer"}</div>
          <h2 className="cond text-[21px] font-semibold leading-snug text-[var(--d-navy)]">{exhibitTitle(e)}</h2>
          {(filters.length > 0 || (e.conditions?.length ?? 0) > 0) && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {filters.map(([k, v]) => <span key={k} className="rounded-full bg-[var(--d-teal-soft)] px-2.5 py-0.5 text-[12px] text-[var(--d-teal)]">{colLabel(k).replace(/ · .*$/, "")} = <b>{blank(v)}</b></span>)}
              {e.conditions?.map((c) => <span key={c} className="rounded-full bg-[var(--d-paper)] px-2.5 py-0.5 font-mono text-[11px] text-[var(--d-ink-2)]">{c}</span>)}
            </div>
          )}
        </div>
        <span className={`rounded-full px-2.5 py-0.5 text-[12px] font-semibold ${e.kind === "compute" ? "bg-[var(--d-teal-soft)] text-[var(--d-teal)]" : "bg-[var(--d-paper)] text-[var(--d-ink-2)]"}`}
          title={e.kind === "compute" ? "Computed from the Power BI model's own measure definitions" : "Rows of the model's tables, not a Power BI measure"}>
          {e.kind === "compute" ? "Power BI measure" : "Raw rows"}
        </span>
        <div className="flex gap-1">
          {(chartable ? ["chart", "table", "how"] as const : ["table", "how"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`rounded-md px-2.5 py-1 text-[12px] ${tab === t ? "bg-[var(--d-navy)] text-white" : "text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]"}`}>
              {t === "how" ? "How it's built" : t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      </div>

      <div ref={box} className="relative px-6 pb-5 pt-4">
        {tab === "chart" && chartable && (
          <div className={showHead ? "grid gap-6 md:grid-cols-[230px_1fr]" : ""}>
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

        {menu && (
          <div className="absolute z-20 w-72 rounded-xl border border-[var(--d-rule)] bg-white p-2 text-[13px] shadow-xl"
            style={{ left: menu.x, top: menu.y }}>
            <div className="flex items-start gap-2 px-2 pb-2">
              <div className="min-w-0 flex-1">
                {e.by.map((d, i) => menu.values[i] !== undefined && (
                  <div key={d} className={i ? "truncate text-[12px] text-[var(--d-muted)]" : "cond truncate text-[16px] font-semibold text-[var(--d-navy)]"}>{i ? `and ${colLabel(d).replace(/ · .*$/, "")} = ` : ""}{blank(menu.values[i])}</div>
                ))}
              </div>
              <button onClick={() => setMenu(null)} aria-label="Close" className="text-[var(--d-muted)] hover:text-[var(--d-navy)]"><X size={14} /></button>
            </div>
            {e.kind === "compute" && (
              <>
                <div className="px-2 text-[11px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">Break it down by</div>
                {next.map((c) => <button key={c} onClick={() => { setMenu(null); actions.drill(e, menu.values, c); }} className="block w-full truncate rounded-md px-2 py-1.5 text-left hover:bg-[var(--d-teal-soft)]">{colLabel(c)}</button>)}
                {trend.map((d) => <button key={d} onClick={() => { setMenu(null); actions.drill(e, menu.values, `${d}:month`); }} className="block w-full truncate rounded-md px-2 py-1.5 text-left hover:bg-[var(--d-teal-soft)]">Trend by month</button>)}
                {!detail && <div className="px-2 py-1 text-[12px] text-[var(--d-muted)]">Reading the columns…</div>}
              </>
            )}
            <div className="mt-1 border-t border-[var(--d-rule)] pt-1">
              <button onClick={() => { setMenu(null); actions.rows(e, menu.values); }} className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-[var(--d-teal-soft)]">Show the rows behind it</button>
              <button onClick={() => {
                setMenu(null);
                const where = e.by.map((d, i) => (menu.values[i] !== undefined ? `${colLabel(d).replace(/ · .*$/, "")} = ${blank(menu.values[i])}` : "")).filter(Boolean).join(", ");
                actions.ask(`Why is ${e.labels[m] ?? m ?? "this"} what it is for ${where}? Compare it with the others.`);
              }} className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-[var(--d-teal-soft)]">Ask about it</button>
            </div>
          </div>
        )}
      </div>
      <div className="border-t border-[var(--d-rule)] px-6 py-2 text-[12px] text-[var(--d-muted)]">
        {e.rows.length} row{e.rows.length === 1 ? "" : "s"} · {e.warehouse ?? ""}{e.kind === "compute" && e.by.length ? " · click a bar, point or cell to drill in" : ""}
      </div>
    </section>
  );
}

function MetricHow({ model, k, label }: { model: string; k: string; label: string }) {
  const [d, setD] = useState<PbiMetricDetail | null>(null);
  useEffect(() => { pbi.metric(model, k).then(setD).catch(() => {}); }, [model, k]);
  return (
    <div>
      <div className="font-semibold text-[var(--d-navy)]">{label}</div>
      {d?.definition && <div className="font-mono text-[12px]">{d.definition}</div>}
      {d?.parts?.map((p) => <div key={p.key} className="ml-3 font-mono text-[11px] text-[var(--d-muted)]">{p.key} = {p.definition}</div>)}
      {d?.dax && <details className="mt-1"><summary className="cursor-pointer text-[var(--d-muted)]">The DAX in Power BI</summary><pre className="mt-1 whitespace-pre-wrap font-mono text-[11px]">{d.dax}</pre></details>}
    </div>
  );
}
