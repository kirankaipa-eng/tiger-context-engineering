"use client";

import { ArrowUp, Square } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Exhibit } from "@/components/desk/exhibits";
import { streamNdjson } from "@/lib/api";
import { useStudio } from "@/lib/studio";

/* eslint-disable @typescript-eslint/no-explicit-any */

type Turn = { role: "user" | "analyst"; text: string; steps: string[]; exhibits: number[]; busy?: boolean; error?: string; drill?: boolean };

const STEP: Record<string, string> = {
  show_trend: "Charting the trend from the governed definition",
  explain_change: "Separating volume from unit cost, then splitting the change",
  show_breakdown: "Breaking the number down",
  find_outliers: "Comparing each provider with its peers",
  show_evidence: "Collecting the definition, owners, sources and history",
  record_gap: "Sending the question to the owners",
};

const STARTERS: Record<string, string[]> = {
  plains_health: [
    "How is Medicare Advantage inpatient PMPM trending in Illinois?",
    "Which clinical categories cost the most per member in Texas last quarter?",
    "Are any providers billing modifier 25 far more often than their peers?",
    "How is IP PMPM defined? Does it include observation stays?",
  ],
};

const newSession = () => `desk-${Math.random().toString(36).slice(2, 10)}`;

export default function Desk() {
  const { client, setClient, clients, clientInfo, people, user, setUser } = useStudio();
  const [session, setSession] = useState(newSession);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [cards, setCards] = useState<any[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const railEnd = useRef<HTMLDivElement>(null);
  const count = useRef(0);

  useEffect(() => { railEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns]);
  useEffect(() => {
    if (cards.length) document.getElementById(`exhibit-${cards.length}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [cards.length]);
  useEffect(() => {  // the desk opens on the medical economics demo client unless someone picked another
    let picked = "";
    try { picked = window.localStorage.getItem("studio.desk.client") ?? ""; } catch { /* private window */ }
    const want = picked || (clients.some((c) => c.name === "plains_health") ? "plains_health" : "");
    if (want && client && want !== client && clients.some((c) => c.name === want)) setClient(want);
  }, [clients, client]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {  // a business persona by default
    if (people.length && !people.find((p) => p.id === user && p.id.includes("medecon"))) {
      const lead = people.find((p) => p.id.includes("medecon-lead"));
      if (lead) setUser(lead.id);
    }
  }, [people]); // eslint-disable-line react-hooks/exhaustive-deps

  const last = (fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)));

  const ask = async (text: string) => {
    const message = text.trim();
    if (!message || busy || !client) return;
    setInput("");
    setBusy(true);
    const drill = message.startsWith("Drill:");
    setTurns((ts) => [...ts, { role: "user", text: drill ? message.replace(/^Drill:\s*/, "").replace(/\s*\([a-z_]+ = [^)]*\)/g, "").replace(/\s*\(([a-z_]+)\)\./, ".") : message, steps: [], exhibits: [], drill },
      { role: "analyst", text: "", steps: [], exhibits: [], busy: true }]);
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      await streamNdjson("/api/chat", { client, session_id: session, message, mode: "insights" }, (e) => {
        if (e.type === "chunk") last((t) => ({ ...t, text: t.text + (e.data.text ?? "") }));
        // text before a tool call is the model narrating its next step (the step line says it); the answer follows the last call
        else if (e.type === "agent_tool_call" && e.data.status === "started") last((t) => ({ ...t, text: "", steps: STEP[e.data.tool] ? [...t.steps, STEP[e.data.tool]] : t.steps }));
        else if (e.type === "studio_canvas") {
          count.current += 1;
          const n = count.current;
          setCards((cs) => [...cs, e.data]);
          last((t) => ({ ...t, exhibits: [...t.exhibits, n] }));
        } else if (e.type === "error") last((t) => ({ ...t, error: e.data.message }));
        else if (e.type === "result") last((t) => ({ ...t, text: t.text || e.data.answer || "" }));
      }, ctrl.signal);
    } catch (err: any) {
      if (err?.name !== "AbortError") last((t) => ({ ...t, error: String(err?.message ?? err) }));
    } finally {
      last((t) => ({ ...t, busy: false }));
      setBusy(false);
      abort.current = null;
    }
  };

  const reset = () => { abort.current?.abort(); setTurns([]); setCards([]); count.current = 0; setSession(newSession()); };
  const starters = STARTERS[client] ?? ["How is medical PMPM trending?", "Why did it change last quarter?", "How is it defined?"];
  const persona = people.find((p) => p.id === user);

  return (
    <div className="flex h-full flex-col bg-[var(--d-paper)]">
      <header className="flex h-14 shrink-0 items-center gap-6 bg-[var(--d-navy)] px-6 text-[var(--d-rail-ink)]">
        <div className="flex items-baseline gap-3">
          <span className="cond text-[19px] font-semibold text-white">{clientInfo?.title ?? "…"}</span>
          <span className="text-[14px] text-[var(--d-rail-muted)]">Medical economics desk</span>
        </div>
        <label className="ml-auto flex items-center gap-2 text-[13px] text-[var(--d-rail-muted)]">
          <span className="sr-only">Health plan</span>
          <select value={client} onChange={(e) => { try { window.localStorage.setItem("studio.desk.client", e.target.value); } catch { /* private window */ } setClient(e.target.value); reset(); }}
            className="rounded-md border border-[var(--d-navy-2)] bg-[var(--d-navy-2)] px-2 py-1.5 text-[13px] text-white">
            {clients.filter((c) => c.line === "health_payer" && c.has_data).map((c) => <option key={c.name} value={c.name}>{c.title}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-[13px] text-[var(--d-rail-muted)]">
          Asking as
          <select value={user} onChange={(e) => setUser(e.target.value)} className="rounded-md border border-[var(--d-navy-2)] bg-[var(--d-navy-2)] px-2 py-1.5 text-[13px] font-semibold text-white">
            {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <button onClick={reset} className="rounded-md px-3 py-1.5 text-[13px] text-[var(--d-rail-ink)] hover:bg-[var(--d-navy-2)]">New analysis</button>
        <Link href="/inbox" className="rounded-md border border-[var(--d-navy-2)] px-3 py-1.5 text-[13px] text-[var(--d-rail-ink)] hover:bg-[var(--d-navy-2)]">Open Context Studio</Link>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* conversation */}
        <aside className="flex w-[420px] shrink-0 flex-col bg-[var(--d-navy)]">
          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-6">
            {turns.length === 0 && (
              <div>
                <h1 className="cond text-[30px] font-semibold leading-tight text-white">Ask about cost, use and coding.</h1>
                <p className="mt-3 text-[14px] leading-relaxed text-[var(--d-rail-muted)]">Every answer is computed from {clientInfo?.title ?? "the plan"}&rsquo;s governed definitions, the same ones behind its medical economics reports. Click anything on an exhibit to drill in.</p>
                <div className="mt-6 space-y-2">
                  {starters.map((s) => (
                    <button key={s} onClick={() => ask(s)} className="block w-full rounded-lg border border-[var(--d-navy-2)] bg-[var(--d-navy-2)] px-4 py-3 text-left text-[14px] text-[var(--d-rail-ink)] hover:border-[var(--d-teal)]">{s}</button>
                  ))}
                </div>
              </div>
            )}
            {turns.map((t, i) => t.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="max-w-[88%] rounded-2xl rounded-br-sm bg-white px-4 py-2.5 text-[14px] text-[var(--d-navy)]">
                  {t.drill && <span className="mb-0.5 block text-[11px] font-semibold text-[var(--d-teal)]">Drill-down</span>}
                  {t.text}
                </div>
              </div>
            ) : (
              <div key={i}>
                {t.steps.map((s, j) => (
                  <div key={j} className="mb-1 flex items-center gap-2 text-[12px] text-[var(--d-rail-muted)]">
                    <span className={`inline-block h-1.5 w-1.5 rounded-full ${t.busy && j === t.steps.length - 1 ? "pulse-soft bg-[#5fc4bf]" : "bg-[#5fc4bf]"}`} />{s}
                  </div>
                ))}
                {t.exhibits.length > 0 && (
                  <div className="mb-2 mt-2 flex flex-wrap gap-1.5">
                    {t.exhibits.map((n) => (
                      <a key={n} href={`#exhibit-${n}`} className="rounded-full bg-[#0f7b78] px-2.5 py-0.5 text-[12px] font-semibold text-white hover:bg-[#13918d]">Exhibit {n}</a>
                    ))}
                  </div>
                )}
                {t.text && <div className="rail-prose"><ReactMarkdown remarkPlugins={[remarkGfm]}>{t.text}</ReactMarkdown></div>}
                {t.busy && !t.text && t.steps.length === 0 && <div className="pulse-soft text-[13px] text-[var(--d-rail-muted)]">Reading the question…</div>}
                {t.error && <div className="mt-2 rounded-md bg-[#4a1f24] px-3 py-2 text-[13px] text-[#ffd9dc]">{t.error}</div>}
              </div>
            ))}
            <div ref={railEnd} />
          </div>
          <form onSubmit={(e) => { e.preventDefault(); ask(input); }} className="border-t border-[var(--d-navy-2)] p-4">
            <div className="flex items-end gap-2 rounded-xl bg-[var(--d-navy-2)] p-1.5 focus-within:ring-2 focus-within:ring-[var(--d-teal)]">
              <label className="min-w-0 flex-1">
                <span className="sr-only">Ask a question</span>
                <textarea value={input} onChange={(e) => setInput(e.target.value)} rows={1}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(input); } }}
                  placeholder={turns.length ? "Ask a follow-up" : "Ask a question"} className="block max-h-36 min-h-10 w-full resize-none bg-transparent px-2.5 py-2.5 text-[14px] text-white outline-none placeholder:text-[var(--d-rail-muted)]" />
              </label>
              {busy ? (
                <button type="button" onClick={() => abort.current?.abort()} aria-label="Stop" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white text-[var(--d-navy)]"><Square size={14} /></button>
              ) : (
                <button type="submit" aria-label="Ask" disabled={!input.trim()} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--d-teal)] text-white disabled:opacity-40"><ArrowUp size={17} /></button>
              )}
            </div>
            {persona && <div className="mt-2 text-[11px] text-[var(--d-rail-muted)]">Answers use {persona.name.replace(/^Plains /, "")} permissions. Personal and health details are never shown.</div>}
          </form>
        </aside>

        {/* canvas */}
        <main className="min-w-0 flex-1 overflow-y-auto">
          {cards.length === 0 ? (
            <div className="flex h-full items-center justify-center px-10">
              <div className="max-w-md text-center">
                <svg width="64" height="64" viewBox="0 0 64 64" aria-hidden className="mx-auto">
                  <path d="M10 50h44" stroke="#9aabbf" strokeWidth="2" /><path d="M14 44 26 32l8 6 16-18" stroke="#0f7b78" strokeWidth="3" fill="none" /><circle cx="50" cy="20" r="4" fill="#c97c06" />
                </svg>
                <p className="cond mt-4 text-[22px] font-semibold text-[var(--d-navy)]">Your analysis builds here, one exhibit per question.</p>
                <p className="mt-2 text-[14px] text-[var(--d-muted)]">Each exhibit says which definition it used and when the data was valued, so it matches the plan&rsquo;s reports.</p>
              </div>
            </div>
          ) : (
            <div className="relative mx-auto max-w-[1180px] px-8 py-8">
              <div className="absolute bottom-8 left-[52px] top-12 w-0.5 bg-[var(--d-rule)]" aria-hidden />
              {cards.map((c, i) => (
                <div key={i} className="relative mb-8 grid grid-cols-[48px_1fr] gap-4">
                  <div className="relative pt-5">
                    <span className="num relative z-10 flex h-9 w-9 items-center justify-center rounded-full border-2 border-[var(--d-teal)] bg-white text-[15px] font-semibold text-[var(--d-teal)]">{i + 1}</span>
                  </div>
                  <Exhibit card={c} n={i + 1} onAsk={ask} landed={i === cards.length - 1} />
                </div>
              ))}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
