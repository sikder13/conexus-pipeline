"""Scope-of-work analysis — three costed approaches per company, for us only.

    python -m tools.analyst --dry-run            # show what would be analysed
    python -m tools.analyst --limit 16           # analyse the top 16 P1s
    python -m tools.analyst --company "Mursix"   # one company
    python -m tools.analyst --thin               # below-floor companies, S1/S4/S6

WHAT THIS IS, AND WHY IT IS NOT THE DRAFTER

The drafter writes to a stranger. Everything about it is shaped by that: the
sentence-typing gate, the hypothesis limit, the ban on notation, the refusal to
say anything a reader could not check for themselves. Those rules cost the
drafter most of its range, and correctly so — the reader has not agreed to hear
from us and cannot cross-examine the page.

This writes to the operator, who can. So the constraint changes shape rather
than disappearing. Sentence typing is gone, because nobody needs protecting
from a paragraph of our own reasoning in our own document. What stays is
everything that keeps the arithmetic honest: a figure is a range or it names
where it came from, an inference reads as an inference, and a feasibility
statement says which half of itself was observed and which half is a guess to
be settled on the call.

THE THREE APPROACHES ARE THE DELIVERABLE

Not the findings — the approaches. A findings document tells an operator what
is wrong; three costed approaches tell them what to sell, and give them
somewhere to go when the first idea lands badly on the phone. Which is why the
distinctness check is a gate and not a style note: three prices for one build
is a discount ladder wearing a strategy costume, and it leaves the operator
with exactly one thing to say.

WHERE THE NUMBERS COME FROM

The generator does not compute. It narrates.

`lib/casefile.py` assembles the whole case before the model is called: three
evaluated `lib/finmodel.py` specs, one per approach, each input naming a claim
path, a cited benchmark or a labelled assumption; the peer table; the regional
rival comparison; the published macro series; and the offer tier the size band
routes to. Out of that comes `traceable_figures`, the complete set of numbers the
finished document may contain.

A figure in the prose that matches nothing in that set is refused BY NAME. That
is the difference between version one and version two: the old gate could ask
whether a point figure gestured at a source, and could not ask whether the figure
was right, because there was nothing to check it against.

Rounding for readability is expected — "about $45,000" for a computed $45,419 is
the right way to write it, and quoting the raw figure is a false precision the
input never had. Rounding to one significant figure is not, because $50,000 for
$45,419 is a ten per cent overstatement the reader repeats as though we measured
it.

The engagement ladder is still copied rather than chosen, and `lib/pricing.py`
still refuses a price that is not one of its bands.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from typing import Any, NamedTuple

import anthropic
from rich.console import Console
from rich.table import Table

from lib import (
    adapters,
    canary,
    casefile,
    db,
    formula,
    macro,
    market,
    peers,
    pricing,
)
from lib.evidence import BLOCK10_COMPETITORS
from lib.integrity import evidence_integrity
from lib.roi_patterns import applicable
from lib.roi_patterns import as_prompt_block as roi_prompt_block
from tools.drafter import main as drafter

ANALYST_MODEL = "claude-sonnet-5"
PRICE_PER_MTOK = (2.00, 10.00)
"""Input and output dollars per million tokens for ANALYST_MODEL.

Beside the model name so that changing one without the other is a visible edit
rather than a silently wrong total, exactly as the drafter does it."""

ANALYSIS_TOKENS = 24000
"""Output ceiling, and it has to cover the reasoning as well as the writing.

A 1,600-word analysis plus three metadata blocks lands near 2,600 tokens, and
the first live run set the ceiling at 6,000 on that basis. Every call returned
empty. The reasoning the model does before it writes is billed and budgeted as
output too, and on a document with six sections and a distinctness constraint it
is the larger half — so the whole budget went on thinking and the ceiling cut
the reply off before a single block was emitted.

The failure is worth recording because it looks like a model problem and is an
arithmetic one: budget for the thinking, not for the prose. A first pass on a
well-evidenced company spends about fourteen thousand tokens reasoning before it
writes twenty-six hundred, and a retry carrying feedback spends more, so the
ceiling is set above the retry rather than above the first attempt."""

WORDS_MIN, WORDS_MAX = 900, 1850
"""How long an analysis may run.

Raised from 1,600 when `WHERE THEY STAND` grew from one thing to three — the peer
comparison, the named-rival counts and a macro paragraph. The first batch under
the new sections landed at 1,740 words and was refused for length, which is the
length rule refusing content the format rule had just asked for. The ceiling
moves with the document; the instruction to cut the writing rather than the
arithmetic does not."""
MAX_ATTEMPTS = drafter.MAX_ATTEMPTS

FULL_SECTIONS = (
    "s1_business", "s2_findings", "approach=1", "approach=2", "approach=3",
    "lead", "s4_standing", "s5_technical", "s6_questions",
)
THIN_SECTIONS = ("s1_business", "s4_standing", "s6_questions")
"""Below the evidence floor there is nothing to cost and nothing to scope, so
the analysis is what the evidence can still carry: who they are, where they
stand against comparable companies, and what the first call must establish.
Generating findings and priced approaches from two facts is the exact failure
the floor exists to prevent, and it does not become acceptable because the
document is internal."""

SECTION_TITLES = {
    "s1_business": "The business",
    "s2_findings": "Findings",
    "lead": "Lead recommendation",
    "s4_standing": "Where they stand",
    "s5_technical": "Technical read",
    "s6_questions": "Discovery questions",
}

APPROACH_LABEL = re.compile(r"^approach\s*=\s*(\d+)$")

FINDING_NUMBER = re.compile(r"(?:^|\n|(?<=[.!?])\s)\s*(?:#{2,4}\s*)?(\d)[.)]\s")
"""Where a numbered finding starts.

A finding may open a paragraph or follow the previous one inside it — both are
normal prose and the numbering is the point either way, since an approach has to
be able to point at finding two. Matching only at a line start refused a
perfectly numbered set for being written as one block."""


class AnalysisRejected(drafter.ProseRejected):
    """Generated analysis broke a rule and must be regenerated."""


class Spend(drafter.Spend):
    """Token usage for one analyst run, priced for ANALYST_MODEL."""

    @property
    def dollars(self) -> float:
        return (self.input_tokens * PRICE_PER_MTOK[0]
                + self.output_tokens * PRICE_PER_MTOK[1]) / 1_000_000


# ------------------------------------------------------------------ sourcing

SOURCE_PHRASES = (
    "grant record", "grant listing", "grant programme", "grant program",
    "grant award", "award record", "round announcement", "case study",
    "their own site", "their site", "their website", "their own words",
    "capabilities page", "careers page", "contact page", "their own pages",
    "peer table", "the comparison", "companies we hold", "our dataset",
    "comparable", "peer", "we could measure", "the group", "engagement ladder",
    "our ladder", "our published band", "our band", "matching money", "the match",
    # The market sources, by the names the analysis is told to attribute them by.
    # A sentence that says "the Bureau of Labor Statistics reports" has named its
    # source as plainly as one carrying a claim reference.
    "bureau of labor", "labor statistics", "federal reserve",
    "industrial production", "association for manufacturing",
)
"""Ways a figure may name its source in words instead of citing a claim id.

