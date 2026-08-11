"""The DATA-1 outreach formula, in the one place the gate reads it from.

WHY THIS MODULE EXISTS

The formula has always been three-part: T1 facts a reader can check, exactly
one labelled hypothesis, and arithmetic they can correct. The gate only ever
enforced the first part. Every sentence had to map to a claim about the
prospect, so the second and third parts — an assumption offered for testing, a
salutation, our own reasoning — were structurally unmappable and blocked every
artifact that obeyed the formula.

A live batch made that concrete. These sentences were refused, and all three
are the formula working as designed:

    "If your internal engineering time runs at a fully loaded cost somewhere
     between eighty and one hundred and twenty dollars an hour..."
    "That is a precise structural diagnosis, not a complaint."
    "I am writing to the owner or president of Polaris Laboratories."

So sentences are now typed, and each type carries its own burden of proof. The
point is not to exempt anything. An assumption is held to rules a fact is not
asked to meet — it must announce itself as conditional, it must be a range
rather than a point, and the artifact carrying it must invite correction. A
sentence about us must assert nothing about the prospect and carry no figure.

Every marker list lives here rather than in the gate, because these are
editorial judgements about language and they should be reviewable in one sitting
by someone who does not read Python.
"""

from __future__ import annotations

import re

FACT = "fact"
ASSUMPTION = "assumption"
ABOUT_US = "about_us"
INFERENCE = "inference"
SENTENCE_TYPES = (FACT, ASSUMPTION, ABOUT_US, INFERENCE)
DEFAULT_TYPE = FACT

EVIDENCE_FLOOR = 3
"""Assertable facts a company must have before anything is drafted for it.

CASE-1 §6: three Tier-1 facts minimum, or the file never ships. A draft built
on one or two facts fills the remaining space with reasoning, which is exactly
what the gate then refuses — so the floor stops the work earlier and more
honestly than the gate can. Polaris, with two, is the case that proved it."""


REASONING_MARKERS = (
    "suggests", "suggest", "tells me", "tells us", "signals", "signal that",
    "implies", "imply", "which means", "that means", "points to", "points at",
    "indicates", "indicate", "reads as", "would explain", "explains why",
    "consistent with", "suggests that", "makes me think", "the shape of that",
    "which is why", "so it follows", "that puts", "that leaves",
)
"""Language that marks a sentence as reasoning FROM something, not asserting it.

An inference is the formula's middle third: we take a fact they can check and
say what we think it means. The marker is what tells the reader which half
they are reading. Without it the sentence presents our conclusion in the same
voice as their own published words."""


CONDITIONAL_MARKERS = (
    "if ", "if,", "assuming", "assume ", "suppose", "somewhere between",
    "somewhere around", "should that hold", "should that be", "if that is right",
    "if that holds", "were that", "on the assumption", "taking ", "say ",
    "roughly ", "about ", "approximately", "in the region of", "order of",
)
"""Language that marks a figure as offered rather than asserted.

An assumption that does not announce itself is an assertion wearing a hedge,
and a reader has no way to tell which one they are holding."""


CORRECTION_INVITATIONS = (
    "test", "check", "checkable", "correct", "corrected", "wrong",
    "tell me", "tell us", "against your own", "your own numbers",
    "your own figures", "your own payroll", "put me right",
)
"""Phrases that make an artifact's arithmetic correctable.

The whole reason to publish a conditional range is to be written back to with
a real number. An artifact that reasons from assumptions without ever asking
to be corrected is not inviting a conversation, it is guessing in public."""


