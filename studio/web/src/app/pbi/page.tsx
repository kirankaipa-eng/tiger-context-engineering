"use client";

/* The Power BI desk: a conversation on the left, the analysis it builds on the right, one exhibit per
   answer (the results a question took to get there fold into it, under "How I got here"). Questions go
   to the same agent as `ictx pbi ask` (numbers only from the model's captured measures, or rows of its
   tables); clicking a mark on an exhibit drills in directly, in SQL, with no
   model in between: break it down by another column, see its trend, or open the rows behind it. */

import { ArrowUp, ChevronDown, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PbiExhibit, type DeskActions, type ExhibitData } from "@/components/pbi-exhibit";
import { InvestigationExhibit, headline } from "@/components/pbi-investigation";
import { MentionBox, type Mention } from "@/components/mention";
import { streamNdjson } from "@/lib/api";
import { colLabel, columnTypes, isDimension, pbi, type PbiMetric, type Tile } from "@/lib/pbi";
import { periodName } from "@/components/auto-chart";
import { drillExhibit, drillNote, fold, owner, rowsExhibit, rowsNote, tileOf } from "@/lib/desk";
import { useStudio } from "@/lib/studio";
import { SettingsMenu, useLook } from "@/lib/theme";

/* eslint-disable @typescript-eslint/no-explicit-any */

type Turn = { role: "user" | "analyst"; text: string; steps: string[]; exhibits: number[]; busy?: boolean; error?: string; drill?: boolean };

const STEP: Record<string, string> = {
  list_metrics: "Reading the model's measures", list_columns: "Finding the columns to break it down by",
  column_values: "Matching values to the data", compute: "Computing from the Power BI measure definitions",
  explore: "Reading rows of the model's tables", explain: "Investigating where the change sits",
};

const newSession = () => `desk-${Math.random().toString(36).slice(2, 10)}`;

