# Canary protocol — pre-registered send rules

**Registered 2026-08-10, before any send.** These rules are written down in
advance so that they cannot be renegotiated later, when a batch is going well
and stopping feels expensive. Every change to them appends a dated entry at the
bottom; nothing above is edited.

`lib/canary.py` is the mechanism. `canary_state` in the database is the shared
state, and it is a database row rather than a config file because a file can be
stale on one machine and an environment variable is invisible to anyone reading
the data.

---

## Why this exists

The safety model changed on 2026-08-10. Outreach now runs **without per-claim
human verification**: nobody opens the source before the sentence goes out. The
human moved from upstream — checking inputs — to downstream, breaking the
circuit on outputs.

That trade is only sound if the circuit-breaker is real. The layered machine
checks (corroboration, the adversarial claim checker, the person gate, the
outbound gate) are what make a first batch defensible. This protocol is what
makes the *second* batch defensible, because it is the only mechanism that
learns from contact with actual prospects.

One rule did not change and cannot: **the machine may never assert what it
cannot source.** Automation changed who checks, not what we may claim.

---

## The rules

### Batch size

Sends go out in batches of **10**. Not 50, not "the P1 list". A batch is small
enough that a systematic error costs ten relationships rather than a hundred,
and large enough that silence across a whole batch means something.

### Batches 1 and 2 — conservative phase

Only claims that are **corroborated by an independent source, or carry an
adversarial-checker verdict of `verbatim`**, may be asserted. `inferable` claims
are excluded entirely. All arithmetic is presented as a conditional range with
its assumptions stated inline.

### Opening up

After **two consecutive batches with zero factual-correction replies**,
`inferable` claims become eligible for assertion. This is the only widening the
protocol allows, and it is automatic only in the sense that the counter permits
it — an operator can still hold.

### The halt

**Any reply correcting a FACT halts sending pipeline-wide, immediately.**

Not a threshold. Not a rate. One is enough. A factual correction means something
we stated *with a source attached* was wrong, and because no human read that
source before it went out, every other unread assertion in every other draft is
now equally suspect. The cost of halting is a delay. The cost of not halting is
discovering on batch nine that batches one through eight were all wrong.

The halt is resumable **only by explicit operator command**, and resuming
requires a note recording what was checked and what was fixed. A halt lifted
without a note is a shrug, and the next one will be lifted the same way.

### Estimates are not facts

**A reply correcting an ESTIMATE is a success, and is explicitly not a halt.**

We publish conditional ranges precisely so that somebody will write back saying
"actually it's about forty a month". That is the model working as designed: it
converts a stranger into a corrector, which is a better first conversation than
agreement. Treating those replies as failures would push the drafter toward
vaguer, safer, less useful claims — the opposite of what we want.

The distinction is drawn by the operator when logging the reply, because it is a
judgement about what the prospect meant. When it is genuinely ambiguous, treat
it as a factual correction and halt. Being wrong in that direction costs a day.

---

## What a factual correction looks like

Recorded here so the judgement is not made from scratch under pressure.

| Reply | Verdict |
| --- | --- |
| "We don't do injection moulding, we do blow moulding." | **FACT** — halt |
| "That grant was 2021, not 2022." | **FACT** — halt |
| "Nobody here is called Jared." | **FACT** — halt, and review the person gate |
| "We're closer to 40 quotes a month than 25." | estimate — log, continue |
| "Our margin's nothing like that." | estimate — log, continue |
| "Not interested." | neither — log, continue |
| "How did you get my email?" | neither — answer honestly, continue |

---

## State machine

```
                 ┌──────────────┐
                 │  conservative │  batches 0-1: verbatim/corroborated only
                 └───────┬──────┘
                         │ 2 clean batches, 0 factual corrections
                         ▼
                 ┌──────────────┐
                 │    opened     │  'inferable' claims also assertable
                 └───────┬──────┘
                         │ ANY factual correction
                         ▼
                 ┌──────────────┐
                 │    HALTED     │  every send path raises SendHalted
                 └───────┬──────┘
                         │ operator command + note
                         ▼
                    (conservative)
```

`canary.assert_sendable()` is the gate. It **raises** rather than returning a
status, so a caller cannot proceed by ignoring a return value, and it takes no
bypass argument — a send path that wants to skip it has to delete the call,
which is visible in review in a way that `force=True` would not be.

---

## Current status

**No send path exists.** None was built in the task that created this document,
deliberately: the state machine is in place first so that when a sender is
written, the thing it must check already exists and already has tests. The audit
asserts that the halt flag exists and that the (currently zero) send paths honour
it — a check that will start doing real work the moment one is added.

---

## Change log

- **2026-08-10** — Protocol registered. Batch size 10, two conservative batches,
  one factual correction halts pipeline-wide, estimates explicitly excluded from
  the halt condition.
