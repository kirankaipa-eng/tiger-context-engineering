"use client";

/* "Add to dashboard" on a chart (or on an answer, for all its charts): the person's dashboards to choose from, and
   a new one named in place. Once added, it says where and offers to open it. */

import { Check, LayoutDashboard, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { owner } from "@/lib/desk";
import { addTo, chartsOf, dashboardsOf, load, type Chart, type Dash } from "@/lib/dashboards";

export function AddToDashboard({ model, charts, label = "Add to dashboard", compact = false }: {
  model: string; charts: () => Chart[]; label?: string; compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [dashes, setDashes] = useState<{ d: Dash; n: number }[] | null>(null);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [done, setDone] = useState<{ id: string; title: string } | null>(null);
  const [error, setError] = useState("");
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    load(model, owner()).then((tiles) => setDashes(dashboardsOf(tiles).map((d) => ({ d, n: chartsOf(tiles, d.id).length }))))
      .catch((e) => setError(String(e?.message ?? e)));
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open, model]);

  const add = async (to: string | { create: string }) => {
    setError("");
    try {
      const r = await addTo(model, owner(), charts(), to);
      setDone({ id: r.id, title: r.title }); setOpen(false); setNaming(false); setName("");
      setTimeout(() => setDone(null), 6000);
    } catch (e: any) { setError(String(e?.message ?? e)); }  // eslint-disable-line @typescript-eslint/no-explicit-any
  };

  return (
    <div ref={box} className="relative">
      {done ? (
        <span className="flex items-center gap-1.5 rounded-full bg-[var(--d-teal-soft)] px-3 py-1.5 text-[12.5px] text-[var(--d-ink)]">
          <Check size={13} className="text-[var(--d-teal)]" />Added to {done.title}
          <Link href={`/pbi/board?d=${encodeURIComponent(done.id)}`} className="font-semibold text-[var(--d-teal)] hover:underline">Open</Link>
        </span>
      ) : (
        <button onClick={() => { setOpen((o) => !o); setDashes(null); }} aria-expanded={open} aria-haspopup="menu" title={label}
          className={compact ? "grid h-7 w-7 place-items-center rounded-lg text-[var(--d-ink-2)] ring-1 ring-[var(--d-rule)] hover:text-[var(--d-teal)] hover:ring-[var(--d-teal)]"
            : "flex items-center gap-1.5 rounded-full bg-[var(--d-teal)] px-3.5 py-1.5 text-[13px] font-semibold text-white"}>
          <LayoutDashboard size={compact ? 14 : 14} />{compact ? <span className="sr-only">{label}</span> : label}
        </button>
      )}
      {open && (
        <div role="menu" className="absolute right-0 top-full z-30 mt-2 w-72 rounded-2xl bg-[var(--d-sheet)] p-1.5 text-left shadow-[0_24px_60px_-24px_rgba(20,35,56,.45)] ring-1 ring-[var(--d-rule)]">
          <p className="px-3 pb-1 pt-2 text-[11.5px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">Add to</p>
          {!dashes && !error && <p className="px-3 py-2 text-[13px] text-[var(--d-muted)]">Loading your dashboards…</p>}
          {dashes?.map(({ d, n }) => (
            <button key={d.id} role="menuitem" onClick={() => add(d.id)}
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-[14px] text-[var(--d-ink)] hover:bg-[var(--d-paper)]">
              <LayoutDashboard size={14} className="text-[var(--d-muted)]" /><span className="min-w-0 flex-1 truncate">{d.title}</span>
              <span className="text-[12px] text-[var(--d-muted)]">{n} {n === 1 ? "chart" : "charts"}</span>
            </button>
          ))}
          <div className="my-1 border-t border-[var(--d-rule)]" />
          {naming ? (
            <form onSubmit={(e) => { e.preventDefault(); if (name.trim()) add({ create: name }); }} className="flex items-center gap-2 px-2 py-1.5">
              <label className="min-w-0 flex-1"><span className="sr-only">Name the new dashboard</span>
                <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Name it, e.g. Q3 cost review"
                  className="w-full rounded-lg bg-[var(--d-paper)] px-2.5 py-1.5 text-[13.5px] text-[var(--d-ink)] outline-none ring-1 ring-[var(--d-rule)] focus:ring-[var(--d-teal)]" /></label>
              <button type="submit" disabled={!name.trim()} className="rounded-lg bg-[var(--d-teal)] px-2.5 py-1.5 text-[13px] font-semibold text-white disabled:opacity-40">Add</button>
            </form>
          ) : (
            <button role="menuitem" onClick={() => setNaming(true)} className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-[14px] font-medium text-[var(--d-teal)] hover:bg-[var(--d-paper)]">
              <Plus size={14} />New dashboard
            </button>
          )}
          {error && <p className="px-3 py-2 text-[12.5px] text-[var(--d-rise-ink)]">{error}</p>}
        </div>
      )}
    </div>
  );
}
