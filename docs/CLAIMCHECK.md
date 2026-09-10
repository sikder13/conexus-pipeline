# The claim checker — what 'unsupported' actually meant on the Canadian set

**Registered 2026-09-09, after a QA pass ordered by the operator.** The
adversarial checker returned `unsupported` on **558 of 757** Canadian claims —
73.7% — against **39 of 141** on the Indiana set — 27.7%. A gap that size is
either a real difference in evidence quality or a defect in the checking, and
the two lead to opposite actions, so nothing Canadian was generated until it was
read.

`lib/claimcheck.py` is the checker. `tools/claimcheck/main.py` chooses the claims
and fetches the source text. This file records what the pass found.

---

## The verdict

**The checker is not wrong. It is being handed the wrong text, and asked about
claims that no source can support.** 87.3% of the Canadian `unsupported`
verdicts are mechanically identifiable input defects — not judgement calls, not
close readings. The checker did its job on every one of them and reported,
accurately, that the page in front of it did not say the thing.

**The Canadian line is HELD.** No Canadian analysis is generated until the four
defects below are fixed and the set is re-checked. Indiana is unaffected: its
claims cite the pages they were read from, which is exactly why its rate is a
third of Canada's.

---

## The sample — 25 unsupported verdicts, stratified across blocks

Each claim was compared against the source text the checker received, re-fetched
through the same politeness gate and the same text extraction the checker used.

| # | Company | Claim path | Classification | Why |
| --- | --- | --- | --- | --- |
| 1 | Sequel Tool And Mold | `block2.flags.program_recency` | **(a) defect** | a derived flag of ours, submitted for source verification |
| 2 | Magellan Biomedical | `block2.grant_program` | **(a) defect** | cites the dataset landing page, not the record |
| 3 | Northmount Industries | `block2.grant_amount` | **(a) defect** | same |
| 4 | Elemental Trucks | `block2.program_purpose` | **(a) defect** | same |
| 5 | NUTRIAG | `block2.agreement_description` | **(a) defect** | same |
| 6 | Ripple Therapeutics | `block2.program_purpose` | **(a) defect** | same |
| 7 | SeeO2 Energy | `block2.grant_awards[0]` | **(a) defect** | same |
| 8 | Trexo Robotics | `block2.agreement_title` | **(a) defect** | same |
| 9 | HCI Lighting | `block2.agreement_title` | **(a) defect** | same |
| 10 | Matrix Engineering | `block2.grant_awards[0]` | **(a) defect** | same |
| 11 | Enedym | `block2.agreement_description` | **(a) defect** | same |
| 12 | GBatteries | `block7.named_people[0]` | (b) unsupported | source says Chief Commercial Officer, claim says Chief Executive Officer |
| 13 | Future Fields Biomanufacturing | `block7.named_people[1]` | (b) unsupported | evidence is from `future.com`, a different company entirely |
| 14 | Vox Pop Labs | `block7.named_people[1]` | (b) unsupported | the name was lifted from a customer testimonial |
| 15 | Space Credibility Canada | `block7.named_people[3]` | (b) unsupported | source says Editor-in-Chief, claim says Director |
| 16 | Axe Living | `block7.named_people[2]` | (b) unsupported | source says CEO & Founder, claim says Operations Manager |
| 17 | Ripple Therapeutics | `block7.named_people[1]` | (b) unsupported | source says Co-Founder & CTO; the name itself is mangled |
| 18 | Matrix Engineering | `block7.named_people[1]` | (b) unsupported | evidence is from a New Jersey company's site |
| 19 | Cedar Valley Selections | `block7.named_people[1]` | (b) unsupported | evidence is from `cedar.com`, and the person is a customer's CFO |
| 20 | Celebright | `block1.self_description` | **(a) defect** | claim is the About page; the URL recorded is the site root |
| 21 | Inkas Safe Manufacturing | `block1.business_model` | **(a) defect** | a derived label of ours, submitted for source verification |
| 22 | Safi | `block1.self_description` | **(a) defect** | checker's reply was unparseable and failed closed to unsupported |
| 23 | PurePave Technologies | `block1.self_description` | **(a) defect** | same |
| 24 | Hylid Diagnostics | `block1.flags.compliance_regime` | **(a) defect** | a derived flag of ours |
| 25 | Inkas Safe Manufacturing | `block1.business_model_basis` | **(a) defect** | claim text is from a division sub-page; the URL recorded is the site root |

**(a) checker defect: 17 of 25 — 68%. (b) genuinely unsupported: 8 of 25 — 32%.**

The threshold for stopping was 30%. It is exceeded twice over, and the sample
understates it: block2 is 83% of the population and only 44% of this sample,
because the sample was spread across blocks rather than drawn in proportion.

---

## The same four defects, counted across all 558

The sample was read by hand; these are counted mechanically over the whole set,
so they are not an extrapolation.

| Defect | Count | Share |
| --- | --- | --- |
| The claim cites the bulk dataset's **landing page** rather than the record | 464 | 83.2% |
| The claim is one of **our own derived flags** | 62 | 11.1% |
| The checker's reply was **unparseable** and failed closed | 5 | 0.9% |
| The claim is **our own derived business-model label** | 4 | 0.7% |
| **Union of the above** | **487** | **87.3%** |
| Residual, needing a human to read | 71 | 12.7% |

