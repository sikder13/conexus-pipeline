"""Everything assembled for one company's analysis, with the numbers already done.

WHY THE ASSEMBLY IS ITS OWN MODULE

The analyst used to hand a generator the evidence, the peer table and a library
of arithmetic templates, and ask it to do the sums. That is the wrong division of
labour twice over. A language model is the best thing available for reading an
evidence file and the worst thing available for multiplying two ranges together,
and the second half is the half a reader will repeat on a phone call.

So the sums move in front of the generator rather than behind it. This module
builds the case file: the models evaluated, the rivals compared, the macro series
read, the offer tier chosen, and — the part that makes the rest enforceable —
`traceable_figures`, the complete set of numbers the finished document is allowed
to contain.

WHAT THE GENERATOR IS LEFT WITH, AND WHY THAT IS THE INTERESTING PART

Reading. Which of the frictions this evidence actually shows. Which approach
answers it. How to say a computed range in a sentence an operator can repeat.
Whether the three approaches are three approaches. Those are judgements, and
they are the ones worth a model's attention — arithmetic never was.

THE FIGURE SET IS THE GATE

`traceable_figures` is what makes "the model never invents a number" a check
rather than an instruction. Every figure in the finished prose has to match
something in here: an output of an evaluated model, a number in one of their own
claims, a quotable benchmark, a count from the peer table or the rival
comparison, a published macro series, or a band from the engagement ladder. A
figure that matches nothing is a rejection, and the rejection names the figure.
"""

from __future__ import annotations

import re
from math import floor, log10
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lib import benchmarks, finmodel, macro, numerals, offermodels, peers, pricing, rivals
from lib.evidence import BLOCK10_COMPETITORS, FLAGS_KEY, read_block
from lib.integrity import is_usable, iter_all_claims

TOLERANCE = 0.02
"""How far a written figure may sit from the computed one and still be it.

Two per cent. Prose says "about $45,000" for a computed $45,419 and it should,
because $45,419 in a sentence is a false precision that invites a reader to
believe the input was precise. The tolerance is what lets the writing round
without letting it invent."""

MAX_APPROACH_MODELS = 3
"""One model per approach, and there are three approaches."""


# --------------------------------------------------------- number traceability


def _significant(value: float, digits: int) -> float:
    if value == 0:
        return 0.0
    exponent = digits - 1 - floor(log10(abs(value)))
    return round(value, int(exponent))


def traceable(written: float, figures: set[float]) -> bool:
    """Whether a figure in the prose is one the case file actually produced.

    Matched numerically rather than as text, because the same quantity is
    legitimately written several ways: a share of 0.35 is written "35%", a
    computed $45,419 is written "about $45,000", and a payback crossing in month
    3 is written "three months". A string comparison would refuse all three and
    teach the writing to quote raw floats, which is worse prose and no safer.
    """
    for candidate in figures:
        for form in (candidate, candidate * 100, candidate / 100):
            if abs(written - form) <= max(0.5, TOLERANCE * abs(form)):
                return True
            # Two significant digits and up, never one. Rounding $45,419 to
            # "$45,000" is good writing; rounding it to "$50,000" is a ten per
            # cent overstatement, and the reader repeats it as if we measured it.
            for digits in (2, 3):
                if _significant(form, digits) == written:
                    return True
    return False


def untraceable_figures(
    text: str, figures: set[float], limit: int = 6
) -> list[tuple[str, str]]:
    """Every quantity in the prose that nothing in the case file produced.

    Returned with the sentence it sits in, because the writer needs to see where
    it came from to cut it, and the retry is handed these verbatim.

    The honest limit of this check: it is a union with no sense of context. A
    figure that happens to equal one of our price bands passes anywhere in the
    document, including in a sentence about pick volumes. It catches invented
    numbers, not numbers used in the wrong place — and the second is a judgement
    a reader makes, which is what the citations beside every figure are for.
    """
    found: list[tuple[str, str]] = []
    for raw in re.split(r"(?<=[.!?])\s+", text or ""):
        sentence = raw.strip()
        if not sentence:
            continue
        for numeral in numerals.quantities(sentence):
            if numeral.value is None or traceable(numeral.value, figures):
                continue
            found.append((numeral.text.strip(), sentence[:160]))
            if len(found) >= limit:
                return found
    return found


