"use client";

/* Exhibits for the business view: one per analysis the agent runs. Every exhibit shows what it
   computed, from which governed definition, as of when, and lets the person drill in by clicking. */

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import { fmtValue } from "../ui";

/* eslint-disable @typescript-eslint/no-explicit-any */

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

const C = {
  navy: "#142338", ink2: "#3d4d63", muted: "#6a7a90", rule: "#d4dce6", teal: "#0f7b78",
  rise: "#c97c06", fall: "#3f6fa3", slate: "#9aabbf", paper: "#edf1f5",
};
const AXIS = { axisLine: { lineStyle: { color: C.rule } }, axisTick: { show: false }, axisLabel: { color: C.muted, fontSize: 12 } };

export type Ask = (message: string) => void;
type Pick = { x: number; y: number; dim?: string; value?: unknown; valueLabel: string; period?: string };

const fmtDate = (d?: string) => d ? new Date(d + (d.length === 10 ? "T00:00:00" : "")).toLocaleDateString("en-US", { day: "numeric", month: "short", year: "numeric" }) : "";
const pct = (v?: number | null) => v === null || v === undefined ? "" : `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)}%`;
const signed = (v: number, f?: string) => `${v >= 0 ? "+" : "−"}${fmtValue(Math.abs(v), f)}`;
const fmtN = (v: number, f?: string) => (f === "number" && Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : fmtValue(v, f));

function scopeText(card: any, without: string[] = []) {
  return (card.scope ?? []).filter((s: any) => !without.includes(s.dim)).map((s: any) => s.value_label).join(", ");
}

/* ------------------------------------------------------------------ drill menu */

function DrillMenu({ pick, card, onAsk, onClose, extra }: { pick: Pick; card: any; onAsk: Ask; onClose: () => void; extra?: { label: string; message: string }[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const off = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", off);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", off); document.removeEventListener("keydown", esc); };
  }, [onClose]);
  const metric = card.metrics?.[0];
  const keep = scopeText(card, pick.dim ? [pick.dim] : []);
  const within = pick.dim ? `within ${pick.valueLabel} (${pick.dim} = ${String(pick.value)})` : `for ${pick.valueLabel}`;
  const go = (m: string) => { onClose(); onAsk(m); };
  const dims = (card.drill ?? []).filter((d: any) => d.dim !== pick.dim);
  return (
    <div ref={ref} role="menu" style={{ left: pick.x, top: pick.y }}
      className="absolute z-30 w-72 overflow-hidden rounded-lg border border-[var(--d-rule)] bg-white text-[13px] shadow-[0_12px_32px_rgba(20,35,56,.18)]">
      <div className="border-b border-[var(--d-rule)] bg-[var(--d-paper)] px-3 py-2">
        <div className="font-semibold">{pick.valueLabel}</div>
        {keep && <div className="text-xs text-[var(--d-muted)]">{keep}</div>}
      </div>
      {extra?.map((x) => (
        <button key={x.label} role="menuitem" onClick={() => go(x.message)} className="block w-full px-3 py-2 text-left hover:bg-[var(--d-teal-soft)]">{x.label}</button>
      ))}
      {dims.length > 0 && <div className="px-3 pb-1 pt-2 text-xs text-[var(--d-muted)]">Break it down by</div>}
      {dims.map((d: any) => (
        <button key={d.dim} role="menuitem"
          onClick={() => go(`Drill: ${within}${pick.period ? `, in ${pick.period}` : ""}${keep ? `, keeping ${keep}` : ""}, show ${metric?.label} broken down by ${d.label.toLowerCase()} (${d.dim}).`)}
          className="block w-full px-3 py-1.5 pl-5 text-left hover:bg-[var(--d-teal-soft)]">{d.label}</button>
      ))}
    </div>
  );
}

function useDrill() {
  const [pick, setPick] = useState<Pick | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const at = (ev: any, p: Omit<Pick, "x" | "y">) => {
    const r = box.current?.getBoundingClientRect();
    const e = ev?.event?.event ?? ev;
    const x = Math.min((e?.clientX ?? 0) - (r?.left ?? 0), (r?.width ?? 600) - 296);
    setPick({ ...p, x: Math.max(8, x), y: (e?.clientY ?? 0) - (r?.top ?? 0) + 8 });
  };
  return { pick, setPick, box, at };
}

