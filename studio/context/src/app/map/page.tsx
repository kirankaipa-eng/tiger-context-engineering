"use client";

import clsx from "clsx";
import { ArrowDown, X } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { ErrorNote, Loading, Pill } from "@/components/ui";
import { api, API_BASE, type ClassInfo, type OntologyModel } from "@/lib/api";
import { useStudio } from "@/lib/studio";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

/* eslint-disable @typescript-eslint/no-explicit-any */

const LAYER = {
  core: { label: "Kernel", color: "#2b2a27" },
  line: { label: "Industry pack", color: "#6f8fdc" },
  client: { label: "Client", color: "#23806e" },
  unit: { label: "Unit", color: "#b8433b" },
} as const;
const layerKey = (l: string) => (l === "core" || l === "line" || l === "client" ? l : l.startsWith("unit") ? "unit" : "client");
const GROUP_COLORS = ["#1f4fbf", "#d97a2b", "#23806e", "#8a4fbf", "#b8433b", "#5f7a1f", "#2b8ab8", "#7a6a5a", "#c2185b", "#00897b"];

type Overlay = {
  labels: { kind: string; name: string; label: string }[]; added_fields: { concept: string; field: string; description: string }[];
  new_value_sets: { name: string; values: string[] }[]; added_values: { value_set: string; values: string[] }[];
  restrictions: { concept: string; field: string; allow: string[]; reason: string }[];
  required: { concept: string; field: string; reason: string }[]; hidden: string[]; shared_identity: string[];
  metric_overrides: { name: string; label?: string }[];
};
type Layers = {
  kernel: { title: string; concepts: string[]; says: string };
  pack: { title: string; version: string; concepts: string[]; value_sets: number; metrics: number; says: string };
  client: Overlay & { title: string; version: string; says: string };
  units: (Overlay & { code: string; name: string; source_system: string; mapped: string[] })[];
};

