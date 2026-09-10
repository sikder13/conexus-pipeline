"""Building a finmodel spec from what we actually hold about one company.

WHY THIS SITS BETWEEN THE EVIDENCE AND THE ARITHMETIC

`lib/finmodel.py` will evaluate any spec it is handed and refuses one whose
inputs have no source. `lib/roi_patterns.py` says which arithmetic fits which
observed friction. Neither of them knows how to read an evidence file, and
neither should: the moment the engine starts reaching into `block4` it stops
being testable arithmetic and starts being a pipeline.

So this module does the one job in between. It takes a prospect, an ROI pattern
and an engagement shape, and builds the spec — reading a figure off a claim
where the evidence holds one, taking a benchmark where a published one exists,
and otherwise stating a labelled assumption with a range wide enough to be
honest about not knowing.

THE HONEST PART IS THE ASSUMPTIONS, NOT THE CLAIMS

Most inputs here are assumptions, and that is not a weakness to be engineered
away. We do not know how many quotes a shop sends in a month, and no amount of
reading their website will tell us. What the model does is make the not-knowing
structural: the figure is labelled, it is a range, it appears in the document as
ours, and the sensitivity analysis says what it would have to be for the
conclusion to hold. That is a better first conversation than a confident number,
because it converts the prospect into a corrector — which is the same argument
`CANARY.md` makes about publishing conditional ranges in outbound.

The claims are used where they exist and they are worth more than everything
else in the spec. A posted salary is a wage rate they published. A grant award
is capital they have already committed. Those anchor the model; the assumptions
fill the rest and say so.

SCENARIOS ARE INPUT SETS, NOT MOODS

Conservative, target and aggressive differ only in the inputs we are least sure
of — the share of the work that automation actually removes, and how long the
work takes today. The formulas are identical across all three. That matters: if
scenarios differed in structure, the three columns would be three arguments
rather than one argument under three readings, and the reader could not tell
which.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from lib import benchmarks, finmodel, peers, pricing
from lib.integrity import is_usable, iter_all_claims

LOADED_MULTIPLIER = "labour.loaded_multiplier.us_manufacturing"

DEFAULT_ESCALATION = finmodel.Interval.span(0.030, 0.045)
"""Annual wage escalation used when no macro series is available.

A range rather than a figure, and deliberately wide. When `lib/macro.py` has a
key the employment cost index replaces this with a published number and the
provenance changes from an assumption of ours to a claim with a URL."""


class WorkUnit(BaseModel):
    """What is being counted, and what we assume about it before they tell us."""

    model_config = ConfigDict(frozen=True)

    pattern_key: str
    unit: str
    """Singular noun for one piece of the work: a quote, an inspection record."""

    unit_plural: str
    volume_words: str
    """How the monthly count is described in prose."""

    volume: tuple[float, float]
    minutes: tuple[float, float]
    share: tuple[float, float]
    """Share of the effort a build removes, at the target reading."""

    headroom: tuple[float, float]
    """Spare capacity in today's manual process, as a share above today's volume.

    Anchored to today's volume rather than guessed as an absolute number of
    hours, because the one thing we can be confident of is that they are coping
    NOW: whatever arrives this month gets done. So the manual rate is today's
    volume plus some headroom, and the capacity question becomes the only one
    that matters — how long the growth takes to eat it.

    An independent guess at available hours produced a crossing in month one,
    which is not a finding, it is two of our own assumptions disagreeing."""

    metric: str
    """The one measure a gain share would be written against."""

    build: str
    """What is built, in one clause, for the approach's own description."""

    volume_nouns: tuple[str, ...] = ()
    """Other words a company would use for the thing this model counts.

    `unit` and `unit_plural` are what WE call it; these are what they call it.
    A shop that says "we send about forty RFQs a week" has stated its quoting
    volume, and `lib/anchors.py` will only recognise that if 'rfq' is written
    down somewhere. Here, beside the unit, rather than in the module that
    searches — a new pattern should arrive with its own vocabulary."""

    anchors_on: str = "volume"
    """What sizes this model: 'volume' counts work, 'capital' costs an asset.

    Every unit but one counts work. The exception exists because a company with
    no volume and no headcount still has a Tier 1 number of its own — the money
    it committed — and the honest way to use it is a different model rather than
    a third guess at how many quotes it sends."""