def claim_figures(claims: list[tuple[str, dict[str, Any]]]) -> set[float]:
    """Every number in the company's own record. Their figures are always sayable."""
    out: set[float] = set()
    for _path, claim in claims or []:
        for numeral in numerals.classify(str(claim.get("value") or "")):
            if numeral.value is not None:
                out.add(numeral.value)
    return out


def ladder_figures() -> set[float]:
    """Every band and duration on the engagement ladder."""
    out: set[float] = set()
    for engagement in pricing.LADDER:
        out |= {float(engagement.band[0]), float(engagement.band[1]),
                float(engagement.weeks[0]), float(engagement.weeks[1])}
    out.add(float(pricing.GAIN_SHARE_PERCENT))
    out.add(pricing.GAIN_SHARE_CAP_MULTIPLE)
    out.add(float(pricing.GAIN_SHARE_TERM_MONTHS))
    return out


def peer_figures(positions: list[peers.Position], group: peers.PeerGroup) -> set[float]:
    """Every figure the peer table computed, which the standing section reads from.

    All three columns, not just the headline. The subject's own value is where
    the peer table puts a figure like "about $300,000 counting the money they
    had to match" — a number we computed from their award record and doubled for
    the required match. Reading only the headline refused an analysis for quoting
    the peer table's own middle column back at it.
    """
    out: set[float] = {float(group.size_of_group)}
    for position in positions:
        measured = getattr(position, "peers_measured", None)
        if measured is not None:
            out.add(float(measured))
        for field in ("headline", "subject_value", "basis"):
            for numeral in numerals.classify(str(getattr(position, field, ""))):
                if numeral.value is not None:
                    out.add(numeral.value)
    return out


# ------------------------------------------------------------------ the file


class ApproachModel(BaseModel):
    """One approach, its engagement shape, and the arithmetic behind it."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    pattern_key: str
    engagement_key: str
    report: finmodel.ModelReport

    @property
    def unit(self) -> offermodels.WorkUnit:
        return offermodels.BY_PATTERN[self.pattern_key]

    @property
    def engagement(self) -> pricing.Engagement:
        return pricing.BY_KEY[self.engagement_key]


class CaseFile(BaseModel):
    """One company, everything computed about it, and what may be said."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    company: str
    tier: pricing.Tier
    models: list[ApproachModel] = Field(default_factory=list)
    gain_share: pricing.GainShare | None = None
    gain_share_reason: str = ""
    gap_table: rivals.GapTable | None = None
    headwind: macro.Headwind | None = None
    tiebreakers: dict[str, Any] = Field(default_factory=dict)
    extra_figures: set[float] = Field(default_factory=set)
    adapter: str | None = None
    """Which source this company came from. Decides the currency quotes render
    in, and nothing else — the numerals are the same in both markets."""

    def traceable_figures(self) -> set[float]:
        """Every number this document is allowed to contain."""
        out: set[float] = set(self.extra_figures)
        out |= ladder_figures()
        out |= benchmarks.figures()
        for model in self.models:
            out |= model.report.traceable_figures()
        if self.gap_table is not None:
            out |= self.gap_table.figures()
        if self.headwind is not None:
            out |= self.headwind.figures()
        if self.gain_share is not None:
            out.add(float(self.gain_share.deployment_fee))
            out |= {float(v) for v in self.gain_share.cap}
            out.add(float(self.gain_share.share_percent))
            out.add(float(self.gain_share.term_months))
        return out

    def assumptions(self) -> list[finmodel.Provenance]:
        seen: dict[str, finmodel.Provenance] = {}
        for model in self.models:
            for source in model.report.assumptions():
                seen.setdefault(source.label, source)
        return list(seen.values())

    def benchmark_ids(self) -> set[str]:
        out: set[str] = set()
        for model in self.models:
            out |= model.report.benchmark_ids()
        return out

    def citations(self) -> list[str]:
        return benchmarks.citations_for(self.benchmark_ids())


# ------------------------------------------------------------------ assembly