A closed list on purpose. "Industry data suggests" is not on it and never will
be: the whole argument for this document is that every number in it can be
traced to something the reader can open, and a phrase that gestures at a source
without naming one is how that guarantee is lost one sentence at a time."""

CITATION = drafter.CITATION
CITED_BRACKET = re.compile(r"\[[^\]\n]{0,120}\]")


def strip_citations(text: str) -> str:
    """The prose with claim references removed, for checks about the writing.

    Citations are notation, and the drafter is right to refuse notation in
    prose a stranger reads. This document has a different reader, so the
    citations stay — but the jargon and readability checks run against the text
    with them taken out, so a claim path is never mistaken for a sentence.
    """
    return CITED_BRACKET.sub(" ", text or "")


def names_a_source(sentence: str, allowed: set[str]) -> bool:
    """Whether a sentence says where its figures came from."""
    if any(c in allowed for c in CITATION.findall(sentence)):
        return True
    lowered = sentence.lower()
    return any(phrase in lowered for phrase in SOURCE_PHRASES)


def unsourced_figures(text: str, allowed: set[str]) -> list[str]:
    """Point figures with nothing behind them.

    The rule the whole document rests on: a figure is either a range, which is
    visibly ours and invites correction, or it names its source. A single
    unattributed number reads as knowledge — and the reader, being an operator
    about to repeat it on a phone call, will treat it as knowledge.
    """
    failures = []
    for raw in re.split(r"(?<=[.!?])\s+", text or ""):
        sentence = raw.strip()
        if not sentence:
            continue
        points = formula.point_quantities(sentence)
        if points and not names_a_source(sentence, allowed):
            failures.append(
                f"the figure {points[0]!r} is a single number with no source, in "
                f"{sentence[:140]!r}. Either write it as a range, or say in the "
                f"same sentence where it came from.")
    return failures


def unknown_citations(text: str, allowed: set[str]) -> list[str]:
    """References to claims that do not exist or do not qualify."""
    bad = sorted({c for c in CITATION.findall(text or "") if c not in allowed})
    return [f"cites something that is not a qualifying claim: {c}" for c in bad[:4]]


def traces_to_evidence(text: str, allowed: set[str]) -> bool:
    """Whether a section rests on anything at all — a claim, or a stated range."""
    if any(c in allowed for c in CITATION.findall(text or "")):
        return True
    return bool(formula.RANGE_SPAN.search(text or ""))


def reads_as_reasoning(text: str) -> bool:
    """Whether reasoning in a section announces itself as reasoning."""
    return formula.reasons_aloud(text or "")


SUPPLY_CHAIN_TIER = re.compile(
    r"\btier[\s-]?(?:one|two|three|1|2|3)\b|\b(?:first|second)[\s-]tier\b"
    r"|\btier[\s-]?(?:one|two|three|1|2|3)?[\s-]?suppliers?\b",
    re.IGNORECASE,
)
"""'Tier 1 supplier' is the automotive industry's own words, not ours.

'tier' is on the jargon list because it is how we grade our own confidence in a
claim, and an analysis that reaches for it is describing our machinery. But half
this dataset supplies the automotive industry, where a company's tier is the
plainest available description of where it sits in the chain — and refusing the
word there costs an attempt and teaches the writing to be vaguer about the one
thing the reader most wants named."""


def jargon_in(text: str) -> list[str]:
    """Internal vocabulary that must not appear even in an internal document.

    Not for the prospect's sake here — for the operator's. The words in that
    list are shorthand for machinery, and an analysis that reaches for them is
    describing our pipeline where it should be describing their business.
    """
    cleaned = SUPPLY_CHAIN_TIER.sub(" ", strip_citations(text))
    lowered = re.sub(r"[^a-z0-9_ ]+", " ", cleaned.lower())
    return sorted(set(lowered.split()) & set(drafter.JARGON))


# ----------------------------------------------------------------- approaches

STOPWORDS = frozenset({
    "that", "this", "with", "from", "into", "over", "under", "they", "them",
    "their", "there", "then", "than", "which", "what", "when", "where", "will",
    "would", "could", "should", "about", "across", "after", "before", "between",
    "during", "each", "every", "some", "more", "most", "other", "another",
    "using", "used", "build", "built", "builds", "building", "work", "works",
    "working", "thing", "things", "around", "while", "also", "onto", "once",
    "only",
})
"""Words that carry no meaning when comparing two descriptions of a build.

"build" and "working" are in here on purpose: almost every approach description
contains them, so leaving them in makes two unrelated builds look half alike and
quietly raises the distinctness threshold nobody adjusted."""


def _significant(text: str) -> set[str]:
    """The words in a phrase that carry its meaning, for comparing two of them."""
    words = re.findall(r"[a-z]{4,}", (text or "").lower())
    return {w for w in words if w not in STOPWORDS}


def similarity(first: str, second: str) -> float:
    """How much two descriptions overlap, 0 to 1."""
    left, right = _significant(first), _significant(second)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


SAME_BUILD = 0.5
SAME_PROBLEM = 0.6
"""Where two descriptions stop being two descriptions.

