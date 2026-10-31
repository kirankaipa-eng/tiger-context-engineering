"use client";

/* Scheduled briefs: a person writes what they want a brief on and when (a cron expression, or one of the usual
   times), and the analyst writes it at each time, as the Ask page would answer it. Each brief shows its latest
   edition, its answer and the numbers behind it, with the earlier editions a click away. */

import clsx from "clsx";
import { CalendarClock, Loader2, Pause, Pencil, Play, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { InvestigationExhibit } from "@/components/pbi-investigation";
import { PbiExhibit, type DeskActions, type ExhibitData } from "@/components/pbi-exhibit";
import { pbi } from "@/lib/pbi";
import { inWords, PRESETS, schedules, type Edition, type Schedule } from "@/lib/schedules";

const TZ = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { return "UTC"; } })();
const when = (t: string | number) => new Date(typeof t === "number" ? t * 1000 : t)
  .toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

type Draft = { id?: string; ask: string; title: string; cron: string };

export function ScheduledBriefs({ model, owner, actions, below, dates, types }: {
  model: string; owner: string; actions: (key: string) => DeskActions; dates: string[]; types: Record<string, string>;
  /** What was drilled into from an exhibit, shown under it (the page keeps the trail). */
  below: (key: string) => React.ReactNode;
}) {
  const [list, setList] = useState<Schedule[] | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const named = owner && owner !== "me";
  // the example is the model's own: one of its measures, a rate or per-member one when it has one
  const [example, setExample] = useState<{ ask: string; title: string }>({ ask: "What changed in our main numbers this month, and what is behind it?", title: "Monthly numbers" });
  useEffect(() => {
    if (!model) return;
    pbi.metrics(model).then(({ metrics }) => {
      const ok = metrics.filter((m) => m.status === "captured" && !m.needs?.by?.length && !Object.keys(m.needs?.params ?? {}).length);
      const m = ok.find((x) => /per |ratio|rate|pmpm|margin|share/i.test(x.label)) ?? ok[0];
      if (m) setExample({ ask: `${m.label}: what changed this month, where, and is it out of the ordinary?`, title: `${m.label} watch` });
    }).catch(() => {});
  }, [model]);

  const load = useCallback(async () => {
    if (!model || !named) return;
    try { setList((await schedules.list(model, owner)).schedules); } catch (e) { setError(String((e as Error).message)); }
  }, [model, owner, named]);
  useEffect(() => { load(); }, [load]); // eslint-disable-line react-hooks/set-state-in-effect -- loads when the model or person changes
  // while an edition is being written, look again every few seconds until it lands
  const running = list?.some((s) => s.running);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [running, load]);

  const act = async (f: () => Promise<unknown>) => {
    setError(null);
    try { await f(); await load(); } catch (e) { setError(String((e as Error).message)); }
  };
  const save = (d: Draft, now: boolean) => act(async () => {
    const s = await schedules.save(model, owner, { ...d, tz: TZ });
    setDraft(null);
    if (now) await schedules.run(model, owner, s.id);
  });

  return (
    <section className="mb-12">
      <div className="flex items-end gap-4">
        <div>
          <h2 className="cond text-[22px] font-semibold">Scheduled briefs</h2>
          <p className="mt-0.5 text-[14px] text-[var(--d-muted)]">Say what you want a brief on and when. The analyst writes it at each time, from that day&rsquo;s numbers.</p>
        </div>
        {named && !draft && (
          <button onClick={() => setDraft({ ask: "", title: "", cron: PRESETS[1].cron })}
            className="ml-auto flex shrink-0 items-center gap-1.5 rounded-lg bg-[var(--d-navy)] px-3.5 py-2 text-[14px] font-medium text-white"><Plus size={15} />New brief</button>
        )}
      </div>
      {!named && <p className="mt-4 text-[14px] text-[var(--d-muted)]">A scheduled brief is written for someone: <Link href="/pbi/team" className="font-medium text-[var(--d-teal)]">say who you are on the Team page</Link> first.</p>}
      {error && <p className="mt-4 rounded-lg bg-[var(--d-rise-soft)] px-4 py-2 text-[13px] text-[var(--d-rise-ink)]">{error}</p>}
      {draft && <Editor model={model} example={example} draft={draft} onCancel={() => setDraft(null)} onSave={save} />}
      <div className="mt-5 space-y-5">
        {list?.map((s) => (
          <BriefCard key={s.id} s={s} model={model} actions={actions} below={below} dates={dates} types={types}
            onRun={() => act(() => schedules.run(model, owner, s.id))}
            onPause={() => act(() => schedules.save(model, owner, { ...s, paused: !s.paused }))}
            onEdit={() => setDraft({ id: s.id, ask: s.ask, title: s.title, cron: s.cron })}
            onDelete={() => act(() => schedules.remove(model, owner, s.id))} />
        ))}
        {named && list?.length === 0 && !draft && (
          <button onClick={() => setDraft({ ask: "", title: "", cron: PRESETS[1].cron })}
            className="flex w-full items-center gap-3 rounded-xl border border-dashed border-[var(--d-rule)] px-5 py-4 text-left text-[14px] text-[var(--d-muted)] hover:border-[var(--d-teal)] hover:text-[var(--d-ink)]">
            <CalendarClock size={18} />For example: &ldquo;{example.ask}&rdquo; every Monday at 7:00.
          </button>
        )}
      </div>
    </section>
  );
}