/* ------------------------------------------------------------------ frame */

function Scope({ card }: { card: any }) {
  if (!card.scope?.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {card.scope.map((s: any) => (
        <span key={s.dim} title={s.label} className="rounded-full border border-[var(--d-rule)] bg-[var(--d-paper)] px-2.5 py-0.5 text-xs text-[var(--d-ink-2)]">{s.value_label}</span>
      ))}
    </div>
  );
}

function TrustStrip({ card, onAsk }: { card: any; onAsk: Ask }) {
  const m = card.metrics?.[0];
  if (!m) return null;
  const pbi = card.powerbi?.[0];
  const item = (k: string, v: React.ReactNode) => (
    <div className="min-w-0"><div className="text-[11px] text-[var(--d-muted)]">{k}</div><div className="truncate text-[13px] text-[var(--d-ink)]">{v}</div></div>
  );
  return (
    <div className="flex flex-wrap items-end gap-x-8 gap-y-2 border-t border-[var(--d-rule)] bg-[#f7f9fb] px-6 py-3">
      <div className="flex items-center gap-2 self-center">
        <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden><path d="M9 1.5 15 4v4.6c0 3.6-2.5 6.5-6 7.9-3.5-1.4-6-4.3-6-7.9V4z" fill="var(--d-teal)" /><path d="m6 9 2 2 4-4" stroke="#fff" strokeWidth="1.6" fill="none" /></svg>
        <span className="text-[13px] font-semibold text-[var(--d-teal)]">Governed</span>
      </div>
      {item("Definition", <span title={m.description}>{m.label}{m.description ? `: ${m.description}` : ""}</span>)}
      {item("Data valued", fmtDate(card.as_of))}
      {item("Context version", <span className="font-mono text-xs">{card.version}</span>)}
      {pbi && item("Same as Power BI measure", `${pbi.measure}`)}
      <button onClick={() => onAsk(`How is ${m.label} defined, and why can I trust this number?`)}
        className="ml-auto rounded-md border border-[var(--d-teal)] px-3 py-1.5 text-[13px] font-semibold text-[var(--d-teal)] hover:bg-[var(--d-teal-soft)]">Why trust this number?</button>
    </div>
  );
}

function FollowUps({ items, onAsk }: { items: string[]; onAsk: Ask }) {
  if (!items.length) return null;
  return (
    <div className="flex flex-wrap gap-2 px-6 pb-4">
      {items.map((q) => (
        <button key={q} onClick={() => onAsk(q)} className="rounded-full border border-[var(--d-rule)] px-3 py-1 text-[13px] text-[var(--d-ink-2)] hover:border-[var(--d-teal)] hover:text-[var(--d-teal)]">{q}</button>
      ))}
    </div>
  );
}

export function Exhibit({ card, n, onAsk, landed }: { card: any; n: number; onAsk: Ask; landed?: boolean }) {
  const body = (() => {
    switch (card.kind) {
      case "trend": return <Trend card={card} onAsk={onAsk} />;
      case "change": return <Change card={card} onAsk={onAsk} />;
      case "breakdown": return <Breakdown card={card} onAsk={onAsk} />;
      case "outliers": return <Outliers card={card} onAsk={onAsk} />;
      case "evidence": return <Evidence card={card} />;
      case "gap": return <Gap card={card} />;
      default: return <pre className="p-6 text-xs">{JSON.stringify(card, null, 2).slice(0, 2000)}</pre>;
    }
  })();
  return (
    <article id={`exhibit-${n}`} className={`overflow-visible rounded-xl border border-[var(--d-rule)] bg-white ${landed ? "landed" : ""}`}>
      <header className="px-6 pt-5">
        <div className="text-[13px] font-semibold text-[var(--d-teal)]">Exhibit {n}</div>
        <h2 className="cond mt-0.5 text-[24px] font-semibold leading-tight text-[var(--d-ink)]">{card.title}</h2>
        <Scope card={card} />
      </header>
      {body}
      {card.kind !== "evidence" && card.kind !== "gap" && <TrustStrip card={card} onAsk={onAsk} />}
    </article>
  );
}

/* ------------------------------------------------------------------ trend */

