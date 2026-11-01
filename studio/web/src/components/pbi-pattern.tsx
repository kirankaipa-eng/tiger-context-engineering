"use client";

/* The pattern map: every place a measure adds up from (a store, then a product in it), each tile sized by its
   value in the month shown and coloured by how far it sits from its own last twelve months. Calm places fade
   into the page; a tile is lit only when chance would not put it there (the test allows for every place and
   month on the map at once). Scrub or play the months and watch the business hold its shape, or break it.
   Colours come from the desk theme, so every theme and dark mode read the same way. */

import { Pause, Play } from "lucide-react";
import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Pattern } from "@/lib/pbi";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const short = (m: string) => `${MON[+m.slice(5, 7) - 1]} ’${m.slice(2, 4)}`;
const long = (m: string) => `${FULL[+m.slice(5, 7) - 1]} ${m.slice(0, 4)}`;

type RGB = [number, number, number];
function rgb(css: string): RGB {
  const s = css.trim();
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(s);
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].split("").map((c) => c + c).join("") : hex[1];
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB;
  }
  const m = s.match(/[\d.]+/g);
  return m ? [+m[0], +m[1], +m[2]] : [128, 128, 128];
}
const mix = (a: RGB, b: RGB, t: number) => `rgb(${a.map((x, i) => Math.round(x + (b[i] - x) * t)).join(",")})`;

/** A value as Power BI shows it, short. */
export function shortValue(v: number, format: string | null) {
  if (format?.includes("%")) return `${(100 * v).toFixed(1)}%`;
  const cur = format?.match(/[$€£¥₹]/)?.[0] ?? "", a = Math.abs(v), sign = v < 0 ? "-" : "";
  if (a >= 1e6) return `${sign}${cur}${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${sign}${cur}${Math.round(a / 1e3)}K`;
  return `${sign}${cur}${Math.round(a).toLocaleString()}`;
}

/** How far from usual, in words a reader can repeat. */
function standingWords(z: number, lit: boolean) {
  if (lit) return z > 0 ? "far above its usual: chance would not put it here" : "far below its usual: chance would not put it here";
  if (Math.abs(z) >= 2) return z > 0 ? "somewhat above its usual" : "somewhat below its usual";
  return "within its usual range";
}

