"use client";

/* The chart that fits an answer, picked from its shape: a number is a KPI, a period a line, a few
   categories bars, many categories sorted horizontal bars, two dimensions stacked bars, lines per
   series (a period and a category) or a heatmap (many by many). The person can switch; clicking a mark
   reports the values under it (`onPick`), which is how a drilldown goes one level deeper. */

import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import { num } from "./pbi";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });
const PALETTE = ["#1f4fbf", "#d97a2b", "#23806e", "#8a4fbf", "#b8433b", "#5f7a1f", "#2b8ab8", "#7a6a5a", "#c2185b", "#455a64"];
/** The desk's colors: governed teal first, then the fall blue and the rise amber. */
export const DESK_PALETTE = ["#0f7b78", "#3f6fa3", "#c97c06", "#142338", "#7a5aa6", "#b8433b", "#5f7a1f", "#9aabbf", "#2b8ab8", "#6a7a90"];
const MAX_CATS = 30;
const MAX_SERIES = 8;

/* eslint-disable @typescript-eslint/no-explicit-any */

export type ChartKind = "kpi" | "line" | "bar" | "hbar" | "stacked" | "lines" | "heatmap";
export const KIND_LABEL: Record<ChartKind, string> = {
  kpi: "Number", line: "Line", bar: "Bars", hbar: "Ranked bars", stacked: "Stacked", lines: "Lines", heatmap: "Heatmap",
};

const PERIOD = /:(year|quarter|month|week|day)$/;
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** 2026-09 -> "Sep 2026", as a person says it (axis labels: "Sep ’26"). */
export const periodName = (v: unknown, short = false) => {
  const m = /^(\d{4})-(\d{2})$/.exec(String(v ?? ""));
  return m ? `${MON[+m[2] - 1]} ${short ? `’${m[1].slice(2)}` : m[1]}` : blank(v);
};
/** A measure that is a share or a ratio (Return Rate, Net Sales Variance %): shown as a percent. */
export const isRatio = (label: string, values: unknown[]) =>
  /%|\brate\b|ratio|pct|percent|share|margin/i.test(label) && values.every((v) => v === null || v === undefined || Math.abs(Number(v)) <= 10);
export const pctText = (v: unknown) => (v === null || v === undefined ? "—" : `${(100 * Number(v)).toFixed(Math.abs(Number(v)) < 0.1 ? 1 : 0)}%`);
const periodAxis = { interval: "auto", rotate: 0, hideOverlap: true, formatter: (v: string) => periodName(v, true) };
export const blank = (v: unknown) => (v === null || v === undefined || v === "" ? "(blank)" : String(v));
const isPeriod = (dim: string, values: unknown[]) =>
  PERIOD.test(dim) || (values.length > 1 && values.every((v) => typeof v === "string" && /^\d{4}(-\d{2}(-\d{2})?|-Q\d)?/.test(v)));

/** The kinds that can show this shape, best first. */
export function chartKinds(dims: string[], metrics: string[], columns: string[], rows: unknown[][]): ChartKind[] {
  if (!dims.length) return ["kpi"];
  const i0 = columns.indexOf(dims[0]);
  const n0 = new Set(rows.map((r) => blank(r[i0]))).size;
  const p0 = isPeriod(dims[0], rows.map((r) => r[i0]));
  if (dims.length === 1) {
    if (p0) return ["line", "bar"];
    return n0 > 12 ? ["hbar", "bar"] : ["bar", "hbar"];
  }
  const i1 = columns.indexOf(dims[1]);
  const n1 = new Set(rows.map((r) => blank(r[i1]))).size;
  const p1 = isPeriod(dims[1], rows.map((r) => r[i1]));
  if (p0 || p1) return ["lines", "stacked", "heatmap"];
  return n0 * n1 > 80 || n1 > MAX_SERIES + 4 ? ["heatmap", "stacked"] : ["stacked", "heatmap"];
}

type Picked = { values: unknown[]; x: number; y: number };

