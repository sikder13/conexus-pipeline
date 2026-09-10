"""What a numeral in employee context IS, and what it is deliberately not.

WHY THIS IS ITS OWN MODULE

Three places need to read a headcount out of a sentence — the node that reads a
company's own pages, the node that reads a programme announcement, and the
console panel where an operator types one in — and a fourth place needs to trust
the answer, because `lib/anchors.py` uses a headcount to size every volume band
in an analysis. Four readers of one rule means the rule is written once.

`lib/numerals.py` already answers the general question: is this numeral a
quantity or a token? This module answers the narrower one that sits on top of
it: is this quantity a count of the people who work here? That is a different
question with a different failure mode, and the failure mode is the reason for
every exclusion below.

THE THREE THINGS THAT LOOK LIKE A HEADCOUNT AND ARE NOT

* **Job creation.** "The project will create 15 jobs" is a forecast about hiring
  somebody made to a funder. Recording it as a headcount would say a 6-person
  shop employs 15 people, sourced to a government page, and the sentence would
  survive an adversarial check because the page really does say 15.
* **Job postings.** Counting open roles gives a number of people the company
  does NOT yet have. It is a real signal — a company advertising nine roles is
  telling you something — so it is kept, as `HIRING_ACTIVITY`, and it may never
  be read as headcount.
* **Everything else a manufacturer counts.** Machines, square feet, customers,
  years in business, parts a day. These are excluded by requiring an employee
  noun rather than by listing what to avoid, because the list of things a
  factory counts has no end and the list of words meaning "the people here" does.

WHAT A READING IS WORTH

A reading carries the phrase it came from, so a claim can quote the sentence
rather than a bare integer. "About 45 employees across two shifts" is worth more
to an operator on a call than `45`, and it is what makes the claim checkable by
somebody who opens the page.

Ranges are read as ranges. "50 to 100 employees" is a band the company chose to
publish, and collapsing it to either end invents a precision they withheld.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from lib import numerals

HEADCOUNT = "headcount"
JOB_CREATION = "job_creation"
HIRING_ACTIVITY = "hiring_activity"
KINDS: tuple[str, ...] = (HEADCOUNT, JOB_CREATION, HIRING_ACTIVITY)
"""What a count of people can be about. Only the first is a headcount."""

PLAUSIBLE = (1, 500_000)
"""The range a stated headcount may fall in.

The floor is one because a sole proprietor is a real answer. The ceiling is not
a judgement about how big a company can be — it is a guard against reading a
revenue figure or a part count that happens to sit next to the word 'people'."""

PEOPLE_NOUNS: tuple[str, ...] = (
    "employees", "employee", "people", "persons", "staff", "team members",
    "team member", "workers", "worker", "workforce", "personnel", "headcount",
    "full-time employees", "full time employees", "fte", "ftes",
    "full-time equivalents", "associates", "associate", "colleagues",
    "tradespeople", "technicians", "machinists", "operators", "engineers",
    "estimators", "estimator", "welders", "fabricators", "assemblers",
    "supervisors", "apprentices", "crew",
)
"""Nouns that name the people who work at a company.

Craft nouns are in here because a small shop describes itself that way — "twelve
machinists and two estimators" is a headcount sentence — and because reading it
as one is the difference between sizing the analysis to this company and sizing
it to the reference band. They are also why `SUM_CRAFT` exists below."""

_NOUNS = "|".join(sorted((re.escape(n) for n in PEOPLE_NOUNS), key=len, reverse=True))

_NUM = r"(\d[\d,]*)"
_RANGE = rf"{_NUM}(?:\s*(?:-|–|—|to)\s*{_NUM})?"

COUNT_THEN_NOUN = re.compile(
    rf"\b(?:about|around|approximately|roughly|nearly|over|more than|some|"
    rf"upwards of|just under)?\s*{_RANGE}\s*\+?\s*(?:{_NOUNS})\b",
    re.IGNORECASE,
)
"""'45 employees', 'about 50 to 100 people', '120+ staff'."""

NOUN_THEN_COUNT = re.compile(
    rf"\b(?:employs|employing|employ|staffed by|a (?:team|staff|workforce|crew) of|"
    rf"our (?:team|staff|workforce) of|team of|staff of|workforce of|"
    rf"headcount of|grown to|now numbers)\s*"
    rf"(?:about|around|approximately|roughly|nearly|over|more than|some)?\s*{_RANGE}\b",
    re.IGNORECASE,
)
"""'employs 45', 'a team of 12', 'workforce of about 200'."""

PERSON_COMPOUND = re.compile(
    rf"\b{_RANGE}\s*-?\s*(?:person|employee|people|staff)\s*"
    rf"(?:team|shop|company|operation|plant|facility|business|firm|crew)\b",
    re.IGNORECASE,
)
"""'a 40-person shop', '12 person team'."""

JOB_CREATION_PATTERN = re.compile(
    rf"\b(?:create|creating|creation of|add|adding|hire|hiring|recruit|recruiting|"
    rf"support|supporting|retain|retaining|bring|generate|generating)\s+"
    rf"(?:up to|as many as|about|around|approximately|an additional|another)?\s*"
    rf"{_RANGE}\s*\+?\s*(?:new\s+|additional\s+|full-time\s+|permanent\s+)*"
    rf"(?:jobs?|positions?|roles?|{_NOUNS})\b",
    re.IGNORECASE,
)
"""A promise about hiring, not a statement about staffing.