function Trend({ card, onAsk }: { card: any; onAsk: Ask }) {
  const { pick, setPick, box, at } = useDrill();
  const m = card.metrics[0];
  const f = m.format;
  const periods: string[] = card.periods;
  const inc = new Set<string>(card.incomplete);
  const lastDone = periods.filter((p) => !inc.has(p)).at(-1);
  const multi = card.series.length > 1;
  const palette = [C.teal, C.rise, C.fall, "#7a5ea8", "#a8505e", C.slate];
  const series = card.series.flatMap((s: any, i: number) => {
    const color = multi ? palette[i % palette.length] : C.teal;
    const solid = s.values.map((v: number, j: number) => (inc.has(periods[j]) ? null : v));
    const dashed = s.values.map((v: number, j: number) => (inc.has(periods[j]) || periods[j] === lastDone ? v : null));
    return [
      { name: s.label, type: "line", data: solid, symbol: "circle", symbolSize: 9, lineStyle: { width: 3, color }, itemStyle: { color },
        areaStyle: multi ? undefined : { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "rgba(15,123,120,.16)" }, { offset: 1, color: "rgba(15,123,120,0)" }] } },
        label: { show: !multi, position: "top", color: C.navy, fontSize: 12, fontWeight: 600, formatter: (p: any) => fmtValue(p.value, f) } },
      { name: `${s.label} (incomplete)`, type: "line", data: dashed, symbol: "emptyCircle", symbolSize: 8, lineStyle: { width: 2, type: "dashed", color }, itemStyle: { color }, tooltip: { show: true } },
    ];
  });
  const option = {
    animation: false,
    grid: { left: 64, right: 24, top: 28, bottom: multi ? 56 : 32 },
    legend: multi ? { bottom: 0, data: card.series.map((s: any) => s.label), textStyle: { color: C.ink2 } } : undefined,
    tooltip: { trigger: "axis", valueFormatter: (v: number) => fmtValue(v, f) },
    xAxis: { type: "category", data: periods, ...AXIS },
    yAxis: { type: "value", ...AXIS, axisLine: { show: false }, splitLine: { lineStyle: { color: "#e8edf2" } }, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => fmtValue(v, f) } },
    series: [
      ...series,
      ...(inc.size ? [{ type: "line", data: [], markArea: { silent: true, itemStyle: { color: "rgba(154,171,191,.14)" },
        label: { color: C.muted, fontSize: 11, position: "insideTop", formatter: "Still receiving claims" },
        data: [[{ xAxis: periods.find((p) => inc.has(p)) }, { xAxis: periods.filter((p) => inc.has(p)).at(-1) }]] } }] : []),
    ],
  };
  const h = card.headline;
  const ch = h?.prior_value ? h.value / h.prior_value - 1 : null;
  const yoy = h?.yoy_value ? h.value / h.yoy_value - 1 : null;
  return (
    <div ref={box} className="relative grid gap-6 px-6 pb-5 pt-4 md:grid-cols-[220px_1fr]">
      {h ? (
        <div className="self-center">
          <div className="text-[13px] text-[var(--d-muted)]">{m.label}, {h.period}</div>
          <div className="num text-[64px] font-semibold leading-none text-[var(--d-navy)]">{fmtValue(h.value, f)}</div>
          {ch !== null && <div className="mt-3 text-[15px]"><span className="num font-semibold" style={{ color: ch >= 0 ? C.rise : C.fall }}>{pct(ch)}</span> <span className="text-[var(--d-muted)]">vs {h.prior_period}</span></div>}
          {yoy !== null && <div className="text-[15px]"><span className="num font-semibold" style={{ color: yoy >= 0 ? C.rise : C.fall }}>{pct(yoy)}</span> <span className="text-[var(--d-muted)]">vs {h.yoy_period}</span></div>}
          {card.incomplete?.length > 0 && <div className="mt-3 text-xs leading-snug text-[var(--d-muted)]">{card.incomplete.join(", ")} is still receiving claims, so it is not compared yet.</div>}
        </div>
      ) : <div />}
      <ReactECharts option={option} style={{ height: 300 }} notMerge
        onEvents={{ click: (e: any) => e.name && at(e, { valueLabel: e.name, period: e.name }) }} />
      {pick && <DrillMenu pick={pick} card={card} onAsk={onAsk} onClose={() => setPick(null)}
        extra={[
          ...(periods.indexOf(pick.period!) > 0 ? [{ label: `Explain the change into ${pick.period}`, message: `Why did ${m.label} change from ${periods[periods.indexOf(pick.period!) - 1]} to ${pick.period}${scopeText(card) ? ` (${scopeText(card)})` : ""}?` }] : []),
        ]} />}
      <div className="md:col-span-2"><FollowUpsInline card={card} onAsk={onAsk} /></div>
    </div>
  );
}