WORK_UNITS: tuple[WorkUnit, ...] = (
    WorkUnit(
        pattern_key="quoting_velocity",
        unit="quote", unit_plural="quotes",
        volume_words="quotes leaving the desk each month",
        volume=(40, 150), minutes=(45, 180), share=(0.30, 0.50),
        headroom=(0.10, 0.25),
        volume_nouns=("rfq", "rfqs", "quotation", "quotations", "estimate",
                      "estimates", "bid", "bids", "tender", "tenders"),
        metric="estimator hours spent assembling a quote",
        build="a quote assembler that drafts from past jobs and material prices",
    ),
    WorkUnit(
        pattern_key="qa_documentation_labour",
        unit="certificate or inspection record", unit_plural="records",
        volume_words="inspection records and certificates produced each month",
        volume=(80, 400), minutes=(10, 40), share=(0.40, 0.65),
        headroom=(0.10, 0.25),
        volume_nouns=("certificate", "certificates", "coc", "cofc", "inspection",
                      "inspections", "test report", "test reports", "packet",
                      "packets"),
        metric="hours spent producing inspection paperwork",
        build="a document generator that fills the packet from the measurements "
              "already recorded",
    ),
    WorkUnit(
        pattern_key="clerical_hire_payback",
        unit="order or scheduling transaction", unit_plural="transactions",
        volume_words="orders and scheduling transactions handled each month",
        volume=(150, 600), minutes=(6, 20), share=(0.35, 0.60),
        headroom=(0.10, 0.25),
        volume_nouns=("order", "orders", "purchase order", "purchase orders", "po",
                      "pos", "job", "jobs", "ticket", "tickets", "release",
                      "releases"),
        metric="hours spent moving order data between systems",
        build="an intake that takes the order once and puts it everywhere it has "
              "to go",
    ),
    WorkUnit(
        pattern_key="machine_data_analysis",
        unit="machine-shift of production", unit_plural="machine-shifts",
        volume_words="machine-shifts run each month",
        volume=(120, 500), minutes=(8, 25), share=(0.45, 0.70),
        headroom=(0.10, 0.25),
        volume_nouns=("machine shift", "machine shifts", "shift", "shifts",
                      "spindle hour", "spindle hours", "cycle", "cycles"),
        metric="hours spent collecting and typing up machine output",
        build="a weekly utilisation and scrap report drawn from what the machines "
              "already record",
    ),
    WorkUnit(
        pattern_key="logistics_billing_anomaly",
        unit="freight invoice", unit_plural="invoices",
        volume_words="freight and supplier invoices checked each month",
        volume=(100, 500), minutes=(5, 18), share=(0.50, 0.75),
        headroom=(0.10, 0.25),
        volume_nouns=("invoice", "invoices", "bill", "bills", "freight bill",
                      "freight bills", "shipment", "shipments", "load", "loads"),
        metric="hours spent checking invoices against what was agreed",
        build="an invoice check that flags only the lines that disagree with the "
              "rate that was agreed",
    ),
    WorkUnit(
        pattern_key="throughput_downtime",
        unit="production run", unit_plural="runs",
        volume_words="production runs started each month",
        volume=(60, 300), minutes=(12, 45), share=(0.30, 0.55),
        headroom=(0.10, 0.25),
        volume_nouns=("run", "runs", "batch", "batches", "changeover", "changeovers",
                      "setup", "setups", "job", "jobs"),
        metric="hours lost to changeover and setup paperwork",
        build="a changeover record that captures the reason a line stopped without "
              "anybody writing it down",
    ),
    WorkUnit(
        pattern_key="ai_search_invisibility",
        unit="inbound enquiry", unit_plural="enquiries",
        volume_words="inbound enquiries arriving each month",
        volume=(10, 60), minutes=(20, 90), share=(0.30, 0.55),
        headroom=(0.10, 0.25),
        volume_nouns=("enquiry", "enquiries", "inquiry", "inquiries", "lead", "leads",
                      "web lead", "web leads", "contact form", "contact forms"),
        metric="hours between an enquiry arriving and a human answering it",
        build="a front door that answers, captures and routes an enquiry the hour "
              "it lands",
    ),
)

CAPITAL_PATTERN = "capital_utilisation"

