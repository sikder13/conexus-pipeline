"""Thesis drafter and outbound artifacts — generation behind a structural gate.

    python -m tools.drafter --dry-run          # show what would be drafted
    python -m tools.drafter --limit 10         # draft the top 10 P1s

WHAT CHANGED, AND WHY THE GATE IS LOAD-BEARING

Outreach now runs without a human checking each claim first. Nobody reads the
source before the sentence goes out. Everything that used to depend on that
reading now depends on this file refusing to emit sentences it cannot map back
to a qualifying claim.

So the formula is not a style guide here; it is enforced by construction. The
generator is handed a pre-selected set of claims that already qualify, and then
an independent audit pass reads the finished text and maps every factual
sentence back to one. A sentence that maps to nothing blocks the artifact. Two
regeneration attempts, then it goes to the operator — never out.

THE TWO-STEP THESIS, AND WHY THEY ARE SEPARATED

Step 1 sees the evidence and no pattern library. It names the most expensive
frictions the evidence supports, whatever they are. Step 2 then costs what step
1 found, using the library as arithmetic.

Merging them would be cheaper and much worse: a model shown a list of things we
can build will find the nearest one and write a diagnosis backwards from it. The
separation is what keeps the diagnosis open — step 1 cannot pattern-match to a
menu it has not been shown.

WHY THE MODEL NO LONGER REPLIES IN JSON

Prose was carried inside a JSON string, and that transport failed at scale. Two
to four paragraphs per opportunity, times several opportunities, put the reply
past roughly twelve kilobytes, and a model writing that much prose inside a
string literal eventually emits a raw newline or an unescaped quote. One stray
character invalidated the entire document, including the paragraphs that were
fine. Escaping control characters after the fact recovered some of it and was
always a patch over the wrong shape: prose is not a JSON scalar.

So the reply is now delimited. Prose is carried as text between markers, where a
quote is a quote and a paragraph break is a paragraph break, and JSON is used
only for the sentence-to-claim map, which is small, machine-shaped, and stays
readable at any prose length.

WHAT IS DELIBERATELY NOT HERE

There is no send path, and there will not be one. The operator sends every
message personally; the pipeline's job ends at a sendable artifact. Any future
export helper is copy/print/mailto only. See the 2026-08-10 amendment in
docs/CANARY.md — the canary rules still govern what may be composed, and the
operator is the dispatch.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from typing import Any, NamedTuple

import anthropic
from rich.console import Console
from rich.table import Table

from lib import (
    adapters,
    anchors,
    canary,
    compliance,
    db,
    formula,
    icp,
    pricing,
)
from lib.claimcheck import is_barred
from lib.claims import Tier, is_derivation, is_operator_entered
from lib.evidence import BLOCKS
from lib.integrity import evidence_integrity, is_usable, iter_all_claims
from lib.persongate import salutation_for
from lib.roi_patterns import applicable, as_prompt_block

JARGON = (
    "tier", "t1", "t2", "t3", "t4", "claim", "block1", "block2", "block3",
    "block4", "block5", "block6", "block7", "block8", "block9", "corroborated",
    "verdict", "verbatim", "inferable", "unsupported", "tainted", "p1", "p2",
    "signal_score", "claimcheck", "evidence_file", "prospect",
)
"""Internal vocabulary that must never appear in prose a stranger reads.

Same list the leave-behind screens for. A reader who meets "T4 hypothesis"
learns that we grade our guesses about them — true, and never their business."""

BRACKET_ID = re.compile(r"\[[a-z0-9_]+(?:\.[a-z0-9_\[\]]+)+\]")


class ProseRejected(RuntimeError):
    """Generated prose broke a shape rule and must be regenerated."""


def validate_prose(prose: str, label: str) -> None:
    """Refuse prose that is notation rather than writing.

    The generator used to emit headings and bracketed ids with little standalone
    text between them. That starved the gate of sentences to map and left the
    leave-behind with nothing once notation was stripped. These rules are the
    fix, enforced after generation rather than hoped for in the prompt.
    """
    text = (prose or "").strip()
    if not text:
        raise ProseRejected(f"{label}: no prose at all")
    if BRACKET_ID.search(text):
        found = BRACKET_ID.findall(text)[:2]
        raise ProseRejected(f"{label}: bracket notation leaked into prose: {found}")
    lowered = re.sub(r"[^a-z0-9_ ]+", " ", text.lower())
    words = set(lowered.split())
    hits = sorted(words & set(JARGON))
    if hits:
        raise ProseRejected(f"{label}: internal vocabulary in prose: {hits[:3]}")
    if len(re.findall(r"[.!?](?:\s|$)", text)) < 2:
        raise ProseRejected(f"{label}: under two sentences of prose")


def _keep(sentence: str) -> bool:
    return bool(sentence) and len(sentence.split()) >= 4 and not sentence.endswith("?")


def _sentences_of(prose: str) -> list[str]:
    """Split prose into the units the gate must account for.

    A line that ends without terminal punctuation and is followed by a blank
    line stands alone. Collapsing every newline to a space used to weld the
    salutation onto the first real sentence — "To the owner or president of
    Circle City Sonorans,  Your operation caught our attention" — producing a
    unit that appeared in no map and taking a properly sourced sentence down
    with it. The salutation still has to be accounted for; it just gets to be
    accounted for as itself.
    """
    out: list[str] = []
    for block in re.split(r"\n\s*\n", prose or ""):
        chunk = block.strip()
        if not chunk:
            continue
        lines = [ln.strip() for ln in chunk.split("\n") if ln.strip()]
        if len(lines) == 1 and not lines[0].endswith((".", "!", "?")):
            if _keep(lines[0]):
                out.append(lines[0])
            continue
        for raw in re.split(r"(?<=[.!?])\s+", " ".join(lines)):
            sentence = raw.strip()
            if _keep(sentence):
                out.append(sentence)
    return out


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").lower()).strip()


def _entry_for(
    sentence: str, lookup: dict[str, tuple[str, list[str]]]
) -> tuple[str, list[str]]:
    """The map entry covering this sentence, exact first then by containment."""
    key = _normalise(sentence)
    if key in lookup:
        return lookup[key]
    for mapped, value in lookup.items():
        if mapped and (mapped in key or key in mapped):
            return value
    return formula.DEFAULT_TYPE, []


def gate_prose(
    prose: str,
    sentence_map: list[dict],
    allowed_paths: set[str],
    hypothesis_paths: set[str],
    person_allowed: bool,
    person_name: str | None,
    company_name: str | None = None,
    kind: str = "email",
    claim_values: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Account for every sentence of the PROSE under the DATA-1 formula.

    The map is the model's account of what it cited; the prose is what a reader
    actually receives. This walks the prose and looks each sentence up, so a
    sentence the model wrote but forgot to map is unmapped — there is no way to
    smuggle an assertion past the gate by leaving it out of the JSON.
    """
    lookup = {}
    for entry in sentence_map or []:
        key = _normalise(entry.get("text", ""))
        if key:
            lookup[key] = (
                entry.get("type") or formula.DEFAULT_TYPE,
                [c for c in (entry.get("claims") or []) if c in allowed_paths],
            )
    unknown_cited = {
        c for entry in sentence_map or [] for c in (entry.get("claims") or [])
        if c not in allowed_paths
    }

    failures: list[str] = []
    mapping: list[dict[str, Any]] = []
    hypothesis_count = 0
    body = strip_signature(prose)
    sentences = _sentences_of(body)

    # Numbers the artifact has established, either by sourcing them or by
    # openly assuming them. Arithmetic may only be built out of these.
    #
    # A calculation contributes only its RESULT, never its inputs. Otherwise a
    # line launders itself: put an invented factor into the working and the
    # same line that uses it is also the line that establishes it.
    supported_numbers: set[float] = set()
    for sentence in sentences:
        sentence_type, claims = _entry_for(sentence, lookup)
        if sentence_type != formula.ASSUMPTION and not claims:
            continue
        supported_numbers |= formula.numbers_in(
            formula.result_of(sentence) if formula.shows_arithmetic(sentence) else sentence
        )

    has_assumption = False
    reasoning_sentences: list[str] = []
    claim_values = claim_values or {}

    for sentence in sentences:
        sentence_type, claims = _entry_for(sentence, lookup)
        entry = {"sentence": sentence, "claims": claims, "type": sentence_type}
        is_hypothesis = any(m in sentence.lower() for m in HYPOTHESIS_MARKERS)
        if is_hypothesis:
            hypothesis_count += 1
            entry["hypothesis"] = True

        if sentence_type not in formula.SENTENCE_TYPES:
            failures.append(
                f"unknown sentence type {sentence_type!r}: {sentence!r}")
        elif sentence_type == formula.ASSUMPTION:
            has_assumption = True
            # A line that shows its working is a derivation, not a fresh
            # assumption: the condition was carried by the inputs, which are
            # checked one by one below. Requiring it to hedge again would only
            # teach the generator to bolt "if" onto a calculation.
            if not (formula.has_conditional(sentence) or formula.shows_arithmetic(sentence)):
                failures.append(
                    f"assumption with nothing conditional about it: {sentence!r}")
            # Operands are excluded on purpose. A calculation's inputs are
            # verified one by one just below and its result must be a range;
            # reading them a second time as bare figures refuses "2 x 0.20-0.40
            # x 40 x $80-$120", which is the formula's third part written out.
            #
            # So is a figure that traces to a claim the sentence cites, which is
            # the exemption an inference has always had and an assumption was
            # never given. The rule exists because an unhedged point figure of
            # OURS is an assertion wearing a hedge — and a number they published
            # is not ours and is not a hedge. It refused "if the equipment funded
            # by the $466,300 award sits idle five to fifteen per cent of the
            # time", where the only point figure in the sentence was the award
            # on their own government record, cited in the same sentence.
            points = [
                found.text.strip() for found in formula.point_numerals(sentence)
                if found.reason != "calculation"
                and not formula.is_their_own_figure(found.text.strip(), claim_values)
            ]
            if points:
                failures.append(
                    f"assumption states a point figure, not a range "
                    f"({', '.join(points[:2])}): {sentence!r}")
        elif sentence_type == formula.INFERENCE:
            reasoning_sentences.append(sentence)
            # Both halves are mandatory. The anchor is what the reader can go
            # and check; the marker is what tells them the rest is ours. An
            # inference missing either is our conclusion wearing their voice.
            if not claims:
                failures.append(
                    f"inference with nothing to reason from — no claim cited: "
                    f"{sentence!r}")
            if not formula.reasons_aloud(sentence):
                failures.append(
                    f"inference that does not show it is reasoning: {sentence!r}")
            stray = [
                q for q in formula.point_quantities(sentence)
                if not formula.traces_to(q, claims, claim_values)
            ]
            if stray:
                failures.append(
                    f"inference states a figure that is neither a range nor in the "
                    f"claims it cites ({', '.join(stray[:2])}): {sentence!r}")
        elif sentence_type == formula.ABOUT_US:
            if formula.asserts_about_prospect(sentence, company_name):
                failures.append(
                    f"sentence typed as ours asserts something about them: "
                    f"{sentence!r}")
        elif not claims:
            if formula.has_quantity(sentence):
                failures.append(f"number with no source: {sentence!r}")
            elif not is_hypothesis:
                failures.append(f"unmapped sentence: {sentence!r}")

        # Working shown to a reader has to be working they can redo.
        unsupported = formula.unsupported_inputs(sentence, supported_numbers)
        if unsupported:
            failures.append(
                f"arithmetic uses figures the artifact never establishes "
                f"({', '.join(unsupported[:3])}): {sentence!r}")
        if formula.shows_arithmetic(sentence):
            tail = formula.result_of(sentence)
            if tail and formula.point_quantities(tail):
                failures.append(
                    f"arithmetic resolves to a point, not a range: {sentence!r}")
        mapping.append(entry)

    if has_assumption and not formula.invites_correction(body):
        failures.append(
            "the draft reasons from assumptions but never asks to be corrected")

    if unknown_cited:
        failures.append(
            f"cites claims that do not qualify: {', '.join(sorted(unknown_cited)[:3])}"
        )

    # The one-hypothesis rule is the COLD-TOUCH formula, so it binds the email
    # and nothing else. A brief and a thesis are documents somebody sits down
    # with; reasoning at length is what they are for, and capping them at a
    # single inference was a rule borrowed from a different artifact. Every
    # inference in them still needs its anchor and its marker.
    if kind == "email":
        budget = {_normalise(s) for s in reasoning_sentences}
        budget |= {
            _normalise(s) for s in sentences
            if any(m in s.lower() for m in HYPOTHESIS_MARKERS)
        }
        if len(budget) > 1:
            failures.append(
                f"{len(budget)} reasoning sentences in an email; the formula allows "
                f"exactly one hypothesis"
            )
    cited_hypotheses = {
        c for entry in mapping for c in entry["claims"] if c in hypothesis_paths
    }
    if len(cited_hypotheses) > 1:
        failures.append(f"cites {len(cited_hypotheses)} T4 claims; only one is allowed")
    if (not person_allowed and person_name
            and re.search(rf"\b{re.escape(person_name.split()[0])}\b", body)):
        failures.append(f"uses a person's name that failed the person gate: {person_name!r}")

    return {
        "passed": not failures,
        "failures": failures,
        "map": mapping,
        "sentences": len(mapping),
        "cited": sorted({c for e in mapping for c in e["claims"]}),
    }


