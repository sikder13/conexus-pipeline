"""What a numeral in a sentence actually is — a quantity, or just a token.

WHY THIS MODULE EXISTS

Three times now the gate has refused a true sentence for containing digits that
measure nothing:

* ``ISO 13485`` — a standard's designation, read as a figure of thirteen
  thousand four hundred and eighty-five (fixed 2026-09-06 by an exclusion);
* ``dates to 2019`` and ``73 of 176 comparable peers`` — a calendar year and a
  count quoted from our own table (fixed 2026-09-06 by two more exclusions);
* ``including the (678) number`` — a telephone area code, which blocked
  Trifecta Medical's analysis on 2026-09-09.

Each fix was an exclusion bolted onto a rule that said *every* run of three or
more digits asserts a quantity. That rule is wrong in its default, and a fourth
exclusion would only postpone the fifth. Postal codes, street numbers, citation
indices, model numbers, docket numbers and part numbers are all sitting in the
same trap.

So the default is inverted here, once, in the one module every gate reads from.

THE RULE

**A numeral is a QUANTITY only when it carries quantity context.** Absent that
context it is a TOKEN: a name written in digits, which measures nothing, asserts
nothing, and needs no source.

Quantity context is one of:

``money``       an adjacent currency symbol or word, or a money noun in front of
                it — ``$30,000``, ``80 dollars``, ``an award of 71,912``.
``proportion``  an adjacent ``%`` or the word *percent*.
``unit``        a unit noun straight after it, naming something that is being
                measured or counted: hours, employees, SKUs, orders, defects,
                sq ft, machines.
``rate``        a rate phrase straight after it: *annually*, *per month*,
                *a week*, ``/hr``.
``notation``    written the way quantities are written and identifiers are not:
                a thousands separator (``102,000``) or a decimal fraction
                (``0.30``). No phone number, postal code, standard or year is
                written with either.
``calculation`` the numeral is an operand — it sits beside an arithmetic
                operator, or the caller declared it as an input to a calculation
                the sentence map carries.

Nothing else. A numeral with none of these is a token and passes.

TOKEN GUARDS

Named identifier shapes — telephone numbers, postal codes, standards
designations, years used as dates, and citation paths — are recognised
explicitly as well. Strictly they are redundant, because none of them carries
quantity context; they are here so the module states in one place what it is
protecting, so each has a test with the live sentence that motivated it, and so
that a unit noun landing next to one by accident cannot drag it back in.

A guard does **not** beat an attached currency symbol or percent sign.
``$2,019 a year`` is money whatever it looks like, which is the case that keeps
the year guard honest.

THE SCAFFOLDING FLOOR

One rule survives the inversion unchanged, because it was never about
classification: a small integer whose only context is a unit or a rate is
scaffolding a reader supplies for themselves. ``2 shifts``, ``40 hours``,
``5 machines`` — these are how people write, not claims anybody needs to source,
and gating them blocked drafts over ordinals once already. The old pattern drew
this line at three digits and it is kept exactly there.

The floor applies **only** to that branch. Money, proportions, decimals,
thousands-separated figures and calculation operands are quantities at any
magnitude, because ``$30 an hour`` and ``0.30`` are precisely the small figures
that launder into a conclusion.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

QUANTITY = "quantity"
TOKEN = "token"

SCAFFOLDING_FLOOR = 100.0
"""Below this, an integer carrying only a unit or a rate is reader-supplied.

Copied from the pattern this module replaces, which asked for three digits or
more. Kept identical so the inversion changes which numerals are examined and
not which counts are considered claims — one change at a time."""


NUMERAL = re.compile(r"(?<![\d.,])\d+(?:,\d{3})+(?:\.\d+)?|(?<![\d.,])\d+\.\d+|(?<![\d.,])\d+")
"""One run of digits, with its thousands separators and decimal part attached.

Ordered longest-first so ``102,000`` matches whole rather than as ``102`` and
``000``, and guarded on the left so the ``000`` of a figure already matched is
never picked up as a numeral of its own."""


RANGE_SPAN = re.compile(
    r"\$?\s?\d[\d,]*(?:\.\d+)?\s?%?"
    r"\s*(?:–|—|-|\bto\b|\band\b)\s*"
    r"\$?\s?\d[\d,]*(?:\.\d+)?\s?%?",
    re.IGNORECASE,
)
"""Two numbers joined into a span: '$80-$120', '25 to 40', 'between 8 and 12'.

