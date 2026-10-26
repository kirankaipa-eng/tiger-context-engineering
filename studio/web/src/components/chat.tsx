"use client";

import clsx from "clsx";
import { ArrowUp, Check, ChevronRight, Loader2, Plus, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, streamNdjson } from "@/lib/api";
import { useStudio } from "@/lib/studio";
import { Canvas } from "./canvases";

/* eslint-disable @typescript-eslint/no-explicit-any */

type Step = { tool: string; label: string; done: boolean; summary?: string };
type Turn = { role: "user" | "assistant"; text: string; steps: Step[]; canvases: any[]; status?: string; error?: string; done?: boolean };

const TOOL_LABEL: Record<string, string> = {
  compute_metrics: "Computing governed metrics", show_metrics: "Charting governed metrics",
  list_metrics: "Reading the metric catalog", list_concepts: "Listing concepts", describe_concept: "Looking up a concept",
  run_sql: "Querying the governed views", loss_triangle: "Building a development triangle", data_quality: "Checking data quality",
  pending_changes: "Checking data drift", show_trace: "Tracing how the number is built",
  list_proposals: "Reading proposals", get_proposal: "Reading the proposal", show_proposal: "Opening the proposal",
  propose_change: "Drafting a proposal", revise_proposal: "Revising the proposal", comment_on_proposal: "Recording a comment",
  answer_question: "Recording your answer", approve_proposal: "Recording your approval",
  search_documents: "Searching documents", list_documents: "Listing documents", scan_documents: "Reading documents",
  history: "Reading history", undo_change: "Opening an undo proposal",
};

function sessionKey(client: string, scope: string) { return `studio.chat.${scope}.${client}`; }

function sessionFor(client: string, scope: string, fresh = false): string {
  if (!client || typeof window === "undefined") return "";
  const key = sessionKey(client, scope);
  let id = "";
  try { id = fresh ? "" : window.localStorage.getItem(key) ?? ""; } catch { /* private window */ }
  if (!id) {
    id = `${scope}-${Math.random().toString(36).slice(2, 10)}`;
    try { window.localStorage.setItem(key, id); } catch { /* private window */ }
  }
  return id;
}

export function useChatSession(scope: string) {
  const { client } = useStudio();
  const [fresh, setFresh] = useState<Record<string, string>>({});
  const sessionId = useMemo(() => fresh[client] ?? sessionFor(client, scope), [client, scope, fresh]);
  const reset = useCallback(() => setFresh((f) => ({ ...f, [client]: sessionFor(client, scope, true) })), [client, scope]);
  return { sessionId, reset };
}

