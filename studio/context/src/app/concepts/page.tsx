"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { Empty, Loading, Pill } from "@/components/ui";
import { api } from "@/lib/api";
import { useStudio } from "@/lib/studio";

type Row = Awaited<ReturnType<typeof api.concepts>>["concepts"][number];

function ConceptList() {
  const { client, version, setScreen } = useStudio();
  const params = useSearchParams();
  const [rows, setRows] = useState<Row[] | null>(null);
  const urlQ = params.get("q") ?? "";
  const [q, setQ] = useState(urlQ);
  const [lastUrlQ, setLastUrlQ] = useState(urlQ);
  if (urlQ !== lastUrlQ) { setLastUrlQ(urlQ); setQ(urlQ); }  // header search navigated here
  useEffect(() => { if (client) api.concepts(client).then((r) => setRows(r.concepts)).catch(() => setRows([])); }, [client, version]);
  useEffect(() => { setScreen({ screen: "concepts", title: "Concepts" }); }, [setScreen]);

  const groups = useMemo(() => {
    const term = q.trim().toLowerCase();
    const list = (rows ?? []).filter((r) => !term || `${r.name} ${r.label} ${r.description}`.toLowerCase().includes(term));
    const out = new Map<string, Row[]>();
    for (const r of list) out.set(r.group, [...(out.get(r.group) ?? []), r]);
    return [...out.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [rows, q]);

  return (
    <div className="mx-auto max-w-5xl space-y-5 px-8 py-6">
      <div className="flex items-end gap-4">
        <div><h1 className="text-2xl font-semibold">Concepts</h1><p className="text-sm text-muted">What things are, in this company&apos;s words. Click one to read or change it.</p></div>
        <label className="ml-auto w-72"><span className="sr-only">Filter concepts</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter" className="w-full rounded-md border border-line bg-panel px-3 py-2 text-sm outline-none focus:border-accent-line" /></label>
      </div>
      {rows === null ? <Loading /> : groups.length === 0 ? <Empty>No concept matches “{q}”.</Empty> : groups.map(([g, list]) => (
        <section key={g}>
          <div className="lbl mb-2">{g}</div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {list.map((c) => (
              <Link key={c.name} href={`/concepts/${c.name}`} className="rounded-lg border border-line bg-panel p-3.5 hover:border-accent-line">
                <div className="flex items-center gap-2"><b className="text-sm">{c.label}</b>{c.pending > 0 && <Pill tone="warn">{c.pending} pending</Pill>}{c.abstract && <Pill>kernel</Pill>}</div>
                {c.label !== c.name && <div className="text-xs text-muted">Standard name: {c.name}</div>}
                <div className="mt-1 line-clamp-2 text-[13px] text-ink-2">{c.description || "No description yet."}</div>
                <div className="mt-1.5 text-xs text-muted">{c.metrics.length} metrics{c.customized.length ? " · customized" : ""}</div>
              </Link>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export default function ConceptsPage() {
  return <Suspense fallback={<div className="p-6"><Loading /></div>}><ConceptList /></Suspense>;
}
