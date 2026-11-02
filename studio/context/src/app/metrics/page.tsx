"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Loading, Pill } from "@/components/ui";
import { api, type MetricDef } from "@/lib/api";
import { useStudio } from "@/lib/studio";

export default function MetricsPage() {
  const { client, version, setScreen } = useStudio();
  const [metrics, setMetrics] = useState<MetricDef[] | null>(null);
  const [q, setQ] = useState("");
  useEffect(() => { if (client) api.metrics(client).then((r) => setMetrics(r.metrics)).catch(() => setMetrics([])); }, [client, version]);
  useEffect(() => { setScreen({ screen: "metrics", title: "Metrics" }); }, [setScreen]);
  const rows = useMemo(() => (metrics ?? []).filter((m) => !q || `${m.name} ${m.label} ${m.description}`.toLowerCase().includes(q.toLowerCase())), [metrics, q]);

  return (
    <div className="mx-auto max-w-5xl space-y-4 px-8 py-6">
      <div className="flex items-end gap-4">
        <div><h1 className="text-2xl font-semibold">Metrics</h1><p className="text-sm text-muted">Governed definitions. Every report and agent uses these; changing one is a proposal.</p></div>
        <label className="ml-auto w-72"><span className="sr-only">Filter metrics</span><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter" className="w-full rounded-md border border-line bg-panel px-3 py-2 text-sm outline-none focus:border-accent-line" /></label>
      </div>
      {metrics === null ? <Loading /> : (
        <div className="overflow-hidden rounded-lg border border-line bg-panel">
          <table className="w-full text-[13px]">
            <thead className="bg-sunk text-left"><tr><th className="px-4 py-2 font-semibold">Metric</th><th className="font-semibold">Definition</th><th className="pr-4 font-semibold">Source</th></tr></thead>
            <tbody>{rows.map((m) => (
              <tr key={m.name} className="border-t border-line-soft align-top hover:bg-sunk">
                <td className="px-4 py-2.5"><Link className="font-semibold text-accent hover:text-accent-ink" href={`/metrics/${m.name}`}>{m.label}</Link><div className="text-xs text-muted">{m.format}</div></td>
                <td className="py-2.5 pr-3 text-ink-2">{m.description}{m.ratio && <div className="font-mono text-[11px] text-muted">{m.ratio[0]} ÷ {m.ratio[1]}</div>}</td>
                <td className="py-2.5 pr-4">{m.source === "pack" ? <Pill>industry pack</Pill> : <Pill tone="accent">client override</Pill>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
