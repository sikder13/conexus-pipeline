"""The benchmark library — outside numbers, each carrying the citation for it.

WHY THIS MODULE EXISTS, AND WHY IT ALMOST DID NOT

`docs/ANALYSIS.md` says plainly that a benchmark from an outside report is "a
number we cannot defend, cannot date, and cannot let a prospect correct", and
that is why the peer table is computed from our own dataset instead. That
argument is still right about the thing it was aimed at: a figure that arrives
in a document as "industry data suggests" has no source, no date and no scope,
and it is worse than no figure at all because it sounds like knowledge.

What changed is not the standard. It is that some outside numbers do meet it.
The Bureau of Labor Statistics publishes what an hour of manufacturing labour
actually costs an employer, quarterly, with a reference period and a methodology.
That is a government record, it is Tier 1, and refusing to use it while
publishing our own guess about the same quantity would be the worse of the two
choices.

So the rule here is the rule everywhere else in this pipeline: **a number ships
with its source or it does not ship.** Every entry carries the value or range,
who published it, the URL a reader can open, the date we retrieved it, how the
figure was produced including sample size, and the scope it is good for. An
entry missing any of those does not exist, because there is nowhere to put it.

TIERS APPLY HERE EXACTLY AS THEY APPLY TO CLAIMS

CLAUDE.md rule 6 is about provenance, not about whether a fact came from a
prospect. So a benchmark is tiered like a claim and constrained like one:

* **T1** — a government record. Assertable as fact.
* **T2** — a named published work or reputable secondary press. Assertable
  **with the attribution in the sentence**.
* **T3** — an aggregator's estimate. **Never assertable.** It may shape a
  scenario or set a plausible range internally; it may not be printed as a
  figure in front of anybody, and `assert_quotable` raises if a caller tries.

That last one is doing real work rather than decorating. The segment-specific
OEE numbers are exactly the kind of figure that circulates as fact, and the best
source available for them is a vendor's aggregation of four other vendors'
aggregations. They ship — because an operator setting a scenario range is better
off with them than with nothing — and they ship barred from the page.

WHAT DID NOT SHIP, AND WHY THAT IS THE POINT

A world-class OEE figure for metal fabrication specifically. It is quoted widely
at 75-82%, and nothing found in this pass supports it: the largest sampled study
located puts the metal fabrication 75th percentile at 71.2%, which is a different
claim. So there is no entry for it. A benchmark library that contains everything
anybody has said is a folklore library, and the value of this one is what it
leaves out.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from lib import finmodel
from lib.claims import Tier, _validate_source_url


class BenchmarkError(RuntimeError):
    """A benchmark cannot be used the way a caller asked to use it."""


class UnknownBenchmark(BenchmarkError):
    """No benchmark by that id. Ids are stable and analyses cite them."""


class NotAssertable(BenchmarkError):
    """A T3 or T4 benchmark was asked to appear as a figure in a document."""


class Benchmark(BaseModel):
    """One outside number, with everything a reader needs to disagree with it."""

    model_config = ConfigDict(frozen=True)

    benchmark_id: str
    label: str
    """How the figure is named in prose."""

    value: finmodel.Interval
    unit: str = ""
    tier: int
    applies_to: str
    """The scope the figure is good for. Read this before using it anywhere."""

    publisher: str
    source_title: str
    source_url: str
    retrieved: str
    """YYYY-MM-DD, the day we opened the page and read this figure off it."""

    method: str
    """How the number was produced — sample, period, what was measured."""

    caution: str = ""
    """What it must NOT be used for. Empty only when there is genuinely nothing."""

    @model_validator(mode="after")
    def _complete(self) -> Benchmark:
        _validate_source_url(self.source_url)
        if self.tier not in (1, 2, 3, 4):
            raise BenchmarkError(f"{self.benchmark_id}: tier must be 1-4")
        try:
            date.fromisoformat(self.retrieved)
        except ValueError as exc:
            raise BenchmarkError(
                f"{self.benchmark_id}: retrieved must be YYYY-MM-DD"
            ) from exc
        for field in ("label", "applies_to", "publisher", "source_title", "method"):
            if not getattr(self, field).strip():
                raise BenchmarkError(
                    f"{self.benchmark_id}: {field} is required — a number without "
                    f"one is a folklore constant, and those do not ship"
                )
        return self

    @property
    def assertable(self) -> bool:
        """True only for T1 and T2, and T2 needs its attribution in the sentence."""
        return self.tier in (int(Tier.T1), int(Tier.T2))

    @property
    def needs_attribution(self) -> bool:
        return self.tier == int(Tier.T2)

    def citation(self) -> str:
        """The line the renderer prints under any figure that used this."""
        span = (
            f"{_number(self.value.low)}"
            if self.value.is_point
            else f"{_number(self.value.low)}-{_number(self.value.high)}"
        )
        unit = f" {self.unit}" if self.unit else ""
        return (
            f"{self.label}: {span}{unit} — {self.publisher}, {self.source_title} "
            f"({self.source_url}), read {self.retrieved}. {self.method} "
            f"Applies to: {self.applies_to}."
            + (f" Caution: {self.caution}" if self.caution else "")
        )

    def in_words(self) -> str:
        """A short attribution phrase for use inside a sentence."""
        return f"{self.publisher}'s {self.source_title}"


def _number(value: float) -> str:
    if 0 < abs(value) < 1:
        return f"{value:.3g}"
    return f"{value:,.4g}"


BLS_ECEC = (
    "https://www.bls.gov/news.release/archives/ecec_09092026.htm"
)
"""The dated release rather than the rolling one.

