"""The engagement ladder — what a bounded piece of work costs and how long it runs.

WHAT THIS IS

Seven engagement shapes, each with a duration range and a price band. The
analyst draws from it when it puts a number on an approach, so that two
analyses written a month apart quote the same work at the same band and the
operator is never surprised by a figure the pipeline invented for one company.

Two of the seven were added later and for one reason: the ladder did not reach
either end of the market it is sold into. It started at $2,500, which is above
what an owner-operated shop will agree to without a meeting, and it jumped from
$20,000 to $25,000 with nothing in the range an operations buyer actually
scopes. `starter_automation` and `premium_scope` close both gaps, and `TIERS`
below decides which end a given company is read from.

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
        key="starter_automation",
        name="First small thing",
        shape=(
            "One workflow automated end to end, chosen because it is the smallest "
            "piece of work that produces a visible result — a report that writes "
            "itself, one form that stops being retyped, one file that moves on its "
            "own. Delivered inside a fortnight."
        ),
        weeks=(1, 2),
        band=(600, 2_500),
        suits=(
            "A company at the core size band that has bought no software beyond "
            "the machines, where the first conversation has to end in something "
            "working rather than in a project plan. It is the rung that makes the "
            "ladder start low enough to step onto."
        ),
        distinct_from=("scoped_build", "diagnostic", "extended_build",
                       "premium_scope"),
        refuses_when=(
            "The friction cannot be reduced to one workflow. Selling a fortnight "
            "against a problem that needs a month is how a first engagement "
            "becomes a bad reference."
        ),
    ),
    Engagement(
        key="premium_scope",
        name="Operations scope",
        shape=(
            "A build sized for an operation that already has systems and a person "
            "whose job is to run them: several workflows joined, instrumented so "
            "the result is measurable, with the baseline captured before anything "
            "changes."
        ),
        weeks=(4, 8),
        band=(15_000, 30_000),
        suits=(
            "A company at the growth size band, where the conversation is with a "
            "VP of Operations rather than an owner, and where the question is not "
            "whether to automate but which constraint to take first."
        ),
        distinct_from=("starter_automation", "diagnostic", "retained_iteration"),
        refuses_when=(
            "Nobody owns operations as a job. Without an internal owner this "
            "becomes a system delivered to a company that cannot absorb it."
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


# ------------------------------------------------------------- offer routing

CORE = "core"
GROWTH = "growth"


class Tier(BaseModel):
    """Which shapes an offer set may draw on, and who it is written for.

    The routing exists because the same three approaches are a different
    document at different sizes. An owner-operated shop that has bought no
    software beyond the machines needs the ladder to start at a number they can
    say yes to on a call; a company with a VP of Operations needs the scope that
    matches the job they already have somebody doing. Quoting either one at the
    other's band is not aggressive or timid, it is a document written for
    somebody who is not in the room.
    """

    model_config = ConfigDict(frozen=True)

    band: str
    lead: str
    """The shape the lead recommendation draws from."""

    allowed: tuple[str, ...]
    """Shapes an approach in this tier may quote."""

    audience: str
    framing: str
    money_words: str


TIERS: dict[str, Tier] = {
    CORE: Tier(
        band=CORE,
        lead="starter_automation",
        allowed=("starter_automation", "diagnostic", "pilot_then_build",
                 "scoped_build", "retained_iteration"),
        audience="the owner or president, who will take the call themselves",
        framing=(
            "Start at the smallest thing that produces a visible result and let "
            "the ladder go up from there. The first engagement's job is to be "
            "worth saying yes to without a meeting about it. Say the price of "
            "the first rung out loud early — an owner who has to ask is an owner "
            "already deciding it is out of reach."
        ),
        money_words=(
            "$600-$8,000 for the first two rungs, and a pilot from $6,000 where "
            "the upside is real but has to be proved on something small first"
        ),
    ),
    GROWTH: Tier(
        band=GROWTH,
        lead="premium_scope",
        allowed=("premium_scope", "scoped_build", "diagnostic",
                 "pilot_then_build", "extended_build"),
        audience=(
            "a VP of Operations or equivalent — somebody whose job is already to "
            "run the systems this touches"
        ),
        framing=(
            "Scope the constraint, instrument the baseline before anything "
            "changes, and hand back a number that can be argued with. The buyer "
            "here is not deciding whether to automate; they are deciding which "
            "constraint to take first."
        ),
        money_words="$15,000-$30,000 for the scope that carries the work",
    ),
}
"""Offer routing by size band. Weights and scoring are untouched by this.