Set by hand rather than tuned, and deliberately generous — the check is meant
to catch the obvious failure, three names over one build, not to police
vocabulary. An analysis rejected here is regenerated with the two offending
approaches quoted back at it."""


class Approach(NamedTuple):
    """One costed way in, with its narrative and the arithmetic we computed."""

    number: int
    name: str
    pitch: str
    core_build: str
    attacks: str
    engagement: str
    weeks: tuple[int, int]
    price: tuple[int, int]
    annual_return: tuple[int, int]
    payback: tuple[float, float]
    closes_peer_gap: str | None
    prose: str

    @property
    def roi_line(self) -> str:
        """The line every approach ends on."""
        return (
            f"{self.weeks[0]}-{self.weeks[1]} weeks · "
            f"${self.price[0]:,}-${self.price[1]:,} · "
            f"returns ${self.annual_return[0]:,}-${self.annual_return[1]:,} a year "
            f"if the assumptions above hold · "
            f"pays back in {self.payback[0]:g}-{self.payback[1]:g} months"
        )


def _pair(value: Any, label: str) -> tuple[int, int]:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise AnalysisRejected(f"{label} must be a two-item range, got {value!r}")
    try:
        low, high = int(float(value[0])), int(float(value[1]))
    except (TypeError, ValueError) as exc:
        raise AnalysisRejected(f"{label} is not a pair of numbers: {value!r}") from exc
    if low > high:
        low, high = high, low
    return low, high


def read_approach(number: int, meta: dict[str, Any], prose: str) -> Approach:
    """Turn one approach's metadata into a checked Approach, or refuse it.

    The price and duration are not read from the model — they are looked up from
    the ladder using the shape it chose, and its own figures are compared
    against them. A generator that quotes a band we do not sell is the failure
    mode this exists to catch, and it is caught by construction rather than by
    reading the output afterwards.
    """
    required = ("name", "pitch", "core_build", "attacks", "engagement",
                "annual_return")
    missing = [key for key in required if not meta.get(key)]
    if missing:
        raise AnalysisRejected(
            f"approach {number} is missing {', '.join(missing)}")

    shape = str(meta["engagement"]).strip()
    if shape not in pricing.BY_KEY:
        raise AnalysisRejected(
            f"approach {number} names the engagement shape {shape!r}, which is not "
            f"on the ladder; the shapes are {', '.join(pricing.BY_KEY)}")
    engagement = pricing.BY_KEY[shape]

    if "price" in meta:
        quoted = _pair(meta["price"], f"approach {number} price")
        if quoted != engagement.band:
            raise AnalysisRejected(
                f"approach {number} quotes ${quoted[0]:,}-${quoted[1]:,}, but the "
                f"{engagement.name} band is "
                f"${engagement.band[0]:,}-${engagement.band[1]:,}. Copy the band.")
    if "weeks" in meta:
        weeks = _pair(meta["weeks"], f"approach {number} weeks")
        if weeks != engagement.weeks:
            raise AnalysisRejected(
                f"approach {number} says {weeks[0]}-{weeks[1]} weeks, but the "
                f"{engagement.name} shape runs "
                f"{engagement.weeks[0]}-{engagement.weeks[1]} weeks.")

    annual = _pair(meta["annual_return"], f"approach {number} annual_return")
    if annual[0] <= 0:
        raise AnalysisRejected(
            f"approach {number} states a return of {annual[0]}, which cannot be "
            f"costed; give a positive range or drop the approach")
    if annual[0] == annual[1]:
        raise AnalysisRejected(
            f"approach {number} states a single return figure, not a range")

    gap = meta.get("closes_peer_gap")
    return Approach(
        number=number,
        name=str(meta["name"]).strip(),
        pitch=str(meta["pitch"]).strip(),
        core_build=str(meta["core_build"]).strip(),
        attacks=str(meta["attacks"]).strip(),
        engagement=shape,
        weeks=engagement.weeks,
        price=engagement.band,
        annual_return=annual,
        payback=pricing.payback_months(engagement.band, annual),
        closes_peer_gap=str(gap).strip() if gap else None,
        prose=prose,
    )


def distinctness_failures(approaches: list[Approach]) -> list[str]:
    """Pairs of approaches that are the same approach twice.

    Two tests, because there are two ways to fake three strategies. Sharing a
    core build is one idea at three prices. Attacking the same problem with the
    same engagement shape is the same idea renamed. Attacking the same problem
    with a genuinely different shape — a pilot where the other is a build — is
    a real choice and passes.
    """
    failures = []
    for i, first in enumerate(approaches):
        for second in approaches[i + 1:]:
            build = similarity(first.core_build, second.core_build)
            if build > SAME_BUILD:
                failures.append(
                    f"approaches {first.number} and {second.number} are the same "
                    f"build at two prices: {first.core_build!r} against "
                    f"{second.core_build!r}. Attack a different problem, or offer a "
                    f"genuinely different engagement shape.")
                continue
            if (first.engagement == second.engagement
                    and similarity(first.attacks, second.attacks) > SAME_PROBLEM):
                failures.append(
                    f"approaches {first.number} and {second.number} attack the same "
                    f"problem ({first.attacks!r} against {second.attacks!r}) with the "
                    f"same engagement shape. One of them must change.")
                continue
            if first.annual_return == second.annual_return:
                failures.append(
                    f"approaches {first.number} and {second.number} claim the same "
                    f"return to the dollar "
                    f"(${first.annual_return[0]:,}-${first.annual_return[1]:,}). "
                    f"Two different problems do not cost the same by coincidence — "
                    f"work each one out from its own evidence, and if one of them "
                    f"is a diagnostic, its return is the value of the decision it "
                    f"produces, not the whole saving the build would deliver.")
    return failures


# -------------------------------------------------------------------- parsing

class Analysis(NamedTuple):
    """One finished analysis: its sections, its approaches, and its verdict."""

    sections: dict[str, str]
    approaches: list[Approach]
    peer: dict[str, Any]
    thin: bool
    words: int

    def body(self) -> str:
        """The analysis as stored and rendered — headed sections, in order."""
        parts = []
        for label in (THIN_SECTIONS if self.thin else FULL_SECTIONS):
            # The approaches and the recommendation are not top-level sections;
            # they are assembled below and spliced in where they belong.
            if label.startswith("approach=") or label == "lead":
                continue
            parts.append(f"## {SECTION_TITLES[label]}\n\n{self.sections[label]}")
        if self.approaches:
            blocks = ["## Three approaches"]
            for approach in self.approaches:
                blocks.append(
                    f"### {approach.number}. {approach.name}\n\n"
                    f"{approach.pitch}\n\n{approach.prose}\n\n"
                    f"{approach.roi_line}")
            blocks.append(
                f"### Lead recommendation\n\n{self.sections.get('lead', '')}")
            # The approaches belong after the findings that motivate them and
            # before the peer position that they are partly answers to.
            index = next((i for i, p in enumerate(parts)
                          if p.startswith(f"## {SECTION_TITLES['s4_standing']}")),
                         len(parts))
            parts[index:index] = blocks
        return "\n\n".join(parts).strip()


def parse_analysis(raw: str, thin: bool) -> tuple[dict[str, str], list[tuple[int, dict]]]:
    """Split a reply into prose sections and approach metadata, or refuse it.

    Reuses the drafter's block reader unchanged. That parser earned its rules
    the hard way — stray text between blocks is a prose block that closed early,
    and guessing at it delivers half a paragraph — and none of those rules stop
    applying because the document downstream is different.
    """
    blocks = drafter.parse_delimited(raw)
    prose: dict[str, str] = {}
    metadata: dict[int, dict] = {}
    for block in blocks:
        if not block.label:
            raise AnalysisRejected(f"a <<<{block.kind}>>> block with no label")
        if block.kind == "PROSE":
            if block.label in prose:
                raise AnalysisRejected(f"two {block.label!r} blocks; expected one")
            prose[block.label] = block.body
            continue
        match = APPROACH_LABEL.match(block.label)
        if not match:
            raise AnalysisRejected(
                f"a data block labelled {block.label!r}; only the approaches "
                f"carry one, labelled 'approach=1' to 'approach=3'")
        metadata[int(match.group(1))] = drafter._parse_json(
            block.body, f"the details for {block.label}")

    wanted = THIN_SECTIONS if thin else FULL_SECTIONS
    missing = [label for label in wanted if label not in prose]
    if missing:
        raise AnalysisRejected(
            f"the reply has no {', '.join(missing)} block(s); it returned "
            f"{', '.join(sorted(prose)) or 'nothing'}")
    if thin:
        return prose, []

    numbers = sorted(metadata)
    if numbers != [1, 2, 3]:
        raise AnalysisRejected(
            f"expected details for approaches 1, 2 and 3; got "
            f"{numbers or 'none'}")
    return prose, [(n, metadata[n]) for n in numbers]


def word_count(sections: dict[str, str]) -> int:
    return sum(len(strip_citations(body).split()) for body in sections.values())


# ---------------------------------------------------------------------- gate

def untraceable_failures(text: str, case: casefile.CaseFile | None) -> list[str]:
    """Figures in the prose that no evaluated model, claim or benchmark produced.

    This is the rule the whole rewrite turns on. The generator no longer does
    arithmetic, so a number it wrote that the case file did not compute is not a
    rounding disagreement — it is an invention, and it is refused by name.

    Falls back to the older, weaker check when there is no case file: a point
    figure must at least name its source. That path exists for a thin analysis,
    which has no models because it has nothing to model.
    """
    if case is None:
        return []
    figures = case.traceable_figures()
    return [
        f"the figure {written!r} is not one this company's models, claims or "
        f"benchmarks produced, in {sentence!r}. Use a computed figure, or cut it."
        for written, sentence in casefile.untraceable_figures(text, figures)
    ]


def gate_analysis(
    sections: dict[str, str], approaches: list[Approach], allowed: set[str],
    thin: bool, case: casefile.CaseFile | None = None,
) -> dict[str, Any]:
    """Read the finished analysis and say whether it may be stored.

    An independent pass over what was actually written, not over what the
    generator meant. Same principle as the outbound gate and for the same
    reason: the generator is the thing that might be wrong.
    """
    failures: list[str] = []
    whole = "\n\n".join(sections.values()) + "\n\n".join(a.prose for a in approaches)

    # Traceability REPLACES the older source-phrase check where a case file
    # exists, rather than running beside it. The two disagree, and the newer one
    # is strictly stronger: it asks whether the figure is one we computed, not
    # merely whether the sentence gestured at a source. Run together, a correctly
    # narrated macro figure — "manufacturing employment fell 1.8 percent between
    # September 2023 and August 2026" — was refused for not containing one of a
    # closed list of phrases, which is the gate refusing its own new output.
    if case is None:
        failures += unsourced_figures(whole, allowed)
    else:
        failures += untraceable_failures(whole, case)
    failures += unknown_citations(whole, allowed)

    if hits := jargon_in(whole):
        failures.append(
            f"internal vocabulary in the writing: {', '.join(hits[:3])}. Say what "
            f"the thing is in the operator's words instead.")

    words = word_count(sections) + sum(
        len(strip_citations(a.prose).split()) for a in approaches)
    if not thin and not WORDS_MIN <= words <= WORDS_MAX:
        failures.append(
            f"the analysis runs {words} words; it must run between {WORDS_MIN} "
            f"and {WORDS_MAX}")

    if not thin:
        findings = sections.get("s2_findings", "")
        numbered = FINDING_NUMBER.findall(findings)
        if len(set(numbered)) < 2:
            failures.append(
                "the findings are not numbered; there must be two to four, each "
                "numbered so an approach can point at one")
        if not traces_to_evidence(findings, allowed):
            failures.append(
                "no finding cites a claim or states a range, so none of them can "
                "be checked")
        for approach in approaches:
            if not traces_to_evidence(approach.prose, allowed):
                failures.append(
                    f"approach {approach.number} rests on nothing citable — its "
                    f"arithmetic must cite a claim or state its assumed ranges")
            if "assum" not in approach.prose.lower():
                failures.append(
                    f"approach {approach.number} never labels an assumption, so a "
                    f"reader cannot tell what must be checked on the call")
        failures += distinctness_failures(approaches)

        standing = sections.get("s4_standing", "")
        if not any(word in standing.lower() for word in
                   ("of the", "peer", "comparable", "companies we hold", "group")):
            failures.append(
                "the standing section never refers to the comparison it is "
                "supposed to read, so nothing in it can be traced")

    for label, body in sections.items():
        # The recommendation is asked for in two or three sentences, so it is
        # held to a shorter floor than a section that has to carry an argument.
        floor = 15 if label == "lead" else 25
        if len(strip_citations(body).split()) < floor:
            failures.append(f"the {label} section is too short to be worth reading")

    if not reads_as_reasoning(whole):
        failures.append(
            "nothing in the analysis reads as reasoning — an inference must "
            "announce itself with words like 'suggests', 'tells me' or 'implies'")

    return {
        "passed": not failures,
        "failures": failures,
        "words": words,
        "approaches": [
            {"number": a.number, "name": a.name, "engagement": a.engagement,
             "core_build": a.core_build, "attacks": a.attacks,
             "price": list(a.price), "weeks": list(a.weeks),
             "annual_return": list(a.annual_return), "payback": list(a.payback),
             "closes_peer_gap": a.closes_peer_gap, "roi_line": a.roi_line}
            for a in approaches
        ],
        "cited": sorted({c for c in CITATION.findall(whole) if c in allowed}),
    }


# ------------------------------------------------------------------- prompts

TRANSPORT = (
    "FORMAT. Reply only in delimited blocks. Nothing before the first block, "
    "nothing between blocks, nothing after the last.\n\n"
    "  <<<PROSE label>>>\n  ...prose...\n  <<<END>>>\n\n"
    "A block carrying structured detail uses <<<MAP label>>> and contains one "
    "JSON object and nothing else. If your prose ever needs to show the "
    "characters <<<END>>> literally, write \\<<<END>>>.\n"
)

HONESTY = (
    "HOW EVERY FIGURE MUST BE WRITTEN. This document is read by a colleague who "
    "will repeat its numbers out loud on a phone call, so each one must survive "
    "being questioned.\n\n"
    "1. A figure is EITHER a range OR it names its source in the same sentence. "
    "A single number with nothing behind it is the one thing this document may "
    "never contain. Ranges look like '$25,000 to $40,000' or '$25,000-$40,000'.\n"
    "2. Name a source by ending the sentence with a CLAIM_ID in square brackets, "
    "copied exactly from the CLAIM_ID column below, or by naming it in words — "
    "'the grant record', 'their careers page', 'the peer table'. Never put a URL "
    "in the brackets.\n"
    "3. Reasoning must read as reasoning: 'that suggests', 'which tells me', "
    "'this implies'. State the fact, then say what you read into it, so the two "
    "are never the same sentence wearing one voice.\n"
    "4. A figure taken from a market source names that source IN THE SENTENCE "
    "THAT USES IT — 'the Bureau of Labor Statistics puts headcount around'. A "
    "market number attributed three sentences earlier is a number the reader "
    "cannot check where they meet it.\n"
    "5. State every assumption in the open, in the sentence that uses it: 'if "
    "quoting runs somewhere between 30 and 50 a month, then'. An assumption you "
    "do not label is a fact you cannot support.\n"
    "6. Never use our internal vocabulary. Do not write tier, claim, gate, P1, "
    "verdict, corroborated, or block followed by a number. Describe the thing.\n"
    "7. Never invent a fact to fill a gap. A gap is a discovery question.\n"
)

FEASIBILITY = (
    "FEASIBILITY IS TWO DIFFERENT STATEMENTS AND MUST READ AS TWO. For every "
    "approach, separate what the evidence SHOWS is in place — a platform we "
    "read, a certification they publish, equipment the grant record names — from "
    "what you are ASSUMING and which must be checked on the call. Write the "
    "second kind as 'assumed, verify on the call'. An approach whose feasibility "
    "reads as one confident paragraph is not usable, because the operator cannot "
    "tell which half will collapse.\n"
)

SYSTEM = (
    "You are a manufacturing operations consultant writing an internal scope-of-"
    "work analysis on one company, for the colleague who will call them. It is "
    "never shown to the company.\n\n"
    "Your job is not to describe the company. It is to work out what could be "
    "built for them, what it would be worth, and which of three genuinely "
    "different ways in to open with.\n\n"
    + HONESTY + "\n" + FEASIBILITY + "\n"
    "THE THREE APPROACHES ARE THE POINT. They must be genuinely different — "
    "different problems attacked, or genuinely different engagement shapes. "
    "Three prices for the same build will be rejected. If the evidence only "
    "supports one good idea, find a second by attacking a different problem and "
    "a third by changing the shape of the engagement — a paid diagnostic where "
    "the others are builds, a pilot where the others are finished work.\n\n"
    "Write for someone who knows the business but not this company. Plain "
    "English, no headings inside a block unless asked, no bullet-point lists "
    "where a paragraph would do.\n"
)


def format_rule(thin: bool) -> str:
    """The blocks this reply must contain, in order, and what goes in each."""
    if thin:
        return (
            TRANSPORT + "\nEmit exactly three blocks, in this order:\n\n"
            "<<<PROSE s1_business>>> — what they make, for whom, and how work "
            "likely flows, reconstructed from the evidence. Say plainly where the "
            "evidence runs out.\n"
            "<<<PROSE s4_standing>>> — where they sit against the comparable "
            "companies, using only the peer comparison supplied.\n"
            "<<<PROSE s6_questions>>> — what the first call must establish, "
            "written as questions. This is the longest of the three: the file is "
            "thin, so the questions are the deliverable.\n"
        )
    return (
        TRANSPORT + "\nEmit exactly twelve blocks, in this order:\n\n"
        "<<<PROSE s1_business>>> — the operating model reconstructed from the "
        "evidence: what they make, for whom, how work likely flows through the "
        "shop, and where the grant-funded equipment sits in that flow.\n\n"
        "<<<PROSE s2_findings>>> — two to four findings, each numbered '1.', "
        "'2.' at the start of its paragraph. For each: the observation, the "
        "evidence for it in words, why it costs money, and the cost stepped out "
        "in ranges with every assumption stated.\n\n"
        "Then, for each of approaches 1, 2 and 3, a PROSE block and a MAP block:\n"
        "<<<PROSE approach=1>>> — the scope in concrete terms (what gets built, "
        "what it connects to given their visible systems), the return arithmetic "
        "stepped out, and the feasibility read split into observed and assumed. "
        "Do not repeat the name, the price or the payback here; they are in the "
        "MAP block and are printed for you.\n"
        "<<<MAP approach=1>>> — one JSON object:\n"
        '  {"name": "short name", "pitch": "one line", "core_build": "the thing "\n'
        '   "that gets built, six to twelve words", "attacks": "the problem it "\n'
        '   "attacks, four to eight words", "engagement": "<a shape key from the "\n'
        '   "ladder>", "annual_return": [low, high], "closes_peer_gap": "<a '
        'dimension from the peer table, or null>"}\n'
        "  annual_return is whole dollars a year, a range, and must be the "
        "arithmetic your prose just stepped out. Both ends must be above zero: "
        "every approach has to say what it is worth, including a diagnostic, "
        "whose return is the value of the decision it produces — the low end of "
        "the problem it would size. If you cannot put a range on an approach, it "
        "is not an approach; choose a different one. Do NOT send a price, a "
        "duration or a payback — those are taken from the ladder and computed "
        "here.\n\n"
        "<<<PROSE lead>>> — which approach to open with and why, in two or three "
        "sentences, written for the reader named at the top of the case file. "
        "If a gain share is available, this is where it is offered as a variant "
        "of one approach, with its metric and its conditions named; if it is "
        "not available, do not mention one.\n\n"
        "<<<PROSE s4_standing>>> — three things, in this order and clearly "
        "separated. First, what the peer comparison means commercially, naming "
        "the approach where one of yours closes a gap it shows. Second, the "
        "named rivals: use the velocity sentences supplied verbatim, because "
        "their denominators are ours and a reworded count is a different claim. "
        "Third, one short paragraph of macro headwind built ONLY from the "
        "published-series sentences supplied — if none were supplied, say the "
        "macro context was not available this run and write nothing else about "
        "the wider economy.\n\n"
        "<<<PROSE s5_technical>>> — the systems they visibly run and the ones "
        "they likely run, the integration surface a build would meet, and the "
        "architecture unknowns written as things to ask.\n\n"
        "<<<PROSE s6_questions>>> — everything the first call must establish, "
        "written as questions.\n\n"
        f"LENGTH IS A HARD RULE and the first live run broke it by three "
        f"hundred words, so budget each block before you write it. The whole "
        f"reply must run between {WORDS_MIN} and {WORDS_MAX} words of prose. "
        f"Aim for: s1_business 150-200, s2_findings 250-350, each approach "
        f"120-180, lead 40-70, s4_standing 200-280 (it carries three things "
        f"now), s5_technical 100-150, s6_questions 80-120. Cut the writing, not "
        f"the arithmetic.\n\n"
        "TWO RULES ABOUT NUMBERS, and both are checked mechanically.\n"
        "1. Every figure you write must be one the case file computed, one in "
        "their own evidence, or one of the benchmark values with its publisher "
        "named in the sentence. Rounding for readability is expected — 'about "
        "$45,000' for a computed $45,419 is right, and quoting the raw figure is "
        "wrong. Inventing one is refused by name.\n"
        "2. Name the inputs in words as you narrate. 'If they run somewhere "
        "between seventy and a hundred and ten quotes a month at forty-five "
        "minutes each' tells the reader what the number depends on; the figure "
        "on its own does not, and the whole point of the arithmetic is that the "
        "prospect can correct it.\n"
    )


def build_prompt(
    prospect: dict[str, Any], group: peers.PeerGroup,
    positions: list[peers.Position], claims: list[tuple[str, dict]],
    thin: bool, notes: str, case: casefile.CaseFile | None = None,
) -> str:
    """Everything the generator is allowed to see, in the order it should read it."""
    size = group.size
    header = (
        f"COMPANY: {prospect.get('company_name')}\n"
        f"Where: {prospect.get('city') or 'city not recorded'}, "
        f"{prospect.get('county')} County · about "
        f"{prospect.get('drive_minutes')} minutes from Muncie\n"
        f"Industry, in the grant listing's own words: "
        f"{prospect.get('industry_desc')}\n"
        f"Scale: {size.words}"
        + (f" ({size.basis})" if size.headcount else "") + "\n"
        f"What the grant funded: {prospect.get('tech_purchased') or 'not recorded'}\n"
    )
    parts = [
        header,
        "EVIDENCE. Every line is something a person can open and read. A "
        "sentence that states a fact about them ends with the CLAIM_ID in "
        "square brackets.\n" + drafter.render_claims(claims),
        peers.as_prompt_block(group, positions),
    ]
    if not thin:
        if case is not None:
            parts.append(casefile.prompt_block(case))
            parts.append(
                "THE THREE APPROACHES MAP ONTO THE THREE MODELS ABOVE, in order: "
                + "; ".join(
                    f"approach {index} uses engagement={model.engagement_key}"
                    for index, model in enumerate(case.models, start=1))
                + ". Quote each one's band as written. They must still be three "
                "materially different engagements against different problems, "
                "and two that collapse into one are rejected.")
        else:
            parts.append(
                "MATH TEMPLATES. These are arithmetic, not a menu of services. "
                "Use one only where the evidence already shows the matching "
                "problem and carries the variables it needs. Each says when it "
                "must NOT be used.\n"
                + roi_prompt_block(applicable(drafter.render_claims(claims))))
        parts.append(pricing.as_prompt_block())
    parts.append(format_rule(thin))
    if notes:
        parts.append(notes)
    return "\n\n".join(parts)


# ---------------------------------------------------------------- generation

async def _call(client: Any, prompt: str, spend: Spend | None) -> str:
    """One request, streamed.

    Adaptive reasoning, because this is the hardest thing we ask a generator to
    do: six sections, three costed approaches and a distinctness constraint held
    in mind at once. The failures from a model that answers immediately are
    structural rather than stylistic — two approaches that turn out to be one,
    arithmetic that does not survive being read twice.

    Streamed because that reasoning takes real wall-clock time, and a
    non-streaming request of this size is a request that eventually times out on
    the company whose file is largest — which is the company whose analysis is
    worth the most.
    """
    async with client.messages.stream(
        model=ANALYST_MODEL,
        max_tokens=ANALYSIS_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = await stream.get_final_message()
    if spend is not None and getattr(response, "usage", None) is not None:
        spend.record(response.usage)
    if response.stop_reason == "max_tokens":
        raise AnalysisRejected(
            "the reply ran out of room before it finished; the budget is too "
            "small for this company's evidence")
    return " ".join(
        b.text for b in response.content if getattr(b, "type", "") == "text"
    ).strip()


async def analyse_prospect(
    prospect: dict[str, Any], universe: list[dict[str, Any]], client: Any,
    thin: bool = False, spend: Spend | None = None,
    failures: list[str] | None = None,
    macro_results: list[macro.SeriesResult] | None = None,
) -> tuple[Analysis, dict[str, Any]]:
    """Produce one analysis and the verdict on it."""
    claims = drafter.qualifying_claims(prospect)
    allowed = {path for path, _ in claims}

    group = peers.peer_group(prospect, universe)
    positions = peers.compare(group)
    case = None if thin else casefile.build(
        prospect, drafter.render_claims(claims), positions, group, claims,
        macro_results)

    prompt = build_prompt(prospect, group, positions, claims, thin,
                          drafter.feedback_block(failures or []), case)
    raw = await _call(client, prompt, spend)

    prose, metadata = parse_analysis(raw, thin)
    approaches = [
        read_approach(number, meta, prose[f"approach={number}"])
        for number, meta in metadata
    ]
    sections = {k: v for k, v in prose.items() if not k.startswith("approach=")}
    verdict = gate_analysis(sections, approaches, allowed, thin, case)
    verdict["models"] = (
        [m.report.spec.as_json_dict() for m in case.models] if case else [])
    verdict["case"] = _case_record(case)
    analysis = Analysis(
        sections=sections, approaches=approaches,
        peer=peers.summarise(group, positions), thin=thin,
        words=verdict["words"],
    )
    return analysis, verdict


def _case_record(case: casefile.CaseFile | None) -> dict[str, Any]:
    """What the artifact stores about the arithmetic behind it.

    The specs rather than the charts. A spec is small, serialisable and
    re-evaluable, so the dossier redraws the same picture from the same numbers
    whenever it is printed — and a chart stored as pixels could not be checked
    against the model it claims to come from."""
    if case is None:
        return {}
    return {
        "tier": case.tier.band,
        "positioning": pricing.POSITIONING,
        # Stored so the standing audit can re-check the prose against the same
        # set the gate used, rather than falling back to the weaker
        # name-a-source rule and reporting figures the gate correctly allowed.
        "figures": sorted(case.traceable_figures()),
        "gain_share": case.gain_share.model_dump() if case.gain_share else None,
        "gain_share_reason": case.gain_share_reason,
        "assumptions": [a.model_dump() for a in case.assumptions()],
        "benchmarks": sorted(case.benchmark_ids()),
        "citations": case.citations(),
        "tiebreakers": case.tiebreakers,
        "velocity": [line.sentence for line in case.gap_table.velocity()]
                    if case.gap_table else [],
        "scarcity": [line.sentence for line in case.gap_table.scarcity()]
                    if case.gap_table else [],
        "rival_basis": case.gap_table.basis if case.gap_table else "",
        "macro": [line.sentence for line in case.headwind.lines]
                 if case.headwind else [],
        "macro_status": case.headwind.status_line if case.headwind else "",
    }


CONCURRENCY = 4
"""How many companies are analysed at once.