`/news.release/ecec.htm` always shows the newest quarter, so a claim citing it
would silently come to mean something else three months from now. The archive
URL is the one a reader can open and find the same numbers we read."""


REGISTRY: tuple[Benchmark, ...] = (
    Benchmark(
        benchmark_id="labour.loaded_multiplier.us_manufacturing",
        label="fully loaded manufacturing labour, as a multiple of wages",
        value=finmodel.Interval.of(1.496),
        unit="x wages",
        tier=int(Tier.T1),
        applies_to=(
            "United States manufacturing employers, employer compensation only"
        ),
        publisher="U.S. Bureau of Labor Statistics",
        source_title="Employer Costs for Employee Compensation, June 2026",
        source_url=BLS_ECEC,
        retrieved="2026-09-09",
        method=(
            "Total compensation for manufacturing averaged $48.62 an hour worked "
            "against $32.50 in wages and salaries and $16.12 in benefits; the "
            "multiplier is the first divided by the second. National survey, "
            "reference period June 2026, released 9 September 2026."
        ),
        caution=(
            "employer compensation only. It does NOT include equipment, "
            "software, floor space, supervision or recruitment, so a shop's own "
            "loaded rate is higher than this and this is a floor, not an estimate"
        ),
    ),
    Benchmark(
        benchmark_id="labour.loaded_multiplier.us_private",
        label="fully loaded private-sector labour, as a multiple of wages",
        value=finmodel.Interval.of(1.429),
        unit="x wages",
        tier=int(Tier.T1),
        applies_to="all United States private industry, employer compensation only",
        publisher="U.S. Bureau of Labor Statistics",
        source_title="Employer Costs for Employee Compensation, June 2026",
        source_url=BLS_ECEC,
        retrieved="2026-09-09",
        method=(
            "Total compensation for private industry averaged $46.89 an hour "
            "worked against $32.82 in wages and salaries and $14.07 in benefits. "
            "Reference period June 2026, released 9 September 2026."
        ),
        caution=(
            "use the manufacturing figure for a manufacturer; this one is here "
            "for an office function that is not on the shop floor"
        ),
    ),
    Benchmark(
        benchmark_id="labour.benefit_share.us_manufacturing",
        label="benefits as a share of manufacturing compensation",
        value=finmodel.Interval.of(0.331),
        unit="share of total compensation",
        tier=int(Tier.T1),
        applies_to="United States manufacturing employers",
        publisher="U.S. Bureau of Labor Statistics",
        source_title="Employer Costs for Employee Compensation, June 2026",
        source_url=BLS_ECEC,
        retrieved="2026-09-09",
        method=(
            "$16.12 of $48.62 total hourly compensation, reference period "
            "June 2026."
        ),
        caution=(
            "a national average across all manufacturing occupations; a single "
            "shop's mix will differ"
        ),
    ),
    Benchmark(
        benchmark_id="oee.world_class.nakajima",
        label="world-class overall equipment effectiveness",
        value=finmodel.Interval.of(0.85),
        unit="OEE",
        tier=int(Tier.T2),
        applies_to=(
            "the TPM tradition, originally continuous single-product lines; it is "
            "a target set from award-winning Japanese plants, not a survey average"
        ),
        publisher="Seiichi Nakajima",
        source_title="Introduction to TPM: Total Productive Maintenance (1988)",
        source_url=(
            "https://books.google.com/books/about/Introduction_to_TPM.html"
            "?id=XKc28H3JeUUC"
        ),
        retrieved="2026-09-09",
        method=(
            "Stated by Nakajima as the minimum a plant should aim at, composed "
            "of roughly 90% availability, 95% performance and 99.9% quality, and "
            "observed in the plants winning the JIPM Distinguished Plant Prize. "
            "Productivity Press, 1988, ISBN 0915299232, OCLC 18441684."
        ),
        caution=(
            "a definition from a named book, not a measurement of anybody's "
            "shop, and it must be attributed to Nakajima when it is used. It was "
            "set for continuous lines running one product, so quoting it at a "
            "job shop as though it were an expectation is unfair to the shop"
        ),
    ),
    Benchmark(
        benchmark_id="oee.typical.discrete_manufacturing",
        label="typical overall equipment effectiveness, discrete manufacturing",
        value=finmodel.Interval.span(0.55, 0.70),
        unit="OEE",
        tier=int(Tier.T3),
        applies_to="discrete manufacturing generally, across mixed sectors",
        publisher="Godlan",
        source_title="OEE Benchmarks by Manufacturing Industry Vertical: 2025 Data",
        source_url="https://godlan.com/oee-benchmark-industry/",
        retrieved="2026-09-09",
        method=(
            "Reported as a 66.8% average across nine sectors and 1,470+ discrete "
            "manufacturing operations for calendar 2025, aggregating figures from "
            "several other vendors' benchmark reports. The range recorded here is "
            "the commonly reported 55-70% band rather than the single average, "
            "because the underlying methodology is not published in enough detail "
            "to defend a point."
        ),
        caution=(
            "TIER 3 — an aggregator's estimate, and it may never be printed as a "
            "figure. Use it to set the ends of a scenario, nothing else"
        ),
    ),
    Benchmark(
        benchmark_id="oee.typical.metal_fabrication",
        label="typical overall equipment effectiveness, metal fabrication",
        value=finmodel.Interval.span(0.537, 0.712),
        unit="OEE",
        tier=int(Tier.T3),
        applies_to="metal fabrication and general machining shops",
        publisher="Godlan",
        source_title="OEE Benchmarks by Manufacturing Industry Vertical: 2025 Data",
        source_url="https://godlan.com/oee-benchmark-industry/",
        retrieved="2026-09-09",
        method=(
            "198 metal fabrication operations, calendar 2025: 25th percentile "
            "53.7%, median 61.9%, average 62.4%, 75th percentile 71.2%. The "
            "interquartile range is recorded rather than the average, because a "
            "spread is what the underlying data actually supports."
        ),
        caution=(
            "TIER 3 — never printed as a figure. Note also that no source found "
            "in this pass supports a world-class metal fabrication figure of "
            "75-82%; the 75th percentile here is 71.2%, which is a different "
            "claim, so no such benchmark exists in this library"
        ),
    ),
    Benchmark(
        benchmark_id="sales.lead_response.qualification_odds",
        label="odds of qualifying a web lead contacted within the hour",
        value=finmodel.Interval.of(7.0),
        unit="x, against contact an hour later",
        tier=int(Tier.T2),
        applies_to=(
            "inbound web enquiries at United States companies; it measures the "
            "odds of QUALIFYING a lead, not of winning the work"
        ),
        publisher="Harvard Business Review",
        source_title=(
            "The Short Life of Online Sales Leads, Oldroyd, McElheran and "
            "Elkington, March 2011"
        ),
        source_url="https://hbr.org/2011/03/the-short-life-of-online-sales-leads",
        retrieved="2026-09-09",
        method=(
            "2,241 United States companies audited with test web enquiries. "
            "Firms attempting contact within an hour were nearly 7 times as "
            "likely to qualify the lead as those attempting an hour later, and "
            "more than 60 times as likely as those waiting 24 hours or more."
        ),
        caution=(
            "attribute it to Harvard Business Review in the sentence, and do not "
            "let it drift into a claim about win rate or about quoted RFQ work — "
            "it is about first contact on inbound web enquiries, and applying it "
            "to a fabrication quote is an extrapolation that must be said aloud"
        ),
    ),
)


BY_ID: dict[str, Benchmark] = {b.benchmark_id: b for b in REGISTRY}

WITHHELD: tuple[tuple[str, str], ...] = (
    (
        "oee.world_class.metal_fabrication",
        "Quoted widely at 75-82%. Nothing located in the 2026-09-09 pass supports "
        "it: the largest sampled source found puts the metal fabrication 75th "
        "percentile at 71.2%, which is not the same claim. No entry ships.",
    ),
    (
        "sales.lead_response.five_minute_rule",
        "The 21x-at-five-minutes figure traces to a 2007 study published by a "
        "sales-software vendor rather than to a peer-reviewed or press source. "
        "The Harvard Business Review entry covers the same point at a tier that "
        "may actually be quoted, so the vendor figure adds risk and no reach.",
    ),
)
"""Benchmarks deliberately not shipped, and why.

