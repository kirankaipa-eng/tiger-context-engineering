/* What a click on an exhibit does, in one place for every screen that shows exhibits (My briefs, the team
   space): break a value down by another column, or open the transactions behind it; and the pinned form of
   an exhibit (its question, never its numbers). */

import { exhibitTitle, narrowed, valueAt, type ExhibitData } from "@/components/pbi-exhibit";
import { headline } from "@/components/pbi-investigation";
import { colLabel, pbi, type Tile } from "@/lib/pbi";

const within = (filters: Record<string, unknown>) =>
  Object.entries(filters).map(([k, v]) => `${colLabel(k).replace(/ · .*$/, "")} = ${v ?? "(blank)"}`).join(", ");
const rowsWithin = (e: ExhibitData, values: unknown[]) => Object.values(narrowed(e, values)).map((v) => v ?? "(blank)").join(", ") || "everything";

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

/** A question asked on the Ask page, kept in this browser so the sidebar can reopen it where it left off. */
export type Asked = { id: string; title: string; question: string; answer: string; specs: unknown[]; focus: [string, unknown][]; at: number;
  /** every question of the conversation, in order (the fields above are its last; kept before threads had none) */
  turns?: { id: string; question: string; answer: string; specs: unknown[]; focus: [string, unknown][]; error?: string }[] };
const askedKey = (model: string) => `ask-history:${model}`;
export function readAsked(model: string): Asked[] {
  try { return JSON.parse(localStorage.getItem(askedKey(model)) ?? "[]"); } catch { return []; }
}
/** Saves the list and tells the sidebar (`ask:history`). */
export function writeAsked(model: string, list: Asked[]) {
  try { localStorage.setItem(askedKey(model), JSON.stringify(list.slice(0, 30))); } catch { /* full or private */ }
  window.dispatchEvent(new Event("ask:history"));
}