Sequential was the honest first shape and it does not survive the batch: one
analysis spends minutes reasoning, and twenty-one of them in a row is most of an
afternoon for work that has no ordering between companies. Four at a time is
chosen to stay well inside the API's limits rather than to go as fast as
possible — the politeness rules that bind the harvester are about the sites we
read, and this reads nothing.

Each company's console output is collected and printed in one block when it
finishes, so a batch running four wide still reads as one company at a time."""


def supersede_earlier(prospect_id: str) -> int:
    """Retire the analyses this one replaces. Returns how many moved.

    Without this, a company that was analysed twice keeps two live records and
    both read as current. The audit found the consequence before a person did:
    it reported failures against an analysis that had already been regenerated,
    because a superseded record that still says 'sendable' is indistinguishable
    from the one actually in the dossier.

    Rows are kept, as everywhere else here. What an earlier gate refused, and
    what an earlier prompt produced, is the most useful thing to have when the
    next rule is proposed.
    """
    moved = 0
    for artifact in db.artifacts_for(prospect_id):
        if artifact.get("kind") == "analysis" and artifact.get("status") in (
                "sendable", "blocked", "draft"):
            db.set_artifact_status(artifact["id"], "superseded")
            moved += 1
    return moved


async def _restand_and_store(
    prospect: dict[str, Any], universe: list[dict[str, Any]], client: Any,
    spend: Spend,
) -> list[str]:
    """Rewrite one company's WHERE THEY STAND and store the updated analysis."""
    lines = [f"\n[cyan]restanding {prospect.get('company_name')}[/cyan]"]
    live = next((a for a in db.artifacts_for(prospect["id"])
                 if a.get("kind") == "analysis"
                 and a.get("status") in ("sendable", "blocked")), None)
    if live is None or not live.get("body"):
        lines.append("  [yellow]no analysis to rewrite[/yellow]")
        return lines
    if (live.get("gate_map") or {}).get("thin"):
        # Below the floor there are no approaches to point a gap at and no
        # findings to stand beside. The thin section stays as it is.
        lines.append("  [dim]thin analysis; left alone[/dim]")
        return lines

    attempt, prose, verdict = 1, "", None
    feedback: list[str] = []
    while attempt <= MAX_ATTEMPTS:
        try:
            prose, verdict = await rewrite_standing(
                prospect, universe, client, live, spend, feedback)
        except drafter.ProseRejected as exc:
            lines.append(f"  [yellow]attempt {attempt} rejected:[/yellow] {exc}")
            feedback = [str(exc)]
            attempt += 1
            continue
        if verdict["passed"]:
            break
        feedback = list(verdict["failures"])
        lines.append(f"  [yellow]attempt {attempt} blocked:[/yellow] "
                     + "; ".join(f[:100] for f in feedback[:2]))
        attempt += 1

    if not prose or verdict is None:
        lines.append("  [red]left unchanged[/red] — the rewrite never parsed")
        return lines

    # What the analysis was worth BEFORE any standing rewrite touched it. Read
    # once and carried forward, because the alternative is what the first run
    # did: a rewrite that blocked the record made every later rewrite inherit
    # that block, so a section that fixed its own failure could never restore
    # the document it had demoted.
    meta = dict(live.get("gate_map") or {})
    base_status = meta.get("base_status") or (
        live.get("status") if not meta.get("market")
        # A record already rewritten once, before this field existed. Its own
        # status is the rewrite's verdict, so read the document instead: an
        # analysis that passed was stored with no failures against it.
        else ("blocked" if live.get("gate_failures") else "sendable"))
    meta["base_status"] = base_status
    passed = bool(verdict["passed"]) and base_status == "sendable"
    meta["peer"] = verdict["peer"]
    meta["market"] = verdict["market"]
    supersede_earlier(prospect["id"])
    db.insert_artifact({
        "prospect_id": prospect["id"], "kind": "analysis",
        "status": "sendable" if passed else "blocked",
        "body": splice_standing(live["body"], prose),
        "gate_map": meta,
        "gate_failures": (live.get("gate_failures") or []) if passed
                         else (verdict["failures"] or live.get("gate_failures") or []),
        "claims_cited": live.get("claims_cited"),
        "attempts": min(attempt, MAX_ATTEMPTS),
        "model": ANALYST_MODEL,
    })
    lines.append(
        f"  [{'green' if passed else 'red'}]"
        f"{'rewritten' if passed else 'rewritten, still blocked'}[/] after "
        f"{min(attempt, MAX_ATTEMPTS)} attempt(s) · {verdict['words']} words · "
        f"running spend {spend.line()}")
    return lines


