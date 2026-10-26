"use client";

import clsx from "clsx";
import { Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ChatThread } from "@/components/chat";
import { when } from "@/components/ui";
import { api } from "@/lib/api";
import { useStudio } from "@/lib/studio";

type Session = { id: string; title: string; turns: number; updated_at: string };
const newId = () => `page-${Math.random().toString(36).slice(2, 10)}`;

export default function ChatPage() {
  const { setScreen, clientInfo, client, user } = useStudio();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [current, setCurrent] = useState<string>(newId);
  useEffect(() => { setScreen({ screen: "chat", title: "Ask the Curator" }); }, [setScreen]);

  const load = useCallback(() => {
    if (client) api.chatSessions(client).then((r) => setSessions(r.sessions)).catch(() => setSessions([]));
  }, [client]);
  useEffect(() => { load(); }, [load, user]);

  const remove = async (id: string) => {
    await api.deleteChatSession(client, id).catch(() => {});
    if (id === current) setCurrent(newId());
    load();
  };

  const health = clientInfo?.line === "health_payer";
  const life = clientInfo?.line === "life_annuity";
  const suggestions = client === "plains_health"
    ? ["What's waiting for me?", "Show IP and OP PMPM by quarter", "Run the PI-25 modifier review for 2025-Q4 and 2026-Q1",
       "Crosswalk our Power BI model"]
    : health ? ["What's waiting for me?", "Show medical PMPM by unit", "How is the medical loss ratio built?", "Scan our documents for changes to the ontology"]
    : life ? ["What's waiting for me?", "Show annuity net flows by product type", "How is 13-month persistency built?", "Scan our documents for changes to the ontology"]
    : ["What's waiting for me?", "Show the loss & ALAE ratio by unit as a chart", "How is ultimate loss ratio built?", "Scan our documents for changes to the ontology"];

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-sunk">
        <div className="p-3">
          <button onClick={() => setCurrent(newId())} className="flex w-full items-center justify-center gap-2 rounded-md border border-[#bdb9ae] bg-panel px-3 py-2 text-sm hover:bg-ground"><Plus size={15} />New conversation</button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {sessions.length === 0 && <div className="px-2 py-4 text-xs text-muted">Your conversations with the Curator appear here.</div>}
          {sessions.map((s) => (
            <div key={s.id} className={clsx("group flex items-start gap-1 rounded-md px-2 py-2", current === s.id ? "bg-panel" : "hover:bg-panel")}>
              <button onClick={() => setCurrent(s.id)} className="min-w-0 flex-1 text-left">
                <div className={clsx("truncate text-[13px]", current === s.id && "font-semibold")}>{s.title}</div>
                <div className="text-[11px] text-muted">{s.turns} message{s.turns === 1 ? "" : "s"} · {when(s.updated_at)}</div>
              </button>
              <button onClick={() => remove(s.id)} aria-label={`Delete ${s.title}`} className="invisible rounded p-1 text-muted hover:text-bad group-hover:visible"><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
      </aside>
      <div className="min-w-0 flex-1">
        <ChatThread key={current} scope="page" session={current} onNewSession={() => setCurrent(newId())} onTurnDone={load} suggestions={suggestions} />
      </div>
    </div>
  );
}
