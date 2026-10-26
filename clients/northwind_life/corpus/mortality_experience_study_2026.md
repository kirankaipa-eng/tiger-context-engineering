---
title: Mortality experience study 2019-2025 and basis update (fictional)
kind: actuarial memo
author: Northwind Life & Annuity, Experience Studies
date: 2026-04-08
source: Internal experience study summary for the Life Pricing and Valuation committees. Fictional document for training.
fictional: true
---
## Page 1

We studied individual life mortality on policies issued 2019 through 2025, measured against the 2015 VBT ultimate rates by attained age, sex and tobacco class. Overall actual-to-expected by count is about 80%.

The ratio rises with policy duration: policies in their first few years run around 60-65% of expected, while the oldest cohorts are near 90%. This is the expected pattern from underwriting selection wearing off, and it means the ultimate-only basis understates early-duration selection and overstates it later.

## Page 2

Recommendation. Measure experience against select-and-ultimate rates: a rate for each issue age (or attained age) and policy duration during the select period, and ultimate rates thereafter. The mortality rate records in the enterprise data model are keyed by table, attained age, sex and tobacco class only.

Please add the policy duration (select period year) to each mortality rate record, so that select rates can be loaded alongside ultimate rates and the actual-to-expected metric can use them.

## Page 3

Until the select rates are loaded, read actual-to-expected by issue year cohort rather than for the block as a whole. Substandard (table-rated) business runs above standard as expected after table ratings. No change to the reserve mortality tables is proposed.
