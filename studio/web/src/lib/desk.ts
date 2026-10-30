/* What a click on an exhibit does, in one place for every screen that shows exhibits (the desk, my
   dashboard): break a value down by another column, or open the transactions behind it; and the pinned
   form of an exhibit (its question, never its numbers). */

import { exhibitTitle, narrowed, valueAt, type ExhibitData } from "@/components/pbi-exhibit";
import { headline } from "@/components/pbi-investigation";
import { colLabel, pbi, type Tile } from "@/lib/pbi";

const within = (filters: Record<string, unknown>) =>
  Object.entries(filters).map(([k, v]) => `${colLabel(k).replace(/ · .*$/, "")} = ${v ?? "(blank)"}`).join(", ");

export const drillNote = (e: ExhibitData, values: unknown[], by: string) => `${exhibitTitle({ ...e, by: [by] })}, within ${within(narrowed(e, values))}`;
const rowsWithin = (e: ExhibitData, values: unknown[]) => Object.values(narrowed(e, values)).map((v) => v ?? "(blank)").join(", ") || "everything";
export const rowsNote = (e: ExhibitData, values: unknown[]) => {
  const m = e.kind === "compute" ? e.metrics[0] : undefined;
  return m ? `The transactions behind ${e.labels[m] ?? m}, for ${rowsWithin(e, values)}` : `The transactions behind ${exhibitTitle(e).toLowerCase()}, for ${rowsWithin(e, values)}`;
};

/** The value under a mark, broken down by another column. */
export async function drillExhibit(model: string, e: ExhibitData, values: unknown[], by: string): Promise<ExhibitData> {
  const filters = narrowed(e, values);
  const w = within(filters);
  const r = await pbi.compute(model, { metrics: e.metrics, by: [by], filters });
  const m = e.metrics[0];
  const parent = m ? { label: e.labels[m] ?? m, value: valueAt(e, values), within: w } : undefined;
  return { kind: "compute", columns: r.columns, rows: r.rows, metrics: e.metrics, by: [by], filters, sql: r.sql, ms: r.ms, warehouse: r.warehouse, labels: e.labels, origin: "drill", parent };
}

/** The transactions behind a mark: for a measure, the rows it adds up with their total to reconcile. */
export async function rowsExhibit(model: string, e: ExhibitData, values: unknown[]): Promise<ExhibitData> {
  const filters = narrowed(e, values);
  const m = e.kind === "compute" ? e.metrics[0] : undefined;
  const w = rowsWithin(e, values);
  if (m) {
    const r = await pbi.rows(model, { metric: m, filters, limit: 50 });
    const label = e.labels[m] ?? m;
    return { kind: "explore", columns: r.columns, rows: r.rows, metrics: [], by: r.columns, filters, sql: r.sql, ms: r.ms, warehouse: r.warehouse, labels: {}, origin: "drill", table: r.table,
      receipt: { metric: m, label, row_count: r.row_count, totals: r.totals, exact: r.exact, why: r.why, conditions: r.conditions, shown: r.rows.length, expected: valueAt(e, values), within: w } };
  }
  const table = e.table;
  const t = table ? (await pbi.tables(model)).tables.find((x) => x.name === table) : undefined;
  const ref = (c: string) => `${/^[A-Za-z_][A-Za-z0-9_]*$/.test(table!) ? table : `'${table!.replace(/'/g, "''")}'`}[${c}]`;
  const own = t ? t.columns.map((c) => ref(c.name)).filter((c) => !/image|sort|colou?r|url/i.test(c)).slice(0, 8) : [];
  const columns = [...Object.keys(filters), ...own].filter((c, i, a) => a.indexOf(c) === i);
  const r = await pbi.explore(model, { columns, filters, limit: 50, table });
  return { kind: "explore", columns: r.columns, rows: r.rows, metrics: [], by: columns, filters, sql: r.sql, ms: r.ms, warehouse: r.warehouse, labels: {}, origin: "drill", table };
}

/** An exhibit as a pin: its question (an investigation's periods, or a measure's breakdown and filters). */
export function tileOf(e: ExhibitData): Tile {
  const spec = e.kind === "explain" && e.explain
    ? { kind: "explain" as const, explain: { metric: e.explain.metric, period: e.explain.period, now: e.explain.now, before: e.explain.before, filters: e.filters } }
    : { kind: "compute" as const, metrics: e.metrics, by: e.by, filters: e.filters, labels: e.labels };
  let h = 0;
  for (const ch of JSON.stringify(spec)) h = (h * 31 + ch.charCodeAt(0)) | 0;
  return { id: `t${(h >>> 0).toString(36)}`, title: e.kind === "explain" && e.explain ? headline(e.explain).title : exhibitTitle(e), at: Date.now() / 1000, ...spec };
}

/** Who the person is, as they told the team space (a name, not a sign-in). */
export const owner = () => { try { return localStorage.getItem("desk-name")?.trim() || "me"; } catch { return "me"; } };

/** One answer per question: within one question's results, a result a later one covers (the same kind, its
    measures all in the later one: a total before its breakdown, a call redone) is a working step, folded into
    that later answer, not an exhibit of its own. Results that answer different parts stay separate.
    `number` is each answer's exhibit number (1, 2, ...); `into` maps a working step to the answer it led to. */
export function fold(cards: ExhibitData[]) {
  const into = new Map<number, number>(), number = new Map<number, number>();
  cards.forEach((c, i) => {
    if (c.turn === undefined || c.origin !== "agent") return;
    const j = cards.findIndex((d, k) => k > i && d.turn === c.turn && d.kind === c.kind && c.metrics.every((m) => d.metrics.includes(m)));
    if (j > 0) into.set(i, j);
  });
  cards.forEach((_, i) => { if (!into.has(i)) number.set(i, number.size + 1); });
  const answer = (i: number): number => (into.has(i) ? answer(into.get(i)!) : i);  // a step's step lands on the last answer
  return { number, into: new Map([...into.keys()].map((i) => [i, answer(i)])) };
}