export function PatternMap({ p, index, onIndex, playing, onPlaying, onPick, claimSince }: {
  p: Pattern; index: number; onIndex: (i: number) => void; playing: boolean; onPlaying: (b: boolean) => void;
  onPick: (group: string, child: string, month: string) => void; claimSince?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [tone, setTone] = useState<Record<string, RGB> | null>(null);
  const [reduced, setReduced] = useState(false);
  useEffect(() => {  // the theme's colours, read from the desk tokens (and again when the theme changes)
    const read = () => {
      const cs = box.current ? getComputedStyle(box.current) : null;
      if (!cs) return;
      const v = (n: string) => rgb(cs.getPropertyValue(n) || "#888");
      setTone({ sheet: v("--d-sheet"), paper: v("--d-paper"), rule: v("--d-rule"), ink: v("--d-ink"), muted: v("--d-muted"),
        hot: v("--d-rise-ink"), warm: v("--d-rise-soft"), cool: v("--d-teal"), coolSoft: v("--d-teal-soft") });
    };
    read();
    const root = box.current?.closest(".desk");
    const obs = root ? new MutationObserver(read) : null;
    if (root && obs) obs.observe(root, { attributes: true, attributeFilter: ["class"] });
    setReduced(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false); // eslint-disable-line react-hooks/set-state-in-effect -- browser-only, read once after mount
    return () => obs?.disconnect();
  }, []);
  // playing: one month a beat, from where the scrubber is to the last month
  useEffect(() => {
    if (!playing) return;
    if (index >= p.months.length - 1) { onPlaying(false); return; }
    const t = setTimeout(() => onIndex(index + 1), reduced ? 250 : 650);
    return () => clearTimeout(t);
  }, [playing, index, p.months.length, onIndex, onPlaying, reduced]);

  const month = p.months[index];
  // a tile too small to read carries no label (its tooltip still does): about 1% of the month
  const floor = 0.012 * p.groups.reduce((t, g) => t + g.children.reduce((u, c) => u + Math.max(0, c.values[index]), 0), 0);
  const inClaim = (g: string, c: string) => !!p.claim && !!claimSince && month >= claimSince &&
    p.claim[0] === g && p.claim[p.claim.length - 1] === c;
  const option = useMemo(() => {
    if (!tone) return null;
    const color = (z: number, lit: boolean) => {
      const a = Math.min(1, Math.abs(z) / 4);
      if (lit) return mix(z > 0 ? tone.warm : tone.coolSoft, z > 0 ? tone.hot : tone.cool, 0.92);
      return Math.abs(z) < 2 ? mix(tone.paper, tone.rule, 0.35) : mix(tone.paper, z > 0 ? tone.warm : tone.coolSoft, 0.4 + 0.6 * a);
    };
    const nested = p.groups.map((g) => ({
      name: g.name, id: g.name,
      children: g.children.map((c) => {
        const lit = c.out[index], z = c.z[index], mine = inClaim(g.name, c.name);
        return {
          name: c.name, id: `${g.name}|${c.name}`, value: Math.max(0, c.values[index]),
          z, lit, raw: c.values[index],
          itemStyle: { color: color(z, lit), borderColor: mine ? `rgb(${tone.ink.join(",")})` : undefined, borderWidth: mine ? 3 : undefined },
          label: { show: c.values[index] >= floor || lit || mine, color: lit ? `rgb(${tone.sheet.join(",")})` : `rgb(${tone.ink.join(",")})`,
            fontWeight: lit || mine ? 700 : 500, fontSize: lit ? 14 : 12 },
        };
      }),
    }));
    // one level (a narrow slice): the values are the tiles themselves, with no group around each
    const data = p.levels.length === 1 ? nested.flatMap((g) => g.children) : nested;
    return {
      animationDurationUpdate: reduced ? 0 : 520, animationEasingUpdate: "quinticOut",
      tooltip: {
        confine: true, backgroundColor: `rgb(${tone.sheet.join(",")})`, borderColor: `rgb(${tone.rule.join(",")})`,
        textStyle: { color: `rgb(${tone.ink.join(",")})`, fontSize: 12.5 },
        formatter: (i: { data: { name: string; raw?: number; z?: number; lit?: boolean }; treePathInfo: { name: string }[] }) => {
          const path = i.treePathInfo.map((x) => x.name).filter(Boolean).join(" · ");
          if (i.data.raw === undefined) return `<b>${path}</b>`;
          return `<b>${path}</b><br/>${shortValue(i.data.raw, p.format)} in ${long(month)}, ${standingWords(i.data.z ?? 0, !!i.data.lit)}`;
        },
      },
      series: [{
        type: "treemap", data, roam: false, nodeClick: false, breadcrumb: { show: false }, top: 0, left: 0, right: 0, bottom: 0,
        width: "100%", height: "100%", squareRatio: 1.4,
        label: { show: true, formatter: (i: { name: string; data: { raw?: number } }) => `${i.name}\n${i.data.raw !== undefined ? shortValue(i.data.raw, p.format) : ""}`,
          fontSize: 12, lineHeight: 16, overflow: "truncate", padding: [6, 8] },
        upperLabel: { show: true, height: 26, color: `rgb(${tone.ink.join(",")})`, fontWeight: 600, fontSize: 12.5, padding: [0, 8] },
        itemStyle: { borderColor: `rgb(${tone.sheet.join(",")})`, gapWidth: 1, borderWidth: 1 },
        levels: [
          { itemStyle: { borderColor: `rgb(${tone.sheet.join(",")})`, borderWidth: 0, gapWidth: 4 } },
          { itemStyle: { borderColor: `rgb(${tone.sheet.join(",")})`, borderWidth: 4, gapWidth: 2, color: `rgb(${tone.sheet.join(",")})` },
            upperLabel: { show: true } },
          { itemStyle: { gapWidth: 1 } },
        ],
      }],
    };
  }, [tone, p, index, month, reduced, floor]); // eslint-disable-line react-hooks/exhaustive-deps -- inClaim reads p and month

  const most = Math.max(1, ...p.disturbed);
  return (
    <div ref={box} className="min-w-0">
      <div className="h-[420px] w-full overflow-hidden rounded-xl bg-[var(--d-sheet)]">
        {option && <ReactECharts option={option} style={{ width: "100%", height: "100%" }}
          onEvents={{ click: (e: { data?: { id?: string } }) => { const id = e.data?.id; if (id?.includes("|")) { const [g, c] = id.split("|"); onPick(g, c, month); } } }} />}
      </div>
      <div className="mt-4 flex items-end gap-3">
        <button onClick={() => { if (index >= p.months.length - 1) onIndex(Math.max(0, p.months.length - 13)); onPlaying(!playing); }}
          aria-label={playing ? "Pause" : "Play the months"}
          className="mb-1 flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--d-ink)] text-[var(--d-sheet)] transition-transform hover:scale-105 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--d-teal)]">
          {playing ? <Pause size={16} /> : <Play size={16} className="ml-0.5" />}
        </button>
        <div className="min-w-0 flex-1">
          {/* how many places broke their pattern each month: the timeline is the story's shape */}
          <div className="flex h-9 items-end gap-[3px]" aria-hidden>
            {p.disturbed.map((n, i) => (
              <button key={p.months[i]} tabIndex={-1} onClick={() => { onPlaying(false); onIndex(i); }}
                className="group flex h-full flex-1 items-end" title={`${long(p.months[i])}: ${n ? `${n} out of pattern` : "nothing out of pattern"}`}>
                <span className={`block w-full rounded-t-[2px] transition-colors ${i === index ? "bg-[var(--d-ink)]" : n ? "bg-[var(--d-rise-ink)] group-hover:opacity-80" : "bg-[var(--d-rule)] group-hover:bg-[var(--d-muted)]"}`}
                  style={{ height: n ? `${30 + 70 * (n / most)}%` : "3px" }} />
              </button>
            ))}
          </div>
          <input type="range" min={0} max={p.months.length - 1} value={index} aria-label="Month shown"
            aria-valuetext={long(month)} onChange={(e) => { onPlaying(false); onIndex(+e.target.value); }}
            className="mt-1 w-full accent-[var(--d-ink)]" />
          <div className="flex justify-between text-[11.5px] tabular-nums text-[var(--d-muted)]">
            <span>{short(p.months[0])}</span><span className="font-semibold text-[var(--d-ink)]">{long(month)}</span><span>{short(p.months[p.months.length - 1])}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
