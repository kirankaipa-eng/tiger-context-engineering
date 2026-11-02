"use client";

import clsx from "clsx";
import { Check, Loader2, Play, X } from "lucide-react";
import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import { Button, ErrorNote, Pill, fmtValue } from "@/components/ui";
import { streamNdjson } from "@/lib/api";
import { useStudio } from "@/lib/studio";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

type Run = { side: "governed" | "raw"; i: number; answer: string; value: number | null; tools: { tool: string; args: any }[]; evidence: string; secs: number }; // eslint-disable-line @typescript-eslint/no-explicit-any
type Side = { runs: number; answered: number; distinct: number; values: number[]; min: number | null; max: number | null; spread_pct: number | null };

const PRESETS: Record<string, string[]> = {
  commercial_pc: [
    "What is our loss and ALAE ratio for the Harbor Program Group unit?",
    "What is total paid loss for Harbor Program Group?",
    "What is our ultimate loss ratio for the whole group?",
  ],
  health_payer: [
    "What is the medical PMPM for our Medicare Advantage business?",
    "What is the medical loss ratio for the commercial business?",
    "What share of prior authorization decisions met the CMS time frames?",
  ],
  life_annuity: [
    "What is our 13-month persistency on life policies?",
    "What is the total face amount in force?",
    "What were annuity net flows in 2025?",
  ],
};

// A client's own questions win over its line's.
const CLIENT_PRESETS: Record<string, string[]> = {
  plains_health: [
    "What was paid per inpatient admit for Commercial members in 2025?",
    "What was our total cost of care PMPM, medical plus pharmacy, in 2025?",
    "What were inpatient admits per 1,000 for our Medicare Advantage members in 2025?",
  ],
};

