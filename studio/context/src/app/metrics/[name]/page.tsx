"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Box, Button, Crumbs, ErrorNote, Loading, Pill, fmtValue } from "@/components/ui";
import { api, type Proposal } from "@/lib/api";
import { useStudio } from "@/lib/studio";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });
type Metric = Awaited<ReturnType<typeof api.metric>>;
const FIELDS = ["label", "description", "measure", "filter"] as const;

export default function MetricPage() {
  const { name } = useParams<{ name: string }>();
  const { client, version, setScreen, user, bump, refreshCounts } = useStudio();
  const router = useRouter();
  const [m, setM] = useState<Metric | null>(null);
  const [by, setBy] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [scope, setScope] = useState<"client" | "pack">("client");
  const [why, setWhy] = useState("");
  const [preview, setPreview] = useState<Proposal["impact"] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => { if (client) api.metric(client, name, by).then(setM).catch((e) => setError(e.message)); }, [client, name, by, version]);
  useEffect(() => { if (m) setScreen({ screen: "metric", metric: name, title: m.definition.label }); }, [m, name, setScreen]);

  const changed = useMemo(() => Object.fromEntries(Object.entries(draft).filter(([k, v]) => v !== String((m?.definition as Record<string, unknown> | undefined)?.[k] ?? ""))), [draft, m]);
  if (error && !m) return <div className="p-6"><ErrorNote error={error} /></div>;
  if (!m) return <div className="p-6"><Loading /></div>;
  const d = m.definition;
  const op = { op: "set_metric", metric: name, fields: changed, ...(scope === "client" ? { client } : { client: null, for_client: client }) };

  const runPreview = async () => {
    setBusy("Computing on your draft"); setError(null);
    try { const r = await api.preview({ client, ops: [op], metrics: [name], by: m.by }); setPreview(r.impact); if (r.checks.some((c) => !c.ok)) setError(r.checks.filter((c) => !c.ok).map((c) => `${c.label}: ${c.detail}`).join("; ")); }
    catch (e: any) { setError(e.message); } finally { setBusy(null); } // eslint-disable-line @typescript-eslint/no-explicit-any
  };
  const submit = async () => {
    setBusy("Sending"); setError(null);
    try {
      const p = await api.createProposal({ client, title: `Change ${d.label}${scope === "client" ? ` for ${client.replace(/_/g, " ")}` : " (industry pack)"}`, summary: why || "Changed in the Studio.", ops: [op] });
      bump(); refreshCounts(); router.push(`/inbox?id=${p.id}`);
    } catch (e: any) { setError(e.message); } finally { setBusy(null); } // eslint-disable-line @typescript-eslint/no-explicit-any
  };

  const rows = m.values?.rows ?? [];
  const nby = m.by.length;
  const chart = {
    animation: false,
    grid: { left: 64, right: 16, top: 16, bottom: 48 },
    tooltip: { trigger: "axis", valueFormatter: (v: number) => fmtValue(v, d.format) },
    xAxis: { type: "category", data: rows.map((r) => r.slice(0, nby).join(" · ") || "Total"), axisLabel: { interval: 0, fontSize: 11 } },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => fmtValue(v, d.format), fontSize: 11 }, splitLine: { lineStyle: { color: "#ece9e2" } } },
    series: [{ type: "bar", barMaxWidth: 48, itemStyle: { color: "#1f4fbf", borderRadius: [3, 3, 0, 0] }, data: rows.map((r) => r[nby] as number) }],
  };
  const editing = Object.keys(draft).length > 0;

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-8 py-6">
      <Crumbs items={[{ label: "Metrics", href: "/metrics" }, { label: d.label }]} />
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-3xl font-semibold">{d.label}</h1>
        {d.source === "pack" ? <Pill>industry pack</Pill> : <Pill tone="accent">client override</Pill>}
        {editing && <Pill tone="warn">Your draft · not live</Pill>}
        {m.pending.map((p) => <Link key={p.id} href={`/inbox?id=${p.id}`}><Pill tone="warn">{p.title}</Pill></Link>)}
        {!editing && <Button className="ml-auto" onClick={() => setDraft(Object.fromEntries(FIELDS.map((f) => [f, String((d as Record<string, unknown>)[f] ?? "")])))} disabled={!user}>Edit definition</Button>}
      </div>

      <Box title="Definition">
        {!editing ? (
          <div className="space-y-2 text-sm">
            <p className="leading-relaxed">{d.description}</p>
            {d.ratio && <div className="flex flex-wrap items-center gap-3"><Link href={`/metrics/${d.ratio[0]}`} className="rounded-lg border border-[#bdb9ae] bg-sunk px-3.5 py-2.5 hover:border-accent-line">{m.parts.find((p) => p.name === d.ratio![0])?.label ?? d.ratio[0]}</Link><span className="text-2xl text-muted">÷</span><Link href={`/metrics/${d.ratio[1]}`} className="rounded-lg border border-[#bdb9ae] bg-sunk px-3.5 py-2.5 hover:border-accent-line">{m.parts.find((p) => p.name === d.ratio![1])?.label ?? d.ratio[1]}</Link></div>}
            {d.sum_of && <div className="text-[13px] text-ink-2">Sum of: {d.sum_of.join(" + ")}</div>}
            {d.measure && <div className="text-[13px]"><span className="text-muted">On</span> {d.entity} · <span className="font-mono text-xs">{d.measure}</span>{d.filter && <> <span className="text-muted">where</span> <span className="font-mono text-xs">{d.filter}</span></>}</div>}
            <div className="text-xs text-muted">Valued as of {m.as_of || "the governed valuation date"} · cuts: {m.dimensions.map((x) => x.replace(/_/g, " ")).join(", ")}</div>
          </div>
        ) : (
          <div className="space-y-3">
            {FIELDS.filter((f) => f !== "measure" || d.measure).filter((f) => f !== "filter" || d.measure).map((f) => (
              <label key={f} className="block"><span className="lbl">{f === "filter" ? "Only count rows where (filter)" : f}</span>
                {f === "description" ? <textarea value={draft[f] ?? ""} onChange={(e) => setDraft({ ...draft, [f]: e.target.value })} rows={2} className="mt-1 w-full rounded-md border border-line p-2.5 text-sm outline-none focus:border-accent-line" />
                  : <input value={draft[f] ?? ""} onChange={(e) => setDraft({ ...draft, [f]: e.target.value })} className={`mt-1 w-full rounded-md border border-line px-3 py-2 text-sm outline-none focus:border-accent-line ${f === "measure" || f === "filter" ? "font-mono text-xs" : ""}`} />}
              </label>
            ))}
            <fieldset className="flex items-center gap-4 text-sm"><legend className="lbl mb-1">Applies to</legend>
              <label className="flex items-center gap-1.5"><input type="radio" checked={scope === "client"} onChange={() => setScope("client")} /> This client only</label>
              <label className="flex items-center gap-1.5"><input type="radio" checked={scope === "pack"} onChange={() => setScope("pack")} /> Every client (industry pack)</label>
            </fieldset>
            <label className="block"><span className="lbl">Reason</span><input value={why} onChange={(e) => setWhy(e.target.value)} placeholder="e.g. actuarial memo, p.1" className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm outline-none focus:border-accent-line" /></label>
          </div>
        )}
      </Box>

      {editing && (
        <Box title={`Before and after · valued ${m.as_of}`} action={<Button small onClick={runPreview} disabled={!!busy || !Object.keys(changed).length}>{busy === "Computing on your draft" ? "Computing…" : "Compute"}</Button>}>
          {!preview ? <div className="text-[13px] text-muted">Compute to see the numbers on your draft next to the live definition.</div> : (
            <table className="w-full text-[13px]">
              <thead><tr className="text-left text-muted"><th className="py-1 font-medium">{m.by.join(", ").replace(/_/g, " ") || "Total"}</th><th className="text-right font-medium">Live today</th><th className="text-right font-medium">With your draft</th><th className="text-right font-medium">Change</th></tr></thead>
              <tbody>{(preview.rows ?? []).map((r, i) => (
                <tr key={i} className="border-t border-line-soft"><td className="py-1.5">{r.group.join(" · ") || "Total"}</td><td className="text-right tabular-nums">{r.before_fmt}</td><td className="text-right font-semibold tabular-nums">{r.after_fmt}</td>
                  <td className="text-right tabular-nums">{r.before != null && r.after != null ? (d.format === "percent" ? `${((r.after - r.before) * 100).toFixed(1)} pts` : fmtValue(r.after - r.before, d.format)) : "—"}</td></tr>
              ))}</tbody>
            </table>
          )}
        </Box>
      )}

      {!editing && (
        <Box title={`By ${m.by.join(", ").replace(/_/g, " ") || "total"}`} action={
          <label className="text-[13px]"><span className="sr-only">Cut by</span>
            <select value={by ?? m.by[0] ?? ""} onChange={(e) => setBy(e.target.value)} className="rounded-md border border-line px-2 py-1">{m.dimensions.map((x) => <option key={x} value={x}>{x.replace(/_/g, " ")}</option>)}</select>
          </label>}>
          {rows.length ? <ReactECharts option={chart} style={{ height: 280 }} notMerge /> : <div className="text-[13px] text-muted">No reference data for this client.</div>}
        </Box>
      )}

      <ErrorNote error={error} />
      {editing && (
        <div className="flex items-center gap-2.5 border-t border-line-soft pt-4">
          <Button primary onClick={submit} disabled={!!busy || !Object.keys(changed).length}>Submit for approval</Button>
          <Button onClick={() => { setDraft({}); setPreview(null); }}>Discard</Button>
          <span className="ml-auto text-[13px] text-muted">Goes to: metric owners{scope === "client" ? " and the client's data steward" : ""}</span>
        </div>
      )}
    </div>
  );
}
