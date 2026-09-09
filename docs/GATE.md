# The outbound gate — typed sentence accounting

**Ruling registered 2026-08-11.** This document records a decision about what
the gate is for, taken after the gate blocked ten artifacts out of ten for
obeying the formula it was supposed to enforce.

`lib/formula.py` holds the vocabulary. `gate_prose()` in `tools/drafter/main.py`
is the enforcement. This file is the reasoning, and it is the thing to read
first if a change here is ever proposed.

---

## What went wrong

The DATA-1 outreach formula has always been three-part:

1. two or three **facts** about the company, each sourced;
2. exactly one **hypothesis**, clearly labelled as ours;
3. **arithmetic they can check and correct**, stated as a conditional range
   with its assumptions in the open.

The gate only ever enforced part one. Every sentence had to map back to a
qualifying claim about the prospect, so parts two and three were structurally
impossible to write: an assumption we are supplying is by definition not in
their evidence file, and a greeting is not a statement about them at all.

A live batch on 2026-08-10 made it concrete. Every one of these was refused,
and every one is the formula working exactly as designed:

| Sentence | Why the gate refused it | What it actually is |
| --- | --- | --- |
| "If your internal engineering time runs at a fully loaded cost somewhere between eighty and one hundred and twenty dollars an hour…" | no claim maps to it | a conditional assumption — part three |
| "I am writing to the owner or president of Polaris Laboratories." | no claim maps to it | a greeting |
| "I came across the Conexus Indiana case study about your work…" | no claim maps to it | how we came to write |
| "That is a precise structural diagnosis, not a complaint." | no claim maps to it | our own framing |
| "The danger is not outright failure but slow velocity…" | no claim maps to it | our reasoning |

The conclusion drawn was that the formula and the gate contradicted each
other. The ruling is that they did not: **the gate was incomplete, not too
strict.** It knew about one of the three legal kinds of sentence.

---

## The ruling

Sentences are **typed**, and each type carries its own burden of proof.
Nothing is exempt. An assumption is held to rules a fact is never asked to
meet.

### `fact` — the default

A statement about the company. Must map to at least one qualifying claim,
exactly as before. **Unchanged.** Any quantity in a sentence that is not
typed `assumption` and not claim-mapped is still a block, which is the rule
this whole gate exists for.

### `assumption`

A figure we are supplying so that they can correct it. It must:

- **announce itself as conditional** — `if`, `assuming`, `suppose`,
  `somewhere between`, `should that hold`. An assumption that does not
  announce itself is an assertion wearing a hedge, and the reader cannot tell
  which one they are holding.
- **state ranges, never points.** "about $30,000 a year" reads as knowledge
  however it is framed; "somewhere between $25,000 and $40,000" is visibly
  ours. A point figure in an assumption is a block.
- appear in an artifact that **invites correction** — asks them to check it
  against their own numbers, or says we would rather be told we are wrong.
  Reasoning from assumptions without ever asking to be corrected is guessing
  in public, and the whole reason to publish a range is to be written back to.

### `about_us`

The greeting, how we came to write, what we are offering, our own framing. It
must contain **no quantity at all**, must not name the company as the subject
of a factual verb, and must not put the company in subject position before a
stative verb. "I read your capabilities page" is about us. "Your line runs
three shifts" is about them, and is a fact that needs a source however it is
labelled.

### Derived arithmetic

Showing the working is encouraged, and **every input is verified, not just the
shape of the result**:

    2 x 0.20-0.40 x 40 x 40 x $80-$120 = $51,200-$153,600

is legal only when the `2`, the `20-40 percent`, the `40`s and the `$80-$120`
each already appear in the same artifact as a claim-mapped fact or a typed
assumption, and the result is a range.

A calculation contributes only its **result** to the pool of established
figures, never its inputs. Otherwise a line launders itself: put an invented
factor into the working, and the same line that uses it is the line that
establishes it. That hole existed in the first implementation and was caught
by its own tests.

---

## What this does not do

It does not create an escape hatch. Every type can fail:

- a `fact` with no claim → block, as always;
- an `assumption` with no conditional language → block;
- an `assumption` stating a point figure → block;
- an artifact with assumptions and no invitation to correct → block;
- an `about_us` sentence carrying a figure or describing the business → block;
- arithmetic built on a figure nothing established → block;
- arithmetic resolving to a point rather than a range → block;
- a type that is not one of the three → block.

