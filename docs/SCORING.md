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
score ≥ 3 with nobody to write to. **P3** = score ≤ 1.

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
