"use client";

/* Ask once, get the picture the question needs. The question goes to the same agent as the desk (the `/ask`
   stream); its answer leads, and the measure it settled on is drawn the way the question asks for it: a
   ranking as a leaderboard, a change as an annotated trend, a comparison side by side with its gap, a "why"
   as a waterfall, a mix as a treemap, a flow as a sankey, and so on (see `layout`). Each view runs directly,
   in SQL, through `/compute`; no model in between. Clicking a mark focuses every view on it (the trail says
   where you are), and Keep pins the views to My dashboard. Follow-ups add to the conversation, each with
   views the ones above do not already show. */

import { ArrowUp, Layers, Repeat, Share2, Square, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { pctText, periodName } from "@/components/auto-chart";
import { AddToDashboard } from "@/components/add-to-dashboard";
import { ExportData } from "@/components/export-data";
import { DrillTree, PickMenu, type DrillStart } from "@/components/ask-drill";
import { EXPORT_GAPS, KIND_NAME, RATE, Visual, kindsFor, noteOf, type Data, type VisualKind } from "@/components/ask-visuals";
import type { ExhibitData } from "@/components/pbi-exhibit";
import { ResultTable, SqlBlock, num } from "@/components/pbi";
import { streamNdjson } from "@/lib/api";
import type { Chart } from "@/lib/dashboards";
import { readAsked, tileOf, writeAsked } from "@/lib/desk";
import { colLabel, columnTypes, readable, isDimension, pbi, type PbiMetric, type Result } from "@/lib/pbi";
import { useStudio } from "@/lib/studio";
import { BUSINESS, PEOPLE, PLACE } from "@/lib/vocab";
import { SettingsMenu, useLook } from "@/lib/theme";
import { ThreadButton, ThreadPanel, browserThreads, type Anchor } from "@/components/chart-thread";

/* eslint-disable @typescript-eslint/no-explicit-any */

/** One picture of the answer: what it draws (`kind`), the computations it draws from (`queries`, each the
    columns to group by), the columns it is about (`dims`, in the order the visual reads them) and those a
    pick on the trail leaves alone (`own`: it keeps showing every value, the picked one marked).
    `table`: rows the agent brought itself (a listing, an analysis), drawn as they are. */
type View = { id: string; kind: VisualKind | "rows"; title: string; metrics: string[]; queries: string[][]; dims: string[]; own: string[];
  filters: Record<string, unknown>; table?: Data; ran?: Ran; was?: View };
/** The answer's own picture: the agent's result whose numbers the answer states, drawn as computed. */
type Picture = { kind: VisualKind; scope: string; data: Data[]; metrics: string[]; dims: string[]; filters?: Record<string, unknown> };
/** The picture's title, in the measures' names as the model has them now. */
const pictureTitle = (p: Picture, labels: Record<string, string>) => {
  const names = p.metrics.map((m) => labels[m] ?? m).join(", "), over = p.dims.filter((d) => !PERIOD.test(d)).map(said);
  return over.length ? `${names} by ${over.join(" and ")}` : names;
};
/** How an analysis was run, as the sandbox reports it: shown under its result, code and all. */
type Ran = { code?: string; rows?: number; seconds?: number; isolated?: boolean; commit?: string | null; row_security?: boolean;
  method?: { test?: string; n?: number; basis?: string; assumptions?: string[]; caveats?: string[] }; checks?: { check: string; passed: boolean }[];
  numbers?: Record<string, unknown> };
/** A figure from an analysis as a reader wants it: a p-value bounded, a rate as a percentage, a small number not rounded to 0. */
const figure = (name: string, v: unknown) => {
  if (typeof v !== "number" || !Number.isFinite(v)) return String(v ?? "–");
  if (/p.?value|^p$/i.test(name)) return v < 0.001 ? "< 0.001" : v.toFixed(3);
  if (/rate|share|pct|percent|ratio|proportion/i.test(name) && !/\(pp\)|points/i.test(name) && Math.abs(v) <= 1) return pctText(v);
  return Math.abs(v) < 10 && !Number.isInteger(v) ? String(Number(v.toPrecision(2))) : num(v);
};
type Run = { results?: Result[]; error?: string; loading?: boolean };
type Focus = [string, unknown][];

const PERIOD = /:(year|quarter|month|week|day)$/;
const STEP: Record<string, string> = {
  list_metrics: "Reading the measures", list_columns: "Finding what it breaks down by", column_values: "Matching your words to the data",
  compute: "Computing from the Power BI measures", explore: "Reading rows", explain: "Working out what moved", analyze: "Running the analysis",
};
/** "Store[Store]" -> "Store", "Calendar[Date]:month" -> "month": as a person says it. */
const said = (ref: string) => (PERIOD.test(ref) ? ref.match(PERIOD)![1] : colLabel(ref).replace(/ · .*$/, ""));
const shown = (ref: string, v: unknown): string => (Array.isArray(v) ? v.map((x) => shown(ref, x)).join(", ") : PERIOD.test(ref) ? periodName(v) : v === null || v === undefined || v === "" ? "(blank)" : String(v));
const key = (metrics: string[], by: string[], filters: Record<string, unknown>) => JSON.stringify([metrics, by, Object.entries(filters).sort()]);
/** A focus on a few values of one column (Product: Word, Excel) is a comparison: the values it names. */
const versusOf = (focus: Focus) => focus.find(([k, v]) => Array.isArray(v) && v.length > 1 && v.length <= 6 && !PERIOD.test(k)) as [string, unknown[]] | undefined;
/** A view as what it shows, whatever its id. */
const sig = (v: View) => JSON.stringify([v.kind, v.metrics[0], v.queries]);
const newSession = () => `ask-${Math.random().toString(36).slice(2, 10)}`;

/** What a question asks for, by its words, most specific first. Several can hold ("where is it highest,
    and how has it moved?"): each gives the picture it asks for, in the order asked. */
type Intent = "flow" | "compare" | "drivers" | "relation" | "spread" | "daily" | "season" | "mix" | "matrix" | "rank" | "trend";
const INTENTS: [Intent, RegExp][] = [
  ["flow", /\bsankey\b|\bflows?\b|\bfrom\b.+\binto\b|\bjourney\b|\bpaths?\b/i],
  ["compare", /\bvs\.?\b|\bversus\b|\bcompar|\bagainst each other\b|\bdifference between\b/i],
  ["drivers", /\bwhy\b|\bdriv|\bcaus|\bexplain|\bbehind\b|\bcontribut|\bwaterfall\b|\bgap\b|\bwhat moved\b/i],
  ["relation", /\brelat(ed|ionship)\b|\bcorrelat|\bscatter\b|\btrade-?off\b|\bassociated\b/i],
  ["spread", /\bdistribution\b|\bspread\b|\bvolatil|\bvariab|\bconsistent\b|\bsteady\b|\boutlier|\bbox ?plot\b/i],
  ["daily", /\bdaily\b|\bday by day\b|\bwhich days?\b|\bcalendar\b|\bweekdays?\b|\bweekends?\b/i],
  ["season", /\bseason|\bpeak months?\b|\bwhich months?\b|\btime of (the )?year\b|\bmonth of the year\b/i],
  ["mix", /\bmix\b|\bshares?\b|\bsplit\b|\bcomposition\b|\bproportion|\bmade up\b|\bmade of\b|\btreemap\b|\bdonut\b|\bpie\b|\bportion\b|\bbreak ?down\b|\bbreak (it|this|that|them) down\b/i],
  ["matrix", /\bheat ?map\b|\bacross\b.+\band\b|\bmatrix\b/i],
  ["rank", /\bhighest\b|\blowest\b|\btop\b|\bbottom\b|\bbest\b|\bworst\b|\bmost\b|\bleast\b|\brank|\blargest\b|\bbiggest\b|\bsmallest\b|\bwhere\b|\bwhich\b|\bleaders?\b/i],
  ["trend", /\btrend|\bmoved?\b|\bover time\b|\bgrow|\bdeclin|\bchang|\bhistory\b|\bmonth by month\b|\bsince\b|\btrajectory\b|\bmoving\b/i],
];
const SIGNIFICANT = /significan|statistic|\bby chance\b|\bprove\b|confiden|p-?value|hypothes/i;

/** The numbers a text states, dates and periods left out; 14.4% reads as 14.4 and 0.144. */
const stated = (t: string) => [...t.replace(/\b(19|20)\d{2}(-\d{2})?\b|\bQ[1-4]\b/gi, " ").matchAll(/(-?\d[\d,]*\.?\d*)\s*(%|[kKmM](?![A-Za-z]))?/g)]
  .flatMap(([, raw, u]) => {
    const x = Number(raw.replace(/,/g, "").replace(/\.$/, ""));
    if (!Number.isFinite(x)) return [];
    return u === "%" ? [x, x / 100] : [u ? x * (/k/i.test(u) ? 1e3 : 1e6) : x];
  });
const close = (a: number, b: number) => Math.abs(a - b) <= Math.max(Math.abs(b) * 0.006, Math.abs(b) >= 10 && Number.isInteger(a) ? 0.51 : 0.051);

/** A result of two periods (before and after) also states each measure's change between them, in percent: a "why did it
    change" answer quotes those (+32.5%), so they count as its numbers. */
const changes = (r: ExhibitData, only?: string) => {
  if (r.rows.length !== 2 || r.by.length !== 1 || !PERIOD.test(r.by[0])) return [];
  const at = r.columns.indexOf(r.by[0]);
  const [a, b] = [...r.rows].sort((x, y) => String(x[at]).localeCompare(String(y[at])));
  return (only ? [only] : r.metrics).flatMap((m) => {
    const i = r.columns.indexOf(m), x = Number(a[i]), y = Number(b[i]);
    return Number.isFinite(x) && Number.isFinite(y) && x ? [(y / x - 1) * 100] : [];
  });
};

/** The answer's picture: of the agent's results, the one whose numbers the answer states most, drawn by its shape
    (two values of a column, two periods most often, as a slope; several measures alone as bars; one column as a
    ranking or, over time, a line). None when the answer states none of its numbers. */
function pictureOf(results: ExhibitData[], answer: string): Picture | undefined {
  const said_ = stated(answer).filter((x) => !(Number.isInteger(x) && Math.abs(x) <= 12));
  let best: ExhibitData | undefined, score = 0;
  for (const r of results) {
    // a breakdown of one row (the agent asked for the top one) shows nothing to compare: the full chart stands in
    if (r.kind !== "compute" || !r.metrics.length || !r.rows.length || (r.by.length && r.rows.length < 2)) continue;
    const cells = [...r.rows.flatMap((row) => r.metrics.map((m) => Number(row[r.columns.indexOf(m)])).filter(Number.isFinite)), ...changes(r)];
    const hits = said_.filter((x) => cells.some((c) => close(x, c))).length;
    if (hits >= score && hits > 0) { best = r; score = hits; }
  }
  if (!best) return undefined;
  // one scale: measures far apart in size (a level and its percent change) are not drawn together; the one the
  // answer states most leads, and those within twenty times its size stay beside it
  const size = (m: string) => {
    const xs = best!.rows.map((row) => Math.abs(Number(row[best!.columns.indexOf(m)]))).filter((x) => Number.isFinite(x) && x > 0).sort((a, b) => a - b);
    return xs[Math.floor(xs.length / 2)] ?? 0;
  };
  const hitsOf = (m: string) => said_.filter((x) => best!.rows.some((row) => close(x, Number(row[best!.columns.indexOf(m)])))
    || changes(best!, m).some((c) => close(x, c))).length;
  const leadM = [...best.metrics].sort((a, b) => hitsOf(b) - hitsOf(a))[0];
  const metrics = best.metrics.filter((m) => m === leadM || (size(m) && size(leadM) && Math.max(size(m), size(leadM)) / Math.min(size(m), size(leadM)) <= 20));
  // a result over every month, an answer about some of them ("from September 2025 to September 2026"): the
  // picture is those months, the ones the answer's numbers are from
  const named = monthsIn(answer), month = best.by.find((d) => /:month$/.test(d));
  const picked = month && named.length >= 2 ? best.rows.filter((row) => named.includes(String(row[best!.columns.indexOf(month)]).slice(0, 7))) : [];
  const rows = picked.length >= 2 ? picked : best.rows;
  const vals = (d: string) => new Set(rows.map((row) => row[best!.columns.indexOf(d)])).size;
  const dims = best.by.filter((d) => vals(d) > 1 || best!.by.length === 1);
  const kind: VisualKind = !dims.length ? (metrics.length > 1 ? "measures" : "number")
    : dims.length === 1 ? (vals(dims[0]) === 2 ? "slope" : PERIOD.test(dims[0]) ? "trend" : "leaderboard")
    : kindsFor(dims, (c) => PERIOD.test(c))[0]?.kind ?? "heatmap";
  const ordered = dims.length === 2 ? kindsFor(dims, (c) => PERIOD.test(c))[0]?.dims ?? dims : dims;
  const scope = Object.entries(best.filters).filter(([k]) => !dims.includes(k)).map(([k, v]) => `${said(k)}: ${shown(k, v)}`).join(" · ");
  // a line or a ranking draws one measure: the picture names only what it draws
  const drawn = kind === "trend" || kind === "leaderboard" ? [leadM] : metrics;
  const filters = { ...best.filters, ...(picked.length >= 2 && month ? { [month]: named.filter((x) => picked.some((row) => String(row[best!.columns.indexOf(month)]).startsWith(x))) } : {}) };
  return { kind, scope, data: [{ columns: best.columns, rows }], metrics: drawn, dims: ordered, filters };
}

/** The month an answer is about: the latest it names ("from September 2025 to September 2026" -> "2026-09"). */
const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const monthsIn = (t: string) => [...new Set([...t.matchAll(/\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(20\d{2})\b/gi)]
  .map((m) => `${m[2]}-${String(MONTHS.indexOf(m[1].toLowerCase()) + 1).padStart(2, "0")}`))].sort();
const monthIn = (t: string) => monthsIn(t).at(-1);

/** An answer's closing offer ("Would you like a split by drug class or the claim rows?") as choices to click. */
const OFFER = /^(?:would you like|do you want|want me to|shall i|should i|i can also|if you(?:'d| would) like,? i can)\b\s*(?:me to\s*)?(.*?)\??\s*$/i;
function offersOf(answer: string): { body: string; offers: string[] } {
  const paras = answer.trim().split(/\n+/), last = paras.at(-1)?.trim() ?? "", m = last.match(OFFER);
  if (!m || paras.length < 2) return { body: answer, offers: [] };
  const offers = m[1].split(/,(?!\d)\s*(?:or\s+)?|\s+or\s+/).map((x) => x.replace(/^(a|an|the)\s+/i, "").replace(/[.?]$/, "").trim()).filter((x) => x.length > 3);
  // what comes before the offer stays exactly as written: a table in it is lines that belong together
  const text = answer.trimEnd();
  return offers.length ? { body: text.slice(0, text.lastIndexOf("\n")).trimEnd(), offers: offers.slice(0, 3) } : { body: answer, offers: [] };
}

/** What a chart is computed under: the trail's focus, but for a pick on its own columns (it keeps showing every
    value, the picked one marked) and a period on a chart over periods (a line by year is not narrowed to one month). */
const overPeriods = (v: View, k: string) => PERIOD.test(k) && v.dims.some((d) => PERIOD.test(d) && d.split(":")[0] === k.split(":")[0]);
const filtersOf = (v: View, f: Focus) => ({ ...Object.fromEntries(f.filter(([k]) => !v.own.includes(k) && !overPeriods(v, k))), ...v.filters });

/** A chart as another kind: its columns reordered as that kind reads them, the old one kept for Undo. */
function asKind(v: View, kind: VisualKind, dims = v.queries[0] ?? v.dims, additive = true): View | null {
  const opt = kindsFor(dims, (c) => PERIOD.test(c), additive).find((o) => o.kind === kind);
  return opt ? { ...v, kind, dims: opt.dims, queries: [opt.dims], own: opt.dims, was: v.was ?? v } : null;
}

/** The pictures for an answer: up to three of different kinds, the first the one the question asks for most.
    From the answer it takes the measures and the columns it settled on; the question's words (and the
    trail's comparison) pick the kinds. A measure that only exists month by month is drawn over months. */
async function layout(model: string, question: string, lead: ExhibitData, focus: Focus, dates: string[], types: Record<string, string>,
  all: PbiMetric[], labels: Record<string, string>, carry?: Intent, context?: string): Promise<View[]> {
  const needs = (k: string) => all.find((x) => x.key === k)?.needs;
  const bare = (k: string) => !needs(k)?.by?.length && !Object.values(needs(k)?.params ?? {}).some((x) => x.default == null);
  const monthly = lead.metrics.filter((k) => !bare(k) && needs(k)!.by.length === 1 && PERIOD.test(needs(k)!.by[0]) && !Object.keys(needs(k)!.params ?? {}).length);
  const metrics = lead.metrics.filter(bare).slice(0, 3);
  const first = metrics[0] ?? monthly[0];
  if (!first) return [];
  const d = await pbi.metric(model, first).catch(() => null);
  const columns = d?.columns ?? [];
  const own = new Set(d?.tables ?? []);
  const date = columns.find((c) => dates.includes(c));
  const table = (c: string) => c.replace(/\[.*$/, "").replace(/^'|'$/g, "");
  const field = (c: string) => c.replace(/^.*\[|\].*$/g, "");
  const calendar = new Set(columns.filter((c) => dates.includes(c)).map(table));
  // over time is by month (an answer by day still trends and has seasons by month)
  const P = monthly.length ? needs(monthly[0])!.by[0] : date ? `${date}:month` : lead.by.find((b) => PERIOD.test(b));
  const day = date ? `${date}:day` : undefined;
  const q = question.toLowerCase();
  const V = versusOf(focus)?.[0];
  // the columns to draw by: those the question names (its words win), then the answer's own, then places for a "where",
  // then other tables' before the measure's own; one per column name (two tables' Product are one)
  const dims = columns.filter((c) => !dates.includes(c) && isDimension(c, types[c]) && !calendar.has(table(c)));
  // named in the question, in the order named ("from category to product")
  // the columns a text names, in the order named ("from category to product"); a word for a kind of column
  // ("demography", "geography") stands for its columns
  const at = (t: string, name: string) => t.search(new RegExp(`\\b${name.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`));
  const nameIn = (t: string) => [
    ...(/demograph|customer profile|\bwho (buys|bought|are)\b/.test(t) ? dims.filter((c) => PEOPLE.test(field(c))) : []),
    // people named by what they are: women, men -> gender; young, older -> age
    ...(/\b(wom[ae]n|m[ae]n|females?|males?|ladies|gentlemen)\b/.test(t) ? dims.filter((c) => /gender|\bsex\b/i.test(field(c))) : []),
    ...(/\b(young(er)?|old(er)?|seniors?|youth|elderly)\b/.test(t) ? dims.filter((c) => /^age|age ?(band|group|range)/i.test(field(c))) : []),
    ...(/geograph|\bregions?\b|\blocations?\b/.test(t) ? dims.filter((c) => PLACE.test(field(c))) : []),
    // whole words only: "Medicare Advantage" does not name Age
    ...dims.filter((c) => at(t, said(c)) >= 0).sort((a, b) => at(t, said(a)) - at(t, said(b))),
  ];
  // a value named stands for its column ("PowerApps" -> Product): the business's usual columns are looked through
  const looked = dims.filter((c) => BUSINESS.test(field(c)) && !PEOPLE.test(field(c))).filter((c, i, a) => a.findIndex((x) => field(x) === field(c)) === i).slice(0, 6);
  const valued = (await Promise.all(looked.map((c) => pbi.values(model, c).then((r) => ({ c, at: Math.min(...r.values
    .map((v) => String(v ?? "").toLowerCase()).filter((v) => v.length > 2).map((v) => q.search(new RegExp(`\\b${v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`))).filter((i) => i >= 0)) }))
    .catch(() => ({ c, at: Infinity })))))
    .filter((x) => x.at < Infinity).sort((a, b) => a.at - b.at).map((x) => x.c);
  // a follow-up that names none ("review it again") means the columns of the question before
  let named = [...nameIn(q), ...valued];
  if (!named.length && context) named = nameIn(context.toLowerCase());
  // the columns to draw by: those named (the question's words win), then the answer's own, then places for a
  // "where", then the business's usual cuts; people (gender, age) only when asked for. One per column name.
  const rest = [...dims.filter((c) => !own.has(table(c))), ...dims.filter((c) => own.has(table(c)))].filter((c) => !PEOPLE.test(field(c)));
  // the answer's own columns count, but not people's (an age, a gender) the question did not ask for: a driver path
  // that passed through Age is no reason to draw Total PMPM by every single age
  const ordered = [...named, ...lead.by.filter((b) => !PERIOD.test(b) && (!PEOPLE.test(field(b)) || named.includes(b))),
    ...(/\bwhere\b/.test(q) ? rest.filter((c) => PLACE.test(field(c))) : []),
    ...rest.filter((c) => BUSINESS.test(field(c))), ...rest.filter((c) => !BUSINESS.test(field(c)))]
    // a part of a date (a Month or Year column) is the trend's, not something to rank or split by
    .filter((c, i, a) => c !== V && !/^(month|year|quarter|week|day|date)/i.test(field(c)) && a.findIndex((x) => field(x) === field(c)) === i);
  // the second column: the one named next, else one of the first's own table (Category with Product: what it is made of)
  const A = ordered[0];
  const B = named.length >= 2 || !A ? ordered[1] : ordered.slice(1).find((c) => table(c) === table(A)) ?? ordered[1];
  const C = ordered.find((c) => c !== A && c !== B);
  const M = labels[metrics[0] ?? first] ?? first, m1 = metrics.slice(0, 1);
  const lower = (c: string) => said(c).toLowerCase();
  const view = (kind: VisualKind, title: string, queries: string[][], dimsOf: string[], ms = m1, keepAll: string[] = dimsOf): View =>
    ({ id: "", kind, title, metrics: ms, queries, dims: dimsOf, own: keepAll.filter((x) => x !== V), filters: {} });

  if (!metrics.length) {  // only month by month: its trend and its seasons
    const L = labels[first] ?? first;
    return P ? [view("trend", `${L}, month by month`, [[P]], [P], [first]), view("seasons", `${L} by month of the year`, [[P]], [P], [first])].map((v, i) => ({ ...v, id: `${v.kind}${i}` })) : [];
  }
  const ratio = RATE.test(M);
  const second = all.find((x) => x.key !== metrics[0] && bare(x.key) && x.status === "captured" && !/%|rate|ratio|variance/i.test(x.label))?.key;
  const pictures: Record<Intent, () => (View | null)[]> = {
    rank: () => [A ? view("leaderboard", `Where ${M} is highest, by ${said(A)}`, [[A]], [A]) : null,
      A && P ? view("trend", `How the top ${lower(A)} values moved`, [[P, A]], [P, A]) : null,
      A && B && !ratio ? view("treemap", `What each ${lower(A)} is made of`, [[A, B]], [A, B]) : null],
    trend: () => [P ? view("trend", `${M}, ${/:month$/.test(P) ? "month by month" : `by ${P.match(PERIOD)![1]}`}`, [[P]], [P]) : null, P && /:month$/.test(P) ? view("seasons", `${M} by month of the year`, [[P]], [P]) : null,
      P && A && !ratio ? view("mix", `How the mix of ${M} by ${lower(A)} shifted`, [[P, A]], [P, A]) : null],
    compare: () => V ? [view("versus", `${M} and more, side by side`, [[V]], [V], metrics, []),
      P ? view("gap", "The gap, month by month", [[P, V]], [P, V], m1, [P]) : null,
      A ? view("gap", `Where the gap comes from, by ${said(A)}`, [[A, V]], [A, V], m1, [A]) : null]
      : [A ? view("leaderboard", `${M} by ${said(A)}`, [[A]], [A]) : null, A && P ? view("heatmap", `${M} across ${lower(A)} and months`, [[A, P]], [A, P]) : null],
    drivers: () => [A && P && !ratio ? view("waterfall", `What moved ${M}, by ${said(A)}`, [[A, P]], [A, P]) : null,
      B && P && !ratio ? view("waterfall", `What moved ${M}, by ${said(B)}`, [[B, P]], [B, P]) : null,
      A && P ? view("heatmap", `${M} by ${lower(A)}, against its own average`, [[A, P]], [A, P]) : null],
    mix: () => [A ? (/\bdonut\b|\bpie\b/.test(q) || !B ? view("donut", `${M} by ${said(A)}`, [[A]], [A]) : view("treemap", `What ${M} is made of`, [[A, B]], [A, B])) : null,
      P && A ? view("mix", `How the mix shifted, by ${said(A)}`, [[P, A]], [P, A]) : null,
      A && B ? view("sankey", `How ${M} flows from ${lower(A)} to ${lower(B)}`, [[A, B]], [A, B]) : null],
    flow: () => {
      const three = new Set(named.map(field)).size >= 3 && C ? [A, B, C] : [A, B];
      return [A && B ? view("sankey", `How ${M} flows ${three.length === 3 ? `across ${lower(A)}, ${lower(B)} and ${lower(C!)}` : `from ${lower(A)} to ${lower(B)}`}`, [three], three) : null,
        A && B && !ratio ? view("treemap", `What each ${lower(A)} is made of`, [[A, B]], [A, B]) : null];
    },
    matrix: () => [A && B ? view("heatmap", `${M} across ${lower(A)} and ${lower(B)}`, [[A, B]], [A, B]) : null,
      A && B ? view("sankey", `How ${M} flows from ${lower(A)} to ${lower(B)}`, [[A, B]], [A, B]) : null],
    relation: () => {
      const pair = metrics.length > 1 ? metrics : second ? [metrics[0], second] : [];
      return [A && pair.length > 1 ? view("scatter", `${labels[pair[0]] ?? pair[0]} against ${labels[pair[1]] ?? pair[1]}, by ${said(A)}`, [[A]], [A], pair.slice(0, 3)) : null,
        A ? view("leaderboard", `${M} by ${said(A)}`, [[A]], [A]) : null];
    },
    spread: () => [A && P ? view("boxplot", `How steady ${M} is month to month, by ${said(A)}`, [[A, P]], [A, P]) : null,
      A && P ? view("heatmap", `${M} by ${lower(A)}, against its own average`, [[A, P]], [A, P]) : null],
    daily: () => [day && latestYear ? { ...view("calendar", `${M}, day by day in ${latestYear}`, [[day]], [day]), filters: { [`${date}:year`]: latestYear } } : null,
      P ? view("seasons", `${M} by month of the year`, [[P]], [P]) : null],
    season: () => [P && /:month$/.test(P) ? view("seasons", `${M} by month of the year`, [[P]], [P]) : null, P ? view("trend", `${M}, month by month`, [[P]], [P]) : null],
  };
  let intents = INTENTS.filter(([, re]) => re.test(q)).map(([i]) => i);
  // "is it significant?": the agent's test is the answer (drawn first, see ask); beside it, the columns against each other
  if (SIGNIFICANT.test(q) && !intents.includes("matrix")) intents.unshift("matrix");
  // days come capped, oldest first: a calendar asks for the latest year's only
  const latestYear = intents.includes("daily") && date
    ? await pbi.compute(model, { metrics: [first], by: [`${date}:year`], filters: {} }).then((r) => r.rows.map((x) => x[0]).sort().at(-1)).catch(() => undefined) : undefined;
  // an answer held to two periods (Sep 2026 against Sep 2025) is about the change between them
  const between = focus.some(([k, v]) => PERIOD.test(k) && Array.isArray(v) && v.length === 2);
  if ((lead.kind === "explain" || between || carry === "drivers") && !intents.includes("drivers")) intents.unshift("drivers");
  if (V && !intents.includes("compare")) intents = intents.length ? [intents[0], "compare", ...intents.slice(1)] : ["compare"];
  if (!intents.length) {  // no telling words: the answer's own shape decides
    const by = lead.by.filter((b) => !PERIOD.test(b)), overTime = lead.by.some((b) => PERIOD.test(b));
    intents = by.length >= 2 ? ["matrix", "rank"] : by.length ? (overTime ? ["spread", "trend"] : ["rank", "mix"]) : overTime ? ["trend", "season"] : ["trend", "rank"];
  }
  // the first picture of each intent asked for, then their others; never two of one kind (but for gaps and
  // waterfalls, which differ by what they split)
  const lists = intents.map((i) => pictures[i]().filter((v): v is View => !!v));
  const order = [...lists.map((l) => l[0]), ...lists.flatMap((l) => l.slice(1))].filter(Boolean);
  const out: View[] = [];
  for (const v of order) {
    if (out.length < 3 && !out.some((x) => (v.kind === "gap" || v.kind === "waterfall" ? sig(x) === sig(v) : x.kind === v.kind))) out.push({ ...v, id: `${v.kind}${out.length}` });
  }
  // a total with nothing else to it reads best as a number
  if (!out.length) out.push({ ...view("number", M, [[], ...(P ? [[P]] : [])], P ? [P] : [], metrics, []), id: "number0" });
  return out;
}

/** One question of the conversation, with what came of it. `repeat`: its pictures are all above already. */
/** `measure`: what the turn was about, for a follow-up that names none. */
type Turn = { id: string; question: string; answer: string; views: View[]; focus: Focus; pending?: boolean; step?: string; chat?: boolean; error?: string; repeat?: boolean; note?: string; measure?: string[]; picture?: Picture };
const newTurn = (question: string): Turn => ({ id: Math.random().toString(36).slice(2, 10), question, answer: "", views: [], focus: [], pending: true, step: "Reading the question" });
const SLOW_STEP = "Still on it. You can keep asking";
/** A kept turn as saved (views under `specs`); one saved before views existed opens with its answer only. */
const fromSaved = (t: { id: string; question: string; answer: string; specs: unknown[]; focus: Focus; error?: string }): Turn =>
  ({ id: t.id, question: t.question, answer: t.answer, focus: t.focus, error: t.error, pending: (t as any).pending, chat: (t as any).chat, note: (t as any).note, measure: (t as any).measure, picture: (t as any).picture, views: (t.specs as any[]).filter((v) => v && "queries" in v && "kind" in v) });

/** A turn's answer and its views: each runs here, re-run when its trail changes. */
function Answer({ turn, model, labels, cache, live, step, first, setFocus, setViews, setPicture, onAsk, dates, types }: {
  turn: Turn; model: string; labels: Record<string, string>; cache: Map<string, Result>; live: boolean; step: string; first: boolean;
  setFocus: (f: Focus | ((f: Focus) => Focus)) => void; setViews: (change: (v: View[]) => View[]) => void; setPicture: (p: Picture) => void; onAsk: (q: string) => void;
  dates: string[]; types: Record<string, string>;
}) {
  const { question, answer, views, focus, error, picture } = turn;
  // the answer's picture is the agent's own result; without one, the first chart drawn for it stands in. Beside it,
  // the answer's own chart stands alone: every other view waits under "More views" and runs only when opened
  const lead = picture ? undefined : views.find((v) => !v.table);
  const linked = new Set(picture?.metrics ?? lead?.metrics ?? []);
  const rest = views.filter((v) => v !== lead && !v.table);
  // a chart of the same measure over the same columns as the answer's picture is that picture again: left out
  const shape = (dims: string[]) => [...dims].sort().join("|");
  const top = picture ?? lead;
  const overTime = (dims: string[]) => dims.length === 1 && PERIOD.test(dims[0]);  // a line of the measure alone, at any grain
  const again = (v: View) => !!top && linked.has(v.metrics[0]) && (shape(v.dims) === shape(top.dims) || (overTime(v.dims) && overTime(top.dims)));
  const supports: View[] = [];
  const more = rest.filter((v) => !again(v));
  const tables = views.filter((v) => v.table);
  const [openMore, setOpenMore] = useState(false);
  const shownIds = [lead, ...supports, ...(openMore ? more : [])].filter(Boolean).map((v) => v!.id).join(",");
  const { body, offers } = useMemo(() => (live ? { body: answer, offers: [] } : offersOf(answer)), [answer, live]);
  // the answer's picture can be shown another way too: a before-and-after side by side or as a table
  const pictureKinds = !picture ? [] : picture.kind === "slope" ? [{ kind: "slope" as VisualKind, dims: picture.dims }, { kind: "versus" as VisualKind, dims: picture.dims }, { kind: "table" as VisualKind, dims: picture.dims }]
    : picture.kind === "versus" || picture.kind === "table" && picture.dims.length === 1 && new Set(picture.data[0].rows.map((r) => r[picture.data[0].columns.indexOf(picture.dims[0])])).size === 2
      ? [{ kind: "slope" as VisualKind, dims: picture.dims }, { kind: "versus" as VisualKind, dims: picture.dims }, { kind: "table" as VisualKind, dims: picture.dims }]
      : picture.kind === "measures" || picture.kind === "number" ? [{ kind: picture.kind, dims: [] }, { kind: "table" as VisualKind, dims: [] }]
        : kindsFor(picture.dims, (c) => PERIOD.test(c), !RATE.test(labels[picture.metrics[0]] ?? picture.metrics[0]));
  /** The chart-kind switch: what a chart can be shown as, the current one pressed. */
  const toggle = (kinds: VisualKind[], current: VisualKind, choose: (k: VisualKind) => void) => (
    <span role="group" aria-label="Show as" className="flex overflow-hidden rounded-lg ring-1 ring-[var(--d-rule)]">
      {kinds.map((k) => (
        <button key={k} aria-pressed={k === current} onClick={() => k !== current && choose(k)}
          className={`px-2 py-1 text-[11.5px] ${k === current ? "bg-[var(--d-navy)] font-semibold text-white" : "text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]"}`}>{KIND_NAME[k]}</button>
      ))}
    </span>
  );
  const switchTo = (v: View, kind: VisualKind) => setViews((vs) => vs.map((x) => (x.id === v.id ? asKind(x, kind) ?? x : x)));

  const [runs, setRuns] = useState<Record<string, Run>>({});

  // every view, re-run whenever the trail changes: one leaves alone a pick on its own columns, so it keeps
  // showing every value and marks the picked one
  useEffect(() => {
    const wanted = new Set(shownIds.split(","));
    for (const v of views) {
      if (v.table || !wanted.has(v.id)) continue;
      const filters = filtersOf(v, focus);
      const keys = v.queries.map((by) => key(v.metrics, by, filters));
      const hits = keys.map((k) => cache.get(k));
      if (hits.every(Boolean)) { setRuns((r) => ({ ...r, [v.id]: { results: hits as Result[] } })); continue; }  // eslint-disable-line react-hooks/set-state-in-effect
      setRuns((r) => ({ ...r, [v.id]: { results: r[v.id]?.results, loading: true } }));
      Promise.all(v.queries.map((by, i) => hits[i] ? Promise.resolve(hits[i]!) : pbi.compute(model, { metrics: v.metrics, by, filters }).then((res) => { cache.set(keys[i], res); return res; })))
        .then((results) => setRuns((r) => ({ ...r, [v.id]: { results } })))
        .catch((err) => setRuns((r) => ({ ...r, [v.id]: { error: String(err?.message ?? err) } })));
    }
  }, [views, focus, model, cache, shownIds]);

  // a pick replaces any earlier one on that column
  const pick = (dim: string, value: unknown) => setFocus((f) => [...f.filter(([k]) => k !== dim), [dim, value]]);
  // a click on a mark asks what to do with it: focus every view, drill into it, or see its rows
  const pointer = useRef({ x: 0, y: 0 });
  const [menu, setMenu] = useState<{ x: number; y: number; v: View; dim: string; value: unknown } | null>(null);
  const [drill, setDrill] = useState<{ metric: string; base: Record<string, unknown>; start: DrillStart } | null>(null);
  const drillFrom = (v: View, start: DrillStart, dim?: string) => {
    const base = filtersOf(v, focus);
    if (dim) delete base[dim];
    setDrill({ metric: v.metrics[0], base, start });
    setMenu(null);
    setTimeout(() => document.getElementById(`drill-${turn.id}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 80);
  };
  // a chart as a dashboard keeps it: its question (measures, columns, filters) and its kind, re-run when opened
  const chartOf = (v: View): Chart => ({
    ...tileOf({ kind: "compute", columns: [], rows: [], metrics: v.metrics, by: v.dims, filters: filtersOf(v, focus), labels, origin: "agent" }),
    title: v.title, chart: v.kind as VisualKind, dims: v.dims });
  const pictureChart = (): Chart[] => picture ? [{
    ...tileOf({ kind: "compute", columns: [], rows: [], metrics: picture.metrics, by: picture.dims, filters: picture.filters ?? {}, labels, origin: "agent" }),
    title: pictureTitle(picture, labels), chart: picture.kind, dims: picture.dims }] : [];
  const everything = () => [...pictureChart(), ...(lead ? [lead] : []).concat(supports).map(chartOf)];
  // a thread on any chart of the answer, kept in this browser with the conversation
  const threads = useMemo(() => browserThreads(model), [model]);
  const anchorOf = (v: View): Anchor => ({ id: `${turn.id}:${v.id}`, title: v.title, metrics: v.metrics, dims: v.dims, filters: filtersOf(v, focus), kind: v.kind as VisualKind });
  const picked = Object.fromEntries(focus);

  const tile = (v: View, place: "answer" | "support" | "more") => {
    const run: Run | undefined = v.table ? { results: [{ ...v.table, sql: "", ms: 0, warehouse: "", truncated: false, notes: [] }] } : runs[v.id];
    const hero = place === "answer", rs = run?.results;
    // until a re-run view's rows arrive, rows without its columns are not drawn
    const ready = !!rs && rs.length === (v.table ? 1 : v.queries.length) && v.queries.every((by, j) => by.every((c) => rs[j].columns.includes(c)));
    const mine = focus.find(([k]) => v.own.includes(k) && !PERIOD.test(k));
    const kinds = v.table ? [] : kindsFor(v.queries[0] ?? v.dims, (c) => PERIOD.test(c), !RATE.test(labels[v.metrics[0]] ?? v.metrics[0]));
    const note = !hero && ready && !run?.loading && v.kind !== "rows" && rs![0].rows.length ? noteOf(v.kind as VisualKind, rs!, v.metrics, v.dims, labels) : null;
    const gap = v.kind !== "rows" ? EXPORT_GAPS[v.kind as VisualKind] : undefined;
    const t = v.table, at = (c: string) => t!.columns.indexOf(c);
    const textCol = t?.columns.find((c) => t.rows.some((r) => typeof r[at(c)] === "string"));
    const nums = t?.columns.filter((c) => t.rows.every((r) => r[at(c)] === null || typeof r[at(c)] === "number")) ?? [];
    // the figure to rank by: a rate or share when there is one (that is usually the finding), else a numeric
    // column that is not a count or an id
    const numCol = nums.find((c) => /rate|share|pct|percent|ratio|prob/i.test(c)) ?? nums.find((c) => !/count|^n$|_n$|\bid$|rank/i.test(c)) ?? nums[0];
    // a name-and-value table, or values of unlike sizes (units beside a p-value): a list of figures, not a chart
    const sizes = numCol ? t!.rows.map((r) => Math.abs(Number(r[at(numCol)]))).filter((x) => x > 0) : [];
    const figures = !!t && !!textCol && nums.length === 1 && t.rows.length <= 30
      && (/^(metric|measure|stat(istic)?|name|key|item|field|quantity|figure)s?$/i.test(textCol) || (sizes.length > 1 && Math.max(...sizes) / Math.min(...sizes) > 1000));
    return (
      <section key={v.id} id={`view-${turn.id}-${v.id}`} className={hero ? "flex min-w-0 flex-col" : "flex min-w-0 flex-col rounded-2xl bg-[var(--d-sheet)] p-5 shadow-[0_1px_2px_rgba(20,35,56,.06)] ring-1 ring-[var(--d-rule)]"}>
        <header className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <h2 className={`cond mr-auto font-semibold text-[var(--d-ink)] ${hero ? "text-[16px]" : "text-[15px]"}`}>{v.title}</h2>
          {mine && <span className="rounded-full bg-[var(--d-teal-soft)] px-2 py-0.5 text-[12px] font-semibold text-[var(--d-teal)]">{shown(mine[0], mine[1])} picked</span>}
          {kinds.length > 1 && toggle(kinds.map((k) => k.kind), v.kind as VisualKind, (k) => switchTo(v, k))}
          {!v.table && <ThreadButton anchor={() => anchorOf(v)} store={threads} />}
          <ExportData title={v.title} tables={ready ? rs : null} labels={labels} />
          {!v.table && <AddToDashboard model={model} charts={() => [chartOf(v)]} label="Add this chart to a dashboard" compact />}
          {(!run || run.loading) && <span className="pulse-soft text-[12px] text-[var(--d-muted)]">Computing…</span>}
        </header>
        {note && <p className="mt-2.5 rounded-lg bg-[var(--d-teal-soft)] px-3 py-2 text-[13.5px] leading-snug text-[var(--d-ink)]">{note}</p>}
        <div className={`mt-3 min-h-0 flex-1 transition-opacity ${run?.loading && ready ? "opacity-40" : ""}`}>
          {run?.error ? <p className="text-[13px] text-[var(--d-rise-ink)]">{run.error}</p>
            : !ready ? <div className={`${hero ? "h-72" : "h-48"} rounded-xl bg-[var(--d-paper)]`} />
            : !rs![0].rows.length ? <p className="py-6 text-[13px] text-[var(--d-muted)]">Nothing here for this focus.</p>
            : v.kind === "rows" && figures ? (
              // a list of figures (units, a rate, a p-value): each by its name, not bars on one scale
              <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-4">
                {t!.rows.map((r, j) => (
                  <div key={j} className="min-w-0">
                    <dt className="truncate text-[12.5px] text-[var(--d-muted)]">{String(r[at(textCol!)])}</dt>
                    <dd className="num mt-0.5 text-[22px] font-semibold leading-tight text-[var(--d-ink)]">{figure(String(r[at(textCol!)]), r[at(numCol!)])}</dd>
                  </div>
                ))}
              </dl>
            ) : v.kind === "rows" ? (
              <div className="space-y-3">
                {textCol && numCol && rs![0].rows.length <= 40 && <Visual kind="leaderboard" data={rs!} metrics={[numCol]} dims={[textCol]} labels={labels} />}
                <div className="max-h-72 overflow-auto"><ResultTable columns={rs![0].columns} rows={rs![0].rows} labels={labels} /></div>
              </div>
            ) : (
              <Visual kind={v.kind} data={rs!} metrics={v.metrics} dims={v.dims} labels={labels} picked={picked} height={hero ? 330 : 260}
                onPick={(dim, value) => setMenu({ ...pointer.current, v, dim, value })} />
            )}
        </div>
        {/* the test's own numbers (p-value, interval, effect): for a statistical question, the answer itself */}
        {v.ran?.numbers && Object.values(v.ran.numbers).some((x) => typeof x === "number") && (
          <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 border-t border-[var(--d-rule)] pt-4 sm:grid-cols-3 lg:grid-cols-5">
            {Object.entries(v.ran.numbers).filter(([, x]) => typeof x === "number").slice(0, 10).map(([k, x]) => (
              <div key={k} className="min-w-0">
                <dt className="truncate text-[12px] text-[var(--d-muted)]" title={k}>{k.replace(/_/g, " ")}</dt>
                <dd className="num mt-0.5 text-[18px] font-semibold text-[var(--d-ink)]">{figure(k, x)}</dd>
              </div>
            ))}
          </dl>
        )}
        {v.ran && (
          <details className="mt-3 text-[12px] text-[var(--d-muted)]">
            <summary className="cursor-pointer select-none hover:text-[var(--d-ink)]">How the analysis was run</summary>
            <div className="mt-2 space-y-2 text-[12.5px] text-[var(--d-ink-2)]">
              <p>
                {v.ran.method?.test ? <><b className="text-[var(--d-ink)]">{v.ran.method.test}</b>{v.ran.method.n ? ` on ${num(v.ran.method.n)} rows` : ""}. </> : null}
                {v.ran.rows !== undefined ? `Python in the analysis sandbox, on a snapshot of ${num(v.ran.rows)} rows` : "Python in the analysis sandbox"}
                {v.ran.row_security ? ", under your row-level security" : ""}{v.ran.isolated ? ", isolated from the network" : ""}
                {v.ran.seconds !== undefined ? `, in ${v.ran.seconds.toFixed(1)} s` : ""}.{v.ran.commit ? ` Kept as ${v.ran.commit.slice(0, 8)}.` : ""}
              </p>
              {!!v.ran.method?.assumptions?.length && <p>Assumes: {v.ran.method.assumptions.join("; ")}.</p>}
              {!!v.ran.method?.caveats?.length && <p>Caveats: {v.ran.method.caveats.join("; ")}.</p>}
              {!!v.ran.checks?.length && (
                <ul className="space-y-0.5">{v.ran.checks.map((c, j) => <li key={j} className={c.passed ? "" : "text-[var(--d-rise-ink)]"}>{c.passed ? "✓" : "✗"} {c.check}</li>)}</ul>
              )}
              {v.ran.code && <pre className="max-h-80 overflow-auto rounded-lg bg-[var(--d-paper)] p-3 font-mono text-[11.5px] leading-relaxed text-[var(--d-ink)]">{v.ran.code}</pre>}
            </div>
          </details>
        )}
        {gap && <p className="mt-2 text-[12px] text-[var(--d-muted)]">Export: {gap}.</p>}
        {ready && rs![0].sql && (
          <details className="mt-3 text-[12px] text-[var(--d-muted)]">
            <summary className="cursor-pointer select-none hover:text-[var(--d-ink)]">How this was computed</summary>
            <div className="mt-2 space-y-2">{rs!.map((r, j) => <SqlBlock key={j} sql={r.sql} warehouse={r.warehouse} ms={r.ms} />)}</div>
          </details>
        )}
      </section>
    );
  };

  return (
    <article id={`turn-${turn.id}`} className={first ? "" : "mt-12 border-t border-[var(--d-rule)] pt-10"}>
      <p className="text-[13px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">{first ? "You asked" : "Then you asked"}</p>
      <h1 className="cond mt-1 text-[22px] font-semibold leading-snug text-[var(--d-ink-2)]">{question}</h1>
      {/* the answer leads the turn, its own picture beside it: the numbers it states, drawn as the agent computed them */}
      <div className={`mt-4 overflow-hidden rounded-2xl border border-[var(--d-rule)] bg-[var(--d-sheet)] shadow-[0_1px_2px_rgba(0,0,0,.04)] ${picture || lead ? "grid lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]" : "max-w-4xl"}`}>
        <div className="flex flex-col px-7 py-6">
          <p className="mb-2 text-[12px] font-semibold uppercase tracking-[.08em] text-[var(--d-teal)]">{live && !answer ? "Working on it" : "The answer"}</p>
          <div className="text-[17px] leading-relaxed text-[var(--d-ink-2)] [&_table]:mb-4 [&_table]:w-full [&_table]:text-[15px] [&_td]:border-t [&_td]:border-[var(--d-rule)] [&_td]:py-1.5 [&_td]:pr-3 [&_td:last-child]:pr-0 [&_td:first-child]:text-[var(--d-ink)] [&_th]:pb-1.5 [&_th]:pr-3 [&_th:last-child]:pr-0 [&_th]:text-[12.5px] [&_th]:font-semibold [&_th]:text-[var(--d-muted)] [&_p]:mb-3 [&_p:last-child]:mb-0 [&>p:first-child]:text-[21px] [&>p:first-child]:font-medium [&>p:first-child]:leading-snug [&>p:first-child]:text-[var(--d-ink)] [&_strong]:font-semibold [&_strong]:text-[var(--d-ink)] [&_ul]:ml-5 [&_ul]:list-disc">
            {body ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown> : live ? <p className="pulse-soft text-[var(--d-muted)]">{step}…</p> : null}
          </div>
          {live && answer && step && <p className="pulse-soft mt-2 text-[13px] text-[var(--d-muted)]">{step}…</p>}
          {offers.length > 0 && (
            // the agent's closing offer, as choices
            <div className="mt-5 flex flex-wrap gap-2">
              {offers.map((o) => (
                <button key={o} onClick={() => onAsk(`Show me ${o}`)} className="rounded-full bg-[var(--d-sheet)] px-3.5 py-1.5 text-left text-[13.5px] text-[var(--d-ink)] ring-1 ring-[var(--d-rule)] hover:ring-[var(--d-teal)]">
                  {o.charAt(0).toUpperCase() + o.slice(1)}
                </button>
              ))}
            </div>
          )}
        </div>
        {picture ? (
          <figure className="flex min-w-0 flex-col border-t border-[var(--d-rule)] bg-[var(--d-paper)]/40 p-5 lg:border-l lg:border-t-0">
            <figcaption className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
              <span className="cond mr-auto text-[16px] font-semibold text-[var(--d-ink)]">{pictureTitle(picture, labels)}</span>
              {pictureKinds.length > 1 && toggle(pictureKinds.map((k) => k.kind), picture.kind, (k) => setPicture({ ...picture, kind: k, dims: pictureKinds.find((o) => o.kind === k)!.dims }))}
              <ThreadButton anchor={() => ({ id: `${turn.id}:picture`, title: pictureTitle(picture, labels), metrics: picture.metrics, dims: picture.dims, filters: picture.filters ?? {}, kind: picture.kind })} store={threads} />
              <ExportData title={pictureTitle(picture, labels)} tables={picture.data} labels={labels} />
              <AddToDashboard model={model} charts={pictureChart} label="Add this chart to a dashboard" compact />
            </figcaption>
            {picture.scope && <p className="mt-0.5 text-[12.5px] text-[var(--d-muted)]">{picture.scope}</p>}
            <div className="mt-2 min-h-0 flex-1">
              <Visual kind={picture.kind} data={picture.data} metrics={picture.metrics} dims={picture.dims} labels={labels} height={280} />
            </div>
          </figure>
        ) : lead ? (
          <div className="border-t border-[var(--d-rule)] bg-[var(--d-paper)]/40 p-5 lg:border-l lg:border-t-0"
            onPointerDownCapture={(e) => { pointer.current = { x: e.clientX, y: e.clientY }; }}>{tile(lead, "answer")}</div>
        ) : null}
      </div>
      {error && <p className="mt-3 rounded-lg bg-[var(--d-rise-soft)] px-3 py-2 text-[13px] text-[var(--d-rise-ink)]">{error}</p>}
      {!live && !views.length && !error && !turn.chat && <p className="mt-3 text-[13px] text-[var(--d-muted)]">{turn.repeat ? "The pictures for this are already on the page, above." : "This answer did not come from a measure, so there is nothing to draw. Try asking about a number."}</p>}

      {turn.note && views.length > 0 && <p className="mt-4 rounded-lg bg-[var(--d-teal-soft)] px-3 py-2 text-[13px] text-[var(--d-teal)]">{turn.note}</p>}
      {views.length > 0 && (
        <>
          <div className="sticky top-0 z-10 -mx-8 mt-6 flex flex-wrap items-center gap-2 bg-[var(--d-ground)]/95 px-8 py-3 backdrop-blur">
            <span className="text-[13px] text-[var(--d-muted)]">Showing</span>
            <button onClick={() => setFocus([])} className={`rounded-full px-3 py-1 text-[13px] ${focus.length ? "text-[var(--d-teal)] hover:underline" : "bg-[var(--d-navy)] font-semibold text-white"}`}>Everything</button>
            {focus.map(([k, v], i) => (
              <span key={k} className="flex items-center gap-1.5">
                <span className="text-[var(--d-muted)]" aria-hidden>›</span>
                <span className={`flex items-center gap-1 rounded-full py-1 pl-3 pr-1.5 text-[13px] ${i === focus.length - 1 ? "bg-[var(--d-navy)] font-semibold text-white" : "bg-[var(--d-sheet)] text-[var(--d-ink)] ring-1 ring-[var(--d-rule)]"}`}>
                  <button onClick={() => setFocus(focus.slice(0, i + 1))}>{said(k)}: {shown(k, v)}</button>
                  <button aria-label={`Stop focusing on ${shown(k, v)}`} onClick={() => setFocus(focus.filter(([x]) => x !== k))} className="rounded-full p-0.5 opacity-70 hover:opacity-100"><X size={12} /></button>
                </span>
              </span>
            ))}
            <span className="ml-auto flex items-center gap-1">
              {views.some((v) => !v.table && v.metrics.length) && (
                <button onClick={() => { const v = views.find((x) => !x.table && x.metrics.length)!; drillFrom(v, { at: Date.now() }); }}
                  className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] text-[var(--d-ink-2)] ring-1 ring-[var(--d-rule)] hover:ring-[var(--d-teal)]"><Layers size={14} />Drill down</button>
              )}
              <AddToDashboard model={model} charts={everything} />
              {[["Share", Share2], ["Repeat", Repeat]].map(([label, Icon]: any) => (
                <button key={label} disabled title={`${label}: not built yet (needs the server side)`} className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] text-[var(--d-muted)] opacity-60"><Icon size={14} />{label}</button>
              ))}
            </span>
          </div>
          {supports.length > 0 && (
            <div className="mt-4 flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-[12px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">Supporting the answer</h2>
            </div>
          )}
          <div className={`mt-3 grid grid-cols-1 gap-4 ${supports.length > 1 ? "lg:grid-cols-2" : ""}`}
            onPointerDownCapture={(e) => { pointer.current = { x: e.clientX, y: e.clientY }; }}>
            {supports.map((v) => tile(v, "support"))}
            {tables.map((v) => <div key={v.id} className="lg:col-span-2">{tile(v, "support")}</div>)}
          </div>
          {more.length > 0 && (
            <div className="mt-4">
              <button onClick={() => setOpenMore((o) => !o)} aria-expanded={openMore}
                className="text-[13.5px] text-[var(--d-ink-2)] hover:text-[var(--d-ink)]">{openMore ? "▾" : "▸"} More views ({more.length}): {more.map((v) => v.title).join(" · ")}</button>
              {openMore && (
                <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2"
                  onPointerDownCapture={(e) => { pointer.current = { x: e.clientX, y: e.clientY }; }}>{more.map((v) => tile(v, "more"))}</div>
              )}
            </div>
          )}
          {drill && (
            <div id={`drill-${turn.id}`}>
              <DrillTree key={drill.start.at} model={model} metric={drill.metric} label={labels[drill.metric] ?? drill.metric} base={drill.base}
                start={drill.start} dates={dates} types={types} onClose={() => setDrill(null)} />
            </div>
          )}
          {menu && (
            <PickMenu x={menu.x} y={menu.y} label={`${said(menu.dim)}: ${shown(menu.dim, menu.value)}`} onClose={() => setMenu(null)}
              onFocus={() => { pick(menu.dim, menu.value); setMenu(null); }}
              onDrill={() => drillFrom(menu.v, { by: menu.dim, value: menu.value, at: Date.now() }, menu.dim)}
              onRows={() => drillFrom(menu.v, { by: menu.dim, value: menu.value, rows: true, at: Date.now() }, menu.dim)} />
          )}
        </>
      )}
    </article>
  );
}

export default function AskPage() {
  const { pbiModel, pbiModels, llm } = useStudio();
  const look = useLook();
  const model = pbiModels.find((m) => m.id === pbiModel);
  const [metrics, setMetrics] = useState<PbiMetric[]>([]);
  const [cols, setCols] = useState<{ dates: string[]; types: Record<string, string> }>({ dates: [], types: {} });
  const [session, setSession] = useState(newSession);
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [error, setError] = useState("");
  const busy = turns.some((t) => t.pending);
  const cache = useRef(new Map<string, Result>());
  /** Each question at work: the box stays free, so there may be several. Left with the chat, picked up when it reopens. */
  const following = useRef(new Set<AbortController>());
  const stopping = useRef(new Set<AbortController>());
  const leave = () => { following.current.forEach((c) => c.abort()); following.current.clear(); };
  const stop = () => following.current.forEach((c) => { stopping.current.add(c); c.abort(); });
  const box = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!pbiModel) return;
    Promise.all([pbi.metrics(pbiModel), pbi.tables(pbiModel)]).then(([r, t]) => {
      setMetrics(r.metrics.filter((m) => m.status === "captured"));
      setCols(columnTypes(t.tables));
    }).catch(() => {});
  }, [pbiModel]);
  const labels = useMemo(() => Object.fromEntries(metrics.map((m) => [m.key, readable(m.label)])), [metrics]);
  const starters = useMemo(() => {
    // a rate or a per-member figure reads as the business's pulse; a raw count (member months) seldom does
    const pulse = (m: { label: string }) => (/pmpm|per |ratio|rate/i.test(m.label) ? 0 : 1);
    const plain = metrics.filter((m) => !m.needs?.by?.length && !Object.keys(m.needs?.params ?? {}).length)
      .sort((a, b) => pulse(a) - pulse(b)).slice(0, 2);
    return plain.length ? [`Where is ${plain[0].label} highest, and how has it moved?`, `Why did ${plain[0].label} change last month?`,
      ...(plain[1] ? [`What is ${plain[1].label} made of?`] : [])] : [];
  }, [metrics]);

  const update = (id: string, change: (t: Turn) => Partial<Turn>) => setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, ...change(t) } : t)));
  const ask = async (text: string) => {
    const message = text.trim();
    if (!message || !pbiModel) return;
    if (!llm) { setError("No language model is configured (API_KEY, API_BASE_URL, CHAT_MODEL in .env)."); return; }
    const turn = newTurn(message), id = turn.id;
    setInput(""); setError("");
    setTurns((ts) => [...ts, turn]);
    // the new question goes to the top of the window, as in a chat; what came before stays above it
    setTimeout(() => document.getElementById(`turn-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    await follow(id, message, session, turns);
  };
  /** One question to its end, in its own turn. Nothing waits for it: the box stays free for the next. */
  const follow = async (id: string, message: string, session: string, turns: Turn[]) => {
    const results: ExhibitData[] = [];
    let slow = false, chat = false;
    const setStep = (step: string) => { if (!slow) update(id, () => ({ step })); };
    const late = setTimeout(() => { setStep(SLOW_STEP); slow = true; }, 15000);
    /** The pictures for what has come back. Choosing them is the slow part, so it starts with the first rows and is
        not done again unless more arrive. */
    const choose = async (rs: ExhibitData[], quiet: boolean) => {
      // the measure it settled on (a "why" answer's too, with the columns it walked down)
      const last = [...rs].reverse().find((r) => (r.kind === "compute" || r.kind === "explain") && r.metrics.length);
      // a period that is a key (no :month) is no thing to draw: its values mean nothing to a reader, and it is no category
      const dated = (p: string) => (p.includes(":") ? [p] : []);
      const lead = last?.explain ? { ...last, by: [...last.explain.steps.map((s) => s.by), ...dated(last.explain.period)] } : last;
      // what the answer narrowed to (Product = Excel) becomes the focus, not a fixed filter: the trail says so,
      // and one click on "Everything" widens it
      const focus: Focus = lead ? Object.entries(lead.filters) : [];
      const views: View[] = [], brought: View[] = [];
      // rows it brought itself (a listing, an analysis), as they are, after the pictures
      rs.filter((r) => r.kind !== "compute" && r.kind !== "explain" && r.columns.length && r.rows.length).forEach((r, i) => brought.push({
        id: `rows${i}`, kind: "rows", title: r.kind === "explore" ? "The rows behind the answer" : "What the analysis found", metrics: [], queries: [], dims: [], own: [], filters: {},
        table: { columns: r.columns, rows: r.rows.slice(0, 200) }, ran: (r as any).analysis }));
      // the same comparison, said plainly: each measure then and now
      const compared = rs.flatMap((r) => (r.explain && !r.explain.period.includes(":") ? [r.explain] : []));
      const n2 = (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 2 });
      if (compared.length) brought.unshift({
        id: "compared", kind: "rows", title: "Then and now", metrics: [], queries: [], dims: [], own: [], filters: {},
        table: { columns: ["Measure", "Before", "Now", "Change"], rows: compared.map((e) => [
          e.label.replace(/_/g, " "), n2(e.total_before), n2(e.total_after),
          e.total_before > 0 ? `${((e.total_after / e.total_before - 1) * 100).toFixed(1)}%` : n2(e.total_after - e.total_before)]) } });
      // no computation of a measure (it ran an analysis, or computed nothing): the measures the question names,
      // or on a follow-up the conversation's own, drawn straight from the measures and said so
      const named = metrics.filter((m) => message.toLowerCase().includes(m.label.toLowerCase())).map((m) => m.key);
      const earlier = [...turns].reverse().map((t) => t.measure ?? t.views.find((v) => v.metrics.length && !v.table)?.metrics.slice(0, 1)).find((m) => m?.length) ?? [];
      // an analysis about no measure in particular ("do women prefer PowerApps?") is drawn from the model's main one
      const main = metrics.find((m) => !m.needs?.by?.length && !Object.keys(m.needs?.params ?? {}).length)?.key;
      const own = named.length ? named : earlier.length ? earlier : brought.length && main ? [main] : [];
      const basis = lead ? { ...lead, metrics: [...new Set([...lead.metrics, ...named])] }
        : own.length ? { kind: "compute", columns: [], rows: [], metrics: own, by: [], filters: {}, labels, origin: "agent" } as ExhibitData : undefined;
      if (basis) {
        if (!quiet) setStep("Choosing the pictures");
        // a follow-up on "it" after a "why" keeps explaining the change ("break it down by age")
        const prior = turns.at(-1)?.views.find((v) => !v.table)?.kind;
        const carry = prior === "waterfall" && /\b(it|this|that|them)\b/i.test(message) ? "drivers" : undefined;
        views.push(...await layout(pbiModel, message, basis, focus, cols.dates, cols.types, metrics, labels, carry, turns.at(-1)?.question));
      }
      // a statistical question's answer is the agent's analysis: it leads
      if (SIGNIFICANT.test(message)) views.unshift(...brought); else views.push(...brought);
      // on a follow-up, a picture the conversation already shows is not drawn again
      const above = new Set(turns.flatMap((t) => t.views.map(sig)));
      const unique = views.filter((v) => v.table || !above.has(sig(v)));
      return { lead, focus, own, brought, views, unique, basis };
    };
    /** What the answer's words settle: its own picture and the month it is about. Cheap, so it is redone at the end. */
    const settle = (chosen: Awaited<ReturnType<typeof choose>>, rs: ExhibitData[], said_: string): Partial<Turn> => {
      const { lead, focus, own, brought, views, unique, basis } = chosen;
      // nothing computed this turn (a question back, an answer from earlier): no picture guessed from its words
      if (!rs.some((r) => r.kind === "compute" || r.kind === "explain")) return { views: [], focus, note: undefined, picture: undefined, repeat: false };
      // an answer about one month ("highest in Illinois, $952 in September 2026"): a chart not over time is of that
      // month too, so its numbers are the answer's, not an average of every month
      const picture = pictureOf(rs, said_), month = monthIn(said_);
      const per = [...(picture?.dims ?? []), ...rs.flatMap((r) => [...r.by, ...Object.keys(r.filters)])].find((d) => /:month$/.test(d));
      const fresh = per && month ? unique.map((v) => v.table || [...v.dims, ...Object.keys(v.filters)].some((d) => PERIOD.test(d)) ? v
        : { ...v, filters: { ...v.filters, [per]: month }, title: `${v.title}, ${periodName(month)}` }) : unique;
      const drawn = own.map((k) => labels[k] ?? k).join(" and ");
      const note = lead || !fresh.some((v) => !v.table) ? undefined : brought.length
        ? `Beside the agent's analysis, these pictures are drawn straight from ${drawn} in the model.`
        : `The agent did not compute this, so these are drawn straight from ${drawn} in the model.`;
      return { views: fresh, focus, note, measure: basis?.metrics.slice(0, 1), repeat: !fresh.length && views.length > 0, picture };
    };
    let answer = "", ended = false;
    let early = null as Promise<{ chosen: Awaited<ReturnType<typeof choose>>; n: number } | null> | null;
    let soon: ReturnType<typeof setTimeout> | undefined;
    const ctrl = new AbortController();
    following.current.add(ctrl);
    try {
      await streamNdjson(pbi.askPath(pbiModel), { message, session_id: session }, (e) => {
        if (e.type === "chunk") { answer += e.data.text ?? ""; update(id, (t) => ({ answer: t.answer + (e.data.text ?? "") })); }
        else if (e.type === "agent_tool_call" && e.data.status === "started") { answer = ""; update(id, () => ({ answer: "" })); if (STEP[e.data.tool]) setStep(STEP[e.data.tool]); }
        else if (e.type === "pbi_result") {
          const d = e.data as any, r: ExhibitData = { ...d, filters: d.filters ?? {}, origin: "agent" };
          results.push(r);
          // the charts start now, while the words are still being written; results arriving together are drawn together
          if (!early && (r.kind === "compute" || r.kind === "explain") && r.metrics.length) {
            clearTimeout(soon);
            soon = setTimeout(() => {
              const rs = [...results], said_ = answer;
              early = choose(rs, true).then((chosen) => {
                if (!ended) update(id, () => settle(chosen, rs, said_));
                return { chosen, n: rs.length };
              }).catch(() => null);
            }, 120);
          }
          // the agent's own total stands in for the same computation, so it need not run twice (not its
          // breakdowns: it may have kept only the top rows it showed)
          if (r.kind === "compute" && !r.by.length) cache.current.set(key(r.metrics, r.by, r.filters), { columns: r.columns, rows: r.rows, sql: r.sql ?? "", ms: r.ms, warehouse: r.warehouse ?? "", truncated: false, notes: [] });
        } else if (e.type === "error") update(id, () => ({ error: e.data.message }));
        else if (e.type === "result") { chat = e.data.type === "chat"; answer = answer || e.data.answer || ""; update(id, (t) => ({ answer: t.answer || e.data.answer || "" })); }
      }, ctrl.signal);
      clearTimeout(soon);
      ended = true;
      if (chat) { update(id, () => ({ chat: true, pending: false })); return; }  // talk has nothing to draw
      const got = early ? await early : null;
      const chosen = got && got.n === results.length ? got.chosen : await choose(results, false);
      update(id, () => ({ ...settle(chosen, results, answer), pending: false }));
    } catch (err: any) {
      if (err?.name !== "AbortError") update(id, () => ({ error: String(err?.message ?? err), pending: false }));
      else if (stopping.current.has(ctrl)) update(id, () => ({ pending: false }));
      // left for another chat: it stays pending, and is picked up when this chat reopens
    } finally {
      clearTimeout(late);
      stopping.current.delete(ctrl);
      following.current.delete(ctrl);
    }
  };
  const fresh = () => { leave(); setSession(newSession()); setTurns([]); setError(""); setTimeout(() => box.current?.focus()); };
  /** A conversation kept in this browser, reopened where it left off: every question with its answer,
      pictures and focus, and its session, so a follow-up still knows what came before. */
  const reopen = (id: string) => {
    const a = readAsked(pbiModel).find((x) => x.id === id);
    if (!a) return fresh();
    leave();
    setSession(a.id); setError("");
    const opened = (a.turns ?? [{ id: "t0", question: a.question, answer: a.answer, specs: a.specs, focus: a.focus }]).map(fromSaved);
    setTurns(opened);
    // what was still at work when this chat was left: its job kept going, and is followed from where it is
    opened.filter((t) => t.pending).forEach((t) => { void follow(t.id, t.question, a.id, opened.filter((o) => o.id !== t.id)); });
  };
  // the sidebar opens a conversation (or a new one); arriving with ?c= opens it, and with ?q= (from a brief or a
  // kept exhibit) asks that question, once the model is known
  useEffect(() => {
    if (!pbiModel) return;
    const at = new URLSearchParams(window.location.search), c = at.get("c"), q = at.get("q");
    if (c || q) window.history.replaceState(null, "", "/pbi/ask");
    if (c) reopen(c);
    else if (q) waiting.current = q;
    const on = (e: Event) => { const id = (e as CustomEvent).detail; if (id) reopen(id); else fresh(); };
    window.addEventListener("ask:open", on);
    return () => window.removeEventListener("ask:open", on);
  }, [pbiModel]); // eslint-disable-line react-hooks/exhaustive-deps
  const waiting = useRef<string | null>(null);
  useEffect(() => {
    if (!metrics.length || !waiting.current) return;
    const q = waiting.current;
    waiting.current = null;
    ask(q);
  }, [metrics]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { window.dispatchEvent(new CustomEvent("ask:showing", { detail: turns.length ? session : null })); }, [turns.length, session]);
  // each conversation kept, titled by its first question; a new question moves it to the top
  useEffect(() => {
    if (!turns.length || !pbiModel) return;
    const list = readAsked(pbiModel), prev = list.find((x) => x.id === session), last = turns[turns.length - 1];
    // a new question is kept at once, so leaving this chat cannot lose it; its words are kept when they settle
    if (busy && prev && (prev.turns?.length ?? 1) === turns.length) return;
    const saved = turns.map((t) => ({ id: t.id, question: t.question, answer: t.answer, specs: t.views, focus: t.focus, error: t.error, note: t.note, measure: t.measure, picture: t.picture, pending: t.pending, chat: t.chat }));
    const entry = { id: session, title: prev?.title ?? turns[0].question, question: last.question, answer: last.answer, specs: last.views, focus: last.focus, turns: saved, at: Date.now() / 1000 };
    const same = prev && (prev.turns?.length ?? 1) === turns.length;
    writeAsked(pbiModel, same ? list.map((x) => (x.id === session ? entry : x)) : [entry, ...list.filter((x) => x.id !== session)]);
  }, [busy, turns, session, pbiModel]);

  const composer = (big: boolean) => (
    <form onSubmit={(e) => { e.preventDefault(); ask(input); }}
      className={`relative flex items-center gap-2 rounded-2xl bg-[var(--d-sheet)] p-1.5 pl-5 shadow-[0_12px_40px_-24px_rgba(20,35,56,.5)] ring-1 ring-[var(--d-rule)] focus-within:ring-2 focus-within:ring-[var(--d-teal)] ${big ? "py-2" : ""}`}>
      <label className="min-w-0 flex-1">
        <span className="sr-only">Ask a question</span>
        {/* the box's ring shows the focus; the desk's outline on the field itself would draw a box inside it */}
        <input ref={box} value={input} onChange={(e) => setInput(e.target.value)} autoFocus={big} style={{ outline: "none" }}
          placeholder={turns.length ? "Ask a follow-up, or something new" : "Ask a question about your numbers"}
          className={`w-full bg-transparent text-[var(--d-ink)] outline-none placeholder:text-[var(--d-muted)] ${big ? "text-[18px]" : "text-[15px]"}`} />
      </label>
      {busy && !input.trim() ? (
        <button type="button" onClick={stop} aria-label="Stop" className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--d-navy)] text-white"><Square size={14} /></button>
      ) : (
        <button type="submit" disabled={!input.trim()} aria-label="Ask" className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--d-teal)] text-white disabled:opacity-30"><ArrowUp size={18} /></button>
      )}
    </form>
  );

  return (
    <div className={`desk ${look.look} relative flex h-screen flex-col bg-[var(--d-ground)] text-[var(--d-ink)]`}>
      {/* light, dark and the look, as on the other pages; it sits over the page's corner, above what scrolls */}
      <div className="absolute right-5 top-3 z-20"><SettingsMenu look={look} /></div>
      <main className="min-h-0 flex-1 overflow-y-auto">
        {!turns.length ? (
          // centred like a search page, lifted a little above the middle where the eye lands
          <div className="mx-auto flex min-h-full w-full max-w-[720px] flex-col items-center justify-center px-6 pb-[14vh] pt-16 text-center">
            <h1 className="cond text-[34px] font-semibold leading-tight tracking-[-.01em]">Ask a question. See the answer.</h1>
            <p className="mt-3 max-w-[54ch] text-[15px] leading-relaxed text-[var(--d-muted)]">
              The answer first, then the picture it calls for. Every number comes from your Power BI measures,
              translated to SQL{model ? ` (${model.captured} of ${model.measures} so far)` : ""}.
            </p>
            <div className="mt-8 w-full text-left">{composer(true)}</div>
            {error && <p className="mt-3 text-[13px] text-[var(--d-rise-ink)]">{error}</p>}
            <div className="mt-5 flex flex-wrap justify-center gap-2">
              {starters.map((s) => (
                <button key={s} onClick={() => ask(s)} className="rounded-full bg-[var(--d-sheet)] px-4 py-2 text-[13.5px] text-[var(--d-ink-2)] ring-1 ring-[var(--d-rule)] hover:ring-[var(--d-teal)]">{s}</button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-[1180px] px-8 pb-32 pt-8">
            {turns.map((t, i) => (
              // the last question keeps a window's height below it, so it can sit at the top while its answer comes
              <div key={t.id} style={i === turns.length - 1 && i > 0 ? { minHeight: "calc(100vh - 160px)" } : undefined}>
                <Answer turn={t} model={pbiModel} labels={labels} cache={cache.current} first={i === 0}
                  live={!!t.pending} step={t.step ?? "Working"}
                  setFocus={(f) => update(t.id, (x) => ({ focus: typeof f === "function" ? f(x.focus) : f }))}
                  setViews={(change) => update(t.id, (x) => ({ views: change(x.views) }))} setPicture={(p) => update(t.id, () => ({ picture: p }))}
                  onAsk={ask} dates={cols.dates} types={cols.types} />
              </div>
            ))}
            {error && <p className="mt-3 rounded-lg bg-[var(--d-rise-soft)] px-3 py-2 text-[13px] text-[var(--d-rise-ink)]">{error}</p>}
          </div>
        )}
      </main>
      <ThreadPanel model={pbiModel} labels={labels} />
      {/* the follow-up box floats at the foot of the window with nothing behind it: the answers scroll on under it */}
      {turns.length > 0 && <div className="pointer-events-none absolute inset-x-0 bottom-0 px-8 pb-6"><div className="pointer-events-auto mx-auto max-w-2xl">
        {error && !busy && <p role="alert" className="mb-2 rounded-xl bg-[var(--d-rise-soft)] px-3 py-2 text-[13px] text-[var(--d-rise-ink)]">{error}</p>}
        {composer(false)}
      </div></div>}
    </div>
  );
}
