"use client";

/* The living brief: what a leader reads first. Where the headline measures will land this quarter (against a
   plan, or the same quarter last year), then the few things that moved out of their own pattern, biggest
   first, each in one line with since when, whether it is growing or easing, and who has it. Everything is
   worked out again from the data when the page opens; why it moved, and whether it is real, are one click
   away. Nothing here is staged: a quiet month says it is quiet. */

import { ArrowRight, Check, ChevronDown, ChevronLeft, ChevronRight, Loader2, MessagesSquare, Pause, Pencil, Play, SearchCheck, ShieldQuestion, UserRound, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ForecastChart } from "@/components/pbi-analysis";
import { PatternMap } from "@/components/pbi-pattern";
import { InvestigationExhibit } from "@/components/pbi-investigation";
import type { DeskActions } from "@/components/pbi-exhibit";
import { pbi, type Brief, type BriefPrefs, type Claim, type Landing, type Pattern } from "@/lib/pbi";
import { owner } from "@/lib/desk";
import { useStudio } from "@/lib/studio";
import { SettingsMenu, useLook } from "@/lib/theme";

const MON = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const month = (m: string) => `${MON[+m.slice(5, 7) - 1]} ${m.slice(0, 4)}`;
const quarter = (q: string) => `Q${q.slice(5)} ${q.slice(0, 4)}`;
const day = (d: string) => `${+d.slice(8, 10)} ${MON[+d.slice(5, 7) - 1]} ${d.slice(0, 4)}`;

/** A value as Power BI shows it: its own format string says currency or percent; short for headlines. */
function shown(v: number | null | undefined, format: string | null, short = false) {
  if (v === null || v === undefined || !Number.isFinite(v)) return "–";
  if (format?.includes("%")) return `${(100 * v).toFixed(1)}%`;
  const cur = format?.match(/[$€£¥₹]/)?.[0] ?? "";
  const a = Math.abs(v), sign = v < 0 ? "-" : "";
  if (short && a >= 1e6) return `${sign}${cur}${(a / 1e6).toFixed(a >= 1e7 ? 1 : 2)}M`;
  if (short && a >= 1e4) return `${sign}${cur}${Math.round(a / 1e3)}K`;
  return `${sign}${cur}${Math.round(a).toLocaleString()}`;
}

/** The places a claim sits, without a value that is only the start of a later one (XBOX before XBOX ONE). */
function place(c: Claim) {
  const vs = c.where.map((w) => w.value);
  return vs.filter((v, i) => !vs.slice(i + 1).some((y) => y.toLowerCase().startsWith(v.toLowerCase()))).join(", ");
}

const STATUS: Record<Claim["status"], { label: string; tone: string }> = {
  new: { label: "New", tone: "bg-[var(--d-rise-soft)] text-[var(--d-rise-ink)]" },
  growing: { label: "Growing", tone: "bg-[var(--d-rise-soft)] text-[var(--d-rise-ink)]" },
  holding: { label: "Holding", tone: "bg-[var(--d-paper)] text-[var(--d-ink-2)]" },
  easing: { label: "Easing", tone: "bg-[var(--d-teal-soft)] text-[var(--d-teal)]" },
  settled: { label: "Back to usual", tone: "bg-[var(--d-teal-soft)] text-[var(--d-teal)]" },
};

function Spark({ series, at }: { series: [string, number][]; at: string }) {
  const w = 132, h = 36, vs = series.map(([, v]) => v), max = Math.max(...vs, 1), min = Math.min(...vs, 0);
  const x = (i: number) => (i / Math.max(1, series.length - 1)) * (w - 6) + 3;
  const y = (v: number) => h - 4 - ((v - min) / (max - min || 1)) * (h - 8);
  const k = series.findIndex(([m]) => m === at);
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden className="shrink-0">
      <polyline points={series.map(([, v], i) => `${x(i)},${y(v)}`).join(" ")} fill="none" stroke="var(--d-muted)" strokeWidth={1.5} strokeLinejoin="round" />
      {k >= 0 && <circle cx={x(k)} cy={y(series[k][1])} r={3.5} fill="var(--d-rise-ink)" />}
    </svg>
  );
}

