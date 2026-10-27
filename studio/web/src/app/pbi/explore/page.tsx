"use client";

/* Ad-hoc rows of the model's tables: pick columns, add conditions (=, >, between, like, in, is null),
   optionally sum or count by the columns, order and limit. The rows are read from the table that reaches
   every table named (or the one chosen), joined as Power BI filters; values are literals, never SQL. */

import { Plus, X } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { AutoChart } from "@/components/auto-chart";
import { ColumnSelect, ResultTable, SqlBlock } from "@/components/pbi";
import { Box, Button, ErrorNote, Loading, Pill } from "@/components/ui";
import { colLabel, pbi, type ExploreBody, type PbiTable, type Result } from "@/lib/pbi";
import { useStudio } from "@/lib/studio";

const OPS = ["=", "!=", ">", ">=", "<", "<=", "between", "in", "like", "is null", "is not null"] as const;
const AGGS = ["sum", "avg", "min", "max", "count", "count_distinct"] as const;
type Cond = { col: string; op: (typeof OPS)[number]; a: string; b: string };
type Agg = { fn: (typeof AGGS)[number]; col: string };

const refOf = (t: string, c: string) => `${/^[A-Za-z_][A-Za-z0-9_]*$/.test(t) ? t : `'${t.replace(/'/g, "''")}'`}[${c}]`;
const lit = (v: string) => (/^-?\d+(\.\d+)?$/.test(v.trim()) ? v.trim() : `'${v.replace(/'/g, "''")}'`);

function condText(c: Cond) {
  if (c.op === "is null" || c.op === "is not null") return `${c.col} ${c.op}`;
  if (c.op === "between") return `${c.col} between ${lit(c.a)} and ${lit(c.b)}`;
  if (c.op === "in") return `${c.col} in (${c.a.split(",").map((x) => lit(x.trim())).join(", ")})`;
  if (c.op === "like") return `${c.col} like ${lit(c.a.includes("%") ? c.a : `%${c.a}%`)}`;
  return `${c.col} ${c.op} ${lit(c.a)}`;
}
const aggText = (a: Agg) => `${a.fn}(${a.col || "*"})`;

