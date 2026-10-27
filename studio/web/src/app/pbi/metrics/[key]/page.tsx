"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Box, Crumbs, ErrorNote, Loading, Pill } from "@/components/ui";
import { colLabel, pbi, type PbiMetricDetail } from "@/lib/pbi";
import { useStudio } from "@/lib/studio";

export default function PbiMetric() {
  const { key } = useParams<{ key: string }>();
  const { pbiModel, version } = useStudio();
  const [m, setM] = useState<PbiMetricDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (pbiModel) pbi.metric(pbiModel, key).then(setM).catch((e) => setError(e.message)); }, [pbiModel, key, version]);
  if (error) return <div className="p-6"><ErrorNote error={error} /></div>;
  if (!m) return <div className="p-6"><Loading /></div>;
  return (
    <div className="mx-auto max-w-5xl space-y-4 px-8 py-6">
      <Crumbs items={[{ label: "Metrics", href: "/pbi/metrics" }, { label: m.label }]} />
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{m.label}</h1>
        {m.status === "captured" ? <Pill tone="ok">captured</Pill> : m.status === "presentation" ? <Pill>display-only</Pill> : <Pill tone="warn">not captured · {m.gap}</Pill>}
        {m.status === "captured" && <Link href={`/pbi/drill?metric=${m.key}`} className="ml-auto rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-ink">Drill into it</Link>}
      </div>
      {m.status !== "captured" && m.why && <Box title={m.status === "presentation" ? "What it is" : "Why it is not captured"}><p className="text-sm text-ink-2">{m.why}</p>
        {m.value && <p className="mt-2 text-sm">It shows <Link className="text-accent" href={`/pbi/metrics/${m.value}`}>{m.value}</Link>.</p>}</Box>}
      {m.definition && (
        <Box title="Definition">
          <div className="font-mono text-[13px]">{m.definition}</div>
          {m.parts && m.parts.length > 0 && (
            <div className="mt-3 space-y-1 border-t border-line-soft pt-3">
              {m.parts.map((p) => <div key={p.key} className="text-[13px]"><span className="font-mono text-xs text-muted">{p.key}</span> = <span className="font-mono text-xs">{p.definition}</span></div>)}
            </div>
          )}
          {m.needs?.by?.length ? <div className="mt-3 text-xs text-muted">Asked per {m.needs.by.map(colLabel).join(", ")}.</div> : null}
          {m.needs && Object.keys(m.needs.params).length > 0 && <div className="mt-1 text-xs text-muted">Reads what-if {Object.entries(m.needs.params).map(([k, p]) => `${p.label ?? k} (default ${p.default ?? "none"})`).join(", ")}.</div>}
        </Box>
      )}
      {m.dax && <Box title="DAX in Power BI"><pre className="overflow-auto whitespace-pre-wrap font-mono text-xs text-ink-2">{m.dax}</pre></Box>}
      {m.sql && <Box title="SQL it compiles to"><pre className="max-h-96 overflow-auto whitespace-pre-wrap font-mono text-[11px]">{m.sql}</pre></Box>}
      {m.sql_error && <Box title="SQL it compiles to"><p className="text-sm text-muted">{m.sql_error}</p></Box>}
      {m.columns && m.columns.length > 0 && (
        <Box title={`${m.columns.length} columns it can be broken down by`}>
          <div className="flex flex-wrap gap-1.5">{m.columns.map((c) => <Pill key={c}>{colLabel(c)}</Pill>)}</div>
        </Box>
      )}
    </div>
  );
}
