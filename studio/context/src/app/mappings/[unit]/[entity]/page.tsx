"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Box, Button, Crumbs, ErrorNote, Loading, Pill } from "@/components/ui";
import { api } from "@/lib/api";
import { useStudio } from "@/lib/studio";

type Mapping = Awaited<ReturnType<typeof api.mapping>>;

export default function MappingPage() {
  const { unit, entity } = useParams<{ unit: string; entity: string }>();
  const { client, version, setScreen, user, bump, refreshCounts } = useStudio();
  const router = useRouter();
  const [m, setM] = useState<Mapping | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [edit, setEdit] = useState<{ slot: string; column: string; expr: string; note: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (client) api.mapping(client, unit, entity).then(setM).catch((e) => setError(e.message)); }, [client, unit, entity, version]);
  useEffect(() => { if (m) setScreen({ screen: "mapping", unit, entity, title: `${unit} ${m.label}` }); }, [m, unit, entity, setScreen]);
  if (error && !m) return <div className="p-6"><ErrorNote error={error} /></div>;
  if (!m) return <div className="p-6"><Loading /></div>;

  const submit = async () => {
    if (!edit) return;
    setBusy(true); setError(null);
    try {
      const p = await api.createProposal({ client, title: `Map ${unit} ${m.label}.${edit.slot} from ${edit.column || "an expression"}`, summary: edit.note || "Changed in the Studio.",
        ops: [{ op: "set_mapping", unit, entity, slot: edit.slot, spec: edit.column ? { column: edit.column } : { expr: edit.expr }, note: edit.note }] });
      bump(); refreshCounts(); router.push(`/inbox?id=${p.id}`);
    } catch (e: any) { setError(e.message); } finally { setBusy(false); } // eslint-disable-line @typescript-eslint/no-explicit-any
  };

  const pendingSlots = new Set(m.findings.map((f) => f.slot));
  return (
    <div className="mx-auto max-w-6xl space-y-4 px-8 py-6">
      <Crumbs items={[{ label: "Mappings", href: "/mappings" }, { label: m.unit.code }, { label: m.label }]} />
      <div>
        <h1 className="text-2xl font-semibold">{m.unit.code} source → {m.label}</h1>
        <div className="mt-1 text-[13px] text-muted">{m.unit.source_system} · {m.table ? <span className="font-mono">{m.source_schema}.{m.table}</span> : "built by a query"} · served as <span className="font-mono">{m.view}</span></div>
        {m.note && <p className="mt-2 max-w-3xl text-[13px] text-ink-2">{m.note}</p>}
        {m.pending.length > 0 && <div className="mt-2 flex flex-wrap gap-2">{m.pending.map((p) => <Link key={p.id} href={`/inbox?id=${p.id}`}><Pill tone="warn">{p.status === "question" ? "Question" : "Proposal"}: {p.title}</Pill></Link>)}</div>}
      </div>

      <div className="overflow-hidden rounded-lg border border-line bg-panel">
        <div className="flex items-center px-4 py-3"><div className="lbl">Source columns to fields</div></div>
        <table className="w-full text-[13px]">
          <thead className="bg-sunk text-left"><tr><th className="px-4 py-2 font-semibold">Field</th><th className="font-semibold">From</th><th className="font-semibold">Note</th><th className="pr-4" /></tr></thead>
          <tbody>{m.slots.map((s) => (
            <tr key={s.slot} className={`border-t border-line-soft align-top ${pendingSlots.has(s.slot) ? "bg-warn-soft" : ""}`}>
              <td className="px-4 py-2"><b>{s.slot}</b>{s.sensitive && <Pill tone="warn" className="ml-2">sensitive</Pill>}</td>
              <td className="py-2 pr-3 font-mono text-xs">{s.column ?? s.expr}</td>
              <td className="py-2 pr-3 text-ink-2">{s.note ?? ""}{pendingSlots.has(s.slot) && <div><Pill tone="warn">{m.findings.filter((f) => f.slot === s.slot).map((f) => `${f.check}: ${f.detail}`).join("; ")}</Pill></div>}</td>
              <td className="py-2 pr-4 text-right"><button disabled={!user} onClick={() => setEdit({ slot: s.slot, column: s.column ?? "", expr: s.expr ?? "", note: "" })} className="text-xs text-accent hover:text-accent-ink disabled:opacity-40">Change</button></td>
            </tr>
          ))}</tbody>
        </table>
      </div>

      {edit && (
        <Box title={`Change ${edit.slot}`}>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="block"><span className="lbl">Source column</span><input value={edit.column} onChange={(e) => setEdit({ ...edit, column: e.target.value, expr: e.target.value ? "" : edit.expr })} className="mt-1 w-full rounded-md border border-line px-3 py-2 font-mono text-xs outline-none focus:border-accent-line" /></label>
            <label className="block"><span className="lbl">Or an expression over src</span><input value={edit.expr} onChange={(e) => setEdit({ ...edit, expr: e.target.value, column: e.target.value ? "" : edit.column })} className="mt-1 w-full rounded-md border border-line px-3 py-2 font-mono text-xs outline-none focus:border-accent-line" /></label>
            <label className="block md:col-span-2"><span className="lbl">Why (kept with the mapping)</span><input value={edit.note} onChange={(e) => setEdit({ ...edit, note: e.target.value })} placeholder="e.g. spec v3.2, p.7: PAID_TD excludes expense from Jan 2027" className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm outline-none focus:border-accent-line" /></label>
          </div>
          <div className="mt-3 flex gap-2"><Button primary onClick={submit} disabled={busy || (!edit.column && !edit.expr)}>Submit mapping change</Button><Button onClick={() => setEdit(null)}>Cancel</Button></div>
        </Box>
      )}

      <Box title="Preview: mapped rows" action={<span className="text-xs text-muted">Sensitive fields hidden</span>}>
        {m.preview.rows.length === 0 ? <div className="text-[13px] text-muted">No reference data.</div> : (
          <div className="overflow-x-auto"><table className="text-[12px]">
            <thead><tr>{m.preview.columns.map((c) => <th key={c} className="whitespace-nowrap px-2 py-1 text-left font-semibold">{c}</th>)}</tr></thead>
            <tbody>{m.preview.rows.map((r, i) => <tr key={i} className="border-t border-line-soft">{r.map((v, j) => <td key={j} className="whitespace-nowrap px-2 py-1 font-mono text-[11px]">{v === null ? "—" : String(v)}</td>)}</tr>)}</tbody>
          </table></div>
        )}
      </Box>

      <div className="flex flex-wrap items-center gap-2">
        <span className="lbl mr-1">Checks on this unit</span>
        {m.findings.length === 0 ? <Pill tone="ok">All conformance checks pass</Pill> : m.findings.map((f, i) => <Pill key={i} tone="warn">{f.slot} · {f.check} · {f.violations.toLocaleString()} rows</Pill>)}
      </div>
      {m.query && <details className="text-[13px]"><summary className="cursor-pointer text-muted">Source query</summary><pre className="mt-2 overflow-auto rounded bg-sunk p-3 font-mono text-[11px]">{m.query}</pre></details>}
      <ErrorNote error={error} />
    </div>
  );
}
