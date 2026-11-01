"use client";

/* An analysis on the Power BI desk: a question no single measure answers (is the jump real, what comes next,
   which ones stand out), worked out by code the assistant wrote and ran in a sealed sandbox on the granular
   rows the person may see. The finding is the headline; under it the numbers that carry it, the result as a
   chart, and how it was tested in plain words. The code, the rows it read and where it is kept in history
   are one click away, so anyone can check it or run it again. */

import { CircleAlert, Check, ChevronDown, FlaskConical, GitCommitHorizontal, ShieldCheck } from "lucide-react";
import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import { AutoChart, DESK_PALETTE, periodName } from "./auto-chart";
import { num } from "./pbi";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });
import type { ExhibitData } from "./pbi-exhibit";

export type Analysis = {
  question: string; answer: string; code: string; rows: number; columns: Record<string, string>;
  method: { test?: string; assumptions?: string[]; n?: number; basis?: string; caveats?: string[]; [k: string]: unknown };
  numbers: Record<string, unknown>; checks?: { check: string; passed: boolean }[]; where: string; isolated: boolean; seconds: number; id: string;
  commit: string | null; row_security: boolean;
};

const isNum = (v: unknown) => typeof v === "number" && Number.isFinite(v);

/** A number as a reader wants it: p-values bounded, rates as percentages, the rest grouped. */
function shown(name: string, v: unknown) {
  if (!isNum(v)) return String(v ?? "–");
  const x = v as number, n = name.toLowerCase();
  if (/^p$|p.?value/.test(n)) return x < 0.001 ? "< 0.001" : x.toFixed(3);
  if (/rate|share|pct|percent|miss/.test(n) && Math.abs(x) <= 1) return `${(100 * x).toFixed(1)}%`;
  return Math.abs(x) >= 1000 ? Math.round(x).toLocaleString() : String(Math.round(x * 1000) / 1000);
}

/** A forecast: the history as a solid line, the forecast dashed from the last actual, its range shaded. */
export function ForecastChart({ columns, rows, height = 260 }: { columns: string[]; rows: unknown[][]; height?: number }) {
  const at = (c: string) => columns.indexOf(c);
  const [P, A, F, L, H] = ["period", "actual", "forecast", "low", "high"].map(at);
  const cats = rows.map((r) => String(r[P]));
  const last = rows.reduce((k, r, i) => (r[A] !== null && r[A] !== undefined ? i : k), -1);
  const val = (r: unknown[], i: number) => (r[i] === null || r[i] === undefined ? null : Number(r[i]));
  // the forecast line starts at the last actual, so the two meet
  const fc = rows.map((r, i) => (i === last ? val(r, A) : val(r, F)));
  const lo = rows.map((r, i) => (i === last ? val(r, A) : val(r, L)));
  const band = rows.map((r, i) => (i === last ? 0 : val(r, H) !== null && val(r, L) !== null ? (val(r, H) as number) - (val(r, L) as number) : null));
  const option = {
    grid: { left: 64, right: 16, top: 28, bottom: 28 },
    legend: { data: ["Actual", "Forecast", "80% range"], top: 0, right: 0, itemWidth: 16, textStyle: { color: "#8b9099", fontSize: 11 } },
    tooltip: { trigger: "axis", confine: true, valueFormatter: (v: number) => (v === null || v === undefined ? "–" : num(v)) },
    xAxis: { type: "category", data: cats, axisLabel: { formatter: (v: string) => periodName(v, true), fontSize: 11, color: "#8b9099" } },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => num(v), fontSize: 11, color: "#8b9099" }, splitLine: { lineStyle: { color: "rgba(140,145,155,.18)" } } },
    series: [
      { name: "range-base", type: "line", data: lo, stack: "band", lineStyle: { opacity: 0 }, symbol: "none", tooltip: { show: false } },
      { name: "80% range", type: "line", data: band, stack: "band", lineStyle: { opacity: 0 }, symbol: "none", areaStyle: { color: DESK_PALETTE[2], opacity: 0.18 }, itemStyle: { color: "rgba(201,124,6,.35)" } },
      { name: "Actual", type: "line", data: rows.map((r) => val(r, A)), symbol: "none", lineStyle: { width: 2, color: DESK_PALETTE[0] }, itemStyle: { color: DESK_PALETTE[0] } },
      { name: "Forecast", type: "line", data: fc, symbol: "none", lineStyle: { width: 2, type: "dashed", color: DESK_PALETTE[2] }, itemStyle: { color: DESK_PALETTE[2] } },
    ],
  };
  return <ReactECharts option={option} style={{ width: "100%", height }} notMerge />;
}

