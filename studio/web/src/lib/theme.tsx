"use client";

/* How the desk looks, chosen in one settings menu: the theme (Tiger Analytics, the solution's own brand and
   the default; the client's, HCSC; or neutral), light, dark or the system's mode, and, on the desk, where
   the conversation sits. An install sets the default theme with NEXT_PUBLIC_DESK_THEME; each person's
   choices are remembered in their browser. */

import { Check, Settings } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export const THEMES = [
  { key: "tiger", label: "Tiger Analytics", swatch: ["#f37820", "#141414", "#00a4db"] },
  { key: "hcsc", label: "HCSC", swatch: ["#003333", "#90cbff", "#628181"] },
  { key: "neutral", label: "Neutral", swatch: ["#1d1d1f", "#0a66c2", "#c7c7cc"] },
] as const;
export type Theme = (typeof THEMES)[number]["key"];
export type Mode = "light" | "dark" | "system";
export type Layout = "bar" | "console" | "rail";

const fallback = (THEMES.find((t) => t.key === process.env.NEXT_PUBLIC_DESK_THEME)?.key ?? "tiger") as Theme;
const read = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const write = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* private window */ } };

/** The theme and mode, and the class names that apply them (`desk theme-tiger mode-dark`). */
export function useLook() {
  const [theme, setTheme] = useState<Theme>(fallback);
  const [mode, setMode] = useState<Mode>("light");
  const [sysDark, setSysDark] = useState(false);
  useEffect(() => {  // browser-only state, read once after mount
    /* eslint-disable react-hooks/set-state-in-effect */
    const t = read("desk-theme"), m = read("desk-mode");
    if (THEMES.some((x) => x.key === t)) setTheme(t as Theme);
    if (m === "light" || m === "dark" || m === "system") setMode(m);
    const q = window.matchMedia?.("(prefers-color-scheme: dark)");
    setSysDark(!!q?.matches);
    /* eslint-enable react-hooks/set-state-in-effect */
    const on = (e: MediaQueryListEvent) => setSysDark(e.matches);
    q?.addEventListener?.("change", on);
    return () => q?.removeEventListener?.("change", on);
  }, []);
  const dark = mode === "dark" || (mode === "system" && sysDark);
  return {
    theme, mode, dark, look: `theme-${theme} ${dark ? "mode-dark" : "mode-light"}`,
    setTheme: (t: Theme) => { setTheme(t); write("desk-theme", t); },
    setMode: (m: Mode) => { setMode(m); write("desk-mode", m); },
  };
}

/** The gear and its menu. `layout` only where the page has a conversation (the desk). */
export function SettingsMenu({ look, layout, setLayout, onDark }: {
  look: ReturnType<typeof useLook>; layout?: Layout; setLayout?: (l: Layout) => void; onDark?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  const seg = (on: boolean) => `flex-1 rounded-md px-2 py-1.5 text-[12.5px] ${on ? "bg-[var(--d-sheet)] font-semibold text-[var(--d-ink)] shadow-sm" : "text-[var(--d-muted)] hover:text-[var(--d-ink)]"}`;
  return (
    <div ref={box} className="relative">
      <button onClick={() => setOpen((x) => !x)} aria-label="Settings" aria-expanded={open}
        className={`flex h-9 w-9 items-center justify-center rounded-full transition ${onDark ? "text-[var(--d-rail-ink)] hover:bg-[var(--d-navy-2)]" : "text-[var(--d-ink-2)] hover:bg-[var(--d-paper)]"}`}>
        <Settings size={18} className={open ? "rotate-45 transition-transform" : "transition-transform"} />
      </button>
      {open && (
        <div role="dialog" aria-label="Settings" className="absolute right-0 top-11 z-50 w-[300px] rounded-2xl border border-[var(--d-rule)] bg-[var(--d-sheet)] p-4 text-[var(--d-ink)] shadow-[0_24px_60px_-20px_rgba(0,0,0,.45)]">
          <div className="text-[11px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">Theme</div>
          <div className="mt-2 space-y-1">
            {THEMES.map((t) => (
              <button key={t.key} onClick={() => look.setTheme(t.key)}
                className={`flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-[13.5px] ${look.theme === t.key ? "bg-[var(--d-paper)]" : "hover:bg-[var(--d-paper)]"}`}>
                <span className="flex -space-x-1" aria-hidden>{t.swatch.map((c) => <span key={c} className="h-4 w-4 rounded-full border-2 border-[var(--d-sheet)]" style={{ background: c }} />)}</span>
                <span className="flex-1">{t.label}</span>
                {look.theme === t.key && <Check size={15} className="text-[var(--d-teal)]" />}
              </button>
            ))}
          </div>
          <div className="mt-4 text-[11px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">Appearance</div>
          <div className="mt-2 flex gap-1 rounded-lg bg-[var(--d-paper)] p-1">
            {(["light", "dark", "system"] as const).map((m) => <button key={m} onClick={() => look.setMode(m)} className={seg(look.mode === m)}>{m[0].toUpperCase() + m.slice(1)}</button>)}
          </div>
          {layout && setLayout && <>
            <div className="mt-4 text-[11px] font-semibold uppercase tracking-[.08em] text-[var(--d-muted)]">Conversation</div>
            <div className="mt-2 flex gap-1 rounded-lg bg-[var(--d-paper)] p-1">
              {([["bar", "Command bar"], ["console", "Console"], ["rail", "Side rail"]] as const).map(([k, l]) => <button key={k} onClick={() => setLayout(k)} className={seg(layout === k)}>{l}</button>)}
            </div>
          </>}
        </div>
      )}
    </div>
  );
}
