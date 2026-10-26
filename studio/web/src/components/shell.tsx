"use client";

import clsx from "clsx";
import { BookOpen, FlaskConical, GitCompareArrows, History, Inbox, MessageSquare, Network, PanelRightClose, PanelRightOpen, Shapes, Sigma, Table2 } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { useStudio } from "@/lib/studio";
import { ChatThread } from "./chat";

const NAV = [
  { href: "/inbox", label: "Inbox", icon: Inbox, badge: true },
  { href: "/map", label: "Map", icon: Network },
  { href: "/concepts", label: "Concepts", icon: Shapes },
  { href: "/metrics", label: "Metrics", icon: Sigma },
  { href: "/mappings", label: "Mappings", icon: Table2 },
  { href: "/history", label: "History", icon: History },
  { href: "/documents", label: "Documents", icon: BookOpen },
  { href: "/chat", label: "Ask the Curator", icon: MessageSquare },
  { href: "/consistency", label: "Consistency lab", icon: FlaskConical },
];

function suggestionsFor(screen: Record<string, unknown>, line?: string, client?: string): string[] {
  const s = screen.screen;
  if (s === "proposal" && screen.kind === "question") return ["What would it take to answer this?", "Which governed metrics come closest?", "Draft a change that would answer it"];
  if (s === "proposal") return ["Why do you think this?", "What changes if I approve it?", "Who else needs to approve?"];
  if (s === "concept") return [`What does ${screen.label ?? "this concept"} mean here?`, "Which metrics use it?", "Is its data clean?"];
  if (s === "metric") return ["How is this metric built?", "Show it by unit over time", "Who owns this definition?"];
  if (s === "mapping") return ["What does this source table hold?", "Any data-quality issues here?", "What changed recently in this feed?"];
  if (s === "history") return ["What changed since the last quarter close?", "Which change moved a reported number?"];
  if (s === "map") return ["What is waiting for approval on the map?", "Which concepts have the most metrics?"];
  if (client === "plains_health") return ["What's waiting for me?", "IP and OP PMPM by quarter", "Run the PI-25 modifier review"];
  if (line === "health_payer") return ["What's waiting for me?", "Medical PMPM by unit", "Scan our documents for changes"];
  if (line === "life_annuity") return ["What's waiting for me?", "Annuity net flows by product", "Scan our documents for changes"];
  return ["What's waiting for me?", "Loss & ALAE ratio by unit", "Scan our documents for changes"];
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { people, clients, user, setUser, client, setClient, clientInfo, counts, panelOpen, setPanelOpen, screen, error } = useStudio();
  const path = usePathname();
  const router = useRouter();
  const [search, setSearch] = useState("");
  const onChat = path?.startsWith("/chat");
  if (path?.startsWith("/insights")) return <>{children}</>;  // the business view has its own frame

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center gap-4 border-b border-line bg-panel px-5">
        <Link href="/inbox" className="flex w-44 items-center gap-2 text-base font-semibold"><GitCompareArrows size={18} className="text-accent" />Context Studio</Link>
        <label className="flex items-center gap-2">
          <span className="sr-only">Client</span>
          <select value={client} onChange={(e) => setClient(e.target.value)} className="min-h-9 rounded-md border border-line bg-panel px-2 text-sm">
            {clients.map((c) => <option key={c.name} value={c.name}>{c.title} · {c.line === "health_payer" ? "Healthcare" : c.line === "commercial_pc" ? "Commercial P&C" : c.line === "life_annuity" ? "Life & Annuity" : c.line}</option>)}
          </select>
        </label>
        <form className="max-w-lg flex-1" onSubmit={(e) => { e.preventDefault(); if (search.trim()) router.push(`/concepts?q=${encodeURIComponent(search.trim())}`); }}>
          <label><span className="sr-only">Search</span>
            <input value={search} onChange={(e) => setSearch(e.target.value)} type="search" placeholder="Search concepts, metrics, proposals"
              className="w-full rounded-md border border-line bg-sunk px-3 py-2 text-sm outline-none focus:border-accent-line" />
          </label>
        </form>
        <label className="ml-auto flex items-center gap-2 text-sm text-muted">
          Signed in as
          <select value={user} onChange={(e) => setUser(e.target.value)} className="min-h-9 rounded-md border border-line bg-panel px-2 text-sm font-semibold text-ink">
            {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        {!onChat && (
          <button onClick={() => setPanelOpen(!panelOpen)} aria-label={panelOpen ? "Hide the Curator" : "Show the Curator"} title={panelOpen ? "Hide the Curator" : "Show the Curator"}
            className="flex h-9 w-9 items-center justify-center rounded-md text-muted hover:bg-sunk">
            {panelOpen ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}
          </button>
        )}
      </header>
      {error && <div className="border-b border-bad-line bg-bad-soft px-5 py-2 text-sm text-bad">{error}</div>}
      <div className="flex min-h-0 flex-1">
        <nav className="flex w-52 shrink-0 flex-col gap-0.5 border-r border-line px-3 py-4">
          {NAV.map(({ href, label, icon: Icon, badge }) => {
            const on = path === href || path?.startsWith(href + "/");
            return (
              <Link key={href} href={href} className={clsx("flex items-center gap-2.5 rounded-md px-3 py-2.5 text-sm", on ? "bg-accent-soft font-semibold text-accent-ink" : "text-ink-2 hover:bg-panel")}>
                <Icon size={16} />{label}
                {badge && (counts.mine ?? 0) > 0 && <span className="ml-auto rounded-full bg-accent px-2 text-xs text-white">{counts.mine}</span>}
              </Link>
            );
          })}
          <div className="mt-auto px-3 text-[11px] leading-relaxed text-muted">
            {clientInfo && <>{clientInfo.title}<br />{clientInfo.units.length} units · {clientInfo.has_data ? "reference data loaded" : "no reference data"}</>}
          </div>
        </nav>
        <main className="min-w-0 flex-1 overflow-y-auto">{children}</main>
        {!onChat && panelOpen && (
          <aside className="flex w-[360px] shrink-0 flex-col border-l border-line bg-panel">
            <div className="border-b border-line-soft px-4 py-3">
              <div className="text-sm font-semibold">Curator</div>
              <div className="truncate text-xs text-muted">About: {String(screen.title ?? screen.label ?? "this screen")}</div>
            </div>
            <div className="min-h-0 flex-1"><ChatThread scope="panel" compact suggestions={suggestionsFor(screen, clientInfo?.line, client)} placeholder="Ask about what you are looking at" /></div>
          </aside>
        )}
      </div>
    </div>
  );
}