/** "Read it as": what a p-value means, said once. */
function reading(numbers: Record<string, unknown>) {
  const p = Object.entries(numbers).find(([k]) => /^p$|p.?value/i.test(k))?.[1];
  if (!isNum(p)) return null;
  return (p as number) < 0.05
    ? "A gap this size would almost never come from chance alone."
    : "A gap this size could well be chance; treat it as noise until more data says otherwise.";
}

export function AnalysisExhibit({ e, n, landed }: { e: ExhibitData; n: number; landed?: boolean }) {
  const a = e.analysis as Analysis;
  const [open, setOpen] = useState<"code" | "data" | null>(null);
  const [first, ...rest] = a.answer.split(/(?<=[.!?])\s+/);
  const { dims, metrics } = useMemo(() => {
    const numeric = e.columns.filter((_, i) => e.rows.length > 0 && e.rows.every((r) => r[i] === null || isNum(r[i])));
    return { dims: e.columns.filter((c) => !numeric.includes(c)).slice(0, 1), metrics: numeric.slice(0, 4) };
  }, [e.columns, e.rows]);
  const numbers = Object.entries(a.numbers ?? {}).slice(0, 4);
  const m = a.method ?? {};
  const read = reading(a.numbers ?? {});
  const filters = Object.entries(e.filters ?? {});

  return (
    <section id={`exhibit-${n}`} className={`min-w-0 overflow-hidden rounded-xl border border-[var(--d-rule)] bg-[var(--d-sheet)] shadow-[0_1px_2px_rgba(20,35,56,.06)] ${landed ? "landed" : ""}`}>
      <div className="border-b border-[var(--d-rule)] px-6 pb-4 pt-4">
        <div className="flex flex-wrap items-center gap-2 text-[11px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">
          <FlaskConical size={14} className="text-[var(--d-teal)]" aria-hidden />{n ? `${n} · Analysis` : "Analysis"}
          <span className="ml-auto flex items-center gap-1.5 rounded-full bg-[var(--d-teal-soft)] px-2.5 py-0.5 text-[12px] normal-case tracking-normal text-[var(--d-teal)]">
            <ShieldCheck size={13} aria-hidden />{a.isolated ? "Sealed sandbox" : "Ran locally"} · {a.rows.toLocaleString()} rows
          </span>
        </div>
        <p className="mt-2 text-[13px] text-[var(--d-muted)]">{a.question}</p>
        <h2 className="cond mt-1 text-[24px] font-semibold leading-snug text-[var(--d-ink)] [text-wrap:balance]">{first}</h2>
        {rest.length > 0 && <p className="mt-1.5 max-w-[72ch] text-[14.5px] leading-relaxed text-[var(--d-ink-2)]">{rest.join(" ")}</p>}
      </div>

      {numbers.length > 0 && (
        <div className={`grid grid-cols-2 gap-px border-b border-[var(--d-rule)] bg-[var(--d-rule)] ${["sm:grid-cols-1", "sm:grid-cols-2", "sm:grid-cols-3", "sm:grid-cols-4"][numbers.length - 1]}`}>
          {numbers.map(([k, v]) => (
            <div key={k} className="min-w-0 bg-[var(--d-sheet)] px-6 py-4">
              <div className="truncate text-[12px] font-medium text-[var(--d-muted)]">{k[0].toUpperCase() + k.slice(1)}</div>
              <div className="num mt-1 text-[24px] font-semibold text-[var(--d-ink)]">{shown(k, v)}</div>
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-6 px-6 pb-5 pt-5 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <div className="min-w-0">
          {["period", "actual", "forecast", "low", "high"].every((c) => e.columns.includes(c)) ? (
            <ForecastChart columns={e.columns} rows={e.rows} />
          ) : metrics.length > 0 && dims.length > 0 && e.rows.length > 1 ? (
            // two or three periods side by side are a comparison, not a trend: bars, not a line
            <AutoChart columns={e.columns} rows={e.rows} dims={dims} metrics={metrics} palette={DESK_PALETTE} height={260} hideKinds
              kind={e.rows.length <= 3 ? "bar" : undefined} />
          ) : e.rows.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead><tr>{e.columns.map((c) => <th key={c} className="border-b border-[var(--d-rule)] py-1.5 pr-4 text-left font-medium text-[var(--d-muted)]">{c}</th>)}</tr></thead>
                <tbody>{e.rows.slice(0, 12).map((r, i) => (
                  <tr key={i}>{r.map((v, j) => <td key={j} className="num border-b border-[var(--d-rule)] py-1.5 pr-4 text-[var(--d-ink)]">{shown(e.columns[j], v)}</td>)}</tr>
                ))}</tbody>
              </table>
            </div>
          ) : <p className="text-[13px] text-[var(--d-muted)]">The analysis returned no table.</p>}
        </div>
        <div className="min-w-0 rounded-lg bg-[var(--d-paper)] px-5 py-4 text-[13.5px] text-[var(--d-ink-2)]">
          <div className="text-[12px] font-semibold uppercase tracking-[.06em] text-[var(--d-muted)]">How it was tested</div>
          {m.test && <p className="mt-2 text-[var(--d-ink)]">{String(m.test)[0].toUpperCase() + String(m.test).slice(1)}{m.basis ? `, on ${m.basis}` : m.n ? `, on ${Number(m.n).toLocaleString()} rows` : ""}.</p>}
          {read && <p className="mt-2">{read}</p>}
          {(m.assumptions ?? []).length > 0 && (
            <ul className="mt-2 space-y-1">{m.assumptions!.map((x) => <li key={x} className="flex gap-2"><Check size={14} className="mt-0.5 shrink-0 text-[var(--d-teal)]" />Assumes {x}</li>)}</ul>
          )}
          {(a.checks ?? []).length > 0 && (
            <ul className="mt-3 space-y-1 border-t border-[var(--d-rule)] pt-3">{a.checks!.map((c) => (
              <li key={c.check} className="flex gap-2">
                {c.passed ? <Check size={14} className="mt-0.5 shrink-0 text-[var(--d-teal)]" /> : <CircleAlert size={14} className="mt-0.5 shrink-0 text-[var(--d-rise-ink)]" />}
                {c.check[0].toUpperCase() + c.check.slice(1)}
              </li>))}
            </ul>
          )}
          {(m.caveats ?? []).length > 0 && <p className="mt-2 text-[var(--d-muted)]">Keep in mind: {m.caveats!.join("; ")}.</p>}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-[var(--d-rule)] px-6 py-3 text-[13px]">
        {(["code", "data"] as const).map((k) => (
          <button key={k} onClick={() => setOpen(open === k ? null : k)} aria-expanded={open === k}
            className={`flex items-center gap-1 rounded-md px-2.5 py-1 ${open === k ? "bg-[var(--d-paper)] text-[var(--d-ink)]" : "text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]"}`}>
            {k === "code" ? "See the code" : "What it read"}<ChevronDown size={14} className={open === k ? "rotate-180" : ""} />
          </button>
        ))}
        <span className="ml-auto flex items-center gap-1.5 text-[12.5px] text-[var(--d-muted)]">
          <GitCommitHorizontal size={14} aria-hidden />
          {a.commit ? <>Kept in history · <span className="font-mono">{a.commit}</span></> : "Saved (version history needs git)"}
        </span>
      </div>
      {open === "code" && (
        <pre className="max-h-[360px] overflow-auto border-t border-[var(--d-rule)] bg-[var(--d-paper)] px-6 py-4 font-mono text-[12px] leading-relaxed text-[var(--d-ink)]">{a.code.trim()}</pre>
      )}
      {open === "data" && (
        <div className="space-y-1.5 border-t border-[var(--d-rule)] bg-[var(--d-paper)] px-6 py-4 text-[13px] text-[var(--d-ink-2)]">
          <p>{a.rows.toLocaleString()} rows of {Object.values(a.columns).join(", ")}{filters.length ? `, for ${filters.map(([, v]) => String(v)).join(", ")}` : ""}.</p>
          <p>{a.row_security ? "Only rows you are allowed to see were read." : "This model has no row-level security; all its rows were readable."}</p>
          <p>It ran in {a.where} in {a.seconds}s. The rows were deleted afterwards; what is kept is the query that reads them again.</p>
        </div>
      )}
    </section>
  );
}
