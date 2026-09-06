# The offer-engineering engine — three approaches per company

**Registered 2026-09-06.** This document records what the second engine is for,
what it is allowed to say, and which rules it inherited from the outbound gate
rather than being freed from. `lib/peers.py` and `lib/pricing.py` hold the
inputs, `tools/analyst/main.py` holds the generator and its gate, and this file
is the reasoning.

---

## Why a second engine

The pipeline had one generator, and it wrote to strangers. Everything about the
drafter is shaped by that reader: typed sentences, one hypothesis, no notation,
nothing a person could not check for themselves. Those rules are right, and they
cost the drafter most of its range — which is why, after the batch recorded in
`GATE.md`, the operator composes outbound by hand.

The thing the operator actually needs before a call is not a better email. It is
an answer to "what would we build for these people, and what is it worth?" That
document has a different reader — us — and holding it to rules written for a
cold email produces a worse document for no gain in safety.

So: a second engine, internal only, never prospect-facing. The email drafter and
the leave-behind path are untouched by it.

---

## What was relaxed, and what was not

**Relaxed.** Sentence typing. There is no `fact` / `assumption` / `about_us` /
`inference` accounting here and no per-sentence claim map. Nobody needs
protecting from a paragraph of our own reasoning in our own document.

**Not relaxed, and these are the whole product:**

- **Every figure is a range, or it names its source in the same sentence.** A
  point figure with nothing behind it is the one thing the document may not
  contain, because the operator will repeat it out loud on a phone call and will
  treat it as knowledge. A source is either a claim reference or one of a closed
  list of phrases — "the grant record", "their careers page", "the peer table".
  "Industry data suggests" is not on that list and will not be added to it.
- **Inferences read as inferences.** The same reasoning markers the outbound
  gate uses. State the fact, then say what you read into it.
- **No internal vocabulary.** Not for the prospect's sake — for the operator's.
  Those words are shorthand for machinery, and an analysis that reaches for them
  is describing our pipeline where it should be describing their business.
- **Input is untainted claims plus the peer dataset, and nothing else.**
- **Feasibility is two statements and must read as two.** What the evidence
  shows is in place, and what is assumed and must be checked on the call. A
  feasibility paragraph that reads as one confident block is unusable, because
  the operator cannot tell which half will collapse.
- **The evidence floor still binds.** Below three assertable facts there is
  nothing to cost and nothing to scope. A thin analysis carries the sections the
  evidence can still hold — the business, where they stand, what to ask — and no
  findings and no priced approaches. Generating those from two facts is exactly
  the failure the floor exists to prevent, and it does not become acceptable
  because the document is internal.

---

## The three approaches are the deliverable

Not the findings. A findings document tells an operator what is wrong; three
costed approaches tell them what to sell, and give them somewhere to go when the
first idea lands badly on the phone.

Which is why distinctness is a **gate**, not a style note. Two tests, because
there are two ways to fake three strategies:

| Failure | Test |
| --- | --- |
| One build at three prices | the core builds overlap above a threshold |
| The same idea renamed | the same problem attacked with the same engagement shape |
| A pilot against the same problem as a build | **passes** — that is a real choice |

An analysis whose approaches collapse is regenerated with the two offending
descriptions quoted back at it.

---

## Where the numbers come from

Three places, and nowhere else.

**Their own evidence**, cited by claim reference. The references stay in the
stored document and are rendered small and grey rather than stripped: they are
why an operator can check a sentence, so tidying them away for looks would
remove the reason the document is trusted.

**The peer table** (`lib/peers.py`), computed from our own dataset before the
model is called and handed over as conclusions it may not add to. Every company
in a comparison is a row in this database, gathered the same way, so the reader
can open any of them. A benchmark from an outside report is a number we cannot
defend, cannot date, and cannot let a prospect correct.

Peer groups are built from industry keyword families, size band, and the
industry code where we hold one. Under four comparable companies a position is
noise, so the group widens — family at any size, then neighbouring families,
then every manufacturer — and **the rung it stopped at is always reported**.
Company size builds the group and is never itself ranked: the size figures we
hold are often somebody's estimate, and ranking on an estimate manufactures a
finding out of a guess.

**The engagement ladder** (`lib/pricing.py`), whose bands are copied rather than
chosen. A quoted price that is not one of the ladder's bands is a rejection, so
a price cannot be invented by a generator having a good day.

Payback is **computed by us** from the ladder band and the return band, and is
never asked of the model — worst case against best case, because quoting the
midpoint of each would produce one flattering number, which is a point estimate
with a division sign in front of it.

---

## Open: the ladder is unconfirmed

`pricing.CONFIRMED` is `False`. The durations and bands are derived from the one
constraint the rest of the pipeline already commits to — bounded work of two to
four weeks, not a platform and not a retainer — and from nothing else. They have
not been checked against a real quote.

Until they are, every run prints the caveat and every rendered analysis carries
it, because a price band a reader assumes is settled is worse than one they know
to check. Correcting the numbers and flipping the flag is one commit, and every
analysis generated afterwards moves with it.

---

## Four bugs this engine found in the old gate

Recorded because each had been live for the whole of the drafter's history and
none was visible from the drafter's own output. All four are the same shape: a
rule that was right about its target and wrong about its edges, surviving
because the drafter's prompt happened to steer around them.

**Percentage ranges were read as point estimates.** `RANGE_SPAN` in
`lib/formula.py` stopped at the first digit of `10% and 20%`, so a sentence
doing exactly what the formula asks — publishing a range so it can be corrected
— came back refused for stating two point figures. `between 20 and 40 percent`
passed the whole time, which is why it survived: the bug only bit when the
writer put the sign on both numbers instead of spelling the word once at the
end. Fixed, and the outbound gate gets the fix too.

**Two claim references side by side merged into one.** The path pattern allowed
brackets anywhere inside a reference so that an indexed claim like
`leadership_quotes[12]` would match. The cost was that `[a.b][c.d]` matched as
the single id `a.b][c.d`, which exists nowhere — so a sentence citing two claims
correctly was refused for citing one claim that does not qualify. The drafter
asks for one citation at the end of a sentence, which is why it never surfaced
there; the analysis cites several in a line and found it on the first company
with a readable site.

**A standard's designation read as a quantity.** "A shop certified to ISO 13485"
was refused for stating an unsourced figure of 13485. The digits are part of a
name and measure nothing, and certifications are among the strongest things the
evidence holds.

**Token budgets were sized for the prose.** The first analyst run set its output
ceiling from the word count and every call came back empty. The reasoning a
model does before it writes is budgeted as output as well, and on a document
with six sections and a distinctness constraint it is the larger half — so the
whole budget went on thinking and the ceiling cut the reply off before a single
block was emitted. It looks like a model problem and is an arithmetic one.

---

## Change log

- **2026-09-06** — Offer-engineering engine added: `lib/peers.py` in-dataset
  benchmarking, `lib/pricing.py` engagement ladder, `tools/analyst` generator
  with a source-and-distinctness gate, and analysis rendering in the dossier and
  the console. Migration 009 adds the `analysis` artifact kind. `RANGE_SPAN`
  fixed to recognise percentage spans.