Matched FIRST and used to suppress, because 'create 15 jobs' also contains
'15 jobs' and the generic reader would take it. This is the single most likely
way a government funding page would put a wrong headcount into an evidence
file, and it is the reason this module exists rather than a regex at a call
site."""

FUTURE_TENSE = re.compile(
    r"\b(?:will|would|expects? to|expected to|plans? to|planning to|aims? to|"
    r"intends? to|projected to|forecast to|plans for|by 20\d\d)\b",
    re.IGNORECASE,
)
"""A clause about the future is not a statement about now.

Applied to the whole sentence rather than to the span, because 'the expansion
will take the plant to 120 employees' reads as a headcount inside its span and
is a plan in its sentence."""

NEGATION = re.compile(
    r"\b(?:no|not|never|fewer than|less than|down from|laid off|redundanc)\b",
    re.IGNORECASE,
)
"""Wording that makes the number a bound or a loss rather than a count.

'down from 200 employees' is about a company that no longer has 200 people, and
'fewer than 50 employees' is a ceiling somebody else set. Both are refused
rather than guessed at: a bound recorded as a count is a fabrication with a
citation attached."""

SUM_CRAFT = re.compile(
    rf"\b{_NUM}\s+(?:{_NOUNS})\b(?=\s*(?:,|and)\s)", re.IGNORECASE
)
"""One term of a list like 'twelve machinists, two estimators and a foreman'.

Not summed. Listed terms are returned as separate readings and the caller keeps
the largest, because adding them would produce a total the page never states —
which is our arithmetic wearing their citation."""


class Reading(NamedTuple):
    """One count of people found in a piece of text, and what it is about."""

    low: int
    high: int
    kind: str
    phrase: str
    """The wording it was read from, so a claim can quote the page."""

    rule: str
    """Which rule matched, for the node's note and for a later argument."""

    @property
    def is_range(self) -> bool:
        return self.high > self.low

    @property
    def words(self) -> str:
        """How the count reads in a claim value."""
        if self.is_range:
            return f"{self.low:,} to {self.high:,} people"
        return f"{self.low:,} {'person' if self.low == 1 else 'people'}"

    @property
    def midpoint(self) -> int:
        """The single figure a band is scaled by. Only for a band we were given."""
        return (self.low + self.high) // 2


