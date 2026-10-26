# Medical economics, made agent-ready: the Plains Health Plan demo

Context Studio on a fictional regional payer (Plains Health Plan) built from tigerclaw's synthetic
MEE data: 15,000 members in five states, 155k medical and 210k pharmacy claims, a provider master
with peer cohorts, CPT modifiers and six seeded anomalies. Internal demo; about 5 minutes recorded.

## What it shows

| Scene | What happens | Proof |
| --- | --- | --- |
| Onboard a client | "Onboard Plains Health" is a proposal: vocabulary, mappings, documents, owners | 7 checks pass; the data's one known issue is recorded; actuarial lead, privacy officer and pack steward approve |
| The curator reads | 9 documents: 4 real changes, 5 decoys | Scored against `clients/plains_health/corpus/answer_key.yaml`: 4 of 4 found, 0 decoys acted on |
| A change the data proves | UM-021: expired authorizations need their own status | 123 requests already carry it; data steward approves |
| Observation stays are outpatient | MP-112 question; the steward asks the curator to draft the fix | A **mapping** change; MA IP PMPM $370 -> $317, OP $0 -> $53, admits/1,000 294 -> 251; EDW engineer approves |
| Their Power BI model stays | The curator crosswalks the MEE model (36 measures) | 8 governed, 11 presentation, 17 gaps; governed metrics generated as TMDL; BH PMPM added with its DAX in the same proposal |
| Agents | MA IP PMPM in Illinois (new definition); PI-25 modifier review | PI-25 (2x cohort, two quarters, z >= 1.645): 7 flagged, all 6 seeded anomalies among them |
| Consistency | Paid per inpatient admit, Commercial, 2025, five runs each | Governed: one answer; raw SQL: several (a live run showed $22,446 / $22,965 / $0) |
| History | Who approved what, from which page; undo is a new proposal | |

## Run it live

```bash
scripts/demo_mee.sh     # starts the API (:8700) and web app (:3700), and the onboarding proposal; safe to rerun
```

Open http://localhost:3700. The walkthrough page (`build_walkthrough.py`) has a short video, what it shows, and what
to click and type for each step.

Then, in the Studio (switch people with **Signed in as**):

1. Inbox: open **Onboard Plains Health**; approve as Actuarial lead, Privacy officer, Pack steward. Reload.
2. Pick **Plains Health Plan**; as Plains data steward, **Run the curator on documents**.
3. Approve **Add expired as a PA status** (Plains data steward).
4. Open the observation question; ask the docked Curator:
   *Medical policy MP-112 says observation stays are outpatient. Please draft that change: institutional
   claims for stays that began as observation should be outpatient, not inpatient; physician claims stay
   professional. Show me what it does to IP PMPM, OP PMPM and admits per 1,000.*
   Check the impact before approving: OP PMPM should rise (MA $0 -> $53). A draft that also moves the
   physician claims shows a larger OP number and a lower Prof PMPM; ask for a revision.
   Open the proposal, show the impact, approve as **Plains EDW engineer**.
5. As **Plains medical economics lead**: *Crosswalk our Power BI model, MEE_Client_Analytics.* Approve it, then:
   *BH PMPM is a gap. Please add it as a governed metric: medical claims paid for mental health claims, per member per month.*
6. Ask the Curator: *What was IP PMPM for Medicare Advantage members in Illinois in 2025 Q3?* and
   *Run the PI-25 modifier review: modifier 25 and modifier 59 rates per provider against their peer cohort,
   flag providers above 2x their cohort in both 2025-Q4 and 2026-Q1, at least 10 claims a quarter,
   significant at 95% (z 1.645).*
7. Consistency lab: the first Plains preset, 5 runs.

Agent steps take 20-120 seconds each through the Tiger AI gateway.

## Record it

`record_mee.py` drives the same steps in a browser with Kokoro narration (see
`placement-advisor/docs/walkthrough/narrated/README.md` for the Kokoro + Playwright setup). It waits on
the app, cuts the waits from the video and marks them on screen ("sped up").

```bash
python record_mee.py http://localhost:3700
```

## The business view: medical economics desk

For business stakeholders. Plain questions on the left; each answer is an exhibit on the canvas, computed
from governed definitions, with its definition, valuation date, context version and Power BI equivalent.
Click any bar or point to drill in; the analyst agent runs the drill.

```bash
scripts/demo_business.sh          # own home (.studio-business) and ports: API :8702, web :3702
                                  # --fresh rebuilds it; first run takes about two minutes
```

Open http://localhost:3702/insights. The home carries three approved changes (UM-021 expired status,
MP-112 observation stays as outpatient, the Power BI crosswalk), so the trust story is real.

| Beat | Do | What it shows |
| --- | --- | --- |
| 1 | Ask *How is Medicare Advantage inpatient PMPM trending in Illinois?* | Headline $538 in 2025-Q4, +31% on Q3; 2026-Q1 marked as still receiving claims |
| 2 | Ask *Why did it jump last quarter?* | Volume or price: admits per 1,000 +2%, paid per admit +28%; waterfall by clinical category (endocrine +$78, mental health +$45, oncology -$35) |
| 3 | Click the Endocrine bar, then *Admit type* | The agent runs the drill: elective $54, emergency $48, urgent $30. No observation row: MP-112 moved it to outpatient |
| 4 | Click *Why trust this number?* | Definition in plain words, source tables to the agent view, and the MP-112 change: Medicare Advantage $370 -> $317, approved by the EDW engineer, with the policy quote |
| 5 | Ask *Run the payment-integrity check: which providers bill modifier 25 at more than twice their peers in 2025-Q4 and 2026-Q1?* | 4 of 471 providers flagged, each significant against its peer cohort |
| 6 | Ask *What is our cost per episode for knee replacement?* | No governed definition, so no number: the question goes to the owner instead |

Each answer takes 15 to 30 seconds (the analyst model reasons before it calls a tool); the numbers
themselves compute in under a second.
