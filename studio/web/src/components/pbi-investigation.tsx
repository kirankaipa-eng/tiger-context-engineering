"use client";

/* An investigation on the Power BI desk: why a measure changed between two periods, as the explain tool
   worked it out. The answer is the headline; under it the trail from the whole change down to where it
   sits, each step sized by how much of the change it carries. Selecting a step shows what moved at that
   level against what did not, and the columns checked and ruled out there. Every number is a query that
   ran; the rows behind the last step are one click away. */

import { Check, ChevronRight, CircleDashed, Equal, Pin, RotateCcw, Rows3, SearchCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { num } from "./pbi";
import type { DeskActions, ExhibitData } from "./pbi-exhibit";
import { colLabel } from "@/lib/pbi";

export type Step = {
  by: string; value: string; before: number; after: number; carries: number; held: number; held_wide: boolean;
  values: [string, number, number][]; n: number; rest: number; sql: string;
  tested: { by: string; value?: string; carries?: number; held?: number; status: "driver" | "also" | "even" | "ordinary" }[];
};
export type Investigation = {
  metric: string; label: string; period: string; before: string; now: string; total_before: number; total_after: number;
  usual: number; quiet: boolean; verdict: "path" | "quiet" | "spread"; steps: Step[];
};

const col = (d: string) => colLabel(d).replace(/ · .*$/, "");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const when = (p: string) => { const m = /^(\d{4})-(\d{2})$/.exec(p); return m ? `${MONTHS[+m[2] - 1]} ${m[1]}` : p; };
const signed = (x: number) => `${x >= 0 ? "+" : "−"}${num(Math.abs(x))}`;
const pct = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(100 * x).toFixed(1)}%`;

export function headline(v: Investigation) {
  const total = v.total_after - v.total_before, rel = v.total_before ? total / v.total_before : 0;
  const moved = `${v.label} went from ${num(v.total_before)} in ${when(v.before)} to ${num(v.total_after)} in ${when(v.now)}`;
  const last = v.steps[v.steps.length - 1];
  if (v.verdict === "path" && last) {
    // a value contained in a later one says nothing more (XBOX before XBOX ONE); each named with what it is
    const steps = v.steps.filter((s, i) => !v.steps.slice(i + 1).some((y) => y.value.toLowerCase().startsWith(s.value.toLowerCase())));
    const where = steps.map((s) => `${s.value} (${col(s.by).toLowerCase()})`).join(", ");
    const share = total ? (last.after - last.before) / total : 0;
    const from = last.before === 0 ? `, where there were none in ${when(v.before)}` : "";
    const dir = total >= 0 ? "up" : "down";
    return {
      title: v.quiet ? `${v.label} looks flat, but ${steps.map((s) => s.value).join(", ")} jumped`
        : `${v.label} ${dir} ${Math.abs(100 * rel).toFixed(0)}%: ${steps.map((s) => s.value).join(", ")}`,
      sub: `${moved}. ${where} alone ${last.after >= last.before ? "added" : "took away"} ${num(Math.abs(last.after - last.before))}${from}` +
        (v.quiet ? "; falls elsewhere hide it in the total." : `: ${Math.round(100 * Math.abs(share))}% of the change.`),
    };
  }
  if (v.verdict === "quiet") return {
    title: `Nothing unusual in ${v.label}`,
    sub: `${moved} (${pct(rel)}). It usually moves about ${(100 * v.usual).toFixed(0)}% from one month to the next, and nothing underneath stood out. Nothing to chase.`,
  };
  return {
    title: `${v.label} moved across the board`,
    sub: `${moved} (${pct(rel)}). Every part moved roughly in line with its size, so no single part explains it.`,
  };
}

export function InvestigationExhibit({ e, n, actions, landed, lean }: { e: ExhibitData; n: number; actions: DeskActions; landed?: boolean; lean?: boolean }) {
  const v = e.explain as Investigation;
  const [level, setLevel] = useState(0);  // 0: the whole change; i: inside step i - 1
  const at = Math.max(0, level - 1);
  const [how, setHow] = useState(false);
  const total = v.total_after - v.total_before;
  const h = headline(v);
  const step = v.steps[at];
  const path = (upto: number) => Object.fromEntries(v.steps.slice(0, upto + 1).map((s) => [s.by, s.value]));
  const rows = () => {
    const last = v.steps.length - 1, s = v.steps[last];
    actions.rows({ kind: "compute", columns: [v.metric], rows: [[s ? s.after : v.total_after]], metrics: [v.metric], by: [],
      filters: { ...e.filters, ...path(last), [v.period]: v.now }, labels: { [v.metric]: v.label }, origin: "drill" }, []);
  };
  const checked = v.steps.reduce((x, s) => x + s.tested.length, 0);

  return (
    <section id={`exhibit-${n}`} className={`overflow-hidden rounded-xl border border-[var(--d-rule)] bg-[var(--d-sheet)] shadow-[0_1px_2px_rgba(20,35,56,.06)] ${landed ? "landed" : ""}`}>
      <div className="border-b border-[var(--d-rule)] px-6 pb-4 pt-4">
        <div className="flex flex-wrap items-center gap-2 text-[11px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">
          <SearchCheck size={14} className="text-[var(--d-teal)]" aria-hidden />{n ? `${n} · Why it moved` : "Why it moved"}
          <span className="ml-auto rounded-full bg-[var(--d-teal-soft)] px-2.5 py-0.5 text-[12px] normal-case tracking-normal text-[var(--d-teal)]">{when(v.before)} vs {when(v.now)} · from your Power BI report</span>
        </div>
        <h2 className="cond mt-1.5 text-[26px] font-semibold leading-tight text-[var(--d-ink)] [text-wrap:balance]">{h.title}</h2>
        <p className="mt-1.5 max-w-[72ch] text-[14.5px] leading-relaxed text-[var(--d-ink-2)]">{h.sub}</p>
      </div>

      {v.steps.length > 0 ? <Dive v={v} level={level} setLevel={setLevel} /> : <Range v={v} />}

      {step && !lean && (
        <div className="grid gap-6 px-6 pb-5 pt-5 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
          <div className="min-w-0">
            <div className="mb-2 text-[12px] font-semibold uppercase tracking-[.06em] text-[var(--d-muted)]">
              How each {col(step.by).toLowerCase()} moved{at > 0 ? `, within ${v.steps.slice(0, at).map((s) => s.value).join(", ")}` : ""}
            </div>
            <Moves step={step} />
            <p className="mt-3 text-[13px] text-[var(--d-ink-2)]">
              <b className="text-[var(--d-ink)]">{step.value}</b> is {Math.round(100 * step.carries)}% of the {total >= 0 ? "increase" : "decrease"}, though it was only {Math.round(100 * step.held)}% of {v.label.toLowerCase()} before.
              {step.n > 1 ? ` The other ${step.n - 1} together moved ${signed(step.rest)}.` : ""}
            </p>
          </div>
          <div className="min-w-0">
            <div className="mb-2 text-[12px] font-semibold uppercase tracking-[.06em] text-[var(--d-muted)]">What else we looked at</div>
            <ul className="space-y-1.5">
              {step.tested.map((t, i) => <Tested key={i} t={t} />)}
            </ul>
          </div>
        </div>
      )}

      {how && (
        <div className="border-t border-[var(--d-rule)] px-6 py-4">
          <p className="mb-2 text-[13px] text-[var(--d-ink-2)]">At each step it breaks the change down every way the report allows (by store, product, promotion and so on) and follows the one part that grew far more than its usual share. It only follows a part whose change is well outside its own normal ups and downs over the past year, so ordinary noise is never reported as a cause. Every number comes from your data; no AI guesses at any step.</p>
          {step && <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-[var(--d-paper)] p-3 font-mono text-[11px]">{step.sql}</pre>}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 border-t border-[var(--d-rule)] px-6 py-2.5 text-[12px] text-[var(--d-muted)]">
        <span>{checked ? `Looked at it ${checked} ways` : "Checked against the past year"} · every number traces back to your data</span>
        <span className="ml-auto flex gap-1">
          {actions.pin && <button onClick={() => actions.pin!(e)} aria-pressed={actions.pinned?.(e)}
            className={`flex items-center gap-1 rounded-md px-2.5 py-1 ${actions.pinned?.(e) ? "text-[var(--d-teal)]" : "text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]"}`}>
            <Pin size={13} className={actions.pinned?.(e) ? "fill-current" : ""} />{actions.pinned?.(e) ? "Pinned" : "Pin to my dashboard"}</button>}
          <button onClick={() => setHow((x) => !x)} className="rounded-md px-2.5 py-1 text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]">{how ? "Hide" : "How we worked it out"}</button>
          {v.steps.length > 0 && (
            <button onClick={rows} className="flex items-center gap-1.5 rounded-md bg-[var(--d-navy)] px-2.5 py-1 font-medium text-white hover:bg-[var(--d-navy-2)]">
              <Rows3 size={13} aria-hidden />See the transactions</button>
          )}
        </span>
      </div>
    </section>
  );
}

/** The change of each value at a step, as bars either side of zero; the one that carries it is marked. */
function Moves({ step }: { step: Step }) {
  const vals = step.values.slice(0, 10);
  const max = Math.max(1, ...vals.map(([, b, a]) => Math.abs(a - b)));
  return (
    <ul className="space-y-1">
      {vals.map(([k, b, a]) => {
        const d = a - b, hit = k === step.value, w = (50 * Math.abs(d)) / max;
        return (
          <li key={k} className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)_6.5rem] items-center gap-3 text-[13px]">
            <span className={`truncate ${hit ? "font-semibold text-[var(--d-ink)]" : "text-[var(--d-ink-2)]"}`} title={k}>{k}</span>
            <span className="relative h-4">
              <span className="absolute inset-y-0 left-1/2 w-px bg-[var(--d-rule)]" aria-hidden />
              <span className={`absolute inset-y-0.5 rounded-sm ${hit ? "bg-[var(--d-teal)]" : d >= 0 ? "bg-[var(--d-slate)]" : "bg-[var(--d-fall)] opacity-60"}`}
                style={d >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
            </span>
            <span className={`num text-right ${hit ? "font-semibold text-[var(--d-ink)]" : "text-[var(--d-ink-2)]"}`} title={`${num(b)} → ${num(a)}`}>{signed(d)}</span>
          </li>
        );
      })}
    </ul>
  );
}

function Tested({ t }: { t: Step["tested"][number] }) {
  const tone = {
    driver: { icon: <Check size={13} />, cls: "bg-[var(--d-teal-soft)] text-[var(--d-teal)]", say: `this is where it is: ${t.value}` },
    also: { icon: <CircleDashed size={13} />, cls: "bg-[var(--d-paper)] text-[var(--d-ink-2)]", say: `${t.value} stands out too, but it is the same story from another angle` },
    even: { icon: <Equal size={13} />, cls: "bg-[var(--d-paper)] text-[var(--d-muted)]", say: "moved in line with its size" },
    ordinary: { icon: <Equal size={13} />, cls: "bg-[var(--d-paper)] text-[var(--d-muted)]", say: "normal ups and downs" },
  }[t.status];
  return (
    <li className="flex items-start gap-2 text-[13px]">
      <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full ${tone.cls}`} aria-hidden>{tone.icon}</span>
      <span className="min-w-0"><b className={t.status === "driver" ? "text-[var(--d-ink)]" : "font-medium text-[var(--d-ink-2)]"}>{col(t.by)}</b>
        <span className="text-[var(--d-muted)]">: {tone.say}</span></span>
    </li>
  );
}

