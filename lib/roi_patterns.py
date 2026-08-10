"""ROI pattern library — math templates, never a pitch menu.

WHAT THIS IS AND WHY THE DISTINCTION MATTERS

Each entry is a *way of costing a problem the evidence already showed*, not a
product we sell. The difference is the whole design. A drafter handed a list of
offerings will go shopping: it finds the nearest item and writes a pitch, and
the diagnosis becomes decoration on a decision already made. A drafter handed
arithmetic has to find the problem first, because the arithmetic is useless
until something real is plugged into it.

That is why `variables` names what must be *observed* before a template can be
used, and why every template carries `refuses_when` — the conditions under which
it must not be applied at all. A template with no observed inputs produces a
number with no provenance, which is the one thing this pipeline may never emit.

HOW THE DRAFTER USES THEM

Step 1 of the thesis names frictions from evidence alone; these templates are
not in that prompt. Only step 2 sees them, and only to cost what step 1 already
found. Ranges are conditional and must state their assumptions inline: "if X is
about N, then...". The company's own T1 numbers come first — a grant match
amount is capital they have already committed and is the strongest anchor
available, far better than an industry average.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RoiPattern(BaseModel):
    """One way of putting a cost on an observed problem."""

    model_config = ConfigDict(frozen=True)

    key: str
    friction: str
    """The problem, phrased as something a person would recognise in their week."""
    variables: list[str] = Field(default_factory=list)
    """What must be OBSERVED in the evidence before this template may be used."""
    math: str
    """The arithmetic, with named variables. Ranges, never point estimates."""
    scoped_fix: str
    """A bounded two-to-four week piece of work, not a platform."""
    refuses_when: str
    """When this template must not be applied."""


PATTERNS: tuple[RoiPattern, ...] = (
    RoiPattern(
        key="quoting_velocity",
        friction=(
            "Quotes are assembled by hand from prints and past jobs, so they go out "
            "in days rather than hours and the slowest ones quietly go cold."
        ),
        variables=["quotes_per_month", "hours_per_quote", "loaded_hourly_rate",
                   "win_rate", "average_order_value"],
        math=(
            "labour = quotes_per_month * hours_per_quote * loaded_hourly_rate\n"
            "lost_orders = quotes_per_month * share_that_go_cold * win_rate\n"
            "annual = (labour * 12) + (lost_orders * average_order_value * 12)\n"
            "State share_that_go_cold as an explicit assumption with a range."
        ),
        scoped_fix=(
            "A quote assembler that pulls dimensions, material and past pricing into "
            "a draft the estimator edits rather than builds. Two to three weeks."
        ),
        refuses_when=(
            "The evidence shows no quoting or estimating activity, or the company "
            "sells a catalogue at list price."
        ),
    ),
    RoiPattern(
        key="clerical_hire_payback",
        friction=(
            "An open clerical or coordination role is really a request for hours "
            "back — order entry, scheduling, chasing paperwork."
        ),
        variables=["open_clerical_roles", "posted_salary_or_regional_median",
                   "tasks_named_in_the_posting"],
        math=(
            "annual_cost = salary * (1 + employer_burden)   # burden 0.25-0.35\n"
            "automatable_share = share of the posting's named tasks that are "
            "rule-based data movement\n"
            "payback = annual_cost * automatable_share\n"
            "Cite the posting. Never assume the role is unnecessary — the arithmetic "
            "is about which PART of it is data movement."
        ),
        scoped_fix=(
            "Automate the two or three named tasks that are pure data movement so "
            "the hire starts on the judgement work. Two weeks."
        ),
        refuses_when=(
            "No posting is in evidence, or the posted duties are physical, "
            "supervisory or customer-facing rather than clerical."
        ),
    ),
    RoiPattern(
        key="qa_documentation_labour",
        friction=(
            "Certification and customer audits are carried by people re-typing the "
            "same numbers into forms — inspection records, CoCs, PPAP packets."
        ),
        variables=["certifications_held", "shipments_or_lots_per_month",
                   "minutes_per_document", "loaded_hourly_rate"],
        math=(
            "annual_hours = lots_per_month * 12 * (minutes_per_document / 60)\n"
            "annual = annual_hours * loaded_hourly_rate\n"
            "Certifications in evidence (ISO 9001, IATF 16949, AS9100) establish that "
            "the paperwork exists and is mandatory; they do not establish its volume, "
            "so volume must be stated as an assumption."
        ),
        scoped_fix=(
            "Generate the recurring documents from the measurement data already "
            "captured, leaving sign-off human. Three to four weeks."
        ),
        refuses_when="No certification or customer-audit requirement is in evidence.",
    ),
    RoiPattern(
        key="machine_data_analysis",
        friction=(
            "New equipment emits data nobody reads. The grant bought the sensor; "
            "the reporting was never scoped."
        ),
        variables=["grant_amount", "grant_match_amount", "technology_named",
                   "year_installed"],
        math=(
            "capital_deployed = grant_amount + grant_match_amount\n"
            "The match is money THEY committed and is a T1 floor on their own "
            "investment. Frame the cost of unread data as a share of the return on "
            "capital already spent, not as a new purchase.\n"
            "annual = capital_deployed * expected_return_share * share_unmeasured\n"
            "State both shares as explicit assumptions."
        ),
        scoped_fix=(
            "A weekly one-page report off the machine's existing output — "
            "utilisation, scrap, downtime causes. Two weeks."
        ),
        refuses_when=(
            "The grant description names no data-generating technology, or the "
            "equipment is purely mechanical."
        ),
    ),
    RoiPattern(
        key="ai_search_invisibility",
        friction=(
            "Buyers increasingly ask an assistant for a supplier shortlist. A site "
            "with no readable capability text is absent from that answer, however "
            "good the shop is."
        ),
        variables=["front_door_weaknesses", "capability_text_present",
                   "structured_data_present"],
        math=(
            "This one resists a confident number and must say so. Frame as exposure, "
            "not savings: inbound_enquiries_per_year * share_originating_in_search. "
            "If neither is in evidence, present it qualitatively with the observed "
            "weaknesses cited and NO figure attached."
        ),
        scoped_fix=(
            "Rewrite the capability pages around what they actually make, with "
            "structured data a machine can read. Two weeks."
        ),
        refuses_when=(
            "The site was not readable at assessment time — a compromised or "
            "unreachable domain says nothing about their real front door."
        ),
    ),
    RoiPattern(
        key="throughput_downtime",
        friction=(
            "Unplanned stoppages are absorbed as normal because nobody has costed "
            "an hour of the constraint."
        ),
        variables=["shifts_per_day", "constraint_machine", "hourly_contribution",
                   "downtime_hours_estimate"],
        math=(
            "annual = downtime_hours_per_week * 50 * hourly_contribution_margin\n"
            "hourly_contribution_margin must come from their own figures or be "
            "stated as an assumed range. Never assert a margin."
        ),
        scoped_fix=(
            "Log stoppage reasons at the constraint for four weeks, then rank by "
            "cost. Two weeks to stand up."
        ),
        refuses_when="No production constraint or shift pattern is in evidence.",
    ),
    RoiPattern(
        key="logistics_billing_anomaly",
        friction=(
            "Freight and supplier invoices are approved on trust because checking "
            "them line by line costs more than the errors appear to."
        ),
        variables=["annual_freight_or_supplier_spend", "invoice_volume"],
        math=(
            "recoverable = annual_spend * anomaly_rate   # anomaly_rate 0.5%-2%\n"
            "State the rate as an assumption drawn from general practice and label "
            "it as such — it is not their number."
        ),
        scoped_fix=(
            "An anomaly check across twelve months of invoices, flagging duplicates, "
            "rate mismatches and accessorial creep. Two to three weeks."
        ),
        refuses_when="No spend figure or shipping activity is in evidence.",
    ),
)

BY_KEY: dict[str, RoiPattern] = {p.key: p for p in PATTERNS}


def as_prompt_block(keys: list[str] | None = None) -> str:
    """Render the templates for a prompt — arithmetic only, no selling language."""
    chosen = [BY_KEY[k] for k in keys if k in BY_KEY] if keys else list(PATTERNS)
    parts = []
    for pattern in chosen:
        parts.append(
            f"### {pattern.key}\n"
            f"Friction this costs: {pattern.friction}\n"
            f"Must be observed first: {', '.join(pattern.variables)}\n"
            f"Arithmetic:\n{pattern.math}\n"
            f"Bounded fix: {pattern.scoped_fix}\n"
            f"DO NOT USE WHEN: {pattern.refuses_when}"
        )
    return "\n\n".join(parts)


def applicable(evidence_summary: str) -> list[str]:
    """Keys whose refusal condition is not obviously triggered. Advisory only.

    A cheap pre-filter so the prompt is not padded with templates that plainly
    cannot apply. It never *selects* a pattern — the drafter must still justify
    any it uses from observed evidence, and a template surviving this filter is
    not evidence that it fits.
    """
    lowered = (evidence_summary or "").lower()
    keep = []
    for pattern in PATTERNS:
        if pattern.key == "clerical_hire_payback" and "posting" not in lowered:
            continue
        if pattern.key == "qa_documentation_labour" and not any(
            c in lowered for c in ("iso", "iatf", "as9100", "certif")
        ):
            continue
        keep.append(pattern.key)
    return keep


def as_dicts() -> list[dict[str, Any]]:
    """The library as plain data, for tests and for the docs."""
    return [p.model_dump() for p in PATTERNS]
