"use client";

/* Drilling down, as a decomposition tree: a measure's value split by one column, a bar of it split by another,
   and so on, as deep as the model goes. Each level picks its own column; a click on a bar opens the next level
   under it, a click on the path goes back up, and the rows behind any bar are one click away. Every level is
   the measure itself, computed in SQL under the path so far; nothing is added up in the browser. */

import clsx from "clsx";
import { ChevronRight, Crosshair, Layers, Rows3, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { periodName } from "@/components/auto-chart";
import { ResultTable, num } from "@/components/pbi";
import { colLabel, isDimension, pbi, type Result, type RowsBehind } from "@/lib/pbi";
import { BUSINESS, PEOPLE } from "@/lib/vocab";

const PERIOD = /:(year|quarter|month|week|day)$/;
const said = (ref: string) => (PERIOD.test(ref) ? `${ref.match(PERIOD)![1][0].toUpperCase()}${ref.match(PERIOD)![1].slice(1)}` : colLabel(ref).replace(/ · .*$/, ""));
const shown = (ref: string, v: unknown) => (PERIOD.test(ref) ? periodName(v) : v === null || v === undefined || v === "" ? "(blank)" : String(v));
const TOP = 12;

type Level = { by: string; value?: unknown };
export type DrillStart = { by?: string; value?: unknown; rows?: boolean; at: number };

/** What a click on a mark can do: focus every view on it, drill into it, or open the rows behind it. */
export function PickMenu({ x, y, label, onFocus, onDrill, onRows, onClose }: {
  x: number; y: number; label: string; onFocus: () => void; onDrill: () => void; onRows: () => void; onClose: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const away = (e: Event) => { if (!box.current?.contains(e.target as Node)) onClose(); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    const t = setTimeout(() => document.addEventListener("pointerdown", away), 0);
    document.addEventListener("keydown", esc);
    return () => { clearTimeout(t); document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", esc); };
  }, [onClose]);
  const item = "flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[13.5px] text-[var(--d-ink)] hover:bg-[var(--d-paper)]";
  // kept inside the window: a click near the right or bottom edge opens the menu up and to the left
  const left = Math.min(x, (typeof window !== "undefined" ? window.innerWidth : 1200) - 280);
  const top = Math.min(y + 8, (typeof window !== "undefined" ? window.innerHeight : 800) - 170);
  return (
    <div ref={box} role="menu" style={{ left, top }} className="fixed z-50 w-[264px] rounded-xl bg-[var(--d-sheet)] p-1.5 shadow-[0_12px_40px_-12px_rgba(20,35,56,.35)] ring-1 ring-[var(--d-rule)]">
      <p className="truncate px-3 pb-1 pt-1.5 text-[12px] font-semibold text-[var(--d-muted)]">{label}</p>
      <button role="menuitem" onClick={onDrill} className={item}><Layers size={15} className="text-[var(--d-teal)]" />Drill into it</button>
      <button role="menuitem" onClick={onFocus} className={item}><Crosshair size={15} className="text-[var(--d-teal)]" />Focus every view on it</button>
      <button role="menuitem" onClick={onRows} className={item}><Rows3 size={15} className="text-[var(--d-teal)]" />See the rows behind it</button>
    </div>
  );
}

export function DrillTree({ model, metric, label, base, start, dates, types, onClose }: {
  model: string; metric: string; label: string; base: Record<string, unknown>; start: DrillStart;
  dates: string[]; types: Record<string, string>; onClose: () => void;
}) {
  const [columns, setColumns] = useState<string[]>([]);
  const [path, setPath] = useState<Level[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [levels, setLevels] = useState<Record<string, Result | { error: string }>>({});
  const [rows, setRows] = useState<{ at: string; r?: RowsBehind; error?: string } | null>(null);
  const strip = useRef<HTMLDivElement>(null);

  // the columns this measure can be split by: its dimensions, and its dates by year, quarter and month
  useEffect(() => {
    let alive = true;
    pbi.metric(model, metric).then((d) => {
      if (!alive) return;
      const cols = d.columns ?? [];
      const dims = cols.filter((c) => !dates.includes(c) && isDimension(c, types[c]));
      const periods = cols.filter((c) => dates.includes(c)).slice(0, 1).flatMap((c) => [`${c}:year`, `${c}:quarter`, `${c}:month`]);
      // the order the next level is suggested in: the business's usual cuts, then the rest, then dates, then a
      // person's traits; a raw number (Age) after its grouped form (Age Band)
      const field = (c: string) => c.replace(/^.*\[|\].*$/g, "");
      const words = (c: string) => (/int|double|decimal/i.test(types[c] ?? "") ? 1 : 0);
      const rank = (c: string) => (PERIOD.test(c) ? 2 : PEOPLE.test(field(c)) ? 3 : BUSINESS.test(field(c)) ? 0 : 1) * 2 + words(c);
      const all = [...dims, ...periods].map((c, i) => [c, i] as const).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(([c]) => c);
      setColumns(all);
      const first: Level[] = start.by ? [{ by: start.by, value: start.value }] : [];
      const next = all.find((c) => !first.some((l) => l.by === c) && !(c in base));
      setPath(start.by && start.value !== undefined && next && !start.rows ? [...first, { by: next }] : first.length ? first : next ? [{ by: next }] : []);
    }).catch(() => {});
    return () => { alive = false; };
  }, [model, metric, start, dates, types, base]);

  const within = (i: number) => ({ ...base, ...Object.fromEntries(path.slice(0, i).map((l) => [l.by, l.value])) });
  const idOf = (i: number) => JSON.stringify([path[i]?.by, within(i)]);

  useEffect(() => {
    pbi.compute(model, { metrics: [metric], filters: base }).then((r) => setTotal(Number(r.rows[0]?.[0] ?? NaN))).catch(() => setTotal(null));
  }, [model, metric, base]);
  // each level, computed once for the path that leads to it
  useEffect(() => {
    path.forEach((l, i) => {
      const id = idOf(i);
      if (levels[id]) return;
      pbi.compute(model, { metrics: [metric], by: [l.by], filters: within(i) })
        .then((r) => setLevels((s) => ({ ...s, [id]: r })))
        .catch((e) => setLevels((s) => ({ ...s, [id]: { error: String(e?.message ?? e) } })));
    });
    setTimeout(() => strip.current?.scrollTo({ left: strip.current.scrollWidth, behavior: "smooth" }), 80);
  }, [path]); // eslint-disable-line react-hooks/exhaustive-deps

  const showRows = (filters: Record<string, unknown>, at: string) => {
    setRows({ at });
    pbi.rows(model, { metric, filters, limit: 50 }).then((r) => setRows({ at, r })).catch((e) => setRows({ at, error: String(e?.message ?? e) }));
  };
  // the rows behind the bar it started on, when that is what was asked for
  useEffect(() => {
    if (start.rows && start.by) showRows({ ...base, [start.by]: start.value }, `${said(start.by)}: ${shown(start.by, start.value)}`); // eslint-disable-line react-hooks/set-state-in-effect -- opens on the rows asked for
  }, [start]); // eslint-disable-line react-hooks/exhaustive-deps

  const choose = (i: number, value: unknown) => {
    const same = path[i]?.value === value;
    const kept = path.slice(0, i + 1).map((l, j) => (j === i ? { by: l.by, value: same ? undefined : value } : l));
    if (same) { setPath(kept); return; }
    const used = new Set([...kept.map((l) => l.by), ...Object.keys(base)]);
    const next = path[i + 1] && !used.has(path[i + 1].by) ? path[i + 1].by : columns.find((c) => !used.has(c));
    setPath(next ? [...kept, { by: next }] : kept);
    setRows(null);
  };
  // back up to a level on the path: its bar stays picked, what was under it goes, its next level reopens
  const back = (i: number) => { setPath(path.slice(0, i + 2).map((l, j) => (j === i + 1 ? { by: l.by } : l))); setRows(null); };
  const regroup = (i: number, by: string) => { setPath([...path.slice(0, i), { by }]); setRows(null); };
  const leaf = path.filter((l) => l.value !== undefined);

  return (
    <section className="mt-4 rounded-2xl bg-[var(--d-sheet)] p-5 shadow-[0_1px_2px_rgba(20,35,56,.06)] ring-1 ring-[var(--d-rule)]">
      <header className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[13.5px]">
        <Layers size={15} className="mr-1 text-[var(--d-teal)]" />
        <button onClick={() => setPath(path.length ? [{ by: path[0].by }] : [])} className="cond text-[16px] font-semibold text-[var(--d-ink)] hover:underline">{label}</button>
        {leaf.map((l, i) => (
          <span key={i} className="flex items-center gap-1.5 text-[var(--d-ink-2)]">
            <ChevronRight size={14} className="text-[var(--d-muted)]" />
            <button onClick={() => back(path.indexOf(l))} className="hover:underline">{said(l.by)}: <b className="text-[var(--d-ink)]">{shown(l.by, l.value)}</b></button>
          </span>
        ))}
        <span className="ml-auto flex items-center gap-1">
          {leaf.length > 0 && (
            <button onClick={() => showRows(within(path.indexOf(leaf[leaf.length - 1]) + 1), leaf.map((l) => shown(l.by, l.value)).join(" · "))}
              className="flex items-center gap-1.5 rounded-full px-3 py-1 text-[13px] text-[var(--d-teal)] hover:bg-[var(--d-paper)]"><Rows3 size={14} />Rows behind this</button>
          )}
          <button onClick={onClose} aria-label="Close the drill-down" className="grid h-8 w-8 place-items-center rounded-lg text-[var(--d-muted)] hover:bg-[var(--d-paper)] hover:text-[var(--d-ink)]"><X size={15} /></button>
        </span>
      </header>

      <div ref={strip} className="mt-4 flex gap-3 overflow-x-auto pb-2">
        <div className="w-36 shrink-0 self-start rounded-xl bg-[var(--d-paper)] p-4">
          <p className="text-[12px] text-[var(--d-muted)]">{Object.keys(base).length ? "In this focus" : "In total"}</p>
          <p className="num mt-1 text-[24px] font-semibold leading-tight text-[var(--d-ink)]">{total === null ? "…" : num(total)}</p>
        </div>
        {path.map((l, i) => {
          const res = levels[idOf(i)];
          const used = new Set([...path.slice(0, i).map((x) => x.by), ...Object.keys(base)]);
          return (
            <div key={i} className="min-w-[220px] max-w-[320px] flex-1 self-start rounded-xl p-3 ring-1 ring-[var(--d-rule)]">
              <label className="flex items-center gap-2 text-[12px] text-[var(--d-muted)]">
                by
                <select value={l.by} onChange={(e) => regroup(i, e.target.value)} aria-label="Split by"
                  className="min-w-0 flex-1 truncate rounded-md bg-[var(--d-paper)] px-2 py-1 text-[13px] font-semibold text-[var(--d-ink)]">
                  {columns.filter((c) => c === l.by || !used.has(c)).map((c) => <option key={c} value={c}>{said(c)}</option>)}
                </select>
              </label>
              {!res ? <div className="mt-3 h-40 animate-pulse rounded-lg bg-[var(--d-paper)]" />
                : "error" in res ? <p className="mt-3 text-[12.5px] text-[var(--d-rise-ink)]">{res.error}</p>
                : <Bars res={res} by={l.by} picked={l.value} onPick={(v) => choose(i, v)} />}
            </div>
          );
        })}
      </div>

      {rows && (
        <div className="mt-4 border-t border-[var(--d-rule)] pt-4">
          <p className="text-[13px] text-[var(--d-ink-2)]">
            The rows behind {label}, for <b className="text-[var(--d-ink)]">{rows.at}</b>
            {rows.r && rows.r.row_count !== null ? `: ${num(rows.r.row_count)} rows${rows.r.totals[0] ? `, adding up to ${num(rows.r.totals[0].value)} ${rows.r.totals[0].label}` : ""}${rows.r.rows.length < (rows.r.row_count ?? 0) ? ` (first ${rows.r.rows.length} shown)` : ""}` : ""}
          </p>
          {rows.error ? <p className="mt-2 text-[12.5px] text-[var(--d-rise-ink)]">{rows.error}</p>
            : !rows.r ? <div className="mt-2 h-32 animate-pulse rounded-lg bg-[var(--d-paper)]" />
            : <div className="mt-2 max-h-80 overflow-auto"><ResultTable columns={rows.r.columns} rows={rows.r.rows} labels={{}} /></div>}
        </div>
      )}
    </section>
  );
}

/** One level: the measure for each value, largest first, as bars against the largest. */
function Bars({ res, by, picked, onPick }: { res: Result; by: string; picked: unknown; onPick: (v: unknown) => void }) {
  const [all, setAll] = useState(false);
  const at = res.columns.indexOf(by), m = res.columns.findIndex((c, j) => j !== at);
  const sorted = [...res.rows].filter((r) => r[m] !== null).sort((a, b) =>
    PERIOD.test(by) ? String(a[at]).localeCompare(String(b[at])) : Math.abs(Number(b[m])) - Math.abs(Number(a[m])));
  const list = all ? sorted : sorted.slice(0, TOP);
  const max = Math.max(...sorted.map((r) => Math.abs(Number(r[m]))), 1e-9);
  if (!sorted.length) return <p className="mt-3 text-[12.5px] text-[var(--d-muted)]">Nothing here.</p>;
  return (
    <ul className="mt-2 space-y-0.5">
      {list.map((r, j) => {
        const on = picked !== undefined && r[at] === picked;
        return (
          <li key={j}>
            <button onClick={() => onPick(r[at])} aria-pressed={on}
              className={clsx("group w-full rounded-lg px-2 py-1.5 text-left", on ? "bg-[var(--d-teal-soft)]" : "hover:bg-[var(--d-paper)]")}>
              <span className="flex items-baseline gap-2 text-[13px]">
                <span className={clsx("min-w-0 flex-1 truncate", on ? "font-semibold text-[var(--d-ink)]" : "text-[var(--d-ink-2)]")}>{shown(by, r[at])}</span>
                <span className="num shrink-0 font-semibold text-[var(--d-ink)]">{num(r[m])}</span>
              </span>
              <span className="mt-1 block h-1.5 rounded-full bg-[var(--d-paper)]">
                <span style={{ width: `${(Math.abs(Number(r[m])) / max) * 100}%` }}
                  className={clsx("block h-1.5 rounded-full", on ? "bg-[var(--d-teal)]" : "bg-[var(--d-teal)]/45 group-hover:bg-[var(--d-teal)]/70")} />
              </span>
            </button>
          </li>
        );
      })}
      {sorted.length > TOP && (
        <li><button onClick={() => setAll(!all)} className="px-2 py-1 text-[12px] text-[var(--d-teal)] hover:underline">{all ? "Show the top 12" : `All ${sorted.length}`}</button></li>
      )}
    </ul>
  );
}
