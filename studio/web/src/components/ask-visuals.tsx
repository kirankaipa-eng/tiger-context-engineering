"use client";

/* The Ask page's visuals: one per kind of question, each drawn from the rows of one or two computations.
   A ranking is a leaderboard, a change over time an annotated line, a comparison the values side by side
   and their gap, a "why" a waterfall, a mix a treemap or a donut, a flow a sankey, two things across each
   other a heatmap, two measures against each other a scatter, a spread a boxplot, days a calendar.
   Colors: one validated categorical order (light and dark, adjacent pairs colorblind-safe), teal for a
   rise and rose for a fall, one teal ramp for magnitude. A click on a mark reports its column and value. */

import dynamic from "next/dynamic";
import { isRatio, pctText, periodName } from "./auto-chart";
import { num } from "./pbi";

const ECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

/* eslint-disable @typescript-eslint/no-explicit-any */

/** Fixed order, never cycled: a seventh series folds into "Other". */
export const VIS_PALETTE = ["#14958e", "#d07d08", "#5468d4", "#cf4a6a", "#9a55c9", "#6a8f17"];
const UP = "#14958e", DOWN = "#cf4a6a", OTHER = "#b9c1cb", GRID = "rgba(140,145,155,.18)", AXIS = "#8b9099";
const RAMP = ["#e3f3f1", "#a8dcd6", "#5fb8ae", "#14958e", "#0b5f5a"];
const PERIOD = /:(year|quarter|month|week|day)$/;

export type VisualKind = "number" | "leaderboard" | "trend" | "seasons" | "versus" | "gap" | "waterfall" | "treemap" | "donut"
  | "mix" | "heatmap" | "sankey" | "scatter" | "boxplot" | "calendar";
export type Data = { columns: string[]; rows: unknown[][] };
type Props = {
  kind: VisualKind; data: Data[]; metrics: string[]; dims: string[]; labels: Record<string, string>;
  /** a value of a column picked elsewhere (the trail), shown as picked here */
  picked?: Record<string, unknown>; onPick?: (dim: string, value: unknown) => void; height?: number;
};

const at = (d: Data, c: string) => d.columns.indexOf(c);
const n = (v: unknown) => (v === null || v === undefined || v === "" ? 0 : Number(v));
const text = (dim: string, v: unknown, short = false) => (PERIOD.test(dim) ? periodName(v, short) : v === null || v === undefined || v === "" ? "(blank)" : String(v));
const pctOf = (x: number) => `${(100 * x).toFixed(Math.abs(x) < 0.1 ? 1 : 0)}%`;
const signed = (s: string, x: number) => `${x >= 0 ? "+" : "−"}${s.replace(/^-/, "")}`;
/** Sums a measure by one column's values, largest first. */
function totals(d: Data, dim: string, m: string) {
  const out = new Map<string, number>();
  for (const r of d.rows) { const k = text(dim, r[at(d, dim)]); out.set(k, (out.get(k) ?? 0) + n(r[at(d, m)])); }
  return [...out.entries()].sort((a, b) => b[1] - a[1]);
}
/** The raw value behind a shown label (a period label back to "2026-09"), for a pick. */
const raw = (d: Data, dim: string, label: string) => d.rows.map((r) => r[at(d, dim)]).find((v) => text(dim, v) === label || text(dim, v, true) === label) ?? label;

