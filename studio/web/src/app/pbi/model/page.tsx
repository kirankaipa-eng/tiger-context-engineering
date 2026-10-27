"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Stat } from "@/components/pbi";
import { Box, ErrorNote, Loading, Pill } from "@/components/ui";
import { pbi, type Overview } from "@/lib/pbi";
import { useStudio } from "@/lib/studio";

export default function PbiCoverage() {
  const { pbiModel, version } = useStudio();
  const [o, setO] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (pbiModel) pbi.overview(pbiModel).then(setO).catch((e) => setError(e.message)); }, [pbiModel, version]);
  if (error) return <div className="p-6"><ErrorNote error={error} /></div>;
  if (!o) return <div className="p-6"><Loading /></div>;
  const c = o.coverage;
  const share = c.measures ? Math.round((100 * (c.captured + c.presentation)) / c.measures) : 0;
  return (
    <div className="mx-auto max-w-5xl space-y-5 px-8 py-6">
      <div>
        <h1 className="text-2xl font-semibold">{o.name}</h1>
        <p className="text-sm text-muted">
          {o.imported ? `Captured from ${o.imported.from} on ${o.imported.on}. ` : ""}
          {o.warehouse ? <>Numbers run on {o.warehouse}.</> : <span className="text-bad">No data to run on: {o.data_error}</span>}
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Measures" value={c.measures} />
        <Stat label="Captured" value={c.captured} tone="ok" />
        <Stat label="Display-only" value={c.presentation} />
        <Stat label="Not captured" value={c.not_captured} tone={c.not_captured ? "warn" : "ok"} />
      </div>
      <div className="flex flex-wrap gap-2 text-sm">
        <Link href="/pbi/drill" className="rounded-md bg-accent px-4 py-2 font-medium text-white hover:bg-accent-ink">Drill into a metric</Link>
        <Link href="/pbi/explore" className="rounded-md border border-line bg-panel px-4 py-2 font-medium hover:bg-sunk">Explore rows</Link>
        <Link href="/pbi/ask" className="rounded-md border border-line bg-panel px-4 py-2 font-medium hover:bg-sunk">Ask a question</Link>
      </div>
      <Box title={`Why ${c.not_captured} measures are not captured (${share}% of the model is usable)`}>
        {c.groups.length === 0 ? <p className="text-sm text-muted">Every measure is captured or display-only.</p> : (
          <table className="w-full text-[13px]">
            <thead className="text-left text-muted"><tr><th className="py-1.5 pr-3 font-semibold">Blocks</th><th className="pr-3 font-semibold">Kind</th><th className="font-semibold">Reason, and measures where it starts</th></tr></thead>
            <tbody>{c.groups.map((g, i) => (
              <tr key={i} className="border-t border-line-soft align-top">
                <td className="py-2 pr-3 tabular-nums" title="measures it stops: its own and those built on them">{g.blocks}</td>
                <td className="pr-3"><Pill>{g.kind}</Pill></td>
                <td className="py-2">
                  <div className="text-ink-2">{g.reason}</div>
                  <div className="mt-1 flex flex-wrap gap-x-3 text-xs">
                    {g.examples.map((e) => <Link key={e.key} className="text-accent hover:text-accent-ink" href={`/pbi/metrics/${e.key}`}>{e.label}</Link>)}
                    {g.roots.length > g.examples.length && <span className="text-muted">and {g.roots.length - g.examples.length} more</span>}
                  </div>
                </td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Box>
      <Box title={`${Object.keys(o.tables).length} tables`} action={<Link href="/pbi/tables" className="text-xs text-accent">Columns and joins</Link>}>
        <div className="flex flex-wrap gap-2">{Object.entries(o.tables).map(([t, n]) => <Pill key={t}>{t} · {n}</Pill>)}</div>
      </Box>
    </div>
  );
}
