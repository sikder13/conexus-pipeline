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

from lib import numerals

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
    # Hedges. A sentence that says "we think" is showing its reasoning as
    # plainly as one that says "suggests", and the first live batch under this
    # rule blocked on exactly that — an inference reading "Our hypothesis is
    # that the procurement steps..." was refused for not announcing itself.
    "we think", "we suspect", "our hypothesis", "we would guess", "our guess",
    "we may be wrong", "appears to", "seems to", "likely", "probably",
    "may be", "might be", "could be",
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


NUMBER = re.compile(r"\$?\s?\d[\d,]*(?:\.\d+)?\s?%?")
"""Any number at all, used when checking the inputs to a stated calculation."""


RANGE_SPAN = numerals.RANGE_SPAN
"""Two numbers joined into a span. Defined in ``lib/numerals.py``."""


ARITHMETIC = re.compile(
    r"\d[\d,.]*\s*(?:%|percent)?\s*"
    r"(?:=|×|x|multiplied by|times|divided by|gives|comes to|works out to|adds up to)"
    r"\s*(?:about|roughly|around|some)?\s*\$?\s?\d",
    re.IGNORECASE,
)
"""A sentence showing its working, which the reader is meant to be able to redo.

Working can be written in words — "$60,000-$120,000 multiplied by 20-40 percent"
is a calculation and reading only for "=" and "x" refused it. But an operator
word alone is not enough: "three times the relationships you manage" and "3
times the grant" are ordinary prose, and treating them as arithmetic blocked
drafts for using a comparison. A calculation has a NUMBER ON BOTH SIDES of its
operator, which is the whole difference."""

RESULT_SPLIT = re.compile(
    r"=|\bgives\b|\bcomes to\b|\bworks out to\b|\badds up to\b", re.IGNORECASE
)
"""Where a calculation stops working and states its answer."""


def calculations(sentence: str) -> list[tuple[str, str]]:
    """Every calculation in a sentence, as (working, result).

    One sentence can carry two. "2 hours x 52 weeks x $150-$400 = $15,600-
    $41,600 for the low end, and 2 hours x 5 days x $150-$400 = $1,500-$4,000"
    is two sums, and reading everything after the first "=" as one result swept
    the second sum's inputs into the first sum's answer. Each is now split out
    and checked on its own.
    """
    parts = RESULT_SPLIT.split(sentence or "")
    if len(parts) < 2:
        return []
    out, working = [], parts[0]
    for part in parts[1:]:
        # A comma inside a figure is a thousands separator, not a clause break.
        # Splitting on it turned "$15,600-$41,600" into a bare "$15".
        head, _, tail = _partition_clause(part)
        out.append((working, head))
        working = tail
    return out


CLAUSE_BREAK = re.compile(r",(?!\d)|;")


def _partition_clause(text: str) -> tuple[str, str, str]:
    match = CLAUSE_BREAK.search(text)
    if not match:
        return text, "", ""
    return text[:match.start()], match.group(0), text[match.end():]


FAILURE_GUIDANCE: tuple[tuple[str, str], ...] = (
    ("unmapped sentence",
     "this sentence has no CLAIM_ID. Either give it a type it can satisfy — "
     "about_us if it is about us, inference if it reasons from a claim you can "
     "name, assumption if it is a figure you are supplying — or cut it."),
    ("number with no source",
     "a figure with nothing behind it. Cite the CLAIM_ID it comes from, or "
     "restate it as an assumption with a range and a conditional."),
    ("inference with nothing to reason from",
     "an inference must cite the CLAIM_ID it reasons FROM. Name the fact, or "
     "cut the sentence."),
    ("inference that does not show it is reasoning",
     "say plainly that you are reasoning: 'that suggests', 'which tells me', "
     "'our hypothesis is'. Without it this reads as their own record."),
    ("inference states a figure",
     "an inference may quote a number from the claim it cites, never introduce "
     "one. Use a range, or drop the figure."),
    ("assumption states a point figure",
     "make it a range. Not 'about $30,000 a year' but 'somewhere between "
     "$25,000 and $40,000 a year'."),
    ("assumption with nothing conditional",
     "say it is an assumption: 'if', 'assuming', 'suppose', 'somewhere "
     "between'."),
    ("never asks to be corrected",
     "you used assumptions, so add one plain sentence asking them to check the "
     "figures against their own — and type that sentence about_us."),
    ("sentence typed as ours asserts something about them",
     "this describes their business, so it is a fact or an inference, not "
     "about_us. Cite a CLAIM_ID, or move the figure out."),
    ("arithmetic uses figures the artifact never establishes",
     "every input must already appear as a fact or an assumption in this same "
     "draft. State the missing figures first, or drop them from the sum."),
    ("arithmetic resolves to a point",
     "the answer must be a range, because the inputs were."),
    ("allows exactly one hypothesis",
     "an email carries ONE piece of reasoning. Keep the strongest and cut the "
     "rest, or move them to the brief."),
    ("cites claims that do not qualify",
     "that CLAIM_ID is not in the evidence you were given. Use one from the "
     "list."),
    ("uses a person's name that failed",
     "do not name this person. Address the role instead."),
    ("unknown sentence type",
     f"the only types are {', '.join(SENTENCE_TYPES)}."),
)
"""What to do about each way a draft can fail, in the words the drafter reads.

The retry used to be handed the same prompt and no account of what went wrong,
so it was guessing at eight simultaneous rules twice over. These sit beside the
rules they explain, because a rule and its remedy drifting apart is how a
generator ends up being told to do something the gate does not accept."""


