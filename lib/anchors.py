"""What the arithmetic in an analysis is sized to, in a strict order.

THE PROBLEM THIS FIXES, STATED AS A NUMBER

Forty-four Canadian analyses had been written and they carried six distinct
headline figures between them. Twenty-five of them quoted the same one:
$16,653-$67,445 a year. Nothing was wrong with the arithmetic — every input was
labelled, every range was honest — and the documents were still worthless,
because twenty-five companies with no headcount on file got the same default
volume band and therefore the same answer.

A number that twenty-five companies share is not about any of them. An operator
who reads two of these documents learns that we have a spreadsheet.

THE ORDER, AND WHY IT IS AN ORDER RATHER THAN A PREFERENCE

1. **A volume they stated.** "We quote about 200 jobs a month" is their number
   about their own desk. Nothing else comes close, and it is checkable by the
   person we are writing to.
2. **A headcount, scaled.** Still our assumption, but anchored to a size fact
   about THIS company. "Scaled to the sixty-two people we hold for you" is a
   sentence a prospect can correct; "somewhere between forty and a hundred and
   fifty quotes a month" is not, because they have no idea where it came from.
3. **Their award, as capital.** A DIFFERENT MODEL, not a third guess at volume.
   This is the distinction the whole module turns on and it was refused once
   already, correctly: an award size is not a proxy for headcount, and a
   quarter-million-dollar grant says nothing whatever about how many people
   work there or how many quotes they send. What it does say — and says at
   Tier 1, from a government record — is how much capital this company has
   committed. So the arithmetic changes shape: instead of counting hours it
   costs the annual charge on capital they already deployed and asks what share
   of it is idle. That is a real question with a real answer, and it is not the
   headcount question wearing a hat.
4. **Nothing.** Then the document says so. The headline renders "pending one
   number from you", the ranges stay in the body labelled as hypothetical, and
   the fragment letter opens on the missing number instead of pretending to
   have it. An analysis with no anchor is not a failure; it is a first call with
   one question in it.

WHAT "RECORDED PER ANALYSIS" MEANS

The anchor is stored on the artifact. Six months from now, "why does this
document say $40,000" has one answer that can be read off the row rather than
reconstructed from the evidence file as it stands today.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from lib import peers
from lib.integrity import is_usable, iter_all_claims

STATED = "stated"
HEADCOUNT = "headcount"
AWARD = "award"
NONE = "none"
ORDER: tuple[str, ...] = (STATED, HEADCOUNT, AWARD, NONE)
"""The four anchors, strongest first. The order is the rule."""

LABOUR_HOURS = "labour_hours"
CAPITAL_UTILISATION = "capital_utilisation"
"""The two model types. An anchor decides which one the arithmetic is.

Named here rather than in `lib/offermodels.py` because the choice is made here:
the module that decides what a document is anchored to is the module that
decides what shape its arithmetic takes."""

PENDING_HEADLINE = "pending one number from you"
"""What the summary says when nothing anchors the arithmetic.

Not "unknown", not a range, and not silence. It is a headline that states the
shape of the next conversation, and the fragment letter uses the same words so
that the document and the letter are asking for the same thing."""

PER_MONTH: dict[str, float] = {
    "day": 21.0, "shift": 21.0, "week": 4.33, "month": 1.0, "quarter": 1 / 3,
    "year": 1 / 12, "annum": 1 / 12,
}
"""How a stated rate converts to a monthly one.

Twenty-one days rather than thirty because a rate stated per day by a
manufacturer is a working day. The conversion is OURS and the analysis says so:
a volume stated per week is reported as "about 220 a month, from the fifty a
week they publish", never as though they had said the monthly figure."""

PERIOD = "|".join(sorted(PER_MONTH, key=len, reverse=True))

