"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Box, Loading } from "@/components/ui";
import { api } from "@/lib/api";
import { useStudio } from "@/lib/studio";

type Units = Awaited<ReturnType<typeof api.mappings>>["units"];

export default function MappingsPage() {
  const { client, version, setScreen } = useStudio();
  const [units, setUnits] = useState<Units | null>(null);
  useEffect(() => { if (client) api.mappings(client).then((r) => setUnits(r.units)).catch(() => setUnits([])); }, [client, version]);
  useEffect(() => { setScreen({ screen: "mappings", title: "Mappings" }); }, [setScreen]);
  return (
    <div className="mx-auto max-w-6xl space-y-4 px-8 py-6">
      <div><h1 className="text-2xl font-semibold">Mappings</h1><p className="text-sm text-muted">How each unit&apos;s source system maps onto the shared concepts. Each unit&apos;s engineers own their mappings.</p></div>
      {units === null ? <Loading /> : (
        <div className="grid gap-4 lg:grid-cols-3">
          {units.map((u) => (
            <Box key={u.code} title={`${u.code} · ${u.name}`}>
              <div className="mb-2 text-[13px] text-muted">{u.source_system}</div>
              <div className="space-y-0.5">{u.entities.map((e) => (
                <Link key={e.entity} href={`/mappings/${u.code}/${e.entity}`} className="flex items-center justify-between rounded px-2 py-1.5 text-[13px] hover:bg-sunk">
                  <span>{e.entity}</span><span className="font-mono text-[11px] text-muted">{e.table ?? "query"} · {e.slots}</span>
                </Link>
              ))}</div>
            </Box>
          ))}
        </div>
      )}
    </div>
  );
}
