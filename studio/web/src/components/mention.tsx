"use client";

/* A text box that knows @: typing @ lists what can be named here (an assistant, a measure, a teammate),
   narrowed as you type; Enter or Tab puts the name in. Enter otherwise sends, Shift+Enter is a new line. */

import { useMemo, useState, type RefObject } from "react";

export type Mention = { label: string; hint: string; kind: "assistant" | "measure" | "person" };

export function MentionBox({ value, onChange, onSubmit, options, placeholder, className, rows = 1, inputRef, up = true }: {
  value: string; onChange: (v: string) => void; onSubmit: () => void; options: Mention[]; placeholder?: string;
  className?: string; rows?: number; inputRef?: RefObject<HTMLTextAreaElement | null>; up?: boolean;
}) {
  const [caret, setCaret] = useState(0);
  const [pick, setPick] = useState(0);
  const [closed, setClosed] = useState<string | null>(null);
  const at = useMemo(() => {  // the @word the caret is in, if any
    const m = /(^|\s)@([^\s@]*)$/.exec(value.slice(0, caret));
    return m ? { start: caret - m[2].length - 1, q: m[2].toLowerCase() } : null;
  }, [value, caret]);
  const list = useMemo(() => !at || closed === `${at.start}` ? [] : options
    .filter((o) => o.label.toLowerCase().replace(/\s+/g, "").includes(at.q.replace(/\s+/g, "")))
    .slice(0, 8), [at, options, closed]);
  const put = (o: Mention) => {
    if (!at) return;
    const next = `${value.slice(0, at.start)}@${o.label} ${value.slice(caret)}`;
    onChange(next);
    const pos = at.start + o.label.length + 2;
    setCaret(pos); setPick(0);
    requestAnimationFrame(() => { inputRef?.current?.setSelectionRange(pos, pos); inputRef?.current?.focus(); });
  };
  return (
    <div className="relative min-w-0 flex-1">
      <textarea ref={inputRef} value={value} rows={rows} placeholder={placeholder} className={className} aria-autocomplete="list"
        onChange={(e) => { onChange(e.target.value); setCaret(e.target.selectionStart ?? e.target.value.length); setPick(0); setClosed(null); }}
        onSelect={(e) => setCaret((e.target as HTMLTextAreaElement).selectionStart ?? 0)}
        onKeyDown={(e) => {
          if (list.length) {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); setPick((p) => (p + (e.key === "ArrowDown" ? 1 : list.length - 1)) % list.length); return; }
            // the name already typed in full: Enter sends, rather than completing what is complete
            const whole = at && list.some((o) => o.label.toLowerCase().replace(/\s+/g, "") === at.q.replace(/\s+/g, ""));
            if (e.key === "Tab" || (e.key === "Enter" && !whole)) { e.preventDefault(); put(list[pick]); return; }
            if (e.key === "Escape") { e.preventDefault(); setClosed(`${at?.start}`); return; }
          }
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSubmit(); }
        }} />
      {list.length > 0 && (
        <ul role="listbox" className={`absolute left-0 z-40 w-[320px] max-w-full overflow-hidden rounded-xl border border-[var(--d-rule)] bg-[var(--d-sheet)] py-1 text-[var(--d-ink)] shadow-[0_18px_40px_-18px_rgba(0,0,0,.45)] ${up ? "bottom-full mb-2" : "top-full mt-2"}`}>
          {list.map((o, i) => (
            <li key={`${o.kind}-${o.label}`} role="option" aria-selected={i === pick}>
              <button type="button" onMouseDown={(e) => { e.preventDefault(); put(o); }} onMouseEnter={() => setPick(i)}
                className={`flex w-full items-baseline gap-2 px-3 py-1.5 text-left text-[13.5px] ${i === pick ? "bg-[var(--d-paper)]" : ""}`}>
                <span className="font-medium">@{o.label}</span>
                <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--d-muted)]">{o.hint}</span>
                <span className="text-[10.5px] uppercase tracking-wide text-[var(--d-muted)]">{o.kind}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