export default function ConsistencyPage() {
  const { client, clientInfo, setScreen } = useStudio();
  const presets = CLIENT_PRESETS[client] ?? PRESETS[clientInfo?.line ?? "commercial_pc"] ?? PRESETS.commercial_pc;
  const [question, setQuestion] = useState("");
  const [n, setN] = useState(5);
  const [runs, setRuns] = useState<Run[]>([]);
  const [summary, setSummary] = useState<Record<string, Side> | null>(null);
  const [version, setVersion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setScreen({ screen: "consistency", title: "Consistency lab" }); }, [setScreen]);

  const q = question || presets[0];
  const fmt = useMemo(() => (/ratio|share|persistency|rate|met the/i.test(q) ? "percent" : /pmpm|paid|amount|flows|premium|loss$/i.test(q) ? "currency" : "number"), [q]);

  const run = async () => {
    setBusy(true); setRuns([]); setSummary(null); setError(null);
    try {
      await streamNdjson("/api/consistency", { client, question: q, n }, (e) => {
        if (e.type === "started") setVersion(e.data.content_version);
        if (e.type === "run") setRuns((r) => [...r, e.data]);
        if (e.type === "summary") setSummary(e.data);
      });
    } catch (e: any) { setError(e.message); } finally { setBusy(false); } // eslint-disable-line @typescript-eslint/no-explicit-any
  };

  const chart = useMemo(() => {
    const pts = (side: string, y: number) => runs.filter((r) => r.side === side && r.value != null).map((r) => [r.value, y + (r.i - n / 2) * 0.04]);
    return {
      animation: false,
      grid: { left: 140, right: 30, top: 16, bottom: 36 },
      tooltip: { formatter: (p: any) => fmtValue(p.value[0], fmt) }, // eslint-disable-line @typescript-eslint/no-explicit-any
      xAxis: { type: "value", scale: true, axisLabel: { formatter: (v: number) => fmtValue(v, fmt), fontSize: 11 }, splitLine: { lineStyle: { color: "#ece9e2" } } },
      yAxis: { type: "value", min: -1, max: 2, interval: 1, axisLabel: { formatter: (v: number) => (v === 1 ? "Governed agent" : v === 0 ? "Raw-data agent" : ""), fontSize: 12 }, splitLine: { show: false } },
      series: [
        { type: "scatter", symbolSize: 16, itemStyle: { color: "#1f4fbf", opacity: 0.8 }, data: pts("governed", 1) },
        { type: "scatter", symbolSize: 16, itemStyle: { color: "#d97a2b", opacity: 0.8 }, data: pts("raw", 0) },
      ],
    };
  }, [runs, n, fmt]);

  const col = (side: "governed" | "raw") => {
    const rs = runs.filter((r) => r.side === side).sort((a, b) => a.i - b.i);
    const s = summary?.[side];
    const good = side === "governed";
    return (
      <section className="flex min-w-0 flex-1 flex-col rounded-lg border border-line bg-panel">
        <div className={clsx("border-b border-line-soft px-5 py-3.5", good ? "bg-accent-soft/50" : "bg-[#fcf1e6]")}>
          <div className="flex items-center gap-2"><b>{good ? "Governed agent" : "Raw-data agent"}</b>
            <span className="text-xs text-muted">{good ? "ontology resolves the words · semantic layer computes the number" : "same model, same question · SQL over source tables"}</span></div>
          {s && (
            <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <span className="text-2xl font-semibold tabular-nums">{s.distinct === 1 ? fmtValue(s.min, fmt) : `${fmtValue(s.min, fmt)} – ${fmtValue(s.max, fmt)}`}</span>
              <Pill tone={s.distinct === 1 ? "ok" : "bad"}>{s.distinct} distinct answer{s.distinct === 1 ? "" : "s"} in {s.runs} runs</Pill>
              {s.answered < s.runs && <Pill tone="warn">{s.runs - s.answered} gave no number</Pill>}
            </div>
          )}
        </div>
        <div className="space-y-2 p-4">
          {rs.length === 0 && busy && <div className="pulse-soft text-sm text-muted">Running {n} times…</div>}
          {rs.map((r) => (
            <details key={r.i} className="group rounded-md border border-line-soft">
              <summary className="flex cursor-pointer list-none items-center gap-3 px-3 py-2 text-[13px]">
                <span className="w-12 text-muted">Run {r.i + 1}</span>
                <b className="w-24 tabular-nums">{fmtValue(r.value, fmt)}</b>
                <span className="flex min-w-0 flex-1 flex-wrap gap-1">{r.tools.map((t, j) => <span key={j} className="rounded bg-sunk px-1.5 py-0.5 font-mono text-[10px] text-ink-2">{t.tool}</span>)}</span>
                <span className="text-xs text-muted">{r.secs}s</span>
              </summary>
              <div className="space-y-2 border-t border-line-soft px-3 py-2 text-[12px]">
                <div className="whitespace-pre-wrap text-ink-2">{r.answer.slice(0, 900)}</div>
                {r.tools.filter((t) => t.tool === "run_sql").map((t, j) => <pre key={j} className="overflow-auto rounded bg-sunk p-2 font-mono text-[10.5px]">{String(t.args?.sql ?? "")}</pre>)}
              </div>
            </details>
          ))}
        </div>
      </section>
    );
  };

  const gov = runs.find((r) => r.side === "governed" && r.evidence);
  return (
    <div className="mx-auto max-w-7xl space-y-4 px-8 py-6">
      <div>
        <h1 className="text-2xl font-semibold">Consistency lab</h1>
        <p className="mt-1 max-w-3xl text-sm text-ink-2">Ask one business question several times. The governed agent answers through the ontology and the semantic layer; the raw-data agent is the same model with SQL over the source tables. Same question, same number, every time is the point.</p>
      </div>
      <div className="space-y-2 rounded-lg border border-line bg-panel p-4">
        <div className="flex flex-wrap gap-2">
          {presets.map((p) => <button key={p} onClick={() => setQuestion(p)} className={clsx("rounded-full border px-3 py-1 text-[13px]", q === p ? "border-accent-line bg-accent-soft text-accent-ink" : "border-line hover:border-accent-line")}>{p}</button>)}
        </div>
        <div className="flex items-center gap-2">
          <label className="flex-1"><span className="sr-only">Question</span>
            <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder={presets[0]} className="w-full rounded-md border border-line px-3 py-2.5 text-sm outline-none focus:border-accent-line" /></label>
          <label className="flex items-center gap-1.5 text-sm text-muted">Runs
            <select value={n} onChange={(e) => setN(Number(e.target.value))} className="rounded-md border border-line px-2 py-2 text-ink">{[3, 5, 8].map((x) => <option key={x}>{x}</option>)}</select></label>
          <Button primary onClick={run} disabled={busy} className="flex items-center gap-2">{busy ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}{busy ? "Running" : "Run both agents"}</Button>
        </div>
      </div>
      <ErrorNote error={error} />
      {(runs.length > 0 || busy) && (
        <>
          <div className="rounded-lg border border-line bg-panel px-2 pt-2"><ReactECharts option={chart} style={{ height: 170 }} notMerge /></div>
          <div className="flex flex-col gap-4 lg:flex-row">{col("governed")}{col("raw")}</div>
          {gov && (
            <section className="rounded-lg border border-line bg-panel p-5 text-[13px]">
              <div className="mb-2 flex items-center gap-2"><Check size={16} className="text-ok" /><b className="text-sm">Why the governed answer repeats</b></div>
              <ul className="mb-3 list-disc space-y-1 pl-5 text-ink-2">
                <li>Business words resolve through the ontology to one concept and one set of joins, for every unit.</li>
                <li>The number comes from one governed metric definition, at the governed valuation date, never re-derived in SQL.</li>
                <li>The definitions are pinned to content version <span className="font-mono">{version}</span>: change a definition and the answer changes for everyone, through an approved proposal.</li>
              </ul>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded bg-sunk p-3 font-mono text-[11px]">{gov.evidence}</pre>
            </section>
          )}
          {summary && summary.raw?.distinct > 1 && (
            <div className="flex items-start gap-2 rounded-lg border border-warn-line bg-warn-soft px-4 py-3 text-[13px] text-warn"><X size={16} className="mt-0.5" />The raw-data agent gave {summary.raw.distinct} different answers. Open its runs to see the SQL: each run chose its own tables, joins, filters and date logic.</div>
          )}
        </>
      )}
    </div>
  );
}
