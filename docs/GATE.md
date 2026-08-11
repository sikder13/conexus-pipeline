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

## Change log

- **2026-08-11** — Typed accounting introduced. `fact` unchanged; `assumption`
  and `about_us` added, each with its own conditions. Derived arithmetic
  verified input by input. Recorded after a batch in which the gate blocked
  every artifact for obeying the formula.
