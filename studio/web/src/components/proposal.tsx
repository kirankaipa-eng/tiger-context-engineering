"use client";

import clsx from "clsx";
import { Check, Clock, Loader2, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type Proposal } from "@/lib/api";
import { personName, useStudio } from "@/lib/studio";
import { Box, Button, ErrorNote, Loading, Pill, StatusPill, when } from "./ui";

function opSentence(op: Record<string, any>): string { // eslint-disable-line @typescript-eslint/no-explicit-any
  switch (op.op) {
    case "add_enum_value": return `Add “${op.value}” to ${op.enum} (${op.layer === "client" ? "this client only" : "industry pack"})`;
    case "set_label": return `Call ${op.name} “${op.label}”`;
    case "set_definition": return `Write the business definition of ${op.concept}`;
    case "set_metric": return `Change metric ${op.metric}${op.client ? " for this client" : " in the industry pack"}: ${Object.keys(op.fields ?? {}).join(", ")}`;
    case "set_mapping": return `Map ${op.unit} ${op.entity}.${op.slot} from ${op.spec?.column ?? "an expression"}`;
    case "write_file": return `Write ${op.path}`;
    default: return String(op.op);
  }
}

export function ProposalDetail({ id, onChanged }: { id: string; onChanged?: () => void }) {
  const { user, people, setScreen, setPanelOpen, bump, refreshCounts, version } = useStudio();
  const [p, setP] = useState<Proposal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [mode, setMode] = useState<"none" | "changes" | "comment" | "answer">("none");
  const [showDiff, setShowDiff] = useState(false);

  useEffect(() => {
    let live = true;
    const load = () => api.proposal(id).then((r) => { if (live) { setP(r); } }).catch((e) => live && setError(e.message));
    load();
    const t = setInterval(() => { if (p?.status === "checking" || p?.checks?.running) load(); }, 2000);
    return () => { live = false; clearInterval(t); };
  }, [id, user, version, p?.status, p?.checks?.running]);

  useEffect(() => {
    if (p) setScreen({ screen: "proposal", proposal: p.id, title: p.title, status: p.status, kind: p.kind });
  }, [p, setScreen]);

  if (error) return <div className="p-6"><ErrorNote error={error} /></div>;
  if (!p) return <div className="p-6"><Loading what="Opening the proposal" /></div>;

  const act = async (fn: () => Promise<Proposal>) => {
    setBusy(true); setError(null);
    try { const r = await fn(); setP({ ...p, ...r }); setMode("none"); setNote(""); bump(); refreshCounts(); onChanged?.(); }
    catch (e: any) { setError(e.message); } // eslint-disable-line @typescript-eslint/no-explicit-any
    finally { setBusy(false); }
  };

  const isQuestion = p.kind === "question";
  const checks = p.checks ?? {};
  const changedRows = (p.impact?.rows ?? []).filter((r) => r.changed);
  const approvedAreas = new Set(p.approvals.flatMap((a) => a.areas));

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-7 py-6">
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill status={p.status} />
          {p.areas.map((a) => <Pill key={a}>{p.area_titles?.[a] ?? a}</Pill>)}
          {p.confidence && <Pill>{p.confidence[0].toUpperCase() + p.confidence.slice(1)} confidence</Pill>}
          <span className="text-xs text-muted">{p.author.kind === "curator" ? "Proposed by the curator" : `Proposed by ${p.author.name}`} · {when(p.created_at)}</span>
        </div>
        <div>
          <h1 className="text-2xl font-semibold">{p.title}</h1>
          <p className="mt-1.5 text-sm leading-relaxed text-ink-2">{p.summary}</p>
        </div>

        {p.evidence.length > 0 && (
          <Box title={p.evidence.length > 1 ? `Why: ${p.evidence.length} passages` : "Why: the source"}>
            <div className="space-y-3">
              {p.evidence.map((e, i) => (
                <div key={i}>
                  <blockquote className="border-l-[3px] border-line pl-3 text-sm leading-relaxed">“{e.quote}”</blockquote>
                  <div className="mt-1 text-[13px] text-muted">{e.title ?? e.doc} · page {e.page} · <Link className="text-accent hover:text-accent-ink" href={`/documents?doc=${encodeURIComponent(e.doc)}&page=${e.page}`}>Open the page</Link></div>
                </div>
              ))}
            </div>
          </Box>
        )}

        <div className="grid gap-4 md:grid-cols-2">
          {!isQuestion && (
            <Box title="Proof in the data">
              <div className="text-sm leading-relaxed">{p.data_proof?.summary ?? (p.author.kind === "curator" ? "Not checkable against the data automatically." : "Proposed by a person; checks below cover the data.")}</div>
              {(checks.findings_cleared ?? []).length > 0 && <div className="mt-2"><Pill tone="ok">{checks.findings_cleared!.length} data-quality finding(s) cleared</Pill></div>}
            </Box>
          )}
          <Box title="Who decides" className={isQuestion ? "md:col-span-2" : ""}>
            <div className="space-y-1.5 text-sm">
              {Object.entries(p.reviewers ?? {}).map(([area, owners]) => (
                <div key={area} className="flex items-center gap-2">
                  {approvedAreas.has(area) ? <Check size={15} className="text-ok" /> : <Clock size={15} className="text-muted" />}
                  <span className="font-medium">{p.area_titles?.[area] ?? area}</span>
                  <span className="text-muted">· {owners.map((o) => personName(people, o)).join(", ")}</span>
                </div>
              ))}
              {p.approvals.map((a, i) => <div key={i} className="text-xs text-muted">Approved by {a.name} {when(a.at)}</div>)}
            </div>
          </Box>
        </div>

        {!isQuestion && (
          <div className="space-y-2">
            <div className="lbl">Checks</div>
            <div className="flex flex-wrap gap-2">
              {checks.running && <Pill><Loader2 size={12} className="animate-spin" />Running checks</Pill>}
              {(checks.items ?? []).map((c) => (
                <Pill key={c.id} tone={c.ok ? "ok" : "bad"} title={c.detail}>{c.ok ? <Check size={12} /> : <X size={12} />}{c.label}{c.ok && c.detail ? ` · ${c.detail}` : ""}</Pill>
              ))}
            </div>
            {(checks.items ?? []).filter((c) => !c.ok && c.detail).map((c) => <div key={c.id} className="rounded-md bg-bad-soft px-3 py-2 text-xs text-bad">{c.label}: {c.detail}</div>)}
            {!checks.running && checks.items && (
              changedRows.length === 0 ? (
                <div className="text-[13px] text-muted">Impact: no governed number changes{p.impact?.as_of ? ` at ${p.impact.as_of}` : ""}{(p.impact?.metrics ?? []).length ? ` (checked ${p.impact!.metrics!.length} metric${p.impact!.metrics!.length > 1 ? "s" : ""})` : ""}.</div>
              ) : (
                <Box title={`Impact at ${p.impact?.as_of ?? "the valuation date"}`}>
                  <table className="w-full text-[13px]">
                    <thead><tr className="text-left text-muted"><th className="py-1 font-medium">Metric</th><th className="font-medium">{(p.impact?.by ?? []).join(", ").replace(/_/g, " ")}</th><th className="text-right font-medium">Live today</th><th className="text-right font-medium">With this change</th></tr></thead>
                    <tbody>{changedRows.slice(0, 20).map((r, i) => (
                      <tr key={i} className="border-t border-line-soft"><td className="py-1.5">{r.label}</td><td>{r.group.join(" · ")}</td><td className="text-right tabular-nums">{r.before_fmt}</td><td className="text-right font-semibold tabular-nums">{r.after_fmt}</td></tr>
                    ))}</tbody>
                  </table>
                </Box>
              )
            )}
          </div>
        )}

        {!isQuestion && (p.ops ?? []).length > 0 && (
          <div>
            <button onClick={() => setShowDiff(!showDiff)} className="text-[13px] text-accent hover:text-accent-ink">{showDiff ? "Hide" : "See"} exactly what changes</button>
            {showDiff && (
              <div className="mt-2 space-y-2">
                <ul className="list-disc pl-5 text-sm">{(p.ops ?? []).map((op, i) => <li key={i}>{opSentence(op)}</li>)}</ul>
                {p.diff?.patch && <details><summary className="cursor-pointer text-xs text-muted">Line-by-line (for engineers)</summary><pre className="mt-2 max-h-80 overflow-auto rounded bg-sunk p-3 font-mono text-[11px] leading-relaxed">{p.diff.patch}</pre></details>}
              </div>
            )}
          </div>
        )}

        {p.comments.length > 0 && (
          <div className="space-y-2">
            <div className="lbl">Conversation</div>
            {p.comments.map((c, i) => (
              <div key={i} className={clsx("rounded-lg px-3 py-2 text-sm", c.kind === "request_changes" ? "bg-warn-soft" : c.kind === "answer" ? "bg-ok-soft" : "bg-sunk")}>
                <div className="text-xs text-muted"><b className="text-ink">{c.name ?? c.by}</b> · {c.kind === "request_changes" ? "asked for changes" : c.kind === "answer" ? "answered" : c.kind === "revision" ? "revised" : "commented"} · {when(c.at)}</div>
                <div className="mt-0.5 whitespace-pre-wrap">{c.text}</div>
              </div>
            ))}
          </div>
        )}
        <ErrorNote error={error} />
      </div>

      <div className="border-t border-line-soft bg-panel px-7 py-4">
        {mode !== "none" ? (
          <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (!note.trim()) return;
            if (mode === "changes") act(() => api.requestChanges(p.id, note));
            else if (mode === "answer") act(() => api.answer(p.id, note));
            else act(() => api.comment(p.id, note)); }}>
            <label className="block"><span className="lbl">{mode === "changes" ? "What should change?" : mode === "answer" ? "Your answer" : "Comment"}</span>
              <textarea autoFocus value={note} onChange={(e) => setNote(e.target.value)} rows={3} className="mt-1 w-full rounded-md border border-line p-2 text-sm outline-none focus:border-accent-line" /></label>
            <div className="flex gap-2"><Button primary type="submit" disabled={busy || !note.trim()}>{mode === "changes" ? "Send to the curator" : mode === "answer" ? "Record answer" : "Comment"}</Button><Button onClick={() => setMode("none")}>Cancel</Button></div>
          </form>
        ) : (
          <div className="flex flex-wrap items-center gap-2.5">
            {isQuestion ? (
              p.status === "question" && <Button primary onClick={() => setMode("answer")} disabled={!user}>Answer</Button>
            ) : (
              ["in_review", "approved"].includes(p.status) && <Button primary onClick={() => act(() => api.approve(p.id))} disabled={busy || !p.can_approve} title={p.can_approve ? "" : "Only an owner of a waiting area can approve"}>Approve</Button>
            )}
            {!isQuestion && ["in_review", "checks_failed", "approved"].includes(p.status) && p.can_review && <Button onClick={() => setMode("changes")}>Request changes</Button>}
            <Button onClick={() => { setPanelOpen(true); setScreen({ screen: "proposal", proposal: p.id, title: p.title, status: p.status, kind: p.kind, ask: Date.now() }); }}>Ask the curator</Button>
            <Button onClick={() => setMode("comment")} disabled={!user}>Comment</Button>
            {p.status === "checks_failed" && <Button onClick={() => act(() => api.recheck(p.id))}>Re-run checks</Button>}
            {!["merged", "closed", "answered"].includes(p.status) && <button onClick={() => act(() => api.close(p.id, "Closed in the Studio."))} className="ml-auto text-[13px] text-muted hover:text-bad">Close without changes</button>}
            {p.status === "merged" && <span className="ml-auto text-[13px] text-ok">Live · see it in <Link href="/history" className="underline">History</Link></span>}
          </div>
        )}
      </div>
    </div>
  );
}