/** A number that counts up to its value when `run` turns on (the briefing reaching it); otherwise just the value. */
function CountUp({ value, format, run }: { value: number; format: string | null; run: boolean }) {
  const [v, setV] = useState(value);
  useEffect(() => {
    if (!run || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { setV(value); return; } // eslint-disable-line react-hooks/set-state-in-effect
    let raf = 0;
    const t0 = performance.now(), dur = 1400;
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / dur);
      setV(value * (1 - Math.pow(2, -10 * k)));  // exponential ease-out
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [run, value]);
  return <>{shown(v, format, true)}</>;
}

function LandingCard({ l, onTarget, run }: { l: Landing; onTarget: (v: number | null) => Promise<void>; run?: boolean }) {
  const [edit, setEdit] = useState(false);
  const [text, setText] = useState("");
  const vs = l.target ? { label: "plan", value: l.target.value } : l.last_year ? { label: quarter(l.last_year.quarter), value: l.last_year.value } : null;
  const gap = vs && l.landing !== null ? (l.landing - vs.value) / Math.abs(vs.value || 1) : null;
  const rows = [...l.history.map(([p, v]) => [p, v, null, null, null]), ...l.ahead.map(([p, v, lo, hi]) => [p, null, v, lo, hi])];
  return (
    <section className="min-w-0 rounded-2xl border border-[var(--d-rule)] bg-[var(--d-sheet)] p-6">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h3 className="cond text-[17px] font-medium leading-snug text-[var(--d-ink-2)]">
          {l.label} in {quarter(l.quarter)}{" "}
          <span className="block text-[34px] font-semibold leading-tight tracking-[-0.01em] text-[var(--d-ink)] tabular-nums">
            {l.landing === null ? "Not enough history" : <>heading for <CountUp value={l.landing} format={l.format} run={!!run} /></>}
          </span>
        </h3>
        {gap !== null && vs && (
          // neutral: the model does not say whether more is good (Net Sales) or bad (Returns)
          <span className="rounded-full bg-[var(--d-paper)] px-2.5 py-0.5 text-[13px] font-medium text-[var(--d-ink-2)]">
            {gap >= 0 ? "+" : ""}{(100 * gap).toFixed(0)}% vs {vs.label} ({shown(vs.value, l.format, true)})
          </span>
        )}
      </div>
      <p className="mt-2 text-[13.5px] text-[var(--d-ink-2)]">
        {l.low !== null && `Likely between ${shown(l.low, l.format, true)} and ${shown(l.high, l.format, true)}`}
        {l.months_done > 0 && `, with ${shown(l.so_far, l.format, true)} already in`}.
        {l.miss !== null && ` On recent months this forecast was off by ${(100 * l.miss).toFixed(0)}% on average.`}
        {l.closed && ` ${quarter(l.closed.quarter)} closed at ${shown(l.closed.actual, l.format, true)}.`}
      </p>
      <div className="mt-3"><ForecastChart columns={["period", "actual", "forecast", "low", "high"]} rows={rows} height={170} /></div>
      <div className="mt-2 flex items-center gap-2 text-[13px] text-[var(--d-muted)]">
        {edit ? (
          <form className="flex items-center gap-2" onSubmit={async (e) => { e.preventDefault(); await onTarget(text.trim() ? Number(text.replace(/[^\d.-]/g, "")) : null); setEdit(false); }}>
            <span>Plan for {quarter(l.quarter)}:</span>
            <input autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. 1000000" inputMode="decimal"
              className="w-36 rounded-md border border-[var(--d-rule)] bg-[var(--d-paper)] px-2 py-1 text-[var(--d-ink)] outline-none focus:border-[var(--d-teal)]" />
            <button className="rounded-md bg-[var(--d-teal)] px-2.5 py-1 font-medium text-white">Save</button>
            <button type="button" onClick={() => setEdit(false)} className="px-1.5 py-1">Cancel</button>
          </form>
        ) : (
          <button onClick={() => { setText(l.target ? String(l.target.value) : ""); setEdit(true); }} className="flex items-center gap-1.5 rounded-md px-1.5 py-1 hover:bg-[var(--d-paper)] hover:text-[var(--d-ink)]">
            <Pencil size={13} />{l.target ? `Plan ${shown(l.target.value, l.format, true)}, set by ${l.target.by}` : "Set a plan for this quarter"}
          </button>
        )}
      </div>
    </section>
  );
}

function ClaimCard({ c, onTake, model, openWhy }: { c: Claim; onTake: () => Promise<void>; model: string; openWhy?: number }) {
  const [why, setWhy] = useState(false);
  useEffect(() => { if (openWhy) setWhy(true); }, [openWhy]); // eslint-disable-line react-hooks/set-state-in-effect -- opened from the map or the briefing
  const [busy, setBusy] = useState(false);
  const s = STATUS[c.status];
  const up = c.excess >= 0;
  const where = place(c);
  const ask = `Is the ${up ? "jump" : "drop"} in ${c.label} at ${where} in ${month(c.month)} real, or just noise?`;
  // the brief has no exhibit trail of its own: the rows open on the desk, asked in words
  const go = (t: string) => { window.location.href = `/pbi?q=${encodeURIComponent(t)}`; };
  const actions: DeskActions = {
    drill: () => go(`Break down ${c.label} at ${where} in ${month(c.month)}`),
    rows: () => go(`Show the transactions behind ${c.label} at ${where} in ${month(c.month)}`),
    ask: go,
  };
  const f = c.finding;
  return (
    <article className="rounded-2xl border border-[var(--d-rule)] bg-[var(--d-sheet)]">
      <div className="flex flex-wrap items-start gap-x-5 gap-y-3 p-5">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full px-2.5 py-0.5 text-[12px] font-semibold ${s.tone}`}>{s.label}</span>
            {c.status !== "new" && c.status !== "settled" && <span className="text-[12.5px] text-[var(--d-muted)]">since {month(c.since)}</span>}
            {c.mine && <span className="rounded-full bg-[var(--d-ink)] px-2.5 py-0.5 text-[12px] font-semibold text-[var(--d-sheet)]">Yours</span>}
          </div>
          <h3 className="cond mt-2 text-[20px] font-semibold leading-snug text-[var(--d-ink)]">{c.label} at {where}</h3>
          <p className="mt-1 text-[14px] text-[var(--d-ink-2)]">
            {c.status === "settled"
              ? <>Back within its usual range in the month after {month(c.month)}, when it was {shown(c.value, c.format, true)} against a usual {shown(c.usual, c.format, true)}.</>
              : <><b className="font-semibold text-[var(--d-ink)]">{shown(c.value, c.format, true)}</b> in {month(c.month)} against a usual {shown(c.usual, c.format, true)} a month: {shown(Math.abs(c.excess), c.format, true)} {up ? "above" : "below"} its own pattern.</>}
            {c.also?.length ? ` ${c.also.join(" and ")} ${c.also.length > 1 ? "show" : "shows"} the same.` : ""}
          </p>
        </div>
        <Spark series={c.series} at={c.month} />
        <div className="flex w-full items-center gap-2 sm:w-auto sm:flex-col sm:items-end">
          {f?.owner ? (
            <span className="flex items-center gap-1.5 rounded-full bg-[var(--d-paper)] px-3 py-1 text-[13px] text-[var(--d-ink)]"><UserRound size={14} />{f.owner} has it{f.status === "acted" ? ", acted" : f.status === "resolved" ? ", resolved" : ""}</span>
          ) : (
            <button disabled={busy} onClick={async () => { setBusy(true); try { await onTake(); } finally { setBusy(false); } }}
              className="flex items-center gap-1.5 rounded-full border border-[var(--d-rule)] px-3 py-1 text-[13px] text-[var(--d-ink-2)] hover:border-[var(--d-teal)] hover:text-[var(--d-teal)]">
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}I&rsquo;ll take it
            </button>
          )}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1 border-t border-[var(--d-rule)] px-4 py-2 text-[13px]">
        <button onClick={() => setWhy((x) => !x)} aria-expanded={why} className="flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]">
          <SearchCheck size={14} />Why<ChevronDown size={14} className={why ? "rotate-180" : ""} />
        </button>
        <Link href={`/pbi?q=${encodeURIComponent(ask)}`} className="flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]"><ShieldQuestion size={14} />Is it real?</Link>
        <Link href={f ? `/pbi/team?f=${f.id}` : "/pbi/team"} className="flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]">
          <MessagesSquare size={14} />{f?.comments?.length ? `${f.comments.length} in the thread` : "Discuss"}
        </Link>
      </div>
      {why && (
        <div className="border-t border-[var(--d-rule)] p-4">
          <InvestigationExhibit e={{ kind: "explain", explain: c.explain, columns: [], rows: [], metrics: [c.metric], by: [], filters: {}, labels: {}, origin: "agent" }} n={0} actions={actions} lean />
          <p className="mt-2 text-[12px] text-[var(--d-muted)]">Where the change sits, worked out from {model}&rsquo;s rows; it shows where, not what caused it.</p>
        </div>
      )}
    </article>
  );
}

const colName = (d: string) => d.replace(/^.*\[|\]$/g, "");

/** What the person's brief covers, in a line: their slice and the measures they follow. */
function covers(b: Brief) {
  const scope = Object.entries(b.prefs.scope).map(([c, vs]) => `${vs.length > 3 ? `${vs.length} ${colName(c).toLowerCase()}s` : vs.join(", ")}`);
  const follow = b.prefs.measures.length ? b.choices.filter((x) => b.prefs.measures.includes(x.metric)).map((x) => x.label) : [];
  return [scope.length ? scope.join("; ") : "the whole business", follow.length ? `following ${follow.join(", ")}` : `all ${b.choices.length} measures`].join(" · ");
}

/** Making the brief one's own: who you are, the measures you follow, which land, and the part of the business you answer for. */
function YourBrief({ b, me, model, onSaved, onClose }: { b: Brief; me: string; model: string; onSaved: (name: string) => void; onClose: () => void }) {
  const [name, setName] = useState(me === "me" ? "" : me);
  const [measures, setMeasures] = useState<string[]>(b.prefs.measures);
  const [headline, setHeadline] = useState<string[]>(b.prefs.headline);
  const first = Object.entries(b.prefs.scope)[0];
  const [col, setCol] = useState(first?.[0] ?? "");
  const [picked, setPicked] = useState<string[]>(first?.[1] ?? []);
  const [options, setOptions] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    if (col) pbi.values(model, col).then((r) => { if (live) setOptions(r.values.filter((v) => v !== null && v !== "").map(String)); }).catch(() => { if (live) setOptions([]); });
    return () => { live = false; };
  }, [col, model]);
  const toggle = (xs: string[], x: string, max = 99) => (xs.includes(x) ? xs.filter((y) => y !== x) : xs.length < max ? [...xs, x] : xs);
  const chip = (on: boolean) => `rounded-full border px-3 py-1 text-[13px] transition-colors ${on ? "border-[var(--d-ink)] bg-[var(--d-ink)] text-[var(--d-sheet)]" : "border-[var(--d-rule)] text-[var(--d-ink-2)] hover:border-[var(--d-ink-2)]"}`;
  const save = async () => {
    if (!name.trim()) { setErr("Your name first: the brief is kept under it."); return; }
    setBusy(true); setErr(null);
    try {
      try { localStorage.setItem("desk-name", name.trim()); } catch { /* private window: kept for this visit */ }
      const prefs: BriefPrefs = { measures, headline, scope: col && picked.length ? { [col]: picked } : {} };
      await pbi.briefPrefs(model, name.trim(), prefs);
      onSaved(name.trim());
    } catch (e: any) { setErr(String(e?.message ?? e)); } // eslint-disable-line @typescript-eslint/no-explicit-any
    finally { setBusy(false); }
  };
  const shownOptions = options.filter((o) => o.toLowerCase().includes(q.toLowerCase())).slice(0, 40);
  return (
    <section className="mt-6 rounded-2xl border border-[var(--d-rule)] bg-[var(--d-sheet)] p-6" aria-label="Make the brief yours">
      <h2 className="cond text-[20px] font-semibold">Make it your brief</h2>
      <div className="mt-5 grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        <label className="text-[13px] font-medium text-[var(--d-ink-2)]" htmlFor="brief-name">Your name</label>
        <input id="brief-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Priya"
          className="w-full max-w-xs rounded-lg border border-[var(--d-rule)] bg-[var(--d-paper)] px-3 py-2 text-[14px] outline-none focus:border-[var(--d-teal)]" />

        <div className="text-[13px] font-medium text-[var(--d-ink-2)]">Measures you follow<p className="mt-1 font-normal text-[var(--d-muted)]">None picked: all of them.</p></div>
        <div className="flex flex-wrap gap-2">
          {b.choices.map((x) => <button key={x.metric} aria-pressed={measures.includes(x.metric)} onClick={() => setMeasures(toggle(measures, x.metric))} className={chip(measures.includes(x.metric))}>{x.label}</button>)}
        </div>

        <div className="text-[13px] font-medium text-[var(--d-ink-2)]">Where each quarter lands<p className="mt-1 font-normal text-[var(--d-muted)]">Up to three.</p></div>
        <div className="flex flex-wrap gap-2">
          {b.choices.map((x) => <button key={x.metric} aria-pressed={headline.includes(x.metric)} onClick={() => setHeadline(toggle(headline, x.metric, 3))} className={chip(headline.includes(x.metric))}>{x.label}</button>)}
        </div>

        <div className="text-[13px] font-medium text-[var(--d-ink-2)]">Your part of the business<p className="mt-1 font-normal text-[var(--d-muted)]">Everything in the brief is worked out inside it.</p></div>
        <div className="min-w-0">
          <select value={col} onChange={(e) => { setCol(e.target.value); setPicked([]); setQ(""); }} aria-label="Narrow by"
            className="rounded-lg border border-[var(--d-rule)] bg-[var(--d-paper)] px-3 py-2 text-[14px] text-[var(--d-ink)]">
            <option value="">The whole business</option>
            {b.slices.map((d) => <option key={d} value={d}>{colName(d)}</option>)}
          </select>
          {col && (
            <div className="mt-3">
              {picked.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-2">{picked.map((v) => (
                  <button key={v} onClick={() => setPicked(picked.filter((x) => x !== v))} className={`${chip(true)} flex items-center gap-1.5`} aria-label={`Remove ${v}`}>{v}<X size={12} /></button>
                ))}</div>
              )}
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Find a ${colName(col).toLowerCase()}`}
                className="w-full max-w-xs rounded-lg border border-[var(--d-rule)] bg-[var(--d-paper)] px-3 py-1.5 text-[13.5px] outline-none focus:border-[var(--d-teal)]" />
              <div className="mt-2 flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
                {shownOptions.filter((o) => !picked.includes(o)).map((o) => <button key={o} onClick={() => setPicked([...picked, o])} className={chip(false)}>{o}</button>)}
                {options.length === 0 && <span className="text-[13px] text-[var(--d-muted)]">Reading its values…</span>}
              </div>
            </div>
          )}
        </div>
      </div>
      {err && <p className="mt-4 text-[13.5px] text-[var(--d-rise-ink)]">{err}</p>}
      <div className="mt-6 flex items-center gap-3">
        <button onClick={save} disabled={busy} className="flex items-center gap-2 rounded-full bg-[var(--d-ink)] px-4 py-2 text-[14px] font-medium text-[var(--d-sheet)] disabled:opacity-50">
          {busy && <Loader2 size={14} className="animate-spin" />}Make it my brief
        </button>
        <button onClick={onClose} className="rounded-full px-3 py-2 text-[14px] text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]">Cancel</button>
      </div>
    </section>
  );
}

