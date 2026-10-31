// Scheduled briefs: what a person wants a brief on, when (a cron expression), and the editions written so far.

import { API_BASE } from "./api";

export type Edition = { at: number; seconds: number; answer: string; results: Record<string, unknown>[]; error?: string };
export type Schedule = {
  id: string; owner: string; title: string; ask: string; cron: string; tz: string; paused: boolean;
  created: number; last_run: number | null; editions: Edition[]; next_run: string | null; running: boolean;
};

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers: { "content-type": "application/json" }, cache: "no-store" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body?.detail ?? `${res.status}`);
  return body as T;
}
const at = (id: string) => `/api/pbi/${encodeURIComponent(id)}/schedules`;

export const schedules = {
  list: (id: string, owner: string) => call<{ schedules: Schedule[] }>(`${at(id)}?owner=${encodeURIComponent(owner)}`),
  preview: (id: string, cron: string, tz: string) =>
    call<{ ok: boolean; next?: string[]; error?: string }>(`${at(id)}/preview`, { method: "POST", body: JSON.stringify({ cron, tz }) }),
  save: (id: string, owner: string, s: Partial<Schedule>) => call<Schedule>(at(id), { method: "POST", body: JSON.stringify({ ...s, owner }) }),
  remove: (id: string, owner: string, sid: string) => call<{ ok: boolean }>(`${at(id)}/${sid}?owner=${encodeURIComponent(owner)}`, { method: "DELETE" }),
  run: (id: string, owner: string, sid: string) => call<Schedule>(`${at(id)}/${sid}/run`, { method: "POST", body: JSON.stringify({ owner }) }),
};

/** The usual times, as a person would say them. */
export const PRESETS = [
  { label: "Weekdays at 7:00", cron: "0 7 * * 1-5" },
  { label: "Mondays at 7:00", cron: "0 7 * * 1" },
  { label: "1st of the month, 8:00", cron: "0 8 1 * *" },
  { label: "Every hour", cron: "0 * * * *" },
];

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const MACROS: Record<string, string> = { "@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *" };
const NAMED: Record<string, number> = Object.fromEntries([
  ...["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"].map((d, i) => [d, i]),
  ...["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"].map((m, i) => [m, i + 1])]);

/** A cron expression in words ("Mondays at 7:00 AM"), or null when it is not a simple one; the server has the last word. */
export function inWords(text: string): string | null {
  const f = (MACROS[text.trim().toLowerCase()] ?? text).trim().split(/\s+/);
  if (f.length !== 5) return null;
  const [mi, h, dom, mon, dow] = f;
  const n = (v: string) => (/^\d+$/.test(v) ? Number(v) : NAMED[v.toUpperCase()]);
  const list = (v: string, names: string[], base = 0) => {
    const m = v.match(/^(\w+)-(\w+)$/);
    if (m) return `${names[n(m[1]) - base]} to ${names[(n(m[2]) - base) % names.length]}`;
    const parts = v.split(",").map((x) => names[(n(x) - base) % names.length]);
    return parts.includes(undefined as unknown as string) ? null : parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}` : parts[0];
  };
  const clock = (hh: number, mm: number) => new Date(2000, 0, 1, hh, mm).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  let time: string | null = null;
  if (/^\d+$/.test(mi) && /^\d+(,\d+)*$/.test(h)) time = h.split(",").map((x) => clock(Number(x), Number(mi))).join(" and ");
  else if (/^\d+$/.test(mi) && h === "*") time = mi === "0" ? "every hour, on the hour" : `every hour at :${mi.padStart(2, "0")}`;
  else if (/^\*\/\d+$/.test(mi) && h === "*") time = `every ${mi.slice(2)} minutes`;
  if (!time) return null;
  const weekdays = dow === "1-5" || dow.toUpperCase() === "MON-FRI";
  // "Mondays and Thursdays", but "Monday to Wednesday"
  const days = dow === "*" ? null : weekdays ? "weekdays" : dow.includes("-") ? list(dow, DAYS) : list(dow, DAYS)?.replace(/(day)\b/g, "$1s") ?? null;
  if (dow !== "*" && !days) return null;
  const on = dom === "*" ? null : /^\d+(,\d+)*$/.test(dom) ? `the ${dom.split(",").map(ordinal).join(" and ")}` : null;
  if (dom !== "*" && !on) return null;
  const month = mon === "*" ? null : list(mon, MONTHS, 1);
  if (mon !== "*" && !month) return null;
  const when = [on && `${on}${month ? ` of ${month}` : " of the month"}`, !on && month && `in ${month}`, days && (on ? `or ${days}` : days)].filter(Boolean).join(", ");
  const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  return cap(when ? `${when}${time.startsWith("every") ? `, ${time}` : ` at ${time}`}` : time.startsWith("every") ? time : `every day at ${time}`);
}

function ordinal(d: string) {
  const v = Number(d), s = v % 100 >= 11 && v % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][v % 10] ?? "th";
  return `${v}${s}`;
}
