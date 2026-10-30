"use client";

/* The team space: what the agents found, and the team working on it together. The Scout investigates every
   measure that adds up its rows (latest month against the month before and a year before); the Skeptic tries
   to knock each finding down before anyone sees it. The team claims a finding, talks it through (@skeptic
   has it checked again), and marks what was done. A finding's link opens the same finding, with its thread,
   for whoever has it; the lens sets how deep it reads (a leader: the answer; an analyst: every step). */

import { Check, CircleAlert, Link2, Loader2, RefreshCw, Rows3, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { InvestigationExhibit, headline } from "@/components/pbi-investigation";
import type { DeskActions, ExhibitData } from "@/components/pbi-exhibit";
import { ResultTable, num } from "@/components/pbi";
import { pbi, type Finding, type RowsBehind, type Team } from "@/lib/pbi";
import { useStudio } from "@/lib/studio";
import { MentionBox, type Mention } from "@/components/mention";
import { SettingsMenu, useLook } from "@/lib/theme";

const STATUS: Record<Finding["status"], { label: string; cls: string }> = {
  new: { label: "New", cls: "bg-[var(--d-rise-soft)] text-[var(--d-rise-ink)]" },
  claimed: { label: "Claimed", cls: "bg-[var(--d-teal-soft)] text-[var(--d-teal)]" },
  acted: { label: "Acted on", cls: "bg-[var(--d-paper)] text-[var(--d-ink)]" },
  resolved: { label: "Resolved", cls: "bg-[var(--d-paper)] text-[var(--d-muted)]" },
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const when = (p: string) => { const m = /^(\d{4})-(\d{2})$/.exec(p); return m ? `${MONTHS[+m[2] - 1]} ${m[1]}` : p; };
const ago = (t: number) => { const s = Date.now() / 1000 - t; return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`; };
const initials = (n: string) => n.split(/\s+/).map((x) => x[0]).join("").slice(0, 2).toUpperCase();
const held = (f: Finding) => f.checks.filter((c) => c.passed).length;

export default function TeamSpace() {
  const { pbiModel } = useStudio();
  const look = useLook();
  const [team, setTeam] = useState<Team | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [lens, setLens] = useState<"leader" | "analyst">("leader");
  const [me, setMe] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {  // browser-only state (the name, the lens, the finding in the link), read once after mount
    /* eslint-disable react-hooks/set-state-in-effect */
    try { setMe(localStorage.getItem("desk-name") ?? ""); setLens((localStorage.getItem("desk-lens") as "leader" | "analyst") ?? "leader"); } catch { /* private window */ }
    setSel(new URLSearchParams(window.location.search).get("f"));
    /* eslint-enable react-hooks/set-state-in-effect */
  }, []);
  useEffect(() => {
    if (!pbiModel) return;
    pbi.team(pbiModel).then(setTeam).catch((e) => setErr(String(e?.message ?? e)));
  }, [pbiModel]);
  const remember = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* fine */ } };
  const findings = team?.findings ?? [];
  const f = findings.find((x) => x.id === sel) ?? findings[0];
  const pick = (id: string) => { setSel(id); window.history.replaceState(null, "", `?f=${id}`); };
  const put = (g: Finding) => setTeam((t) => t && { ...t, findings: t.findings.map((x) => (x.id === g.id ? g : x)) });
  const rescout = async () => { setBusy(true); try { setTeam(await pbi.scout(pbiModel)); } catch (e: any) { setErr(String(e?.message ?? e)); } finally { setBusy(false); } }; // eslint-disable-line @typescript-eslint/no-explicit-any

  const s = team?.scouted;

  return (
    <div className={`desk ${look.look} flex h-screen flex-col bg-[var(--d-paper)] text-[var(--d-ink)]`}>
      <header className="flex h-14 shrink-0 items-center gap-4 border-b border-[var(--d-rule)] bg-[var(--d-sheet)] px-6">
        <span className="cond text-[19px] font-semibold text-[var(--d-ink)]">{team?.model ?? "…"}</span>
        <span className="text-[14px] text-[var(--d-muted)]">Team</span>
        <div className="ml-auto flex items-center gap-3 text-[13px]">
          <div className="flex rounded-full bg-[var(--d-paper)] p-0.5" role="group" aria-label="Lens">
            {(["leader", "analyst"] as const).map((l) => (
              <button key={l} onClick={() => { setLens(l); remember("desk-lens", l); }} aria-pressed={lens === l}
                className={`rounded-full px-3 py-1 ${lens === l ? "bg-[var(--d-navy)] text-white" : "text-[var(--d-ink-2)]"}`}>{l === "leader" ? "Leader" : "Analyst"}</button>
            ))}
          </div>
          <label className="flex items-center gap-2 text-[var(--d-muted)]">You are
            <input value={me} onChange={(e) => { setMe(e.target.value); remember("desk-name", e.target.value); }} placeholder="your name"
              className="w-28 rounded-md border border-[var(--d-rule)] bg-[var(--d-sheet)] px-2 py-1 text-[var(--d-ink)] outline-none focus:border-[var(--d-teal)]" /></label>
          <SettingsMenu look={look} />
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-[400px] shrink-0 flex-col border-r border-[var(--d-rule)] bg-[var(--d-sheet)]">
          <div className="px-6 pb-4 pt-7">
            {!team && !err && <p className="flex items-center gap-2 text-[14px] text-[var(--d-muted)]"><Loader2 size={15} className="animate-spin" />Looking through your numbers…</p>}
            {err && <p className="text-[13px] text-[#9b2c2c]">{err}</p>}
            {team && <>
              <h1 className="cond text-[30px] font-semibold leading-tight text-[var(--d-ink)]">
                {findings.length ? `${findings.length} thing${findings.length === 1 ? "" : "s"} moved` : "Nothing moved"}
              </h1>
              <p className="mt-1.5 text-[13.5px] leading-relaxed text-[var(--d-ink-2)]">
                {s ? <>We checked {s.measures} numbers from your report for {when(s.checked[0]?.now ?? "")}, against the month before and the same month last year.{findings.length ? " Everything else is normal." : ""}</> : null}
              </p>
              <button onClick={rescout} disabled={busy} className="mt-3 flex items-center gap-1.5 text-[12.5px] text-[var(--d-teal)] hover:underline disabled:opacity-50">
                <RefreshCw size={13} className={busy ? "animate-spin" : ""} />{busy ? "Checking…" : `Check again${s ? ` · last checked ${ago(s.at)}` : ""}`}</button>
            </>}
          </div>
          <ul className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 pb-6">
            {findings.map((x) => {
              const h = headline(x.explain), on = x.id === f?.id, last = x.explain.steps[x.explain.steps.length - 1];
              return (
                <li key={x.id}>
                  <button onClick={() => pick(x.id)} className={`w-full rounded-xl border px-4 py-3.5 text-left transition ${on ? "border-[var(--d-teal)] bg-[var(--d-teal-soft)]" : "border-[var(--d-rule)] bg-[var(--d-sheet)] hover:border-[var(--d-slate)]"}`}>
                    <div className="flex items-center gap-2 text-[11.5px]">
                      <span className={`rounded-full px-2 py-0.5 font-semibold ${STATUS[x.status].cls}`}>{STATUS[x.status].label}</span>
                      {x.owner && <span className="text-[var(--d-muted)]">{x.owner}</span>}
                      <span className="ml-auto text-[var(--d-muted)]">{x.comments.length ? `${x.comments.length} ${x.comments.length === 1 ? "reply" : "replies"}` : "Spotted automatically"}</span>
                    </div>
                    <p className="mt-2 text-[15px] font-semibold leading-snug text-[var(--d-ink)]">{h.title}</p>
                    <div className="num mt-2 flex flex-wrap items-center gap-2 text-[12px] text-[var(--d-ink-2)]">
                      <span>{x.label} {last ? `${last.after >= last.before ? "+" : "−"}${num(Math.abs(last.after - last.before))}` : ""}</span>
                      <span aria-hidden>·</span><span>{when(x.before)} → {when(x.now)}</span>
                      <span className={`ml-auto flex items-center gap-1 ${held(x) === x.checks.length ? "text-[var(--d-teal)]" : "text-[var(--d-rise-ink)]"}`}>
                        {held(x) === x.checks.length ? <Check size={12} /> : <CircleAlert size={12} />}{held(x) === x.checks.length ? "Verified" : "Needs a look"}</span>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto">
          {f ? <FindingView key={f.id} f={f} model={pbiModel} lens={lens} me={me} put={put} onScout={() => pbi.team(pbiModel).then(setTeam).catch(() => {})} /> : team && (
            <div className="flex h-full items-center justify-center text-[15px] text-[var(--d-muted)]">Every measure moved within its usual pattern. Nothing to chase.</div>
          )}
        </main>
      </div>
    </div>
  );
}

function FindingView({ f, model, lens, me, put, onScout }: { f: Finding; model: string; lens: "leader" | "analyst"; me: string; put: (f: Finding) => void; onScout: () => void }) {
  const [text, setText] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [rows, setRows] = useState<RowsBehind | null>(null);
  const [copied, setCopied] = useState(false);
  const who = me.trim() || "Someone";
  const people = [...new Set(f.comments.filter((c) => !c.agent).map((c) => c.who))].filter((p) => p !== who && p !== "Someone");
  const mentions: Mention[] = [
    { label: "Fact-checker", hint: "checks this finding again", kind: "assistant" },
    { label: "Investigator", hint: "works out again why it moved, on today's numbers", kind: "assistant" },
    { label: "Scout", hint: "checks every number again for anything else that moved", kind: "assistant" },
    ...people.map((p) => ({ label: p, hint: "in this thread", kind: "person" as const })),
    { label: f.label, hint: "the measure this is about", kind: "measure" },
  ];
  const e: ExhibitData = useMemo(() => ({ kind: "explain", explain: f.explain, columns: [], rows: [], metrics: [f.metric], by: [], filters: {}, labels: {}, origin: "agent" }), [f]);
  const actions: DeskActions = {
    drill: () => {}, ask: () => {},
    rows: async (x) => { setRows(await pbi.rows(model, { metric: f.metric, filters: x.filters, limit: 50 })); setTimeout(() => document.getElementById("rows")?.scrollIntoView({ behavior: "smooth" }), 50); },
  };
  const [oops, setOops] = useState<string | null>(null);
  const post = async () => {
    if (!text.trim()) return;
    try {
      const g = await pbi.comment(model, f.id, who, text);
      put(g); setText(""); setOops(null);
      if (/@scout/i.test(text)) onScout();
    } catch (err: any) { setOops(String(err?.message ?? err)); onScout(); } // eslint-disable-line @typescript-eslint/no-explicit-any
  };
  const status = async (st: Finding["status"], n?: string) => {
    try { put(await pbi.status(model, f.id, who, st, n)); setNote(null); setOops(null); }
    catch (err: any) { setOops(String(err?.message ?? err)); onScout(); } // eslint-disable-line @typescript-eslint/no-explicit-any
  };
  const share = async () => { try { await navigator.clipboard.writeText(window.location.href); setCopied(true); setTimeout(() => setCopied(false), 1600); } catch { /* select it instead */ } };
  const last = f.explain.steps[f.explain.steps.length - 1];
  const total = rows?.totals?.[0]?.value;

  return (
    <div className="mx-auto max-w-[920px] space-y-5 px-8 py-8">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <span className={`rounded-full px-2.5 py-0.5 font-semibold ${STATUS[f.status].cls}`}>{STATUS[f.status].label}{f.owner ? ` · ${f.owner}` : ""}</span>
        {f.also.length > 0 && <span className="text-[var(--d-muted)]">Also shows in {f.also.join(", ")}</span>}
        <span className="ml-auto flex gap-1.5">
          {f.status === "new" && <button onClick={() => status("claimed")} className="rounded-md bg-[var(--d-navy)] px-3 py-1.5 font-medium text-white hover:bg-[var(--d-navy-2)]">I&rsquo;ll take it</button>}
          {f.status === "claimed" && <button onClick={() => setNote("")} className="rounded-md bg-[var(--d-navy)] px-3 py-1.5 font-medium text-white hover:bg-[var(--d-navy-2)]">Mark what was done</button>}
          {f.status === "acted" && <button onClick={() => status("resolved")} className="rounded-md bg-[var(--d-navy)] px-3 py-1.5 font-medium text-white hover:bg-[var(--d-navy-2)]">Resolve</button>}
          <button onClick={share} className="flex items-center gap-1.5 rounded-md border border-[var(--d-rule)] bg-[var(--d-sheet)] px-3 py-1.5 text-[var(--d-ink-2)] hover:border-[var(--d-slate)]">
            <Link2 size={14} />{copied ? "Link copied" : "Share"}</button>
        </span>
      </div>
      {oops && <p className="rounded-lg bg-[var(--d-rise-soft)] px-4 py-2.5 text-[13.5px] text-[var(--d-rise-ink)]">{oops}</p>}
      {note !== null && (
        <form onSubmit={(ev) => { ev.preventDefault(); status("acted", note); }} className="flex gap-2">
          <input autoFocus value={note} onChange={(ev) => setNote(ev.target.value)} placeholder="What was done? e.g. supplier contacted, batch pulled"
            className="min-w-0 flex-1 rounded-md border border-[var(--d-rule)] bg-[var(--d-sheet)] px-3 py-2 text-[14px] outline-none focus:border-[var(--d-teal)]" />
          <button className="rounded-md bg-[var(--d-teal)] px-3 text-[13px] font-medium text-white">Save</button>
        </form>
      )}

      <InvestigationExhibit e={e} n={0} actions={actions} lean={lens === "leader"} />

      <section className="rounded-xl border border-[var(--d-rule)] bg-[var(--d-sheet)] px-6 py-4">
        <h3 className="mb-2.5 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[.06em] text-[var(--d-muted)]"><Sparkles size={13} className="text-[var(--d-teal)]" />Is it real? We checked</h3>
        <ul className="space-y-1.5">
          {f.checks.map((c) => (
            <li key={c.check} className="flex items-start gap-2.5 text-[14px] text-[var(--d-ink-2)]">
              <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full ${c.passed ? "bg-[var(--d-teal-soft)] text-[var(--d-teal)]" : "bg-[var(--d-rise-soft)] text-[var(--d-rise-ink)]"}`} aria-hidden>{c.passed ? <Check size={12} /> : <CircleAlert size={12} />}</span>
              <span>{c.said[0].toUpperCase() + c.said.slice(1)}.</span>
            </li>
          ))}
        </ul>
      </section>

      {rows && (
        <section id="rows" className="rounded-xl border border-[var(--d-rule)] bg-[var(--d-sheet)]">
          <div className="flex items-start gap-2.5 border-b border-[var(--d-rule)] bg-[var(--d-teal-soft)] px-6 py-3 text-[14px] text-[var(--d-ink)]">
            <Rows3 size={16} className="mt-0.5 shrink-0 text-[var(--d-teal)]" aria-hidden />
            <span><b className="num">{num(rows.row_count)}</b> rows make up {f.label} for {f.explain.steps.map((x) => x.value).join(" · ")} in {when(f.now)}. Added up they come to <b className="num">{num(total)}</b>
              {last && total != null && Math.abs(Number(total) - last.after) < 0.5 ? ", exactly the number in the finding." : "."}</span>
          </div>
          <div className="max-h-[360px] overflow-auto px-4 py-3"><ResultTable columns={rows.columns} rows={rows.rows} numeric={[]} labels={{}} /></div>
        </section>
      )}

      <section className="rounded-xl border border-[var(--d-rule)] bg-[var(--d-sheet)] px-6 py-5">
        <h3 className="mb-3 text-[12px] font-semibold uppercase tracking-[.06em] text-[var(--d-muted)]">Thread</h3>
        <ol className="space-y-3.5">
          <Entry who="Scout" agent at={f.created} text={`I spotted this while checking ${f.label} for ${when(f.now)}, traced where it comes from, and checked it ${f.checks.length} ways. ${held(f) === f.checks.length ? "It holds up." : `${held(f)} of the ${f.checks.length} checks hold up, so look at the others before acting.`}`} />
          {f.comments.map((c, i) => <Entry key={i} who={c.who} agent={c.agent} at={c.at} text={c.text} event={c.event} checks={c.checks} />)}
        </ol>
        <form onSubmit={(ev) => { ev.preventDefault(); post(); }} className="mt-4 flex items-end gap-2">
          <MentionBox value={text} onChange={setText} onSubmit={post} options={mentions} rows={2}
            placeholder={`Reply as ${who}. Type @ to bring in an assistant or a teammate.`}
            className="block w-full resize-none rounded-lg border border-[var(--d-rule)] bg-[var(--d-sheet)] px-3 py-2 text-[14px] text-[var(--d-ink)] outline-none focus:border-[var(--d-teal)]" />
          <button disabled={!text.trim()} className="rounded-lg bg-[var(--d-teal)] px-4 py-2 text-[13px] font-medium text-white disabled:opacity-40">Post</button>
        </form>
      </section>
    </div>
  );
}