async def _analyse_and_store(
    prospect: dict[str, Any], universe: list[dict[str, Any]], client: Any,
    thin: bool, spend: Spend,
    macro_results: list[macro.SeriesResult] | None = None,
) -> list[str]:
    """Analyse one company, store the result, and return what to print.

    Returns its output rather than printing it because several of these run at
    once, and interleaved half-lines from four companies is a log nobody can
    read afterwards.
    """
    lines = [f"\n[cyan]analysing {prospect.get('company_name')}[/cyan]"]
    attempt, result, verdict = 1, None, None
    rejections: list[str] = []
    feedback: list[str] = []
    while attempt <= MAX_ATTEMPTS:
        try:
            result, verdict = await analyse_prospect(
                prospect, universe, client, thin, spend, feedback, macro_results)
        except drafter.ProseRejected as exc:
            rejections.append(f"attempt {attempt}: {exc}")
            feedback = [str(exc)]
            lines.append(f"  [yellow]attempt {attempt} rejected:[/yellow] {exc}")
            attempt += 1
            continue
        if verdict["passed"]:
            break
        feedback = list(verdict["failures"])
        lines.append(f"  [yellow]attempt {attempt} blocked:[/yellow] "
                     + "; ".join(f[:110] for f in feedback[:3]))
        attempt += 1

    if result is None:
        supersede_earlier(prospect["id"])
        db.insert_artifact({
            "prospect_id": prospect["id"], "kind": "analysis",
            "status": "blocked", "body": "", "gate_failures": rejections,
            "attempts": MAX_ATTEMPTS, "model": ANALYST_MODEL,
        })
        lines.append(f"  [red]blocked[/red] — never parsed: {rejections[-1]}")
        return lines

    passed = bool(verdict and verdict["passed"])
    supersede_earlier(prospect["id"])
    db.insert_artifact({
        "prospect_id": prospect["id"], "kind": "analysis",
        "status": "sendable" if passed else "blocked",
        "body": result.body(),
        "gate_map": {"peer": result.peer, "approaches": verdict["approaches"],
                     "thin": result.thin,
                     "models": verdict.get("models") or [],
                     "case": verdict.get("case") or {}},
        "gate_failures": (verdict["failures"] if passed
                          else rejections + verdict["failures"]),
        "claims_cited": verdict["cited"],
        "attempts": min(attempt, MAX_ATTEMPTS),
        "model": ANALYST_MODEL,
    })
    lines.append(
        f"  [{'green' if passed else 'red'}]"
        f"{'usable' if passed else 'blocked'}[/] after "
        f"{min(attempt, MAX_ATTEMPTS)} attempt(s) · {verdict['words']} words · "
        f"{len(result.approaches)} approach(es) · running spend {spend.line()}")
    if not passed:
        for failure in (verdict["failures"] or [])[:3]:
            lines.append(f"    [red]{failure[:160]}[/red]")
    return lines