/** One beat of the briefing: what it says, which part of the page it lights, how long it holds. */
type Beat = { id: string; say: string; ms: number; run?: () => void };

/** The briefing, written from the data: the quarters, the map's year, then each claim and why it sits where it does. */
function beats(b: Brief, p: Pattern | null, hooks: { mapFrom: () => void; why: (i: number) => void }): Beat[] {
  const out: Beat[] = [];
  if (b.me) out.push({ id: "end", ms: 4500, say: `${b.me}, this is ${month(b.as_of)}: ${covers(b)}.` });
  b.landing.forEach((l, i) => {
    if (l.landing === null) return;
    const vs = l.target ? ["plan", l.target.value] as const : l.last_year ? [quarter(l.last_year.quarter), l.last_year.value] as const : null;
    const gap = vs ? (l.landing - vs[1]) / Math.abs(vs[1] || 1) : null;
    out.push({ id: `landing-${i}`, ms: 6500,
      say: `${l.label} is heading for ${shown(l.landing, l.format, true)} in ${quarter(l.quarter)}` +
        (gap !== null && vs ? `, ${Math.abs(100 * gap).toFixed(0)}% ${gap >= 0 ? "above" : "below"} ${vs[0]}.` : ".") });
  });
  if (p) {
    const lit = p.groups.flatMap((g) => g.children.flatMap((c) => c.out.map((o, i) => (o ? { where: `${g.name} · ${c.name}`, m: p.months[i] } : null)))).filter(Boolean) as { where: string; m: string }[];
    const span = `${month(p.months[Math.max(0, p.months.length - 13)])} to ${month(p.months[p.months.length - 1])}`;
    out.push({ id: "map", ms: 13 * 650 + 2200, run: hooks.mapFrom,
      say: lit.length === 0
        ? `From ${span}, every one of the ${p.places} places ${p.label} adds up from held its usual shape.`
        : `From ${span}, ${p.label} held its shape across ${p.places} places, until ${lit.map((x) => `${x.where} in ${month(x.m)}`).slice(-2).join(" and ")}.` });
  }
  b.claims.slice(0, 3).forEach((c, i) => {
    out.push({ id: `claim-${i}`, ms: 7000,
      say: `${c.label} at ${place(c)}: ${shown(c.value, c.format, true)} in ${month(c.month)} against a usual ${shown(c.usual, c.format, true)}` +
        (c.status === "new" ? ", new this month." : `, ${STATUS[c.status].label.toLowerCase()} since ${month(c.since)}.`) +
        (c.finding?.owner ? ` ${c.finding.owner} has it.` : " Nobody has taken it yet.") });
    const first = c.explain.steps[0], last = c.explain.steps[c.explain.steps.length - 1];
    if (first) out.push({ id: `claim-${i}`, ms: 9000, run: () => hooks.why(i),
      // the same share the investigation shows ("Leo is 83% of it"): its change over the whole change
      say: `Why: ${first.value} is ${Math.round(100 * (first.after - first.before) / ((c.explain.total_after - c.explain.total_before) || 1))}% of the change in ${c.label.toLowerCase()}` +
        (last && last !== first ? `, and within it, ${last.value}.` : ".") + " Where it sits, not what caused it." });
  });
  out.push({ id: "end", ms: 4500, say: b.claims.length ? `That is ${month(b.as_of)}. Ask anything about it.` : `That is ${month(b.as_of)}: nothing out of pattern. Ask anything about it.` });
  return out;
}

