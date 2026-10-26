---
title: Product filing supplement, Performance Trigger strategy for Index Advantage 7 and 10 (fictional)
kind: product filing
author: Northwind Life & Annuity, Annuity Product Development
date: 2026-01-26
source: Filing supplement and internal implementation notes for the Index Advantage fixed indexed annuities. Fictional document for training.
fictional: true
---
## Page 1

Northwind will add a Performance Trigger index strategy to the Index Advantage 7 and Index Advantage 10 fixed indexed annuities, available on applications signed on or after March 1, 2026, subject to state approval.

How it works: at the end of each one-year term, if the index value is equal to or higher than at the start of the term, the strategy credits the declared trigger rate (6.25% on Index Advantage 7 and 6.75% on Index Advantage 10 at launch). If the index is lower, nothing is credited. The credit does not depend on how much the index rose.

## Page 2

Implementation. The annuity platform stores the strategy as a crediting option with method code TRIGGER. The trigger rate is stored in the option's declared rate field; the cap and participation fields are left empty because they do not apply.

Data Engineering: performance trigger is a crediting method of its own. It is not an annual point-to-point strategy with a low cap, and it is not a declared-rate (fixed) strategy. Please add it to the enterprise list of crediting methods so that the new options conform and cap-rate reporting does not treat them as capped strategies.

## Page 3

Rates. Trigger rates are declared on the rate sheet like caps and may change for new terms. The minimum guaranteed trigger rate is 1.00%. Marketing will position the strategy for clients who expect modest index growth.