export function AutoChart({ columns, rows, dims, metrics, labels = {}, kind, onKind, onPick, height = 340, palette = PALETTE, hideKinds }: {
  columns: string[]; rows: unknown[][]; dims: string[]; metrics: string[]; labels?: Record<string, string>;
  kind?: ChartKind; onKind?: (k: ChartKind) => void; onPick?: (p: Picked) => void; height?: number; palette?: string[];
  hideKinds?: boolean;
}) {
  const kinds = useMemo(() => chartKinds(dims, metrics, columns, rows), [dims, metrics, columns, rows]);
  const [own, setOwn] = useState<ChartKind | null>(null);
  const chosen = kind && kinds.includes(kind) ? kind : own && kinds.includes(own) ? own : kinds[0];
  const setKind = (k: ChartKind) => (onKind ? onKind(k) : setOwn(k));
  const label = (k: string) => labels[k] ?? k;
  // one measure at a time, the person picks which, whenever several would crowd one chart: two
  // dimensions, more than three measures, or measures of different kinds (a percent beside an amount)
  const [lead, setLead] = useState(0);
  const ratios = metrics.map((m) => isRatio(label(m), rows.map((r) => r[columns.indexOf(m)])));
  const one = metrics.length > 1 && (dims.length > 1 || metrics.length > 3 || new Set(ratios).size > 1);
  const shown = useMemo(() => (one ? [metrics[Math.min(lead, metrics.length - 1)]] : metrics), [one, metrics, lead]);

  const pct = shown.every((m) => ratios[metrics.indexOf(m)]);
  // a long ranking shows its top ten; the rest on request
  const [all, setAll] = useState(false);
  const ranked = dims.length === 1 && (chosen === "hbar" || chosen === "bar") && !isPeriod(dims[0], rows.map((r) => r[columns.indexOf(dims[0])]));
  const many = ranked ? new Set(rows.map((r) => blank(r[columns.indexOf(dims[0])]))).size : 0;
  const limit = ranked && many > 12 && !all ? 10 : MAX_CATS;
  const built = useMemo(() => build(chosen, columns, rows, dims, shown, label, palette, pct, limit), [chosen, columns, rows, dims, shown, palette, pct, limit]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="w-full min-w-0 overflow-hidden">
      {((kinds.length > 1 && !hideKinds) || one || (ranked && many > 12)) && (
        <div className="mb-1 flex justify-end gap-1">
          {one && metrics.map((m, i) => (
            <button key={m} onClick={() => setLead(i)} className={`rounded-full border px-2.5 py-0.5 text-xs ${shown[0] === m ? "border-transparent text-white" : "border-line text-ink-2 hover:bg-sunk"}`}
              style={shown[0] === m ? { background: palette[0] } : undefined}>{label(m)}</button>
          ))}
          {one && <span className="mx-1 w-px bg-line" />}
          {ranked && many > 12 && <button onClick={() => setAll((x) => !x)} className="mr-2 rounded px-2 py-0.5 text-xs text-ink-2 hover:bg-sunk">{all ? "Top 10" : `Show all ${many}`}</button>}
          {!hideKinds && kinds.length > 1 && kinds.map((k) => (
            <button key={k} onClick={() => setKind(k)} className={`rounded px-2 py-0.5 text-xs ${chosen === k ? "bg-ink text-white" : "text-ink-2 hover:bg-sunk"}`}>{KIND_LABEL[k]}</button>
          ))}
        </div>
      )}
      {chosen === "kpi" ? (
        <div className="flex flex-wrap gap-6 py-4">
          {metrics.map((m) => (
            <div key={m}>
              <div className="text-[13px] text-muted">{label(m)}</div>
              <div className="text-4xl font-semibold tabular-nums">{num(rows[0]?.[columns.indexOf(m)])}</div>
            </div>
          ))}
        </div>
      ) : built ? (
        <ReactECharts option={{ ...built.option, textStyle: { color: "#8b9099" }, ...(built.option.legend ? { legend: { ...built.option.legend, textStyle: { color: "#8b9099" } } } : {}) }} style={{ width: "100%", height: chosen === "hbar" ? Math.max(220, built.count * 24 + 60) : chosen === "heatmap" ? 420 : height }} notMerge
          onEvents={onPick ? { click: (e: any) => { const v = built.pick(e); if (v) onPick({ values: v, x: e.event?.offsetX ?? 0, y: e.event?.offsetY ?? 0 }); } } : undefined} />
      ) : null}
    </div>
  );
}