export default function BriefPage() {
  const { pbiModel } = useStudio();
  const look = useLook();
  const [b, setB] = useState<Brief | null>(null);
  const [asOf, setAsOf] = useState<string | undefined>();
  const [oops, setOops] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [pat, setPat] = useState<Pattern | null>(null);
  const [mapMetric, setMapMetric] = useState<string | undefined>();
  const [mi, setMi] = useState(0);
  const [mapPlaying, setMapPlaying] = useState(false);
  const [why, setWhy] = useState<Record<number, number>>({});
  const [me, setMe] = useState<string | null>(null);
  const [mine, setMine] = useState(false);
  useEffect(() => { setMe(owner()); }, []); // eslint-disable-line react-hooks/set-state-in-effect -- the name lives in this browser
  const load = useCallback(async () => {
    if (!pbiModel || me === null) return;
    setLoading(true); setOops(null);
    try { setB(await pbi.brief(pbiModel, asOf, me)); } catch (e: any) { setOops(String(e?.message ?? e)); } // eslint-disable-line @typescript-eslint/no-explicit-any
    finally { setLoading(false); }
  }, [pbiModel, asOf, me]);
  useEffect(() => { load(); }, [load]); // eslint-disable-line react-hooks/set-state-in-effect -- loads the brief when the model or month changes
  useEffect(() => {
    if (!pbiModel || !b) return;
    let live = true;
    pbi.pattern(pbiModel, mapMetric, b.as_of, me ?? undefined).then((x) => { if (live) { setPat(x); setMi(x.months.length - 1); } }).catch(() => { if (live) setPat(null); });
    return () => { live = false; };
  }, [pbiModel, b, mapMetric, me]);

  // the briefing: one beat at a time, lighting its part of the page; Space pauses, arrows step, Esc ends
  const [beat, setBeat] = useState<number | null>(null);
  const [paused, setPaused] = useState(false);
  const [plan, setPlan] = useState<Beat[]>([]);
  const start = () => {
    if (!b) return;
    setPlan(beats(b, pat, {
      mapFrom: () => { if (pat) { setMi(Math.max(0, pat.months.length - 13)); setMapPlaying(true); } },
      why: (i) => setWhy((w) => ({ ...w, [i]: (w[i] ?? 0) + 1 })),
    }));
    setPaused(false); setBeat(0);
  };
  useEffect(() => {
    if (beat === null) return;
    const bt = plan[beat];
    if (!bt) { setBeat(null); return; } // eslint-disable-line react-hooks/set-state-in-effect -- the briefing has ended
    const el = document.querySelector(`[data-beat="${bt.id}"]`);
    const calm = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el?.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "center" });
    bt.run?.();
  }, [beat, plan]);
  useEffect(() => {
    if (beat === null || paused) return;
    const t = setTimeout(() => setBeat((x) => (x === null ? null : x + 1)), plan[beat]?.ms ?? 4000);
    return () => clearTimeout(t);
  }, [beat, paused, plan]);
  useEffect(() => {
    if (beat === null) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setBeat(null); setMapPlaying(false); }
      else if (e.key === " ") { e.preventDefault(); setPaused((x) => !x); }
      else if (e.key === "ArrowRight") setBeat((x) => (x === null ? null : Math.min(plan.length - 1, x + 1)));
      else if (e.key === "ArrowLeft") setBeat((x) => (x === null ? null : Math.max(0, x - 1)));
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [beat, plan.length]);
  const on = beat !== null ? plan[beat]?.id : null;
  // while the briefing plays, the part it speaks about stays lit and the rest steps back
  const lit = (id: string) => (on === null ? "" : on === id ? "relative z-10 transition-[opacity,filter] duration-500" : "opacity-30 saturate-50 transition-[opacity,filter] duration-500");

  const claimIndex = (g: string, c: string) => b?.claims.findIndex((x) => x.where[0]?.value === g && x.where[x.where.length - 1]?.value === c) ?? -1;

  return (
    <div className={`desk ${look.look} flex h-screen flex-col bg-[var(--d-paper)] text-[var(--d-ink)] selection:bg-[var(--d-teal-soft)]`}>
      <header className="flex h-14 shrink-0 items-center gap-4 border-b border-[var(--d-rule)] bg-[var(--d-sheet)] px-6">
        <span className="cond text-[19px] font-semibold">Brief</span>
        <div className="ml-auto flex items-center gap-1 text-[13px]">
          <Link href="/pbi" className="rounded-md px-3 py-1.5 text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]">Ask</Link>
          <Link href="/pbi/board" className="rounded-md px-3 py-1.5 text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]">My dashboard</Link>
          <Link href="/pbi/team" className="rounded-md px-3 py-1.5 text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]">Team space</Link>
          <SettingsMenu look={look} />
        </div>
      </header>
      <main className="min-h-0 flex-1 overflow-y-auto pb-28">
        <div className="mx-auto max-w-[1080px] px-4 py-8 sm:px-8">
          {!b && !oops && <p className="flex items-center gap-2 text-[14px] text-[var(--d-muted)]"><Loader2 size={15} className="animate-spin" />Working out this month&rsquo;s brief from the data…</p>}
          {oops && <p className="rounded-lg bg-[var(--d-rise-soft)] px-4 py-3 text-[14px] text-[var(--d-rise-ink)]">{oops}</p>}
          {b && (
            <>
              <div className={`flex flex-wrap items-end gap-x-4 gap-y-3 ${lit("end")}`} data-beat="end">
                <div>
                  <h1 className="cond text-[34px] font-semibold leading-tight tracking-[-0.01em]">
                    {b.me ? `${b.me}\u2019s ${month(b.as_of)}` : month(b.as_of)}
                  </h1>
                  <p className="mt-1 text-[14.5px] text-[var(--d-ink-2)]">
                    {covers(b)}
                    <button onClick={() => setMine((x) => !x)} className="ml-2 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[13.5px] font-medium text-[var(--d-teal)] hover:bg-[var(--d-sheet)]">
                      <Pencil size={13} />{b.me ? "Change" : "Make it yours"}
                    </button>
                  </p>
                  <p className="mt-1 text-[13px] text-[var(--d-muted)]">
                    Data through {day(b.data_through)}{b.partial ? ` (${month(b.partial)} is not over, so it waits for next month)` : ""}. Worked out again each time this page opens.
                  </p>
                </div>
                <div className="ml-auto flex flex-wrap items-center gap-3">
                  <label className="flex items-center gap-2 text-[13px] text-[var(--d-muted)]">
                    {loading && <Loader2 size={14} className="animate-spin" />}What the brief said in
                    <select value={b.as_of} onChange={(e) => setAsOf(e.target.value)}
                      className="rounded-md border border-[var(--d-rule)] bg-[var(--d-sheet)] px-2 py-1 text-[var(--d-ink)]">
                      {[...b.complete].reverse().map((m) => <option key={m} value={m}>{month(m)}</option>)}
                    </select>
                  </label>
                  <button onClick={start} disabled={beat !== null}
                    className="flex items-center gap-2 rounded-full bg-[var(--d-ink)] px-4 py-2 text-[14px] font-medium text-[var(--d-sheet)] shadow-[0_6px_18px_-8px_rgba(0,0,0,.5)] transition hover:-translate-y-px disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--d-teal)]">
                    <Play size={15} />Play the brief
                  </button>
                </div>
              </div>

              {mine && me !== null && (
                <YourBrief b={b} me={me} model={pbiModel} onClose={() => setMine(false)}
                  onSaved={(n) => { setMine(false); setMapMetric(undefined); if (n === me) load(); else setMe(n); }} />
              )}

              {b.landing.length > 0 && (
                <div className={`mt-6 grid gap-4 ${b.landing.length > 1 ? "lg:grid-cols-2" : ""}`}>
                  {b.landing.map((l, i) => (
                    <div key={l.metric} data-beat={`landing-${i}`} className={lit(`landing-${i}`)}>
                      <LandingCard l={l} run={on === `landing-${i}`} onTarget={async (v) => { await pbi.setTarget(pbiModel, l.metric, l.quarter, v, owner()); await load(); }} />
                    </div>
                  ))}
                </div>
              )}

              {pat && (
                <section data-beat="map" className={`mt-10 ${lit("map")}`}>
                  <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
                    <div className="min-w-0">
                      <h2 className="cond text-[22px] font-semibold leading-snug">Where {pat.label.toLowerCase()} sits against its own usual</h2>
                      <p className="mt-1 max-w-[70ch] text-[13.5px] text-[var(--d-ink-2)]">
                        Each tile is a {pat.levels.map((l) => l.replace(/^.*\[|\]$/g, "").toLowerCase()).join(", then a ")}, sized by {pat.label.toLowerCase()} in the month shown.
                        A tile is lit when it sits further from its own last twelve months than chance would put any of the {pat.places} places in {pat.months.length} months.
                      </p>
                    </div>
                    {pat.choices.length > 1 && (
                      <div className="ml-auto flex gap-1 rounded-lg bg-[var(--d-sheet)] p-1 text-[13px]" role="group" aria-label="Measure on the map">
                        {pat.choices.map((x) => (
                          <button key={x.metric} onClick={() => setMapMetric(x.metric)} aria-pressed={x.metric === pat.metric}
                            className={`rounded-md px-3 py-1 ${x.metric === pat.metric ? "bg-[var(--d-ink)] font-medium text-[var(--d-sheet)]" : "text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]"}`}>{x.label}</button>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="mt-4">
                    <PatternMap p={pat} index={mi} onIndex={setMi} playing={mapPlaying} onPlaying={setMapPlaying}
                      claimSince={b.claims.find((c) => c.metric === pat.metric)?.since}
                      onPick={(g, c, m) => {
                        const i = claimIndex(g, c);
                        if (i >= 0 && b.claims[i].metric === pat.metric) {
                          document.querySelector(`[data-beat="claim-${i}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
                          setWhy((w) => ({ ...w, [i]: (w[i] ?? 0) + 1 }));
                        } else window.location.href = `/pbi?q=${encodeURIComponent(`Why did ${pat.label} at ${g}, ${c} change in ${month(m)}?`)}`;
                      }} />
                  </div>
                </section>
              )}

              <h2 className="cond mt-12 text-[22px] font-semibold">What moved out of its usual pattern</h2>
              {b.claims.length === 0 ? (
                <p className="mt-3 rounded-2xl border border-[var(--d-rule)] bg-[var(--d-sheet)] p-6 text-[14.5px] text-[var(--d-ink-2)]">
                  Nothing in {month(b.as_of)}. Every place {b.checked} measures add up from was checked against its own last twelve months, and none sits outside them.
                </p>
              ) : (
                <div className="mt-3 space-y-3">
                  {b.claims.map((c, i) => (
                    <div key={`${c.metric}-${place(c)}`} data-beat={`claim-${i}`} className={lit(`claim-${i}`)}>
                      <ClaimCard c={c} model={pbiModel} openWhy={why[i]}
                        onTake={async () => { await pbi.take(pbiModel, c.metric, c.where.map((w) => w.value), owner(), b.as_of); await load(); }} />
                    </div>
                  ))}
                  {b.more > 0 && <p className="text-[13px] text-[var(--d-muted)]">{b.more} smaller {b.more > 1 ? "ones are" : "one is"} in the team space.</p>}
                </div>
              )}

              {b.settled.length > 0 && (
                <>
                  <h2 className="cond mt-12 text-[22px] font-semibold">Back to usual</h2>
                  <div className="mt-3 space-y-3">
                    {b.settled.map((c) => <ClaimCard key={`s-${c.metric}-${place(c)}`} c={c} model={pbiModel}
                      onTake={async () => { await pbi.take(pbiModel, c.metric, c.where.map((w) => w.value), owner(), b.as_of); await load(); }} />)}
                  </div>
                </>
              )}

              <p className="mt-12 flex items-center gap-1.5 border-t border-[var(--d-rule)] pt-4 text-[12.5px] text-[var(--d-muted)]">
                Every number comes from the Power BI model&rsquo;s own measures, run on the data. A place is listed when its month sits far outside its own last twelve.
                <Link href="/pbi" className="ml-auto flex items-center gap-1 text-[var(--d-teal)]">Ask about anything <ArrowRight size={13} /></Link>
              </p>
            </>
          )}
        </div>
      </main>

      {beat !== null && plan[beat] && (
        <div className="pointer-events-none fixed inset-x-0 bottom-6 z-50 flex justify-center px-4" role="region" aria-label="Briefing" aria-live="polite">
          <div className="pointer-events-auto w-full max-w-[760px] rounded-2xl bg-[var(--d-ink)] px-6 pb-4 pt-3 text-[var(--d-sheet)] shadow-[0_24px_60px_-20px_rgba(0,0,0,.55)]">
            <div className="flex gap-1.5" aria-hidden>
              {plan.map((_, i) => (
                <span key={i} className="h-1 flex-1 overflow-hidden rounded-full bg-white/20">
                  <span className={`block h-full rounded-full bg-[var(--d-sheet)] ${i < beat ? "w-full" : i === beat && !paused ? "brief-fill" : "w-0"}`}
                    style={i === beat ? { animationDuration: `${plan[beat].ms}ms` } : undefined} />
                </span>
              ))}
            </div>
            <p key={beat} className="brief-say mt-3 text-[18px] leading-snug [text-wrap:pretty]">{plan[beat].say}</p>
            <div className="mt-3 flex items-center gap-1 text-[12.5px] text-white/70">
              <button onClick={() => setBeat(Math.max(0, beat - 1))} aria-label="Back" className="rounded-md p-1.5 hover:bg-white/10"><ChevronLeft size={16} /></button>
              <button onClick={() => setPaused((x) => !x)} aria-label={paused ? "Play" : "Pause"} className="rounded-md p-1.5 hover:bg-white/10">{paused ? <Play size={16} /> : <Pause size={16} />}</button>
              <button onClick={() => setBeat(beat + 1)} aria-label="Next" className="rounded-md p-1.5 hover:bg-white/10"><ChevronRight size={16} /></button>
              <span className="ml-2 tabular-nums">{beat + 1} of {plan.length}</span>
              <span className="ml-auto hidden sm:inline">Space pauses · arrows step · Esc ends</span>
              <button onClick={() => { setBeat(null); setMapPlaying(false); }} aria-label="End the briefing" className="ml-2 rounded-md p-1.5 hover:bg-white/10"><X size={16} /></button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