A band we do not hold routes to CORE, because the smaller ladder is the one that
can be walked away from cheaply, and quoting an operations scope to a company we
cannot size is the expensive direction to be wrong in."""


def tier_for(size_band: str | None) -> Tier:
    """The offer tier for a size band, defaulting to the cheaper mistake."""
    return TIERS.get((size_band or "").strip().lower(), TIERS[CORE])


POSITIONING = (
    "Capacity unlocked without capital expenditure. The work below does not add "
    "a machine, a building or a headcount — it takes hours out of the ones "
    "already being paid for. We come in as an auditor, not a vendor: the first "
    "deliverable is a number about their operation that they can check and "
    "correct, and every figure here is either theirs, cited, or ours and "
    "labelled as ours."
)
"""How the offers are positioned, in one place so it cannot drift per document."""


class GainShare(BaseModel):
    """A deployment fee plus a capped share of savings we can both see.

    Only offered where a baseline can actually be instrumented before anything
    changes. Without that, a share of savings is a share of a number one side
    computed and the other side has to take on trust, which is a worse
    arrangement for the prospect than a fixed price and reads as a better one.
    """

    model_config = ConfigDict(frozen=True)

    base_key: str
    deployment_fee: tuple[int, int]
    share_percent: tuple[int, int]
    cap_multiple: float
    """The total share payment is capped at this multiple of the deployment fee."""

    metric: str
    """The ONE pre-agreed measure. One, because two metrics is a negotiation
    every month about which one counts."""

    requirements: tuple[str, ...]

    def cap_dollars(self) -> tuple[int, int]:
        return (int(self.deployment_fee[0] * self.cap_multiple),
                int(self.deployment_fee[1] * self.cap_multiple))

    def words(self) -> str:
        low, high = self.deployment_fee
        cap_low, cap_high = self.cap_dollars()
        return (
            f"${low:,}-${high:,} to deploy, then {self.share_percent[0]}-"
            f"{self.share_percent[1]}% of the verified movement in {self.metric}, "
            f"capped at ${cap_low:,}-${cap_high:,} in total"
        )


GAIN_SHARE_DEPLOYMENT_SHARE = 0.5
"""How much of the fixed band is charged up front under a gain share.

Half, so that the deployment is genuinely funded and the share is genuinely at
risk. Charging the whole band and adding a share is a price rise with a story
attached; charging nothing makes us the only party who has bet anything."""

GAIN_SHARE_PERCENT = (15, 25)
GAIN_SHARE_CAP_MULTIPLE = 2.0

GAIN_SHARE_REQUIREMENTS: tuple[str, ...] = (
    "a baseline measured for at least four weeks BEFORE anything is changed, "
    "from their system rather than from anybody's recollection",
    "one metric agreed in writing before the work starts, with its definition "
    "and its data source named",
    "read access to the system that produces the metric, for the term",
    "a named person on their side who signs off the monthly reading",
    "a stated end date, after which the share stops whether or not the saving "
    "continues",
)
"""What has to be true operationally, stated in the offer rather than discovered.

Every one of these is a reason the arrangement fails if it is missing, and a
gain share that starts without them ends in an argument about a number."""


def gain_share_for(base_key: str, metric: str) -> GainShare:
    """The gain-share variant of a fixed-price shape, against one named metric."""
    engagement = BY_KEY[base_key]
    low, high = engagement.band
    return GainShare(
        base_key=base_key,
        deployment_fee=(int(low * GAIN_SHARE_DEPLOYMENT_SHARE),
                        int(high * GAIN_SHARE_DEPLOYMENT_SHARE)),
        share_percent=GAIN_SHARE_PERCENT,
        cap_multiple=GAIN_SHARE_CAP_MULTIPLE,
        metric=metric,
        requirements=GAIN_SHARE_REQUIREMENTS,
    )


def instrumentable(metric: str | None, has_named_system: bool) -> bool:
    """Whether a gain share may be offered at all.

    Two conditions, both necessary. There must be one metric worth agreeing on,
    and there must be a system that produces it — because a baseline measured by
    asking people how long something takes is not a baseline, and a share
    computed from one is a share computed from a memory.
    """
    return bool((metric or "").strip()) and has_named_system
