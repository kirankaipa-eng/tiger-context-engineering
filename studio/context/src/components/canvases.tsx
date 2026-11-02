"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useState } from "react";
import type { Proposal, ScanReport } from "@/lib/api";
import { Pill, StatusPill, fmtValue } from "./ui";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

const PALETTE = ["#1f4fbf", "#d97a2b", "#23806e", "#8a4fbf", "#b8433b", "#5f7a1f", "#2b8ab8", "#7a6a5a"];

/* eslint-disable @typescript-eslint/no-explicit-any */

export type MetricsCanvasData = {
  kind: "metrics"; title: string; metrics: { name: string; label: string; format: string }[];
  by: string[]; chart: string; as_of: string; columns: string[]; rows: unknown[][]; sql: string;
  definitions: Record<string, { label: string; description?: string; source: string; ratio?: string[]; measure?: string; filter?: string }>;
};

export function MetricsCanvas({ data }: { data: MetricsCanvasData }) {
  const [tab, setTab] = useState<"chart" | "table" | "how">(data.chart === "table" || data.rows.length === 1 && !data.by.length ? "table" : "chart");
  const nby = data.by.length;
  const cats = data.rows.map((r) => r.slice(0, nby).map(String).join(" · ") || "Total");
  const fmtOf = Object.fromEntries(data.metrics.map((m) => [m.name, m.format]));
  const series = data.metrics.map((m, i) => ({
    name: m.label, type: data.chart === "line" ? "line" : "bar", barMaxWidth: 42,
    itemStyle: { color: PALETTE[i % PALETTE.length], borderRadius: [3, 3, 0, 0] },
    data: data.rows.map((r) => r[data.columns.indexOf(m.name)] as number),
    yAxisIndex: data.metrics.length > 1 && m.format !== data.metrics[0].format ? 1 : 0,
  }));
  const axisFmt = (f: string) => (v: number) => fmtValue(v, f);
  const option = {
    animation: false,
    grid: { left: 64, right: 24, top: 36, bottom: cats.some((c) => c.length > 14) ? 72 : 36 },
    tooltip: { trigger: "axis", valueFormatter: (v: number) => fmtValue(v, data.metrics[0].format) },
    legend: data.metrics.length > 1 ? { top: 0 } : undefined,
    xAxis: { type: "category", data: cats, axisLabel: { interval: 0, rotate: cats.some((c) => c.length > 14) ? 25 : 0, fontSize: 11 } },
    yAxis: [
      { type: "value", axisLabel: { formatter: axisFmt(data.metrics[0].format), fontSize: 11 }, splitLine: { lineStyle: { color: "#ece9e2" } } },
      ...(series.some((s) => s.yAxisIndex === 1) ? [{ type: "value", axisLabel: { formatter: axisFmt(data.metrics.find((m) => m.format !== data.metrics[0].format)!.format) } }] : []),
    ],
    series,
  };
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-panel">
      <div className="flex flex-wrap items-center gap-2 border-b border-line-soft px-4 py-2.5">
        <b className="text-sm">{data.title}</b>
        <Pill tone="ok" title="Computed from governed definitions">Governed</Pill>
        <span className="text-xs text-muted">valued {data.as_of}</span>
        <div className="ml-auto flex gap-1">
          {(["chart", "table", "how"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`rounded px-2 py-1 text-xs ${tab === t ? "bg-ink text-white" : "text-ink-2 hover:bg-sunk"}`}>
              {t === "how" ? "How it's built" : t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      </div>
      {tab === "chart" && <ReactECharts option={option} style={{ height: 300 }} notMerge />}
      {tab === "table" && (
        <div className="max-h-80 overflow-auto">
          <table className="w-full text-[13px]">
            <thead className="sticky top-0 bg-sunk"><tr>{data.columns.map((c) => <th key={c} className="px-4 py-2 text-left font-semibold">{data.metrics.find((m) => m.name === c)?.label ?? c.replace(/_/g, " ")}</th>)}</tr></thead>
            <tbody>{data.rows.map((r, i) => <tr key={i} className="border-t border-line-soft">{r.map((v, j) => <td key={j} className={`px-4 py-1.5 ${j >= nby ? "text-right tabular-nums" : ""}`}>{fmtValue(v, fmtOf[data.columns[j]])}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
      {tab === "how" && (
        <div className="space-y-3 p-4 text-[13px]">
          {Object.entries(data.definitions).map(([n, d]) => (
            <div key={n}>
              <div className="font-semibold">{d.label} <span className="font-normal text-muted">({d.source === "pack" ? "industry pack" : d.source.replace("client:", "client override: ")})</span></div>
              <div className="text-ink-2">{d.description}</div>
              {d.ratio && <div className="mt-0.5 font-mono text-xs text-muted">= {d.ratio[0]} ÷ {d.ratio[1]}</div>}
              {d.measure && <div className="mt-0.5 font-mono text-xs text-muted">{d.measure}{d.filter ? `  where ${d.filter}` : ""}</div>}
            </div>
          ))}
          <details><summary className="cursor-pointer text-muted">Generated SQL</summary><pre className="mt-2 max-h-64 overflow-auto rounded bg-sunk p-3 font-mono text-[11px]">{data.sql}</pre></details>
        </div>
      )}
    </div>
  );
}

export function TraceCanvas({ trace }: { trace: any }) {
  const sem = trace.semantic ?? {};
  const onto = trace.ontology ?? {};
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-panel text-[13px]">
      <div className="border-b border-line-soft px-4 py-2.5"><b className="text-sm">How this number is built</b> <span className="text-xs text-muted">valued {sem.valuation_date}</span></div>
      <div className="grid gap-px bg-line-soft md:grid-cols-3">
        <div className="bg-panel p-4">
          <div className="lbl mb-2">Semantic layer · metrics</div>
          {(sem.metrics ?? []).map((m: any) => <MetricTree key={m.name} node={m} />)}
          {(sem.dimensions ?? []).map((d: any) => <div key={d.name} className="mt-1 text-muted">{d.role}: {d.label}</div>)}
        </div>
        <div className="bg-panel p-4">
          <div className="lbl mb-2">Ontology · concepts and joins</div>
          {(onto.entities ?? []).map((e: any) => <div key={e.name}><b>{e.label}</b> <span className="text-muted">{e.name !== e.label ? `(${e.name})` : ""}</span></div>)}
          {(onto.relationships ?? []).slice(0, 8).map((r: any, i: number) => <div key={i} className="text-muted">{r.from} → {r.to} <span className="font-mono text-xs">via {r.slot}</span></div>)}
        </div>
        <div className="bg-panel p-4">
          <div className="lbl mb-2">Context layer · sources</div>
          {(trace.context ?? []).map((c: any) => (
            <div key={c.entity} className="mb-1.5">
              <b>{c.label}</b>
              {(c.units ?? []).map((u: any, i: number) => <div key={i} className="text-muted">{u.unit}: <span className="font-mono text-xs">{(u.tables ?? []).join(", ") || "query"}</span>{u.transform?.length ? ` · ${u.transform.join(", ")}` : ""}{u.rows != null ? ` · ${Number(u.rows).toLocaleString()} rows` : ""}</div>)}
            </div>
          ))}
          {(trace.trust?.findings ?? []).length > 0 && <div className="mt-2"><Pill tone="warn">{trace.trust.findings.length} data-quality finding(s)</Pill></div>}
        </div>
      </div>
    </div>
  );
}

function MetricTree({ node, depth = 0 }: { node: any; depth?: number }) {
  return (
    <div style={{ marginLeft: depth * 12 }}>
      <div><b>{node.label ?? node.name}</b>{node.entity ? <span className="text-muted"> on {node.entity}</span> : null}</div>
      {node.formula && <div className="font-mono text-[11px] text-muted">{node.formula}</div>}
      {(node.children ?? []).map((c: any) => <MetricTree key={c.name} node={c} depth={depth + 1} />)}
    </div>
  );
}

export function ScanCanvas({ report }: { report: ScanReport }) {
  const proposed = report.results.filter((r) => r.action === "proposed");
  const asked = report.results.filter((r) => r.action === "asked");
  const other = report.results.filter((r) => !["proposed", "asked"].includes(r.action));
  const ev = report.evaluation;
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-panel text-[13px]">
      <div className="flex flex-wrap items-center gap-2 border-b border-line-soft px-4 py-2.5">
        <b className="text-sm">Curator read {report.documents} documents</b>
        <Pill tone="accent">{proposed.length} proposed</Pill><Pill tone="warn">{asked.length} questions</Pill><Pill>{report.set_aside.length} set aside</Pill>
        {ev && ev.needles > 0 && <span className="ml-auto text-xs text-muted">answer key: {ev.found}/{ev.needles} needles · {ev.decoys_acted_on} decoys acted on</span>}
      </div>
      <div className="grid gap-px bg-line-soft md:grid-cols-2">
        <div className="bg-panel p-4">
          <div className="lbl mb-2">Drafted for owners</div>
          {proposed.length === 0 && asked.length === 0 && <div className="text-muted">Nothing new.</div>}
          {[...proposed, ...asked].map((r, i) => (
            <div key={i} className="mb-1.5 flex gap-2"><Pill tone={r.action === "proposed" ? "accent" : "warn"}>{r.action === "proposed" ? "Proposal" : "Question"}</Pill>
              {r.id ? <Link href={`/inbox?id=${r.id}`} className="text-accent hover:text-accent-ink">{r.title}</Link> : r.title}</div>
          ))}
          {other.map((r, i) => <div key={i} className="mb-1 text-muted">{r.action.replace(/_/g, " ")}: {r.title ?? r.why}</div>)}
        </div>
        <div className="bg-panel p-4">
          <div className="lbl mb-2">Set aside, with the reason</div>
          {report.set_aside.slice(0, 12).map((s, i) => <div key={i} className="mb-1.5"><span className="text-ink-2">{s.title}, p.{s.page}:</span> <span className="text-muted">{s.why}</span></div>)}
        </div>
      </div>
    </div>
  );
}

export function ProposalCanvas({ proposal }: { proposal: Proposal }) {
  return (
    <Link href={`/inbox?id=${proposal.id}`} className="block rounded-lg border border-line bg-panel p-4 hover:border-accent-line">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill status={proposal.status} />
        {(proposal.areas ?? []).map((a) => <Pill key={a}>{proposal.area_titles?.[a] ?? a}</Pill>)}
      </div>
      <div className="mt-2 text-[15px] font-semibold">{proposal.title}</div>
      <div className="mt-1 text-[13px] text-ink-2">{proposal.summary}</div>
      {proposal.evidence?.[0] && <blockquote className="mt-2 border-l-[3px] border-line pl-3 text-[13px] text-ink-2">“{proposal.evidence[0].quote.slice(0, 240)}{proposal.evidence[0].quote.length > 240 ? "…" : ""}” <span className="text-muted">— {proposal.evidence[0].title ?? proposal.evidence[0].doc}, p.{proposal.evidence[0].page}</span></blockquote>}
      <div className="mt-2 text-xs text-accent">Open in the inbox →</div>
    </Link>
  );
}

export function Canvas({ data }: { data: any }) {
  if (data.kind === "metrics") return <MetricsCanvas data={data} />;
  if (data.kind === "trace") return <TraceCanvas trace={data.trace} />;
  if (data.kind === "scan") return <ScanCanvas report={data.report} />;
  if (data.kind === "proposal") return <ProposalCanvas proposal={data.proposal} />;
  return null;
}