The percent signs are load-bearing and were once missing. "somewhere between
10% and 20% a year" is a range by any reading, but with the sign unmatched the
span ended at the first digit and both figures came back as point estimates —
so a sentence doing exactly what the formula asks for was refused for doing it.
"between 20 and 40 percent" passed the whole time, which is why it survived."""


# ------------------------------------------------------------------ context

CURRENCY_SYMBOL = re.compile(
    r"(?:(?:C|CA|US|A|NZ)?[$£€¥]|\b(?:USD|CAD|CDN|AUD|EUR|GBP))\s?$", re.IGNORECASE
)
"""A currency symbol or code sitting immediately in front of the numeral.

The three-letter codes are here so that 'CAD1200' is money rather than a part
number: the designation guard below reads letters glued to digits as a name, and
without this a Canadian figure written the compact way would be swallowed."""

CURRENCY_WORD = re.compile(
    r"^\s?(?:dollars?|usd|cad|cdn|aud|eur|gbp|cents?|euros?|k\b|m\b|million|billion)\b",
    re.IGNORECASE,
)
"""A currency word immediately after it — '80 dollars', '250k'."""

MONEY_NOUN = re.compile(
    r"\b(?:award(?:ed)?|grant(?:ed)?|revenue|turnover|budget(?:ed)?|cost(?:s|ing)?|"
    r"price[ds]?|spend(?:ing)?|payroll|saving[s]?|fee[s]?|salar(?:y|ies)|wage[s]?|"
    r"margin[s]?|capital|funding|contract|invoice[ds]?|bill(?:ed)?|"
    r"payment|investment|worth|paid)\s+"
    r"(?:of|at|totall?ing|worth|around|about|roughly|near)\s+$",
    re.IGNORECASE,
)
"""A money noun introducing a bare figure — 'an award of 71,912'.

Two things keep this tight, and both were learned from a false positive.

Position is the first: a money noun leads its figure, a unit noun follows it.
That matters for words which are both, and 'quote' is the one that proves it —
'a quote of 12,000' is money, '40 quotes a month' is a count.

The link word is the second, and it must sit immediately between the noun and
the figure. An earlier version allowed a few words in between, so 'the equipment
funded by the 2020 grant' read 'funded' as money context, which outranked the
year guard and refused a question about when a grant was awarded. A money noun
that is not introducing an amount is just a word."""

PROPORTION = re.compile(
    r"^\s?(?:%|(?:percent|per cent|percentage points?|basis points?)\b)", re.IGNORECASE
)
"""A percent sign or word immediately after the numeral."""

UNIT_NOUNS: tuple[str, ...] = (
    # effort and capacity
    "hour", "hr", "man-hour", "manhour", "labour-hour", "labor-hour", "minute",
    "min", "shift", "fte", "employee", "person", "people", "staff", "worker",
    "headcount", "operator", "technician", "engineer", "machinist", "welder",
    "programmer", "estimator", "seat", "licence", "license", "machine", "press",
    "cell", "station", "truck", "trailer", "bay", "spindle", "line", "workstation",
    # volume of work
    "order", "quote", "quotation", "rfq", "po", "invoice", "job", "ticket",
    "part", "unit", "piece", "sku", "item", "batch", "run", "cycle", "changeover",
    "setup", "drawing", "revision", "record", "row", "page", "email", "call",
    "enquiry", "inquiry", "lead", "shipment", "pallet", "case", "load", "delivery",
    "transaction", "entry", "form", "report", "document", "file",
    # quality
    "defect", "reject", "scrap", "rework", "nonconformance", "non-conformance",
    "return", "complaint", "recall", "deviation",
    # physical capacity
    "ton", "tonne", "lb", "lbs", "kg", "sq ft", "sqft", "square foot",
    "square feet", "square metre", "square meter", "sq m", "acre", "gallon",
    "litre", "liter", "pallet position",
)
"""Nouns that name the thing a numeral is counting or measuring.

