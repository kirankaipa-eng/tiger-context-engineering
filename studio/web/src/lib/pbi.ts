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
export type RowsBehind = Result & {
  row_count: number | null; totals: { key: string; label: string; value: number | null }[];
  exact: boolean; why: string | null; conditions: string[]; table: string;
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
  rows: (id: string, body: { metric: string; filters?: Record<string, unknown>; limit?: number }) => call<RowsBehind>(`${m(id)}/rows`, { method: "POST", body: JSON.stringify(body) }),
  askPath: (id: string) => `${m(id)}/ask`,
  explain: (id: string, body: { metric: string; period: string; now: string; before: string; filters?: Record<string, unknown> }) =>
    call<{ explain: import("@/components/pbi-investigation").Investigation; said: string }>(`${m(id)}/explain`, { method: "POST", body: JSON.stringify(body) }),
  check: (id: string, explain: unknown) => call<{ checks: Check[] }>(`${m(id)}/check`, { method: "POST", body: JSON.stringify({ explain }) }),
  board: (id: string, owner: string) => call<Board>(`${m(id)}/board?owner=${encodeURIComponent(owner)}`),
  saveBoard: (id: string, owner: string, tiles: Tile[]) => call<Board>(`${m(id)}/board`, { method: "POST", body: JSON.stringify({ owner, tiles }) }),
  team: (id: string) => call<Team>(`${m(id)}/team`),
  scout: (id: string) => call<Team>(`${m(id)}/team/scout`, { method: "POST" }),
  comment: (id: string, fid: string, who: string, text: string) =>
    call<Finding>(`${m(id)}/team/${fid}/comment`, { method: "POST", body: JSON.stringify({ who, text }) }),
  status: (id: string, fid: string, who: string, status: Finding["status"], note?: string) =>
    call<Finding>(`${m(id)}/team/${fid}/status`, { method: "POST", body: JSON.stringify({ who, status, note }) }),
  brief: (id: string, asOf?: string, me?: string) =>
    call<Brief>(`${m(id)}/brief?${new URLSearchParams({ ...(asOf ? { as_of: asOf } : {}), ...(me && me !== "me" ? { me } : {}) })}`),
  briefPrefs: (id: string, who: string, prefs: BriefPrefs) =>
    call<BriefPrefs>(`${m(id)}/brief/prefs`, { method: "POST", body: JSON.stringify({ who, ...prefs }) }),
  setTarget: (id: string, metric: string, quarter: string, value: number | null, who: string) =>
    call<unknown>(`${m(id)}/targets`, { method: "POST", body: JSON.stringify({ metric, quarter, value, who }) }),
  pattern: (id: string, metric?: string, asOf?: string, me?: string) =>
    call<Pattern>(`${m(id)}/pattern?${new URLSearchParams({ ...(metric ? { metric } : {}), ...(asOf ? { as_of: asOf } : {}), ...(me && me !== "me" ? { me } : {}) })}`),
  take: (id: string, metric: string, values: string[], who: string, asOf?: string) =>
    call<Finding>(`${m(id)}/brief/take`, { method: "POST", body: JSON.stringify({ metric, values, who, as_of: asOf }) }),
};

/** The living brief: where the headline measures land, and what moved out of its own pattern. */
export type Landing = {
  metric: string; label: string; format: string | null; quarter: string; months: string[]; so_far: number; months_done: number;
  landing: number | null; low: number | null; high: number | null; miss: number | null; method: string | null;
  last_year: { quarter: string; value: number } | null; target: { value: number; by: string; on: string } | null;
  closed: { quarter: string; actual: number } | null; history: [string, number][]; ahead: [string, number, number, number][];
};
export type Claim = {
  metric: string; label: string; format: string | null; where: { by: string; value: string }[];
  status: "new" | "growing" | "holding" | "easing" | "settled"; month: string; since: string; value: number; usual: number;
  excess: number; z: number | null; share: number; series: [string, number][]; also?: string[]; period: string;
  explain: import("@/components/pbi-investigation").Investigation;
  finding: { id: string; owner: string | null; status: Finding["status"]; comments: Finding["comments"] } | null;
  /** The person reading owns it. */
  mine?: boolean;
};
/** A person's brief: the measures they follow (none: all), which get a landing, the slice they answer for. */
export type BriefPrefs = { measures: string[]; headline: string[]; scope: Record<string, string[]> };
export type Brief = {
  as_of: string; as_of_name: string; data_through: string; complete: string[]; partial: string | null;
  landing: Landing[]; claims: Claim[]; more: number; settled: Claim[]; checked: number; ms: number;
  me: string | null; prefs: BriefPrefs; choices: { metric: string; label: string }[]; slices: string[];
  spent?: Spent;
};
/** What a sweep of the warehouse spent: queries against its budget; partial when it stopped at the budget. */
export type Spent = { queries: number; budget: number; partial: boolean };

export type Check = { check: string; passed: boolean | null; said: string };
export type Finding = {
  id: string; metric: string; label: string; now: string; before: string; impact: number; also: string[];
  explain: import("@/components/pbi-investigation").Investigation; checks: Check[]; status: "new" | "claimed" | "acted" | "resolved";
  owner?: string | null; created: number; source: string;
  comments: { who: string; text: string; at: number; agent?: boolean; event?: string; checks?: Check[] }[];
};
export type Team = {
  model: string; findings: Finding[];
  scouted: { at: number; ms: number; measures: number; checked: { metric: string; label: string; now: string; before: string; verdict: string }[];
    /** the run's warehouse queries against its budget; partial when it stopped at the budget */
    spent?: Spent } | null;
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

/** A name written as an identifier (Current_AllowedAmount_Pharmacy, MemberAgeBand), as words, so it can wrap:
    "Current Allowed Amount Pharmacy". A name that already has spaces is left as its owner wrote it. Display only. */
export function readable(name: string) {
  if (!name || /\s/.test(name) || !/[_a-z][A-Z]|_/.test(name)) return name;
  return name.replace(/_+/g, " ").replace(/([a-z\d])([A-Z])/g, "$1 $2").replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2").trim();
}

export function colLabel(ref: string) {
  const { table, column, grain } = splitRef(ref);
  // the table only where it tells two columns apart, without its Dim_/Fact_ prefix
  const t = readable(table.replace(/^(dim|fact)_?/i, ""));
  return `${readable(column)}${grain ? ` (${grain})` : ""}`.trim() + (!table || column.toLowerCase().includes(t.replace(/\s/g, "").toLowerCase()) ? "" : ` · ${t}`);
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

/** A pinned question on a person's dashboard: re-run each time it is opened, never a copy of the numbers. */
export type Tile = {
  id: string; title: string; kind: "compute" | "explain"; at: number;
  metrics?: string[]; by?: string[]; filters?: Record<string, unknown>; labels?: Record<string, string>;
  explain?: { metric: string; period: string; now: string; before: string; filters?: Record<string, unknown> };
};
export type Board = { owner: string; tiles: Tile[]; at?: number };

/** The pattern map: every place a measure adds up from, two levels deep, month by month, against its own usual. */
export type Pattern = {
  metric: string; label: string; format: string | null; levels: string[]; months: string[]; as_of: string;
  groups: { name: string; children: { name: string; values: number[]; z: number[]; out: boolean[] }[] }[];
  disturbed: number[]; alpha: number; places: number; claim: string[] | null; choices: { metric: string; label: string }[];
};
