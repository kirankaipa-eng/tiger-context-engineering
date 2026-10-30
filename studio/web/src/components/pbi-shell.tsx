"use client";

/* Every Power BI page sits beside one sidebar, as in a chat app: a new question at the top, the three
   places (Ask, My briefs, Team), the questions asked recently (reopened where they left off), and at the
   foot what the data covers and who you are. It folds to a rail of icons. The analyst's pages (coverage,
   measures, tables, drilldown, rows) share one frame under "About the data". */

import clsx from "clsx";
import { Database, LayoutGrid, MessageSquareText, PanelLeft, SquarePen, Users, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { owner, readAsked, writeAsked, type Asked } from "@/lib/desk";
import { useStudio } from "@/lib/studio";
import { useLook } from "@/lib/theme";

const OWN_FRAME = ["/pbi", "/pbi/team", "/pbi/board", "/pbi/ask"];
const PLACES = [
  { href: "/pbi/ask", label: "Ask", icon: MessageSquareText },
  { href: "/pbi/board", label: "My briefs", icon: LayoutGrid },
  { href: "/pbi/team", label: "Team", icon: Users },
];
const DATA = [
  { href: "/pbi/model", label: "Coverage" }, { href: "/pbi/metrics", label: "Measures" }, { href: "/pbi/tables", label: "Tables" },
  { href: "/pbi/drill", label: "Drilldown" }, { href: "/pbi/explore", label: "Explore rows" },
];

/** Opens a kept question on the Ask page (or a fresh one, for null): directly when it is showing, else by its address. */
export function openAsked(id: string | null, path: string | null, push: (url: string) => void) {
  if (path === "/pbi/ask") window.dispatchEvent(new CustomEvent("ask:open", { detail: id }));
  else push(id ? `/pbi/ask?c=${encodeURIComponent(id)}` : "/pbi/ask");
}

function Sidebar() {
  const { pbiModels, pbiModel, setPbiModel } = useStudio();
  const look = useLook();
  const path = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(true);
  const [asked, setAsked] = useState<Asked[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [me, setMe] = useState("me");
  useEffect(() => {
    try { if (localStorage.getItem("pbi-sidebar") === "0") setOpen(false); } catch { /* private window */ }  // eslint-disable-line react-hooks/set-state-in-effect
    setMe(owner());
    const on = (e: Event) => setActive((e as CustomEvent).detail ?? null);
    window.addEventListener("ask:showing", on);
    return () => window.removeEventListener("ask:showing", on);
  }, []);
  useEffect(() => {
    if (!pbiModel) return;
    const read = () => setAsked(readAsked(pbiModel));
    read();
    window.addEventListener("ask:history", read);
    return () => window.removeEventListener("ask:history", read);
  }, [pbiModel]);
  const fold = (v: boolean) => { setOpen(v); try { localStorage.setItem("pbi-sidebar", v ? "1" : "0"); } catch { /* fine */ } };
  const on = (href: string) => (href === "/pbi/ask" ? path === href : path === href || path?.startsWith(href + "/"));
  const model = pbiModels.find((m) => m.id === pbiModel);
  const item = "flex items-center gap-3 rounded-lg px-2.5 py-2 text-[14px] text-[var(--d-rail-ink)] hover:bg-[var(--d-navy-2)]";

  if (!open) return (
    <nav aria-label="Main" className={`desk ${look.look} flex w-[60px] shrink-0 flex-col items-center gap-1 bg-[var(--d-navy)] py-3`}>
      <button onClick={() => fold(true)} aria-label="Open the sidebar" className="grid h-10 w-10 place-items-center rounded-lg text-[var(--d-rail-ink)] hover:bg-[var(--d-navy-2)]"><PanelLeft size={18} /></button>
      <button onClick={() => openAsked(null, path, router.push)} aria-label="New question" className="grid h-10 w-10 place-items-center rounded-lg text-[var(--d-rail-ink)] hover:bg-[var(--d-navy-2)]"><SquarePen size={18} /></button>
      {PLACES.map(({ href, label, icon: Icon }) => (
        <Link key={href} href={href} aria-label={label} title={label} className={clsx("grid h-10 w-10 place-items-center rounded-lg text-[var(--d-rail-ink)] hover:bg-[var(--d-navy-2)]", on(href) && "bg-[var(--d-navy-2)]")}><Icon size={18} /></Link>
      ))}
      <Link href="/pbi/model" aria-label="About the data" title="About the data" className="mt-auto grid h-10 w-10 place-items-center rounded-lg text-[var(--d-rail-muted)] hover:bg-[var(--d-navy-2)]"><Database size={18} /></Link>
    </nav>
  );

  return (
    <nav aria-label="Main" className={`desk ${look.look} flex w-[264px] shrink-0 flex-col bg-[var(--d-navy)] text-[var(--d-rail-ink)]`}>
      <div className="flex items-center gap-2 px-3 pb-2 pt-3">
        {pbiModels.length > 1 ? (
          <select value={pbiModel} onChange={(e) => setPbiModel(e.target.value)} aria-label="Power BI model"
            className="min-w-0 flex-1 truncate rounded-lg bg-transparent px-2 py-1.5 text-[15px] font-semibold text-white hover:bg-[var(--d-navy-2)]">
            {pbiModels.map((m) => <option key={m.id} value={m.id} className="text-black">{m.name}</option>)}
          </select>
        ) : <span className="cond min-w-0 flex-1 truncate px-2 text-[16px] font-semibold text-white">{model?.name ?? "…"}</span>}
        <button onClick={() => fold(false)} aria-label="Close the sidebar" className="grid h-9 w-9 place-items-center rounded-lg text-[var(--d-rail-muted)] hover:bg-[var(--d-navy-2)] hover:text-white"><PanelLeft size={18} /></button>
      </div>

      <div className="space-y-0.5 px-2">
        <button onClick={() => openAsked(null, path, router.push)} className={`${item} w-full font-semibold`}><SquarePen size={17} />New question</button>
        {PLACES.slice(1).map(({ href, label, icon: Icon }) => (
          <Link key={href} href={href} className={clsx(item, on(href) && "bg-[var(--d-navy-2)] font-semibold")}><Icon size={17} />{label}</Link>
        ))}
      </div>

      <div className="mt-5 min-h-0 flex-1 overflow-y-auto px-2">
        <div className="px-2.5 pb-1.5 text-[12px] font-semibold text-[var(--d-rail-muted)]">Recent</div>
        {asked.length === 0 && <p className="px-2.5 text-[13px] leading-relaxed text-[var(--d-rail-muted)]">Questions you ask show up here, to pick up where you left off.</p>}
        {asked.map((a) => (
          <div key={a.id} className={clsx("group flex items-center rounded-lg hover:bg-[var(--d-navy-2)]", path === "/pbi/ask" && active === a.id && "bg-[var(--d-navy-2)]")}>
            <button onClick={() => openAsked(a.id, path, router.push)} title={a.title} className="min-w-0 flex-1 truncate px-2.5 py-2 text-left text-[13.5px] text-[var(--d-rail-ink)]">{a.title}</button>
            <button onClick={() => writeAsked(pbiModel, asked.filter((x) => x.id !== a.id))} aria-label={`Remove "${a.title}" from recent`}
              className="mr-1 hidden h-7 w-7 shrink-0 place-items-center rounded-md text-[var(--d-rail-muted)] hover:text-white group-hover:grid group-focus-within:grid"><X size={14} /></button>
          </div>
        ))}
      </div>

      <div className="space-y-0.5 border-t border-[var(--d-navy-2)] px-2 py-2">
        <Link href="/pbi/model" className={clsx(item, DATA.some((d) => on(d.href)) && "bg-[var(--d-navy-2)]")}>
          <Database size={17} className="text-[var(--d-rail-muted)]" />
          <span className="min-w-0 flex-1">About the data<span className="block text-[12px] text-[var(--d-rail-muted)]">{model ? `${model.captured} of ${model.measures} measures translated` : "…"}</span></span>
        </Link>
        <Link href="/pbi" className={clsx(item, "text-[13px] text-[var(--d-rail-muted)]", path === "/pbi" && "bg-[var(--d-navy-2)]")}><span className="w-[17px]" />Classic desk</Link>
        <div className="flex items-center gap-3 px-2.5 py-2">
          <span className="grid h-7 w-7 place-items-center rounded-full bg-[var(--d-teal)] text-[12px] font-semibold uppercase text-white">{(me === "me" ? "You" : me).slice(0, 1)}</span>
          <span className="min-w-0 flex-1 truncate text-[14px] text-[var(--d-rail-ink)]">{me === "me" ? "You" : me}</span>
        </div>
      </div>
    </nav>
  );
}

/** The analyst's pages: what the model holds and how much of it is translated, as tabs. */
function DataFrame({ children }: { children: React.ReactNode }) {
  const { pbiModels, pbiModel, error } = useStudio();
  const path = usePathname();
  const current = pbiModels.find((m) => m.id === pbiModel);
  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <header className="shrink-0 border-b border-line px-6 pt-4">
        <div className="flex items-baseline gap-3">
          <h1 className="text-[18px] font-semibold">About the data</h1>
          {current && (
            <span className="truncate text-xs text-muted" title={current.data_error ?? current.warehouse ?? ""}>
              {current.warehouse ? `Data: ${current.warehouse}` : `No data: ${current.data_error ?? "not opened yet"}`}
            </span>
          )}
        </div>
        <nav className="mt-3 flex gap-1" aria-label="About the data">
          {DATA.map(({ href, label }) => {
            const on = path === href || path?.startsWith(href + "/");
            return <Link key={href} href={href} className={clsx("border-b-2 px-3 pb-2.5 pt-1 text-sm", on ? "border-accent font-semibold text-ink" : "border-transparent text-ink-2 hover:text-ink")}>{label}</Link>;
          })}
        </nav>
      </header>
      {error && <div className="border-b border-bad-line bg-bad-soft px-5 py-2 text-sm text-bad">{error}</div>}
      <main className="min-w-0 flex-1 overflow-y-auto">{children}</main>
    </div>
  );
}

export function PbiShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="min-w-0 flex-1 overflow-hidden">{OWN_FRAME.includes(path ?? "") ? children : <DataFrame>{children}</DataFrame>}</div>
    </div>
  );
}