Calendar words are deliberately absent. 'within 12 months' states a horizon, not
a quantity; a duration becomes a measurement when money or a rate is attached to
it, and both of those are detected on their own."""

_UNIT_ALTERNATION = "|".join(
    sorted((re.escape(u) for u in UNIT_NOUNS), key=len, reverse=True)
)
UNIT = re.compile(rf"^[\s-]?(?:{_UNIT_ALTERNATION})(?:s|es)?\b", re.IGNORECASE)

RATE_PHRASE = re.compile(
    r"^[\s\w-]{0,14}?\s?(?:"
    r"annually|per annum|yearly|monthly|weekly|daily|hourly|"
    r"per\s+(?:year|month|week|day|hour|unit|part|job|order|shift|head|employee|fte)|"
    r"a\s+(?:year|month|week|day|shift)\b|an\s+hour\b|"
    r"each\s+(?:year|month|week|day)|every\s+(?:year|month|week|day)|"
    r"/\s?(?:hr|hour|yr|year|mo|month|wk|week|day)"
    r")\b",
    re.IGNORECASE,
)
"""A rate phrase following the numeral, possibly across its unit noun.

'40 hours a week' and '$4,000 per month' are both rates; the short window is
what lets the unit sit between the figure and the phrase without letting the
next clause in."""

OPERATOR_BEFORE = re.compile(
    r"(?:=|×|÷|\*|/|\bx\b|\bplus\b|\bminus\b|multiplied by|times|divided by|"
    r"gives|comes to|works out to|adds up to)\s*\$?\s?$",
    re.IGNORECASE,
)
OPERATOR_AFTER = re.compile(
    r"^\s?%?\s*(?:=|×|÷|\*|/|\bx\b|\bplus\b|\bminus\b|multiplied by|times|"
    r"divided by|gives|comes to|works out to|adds up to)\b",
    re.IGNORECASE,
)
"""An arithmetic operator on one side of the numeral, making it an operand.

An operand is a quantity however small, because the laundering attack this whole
gate exists to stop is exactly a small invented factor dropped into a sum."""


# ------------------------------------------------------------- token guards

PHONE = re.compile(
    r"\(\s?\d{3}\s?\)|"                                  # (678)
    r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)|"
    r"(?<!\d)\d{3}[.-]\d{4}(?!\d)",
    re.IGNORECASE,
)
"""A telephone number, or the bracketed area code on its own.

The bare '(678)' is the shape that blocked Trifecta Medical: a question asking
whether five listed numbers route to one location named the out-of-state area
code, and the gate read it as an unsourced figure of six hundred and seventy
eight."""

POSTAL = re.compile(
    r"(?<!\d)\d{5}-\d{4}(?!\d)|"                             # 46250-1234
    r"(?:,\s*|\b[A-Z]{2}\s+)\d{5}(?!\d)(?=[\s,.;)]|$)|"      # ..., IN 46250
    r"\b[A-Z]\d[A-Z][ -]?\d[A-Z]\d\b",                       # K1A 0B1
)
"""A US ZIP or a Canadian postal code.

Both are addresses, and both are runs of digits the old rule read as
measurements. The bare US form is required to sit in an address line — after a
state code or a comma — because five digits on their own are also a docket, a
part number and a ticket id. Those are tokens too, but they are tokens for the
ordinary reason, and a guard that claims them would be reporting a shape it
cannot actually see."""

STANDARD = re.compile(
    r"\b(?:ISO|IATF|AS|ANSI|ASTM|SAE|MIL|NIST|NADCAP|AWS|API|FDA|IEC|EN|UL|"
    r"CSA|CGSB|NFPA|OSHA|ITAR|CMMC|SOC)[\s/-]?(?:No\.?\s?)?"
    r"\d{2,5}(?:\.\d{1,3})?(?:[\s:-]\d{4})?[A-Za-z]?\b",
    re.IGNORECASE,
)
"""A quality standard's designation — ISO 13485, IATF 16949, AS9100.

The digits are part of a name and measure nothing, and certifications are one of
the strongest things the evidence holds, so a rule that will not let a document
say which standard a shop works to is aimed at the wrong target.