export default function MapPage() {
  const { client, version, setScreen, panelOpen, setPanelOpen } = useStudio();
  const [model, setModel] = useState<OntologyModel | null>(null);
  const [layers, setLayers] = useState<Layers | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"graph" | "layers">("graph");
  const [selected, setSelected] = useState<string | null>(null);
  const [unit, setUnit] = useState("all");
  const [colorBy, setColorBy] = useState<"layer" | "group">("layer");
  const [showSources, setShowSources] = useState(false);
  const [showKernel, setShowKernel] = useState(true);
  const [find, setFind] = useState("");
  const panelWas = useRef(panelOpen);

  // The map needs the room: tuck the Curator away here and give it back on the way out.
  useEffect(() => {
    const was = panelWas.current;
    setPanelOpen(false);
    return () => setPanelOpen(was);
  }, [setPanelOpen]);

  useEffect(() => {
    if (!client) return;
    api.map(client).then(setModel).catch((e) => setError(e.message));
    fetch(`${API_BASE}/api/layers?client=${client}`).then((r) => r.json()).then(setLayers).catch(() => {});
  }, [client, version]);
  useEffect(() => { setScreen({ screen: "map", title: selected ? `Map: ${selected}` : "Ontology map", selected }); }, [selected, setScreen]);

  const byName = useMemo(() => new Map((model?.classes ?? []).map((c) => [c.name, c])), [model]);

  const option = useMemo(() => {
    if (!model) return null;
    // `Entity` is the technical root everything descends from; drawing it only adds a hairball.
    let classes = model.classes.filter((c) => c.name !== "Entity" && (showKernel || c.layer !== "core"));
    if (unit !== "all") classes = classes.filter((c) => c.layer === "core" || c.sources.some((s) => s.unit === unit));
    const groups = [...new Set(classes.map((c) => c.group))].sort();
    const degree = new Map<string, number>();
    for (const r of model.relationships) { degree.set(r.from, (degree.get(r.from) ?? 0) + 1); degree.set(r.to, (degree.get(r.to) ?? 0) + 1); }
    const selected = null as string | null;  // selection is highlighted on the chart, so the layout never moves
    const near = new Set<string>();
    const q = find.trim().toLowerCase();
    const nodes: any[] = classes.map((c) => {
      const pending = (model.pending[c.name] ?? []).length > 0;
      const color = colorBy === "layer" ? LAYER[layerKey(c.layer)].color : GROUP_COLORS[groups.indexOf(c.group) % GROUP_COLORS.length];
      const faded = (selected && !near.has(c.name)) || (q && !`${c.name} ${c.label}`.toLowerCase().includes(q));
      return {
        id: c.name, name: c.label, symbol: c.abstract ? "diamond" : "circle",
        symbolSize: 12 + Math.min(degree.get(c.name) ?? 0, 10) * 2.2 + (selected === c.name ? 10 : 0),
        itemStyle: { color, opacity: faded ? 0.15 : 1, borderColor: pending ? "#e0b25c" : "#ffffff", borderWidth: pending ? 4 : 1.5 },
        label: { show: true, position: "right", fontSize: 11, color: faded ? "#d2cfc7" : "#1c1c1a", fontWeight: selected === c.name ? 600 : 400 },
      };
    });
    const present = new Set(nodes.map((n) => n.id));
    const links: any[] = [];
    for (const r of model.relationships) {
      if (!present.has(r.from) || !present.has(r.to) || r.from === r.to) continue;
      const hot = selected && (r.from === selected || r.to === selected);
      links.push({ source: r.from, target: r.to, lineStyle: { color: hot ? "#1f4fbf" : "#cfccc4", width: hot ? 2 : 1, opacity: selected && !hot ? 0.08 : 0.8 } });
    }
    for (const c of classes) if (c.parent && present.has(c.parent)) {
      links.push({ source: c.name, target: c.parent, lineStyle: { color: "#8a8378", type: "dashed", width: 1, opacity: selected && selected !== c.name ? 0.08 : 0.5 } });
    }
    if (showSources) {
      for (const u of model.client.units.filter((u) => unit === "all" || u.code === unit)) {
        nodes.push({ id: `unit:${u.code}`, name: `${u.code} source`, symbol: "roundRect", symbolSize: 16,
          itemStyle: { color: "#f4f3ef", borderColor: "#8a8378", borderWidth: 1.5, opacity: selected && !near.has(`unit:${u.code}`) ? 0.2 : 1 },
          label: { show: true, position: "bottom", fontSize: 11, color: "#3d3c38" } });
      }
      const unitIds = new Set(nodes.filter((n) => String(n.id).startsWith("unit:")).map((n) => n.id));
      for (const c of classes) for (const s of c.sources) if (unitIds.has(`unit:${s.unit}`)) {
        const hot = selected === c.name;
        links.push({ source: `unit:${s.unit}`, target: c.name, lineStyle: { color: "#8a8378", type: "dotted", width: hot ? 1.6 : 0.6, opacity: hot ? 0.9 : selected ? 0.05 : 0.25 } });
      }
    }
    return {
      animation: false,
      tooltip: {
        confine: true, extraCssText: "max-width:280px;white-space:normal;",
        formatter: (p: any) => {
          if (p.dataType !== "node") return "";
          const c = byName.get(p.data.id);
          if (!c) return p.data.name;
          const d = c.description.length > 140 ? c.description.slice(0, 140) + "…" : c.description;
          return `<b>${c.label}</b><br/><span style="color:#6b6960;font-size:12px">${LAYER[layerKey(c.layer)].label} · ${c.metrics.length} metrics</span>${d ? `<br/><span style="font-size:12px">${d}</span>` : ""}`;
        },
      },
      series: [{
        type: "graph", layout: "force", roam: true, draggable: true, data: nodes, links,
        force: { repulsion: 320, edgeLength: [60, 150], gravity: 0.08, layoutAnimation: false }, zoom: 1.05,
        labelLayout: { hideOverlap: true }, edgeSymbol: ["none", "arrow"], edgeSymbolSize: 5,
        emphasis: { focus: "adjacency", label: { show: true, fontWeight: 600 }, lineStyle: { width: 2, color: "#1f4fbf" } },
        blur: { itemStyle: { opacity: 0.15 }, lineStyle: { opacity: 0.06 }, label: { color: "#d2cfc7" } },
        scaleLimit: { min: 0.4, max: 3 },
      }],
    };
  }, [model, unit, colorBy, showSources, showKernel, find, byName]);

  // Highlight the selected concept and its neighbours without re-running the layout.
  const chartRef = useRef<any>(null);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !option) return;
    chart.dispatchAction({ type: "downplay", seriesIndex: 0 });
    if (selected) {
      const i = (option.series[0].data as any[]).findIndex((n) => n.id === selected);
      if (i >= 0) chart.dispatchAction({ type: "highlight", seriesIndex: 0, dataIndex: i });
    }
  }, [selected, option]);

  if (error) return <div className="p-6"><ErrorNote error={error} /></div>;
  if (!model || !option) return <div className="p-6"><Loading what="Drawing the ontology" /></div>;
  const sel = selected ? byName.get(selected) : null;
  const pendingCount = Object.keys(model.pending).length;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-panel px-5 py-2.5">
        <b className="whitespace-nowrap">Ontology map</b>
        <div className="flex rounded-md border border-line p-0.5 text-[13px]">
          {(["graph", "layers"] as const).map((v) => (
            <button key={v} onClick={() => setView(v)} className={clsx("rounded px-3 py-1", view === v ? "bg-ink text-white" : "text-ink-2 hover:bg-sunk")}>{v === "graph" ? "Graph" : "How it's layered"}</button>
          ))}
        </div>
        {view === "graph" && <>
          <label className="flex items-center gap-1.5 text-[13px]"><span className="text-muted">Unit</span>
            <select value={unit} onChange={(e) => setUnit(e.target.value)} className="rounded-md border border-line bg-panel px-2 py-1">
              <option value="all">Whole group</option>{model.client.units.map((u) => <option key={u.code} value={u.code}>{u.code} · {u.name}</option>)}
            </select></label>
          <label className="flex items-center gap-1.5 text-[13px]"><span className="text-muted">Colour</span>
            <select value={colorBy} onChange={(e) => setColorBy(e.target.value as "layer" | "group")} className="rounded-md border border-line bg-panel px-2 py-1">
              <option value="layer">by layer</option><option value="group">by subject area</option>
            </select></label>
          <label className="flex items-center gap-1.5 text-[13px]"><input type="checkbox" checked={showSources} onChange={(e) => setShowSources(e.target.checked)} />Source systems</label>
          <label className="flex items-center gap-1.5 text-[13px]"><input type="checkbox" checked={showKernel} onChange={(e) => setShowKernel(e.target.checked)} />Kernel</label>
          <label className="ml-auto"><span className="sr-only">Find a concept</span>
            <input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a concept" className="w-48 rounded-md border border-line px-2.5 py-1 text-[13px] outline-none focus:border-accent-line" /></label>
        </>}
      </div>

      {view === "layers" ? <LayersView layers={layers} model={model} onPick={(c) => { setSelected(c); setView("graph"); }} /> : (
        <div className="flex min-h-0 flex-1">
          <div className="relative min-w-0 flex-1 bg-[#fbfaf7]">
            <div className="absolute inset-0">
              <ReactECharts onChartReady={(inst: any) => { chartRef.current = inst; }} option={option} style={{ height: "100%", width: "100%" }} notMerge
                onEvents={{ click: (p: any) => { if (p.dataType === "node" && !String(p.data.id).startsWith("unit:")) setSelected(p.data.id === selected ? null : p.data.id); } }} />
            </div>
            <div className="pointer-events-none absolute bottom-3 left-3 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md bg-panel/90 px-3 py-1.5 text-[11px] text-ink-2">
              {(["core", "line", "client"] as const).map((k) => <span key={k} className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: LAYER[k].color }} />{LAYER[k].label}</span>)}
              <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rotate-45 bg-[#2b2a27]" />Abstract</span>
              <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full border-[3px] border-warn-line" />Change pending</span>
              <span className="text-muted">Size = connections · drag to arrange · scroll to zoom</span>
            </div>
          </div>
          <aside className="flex w-[360px] shrink-0 flex-col border-l border-line bg-panel">
            {sel ? <Inspector c={sel} model={model} layers={layers} byName={byName} onClose={() => setSelected(null)} onPick={setSelected} /> : (
              <div className="space-y-4 overflow-y-auto p-5 text-[13px] text-ink-2">
                <div><div className="text-base font-semibold text-ink">{model.client.title}</div>
                  <div className="text-muted">{model.classes.length} concepts · {model.enums.length} value sets · {model.metrics.length} metrics</div></div>
                <p>Click a concept to see what it connects to, where its data comes from, and how each layer shaped it. <button onClick={() => setView("layers")} className="text-accent hover:text-accent-ink">How the layers work</button></p>
                {pendingCount > 0 && <div><div className="lbl mb-1.5">Changes waiting for approval</div>
                  {Object.entries(model.pending).map(([c, ps]) => <button key={c} onClick={() => setSelected(c)} className="block py-0.5 text-left text-accent hover:text-accent-ink">{byName.get(c)?.label ?? c} · {ps.length}</button>)}</div>}
                {!panelOpen && <button onClick={() => setPanelOpen(true)} className="text-accent hover:text-accent-ink">Ask the Curator about the map</button>}
              </div>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

function Inspector({ c, model, layers, byName, onClose, onPick }: {
  c: ClassInfo; model: OntologyModel; layers: Layers | null; byName: Map<string, ClassInfo>; onClose: () => void; onPick: (n: string) => void;
}) {
  const [tab, setTab] = useState<"built" | "connections">("built");
  const out = [...new Set(model.relationships.filter((r) => r.from === c.name).map((r) => r.to))];
  const inn = [...new Set(model.relationships.filter((r) => r.to === c.name).map((r) => r.from))];
  const pending = model.pending[c.name] ?? [];
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-start gap-2 border-b border-line-soft p-4">
        <div className="min-w-0 flex-1">
          <div className="text-lg font-semibold leading-tight">{c.label}</div>
          <div className="text-xs text-muted">{c.label !== c.name ? `Standard name ${c.name}` : LAYER[layerKey(c.layer)].label}{c.parent ? ` · a kind of ${byName.get(c.parent)?.label ?? c.parent}` : ""}</div>
        </div>
        <button onClick={onClose} aria-label="Close" className="rounded p-1 text-muted hover:bg-sunk"><X size={16} /></button>
      </div>
      <div className="flex gap-4 border-b border-line-soft px-4 text-[13px]">
        {(["built", "connections"] as const).map((t) => <button key={t} onClick={() => setTab(t)} className={clsx("-mb-px border-b-2 py-2", tab === t ? "border-ink font-semibold" : "border-transparent text-muted")}>{t === "built" ? "How it's built" : "Connections"}</button>)}
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-[13px]">
        {pending.length > 0 && (
          <div className="rounded-md border border-warn-line bg-warn-soft p-3">
            <div className="lbl mb-1 text-warn">{pending.length} waiting for approval</div>
            {pending.map((p) => <Link key={p.id} href={`/inbox?id=${p.id}`} className="block py-0.5 text-ink hover:text-accent">{p.status === "question" ? "Question: " : "Proposal: "}{p.title}</Link>)}
          </div>
        )}
        {tab === "built" ? <BuiltStack c={c} byName={byName} layers={layers} /> : (
          <>
            {c.description && <p className="leading-relaxed text-ink-2">{c.description}</p>}
            <div><div className="lbl mb-1">Points to</div><div className="flex flex-wrap gap-1.5">{out.length ? out.map((n) => <button key={n} onClick={() => onPick(n)}><Pill>{byName.get(n)?.label ?? n}</Pill></button>) : <span className="text-muted">—</span>}</div></div>
            <div><div className="lbl mb-1">Pointed to by</div><div className="flex flex-wrap gap-1.5">{inn.length ? inn.map((n) => <button key={n} onClick={() => onPick(n)}><Pill>{byName.get(n)?.label ?? n}</Pill></button>) : <span className="text-muted">—</span>}</div></div>
            <div><div className="lbl mb-1">Used by metrics</div><div className="flex flex-wrap gap-1.5">{c.metrics.length ? c.metrics.map((m) => <Link key={m} href={`/metrics/${m}`}><Pill tone="accent">{m.replace(/_/g, " ")}</Pill></Link>) : <span className="text-muted">None</span>}</div></div>
          </>
        )}
      </div>
      <div className="flex gap-2 border-t border-line-soft p-3">
        <Link href={`/concepts/${c.name}`} className="flex-1 rounded-md bg-accent px-3 py-2 text-center text-sm font-semibold text-white hover:bg-accent-ink">Open concept</Link>
        {c.sources[0] && <Link href={`/mappings/${c.sources[0].unit}/${c.name}`} className="flex-1 rounded-md border border-[#bdb9ae] px-3 py-2 text-center text-sm hover:bg-sunk">Source data</Link>}
      </div>
    </div>
  );
}

/** One concept, layer by layer: what the kernel says, what the pack adds, what this client and each unit change. */
function BuiltStack({ c, byName, layers }: { c: ClassInfo; byName: Map<string, ClassInfo>; layers: Layers | null }) {
  const kernelParents = [c.name, ...c.is_a].filter((n) => n !== "Entity" && byName.get(n)?.layer === "core");
  const coreFields = c.attributes.filter((a) => a.layer === "core");
  const packFields = c.attributes.filter((a) => a.layer === "line");
  const clientAdded = c.attributes.filter((a) => a.layer !== "core" && a.layer !== "line" && !a.layer.startsWith("unit"));
  const changed = c.attributes.filter((a) => a.change && (a.layer === "core" || a.layer === "line"));
  const relabel = c.customized.find((x) => x.startsWith("relabelled"));
  const clientEmpty = !relabel && !clientAdded.length && !changed.length;
  const steps = [
    {
      key: "core", title: "Kernel", empty: kernelParents.length === 0,
      body: kernelParents.length
        ? <>A kind of <b>{kernelParents.map((n) => byName.get(n)?.label ?? n).join(" → ")}</b>, shared by every line. Gives {coreFields.length} fields{coreFields.length ? `: ${coreFields.slice(0, 5).map((a) => a.label).join(", ")}${coreFields.length > 5 ? "…" : ""}` : ""}.</>
        : <>Nothing: this concept starts in a higher layer.</>,
    },
    {
      key: "line", title: layers?.pack.title.replace(/\s*\(L2\)/, "") ?? "Industry pack", empty: c.layer !== "line" && packFields.length === 0,
      body: c.layer === "line" || packFields.length
        ? <>{c.layer === "line" ? <>Defines <b>{c.name}</b>. </> : null}Adds {packFields.length} fields{packFields.length ? `: ${packFields.slice(0, 6).map((a) => a.label).join(", ")}${packFields.length > 6 ? "…" : ""}` : ""}.</>
        : <>No additions.</>,
    },
    {
      key: "client", title: layers?.client.title ?? "This client", empty: clientEmpty,
      body: clientEmpty ? <>Uses the pack as is.</> : (
        <ul className="list-disc space-y-0.5 pl-4">
          {relabel && <li>Calls it <b>{c.label}</b> (the standard name stays {c.name}, so exports and agents still match)</li>}
          {clientAdded.map((a) => <li key={a.name}>Adds <b>{a.label}</b>{a.description ? `: ${a.description}` : ""}</li>)}
          {changed.map((a) => <li key={a.name}><b>{a.label}</b>: {a.change}</li>)}
        </ul>
      ),
    },
    {
      key: "unit", title: "Units (source systems)", empty: c.sources.length === 0,
      body: c.sources.length === 0 ? <>Not mapped yet.</> : (
        <ul className="space-y-1.5">
          {c.sources.map((s) => {
            const unitAdds = c.attributes.filter((a) => a.layer === `unit:${s.unit}`);
            return (
              <li key={s.unit}>
                <Link href={`/mappings/${s.unit}/${c.name}`} className="font-semibold text-accent hover:text-accent-ink">{s.unit}</Link> <span className="text-muted">{s.source_system}</span>
                <div className="text-ink-2">from <span className="font-mono text-[11px]">{s.tables.join(", ") || "a query"}</span> · {s.slots_mapped}/{s.slots_total} fields{s.rows != null ? ` · ${s.rows.toLocaleString()} rows` : ""}</div>
                {unitAdds.length > 0 && <div className="text-ink-2">adds {unitAdds.map((a) => a.label).join(", ")}</div>}
              </li>
            );
          })}
        </ul>
      ),
    },
  ];
  return (
    <div>
      {steps.map((s, i) => (
        <div key={s.key}>
          <div className={clsx("rounded-md border p-3", s.empty ? "border-dashed border-line text-muted" : "border-line")}>
            <div className="mb-1 flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full" style={{ background: LAYER[s.key as keyof typeof LAYER].color }} /><b className="text-ink">{s.title}</b></div>
            <div className="leading-relaxed">{s.body}</div>
          </div>
          {i < steps.length - 1 && <div className="flex justify-center py-0.5 text-muted"><ArrowDown size={14} /></div>}
        </div>
      ))}
      <p className="mt-3 text-xs text-muted">Each layer only adds or narrows. Nothing below is renamed or redefined, so anything built on a lower layer keeps working.</p>
    </div>
  );
}

function Tier({ color, n, title, says, children }: { color: string; n: number; title: string; says: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-panel">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line-soft px-5 py-3">
        <span className="flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold text-white" style={{ background: color }}>{n}</span>
        <b>{title}</b><span className="text-[13px] text-muted">{says}</span>
      </div>
      <div className="p-5 text-[13px]">{children}</div>
    </section>
  );
}

/** The whole client, layer by layer. */
function LayersView({ layers, model, onPick }: { layers: Layers | null; model: OntologyModel; onPick: (c: string) => void }) {
  if (!layers) return <div className="p-6"><Loading /></div>;
  const label = (n: string) => model.classes.find((c) => c.name === n)?.label ?? n;
  const chips = (names: string[], max = 40) => (
    <div className="flex flex-wrap gap-1.5">{names.slice(0, max).map((n) => <button key={n} onClick={() => onPick(n)} className="rounded-full border border-line bg-panel px-2.5 py-0.5 text-xs hover:border-accent-line hover:text-accent-ink">{label(n)}</button>)}{names.length > max && <span className="text-xs text-muted">+{names.length - max}</span>}</div>
  );
  const cl = layers.client;
  const clientItems: [string, React.ReactNode[]][] = [
    ["Calls things by its own names", cl.labels.map((l) => <li key={l.name}>{l.name} → <b>{l.label}</b>{l.kind === "field" ? " (field)" : ""}</li>)],
    ["Adds fields", cl.added_fields.map((f) => <li key={f.field}><b>{f.field.replace(/_/g, " ")}</b> on {label(f.concept)}{f.description ? `: ${f.description}` : ""}</li>)],
    ["Adds value sets", cl.new_value_sets.map((v) => <li key={v.name}><b>{v.name}</b>: {v.values.join(", ")}</li>)],
    ["Adds values", cl.added_values.map((v) => <li key={v.value_set}>{v.value_set}: {v.values.join(", ")}</li>)],
    ["Makes required", cl.required.map((r) => <li key={r.concept + r.field}><b>{r.field.replace(/_/g, " ")}</b> on {label(r.concept)}{r.reason ? `: ${r.reason}` : ""}</li>)],
    ["Restricts", cl.restrictions.map((r) => <li key={r.concept + r.field}><b>{r.field.replace(/_/g, " ")}</b> on {label(r.concept)} to {r.allow.join(", ")}{r.reason ? `: ${r.reason}` : ""}</li>)],
    ["Hides from agents", cl.hidden.map((h) => <li key={h}>{h}</li>)],
    ["Shares across units", cl.shared_identity.map((s) => <li key={s}>{label(s)}: one record across units</li>)],
    ["Overrides metrics", cl.metric_overrides.map((m) => <li key={m.name}>{m.label ?? m.name}</li>)],
  ];
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-2 px-8 py-6">
        <p className="mb-4 max-w-3xl text-sm leading-relaxed text-ink-2">
          This client&apos;s ontology is four layers stacked on each other. Lower layers are shared; higher layers are this company&apos;s differences, kept as a short reviewable list.
          A layer can add or narrow, never rename or redefine what is below, so every report, export and agent built on a lower layer keeps working. Click any concept to see it on the graph.
        </p>
        <Tier color={LAYER.core.color} n={1} title={layers.kernel.title} says={layers.kernel.says}>{chips(layers.kernel.concepts.filter((n) => n !== "Entity"))}</Tier>
        <div className="flex justify-center text-muted"><ArrowDown size={16} /></div>
        <Tier color={LAYER.line.color} n={2} title={layers.pack.title.replace(/\s*\(L2\)/, "")} says={layers.pack.says}>
          <div className="mb-2 text-muted">{layers.pack.concepts.length} concepts · {layers.pack.value_sets} value sets · {layers.pack.metrics} governed metrics · version {layers.pack.version}</div>
          {chips(layers.pack.concepts)}
        </Tier>
        <div className="flex justify-center text-muted"><ArrowDown size={16} /></div>
        <Tier color={LAYER.client.color} n={3} title={`${cl.title} overlay`} says={cl.says}>
          <div className="grid gap-x-8 gap-y-4 md:grid-cols-2">
            {clientItems.filter(([, items]) => items.length).map(([t, items]) => (
              <div key={t}><div className="lbl mb-1">{t} · {items.length}</div><ul className="list-disc space-y-0.5 pl-4 text-ink-2">{items}</ul></div>
            ))}
          </div>
        </Tier>
        <div className="flex justify-center text-muted"><ArrowDown size={16} /></div>
        <Tier color={LAYER.unit.color} n={4} title="Units" says="Each operating unit maps its own source system, and may add a field or two of its own.">
          <div className="grid gap-4 md:grid-cols-3">
            {layers.units.map((u) => (
              <div key={u.code} className="rounded-md border border-line-soft p-3">
                <div className="font-semibold">{u.code} · {u.name}</div>
                <div className="mb-2 text-xs text-muted">{u.source_system}</div>
                <div className="lbl mb-1">Maps {u.mapped.length} concepts</div>
                {chips(u.mapped, 12)}
                {(u.added_fields.length > 0 || u.labels.length > 0) && <div className="mt-2 text-xs text-ink-2">
                  {u.labels.map((l) => <div key={l.name}>Calls {l.name} “{l.label}”</div>)}
                  {u.added_fields.map((f) => <div key={f.field}>Adds {f.field.replace(/_/g, " ")} on {label(f.concept)}</div>)}
                </div>}
              </div>
            ))}
          </div>
        </Tier>
      </div>
    </div>
  );
}