Mislabelling is the obvious attack — call a claim `about_us` and skip the
sourcing. That is why `about_us` is the most constrained of the three rather
than the least.

Numbers spelled as single words are recognised (`two`, `thirty`, `eighty`);
compounds like "one hundred and twenty" are not, so anything an artifact
intends to compute with must appear in digits at least once. The prompt asks
for that directly.

---

---

## Amendment — 2026-08-11 — `inference`, a scoped hypothesis limit, and a floor

Typed accounting shipped with three types and immediately ran into a fourth
thing artifacts are made of. In the first batch under the new gate, coverage
was complete and all three types were in use, and drafts still blocked: nine
of sixteen brief sentences were typed `fact` with no claim, and reading them,
they were neither facts nor assumptions nor about us. They were **reasoning
from a fact**:

    "That placement tells me your customers treat speed of results as a
     contractual expectation, not a background preference."
    "Robotics at that stage of the workflow is a strong signal that manual
     preparation was the constraint."
    "It also means any bottleneck upstream of the analytical instrument —
     including sample preparation — shows up as a delivery delay."

Each is about the prospect, so `about_us` is wrong. None carries a figure or a
condition, so `assumption` is wrong. None restates their record, so `fact` is
wrong. This is the formula's middle third — the labelled hypothesis — and the
gate had no way to express it.

### The `inference` type

**Two requirements, both mandatory, either one missing is a block:**

1. it **cites at least one parent claim** — the fact it reasons from, which is
   the thing the reader can go and check;
2. it **shows that it is reasoning** — `suggests`, `tells me`, `signals`,
   `implies`, `which means`, `points to`, `indicates`. The marker list is in
   `lib/formula.py`.

A figure inside an inference must either follow the assumption rules (a range,
never a point) or **trace to a claim the sentence actually cited** — an
inference may quote a number it reasons from, never introduce one.

The distinction, worked through:

| Sentence | Type |
| --- | --- |
| "Your line runs three shifts", with a claim | `fact` — their record, restated |
| "That investment tells me speed is a buying criterion", citing the investment claim | `inference` — their record plus our reading, with the join visible |
| Reasoning anchored to nothing | **does not belong in the draft** |

The anchor is the load-bearing half. Without it an inference is an assertion
about someone's business in the same voice as their own published words.

### The hypothesis limit is per artifact kind

"Exactly one hypothesis" is the **cold-touch** formula, and it binds the
**email** only. An email carries one piece of reasoning; a second inference or
hedged sentence in one is rejected, as before.

The **brief** and the **thesis** have no such limit. They are documents
somebody sits down with, and reasoning at length is what they are for. Capping
them at a single inference was a rule borrowed from a different artifact.
Every inference in them still needs its anchor and its marker — the limit is
lifted, the burden is not.

The separate rule that only one T4 claim may be cited is **unchanged and still
global**: that is a source-tier constraint from CLAUDE.md rule 6, not a
formula-shape one.

### The drafting floor

**CASE-1 §6 — three Tier-1 facts minimum, or the file never ships — is now a
machine gate.** A prospect with fewer than three assertable facts
(corroborated or checker-verbatim, untainted, T1) is not drafted at all. It is
recorded as `skipped` with the reason `below evidence floor: N assertable
facts, 3 required`, shown in the console and kept in the artifact record.

This is not an error and is not a gate failure. It is the difference between
"this draft is bad" and "this should never have been attempted". Polaris, with
two assertable facts, blocked four times across three batches while the
generator filled the space it could not source with reasoning; the floor stops
that at the door and says so plainly.

---

---

## Status — 2026-08-11 — manual composition is the current mode

**The drafter did not reach batch-two readiness, and it is close.** The final
batch under retry feedback produced **7 sendable emails from 16 eligible
companies**, against a bar of 8. One batch earlier it produced 1.

So, recorded plainly: **the operator composes by hand, from the dossiers.** The
pipeline's job today is research, not composition — it finds the companies,
gathers and tiers the evidence, refuses what it cannot source, surfaces the
contact paths, and prints a briefing. The seven artifacts that did pass are
usable as written; the rest of the batch is briefing material under the
"Internal analysis" heading, which is why blocked theses are now printed for
the operator rather than withheld.

**The drafter resumes as a rollout-phase project**, not a blocker. Nothing about
the gate needs relaxing to get there — the same batch shows a draft can satisfy
all of it. What it needs is:

- more than two attempts, or attempts that keep what passed and rewrite only
  what failed. A draft is currently thrown away whole for one bad sentence out
  of eighteen;
