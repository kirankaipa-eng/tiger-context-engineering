"use client";

import clsx from "clsx";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Box, Button, Crumbs, ErrorNote, Loading, Pill } from "@/components/ui";
import { api } from "@/lib/api";
import { personName, useStudio } from "@/lib/studio";

type Concept = Awaited<ReturnType<typeof api.concept>>;
const TABS = ["Definition", "Fields", "Where it comes from", "Related"] as const;

export default function ConceptPage() {
  const { name } = useParams<{ name: string }>();
  const { client, version, setScreen, people, user, bump, refreshCounts } = useStudio();
  const router = useRouter();
  const [c, setC] = useState<Concept | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Definition");
  const [editing, setEditing] = useState(false);
  const [def, setDef] = useState("");
  const [label, setLabel] = useState("");
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!client) return;
    api.concept(client, name).then((r) => { setC(r); setDef(r.definition?.text ?? r.description); setLabel(r.label); }).catch((e) => setError(e.message));
  }, [client, name, version]);
  useEffect(() => { if (c) setScreen({ screen: "concept", concept: c.name, label: c.label, title: c.label }); }, [c, setScreen]);

  if (error) return <div className="p-6"><ErrorNote error={error} /></div>;
  if (!c) return <div className="p-6"><Loading /></div>;

  const submit = async () => {
    const ops: Record<string, unknown>[] = [];
    if (def.trim() && def.trim() !== (c.definition?.text ?? c.description).trim()) ops.push({ op: "set_definition", concept: c.name, title: label || c.label, text: def });
    if (label.trim() && label.trim() !== c.label) ops.push({ op: "set_label", kind: "classes", name: c.name, label: label.trim() });
    if (!ops.length) { setError("Nothing changed."); return; }
    setBusy(true); setError(null);
    try {
      const p = await api.createProposal({ client, title: label.trim() !== c.label ? `Call ${c.label} “${label.trim()}”` : `Update the definition of ${c.label}`, summary: why || "Suggested in the Studio.", ops });
      bump(); refreshCounts(); router.push(`/inbox?id=${p.id}`);
    } catch (e: any) { setError(e.message); } finally { setBusy(false); } // eslint-disable-line @typescript-eslint/no-explicit-any
  };

  const enumAttrs = c.attributes.filter((a) => a.values?.length);
  return (
    <div className="mx-auto max-w-6xl space-y-4 px-8 py-6">
      <Crumbs items={[{ label: "Concepts", href: "/concepts" }, { label: c.group }, { label: c.label }]} />
      <div className="flex items-end gap-4">
        <div>
          <h1 className="text-3xl font-semibold">{c.label}</h1>
          <div className="mt-1 text-[13px] text-muted">{c.label !== c.name ? `Standard name: ${c.name} · ` : ""}{c.aliases.length ? `also called ${c.aliases.join(", ")} · ` : ""}owned by {c.definition_owners.map((o) => personName(people, o)).join(", ") || "—"}</div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {c.pending.map((p) => <Link key={p.id} href={`/inbox?id=${p.id}`}><Pill tone="warn">{p.title}</Pill></Link>)}
          <Button primary onClick={() => { setEditing(true); setTab("Definition"); }} disabled={!user}>Suggest a change</Button>
        </div>
      </div>
      <div className="flex gap-6 border-b border-line">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={clsx("-mb-px border-b-2 px-0.5 py-2.5 text-sm", tab === t ? "border-ink font-semibold" : "border-transparent text-muted")}>
            {t}{t === "Fields" ? ` · ${c.attributes.length}` : t === "Where it comes from" ? ` · ${c.sources.length}` : ""}
          </button>
        ))}
      </div>

      {tab === "Definition" && (
        <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
          <Box title={`What it means at ${client.replace(/_/g, " ")}`}>
            {editing ? (
              <div className="space-y-3">
                <label className="block"><span className="lbl">Name people use</span><input value={label} onChange={(e) => setLabel(e.target.value)} className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm outline-none focus:border-accent-line" /></label>
                <label className="block"><span className="lbl">Business definition</span><textarea value={def} onChange={(e) => setDef(e.target.value)} rows={5} className="mt-1 w-full rounded-md border border-line p-3 text-sm leading-relaxed outline-none focus:border-accent-line" /></label>
                <label className="block"><span className="lbl">Why (for the owner)</span><input value={why} onChange={(e) => setWhy(e.target.value)} placeholder="e.g. the claims manual changed in September" className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm outline-none focus:border-accent-line" /></label>
                <ErrorNote error={error} />
                <div className="flex items-center gap-2"><Button primary onClick={submit} disabled={busy}>Send for approval</Button><Button onClick={() => setEditing(false)}>Cancel</Button>
                  <span className="text-xs text-muted">Goes to {c.definition_owners.map((o) => personName(people, o)).join(", ")}</span></div>
              </div>
            ) : (
              <>
                <p className="text-sm leading-relaxed">{c.definition?.text ?? c.description ?? "No definition yet."}</p>
                <div className="mt-2 text-xs text-muted">{c.definition ? `Written by people · updated ${c.definition.updated}` : "From the industry pack; no client definition written yet."}</div>
              </>
            )}
          </Box>
          <Box title="Where it comes from">
            <div className="space-y-1.5 text-[13px]">
              {c.sources.length === 0 ? <div className="text-muted">Not mapped in any unit.</div> : c.sources.map((s) => (
                <Link key={s.unit} href={`/mappings/${s.unit}/${c.name}`} className="block hover:text-accent"><b>{s.unit}</b> · {s.source_system}<div className="text-xs text-muted">{s.rows != null ? `${s.rows.toLocaleString()} rows · ` : ""}{s.slots_mapped}/{s.slots_total} fields mapped</div></Link>
              ))}
            </div>
          </Box>
          {enumAttrs.length > 0 && (
            <Box title="Key value sets" className="lg:col-span-2">
              <div className="space-y-2 text-[13px]">{enumAttrs.slice(0, 6).map((a) => (
                <div key={a.name}><b>{a.label}</b>: <span className="text-ink-2">{a.values!.slice(0, 14).join(", ")}{a.values!.length > 14 ? ` … (${a.values!.length})` : ""}</span>
                  {c.pending.length > 0 && a.change && <Pill tone="warn" className="ml-2">{a.change}</Pill>}</div>
              ))}</div>
            </Box>
          )}
        </div>
      )}

      {tab === "Fields" && (
        <div className="overflow-hidden rounded-lg border border-line bg-panel">
          <table className="w-full text-[13px]">
            <thead className="bg-sunk text-left"><tr><th className="px-4 py-2 font-semibold">Field</th><th className="font-semibold">Meaning</th><th className="font-semibold">Values</th><th className="pr-4 font-semibold">Notes</th></tr></thead>
            <tbody>{c.attributes.map((a) => (
              <tr key={a.name} className="border-t border-line-soft align-top">
                <td className="px-4 py-2"><b>{a.label}</b>{a.label !== a.name && <div className="font-mono text-[11px] text-muted">{a.name}</div>}</td>
                <td className="py-2 pr-3 text-ink-2">{a.description || "—"}</td>
                <td className="py-2 pr-3">{a.values ? `${a.values.slice(0, 6).join(", ")}${a.values.length > 6 ? ` … (${a.values.length})` : ""}` : a.kind === "class" ? `→ ${a.range}` : a.range}</td>
                <td className="py-2 pr-4">
                  <div className="flex flex-wrap gap-1">
                    {a.required && <Pill>required</Pill>}
                    {a.sensitivity && <Pill tone="warn">{a.sensitivity} · hidden from agents</Pill>}
                    {a.change && <Pill tone="accent">{a.change}</Pill>}
                  </div>
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {tab === "Where it comes from" && (
        <div className="grid gap-3 md:grid-cols-2">{c.sources.map((s) => (
          <Box key={s.unit} title={`${s.unit} · ${s.unit_name}`} action={<Link className="text-[13px] text-accent" href={`/mappings/${s.unit}/${c.name}`}>Open mapping</Link>}>
            <div className="space-y-1 text-[13px]">
              <div>{s.source_system}</div>
              <div className="font-mono text-xs text-muted">{s.tables.join(", ") || "query"}</div>
              <div className="text-muted">{s.transform.join(", ")} · {s.slots_mapped}/{s.slots_total} fields{s.rows != null ? ` · ${s.rows.toLocaleString()} rows` : ""}</div>
              {s.notes.map((n, i) => <p key={i} className="text-ink-2">{n}</p>)}
            </div>
          </Box>
        ))}</div>
      )}

      {tab === "Related" && (
        <div className="grid gap-4 md:grid-cols-2">
          <Box title="Relationships">
            <div className="space-y-1 text-[13px]">{c.relationships.map((r, i) => (
              <div key={i}>{r.from === c.name ? <>{r.label} → <Link className="text-accent" href={`/concepts/${r.to}`}>{r.to}</Link></> : <><Link className="text-accent" href={`/concepts/${r.from}`}>{r.from}</Link> → this ({r.label})</>}</div>
            ))}</div>
          </Box>
          <Box title="Metrics that use it">
            <div className="flex flex-wrap gap-1.5">{c.metrics.length ? c.metrics.map((m) => <Link key={m} href={`/metrics/${m}`}><Pill tone="accent">{m.replace(/_/g, " ")}</Pill></Link>) : <span className="text-[13px] text-muted">None</span>}</div>
          </Box>
        </div>
      )}
    </div>
  );
}