/** The dive: the change as one bar that splits into what moved; the part that carries it swells to fill
   the bar and splits again, one sentence a level, until the change stops being concentrated. */
function Dive({ v, level, setLevel }: { v: Investigation; level: number; setLevel: (n: number) => void }) {
  const [zoom, setZoom] = useState(false);
  const [auto, setAuto] = useState(true);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const still = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const total = v.total_after - v.total_before, sign = total >= 0 ? 1 : -1;
  const last = v.steps.length;

  const go = (to: number) => {
    if (timer.current) clearTimeout(timer.current);
    if (to === level + 1 && level >= 1 && !still) {  // swell the part that carries it, then split it
      setZoom(true);
      timer.current = setTimeout(() => { setZoom(false); setLevel(to); }, 650);
    } else { setZoom(false); setLevel(to); }
  };
  useEffect(() => {
    if (!auto || level >= last) return;
    timer.current = setTimeout(() => go(level + 1), level === 0 ? 1300 : 2300);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [auto, level]); // eslint-disable-line react-hooks/exhaustive-deps

  // the segments at this level: the values of the split that chose this step, by their part of the movement
  const s = level ? v.steps[level - 1] : null;
  const parentDelta = level > 1 ? v.steps[level - 2].after - v.steps[level - 2].before : total;
  type Seg = { key: string; d: number; share: number; hit: boolean };
  let segs: Seg[] = [{ key: v.label, d: total, share: 1, hit: true }];
  if (s) {
    const moving = s.values.reduce((x, [, b, a]) => x + Math.max(0, (a - b) * sign), 0) || 1;
    const all = s.values.map(([k, b, a]) => ({ key: k, d: a - b, share: Math.max(0, (a - b) * sign) / moving, hit: k === s.value }));
    const big = all.filter((x) => x.hit || x.share >= 0.04), small = all.filter((x) => !x.hit && x.share < 0.04);
    const rest = small.reduce((x, y) => x + y.share, 0);
    segs = [...big.sort((x, y) => Number(y.hit) - Number(x.hit) || y.share - x.share),
      ...(rest > 0.005 ? [{ key: `${small.length} others`, d: small.reduce((x, y) => x + y.d, 0), share: rest, hit: false }] : [])];
  }
  const said = (() => {
    if (!s) return { big: `${v.label} ${total >= 0 ? "went up" : "went down"} ${num(Math.abs(total))}`, small: `${when(v.before)} to ${when(v.now)}${v.quiet ? ". Normal for the total, but something underneath was not" : ""}` };
    const share = parentDelta ? (s.after - s.before) / parentDelta : 0;
    const pc = Math.round(100 * Math.abs(share));
    const part = pc >= 95 ? "nearly all of it" : `${pc}% of it`;
    const within = level > 1 ? `Within ${v.steps[level - 2].value}, ` : "";
    return {
      big: `${within}${s.value} is ${part}`,
      small: `${col(s.by)} · ${s.before === 0 ? `none in ${when(v.before)}` : `${num(s.before)} in ${when(v.before)}`}, ${num(s.after)} in ${when(v.now)}`,
    };
  })();

  return (
    <div className="dive bg-[var(--d-dive)] px-6 pb-5 pt-6 text-[var(--d-dive-ink)]">
      <div key={level} className="dive-say">
        <p className="cond text-[30px] font-semibold leading-tight [text-wrap:balance]">{said.big}</p>
        <p className="num mt-1 text-[13px] text-[var(--d-dive-muted)]">{said.small}</p>
      </div>
      <div className="mt-5 flex h-16 w-full gap-[3px] overflow-hidden rounded-md" role="img"
        aria-label={segs.map((g) => `${g.key} ${signed(g.d)}`).join(", ")}>
        {segs.map((g) => (
          <button key={`${level}-${g.key}`} onClick={() => g.hit && level < last && (setAuto(false), go(level + 1))}
            className={`dive-seg relative flex min-w-0 flex-col justify-center overflow-hidden rounded-[4px] px-3 text-left ${g.hit ? "bg-[var(--d-dive-hit)] text-[var(--d-dive-hit-ink)]" : "bg-[var(--d-dive-seg)] text-[var(--d-dive-seg-ink)]"} ${zoom && !g.hit ? "opacity-0" : ""}`}
            style={{ flexGrow: zoom ? (g.hit ? 1 : 0) : g.share, flexBasis: 0, cursor: g.hit && level < last ? "zoom-in" : "default" }}
            title={`${g.key}: ${signed(g.d)}`} tabIndex={g.hit && level < last ? 0 : -1}>
            {(g.share >= 0.07 || g.hit) && <>
              <span className="truncate text-[13px] font-semibold">{g.key}</span>
              <span className="num truncate text-[12px] opacity-80">{signed(g.d)}</span>
            </>}
          </button>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-1 text-[13px]">
        {[{ label: `All ${v.label.toLowerCase()}` }, ...v.steps.map((x) => ({ label: x.value }))].map((c, i) => (
          <span key={i} className="flex items-center gap-1">
            {i > 0 && <ChevronRight size={14} className="text-[var(--d-slate)]" aria-hidden />}
            <button onClick={() => { setAuto(false); go(i); }} disabled={i > level}
              className={`rounded px-1.5 py-0.5 ${i === level ? "bg-[var(--d-dive-chip)] font-semibold" : i < level ? "opacity-80 hover:bg-[var(--d-dive-chip)]" : "text-[var(--d-dive-muted)] opacity-70"}`}>{c.label}</button>
          </span>
        ))}
        <span className="ml-auto flex items-center gap-2 text-[12px] text-[var(--d-dive-muted)]">
          {level < last ? <button onClick={() => { setAuto(false); go(level + 1); }} className="rounded-md bg-[var(--d-dive-chip)] px-2.5 py-1 font-medium text-[var(--d-dive-ink)]">Go deeper</button>
            : <span>{v.steps.length > 0 ? "That's the root of it" : ""}</span>}
          <button onClick={() => { setAuto(true); go(0); }} aria-label="Play it again" className="rounded-md p-1 hover:bg-[var(--d-dive-chip)]"><RotateCcw size={14} /></button>
        </span>
      </div>
    </div>
  );
}

/** Where a change sits against the measure's usual swing: inside the band, nothing to chase. */
function Range({ v }: { v: Investigation }) {
  const rel = v.total_before ? (v.total_after - v.total_before) / v.total_before : 0;
  const span = Math.max(0.05, 2.5 * v.usual, 1.3 * Math.abs(rel));
  const x = (r: number) => `${50 + (50 * r) / span}%`;
  return (
    <div className="px-6 pb-2 pt-6">
      <div className="relative h-12">
        <div className="absolute inset-x-0 top-5 h-2 rounded-full bg-[var(--d-paper)]" />
        <div className="absolute top-5 h-2 rounded-full bg-[var(--d-teal-soft)]" style={{ left: x(-v.usual), right: `calc(100% - ${x(v.usual)})` }} />
        <div className="absolute top-3 h-6 w-px bg-[var(--d-slate)]" style={{ left: "50%" }} />
        <div className="absolute top-[14px] h-4 w-4 -translate-x-1/2 rounded-full border-2 border-white bg-[var(--d-navy)] shadow" style={{ left: x(rel) }} />
        <span className="num absolute top-[38px] -translate-x-1/2 text-[12px] font-semibold text-[var(--d-ink)]" style={{ left: x(rel) }}>{pct(rel)}</span>
      </div>
      <p className="mt-3 text-[12px] text-[var(--d-muted)]">Shaded: its usual change from one month to the next (±{(100 * v.usual).toFixed(0)}%). Every split was checked against its own history too.</p>
    </div>
  );
}