RATE = re.compile(
    r"(?:about|around|approximately|roughly|over|more than|some|up to)?\s*"
    r"(\d[\d,]*)(?:\s*(?:-|–|to|and)\s*(\d[\d,]*))?\s*"
    r"(?:\+\s*)?([a-z][a-z\- ]{2,28}?)\s*"
    r"(?:a|per|each|every)\s+(" + PERIOD + r")\b",
    re.IGNORECASE,
)
"""'200 quotes a month', '40-60 orders per week', 'between 40 and 60 quotes a month'.

'and' joins a range only where a figure follows it immediately, so "3 quotes and
60 orders a month" still reads as sixty orders rather than as a range of three
to sixty — the noun between the two figures breaks the alternation.

The noun is captured loosely and checked against the work unit's own nouns
afterwards, because the alternative — a noun alternation inside the pattern —
would match 'about 200 dollars a month' as a volume of dollars."""

MAX_STATED = 500_000
"""A ceiling on a stated volume, guarding against a money figure read as a count.

The unit-noun check already refuses '$40,000 a month'. This catches the case
where a page writes a count and a currency in the same phrase."""


class Anchor(NamedTuple):
    """What one company's arithmetic is sized to, and how to say so."""

    kind: str
    model_type: str
    volume: tuple[float, float] | None = None
    """The monthly volume band, when there is one. None for award and none."""

    words: str = ""
    """How the anchor is described in the assumption that carries it."""

    claim_path: str | None = None
    """The evidence path behind a stated volume, for the input's provenance."""

    headcount: int | None = None
    capital: float | None = None
    capital_path: str | None = None
    detail: str = ""
    """One line for the operator and for the stored record."""

    @property
    def anchored(self) -> bool:
        """Whether anything about this company sized the arithmetic."""
        return self.kind != NONE

    @property
    def headline_available(self) -> bool:
        """Whether the summary may quote a figure as this company's headline.

        Same question as `anchored`, asked in the words the renderer uses. An
        unanchored document still contains ranges — they are our hypothesis
        about a company of this shape — and what it may not do is put one at the
        top as though it were a finding.
        """
        return self.anchored

    def as_record(self) -> dict[str, Any]:
        """The anchor as it is stored on the artifact."""
        return {
            "kind": self.kind,
            "model_type": self.model_type,
            "volume": list(self.volume) if self.volume else None,
            "claim_path": self.claim_path,
            "headcount": self.headcount,
            "capital": self.capital,
            "capital_path": self.capital_path,
            "detail": self.detail,
        }