# ------------------------------------------------- where they stand, rewritten

NO_MARKET_ADMISSIONS = (
    "no market", "no reliable market", "no sourced market", "no segment data",
    "no trend on record", "nothing on record about the market",
    "we found no source", "no market context",
)
"""Ways of saying the segment has no context we would stand behind.

Checked as a closed list rather than by looking for "market" near "no", which
is what the first version did — and a section saying "they name no competitor"
satisfied it while describing the market freely two sentences later."""


STANDING_TITLE = SECTION_TITLES["s4_standing"]

STANDING_SYSTEM = (
    "You are rewriting one section of an internal scope-of-work analysis: where "
    "this company stands. It is read by the colleague who will call them, and "
    "never shown to the company.\n\n"
    "The section has four jobs, in this order, as flowing prose and not as a "
    "list:\n"
    "1. What the peer comparison means commercially — not a restatement of the "
    "table, which is printed beside you, but what the gaps and the leads are "
    "worth. Where one of their offered approaches closes a gap the comparison "
    "shows, say so and name the approach.\n"
    "2. Where the segment itself is going, using ONLY the market statements "
    "supplied, each attributed in words in the sentence that uses it.\n"
    "3. What we observed on the sites of any competitor they name. If none is "
    "named, say plainly that they name none and that the comparison rests on "
    "the peer group.\n"
    "4. A closing paragraph on where this segment is heading on those sourced "
    "trends and what standing still costs this company. This is reasoning and "
    "must read as reasoning.\n\n"
    + HONESTY
)

STANDING_FORMAT = (
    TRANSPORT + "\nEmit exactly one block:\n\n"
    "<<<PROSE s4_standing>>> — the four jobs above, 220 to 380 words of prose. "
    "No headings inside the block.\n"
)