CITE_RULE = (
    "CITATIONS: every factual sentence ends with a CLAIM_ID in square brackets, "
    "copied exactly from the CLAIM_ID column of the evidence, for example "
    "[block1_what_they_make.self_description]. NEVER put a URL inside the "
    "brackets — a URL is not a claim id and the sentence will be rejected. A "
    "sentence carrying no bracketed CLAIM_ID is discarded.\n"
)
"""One wording, used by every prompt.

The first live run failed on this: the model bracketed source URLs instead of
claim ids, so the gate rejected every artifact. The gate was right; the
instruction was ambiguous because the evidence lines showed both a path and a
URL and did not say which one to copy."""

THESIS_MODEL = "claude-sonnet-4-6"
TEMPERATURE = 0.2
MAX_ATTEMPTS = 2

STEP1_TOKENS = 1600
STEP2_TOKENS = 8000
EMAIL_TOKENS = 4000
"""Output budgets, sized to the artifact rather than to a round number.

The first delimited run truncated: step 2 stopped at exactly its 3200-token
ceiling, mid-string, roughly 15KB in. The parser did its job and refused the
half-written reply, but the reply was fine — the budget was not. Four
opportunities of real paragraphs plus their maps is 4000-5000 tokens, and the
ceiling has to sit clear of that rather than on top of it. These stay under the
point where a non-streaming request risks an HTTP timeout."""

PRICE_PER_MTOK = (3.00, 15.00)
"""Input and output dollars per million tokens for THESIS_MODEL.

Published list price. It lives beside the model name so that changing one
without the other is a visible edit rather than a silently wrong total."""


class Spend:
    """Token usage for one run.

    A batch that regenerates twice for every prospect costs three times what
    the happy path costs, and that only shows up if something counts it. This
    counts calls too, because the interesting number when a format is failing
    is how many attempts it took, not just the dollars.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def record(self, usage: Any) -> None:
        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0

    @property
    def dollars(self) -> float:
        return (self.input_tokens * PRICE_PER_MTOK[0]
                + self.output_tokens * PRICE_PER_MTOK[1]) / 1_000_000

    def line(self) -> str:
        return (f"{self.calls} call(s) · {self.input_tokens:,} in · "
                f"{self.output_tokens:,} out · ${self.dollars:.2f}")

# The identification block has one definition, in lib/compliance.py, because
# two laws demand it and neither is satisfied by a second copy that drifts.
# Re-exported here so the prompts below can quote it.
SENDER_NAME = compliance.SENDER_NAME
SENDER_COMPANY = compliance.SENDER_COMPANY
SENDER_ADDRESS = compliance.SENDER_ADDRESS
OPT_OUT = compliance.OPT_OUT

CITATION = re.compile(r"\[([a-z0-9_]+(?:\.[a-z0-9_]+(?:\[\d+\])?)+)\]")
"""A claim reference in generated text, e.g. [block2_grant_funded.grant_amount].

The index suffix is spelled out rather than folded into the path characters.
Written the loose way — brackets allowed anywhere inside — two references side
by side merged into one: "[a.b][c.d]" matched as the single id "a.b][c.d",
which exists nowhere, so a sentence that cited two claims correctly was refused
for citing one claim that does not qualify. The drafter's prompt asks for one
citation at the end of a sentence, which is why this went unseen there; the
analysis cites several in a line and found it immediately."""

has_quantity = formula.has_quantity
"""Re-exported so the citation gate and the formula cannot drift apart.

Whether a numeral asserts a quantity is decided in ``lib/numerals.py``, where a
number is a figure only when it carries quantity context — money, a proportion,
a unit, a rate, or a place in a calculation. A phone number is not a figure."""

HYPOTHESIS_MARKERS = (
    "we think", "our hypothesis", "we suspect", "if that is right",
    "we would guess", "our guess", "we may be wrong",
)
"""Template language a T4 claim must be wrapped in. A hypothesis that does not
announce itself is an assertion."""


# ------------------------------------------------------------------ selection

def qualifying_claims(prospect: dict[str, Any]) -> list[tuple[str, dict]]:
    """Claims a draft may cite at all: usable, not barred, inside a real block."""
    out = []
    for path, claim in iter_all_claims(prospect.get("evidence_file") or {}):
        trimmed = path.removeprefix("evidence_file.")
        if trimmed.split(".")[0] not in BLOCKS:
            continue
        if not is_usable(claim) or is_barred(claim):
            continue
        out.append((trimmed, claim))
    return out


def assertable_claims(
    prospect: dict[str, Any], verdicts: tuple[str, ...]
) -> list[tuple[str, dict]]:
    """Claims strong enough to state as fact in outbound text.

    T1, NOT a derivation, and (corroborated OR an allowed checker verdict). This
    is the DATA-1 formula's "2-3 facts" pool, enforced by selection rather than
    by asking the model nicely.

    The derivation exclusion was found by repairing something else. Trifecta
    Medical cleared the three-fact floor on `named_people`, `self_description_raw`
    and `flags.has_case_study` — and the third of those is our own boolean about
    whether a case study exists, not a fact about the company from a source.
    CASE-1 §6 asks for three Tier-1 facts, and a flag we computed is not one
    however true it is. The floor was inflated by exactly the claims that could
    never fail a check, because they were never checkable.
    """
    return [
        (path, claim) for path, claim in qualifying_claims(prospect)
        if claim.get("tier") == int(Tier.T1)
        and not is_derivation(claim)
        and (claim.get("corroborated") is True
             or claim.get("claimcheck") in verdicts
             # An operator entry is a fact a person read off the source and
             # typed in. The corroboration and the checker are both stand-ins
             # for that reading, so requiring one of them here would refuse the
             # strongest evidence in the file for lacking a substitute for
             # itself — and would leave a company held below the floor that a
             # human had just finished researching.
             or is_operator_entered(claim))
    ]


def hypothesis_claims(prospect: dict[str, Any]) -> list[tuple[str, dict]]:
    """T4 inferences — exactly one of which may appear, clearly labelled."""
    return [
        (path, claim) for path, claim in qualifying_claims(prospect)
        if claim.get("tier") == int(Tier.T4)
    ]


def render_claims(claims: list[tuple[str, dict]]) -> str:
    """Claims as prompt lines, carrying everything the model must respect."""
    lines = []
    for path, claim in claims:
        marks = []
        if claim.get("corroborated"):
            marks.append(f"corroborated by {claim.get('corroborated_by')}")
        if claim.get("claimcheck"):
            marks.append(f"checker: {claim['claimcheck']}")
        if claim.get("conflict"):
            marks.append("CONFLICTED — sources disagree, do not assert")
        suffix = f"  [{'; '.join(marks)}]" if marks else ""
        lines.append(
            f"CLAIM_ID {path} | T{claim.get('tier')} | {claim.get('value')}"
            f" | source: {claim.get('source_url')}{suffix}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------- delimited transport

BLOCK_OPEN = re.compile(r"(?<!\\)<<<[ \t]*(PROSE|MAP)\b[ \t]*([^>\n]*?)[ \t]*>>>")
BLOCK_CLOSE = re.compile(r"(?<!\\)<<<[ \t]*END[ \t]*>>>")
ESCAPED_DELIMITER = re.compile(r"\\(?=<<<)")
"""The transport grammar.

Whitespace inside and around a marker is insignificant, so ``<<<MAP>>>{...}
<<<END>>>`` on one line and the same three tokens on three lines parse
identically. Nothing else is tolerated: a malformed reply is regenerated rather
than guessed at.