def read_tiebreakers(prospect: dict[str, Any]) -> dict[str, Any]:
    """The signals recorded for ranking but never weighted.

    Recorded here rather than in the score because noticing something and
    changing what a score means are separate decisions, and doing both in one
    commit is how a scale stops being comparable with itself.
    """
    evidence = prospect.get("evidence_file") or {}
    hiring_flags = (read_block(evidence, "block3_hiring_signals").get(FLAGS_KEY) or {})
    data_role = hiring_flags.get("data_role_posting") or {}
    systems = [
        str(claim.get("value"))
        for path, claim in iter_all_claims(evidence)
        if "systems_named_in_postings" in path and is_usable(claim)
    ]
    return {
        "data_role_posting": bool(data_role.get("value")),
        "data_role_titles": list(data_role.get("matched_roles") or []),
        "named_systems": sorted(set(systems)),
        "published_contacts": _published_contacts(prospect),
    }


def _published_contacts(prospect: dict[str, Any]) -> int:
    front = read_block(prospect.get("evidence_file") or {}, "block4_digital_front_door")
    total = 0
    for key in ("published_emails", "published_phones"):
        found = front.get(key)
        if isinstance(found, list):
            total += sum(1 for c in found if isinstance(c, dict) and is_usable(c))
    return total


def read_gap_table(prospect: dict[str, Any]) -> rivals.GapTable | None:
    """The rival comparison the harvester stored, rebuilt into its table.

    Rebuilt rather than stored as prose, because the counts in a velocity
    sentence are computed from the observations and a stored sentence would
    freeze a denominator the next rival read should change.
    """
    from tools.harvester.nodes.rival_scan import RIVAL_SCAN_KEY

    block = read_block(prospect.get("evidence_file") or {}, BLOCK10_COMPETITORS)
    stored = block.get(RIVAL_SCAN_KEY)
    if not isinstance(stored, dict) or not stored.get("rivals"):
        return None
    observations = [rivals.RivalObservation(**row) for row in stored["rivals"]]
    subject_row = stored.get("subject")
    subject = rivals.RivalObservation(**subject_row) if subject_row else None
    return rivals.build_gap_table(
        str(prospect.get("company_name") or ""), subject, observations,
        shortfall=str(stored.get("shortfall") or ""),
    )


def choose_patterns(prospect: dict[str, Any], evidence_text: str) -> list[str]:
    """Which arithmetic the evidence actually supports, best first.

    Falls back to the quoting model only when nothing matched, because every
    manufacturer quotes and it is the one friction that needs no special
    evidence to be plausible — but a fallback is recorded as one so a reader can
    see that the choice was ours rather than theirs.
    """
    from lib.roi_patterns import applicable

    matched = [key for key in applicable(evidence_text) if key in offermodels.BY_PATTERN]
    if matched:
        return matched
    return ["quoting_velocity"]


def engagements_for(tier: pricing.Tier, count: int = MAX_APPROACH_MODELS) -> list[str]:
    """The engagement shapes an offer set at this tier draws on, lead first."""
    order = [tier.lead] + [k for k in tier.allowed if k != tier.lead]
    return order[:count]


def build(
    prospect: dict[str, Any],
    evidence_text: str,
    positions: list[peers.Position] | None = None,
    group: peers.PeerGroup | None = None,
    claims: list[tuple[str, dict[str, Any]]] | None = None,
    macro_results: list[macro.SeriesResult] | None = None,
) -> CaseFile:
    """Assemble everything one analysis needs, with the arithmetic already run."""
    company = str(prospect.get("company_name") or "this company")
    tier = pricing.tier_for(prospect.get("size_band"))
    patterns = choose_patterns(prospect, evidence_text)
    shapes = engagements_for(tier)

    headwind = macro.headwind(macro_results or []) if macro_results is not None else None
    macro_claim, macro_path = _escalation_claim(macro_results or [])

    models: list[ApproachModel] = []
    for index, engagement_key in enumerate(shapes):
        pattern_key = patterns[index % len(patterns)]
        models.append(ApproachModel(
            pattern_key=pattern_key,
            engagement_key=engagement_key,
            report=offermodels.run_for(
                pattern_key, prospect, engagement_key, macro_claim, macro_path),
        ))

    tiebreakers = read_tiebreakers(prospect)
    gain_share, reason = _gain_share(
        models, tiebreakers, prospect.get("source_adapter"))

    extra = claim_figures(claims or [])
    if positions is not None and group is not None:
        extra |= peer_figures(positions, group)

    return CaseFile(
        company=company, tier=tier, models=models,
        gain_share=gain_share, gain_share_reason=reason,
        gap_table=read_gap_table(prospect), headwind=headwind,
        tiebreakers=tiebreakers, extra_figures=extra,
        adapter=prospect.get("source_adapter"),
    )