def competitor_block(prospect: dict[str, Any]) -> str:
    """What we hold on the rivals this company named, as prompt text."""
    block = (prospect.get("evidence_file") or {}).get(BLOCK10_COMPETITORS) or {}
    lines: list[str] = []
    for key in ("named_by_them", "observed"):
        value = block.get(key)
        entries = value if isinstance(value, list) else [value] if value else []
        for claim in entries:
            if isinstance(claim, dict) and claim.get("value"):
                lines.append(f"- {claim['value']}")
    if not lines:
        return (
            "NAMED COMPETITORS: none. They name no competitor on their site and "
            "none appears in coverage of them, and we did not infer one. Say that "
            "plainly and let the peer comparison carry the section."
        )
    return (
        "NAMED COMPETITORS. These are rivals THEY named, with what we saw on the "
        "sites we could verify. You may not add a competitor to this list.\n"
        + "\n".join(lines)
    )


def standing_prompt(
    prospect: dict[str, Any], group: peers.PeerGroup,
    positions: list[peers.Position], claims: list[tuple[str, dict]],
    context: dict[str, Any] | None, approaches: list[dict[str, Any]], notes: str,
) -> str:
    """Everything the rewrite may see, and nothing else."""
    offered = "\n".join(
        f"- approach {a['number']}: {a['name']} — attacks {a['attacks']}"
        + (f", closes the {a['closes_peer_gap']} gap" if a.get("closes_peer_gap") else "")
        for a in approaches
    ) or "- none on file"
    return "\n\n".join([
        f"COMPANY: {prospect.get('company_name')}\n"
        f"Industry, in the grant listing's own words: {prospect.get('industry_desc')}",
        "EVIDENCE you may cite:\n" + drafter.render_claims(claims),
        peers.as_prompt_block(group, positions),
        market.as_prompt_block(context),
        competitor_block(prospect),
        "THE APPROACHES THIS ANALYSIS ALREADY OFFERS. Name one only where it "
        "genuinely closes a gap the comparison shows.\n" + offered,
        STANDING_FORMAT,
        notes,
    ]).strip()


def gate_standing(prose: str, allowed: set[str], context: dict[str, Any] | None,
                  competitors: str) -> dict[str, Any]:
    """Hold the rewritten section to the rules the rest of the document keeps."""
    failures: list[str] = []
    failures += unsourced_figures(prose, allowed)
    failures += unknown_citations(prose, allowed)
    if hits := jargon_in(prose):
        failures.append(f"internal vocabulary in the writing: {', '.join(hits[:3])}")
    words = len(strip_citations(prose).split())
    if not 200 <= words <= 420:
        failures.append(f"the section runs {words} words; it must run 200 to 420")
    if not reads_as_reasoning(prose):
        failures.append(
            "nothing here reads as reasoning, and the closing paragraph is "
            "supposed to be exactly that")
    lowered = prose.lower()
    if not any(word in lowered for word in
               ("of the", "peer", "comparable", "companies we hold", "group")):
        failures.append("the section never refers to the comparison it is reading")
    if context and context.get("statements"):
        # A market statement that arrives without its source is the one thing a
        # reader cannot check, and this section exists to be checkable.
        attributions = ("bureau of labor", "federal reserve", "industrial production",
                        "labor statistics", "association for manufacturing")
        if not any(word in lowered for word in attributions):
            failures.append(
                "a market statement is used without naming where it came from; "
                "attribute it in the sentence that uses it")
    elif not any(phrase in lowered for phrase in NO_MARKET_ADMISSIONS):
        failures.append(
            "there is no market context for this segment, so the section must "
            "say so in as many words rather than describing the market from "
            "general knowledge")
    names_nobody = ("name no competitor" in competitors.lower()
                    or "none." in competitors.lower())
    if names_nobody and "competitor" not in lowered:
        failures.append("they name no competitor and the section does not say so")
    return {"passed": not failures, "failures": failures, "words": words}


