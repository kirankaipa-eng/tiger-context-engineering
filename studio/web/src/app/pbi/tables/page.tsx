"use client";

import { useEffect, useState } from "react";
import { Box, ErrorNote, Loading, Pill } from "@/components/ui";
import { pbi, type Join, type PbiTable } from "@/lib/pbi";
import { useStudio } from "@/lib/studio";

export default function PbiTables() {
  const { pbiModel, version } = useStudio();
  const [data, setData] = useState<{ tables: PbiTable[]; joins: Join[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (pbiModel) pbi.tables(pbiModel).then(setData).catch((e) => setError(e.message)); }, [pbiModel, version]);
  if (error) return <div className="p-6"><ErrorNote error={error} /></div>;
  if (!data) return <div className="p-6"><Loading /></div>;
  return (
    <div className="mx-auto max-w-6xl space-y-4 px-8 py-6">
      <div><h1 className="text-2xl font-semibold">Tables</h1><p className="text-sm text-muted">The model&apos;s tables, the warehouse table each reads, and how they join (many side to one side, as Power BI filters).</p></div>
      <Box title={`${data.joins.length} relationships`}>
        <div className="grid gap-1 text-[13px] sm:grid-cols-2">
          {data.joins.map((j, i) => (
            <div key={i} className={j.inactive ? "text-muted" : ""}><span className="font-mono text-xs">{j.from}</span> → <span className="font-mono text-xs">{j.to}</span>{j.inactive && " (inactive)"}{j.one_to_one && " (1:1)"}</div>
          ))}
        </div>
      </Box>
      <div className="grid gap-4 md:grid-cols-2">
        {data.tables.map((t) => (
          <Box key={t.name} title={t.name} action={t.warehouse ? <span className="font-mono text-[11px] text-muted">{t.warehouse}</span> : <Pill tone="warn">no warehouse table</Pill>}>
            {t.why && <p className="mb-2 text-xs text-muted">{t.why}</p>}
            {t.from && t.from.startsWith("assumed") && <p className="mb-2 text-xs text-warn">Warehouse name assumed; confirm it in capture.yaml.</p>}
            <table className="w-full text-[12px]">
              <tbody>{t.columns.map((c) => (
                <tr key={c.name} className="border-t border-line-soft"><td className="py-1 pr-2">{c.name}</td><td className="py-1 pr-2 font-mono text-[11px] text-muted">{c.column !== c.name ? c.column : ""}</td><td className="py-1 text-right text-muted">{c.type}</td></tr>
              ))}</tbody>
            </table>
          </Box>
        ))}
      </div>
    </div>
  );
}
