"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ErrorNote, Loading, Pill } from "@/components/ui";
import { colLabel, pbi, type PbiMetric } from "@/lib/pbi";
import { useStudio } from "@/lib/studio";

const TABS = [
  { id: "captured", label: "Captured" },
  { id: "presentation", label: "Display-only" },
  { id: "not_captured", label: "Not captured" },
  { id: "all", label: "All" },
] as const;

export default function PbiMetrics() {
  const { pbiModel, version } = useStudio();
  const [metrics, setMetrics] = useState<PbiMetric[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("captured");
  const [q, setQ] = useState("");
  useEffect(() => { if (pbiModel) pbi.metrics(pbiModel).then((r) => setMetrics(r.metrics)).catch((e) => setError(e.message)); }, [pbiModel, version]);
  const counts = useMemo(() => Object.fromEntries(TABS.map((t) => [t.id, (metrics ?? []).filter((m) => t.id === "all" || m.status === t.id).length])), [metrics]);
  const rows = useMemo(() => (metrics ?? []).filter((m) => (tab === "all" || m.status === tab)
    && (!q || `${m.key} ${m.label} ${m.definition ?? ""} ${m.why ?? ""}`.toLowerCase().includes(q.toLowerCase()))), [metrics, tab, q]);

  if (error) return <div className="p-6"><ErrorNote error={error} /></div>;
  if (!metrics) return <div className="p-6"><Loading /></div>;
  return (
    <div className="mx-auto max-w-6xl space-y-4 px-8 py-6">
      <div className="flex flex-wrap items-end gap-4">
        <div><h1 className="text-2xl font-semibold">Metrics</h1><p className="text-sm text-muted">The Power BI measures, as captured definitions, or why they are not.</p></div>
        <label className="ml-auto w-72"><span className="sr-only">Filter metrics</span><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter" className="w-full rounded-md border border-line bg-panel px-3 py-2 text-sm outline-none focus:border-accent-line" /></label>
      </div>
      <div className="flex gap-1">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)} className={`rounded-md px-3 py-1.5 text-sm ${tab === t.id ? "bg-ink text-white" : "text-ink-2 hover:bg-panel"}`}>{t.label} <span className="opacity-70">{counts[t.id]}</span></button>
        ))}
      </div>
      <div className="overflow-hidden rounded-lg border border-line bg-panel">
        <table className="w-full text-[13px]">
          <thead className="bg-sunk text-left"><tr><th className="w-64 px-4 py-2 font-semibold">Measure</th><th className="font-semibold">{tab === "not_captured" ? "Why not" : "Definition"}</th><th className="pr-4 font-semibold" /></tr></thead>
          <tbody>{rows.map((m) => (
            <tr key={m.key} className="border-t border-line-soft align-top hover:bg-sunk">
              <td className="px-4 py-2.5">
                <Link className="font-semibold text-accent hover:text-accent-ink" href={`/pbi/metrics/${m.key}`}>{m.label}</Link>
                <div className="font-mono text-[11px] text-muted">{m.key}</div>
              </td>
              <td className="py-2.5 pr-3 text-ink-2">
                {m.status === "captured" ? <span className="font-mono text-xs">{m.definition}</span> : m.why}
                {m.needs?.by?.length ? <div className="mt-1 text-xs text-muted">needs {m.needs.by.map(colLabel).join(", ")}</div> : null}
              </td>
              <td className="whitespace-nowrap py-2.5 pr-4 text-right">
                {m.status === "captured" ? <Link href={`/pbi/drill?metric=${m.key}`} className="text-xs text-accent hover:text-accent-ink">Drill</Link>
                  : m.status === "presentation" ? <Pill>display</Pill> : <Pill tone="warn">{m.gap ?? "not captured"}</Pill>}
              </td>
            </tr>
          ))}</tbody>
        </table>
        {!rows.length && <div className="px-4 py-6 text-center text-sm text-muted">Nothing here.</div>}
      </div>
    </div>
  );
}
