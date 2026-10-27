---
target: studio/web/src/app/pbi
total_score: 21
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 3
target_identity: "file:/Users/kirankaipa/Official/TigerAnalytics/code/tiger-context-engineering/studio/web/src/app/pbi"
timestamp: 2026-10-27T04-41-50Z
slug: studio-web-src-app-pbi
---
# Critique: Power BI analysis desk (studio/web/src/app/pbi)
Method: dual-agent (A: design review in source + live desk; B: impeccable detect + in-page overlay on 5 views)

## Design Health Score: 21/40 (Acceptable)
1 Visibility 3 · 2 Match 2 · 3 Control 2 · 4 Consistency 2 · 5 Error prevention 1 · 6 Recognition 2 · 7 Flexibility 2 · 8 Aesthetic 3 · 9 Error recovery 2 · 10 Help 2

## Design specificity
The desk (/pbi) is partly authored for this product: numbered exhibits, Power BI measure / Raw rows badge, How it's built (definition, DAX, SQL), teal = governed, honest coverage copy. The secondary screens (Coverage, Drilldown, Metrics, Explore, Tables) still use the generic beige/blue Studio shell. "Receipts" is undermined (P0); "explains, not just shows" is not yet visible.
Code scan: 0 findings in 12 files. In-page overlay: 20 real findings, all 11px text or contrast.
- Contrast near misses at 4.4:1: --d-muted #6a7a90 (pbi-exhibit.tsx:68,71,103,175) and --d-teal on --d-teal-soft (pbi-exhibit.tsx:113).
- 11px text: page.tsx:260, and 13 metric keys from metrics/page.tsx:47.
- Filter placeholder at 3.3:1: metrics/page.tsx:33.
- Text-occlusion hits are false positives (the overlay's own labels).

## Priority issues
1. [P0] Rows behind it contradicts the number. The measure's own filter is dropped (Net Sales Status=Sold returned only Returned rows). Fix: apply the measure's row filter, add a reconciliation line, show a warning until then. harden
2. [P1] The drill menu lists columns instead of explaining. It shows 12 unranked options and single-value splits, without the clicked value's number or share, and a child exhibit doesn't reconcile to its parent. Fix: menu header with value and share, rank dimensions by contribution, show the top 3–4, hide single-value splits, label "of X". shape, then distill and clarify
3. [P1] Exhibit layout breaks on wide tables. grid-cols-[48px_1fr] (page.tsx ~288) lets the card stretch, so the main column scrolls sideways and exhibit numbers are hidden. The drill menu overflows its card. The fixed w-[420px] rail breaks at narrow widths. Fix: minmax(0,1fr), scroll inside the table, rail becomes a drawer below lg. adapt
4. [P1] Two products stitched together. Desk shell vs Studio shell, two palettes, Measures vs Metrics. Metrics Drill goes to /pbi/drill, and Coverage "Ask" links to a 404 at /pbi/ask. Fix: desk tokens and palette on all /pbi routes, every Drill/Ask goes to the desk with an exhibit pre-opened. polish
5. [P2] Change colour ignores polarity. −63% Returns shows in amber (pbi-exhibit.tsx:420). No units, part-month compared with a full month, --d-muted and --d-rise fail AA. Fix: per-measure polarity, units from the format string, partial-period flag, darken the tokens. clarify

## Persona red flags
- Alex: no shortcuts, copy-SQL, export or compare. New analysis can't be undone. LLM answers take ~30s with no estimate. Explore opens blank.
- Sam: canvas charts can only be drilled with a mouse. The drill menu lacks role=menu, Esc and a focus trap. No aria-live. Contrast failures.
- Priya (leader): nothing to share or export. Machine-built titles. SQL appears before plain language. Wrong polarity colour. A slice reads as the whole.

## Minor
- "Why 1 measures" grammar, and its % counts display-only measures as usable.
- Dead gutter on ranked bars.
- The drill hint still shows on the How it's built tab.
- The narrative repeats the chart's values.
- A one-row result reads as an achievement.
- The 11px metric keys are one design decision.

## Questions
Ship rows-behind without the filter? Drill menu that opens with the answer? Exhibit stack as the shareable finding? Coverage, Metrics and Explore as panels on an exhibit?