export function Visual({ kind, data, metrics, dims, labels, picked = {}, onPick, height = 300 }: Props) {
  const d = data[0];
  if (!d) return null;
  // marks are separated by the card's own color, so the gaps read as gaps in dark mode too
  const sheet = (typeof document !== "undefined" && getComputedStyle(document.querySelector(".desk") ?? document.body).getPropertyValue("--d-sheet").trim()) || "#fff";
  const m = metrics[0], label = (k: string) => labels[k] ?? k;
  const ratio = (k: string) => isRatio(label(k), d.rows.map((r) => r[at(d, k)]));
  const f = (k: string) => (v: unknown) => (ratio(k) ? pctText(v) : num(v));
  const fm = f(m);
  const base = { animationDuration: 500, textStyle: { color: AXIS, fontFamily: "inherit" } };
  const axisNum = (k = m) => ({ type: "value", axisLabel: { formatter: (v: number) => f(k)(v), fontSize: 11, color: AXIS }, splitLine: { lineStyle: { color: GRID } } });
  const catAxis = (dim: string, cats: string[]) => ({ type: "category", data: cats, axisTick: { show: false }, axisLine: { lineStyle: { color: GRID } },
    axisLabel: { color: AXIS, fontSize: 11, hideOverlap: true, ...(PERIOD.test(dim) ? { formatter: (v: string) => v } : {}) } });
  const chart = (option: any, h = height, click?: (e: any) => void) => (
    <ECharts option={{ ...base, ...option }} style={{ width: "100%", height: h }} notMerge
      onEvents={click && onPick ? { click } : undefined} />
  );

  switch (kind) {
    case "number": {
      // the total, its months as a sparkline, and how the last month moved on the one before
      const series = data[1];
      return (
        <div className="flex h-full flex-col justify-center gap-5">
          {metrics.map((k, j) => {
            const v = d.rows[0]?.[at(d, k)];
            const months = series ? [...series.rows].sort((a, b) => String(a[at(series, dims[0])]).localeCompare(String(b[at(series, dims[0])]))) : [];
            const last = months.at(-1), prev = months.at(-2);
            const change = last && prev && n(prev[at(series!, k)]) ? n(last[at(series!, k)]) / n(prev[at(series!, k)]) - 1 : null;
            return (
              <div key={k}>
                <div className="text-[13px] text-[var(--d-muted)]">{label(k)}</div>
                <div className="flex items-end gap-4">
                  <div className="num text-[40px] font-semibold leading-none text-[var(--d-ink)]">{f(k)(v)}</div>
                  {months.length > 2 && (
                    <div className="min-w-0 flex-1">
                      <ECharts style={{ width: "100%", height: 44 }} notMerge option={{
                        animation: false, grid: { left: 2, right: 2, top: 4, bottom: 2 }, xAxis: { type: "category", show: false, data: months.map((r) => r[at(series!, dims[0])]) },
                        yAxis: { type: "value", show: false, scale: true },
                        series: [{ type: "line", data: months.map((r) => r[at(series!, k)]), symbol: "none", lineStyle: { width: 2, color: VIS_PALETTE[j] },
                          areaStyle: { color: VIS_PALETTE[j], opacity: 0.08 } }],
                      }} />
                    </div>
                  )}
                </div>
                {change !== null && (
                  <div className="mt-1.5 text-[12.5px]" style={{ color: change >= 0 ? UP : DOWN }}>
                    {signed(pctOf(change), change)} <span className="text-[var(--d-muted)]">{periodName(last![at(series!, dims[0])])} on {periodName(prev![at(series!, dims[0])])}</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      );
    }

    case "leaderboard": {
      // ranked, the leader in full color, each row's share of the whole (not for a rate)
      const [dim] = dims, rows = totals(d, dim, m), shown = rows.slice(0, 12), whole = rows.reduce((s, [, v]) => s + v, 0);
      const rest = rows.slice(12).reduce((s, [, v]) => s + v, 0);
      const items = [...shown, ...(rest && !ratio(m) ? [["Other", rest] as [string, number]] : [])];
      const pick = picked[dim] !== undefined ? new Set((Array.isArray(picked[dim]) ? picked[dim] as unknown[] : [picked[dim]]).map((v) => text(dim, v))) : null;
      return chart({
        grid: { left: 8, right: 110, top: 8, bottom: 8, containLabel: true },
        tooltip: { trigger: "item", confine: true, formatter: (p: any) => `${p.name}<br/><b>${fm(p.value)}</b>${ratio(m) ? "" : ` · ${pctOf(p.value / whole)} of all`}` },
        xAxis: { type: "value", show: false }, yAxis: { ...catAxis(dim, items.map(([k]) => k)), inverse: true, axisLabel: { color: "#5b6472", fontSize: 12 } },
        series: [{ type: "bar", barMaxWidth: 22, data: items.map(([k, v], i) => ({ value: v, itemStyle: {
          color: k === "Other" ? OTHER : (pick ? pick.has(k) : i === 0) ? UP : "rgba(20,149,142,.42)", borderRadius: [0, 4, 4, 0] } })),
          label: { show: true, position: "right", color: "#5b6472", fontSize: 11.5, formatter: (p: any) => `${fm(p.value)}${ratio(m) ? "" : `  ${pctOf(p.value / whole)}`}` } }],
      }, Math.max(200, items.length * 28 + 24), (e: any) => e.name !== "Other" && onPick?.(dim, raw(d, dim, e.name)));
    }

    case "trend": {
      // a line, or one per value (six at most); the high and the latest marked, the latest against a year before
      const [p, s] = dims;
      const periods = [...new Set(d.rows.map((r) => String(r[at(d, p)])))].sort();
      const groups = s ? totals(d, s, m).slice(0, 6).map(([k]) => k) : [null];
      const cell = new Map(d.rows.map((r) => [`${r[at(d, p)]}\u0000${s ? text(s, r[at(d, s)]) : ""}`, r[at(d, m)]]));
      const line = (g: string | null) => periods.map((x) => cell.get(`${x}\u0000${g ?? ""}`) ?? null);
      const one = line(groups[0]), last = periods.at(-1), yearAgo = last && /^\d{4}-\d{2}$/.test(last) ? `${+last.slice(0, 4) - 1}${last.slice(4)}` : null;
      const yoy = !s && yearAgo && cell.get(`${yearAgo}\u0000`) ? n(one.at(-1)) / n(cell.get(`${yearAgo}\u0000`)) - 1 : null;
      return (
        <div>
          {!s && last && (
            <p className="mb-1 text-[13px] text-[var(--d-ink-2)]">
              <b className="num text-[var(--d-ink)]">{fm(one.at(-1))}</b> in {periodName(last)}
              {yoy !== null && <span style={{ color: yoy >= 0 ? UP : DOWN }}> · {signed(pctOf(yoy), yoy)} on {periodName(yearAgo)}</span>}
            </p>
          )}
          {chart({
            grid: { left: 8, right: 24, top: s ? 36 : 20, bottom: 8, containLabel: true },
            legend: s ? { top: 0, icon: "roundRect", itemWidth: 12, itemHeight: 4, textStyle: { color: AXIS } } : undefined,
            tooltip: { trigger: "axis", confine: true, valueFormatter: (v: number) => fm(v) },
            xAxis: { ...catAxis(p, periods.map((x) => periodName(x, true))), boundaryGap: false },
            yAxis: axisNum(),
            series: groups.map((g, j) => ({
              name: g ?? label(m), type: "line", data: line(g), symbolSize: 6, showSymbol: false, lineStyle: { width: 2 }, itemStyle: { color: VIS_PALETTE[j] },
              ...(s ? { endLabel: { show: groups.length <= 4, formatter: "{a}", color: "#5b6472", fontSize: 11 } } : {
                areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "rgba(20,149,142,.22)" }, { offset: 1, color: "rgba(20,149,142,0)" }] } },
                markPoint: { symbol: "circle", symbolSize: 8, label: { position: "top", color: "#5b6472", fontSize: 11, formatter: (q: any) => fm(q.value) },
                  data: [{ type: "max", name: "high" }, { coord: [periods.length - 1, one.at(-1)], value: one.at(-1) }] },
              }),
            })),
          }, height, (e: any) => onPick?.(p, periods[e.dataIndex]))}
        </div>
      );
    }

    case "seasons": {
      // month of the year across, years down: when in the year it is high
      const [p] = dims, MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
      const cells = d.rows.map((r) => [String(r[at(d, p)]).slice(0, 7), n(r[at(d, m)])] as const).filter(([x]) => /^\d{4}-\d{2}$/.test(x));
      const years = [...new Set(cells.map(([x]) => x.slice(0, 4)))].sort();
      const vals = cells.map(([, v]) => v);
      if (!vals.length) return <p className="py-6 text-[13px] text-[var(--d-muted)]">No months to show.</p>;
      return chart({
        grid: { left: 8, right: 8, top: 8, bottom: 40, containLabel: true },
        tooltip: { confine: true, formatter: (q: any) => `${MON[q.value[0]]} ${years[q.value[1]]}<br/><b>${fm(q.value[2])}</b>` },
        xAxis: { ...catAxis("", MON), splitArea: { show: false } }, yAxis: { ...catAxis("", years), inverse: true },
        visualMap: { min: Math.min(...vals), max: Math.max(...vals), orient: "horizontal", left: "center", bottom: 0, itemHeight: 120, itemWidth: 10,
          inRange: { color: RAMP }, text: ["high", "low"], textStyle: { color: AXIS, fontSize: 11 }, formatter: (v: number) => fm(v) },
        series: [{ type: "heatmap", data: cells.map(([x, v]) => [+x.slice(5) - 1, years.indexOf(x.slice(0, 4)), v]),
          itemStyle: { borderColor: sheet, borderWidth: 2, borderRadius: 4 },
          label: { show: false } }],
      }, Math.max(180, years.length * 38 + 70), (e: any) => onPick?.(p, `${years[e.value[1]]}-${String(e.value[0] + 1).padStart(2, "0")}`));
    }

    case "versus": {
      // each measure for each compared value, side by side, and the gap between the first two
      const [v] = dims, values = [...new Set(d.rows.map((r) => r[at(d, v)]))];
      const of = (x: unknown, k: string) => d.rows.find((r) => r[at(d, v)] === x)?.[at(d, k)];
      return (
        <div className="flex h-full flex-col justify-center gap-5">
          {metrics.map((k) => {
            const a = n(of(values[0], k)), b = n(of(values[1], k));
            return (
              <div key={k}>
                <div className="text-[13px] text-[var(--d-muted)]">{label(k)}</div>
                <div className="mt-1.5 grid gap-4" style={{ gridTemplateColumns: `repeat(${values.length}, minmax(0, 1fr))` }}>
                  {values.map((x, j) => (
                    <div key={String(x)} className="min-w-0">
                      <div className="num truncate text-[30px] font-semibold leading-none text-[var(--d-ink)]">{f(k)(of(x, k))}</div>
                      <div className="mt-1 flex items-center gap-1.5 truncate text-[12.5px] font-semibold text-[var(--d-ink-2)]">
                        <span className="inline-block h-2 w-2 rounded-full" style={{ background: VIS_PALETTE[j] }} />{text(v, x)}
                      </div>
                    </div>
                  ))}
                </div>
                {values.length === 2 && b !== 0 && (
                  <div className="mt-1.5 text-[12.5px] text-[var(--d-muted)]">
                    {text(v, values[0])} is <span style={{ color: a >= b ? UP : DOWN }}>{ratio(k) ? `${signed(pctText(a - b), a - b)} points` : signed(pctOf(a / b - 1), a - b)}</span> on {text(v, values[1])}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      );
    }

    case "gap": {
      // the first compared value minus the second, along a column (months, stores): above or below zero
      const [x, v] = dims;
      const values = [...new Set(d.rows.map((r) => text(v, r[at(d, v)])))].slice(0, 2);
      const cell = new Map(d.rows.map((r) => [`${text(x, r[at(d, x)])}\u0000${text(v, r[at(d, v)])}`, n(r[at(d, m)])]));
      let cats = [...new Set(d.rows.map((r) => text(x, r[at(d, x)])))];
      const diff = (c: string) => (cell.get(`${c}\u0000${values[0]}`) ?? 0) - (cell.get(`${c}\u0000${values[1]}`) ?? 0);
      const period = PERIOD.test(x);
      if (period) cats = [...new Set(d.rows.map((r) => String(r[at(d, x)])))].sort().map((c) => text(x, c));
      else cats = cats.sort((a, b) => Math.abs(diff(b)) - Math.abs(diff(a))).slice(0, 12);
      return chart({
        grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
        tooltip: { trigger: "axis", confine: true, valueFormatter: (q: number) => signed(fm(q), q) },
        xAxis: period ? catAxis(x, cats.map((c) => c.replace(/ (\d{2})(\d{2})$/, " ’$2"))) : { type: "value", axisLabel: { color: AXIS, fontSize: 11, formatter: (q: number) => fm(q) }, splitLine: { lineStyle: { color: GRID } } },
        yAxis: period ? axisNum() : { ...catAxis(x, cats), inverse: true, axisLabel: { color: "#5b6472", fontSize: 12 } },
        series: [{ type: "bar", name: `${values[0]} − ${values[1]}`, barMaxWidth: period ? 14 : 20,
          data: cats.map((c) => ({ value: diff(c), itemStyle: { color: diff(c) >= 0 ? UP : DOWN, borderRadius: period ? (diff(c) >= 0 ? [3, 3, 0, 0] : [0, 0, 3, 3]) : (diff(c) >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4]) } })),
          markLine: { silent: true, symbol: "none", lineStyle: { color: AXIS, type: "solid", width: 1 }, label: { show: false }, data: [period ? { yAxis: 0 } : { xAxis: 0 }] } }],
      }, period ? height : Math.max(200, cats.length * 28 + 30), (e: any) => onPick?.(x, raw(d, x, cats[e.dataIndex])));
    }

    case "waterfall": {
      // from the earlier period's total to the later one's, step by step: what each value added or took away
      const [dim, p] = dims;
      const periods = [...new Set(d.rows.map((r) => String(r[at(d, p)])))].sort();
      const p1 = periods.at(-1)!, back = /^\d{4}-\d{2}$/.test(p1) ? `${+p1.slice(0, 4) - 1}${p1.slice(4)}` : "";
      const p0 = periods.includes(back) ? back : periods.at(-2)!;
      const sum = (per: string) => { const o = new Map<string, number>(); d.rows.filter((r) => String(r[at(d, p)]) === per).forEach((r) => o.set(text(dim, r[at(d, dim)]), (o.get(text(dim, r[at(d, dim)])) ?? 0) + n(r[at(d, m)]))); return o; };
      const a = sum(p0), b = sum(p1), keys = [...new Set([...a.keys(), ...b.keys()])];
      const moves = keys.map((k) => [k, (b.get(k) ?? 0) - (a.get(k) ?? 0)] as [string, number]).sort((x, y) => Math.abs(y[1]) - Math.abs(x[1]));
      const top = moves.slice(0, 8), rest = moves.slice(8).reduce((s, [, v]) => s + v, 0);
      const steps = [...top, ...(Math.abs(rest) > 0 ? [["Other", rest] as [string, number]] : [])];
      const t0 = [...a.values()].reduce((s, v) => s + v, 0), t1 = [...b.values()].reduce((s, v) => s + v, 0);
      let run = t0;
      const bases: number[] = [0], bars: any[] = [{ value: t0, itemStyle: { color: "#5b6472", borderRadius: [4, 4, 0, 0] } }];
      for (const [, v] of steps) { bases.push(v >= 0 ? run : run + v); bars.push({ value: Math.abs(v), signed: v, itemStyle: { color: v >= 0 ? UP : DOWN, borderRadius: 3 } }); run += v; }
      bases.push(0); bars.push({ value: t1, itemStyle: { color: "#5b6472", borderRadius: [4, 4, 0, 0] } });
      const cats = [periodName(p0), ...steps.map(([k]) => k), periodName(p1)];
      return chart({
        grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
        tooltip: { trigger: "axis", confine: true, axisPointer: { type: "shadow" }, formatter: (ps: any) => { const q = ps.find((x: any) => x.seriesIndex === 1); const s = q?.data?.signed; return `${q?.name}<br/><b>${s === undefined ? fm(q?.value) : signed(fm(s), s)}</b>`; } },
        xAxis: { ...catAxis("", cats), axisLabel: { color: "#5b6472", fontSize: 11, interval: 0, rotate: cats.some((c) => c.length > 9) ? 28 : 0 } },
        yAxis: { ...axisNum(), scale: false },
        series: [
          { type: "bar", stack: "w", silent: true, itemStyle: { color: "transparent" }, data: bases },
          { type: "bar", stack: "w", barMaxWidth: 40, data: bars,
            label: { show: true, position: "top", color: "#5b6472", fontSize: 11, formatter: (q: any) => (q.data.signed === undefined ? fm(q.value) : signed(fm(q.data.signed), q.data.signed)) } },
        ],
      }, height, (e: any) => e.dataIndex > 0 && e.dataIndex <= steps.length && steps[e.dataIndex - 1][0] !== "Other" && onPick?.(dim, raw(d, dim, steps[e.dataIndex - 1][0])));
    }

    case "treemap": {
      // what the whole is made of: the coarser column's values as colors (named in the key above), the finer inside
      const [x, y] = [...dims].sort((p, q) => new Set(d.rows.map((r) => r[at(d, p)])).size - new Set(d.rows.map((r) => r[at(d, q)])).size);
      const outer = totals(d, x, m), whole = outer.reduce((s, [, v]) => s + v, 0);
      const color = (j: number) => (j < VIS_PALETTE.length ? VIS_PALETTE[j] : OTHER);
      const tree = outer.map(([k, v], j) => ({
        name: k, value: v, dim: x, itemStyle: { color: color(j) },
        children: y ? totals({ columns: d.columns, rows: d.rows.filter((r) => text(x, r[at(d, x)]) === k) }, y, m).map(([c, w]) => ({ name: c, value: w, dim: y })) : undefined,
      }));
      return (
        <div>
          {y && (
            <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-[var(--d-ink-2)]">
              {outer.slice(0, 8).map(([k, v], j) => (
                <button key={k} onClick={() => onPick?.(x, raw(d, x, k))} className="flex items-center gap-1.5 hover:text-[var(--d-ink)]">
                  <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color(j) }} />{k} <span className="num text-[var(--d-muted)]">{pctOf(v / whole)}</span>
                </button>
              ))}
            </div>
          )}
          {chart({
            tooltip: { confine: true, formatter: (q: any) => `${q.treePathInfo?.slice(1).map((t: any) => t.name).join(" › ") ?? q.name}<br/><b>${fm(q.value)}</b> · ${pctOf(q.value / whole)} of all` },
            series: [{ type: "treemap", data: tree, roam: false, nodeClick: false, breadcrumb: { show: false }, width: "100%", height: "100%", top: 0, left: 0,
              label: { color: "#fff", fontSize: 12, formatter: (q: any) => `${q.name}\n${pctOf(q.value / whole)}` },
              levels: [{ itemStyle: { borderWidth: 0, gapWidth: 4 } }, { itemStyle: { borderColor: sheet, borderWidth: 0, gapWidth: 2, borderRadius: 4 } },
                { colorAlpha: [0.6, 1], itemStyle: { borderColor: sheet, borderWidth: 1, gapWidth: 1, borderRadius: 3 } }] }],
          }, height, (e: any) => e.data?.dim && onPick?.(e.data.dim, raw(d, e.data.dim, e.name)))}
        </div>
      );
    }

    case "donut": {
      const [dim] = dims, rows = totals(d, dim, m), top = rows.slice(0, 5), rest = rows.slice(5).reduce((s, [, v]) => s + v, 0), whole = rows.reduce((s, [, v]) => s + v, 0);
      const items = [...top, ...(rest ? [["Other", rest] as [string, number]] : [])];
      return chart({
        tooltip: { confine: true, formatter: (q: any) => `${q.name}<br/><b>${fm(q.value)}</b> · ${pctOf(q.value / whole)}` },
        series: [{ type: "pie", radius: ["52%", "76%"], center: ["50%", "52%"], padAngle: 1.5, itemStyle: { borderRadius: 4 },
          data: items.map(([k, v], j) => ({ name: k, value: v, itemStyle: { color: k === "Other" ? OTHER : VIS_PALETTE[j] } })),
          label: { color: "#5b6472", fontSize: 11.5, formatter: (q: any) => `${q.name}\n${pctOf(q.value / whole)}` }, labelLine: { length: 8, length2: 8 } }],
        graphic: [{ type: "text", left: "center", top: "46%", style: { text: fm(whole), fill: "#1b2733", fontSize: 18, fontWeight: 600, textAlign: "center" } }],
      }, height, (e: any) => e.name !== "Other" && onPick?.(dim, raw(d, dim, e.name)));
    }

    case "mix": {
      // each period's whole as 100%: how the shares shifted
      const [p, s] = dims, periods = [...new Set(d.rows.map((r) => String(r[at(d, p)])))].sort();
      const groups = totals(d, s, m).slice(0, 5).map(([k]) => k);
      const cell = new Map<string, number>();
      d.rows.forEach((r) => { const g = groups.includes(text(s, r[at(d, s)])) ? text(s, r[at(d, s)]) : "Other"; const k = `${r[at(d, p)]}\u0000${g}`; cell.set(k, (cell.get(k) ?? 0) + n(r[at(d, m)])); });
      const all = [...groups, ...(totals(d, s, m).length > 5 ? ["Other"] : [])];
      const whole = (x: string) => all.reduce((t, g) => t + (cell.get(`${x}\u0000${g}`) ?? 0), 0) || 1;
      return chart({
        grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
        legend: { top: 0, icon: "roundRect", itemWidth: 12, itemHeight: 8, textStyle: { color: AXIS } },
        tooltip: { trigger: "axis", confine: true, valueFormatter: (q: number) => pctOf(q) },
        xAxis: { ...catAxis(p, periods.map((x) => periodName(x, true))), boundaryGap: false },
        yAxis: { type: "value", max: 1, axisLabel: { formatter: (q: number) => pctOf(q), color: AXIS, fontSize: 11 }, splitLine: { lineStyle: { color: GRID } } },
        series: all.map((g, j) => ({ name: g, type: "line", stack: "mix", symbol: "none", lineStyle: { width: 0 },
          areaStyle: { color: g === "Other" ? OTHER : VIS_PALETTE[j], opacity: 0.85 }, itemStyle: { color: g === "Other" ? OTHER : VIS_PALETTE[j] },
          data: periods.map((x) => (cell.get(`${x}\u0000${g}`) ?? 0) / whole(x)) })),
      }, height);
    }

    case "heatmap": {
      // one column down, the other across; across months, each row against its own average
      const [y, x] = dims, period = PERIOD.test(x);
      const ys = totals(d, y, m).slice(0, 14).map(([k]) => k);
      const xs = period ? [...new Set(d.rows.map((r) => String(r[at(d, x)])))].sort() : totals(d, x, m).slice(0, 16).map(([k]) => k);
      const key = (r: unknown[]) => (period ? String(r[at(d, x)]) : text(x, r[at(d, x)]));
      const cell = new Map(d.rows.map((r) => [`${key(r)}\u0000${text(y, r[at(d, y)])}`, n(r[at(d, m)])]));
      const avg = new Map(ys.map((k) => { const v = xs.map((c) => cell.get(`${c}\u0000${k}`) ?? 0); return [k, v.reduce((s, q) => s + q, 0) / (v.length || 1)]; }));
      const data = ys.flatMap((k, j) => xs.map((c, i) => { const v = cell.get(`${c}\u0000${k}`) ?? null; return [i, j, v === null ? null : period ? (avg.get(k) ? v / avg.get(k)! - 1 : 0) : v, v]; }));
      const vals = data.map((q) => q[2]).filter((q): q is number => q !== null);
      if (!vals.length) return <p className="py-6 text-[13px] text-[var(--d-muted)]">Nothing to show.</p>;
      const span = period ? Math.max(0.05, ...vals.map(Math.abs)) : 0;
      return chart({
        grid: { left: 8, right: 8, top: 8, bottom: 44, containLabel: true },
        tooltip: { confine: true, formatter: (q: any) => `${ys[q.value[1]]} · ${period ? periodName(xs[q.value[0]]) : xs[q.value[0]]}<br/><b>${fm(q.value[3])}</b>${period ? ` · ${signed(pctOf(q.value[2]), q.value[2])} on its average` : ""}` },
        xAxis: catAxis(x, xs.map((c) => (period ? periodName(c, true) : c))), yAxis: { ...catAxis(y, ys), inverse: true, axisLabel: { color: "#5b6472", fontSize: 11.5 } },
        visualMap: period
          ? { min: -span, max: span, orient: "horizontal", left: "center", bottom: 0, itemHeight: 120, itemWidth: 10, inRange: { color: [DOWN, "#eef0f3", UP] }, text: ["above its average", "below"], textStyle: { color: AXIS, fontSize: 11 }, dimension: 2 }
          : { min: Math.min(...vals), max: Math.max(...vals), orient: "horizontal", left: "center", bottom: 0, itemHeight: 120, itemWidth: 10, inRange: { color: RAMP }, text: ["high", "low"], textStyle: { color: AXIS, fontSize: 11 }, dimension: 2, formatter: (v: number) => fm(v) },
        series: [{ type: "heatmap", data, itemStyle: { borderColor: sheet, borderWidth: 1.5, borderRadius: 3 },
          label: { show: !period && xs.length <= 8 && ys.length <= 10, color: "#0b2b29", fontSize: 10, formatter: (q: any) => fm(q.value[3]) } }],
      }, Math.max(220, ys.length * 26 + 90), (e: any) => onPick?.(y, raw(d, y, ys[e.value[1]])));
    }

    case "sankey": {
      // how the whole flows from one column's values into the next's, in the order asked; eight per level, the rest "Other"
      const levels = dims;
      const keep = new Map(levels.map((l) => [l, new Set(totals(d, l, m).slice(0, 8).map(([k]) => k))]));
      const names = new Map<string, string>(); const used = new Set<string>();
      const node = (l: string, v: unknown) => {
        const k = keep.get(l)!.has(text(l, v)) ? text(l, v) : "Other", id = `${l}\u0000${k}`;
        if (!names.has(id)) { let s = k; if (used.has(s)) s = `${k} · ${l.replace(/^.*\[|\].*$/g, "")}`; used.add(s); names.set(id, s); }
        return names.get(id)!;
      };
      const links = new Map<string, number>();
      d.rows.forEach((r) => levels.slice(0, -1).forEach((l, i) => {
        const k = `${node(l, r[at(d, l)])}\u0000${node(levels[i + 1], r[at(d, levels[i + 1])])}`;
        links.set(k, (links.get(k) ?? 0) + n(r[at(d, m)]));
      }));
      const first = [...names.entries()].filter(([id]) => id.startsWith(levels[0] + "\u0000")).map(([, s]) => s);
      const color = (s: string) => (s.startsWith("Other") ? OTHER : VIS_PALETTE[first.indexOf(s) % VIS_PALETTE.length] ?? "#7d8794");
      const level = new Map([...names.entries()].map(([id, s]) => [s, id.split("\u0000")[0]]));
      return chart({
        tooltip: { confine: true, formatter: (q: any) => q.dataType === "edge" ? `${q.data.source} → ${q.data.target}<br/><b>${fm(q.value)}</b>` : `${q.name}<br/><b>${fm(q.value)}</b>` },
        series: [{ type: "sankey", left: 8, right: 110, top: 8, bottom: 8, nodeGap: 10, nodeWidth: 12, draggable: false, emphasis: { focus: "adjacency" },
          data: [...names.values()].map((s) => ({ name: s, itemStyle: { color: level.get(s) === levels[0] ? color(s) : "#7d8794", borderWidth: 0 } })),
          links: [...links.entries()].filter(([, v]) => v > 0).map(([k, v]) => { const [source, target] = k.split("\u0000"); return { source, target, value: v }; }),
          lineStyle: { color: "source", opacity: 0.3, curveness: 0.5 }, label: { color: "#3c4654", fontSize: 11.5 } }],
      }, Math.max(height, 34 * Math.max(...levels.map((l) => Math.min(9, new Set(d.rows.map((r) => r[at(d, l)])).size))) + 40),
      (e: any) => e.dataType === "node" && !e.name.startsWith("Other") && onPick?.(level.get(e.name)!, raw(d, level.get(e.name)!, e.name.replace(/ · .*$/, ""))));
    }

    case "scatter": {
      // two measures against each other, a dot per value; a third, if any, as its size
      const [dim] = dims, [mx, my, ms] = metrics;
      const pts = d.rows.map((r) => ({ name: text(dim, r[at(d, dim)]), value: [n(r[at(d, mx)]), n(r[at(d, my)]), ms ? n(r[at(d, ms)]) : 1] }));
      const big = Math.max(...pts.map((q) => q.value[2]), 1), named = new Set([...pts].sort((a, b) => b.value[0] - a.value[0]).slice(0, 8).map((q) => q.name));
      return chart({
        grid: { left: 8, right: 24, top: 16, bottom: 28, containLabel: true },
        tooltip: { confine: true, formatter: (q: any) => `${q.name}<br/>${label(mx)} <b>${f(mx)(q.value[0])}</b><br/>${label(my)} <b>${f(my)(q.value[1])}</b>${ms ? `<br/>${label(ms)} <b>${f(ms)(q.value[2])}</b>` : ""}` },
        xAxis: { ...axisNum(mx), name: label(mx), nameLocation: "middle", nameGap: 26, nameTextStyle: { color: AXIS, fontSize: 11 }, scale: true },
        yAxis: { ...axisNum(my), name: label(my), nameTextStyle: { color: AXIS, fontSize: 11, align: "left" }, scale: true },
        series: [{ type: "scatter", data: pts, symbolSize: (v: number[]) => (ms ? 8 + 22 * Math.sqrt(v[2] / big) : 11),
          itemStyle: { color: "rgba(20,149,142,.7)", borderColor: sheet, borderWidth: 1.5 },
          label: { show: true, position: "right", color: "#5b6472", fontSize: 11, formatter: (q: any) => (named.has(q.name) ? q.name : "") } }],
      }, height, (e: any) => onPick?.(dim, raw(d, dim, e.name)));
    }

    case "boxplot": {
      // each value's months as a spread: the middle half in the box, the median across it, the range on the whiskers
      const [dim, p] = dims, names = totals(d, dim, m).slice(0, 10).map(([k]) => k);
      const q = (s: number[], t: number) => { const i = (s.length - 1) * t, lo = Math.floor(i); return s[lo] + (s[Math.min(lo + 1, s.length - 1)] - s[lo]) * (i - lo); };
      const stats = names.map((k) => { const s = d.rows.filter((r) => text(dim, r[at(d, dim)]) === k).map((r) => n(r[at(d, m)])).sort((a, b) => a - b); return [s[0], q(s, 0.25), q(s, 0.5), q(s, 0.75), s.at(-1)!]; });
      return chart({
        grid: { left: 8, right: 24, top: 12, bottom: 8, containLabel: true },
        tooltip: { trigger: "item", confine: true, formatter: (x: any) => `${x.name} · each ${p.match(PERIOD)?.[1] ?? "period"}<br/>median <b>${fm(x.value[3])}</b><br/>middle half ${fm(x.value[2])} – ${fm(x.value[4])}<br/>range ${fm(x.value[1])} – ${fm(x.value[5])}` },
        xAxis: axisNum(), yAxis: { ...catAxis(dim, names), inverse: true, axisLabel: { color: "#5b6472", fontSize: 12 } },
        series: [{ type: "boxplot", data: stats, boxWidth: [8, 18], itemStyle: { color: "rgba(20,149,142,.18)", borderColor: UP, borderWidth: 1.5 } }],
      }, Math.max(200, names.length * 30 + 40), (e: any) => onPick?.(dim, raw(d, dim, names[e.dataIndex])));
    }

    case "calendar": {
      // the last year of days, a cell each, darker for more
      const [day] = dims;
      const cells = d.rows.map((r) => [String(r[at(d, day)]).slice(0, 10), n(r[at(d, m)])] as [string, number]).filter(([x]) => /^\d{4}-\d{2}-\d{2}$/.test(x)).sort();
      // one year of days (the rows' latest), a week a column
      const year = cells.at(-1)?.[0].slice(0, 4), shown = cells.filter(([x]) => x.startsWith(year ?? "-")), vals = shown.map(([, v]) => v);
      if (!shown.length) return <p className="py-6 text-[13px] text-[var(--d-muted)]">No days to show.</p>;
      return chart({
        tooltip: { confine: true, formatter: (q: any) => `${q.value[0]}<br/><b>${fm(q.value[1])}</b>` },
        visualMap: { min: Math.min(...vals), max: Math.max(...vals), orient: "horizontal", left: "center", bottom: 0, itemHeight: 120, itemWidth: 10, inRange: { color: RAMP }, text: ["more", "less"], textStyle: { color: AXIS, fontSize: 11 }, formatter: (v: number) => fm(v) },
        calendar: { range: year, top: 28, left: 36, right: 8, cellSize: ["auto", 15], itemStyle: { borderColor: sheet, borderWidth: 2, color: "#f3f5f7" },
          splitLine: { show: false }, yearLabel: { show: false }, dayLabel: { color: AXIS, fontSize: 10, firstDay: 1, nameMap: "en" }, monthLabel: { color: AXIS, fontSize: 10.5 } },
        series: [{ type: "heatmap", coordinateSystem: "calendar", data: shown }],
      }, 210);
    }
  }
}
