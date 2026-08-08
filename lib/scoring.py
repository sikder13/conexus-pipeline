"""Signal scoring and priority assignment for prospects.

Pure functions only — no network, no database, no clock. Scoring has to be
reproducible and unit-testable, because the score decides who gets human
research time, and we want to be able to re-run it over historical records
when the weights are recalibrated.

Component weights live in ``COMPONENT_WEIGHTS`` and nowhere else. The database
stores the per-component breakdown rather than just the total (see the
``score_breakdown`` column in migration 001) precisely so that a future
recalibration from outcome data can re-total every existing prospect without
re-researching anybody.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

COMPONENT_WEIGHTS: dict[str, int] = {
    "clerical_posting": 1,
    "data_gen_tech": 1,
    "case_study": 1,
    "weak_front_door": 1,
    "friction_reviews": 1,
    "decision_maker_found": 1,
    "in_drive_radius": 1,
    "too_big": -1,
    "status_uncertain": -1,
}
"""Weight per signal component. The single place to recalibrate from outcome data.

Keys match the ``score_breakdown`` JSONB shape documented in migration 001.
Changing a weight here changes scoring everywhere; it does not require touching
any logic, and stored breakdowns can be re-totalled against the new weights.
"""

JOB_POSTING_MAX_AGE_DAYS = 60
"""A clerical or coordination posting only counts if dated within this window."""

DATA_GENERATING_TECH_TERMS: tuple[str, ...] = (
    "IIoT",
    "vision",
    "robotics",
    "automated line",
    "sensors",
    "batching",
    "monitoring",
)
"""Reference list of technologies whose presence in a grant description implies
the company is now generating machine data it probably is not yet using."""

EMPLOYEE_CEILING = 250
"""Above this headcount the prospect is out of ICP and the score is penalised."""

MAX_DRIVE_MINUTES = 90
"""Drive time from Muncie, Indiana at or under which an in-person visit is practical."""

P1_MIN_SCORE = 4
"""Minimum score for P1 — and P1 additionally requires a named decision-maker."""

P2_MIN_SCORE = 2
"""Minimum score for P2."""


class SignalInputs(BaseModel):
    """The nine observed signals for one prospect, one field per component.

    Every field is a decision already made by a researcher or a tool from
    evidence. Nothing here is inferred at scoring time.
    """

    model_config = ConfigDict(frozen=True)

    clerical_posting: bool = Field(
        default=False,
        description=f"An active clerical or coordination job posting dated within "
        f"{JOB_POSTING_MAX_AGE_DAYS} days.",
    )
    data_gen_tech: bool = Field(
        default=False,
        description="The grant description names data-generating technology "
        "(see DATA_GENERATING_TECH_TERMS).",
    )
    case_study: bool = Field(
        default=False, description="A Conexus case-study subpage exists for the company."
    )
    weak_front_door: bool = Field(
        default=False, description="Weak digital front door (thin, stale, or broken web presence)."
    )
    friction_reviews: bool = Field(
        default=False, description="Customer-friction quotes found in public reviews."
    )
    decision_maker_found: bool = Field(
        default=False, description="A named decision-maker is locatable with a contact path."
    )
    in_drive_radius: bool = Field(
        default=False,
        description=f"Drive time from Muncie, Indiana is {MAX_DRIVE_MINUTES} minutes or less.",
    )
    too_big: bool = Field(
        default=False,
        description=f"More than {EMPLOYEE_CEILING} employees, or clear enterprise ownership.",
    )
    status_uncertain: bool = Field(
        default=False,
        description="Business status uncertain (possibly closed, merged, or dormant).",
    )


class ScoreResult(BaseModel):
    """A total score plus the per-component breakdown written to the database."""

    model_config = ConfigDict(frozen=True)

    total: int
    breakdown: dict[str, int]


def compute_score(signals: SignalInputs) -> ScoreResult:
    """Score a prospect from its observed signals.

    The breakdown carries every component, including the ones that scored zero,
    so a stored record shows what was checked and not merely what fired.
    """
    breakdown = {
        component: (weight if getattr(signals, component) else 0)
        for component, weight in COMPONENT_WEIGHTS.items()
    }
    return ScoreResult(total=sum(breakdown.values()), breakdown=breakdown)


def assign_priority(score: int, has_named_decision_maker: bool) -> str:
    """Map a score to 'P1', 'P2' or 'P3'.

    P1 is reserved for prospects we can actually start a conversation with:
    a strong score is not enough without a named decision-maker and a contact
    path, because there is nobody to send the work to. A high-scoring prospect
    with no named contact therefore lands in P2 — worth the research time it
    takes to find the human, but not yet worth outreach effort.
    """
    if score >= P1_MIN_SCORE and has_named_decision_maker:
        return "P1"
    if score >= P2_MIN_SCORE:
        return "P2"
    return "P3"