The dotted and year-suffixed forms are here because they are how the documents
are actually cited — CSA C22.2, ISO 9001:2015 — and a decimal point is otherwise
read as the notation a quantity is written in."""

YEAR = re.compile(r"(?<![\d$])(?:19|20)\d{2}(?![\d%])")
"""A calendar year, which dates something rather than measuring it.

"Their newest visible content dates to 2019" was refused for stating an
unsourced figure of 2019. The year is the whole point of that sentence and it is
read straight off their site."""

CITATION_PATH = re.compile(r"\[[^\]\n]{0,160}\]")
"""A claim reference. 'published_phones[4]' and 'leadership_quotes[12]' carry
digits that index our own evidence file and describe nothing about anybody."""

DESIGNATION = re.compile(r"\b[A-Za-z]{1,12}-?\d+[A-Za-z0-9-]*\b")
"""A model, part or serial designation — letters glued straight onto digits.

'AE2510', 'VF2', 'MK4'. This is the fifth shape of this bug and it was found by
re-gating the very artifact the fourth one blocked: "Turn the AE2510's own data
into a weekly report" passed, but "the AE2510 press" did not, because 'press' is
a unit noun and it was sitting right after the number.

The rule underneath is worth stating, because it generalises: a quantity is
never written glued to letters. '$30,000' carries a symbol and '40 hours' a
space; 'AE2510' is one word, and one word is a name."""

ADDRESS = re.compile(
    r"(?<!\d)\d{1,6}\s+(?:[A-Z][\w.']*\s+){0,3}"
    r"(?:st|street|ave|avenue|rd|road|ln|lane|dr|drive|blvd|boulevard|way|"
    r"ct|court|pl|place|pkwy|parkway|hwy|highway|ste|suite|unit|floor|fl)\b\.?",
    re.IGNORECASE,
)
"""A street number. '6902 Challenge Ln' is where we are, not how much of it."""

GUARDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("phone number", PHONE),
    ("standard designation", STANDARD),
    ("model designation", DESIGNATION),
    ("street address", ADDRESS),
    ("postal code", POSTAL),
    ("citation path", CITATION_PATH),
    ("calendar year", YEAR),
)
"""Named identifier shapes, most specific first, and reported in this order.

The order is not decoration. A designation and a ZIP are both five digits, so
ISO 13485 came back reported as a postal code until the standard was asked
first; a street number and a ZIP sit in the same address line for the same
reason."""


@dataclass(frozen=True)
class Numeral:
    """One run of digits, and what this module decided it was."""

    text: str
    """The figure as written, including an attached currency symbol or percent."""

    value: float | None
    kind: str
    """``quantity`` or ``token``."""

    reason: str
    """Which context made it a quantity, or which guard made it a token."""

    start: int
    end: int

    @property
    def is_quantity(self) -> bool:
        return self.kind == QUANTITY


def _guard_spans(text: str) -> list[tuple[int, int, str]]:
    return [
        (m.start(), m.end(), name)
        for name, pattern in GUARDS
        for m in pattern.finditer(text)
    ]


def _display_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Widen a numeral's span over an attached currency symbol and percent sign.

    So that a refusal quotes ``$30,000`` rather than ``30,000``, which is what
    the reader has to go and find in their own draft.
    """
    left = start
    prefix = text[max(0, start - 4):start]
    symbol = CURRENCY_SYMBOL.search(prefix)
    if symbol:
        left = start - (len(prefix) - symbol.start())
    right = end
    trailing = re.match(r"\s?%", text[end:end + 2])
    if trailing:
        right = end + trailing.end()
    return left, right


