# Signal scoring — scale, changes, and rationale

This file is the audit trail for the calibration loop. **Every change to a
component weight or a priority threshold appends a dated entry here**, with the
reasoning, before the change ships. `lib/scoring.py` holds the numbers; this
file holds why they are what they are.

The point of keeping the two together is that a score is a claim about a
company, and a claim without provenance is exactly what this pipeline exists to
prevent. A weight that nobody can justify is a weight that will be defended by
habit.

---

## The scale

**There are two, one per source adapter, since 2026-09-08.** The table below is
the `conexus_iedc` scale; the `canada_gc` scale is in the 2026-09-08 entry and
the two are shown side by side there. A prospect is scored on the profile named
by its `source_adapter` column and on no other, and the profile used is recorded
in `score_evidence._profile`.

Each component contributes its weight when the signal fires, zero when it does
not. The per-component breakdown is stored on the prospect (`score_breakdown`)
alongside the total, so a re-weighting can re-total every existing record
without re-researching anybody.

| Component | Weight | Fires when |
| --- | ---: | --- |
| `clerical_posting` | +1 | An active clerical or coordination posting dated within 60 days |
| `data_gen_tech` | +1 | The grant description names data-generating technology |
| `case_study` | +1 | A Conexus case-study subpage exists for the company |
| `weak_front_door` | +1 | Two or more of the seven front-door weakness criteria |
| `decision_maker_found` | +1 | A named human is attached to a stated leadership role |
| `in_drive_radius` | +1 | Estimated drive time from Muncie is 90 minutes or less |
| `too_big` | −1 | Over 250 employees, or clear enterprise ownership |
| `status_uncertain` | −1 | Business status uncertain; the company could not be located online |

Priority: **P1** = score ≥ 3 **and** a named decision-maker. **P2** = score 2, or
score ≥ 3 with nobody to write to. **P3** = score ≤ 1. The same thresholds apply
on both scales.

---

## 2026-08-09 — remove `friction_reviews`; lower the P1 threshold to 3

### What changed

1. **`friction_reviews` removed from the scale entirely** — from `SignalInputs`,
   from `COMPONENT_WEIGHTS`, and from `score_breakdown`. The maximum positive
   score falls from 7 to 6.
2. **P1 threshold lowered from 4 to 3.** P2 becomes score 2 (plus the existing
   overflow case: score ≥ 3 with no named decision-maker is P2, because there is
   nobody to send the work to). P3 becomes score ≤ 1.

### The scale before this change

Seven positive components (the six above plus `friction_reviews`) and two
penalties, with P1 at score ≥ 4 and P2 at 2–3.

### Why

**`friction_reviews` could never fire.** Review scraping was descoped for legal
reasons, so no node populates it and none is planned. It sat in the scale
contributing a guaranteed zero to every prospect, which is worse than absent: it
made the ceiling look like 7 when it was really 6, and every threshold set
against that ceiling was implicitly one point too strict. A signal that cannot
fire must not sit in the scale. Block 5 (customer friction) remains as an
evidence block for manual entry — the evidence is still worth recording, it is
just not scored.

**The threshold was calibrated against a ceiling that did not exist.** Measured
across the first ten fully scored prospects, only three components ever fired —
`case_study`, `in_drive_radius`, and `decision_maker_found` — and the observed
range was 2 to 4 against a P1 threshold of 4. One prospect in ten reached P1.
Two components are structurally dead in the current pipeline
(`friction_reviews`, now removed) or near-dead (`clerical_posting` fired zero
times in ten, because most small-manufacturer careers pages list no roles).

That leaves five components that realistically fire, graded against a threshold
built for seven. The effect was not a strict filter but a flat one: nearly
everything landed in P2, and P2 is not a queue anyone works. Lowering P1 to 3
restores discrimination — a company with a case study, inside the drive radius,
and a named human to contact is a genuine first call, and that is exactly the
combination that now scores 3.

### What this is not

This is **not** a recalibration from outcome data. No outreach has happened, so
there are no wins or losses to fit against. It is a correction for a scale that
was mis-specified: one component that cannot fire, and a threshold set against a
ceiling that was never reachable. The real calibration is still ahead.

### Effect on the existing records

The ten prospects scored under the old scale were re-scored. Distribution moved
from 1×P1 / 9×P2 to 5×P1 / 5×P2. No component values changed — the ten had
`friction_reviews: 0` throughout, so only the threshold moved them.

### Effect across the full 572, after Pass A

Scored under the new scale with every node run:

| | All 572 | Within 90 minutes (209) |
| --- | ---: | ---: |
| P1 | 22 (3.8%) | 13 (6.2%) |
| P2 | 129 (22.6%) | 90 (43.1%) |
| P3 | 421 (73.6%) | 106 (50.7%) |

Before the contact-validation fix described below, this read 27 P1 / 125 P2.
The five that moved were promoted on a contact who did not exist; each fell to
P2 once the name was rejected, which is the correct answer — the company is
still interesting, there is just nobody identified to call yet.

A P1 rate near 4% is a workable first-call queue rather than the flat
distribution the old threshold produced.