Indiana, counted the same way: 8 of 39 mechanical, 31 residual. The two sets are
not measuring the same thing, and the headline rates were never comparable.

---

## The four defects, and what each one is

### 1. A bulk dataset has no per-record page — 464 claims

`canada_gc` reads the Government of Canada's Proactive Disclosure of Grants and
Contributions from the published CSV, and records every claim's `source_url` as
the dataset's landing page on `open.canada.ca`. That page is 2,938 characters of
portal metadata: what the dataset is, who publishes it, where the CSV lives. It
does not contain, and cannot contain, one recipient's award.

So the checker fetched a description of a filing cabinet and was asked whether
one file was in it. Every verdict it returned was correct about that text.

This is not a small bug and it is not only the checker's problem. **A claim's
`source_url` is the promise that a human can open it and check the fact.** For
464 Canadian claims that promise is not kept — the link opens a portal page, not
the record. The Indiana adapter cites the case-study page each fact was read
from, which is why nothing there behaves this way.

The fix is a per-record citation. The dataset exposes a searchable record view;
until a claim can cite the row it came from, a Canadian grant claim is
unverifiable by construction, however true it is.

### 2. Our own inferences were submitted for verification — 66 claims

`block1.flags.*` and `block2.flags.*` are booleans **we** derive, and
`block1.business_model` is a label **we** assign. Asking whether a company's own
page states `compliance_regime: True` is a category error: the flag is our
reading of the page, not a sentence on it. The checker answered the only way it
could.

`lib/integrity.py` already knows this — `substantive_block1` excludes flags
because "they are our own derived booleans" — but `claims_to_check` does not,
so it selects them. Two separate places disagree about what a flag is.

There is a tiering question underneath it. A derived flag carrying `tier: 1` is
a T4 inference wearing a T1 label, which is the one thing CLAUDE.md rule 6
forbids. It has no outbound consequence today, because nothing drafts from
flags — but it is exactly the shape of error the tier system exists to prevent,
and it should be corrected at the source rather than filtered downstream.

### 3. A claim cites the site root, not the page it was read from

Celebright's `self_description` is the text of an About Us page; the URL recorded
is `https://celebright.com`. Inkas's `business_model_basis` describes a
subsidiary's cement technology; the URL recorded is `https://inkas.ca`. The
checker fetched the homepage in both cases and correctly found neither passage.

Same shape as defect 1, on a smaller scale, and the same consequence: the link
does not open the fact.

### 4. An unparseable reply reads as a refusal — 5 claims

`_parse` fails closed, which is right: an unreadable verdict must never read as
approval. But it fails closed to `unsupported`, which is a **verdict**, and it is
then indistinguishable from the checker having read the page and disagreed. Safi
and PurePave both have substantial genuine overlap between claim and source, and
both are recorded as refusals of the claim.

A failure to check is not a finding. It needs its own value — `unchecked`, or an
error state — so a retry is possible and so the count of real refusals is
honest. Failing closed and failing legibly are not in tension.

---

## What the (b) verdicts turned out to be worth

All eight genuine refusals in the sample are `block7` person claims, and every
one is a real catch:

- **five title errors** — Chief Commercial Officer read as Chief Executive
  Officer, Editor-in-Chief as Director, CTO as CEO, CEO & Founder as Operations
  Manager, EVP as VP;
- **three cases of a name lifted from the wrong company** — a customer
  testimonial, a magazine byline, and a customer's CFO.

CANARY.md prices this exactly: *"Nobody here is called Jared" — **FACT** — halt,
and review the person gate.* The checker is catching, before any send, the one
class of error that ends a conversation. That is the mechanism working, and the
Canadian residual of 71 is where it is working.

Three of the eight point somewhere else as well. `future.com` for Future Fields
Biomanufacturing, a New Jersey engineering firm for Matrix Engineering & Trading,
`cedar.com` for Cedar Valley Selections: the harvester resolved a plausible
domain belonging to a different company, and then read a stranger's leadership
page as this company's. That is the Decatur Plastic Products failure with a
different cause — evidence that is internally valid and externally about someone
else — and it is a **separate stop-and-report**, recorded here and not fixed in
this pass. It is a scoring-input question, not a checker question.

---

## What must happen before a Canadian analysis is generated

1. **Cite the record, not the dataset.** A `canada_gc` claim gets a per-record
   `source_url`, or it is not checkable and must not be asserted.
2. **Stop submitting our own inferences for source verification**, and correct
   the tier they carry.
3. **Give a failed check its own state.** `unsupported` must mean the source was
   read and did not support the claim.
4. **Record the page a passage was read from**, not the site it was read on.
5. **Re-check the Canadian set** and re-measure. Only then is 73.7% a number
   about evidence quality.

Nothing here loosens a gate, and nothing here is a reason to accept an
unsupported claim. Every one of the 558 stays barred from outbound exactly as it
is today: `is_barred` treats `unsupported` as final, and that does not change
while the reason for the verdict is under review. The point of the pass was to
find out what the number meant, and it does not mean what it appeared to mean.

---

## Change log

- **2026-09-09** — QA pass registered. 25 unsupported Canadian verdicts read
  against the source text the checker received: 17 input defects, 8 genuine.
  Counted across all 558, 87.3% are mechanically identifiable input defects.
  Canadian analysis HELD pending the five items above. A separate
  domain-resolution problem recorded for its own pass.
