"""The engagement ladder — what a bounded piece of work costs and how long it runs.

WHAT THIS IS

Five engagement shapes, each with a duration range and a price band. The
analyst draws from it when it puts a number on an approach, so that two
analyses written a month apart quote the same work at the same band and the
operator is never surprised by a figure the pipeline invented for one company.

WHY IT IS A TABLE AND NOT A PARAGRAPH IN A PROMPT

A price that lives in prompt text is a price nobody can grep for, diff, or
correct in one place. Putting it here means changing what an engagement costs
is a one-line edit with a commit message attached, and every analysis generated
afterwards moves with it. It also means the bands can be asserted in tests,
which is the only way to be sure the generator did not quietly round one up.

THE BANDS ARE UNCONFIRMED AND SAY SO

`CONFIRMED` below is False. These durations and bands are derived from the one
constraint the rest of the pipeline already commits to — bounded work of two to
four weeks, not a platform and not a retainer — and from nothing else. They have
not been signed off against a real quote. Until they are, every rendered
analysis carries the caveat, because a price band a reader assumes is settled is
worse than one they know to check.

Set CONFIRMED to True in the same commit that corrects the numbers, and the
caveat stops printing.

HOW A SHAPE IS CHOSEN

Not by price. A shape is chosen by what the work actually is — a diagnostic
that produces a decision, a build that produces a running thing, a pilot that
buys evidence before a build, a longer integration, a standing arrangement.
Three approaches at three prices for the same build is the failure this is meant
to make visible rather than easy: `distinct_from` names the shapes that are
genuinely different engagements rather than the same one resized.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CONFIRMED = False
"""Whether these bands have been checked against real quotes by the operator.