function FollowUpsInline({ card, onAsk }: { card: any; onAsk: Ask }) {
  const m = card.metrics?.[0]?.label;
  const h = card.headline;
  const items = card.kind === "trend" && h?.prior_period
    ? [`Why did ${m} change from ${h.prior_period} to ${h.period}?`, ...(card.drill?.[0] ? [`Show ${m} in ${h.period} by ${card.drill.find((d: any) => d.dim !== "state")?.label.toLowerCase() ?? card.drill[0].label.toLowerCase()}`] : [])]
    : [];
  return <div className="-mx-6"><FollowUps items={items} onAsk={onAsk} /></div>;
}

/* ------------------------------------------------------------------ change */

function Change({ card, onAsk }: { card: any; onAsk: Ask }) {
  const { pick, setPick, box, at } = useDrill();
  const m = card.metrics[0];
  const f = m.format;
  const d = card.drivers;
  const steps: any[] = card.steps ?? [];
  const cats = [card.from_period, ...steps.map((s) => s.label), card.to_period];
  let run = card.start;
  const base: (number | string)[] = ["-"];
  const up: (number | string)[] = ["-"];
  const down: (number | string)[] = ["-"];
  for (const s of steps) {
    const c = s.contribution;
    base.push(c >= 0 ? run : run + c);
    up.push(c >= 0 ? c : "-");
    down.push(c < 0 ? -c : "-");
    run += c;
  }
  base.push("-"); up.push("-"); down.push("-");
  const ends = cats.map((_, i) => (i === 0 ? card.start : i === cats.length - 1 ? card.end : "-"));
  const lows = [card.start, card.end, ...base.filter((x) => typeof x === "number") as number[]];
  const lo = Math.max(0, Math.floor((Math.min(...lows) * 0.82) / 50) * 50);
  const option = {
    animation: false,
    grid: { left: 64, right: 16, top: 30, bottom: 64 },
    tooltip: { trigger: "item", formatter: (p: any) => {
      const s = steps[p.dataIndex - 1];
      if (!s) return `${p.name}: <b>${fmtValue(p.value, f)}</b>`;
      return `<b>${s.label}</b><br/>Contribution: ${signed(s.contribution, f)}${card.part ? `<br/>${card.part.label}: ${fmtValue(s.from, card.part.format)} → ${fmtValue(s.to, card.part.format)}` : ""}`;
    } },
    xAxis: { type: "category", data: cats, ...AXIS, axisLabel: { ...AXIS.axisLabel, interval: 0, rotate: cats.some((c) => c.length > 12) ? 22 : 0 } },
    yAxis: { type: "value", min: lo, ...AXIS, axisLine: { show: false }, splitLine: { lineStyle: { color: "#e8edf2" } }, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => fmtValue(v, f) } },
    series: [
      { type: "bar", stack: "w", data: base, itemStyle: { color: "transparent" }, emphasis: { disabled: true }, tooltip: { show: false } },
      { type: "bar", stack: "w", data: up, barMaxWidth: 46, itemStyle: { color: C.rise, borderRadius: 2 }, label: { show: true, position: "top", color: C.rise, fontWeight: 600, formatter: (p: any) => signed(p.value, f) } },
      { type: "bar", stack: "w", data: down, barMaxWidth: 46, itemStyle: { color: C.fall, borderRadius: 2 }, label: { show: true, position: "bottom", color: C.fall, fontWeight: 600, formatter: (p: any) => signed(-p.value, f) } },
      { type: "bar", stack: "w", data: ends, barMaxWidth: 46, itemStyle: { color: C.navy, borderRadius: 2 }, label: { show: true, position: "top", color: C.navy, fontWeight: 700, formatter: (p: any) => fmtValue(p.value, f) } },
    ],
  };
  const total = card.end - card.start;
  const top = steps.filter((s) => !s.other).sort((a, b) => b.contribution - a.contribution)[0];
  return (
    <div ref={box} className="relative px-6 pb-2 pt-4">
      <div className="flex flex-wrap items-end gap-x-10 gap-y-4">
        <div>
          <div className="text-[13px] text-[var(--d-muted)]">{m.label}</div>
          <div className="num flex items-baseline gap-3 text-[44px] font-semibold leading-none">
            <span className="text-[var(--d-muted)]">{fmtValue(card.start, f)}</span>
            <svg width="28" height="16" viewBox="0 0 28 16" aria-hidden><path d="M2 8h22m-6-6 6 6-6 6" stroke={C.ink2} strokeWidth="2" fill="none" /></svg>
            <span>{fmtValue(card.end, f)}</span>
          </div>
          <div className="mt-2 text-[14px]"><span className="num font-semibold" style={{ color: total >= 0 ? C.rise : C.fall }}>{signed(total, f)} ({pct(card.pct)})</span> <span className="text-[var(--d-muted)]">from {card.from_period} to {card.to_period}</span></div>
        </div>
        {d && <Drivers d={d} total={total} f={f} />}
      </div>
      {steps.length > 0 && (
        <>
          <div className="mt-5 text-[13px] text-[var(--d-ink-2)]">What each {card.by_label?.toLowerCase()} added to the change{steps.some((s) => s.other) ? "" : ""}. Click a bar to drill in.</div>
          <ReactECharts option={option} style={{ height: 320 }} notMerge
            onEvents={{ click: (e: any) => { const s = steps[e.dataIndex - 1]; if (s && !s.other) at(e, { dim: card.by, value: s.key, valueLabel: s.label, period: card.to_period }); } }} />
        </>
      )}
      {pick && <DrillMenu pick={pick} card={card} onAsk={onAsk} onClose={() => setPick(null)}
        extra={[{ label: `Explain the change within ${pick.valueLabel}`, message: `Drill: within ${pick.valueLabel} (${pick.dim} = ${String(pick.value)})${scopeText(card) ? `, keeping ${scopeText(card)}` : ""}, explain why ${m.label} changed from ${card.from_period} to ${card.to_period}.` }]} />}
      <div className="-mx-6"><FollowUps onAsk={onAsk} items={[
        ...(top ? [`What drove ${top.label} in ${card.to_period}?`] : []),
        `Is this unusual compared with other states?`,
      ]} /></div>
    </div>
  );
}

