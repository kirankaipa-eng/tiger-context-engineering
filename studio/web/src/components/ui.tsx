"use client";

import clsx from "clsx";
import Link from "next/link";
import { STATUS_LABEL } from "@/lib/api";

export function Pill({ children, tone = "plain", className, title }: { children: React.ReactNode; tone?: "plain" | "accent" | "ok" | "warn" | "bad"; className?: string; title?: string }) {
  const tones = {
    plain: "border-line bg-sunk text-ink-2",
    accent: "border-accent-line bg-accent-soft text-accent-ink",
    ok: "border-ok-line bg-ok-soft text-ok",
    warn: "border-warn-line bg-warn-soft text-warn",
    bad: "border-bad-line bg-bad-soft text-bad",
  };
  return <span title={title} className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs", tones[tone], className)}>{children}</span>;
}

export function StatusPill({ status }: { status: string }) {
  const tone = status === "merged" || status === "answered" ? "ok"
    : status === "checks_failed" ? "bad"
    : status === "changes_requested" || status === "question" ? "warn"
    : status === "in_review" || status === "approved" ? "accent" : "plain";
  return <Pill tone={tone}>{status === "checking" && <span className="pulse-soft">●</span>}{STATUS_LABEL[status] ?? status}</Pill>;
}

export function Button({ children, onClick, primary, disabled, small, type = "button", title, className }: {
  children: React.ReactNode; onClick?: () => void; primary?: boolean; disabled?: boolean; small?: boolean;
  type?: "button" | "submit"; title?: string; className?: string;
}) {
  return (
    <button type={type} title={title} onClick={onClick} disabled={disabled}
      className={clsx("rounded-md border font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        small ? "min-h-8 px-3 text-[13px]" : "min-h-11 px-4 text-sm",
        primary ? "border-accent bg-accent text-white hover:bg-accent-ink" : "border-[#bdb9ae] bg-panel text-ink hover:bg-sunk",
        className)}>
      {children}
    </button>
  );
}

export function Box({ children, className, title, action }: { children: React.ReactNode; className?: string; title?: string; action?: React.ReactNode }) {
  return (
    <section className={clsx("rounded-lg border border-line bg-panel", className)}>
      {title && <div className="flex items-center gap-3 px-4 pt-3.5"><div className="lbl">{title}</div><div className="ml-auto">{action}</div></div>}
      <div className={title ? "px-4 pb-4 pt-2" : "p-4"}>{children}</div>
    </section>
  );
}

export function Crumbs({ items }: { items: { label: string; href?: string }[] }) {
  return (
    <div className="text-[13px] text-muted">
      {items.map((it, i) => (
        <span key={i}>{i > 0 && " › "}{it.href ? <Link className="text-accent hover:text-accent-ink" href={it.href}>{it.label}</Link> : it.label}</span>
      ))}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line px-4 py-8 text-center text-sm text-muted">{children}</div>;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="pulse-soft px-1 py-6 text-sm text-muted">{what}…</div>;
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="rounded-md border border-bad-line bg-bad-soft px-3 py-2 text-sm text-bad">{error}</div>;
}

export function when(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function fmtValue(v: unknown, format?: string | null) {
  if (v === null || v === undefined) return "—";
  if (typeof v !== "number") return String(v);
  if (format === "percent") return `${(v * 100).toFixed(1)}%`;
  if (format === "currency") {
    const a = Math.abs(v);
    if (a >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
    if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
    if (a >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
    return `$${v.toFixed(0)}`;
  }
  if (format === "ratio") return v.toFixed(3);
  return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}