QUANTITY = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?"      # $102,000 — currency first, so it matches whole
    r"|\d[\d,]*\.\d+"               # 0.30
    r"|\d{1,3}(?:,\d{3})+"          # 102,000
    r"|\b\d+\s?%"                   # 25%
    r"|\b\d{3,}\b"                  # 3200
)
"""A number that ASSERTS a quantity, and so needs a source or an assumption.

Deliberately not "any digit". The first live run blocked artifacts over the '1'
in a 'FINDING 1' heading and the street number in the CAN-SPAM signature —
neither of which claims anything about the prospect.

The alternatives are ordered so a match covers the WHOLE figure. Written the
other way round, "$102,000" matched as "$1" followed by "02,000", which was
invisible while this was only ever asked whether a quantity was present, and
wrong the moment anything read the matched text back."""


NUMBER = re.compile(r"\$?\s?\d[\d,]*(?:\.\d+)?\s?%?")
"""Any number at all, used when checking the inputs to a stated calculation."""


RANGE_SPAN = re.compile(
    r"\$?\s?\d[\d,]*(?:\.\d+)?\s*(?:–|—|-|\bto\b|\band\b)\s*\$?\s?\d[\d,]*(?:\.\d+)?",
    re.IGNORECASE,
)
"""Two numbers joined into a span: '$80-$120', '25 to 40', 'between 8 and 12'."""


ARITHMETIC = re.compile(r"=|×|\bx\b(?=\s*\$?\s?\d)")
"""A sentence showing its working, which the reader is meant to be able to redo."""


def has_conditional(sentence: str) -> bool:
    """True when the sentence frames its figure as an assumption."""
    return any(m in (sentence or "").lower() for m in CONDITIONAL_MARKERS)


def reasons_aloud(sentence: str) -> bool:
    """True when the sentence shows the reader that it is reasoning."""
    return any(m in (sentence or "").lower() for m in REASONING_MARKERS)


def invites_correction(text: str) -> bool:
    """True when the artifact asks to be told it is wrong."""
    return any(m in (text or "").lower() for m in CORRECTION_INVITATIONS)


def _spans(sentence: str) -> list[tuple[int, int]]:
    return [m.span() for m in RANGE_SPAN.finditer(sentence or "")]


def point_quantities(sentence: str) -> list[str]:
    """Quantities stated as a single figure rather than a span.

    An assumption must be a range. A point estimate reads as knowledge — "your
    quoting desk costs $30,000 a year" is a claim about them however it is
    hedged, whereas "somewhere between $25,000 and $40,000" is visibly ours.
    """
    text = sentence or ""
    covered = _spans(text)
    out = []
    for match in QUANTITY.finditer(text):
        start, end = match.span()
        if not any(a <= start and end <= b for a, b in covered):
            out.append(match.group(0).strip())
    return out


WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
"""Numbers spelled as words, because good prose spells small ones out.

"If two engineers spend thirty percent of their week" establishes both figures
for a reader, and arithmetic written as "2 x 0.30" is checkable against it.
Only single words are recognised — a compound like "one hundred and twenty"
is not, so anything an artifact intends to compute with should appear in
digits at least once. The prompt asks for exactly that."""

WORD_NUMBER = re.compile(rf"\b({'|'.join(WORD_NUMBERS)})\b", re.IGNORECASE)


def traces_to(quantity: str, claims: list[str], claim_values: dict[str, str]) -> bool:
    """True when a figure in an inference appears in a claim that inference cites.

    An inference may restate a number it is reasoning from — "that $102,000
    award tells me..." is quoting their own record back at them. It may not
    introduce one. So the figure has to be findable in the text of a claim the
    sentence actually cited, not merely somewhere in the evidence file.
    """
    wanted = numbers_in(quantity)
    if not wanted:
        return True
    return any(
        wanted & numbers_in(str(claim_values.get(path, "")))
        for path in claims or []
    )


def numbers_in(text: str) -> set[float]:
    """Every number in a piece of text, as values rather than spellings.

    Compared numerically because the same input is legitimately written two
    ways: an assumption says "between 20 and 40 percent" and the arithmetic
    beside it says "0.20-0.40". Those are the same figure, and a string
    comparison would call the arithmetic unsupported.
    """
    out = set()
    for match in NUMBER.finditer(text or ""):
        raw = match.group(0).replace("$", "").replace(",", "").replace("%", "")
        try:
            out.add(float(raw.strip().rstrip(".")))
        except ValueError:
            continue
    for word in WORD_NUMBER.findall(text or ""):
        out.add(float(WORD_NUMBERS[word.lower()]))
    return out