- per-attempt failure counts recorded in the artifact, so the effect of retry
  feedback can be measured. Right now only the final attempt's failures are
  stored, and the console line caps at three, so the question "did feedback
  help?" cannot be answered from what the run wrote down;
- the evidence floor raised or the fact pool widened. Companies at exactly
  three assertable facts fail most often, which is the floor telling us three
  is the minimum for drafting and not yet the comfortable number.

---

## Amendment — 2026-09-08 — CASL, and a second refusal the prose cannot see

Canadian prospects are governed by CASL, not CAN-SPAM, and CASL is a different
shape of law. CAN-SPAM says: say who you are, give a real address, honour an
opt-out. CASL says: **do not send at all without consent**, and then defines a
small number of exemptions.

`lib/compliance.py` holds a profile per source adapter, keyed exactly as the
scoring profiles are. `conexus_iedc` keeps CAN-SPAM, unchanged.

### The exemption we rely on, and why every clause of it is tested

CASL does not apply to a commercial electronic message sent to an electronic
address the recipient has **conspicuously published**, where the publication
carries no statement refusing unsolicited messages, and where **the message is
relevant to the recipient's business role, functions or duties**.

Every clause of that is a fact about one message to one address, so the gate
tests each one rather than asserting the exemption in general:

| Clause | How it is enforced |
| --- | --- |
| conspicuously published | The address must be one `contact_discovery` read off the company's own pages, and the claim must carry the URL it was read from. An address with no source URL is not evidence that anything was published |
| no accompanying refusal | The source page travels with the address so the operator can read it |
| relevant to their role | The basis line states the relevance and is stored on the artifact |

### The rules, as enforced for `kind='email'`

- **Only a published address may be targeted.** Never an inferred one, never a
  constructed one, never a pattern. A company with no published address recorded
  cannot be emailed at all — the artifact is blocked, and the block says
  "Nothing may be guessed".
- **A guessed address written into the body blocks the artifact**, even when the
  target is fine. A draft that says "reply to dave.whitmore@…" is proposing a
  send to an address nobody published.
- **The CASL basis is recorded on the artifact**, in the new `compliance` column
  (migration 013): the regime, the basis, whether it passed, the address it was
  for, and the published addresses that were available. A blocked artifact keeps
  it too.
- **The identification block is required**: Nahl Technologies Inc., 6902
  Challenge Ln, Indianapolis IN 46250, USA, plus the opt-out line. Identical
  under both regimes, defined once in `lib/compliance.py`, and stripped before
  sentence typing exactly as before.
- **LinkedIn artifacts are unaffected by the email rules.** A LinkedIn message is
  sent by hand inside a platform with its own rules; it is not an electronic
  message to an address we hold. The regime is still recorded on it.

### Why this is a gate and not a checklist

Because the failure is invisible in the prose. `first.last@company.ca` reads
exactly like a published address in a finished draft — the difference is in the
evidence file, not in the sentence. Typed sentence accounting cannot see it,
and no amount of reading the email would catch it.

So the compliance verdict is a **second, independent refusal** that reads the
evidence rather than the text, and its failures are folded into the same email
gate result. An email whose prose is perfect and whose address was guessed comes
out `blocked`, with the reason recorded.

This is the second half of a promise the pipeline already made. The first half
is in `tools/harvester/nodes/contact_discovery.py`: no address is ever
constructed. The second half is that we cannot send to one we did not read.

### An undeclared source is not given the laxer regime

`profile_for` raises on a `source_adapter` with no profile rather than falling
back to CAN-SPAM. A new source is a new jurisdiction until somebody says
otherwise, and defaulting would answer that question by accident.

---

## Change log

- **2026-09-08** — CASL profile added for `canada_gc`, enforced on `kind='email'`
  as a second refusal alongside sentence typing: only conspicuously published
  addresses may be targeted or named, the basis is stored on the artifact
  (migration 013), and LinkedIn is out of scope of the email rules.
  `conexus_iedc` keeps CAN-SPAM unchanged.
- **2026-08-11** — `inference` type added, anchored and marked. Hypothesis
  limit scoped to the email. CASE-1 §6 evidence floor enforced as a machine
  gate with a stored skip reason (migration 008).
- **2026-08-11** — Typed accounting introduced. `fact` unchanged; `assumption`
  and `about_us` added, each with its own conditions. Derived arithmetic
  verified input by input. Recorded after a batch in which the gate blocked
  every artifact for obeying the formula.