THE ESCAPE RULE. A backslash immediately before ``<<<`` makes the marker
literal text, and the backslash is removed from the prose. So a paragraph that
genuinely needs to show the characters ``<<<END>>>`` writes ``\\<<<END>>>``.
An UNESCAPED ``<<<END>>>`` inside prose closes the block early, which strands
the rest of the paragraph outside any block, which is a parse error and a
regeneration. That is the intended direction to fail: the alternative is
silently delivering a truncated paragraph to a stranger."""


class Block(NamedTuple):
    """One delimited block: its kind, its label, and its verbatim contents."""

    kind: str
    label: str
    body: str


class Section(NamedTuple):
    """A prose block paired with the sentence map that must accompany it."""

    label: str
    prose: str
    sentence_map: list[dict]


def parse_delimited(raw: str) -> list[Block]:
    """Split a model reply into blocks, or refuse it.

    Stray text is tolerated only BEFORE the first block, where it can only be a
    preamble the model was told not to write. Between or after blocks it is
    refused, because there the likeliest cause is a prose block that closed
    early on an unescaped marker — and quietly dropping the remainder of a
    paragraph is exactly the failure this format exists to prevent.
    """
    text = raw or ""
    blocks: list[Block] = []
    cursor = 0
    while (opener := BLOCK_OPEN.search(text, cursor)) is not None:
        if blocks and (stray := text[cursor:opener.start()].strip()):
            raise ProseRejected(f"text between blocks: {stray[:80]!r}")
        closer = BLOCK_CLOSE.search(text, opener.end())
        if closer is None:
            raise ProseRejected(
                f"unterminated <<<{opener.group(1)} {opener.group(2)}>>> block"
            )
        nested = BLOCK_OPEN.search(text, opener.end())
        if nested is not None and nested.start() < closer.start():
            raise ProseRejected(
                f"<<<{nested.group(1)}>>> opened inside <<<{opener.group(1)}>>> "
                f"before it closed"
            )
        body = ESCAPED_DELIMITER.sub("", text[opener.end():closer.start()]).strip()
        blocks.append(Block(opener.group(1), opener.group(2).strip(), body))
        cursor = closer.end()

    if not blocks:
        raise ProseRejected("the model returned no delimited blocks")
    if trailing := text[cursor:].strip():
        raise ProseRejected(f"text after the last block: {trailing[:80]!r}")
    return blocks


def _map_entries(body: str, label: str) -> list[dict]:
    """Read one MAP block into the entry list the gate already understands.

    The wire form is compact — sentence text to a list of claim ids — because
    it is written once per sentence and read by a machine. The gate's form is
    unchanged, so the map's shape is this function's problem alone.
    """
    parsed = _parse_json(body, f"the map for {label}")
    entries: list[dict] = []
    for sentence, value in parsed.items():
        # A bare list is the common case and means "fact" — the default type,
        # spelled the short way so the wire form stays small.
        if isinstance(value, list):
            kind, claims = formula.DEFAULT_TYPE, value
        elif isinstance(value, dict):
            kind = str(value.get("type") or formula.DEFAULT_TYPE)
            claims = value.get("claims") or []
            if not isinstance(claims, list):
                raise ProseRejected(
                    f"the map for {label} gives {sentence!r} claims that are "
                    f"a {type(claims).__name__}, not a list")
        else:
            raise ProseRejected(
                f"the map for {label} gives {sentence!r} a "
                f"{type(value).__name__}, not a list of claim ids or a typed entry")
        if kind not in formula.SENTENCE_TYPES:
            raise ProseRejected(
                f"the map for {label} types {sentence!r} as {kind!r}; "
                f"the only types are {', '.join(formula.SENTENCE_TYPES)}")
        entries.append(
            {"text": str(sentence), "type": kind, "claims": [str(c) for c in claims]}
        )
    return entries


def parse_sections(raw: str) -> list[Section]:
    """Pair every PROSE block with the MAP block that must follow it.

    Requiring the map immediately after its prose keeps the accounting next to
    the thing accounted for, and makes an omitted map a parse error rather than
    a silently unmapped section that the gate would then have to catch.
    """
    blocks = parse_delimited(raw)
    sections: list[Section] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if block.kind != "PROSE":
            raise ProseRejected("a <<<MAP>>> block with no prose before it")
        if not block.label:
            raise ProseRejected("a <<<PROSE>>> block with no label")
        following = blocks[index + 1] if index + 1 < len(blocks) else None
        if following is None or following.kind != "MAP":
            raise ProseRejected(f"no <<<MAP>>> block after prose {block.label!r}")
        sections.append(
            Section(block.label, block.body, _map_entries(following.body, block.label))
        )
        index += 2
    return sections


def section_named(sections: list[Section], label: str) -> Section:
    """The one section with this label, or a parse error naming what arrived."""
    found = [s for s in sections if s.label == label]
    if not found:
        present = ", ".join(s.label for s in sections) or "nothing"
        raise ProseRejected(f"no {label!r} block in the reply; got {present}")
    if len(found) > 1:
        raise ProseRejected(f"{len(found)} {label!r} blocks; expected one")
    return found[0]


OPPORTUNITY_LABEL = re.compile(r"^opportunity\s*=\s*(\d+)$")


def opportunity_sections(sections: list[Section]) -> list[Section]:
    """The numbered opportunity sections, in the order the model numbered them."""
    numbered = [
        (int(match.group(1)), section)
        for section in sections
        if (match := OPPORTUNITY_LABEL.match(section.label))
    ]
    return [section for _n, section in sorted(numbered, key=lambda pair: pair[0])]


# -------------------------------------------------------------------- prompts

STEP1_SYSTEM = (
    "You are a manufacturing operations analyst reading a research file on one "
    "company. Name the most EXPENSIVE frictions this evidence supports.\n\n"
    "Rules:\n"
    "1. Diagnose from the evidence only. You have not been shown any list of "
    "services and must not guess at one. Whatever the evidence supports is the "
    "answer, even if it is unglamorous.\n"
    + CITE_RULE +
    "3. Do not propose solutions. Not yet. Name problems and what they plausibly "
    "cost, and say which evidence makes you think so.\n"
    "4. If the evidence is thin, say so and name fewer frictions. Two well-"
    "evidenced frictions beat five speculative ones.\n\n"
    "Output 2-4 frictions. For each: the observation with citations, why it is "
    "expensive, and what you would need to know to size it."
)

TYPE_RULE = (
    "EVERY SENTENCE HAS A TYPE. The value in the map is either a plain list of "
    "CLAIM_IDs — which means the sentence is a FACT — or an object naming its "
    "type. The three types, and what each one must satisfy:\n\n"
    'fact — a statement about THEM. Written as {"Your line runs": '
    '["block1_what_they_make.what"]}. It must rest on at least one CLAIM_ID. '
    "Any figure in it must come from the evidence.\n\n"
    'assumption — a figure YOU are supplying so they can correct it. Written '
    'as {"If quoting runs somewhere between": {"type": "assumption"}}. It must '
    "say it is conditional ('if', 'assuming', 'suppose', 'somewhere between'), "
    "and every quantity in it must be a RANGE, never a single number. 'about "
    "$30,000 a year' is rejected; 'somewhere between $25,000 and $40,000 a "
    "year' is accepted. Write these figures as digits.\n\n"
    'about_us — a sentence about US, not them: the greeting, how we came to '
    'write, what we are offering. Written as {"I am writing to the owner": '
    '{"type": "about_us"}}. It must contain no figure at all and must not '
    "describe their business. 'I read your capabilities page' is about us; "
    "'Your line runs three shifts' is about them and is a fact.\n\n"
    "IF THE DRAFT CONTAINS ANY ASSUMPTION, it must also invite correction in "
    "plain words — ask them to check it against their own numbers, or say you "
    "would rather be corrected. A draft that reasons from assumptions without "
    "asking to be told it is wrong is rejected.\n\n"
    "THAT INVITATION IS ITSELF about_us. 'Here is the arithmetic, and please "
    "correct it if the inputs are wrong' and 'I would rather you correct that "
    "figure than trust it' are sentences about us and how we work. Type them "
    "about_us. They cite no CLAIM_ID because they assert nothing about the "
    "company, and left as facts they take the whole draft down.\n\n"
    'inference — what you think a fact MEANS. Written as {"That investment '
    'tells me speed is a": {"type": "inference", "claims": '
    '["block2_grant_funded.tech_purchased"]}}. Two things are required and '
    "both are checked: it must cite the CLAIM_ID it reasons FROM, and it must "
    "show that it is reasoning — 'suggests', 'tells me', 'signals', 'implies', "
    "'which means', 'points to', 'indicates'. Any figure in it must either be "
    "a range or appear in the claims it cites.\n\n"
    "THE DISTINCTION, WORKED THROUGH. 'Your line runs three shifts', with a "
    "CLAIM_ID, is a fact — their own record, restated. 'That investment tells "
    "me speed of results is a buying criterion', citing the investment claim, "
    "is an inference — their record, plus our reading of it, with the join "
    "visible. Reasoning you cannot anchor to a claim does not belong in the "
    "draft at all; cut it or find the fact it rests on.\n\n"
    "IN THE EMAIL, YOU GET ONE. The email is a cold first contact and carries "
    "exactly one piece of reasoning — that is the hypothesis. A second "
    "inference or hedged sentence in an email is rejected. The brief and the "
    "analysis have no such limit: reason as much as the evidence carries, so "
    "long as every inference is anchored and marked.\n\n"
    "WHAT YOU ARE OFFERING IS about_us, NOT AN INFERENCE. 'The bounded fix is "
    "a weekly one-page report' describes what we would do; it reasons from "
    "nothing of theirs and cites no claim. Type it about_us and keep the "
    "figures out of it.\n\n"
    "A SENTENCE THAT STARTS WITH 'IF' AND CARRIES A FIGURE IS AN ASSUMPTION, "
    "never about_us. If it has a number in it, it is not a sentence about us.\n\n"
    "A FACT WITH NO CLAIM_ID IS ALWAYS REJECTED. If you cannot name the "
    "CLAIM_ID a sentence rests on, it is not a fact — decide what it really "
    "is. A framing line like 'Three findings from your public record' or 'Two "
    "things stood out' or 'That is hard-won knowledge' describes this letter "
    "and is about_us. A figure you are supplying is an assumption. Leaving it "
    "as a fact and hoping is the single most common way a draft is thrown "
    "away.\n\n"
    "DO NOT PREFIX A SENTENCE WITH A LABEL. Write 'Finding two:' as its own "
    "short line, or leave it out — never 'Finding two: your lead time runs "
    "long', because the label and the assertion then have to share one type "
    "and one source.\n\n"
    "A PERCENTAGE IS A QUANTITY. '25%' is a point figure and is rejected in an "
    "assumption; write 'between 20 and 30 percent'.\n\n"
    "SHOWING YOUR WORKING is encouraged, and every input must already appear "
    "in the draft as a fact or an assumption. '2 x 0.20-0.40 x 40 x $80-$120 = "
    "$51,200-$153,600' is accepted only if the 2, the 20-40 percent, the 40 "
    "and the $80-$120 were each stated earlier. The result must be a range. "
    "Type the calculation itself as an assumption.\n\n"
)

PROSE_RULE = (
    "PROSE is what a person reads. Written for a manufacturing owner who has "
    "never heard of us. No headings inside it, no square brackets, no bullet "
    "markers, and none of this vocabulary: tier, claim, block, corroborated, "
    "verified, hypothesis-tier, P1. Write sentences, not notes.\n\n"
    "'Tier' is banned even in its industry sense. A supplier to the automakers "
    "is 'a direct supplier to the automakers', never 'a tier one supplier' — "
    "the word is reserved here and a draft carrying it is discarded.\n\n"
    "ADDRESS THEM DIRECTLY, in the second person: 'you', 'your', 'your shop'. "
    "Never write the company's name as a third-person subject — not 'Acme has "
    "invested', but 'you have invested'. This is a letter to them, and the "
    "leave-behind discards any paragraph that talks about them instead of to "
    "them.\n\n"
    "Every paragraph that names a difficulty must also carry the thought about "
    "it — what it plausibly costs, or what a bounded fix would be. A paragraph "
    "that states a problem and stops is discarded.\n\n"
    "ARITHMETIC ABOUT THE GRANT: use only the award figures given in the "
    "evidence. Do not total, combine, or infer additional rounds. If the "
    "evidence carries one award, there is one award.\n\n"
    "A MAP is your accounting of the prose that came before it. It is one JSON "
    "object whose keys identify factual sentences in that prose, and whose "
    "values are the lists of CLAIM_IDs each sentence rests on. Every factual "
    "sentence in the prose must appear as a key. A sentence you leave out is "
    "treated as unsourced and the whole draft is rejected, so omit none.\n\n"
    "KEEP EACH KEY SHORT: the first six to ten words of the sentence, copied "
    "VERBATIM from the prose, and stop there — do not write the whole sentence "
    "and do not paraphrase. Drop any trailing comma or quote mark so the key "
    "needs no escaping. Long keys are what break this map: one draft failed "
    "with an unreadable map three thousand characters into a single line.\n\n"
    + TYPE_RULE
    + "Name sources inside the prose in words a reader can follow — 'the state's "
    "announcement of your grant', 'your own capabilities page' — never as an id.\n"
)

def format_rule(example_label: str) -> str:
    """The output-format instructions, shown with THIS prompt's own first label.

    The example carries more weight than the sentence naming the sections. A
    single shared example reading 'opportunity=1' made the email step emit
    opportunity blocks in six attempts out of ten, and once produced the
    blend 'opportunity="subject"' — the model reconciling an example and an
    instruction that disagreed. So the example is generated per prompt and
    always shows a label that prompt actually wants.
    """
    return (
        "OUTPUT FORMAT. Delimited blocks, and nothing outside them — no "
        "preamble, no closing remark, no code fences. Prose is plain text "
        "between markers, so write quotes, apostrophes and paragraph breaks "
        "normally; do not escape them and do not put prose inside JSON.\n\n"
        "Every <<<PROSE ...>>> block is followed immediately by its <<<MAP>>> "
        f"block. The label is the section name, exactly as listed below — "
        f"'{example_label}' here. The exact shape:\n\n"
        f"<<<PROSE {example_label}>>>\n"
        "First paragraph. Second paragraph.\n"
        "<<<END>>>\n"
        '<<<MAP>>>{"First paragraph": ["block2_grant_funded.grant_amount"]}'
        "<<<END>>>\n\n"
        "If prose must contain the characters <<<END>>> or <<<PROSE, write a "
        "backslash first: \\<<<END>>>. Unescaped, they end the block early and "
        "the draft is thrown away.\n"
    )

STEP2_FORMAT = (
    format_rule("opportunity=1")
    + "\nEmit, in this order: one block pair per opportunity labelled "
    "opportunity=1, opportunity=2 and so on; then a pair labelled anti_pitch; "
    "then a pair labelled discovery_questions.\n\n"
    "Each opportunity is two to four full paragraphs.\n"
    "anti_pitch: what NOT to say to this company, in plain prose — what they "
    "already do well, what would sound ignorant.\n"
    "discovery_questions: the gaps, written as questions to ask on a call, in "
    "plain prose.\n\n"
    + PROSE_RULE
)

STEP2_SYSTEM = (
    "You are costing frictions that have already been diagnosed, and scoping "
    "bounded fixes for them.\n\n"
    "You are given MATH TEMPLATES. They are arithmetic, not a menu of services. "
    "Use one only where the diagnosis already found the matching problem and the "
    "evidence carries the variables it needs. Each template lists what must be "
    "observed and when it must NOT be used — obey both.\n\n"
    "Rules:\n"
    "1. Use THEIR numbers first. A grant match amount is capital they have "
    "already committed and is a T1 floor on their own investment — the strongest "
    "anchor available. Industry averages are a last resort and must be labelled.\n"
    "2. Every figure is a conditional range with its assumptions stated inline: "
    "'if quotes run about 40 a month, then...'. Never a point estimate.\n"
    "3. Every figure is checkable: say where it came from in words, and state "
    "your assumptions in the same sentence.\n"
    "4. Scoped fixes are two to four weeks of work. Not a platform, not a "
    "retainer, not a transformation.\n"
    "5. Confidence per opportunity: high / medium / low, with the reason.\n\n"
    + STEP2_FORMAT
)

EMAIL_SYSTEM = (
    "Write a first-contact email and a three-finding brief for a manufacturer.\n\n"
    "The email is short — under 150 words. It states two or three specific, "
    "sourced facts about their business, offers exactly one clearly-labelled "
    "hypothesis, and shows one piece of checkable arithmetic naming its sources "
    "inline. It asks for a short conversation. It does not pitch a service.\n\n"
    "The hypothesis sentence must use hedging language ('we think', 'our "
    "hypothesis is', 'if that is right') so a reader cannot mistake it for a "
    "fact. There must be exactly ONE hypothesis in the whole email.\n\n"
    "Then a BRIEF: three findings, each with its evidence named in words, and the "
    "arithmetic laid out so the reader can check it.\n\n"
    + format_rule("subject")
    + "\nEmit exactly three block pairs, in this order: subject, email, brief. "
    "The subject is one line and asserts nothing, so its map is the empty "
    "object: <<<MAP>>>{}<<<END>>>\n\n"
    + PROSE_RULE
)


async def _call(
    client: Any, system: str, prompt: str, max_tokens: int = 2000,
    spend: Spend | None = None,
) -> str:
    response = await client.messages.create(
        model=THESIS_MODEL,
        max_tokens=max_tokens,
        temperature=TEMPERATURE,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    if spend is not None and getattr(response, "usage", None) is not None:
        spend.record(response.usage)
    return " ".join(
        b.text for b in response.content if getattr(b, "type", "") == "text"
    ).strip()


# --------------------------------------------------------------------- gate

def factual_sentences(text: str) -> list[str]:
    """Sentences that assert something about the world.

    Questions, headings and pure calls-to-action are not factual assertions and
    are not required to carry a citation. Everything else is.
    """
    out = []
    for raw in re.split(r"(?<=[.!?])\s+", text or ""):
        sentence = raw.strip()
        if not sentence or sentence.endswith("?"):
            continue
        if len(sentence.split()) < 4:
            continue
        if re.fullmatch(r"[A-Z0-9 \-—:]+", sentence):
            continue
        out.append(sentence)
    return out


def strip_signature(text: str) -> str:
    """Remove the identification block before gating.

    It is required boilerplate under both regimes — our own name, our own
    address, the opt-out line — and asserts nothing about the prospect. Gating
    it blocked every artifact over the street number.
    """
    return re.split(r"\n--\s*\n", text or "", maxsplit=1)[0]


def gate_artifact(
    text: str,
    allowed_paths: set[str],
    hypothesis_paths: set[str],
    person_allowed: bool,
    person_name: str | None,
) -> dict[str, Any]:
    """Map every factual sentence back to a qualifying claim. Returns the verdict.

    This is an independent pass over the FINISHED text. It does not trust the
    generator's intentions, only what the generator actually wrote — which is
    the whole point, because the generator is the thing that might be wrong.
    """
    failures: list[str] = []
    mapping: list[dict[str, Any]] = []
    hypothesis_count = 0
    body = strip_signature(text)

    for sentence in factual_sentences(body):
        cites = CITATION.findall(sentence)
        known = [c for c in cites if c in allowed_paths]
        entry = {"sentence": sentence[:300], "claims": known}
        is_hypothesis = any(m in sentence.lower() for m in HYPOTHESIS_MARKERS)
        if is_hypothesis:
            hypothesis_count += 1
            entry["hypothesis"] = True

        if not cites:
            # A sentence with a number and no citation is the exact failure this
            # gate exists for: a figure that looks sourced because everything
            # around it is.
            if has_quantity(sentence):
                failures.append(f"number with no source: {sentence!r}")
            elif not is_hypothesis:
                failures.append(f"unmappable factual sentence: {sentence!r}")
        else:
            unknown = [c for c in cites if c not in allowed_paths]
            if unknown:
                failures.append(
                    f"cites a claim that does not qualify ({', '.join(unknown[:2])}): "
                    f"{sentence!r}"
                )
        mapping.append(entry)

    if hypothesis_count > 1:
        failures.append(
            f"{hypothesis_count} hypotheses present; the formula allows exactly one"
        )

    cited_hypotheses = {
        c for entry in mapping for c in entry["claims"] if c in hypothesis_paths
    }
    if len(cited_hypotheses) > 1:
        failures.append(
            f"cites {len(cited_hypotheses)} T4 claims; only one hypothesis is allowed"
        )

    if (not person_allowed and person_name
            and re.search(rf"\b{re.escape(person_name.split()[0])}\b", body)):
        failures.append(
            f"uses a person's name that failed the person gate: {person_name!r}"
        )

    return {
        "passed": not failures,
        "failures": failures,
        "map": mapping,
        "sentences": len(mapping),
        "cited": sorted({c for e in mapping for c in e["claims"]}),
    }


# ------------------------------------------------------------------ generation

def feedback_block(failures: list[str]) -> str:
    """The previous attempt's verdict, written for the generator that failed it.

    The retry used to be handed the same prompt and no account of what went
    wrong. Eight rules have to be satisfied at once, and a draft that missed
    two of them was asked to guess again blind — so the second attempt failed
    the same way as the first about as often as not.

    The sentences go in whole. Truncating them here would reproduce, in the
    place it matters most, the exact mistake that cost a diagnostic cycle:
    a message that names a problem the reader cannot see.
    """
    if not failures:
        return ""
    lines = [
        "FEEDBACK ON YOUR PREVIOUS ATTEMPT. It was rejected. Every point below "
        "must be fixed; nothing else about the task has changed.\n"
    ]
    for failure in failures:
        lines.append(f"- {failure}")
        advice = formula.guidance_for(failure)
        if advice:
            lines.append(f"  FIX: {advice}")
    lines.append(
        "\nRewrite the whole draft. Do not simply delete the offending "
        "sentences if that leaves the draft thin — find the claim that "
        "supports them, or say the same thing in a form the rules allow."
    )
    return "\n".join(lines) + "\n"


async def draft_prospect(
    prospect: dict[str, Any], client: Any, verdicts: tuple[str, ...],
    spend: Spend | None = None, failures: list[str] | None = None,
) -> dict[str, Any]:
    """Produce the thesis, email and brief for one prospect, gated."""
    facts = assertable_claims(prospect, verdicts)
    hypotheses = hypothesis_claims(prospect)
    all_qualifying = qualifying_claims(prospect)
    allowed = {p for p, _ in all_qualifying}
    hypothesis_paths = {p for p, _ in hypotheses}

    salutation, gate_result = salutation_for(prospect)
    person_allowed = bool(gate_result and gate_result.allowed)
    person_name = gate_result.name if gate_result else None

    header = (
        f"COMPANY: {prospect.get('company_name')}\n"
        f"County: {prospect.get('county')} · about "
        f"{prospect.get('drive_minutes')} minutes from Muncie\n"
        f"Industry (from the grant listing): {prospect.get('industry_desc')}\n"
    )
    evidence_block = render_claims(all_qualifying)

    notes = feedback_block(failures or [])
    step1 = await _call(
        client, STEP1_SYSTEM, f"{header}\nEVIDENCE:\n{evidence_block}",
        max_tokens=STEP1_TOKENS, spend=spend,
    )
    raw2 = await _call(
        client, STEP2_SYSTEM,
        f"{header}\nEVIDENCE:\n{evidence_block}\n\n"
        f"DIAGNOSIS FROM STEP 1:\n{step1}\n\n"
        f"MATH TEMPLATES (arithmetic, not a service menu):\n"
        f"{as_prompt_block(applicable(evidence_block))}\n\n{notes}",
        max_tokens=STEP2_TOKENS, spend=spend,
    )
    analysis = parse_sections(raw2)
    opportunities = opportunity_sections(analysis)
    if not opportunities:
        raise ProseRejected("the analysis returned no opportunities")
    for index, opp in enumerate(opportunities, 1):
        validate_prose(opp.prose, f"opportunity {index}")

    thesis_sentences = [entry for opp in opportunities for entry in opp.sentence_map]
    thesis = "\n\n".join(opp.prose for opp in opportunities)
    for label, heading in (("anti_pitch", "What not to say"),
                           ("discovery_questions", "What to ask")):
        extra = next((s for s in analysis if s.label == label), None)
        if extra and extra.prose:
            thesis += f"\n\n{heading}\n\n{extra.prose}"
            thesis_sentences += extra.sentence_map

    fact_lines = render_claims(facts[:8])
    hyp_lines = render_claims(hypotheses[:3])
    raw_email = await _call(
        client, EMAIL_SYSTEM,
        f"{header}\n"
        f"Greeting must address: {salutation}\n"
        f"(the person gate {'passed' if person_allowed else 'FAILED — use no name'})\n\n"
        f"FACTS YOU MAY ASSERT:\n{fact_lines or '(none qualify — say less)'}\n\n"
        f"HYPOTHESES — choose exactly ONE, hedged:\n{hyp_lines or '(none available)'}\n\n"
        f"THE ANALYSIS:\n{thesis[:4000]}\n\n{notes}",
        max_tokens=EMAIL_TOKENS, spend=spend,
    )
    mail = parse_sections(raw_email)
    subject = section_named(mail, "subject")
    email_part = section_named(mail, "email")
    brief_part = section_named(mail, "brief")
    validate_prose(email_part.prose, "email")
    validate_prose(brief_part.prose, "brief")

    profile = compliance.profile_for(prospect.get("source_adapter"))
    email_body = _append_identification(email_part.prose, profile)
    brief_body = brief_part.prose

    company = prospect.get("company_name")
    values = {path: str(claim.get("value")) for path, claim in all_qualifying}
    email_gate = gate_prose(email_body, email_part.sentence_map, allowed,
                            hypothesis_paths, person_allowed, person_name, company,
                            "email", values)
    # The compliance verdict is a second, independent refusal. It reads the
    # evidence rather than the prose, because a guessed address looks exactly
    # like a published one in a draft: the difference is only visible in what
    # contact discovery actually read off a page.
    email_compliance = compliance.check_artifact(prospect, "email", email_body)
    if not email_compliance.passed:
        email_gate = {
            **email_gate,
            "passed": False,
            "failures": [
                *email_gate["failures"],
                *(f"{email_compliance.regime}: {reason}"
                  for reason in email_compliance.failures),
            ],
        }
    brief_gate = gate_prose(brief_body, brief_part.sentence_map, allowed,
                            hypothesis_paths, person_allowed, person_name, company,
                            "brief", values)
    thesis_gate = gate_prose(thesis, thesis_sentences, allowed, hypothesis_paths,
                             person_allowed, person_name, company, "thesis", values)

    return {
        # The basis travels with the artifact, not with the run. "Which law
        # governed this message, and on what grounds" is a question that gets
        # asked long after the console has scrolled away.
        "compliance": {
            "email": email_compliance.as_dict(),
            "brief": compliance.check_artifact(prospect, "brief", brief_body).as_dict(),
            "thesis": compliance.check_artifact(prospect, "thesis", thesis).as_dict(),
        },
        "thesis": thesis,
        "thesis_gate": thesis_gate,
        "subject": subject.prose.strip(),
        "email": email_body,
        "brief": brief_body,
        "email_gate": email_gate,
        "brief_gate": brief_gate,
        "salutation": salutation,
        "person_allowed": person_allowed,
        "facts_available": len(facts),
    }


def _escape_control_chars(blob: str) -> str:
    """Escape raw newlines and tabs appearing inside JSON string literals."""
    out, in_string, escaped = [], False, False
    swap = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for char in blob:
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\":
            out.append(char)
            escaped = True
        elif char == '"':
            in_string = not in_string
            out.append(char)
        elif in_string and char in swap:
            out.append(swap[char])
        else:
            out.append(char)
    return "".join(out)


def _parse_json(raw: str, label: str) -> dict[str, Any]:
    """Read one MAP block's JSON object.

    Only the map travels as JSON now, so this handles machine-shaped data of a
    few hundred bytes rather than pages of prose. The control-character repair
    stays because sentence keys are copied out of prose and a model still
    occasionally copies a line break along with them.
    """
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        raise ProseRejected(f"{label} contains no JSON object")
    blob = match.group(0)
    try:
        parsed = json.loads(blob)
    except (ValueError, TypeError):
        try:
            parsed = json.loads(_escape_control_chars(blob))
        except (ValueError, TypeError) as exc:
            raise ProseRejected(f"{label} was unreadable: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProseRejected(f"{label} was not a JSON object")
    return parsed


def _append_identification(body: str, profile: compliance.ComplianceProfile) -> str:
    """Real identity, a physical address and a working opt-out on every email.

    Required by CAN-SPAM and by CASL alike, and identical under both, so the
    profile supplies the block rather than this function deciding it.
    """
    return compliance.append_identification(body, profile)



# ------------------------------------------------------------------- letter

LETTER_WORDS = (90, 330)
"""How long a one-page letter may run, in words.

