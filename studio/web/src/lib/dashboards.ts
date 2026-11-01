// A person's dashboards: named pages of charts kept from Ask, each chart re-run when a dashboard opens. They live
// in the person's saved board as its tiles (no change to the board's API): a chart tile names its dashboard, its
// kind, the columns it is drawn by, its width and place; a dashboard is itself a tile of kind "dashboard" holding
// its name and the filters set on it. Charts kept before dashboards existed belong to the first, "My dashboard".

import { RATE, kindsFor, type VisualKind } from "@/components/ask-visuals";
import type { Said } from "@/components/chart-thread";
import { pbi, type Tile } from "./pbi";

export const MAIN = "main";
/** The board holds this many tiles at most, dashboards and charts together (the server's limit). */
export const LIMIT = 40;

export type Chart = Tile & {
  dash?: string; chart?: VisualKind; dims?: string[]; w?: number; pos?: number;
  /** the measure the chart is about, as a heading for its group when arranged */
  section?: string;
  /** its threads: the conversation on it, and on any chart one of its replies brought (by that chart's id) */
  threads?: Record<string, Said[]>;
};
export type Dash = { id: string; kind: "dashboard"; title: string; at: number; filters?: Record<string, unknown>; arranged?: boolean };
type Saved = Chart | Dash;

const isDash = (t: Saved): t is Dash => (t as { kind: string }).kind === "dashboard";
export const dashboardsOf = (tiles: Saved[]): Dash[] => {
  const named = tiles.filter(isDash);
  return named.some((d) => d.id === MAIN) ? named : [{ id: MAIN, kind: "dashboard", title: "My dashboard", at: 0 }, ...named];
};
export const chartsOf = (tiles: Saved[], dash: string): Chart[] =>
  tiles.filter((t): t is Chart => !isDash(t) && (t.dash ?? MAIN) === dash)
    .sort((a, b) => (a.pos ?? 1e9) - (b.pos ?? 1e9) || a.at - b.at);

export async function load(model: string, owner: string): Promise<Saved[]> {
  // one of each: a board saved before Keep dropped repeats can hold a chart twice (and is set right on its next save)
  const tiles = ((await pbi.board(model, owner)).tiles ?? []) as Saved[];
  return tiles.filter((t, i) => tiles.findIndex((x) => x.id === t.id) === i);
}
export async function save(model: string, owner: string, tiles: Saved[]) {
  if (tiles.length > LIMIT) throw new Error(`a person's dashboards hold ${LIMIT} charts in all; remove one first`);
  await pbi.saveBoard(model, owner, tiles as unknown as Tile[]);
}

/** A new dashboard's id: its name made safe, kept unique. */
export function newDash(tiles: Saved[], title: string): Dash {
  const base = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "dashboard";
  let id = base, i = 2;
  while (dashboardsOf(tiles).some((d) => d.id === id)) id = `${base}-${i++}`;
  return { id, kind: "dashboard", title: title.trim() || "Untitled", at: Date.now() / 1000 };
}

/** Add charts to a dashboard (made first when `create` names a new one); a chart already on it is not added twice. */
export async function addTo(model: string, owner: string, charts: Chart[], dash: string | { create: string }) {
  const tiles = await load(model, owner);
  let id = typeof dash === "string" ? dash : "";
  if (typeof dash !== "string") { const d = newDash(tiles, dash.create); tiles.push(d); id = d.id; }
  const on = chartsOf(tiles, id), last = on.reduce((m, t) => Math.max(m, t.pos ?? 0), 0);
  const fresh = charts.map((c, i) => ({ ...c, id: `${c.id}-${c.chart ?? "x"}-${id}`, dash: id, pos: last + i + 1 }))
    .filter((c) => !on.some((t) => t.id === c.id));
  await save(model, owner, [...tiles, ...fresh]);
  return { id, title: dashboardsOf(tiles).find((d) => d.id === id)?.title ?? id, added: fresh.length };
}

const PERIOD = /:(year|quarter|month|week|day)$/;

/** The form a chart is drawn in: the one it was kept as; or, for a chart kept without one, as bars that would rank
    months by size or add across a second column, or a rate drawn as parts of a whole, the first the Ask page would
    offer for its columns (a line over time, a heatmap across two columns). */
export function formOf(c: Chart, labels: Record<string, string>): { chart: VisualKind; dims: string[] } {
  const dims = c.dims ?? c.by ?? [];
  if (c.kind === "explain" || !dims.length) return { chart: c.chart ?? (dims.length ? "leaderboard" : "number"), dims };
  const m = c.metrics?.[0] ?? "", additive = !RATE.test(labels[m] ?? m);
  const misdrawn = (c.chart === "leaderboard" && (dims.length > 1 || dims.some((d) => PERIOD.test(d))))
    || (!additive && ["waterfall", "treemap", "donut", "mix", "sankey"].includes(c.chart ?? ""));  // a rate is not a sum of parts
  if (c.chart && !misdrawn) return { chart: c.chart, dims };
  const first = kindsFor(dims, (d) => PERIOD.test(d), additive)[0];
  return first ? { chart: first.kind, dims: first.dims } : { chart: c.chart ?? "leaderboard", dims };
}

