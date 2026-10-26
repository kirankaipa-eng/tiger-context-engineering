---
title: Valuation memo, principle-based reserves for non-variable annuities (VM-22) election (fictional)
kind: actuarial memo
author: Northwind Life & Annuity, Corporate Actuarial (Valuation)
date: 2026-01-12
source: Internal memo to the CFO, Finance, Annuity Operations and Data Engineering. Fictional document for training.
fictional: true
---
## Page 1

The Valuation Manual's principle-based requirements for non-variable annuities (VM-22) apply to contracts issued on or after January 1, 2026, with an optional transition period before they become mandatory. Until now, our deferred annuities have been reserved under CARVM with prescribed assumptions.

The Valuation Committee has elected to apply VM-22 principle-based reserving from January 1, 2026 for new issues of our fixed deferred (Secure Rate) and fixed indexed (Index Advantage) products. Registered index-linked contracts (Shield 6) are excluded from this election pending a separate review. Contracts issued before 2026 stay on CARVM.

## Page 2

The annuity platform records the reserve method on every reserve row. From the first 2026 quarterly valuation it records the new basis as method code VM22PBR for the elected contracts.

The enterprise data model's reserve method list does not have a value for VM-22 principle-based reserves. Please add one, so that 2026 reserves conform and the Statutory Reserve report can be cut by method for the annual statement exhibits and the transition disclosures.

## Page 3

A caution on codes. The platform already uses the code VM22 for annuitized (payout) contracts, where it means the older section 22 requirement: maximum valuation interest rates used with CARVM for income payments. That meaning does not change. Do not map VM22PBR onto the existing VM22 value; they are different methods and must be reported separately.

Reserve levels on the elected business are expected to be modestly lower than CARVM at issue. Finance will monitor the difference quarterly.