def _escalation_claim(results: list[macro.SeriesResult]) -> tuple[dict[str, Any] | None, str]:
    """The published wage-escalation reading, if one came back."""
    for result in results:
        if result.definition.reading == "rate" and result.available:
            return macro.as_claim(result), result.definition.series_id
    return None, ""


def _gain_share(
    models: list[ApproachModel], tiebreakers: dict[str, Any],
    adapter: str | None = None,
) -> tuple[pricing.GainShare | None, str]:
    """The gain-share variant, or plainly why it is not on the table.

    Two conditions and both are operational rather than commercial: there has to
    be one metric worth agreeing on, and a system that produces it. Without the
    second, the baseline is somebody's recollection and the share is computed
    from a memory — which is a worse deal for the prospect than a fixed price and
    reads as a better one.
    """
    if not models:
        return None, "no model, so no metric to write a share against"
    lead = models[0]
    metric = lead.unit.metric
    has_system = bool(tiebreakers.get("named_systems"))
    if not pricing.instrumentable(metric, has_system):
        return None, (
            "no business system is named anywhere in their evidence, so a "
            "baseline could only be measured by asking people how long things "
            "take — which is not a baseline, and a share computed from one is a "
            "share computed from a memory"
        )
    return pricing.gain_share_for(lead.engagement_key, metric, adapter), (
        f"a baseline can be instrumented: their postings name "
        f"{', '.join(tiebreakers['named_systems'][:3])}"
    )


# ------------------------------------------------------------- prompt blocks


def _range_words(interval: finmodel.Interval, unit: str = "") -> str:
    def one(value: float) -> str:
        if unit == "$":
            return f"${value:,.0f}"
        if unit in ("share", "a year"):
            return f"{value * 100:.0f}%"
        return f"{value:,.4g}"

    tail = f" {unit}" if unit and unit not in ("$", "share", "a year") else ""
    if interval.is_point:
        return one(interval.low) + tail
    return f"{one(interval.low)}-{one(interval.high)}{tail}"


REPORTED_OUTPUTS: tuple[tuple[str, str], ...] = (
    ("hours_per_month", "hours a month the work takes today"),
    ("annual_labour_cost", "what that costs a year, fully loaded"),
    ("hours_returned_a_month", "hours a month a build gives back"),
    ("monthly_saving", "what those hours are worth a month"),
    ("annual_saving", "and a year"),
)


def model_block(model: ApproachModel) -> str:
    """One approach's arithmetic, written out for the generator to narrate."""
    report = model.report
    spec = report.spec
    lines = [
        f"### APPROACH USING {model.engagement.name.upper()} "
        f"(${model.engagement.band[0]:,}-${model.engagement.band[1]:,}, "
        f"{model.engagement.weeks[0]}-{model.engagement.weeks[1]} weeks)",
        f"What is built: {model.unit.build}.",
        f"Model id: {spec.model_id}",
        "",
        "INPUTS, and where each came from:",
    ]
    for model_input in spec.inputs.values():
        lines.append(
            f"  - {model_input.in_words}: "
            f"{_range_words(model_input.value, model_input.unit)} "
            f"[{model_input.provenance.describe()}]"
        )
    lines.append("")
    lines.append("COMPUTED. These are the only figures you may use for this approach:")
    for scenario, evaluated in report.scenarios.items():
        lines.append(f"  {scenario.upper()} reading:")
        for key, words in REPORTED_OUTPUTS:
            if key in evaluated.values:
                lines.append(
                    f"    - {words}: "
                    f"{_range_words(evaluated.value(key), spec.unit_of(key))}")
        payback = report.payback.get(scenario)
        if payback is not None:
            lines.append(f"    - payback: {payback.describe()}")
    collapse = report.collapse.get(finmodel.TARGET)
    if collapse is not None:
        lines.append(f"  Capacity: {collapse.describe()}")
    for item in report.sensitivities:
        lines.append(f"  Sensitivity: {item.describe()}")
    discount = report.discounting.get(finmodel.TARGET)
    if discount is not None:
        lines.append(f"  Discounting: {discount.describe()}")
    return "\n".join(lines)


