"use client";

import clsx from "clsx";
import { BarChart3, Database, LayoutDashboard, MessageSquare, Rows3, Sigma } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useStudio } from "@/lib/studio";

const NAV = [
  { href: "/pbi", label: "Analysis desk", icon: MessageSquare, exact: true },
  { href: "/pbi/model", label: "Coverage", icon: LayoutDashboard },
  { href: "/pbi/drill", label: "Drilldown", icon: BarChart3 },
  { href: "/pbi/metrics", label: "Metrics", icon: Sigma },
  { href: "/pbi/explore", label: "Explore rows", icon: Rows3 },
  { href: "/pbi/tables", label: "Tables", icon: Database },
];

export function PbiShell({ children }: { children: React.ReactNode }) {
  const { pbiModels, pbiModel, setPbiModel, error } = useStudio();
  const path = usePathname();
  const current = pbiModels.find((m) => m.id === pbiModel);
  if (path === "/pbi") return <>{children}</>;  // the desk has its own frame
  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center gap-4 bg-[#142338] px-5 text-[#e6edf5]">
        <Link href="/pbi" className="flex w-44 items-center gap-2 text-base font-semibold text-white"><BarChart3 size={18} className="text-[#5fc4bf]" />Context Studio</Link>
        <label className="flex items-center gap-2">
          <span className="sr-only">Power BI model</span>
          <select value={pbiModel} onChange={(e) => setPbiModel(e.target.value)} className="min-h-9 rounded-md border border-[#1d3150] bg-[#1d3150] px-2 text-sm text-white">
            {pbiModels.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </label>
        {current && (
          <span className="truncate text-xs text-[#92a3bb]" title={current.data_error ?? current.warehouse ?? ""}>
            {current.warehouse ? `Data: ${current.warehouse}` : `No data: ${current.data_error ?? "not opened yet"}`}
          </span>
        )}
      </header>
      {error && <div className="border-b border-bad-line bg-bad-soft px-5 py-2 text-sm text-bad">{error}</div>}
      <div className="flex min-h-0 flex-1">
        <nav className="flex w-52 shrink-0 flex-col gap-0.5 border-r border-line px-3 py-4">
          {NAV.map(({ href, label, icon: Icon, exact }) => {
            const on = exact ? path === href : path === href || path?.startsWith(href + "/");
            return (
              <Link key={href} href={href} className={clsx("flex items-center gap-2.5 rounded-md px-3 py-2.5 text-sm", on ? "bg-accent-soft font-semibold text-accent-ink" : "text-ink-2 hover:bg-panel")}>
                <Icon size={16} />{label}
              </Link>
            );
          })}
          {current && (
            <div className="mt-auto px-3 text-[11px] leading-relaxed text-muted">
              {current.name}<br />{current.captured} of {current.measures} measures captured
            </div>
          )}
        </nav>
        <main className="min-w-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