export function ChatThread({ scope, compact, suggestions, placeholder, session, onNewSession, onTurnDone }: {
  scope: string; compact?: boolean; suggestions?: string[]; placeholder?: string;
  session?: string; onNewSession?: () => void; onTurnDone?: () => void;
}) {
  const { client, screen, bump, refreshCounts, me } = useStudio();
  const own = useChatSession(scope);
  const sessionId = session ?? own.sessionId;
  const reset = onNewSession ?? own.reset;
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const [threadOf, setThreadOf] = useState(sessionId);
  if (threadOf !== sessionId) { setThreadOf(sessionId); setTurns([]); }  // new conversation or client
  useEffect(() => {
    if (!client || !sessionId) return;
    let live = true;
    api.chatSession(client, sessionId).then((d) => {
      if (!live) return;
      setTurns((now) => now.length ? now : d.turns.flatMap((t) => [
        { role: "user" as const, text: t.user, steps: [], canvases: [] },
        { role: "assistant" as const, text: t.answer, canvases: t.canvases as any[], done: true,
          steps: t.steps.map((tool) => ({ tool, label: TOOL_LABEL[tool] ?? tool.replace(/_/g, " "), done: true })) },
      ]));
    }).catch(() => { /* a new conversation has no transcript yet */ });
    return () => { live = false; };
  }, [client, sessionId]);
  useEffect(() => { if (scope === "panel" && screen.ask) inputRef.current?.focus(); }, [scope, screen.ask]);  // "Ask the curator" on a proposal
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns]);

  const update = (fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)));

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || busy || !sessionId) return;
    setInput("");
    setBusy(true);
    setTurns((ts) => [...ts, { role: "user", text: message, steps: [], canvases: [] },
      { role: "assistant", text: "", steps: [], canvases: [], status: "Thinking" }]);
    const ctrl = new AbortController();
    abort.current = ctrl;
    let wrote = false;
    try {
      await streamNdjson("/api/chat", { client, session_id: sessionId, message, screen }, (e) => {
        if (e.type === "chunk") update((t) => ({ ...t, text: t.text + (e.data.text ?? ""), status: undefined }));
        else if (e.type === "agent_tool_call") {
          const tool = e.data.tool as string;
          if (["propose_change", "revise_proposal", "approve_proposal", "comment_on_proposal", "answer_question", "undo_change", "scan_documents"].includes(tool)) wrote = true;
          // text before a tool call narrates the step the list already shows; the answer follows the last call
          if (e.data.status === "started") update((t) => ({ ...t, text: "", steps: [...t.steps,{ tool, label: TOOL_LABEL[tool] ?? tool.replace(/_/g, " "), done: false }] }));
          else update((t) => {
            const steps = [...t.steps];
            const i = steps.map((s) => s.tool).lastIndexOf(tool);
            if (i >= 0) steps[i] = { ...steps[i], done: true, summary: e.data.summary };
            return { ...t, steps };
          });
        } else if (e.type === "agent_status" && e.data.detail && e.data.detail !== "Analyzing request...") update((t) => ({ ...t, status: e.data.detail }));
        else if (e.type === "studio_canvas") update((t) => ({ ...t, canvases: [...t.canvases, e.data] }));
        else if (e.type === "error") update((t) => ({ ...t, error: e.data.message, status: undefined }));
        else if (e.type === "result") update((t) => ({ ...t, done: true, status: undefined, text: t.text || e.data.answer || "" }));
      }, ctrl.signal);
    } catch (err: any) {
      if (err?.name !== "AbortError") update((t) => ({ ...t, error: String(err?.message ?? err) }));
    } finally {
      update((t) => ({ ...t, done: true, status: undefined }));
      setBusy(false);
      abort.current = null;
      if (wrote) { bump(); refreshCounts(); }
      onTurnDone?.();
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className={clsx("min-h-0 flex-1 overflow-y-auto", compact ? "space-y-3 p-4" : "space-y-5 px-8 py-6")}>
        {turns.length === 0 && (
          <div className={clsx("text-sm text-muted", compact ? "" : "mx-auto max-w-2xl pt-8")}>
            {!compact && <h1 className="mb-2 text-2xl font-semibold text-ink">Ask the Curator</h1>}
            <p className="mb-3">Ask about the business with governed numbers, or about the ontology: what changed, why, and what is waiting for {me ? "you" : "owners"}.</p>
            <div className={clsx("flex flex-col gap-2", !compact && "sm:flex-row sm:flex-wrap")}>
              {(suggestions ?? []).map((s) => (
                <button key={s} onClick={() => send(s)} className="rounded-lg border border-line bg-panel px-3 py-2 text-left text-[13px] text-ink-2 hover:border-accent-line hover:text-accent-ink">{s}</button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t, i) => t.role === "user" ? (
          <div key={i} className="flex justify-end"><div className={clsx("rounded-lg bg-accent-soft px-3 py-2 text-[13px] text-ink", compact ? "max-w-[85%]" : "max-w-2xl")}>{t.text}</div></div>
        ) : (
          <div key={i} className={clsx(!compact && "mx-auto max-w-4xl")}>
            {t.steps.length > 0 && (
              <div className="mb-2 space-y-1">
                {t.steps.map((s, j) => (
                  <details key={j} className="group text-xs text-muted">
                    <summary className="flex cursor-pointer list-none items-center gap-1.5">
                      {s.done ? <Check size={13} className="text-ok" /> : <Loader2 size={13} className="animate-spin" />}
                      {s.label}
                      {s.summary && <ChevronRight size={12} className="transition group-open:rotate-90" />}
                    </summary>
                    {s.summary && <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-sunk p-2 font-mono text-[11px]">{s.summary.slice(0, 1500)}</pre>}
                  </details>
                ))}
              </div>
            )}
            {t.canvases.map((c, j) => <div key={j} className="mb-3"><Canvas data={c} /></div>)}
            {t.text && <div className={clsx("prose-studio", compact ? "rounded-lg bg-ground px-3 py-2 text-[13px]" : "")}><ReactMarkdown remarkPlugins={[remarkGfm]}>{t.text}</ReactMarkdown></div>}
            {t.status && !t.done && <div className="pulse-soft text-xs text-muted">{t.status}…</div>}
            {t.error && <div className="rounded-md border border-bad-line bg-bad-soft px-3 py-2 text-xs text-bad">{t.error}</div>}
          </div>
        ))}
        <div ref={end} />
      </div>
      <form onSubmit={(e) => { e.preventDefault(); send(input); }} className={clsx("border-t border-line-soft", compact ? "p-3" : "px-8 py-4")}>
        <div className={clsx("flex items-end gap-2 rounded-lg border border-line bg-panel p-1.5 focus-within:border-accent-line", !compact && "mx-auto max-w-4xl")}>
          <button type="button" onClick={() => { reset(); setTurns([]); }} title="New conversation" aria-label="New conversation" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-muted hover:bg-sunk"><Plus size={16} /></button>
          <label className="min-w-0 flex-1">
            <span className="sr-only">Message the Curator</span>
            <textarea ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)} rows={1}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
              placeholder={placeholder ?? "Ask the Curator"} className="block max-h-40 min-h-9 w-full resize-none bg-transparent px-1 py-2 text-sm outline-none" />
          </label>
          {busy ? (
            <button type="button" onClick={() => abort.current?.abort()} aria-label="Stop" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-ink text-white"><Square size={14} /></button>
          ) : (
            <button type="submit" aria-label="Send" disabled={!input.trim()} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-accent text-white disabled:opacity-40"><ArrowUp size={16} /></button>
          )}
        </div>
      </form>
    </div>
  );
}