def prompt_block(case: CaseFile) -> str:
    """The whole case file, as the section of the prompt that carries the numbers."""
    parts = [
        "THE ARITHMETIC IS ALREADY DONE.\n"
        "Every figure below was computed from named inputs, each of which says "
        "where it came from. Your job is to decide which of these findings "
        "matters for this company, to choose and describe three genuinely "
        "different approaches, and to narrate the computed figures in sentences "
        "an operator can repeat on a call — naming the inputs in words as you "
        "go, so the reader knows what the number depends on.\n\n"
        "YOU MAY NOT COMPUTE. Do not multiply, add, extrapolate, annualise or "
        "re-round anything. A figure that is not below, not in the evidence, and "
        "not in the benchmark list will be rejected by name.",
        f"WHO THIS IS WRITTEN FOR: {case.tier.audience}.\n"
        f"HOW TO PITCH IT: {case.tier.framing}\n"
        f"THE MONEY SHAPE AT THIS SIZE: {case.tier.money_words}.",
        f"POSITIONING: {pricing.POSITIONING}",
    ]
    parts.extend(model_block(model) for model in case.models)

    if case.gain_share is not None:
        share = case.gain_share
        parts.append(
            "GAIN SHARE IS AVAILABLE for one of the three approaches, and if you "
            "offer it you must state its conditions.\n"
            f"Shape: {share.words()}.\n"
            f"Why it is possible here: {case.gain_share_reason}.\n"
            "It requires all of: " + "; ".join(share.requirements) + "."
        )
    else:
        parts.append(
            "GAIN SHARE IS NOT AVAILABLE for this company and must not be "
            f"offered. Reason: {case.gain_share_reason}"
        )

    care = pricing.BY_KEY["care_plan"]
    parts.append(
        f"CARE PLAN, an ADD-ON LINE beside the three approaches and never one "
        f"of them: ${care.band[0]:,}-${care.band[1]:,} "
        f"{pricing.currency_for(case.adapter)} a month after a build is "
        f"delivered — {pricing.FRAMING}. {care.shape} Offer it as one sentence "
        f"at the end of the lead recommendation, and only there."
    )

    if case.gap_table is not None and case.gap_table.usable:
        lines = [case.gap_table.basis]
        lines += [f"  - {line.sentence}" for line in case.gap_table.velocity()]
        lines += [f"  - {line.sentence}" for line in case.gap_table.scarcity()]
        if case.gap_table.shortfall:
            lines.append(f"  Caveat: {case.gap_table.shortfall}")
        parts.append(
            "NAMED RIVALS, read off their own sites. Use these sentences as "
            "written or not at all; the counts are ours and the denominators "
            "matter.\n" + "\n".join(lines))

    if case.headwind is not None and case.headwind.configured:
        parts.append(
            f"MACRO, over {case.headwind.window_months} months, from published "
            f"series only. Use these sentences or none.\n"
            + "\n".join(f"  - {line.sentence}" for line in case.headwind.lines))

    if case.tiebreakers.get("named_systems"):
        parts.append(
            "SYSTEMS THEY NAME IN THEIR OWN POSTINGS: "
            + ", ".join(case.tiebreakers["named_systems"])
            + ". These are the integration surface a build would meet.")
    if case.tiebreakers.get("data_role_posting"):
        parts.append(
            "THEY ARE HIRING AN ANALYST: "
            + ", ".join(case.tiebreakers.get("data_role_titles") or ["an analyst role"])
            + ". They have already decided their own numbers deserve somebody's "
            "attention, so the conversation is shorter than usual.")

    if case.citations():
        parts.append(
            "BENCHMARK CITATIONS. Where you use one, attribute it in the "
            "sentence.\n" + "\n".join(f"  - {c}" for c in case.citations()))
    return "\n\n".join(parts)