async def rewrite_standing(
    prospect: dict[str, Any], universe: list[dict[str, Any]], client: Any,
    artifact: dict[str, Any], spend: Spend, failures: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Produce a new WHERE THEY STAND for one company, and the verdict on it."""
    claims = drafter.qualifying_claims(prospect)
    allowed = {path for path, _ in claims}
    group = peers.peer_group(prospect, universe)
    positions = peers.compare(group)
    context = db.market_context(
        market.context_key(group.family.key, prospect.get('source_adapter')))
    meta = artifact.get("gate_map") or {}
    prompt = standing_prompt(prospect, group, positions, claims, context,
                             meta.get("approaches") or [],
                             drafter.feedback_block(failures or []))

    async with client.messages.stream(
        model=ANALYST_MODEL, max_tokens=ANALYSIS_TOKENS,
        thinking={"type": "adaptive"}, output_config={"effort": "high"},
        system=STANDING_SYSTEM, messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = await stream.get_final_message()
    if spend is not None and getattr(response, "usage", None) is not None:
        spend.record(response.usage)
    if response.stop_reason == "max_tokens":
        raise AnalysisRejected("the rewrite ran out of room before it finished")
    raw = " ".join(b.text for b in response.content
                   if getattr(b, "type", "") == "text").strip()

    blocks = drafter.parse_delimited(raw)
    prose = next((b.body for b in blocks
                  if b.kind == "PROSE" and b.label == "s4_standing"), "")
    if not prose:
        raise AnalysisRejected("the reply carried no s4_standing block")
    verdict = gate_standing(prose, allowed, context, competitor_block(prospect))
    verdict["peer"] = peers.summarise(group, positions)
    verdict["market"] = context or {}
    return prose, verdict


def splice_standing(body: str, prose: str) -> str:
    """Put the rewritten section back where the old one was.

    Only this section moves. Regenerating the whole document to change one
    paragraph would re-roll the findings and the three approaches, which have
    already been judged and, for most of these companies, already passed.
    """
    heading = f"## {STANDING_TITLE}"
    if heading not in body:
        return body.rstrip() + f"\n\n{heading}\n\n{prose.strip()}"
    head, _, tail = body.partition(heading)
    rest = tail.split("\n## ", 1)
    remainder = f"\n## {rest[1]}" if len(rest) > 1 else ""
    return f"{head}{heading}\n\n{prose.strip()}\n{remainder}"


# -------------------------------------------------------------------- the run

def blocked_last_time(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The companies whose most recent analysis was refused.

    A gate bug invalidates the refusals it caused, and re-running the whole batch
    to recover them pays again for every analysis that was fine and risks
    turning a pass into a failure for no reason. This selects only the records
    the fix could plausibly change.
    """
    newest: dict[str, dict[str, Any]] = {}
    for artifact in db.all_artifacts():
        if artifact.get("kind") != "analysis":
            continue
        current = newest.get(artifact["prospect_id"])
        if current is None or artifact["created_at"] > current["created_at"]:
            newest[artifact["prospect_id"]] = artifact
    return [p for p in prospects
            if (found := newest.get(p["id"])) and found.get("status") == "blocked"]


def published_contacts(prospect: dict[str, Any]) -> int:
    """How many addresses and numbers this company has published itself.

    A tiebreaker, not a score. Between two companies of equal signal the one an
    operator can actually reach is the one to write first, and that is a fact
    about their site rather than a judgement about their business."""
    return casefile.read_tiebreakers(prospect)["published_contacts"]


def evidence_richness(prospect: dict[str, Any]) -> int:
    """Usable claims in the file. The last tiebreaker, and the weakest."""
    from lib.integrity import is_usable, iter_all_claims
    return sum(1 for _path, claim in iter_all_claims(prospect.get("evidence_file") or {})
               if is_usable(claim))


def ranked_selection(
    count: int, adapter: str | None, verdicts: tuple[str, ...],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The top N companies to analyse, and the counts behind the shortlist.

    The order is signal score, then whether they can be reached, then how much
    evidence there is. P1s first and in full; the best P2s that clear the
    drafting floor fill the rest. Nothing that failed the size gate without an
    override is eligible at all, because the size gate is about whether we
    should be selling to them and no amount of signal answers that.

    Returns the counts as well as the rows so the operator is told how many
    there were to choose from — a shortlist of twenty against a request for
    twenty-five is a finding, not a rounding.
    """
    rows = db.list_prospects_full(adapter)
    gated = [p for p in rows
             if not (p.get("size_review") and not p.get("size_override"))]
    eligible = [p for p in gated if evidence_integrity(p).passing]

    def key(prospect: dict[str, Any]) -> tuple:
        return (-(prospect.get("signal_score") or 0),
                -published_contacts(prospect),
                -evidence_richness(prospect),
                str(prospect.get("company_name")))

    first = sorted([p for p in eligible if p.get("priority") == "P1"], key=key)
    second = sorted(
        [p for p in eligible if p.get("priority") == "P2"
         and drafter.below_floor(p, verdicts) is None], key=key)
    chosen = (first + second)[:count]
    return chosen, {
        "rows": len(rows), "size_gated": len(gated), "integrity_passing": len(eligible),
        "p1": len(first), "p2_on_floor": len(second), "chosen": len(chosen),
        "asked_for": count,
    }


def select(limit: int | None, company: str | None, thin: bool,
           redo_blocked: bool, verdicts: tuple[str, ...],
           adapter: str | None = None) -> list[dict[str, Any]]:
    """The companies to analyse, in the order the operator would work them."""
    if company:
        matches = [p for p in db.list_prospects_full(adapter)
                   if company.lower() in str(p.get("company_name") or "").lower()]
        if not matches:
            raise SystemExit(f"no company matching {company!r}")
        return matches[:1]
    drafting, held = drafter.eligible_prospects(limit, verdicts, adapter)
    rows = [p for p, _reason in held] if thin else drafting
    return blocked_last_time(rows) if redo_blocked else rows


def estimate(count: int) -> float:
    """What a run of this size costs before it is run.

    Measured rather than reasoned from the prose length, which is the mistake
    that set the first token budget too low: the output side is dominated by the
    reasoning, not by the words that survive it. Roughly seven thousand tokens in
    and fifteen thousand out per company, with about two in five generated twice.
    """
    per_company = (7_000 * PRICE_PER_MTOK[0] + 15_000 * PRICE_PER_MTOK[1]) / 1_000_000
    return round(count * per_company * 1.35, 2)


CEILING = 12.00
"""What a run may cost before it has to stop and ask. Not a budget — a tripwire
against a loop that regenerates forever on a rule nobody can satisfy."""


STATE_BY_ADAPTER = {"conexus_iedc": "IN"}
"""Which state's manufacturing employment series belongs to which source.

One entry, because one adapter is American. `canada_gc` is absent on purpose:
no provincial series was found and substituting a national figure for a
provincial one would be a fabrication with a real source attached."""


def _state_series(rows: list[dict[str, Any]]) -> list[macro.SeriesDef]:
    """The state employment series for the adapters in this batch."""
    codes = {STATE_BY_ADAPTER.get(str(r.get("source_adapter"))) for r in rows}
    return [macro.state_series(code) for code in sorted(c for c in codes if c)]


async def _run(args: argparse.Namespace, console: Console) -> int:
    state = canary.read_state()
    verdicts = state.allowed_verdicts()
    # The peer universe is scoped to the same source as the run. A peer group
    # already refuses to reach across adapters (lib/peers.py), so passing the
    # whole database here would load the other source's rows to discard them.
    universe = db.list_prospects_full(args.adapter)
    if args.top:
        rows, counts = ranked_selection(args.top, args.adapter, verdicts)
        console.print(
            f"[dim]{counts['rows']} rows · {counts['size_gated']} past the size "
            f"gate · {counts['integrity_passing']} pass integrity · "
            f"{counts['p1']} P1 · {counts['p2_on_floor']} P2 clear the drafting "
            f"floor[/dim]")
        below = [p for p in rows if drafter.below_floor(p, verdicts) is not None]
        if below:
            console.print(
                f"[yellow]{len(below)} of the {len(rows)} are below the drafting "
                f"floor and get the thin sections — the business, where they "
                f"stand, and what the first call must establish. Costed findings "
                f"from two facts is the failure the floor exists to prevent."
                f"[/yellow]")
        if counts["chosen"] < counts["asked_for"]:
            console.print(
                f"[yellow]Only {counts['chosen']} companies qualify against a "
                f"request for {counts['asked_for']}. The shortlist is short "
                f"because the pool is, not because the run was trimmed.[/yellow]")
    else:
        rows = select(args.limit, args.company, args.thin,
                      args.redo_blocked, verdicts, args.adapter)

    table = Table(
        title=f"{len(rows)} compan{'y' if len(rows) == 1 else 'ies'} to analyse"
              f"{' — thin, below the evidence floor' if args.thin else ''}",
        title_justify="left")
    for column in ("Company", "Facts", "Peer group", "How it was built", "Widened"):
        table.add_column(column)
    for prospect in rows:
        group = peers.peer_group(prospect, universe)
        table.add_row(
            str(prospect.get("company_name"))[:34],
            str(len(drafter.assertable_claims(prospect, verdicts))),
            f"{group.size_of_group}",
            group.family.words[:28],
            "no" if group.widened == "family_and_size" else group.widened)
    console.print(table)

    projected = (estimate(len(rows)) if args.section == "all"
                 else round(estimate(len(rows)) * 0.45, 2))
    console.print(f"\nEstimated spend for this run: [bold]${projected:.2f}[/bold] "
                  f"(ceiling ${CEILING:.2f})")
    if projected > CEILING:
        console.print(
            "[red]Stopping: the estimate is over the ceiling.[/red] Re-run with "
            "--limit to take it in batches.")
        return 1
    if not pricing.CONFIRMED:
        console.print(f"[yellow]{pricing.CAVEAT}[/yellow]")

    if args.dry_run:
        console.print("\n[dim]--dry-run: nothing generated, nothing written.[/dim]")
        return 0

    from lib.config import settings
    if not settings.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY is not set; cannot analyse.[/red]")
        return 1

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    spend = Spend()
    gate = asyncio.Semaphore(CONCURRENCY)

    # Fetched once for the whole batch and cached for the week. The headwind is
    # the same sentence for every company in one run, and asking a public agency
    # for it twenty times would be a request they did not need to serve for an
    # answer that does not change.
    macro_results = macro.fetch(list(macro.NATIONAL) + _state_series(rows))
    status = macro.status()
    console.print(f"[dim]{status.report()}[/dim]")
    missing = [r for r in macro_results if not r.available]
    if missing:
        console.print(
            f"[dim]{len(missing)} macro series unavailable this run; the "
            f"headwind paragraph is drawn from the {len(macro_results) - len(missing)} "
            f"that came back.[/dim]")

    def thin_for(prospect: dict[str, Any]) -> bool:
        """Whether THIS company gets the thin sections.

        A ranked run mixes companies that clear the drafting floor with ones
        that do not, and the choice belongs to the company rather than to the
        run. Asking for findings and priced approaches from two assertable facts
        is the exact failure the floor exists to prevent, and it does not become
        acceptable because the rest of the batch had enough."""
        if not args.top:
            return args.thin
        return drafter.below_floor(prospect, verdicts) is not None

    async def one(prospect: dict[str, Any]) -> None:
        async with gate:
            work = (_restand_and_store(prospect, universe, client, spend)
                    if args.section == "standing"
                    else _analyse_and_store(
                        prospect, universe, client, thin_for(prospect), spend,
                        macro_results))
            for line in await work:
                console.print(line)

    await asyncio.gather(*(one(prospect) for prospect in rows))
    console.print(f"\n[dim]API spend: {spend.line()}[/dim]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write per-company scope-of-work analyses. Internal only.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--company", type=str, default=None,
                        help="analyse one company by name")
    parser.add_argument("--thin", action="store_true",
                        help="analyse the companies held back below the evidence "
                             "floor, with the sections their evidence can carry")
    parser.add_argument("--section", choices=("all", "standing"), default="all",
                        help="'standing' rewrites only WHERE THEY STAND on an "
                             "analysis that already exists, leaving the findings "
                             "and the three approaches as they were judged")
    parser.add_argument("--top", type=int, default=None,
                        help="analyse the top N by signal, reachability and "
                             "evidence — P1s first, then P2s that clear the "
                             "drafting floor")
    parser.add_argument("--redo-blocked", action="store_true",
                        help="re-analyse only the companies whose most recent "
                             "analysis was refused — for after a gate fix")
    adapters.add_argument(parser)
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be analysed; generate nothing")
    args = parser.parse_args()
    return asyncio.run(_run(args, Console()))


if __name__ == "__main__":
    raise SystemExit(main())