def _value_of(raw: str) -> float | None:
    cleaned = re.sub(r"[^\d.]", "", raw.replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


def _context(text: str, start: int, end: int) -> str | None:
    """Which kind of quantity context this numeral carries, if any."""
    before = text[:start]
    after = text[end:]
    tail = before[-40:]

    if CURRENCY_SYMBOL.search(before[-4:]) or CURRENCY_WORD.match(after):
        return "money"
    if PROPORTION.match(after):
        return "proportion"
    if MONEY_NOUN.search(tail):
        return "money"
    if OPERATOR_BEFORE.search(before[-20:]) or OPERATOR_AFTER.match(after):
        return "calculation"
    if UNIT.match(after):
        return "unit"
    if RATE_PHRASE.match(after):
        return "rate"
    return None


UNFLOORED = ("money", "proportion", "calculation", "notation", "declared")
"""Contexts a figure is a quantity under whatever its magnitude.

A small money figure is still money and a small operand is still an operand. The
floor exists for counts written in passing, and only for those."""


def classify(text: str, declared: Iterable[float] | None = None) -> list[Numeral]:
    """Every numeral in ``text``, each labelled quantity or token, with a reason.

    ``declared`` is the set of values the sentence map declares as inputs to a
    calculation. A declared input is a quantity wherever it appears, because the
    artifact has already said it is computing with it.
    """
    body = text or ""
    declared_values = {float(v) for v in (declared or ())}
    guards = _guard_spans(body)
    out: list[Numeral] = []

    for match in NUMERAL.finditer(body):
        start, end = match.span()
        left, right = _display_span(body, start, end)
        written = body[left:right]
        value = _value_of(match.group(0))
        context = _context(body, start, end)

        if context is None and value is not None and value in declared_values:
            context = "declared"
        # Notation is read alongside whatever else the numeral carries, not
        # only when nothing else does. "24.0 engineer-weeks" is a unit AND a
        # decimal, and reading only the unit put it under the scaffolding floor
        # — so a calculation resolving to 24.0 came back looking like a range.
        notation = "," in match.group(0) or "." in match.group(0)
        if context is None and notation:
            context = "notation"

        guard = next(
            (name for gstart, gend, name in guards if gstart <= start and end <= gend),
            None,
        )
        # A guard names an identifier. It cannot outrank a currency symbol or a
        # percent sign, which are written on purpose and mean one thing —
        # '$2,019 a year' is money however much it looks like a date.
        if guard and context not in ("money", "proportion"):
            out.append(Numeral(written, value, TOKEN, guard, left, right))
            continue

        if context is None:
            out.append(Numeral(written, value, TOKEN, "no quantity context", left, right))
            continue

        if (
            context not in UNFLOORED
            and not notation
            and value is not None
            and abs(value) < SCAFFOLDING_FLOOR
        ):
            out.append(
                Numeral(written, value, TOKEN, f"{context} below the scaffolding floor",
                        left, right)
            )
            continue

        out.append(Numeral(written, value, QUANTITY, context, left, right))

    return out


def quantities(text: str, declared: Iterable[float] | None = None) -> list[Numeral]:
    """Only the numerals that assert a quantity."""
    return [n for n in classify(text, declared) if n.is_quantity]


def tokens(text: str, declared: Iterable[float] | None = None) -> list[Numeral]:
    """Only the numerals that name something rather than measuring it."""
    return [n for n in classify(text, declared) if not n.is_quantity]


def has_quantity(text: str, declared: Iterable[float] | None = None) -> bool:
    """True when the text asserts any quantity at all."""
    return bool(quantities(text, declared))


def range_spans(text: str) -> list[tuple[int, int]]:
    """Where the text states a span rather than a point."""
    return [m.span() for m in RANGE_SPAN.finditer(text or "")]


def point_numerals(text: str, declared: Iterable[float] | None = None) -> list[Numeral]:
    """Quantities stated as a single figure rather than as a span.

    An assumption must be a range. A point estimate reads as knowledge — "your
    quoting desk costs $30,000 a year" is a claim about them however it is
    hedged, whereas "somewhere between $25,000 and $40,000" is visibly ours.

    Returned as ``Numeral`` rather than text because a caller sometimes needs
    the reason: a gate checking a sentence that shows its working has already
    verified the operands one by one, and re-reading them here as bare figures
    refuses the exact sentence the formula asks for.
    """
    body = text or ""
    covered = range_spans(body)
    return [
        found
        for found in quantities(body, declared)
        if not any(a <= found.start and found.end <= b for a, b in covered)
    ]


def point_quantities(text: str, declared: Iterable[float] | None = None) -> list[str]:
    """The point figures in ``text``, as they are written."""
    return [found.text.strip() for found in point_numerals(text, declared)]