function Explore() {
  const { pbiModel, version } = useStudio();
  const params = useSearchParams();
  const [tables, setTables] = useState<PbiTable[] | null>(null);
  const [table, setTable] = useState(params.get("table") ?? "");
  const [columns, setColumns] = useState<string[]>([]);
  const [filters, setFilters] = useState<Record<string, unknown>>(() => { try { return JSON.parse(params.get("filters") ?? "{}"); } catch { return {}; } });
  const [conds, setConds] = useState<Cond[]>([]);
  const [aggs, setAggs] = useState<Agg[]>([]);
  const [order, setOrder] = useState("");
  const [desc, setDesc] = useState(true);
  const [limit, setLimit] = useState(100);
  const [distinct, setDistinct] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"table" | "chart">("table");

  useEffect(() => {
    if (!pbiModel) return;
    pbi.tables(pbiModel).then((r) => {
      setTables(r.tables);
      // arriving from a drilldown: the filter columns, then the fact table's own columns, run at once
      if (!Object.keys(filters).length) return;
      const t = r.tables.find((x) => x.name === table);
      const own = t ? t.columns.map((c) => refOf(t.name, c.name)).filter((c) => !/image|sort|colou?r|url/i.test(c)).slice(0, 8) : [];
      const start = [...Object.keys(filters), ...own].filter((c, i, a) => a.indexOf(c) === i);
      setColumns(start);
      runWith({ columns: start, filters, limit: 100, table: table || undefined });
    }).catch((e) => setError(e.message));
  }, [pbiModel, version]); // eslint-disable-line react-hooks/exhaustive-deps
  const all = useMemo(() => (tables ?? []).flatMap((t) => t.columns.map((c) => refOf(t.name, c.name))), [tables]);
  const dates = useMemo(() => (tables ?? []).flatMap((t) => t.columns.filter((c) => c.type === "dateTime" || c.type === "date").map((c) => refOf(t.name, c.name))), [tables]);

  const body = () => ({
    columns, aggregates: aggs.map(aggText), filters, conditions: conds.filter((c) => c.col && (c.a || c.op.startsWith("is"))).map(condText),
    order_by: order ? [order] : undefined, descending: desc, limit, distinct, table: table || undefined,
  });
  async function runWith(b: ExploreBody) {
    if (!pbiModel) return;
    setBusy(true); setError(null);
    try { setResult(await pbi.explore(pbiModel, b)); } catch (e: any) { setResult(null); setError(e.message); } finally { setBusy(false); } // eslint-disable-line @typescript-eslint/no-explicit-any
  }
  const run = () => runWith(body());

  if (!tables) return <div className="p-6">{error ? <ErrorNote error={error} /> : <Loading />}</div>;
  const orderable = [...columns, ...aggs.map(aggText)];
  const chartable = result && aggs.length > 0 && columns.length <= 2;

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-8 py-6">
      <div><h1 className="text-2xl font-semibold">Explore rows</h1><p className="text-sm text-muted">Rows of the model&apos;s tables, for questions no metric answers. Add a sum or a count to group them.</p></div>
      <Box>
        <div className="space-y-3 text-[13px]">
          <div className="flex flex-wrap items-center gap-2">
            <span className="lbl w-24">Columns</span>
            {columns.map((c) => <Pill key={c} tone="accent">{colLabel(c)}<button onClick={() => setColumns(columns.filter((x) => x !== c))} aria-label={`Remove ${c}`}><X size={11} /></button></Pill>)}
            <ColumnSelect columns={all} dates={dates} value="" placeholder="+ add a column" exclude={columns} onChange={(v) => v && setColumns([...columns, v])} />
          </div>
          {Object.keys(filters).length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="lbl w-24">Filters</span>
              {Object.entries(filters).map(([k, v]) => <Pill key={k}>{colLabel(k)} = {String(v ?? "(blank)")}<button onClick={() => { const f = { ...filters }; delete f[k]; setFilters(f); }} aria-label={`Remove ${k}`}><X size={11} /></button></Pill>)}
            </div>
          )}
          {conds.map((c, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <span className="lbl w-24">{i ? "and" : "Where"}</span>
              <ColumnSelect columns={all} dates={[]} allowAll value={c.col} onChange={(v) => setConds(conds.map((x, j) => (j === i ? { ...x, col: v } : x)))} />
              <select value={c.op} onChange={(e) => setConds(conds.map((x, j) => (j === i ? { ...x, op: e.target.value as Cond["op"] } : x)))} className="min-h-9 rounded-md border border-line bg-panel px-2">
                {OPS.map((o) => <option key={o}>{o}</option>)}
              </select>
              {!c.op.startsWith("is") && <input value={c.a} onChange={(e) => setConds(conds.map((x, j) => (j === i ? { ...x, a: e.target.value } : x)))} placeholder={c.op === "in" ? "a, b, c" : "value"} className="min-h-9 w-40 rounded-md border border-line px-2" />}
              {c.op === "between" && <><span>and</span><input value={c.b} onChange={(e) => setConds(conds.map((x, j) => (j === i ? { ...x, b: e.target.value } : x)))} className="min-h-9 w-32 rounded-md border border-line px-2" /></>}
              <button onClick={() => setConds(conds.filter((_, j) => j !== i))} aria-label="Remove condition" className="text-muted hover:text-ink"><X size={14} /></button>
            </div>
          ))}
          {aggs.map((a, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <span className="lbl w-24">{i ? "and" : "Summarize"}</span>
              <select value={a.fn} onChange={(e) => setAggs(aggs.map((x, j) => (j === i ? { ...x, fn: e.target.value as Agg["fn"] } : x)))} className="min-h-9 rounded-md border border-line bg-panel px-2">
                {AGGS.map((f) => <option key={f}>{f}</option>)}
              </select>
              <span>of</span>
              <ColumnSelect columns={all} dates={[]} allowAll value={a.col} placeholder={a.fn === "count" ? "rows (*)" : "Pick a column"}
                onChange={(v) => setAggs(aggs.map((x, j) => (j === i ? { ...x, col: v } : x)))} />
              <button onClick={() => setAggs(aggs.filter((_, j) => j !== i))} aria-label="Remove summary" className="text-muted hover:text-ink"><X size={14} /></button>
            </div>
          ))}
          <div className="flex flex-wrap items-center gap-3 border-t border-line-soft pt-3">
            <button onClick={() => setConds([...conds, { col: "", op: "=", a: "", b: "" }])} className="flex items-center gap-1 text-accent"><Plus size={13} />condition</button>
            <button onClick={() => setAggs([...aggs, { fn: "count", col: "" }])} className="flex items-center gap-1 text-accent"><Plus size={13} />sum or count</button>
            <label className="ml-4 flex items-center gap-2">order by
              <select value={order} onChange={(e) => setOrder(e.target.value)} className="min-h-9 rounded-md border border-line bg-panel px-2">
                <option value="">—</option>{orderable.map((o) => <option key={o} value={o}>{o.includes("[") && !o.includes("(") ? colLabel(o) : o}</option>)}
              </select>
              <select value={desc ? "desc" : "asc"} onChange={(e) => setDesc(e.target.value === "desc")} className="min-h-9 rounded-md border border-line bg-panel px-2"><option value="desc">high first</option><option value="asc">low first</option></select>
            </label>
            <label className="flex items-center gap-2">limit<input type="number" min={1} max={500} value={limit} onChange={(e) => setLimit(Math.max(1, Math.min(500, Number(e.target.value) || 100)))} className="min-h-9 w-20 rounded-md border border-line px-2" /></label>
            <label className="flex items-center gap-1"><input type="checkbox" checked={distinct} onChange={(e) => setDistinct(e.target.checked)} />distinct</label>
            <label className="flex items-center gap-2">rows of
              <select value={table} onChange={(e) => setTable(e.target.value)} className="min-h-9 rounded-md border border-line bg-panel px-2">
                <option value="">(the table that reaches them all)</option>{tables.filter((t) => t.columns.length).map((t) => <option key={t.name}>{t.name}</option>)}
              </select>
            </label>
            <Button primary small onClick={run} disabled={busy || (!columns.length && !aggs.length)} className="ml-auto">{busy ? "Running…" : "Run"}</Button>
          </div>
        </div>
      </Box>
      <ErrorNote error={error} />
      {result && (
        <Box>
          <div className="mb-2 flex items-center gap-2 text-xs text-muted">
            <span>{result.rows.length} row{result.rows.length === 1 ? "" : "s"}{result.truncated ? ` (first ${limit}; raise the limit or narrow it)` : ""}</span>
            {result.notes.length > 0 && <span>· {result.notes.join("; ")}</span>}
            {chartable && <div className="ml-auto flex gap-1">{(["table", "chart"] as const).map((v) => <button key={v} onClick={() => setView(v)} className={`rounded px-2 py-0.5 ${view === v ? "bg-ink text-white" : "text-ink-2 hover:bg-sunk"}`}>{v[0].toUpperCase() + v.slice(1)}</button>)}</div>}
          </div>
          {chartable && view === "chart"
            ? <AutoChart columns={result.columns} rows={result.rows} dims={columns} metrics={result.aggregates ?? []} />
            : <ResultTable columns={result.columns} rows={result.rows} numeric={result.aggregates} />}
          <div className="mt-3"><SqlBlock sql={result.sql} warehouse={result.warehouse} ms={result.ms} /></div>
        </Box>
      )}
    </div>
  );
}

export default function ExplorePage() {
  return <Suspense fallback={<div className="p-6"><Loading /></div>}><Explore /></Suspense>;
}
