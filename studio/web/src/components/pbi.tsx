"use client";

import { useMemo, useState } from "react";
import { colLabel, isDimension, splitRef } from "@/lib/pbi";
import { fmtValue } from "./ui";

export function num(v: unknown) {
  return typeof v === "number" ? fmtValue(v) : v === null || v === undefined ? "—" : String(v);
}

export function ResultTable({ columns, rows, numeric, labels }: { columns: string[]; rows: unknown[][]; numeric?: string[]; labels?: Record<string, string> }) {
  const isNum = (c: string, j: number) => numeric ? numeric.includes(c) : rows.some((r) => typeof r[j] === "number");
  return (
    <div className="max-h-[28rem] overflow-auto rounded-lg border border-line bg-panel">
      <table className="w-full text-[13px]">
        <thead className="sticky top-0 bg-sunk">
          <tr>{columns.map((c, j) => <th key={c} className={`whitespace-nowrap px-3 py-2 font-semibold ${isNum(c, j) ? "text-right" : "text-left"}`}>{labels?.[c] ?? (c.includes("[") ? colLabel(c) : c)}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-line-soft">
              {r.map((v, j) => <td key={j} className={`px-3 py-1.5 ${isNum(columns[j], j) ? "text-right tabular-nums" : ""}`}>{isNum(columns[j], j) ? num(v) : v === null || v === undefined ? "—" : String(v).replace(/^(\d{4}-\d{2}-\d{2})T00:00:00(\.0+)?$/, "$1")}</td>)}
            </tr>
          ))}
          {!rows.length && <tr><td colSpan={columns.length} className="px-3 py-6 text-center text-muted">No rows match.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

export function SqlBlock({ sql, warehouse, ms }: { sql?: string; warehouse?: string; ms?: number }) {
  if (!sql) return null;
  return (
    <details className="text-[13px]">
      <summary className="cursor-pointer text-muted">SQL that ran{warehouse ? ` on ${warehouse}` : ""}{ms !== undefined ? ` · ${ms} ms` : ""}</summary>
      <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-sunk p-3 font-mono text-[11px]">{sql}</pre>
    </details>
  );
}

/** A column picker grouped by table, with a filter box. Date columns get a grain. */
export function ColumnSelect({ columns, value, onChange, placeholder = "Pick a column", allowAll = false, dates = [], exclude = [], types = {} }: {
  columns: string[]; value: string; onChange: (v: string) => void; placeholder?: string; allowAll?: boolean;
  dates?: string[]; exclude?: string[]; types?: Record<string, string>;
}) {
  const [all, setAll] = useState(allowAll);
  const groups = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const c of columns) {
      if (exclude.includes(c) || (!all && !dates.includes(c) && !isDimension(c, types[c]))) continue;
      const t = splitRef(c).table;
      if (dates.includes(c)) for (const g of ["year", "quarter", "month", "week", "day"]) (out[t] ??= []).push(`${c}:${g}`);
      else (out[t] ??= []).push(c);
    }
    return out;
  }, [columns, all, dates, exclude, types]);
  return (
    <span className="inline-flex items-center gap-2">
      <select value={value} onChange={(e) => onChange(e.target.value)} className="min-h-9 max-w-xs rounded-md border border-line bg-panel px-2 text-sm">
        <option value="">{placeholder}</option>
        {Object.entries(groups).map(([t, cs]) => (
          <optgroup key={t} label={t}>{cs.map((c) => <option key={c} value={c}>{colLabel(c).replace(` · ${t}`, "")}</option>)}</optgroup>
        ))}
      </select>
      {!allowAll && <label className="flex items-center gap-1 text-xs text-muted"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />all columns</label>}
    </span>
  );
}

export function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "ok" | "warn" | "bad" }) {
  const color = tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : tone === "bad" ? "text-bad" : "text-ink";
  return (
    <div className="rounded-lg border border-line bg-panel px-4 py-3">
      <div className="lbl">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}