def guidance_for(failure: str) -> str:
    """The one-line remedy for a gate failure, or '' if it has none yet."""
    lowered = (failure or "").lower()
    for marker, advice in FAILURE_GUIDANCE:
        if marker.lower() in lowered:
            return advice
    return ""


def has_conditional(sentence: str) -> bool:
    """True when the sentence frames its figure as an assumption."""
    return any(m in (sentence or "").lower() for m in CONDITIONAL_MARKERS)


def reasons_aloud(sentence: str) -> bool:
    """True when the sentence shows the reader that it is reasoning."""
    return any(m in (sentence or "").lower() for m in REASONING_MARKERS)


def invites_correction(text: str) -> bool:
    """True when the artifact asks to be told it is wrong."""
    return any(m in (text or "").lower() for m in CORRECTION_INVITATIONS)


STANDARD = numerals.STANDARD
YEAR = numerals.YEAR
"""Kept as names for readers who learned them here. Both now live with the rest
of the token guards in ``lib/numerals.py``, where the default they exist to
correct has been inverted."""


def point_quantities(sentence: str) -> list[str]:
    """Quantities stated as a single figure rather than as a span.

    An assumption must be a range: "your quoting desk costs $30,000 a year" is a
    claim about them however it is hedged, whereas "somewhere between $25,000
    and $40,000" is visibly ours.

    Which numerals count as quantities at all is decided in ``lib/numerals.py``.
    """
    return numerals.point_quantities(sentence)


def point_numerals(sentence: str) -> list[numerals.Numeral]:
    """The point figures, carrying the reason each was read as a quantity."""
    return numerals.point_numerals(sentence)


def has_quantity(sentence: str) -> bool:
    """True when the sentence asserts a quantity that needs a source."""
    return numerals.has_quantity(sentence)


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


def is_their_own_figure(quantity: str, claim_values: dict[str, str]) -> bool:
    """True when this figure appears in ANY claim the artifact may cite.

    A weaker test than `traces_to`, deliberately, and used for one thing: the
    point-figure rule on an assumption.

    That rule exists because an unhedged point figure of OURS is an assertion
    wearing a hedge. Whether a figure is ours is decided by whether it appears
    in the company's own evidence — not by which sentence a generator happened
    to attach a citation to. Fifteen of the first twenty-four fragment letters
    were refused for "stating a precise figure" that was, every time, the
    company's own award off its own government record, written in the sentence
    that costs it.

    The looser matching is the price, and it is the same price the analyst's
    `traceable_figures` already pays: a union with no sense of context, which
    catches invented numbers and not numbers used in the wrong place. What still
    binds separately is the citation rule — a factual sentence that maps to
    nothing is refused whatever numbers are in it.
    """
    wanted = numbers_in(quantity)
    if not wanted:
        return True
    return any(wanted & numbers_in(str(value)) for value in claim_values.values())


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
    missing: set[float] = set()
    for working, _result in calculations(sentence):
        missing |= {
            value for value in numbers_in(working)
            if _is_substantive(value) and not _establishes(value, supported)
        }
    return [f"{v:g}" for v in sorted(missing)]


def unranged_results(sentence: str) -> list[str]:
    """Calculations in this sentence whose answer is a point, not a range."""
    return [
        result.strip() for _working, result in calculations(sentence)
        if point_quantities(result)
    ]


def _is_substantive(value: float) -> bool:
    """Ignore the small integers that are grammar rather than quantity.

    A bare "1" in "finding 1", a "12" for months, a "52" for weeks: these are
    scaffolding a reader supplies for themselves. The line is drawn low enough
    to catch a real input and high enough to skip ordinals and calendar units.
    """
    return value not in (0, 1, 12, 52, 100) and abs(value) >= 0.01


def result_of(sentence: str) -> str:
    """Whatever the calculation claims to produce."""
    parts = RESULT_SPLIT.split(sentence or "", maxsplit=1)
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
    if numerals.has_quantity(text):
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