A page of 12-point type with a signature block on it holds about 350 words, and
the whole bet here is that it does NOT fill. The floor is low deliberately —
ninety words carrying an anchor, one number and an ask is the artifact working,
not a draft that ran out. What catches a letter that dropped a move is the
structural check below, not the word count, because a missing ask is a missing
ask at any length."""

LETTER_ATTEMPTS = 3
"""One more than the email, for the same reason LinkedIn gets one: two of the
three ways this artifact fails — too long, and the missing structural line —
are mechanical, and spending a reasoning attempt on a word count is waste."""

FOUNDING_LINE = pricing.FRAMING
CORRECT_ME = (
    "the close has to hand the risk to us by asking to be corrected: "
    "if the number is wrong, say so and the letter has done its job"
)


def letter_context(
    prospect: dict[str, Any], analysis: dict[str, Any] | None
) -> dict[str, Any]:
    """The four things a fragment letter is built out of.

    Read from the ANALYSIS the operator already holds rather than recomputed.
    Two documents about one company that disagree about its arithmetic is worse
    than either of them alone, and the analysis is the one on the desk.
    """
    from lib import dashboard

    meta = (analysis or {}).get("gate_map") or {}
    approaches = meta.get("approaches") or []
    lead = approaches[0] if approaches else None
    anchor = ((meta.get("case") or {}).get("anchor")) or {}
    # An analysis written before anchors were recorded says nothing about what
    # its figure rests on, and "not recorded" is not the same as "no anchor".
    # The letter still cannot quote the figure — a number whose basis we cannot
    # state is a number we cannot ask to have corrected — so it falls to the
    # pending hook, and the fix is to regenerate the analysis rather than to
    # guess retroactively at what it was sized to.
    kind = anchor.get("kind") or anchors.NONE

    grant = None
    for path, claim in qualifying_claims(prospect):
        if path.startswith("block2_grant_funded") and claim.get("tier") == int(Tier.T1):
            grant = (path, str(claim.get("value")))
            break

    figure = None
    if lead and kind != anchors.NONE and lead.get("annual_return"):
        low, high = lead["annual_return"][0], lead["annual_return"][1]
        figure = {
            # Written the way the engagement ladder writes a band — "$41,000 to
            # $88,000 USD" — rather than with the code in front of the number.
            # The two documents quote the same figure and a reader should not
            # have to notice they are formatted differently.
            "words": f"${low:,}-${high:,} "
                     f"{pricing.currency_for(prospect.get('source_adapter'))} a year",
            "reading": lead.get("reading") or "target",
            "name": lead.get("name") or "the lead approach",
            "basis": anchor.get("detail") or "",
            "means": figure_meaning(anchor, lead),
            "model_type": anchor.get("model_type") or anchors.LABOUR_HOURS,
        }

    return {
        "grant": grant,
        "figure": figure,
        "anchor_kind": kind,
        "pending": anchors.PENDING_HEADLINE,
        "dashboard": dashboard.dashboard_url(prospect),
        "company": prospect.get("company_name"),
    }


FIGURE_MEANINGS: dict[str, str] = {
    anchors.CAPITAL_UTILISATION: (
        "the value of the funded equipment's IDLE TIME that a utilisation record "
        "would recover in a year. It is a share of the annual capital charge on "
        "the money they have already committed. It is NOT interest, NOT a "
        "financing cost, NOT a carrying cost, and NOT revenue"
    ),
    anchors.LABOUR_HOURS: (
        "the annual cost of the hours a build gives back — the time the work "
        "takes today, valued at a loaded wage, times the share a build removes. "
        "It is NOT revenue, NOT profit and NOT a saving on anything they buy"
    ),
}
"""What each model's headline figure actually MEASURES, in words for the writer.

