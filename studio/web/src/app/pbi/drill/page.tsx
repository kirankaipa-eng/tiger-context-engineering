"use client";

/* Multi-dimensional drilldown over a captured metric: break it down by a column, click a mark to keep
   that value and break it down by the next column, as deep as the model's relationships go. A split
   adds a second dimension. Every level is a filter on the same metric, computed in SQL; the chart is
   picked from the answer's shape (AutoChart), and the trail above it goes back up. */

import { ChevronRight, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { AutoChart, blank, type ChartKind } from "@/components/auto-chart";
import { ColumnSelect, ResultTable, SqlBlock } from "@/components/pbi";
import { Box, ErrorNote, Loading, Pill } from "@/components/ui";
import { colLabel, columnTypes, isDimension, pbi, type PbiMetric, type PbiMetricDetail, type Result } from "@/lib/pbi";
import { useStudio } from "@/lib/studio";

type Step = { by: string; pick?: unknown; picked?: boolean };

function Drill() {
  const { pbiModel, version } = useStudio();
  const params = useSearchParams();
  const router = useRouter();
  const [metrics, setMetrics] = useState<PbiMetric[] | null>(null);
  const [metric, setMetric] = useState(params.get("metric") ?? "");
  const [also, setAlso] = useState("");
  const [detail, setDetail] = useState<PbiMetricDetail | null>(null);
  const [dates, setDates] = useState<string[] | null>(null);
  const [types, setTypes] = useState<Record<string, string>>({});
  const [steps, setSteps] = useState<Step[]>([]);
  const [split, setSplit] = useState("");
  const [kind, setKind] = useState<ChartKind | undefined>(undefined);
  const [table, setTable] = useState(false);
  const [answer, setAnswer] = useState<{ key: string; result?: Result; error?: string } | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [menuAt, setMenu] = useState<{ key: string; x: number; y: number; values: unknown[] } | null>(null);

  useEffect(() => {
    if (!pbiModel) return;
    pbi.metrics(pbiModel).then((r) => {
      const ok = r.metrics.filter((m) => m.status === "captured").sort((a, b) => a.label.localeCompare(b.label));
      setMetrics(ok);
      setMetric((cur) => (cur && ok.some((m) => m.key === cur) ? cur : ok[0]?.key ?? ""));
    }).catch((e) => setFailed(e.message));
    pbi.tables(pbiModel).then((r) => { const ct = columnTypes(r.tables); setTypes(ct.types); setDates(ct.dates); }).catch(() => setDates([]));
  }, [pbiModel, version]);

  useEffect(() => {
    if (!pbiModel || !metric || dates === null) return;
    pbi.metric(pbiModel, metric).then((d) => {
      setDetail(d); setSplit(""); setAlso(""); setKind(undefined); setFailed(null);
      const cols = (d.columns ?? []).filter((c) => !dates.includes(c) && isDimension(c, types[c]));
      // a column of a table the metric's rows reach (a dimension), before its own table's columns
      const own = new Set(d.tables ?? []);
      const date = (d.columns ?? []).find((c) => dates.includes(c));
      const first = d.needs?.by?.[0] ?? cols.find((c) => !own.has(c.replace(/\[.*$/, "").replace(/^'|'$/g, ""))) ?? cols[0] ?? (date ? `${date}:month` : "");
      setSteps(first ? [{ by: first }] : []);
    }).catch((e) => setFailed(e.message));
  }, [pbiModel, metric, dates]); // eslint-disable-line react-hooks/exhaustive-deps

  const current = steps.at(-1);
  const filters = useMemo(() => Object.fromEntries(steps.filter((s) => s.picked).map((s) => [s.by, s.pick ?? null])), [steps]);
  const asked = useMemo(() => [metric, ...(also && also !== metric ? [also] : [])], [metric, also]);
  const curBy = current?.by;
  const dims = useMemo(() => (curBy ? [curBy, ...(split && split !== curBy ? [split] : [])] : []), [curBy, split]);
  // each question is keyed; what is on screen belongs to the latest one, so nothing needs resetting
  const key = detail?.key === metric && dims.length ? JSON.stringify([pbiModel, asked, dims, filters]) : "";

  useEffect(() => {
    if (!key) return;
    let live = true;
    pbi.compute(pbiModel, { metrics: asked, by: dims, filters })
      .then((r) => { if (live) setAnswer({ key, result: r }); })
      .catch((e) => { if (live) setAnswer({ key, error: e.message }); });
    return () => { live = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  const busy = !!key && answer?.key !== key;
  const result = answer?.result && (answer.key === key || busy) ? answer.result : null;
  const error = failed ?? (answer?.key === key ? answer.error ?? null : null);
  const menu = menuAt && menuAt.key === key ? menuAt : null;

  const columns = detail?.columns ?? [];
  const labels = Object.fromEntries((metrics ?? []).map((m) => [m.key, m.label]));
  const used = steps.map((s) => s.by.replace(/:[a-z]+$/, ""));
  const next = columns.filter((c) => isDimension(c, types[c]) && !used.includes(c) && c !== split && !(dates ?? []).includes(c)).slice(0, 10);
  const trends = (dates ?? []).filter((d) => columns.includes(d) && !used.includes(d)).slice(0, 2);

  /** Keep the clicked values (one per dimension shown) and break down by `by`. */
  const drill = (values: unknown[], by: string) => {
    setMenu(null); setKind(undefined);
    setSteps((ss) => {
      const out = ss.slice(0, -1).concat({ ...ss.at(-1)!, pick: values[0], picked: true });
      if (dims[1] && values[1] !== undefined) out.push({ by: dims[1], pick: values[1], picked: true });
      return [...out, { by }];
    });
    if (dims[1] && (values[1] !== undefined || by === dims[1])) setSplit("");
  };
  const rowsBehind = (values: unknown[]) => {
    const f: Record<string, unknown> = { ...filters };
    dims.forEach((d, i) => { if (values[i] !== undefined) f[d] = values[i]; });
    const t = detail?.tables?.[0];
    router.push(`/pbi/explore?filters=${encodeURIComponent(JSON.stringify(f))}${t ? `&table=${encodeURIComponent(t)}` : ""}`);
  };

  const pivot = useMemo(() => {
    if (!result) return null;
    const iM = result.columns.indexOf(asked[0]);
    if (dims.length < 2) {
      const total = result.rows.reduce((t, r) => t + Number(r[iM] ?? 0), 0);
      return { columns: [...result.columns, "share of total"], rows: result.rows.map((r) => [...r, total ? `${((100 * Number(r[iM] ?? 0)) / total).toFixed(1)}%` : "—"]) };
    }
    const [i0, i1] = dims.map((d) => result.columns.indexOf(d));
    const ss = [...new Set(result.rows.map((r) => blank(r[i1])))];
    const cs = [...new Set(result.rows.map((r) => blank(r[i0])))];
    const cell = new Map(result.rows.map((r) => [`${blank(r[i0])}\u0000${blank(r[i1])}`, r[iM]]));
    return { columns: [dims[0], ...ss], rows: cs.map((c) => [c, ...ss.map((s) => cell.get(`${c}\u0000${s}`) ?? null)]) };
  }, [result, dims, asked]);

  if (error && !detail) return <div className="p-6"><ErrorNote error={error} /></div>;
  if (!metrics) return <div className="p-6"><Loading /></div>;
  if (!metrics.length) return <div className="p-6 text-sm text-muted">No captured metrics in this model.</div>;

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-8 py-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="mr-auto"><h1 className="text-2xl font-semibold">Drilldown</h1><p className="text-sm text-muted">Click a bar, a point or a cell to keep that value and break it down by another column.</p></div>
        <label className="text-sm"><span className="lbl block">Metric</span>
          <select value={metric} onChange={(e) => setMetric(e.target.value)} className="min-h-9 max-w-xs rounded-md border border-line bg-panel px-2 text-sm">
            {metrics.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
          </select>
        </label>
        <label className="text-sm"><span className="lbl block">Compare with</span>
          <select value={also} onChange={(e) => setAlso(e.target.value)} className="min-h-9 max-w-xs rounded-md border border-line bg-panel px-2 text-sm">
            <option value="">—</option>
            {metrics.filter((m) => m.key !== metric).map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
          </select>
        </label>
      </div>
      {detail?.definition && <div className="text-[13px] text-ink-2"><span className="font-semibold">{detail.label}</span> = <span className="font-mono text-xs">{detail.definition}</span> <Link href={`/pbi/metrics/${metric}`} className="ml-1 text-xs text-accent">definition</Link></div>}

      <div className="flex flex-wrap items-center gap-1.5 text-[13px]">
        {steps.map((s, i) => (
          <span key={i} className="flex items-center gap-1.5">
            {i > 0 && <ChevronRight size={14} className="text-muted" />}
            {s.picked ? (
              <button onClick={() => { setSteps(steps.slice(0, i).concat({ by: s.by })); setKind(undefined); }} title="Go back to this level"
                className="rounded-full border border-accent-line bg-accent-soft px-2.5 py-0.5 text-accent-ink hover:bg-white">
                {colLabel(s.by)} = <b>{blank(s.pick)}</b>
              </button>
            ) : (
              <span className="flex items-center gap-2">
                <span className="text-muted">by</span>
                <ColumnSelect columns={columns} dates={dates ?? []} types={types} value={s.by} exclude={steps.slice(0, -1).map((x) => x.by)}
                  onChange={(v) => { if (v) { setSteps(steps.slice(0, -1).concat({ by: v })); setKind(undefined); } }} />
              </span>
            )}
          </span>
        ))}
        {steps.some((s) => s.picked) && <button onClick={() => setSteps([{ by: steps[0].by }])} className="ml-2 flex items-center gap-1 text-xs text-muted hover:text-ink"><X size={12} />clear</button>}
        <span className="ml-4 flex items-center gap-2"><span className="text-muted">split by</span>
          <ColumnSelect columns={columns} dates={dates ?? []} types={types} value={split} placeholder="— no split —" exclude={steps.map((s) => s.by)} onChange={(v) => { setSplit(v); setKind(undefined); }} />
        </span>
      </div>
      {detail?.needs?.by?.length ? <div className="text-xs text-muted">{detail.label} is answered per {detail.needs.by.map(colLabel).join(", ")}; keep it in the breakdown or the split.</div> : null}
      <ErrorNote error={error} />

      <Box>
        <div className="relative">
          <div className="mb-1 flex items-center gap-2 text-xs text-muted">
            {busy && <span className="pulse-soft">computing…</span>}
            {result && <span>{result.rows.length} rows{result.truncated ? " (first 500)" : ""}</span>}
            {Object.keys(filters).length > 0 && <Pill tone="accent">{Object.keys(filters).length} filter{Object.keys(filters).length > 1 ? "s" : ""}</Pill>}
            <button onClick={() => setTable(!table)} className={`ml-auto rounded px-2 py-0.5 ${table ? "bg-ink text-white" : "text-ink-2 hover:bg-sunk"}`}>Table</button>
          </div>
          {!result && busy && <Loading what="Computing" />}
          {result && (table && pivot
            ? <ResultTable columns={pivot.columns} rows={pivot.rows} labels={labels} />
            : <AutoChart columns={result.columns} rows={result.rows} dims={dims} metrics={asked} labels={labels} kind={kind} onKind={setKind}
                onPick={(p) => setMenu({ key, x: p.x, y: p.y + 28, values: p.values })} />)}
          {menu && (
            <div className="absolute z-10 w-64 rounded-lg border border-line bg-panel p-2 text-[13px] shadow-lg" style={{ left: Math.max(0, Math.min(menu.x, 700)), top: menu.y }}>
              <div className="flex items-start gap-2 px-2 pb-1.5">
                <div className="min-w-0 flex-1">
                  {dims.map((d, i) => menu.values[i] !== undefined && <div key={d} className={i ? "truncate text-xs text-muted" : "truncate font-semibold"}>{i ? `and ${colLabel(d)} = ` : ""}{blank(menu.values[i])}</div>)}
                </div>
                <button onClick={() => setMenu(null)} aria-label="Close" className="text-muted hover:text-ink"><X size={14} /></button>
              </div>
              <div className="lbl px-2 pt-1">Break it down by</div>
              {next.map((c) => <button key={c} onClick={() => drill(menu.values, c)} className="block w-full truncate rounded px-2 py-1 text-left hover:bg-sunk">{colLabel(c)}</button>)}
              {trends.map((d) => <button key={d} onClick={() => drill(menu.values, `${d}:month`)} className="block w-full truncate rounded px-2 py-1 text-left hover:bg-sunk">{colLabel(`${d}:month`)} (trend)</button>)}
              <div className="mt-1 border-t border-line-soft pt-1">
                <button onClick={() => rowsBehind(menu.values)} className="block w-full rounded px-2 py-1 text-left hover:bg-sunk">Show the rows behind it</button>
              </div>
            </div>
          )}
        </div>
        {result && <div className="mt-3"><SqlBlock sql={result.sql} warehouse={result.warehouse} ms={result.ms} /></div>}
        {result && (result.notes.length > 0 || (result.what_if?.length ?? 0) > 0) && <div className="mt-2 text-xs text-muted">{[...result.notes, ...(result.what_if ?? [])].join("; ")}</div>}
      </Box>
      {columns.length === 0 && detail && <div className="text-sm text-muted">{detail.label} has no columns to break it down by.</div>}
    </div>
  );
}

export default function DrillPage() {
  return <Suspense fallback={<div className="p-6"><Loading /></div>}><Drill /></Suspense>;
}