function Drivers({ d, total, f }: { d: any; total: number; f: string }) {
  const parts = [{ k: "Utilization", x: d.utilization, color: C.slate }, { k: "Unit cost", x: d.unit_cost, color: C.rise }];
  const span = parts.reduce((a, p) => a + Math.abs(p.x.contribution), 0) || 1;
  return (
    <div className="min-w-[320px] flex-1">
      <div className="mb-2 text-[13px] text-[var(--d-ink-2)]">Volume or price? The change splits into</div>
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-[var(--d-paper)]">
        {parts.map((p) => <div key={p.k} style={{ width: `${(Math.abs(p.x.contribution) / span) * 100}%`, background: p.x.contribution >= 0 ? p.color : C.fall }} />)}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-4">
        {parts.map((p) => (
          <div key={p.k}>
            <div className="flex items-center gap-1.5 text-[13px] font-semibold"><span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: p.x.contribution >= 0 ? p.color : C.fall }} />{p.k}</div>
            <div className="text-xs text-[var(--d-muted)]">{p.x.label}</div>
            <div className="num mt-1 text-[17px]">{fmtN(p.x.from, p.x.format)} → {fmtN(p.x.to, p.x.format)} <span className="font-semibold" style={{ color: p.x.pct >= 0 ? C.rise : C.fall }}>{pct(p.x.pct)}</span></div>
            <div className="text-xs text-[var(--d-muted)]">{signed(p.x.contribution, f)} of the {signed(total, f)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ breakdown */

function Breakdown({ card, onAsk }: { card: any; onAsk: Ask }) {
  const { pick, setPick, box, at } = useDrill();
  const m = card.metrics[0];
  const f = m.format;
  const rows: any[] = [...card.rows].reverse();
  const option = {
    animation: false,
    grid: { left: 8, right: 72, top: 8, bottom: 8, containLabel: true },
    tooltip: { trigger: "item", valueFormatter: (v: number) => fmtValue(v, f) },
    xAxis: { type: "value", show: false },
    yAxis: { type: "category", data: rows.map((r) => r.label), ...AXIS, axisLine: { show: false }, axisLabel: { color: C.navy, fontSize: 13 } },
    series: [{ type: "bar", barMaxWidth: 26, data: rows.map((r, i) => ({ value: r.value, itemStyle: { color: r.key === null ? C.slate : i === rows.length - 1 ? C.teal : "#5f9f9c", borderRadius: [0, 3, 3, 0] } })),
      label: { show: true, position: "right", color: C.navy, fontWeight: 600, fontSize: 13, formatter: (p: any) => fmtValue(p.value, f) } }],
  };
  return (
    <div ref={box} className="relative px-6 pb-2 pt-4">
      <div className="flex flex-wrap items-baseline gap-x-3 text-[14px] text-[var(--d-ink-2)]">
        <span>{m.label} overall</span><span className="num text-[22px] font-semibold text-[var(--d-navy)]">{fmtValue(card.total, f)}</span>
        {card.share_of_total && <span className="text-xs text-[var(--d-muted)]">Each {card.by_label.toLowerCase()} is its paid amount over all member months, so they add up to the total.</span>}
      </div>
      <ReactECharts option={option} style={{ height: Math.max(180, rows.length * 38 + 20) }} notMerge
        onEvents={{ click: (e: any) => { const r = rows[e.dataIndex]; at(e, { dim: card.by, value: r.key, valueLabel: r.label }); } }} />
      {card.more > 0 && <div className="pb-2 text-xs text-[var(--d-muted)]">{card.more} smaller values not shown.</div>}
      {pick && <DrillMenu pick={pick} card={card} onAsk={onAsk} onClose={() => setPick(null)}
        extra={[{ label: `Trend for ${pick.valueLabel}`, message: `Drill: show the quarterly trend of ${m.label} within ${pick.valueLabel} (${pick.dim} = ${String(pick.value)})${scopeText(card, ["quarter"]) ? `, keeping ${scopeText(card, ["quarter"])}` : ""}.` }]} />}
    </div>
  );
}

/* ------------------------------------------------------------------ outliers */

function Outliers({ card, onAsk }: { card: any; onAsk: Ask }) {
  const m = card.metrics[0];
  const pts: any[] = card.points;
  const flagged = pts.filter((p) => p.flagged);
  const short = (s: string) => s.replace(/\s*\(PRV-[^)]*\)/, "");
  const cohort = (s: string) => String(s).replace(/^cohort_/, "").replace(/_([a-z]{2})$/, (_: string, st: string) => `, ${st.toUpperCase()}`).replace(/_/g, " ");
  const option = {
    animation: false,
    grid: { left: 56, right: 24, top: 24, bottom: 48 },
    tooltip: { trigger: "item", formatter: (p: any) => { const x = p.data.raw; return `<b>${short(x.entity)}</b><br/>${cohort(x.peers)}<br/>${fmtValue(x.value, m.format)} vs peers ${fmtValue(x.group, m.format)} (${x.ratio.toFixed(1)}x)${x.volume ? `<br/>${x.volume.toLocaleString()} claims` : ""}`; } },
    xAxis: { type: card.volume_metric ? "log" : "value", name: card.volume_label ? `${card.volume_label} (log scale)` : "", nameLocation: "middle", nameGap: 30, nameTextStyle: { color: C.muted }, ...AXIS, splitLine: { show: false } },
    yAxis: { type: "value", name: "Times the peer rate", nameTextStyle: { color: C.muted, align: "left" }, ...AXIS, axisLine: { show: false }, splitLine: { lineStyle: { color: "#e8edf2" } }, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => `${v}x` } },
    series: [
      { type: "scatter", symbolSize: 7, itemStyle: { color: C.slate, opacity: 0.55 }, data: pts.filter((p) => !p.flagged).map((p) => ({ value: [p.volume ?? 1, p.ratio], raw: p })),
        markLine: { silent: true, symbol: "none", lineStyle: { color: C.rise, type: "dashed" }, label: { formatter: `${card.threshold}x peers`, color: C.rise }, data: [{ yAxis: card.threshold }] } },
      { type: "scatter", symbolSize: 14, itemStyle: { color: C.rise, borderColor: "#fff", borderWidth: 1.5 }, data: flagged.map((p) => ({ value: [p.volume ?? 1, p.ratio], raw: p })),
        label: { show: true, position: "right", color: C.navy, fontSize: 12, fontWeight: 600, formatter: (p: any) => short(p.data.raw.entity) } },
    ],
  };
  return (
    <div className="grid gap-4 px-6 pb-4 pt-4 lg:grid-cols-[1fr_300px]">
      <div>
        <div className="text-[13px] text-[var(--d-ink-2)]">Each dot is one {card.entity_label.toLowerCase()}, against its {card.peers_label.toLowerCase()}{card.periods?.length ? ` in ${card.periods.join(" and ")}` : ""}{card.min_z ? "; flagged only when the excess is statistically significant" : ""}.</div>
        {pts.length ? <ReactECharts option={option} style={{ height: 340 }} notMerge />
          : <div className="mt-4 rounded-lg bg-[var(--d-paper)] p-6 text-[14px] text-[var(--d-ink-2)]">No {card.entity_label.toLowerCase()} had enough volume in every period to compare. Try fewer filters or other quarters.</div>}
      </div>
      <div>
        <div className="num text-[44px] font-semibold leading-none">{flagged.length}<span className="ml-2 text-[16px] font-normal text-[var(--d-muted)]">flagged of {pts.length}</span></div>
        <ul className="mt-3 divide-y divide-[var(--d-rule)] border-y border-[var(--d-rule)]">
          {flagged.slice(0, 8).map((p) => (
            <li key={p.entity}>
              <button onClick={() => onAsk(`Drill: show the quarterly trend of ${m.label} for ${short(p.entity)} (provider = ${p.entity}).`)}
                className="flex w-full items-baseline justify-between gap-2 py-2 text-left hover:text-[var(--d-teal)]">
                <span className="min-w-0"><span className="block truncate text-[14px] font-semibold">{short(p.entity)}</span><span className="block truncate text-xs text-[var(--d-muted)]">{cohort(p.peers)}</span></span>
                <span className="num shrink-0 text-[18px] font-semibold text-[var(--d-rise)]">{p.ratio.toFixed(1)}x</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ evidence */

function readable(filter?: string) {
  if (!filter) return "";
  return filter
    .replace(/\{([a-z_.]+)\}/g, (_, x) => x.split(".").pop().replace(/_/g, " "))
    .replace(/\s+NOT IN\s+\(([^)]*)\)/gi, (_, v) => ` is not one of ${v.replace(/'/g, "")}`)
    .replace(/\s+IN\s+\(([^)]*)\)/gi, (_, v) => ` is one of ${v.replace(/'/g, "")}`)
    .replace(/\s*<>\s*'([^']*)'/g, " is not $1").replace(/\s*=\s*'([^']*)'/g, " is $1").replace(/\s+AND\s+/gi, ", and ");
}

function Evidence({ card }: { card: any }) {
  const d = card.definition ?? {};
  return (
    <div className="grid gap-x-8 gap-y-6 px-6 pb-6 pt-4 lg:grid-cols-3">
      <section>
        <h3 className="cond text-[17px] font-semibold">What it counts</h3>
        <div className="num mt-2 text-[22px] font-semibold leading-snug">{d.label} = {(d.children ?? []).map((c: any) => c.label).join(d.kind === "ratio" ? " ÷ " : " + ")}</div>
        <p className="mt-1 text-[13px] text-[var(--d-ink-2)]">{d.description}</p>
        <ul className="mt-3 space-y-3">
          {(d.children ?? []).map((c: any) => (
            <li key={c.name} className="border-l-2 border-[var(--d-teal)] pl-3">
              <div className="text-[14px] font-semibold">{c.label}</div>
              <div className="text-[13px] text-[var(--d-ink-2)]">{c.description}</div>
              {c.filter && <div className="mt-1 text-xs text-[var(--d-muted)]">Counts claims where {readable(c.filter)}.</div>}
            </li>
          ))}
        </ul>
        <div className="mt-4 text-[13px]"><span className="text-[var(--d-muted)]">Owned by </span>{card.owners?.map((o: any) => o.people.join(", ")).filter(Boolean).join("; ")}</div>
        {card.powerbi?.length > 0 && <div className="mt-1 text-[13px]"><span className="text-[var(--d-muted)]">Same as Power BI measure </span>{card.powerbi.map((p: any) => `${p.measure} (${p.model})`).join(", ")}</div>}
      </section>
      <section>
        <h3 className="cond text-[17px] font-semibold">Where the data comes from</h3>
        <ol className="mt-3">
          {(card.lineage ?? []).slice(0, 3).map((l: any, i: number) => (
            <li key={i} className="mb-4">
              {[
                { k: "Source system", v: l.system?.split(" (")[0] },
                { k: "Source tables", v: (l.tables ?? []).join(", ") },
                { k: "Governed view", v: `${l.view}${l.rows ? `, ${Number(l.rows).toLocaleString()} rows` : ""}` },
                { k: "Agents see", v: l.agent_view + (l.redacted?.length ? `, with ${l.redacted.join(", ").replace(/_/g, " ")} redacted` : "") },
              ].map((s, j, all) => (
                <div key={s.k} className="relative pb-3 pl-6">
                  <span className="absolute left-0 top-1 h-3 w-3 rounded-full border-2 border-[var(--d-teal)] bg-white" />
                  {j < all.length - 1 && <span className="absolute left-[5px] top-4 h-[calc(100%-8px)] w-0.5 bg-[var(--d-teal-soft)]" />}
                  <div className="text-[11px] text-[var(--d-muted)]">{s.k}</div>
                  <div className="break-words text-[13px]">{s.v}</div>
                </div>
              ))}
            </li>
          ))}
        </ol>
      </section>
      <section>
        <h3 className="cond text-[17px] font-semibold">Changes that moved this number</h3>
        {(card.changes ?? []).length === 0 && <p className="mt-2 text-[13px] text-[var(--d-muted)]">No approved change has moved it since it was defined.</p>}
        <ul className="mt-2 space-y-4">
          {(card.changes ?? []).map((c: any) => (
            <li key={c.id} className="rounded-lg border border-[var(--d-rule)] p-3">
              <div className="text-xs text-[var(--d-muted)]">{fmtDate(c.at?.slice(0, 10))}{c.approved_by?.length ? `, approved by ${c.approved_by.join(", ")}` : ""}</div>
              <div className="mt-0.5 text-[14px] font-semibold leading-snug">{c.title}</div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {c.impact.map((i: any, k: number) => <span key={k} className="num rounded bg-[var(--d-paper)] px-2 py-0.5 text-[13px]">{i.group}: {i.before} → <b>{i.after}</b></span>)}
              </div>
              {c.source?.quote && <blockquote className="mt-2 border-l-2 border-[var(--d-rule)] pl-2 text-[13px] italic text-[var(--d-ink-2)]">“{c.source.quote}”<div className="not-italic text-xs text-[var(--d-muted)]">{c.source.title}{c.source.page ? `, page ${c.source.page}` : ""}</div></blockquote>}
            </li>
          ))}
        </ul>
        {(card.pending ?? []).length > 0 && (
          <div className="mt-4 rounded-lg bg-[#fdf6e7] p-3 text-[13px]"><b>Under review:</b> {card.pending.map((p: any) => p.title).join("; ")}</div>
        )}
      </section>
    </div>
  );
}

/* ------------------------------------------------------------------ gap */

function Gap({ card }: { card: any }) {
  return (
    <div className="px-6 pb-6 pt-3">
      <div className="rounded-lg border border-dashed border-[var(--d-rise)] bg-[#fdf8ee] p-4">
        <div className="text-[15px] font-semibold">No governed definition answers this yet, so no number was given.</div>
        <p className="mt-1 text-[13px] text-[var(--d-ink-2)]">{card.detail}</p>
        <div className="mt-3 text-[13px]">Sent to <b>{card.owners?.join(", ") || "the owners"}</b>{card.areas?.length ? ` (${card.areas.join(", ")})` : ""} as a question. Once they approve a definition, every agent and report can answer it.</div>
      </div>
    </div>
  );
}