The gate checks that a figure came from somewhere. It cannot check that the
sentence around it says what the figure is, and the first live letter is why
this exists: handed $3,885-$10,880 from the capital model, the writer narrated
it as "the financing rate on capital between $400,000 and $500,000 ... in
carrying cost", invented a rate of one to three per cent, and passed every check
in the pipeline. Every figure in that sentence was real. The sentence was about
a different quantity.

A wrong meaning is worse than a wrong number, because a prospect can correct a
number and has no way to correct a definition they were not given."""

FORBIDDEN_MEANINGS: dict[str, tuple[str, ...]] = {
    anchors.CAPITAL_UTILISATION: (
        "financing", "interest rate", "carrying cost", "cost of borrowing",
        "loan", "repayment", "debt service",
    ),
    anchors.LABOUR_HOURS: ("revenue", "turnover", "profit margin", "gross margin"),
}
"""Words that mean the figure has been re-described as something it is not.

Checked mechanically, because the failure is a plausible sentence rather than a
missing one and nothing else in the pipeline is looking for it."""


def figure_meaning(anchor: dict[str, Any], lead: dict[str, Any]) -> str:
    """What the headline figure measures, and what it must not be called."""
    model_type = anchor.get("model_type") or anchors.LABOUR_HOURS
    words = FIGURE_MEANINGS.get(model_type, FIGURE_MEANINGS[anchors.LABOUR_HOURS])
    attacks = str(lead.get("attacks") or "").strip()
    return f"{words}. What it attacks: {attacks}" if attacks else words


LETTER_STRUCTURE = (
    "THE LETTER HAS FOUR MOVES AND NOTHING ELSE.\n\n"
    "1. THE ANCHOR. Open on their award, in their own record's words, with its "
    "CLAIM_ID. One sentence. It is there to prove in the first line that this "
    "was written about them and not sent to them.\n"
    "2. ONE NUMBER. Exactly one piece of arithmetic, and it is theirs to "
    "correct. Write it as a conditional range with the assumption in the same "
    "sentence — 'if X runs near Y, that is Z a year' — and type it "
    '{"type": "assumption"} in the map. Not two numbers. Not a table. One.\n'
    "3. THE FRAGMENT. Say plainly which part you could not compute and why: the "
    "figure rests on something only they know. This is the whole letter. A "
    "complete argument invites agreement or silence; an incomplete one invites "
    "the missing number.\n"
    f"4. THE CLOSE. Shift the risk: {CORRECT_ME}. Then the founding-client "
    f"line, in these words — '{FOUNDING_LINE}'.\n\n"
    "WHAT MUST NOT BE IN IT. A second computed figure. A list of services. A "
    "case study. A meeting request with a time in it. Anything that makes the "
    "page look finished.\n"
)

LETTER_SYSTEM = (
    "Write one printed letter to one manufacturer, for a colleague to sign and "
    "post. It is a cold touch to a stranger and answers to the same rules as a "
    "first email.\n\n"
    f"LENGTH: {LETTER_WORDS[0]} to {LETTER_WORDS[1]} words. It has to fit on "
    "one page above a signature. Count before you emit.\n\n"
    + LETTER_STRUCTURE
    + "\nTONE. One person writing to another about their business. No "
    "salutation flourishes, no 'I hope this finds you well', no bullet points — "
    "this is a letter, not a deck.\n\n"
    "THE GREETING AND THE SIGN-OFF ARE ABOUT US, NOT ABOUT THEM. Type them "
    '{"type": "about_us"} in the map. They must contain no figure and must not '
    "describe their business.\n\n"
    "Do NOT include a postal address block or an unsubscribe line. A letter is "
    "not an electronic message and those are email furniture.\n\n"
    + TYPE_RULE + "\n" + PROSE_RULE
)

PENDING_RULE = (
    "THERE IS NO COMPUTED NUMBER FOR THIS COMPANY, and that is what the letter "
    "is about.\n\n"
    "Nothing they have published sizes the work, so move 2 is NOT a figure. It "
    "is the request for the one number that would let us compute anything: name "
    "the number, say in one sentence what it would let us work out, and say that "
    "until it arrives everything we could write would be a guess about a company "
    "of their shape rather than about them.\n\n"
    "Do NOT put a dollar range in this letter. Not hedged, not 'typically', not "
    "'companies like yours'. The absence is the argument."
)


def letter_failures(prose: str, context: dict[str, Any]) -> list[str]:
    """The shape rules a letter must keep, over and above the outbound gate.

    Structural rather than editorial. Each one is a move the letter is built out
    of, and a letter missing one is not a shorter letter — it is a different
    artifact that happens to be the same length.
    """
    body = strip_signature(prose or "")
    words = len(body.split())
    failures: list[str] = []
    if words > LETTER_WORDS[1]:
        failures.append(
            f"the letter runs {words} words; one page holds {LETTER_WORDS[1]}")
    if words < LETTER_WORDS[0]:
        failures.append(
            f"the letter runs {words} words, under the {LETTER_WORDS[0]} a letter "
            f"carrying an anchor, a number and an ask needs")
    if FOUNDING_LINE.lower() not in body.lower():
        failures.append(
            f"the founding-client line is missing; it must read {FOUNDING_LINE!r}")
    if not formula.invites_correction(body):
        failures.append("the close never asks to be corrected, so the risk stays "
                        "with the reader")
    if context.get("anchor_kind") == "none":
        money = re.findall(r"[$£€]\s?\d", body)
        if money:
            failures.append(
                "this company has nothing that sizes the work, so the letter may "
                f"not quote a figure at all; it quotes {len(money)}")

    # The figure has to keep its meaning. See FIGURE_MEANINGS: a letter that
    # renames a utilisation recovery as a financing cost passes every other
    # check in the pipeline, because every number in it is real.
    model_type = (context.get("figure") or {}).get("model_type") or (
        anchors.CAPITAL_UTILISATION
        if context.get("anchor_kind") == anchors.AWARD else anchors.LABOUR_HOURS)
    lowered = body.lower()
    renamed = [word for word in FORBIDDEN_MEANINGS.get(model_type, ())
               if word in lowered]
    if renamed and context.get("figure"):
        failures.append(
            f"the figure is described as {renamed[0]!r}, which is not what it "
            f"measures. It is {(context['figure']['means'].split('.')[0])}")
    return failures


async def draft_letter(
    prospect: dict[str, Any], client: Any, verdicts: tuple[str, ...],
    context: dict[str, Any], spend: Spend | None = None,
    failures: list[str] | None = None,
) -> dict[str, Any]:
    """Produce one fragment letter, gated."""
    facts = assertable_claims(prospect, verdicts)
    hypotheses = hypothesis_claims(prospect)
    all_qualifying = qualifying_claims(prospect)
    allowed = {p for p, _ in all_qualifying}
    hypothesis_paths = {p for p, _ in hypotheses}
    salutation, gate_result = salutation_for(prospect)
    person_allowed = bool(gate_result and gate_result.allowed)
    person_name = gate_result.name if gate_result else None

    if context.get("figure"):
        number_block = (
            f"THE ONE NUMBER, already computed. Use this and no other:\n"
            f"  {context['figure']['words']}, on the "
            f"{context['figure']['reading']} reading of "
            f"{context['figure']['name']}.\n"
            f"  WHAT IT MEASURES: {context['figure']['means']}.\n"
            f"  What it rests on: {context['figure']['basis']}\n"
            f"  The sentence that quotes it must say what it measures, in those "
            f"terms. You may not re-describe it as anything else, and you may "
            f"not invent a rate, a multiplier or a second figure to get to it.\n"
            f"  IF THAT SENTENCE NAMES THEIR AWARD FIGURE, CITE THE AWARD'S "
            f"CLAIM_ID ON IT IN THE MAP. Their award is a single exact number "
            f"and you may say it — but only as their figure, which means the "
            f"map has to point at the claim it comes from. Uncited, it reads as "
            f"a precise number of ours inside a hedged sentence, and it is "
            f"rejected.\n"
            f"  Write it as a conditional range and name what it depends on in "
            f"the same sentence."
        )
    else:
        number_block = PENDING_RULE

    anchor_line = (
        f"THEIR AWARD, which the letter opens on. Cite this CLAIM_ID on the "
        f"opening sentence, and on any later sentence that repeats the figure:"
        f"\n  CLAIM_ID {context['grant'][0]} | {context['grant'][1]}"
        if context.get("grant") else
        "THEY HAVE NO TIER 1 AWARD CLAIM ON FILE. Open instead on the "
        "strongest fact below, and keep it to one sentence."
    )

    raw = await _call(
        client, LETTER_SYSTEM,
        f"COMPANY: {prospect.get('company_name')}\n"
        f"Industry, in the grant listing's own words: {prospect.get('industry_desc')}\n"
        f"Greeting must address: {salutation}\n"
        f"(the person gate {'passed' if person_allowed else 'FAILED — use no name'})\n\n"
        f"{anchor_line}\n\n"
        f"{number_block}\n\n"
        f"FACTS YOU MAY ASSERT:\n{render_claims(facts[:8]) or '(none qualify)'}\n\n"
        f"HYPOTHESES — at most ONE, hedged:\n"
        f"{render_claims(hypotheses[:2]) or '(none available)'}\n\n"
        f"{format_rule('letter')}\nEmit exactly one block pair, labelled letter.\n\n"
        f"{feedback_block(failures or [])}",
        max_tokens=EMAIL_TOKENS, spend=spend,
    )
    section = section_named(parse_sections(raw), "letter")
    validate_prose(section.prose, "letter")

    values = {path: str(claim.get("value")) for path, claim in all_qualifying}
    verdict = gate_prose(
        section.prose, section.sentence_map, allowed, hypothesis_paths,
        person_allowed, person_name, prospect.get("company_name"), "email", values)
    verdict["failures"] = list(verdict["failures"]) + letter_failures(
        section.prose, context)
    verdict["passed"] = not verdict["failures"]
    return {
        "letter": section.prose.strip(),
        "gate": verdict,
        "salutation": salutation,
        "person_allowed": person_allowed,
    }


def current_letters() -> dict[str, dict[str, Any]]:
    """Prospect id -> the letter that counts, which is the newest live one."""
    newest: dict[str, dict[str, Any]] = {}
    for artifact in db.all_artifacts():
        if (artifact.get("kind") != "letter"
                or artifact.get("status") not in ("sendable", "blocked", "draft")):
            continue
        current = newest.get(artifact["prospect_id"])
        if current is None or artifact["created_at"] > current["created_at"]:
            newest[artifact["prospect_id"]] = artifact
    return newest


def newest_analysis_by_prospect() -> dict[str, dict[str, Any]]:
    """The live analysis per company, which is what a letter quotes from."""
    newest: dict[str, dict[str, Any]] = {}
    for artifact in db.all_artifacts():
        if (artifact.get("kind") != "analysis"
                or artifact.get("status") not in ("sendable", "held")):
            continue
        current = newest.get(artifact["prospect_id"])
        if current is None or artifact["created_at"] > current["created_at"]:
            newest[artifact["prospect_id"]] = artifact
    return newest


def letter_candidates(
    limit: int | None, adapter: str | None, only_blocked: bool = True,
    company: str | None = None,
) -> list[dict[str, Any]]:
    """Full-dossier companies, in the order the desk would work them.

    Full-dossier only, by `lib/routing.py`: a letter asserts claims, and a
    company below the floor is one we may not assert claims about however
    interesting it is. The routing already says so in one place.
    """
    from lib import routing, triggers

    verdicts = canary.read_state().allowed_verdicts()
    # A letter quotes ONE computed output of their financial model, and without
    # an analysis there is no model to quote. A company with no analysis would
    # get the pending-number letter, which is a real artifact — but it is the
    # artifact for a company whose evidence cannot size the work, not for one
    # nobody has analysed yet, and sending the first as the second would tell a
    # prospect we looked and found nothing when we have not looked.
    analysed = set(newest_analysis_by_prospect())
    rows = [p for p in db.list_prospects_full(adapter)
            if p.get("priority") in ("P1", "P2")
            and p["id"] in analysed
            and icp.outreach_eligible(p)
            and evidence_integrity(p).passing
            and routing.may_write_claims(p, verdicts)]
    if only_blocked:
        live = current_letters()
        rows = [p for p in rows if (live.get(p["id"]) or {}).get("status") != "sendable"]
    if company:
        needle = company.lower()
        rows = [p for p in rows
                if needle in str(p.get("company_name") or "").lower()]
    rows.sort(key=triggers.sort_key)
    return rows[:limit] if limit else rows


async def _run_letters(limit: int | None, dry_run: bool, console: Console,
                       only_blocked: bool = True, adapter: str | None = None,
                       company: str | None = None) -> int:
    """Write the fragment letter for every full-dossier company."""
    state = canary.read_state()
    verdicts = state.allowed_verdicts()
    rows = letter_candidates(limit, adapter, only_blocked, company)
    analyses = newest_analysis_by_prospect()

    table = Table(title=f"{len(rows)} fragment letter(s) to write", title_justify="left")
    for column in ("Company", "Anchor", "The one number", "Regime"):
        table.add_column(column)
    contexts = {}
    for prospect in rows:
        context = letter_context(prospect, analyses.get(prospect["id"]))
        contexts[prospect["id"]] = context
        table.add_row(
            str(prospect.get("company_name"))[:34],
            context["anchor_kind"],
            (context["figure"] or {}).get("words") or context["pending"],
            compliance.profile_for(prospect.get("source_adapter")).regime)
    console.print(table)
    if dry_run:
        console.print("\n[dim]--dry-run: nothing generated, nothing written.[/dim]")
        return 0

    from lib.config import settings
    if not settings.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY is not set; cannot draft.[/red]")
        return 1

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    spend = Spend()
    for prospect in rows:
        console.print(f"\n[cyan]{prospect.get('company_name')}[/cyan]")
        context = contexts[prospect["id"]]
        attempt, result, rejections, feedback = 1, None, [], []
        while attempt <= LETTER_ATTEMPTS:
            try:
                result = await draft_letter(
                    prospect, client, verdicts, context, spend, feedback)
            except ProseRejected as exc:
                rejections.append(f"attempt {attempt}: {exc}")
                feedback = [str(exc)]
                console.print(f"  [yellow]attempt {attempt} rejected:[/yellow] {exc}")
                attempt += 1
                continue
            if result["gate"]["passed"]:
                break
            feedback = list(result["gate"]["failures"])
            console.print(f"  [yellow]attempt {attempt} blocked:[/yellow] "
                          + "; ".join(f[:90] for f in feedback[:2]))
            attempt += 1

        if result is None:
            db.insert_artifact({
                "prospect_id": prospect["id"], "kind": "letter",
                "status": "blocked", "body": "", "gate_failures": rejections,
                "attempts": LETTER_ATTEMPTS, "model": THESIS_MODEL,
                "compliance": compliance.not_drafted(prospect, "letter"),
            })
            console.print(f"  [red]blocked[/red] — never parsed: {rejections[-1]}")
            continue

        passed = result["gate"]["passed"]
        body = _append_signature(result["letter"], prospect)
        db.insert_artifact({
            "prospect_id": prospect["id"], "kind": "letter",
            "status": "sendable" if passed else "blocked",
            "body": body,
            "gate_map": {
                "sentences": result["gate"]["map"],
                "context": {k: v for k, v in context.items() if k != "grant"},
                "grant_claim": (context.get("grant") or [None])[0],
            },
            "gate_failures": [] if passed else rejections + result["gate"]["failures"],
            "claims_cited": result["gate"]["cited"],
            "attempts": min(attempt, LETTER_ATTEMPTS),
            "model": THESIS_MODEL,
            # A letter is posted, not sent electronically, so CASL's
            # published-address test does not bind it — the same reasoning the
            # LinkedIn artifact records. Which law governed is still a fact
            # about the artifact whatever the answer.
            "compliance": compliance.check_artifact(
                prospect, "letter", body).as_dict(),
        })
        console.print(
            f"  [{'green' if passed else 'red'}]{'sendable' if passed else 'blocked'}[/]"
            f" · {len(strip_signature(body).split())} words · running spend "
            f"{spend.line()}")
        if not passed:
            for failure in result["gate"]["failures"][:2]:
                console.print(f"    [red]{failure[:140]}[/red]")
    console.print(f"\n[dim]API spend: {spend.line()}[/dim]")
    return 0


def _append_signature(body: str, prospect: dict[str, Any]) -> str:
    """The sign-off a posted letter carries.

    Our name and our company, and no opt-out line. An unsubscribe instruction on
    a printed letter is furniture borrowed from email: there is no list to come
    off, and printing one would misdescribe what this is.
    """
    profile = compliance.profile_for(prospect.get("source_adapter"))
    if compliance.SENDER_NAME in (body or ""):
        return body
    return (f"{body}\n\n—\n{compliance.SENDER_NAME}\n{compliance.SENDER_COMPANY}\n"
            f"Written under {profile.regime}; posted, not emailed.")

# ------------------------------------------------------------------------ CLI

# ----------------------------------------------------------------- linkedin

CONNECTION_LIMIT = 280
FOLLOWUP_LIMIT = 700
LINKEDIN_ATTEMPTS = 3
"""One more attempt than the email gets, and for a reason that is not a relaxed
standard: the rule these messages break is length, which is mechanical. The
formula's two-attempt limit is about how many times we let a generator re-argue
a diagnosis, and counting a message that was too long as one of those spends a
reasoning attempt on arithmetic."""
"""What LinkedIn actually allows, and therefore what the gate enforces.