/** How wide a chart is drawn, of twelve columns, by what it shows: a number narrow, a ranking a third, a line over
    time two thirds, two columns across each other (a heatmap, a flow) or a table the full width. */
export function widthOf(c: Chart): number {
  if (c.w) return c.w;
  const k = c.kind === "explain" ? "explain" : c.chart ?? "leaderboard";
  const overTime = (c.dims ?? c.by ?? []).some((d) => /:(year|quarter|month|week|day)$/.test(d));
  if (k === "number" || k === "versus") return 3;
  if (k === "leaderboard" || k === "donut" || (k === "columns" && !overTime)) return 4;
  if (k === "trend" || k === "seasons" || k === "slope" || k === "columns") return 8;
  if (k === "boxplot" || k === "waterfall" || k === "mix") return 6;
  return 12;
}

const RANK: Record<string, number> = { number: 0, versus: 0, trend: 1, seasons: 1, slope: 1, columns: 2, leaderboard: 2, donut: 2,
  waterfall: 3, mix: 3, boxplot: 3, heatmap: 4, treemap: 4, sankey: 4, scatter: 4, calendar: 4, explain: 5, table: 6 };

/** Arrange a dashboard as a reader takes it in, the way a report is read: the headline number of each measure first,
    in a strip; then how each moved over time, where each is highest, what drove it, and the detail. Within each, the
    broadest measure leads (a total before its parts), then the one with most charts. A measure with no headline number
    gets one; a chart kept without a form, or in one that misreads its columns, is drawn as Ask would draw it. The
    charts fill rows of twelve columns, so the page is a grid. */
/** Headlines share their row: up to four to a row, each row filled (one 12, two 6, three 4, four 3 of twelve columns). */
export function headlineWidth(i: number, n: number): number {
  const rows = Math.ceil(n / 4), per = Math.ceil(n / rows);          // 5 → rows of 3 and 2, never 4 and a lone 1
  const row = Math.floor(i / per), inRow = Math.min(per, n - row * per);
  return 12 / inRow;
}

export function arrange(charts: Chart[], labels: Record<string, string>, room: number): Chart[] {
  const measure = (c: Chart) => (c.kind === "explain" ? c.explain?.metric : c.metrics?.[0]) ?? "";
  const counts = new Map<string, number>();
  charts.forEach((c) => counts.set(measure(c), (counts.get(measure(c)) ?? 0) + 1));
  const broad = (m: string) => (/\btotal\b|\boverall\b|\ball\b/i.test(labels[m] ?? m) ? 0 : 1);
  const order = [...counts.keys()].sort((a, b) => broad(a) - broad(b) || (counts.get(b) ?? 0) - (counts.get(a) ?? 0));
  const headline = (m: string): Chart => ({ id: `kpi-${m}`, title: labels[m] ?? m, kind: "compute", at: Date.now() / 1000, metrics: [m],
    by: [], filters: {}, chart: "number", dims: [], dash: charts[0]?.dash });
  const numbers = order.map((m) => charts.find((c) => measure(c) === m && c.chart === "number")
    ?? (room-- > 0 && m ? headline(m) : null)).filter(Boolean) as Chart[];
  const role = (c: Chart) => RANK[c.kind === "explain" ? "explain" : c.chart ?? ""] ?? 4;
  const rest = charts.filter((c) => !numbers.includes(c)).map((c) => ({ ...c, ...formOf(c, labels) }))
    .sort((a, b) => role(a) - role(b) || order.indexOf(measure(a)) - order.indexOf(measure(b)));
  return [...numbers.map((c, i) => ({ ...c, section: "Headlines", w: headlineWidth(i, numbers.length) })),
    ...rows(rest.map((c) => ({ ...c, section: labels[measure(c)] ?? measure(c), w: undefined }))).flat()]
    .map((c, i) => ({ ...c, pos: i + 1 }));
}

/** Charts in rows that fill the twelve columns, so a dashboard reads as a grid: each shares a row as far as it can
    shrink (a line over time, a flow or a heatmap to half), then the row's charts widen to fill it. */
function rows(charts: Chart[]): Chart[][] {
  const out: Chart[][] = []; let row: Chart[] = [], used = 0;
  const least = (c: Chart) => Math.min(widthOf(c), 6);
  for (const c of charts) {
    if (used + least(c) > 12) { out.push(row); row = []; used = 0; }
    row.push(c); used += least(c);
  }
  if (row.length) out.push(row);
  return out.map((r) => {
    const want = r.map(widthOf);
    const w = r.length === 1 ? [12]
      : r.length === 2 ? (Math.max(...want) >= 8 && Math.min(...want) <= 4 ? want.map((x) => (x >= 8 ? 8 : 4)) : [6, 6])
      : r.length === 3 ? (want.filter((x) => x <= 3).length === 2 ? want.map((x) => (x <= 3 ? 3 : 6)) : [4, 4, 4])
      : r.map(() => 3);
    return r.map((c, i) => ({ ...c, w: w[i] }));
  });
}