Kept in the module rather than in a commit message because the next person to
want one of these numbers will look here first, and the useful answer is not
silence — it is what was searched for and what was found instead."""


def get(benchmark_id: str) -> Benchmark:
    """The benchmark with this id, or raise naming it."""
    if benchmark_id not in BY_ID:
        raise UnknownBenchmark(
            f"no benchmark {benchmark_id!r}; the library holds "
            f"{', '.join(sorted(BY_ID))}"
        )
    return BY_ID[benchmark_id]


def assert_quotable(benchmark_id: str) -> Benchmark:
    """The benchmark, if it may appear as a figure in a document. Else raise.

    T3 is an aggregator's estimate and CLAUDE.md rule 6 says it never leaves the
    building. This is where that is enforced rather than remembered.
    """
    found = get(benchmark_id)
    if not found.assertable:
        raise NotAssertable(
            f"{benchmark_id} is tier {found.tier} and may not be printed as a "
            f"figure: {found.caution or 'aggregator estimate, internal use only'}"
        )
    return found


def as_input(
    benchmark_id: str,
    name: str = "",
    description: str = "",
) -> finmodel.Input:
    """The benchmark as a named model input, carrying benchmark provenance.

    Any tier may feed a model — a T3 figure setting the ends of a scenario is
    exactly the internal use it is allowed. What the tier decides is whether the
    resulting figure may be printed, and that is checked where printing happens.
    """
    found = get(benchmark_id)
    return finmodel.make_input(
        name=name or benchmark_id.replace(".", "_"),
        value=found.value,
        provenance=finmodel.benchmark_source(
            benchmark_id, label=found.label, note=found.applies_to),
        unit=found.unit,
        description=description or found.label,
    )


def citation(benchmark_id: str) -> str:
    """The full citation line, for the renderer to print under a figure."""
    return get(benchmark_id).citation()


def citations_for(benchmark_ids: Any) -> list[str]:
    """Citation lines for every id a model report says it used, in id order."""
    return [citation(b) for b in sorted(set(benchmark_ids or ()))]


def quotable_ids() -> list[str]:
    """Every benchmark that may appear as a figure in a document."""
    return sorted(b.benchmark_id for b in REGISTRY if b.assertable)


def figures() -> set[float]:
    """Every number the library legitimately puts in front of a reader.

    Handed to the analyst's validation alongside the model outputs, so a figure
    in the prose is traceable to a model, a claim, or one of these — and to
    nothing else."""
    out: set[float] = set()
    for entry in REGISTRY:
        if not entry.assertable:
            continue
        out.add(entry.value.low)
        out.add(entry.value.high)
        # The percentage spelling of a share, because prose says 85% and the
        # library holds 0.85, and the validator compares numbers rather than
        # strings.
        out.add(entry.value.low * 100)
        out.add(entry.value.high * 100)
    return out