def _number(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _nouns_for(unit: Any) -> set[str]:
    """The words that would name the thing this model counts.

    Taken from the work unit rather than listed here, so a new ROI pattern
    brings its own vocabulary with it instead of needing a second edit in a
    module that does not know what it counts.
    """
    words: set[str] = set()
    for source in (getattr(unit, "unit", ""), getattr(unit, "unit_plural", ""),
                   *getattr(unit, "volume_nouns", ())):
        for token in re.findall(r"[a-z]{3,}", str(source).lower()):
            words.add(token)
            words.add(token.rstrip("s"))
    return words


def stated_volume(
    prospect: dict[str, Any], unit: Any
) -> tuple[tuple[float, float], str, str] | None:
    """A monthly volume this company published, or None.

    Read only from claims that are usable — not killed, not tainted, not refused
    by the adversarial checker. A volume nobody may quote is not an anchor; it
    is a number we would have to explain away on the call.
    """
    nouns = _nouns_for(unit)
    if not nouns:
        return None
    for path, claim in iter_all_claims(prospect.get("evidence_file") or {}):
        if not is_usable(claim):
            continue
        text = str(claim.get("value") or "")
        for match in RATE.finditer(text):
            low = _number(match.group(1))
            high = _number(match.group(2)) or low
            noun = match.group(3).strip().lower()
            period = match.group(4).lower()
            if low is None or high is None or low <= 0 or high > MAX_STATED:
                continue
            tokens = set(re.findall(r"[a-z]{3,}", noun))
            if not tokens & nouns:
                continue
            factor = PER_MONTH[period]
            band = (min(low, high) * factor, max(low, high) * factor)
            said = match.group(0).strip()
            words = (f"{said}, as they state it"
                     if period == "month"
                     else f"{said}, which is {band[0]:,.0f} to {band[1]:,.0f} a month "
                          f"on our conversion")
            return band, words, path.removeprefix("evidence_file.")
    return None


def award_capital(prospect: dict[str, Any]) -> tuple[float, str] | None:
    """The capital this company has committed, from its own award record.

    The column first, because both loaders write it from the source record and
    it is the figure the rest of the pipeline already reconciles against. The
    block 2 claim is the fallback and carries the path, which is what the
    capital model's provenance needs.
    """
    amount = _number(prospect.get("grant_amount"))
    if amount and amount > 0:
        for path, claim in iter_all_claims(prospect.get("evidence_file") or {}):
            trimmed = path.removeprefix("evidence_file.")
            if trimmed.endswith("grant_amount") and is_usable(claim):
                return amount, trimmed
        return amount, "block2_grant_funded.grant_amount"
    for path, claim in iter_all_claims(prospect.get("evidence_file") or {}):
        trimmed = path.removeprefix("evidence_file.")
        if not trimmed.endswith(("grant_amount", "grant_awards_total")):
            continue
        if not is_usable(claim):
            continue
        found = _number(re.sub(r"[^\d.,]", "", str(claim.get("value") or "")))
        if found and found > 0:
            return found, trimmed
    return None


def anchor_for(prospect: dict[str, Any], unit: Any) -> Anchor:
    """What this company's arithmetic is sized to, taking the first that holds.

    Strict order. A later anchor is never consulted while an earlier one is
    available, and the reason is not tidiness: consulting two and picking the
    nicer answer is how a document ends up sized to whichever assumption
    flattered it.
    """
    stated = stated_volume(prospect, unit)
    if stated is not None:
        band, words, path = stated
        return Anchor(
            kind=STATED, model_type=LABOUR_HOURS, volume=band, words=words,
            claim_path=path,
            detail=f"anchored on a volume they published: {words}")

    size = peers.size_of(prospect)
    if size.headcount:
        base = tuple(getattr(unit, "volume", (40, 150)))
        from lib.offermodels import volume_for

        band, scaled = volume_for(base, size.headcount)
        return Anchor(
            kind=HEADCOUNT, model_type=LABOUR_HOURS, volume=band,
            words=f"{band[0]:,.0f} to {band[1]:,.0f} "
                  f"{getattr(unit, 'unit_plural', 'units')} a month{scaled}",
            headcount=size.headcount,
            detail=f"anchored on {size.basis}: {size.headcount:,} people")

    capital = award_capital(prospect)
    if capital is not None:
        from lib.offermodels import award_floor

        amount, path = capital
        if amount < award_floor():
            # The capital model runs on this and always answers no. Below the
            # floor the annual charge on the award is smaller than the cheapest
            # build we sell, so every figure it produced would be a number whose
            # only honest reading is "not worth doing" — and a document is worse
            # for carrying one of those than for saying it has no anchor.
            return Anchor(
                kind=NONE, model_type=LABOUR_HOURS,
                words="nothing this company has published sizes the work",
                capital=amount, capital_path=path,
                detail=f"the ${amount:,.0f} award is below the "
                       f"${award_floor():,.0f} at which a capital model says "
                       f"anything but no, so the arithmetic has no anchor")
        return Anchor(
            kind=AWARD, model_type=CAPITAL_UTILISATION, capital=amount,
            capital_path=path,
            words=f"${amount:,.0f} of capital they have committed, from their own "
                  f"award record",
            detail=f"no volume and no headcount, so the arithmetic is about the "
                   f"${amount:,.0f} of capital their award records rather than "
                   f"about hours")

    return Anchor(
        kind=NONE, model_type=LABOUR_HOURS,
        words="nothing this company has published sizes the work",
        detail="no stated volume, no headcount and no award amount; the summary "
               "asks for one number and the ranges stay hypothetical")


def headline_words(anchor: Anchor, figure: str) -> str:
    """The summary headline: their figure, or the sentence that asks for one."""
    return figure if anchor.headline_available else PENDING_HEADLINE