function Editor({ model, example, draft, onCancel, onSave }: { model: string; example: { ask: string; title: string }; draft: Draft; onCancel: () => void; onSave: (d: Draft, now: boolean) => void }) {
  const [d, setD] = useState(draft);
  const [check, setCheck] = useState<{ ok: boolean; next?: string[]; error?: string } | null>(null);
  // the server reads the expression: when it comes round next, or why it is not one
  useEffect(() => {
    const t = setTimeout(() => { schedules.preview(model, d.cron, TZ).then(setCheck).catch(() => setCheck(null)); }, 250);
    return () => clearTimeout(t);
  }, [model, d.cron]);
  const words = inWords(d.cron);
  const ok = d.ask.trim() && check?.ok;
  return (
    <div className="mt-5 rounded-2xl bg-[var(--d-sheet)] p-6 ring-1 ring-[var(--d-rule)] shadow-[0_1px_2px_rgba(20,35,56,.06)]">
      <label className="block">
        <span className="text-[13px] font-semibold text-[var(--d-ink-2)]">What do you want a brief on?</span>
        <textarea value={d.ask} onChange={(e) => setD({ ...d, ask: e.target.value })} rows={3} autoFocus
          placeholder={example.ask}
          className="mt-1.5 w-full resize-y rounded-lg bg-[var(--d-paper)] px-3.5 py-2.5 text-[15px] leading-relaxed text-[var(--d-ink)] placeholder:text-[var(--d-muted)]" />
      </label>
      <label className="mt-4 block">
        <span className="text-[13px] font-semibold text-[var(--d-ink-2)]">Title <span className="font-normal text-[var(--d-muted)]">(optional)</span></span>
        <input value={d.title} onChange={(e) => setD({ ...d, title: e.target.value })} placeholder={example.title}
          className="mt-1.5 w-full rounded-lg bg-[var(--d-paper)] px-3.5 py-2 text-[14px] text-[var(--d-ink)] placeholder:text-[var(--d-muted)]" />
      </label>
      <div className="mt-4">
        <span className="text-[13px] font-semibold text-[var(--d-ink-2)]">When</span>
        <div className="mt-1.5 flex flex-wrap gap-2">
          {PRESETS.map((p) => (
            <button key={p.cron} onClick={() => setD({ ...d, cron: p.cron })}
              className={clsx("rounded-full px-3.5 py-1.5 text-[13px] ring-1", d.cron === p.cron
                ? "bg-[var(--d-navy)] text-white ring-[var(--d-navy)]" : "text-[var(--d-ink-2)] ring-[var(--d-rule)] hover:ring-[var(--d-teal)]")}>{p.label}</button>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2">
            <span className="text-[12px] text-[var(--d-muted)]">Cron</span>
            <input value={d.cron} onChange={(e) => setD({ ...d, cron: e.target.value })} spellCheck={false} aria-label="Cron expression"
              className="w-44 rounded-md bg-[var(--d-paper)] px-2.5 py-1.5 font-mono text-[13px] text-[var(--d-ink)]" />
          </label>
          <span className="text-[14px] text-[var(--d-ink)]">{check?.ok === false ? "" : words ?? ""}</span>
        </div>
        <p className={clsx("mt-1.5 text-[12.5px]", check?.ok === false ? "text-[var(--d-rise-ink)]" : "text-[var(--d-muted)]")}>
          {check?.ok === false ? check.error : check?.next ? `Next: ${check.next.map(when).join(" · ")} (${TZ.replace(/_/g, " ")})` : " "}
        </p>
      </div>
      <div className="mt-5 flex flex-wrap gap-2">
        <button disabled={!ok} onClick={() => onSave(d, true)} className="rounded-lg bg-[var(--d-teal)] px-4 py-2 text-[14px] font-medium text-white disabled:opacity-40">Save and write one now</button>
        <button disabled={!ok} onClick={() => onSave(d, false)} className="rounded-lg px-4 py-2 text-[14px] font-medium text-[var(--d-ink)] ring-1 ring-[var(--d-rule)] disabled:opacity-40">Save</button>
        <button onClick={onCancel} className="rounded-lg px-4 py-2 text-[14px] text-[var(--d-muted)] hover:text-[var(--d-ink)]">Cancel</button>
      </div>
    </div>
  );
}

function BriefCard({ s, model, actions, below, dates, types, onRun, onPause, onEdit, onDelete }: {
  s: Schedule; model: string; actions: (key: string) => DeskActions; below: (key: string) => React.ReactNode; dates: string[]; types: Record<string, string>;
  onRun: () => void; onPause: () => void; onEdit: () => void; onDelete: () => void;
}) {
  const [pick, setPick] = useState(0);
  const [sure, setSure] = useState(false);
  const ed: Edition | undefined = s.editions[Math.min(pick, s.editions.length - 1)];
  const icon = "grid h-8 w-8 place-items-center rounded-lg text-[var(--d-muted)] hover:bg-[var(--d-paper)] hover:text-[var(--d-ink)]";
  return (
    <article className="rounded-2xl bg-[var(--d-sheet)] p-6 ring-1 ring-[var(--d-rule)] shadow-[0_1px_2px_rgba(20,35,56,.06)]">
      <header className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-[17px] font-semibold">{s.title}</h3>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 text-[13px] text-[var(--d-muted)]">
            <CalendarClock size={13} />{inWords(s.cron) ?? "On its schedule"}
            <code className="rounded bg-[var(--d-paper)] px-1.5 py-0.5 font-mono text-[12px] text-[var(--d-ink-2)]">{s.cron}</code>
            <span>·</span>
            {s.running ? <span className="flex items-center gap-1 text-[var(--d-teal)]"><Loader2 size={12} className="animate-spin" />Writing an edition…</span>
              : s.paused ? "Paused" : s.next_run ? `Next ${when(s.next_run)}` : ""}
          </p>
        </div>
        <button onClick={onRun} disabled={s.running} title="Write one now" aria-label="Write one now" className={icon}><Play size={15} /></button>
        <button onClick={onPause} title={s.paused ? "Resume" : "Pause"} aria-label={s.paused ? "Resume" : "Pause"} className={icon}>{s.paused ? <Play size={15} className="text-[var(--d-teal)]" /> : <Pause size={15} />}</button>
        <button onClick={onEdit} title="Edit" aria-label="Edit" className={icon}><Pencil size={15} /></button>
        {sure ? <button onClick={onDelete} onBlur={() => setSure(false)} className="rounded-lg bg-[var(--d-rise-soft)] px-2.5 py-1.5 text-[13px] text-[var(--d-rise-ink)]">Delete it</button>
          : <button onClick={() => setSure(true)} title="Delete" aria-label="Delete" className={icon}><Trash2 size={15} /></button>}
      </header>
      <p className="mt-3 border-l-2 border-[var(--d-rule)] pl-3 text-[14px] italic text-[var(--d-ink-2)]">{s.ask}</p>

      {!ed ? (
        <p className="mt-5 text-[14px] text-[var(--d-muted)]">
          {s.running ? "The first edition is being written." : `No edition yet${s.next_run ? `: the first is written ${when(s.next_run)}` : ""}.`}
          {!s.running && <button onClick={onRun} className="ml-2 font-medium text-[var(--d-teal)]">Write one now</button>}
        </p>
      ) : (
        <div className="mt-5">
          <div className="flex flex-wrap items-center gap-2 text-[12px]">
            <span className="font-semibold uppercase tracking-[0.08em] text-[var(--d-muted)]">Edition</span>
            {s.editions.map((x, i) => (
              <button key={x.at} onClick={() => setPick(i)} className={clsx("rounded-full px-2.5 py-1", i === pick
                ? "bg-[var(--d-navy)] text-white" : "text-[var(--d-ink-2)] ring-1 ring-[var(--d-rule)] hover:ring-[var(--d-teal)]")}>{when(x.at)}</button>
            ))}
          </div>
          {ed.error ? <p className="mt-4 rounded-lg bg-[var(--d-rise-soft)] px-4 py-2 text-[13px] text-[var(--d-rise-ink)]">This edition could not be written: {ed.error}</p> : (
            <div className="mt-4 max-w-3xl space-y-3 text-[15.5px] leading-relaxed">
              {ed.answer.split(/\n{2,}/).filter(Boolean).map((p, i) => <p key={i}>{p}</p>)}
            </div>
          )}
          {ed.results.length > 0 && (
            <div className="mt-6 space-y-5">
              {ed.results.map((r, i) => {
                const e = { ...r, origin: "agent" } as unknown as ExhibitData, key = `${s.id}-${ed.at}-${i}`;
                return (
                  <div key={key}>
                    {e.kind === "explain" ? <InvestigationExhibit e={e} n={i + 1} actions={actions(key)} lean />
                      : <PbiExhibit e={e} n={i + 1} model={model} actions={actions(key)} dates={dates} types={types} />}
                    {below(key)}
                  </div>
                );
              })}
            </div>
          )}
          <p className="mt-4 text-[12px] text-[var(--d-muted)]">Written in {ed.seconds}s from the numbers on {when(ed.at)}.</p>
        </div>
      )}
    </article>
  );
}
