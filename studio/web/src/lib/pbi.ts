// The Studio over captured Power BI models (`ctx-studio serve --pbi <dir>`): any model, its metrics,
// ad-hoc rows of its tables, drilldowns and questions.

import { API_BASE } from "./api";

export type PbiModel = {
  id: string; name: string; folder: string; warehouse: string | null; data_error: string | null;
  measures: number; captured: number; presentation: number; not_captured: number;
};
export type CoverageGroup = { kind: string; reason: string; blocks: number; roots: string[]; examples: { key: string; label: string }[] };
export type Overview = {
  id: string; name: string; folder: string; warehouse: string | null; data_error: string | null;
  imported?: { from: string; on: string };
  coverage: { measures: number; captured: number; presentation: number; not_captured: number; groups: CoverageGroup[] };
  tables: Record<string, number>; parameters: Record<string, { label?: string; min?: number; max?: number; step?: number; default?: number | null }>;
};
export type Needs = { by: string[]; params: Record<string, { label?: string; min?: number; max?: number; default?: number | null }> };
export type PbiMetric = {
  key: string; label: string; status: "captured" | "presentation" | "not_captured"; table?: string | null;
  why?: string | null; gap?: string | null; dax?: string | null; definition?: string; needs?: Needs;
  shown_by?: string[]; value?: string | null;
};
export type PbiMetricDetail = PbiMetric & { tables?: string[]; parts?: { key: string; definition: string }[]; columns?: string[]; sql?: string; sql_error?: string };
export type PbiTable = { name: string; warehouse: string | null; from?: string; why?: string; columns: { name: string; column: string; type?: string }[] };
export type Join = { from: string; to: string; many_to_one?: boolean; one_to_one?: boolean; inactive?: boolean };
export type Result = {
  columns: string[]; rows: unknown[][]; truncated: boolean; notes: string[]; what_if?: string[]; sql: string;
  warehouse: string; ms?: number; metrics?: string[]; aggregates?: string[];
};
export type ComputeBody = { metrics: string[]; by?: string[]; filters?: Record<string, unknown>; top?: number; order_by?: string; ascending?: boolean; params?: Record<string, number> };
export type ExploreBody = {
  columns?: string[]; aggregates?: string[]; filters?: Record<string, unknown>; conditions?: string[];
  order_by?: string[]; descending?: boolean; limit?: number; distinct?: boolean; table?: string;
};

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers: { "content-type": "application/json", ...(init.headers ?? {}) }, cache: "no-store" });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not json */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

const m = (id: string) => `/api/pbi/${encodeURIComponent(id)}`;

export const pbi = {
  health: () => call<{ ok: boolean; mode?: string; llm?: boolean; models?: string[] }>("/api/health"),
  models: () => call<{ models: PbiModel[]; llm: boolean }>("/api/pbi/models"),
  overview: (id: string) => call<Overview>(`${m(id)}/overview`),
  metrics: (id: string) => call<{ metrics: PbiMetric[] }>(`${m(id)}/metrics`),
  metric: (id: string, key: string) => call<PbiMetricDetail>(`${m(id)}/metrics/${encodeURIComponent(key)}`),
  tables: (id: string) => call<{ tables: PbiTable[]; joins: Join[] }>(`${m(id)}/tables`),
  values: (id: string, column: string) => call<{ column: string; values: unknown[]; more: boolean }>(`${m(id)}/values?column=${encodeURIComponent(column)}`),
  compute: (id: string, body: ComputeBody) => call<Result>(`${m(id)}/compute`, { method: "POST", body: JSON.stringify(body) }),
  explore: (id: string, body: ExploreBody) => call<Result>(`${m(id)}/explore`, { method: "POST", body: JSON.stringify(body) }),
  askPath: (id: string) => `${m(id)}/ask`,
};

/** "'Associated Product'[Product]:month" -> { table, column, grain } */
export function splitRef(ref: string) {
  const g = ref.match(/^(.*\])(?::([a-z]+))?$/);
  const base = g ? g[1] : ref;
  const grain = g?.[2];
  const i = base.indexOf("[");
  const table = base.slice(0, i).replace(/^'(.*)'$/, "$1").replace(/''/g, "'");
  return { table, column: base.slice(i + 1, -1), grain };
}

export function colLabel(ref: string) {
  const { table, column, grain } = splitRef(ref);
  return `${column}${grain ? ` (${grain})` : ""}`.trim() + (table && column.toLowerCase() === table.toLowerCase() ? "" : ` · ${table}`);
}

/** Columns worth breaking a number down by: no images, sort keys, colors, links or keys, and no
    numbers (amounts are what gets added up) unless they name a period. `type` from the tables. */
export function isDimension(ref: string, type?: string) {
  const c = splitRef(ref).column;
  if (/image|sort|colou?r|url|link|icon/i.test(c) || /ID$|(^|[\s_])id$|key$/i.test(c) && !/paid$/i.test(c)) return false;
  return !(type && /int|double|decimal|float|number/i.test(type) && !/year|month|quarter|week|day|age/i.test(c));
}

/** {ref: type} for every column of the model, and its date columns. */
export function columnTypes(tables: PbiTable[]) {
  const types: Record<string, string> = {};
  for (const t of tables) for (const c of t.columns) types[`${/^[A-Za-z_][A-Za-z0-9_]*$/.test(t.name) ? t.name : `'${t.name.replace(/'/g, "''")}'`}[${c.name}]`] = c.type ?? "";
  return { types, dates: Object.keys(types).filter((k) => /^date/i.test(types[k])) };
}
