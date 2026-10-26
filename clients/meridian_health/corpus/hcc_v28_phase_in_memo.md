---
title: Risk adjustment memo, CMS-HCC V28 transition complete for payment year 2026 (fictional)
kind: actuarial memo
author: Meridian Risk Adjustment & Actuarial
date: 2026-02-03
source: Internal memo to finance, revenue integrity and data engineering. Fictional document for training.
fictional: true
---
## Page 1

CMS phased in the 2024 CMS-HCC model (V28) over three payment years. Payment year 2024 risk scores blended 67% of the 2020 model (V24) with 33% of V28. Payment year 2025 blended 33% V24 with 67% V28.

For payment year 2026 the phase-in is complete: Part C risk scores are calculated 100% under V28. There is no blend.

## Page 2

Our MA platform has recorded the score basis for every member and payment year. Through payment year 2025 every non-ESRD score carried the blended basis. Starting with the payment year 2026 initial sweep, the platform records the basis as V28 only.

The enterprise data model only knows the V24-only and blended bases. Please add a "CMS-HCC V28 (100%)" score basis so payment year 2026 scores conform and can be trended against the blend years.

## Page 3

Expect lower raw scores under V28 for the same population, partly offset by the normalization factor. Revenue forecasts for 2026 use the V28 scores and the published normalization factor and MA coding pattern adjustment.

Finance should cut average risk score by payment year and score basis when comparing 2026 with prior years.