False means every analysis prints the caveat. See the module docstring."""

CAVEAT = (
    "Price bands and durations below come from our internal engagement ladder "
    "and have not yet been reconciled against signed work. Treat them as the "
    "shape of a quote, not the quote."
)
"""Printed wherever a band is shown while CONFIRMED is False."""


class Engagement(BaseModel):
    """One shape of work, with what it costs and how long it runs."""

    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    """What it is called in front of the operator."""
    shape: str
    """What the engagement IS — the thing that makes it different from the others."""
    weeks: tuple[int, int]
    """Duration range, low to high. Never a single number."""
    band: tuple[int, int]
    """Price range in dollars, low to high. Never a point."""
    suits: str
    """When this shape is the right one."""
    distinct_from: tuple[str, ...] = Field(default_factory=tuple)
    """Shapes this one is genuinely a different engagement from, not a resize of."""
    refuses_when: str = ""
    """When this shape must not be quoted."""


LADDER: tuple[Engagement, ...] = (
    Engagement(
        key="diagnostic",
        name="Paid diagnostic",
        shape=(
            "A short engagement whose deliverable is a decision, not a system: "
            "what is actually costing money here, measured rather than estimated, "
            "and what it would take to fix."
        ),
        weeks=(1, 2),
        band=(2_500, 6_000),
        suits=(
            "A company whose problem is visible from outside but whose size cannot "
            "be established without their numbers, and where guessing at the size "
            "would put a wrong figure in front of them."
        ),
        distinct_from=("scoped_build", "pilot_then_build"),
        refuses_when=(
            "The evidence already sizes the problem well enough to quote the fix. "
            "Selling a diagnostic for something already diagnosed is billing for "
            "reading."
        ),
    ),
    Engagement(
        key="scoped_build",
        name="Scoped build",
        shape=(
            "One bounded thing built, delivered running, against a fixed scope "
            "agreed up front. The unit of work this whole pipeline is sized around."
        ),
        weeks=(2, 4),
        band=(8_000, 20_000),
        suits=(
            "A friction the evidence already sizes, where the fix is a component "
            "rather than a programme and the integration surface is known."
        ),
        distinct_from=("diagnostic", "retained_iteration"),
        refuses_when=(
            "The work cannot be described as a finished thing at the end of four "
            "weeks. That is an extended build or a pilot, and calling it a scoped "
            "build sets a date that will be missed."
        ),
    ),
    Engagement(
        key="pilot_then_build",
        name="Pilot, then build",
        shape=(
            "A deliberately small first pass on one line, one cell or one product "
            "family, priced separately, with the full build quoted only after the "
            "pilot has produced numbers. Two decisions, not one."
        ),
        weeks=(3, 6),
        band=(6_000, 15_000),
        suits=(
            "A company where the upside is real but unproven, or where an "
            "operations team has been burned before and needs to see it work on "
            "something small before it touches the constraint."
        ),
        distinct_from=("scoped_build", "extended_build"),
        refuses_when=(
            "There is no natural small slice — a pilot that has to touch the whole "
            "operation to prove anything is just a build with a discount."
        ),
    ),
    Engagement(
        key="extended_build",
        name="Extended build",
        shape=(
            "A larger integration delivered in stages, each stage useful on its "
            "own, where the work spans more than one system or more than one site."
        ),
        weeks=(6, 12),
        band=(25_000, 60_000),
        suits=(
            "A company whose systems are already substantial — several plants, a "
            "real ERP, existing automation — where the value is in joining things "
            "that already run rather than in building a first one."
        ),
        distinct_from=("scoped_build", "diagnostic"),
        refuses_when=(
            "The evidence shows a company small enough that this would be the "
            "largest software project they have ever run. Quote a scoped build and "
            "earn the rest."
        ),
    ),
    Engagement(
        key="retained_iteration",
        name="Standing arrangement",
        shape=(
            "A recurring monthly commitment to keep something built working and to "
            "extend it as the operation changes. Priced per month, not per project."
        ),
        weeks=(4, 4),
        band=(2_000, 6_000),
        suits=(
            "A company that has already had something built and whose problem is "
            "now drift — reports nobody maintains, integrations that break when a "
            "supplier changes a format."
        ),
        distinct_from=("scoped_build", "diagnostic"),
        refuses_when=(
            "Nothing has been built yet. A standing arrangement before a first "
            "delivery is a retainer for availability, which is not what we sell."
        ),
    ),
)

BY_KEY: dict[str, Engagement] = {e.key: e for e in LADDER}


def band_words(key: str) -> str:
    """One engagement's price and duration, written the way it must be quoted.

    Always a range on both axes. A caller that wants a single number is asking
    the wrong question, and there is deliberately no helper that answers it.
    """
    engagement = BY_KEY[key]
    return (
        f"{engagement.weeks[0]}-{engagement.weeks[1]} weeks, "
        f"${engagement.band[0]:,}-${engagement.band[1]:,}"
    )


def monthly_equivalent(key: str) -> tuple[int, int] | None:
    """The band read as a monthly figure, for the one shape that is priced that way."""
    engagement = BY_KEY[key]
    return engagement.band if engagement.key == "retained_iteration" else None


def payback_months(price: tuple[int, int], annual_return: tuple[int, int]) -> tuple[float, float]:
    """Months to pay a band back out of an annual return band.

    Worst case against best case, deliberately: the slow end pays the top of the
    price out of the bottom of the return. Quoting the midpoint of each would
    produce a single flattering number, which is the arithmetic equivalent of a
    point estimate and is refused everywhere else in this pipeline.
    """
    low_return, high_return = annual_return
    low_price, high_price = price
    if low_return <= 0 or high_return <= 0:
        raise ValueError("an annual return band must be positive at both ends")
    fastest = (low_price / high_return) * 12
    slowest = (high_price / low_return) * 12
    return (round(fastest, 1), round(slowest, 1))


def distinct(first: str, second: str) -> bool:
    """Whether two engagement shapes are genuinely different engagements.

    Used by the analyst's distinctness check as one half of the test. Two
    approaches sharing a shape are not automatically the same approach — they
    may attack different problems — so this never decides alone.
    """
    if first == second:
        return False
    return (second in BY_KEY[first].distinct_from
            or first in BY_KEY[second].distinct_from)


def as_prompt_block() -> str:
    """The ladder as prompt text — shapes and bands, no selling language."""
    parts = []
    for engagement in LADDER:
        parts.append(
            f"### {engagement.key}\n"
            f"Name: {engagement.name}\n"
            f"What it is: {engagement.shape}\n"
            f"Duration: {engagement.weeks[0]}-{engagement.weeks[1]} weeks\n"
            f"Price band: ${engagement.band[0]:,}-${engagement.band[1]:,}"
            f"{' per month' if engagement.key == 'retained_iteration' else ''}\n"
            f"Suits: {engagement.suits}\n"
            f"DO NOT QUOTE WHEN: {engagement.refuses_when}"
        )
    header = (
        "THE ENGAGEMENT LADDER. Every price you quote comes from this table and "
        "is quoted as the band, never as a single number and never as a figure "
        "between the bands. Pick the shape by what the work IS, not by what you "
        "want it to cost.\n\n"
    )
    if not CONFIRMED:
        header += f"NOTE TO CARRY INTO THE TEXT: {CAVEAT}\n\n"
    return header + "\n\n".join(parts)


def as_dicts() -> list[dict[str, Any]]:
    """The ladder as plain data, for tests and for the docs."""
    return [e.model_dump() for e in LADDER]
