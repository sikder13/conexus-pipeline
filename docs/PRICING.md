# The engagement ladder — what the work costs, and why

**Bands confirmed 2026-09-09 by the operator.** `lib/pricing.py` is the table;
this file is the reasoning behind the numbers and the record of what they were
set against. A price that lives only in code is a price nobody can argue with,
and these are meant to be argued with — by the operator, on a call, out loud.

---

## The bands

| Rung | What it is | Duration | Band | Confirmed |
| --- | --- | --- | --- | --- |
| `starter_automation` | First small thing — one workflow, end to end | 1-2 weeks | **$600 - $2,500** | ✅ 2026-09-09 |
| `diagnostic` | Paid diagnostic — the deliverable is a decision | 1-2 weeks | **$2,500 - $8,000** | ✅ 2026-09-09 |
| `scoped_build` | Scoped build — one bounded thing, delivered running | 2-4 weeks | **$8,000 - $20,000** | ✅ 2026-09-09 |
| `premium_scope` | Operations scope — several workflows joined, instrumented | 4-8 weeks | **$15,000 - $30,000** | ✅ 2026-09-09 |
| `care_plan` | Care plan — monthly, **after** a build | per month | **$500 - $1,500 / month** | ✅ 2026-09-09 |
| `pilot_then_build` | Pilot, then build — two decisions, not one | 3-6 weeks | $6,000 - $15,000 | ❌ not confirmed |
| `extended_build` | Extended build — staged, multi-system | 6-12 weeks | $25,000 - $60,000 | ❌ not confirmed |
| `retained_iteration` | Standing arrangement (superseded by the care plan) | monthly | $2,000 - $6,000 / month | ❌ not confirmed |

**Confirmation is per rung, not per module.** Four rungs plus the care plan were
signed off; three were not part of that decision and still carry the caveat. A
single module-wide flag would have marked all seven settled on the strength of a
decision about five, which is the quiet widening this codebase is careful about
everywhere else. `caveat_for()` prints the caveat only when an unconfirmed rung
is actually quoted — and the tier routing quotes confirmed rungs only, so an
ordinary analysis now prints no caveat at all.

That last point is the reason the caveat was worth removing rather than leaving
in as harmless: **a caveat attached to a settled price teaches a reader to
discount every price, including the ones that are settled.**

---

## The framing: a founding-client rate, locked 12 months

Every band renders with this phrase attached, in the analysis and in outreach,
defined once in `pricing.FRAMING` so the two cannot drift.

It does two jobs. It says the number is deliberately below where it will sit,
which is true and which the benchmarks below make checkable. And it puts a date
on that being true, so the position reads as a decision with a reason rather
than as a discount with none — the difference between "we are cheap" and "we are
buying the first ten references, and here is when that stops."

---

## What the bands were set against

Market reference points, **supplied by the operator on 2026-09-09** from their
own read of the market:

| Reference | Figure |
| --- | --- |
| SMB automation implementations, typical | $10,000 - $15,000 |
| Single-workflow automation | $5,000 - $15,000 |
| Assessments and audits | from ~$2,000 |
| Boutique consultancy, hourly | $125 - $250 / hour |

**These are recorded as the operator's figures and are not independently
sourced.** That distinction matters and is kept deliberately: `lib/benchmarks.py`
holds numbers with a publisher, a URL and a retrieval date, and these have none
of those. They are a practitioner's read of a market they sell into, which is
worth more than nothing and less than a citation, and writing them down as one
thing while treating them as the other is exactly the drift this pipeline exists
to prevent. If a sourced study of SMB automation pricing is found later, it goes
in `lib/benchmarks.py` and this table cites it.

### Where our bands sit against them

- **`scoped_build` at $8,000-$20,000** brackets the $10,000-$15,000 typical
  implementation. The floor is below it and the ceiling above: the same rung
  covers a narrow build and a wide one, and the range is where the honesty is.
- **`starter_automation` at $600-$2,500** sits *below* the bottom of the
  single-workflow range. Deliberately. It is the rung that makes the ladder start
  low enough to step onto, and its job is to be worth saying yes to without a
  meeting about it.
- **`diagnostic` at $2,500-$8,000** starts at the assessment floor. A diagnostic
  that costs less than an assessment is not a diagnostic.
- **`premium_scope` at $15,000-$30,000** is roughly 60-240 hours at the boutique
  hourly range. That is the sanity check: an operations scope that could not be
  defended as that many hours of senior work would be priced wrong.
- **`care_plan` at $500-$1,500 a month** is under one boutique day a month.

### The below-market rationale

We are below market on purpose and for a bounded period. Three reasons, in the
order they matter:

1. **References are worth more than margin right now.** A signed piece of work
   that produces a measurable result is the asset this business does not yet
   have, and it cannot be bought at any price except by doing the work.
2. **The bands are unproven against delivery.** Nothing here has been reconciled
   against how long the work actually takes on a real shop floor. Pricing at
   market while still learning the delivery cost is how a fixed price becomes a
   loss quietly.
3. **The buyer is an owner-operator who has not bought software before.** The
   first number they hear decides whether there is a second conversation. Below
   market is a way of making the first number small enough to be answered rather
   than escalated.

The framing carries the exit: the rate is locked for twelve months, after which
it is repriced against what delivery actually costs.

---

## Currency

`canada_gc` quotes **the same numerals in CAD**. Not a conversion.

The work costs what it costs to do; the two markets are close enough that a
converted figure would imply a precision these bands do not have; and a Canadian
prospect quoted an odd number — "$20,412" — would rightly ask what it was
converted from and at what rate on what date. `currency_for()` decides the label
from the source adapter and nothing else.

---

## Gain share

Available only where a baseline can be instrumented before anything changes.
Where no business system is named anywhere in a company's evidence, it is **not
offered**, and the analysis says so in terms: a baseline measured by asking
people how long things take is not a baseline, and a share computed from one is
a share computed from a memory. That is a worse arrangement for the prospect than
a fixed price, and it reads as a better one — which is why it is refused rather
than left to judgement.

The shape, as of 2026-09-09:

- **Deployment fee: the rung's floor.** Not half its band. The floor is a real
  price we would do the work for; half a band is an arithmetic convenience, and
  a prospect who later sees the fixed-price band would find the two hard to
  reconcile.
- **15% of verified savings.** A single figure, not a range. The one number in
  an arrangement neither side can fully audit that must not be negotiable is the
  one deciding what we are paid.
- **Total payment capped at 2× the fixed-price equivalent**, which is twice this
  rung's band.
- **12-month term**, stated, after which the share stops whether or not the
  saving continues.
- Five operational conditions, stated in the offer rather than discovered later.
  Each is a reason the arrangement fails if it is missing.

---

## Change log

- **2026-09-09** — Four bands confirmed against the market references above,
  plus the care plan. `diagnostic` moved from $2,500-$6,000 to $2,500-$8,000.
  Confirmation made per rung; `pilot_then_build`, `extended_build` and
  `retained_iteration` remain unconfirmed and keep the caveat. Framing rule
  added. CAD quoting for `canada_gc` at the same numerals. Care plan added as an
  S3 add-on line. Gain share changed from half-band + 15-25% capped at 2× the
  fee, to rung floor + 15% capped at 2× the fixed price over 12 months. Tier
  routing narrowed to confirmed rungs so an ordinary analysis prints no caveat.
