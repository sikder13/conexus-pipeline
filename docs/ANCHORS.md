# What an analysis is sized to, and what happens when nothing sizes it

**Registered 2026-09-10.** `lib/anchors.py` holds the rule, `lib/offermodels.py`
holds the two model types it routes between, and this file is the reasoning.

---

## The finding that caused it

Forty-four Canadian analyses had been written. Between them they carried **six**
distinct headline figures, and twenty-five of them printed the same one:
`$16,653-$67,445 a year`. A second band, `$3,554-$25,737`, accounted for fifteen
more. Indiana was better and not by much: fifty-two analyses, twenty-three
distinct headlines, ten of them sharing the Canadian default.

Nothing was wrong with any of the arithmetic. Every input was labelled, every
range was honest, every figure was traceable to the spec that produced it. The
documents were still worthless, for a reason no gate could see: twenty-five
companies with no headcount on file got the same default volume band, so they
got the same answer.

A number twenty-five companies share is not about any of them. An operator who
reads two of those documents does not learn about two companies; they learn that
we have a spreadsheet.

---

## The order

Volume and scale inputs anchor in a strict order. The first that holds is used,
and a later one is never consulted while an earlier one is available — because
consulting two and keeping the nicer answer is how a document ends up sized to
whichever assumption flattered it.

| | Anchor | Where it comes from | What the document may claim |
| --- | --- | --- | --- |
| 1 | **A stated volume** | A claim of theirs saying how much of the work there is: "about 200 quotes a month". | The busiest input is *theirs*. Provenance is a claim path, not an assumption of ours. |
| 2 | **A headcount, scaled** | `lib/peers.py` size, from block 8. | Ours, anchored to a size fact about them. The prose must name the scaling. |
| 3 | **Their award, as capital** | The grant amount, Tier 1, from a government record. | A **different model**. See below. |
| 4 | **Nothing** | — | The headline is *"pending one number from you"*. Ranges stay in the body, explicitly hypothetical. |

The anchor is recorded on the artifact, in `gate_map.case.anchor`. "Why does this
document say $40,000" is asked months later and has to be answerable from the
row rather than from the evidence file as it stands on the day somebody asks.

---

## Why the award is a different model and not a third guess

An award size is **not a proxy for a headcount**. That inference was proposed in
an earlier relay and refused, and the refusal stands: a quarter of a million
dollars buys one machine at one company and pays six salaries at another, and
reading a payroll out of a purchase is a fabrication with a government citation
attached.

What an award size does say, at Tier 1, on a record the company is named on, is
**how much capital this company has committed**. That is a different question,
and it supports different arithmetic: what does that capital cost per year over
its life, and what share of that charge is being paid on an asset nobody can
prove ran?

So `offermodels.CAPITAL_UNIT` costs an asset instead of counting work:

```
annual_capital_charge  = capital_deployed / asset_life_years
capital_at_work        = annual_capital_charge x utilisation_today
idle_capital_a_year    = annual_capital_charge x (1 - utilisation_today)
annual_saving          = annual_capital_charge x utilisation_gain
```

**There is no wage in that spec and no volume.** That is the test of whether the
distinction is real rather than a label, and it is asserted in
`tests/test_anchors.py` rather than promised here.

Three details that are not obvious:

* **The award is a floor.** Both programmes disclose the contribution, not the
  project cost, and the Indiana one additionally requires a 1:1 match. Using the
  floor means every figure the model produces is the smallest honest version of
  itself, which is the right direction for a number somebody repeats on a call.
* **Idle capital is written as a product, not a subtraction.** `charge - at_work`
  over intervals returned **minus $1,694** on the first live run, because
  interval subtraction assumes the two are independent and they are not — one is
  derived from the other. A negative idle figure is not a conservative reading;
  it is arithmetic reporting a pairing that cannot occur.
* **There is a floor on the award itself.** Below `offermodels.award_floor()` —
  derived from the engagement ladder, not typed — the model runs and always
  answers no. A $72,600 contribution over a ten-year life is a capital charge of
  $7,260 a year, five to fifteen per cent of which is $360 to $1,090 against a
  build that starts at $2,500. The model is not wrong there; it is saying the
  approach does not apply, and printing it anyway would put a figure in front of
  a prospect whose only honest reading is "not worth doing". Below the floor, the
  anchor is `none`.

Only the **lead** approach becomes the capital model. Three capital models at
three prices would be one build wearing three names — the exact thing the
distinctness gate refuses — and a company does not have three different capital
utilisation problems.

---

## What "no anchor" does to a document

It changes the voice, not the contents. The arithmetic still runs, because our
reading of what a company of this shape probably spends its hours on is worth
having; what changes is that the document may not present it as a finding.

* The summary headline renders `pending one number from you`
  (`anchors.PENDING_HEADLINE`), in `lib/theten.py` and everywhere else a headline
  is printed.
* The generator is told, in `casefile.ANCHOR_RULES`, to write every figure as a
  hypothesis about a company of this shape and never as a statement about them.
* The fragment letter uses the absence as its hook: it names the missing number,
  says what it would let us work out, and asks for it. `letter_failures` refuses
  a letter for such a company that quotes any figure at all.

An analysis with no anchor is not a failed analysis. It is a first call with one
question in it, and saying so is more useful than a range dressed as an answer.

---

## "Not recorded" is not "none"

An analysis written before 2026-09-10 records no anchor. That is a third state
and it is treated as one: `anchor_rank` puts it *below* `none`, because `none` is
a finding — we looked and there was nothing — and an absent record is a document
that never asked the question.

The fragment letter will not quote a figure from an analysis with no anchor
recorded, even though the figure is real. A number whose basis we cannot state is
a number we cannot ask to have corrected, and the correction is the entire point
of the letter. The fix is to regenerate the analysis
(`python -m tools.analyst --reanchor`), not to guess retroactively at what it was
sized to.

---

## What the order actually resolved to, on 2026-09-10

Across all 872 companies:

| Anchor | Companies |
| --- | --- |
| a volume they stated | **0** |
| a headcount | 129 |
| their award, as capital | 348 |
| nothing | 395 |

**The strongest anchor never fires**, and that is worth recording rather than
quietly noting. Not one company in either set publishes how much of the work
there is — no "we quote about two hundred jobs a month" on any about page, in
any case study, or in any government record we hold. It is not a gap in the
reader; the sentence is not there to read.

The rule stays, for two reasons. A stated volume is the one anchor a prospect
cannot argue with, so it must be used the moment one appears — and there is now
a path for one to appear that does not depend on a crawler finding it: the
operator's evidence panel. A number heard on a call and typed in becomes the
strongest anchor in the file, and every figure in the document re-sizes to it.

Which is also the honest reading of the fragment letter. Its whole ask is for
exactly this number, from exactly the person who has it.