def _int(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _plausible(low: int, high: int) -> bool:
    return PLAUSIBLE[0] <= low <= high <= PLAUSIBLE[1]


def sentences(text: str) -> list[str]:
    """The text split into the units a reading is judged inside.

    Judged per sentence because every suppression rule here is about context —
    a future tense, a negation, a promise to a funder — and a rule applied to a
    span rather than to the sentence around it cannot see any of them.
    """
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+", cleaned) if s.strip()]


IDENTIFIER_REASONS: frozenset[str] = frozenset(name for name, _p in numerals.GUARDS)
"""The reasons `lib/numerals.py` gives for a numeral that NAMES something.

This module borrows the identifier half of that module and not the other half,
and the distinction is worth stating because it looks like a shortcut and is
not. `numerals.quantities` also refuses a small count written in passing — the
scaffolding floor — because its question is "does this figure in OUR prose need
a source?", and a count under a hundred usually does not. The question here is
"is this numeral counting people?", and forty-five is the most common true
answer there is. Reusing the wrong half refused every small shop in the set,
which is exactly the population this pipeline sells to."""


def _is_a_count_not_an_identifier(sentence: str, span: tuple[int, int]) -> bool:
    """Whether `lib/numerals.py` reads this numeral as something other than a name.

    A model designation, a phone number, a postal code or a calendar year that
    happened to sit beside the word 'staff' is refused there, once, by the rules
    that already know what each of those looks like.
    """
    for numeral in numerals.classify(sentence):
        overlaps = (numeral.start <= span[0] < numeral.end
                    or span[0] <= numeral.start < span[1])
        if overlaps:
            return numeral.reason not in IDENTIFIER_REASONS
    return False


def _reading(match: re.Match[str], sentence: str, kind: str, rule: str) -> Reading | None:
    low = _int(match.group(1))
    high = _int(match.group(2)) if match.lastindex and match.lastindex >= 2 else None
    if low is None:
        return None
    high = high if high is not None else low
    if high < low:
        low, high = high, low
    if not _plausible(low, high):
        return None
    if not _is_a_count_not_an_identifier(sentence, match.span(1)):
        return None
    return Reading(low, high, kind, match.group(0).strip(), rule)


def read(text: str) -> list[Reading]:
    """Every count of people in this text, each labelled with what it counts.

    Returns readings of all three kinds. The caller decides which it wants;
    separating them here rather than filtering at the call site is what stops a
    job-creation figure being recorded as a headcount by a node that forgot to
    ask.
    """
    found: list[Reading] = []
    for sentence in sentences(text):
        creation_spans = [m.span() for m in JOB_CREATION_PATTERN.finditer(sentence)]
        for match in JOB_CREATION_PATTERN.finditer(sentence):
            reading = _reading(match, sentence, JOB_CREATION, "job creation")
            if reading:
                found.append(reading)
        if FUTURE_TENSE.search(sentence) or NEGATION.search(sentence):
            # The sentence is about a plan, a loss or a bound. Any count in it is
            # one of those things, and a count of that kind recorded as a
            # headcount is wrong in the direction that reaches a prospect.
            continue
        for pattern, rule in (
            (NOUN_THEN_COUNT, "employs N"),
            (PERSON_COMPOUND, "N-person"),
            (COUNT_THEN_NOUN, "N people"),
            (SUM_CRAFT, "listed trades"),
        ):
            for match in pattern.finditer(sentence):
                if any(start <= match.start() < end for start, end in creation_spans):
                    continue
                reading = _reading(match, sentence, HEADCOUNT, rule)
                if reading and not _duplicate(found, reading):
                    found.append(reading)
    return found


def _duplicate(found: list[Reading], candidate: Reading) -> bool:
    """Whether this reading is one already held, matched on span rather than text.

    Two patterns legitimately match the same words — 'a team of 40' is read by
    both `NOUN_THEN_COUNT` and `COUNT_THEN_NOUN` — and recording it twice would
    make one sentence look like corroboration of itself.
    """
    return any(r.kind == candidate.kind and r.low == candidate.low
               and r.high == candidate.high for r in found)


def headcounts(text: str) -> list[Reading]:
    """Only the readings that state how many people work there."""
    return [r for r in read(text) if r.kind == HEADCOUNT]


def best(text: str) -> Reading | None:
    """The one headcount reading to record, or None.

    The largest, and that is a decision worth stating. A page that says "twelve
    machinists" in one sentence and "forty people" in another is describing a
    part and a whole, and the whole is the company. Taking the smaller figure
    would size every downstream band to one department.
    """
    readings = headcounts(text)
    if not readings:
        return None
    return max(readings, key=lambda r: (r.high, r.low))


def job_creation(text: str) -> list[Reading]:
    """Counts of jobs somebody promised to create. Never a headcount."""
    return [r for r in read(text) if r.kind == JOB_CREATION]


def hiring_activity(open_roles: int, source: str = "") -> Reading | None:
    """A count of open postings, recorded as what it is.

    A company advertising nine roles is telling you something real about its
    year, and it is not telling you how many people it has. This returns the
    signal with the label that keeps the two apart for good.
    """
    if open_roles <= 0:
        return None
    return Reading(
        open_roles, open_roles, HIRING_ACTIVITY,
        f"{open_roles} open role{'' if open_roles == 1 else 's'}"
        + (f" on {source}" if source else ""),
        "open postings",
    )


def claim_value(reading: Reading) -> str:
    """How a headcount reading is written into a claim.

    The phrase, not the integer. An operator reading "about 45 employees across
    two shifts" on a call can check it against what they are told; an operator
    reading "45" cannot tell whether we counted or they said so.
    """
    if reading.kind != HEADCOUNT:
        return reading.phrase
    if reading.phrase.strip().lower().startswith(str(reading.low)):
        return reading.phrase
    return f"{reading.phrase} ({reading.words})"
