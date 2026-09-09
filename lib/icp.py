"""Company size as an ICP boundary — the gate that runs before the arithmetic.

WHAT WENT WRONG

Batesville Tool & Die held P1 at score 4 while its own evidence recorded 1,358
employees. Nothing was broken in the sense of throwing an error: the extractor
excludes a giant when the SOURCE LISTING says so, and that check ran at
extraction and never again. The 1,358 arrived later, from the case study, and by
then the only thing reading size was the `too_big` scoring component — which is
worth minus one point. A company five times outside the ICP lost a point and
came top of the list.

That is the shape of the bug: a ceiling expressed as a penalty is not a ceiling.
A penalty says "this is less interesting"; the ICP says "this is not a customer".
So size now decides eligibility before anything is scored, in the same position
and for the same reason as the evidence-integrity gate — a company we do not
sell to should not be ranked among the companies we do, however good its
evidence is.

WHAT COUNTS AS EVIDENCE

Only a headcount the pipeline recorded as a structured claim, and only at T1 or
T2. Two reasons, both learned the hard way. Aggregator estimates are T3 and
CLAUDE.md rule 6 says they never leave the building, so killing a real prospect
on one would be exactly the inflation the tiers exist to prevent. And scanning
free text for a number near the word "employees" reads square footage: the first
version of this scan returned 750,000 for a farm-equipment platform and 514,000
for a machine shop, both of them floor area.

WHEN TWO CLAIMS DISAGREE, THE LARGER WINS

Batesville's site and its case study do not agree, and a ceiling is the one
place where taking the bigger number is the safe direction: the cost of holding
a real small manufacturer for a human glance is one glance, and the cost of
mailing a 1,358-person contract manufacturer an offer priced for a 60-person
shop is the credibility of everything else in the letter.

THE THREE OUTCOMES

Over 500 is DEAD — recorded, with the evidence, never deleted. 251 to 500 is a
REVIEW: the company is held out of every outreach set until an operator writes
an override, because the boundary is genuinely arguable there and a machine
should not be the one arguing. 250 and under passes, and is banded for offer
routing.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from lib.claims import Tier
from lib.evidence import BLOCK8_FINANCIAL_SCALE, read_claim

CORE_MAX = 120
GROWTH_MAX = 250
REVIEW_MAX = 500
"""The three boundaries. `GROWTH_MAX` is the ICP ceiling and matches
`lib.scoring.EMPLOYEE_CEILING`; `REVIEW_MAX` is where arguable becomes absurd."""

TRUSTED_TIERS: tuple[int, ...] = (int(Tier.T1), int(Tier.T2))
"""Tiers a company may be excluded on. T3 is an aggregator's guess and rule 6
keeps it inside the building; killing a prospect on one would be tier inflation
with a body count."""

SIZE_CLAIMS: tuple[tuple[str, str], ...] = (
    ("employee_count", "a headcount on the company's own site"),
    ("company_size", "a headcount in press or case-study coverage"),
)
"""The structured block-8 claims that hold a headcount, and how each is described.

A closed list on purpose. Anything read out of prose is a number that happened
to sit near the word "employees", and this module has already been shown what
that finds."""

OK, REVIEW, DEAD = "ok", "review", "dead"
CORE, GROWTH = "core", "growth"

_DIGITS = re.compile(r"\d[\d,]*")


def _headcount(value: Any) -> int | None:
    """The first whole number in a stored size value, or None."""
    found = _DIGITS.search(str(value or ""))
    return int(found.group(0).replace(",", "")) if found else None


class SizeEvidence(NamedTuple):
    """One trusted headcount, and where it came from."""

    headcount: int
    tier: int
    source_url: str
    where: str

    @property
    def words(self) -> str:
        return (f"{self.headcount:,} employees — {self.where} "
                f"(T{self.tier}, {self.source_url})")


def size_claims(prospect: dict[str, Any]) -> list[SizeEvidence]:
    """Every trusted headcount recorded for this company, largest first."""
    evidence = prospect.get("evidence_file")
    found: list[SizeEvidence] = []
    for key, words in SIZE_CLAIMS:
        claim = read_claim(evidence, BLOCK8_FINANCIAL_SCALE, key)
        if not claim or claim.get("tier") not in TRUSTED_TIERS:
            continue
        count = _headcount(claim.get("value"))
        if count is not None:
            found.append(SizeEvidence(count, int(claim["tier"]),
                                      str(claim.get("source_url") or ""), words))

    # The column is written by the same nodes that write the claims and carries
    # its tier as a marker in `employee_source`. Read as a fallback so a record
    # written before the claim existed is still gated.
    column = _headcount(prospect.get("employee_estimate"))
    source = str(prospect.get("employee_source") or "")
    tier = 1 if "[T1]" in source else 2 if "[T2]" in source else None
    if column is not None and tier is not None:
        found.append(SizeEvidence(column, tier, source, "the employee_estimate column"))

    return sorted(found, key=lambda e: -e.headcount)


def band_for(headcount: int | None) -> str | None:
    """Which offer tier a company's scale puts it in, or None above the ICP."""
    if headcount is None:
        return None
    if headcount <= CORE_MAX:
        return CORE
    if headcount <= GROWTH_MAX:
        return GROWTH
    return None


class SizeVerdict(NamedTuple):
    """Whether this company is inside the ICP by size, and on what evidence."""

    outcome: str
    band: str | None
    reason: str
    evidence: SizeEvidence | None

    @property
    def is_dead(self) -> bool:
        return self.outcome == DEAD

    @property
    def needs_review(self) -> bool:
        return self.outcome == REVIEW


def size_verdict(prospect: dict[str, Any]) -> SizeVerdict:
    """Decide the company's ICP standing from the largest trusted headcount.

    A company with no trusted headcount passes with no band. That is not an
    oversight: most small manufacturers publish no number at all, and refusing
    everyone we cannot measure would empty the list. The gate excludes on
    evidence, never on its absence.
    """
    claims = size_claims(prospect)
    if not claims:
        return SizeVerdict(OK, None, "no trusted headcount is recorded", None)

    largest = claims[0]
    if largest.headcount > REVIEW_MAX:
        return SizeVerdict(
            DEAD, None,
            f"outside the ICP by size: {largest.words}. The ceiling is "
            f"{GROWTH_MAX}; over {REVIEW_MAX} is not a borderline call",
            largest,
        )
    if largest.headcount > GROWTH_MAX:
        return SizeVerdict(
            REVIEW, None,
            f"above the ICP ceiling of {GROWTH_MAX} but under {REVIEW_MAX}: "
            f"{largest.words}. Held out of outreach until an operator decides",
            largest,
        )
    return SizeVerdict(OK, band_for(largest.headcount),
                       f"inside the ICP: {largest.words}", largest)


def is_overridden(prospect: dict[str, Any]) -> bool:
    """True when an operator has written a note taking responsibility for this one."""
    return bool(str(prospect.get("size_override") or "").strip())


def held_for_size(prospect: dict[str, Any]) -> bool:
    """True when a size review is outstanding and nobody has overridden it.

    Read from the stored column rather than recomputed, so that a record held by
    a run yesterday stays held today even if its evidence has since changed —
    the decision is a human's to make, and quietly withdrawing the question is
    not the same as answering it.
    """
    return bool(prospect.get("size_review")) and not is_overridden(prospect)


def outreach_eligible(prospect: dict[str, Any]) -> bool:
    """Whether this company may appear in any set that leads to a message."""
    return not held_for_size(prospect)