export default function PbiDesk() {
  const { pbiModel, pbiModels, setPbiModel, llm } = useStudio();
  const look = useLook();
  const model = pbiModels.find((m) => m.id === pbiModel);
  const [session, setSession] = useState(newSession);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [cards, setCards] = useState<ExhibitData[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [metrics, setMetrics] = useState<PbiMetric[]>([]);
  const [cols, setCols] = useState<{ dates: string[]; types: Record<string, string> }>({ dates: [], types: {} });
  const [starters, setStarters] = useState<string[]>([]);
  const [pick, setPick] = useState({ metric: "", by: "" });
  const [pickCols, setPickCols] = useState<string[]>([]);
  const abort = useRef<AbortController | null>(null);
  const railEnd = useRef<HTMLDivElement>(null);
  const count = useRef(0);
  const turnNo = useRef(0);  // which question a result came from
  const [steps, setSteps] = useState<Set<number>>(new Set());  // answers whose working steps are open
  const box = useRef<HTMLTextAreaElement>(null);
  // where the conversation sits: a command bar over the canvas (default), a console under it, or a side rail
  const [layout, setLayout] = useState<"bar" | "console" | "rail">("bar");
  const [history, setHistory] = useState(false);
  const [shut, setShut] = useState(-1);  // the reply caption hidden, until the next turn
  useEffect(() => {
    try { const l = localStorage.getItem("desk-layout"); if (l === "bar" || l === "console" || l === "rail") setLayout(l); } catch { /* private window */ }
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) { e.preventDefault(); box.current?.focus(); }
      if (e.key === "Escape") setHistory(false);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  const asked = useRef(false);
  useEffect(() => {  // arriving with ?q= (a dashboard's "ask about it"): ask it once the model is ready
    const q = new URLSearchParams(window.location.search).get("q");
    if (!q || asked.current || !pbiModel || !llm) return;
    asked.current = true;
    window.history.replaceState(null, "", "/pbi");
    ask(q);
  }, [pbiModel, llm]); // eslint-disable-line react-hooks/exhaustive-deps
  const place = (l: "bar" | "console" | "rail") => { setLayout(l); try { localStorage.setItem("desk-layout", l); } catch { /* fine */ } };

  useEffect(() => { railEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns]);
  const folded = fold(cards);
  const answers = folded.number.size;
  useEffect(() => { if (answers) document.getElementById(`exhibit-${answers}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); }, [answers]);
  /** A turn's exhibit numbers: its answers, each once (a working step points at the answer it led to). */
  const shown = (ids: number[]) => [...new Set(ids.map((id) => folded.number.get(folded.into.get(id - 1) ?? id - 1)).filter((n): n is number => n !== undefined))];

  useEffect(() => {
    if (!pbiModel) return;
    Promise.all([pbi.metrics(pbiModel), pbi.tables(pbiModel)]).then(async ([r, t]) => {
      const ct = columnTypes(t.tables);
      setCols(ct);
      const ok = r.metrics.filter((m) => m.status === "captured").sort((a, b) => a.label.localeCompare(b.label));
      setMetrics(ok);
      const plain = r.metrics.filter((m) => m.status === "captured" && !m.needs?.by?.length && !Object.keys(m.needs?.params ?? {}).length);
      const a = plain[0], b = plain[1];
      const out: string[] = [];
      if (a) {
        const d = await pbi.metric(pbiModel, a.key).catch(() => null);
        const own = new Set(d?.tables ?? []);
        const dims = (d?.columns ?? []).filter((c) => !ct.dates.includes(c) && isDimension(c, ct.types[c]));
        const dim = dims.find((c) => !own.has(c.replace(/\[.*$/, "").replace(/^'|'$/g, ""))) ?? dims[0];
        const date = (d?.columns ?? []).find((c) => ct.dates.includes(c));
        if (date) out.push(`How is ${a.label} trending by month?`);
        if (dim) out.push(`Which ${colLabel(dim).replace(/ · .*$/, "").toLowerCase()} has the highest ${a.label}${b ? `, and what is its ${b.label}` : ""}?`);
        const fact = d?.tables?.[0];
        if (fact) out.push(`Show the 10 largest rows of ${fact}`);
        setPick({ metric: a.key, by: dim ?? "" });
        setPickCols(d?.columns ?? []);
      }
      out.push("What can I ask about this model?");
      setStarters(out);
    }).catch(() => {});
  }, [pbiModel]);

  const last = (fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)));
  /** Posts an exhibit and returns its number. */
  const post = (c: ExhibitData) => {
    count.current += 1;
    setCards((cs) => [...cs, c]);
    return count.current;
  };

  /** An assistant asked by name (@Scout, @Fact-checker): it runs directly, in SQL, no language model involved. */
  const agentTurn = async (note: string, step: string, run: () => Promise<{ text: string; cards: ExhibitData[] }>) => {
    setInput(""); setBusy(true);
    setTurns((ts) => [...ts, { role: "user", text: note, steps: [], exhibits: [] }, { role: "analyst", text: "", steps: [step], exhibits: [], busy: true }]);
    try {
      const r = await run();
      const ns = r.cards.map(post);
      last((t) => ({ ...t, text: r.text, exhibits: ns }));
    } catch (err: any) { last((t) => ({ ...t, error: String(err?.message ?? err) })); } finally { last((t) => ({ ...t, busy: false })); setBusy(false); }
  };

  const ask = async (text: string) => {
    const message = text.trim();
    if (!message || busy || !pbiModel) return;
    const said = message.toLowerCase();
    if (said.includes("@scout")) return agentTurn(message, "The Scout is checking every number", async () => {
      const t = await pbi.scout(pbiModel);
      const s = t.scouted, month = s?.checked[0]?.now ? periodName(s.checked[0].now) : "the latest month";
      return {
        text: `I checked ${s?.measures ?? 0} numbers for ${month}, against the month before and the same month last year. ` +
          (t.findings.length ? `${t.findings.length} moved more than usual: ${t.findings.map((f) => headline(f.explain).title).join("; ")}. They are in the team space too.` : "Nothing moved more than usual."),
        cards: t.findings.map((f) => ({ kind: "explain", explain: f.explain, columns: [], rows: [], metrics: [f.metric], by: [], filters: {}, labels: {}, origin: "agent", question: message })),
      };
    });
    if (said.includes("@fact-checker") || said.includes("@check")) return agentTurn(message, "The fact-checker is checking", async () => {
      const inv = [...cards].reverse().find((c) => c.kind === "explain" && c.explain?.steps.length);
      if (!inv?.explain) return { text: "There is nothing to check yet. Ask why something moved (or @Scout), then @Fact-checker can check what it found.", cards: [] };
      const { checks } = await pbi.check(pbiModel, inv.explain);
      const ok = checks.filter((c) => c.passed).length;
      return { text: `I checked "${headline(inv.explain).title}". ${ok === checks.length ? `It holds up on all ${checks.length} checks` : `${ok} of ${checks.length} checks hold up`}:\n\n${checks.map((c) => `- ${c.passed ? "✓" : "⚠"} ${c.said[0].toUpperCase() + c.said.slice(1)}.`).join("\n")}`, cards: [] };
    });
    if (!llm) { setTurns((ts) => [...ts, { role: "user", text: message, steps: [], exhibits: [] }, { role: "analyst", text: "", steps: [], exhibits: [], error: "No LLM is configured (API_KEY, API_BASE_URL, CHAT_MODEL in .env). Drill into exhibits, or start one from a metric below." }]); return; }
    setInput(""); setBusy(true);
    setTurns((ts) => [...ts, { role: "user", text: message, steps: [], exhibits: [] }, { role: "analyst", text: "", steps: [], exhibits: [], busy: true }]);
    const ctrl = new AbortController();
    abort.current = ctrl;
    const turn = ++turnNo.current;
    try {
      await streamNdjson(pbi.askPath(pbiModel), { message, session_id: session }, (e) => {
        if (e.type === "chunk") last((t) => ({ ...t, text: t.text + (e.data.text ?? "") }));
        else if (e.type === "agent_tool_call" && e.data.status === "started") last((t) => ({ ...t, text: "", steps: STEP[e.data.tool] && !t.steps.includes(STEP[e.data.tool]) ? [...t.steps, STEP[e.data.tool]] : t.steps }));
        else if (e.type === "pbi_result") {
          const d = e.data as any;
          const n = post({ ...d, filters: d.filters ?? {}, origin: "agent", question: message, turn });
          last((t) => ({ ...t, exhibits: [...t.exhibits, n] }));
        } else if (e.type === "error") last((t) => ({ ...t, error: e.data.message }));
        else if (e.type === "result") last((t) => ({ ...t, text: t.text || e.data.answer || "" }));
      }, ctrl.signal);
    } catch (err: any) {
      if (err?.name !== "AbortError") last((t) => ({ ...t, error: String(err?.message ?? err) }));
    } finally {
      last((t) => ({ ...t, busy: false }));
      setBusy(false); abort.current = null;
    }
  };

  const labels = Object.fromEntries(metrics.map((m) => [m.key, m.label]));
  const exhibit = (c: ExhibitData, n: number, landed: boolean) =>
    c.kind === "explain" && c.explain ? <InvestigationExhibit e={c} n={n} actions={actions} landed={landed} />
      : <PbiExhibit e={c} n={n} model={pbiModel} actions={actions} landed={landed} dates={cols.dates} types={cols.types} />;
  /** A drill or a start: computed directly, posted as an exhibit and noted in the conversation. */
  const direct = async (note: string, run: () => Promise<ExhibitData>) => {
    setTurns((ts) => [...ts, { role: "user", text: note, steps: [], exhibits: [], drill: true }, { role: "analyst", text: "", steps: ["Computing in SQL"], exhibits: [], busy: true }]);
    try {
      const c = await run();
      const n = post(c);
      last((t) => ({ ...t, busy: false, steps: [], exhibits: [n], text: c.rows.length ? "" : "No rows match." }));
    } catch (err: any) {
      last((t) => ({ ...t, busy: false, error: String(err?.message ?? err) }));
    }
  };

  const actions: DeskActions = {
    drill: (e, values, by) => direct(drillNote(e, values, by), () => drillExhibit(pbiModel, e, values, by)),
    rows: (e, values) => direct(rowsNote(e, values), () => rowsExhibit(pbiModel, e, values)),
    ask: (text) => ask(text),
    pin: (e) => togglePin(e),
    pinned: (e) => tiles.some((t) => t.id === tileOf(e).id),
  };
  const start = () => {
    if (!pick.metric || !pick.by) return;
    const label = labels[pick.metric] ?? pick.metric;
    direct(`${label} by ${colLabel(pick.by).replace(/ · .*$/, "")}`, async () => {
      const r = await pbi.compute(pbiModel, { metrics: [pick.metric], by: [pick.by] });
      return { kind: "compute", columns: r.columns, rows: r.rows, metrics: [pick.metric], by: [pick.by], filters: {}, sql: r.sql, ms: r.ms, warehouse: r.warehouse, labels, origin: "drill" };
    });
  };
  const reset = () => { abort.current?.abort(); setTurns([]); setCards([]); count.current = 0; setSteps(new Set()); setSession(newSession()); };

  // my dashboard: the questions I pinned (the board re-runs them, live)
  const [tiles, setTiles] = useState<Tile[]>([]);
  useEffect(() => { if (pbiModel) pbi.board(pbiModel, owner()).then((b) => setTiles(b.tiles)).catch(() => {}); }, [pbiModel]);
  const togglePin = async (e: ExhibitData) => {
    const t = tileOf(e);
    const next = tiles.some((x) => x.id === t.id) ? tiles.filter((x) => x.id !== t.id) : [...tiles, t];
    setTiles(next);
    try { setTiles((await pbi.saveBoard(pbiModel, owner(), next)).tiles); } catch { /* kept on screen; saved next time */ }
  };

  // my conversations: each kept in this browser, reopened where it left off
  const [past, setPast] = useState<{ id: string; title: string; at: number; turns: Turn[]; cards: ExhibitData[] }[]>([]);
  const [showPast, setShowPast] = useState(false);
  const store = `desk-history:${pbiModel}`;
  useEffect(() => { try { setPast(JSON.parse(localStorage.getItem(store) ?? "[]")); } catch { setPast([]); } }, [store]);
  useEffect(() => {
    if (busy || !turns.length || !pbiModel) return;
    const title = turns.find((t) => t.role === "user")?.text ?? "Conversation";
    setPast((ps) => {
      const next = [{ id: session, title, at: Date.now() / 1000, turns, cards }, ...ps.filter((p) => p.id !== session)].slice(0, 20);
      try { localStorage.setItem(store, JSON.stringify(next)); } catch { /* full or private: kept for this visit */ }
      return next;
    });
  }, [busy, turns, cards, session, store, pbiModel]);
  const reopen = (p: (typeof past)[number]) => {
    abort.current?.abort(); setTurns(p.turns); setCards(p.cards); count.current = p.cards.length; setSteps(new Set());
    turnNo.current = Math.max(0, ...p.cards.map((c) => c.turn ?? 0)); setSession(p.id); setShowPast(false);
  };

  const latest = [...turns].reverse().find((t) => t.role !== "user");
  const mentions: Mention[] = [
    { label: "Scout", hint: "checks every number and brings back what moved", kind: "assistant" },
    { label: "Investigator", hint: "works out why something moved", kind: "assistant" },
    { label: "Fact-checker", hint: "checks the latest finding", kind: "assistant" },
    ...metrics.map((m) => ({ label: m.label, hint: "a measure in your report", kind: "measure" as const })),
  ];
  const intro = (
              <div>
                <h1 className="cond text-[30px] font-semibold leading-tight text-white">Ask about {model?.name ?? "the model"}.</h1>
                <p className="mt-3 text-[14px] leading-relaxed text-[var(--d-rail-muted)]">
                  Every number is computed from the Power BI model&rsquo;s own measure definitions, translated to SQL ({model ? `${model.captured} of ${model.measures} so far` : "…"}), or from rows of its tables when no measure answers. Click anything on an exhibit to drill in.
                </p>
                <div className="mt-6 space-y-2">
                  {starters.map((s) => (
                    <button key={s} onClick={() => ask(s)} className="block w-full rounded-lg border border-[var(--d-navy-2)] bg-[var(--d-navy-2)] px-4 py-3 text-left text-[14px] text-[var(--d-rail-ink)] hover:border-[var(--d-teal)]">{s}</button>
                  ))}
                </div>
                <div className="mt-6 rounded-lg border border-[var(--d-navy-2)] p-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[.08em] text-[var(--d-rail-muted)]">Or start from a measure</div>
                  <select value={pick.metric} onChange={async (e) => {
                    const k = e.target.value;
                    const d = await pbi.metric(pbiModel, k).catch(() => null);
                    const dims = (d?.columns ?? []).filter((c) => !cols.dates.includes(c) && isDimension(c, cols.types[c]));
                    setPickCols(d?.columns ?? []);
                    setPick({ metric: k, by: d?.needs?.by?.[0] ?? dims[0] ?? "" });
                  }} className="mt-2 w-full rounded-md bg-[var(--d-navy-2)] px-2 py-2 text-[13px] text-white">
                    {metrics.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
                  </select>
                  <div className="mt-2 flex gap-2">
                    <select value={pick.by} onChange={(e) => setPick({ ...pick, by: e.target.value })} className="min-w-0 flex-1 rounded-md bg-[var(--d-navy-2)] px-2 py-2 text-[13px] text-white">
                      {pickCols.filter((c) => !cols.dates.includes(c) && isDimension(c, cols.types[c])).map((c) => <option key={c} value={c}>by {colLabel(c)}</option>)}
                      {pickCols.filter((c) => cols.dates.includes(c)).map((c) => <option key={c} value={`${c}:month`}>by {colLabel(c)} (month)</option>)}
                    </select>
                    <button onClick={start} disabled={!pick.by} className="rounded-md bg-[var(--d-teal)] px-3 text-[13px] font-semibold text-white disabled:opacity-40">Show</button>
                  </div>
                </div>
              </div>
  );
  const transcript = (
    <>
            {turns.map((t, i) => t.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="max-w-[88%] rounded-2xl rounded-br-sm bg-[var(--d-sheet)] px-4 py-2.5 text-[14px] text-[var(--d-ink)]">
                  {t.drill && <span className="mb-0.5 block text-[11px] font-semibold text-[var(--d-teal)]">Drill-down</span>}
                  {t.text}
                </div>
              </div>
            ) : (
              <div key={i}>
                {t.steps.map((s, j) => (
                  <div key={j} className="mb-1 flex items-center gap-2 text-[12px] text-[var(--d-rail-muted)]">
                    <span className={`inline-block h-1.5 w-1.5 rounded-full bg-[#5fc4bf] ${t.busy && j === t.steps.length - 1 ? "pulse-soft" : ""}`} />{s}
                  </div>
                ))}
                {shown(t.exhibits).length > 0 && (
                  <div className="mb-2 mt-2 flex flex-wrap gap-1.5">
                    {shown(t.exhibits).map((n) => <a key={n} href={`#exhibit-${n}`} className="rounded-full bg-[#0f7b78] px-2.5 py-0.5 text-[12px] font-semibold text-white hover:bg-[#13918d]">Exhibit {n}</a>)}
                  </div>
                )}
                {t.text && <div className="rail-prose"><ReactMarkdown remarkPlugins={[remarkGfm]}>{t.text}</ReactMarkdown></div>}
                {t.busy && !t.text && t.steps.length === 0 && <div className="pulse-soft text-[13px] text-[var(--d-rail-muted)]">Reading the question…</div>}
                {t.error && <div className="mt-2 rounded-md bg-[#4a1f24] px-3 py-2 text-[13px] text-[#ffd9dc]">{t.error}</div>}
              </div>
            ))}
    </>
  );
  const composer = (
          <form onSubmit={(e) => { e.preventDefault(); ask(input); }} className="border-t border-[var(--d-navy-2)] p-4">
            <div className="flex items-end gap-2 rounded-xl bg-[var(--d-navy-2)] p-1.5 focus-within:ring-2 focus-within:ring-[var(--d-teal)]">
              <label className="min-w-0 flex-1">
                <span className="sr-only">Ask a question</span>
                <MentionBox inputRef={box} value={input} onChange={setInput} onSubmit={() => ask(input)} options={mentions}
                  placeholder={turns.length ? "Ask a follow-up" : layout === "rail" ? "Ask about the numbers, or for rows" : "Ask about the numbers, or type @ for a measure   ( / )"}
                  className="block max-h-36 min-h-10 w-full resize-none bg-transparent px-2.5 py-2.5 text-[14px] text-white outline-none placeholder:text-[var(--d-rail-muted)]" />
              </label>
              {busy ? (
                <button type="button" onClick={() => abort.current?.abort()} aria-label="Stop" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--d-sheet)] text-[var(--d-ink)]"><Square size={14} /></button>
              ) : (
                <button type="submit" aria-label="Ask" disabled={!input.trim()} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--d-teal)] text-white disabled:opacity-40"><ArrowUp size={17} /></button>
              )}
            </div>
            <div className="desk-data mt-2 truncate text-[11px] text-[var(--d-rail-muted)]">{model?.warehouse ? `Data: ${model.warehouse}` : `No data: ${model?.data_error ?? "…"}`}</div>
          </form>
  );

  return (
    <div className={`desk ${look.look} flex h-screen flex-col bg-[var(--d-paper)] text-[var(--d-ink)]`}>
      <header className="flex h-14 shrink-0 items-center gap-5 bg-[var(--d-navy)] px-6 text-[var(--d-rail-ink)]">
        <div className="flex items-baseline gap-3">
          <span className="cond text-[19px] font-semibold text-white">{model?.name ?? "…"}</span>
          <span className="text-[14px] text-[var(--d-rail-muted)]">Power BI analysis desk</span>
        </div>
        {pbiModels.length > 1 && (
          <select value={pbiModel} onChange={(e) => { setPbiModel(e.target.value); reset(); }} className="rounded-md border border-[var(--d-navy-2)] bg-[var(--d-navy-2)] px-2 py-1.5 text-[13px] text-white">
            {pbiModels.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        )}
        <span className="ml-auto" />
        <nav className="flex items-center gap-1 text-[13px]">
          <button onClick={() => setShowPast(true)} className="rounded-md px-3 py-1.5 text-[var(--d-rail-ink)] hover:bg-[var(--d-navy-2)]">History</button>
          <button onClick={reset} className="ml-2 rounded-md border border-[var(--d-navy-2)] px-3 py-1.5 text-[var(--d-rail-ink)] hover:bg-[var(--d-navy-2)]">New analysis</button>
        </nav>
        <SettingsMenu look={look} layout={layout} setLayout={place} onDark />
      </header>

      <div className="relative flex min-h-0 flex-1">
        {layout === "rail" && (
          <aside className="flex w-[420px] shrink-0 flex-col bg-[var(--d-navy)]">
            <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-6">
              {turns.length === 0 && intro}
              {transcript}
              <div ref={railEnd} />
            </div>
            {composer}
          </aside>
        )}
        <div className="flex min-w-0 flex-1 flex-col">
          <main className={`min-h-0 min-w-0 flex-1 overflow-y-auto ${layout === "bar" ? "pb-44" : ""} ${history ? "pr-[440px]" : ""}`}>
            {cards.length === 0 ? (layout === "rail" ? (
            <div className="flex h-full items-center justify-center px-10">
              <div className="max-w-md text-center">
                <svg width="64" height="64" viewBox="0 0 64 64" aria-hidden className="mx-auto">
                  <path d="M10 50h44" stroke="#9aabbf" strokeWidth="2" /><path d="M14 44 26 32l8 6 16-18" stroke="#0f7b78" strokeWidth="3" fill="none" /><circle cx="50" cy="20" r="4" fill="#c97c06" />
                </svg>
                <p className="cond mt-4 text-[22px] font-semibold text-[var(--d-ink)]">Your analysis builds here, one exhibit per answer.</p>
                <p className="mt-2 text-[14px] text-[var(--d-muted)]">Each exhibit says which Power BI measure it used and shows the SQL behind it, so you can check it against the report.</p>
              </div>
            </div>
            ) : (
              <div className="flex min-h-full items-center justify-center px-8 py-10">
                <div className="w-full max-w-xl rounded-2xl bg-[var(--d-navy)] p-8 shadow-[0_24px_60px_-30px_rgba(20,35,56,.6)]">{intro}</div>
              </div>
            )) : (
            <div className="relative mx-auto max-w-[1180px] px-8 py-8">
              <div className="absolute bottom-8 left-[52px] top-12 w-0.5 bg-[var(--d-rule)]" aria-hidden />
              {cards.map((c, i) => {
                const n = folded.number.get(i);
                if (n === undefined) return null;  // a working step: under its answer
                const before = cards.flatMap((d, k) => (folded.into.get(k) === i ? [d] : []));
                const open = steps.has(i);
                return (
                <div key={i} className="relative mb-8 grid grid-cols-[48px_minmax(0,1fr)] gap-4">
                  <div className="relative pt-5">
                    <span className="num relative z-10 flex h-9 w-9 items-center justify-center rounded-full border-2 border-[var(--d-teal)] bg-[var(--d-sheet)] text-[15px] font-semibold text-[var(--d-teal)]">{n}</span>
                  </div>
                  <div className="min-w-0">
                    {exhibit(c, n, n === answers)}
                    {before.length > 0 && (
                      <div className="mt-2">
                        <button onClick={() => setSteps((s) => { const x = new Set(s); if (open) x.delete(i); else x.add(i); return x; })} aria-expanded={open}
                          className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[12.5px] text-[var(--d-ink-2)] hover:bg-[var(--d-sheet)] hover:text-[var(--d-ink)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--d-teal)]">
                          <ChevronDown size={14} className={`transition-transform ${open ? "" : "-rotate-90"}`} />
                          How I got here: {before.length} earlier result{before.length === 1 ? "" : "s"}
                        </button>
                        {open && (
                          <ol className="mt-2 space-y-4 border-l border-[var(--d-rule)] pl-5">
                            {before.map((d, k) => (
                              <li key={k}>{exhibit(d, 0, false)}</li>
                            ))}
                          </ol>
                        )}
                      </div>
                    )}
                  </div>
                </div>
                );
              })}
            </div>
            )}
          </main>
          {layout === "console" && (
            <section className="flex h-[250px] shrink-0 flex-col border-t border-[var(--d-navy-2)] bg-[#0b1422]" aria-label="Console">
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-6 py-3 font-mono text-[12.5px] [&_.rail-prose]:text-[12.5px]">
                {turns.length === 0 && <p className="text-[var(--d-rail-muted)]">› ask about the numbers, or press / to focus</p>}
                {transcript}
                <div ref={railEnd} />
              </div>
              <div className="font-mono">{composer}</div>
            </section>
          )}
        </div>
        {layout === "bar" && (
          <div className={`pointer-events-none absolute bottom-5 left-0 flex justify-center px-6 ${history ? "right-[440px]" : "right-0"}`}>
            <div className="pointer-events-auto w-full max-w-[760px]">
              {latest && (latest.busy || latest.text || latest.error) && shut !== turns.length && !history && (
                <div className="mb-2 rounded-2xl border border-[var(--d-rule)] bg-[var(--d-sheet)] px-5 py-3 shadow-[0_12px_40px_-20px_rgba(20,35,56,.5)] backdrop-blur">
                  {latest.busy && !latest.text ? (
                    <p className="flex items-center gap-2 text-[13px] text-[var(--d-ink-2)]"><span className="pulse-soft inline-block h-1.5 w-1.5 rounded-full bg-[var(--d-teal)]" />{latest.steps[latest.steps.length - 1] ?? "Reading the question…"}</p>
                  ) : latest.error ? <p className="text-[13px] text-[#9b2c2c]">{latest.error}</p> : (
                    <div className="line-clamp-3 text-[14px] leading-relaxed text-[var(--d-ink)] [&_p]:inline"><ReactMarkdown remarkPlugins={[remarkGfm]}>{latest.text}</ReactMarkdown></div>
                  )}
                  <div className="mt-1.5 flex gap-3 text-[12px] text-[var(--d-muted)]">
                    {shown(latest.exhibits).map((n) => <a key={n} href={`#exhibit-${n}`} className="font-semibold text-[var(--d-teal)] hover:underline">Exhibit {n}</a>)}
                    <button onClick={() => setHistory(true)} className="ml-auto hover:text-[var(--d-ink)]">Conversation ({turns.filter((t) => t.role === "user").length})</button>
                    {!latest.busy && <button onClick={() => setShut(turns.length)} aria-label="Hide the reply" className="hover:text-[var(--d-ink)]">Hide</button>}
                  </div>
                </div>
              )}
              <div className="overflow-hidden rounded-2xl bg-[var(--d-navy)] shadow-[0_24px_60px_-24px_rgba(20,35,56,.7)] [&_.desk-data]:hidden [&_form]:border-0 [&_form]:p-2.5">{composer}</div>
            </div>
          </div>
        )}
        {showPast && (
          <div className="absolute inset-y-0 right-0 z-30 flex w-[400px] max-w-full flex-col border-l border-[var(--d-rule)] bg-[var(--d-sheet)] text-[var(--d-ink)] shadow-2xl">
            <div className="flex items-center justify-between px-6 pb-2 pt-5"><span className="text-[15px] font-semibold">Your conversations</span>
              <button onClick={() => setShowPast(false)} className="rounded px-2 py-1 text-[13px] text-[var(--d-muted)] hover:bg-[var(--d-paper)]">Close</button></div>
            <p className="px-6 text-[12px] text-[var(--d-muted)]">Kept in this browser. Open one to see its answers and carry on.</p>
            <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto px-3 py-3">
              {past.length === 0 && <li className="px-3 py-2 text-[13px] text-[var(--d-muted)]">Nothing yet. Your questions will be kept here.</li>}
              {past.map((p) => (
                <li key={p.id}>
                  <button onClick={() => reopen(p)} className={`w-full rounded-lg px-3 py-2.5 text-left hover:bg-[var(--d-paper)] ${p.id === session ? "bg-[var(--d-paper)]" : ""}`}>
                    <span className="line-clamp-2 text-[14px] font-medium">{p.title}</span>
                    <span className="mt-0.5 block text-[12px] text-[var(--d-muted)]">{fold(p.cards).number.size} answer{fold(p.cards).number.size === 1 ? "" : "s"} · {new Date(p.at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {history && (
          <div className="absolute inset-y-0 right-0 z-20 flex w-[440px] max-w-full flex-col bg-[var(--d-navy)] shadow-2xl">
            <div className="flex items-center justify-between px-6 pt-5 text-[13px] text-[var(--d-rail-muted)]">Conversation
              <button onClick={() => setHistory(false)} className="rounded px-2 py-1 hover:bg-white/10">Close</button></div>
            <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-4">{transcript}</div>
          </div>
        )}
      </div>
    </div>
  );
}