CAPITAL_UNIT = WorkUnit(
    pattern_key=CAPITAL_PATTERN,
    unit="dollar of committed capital", unit_plural="dollars of committed capital",
    volume_words="capital committed under the award",
    # The three volume fields are unused by this unit and are here because the
    # type requires them. `anchors_on='capital'` is what actually routes it, and
    # `build_capital_spec` never reads a volume, a minute count or a share.
    volume=(0, 0), minutes=(0, 0), share=(0.0, 0.0), headroom=(0.0, 0.0),
    anchors_on="capital",
    metric="the share of the funded asset's available time it actually runs",
    build="a utilisation record for the grant-funded equipment, drawn from what "
          "the machines already log",
)
"""The one model that costs an asset rather than counting work.

WHY IT EXISTS, AND WHY IT IS NOT A HEADCOUNT PROXY

A grant amount says nothing about how many people work somewhere. A quarter of a
million dollars buys one machine at one company and pays six salaries at
another, and any model that reads a headcount out of an award size is inventing
a fact about a payroll from a fact about a purchase. That inference was proposed
and refused, and the refusal stands.

What an award size DOES say, at Tier 1, from a government record the company
itself is named on, is how much capital this company has committed. That is a
different question with a different answer, and it supports different
arithmetic: what does that capital cost per year over its life, and what share
of that charge is being paid on an asset nobody can prove ran?

Nothing here multiplies people by hours. There is no wage in this spec and no
volume. That is the test of whether the distinction is real, and it is why the
inputs below look nothing like the ones above."""

ASSET_LIFE = (5.0, 10.0)
"""Years a grant-funded production asset earns, before it is replaced or written off.

Wide, and an assumption of ours. A readiness grant buys machine tools, inspection
equipment and automation cells; the range covers a fast-moving inspection rig at
one end and a press at the other. The sensitivity says what it would have to be."""

UTILISATION_TODAY = (0.45, 0.70)
"""Share of available time the funded asset runs today, before anyone measures it.

The honest position is that we do not know and neither, usually, do they —
which is the entire argument for the build. Stated as a band and put in front of
the prospect as the thing to correct."""

UTILISATION_GAIN = (0.05, 0.15)
"""Share of available time that measurement recovers.

Not a share of the *idle* time — a share of the whole, which is the more
conservative reading of the same claim. It is small on purpose: a utilisation
record does not run the machine, it tells somebody which shift stopped and why,
and the recovery is whatever they do with that."""

BY_PATTERN: dict[str, WorkUnit] = {w.pattern_key: w for w in (*WORK_UNITS, CAPITAL_UNIT)}

SCENARIO_INPUTS: tuple[str, ...] = (
    "volume_per_month", "minutes_per_unit", "automatable_share",
)
"""The inputs a scenario moves. Everything else is identical across all three.

A scenario is an INPUT SET, not a mood. These are the three figures the prospect
knows exactly and we do not know at all — how much of the work there is, how
long a piece of it takes, and how much of that a build removes — and they are
the three that a first call settles.

The wage, the loaded multiplier, the escalation rate and the cost of capital do
NOT move between scenarios, because they are not things this company's answer
changes. Moving them would make the aggressive column an argument rather than a
reading."""

AGGRESSIVE_CEILING = 0.85
"""No scenario claims a build removes more than this share of the effort.

Because it does not. Somebody still checks the output, handles the exception and
talks to the customer, and a model that quietly assumes otherwise produces a
saving the operator will have to walk back on the call."""


def thirds(low: float, high: float) -> dict[str, finmodel.Interval]:
    """One declared range split into the three readings, low to high.

    This is what stopped the model saying anything. With one wide band on every
    input, interval arithmetic multiplied the widths together: a plausible range
    of quote volumes times a plausible range of minutes times a plausible range
    of wages came out as an annual cost of eleven thousand to two hundred and
    seventy-five thousand dollars, which is arithmetically correct and worth
    nothing to a reader.

    Splitting the uncertainty across the three scenarios instead is what a
    scenario table is FOR. Each column is narrow enough to mean something, and
    the three together still span the whole range we were honestly unsure about
    — nothing is hidden, it is just reported in the shape somebody can use.
    """
    width = (high - low) / 3
    return {
        finmodel.CONSERVATIVE: finmodel.Interval.span(low, low + width),
        finmodel.TARGET: finmodel.Interval.span(low + width, low + 2 * width),
        finmodel.AGGRESSIVE: finmodel.Interval.span(low + 2 * width, high),
    }


# ------------------------------------------------------------ reading claims

MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")
HOURLY = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:an?\s+hour|per\s+hour|/\s?h(?:r|our))",
                    re.IGNORECASE)