function build(kind: ChartKind, columns: string[], rows: unknown[][], dims: string[], metrics: string[], label: (k: string) => string, PALETTE: string[], pct = false, limit = MAX_CATS) {
  const f = (v: unknown) => (pct ? pctText(v) : num(v));
  if (kind === "kpi" || !dims.length) return null;
  const iM = metrics.map((m) => columns.indexOf(m));
  const i0 = columns.indexOf(dims[0]);
  const axisNum = { type: "value", axisLabel: { formatter: (v: number) => f(v), fontSize: 11 }, splitLine: { lineStyle: { color: "rgba(140,145,155,.18)" } } };
  const tip = { trigger: "axis", confine: true, valueFormatter: (v: number) => f(v), axisPointer: { label: { formatter: (p: any) => periodName(p.value) } } };

  if (dims.length === 1) {
    const period = kind === "line" || isPeriod(dims[0], rows.map((r) => r[i0]));
    let shown = [...rows];
    if (period) shown.sort((a, b) => String(a[i0]).localeCompare(String(b[i0])));
    else shown.sort((a, b) => Number(b[iM[0]] ?? 0) - Number(a[iM[0]] ?? 0));
    if (!period) shown = shown.slice(0, limit);
    const cats = shown.map((r) => blank(r[i0]));
    // a second metric of another size gets its own axis
    const mags = iM.map((i) => Math.max(...shown.map((r) => Math.abs(Number(r[i] ?? 0))), 0));
    const dual = metrics.length === 2 && mags[0] && mags[1] && (mags[0] / mags[1] > 20 || mags[1] / mags[0] > 20);
    const hbar = kind === "hbar";
    const catAxis = { type: "category", data: hbar ? [...cats].reverse() : cats, axisLabel: period && !hbar ? { ...periodAxis, fontSize: 11 } : { interval: 0, fontSize: 11, rotate: !hbar && (cats.length > 10 || cats.some((c) => c.length > 12)) ? 30 : 0, width: hbar ? 160 : undefined, overflow: "truncate" } };
    const series = metrics.map((m, j) => ({
      name: label(m), type: kind === "line" ? "line" : "bar", barMaxWidth: 46, symbolSize: 6, smooth: false,
      yAxisIndex: hbar ? undefined : dual && j === 1 ? 1 : 0, xAxisIndex: hbar ? (dual && j === 1 ? 1 : 0) : undefined,
      itemStyle: { color: PALETTE[j % PALETTE.length], borderRadius: kind === "line" ? 0 : hbar ? [0, 3, 3, 0] : [3, 3, 0, 0] },
      data: (hbar ? [...shown].reverse() : shown).map((r) => r[iM[j]] as number),
      // the value at the end of each bar, when there is room to read it
      label: metrics.length === 1 && kind !== "line" && shown.length <= 15 ? { show: true, position: hbar ? "right" : "top", fontSize: 11, color: "#8b9099", formatter: (p: any) => f(p.value) } : undefined,
    }));
    const values = (hbar ? [...shown].reverse() : shown).map((r) => r[i0]);
    const option = {
      animation: false, tooltip: tip, legend: metrics.length > 1 ? { top: 0, type: "scroll" } : undefined,
      grid: hbar ? { left: 170, right: 72, top: metrics.length > 1 ? 32 : 8, bottom: 24 }
        : { left: 70, right: dual ? 70 : 24, top: metrics.length > 1 ? 36 : 16, bottom: cats.some((c) => c.length > 12) ? 90 : 40 },
      xAxis: hbar ? (dual ? [axisNum, axisNum] : axisNum) : catAxis,
      yAxis: hbar ? catAxis : dual ? [axisNum, { ...axisNum, splitLine: { show: false } }] : axisNum,
      series,
    };
    return { option, count: cats.length, pick: (e: any) => (e.dataIndex !== undefined ? [values[e.dataIndex]] : null) };
  }

  // two dimensions: the first along the axis, the second as series (or rows of a heatmap)
  const i1 = columns.indexOf(dims[1]);
  let a = i0, b = i1;
  // a period always runs along the axis, in order, whichever kind
  if (!isPeriod(dims[0], rows.map((r) => r[i0])) && isPeriod(dims[1], rows.map((r) => r[i1]))) [a, b] = [i1, i0];
  const m0 = iM[0];
  const totals = new Map<string, number>(), sTotals = new Map<string, number>();
  const raw = new Map<string, unknown>(), sRaw = new Map<string, unknown>();
  for (const r of rows) {
    const c = blank(r[a]), s = blank(r[b]), v = Number(r[m0] ?? 0);
    totals.set(c, (totals.get(c) ?? 0) + v); sTotals.set(s, (sTotals.get(s) ?? 0) + v);
    raw.set(c, r[a]); sRaw.set(s, r[b]);
  }
  const period = isPeriod(columns[a], [...raw.values()]);
  const cats = (period ? [...totals.keys()].sort() : [...totals.entries()].sort((x, y) => y[1] - x[1]).map(([c]) => c)).slice(0, period ? 400 : MAX_CATS);
  // two or three periods are a before and after: the series that moved most come first, not the largest
  const span = period && cats.length >= 2 && cats.length <= 3;
  const ends = (s: string) => {
    const v = (c: string) => rows.find((r) => blank(r[a]) === c && blank(r[b]) === s)?.[m0];
    return Math.abs(Number(v(cats[cats.length - 1]) ?? 0) - Number(v(cats[0]) ?? 0));
  };
  const order = span ? [...sTotals.keys()].sort((x, y) => ends(y) - ends(x))
    : [...sTotals.entries()].sort((x, y) => y[1] - x[1]).map(([s]) => s);
  let top = order.slice(0, kind === "heatmap" ? MAX_CATS : MAX_SERIES);
  // how far a series' last two periods broke from its own history (in its own spread): what moved
  const at = new Map<string, number>();
  for (const r of rows) at.set(`${blank(r[a])}\u0000${blank(r[b])}`, Number(r[m0] ?? 0));
  const usual = (s: string) => {
    const v = cats.map((c) => at.get(`${c}\u0000${s}`) ?? 0), hist = v.slice(0, -2);
    const mu = hist.reduce((x, y) => x + y, 0) / hist.length;
    const sd = Math.sqrt(hist.reduce((x, y) => x + (y - mu) ** 2, 0) / hist.length);
    const recent = (v[v.length - 1] + v[v.length - 2]) / 2;
    return { mu, z: Math.abs(recent - mu) / Math.max(sd, 0.05 * Math.abs(mu), 1e-9) };
  };
  const moved = period && cats.length >= 6;
  if (moved && kind !== "heatmap") {
    // keep a series that broke from its history though its total is small: folded into "Other" it would vanish
    const movers = order.filter((s) => !top.includes(s)).map((s) => [s, usual(s).z] as const)
      .filter(([, z]) => z >= 4).sort((x, y) => y[1] - x[1]).slice(0, 2).map(([s]) => s);
    if (movers.length) top = [...top.slice(0, MAX_SERIES - movers.length), ...movers];
  }
  // what moved most first, as many rows as read at this height
  if (moved && kind === "heatmap") top = [...top].sort((x, y) => usual(y).z - usual(x).z).slice(0, 14);
  const series = [...top, ...(order.length > top.length && !(moved && kind === "heatmap") ? ["Other"] : [])];
  const cell = new Map<string, number>();
  for (const r of rows) {
    const s = blank(r[b]);
    const k = `${blank(r[a])}\u0000${top.includes(s) ? s : "Other"}`;
    cell.set(k, (cell.get(k) ?? 0) + Number(r[m0] ?? 0));
  }
  const pickValues = (c: string, s: string) => {
    const out: unknown[] = [];
    out[a === i0 ? 0 : 1] = raw.get(c);
    out[a === i0 ? 1 : 0] = s === "Other" ? undefined : sRaw.get(s);
    return out;
  };
  if (kind === "heatmap") {
    // over periods, each row is colored against its own average, so a small series that moved shows as
    // strongly as a large one; otherwise by value
    const rel = moved;
    const avg = new Map(series.map((s) => [s, rel ? usual(s).mu : 0]));
    const data = cats.flatMap((c, x) => series.map((s, y) => {
      const v = cell.get(`${c}\u0000${s}`) ?? null, mu = avg.get(s) ?? 0;
      return [x, series.length - 1 - y, v === null ? null : rel ? (mu ? v / mu - 1 : 0) : v, v];
    }));
    const vals = data.map((d) => d[2]).filter((v): v is number => v !== null);
    const lim = Math.min(1.5, Math.max(0.25, ...vals.map((v) => Math.abs(v))));
    const ys = [...series].reverse();
    return {
      count: cats.length,
      option: {
        animation: false, grid: { left: 150, right: 24, top: 16, bottom: rel ? 70 : 110 },
        tooltip: { confine: true, formatter: (p: any) => `${cats[p.value[0]]} · ${ys[p.value[1]]}<br/><b>${num(p.value[3])}</b>` +
          (rel && p.value[2] !== null ? `, ${p.value[2] >= 0 ? "+" : ""}${Math.round(100 * p.value[2])}% vs its average` : "") },
        xAxis: { type: "category", data: cats, axisLabel: period ? { ...periodAxis, fontSize: 11 } : { interval: 0, rotate: cats.some((c) => c.length > 8) ? 30 : 0, fontSize: 11 } },
        yAxis: { type: "category", data: ys, axisLabel: { interval: 0, fontSize: 11, width: 140, overflow: "truncate" } },
        visualMap: rel
          ? { dimension: 2, min: -lim, max: lim, calculable: false, orient: "horizontal", left: "center", bottom: 0, itemHeight: 160,
              text: ["above its average", "below"], textStyle: { fontSize: 11 }, inRange: { color: ["#3f6fa3", "#f3f5f7", "#c97c06"] } }
          : { dimension: 2, min: Math.min(...vals, 0), max: Math.max(...vals, 1), calculable: true, orient: "horizontal", left: "center", bottom: 0, inRange: { color: ["#eef4f4", PALETTE[0]] } },
        series: [{ type: "heatmap", data, label: { show: cats.length * series.length <= 80, formatter: (p: any) => num(p.value[3]), fontSize: 10 } }],
      },
      pick: (e: any) => (e.value ? pickValues(cats[e.value[0]], ys[e.value[1]]) : null),
    };
  }
  const line = kind === "lines";
  return {
    count: cats.length,
    option: {
      animation: false, tooltip: tip, legend: { top: 0, type: "scroll" },
      grid: { left: 70, right: 24, top: 40, bottom: !period && (cats.length > 10 || cats.some((c) => c.length > 12)) ? 90 : 40 },
      xAxis: { type: "category", data: cats, axisLabel: period ? { ...periodAxis, fontSize: 11 } : { interval: line ? "auto" : 0, rotate: cats.length > 10 || cats.some((c) => c.length > 12) ? 30 : 0, fontSize: 11 } },
      yAxis: axisNum,
      series: series.map((s, j) => ({
        name: s, type: line ? "line" : "bar", stack: line ? undefined : "split", barMaxWidth: 46, symbolSize: 5,
        itemStyle: { color: s === "Other" ? "#b9b5aa" : PALETTE[j % PALETTE.length] },
        data: cats.map((c) => cell.get(`${c}\u0000${s}`) ?? null),
      })),
    },
    pick: (e: any) => (e.dataIndex !== undefined ? pickValues(cats[e.dataIndex], series[e.seriesIndex]) : null),
  };
}
