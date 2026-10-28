"use client";

/* My dashboard: the questions a person pinned from the desk, each run again when the page opens, so the
   numbers are today's, never a copy. A pinned investigation is investigated again; a pinned answer is
   computed again. Pins are saved under the person's name, so the same board opens on any machine. */

import { CornerDownRight, Loader2, PinOff, RefreshCw, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { InvestigationExhibit } from "@/components/pbi-investigation";
import { PbiExhibit, type DeskActions, type ExhibitData } from "@/components/pbi-exhibit";
import { columnTypes, pbi, type Tile } from "@/lib/pbi";
import { drillExhibit, rowsExhibit, tileOf } from "@/lib/desk";
import { useStudio } from "@/lib/studio";
import { SettingsMenu, useLook } from "@/lib/theme";

type Live = { tile: Tile; e?: ExhibitData; error?: string };

export default function MyDashboard() {
  const { pbiModel } = useStudio();
  const look = useLook();
  const [owner, setOwner] = useState("me");
  const [live, setLive] = useState<Live[] | null>(null);
  const [cols, setCols] = useState<{ dates: string[]; types: Record<string, string> }>({ dates: [], types: {} });
  const [at, setAt] = useState<Date | null>(null);

  useEffect(() => { try { setOwner(localStorage.getItem("desk-name")?.trim() || "me"); } catch { /* the default */ } }, []); // eslint-disable-line react-hooks/set-state-in-effect
  const run = useCallback(async () => {
    if (!pbiModel) return;
    setLive(null);
    const [board, t] = await Promise.all([pbi.board(pbiModel, owner), pbi.tables(pbiModel).catch(() => null)]);
    if (t) setCols(columnTypes(t.tables));
    const out = await Promise.all(board.tiles.map(async (tile): Promise<Live> => {
      try {
        if (tile.kind === "explain" && tile.explain) {
          const r = await pbi.explain(pbiModel, tile.explain);
          return { tile, e: { kind: "explain", explain: r.explain, columns: [], rows: [], metrics: [tile.explain.metric], by: [], filters: tile.explain.filters ?? {}, labels: {}, origin: "agent" } };
        }
        const r = await pbi.compute(pbiModel, { metrics: tile.metrics ?? [], by: tile.by ?? [], filters: tile.filters ?? {} });
        return { tile, e: { kind: "compute", columns: r.columns, rows: r.rows, metrics: tile.metrics ?? [], by: tile.by ?? [], filters: tile.filters ?? {}, sql: r.sql, ms: r.ms, warehouse: r.warehouse, labels: tile.labels ?? {}, origin: "agent" } };
      } catch (err: any) { // eslint-disable-line @typescript-eslint/no-explicit-any
        return { tile, error: String(err?.message ?? err) };
      }
    }));
    setLive(out); setAt(new Date());
  }, [pbiModel, owner]);
  useEffect(() => { run(); }, [run]); // eslint-disable-line react-hooks/set-state-in-effect -- loads the board when the model or person changes

  const unpin = async (id: string) => {
    const next = (live ?? []).filter((x) => x.tile.id !== id);
    setLive(next);
    await pbi.saveBoard(pbiModel, owner, next.map((x) => x.tile)).catch(() => {});
  };
  // a click on a pinned exhibit drills in right under it: a trail per pin, deeper steps replacing later ones
  const [trail, setTrail] = useState<Record<string, ExhibitData[]>>({});
  const [working, setWorking] = useState<string | null>(null);
  const extend = async (id: string, depth: number, run: () => Promise<ExhibitData>) => {
    setWorking(id);
    try {
      const e = await run();
      setTrail((t) => ({ ...t, [id]: [...(t[id] ?? []).slice(0, depth), e] }));
      setTimeout(() => document.getElementById(`trail-${id}-${depth}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 60);
    } catch (err: any) { // eslint-disable-line @typescript-eslint/no-explicit-any
      setTrail((t) => ({ ...t, [id]: [...(t[id] ?? []).slice(0, depth)] }));
      alertLine(String(err?.message ?? err));
    } finally { setWorking(null); }
  };
  const [note, alertLine] = useState<string | null>(null);
  const pinned = new Set((live ?? []).map((x) => x.tile.id));
  const togglePin = async (e: ExhibitData) => {
    const t = tileOf(e);
    const tiles = (live ?? []).map((x) => x.tile);
    const next = pinned.has(t.id) ? tiles.filter((x) => x.id !== t.id) : [...tiles, t];
    await pbi.saveBoard(pbiModel, owner, next).catch(() => {});
    run();
  };
  const actionsFor = (id: string, depth: number): DeskActions => ({
    drill: (e, values, by) => extend(id, depth, () => drillExhibit(pbiModel, e, values, by)),
    rows: (e, values) => extend(id, depth, () => rowsExhibit(pbiModel, e, values)),
    ask: (text) => { window.location.href = `/pbi?q=${encodeURIComponent(text)}`; },
    pin: (e) => togglePin(e),
    pinned: (e) => pinned.has(tileOf(e).id),
  });

  return (
    <div className={`desk ${look.look} flex h-screen flex-col bg-[var(--d-paper)] text-[var(--d-ink)]`}>
      <header className="flex h-14 shrink-0 items-center gap-4 border-b border-[var(--d-rule)] bg-[var(--d-sheet)] px-6">
        <span className="cond text-[19px] font-semibold">My dashboard</span>
        <span className="text-[14px] text-[var(--d-muted)]">{owner === "me" ? "" : owner}</span>
        <div className="ml-auto flex items-center gap-3 text-[13px]">
          <button onClick={run} className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]">
            <RefreshCw size={14} />{at ? `Up to date · ${at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : "Refreshing…"}</button>
          <Link href="/pbi" className="rounded-md px-3 py-1.5 text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]">Analysis desk</Link>
          <Link href="/pbi/team" className="rounded-md px-3 py-1.5 text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]">Team space</Link>
          <SettingsMenu look={look} />
        </div>
      </header>
      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1180px] px-8 py-8">
          {note && <p className="mb-4 rounded-lg bg-[var(--d-rise-soft)] px-4 py-2 text-[13px] text-[var(--d-rise-ink)]">{note}</p>}
          {!live && <p className="flex items-center gap-2 text-[14px] text-[var(--d-muted)]"><Loader2 size={15} className="animate-spin" />Running your questions against today&rsquo;s numbers…</p>}
          {live && live.length === 0 && (
            <div className="mx-auto mt-16 max-w-md text-center">
              <p className="cond text-[24px] font-semibold">Nothing pinned yet</p>
              <p className="mt-2 text-[14px] text-[var(--d-muted)]">On the analysis desk, pin any answer or investigation. It shows up here and is worked out again every time you open this page.</p>
              <Link href="/pbi" className="mt-5 inline-block rounded-lg bg-[var(--d-navy)] px-4 py-2 text-[14px] font-medium text-white">Go to the desk</Link>
            </div>
          )}
          <div className="space-y-6">
            {live?.map((x, i) => (
              <div key={x.tile.id} className="group relative">
                <button onClick={() => unpin(x.tile.id)} title="Unpin" className="absolute -right-3 -top-3 z-10 hidden h-8 w-8 items-center justify-center rounded-full border border-[var(--d-rule)] bg-[var(--d-sheet)] text-[var(--d-muted)] shadow group-hover:flex hover:text-[var(--d-ink)]"><PinOff size={14} /></button>
                {x.error ? (
                  <div className="rounded-xl border border-[var(--d-rule)] bg-[var(--d-sheet)] px-6 py-4 text-[14px]"><b>{x.tile.title}</b><p className="mt-1 text-[var(--d-muted)]">Could not run it today: {x.error}</p></div>
                ) : x.e?.kind === "explain" ? <InvestigationExhibit e={x.e} n={i + 1} actions={actionsFor(x.tile.id, 0)} lean />
                  : x.e && <PbiExhibit e={x.e} n={i + 1} model={pbiModel} actions={actionsFor(x.tile.id, 0)} dates={cols.dates} types={cols.types} />}
                {(trail[x.tile.id]?.length ?? 0) > 0 && (
                  <div className="mt-3 space-y-3 border-l-2 border-[var(--d-rule)] pl-5">
                    <div className="flex items-center gap-2 text-[12px] text-[var(--d-muted)]"><CornerDownRight size={13} />Drilled in from here
                      <button onClick={() => setTrail((t) => ({ ...t, [x.tile.id]: [] }))} className="ml-auto flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-[var(--d-paper)]"><X size={12} />Close</button></div>
                    {trail[x.tile.id].map((d, j) => (
                      <div key={j} id={`trail-${x.tile.id}-${j}`}>
                        <PbiExhibit e={d} n={0} model={pbiModel} actions={actionsFor(x.tile.id, j + 1)} dates={cols.dates} types={cols.types} landed={j === trail[x.tile.id].length - 1} />
                      </div>
                    ))}
                  </div>
                )}
                {working === x.tile.id && <p className="mt-2 flex items-center gap-2 pl-5 text-[13px] text-[var(--d-muted)]"><Loader2 size={13} className="animate-spin" />Working it out…</p>}
              </div>
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}