def _establishes(value: float, supported: set[float]) -> bool:
    """True when the artifact stated this figure, in percent or fraction form."""
    return any(
        abs(value - candidate) < 1e-9
        or abs(value * 100 - candidate) < 1e-9
        or abs(value / 100 - candidate) < 1e-9
        for candidate in supported
    )


def shows_arithmetic(sentence: str) -> bool:
    """True when a sentence lays out a calculation rather than a single figure."""
    return bool(ARITHMETIC.search(sentence or ""))


def unsupported_inputs(sentence: str, supported: set[str]) -> list[str]:
    """Inputs to a stated calculation that nothing in the artifact establishes.

    Checking that the RESULT is a range is not enough. "2 engineers x 0.30 x 40
    weeks x $80-120 = $76,800-115,200" is only checkable if the reader was
    already told where the 2, the 0.30 and the 40 came from. Every input must
    be either mapped to a claim or stated as an assumption somewhere in the
    same artifact; otherwise the arithmetic launders an invented number into a
    conclusion that looks derived.
    """
    if not shows_arithmetic(sentence):
        return []
    left = re.split(r"=", sentence, maxsplit=1)[0]
    missing = [
        value for value in sorted(numbers_in(left))
        if _is_substantive(value) and not _establishes(value, supported)
    ]
    return [f"{v:g}" for v in missing]


def _is_substantive(value: float) -> bool:
    """Ignore the small integers that are grammar rather than quantity.

    A bare "1" in "finding 1", a "12" for months, a "52" for weeks: these are
    scaffolding a reader supplies for themselves. The line is drawn low enough
    to catch a real input and high enough to skip ordinals and calendar units.
    """
    return value not in (0, 1, 12, 52, 100) and abs(value) >= 0.01


def result_of(sentence: str) -> str:
    """Whatever the calculation claims to produce."""
    parts = re.split(r"=", sentence or "", maxsplit=1)
    return parts[1] if len(parts) > 1 else ""


STATIVE_VERBS = (
    r"is|are|was|were|has|have|had|runs?|ran|makes?|made|builds?|built|"
    r"operates?|employs?|sells?|ships?|produces?|uses?|owns?|carries|carry"
)

PROSPECT_SUBJECT = re.compile(
    rf"^\s*(?:and\s+|but\s+|so\s+)?your\s+[\w-]+(?:\s+[\w-]+)?\s+(?:{STATIVE_VERBS})\b",
    re.IGNORECASE,
)
"""A second-person SUBJECT with a factual verb — a claim, however it is labelled.

The distinction is grammatical position, not the word "your". "I came across
your capabilities page" is us describing what we did and needs no source.
"Your line runs three shifts" is a statement about their business and must be
sourced. Only the second has them as the subject of a stative verb."""


def asserts_about_prospect(sentence: str, company_name: str | None = None) -> bool:
    """True when a supposedly about-us sentence actually describes the prospect."""
    text = (sentence or "").strip()
    if QUANTITY.search(text):
        return True
    if company_name:
        stem = re.sub(
            r"\b(inc|llc|ltd|corp|corporation|company|co|plc|group|limited)\b\.?",
            "", company_name, flags=re.I,
        ).strip()
        first = stem.split()[0] if stem.split() else ""
        # Naming the company is fine in a salutation; describing it is not.
        if first and len(first) > 3 and re.search(
            rf"\b{re.escape(first)}\b\s+(?:{STATIVE_VERBS})\b", text, re.I,
        ):
            return True
    return bool(PROSPECT_SUBJECT.search(text))