---

## 2026-08-09 — contact validation tightened (affects `decision_maker_found`)

Not a weight change, but it changes which prospects score the point, so it
belongs in this log.

The full run wrote contacts that were not people into nine P1 and P2 records:
another prospect's company name (`Insects Limited` recorded against Catalyst
Product Development), organisations (`Atlanta Track Club`, `National
Transportation`, `Purdue University Analytical`), page furniture (`Email Phone
Bio`), a machine-tool brand read as a surname (`Dave Solidworks`), and an
unfilled `John Doe` template. Each of those scored `decision_maker_found`, and
`decision_maker_found` is half of what makes a P1.

Two causes, both fixed:

1. The name validator accepted organisation and chrome words. It now rejects
   institution words, page furniture, tool brands, and placeholder names.
2. A re-run that found nobody wrote no `named_people` key at all, so the merge
   kept whatever an earlier, looser run had left there. The node now writes an
   empty list explicitly, which replaces the stale one.

`tools/audit.py` gained a matching standing check — "Named contacts are people"
— because the existing "P1 has a named human" check only asked whether a name
was present, and presence is not personhood. It passed throughout.

---

## 2026-08-09 — evidence integrity gates scoring; P1 requires an untainted block1

### What changed

1. **A score is now computed only if the evidence passes an integrity check.**
   On failure `signal_score` and `priority` are set to **null**, not zero, the
   stage becomes `needs_review`, and an `integrity_report` records exactly what
   failed. Null and zero mean different things and must not be collapsed: zero
   is a finding about a company we researched, null is "this cannot be computed
   from what we have".
2. **P1 additionally requires block1 populated with untainted claims.** A
   prospect that scores 3+ with a named decision-maker but no surviving account
   of what it makes is held at P2.

### The motivating case: Decatur Plastic Products LLC

Decatur scored **4** and sat at **P1**. Its `self_description` was the text of
an Indonesian gambling site — ALEXISTOGEL — because `decaturplastics.com` had
expired, been re-registered, and redirected through `asselsestraat.nl` to
`savvycellar.com`.

Three of its four points came from the genuine Conexus case study and were
correct. The fourth came from `weak_front_door`, measured against the gambling
page: *"no visible content or copyright date; no contact or quote form; no phone
number visible"* — all true of a togel site, and all meaningless about Decatur.
That fourth point is what crossed the P1 threshold of 3.

Nothing in a one-dimensional pipeline could catch it. Every claim was correctly
formed — sourced, tiered, dated. The file was internally valid and externally
false. A score answers "how interesting is this company"; it cannot answer "is
this evidence about that company at all". That is the second dimension, and it
is why the gate runs *before* the arithmetic rather than adjusting it after.

Decatur was not alone: Addman Engineering (P1, Russian sports betting), Sip and
Share Wines and Nextremity Solutions were the same failure, and 59 prospects in
total had been stored at confidence 80 after a name check that explicitly
failed — because the failure branch returned a confidence above the trust floor
of 70.

### What this is not

It is not a judgement about the companies. Decatur is a real manufacturer with a
real grant and a real case study; only its domain was stolen. The gate says the
file cannot be scored as it stands, routes it to a human, and deletes nothing.

---

## 2026-09-08 — the scale becomes per source; the canada_gc profile

### What changed

`lib/scoring.py` now holds a **profile per source adapter** rather than one set
of weights. A prospect is scored on the profile named by its `source_adapter`
column and on no other. Two profiles exist:

* **`conexus_iedc` — unchanged.** Same six positive components, same two
  deductions, same thresholds. Nothing about an Indiana prospect scores
  differently than it did yesterday, and `COMPONENT_WEIGHTS` still holds exactly
  the key set migration 001 documents.
* **`canada_gc` — new, and UNCALIBRATED.**

The score node records which scale it used, in `score_evidence._profile`,
because a breakdown read a year from now has to say which scale produced it.

### The two scales, side by side

| Component | conexus_iedc | canada_gc | Fires when |
| --- | :---: | :---: | --- |
| `clerical_posting` | +1 | +1 | An active clerical or coordination posting dated within 60 days |
| `weak_front_door` | +1 | +1 | Two or more of the seven front-door weakness criteria |
| `decision_maker_found` | +1 | +1 | A named human is attached to a stated leadership role |
| `data_gen_tech` | +1 | — | The Conexus grant description names data-generating technology |
| `case_study` | +1 | — | A Conexus case-study subpage exists for the company |
| `in_drive_radius` | +1 | — | Estimated drive time from Muncie is 90 minutes or less |
| `program_recency` | — | +1 | The company's most recent federal award starts in 2023 or later |
| `english_site` | — | +1 | The company's own site is in English |
| `purpose_names_data_generating_tech` | — | +1 | The government award record names data-generating technology |
| `compliance_regime` | — | +1 | The company publishes a quality or food-safety certification |
| `external_tech_engagement` | — | +1 | The award record names an outside technology partner or collaborator |
| `too_big` | −1 | −1 | Over 250 employees, or clear enterprise ownership |
| `status_uncertain` | −1 | −1 | Business status uncertain; the company could not be located online |