A note that overruns is not a style problem — it is a message the operator
cannot send, so length is checked here rather than left to be discovered in the
box."""

LINKEDIN_SYSTEM = (
    "Write two LinkedIn messages to one manufacturer, for a colleague to send by "
    "hand. They are cold touches to a stranger and answer to the same rules as a "
    "first email.\n\n"
    f"1. A CONNECTION NOTE. HARD LIMIT {CONNECTION_LIMIT} characters including "
    f"spaces. AIM FOR about 35 to 42 words. It is the thing attached to a "
    f"connection "
    "request, so it is one or two sentences: one specific, sourced thing about "
    "their business and one line saying why you are reaching out. No pitch, no "
    "arithmetic, no ask beyond connecting.\n"
    f"2. A FOLLOW-UP MESSAGE. HARD LIMIT {FOLLOWUP_LIMIT} characters including "
    f"spaces. AIM FOR about 80 to 100 words. Sent after they accept: two or "
    f"three short "
    "paragraphs with the same sourced facts, at most one clearly-hedged "
    "hypothesis, and a request for a short conversation.\n\n"
    "LENGTH IS THE RULE THIS BREAKS MOST. Count the WORDS before you emit the "
    "block and stay inside the targets above; a message over the limit cannot "
    "be sent at all.\n\n"
    "THE GREETING IS ABOUT US, NOT ABOUT THEM. 'Thanks for connecting' and "
    "'I am writing because' are our own words and carry no claim, so type them "
    '{"type": "about_us"} in the map. They must contain no figure and must not '
    "describe their business.\n\n"
    "TONE. Personal, not templated. These are shorter and warmer than the email "
    "and they rest on the SAME evidence — a reader who saw both should recognise "
    "one person writing twice, not a sequence firing.\n\n"
    "Do NOT include an unsubscribe line, a postal address or a signature block. "
    "Those are email furniture and mean nothing here.\n\n"
    + TYPE_RULE + "\n" + PROSE_RULE
)
"""CITE_RULE is deliberately absent.