function Entry({ who, agent, at, text, event, checks }: { who: string; agent?: boolean; at: number; text: string; event?: string; checks?: Finding["checks"] }) {
  if (event) return (
    <li className="flex items-center gap-3 pl-11 text-[13px] text-[var(--d-muted)]">
      <span><b className="font-semibold text-[var(--d-ink-2)]">{who}</b> {text}</span><span className="text-[12px]">· {ago(at)}</span>
    </li>
  );
  return (
    <li className="flex gap-3">
      <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[12px] font-semibold ${agent ? "bg-[var(--d-teal-soft)] text-[var(--d-teal)]" : "bg-[var(--d-paper)] text-[var(--d-ink)]"}`} aria-hidden>
        {agent ? <Sparkles size={14} /> : initials(who)}</span>
      <div className="min-w-0 text-[14px]">
        <div className="flex items-baseline gap-2"><b className="text-[var(--d-ink)]">{who}</b>{agent && <span className="rounded bg-[var(--d-paper)] px-1.5 text-[10.5px] font-semibold uppercase tracking-wide text-[var(--d-muted)]">assistant</span>}<span className="text-[12px] text-[var(--d-muted)]">{ago(at)}</span></div>
        <p className="text-[var(--d-ink-2)]">{text}</p>
        {checks && (
          <ul className="mt-1.5 space-y-1 border-l-2 border-[var(--d-teal-soft)] pl-3">
            {checks.map((c) => (
              <li key={c.check} className="flex items-start gap-1.5 text-[13px] text-[var(--d-ink-2)]">
                {c.passed ? <Check size={13} className="mt-0.5 shrink-0 text-[var(--d-teal)]" /> : <CircleAlert size={13} className="mt-0.5 shrink-0 text-[var(--d-rise-ink)]" />}
                <span>{c.said[0].toUpperCase() + c.said.slice(1)}.</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}