Ceiling 6 for Indiana, **8 for Canada**. Priority is unchanged on both:
**P1** = score ≥ 3 **and** a named decision-maker; **P2** = 2, or ≥ 3 with
nobody to write to; **P3** = ≤ 1. The drafting floor (three assertable T1 facts)
and the integrity gate are unchanged and are not per-profile — they are about
whether a fact may be used at all, and that does not become negotiable because
the company is Canadian.

### Why split the scale at all

Because two of Indiana's six positive components are statements about Indiana.
`in_drive_radius` measures the drive from Muncie; `case_study` asks whether
Conexus published a case study, and Conexus has never heard of Ontario. Scored
on the Indiana scale, every Canadian prospect would carry two components that
structurally cannot fire.

That is the `friction_reviews` defect from the 2026-08-09 entry above, exactly
repeated: a component that cannot fire is worse than an absent one, because it
makes the ceiling look higher than it is and every threshold set against that
ceiling is implicitly too strict. Removing them is the same correction, applied
before the mistake rather than after it.

### Where each Canadian component gets its evidence

Every one of them can fire, and this is what sets it:

| Component | Set by | Tier | Basis |
| --- | --- | :---: | --- |
| `program_recency` | `tools/canada_gc` at load | T1 | The award's start year, straight from the government record |
| `purpose_names_data_generating_tech` | `tools/canada_gc` at load | T4 | Our reading of their words: `DATA_GENERATING_TECH_TERMS` matched against the programme, purpose, title, description and expected results |
| `external_tech_engagement` | `tools/canada_gc` at load | T4 | Our reading of their words: the award record names a partner, collaborator, integrator or research institute. Filed in block 2 with the record that says it, not in block 6 — nobody observed a stack |
| `english_site` | `front_door` node | T1 | The site's declared `lang`, else English-versus-French function words. An undetermined language writes **no flag**, not a False |
| `compliance_regime` | `front_door` node | T1 | A quality or food-safety certification the company publishes about itself (ISO 9001/13485/14001/22000/45001, IATF 16949, AS9100, HACCP, SQF, BRCGS, FSSC 22000, GFSI, CFIA registration, Canada Organic) |

The certification pattern was extended for this: the North American
manufacturing certifications alone would have found nothing on a winery or a
bakery, and half of the four Canadian industries are food.

### Two components that were not carried over — they are new

The brief for this work described `compliance_regime` and
`external_tech_engagement` as components to *keep*. Neither existed in the
Indiana scale; there is nothing to keep. They are recorded here as **additions
to the Canadian profile**, each with a definition and a real evidence source
(above), so that the record does not later read as though they had always been
there. If the intention was different, this is the entry to correct.

### THIS SCALE IS UNCALIBRATED

Stated plainly because it is the most important sentence in this entry. **Every
Canadian weight is judgment, not measurement.** No Canadian prospect has been
contacted, so there is no reply data to fit against, and the numbers are a
starting point for the first batch rather than a finding. The score node says so
in its own notes on every Canadian run, and `CANADA_PROFILE.calibrated` is
`False`.

One thing to watch when the calibration does happen: **the P1 threshold of 3 was
set against a ceiling of 6 and is now also being used against a ceiling of 8.**
Three of eight is a looser bar than three of six, so the Canadian P1 rate should
be expected to run higher than Indiana's 3.8% for arithmetic reasons alone,
before any difference between the two markets. The threshold was left at 3
because the brief said the P1 rule was unchanged, and moving it on judgment
would be inventing calibration; the first Canadian batch is what should move it.

### Peer groups are scoped to the source

Not a weight change, but it belongs here. `lib/peers.py` now builds a peer group
from companies sharing the subject's `source_adapter`. A mixed group would
report the difference between two datasets as a difference in industrial
practice — an Indiana record carries a drive time and a Conexus case study, a
Canadian one carries neither, so "three of eleven comparable companies publish a
certification" would partly be measuring which country a company is in.

---

## Future recalibration from outcome data

*(Placeholder — nothing to record yet.)*

Once outreach has run, this section records each recalibration fitted against
real outcomes rather than judgment. The data the schema already keeps for this:

- `outreach_touches.response` — how each contacted prospect replied.
- `outreach_touches.corrections` — where a prospect corrected our numbers. A
  correction is both engagement and calibration data: it tells us which claims
  we get wrong and by how much.
- `outreach_touches.quoted_back_blocks` — which evidence blocks a prospect
  referenced. Blocks that get quoted back earn more research time.
- `prospects.outcome_value` and `outcome_notes` — signed deal value on a win.
- `prospects.research_minutes` — time spent, for a time-versus-outcome review.

The method when there is enough data: re-total every stored `score_breakdown`
under candidate weights, compare the resulting ranking against actual outcomes,
and only then change `COMPONENT_WEIGHTS`. Because the breakdown is stored
per component, this can be done offline over the whole history without
re-researching a single company.

Each such change appends an entry above this section, with the date, the sample
size it was fitted on, and what moved.
