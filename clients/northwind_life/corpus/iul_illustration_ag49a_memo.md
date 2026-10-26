---
title: Compliance memo, IUL illustrations under Actuarial Guideline 49-A (fictional)
kind: compliance memo
author: Northwind Life & Annuity, Life Product Compliance
date: 2026-03-04
source: Internal memo to Life Product Management, Illustration Systems and Data Engineering. Fictional document for training.
fictional: true
---
## Page 1

Actuarial Guideline 49-A governs illustrations of indexed universal life. It limits the illustrated rate of every indexed account to that of a benchmark index account (an annual point-to-point account on a broad equity index with a cap and no multipliers or bonuses), so that accounts with bonuses or leverage cannot be illustrated more favorably than the benchmark.

Our IUL Accumulator and IUL Protector illustrations comply today. The maximum illustrated rate is computed in the illustration system each time a rate sheet changes.

## Page 2

Illustration audits now ask us to show, for every index strategy and every rate sheet, the maximum illustrated rate that applied alongside the cap and participation rate in force. Our strategy records carry the cap, participation rate and floor, but not the maximum illustrated rate, so the audit trail lives only in illustration system logs.

Please add the maximum illustrated rate as an attribute of each crediting strategy rate record. It is a regulatory limit on illustrations, not a crediting term: it must not be confused with the cap rate, which limits what is credited.

## Page 3

No change to crediting, to the strategies offered or to in-force policies is required. Illustration Systems will backfill the attribute for rate sheets from 2021 onward.