It tells the writer to end every factual sentence with a bracketed CLAIM_ID,
and PROSE_RULE refuses prose containing bracket notation. Both belong to this
pipeline and they contradict each other, because the sentence-to-claim mapping
travels in the MAP block rather than in the prose — which is exactly how the
email prompt is built. Including both made the first live run argue with itself
in its own reply: "Wait — I cannot put bracketed CLAIM_IDs in the prose itself."
"""


def linkedin_format_rule() -> str:
    """The blocks a LinkedIn reply must contain."""
    return (
        format_rule("connection")
        + "\nEmit exactly two block pairs, in this order: connection, followup."
    )


def text_as_sent(prose: str) -> str:
    """The message as it will be pasted, with the citations taken out.

    Named for what it returns rather than for what it is checked against: the
    obvious name began with "send", which is the prefix the audit greps for when
    it looks for a path that could put something on the wire without checking
    the halt flag. A helper that counts characters should not look like one.

    A citation is notation for the gate, not part of what anybody sends: the
    operator strips them before the message goes anywhere. Counting them against
    LinkedIn's limit charged each message forty characters per sourced sentence
    and made a note that was comfortably inside the limit read as over it.
    """
    return re.sub(r"\s+", " ", CITATION.sub("", prose or "")).strip()


def too_long(text: str, limit: int, label: str) -> list[str]:
    """A message the operator could not actually send is a failed message."""
    length = len(text_as_sent(text))
    if length <= limit:
        return []
    return [f"the {label} runs {length} characters once the sources are stripped "
            f"out; LinkedIn allows {limit}"]


SHORTEN_SYSTEM = (
    "You are shortening a message that is already correct in every way except "
    "its length. Keep the same facts, the same order, the same voice and the "
    "same sentence map. Cut adjectives, subordinate clauses and any sentence "
    "that repeats another. Do not add a fact, do not drop a source, and do not "
    "reword a sentence into something the map no longer describes.\n\n"
    + PROSE_RULE
)


def only_too_long(failures: list[str]) -> bool:
    """Whether length is the only thing wrong with a message.

    Worth asking, because a length overrun is not a reasoning failure. A message
    that is seven characters over does not need a different idea; it needs seven
    fewer characters, and re-rolling the whole draft to get them throws away a
    piece of writing that already passed every rule that matters.
    """
    return bool(failures) and all("LinkedIn allows" in f for f in failures)


async def shorten_linkedin(
    result: dict[str, Any], prospect: dict[str, Any], client: Any,
    verdicts: tuple[str, ...], spend: Spend | None = None,
) -> dict[str, Any]:
    """Ask for the same two messages, shorter, and re-gate what comes back."""
    over = [
        (label, limit) for label, limit in
        (("connection", CONNECTION_LIMIT), ("followup", FOLLOWUP_LIMIT))
        if not result[f"{label}_gate"]["passed"]
    ]
    asks = "\n\n".join(
        f"--- {label.upper()} — currently "
        f"{len(text_as_sent(result[label]))} characters, must be at most {limit}. "
        f"Cut roughly {max(10, len(text_as_sent(result[label])) - limit + 20)} "
        f"characters.\n{result[label]}"
        for label, limit in over
    )
    raw = await _call(
        client, SHORTEN_SYSTEM,
        f"COMPANY: {prospect.get('company_name')}\n\n{asks}\n\n"
        + format_rule("connection")
        + "\nEmit exactly two block pairs, in this order: connection, followup. "
          "Emit BOTH even if only one needed shortening; the one that was already "
          "short comes back unchanged.",
        max_tokens=EMAIL_TOKENS, spend=spend,
    )
    sections = parse_sections(raw)
    connection = section_named(sections, "connection")
    followup = section_named(sections, "followup")
    validate_prose(connection.prose, "connection note")
    validate_prose(followup.prose, "follow-up")

    all_qualifying = qualifying_claims(prospect)
    allowed = {path for path, _ in all_qualifying}
    hypothesis_paths = {path for path, _ in hypothesis_claims(prospect)}
    values = {path: str(claim.get("value")) for path, claim in all_qualifying}
    _sal, gate_result = salutation_for(prospect)
    person_allowed = bool(gate_result and gate_result.allowed)
    person_name = gate_result.name if gate_result else None

    out = dict(result)
    for label, part, limit in (("connection", connection, CONNECTION_LIMIT),
                               ("followup", followup, FOLLOWUP_LIMIT)):
        verdict = gate_prose(part.prose, part.sentence_map, allowed, hypothesis_paths,
                             person_allowed, person_name,
                             prospect.get("company_name"), "email", values)
        verdict["failures"] = list(verdict["failures"]) + too_long(
            part.prose, limit, f"{label} message")
        verdict["passed"] = not verdict["failures"]
        out[label] = part.prose.strip()
        out[f"{label}_gate"] = verdict
    return out


async def draft_linkedin(
    prospect: dict[str, Any], client: Any, verdicts: tuple[str, ...],
    email_body: str, spend: Spend | None = None,
    failures: list[str] | None = None,
) -> dict[str, Any]:
    """Produce the connection note and the follow-up, both gated."""
    facts = assertable_claims(prospect, verdicts)
    hypotheses = hypothesis_claims(prospect)
    all_qualifying = qualifying_claims(prospect)
    allowed = {p for p, _ in all_qualifying}
    hypothesis_paths = {p for p, _ in hypotheses}

    salutation, gate_result = salutation_for(prospect)
    person_allowed = bool(gate_result and gate_result.allowed)
    person_name = gate_result.name if gate_result else None

    header = (
        f"COMPANY: {prospect.get('company_name')}\n"
        f"County: {prospect.get('county')} · about "
        f"{prospect.get('drive_minutes')} minutes from Muncie\n"
        f"Industry (from the grant listing): {prospect.get('industry_desc')}\n"
    )
    raw = await _call(
        client, LINKEDIN_SYSTEM,
        f"{header}\n"
        f"Greeting must address: {salutation}\n"
        f"(the person gate {'passed' if person_allowed else 'FAILED — use no name'})\n\n"
        f"FACTS YOU MAY ASSERT:\n{render_claims(facts[:8]) or '(none qualify)'}\n\n"
        f"HYPOTHESES — at most ONE, hedged:\n"
        f"{render_claims(hypotheses[:3]) or '(none available)'}\n\n"
        f"THE EMAIL ALREADY WRITTEN TO THEM. Rest on the same evidence; do not "
        f"repeat its sentences word for word.\n{email_body[:2500]}\n\n"
        f"{linkedin_format_rule()}\n\n{feedback_block(failures or [])}",
        max_tokens=EMAIL_TOKENS, spend=spend,
    )
    sections = parse_sections(raw)
    connection = section_named(sections, "connection")
    followup = section_named(sections, "followup")
    validate_prose(connection.prose, "connection note")
    validate_prose(followup.prose, "follow-up")

    company = prospect.get("company_name")
    values = {path: str(claim.get("value")) for path, claim in all_qualifying}
    gates = {}
    for label, part, limit in (
        ("connection", connection, CONNECTION_LIMIT),
        ("followup", followup, FOLLOWUP_LIMIT),
    ):
        verdict = gate_prose(part.prose, part.sentence_map, allowed, hypothesis_paths,
                             person_allowed, person_name, company, "email", values)
        verdict["failures"] = list(verdict["failures"]) + too_long(
            part.prose, limit, f"{label} message")
        verdict["passed"] = not verdict["failures"]
        gates[label] = verdict

    return {
        "connection": connection.prose.strip(),
        "followup": followup.prose.strip(),
        "connection_gate": gates["connection"],
        "followup_gate": gates["followup"],
        "salutation": salutation,
        "person_allowed": person_allowed,
    }


def current_linkedin() -> dict[str, dict[str, Any]]:
    """Prospect id -> the LinkedIn artifact that counts, which is the newest live one.

    Same rule the email is judged by. "Ever passed once" is the wrong question:
    it would let a company whose latest pair was refused go on being counted as
    ready on the strength of a draft nothing now points at.
    """
    newest: dict[str, dict[str, Any]] = {}
    for artifact in db.all_artifacts():
        if (artifact.get("kind") != "linkedin"
                or artifact.get("status") not in ("sendable", "blocked", "draft")):
            continue
        current = newest.get(artifact["prospect_id"])
        if current is None or artifact["created_at"] > current["created_at"]:
            newest[artifact["prospect_id"]] = artifact
    return newest


def companies_with_a_sendable_email() -> dict[str, dict[str, Any]]:
    """Prospect id -> the email artifact that passed, newest first.

    LinkedIn is written for companies we can already write to. A second channel
    for a company whose first message never cleared the gate would be a second
    way to send something we already refused to send once.
    """
    newest: dict[str, dict[str, Any]] = {}
    for artifact in db.all_artifacts():
        # Superseded means "not current", so a superseded row is not a candidate
        # for newest — otherwise a retired draft outranks the live one behind it.
        if (artifact.get("kind") != "email"
                or artifact.get("status") not in ("sendable", "blocked", "draft")):
            continue
        current = newest.get(artifact["prospect_id"])
        if current is None or artifact["created_at"] > current["created_at"]:
            newest[artifact["prospect_id"]] = artifact
    # The NEWEST email has to be the one that passed, not merely some email that
    # once did. Reading "the newest sendable" instead would let a company whose
    # latest draft was refused keep an older pass alive and go on collecting a
    # second channel on the strength of it.
    return {pid: a for pid, a in newest.items() if a.get("status") == "sendable"}


def below_floor(prospect: dict[str, Any], verdicts: tuple[str, ...]) -> str | None:
    """Why this company is not worth drafting yet, or None.

    CASE-1 §6, enforced by machine rather than by intention: three assertable
    facts or the file does not ship. Below that there is nothing to build a
    letter out of, and a generator told to try anyway pads the space with
    reasoning the gate then refuses — so the record ends up saying the draft
    was bad when the truth is it should never have been attempted.
    """
    facts = len(assertable_claims(prospect, verdicts))
    if facts >= formula.EVIDENCE_FLOOR:
        return None
    return (f"below evidence floor: {facts} assertable fact"
            f"{'' if facts == 1 else 's'}, {formula.EVIDENCE_FLOOR} required")


def email_impossible(prospect: dict[str, Any]) -> str | None:
    """Why an email to this company can never pass its own regime, or None.

    CASL's exemption is the conspicuously published address, and a company that
    has published none is not covered by it. Generating a draft for one and
    watching the gate refuse it costs a model call to learn something the
    evidence file already said, and leaves a `blocked` row that reads as a
    writing failure when the truth is that this channel is shut.

    A skip says the true thing: the letter and LinkedIn are still open, and what
    would change this is contact discovery finding a published address, not a
    better draft.
    """
    profile = compliance.profile_for(prospect.get("source_adapter"))
    if not profile.requires_published_address:
        return None
    if compliance.published_addresses(prospect):
        return None
    return (
        f"no published email address is recorded, so {profile.regime}'s "
        f"conspicuous-publication exemption does not cover a message to this "
        f"company. Nothing may be guessed. The letter and LinkedIn are unaffected"
    )


def candidate_prospects(
    limit: int | None, adapter: str | None = None
) -> list[dict[str, Any]]:
    """P1 prospects passing integrity, nearest first, never one under verification."""
    locked = {s["prospect_id"] for s in db.open_sessions()}
    rows = [
        p for p in db.list_prospects_full(adapter)
        if p.get("priority") == "P1"
        and p["id"] not in locked
        and evidence_integrity(p).passing
        # A company held for an operator's decision on size is not a company we
        # have decided to write to. It stays researched and stays scored; it
        # simply does not reach a draft until somebody says it should.
        and icp.outreach_eligible(p)
    ]
    rows.sort(key=lambda p: (
        (p.get("drive_minutes") or 999) > 90,
        -(p.get("signal_score") or 0),
        p.get("drive_minutes") or 999,
    ))
    return rows[:limit] if limit else rows


def eligible_prospects(
    limit: int | None, verdicts: tuple[str, ...] = ("verbatim",),
    adapter: str | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    """The companies worth drafting, and the ones held back with their reason."""
    drafting, held = [], []
    for prospect in candidate_prospects(limit, adapter):
        reason = below_floor(prospect, verdicts)
        if reason:
            held.append((prospect, reason))
        else:
            drafting.append(prospect)
    return drafting, held


async def _run_linkedin(limit: int | None, dry_run: bool, console: Console,
                        only_blocked: bool = True, adapter: str | None = None) -> int:
    """Write the two LinkedIn messages for every company whose email cleared."""
    from lib import routing

    state = canary.read_state()
    verdicts = state.allowed_verdicts()
    emails = companies_with_a_sendable_email()
    # Every full-dossier company, not only the ones whose email cleared.
    #
    # The old rule — "a second channel for a company whose first message never
    # cleared would be a second way to send something we already refused" — is
    # right about a refused draft and wrong about a company CASL forbids us to
    # email at all. Those two look identical from here and are not: one is a
    # sentence we could not write, the other is a channel that is shut. Gating
    # LinkedIn behind an email the law forbids meant the Canadian set had no
    # open channel whatever, on the one platform where the operator is the
    # sender and the published-address test does not bind.
    #
    # What has not loosened: the company must still clear the evidence floor,
    # and the LinkedIn pair still goes through the same gate the email does.
    rows = [p for p in db.list_prospects_full(adapter)
            if icp.outreach_eligible(p)
            and evidence_integrity(p).passing
            and routing.may_write_claims(p, verdicts)]
    if only_blocked:
        # Never re-roll a company that already passed. The generator is not
        # deterministic, so a second run over a sendable artifact is a coin
        # flip that can only lose — which is exactly what a full re-run did
        # before this existed, replacing good messages with refused ones.
        #
        # Read ONCE. This was inside the comprehension, so it re-fetched every
        # artifact in the database per prospect — two hundred full table reads
        # to answer two hundred questions about one table. The dry run timed
        # out before it could print its own plan.
        live = current_linkedin()
        rows = [p for p in rows
                if (live.get(p["id"]) or {}).get("status") != "sendable"]
    from lib import triggers

    rows.sort(key=triggers.sort_key)
    if limit:
        rows = rows[:limit]

    table = Table(title=f"{len(rows)} full-dossier compan"
                        f"{'y' if len(rows) == 1 else 'ies'} to write to on LinkedIn",
                  title_justify="left")
    for column in ("Company", "Score", "Person gate", "Email"):
        table.add_column(column)
    for prospect in rows:
        _sal, gate = salutation_for(prospect)
        table.add_row(str(prospect.get("company_name"))[:38],
                      str(prospect.get("signal_score")),
                      "named" if (gate and gate.allowed) else "role only",
                      "cleared" if prospect["id"] in emails else
                      ("shut" if email_impossible(prospect) else "not yet"))
    console.print(table)
    if dry_run:
        console.print("\n[dim]--dry-run: nothing generated, nothing written.[/dim]")
        return 0

    from lib.config import settings
    if not settings.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY is not set; cannot draft.[/red]")
        return 1

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    spend = Spend()
    for prospect in rows:
        console.print(f"\n[cyan]{prospect.get('company_name')}[/cyan]")
        attempt, result = 1, None
        rejections: list[str] = []
        feedback: list[str] = []
        while attempt <= LINKEDIN_ATTEMPTS:
            try:
                result = await draft_linkedin(
                    prospect, client, verdicts,
                    (emails.get(prospect["id"]) or {}).get("body") or "",
                    spend, feedback)
            except ProseRejected as exc:
                rejections.append(f"attempt {attempt}: {exc}")
                feedback = [str(exc)]
                console.print(f"  [yellow]attempt {attempt} rejected:[/yellow] {exc}")
                attempt += 1
                continue
            if result["connection_gate"]["passed"] and result["followup_gate"]["passed"]:
                break
            feedback = [f for gate in ("connection_gate", "followup_gate")
                        for f in result[gate]["failures"]]
            console.print(f"  [yellow]attempt {attempt} blocked:[/yellow] "
                          + "; ".join(f[:100] for f in feedback[:2]))
            attempt += 1

        # One targeted shortening pass when length is all that is wrong. It is a
        # different operation from regenerating, so it gets its own attempt
        # rather than eating one of the two the formula allows.
        if result is not None:
            length_only = [
                f for gate in ("connection_gate", "followup_gate")
                for f in result[gate]["failures"]
            ]
            if only_too_long(length_only):
                try:
                    result = await shorten_linkedin(
                        result, prospect, client, verdicts, spend)
                    console.print("  [dim]shortened and re-checked[/dim]")
                except ProseRejected as exc:
                    console.print(f"  [yellow]shortening rejected:[/yellow] {exc}")

        if result is None:
            db.insert_artifact({
                "prospect_id": prospect["id"], "kind": "linkedin",
                "status": "blocked", "body": "", "gate_failures": rejections,
                "attempts": LINKEDIN_ATTEMPTS, "model": THESIS_MODEL,
                "compliance": compliance.not_drafted(prospect, "linkedin"),
            })
            console.print(f"  [red]blocked[/red] — never parsed: {rejections[-1]}")
            continue

        # Both messages live in one artifact because they are one approach on one
        # channel: a connection note nobody follows up is not a touch, and a
        # follow-up with no note in front of it cannot be sent at all.
        passed = all(result[g]["passed"] for g in ("connection_gate", "followup_gate"))
        note_len = len(text_as_sent(result["connection"]))
        follow_len = len(text_as_sent(result["followup"]))
        body = (f"CONNECTION NOTE ({note_len} characters as sent)\n\n"
                f"{result['connection']}\n\n"
                f"FOLLOW-UP MESSAGE ({follow_len} characters as sent)\n\n"
                f"{result['followup']}")
        db.insert_artifact({
            "prospect_id": prospect["id"], "kind": "linkedin",
            "status": "sendable" if passed else "blocked",
            "body": body,
            "gate_map": (result["connection_gate"]["map"]
                         + result["followup_gate"]["map"]),
            "gate_failures": (
                [] if passed else rejections
                + result["connection_gate"]["failures"]
                + result["followup_gate"]["failures"]),
            "claims_cited": sorted(set(result["connection_gate"]["cited"])
                                   | set(result["followup_gate"]["cited"])),
            "attempts": min(attempt, LINKEDIN_ATTEMPTS),
            "model": THESIS_MODEL,
            # The regime is recorded on a LinkedIn artifact too, and records
            # that CASL's published-address test does not bind it: the operator
            # sends this by hand inside a platform with its own rules, so it is
            # not an electronic message to an address we hold. Saying which law
            # applied is a fact about the artifact whatever the answer.
            "compliance": compliance.check_artifact(
                prospect, "linkedin", body).as_dict(),
        })
        console.print(
            f"  [{'green' if passed else 'red'}]{'sendable' if passed else 'blocked'}[/]"
            f" · note {note_len}c · follow-up {follow_len}c · running spend "
            f"{spend.line()}")
    console.print(f"\n[dim]API spend: {spend.line()}[/dim]")
    return 0


async def _run(limit: int | None, dry_run: bool, console: Console,
               only_blocked: bool = True, adapter: str | None = None) -> int:
    state = canary.read_state()
    if state.halted:
        console.print(f"[red]Pipeline is halted: {state.halt_reason}[/red]")
        console.print("Drafting is allowed while halted, but nothing may be sent.")
    verdicts = state.allowed_verdicts()
    console.print(
        f"Canary: batch {state.batches_sent}, assertable verdicts {verdicts}, "
        f"halted={state.halted}\n"
    )

    rows, held = eligible_prospects(limit, verdicts, adapter)
    if only_blocked:
        # Same rule the LinkedIn run keeps, and for the same reason: the
        # generator is not deterministic, so a second pass over a company whose
        # email already cleared is a coin flip that can only lose. A full re-run
        # did exactly that — two companies that had passed came back refused —
        # which is why this is the default rather than a flag.
        passing = set(companies_with_a_sendable_email())
        skipped = [p for p in rows if p["id"] in passing]
        rows = [p for p in rows if p["id"] not in passing]
        if skipped:
            console.print(f"[dim]{len(skipped)} compan"
                          f"{'y' if len(skipped) == 1 else 'ies'} already have a "
                          f"sendable email and were left alone; --redo-all to "
                          f"rewrite them[/dim]")
    table = Table(title=f"{len(rows)} prospect(s) eligible for drafting",
                  title_justify="left")
    for column in ("Company", "Score", "Drive", "Assertable facts", "Person gate"):
        table.add_column(column)
    for p in rows:
        facts = assertable_claims(p, verdicts)
        _sal, gate = salutation_for(p)
        table.add_row(
            str(p.get("company_name"))[:38],
            str(p.get("signal_score")),
            f"{p.get('drive_minutes')}m",
            str(len(facts)),
            "named" if (gate and gate.allowed) else "role only",
        )
    console.print(table)

    if held:
        skipped = Table(title=f"{len(held)} held back below the evidence floor",
                        title_justify="left")
        for column in ("Company", "Reason"):
            skipped.add_column(column)
        for prospect, reason in held:
            skipped.add_row(str(prospect.get("company_name"))[:38], reason)
        console.print(skipped)

    if dry_run:
        console.print("\n[dim]--dry-run: nothing generated, nothing written.[/dim]")
        return 0

    # Recorded, not merely printed: the reason a company was passed over has to
    # outlive the run that decided it, or the next operator re-litigates it.
    for prospect, reason in held:
        db.insert_artifact({
            "prospect_id": prospect["id"], "kind": "email", "status": "skipped",
            "body": "", "gate_failures": [reason], "attempts": 0,
            "model": THESIS_MODEL,
            "compliance": compliance.not_drafted(prospect, "email"),
        })
    for prospect in list(rows):
        reason = email_impossible(prospect)
        if not reason:
            continue
        rows.remove(prospect)
        db.insert_artifact({
            "prospect_id": prospect["id"], "kind": "email", "status": "skipped",
            "body": "", "gate_failures": [reason], "attempts": 0,
            "model": THESIS_MODEL,
            "compliance": compliance.not_drafted(prospect, "email"),
        })
        console.print(f"[dim]skipped {prospect.get('company_name')}: "
                      f"{reason[:96]}[/dim]")

    from lib.config import settings
    if not settings.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY is not set; cannot draft.[/red]")
        return 1

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    spend = Spend()
    for prospect in rows:
        console.print(f"\n[cyan]drafting {prospect.get('company_name')}[/cyan]")
        attempt, result = 1, None
        # Kept across attempts and stored with the artifact: a reply we could
        # not read is the most useful thing to have afterwards, and the second
        # attempt overwriting the first one's error hides why it regenerated.
        rejections: list[str] = []
        # What the previous attempt got wrong, handed forward so the retry is
        # not guessing at eight simultaneous rules a second time.
        feedback: list[str] = []
        while attempt <= MAX_ATTEMPTS:
            try:
                result = await draft_prospect(
                    prospect, client, verdicts, spend, feedback)
            except ProseRejected as exc:
                rejections.append(f"attempt {attempt}: {exc}")
                feedback = [str(exc)]
                console.print(f"  [yellow]attempt {attempt} rejected:[/yellow] {exc}")
                attempt += 1
                continue
            if result["email_gate"]["passed"] and result["brief_gate"]["passed"]:
                break
            feedback = [
                f for gate in ("email_gate", "brief_gate", "thesis_gate")
                for f in result[gate]["failures"]
            ]
            console.print(
                f"  [yellow]attempt {attempt} blocked:[/yellow] "
                + "; ".join(f[:110] for f in feedback[:3])
            )
            attempt += 1
        if result is None:
            db.insert_artifact({
                "prospect_id": prospect["id"], "kind": "email", "status": "blocked",
                "body": "", "gate_failures": rejections,
                "attempts": MAX_ATTEMPTS, "model": THESIS_MODEL,
            })
            console.print(f"  [red]blocked[/red] — never parsed: {rejections[-1]}")
            continue
        # The thesis is judged too. It was carried as a perpetual draft, which
        # meant the leave-behind — the one artifact a company physically holds
        # — was built from prose nothing had ever passed or failed.
        passed = all(result[g]["passed"]
                     for g in ("email_gate", "brief_gate", "thesis_gate"))
        # Each artifact carries its own verdict rather than the batch's, so a
        # thesis that passed is usable for a leave-behind even when the email
        # beside it failed. The prospect-level line below still reports the
        # stricter all-three answer.
        status = "sendable" if passed else "blocked"
        for kind, body, gate in (
            ("thesis", result["thesis"], result["thesis_gate"]),
            ("email", result["email"], result["email_gate"]),
            ("brief", result["brief"], result["brief_gate"]),
        ):
            db.insert_artifact({
                "prospect_id": prospect["id"],
                "kind": kind,
                "compliance": (result.get("compliance") or {}).get(kind),
                "status": "sendable" if (gate or {}).get("passed") else "blocked",
                "body": body,
                "gate_map": (gate or {}).get("map"),
                # A rejection belongs to the attempt that was thrown away, not
                # to the artifact that survived it. Recording both in one field
                # made an email whose own gate found nothing read as a sendable
                # artifact with failures, which is a contradiction the audit
                # rightly refuses. Blocked artifacts keep the full history.
                "gate_failures": (
                    (gate or {}).get("failures") or []
                    if (gate or {}).get("passed")
                    else rejections + ((gate or {}).get("failures") or [])
                ),
                "claims_cited": (gate or {}).get("cited"),
                "attempts": attempt if attempt <= MAX_ATTEMPTS else MAX_ATTEMPTS,
                "model": THESIS_MODEL,
            })
        # Printed per prospect, not only at the end: a run killed part-way
        # through still has to be able to say what it cost, and the first two
        # batches could not because the total never reached the console.
        console.print(
            f"  [{'green' if passed else 'red'}]{status}[/] after {min(attempt, MAX_ATTEMPTS)} "
            f"attempt(s) · running spend {spend.line()}"
        )
    console.print(f"\n[dim]API spend: {spend.line()}[/dim]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Draft theses and gated outbound artifacts for P1 prospects."
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--linkedin", action="store_true",
                        help="write the connection note and follow-up for every "
                             "company whose email already cleared the gate")
    parser.add_argument("--letters", action="store_true",
                        help="write the one-page fragment letter for every "
                             "full-dossier company, freshest trigger first")
    parser.add_argument("--redo-all", action="store_true",
                        help="rewrite companies that already passed too. Off by "
                             "default: the generator is not deterministic, so "
                             "re-rolling a pass can only lose")
    parser.add_argument("--company", default=None,
                        help="with --letters, write for one company by name")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be drafted; generate nothing")
    adapters.add_argument(parser)
    args = parser.parse_args()
    console = Console()
    console.print(f"Scope: [bold]{adapters.words(args.adapter)}[/bold]")
    if args.letters:
        return asyncio.run(_run_letters(
            args.limit, args.dry_run, console, not args.redo_all, args.adapter,
            args.company))
    if args.linkedin:
        return asyncio.run(_run_linkedin(
            args.limit, args.dry_run, console, not args.redo_all, args.adapter))
    return asyncio.run(_run(args.limit, args.dry_run, console,
                            not args.redo_all, args.adapter))


if __name__ == "__main__":
    raise SystemExit(main())
