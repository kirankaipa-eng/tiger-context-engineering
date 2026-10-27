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
  // two dimensions draw one measure at a time: the person picks which
  const [lead, setLead] = useState(0);
  const one = dims.length > 1 && metrics.length > 1;
  const shown = useMemo(() => (one ? [metrics[Math.min(lead, metrics.length - 1)]] : metrics), [one, metrics, lead]);

  const built = useMemo(() => build(chosen, columns, rows, dims, shown, label, palette), [chosen, columns, rows, dims, shown, palette]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      {((kinds.length > 1 && !hideKinds) || one) && (
        <div className="mb-1 flex justify-end gap-1">
          {one && metrics.map((m, i) => (
            <button key={m} onClick={() => setLead(i)} className={`rounded-full border px-2.5 py-0.5 text-xs ${shown[0] === m ? "border-transparent text-white" : "border-line text-ink-2 hover:bg-sunk"}`}
              style={shown[0] === m ? { background: palette[0] } : undefined}>{label(m)}</button>
          ))}
          {one && <span className="mx-1 w-px bg-line" />}
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
        <ReactECharts option={built.option} style={{ height: chosen === "hbar" ? Math.max(220, built.count * 24 + 60) : chosen === "heatmap" ? 420 : height }} notMerge
          onEvents={onPick ? { click: (e: any) => { const v = built.pick(e); if (v) onPick({ values: v, x: e.event?.offsetX ?? 0, y: e.event?.offsetY ?? 0 }); } } : undefined} />
      ) : null}
    </div>
  );
}

function build(kind: ChartKind, columns: string[], rows: unknown[][], dims: string[], metrics: string[], label: (k: string) => string, PALETTE: string[]) {
  if (kind === "kpi" || !dims.length) return null;
  const iM = metrics.map((m) => columns.indexOf(m));
  const i0 = columns.indexOf(dims[0]);
  const axisNum = { type: "value", axisLabel: { formatter: (v: number) => num(v), fontSize: 11 }, splitLine: { lineStyle: { color: "#ece9e2" } } };
  const tip = { trigger: "axis", valueFormatter: (v: number) => num(v) };

  if (dims.length === 1) {
    const period = kind === "line" || isPeriod(dims[0], rows.map((r) => r[i0]));
    let shown = [...rows];
    if (period) shown.sort((a, b) => String(a[i0]).localeCompare(String(b[i0])));
    else shown.sort((a, b) => Number(b[iM[0]] ?? 0) - Number(a[iM[0]] ?? 0));
    if (!period) shown = shown.slice(0, MAX_CATS);
    const cats = shown.map((r) => blank(r[i0]));
    // a second metric of another size gets its own axis
    const mags = iM.map((i) => Math.max(...shown.map((r) => Math.abs(Number(r[i] ?? 0))), 0));
    const dual = metrics.length === 2 && mags[0] && mags[1] && (mags[0] / mags[1] > 20 || mags[1] / mags[0] > 20);
    const hbar = kind === "hbar";
    const catAxis = { type: "category", data: hbar ? [...cats].reverse() : cats, axisLabel: { interval: 0, fontSize: 11, rotate: !hbar && cats.some((c) => c.length > 12) ? 30 : 0, width: hbar ? 160 : undefined, overflow: "truncate" } };
    const series = metrics.map((m, j) => ({
      name: label(m), type: kind === "line" ? "line" : "bar", barMaxWidth: 46, symbolSize: 6, smooth: false,
      yAxisIndex: hbar ? undefined : dual && j === 1 ? 1 : 0, xAxisIndex: hbar ? (dual && j === 1 ? 1 : 0) : undefined,
      itemStyle: { color: PALETTE[j % PALETTE.length], borderRadius: kind === "line" ? 0 : hbar ? [0, 3, 3, 0] : [3, 3, 0, 0] },
      data: (hbar ? [...shown].reverse() : shown).map((r) => r[iM[j]] as number),
    }));
    const values = (hbar ? [...shown].reverse() : shown).map((r) => r[i0]);
    const option = {
      animation: false, tooltip: tip, legend: metrics.length > 1 ? { top: 0 } : undefined,
      grid: hbar ? { left: 170, right: 32, top: metrics.length > 1 ? 32 : 8, bottom: 24 }
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
  if (kind === "lines" && !isPeriod(dims[0], rows.map((r) => r[i0])) && isPeriod(dims[1], rows.map((r) => r[i1]))) [a, b] = [i1, i0];
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
  const order = [...sTotals.entries()].sort((x, y) => y[1] - x[1]).map(([s]) => s);
  const top = order.slice(0, kind === "heatmap" ? MAX_CATS : MAX_SERIES);
  const series = [...top, ...(order.length > top.length ? ["Other"] : [])];
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
    const data = cats.flatMap((c, x) => series.map((s, y) => [x, y, cell.get(`${c}\u0000${s}`) ?? null]));
    const vals = data.map((d) => d[2]).filter((v): v is number => v !== null);
    return {
      count: cats.length,
      option: {
        animation: false, grid: { left: 150, right: 24, top: 16, bottom: 110 },
        tooltip: { formatter: (p: any) => `${cats[p.value[0]]} · ${series[p.value[1]]}<br/><b>${num(p.value[2])}</b>` },
        xAxis: { type: "category", data: cats, axisLabel: { interval: 0, rotate: 30, fontSize: 11 } },
        yAxis: { type: "category", data: series, axisLabel: { fontSize: 11, width: 140, overflow: "truncate" } },
        visualMap: { min: Math.min(...vals, 0), max: Math.max(...vals, 1), calculable: true, orient: "horizontal", left: "center", bottom: 0, inRange: { color: ["#eef4f4", PALETTE[0]] } },
        series: [{ type: "heatmap", data, label: { show: cats.length * series.length <= 80, formatter: (p: any) => num(p.value[2]), fontSize: 10 } }],
      },
      pick: (e: any) => (e.value ? pickValues(cats[e.value[0]], series[e.value[1]]) : null),
    };
  }
  const line = kind === "lines";
  return {
    count: cats.length,
    option: {
      animation: false, tooltip: tip, legend: { top: 0, type: "scroll" },
      grid: { left: 70, right: 24, top: 40, bottom: cats.some((c) => c.length > 12) ? 90 : 40 },
      xAxis: { type: "category", data: cats, axisLabel: { interval: line ? "auto" : 0, rotate: cats.some((c) => c.length > 12) ? 30 : 0, fontSize: 11 } },
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
