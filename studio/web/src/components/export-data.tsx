"use client";

/* The numbers behind a chart, as a CSV to open in Excel: what is on screen, under the filters it is drawn with, with the
   measures' names as the page shows them. It runs nothing: the rows are already here. */

import { Download } from "lucide-react";

type Table = { columns: string[]; rows: unknown[][] };

const cell = (v: unknown): string => {
  if (v === null || v === undefined) return "";
  const s = String(v);
  // text a spreadsheet would run as a formula stays text; a negative number stays a number
  const safe = typeof v === "string" && /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
  return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
};

/** "Calendar[Date]:month" -> "Month", "Member[Plan]" -> "Plan": as a person reads a heading. */
const heading = (c: string, labels: Record<string, string>): string => {
  if (labels[c]) return labels[c];
  const grain = c.match(/:(\w+)$/)?.[1], name = grain ?? c.match(/\[([^\]]+)\]/)?.[1] ?? c;
  return name.charAt(0).toUpperCase() + name.slice(1);
};

/** Several tables (a chart and the series beside it) are blocks one under the other. */
export const csvOf = (tables: Table[], labels: Record<string, string> = {}): string =>
  tables.map((t) => [t.columns.map((c) => cell(heading(c, labels))).join(","), ...t.rows.map((r) => r.map(cell).join(","))].join("\r\n")).join("\r\n\r\n");

export const fileName = (title: string): string =>
  `${title.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "").slice(0, 80).toLowerCase() || "data"}.csv`;

/** Held back for now: NEXT_PUBLIC_DESK_EXPORT=on shows it. */
const SHOWN = process.env.NEXT_PUBLIC_DESK_EXPORT === "on";

export function ExportData({ title, tables, labels }: { title: string; tables: Table[] | null | undefined; labels?: Record<string, string> }) {
  if (!SHOWN) return null;
  const ready = !!tables?.some((t) => t.rows.length);
  const save = () => {
    // the mark at the start tells Excel this is UTF-8, so names with accents survive
    const url = URL.createObjectURL(new Blob(["﻿", csvOf(tables!, labels)], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url; a.download = fileName(title);
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const say = ready ? "Export this data as CSV" : "No data to export yet";
  return (
    <button type="button" onClick={save} disabled={!ready} title={say} aria-label={say}
      className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-[var(--d-ink-2)] ring-1 ring-[var(--d-rule)] hover:text-[var(--d-teal)] hover:ring-[var(--d-teal)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:text-[var(--d-ink-2)] disabled:hover:ring-[var(--d-rule)]">
      <Download size={14} />
    </button>
  );
}