ANNUAL_SALARY = re.compile(
    r"\$\s?([\d,]{4,})(?:\s*(?:-|–|to)\s*\$?\s?([\d,]{4,}))?\s*(?:a\s+year|per\s+year|"
    r"annually|/\s?y(?:r|ear)|salary)", re.IGNORECASE)

WORKING_HOURS_A_YEAR = 2080


def _number(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except (TypeError, ValueError):
        return None


Wage = tuple[finmodel.Interval, finmodel.Provenance]


def wage_from_evidence(prospect: dict[str, Any]) -> Wage | None:
    """An hourly wage the company published, if the evidence holds one.

    A posted salary is the strongest input this model can have, because it is
    their number about their own payroll. Read from a hiring claim only —
    inferring a wage from a headcount or a grant size would be exactly the
    fabrication this pipeline exists to refuse.
    """
    for path, claim in iter_all_claims(prospect.get("evidence_file") or {}):
        if "hiring" not in path or not is_usable(claim):
            continue
        text = str(claim.get("value") or "")
        hourly = HOURLY.search(text)
        if hourly and (value := _number(hourly.group(1))):
            return (
                finmodel.Interval.of(value),
                finmodel.claim_source(path.removeprefix("evidence_file."),
                                      label="an hourly rate they posted"),
            )
        salary = ANNUAL_SALARY.search(text)
        if salary:
            low = _number(salary.group(1))
            high = _number(salary.group(2)) if salary.group(2) else low
            if low and high:
                return (
                    finmodel.Interval.span(low / WORKING_HOURS_A_YEAR,
                                           high / WORKING_HOURS_A_YEAR),
                    finmodel.claim_source(path.removeprefix("evidence_file."),
                                          label="a salary they posted"),
                )
    return None


REFERENCE_HEADCOUNT = 50
"""The company size the default volume bands are written for.

Without this every company got the same arithmetic, because every volume input
was the same default assumption — and twenty analyses quoting the same annual
figure read as a template however honestly each one was labelled. The bands are
now scaled to the headcount we hold, which is the one size fact this pipeline
usually has."""

VOLUME_SCALE_LIMITS = (0.25, 6.0)
"""How far a headcount may move the volume band, up or down.

Clamped because the relationship is real and not linear: a five-hundred-person
plant does not send ten times the quotes of a fifty-person shop, it sends more
quotes through more people with more process. Beyond this range the scaling
would be asserting something about their operating model that we have not
observed."""


def volume_for(base: tuple[float, float], headcount: int | None) -> tuple[tuple[float, float], str]:
    """The volume band scaled to a company of this size, and how to say so.

    Still an assumption and still labelled as one. What changes is the anchor:
    "somewhere between seventy and a hundred and ten a month" is our guess about
    manufacturers in general, and "scaled to the hundred and seventy-one people
    their case study reports" is our guess about THIS one. The second is
    correctable on the call in a way the first is not, because the prospect knows
    the headcount we used.
    """
    if not headcount or headcount <= 0:
        return base, ""
    low, high = VOLUME_SCALE_LIMITS
    scale = min(max(headcount / REFERENCE_HEADCOUNT, low), high)
    return ((base[0] * scale, base[1] * scale),
            f", scaled to the {headcount:,} people we hold for them")


DEFAULT_WAGE = finmodel.Interval.span(22.0, 34.0)
"""Hourly wage assumed when nothing was posted, before the loaded multiplier.

Wide because it has to cover an estimator and a shipping clerk, and because the
sensitivity analysis will say what it would have to be for the conclusion to
change — which is more useful than a narrower guess."""


def escalation_input(macro_claim: dict[str, Any] | None, path: str = "") -> finmodel.Input:
    """Wage escalation from the published series where we have it, else labelled.

    The provenance changes with the source and the document changes with it: a
    trajectory drawn on a published index reads differently from one drawn on
    our own range, and it should.
    """
    if macro_claim and path:
        rate = _rate_in(str(macro_claim.get("value") or ""))
        if rate is not None:
            return finmodel.make_input(
                "wage_escalation", finmodel.Interval.of(rate),
                finmodel.claim_source(path, label="the published employment cost index"),
                unit="a year", description="annual wage escalation")
    return finmodel.make_input(
        "wage_escalation", DEFAULT_ESCALATION,
        finmodel.assumed("wage escalation of three to four and a half percent a year"),
        unit="a year", description="annual wage escalation")


PERCENT = re.compile(r"([\d.]+)\s*percent", re.IGNORECASE)


def _rate_in(text: str) -> float | None:
    found = PERCENT.search(text)
    value = _number(found.group(1)) if found else None
    return value / 100 if value is not None else None


# ------------------------------------------------------------- the spec


def scenario_share(base: tuple[float, float], scenario: str) -> finmodel.Interval:
    """One scenario's automatable share, capped so no reading claims the lot."""
    band = thirds(base[0], base[1])[scenario]
    return finmodel.Interval.span(
        min(band.low, AGGRESSIVE_CEILING), min(band.high, AGGRESSIVE_CEILING))


def scenario_sets(
    unit: WorkUnit, volume_band: tuple[float, float] | None = None,
) -> dict[str, dict[str, finmodel.Interval]]:
    """The three input sets, each a coherent reading rather than a knob."""
    volume = thirds(*(volume_band or unit.volume))
    minutes = thirds(*unit.minutes)
    return {
        name: {
            "volume_per_month": volume[name],
            "minutes_per_unit": minutes[name],
            "automatable_share": scenario_share(unit.share, name),
        }
        for name in finmodel.SCENARIOS
    }


def build_spec(
    pattern_key: str,
    prospect: dict[str, Any],
    engagement_key: str,
    macro_claim: dict[str, Any] | None = None,
    macro_path: str = "",
    horizon_months: int = 36,
    anchor: Any = None,
) -> finmodel.ModelSpec:
    """One approach's arithmetic, as a spec that says where every input came from.

    ``anchor`` is what the volume band is sized to, from `lib/anchors.py`. It is
    optional so that a caller with no anchor still gets the old behaviour — the
    reference band scaled by whatever headcount is on file — but every caller in
    the pipeline passes one, because an unanchored band is how twenty-five
    companies came to share a headline.
    """
    unit = BY_PATTERN.get(pattern_key)
    if unit is None:
        raise KeyError(
            f"no work unit for ROI pattern {pattern_key!r}; the library covers "
            f"{', '.join(sorted(BY_PATTERN))}"
        )
    if unit.anchors_on == "capital":
        return build_capital_spec(
            prospect, engagement_key, macro_claim, macro_path, horizon_months, anchor)

    engagement = pricing.BY_KEY[engagement_key]
    company = str(prospect.get("company_name") or "this company")
    headcount = peers.size_of(prospect).headcount
    if anchor is not None and getattr(anchor, "volume", None):
        volume_band = tuple(anchor.volume)
        scaled_words = f", {anchor.words}" if anchor.words else ""
    else:
        volume_band, scaled_words = volume_for(unit.volume, headcount)

    wage = wage_from_evidence(prospect)
    wage_input = (
        finmodel.make_input("wage_rate", wage[0], wage[1], unit="$/hr",
                            description="the hourly wage of the person doing this work")
        if wage else
        finmodel.make_input(
            "wage_rate", DEFAULT_WAGE,
            finmodel.assumed(
                "an hourly wage of twenty-two to thirty-four dollars for the person "
                "doing this work"),
            unit="$/hr",
            description="the hourly wage of the person doing this work")
    )

    # A volume they published is a CLAIM, and the provenance has to say so. The
    # difference is the whole point of the anchoring order: a document whose
    # busiest input is sourced to a page reads differently from one whose
    # busiest input is sourced to us, and the reader is entitled to know which.
    stated_path = getattr(anchor, "claim_path", None) if anchor is not None else None
    volume_provenance = (
        finmodel.claim_source(stated_path, label=f"a volume they published: "
                                                 f"{anchor.words}")
        if stated_path else
        finmodel.assumed(f"{volume_band[0]:,.0f} to {volume_band[1]:,.0f} "
                         f"{unit.unit_plural} a month{scaled_words}")
    )
    inputs = {
        "volume_per_month": finmodel.make_input(
            "volume_per_month", thirds(*volume_band)[finmodel.TARGET],
            volume_provenance,
            unit=unit.unit_plural, description=unit.volume_words),
        "minutes_per_unit": finmodel.make_input(
            "minutes_per_unit", thirds(*unit.minutes)[finmodel.TARGET],
            finmodel.assumed(f"{unit.minutes[0]:g} to {unit.minutes[1]:g} minutes "
                             f"of work per {unit.unit}"),
            unit="minutes", description=f"minutes of work per {unit.unit}"),
        "wage_rate": wage_input,
        "loaded_multiplier": benchmarks.as_input(
            LOADED_MULTIPLIER, name="loaded_multiplier",
            description="the multiple from wages to what an hour actually costs"),
        "automatable_share": finmodel.make_input(
            "automatable_share", scenario_share(unit.share, finmodel.TARGET),
            finmodel.assumed(
                f"a build removing {unit.share[0] * 100:.0f} to "
                f"{unit.share[1] * 100:.0f} percent of that effort"),
            unit="share", description="the share of the effort a build removes"),
        "capacity_headroom": finmodel.make_input(
            "capacity_headroom", unit.headroom,
            finmodel.assumed(
                f"{unit.headroom[0] * 100:.0f} to {unit.headroom[1] * 100:.0f} percent "
                f"spare capacity in the manual process as it runs today"),
            unit="share above today's volume",
            description="spare capacity in the way it is done today"),
        "deployment_fee": finmodel.make_input(
            "deployment_fee", engagement.band,
            finmodel.assumed(
                f"our published band for {engagement.name.lower()}, "
                f"${engagement.band[0]:,} to ${engagement.band[1]:,}, which has not "
                f"yet been reconciled against signed work"),
            unit="$", description=f"the one-off cost of {engagement.name.lower()}"),
        "wage_escalation": escalation_input(macro_claim, macro_path),
        "discount_rate": finmodel.make_input(
            "discount_rate", finmodel.Interval.of(0.12),
            finmodel.assumed("a twelve percent cost of capital"),
            unit="a year", description="the discount rate"),
        "volume_growth": finmodel.make_input(
            "volume_growth", finmodel.Interval.span(0.03, 0.10),
            finmodel.assumed("volume growth of three to ten percent a year"),
            unit="a year", description=f"growth in {unit.unit_plural} a month"),
    }

    formulas = {
        "hours_per_unit": finmodel.div(
            finmodel.ref("minutes_per_unit"),
            finmodel.const(60, "minutes in an hour")),
        "loaded_hourly_rate": finmodel.mul(
            finmodel.ref("wage_rate"), finmodel.ref("loaded_multiplier")),
        "hours_per_month": finmodel.mul(
            finmodel.ref("volume_per_month"), finmodel.ref("hours_per_unit")),
        "annual_labour_cost": finmodel.mul(
            finmodel.ref("hours_per_month"), finmodel.ref("loaded_hourly_rate"),
            finmodel.const(12, "months in a year")),
        "monthly_saving": finmodel.mul(
            finmodel.ref("hours_per_month"), finmodel.ref("loaded_hourly_rate"),
            finmodel.ref("automatable_share")),
        "annual_saving": finmodel.annualise(finmodel.ref("monthly_saving")),
        "hours_returned_a_month": finmodel.mul(
            finmodel.ref("hours_per_month"), finmodel.ref("automatable_share")),
        "manual_capacity": finmodel.mul(
            finmodel.ref("volume_per_month"),
            finmodel.add(finmodel.const(1, "today's volume"),
                         finmodel.ref("capacity_headroom"))),
    }

    return finmodel.ModelSpec(
        model_id=f"{_slug(company)}.{pattern_key}.{engagement_key}",
        title=f"{unit.build.capitalize()} for {company}",
        inputs=inputs,
        formulas=formulas,
        scenarios=scenario_sets(unit, volume_band),
        roles=finmodel.Roles(
            investment="deployment_fee",
            monthly_saving="monthly_saving",
            annual_saving="annual_saving",
            escalating_cost="annual_labour_cost",
            escalation_rate="wage_escalation",
            discount_rate="discount_rate",
            monthly_volume="volume_per_month",
            volume_growth="volume_growth",
            manual_capacity="manual_capacity",
        ),
        horizon_months=horizon_months,
        units={
            "hours_per_unit": "hours", "loaded_hourly_rate": "$/hr",
            "hours_per_month": "hours", "annual_labour_cost": "$",
            "monthly_saving": "$", "annual_saving": "$",
            "hours_returned_a_month": "hours", "manual_capacity": unit.unit_plural,
        },
        notes=[
            pricing.caveat_for([engagement_key]),
            f"The metric a gain share would be written against: {unit.metric}.",
        ],
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40] or "company"


CAPITAL_SCENARIO_INPUTS: tuple[str, ...] = ("utilisation_today", "utilisation_gain")
"""The two inputs a scenario moves in the capital model.

Same principle as the labour model's three: these are the figures the company
knows and we do not. The asset life does not move between scenarios, because a
press does not have a different working life under a pessimistic reading."""

CAPITAL_SENSITIVITY: tuple[tuple[str, int], ...] = (("utilisation_gain", 12),)
"""What we solve for. One input, because one input carries this model.

The sentence it produces is the only one worth saying on the call: what the
recovered share would have to be for the build to pay for itself inside a year.
Everything else in the spec is either their own number or a range we published."""


HORIZON_YEARS = 3.0
"""The window a payback has to land inside for an approach to be offerable."""


def award_floor() -> float:
    """The smallest award this model can honestly say anything about.

    Below it the arithmetic still runs and the answer is always the same: no.
    A $72,600 contribution over a ten-year life is a capital charge of $7,260 a
    year, five to fifteen per cent of which is $360 to $1,090 — against a build
    that starts at $2,500. The model is not wrong there; it is telling us the
    approach does not apply, and printing it anyway would put a number in front
    of a prospect whose only honest reading is "not worth doing".

    So the floor is computed from the ladder rather than chosen: the capital
    that would let the CHEAPEST engagement pay back inside the horizon at the
    weakest reading. Derived rather than typed, so a change to the ladder moves
    it and nobody has to remember to.
    """
    cheapest = min(e.band[0] for e in pricing.LADDER)
    years = HORIZON_YEARS
    weakest_gain = thirds(*UTILISATION_GAIN)[finmodel.CONSERVATIVE].low
    longest_life = ASSET_LIFE[1]
    return (cheapest / years) * longest_life / weakest_gain



def build_capital_spec(
    prospect: dict[str, Any],
    engagement_key: str,
    macro_claim: dict[str, Any] | None = None,
    macro_path: str = "",
    horizon_months: int = 36,
    anchor: Any = None,
) -> finmodel.ModelSpec:
    """The arithmetic for a company whose only number is the capital it committed.

    Read the docstring on `CAPITAL_UNIT` first: this is a different model, not a
    different guess. It costs the annual charge on an asset the company has
    already paid for and asks what share of that charge is being paid on time
    nobody can prove the asset ran.

    The award amount is a FLOOR and is treated as one. Both programmes disclose
    the contribution rather than the total project cost, and the Indiana one
    additionally requires a 1:1 match — so the real capital is at least this and
    usually more. Using the floor means every figure this model produces is the
    smallest honest version of itself, which is the right direction for a number
    somebody will repeat on a phone call.
    """
    engagement = pricing.BY_KEY[engagement_key]
    company = str(prospect.get("company_name") or "this company")
    capital = float(getattr(anchor, "capital", 0.0) or 0.0)
    capital_path = getattr(anchor, "capital_path", None)
    if capital <= 0:
        raise ValueError(
            "the capital model needs an award amount; without one there is "
            "nothing to compute a capital charge on")

    capital_provenance = (
        finmodel.claim_source(
            capital_path,
            label="the award their own government record states",
            note="a floor on capital deployed, not a total: the disclosure is the "
                 "contribution, not the project")
        if capital_path else
        finmodel.assumed(f"an award of ${capital:,.0f} recorded against them")
    )

    inputs = {
        "capital_deployed": finmodel.make_input(
            "capital_deployed", finmodel.Interval.of(capital), capital_provenance,
            unit="$", description="capital they have committed under the award"),
        "asset_life_years": finmodel.make_input(
            "asset_life_years", ASSET_LIFE,
            finmodel.assumed(
                f"a working life of {ASSET_LIFE[0]:g} to {ASSET_LIFE[1]:g} years for "
                f"equipment bought under a readiness or research contribution"),
            unit="years", description="how long the funded asset earns"),
        "utilisation_today": finmodel.make_input(
            "utilisation_today", thirds(*UTILISATION_TODAY)[finmodel.TARGET],
            finmodel.assumed(
                f"the funded asset running {UTILISATION_TODAY[0] * 100:.0f} to "
                f"{UTILISATION_TODAY[1] * 100:.0f} percent of its available time "
                f"today, which is the figure we are asking them to correct"),
            unit="share", description="share of its available time the asset runs today"),
        "utilisation_gain": finmodel.make_input(
            "utilisation_gain", thirds(*UTILISATION_GAIN)[finmodel.TARGET],
            finmodel.assumed(
                f"measurement recovering {UTILISATION_GAIN[0] * 100:.0f} to "
                f"{UTILISATION_GAIN[1] * 100:.0f} percent of the asset's available "
                f"time — a share of the whole, not of the idle part"),
            unit="share", description="share of available time a utilisation record recovers"),
        "deployment_fee": finmodel.make_input(
            "deployment_fee", engagement.band,
            finmodel.assumed(
                f"our published band for {engagement.name.lower()}, "
                f"${engagement.band[0]:,} to ${engagement.band[1]:,}, which has not "
                f"yet been reconciled against signed work"),
            unit="$", description=f"the one-off cost of {engagement.name.lower()}"),
        "wage_escalation": escalation_input(macro_claim, macro_path),
        "discount_rate": finmodel.make_input(
            "discount_rate", finmodel.Interval.of(0.12),
            finmodel.assumed("a twelve percent cost of capital"),
            unit="a year", description="the discount rate"),
    }

    formulas = {
        "annual_capital_charge": finmodel.div(
            finmodel.ref("capital_deployed"), finmodel.ref("asset_life_years")),
        "capital_at_work": finmodel.mul(
            finmodel.ref("annual_capital_charge"), finmodel.ref("utilisation_today")),
        # Written as a product rather than as charge MINUS at-work, and the
        # difference is not cosmetic. The two quantities are correlated — one is
        # derived from the other — and interval subtraction assumes they are
        # not, so the worst case paired the smallest charge with the largest
        # at-work figure and returned MINUS $1,694 of idle capital. A negative
        # idle figure is not a conservative reading, it is arithmetic reporting
        # a pairing that cannot occur.
        "idle_capital_a_year": finmodel.mul(
            finmodel.ref("annual_capital_charge"),
            finmodel.sub(finmodel.const(1, "the whole of the available time"),
                         finmodel.ref("utilisation_today"))),
        "annual_saving": finmodel.mul(
            finmodel.ref("annual_capital_charge"), finmodel.ref("utilisation_gain")),
        "monthly_saving": finmodel.monthlyise(finmodel.ref("annual_saving")),
    }

    return finmodel.ModelSpec(
        model_id=f"{_slug(company)}.{CAPITAL_PATTERN}.{engagement_key}",
        title=f"{CAPITAL_UNIT.build.capitalize()} for {company}",
        inputs=inputs,
        formulas=formulas,
        scenarios={
            name: {
                "utilisation_today": thirds(*UTILISATION_TODAY)[name],
                "utilisation_gain": thirds(*UTILISATION_GAIN)[name],
            }
            for name in finmodel.SCENARIOS
        },
        roles=finmodel.Roles(
            investment="deployment_fee",
            monthly_saving="monthly_saving",
            annual_saving="annual_saving",
            escalating_cost="annual_capital_charge",
            escalation_rate="wage_escalation",
            discount_rate="discount_rate",
        ),
        horizon_months=horizon_months,
        units={
            "annual_capital_charge": "$", "capital_at_work": "$",
            "idle_capital_a_year": "$", "annual_saving": "$", "monthly_saving": "$",
        },
        notes=[
            pricing.caveat_for([engagement_key]),
            f"The metric a gain share would be written against: {CAPITAL_UNIT.metric}.",
            f"${capital:,.0f} is a FLOOR on capital deployed, not a total: the "
            f"disclosure states the contribution rather than the project cost.",
            "This model contains no wage and no volume. It is not a headcount "
            "estimate wearing a different name; it costs an asset.",
        ],
    )


SENSITIVITY_TARGETS: tuple[tuple[str, int], ...] = (
    ("minutes_per_unit", 12),
    ("volume_per_month", 12),
)
"""What we solve for, and against what.

Twelve months because that is the horizon an owner will hold in their head, and
these two inputs because they are the two the prospect knows exactly and we do
not know at all. The sensitivity sentence is the one that turns the whole model
into a question worth asking on the call."""


def run_for(
    pattern_key: str,
    prospect: dict[str, Any],
    engagement_key: str,
    macro_claim: dict[str, Any] | None = None,
    macro_path: str = "",
    anchor: Any = None,
) -> finmodel.ModelReport:
    """Build and evaluate one approach's model, every way its roles support."""
    spec = build_spec(
        pattern_key, prospect, engagement_key, macro_claim, macro_path, anchor=anchor)
    unit = BY_PATTERN[pattern_key]
    targets = (CAPITAL_SENSITIVITY if unit.anchors_on == "capital"
               else SENSITIVITY_TARGETS)
    return finmodel.run(spec, sensitivity_targets=targets)
