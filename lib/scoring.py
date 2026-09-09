"""Signal scoring and priority assignment for prospects.

Pure functions only — no network, no database, no clock. Scoring has to be
reproducible and unit-testable, because the score decides who gets human
research time, and we want to be able to re-run it over historical records
when the weights are recalibrated.

Component weights live in a PROFILE, one per source adapter, and nowhere else.
The database stores the per-component breakdown rather than just the total (see
the ``score_breakdown`` column in migration 001) precisely so that a future
recalibration from outcome data can re-total every existing prospect without
re-researching anybody.

WHY THE SCALE IS PER ADAPTER

Because half of the Indiana scale is a statement about Indiana. `in_drive_radius`
measures the drive from Muncie, which is not a fact about a company in Calgary;
`case_study` asks whether Conexus published a case study, and Conexus has never
heard of Ontario. Scoring a Canadian prospect on those two components does not
give it a low score — it gives it a meaningless one, because two of the six
things that could fire structurally cannot.

That is the same defect this file's own history records under `friction_reviews`:
a component that cannot fire is worse than an absent one, since it makes the
ceiling look higher than it is and every threshold set against that ceiling one
notch too strict. So each source gets a scale whose components can all actually
fire, and the profile travels with the prospect's `source_adapter`.

What does NOT vary by profile: the claim tiers, the integrity gate, the person
gate, the drafting floor, and the rule that a score is computed only from
evidence that passed integrity. Those are about whether a fact may be used at
all, and they do not become negotiable because the company is Canadian.

`docs/SCORING.md` carries both scales, dated, with the reasoning.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

COMPONENT_WEIGHTS: dict[str, int] = {
    "clerical_posting": 1,
    "data_gen_tech": 1,
    "case_study": 1,
    "weak_front_door": 1,
    "decision_maker_found": 1,
    "in_drive_radius": 1,
    "too_big": -1,
    "status_uncertain": -1,
}
"""The Indiana scale: weight per signal component, unchanged since 2026-08-09.

Keys match the ``score_breakdown`` JSONB shape documented in migration 001.
Kept as a module-level name as well as inside the profile because it is what
migration 001 documents and what the audit checks against."""

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

P1_MIN_SCORE = 3
"""Minimum score for P1 — and P1 additionally requires a named decision-maker.

Lowered from 4 on 2026-08-09. Only five components can fire in practice, so a
threshold of 4 asked a prospect to hit four of five. See docs/SCORING.md."""

P2_MIN_SCORE = 2
"""Minimum score for P2."""

PROGRAM_RECENCY_YEAR = 2023
"""An award starting in this year or later counts as recent (canada_gc).

Three years is roughly how long a capital purchase takes to become a data
problem: long enough that the machine is running and producing readings nobody
is using, short enough that the decision is still the same person's."""


class SignalInputs(BaseModel):
    """Every observed signal any profile can read, one field per component.

    Every field is a decision already made by a researcher or a tool from
    evidence. Nothing here is inferred at scoring time.

    The model carries the union of both profiles' components; the profile
    decides which of them are read. A field a profile does not use is not
    scored, does not appear in the breakdown, and is not collected — which is
    the difference between "this company has no Conexus case study" and "case
    studies are not a thing where this company is".
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

    # --- canada_gc components ------------------------------------------------
    program_recency: bool = Field(
        default=False,
        description=f"The company's most recent federal award starts in "
        f"{PROGRAM_RECENCY_YEAR} or later.",
    )
    english_site: bool = Field(
        default=False,
        description="The company's own site is in English, so an English approach "
        "is the right one.",
    )
    purpose_names_data_generating_tech: bool = Field(
        default=False,
        description="The government record's statement of what the award is for "
        "names data-generating technology (see DATA_GENERATING_TECH_TERMS).",
    )
    compliance_regime: bool = Field(
        default=False,
        description="The company publishes a quality or food-safety certification, "
        "so it is already under an audited documentation regime.",
    )
    external_tech_engagement: bool = Field(
        default=False,
        description="The record names an outside technology partner, collaborator "
        "or integrator the company has already worked with.",
    )


class ScoringProfile(BaseModel):
    """One source's scale: which components count, what each is worth, and why.

    A profile is chosen by `source_adapter` and by nothing else. Two prospects
    from the same source are always scored the same way, and a prospect is never
    scored on a component the source cannot supply.
    """

    model_config = ConfigDict(frozen=True)

    adapter_id: str
    display_name: str
    weights: dict[str, int]
    p1_min_score: int = P1_MIN_SCORE
    p2_min_score: int = P2_MIN_SCORE
    calibrated: bool = False
    note: str = ""

    @property
    def components(self) -> tuple[str, ...]:
        return tuple(self.weights)

    @property
    def ceiling(self) -> int:
        """The highest total this scale can produce, which is what a threshold
        has to be read against."""
        return sum(w for w in self.weights.values() if w > 0)

    @property
    def floor(self) -> int:
        return sum(w for w in self.weights.values() if w < 0)


CONEXUS_PROFILE = ScoringProfile(
    adapter_id="conexus_iedc",
    display_name="Conexus Indiana / IEDC MRG",
    weights=dict(COMPONENT_WEIGHTS),
    note=(
        "The original scale, unchanged. Six positive components against a "
        "ceiling of 6 and a P1 threshold of 3, set on 2026-08-09 after the "
        "first ten prospects were measured. Still uncalibrated against outcome "
        "data, because no outreach has produced a reply to fit against."
    ),
)

CANADA_PROFILE = ScoringProfile(
    adapter_id="canada_gc",
    display_name="Government of Canada Grants & Contributions (ON, AB)",
    weights={
        "clerical_posting": 1,
        "weak_front_door": 1,
        "decision_maker_found": 1,
        "program_recency": 1,
        "english_site": 1,
        "purpose_names_data_generating_tech": 1,
        "compliance_regime": 1,
        "external_tech_engagement": 1,
        "too_big": -1,
        "status_uncertain": -1,
    },
    note=(
        "UNCALIBRATED. Every weight here is judgment, not measurement: no "
        "Canadian prospect has been contacted, so there is no reply data to fit "
        "against and the numbers are a starting point rather than a finding. "
        "`in_drive_radius` and `case_study` are absent because neither can fire "
        "in Canada; `data_gen_tech` is replaced by "
        "`purpose_names_data_generating_tech`, which is the same idea read off "
        "the government record instead of the Conexus listing."
    ),
)

PROFILES: dict[str, ScoringProfile] = {
    CONEXUS_PROFILE.adapter_id: CONEXUS_PROFILE,
    CANADA_PROFILE.adapter_id: CANADA_PROFILE,
}
"""Every scale, keyed by `source_adapter`. One row in `source_adapters`, one
entry here — adding a source without a profile is an error, not a default."""

DEFAULT_ADAPTER = CONEXUS_PROFILE.adapter_id
"""What an absent `source_adapter` means, mirroring migration 001.

The column is `not null default 'conexus_iedc'`, so a row that does not carry
the field is a row the database would answer 'conexus_iedc' for. Reading it the
same way is not a guess. A source_adapter that is present and unrecognised is a
different matter and raises."""


class UnknownScoringProfile(KeyError):
    """A prospect names a source adapter that has no scale.

    Raised rather than defaulted. Falling back to another source's scale would
    score a company on components its source cannot supply and produce a number
    that looks like every other number — which is precisely the failure the
    per-adapter split exists to prevent.
    """


def profile_for(source_adapter: str | None) -> ScoringProfile:
    """The scale for one source. Raises if the source has no scale declared."""
    key = (source_adapter or "").strip() or DEFAULT_ADAPTER
    if key not in PROFILES:
        raise UnknownScoringProfile(
            f"no scoring profile for source_adapter {source_adapter!r}. Declare one "
            f"in lib/scoring.py and document it in docs/SCORING.md before scoring "
            f"anything from this source; known profiles: {', '.join(sorted(PROFILES))}"
        )
    return PROFILES[key]


class ScoreResult(BaseModel):
    """A total score plus the per-component breakdown written to the database."""

    model_config = ConfigDict(frozen=True)

    total: int
    breakdown: dict[str, int]


def compute_score(
    signals: SignalInputs, profile: ScoringProfile = CONEXUS_PROFILE
) -> ScoreResult:
    """Score a prospect from its observed signals, on one source's scale.

    The breakdown carries every component of that profile, including the ones
    that scored zero, so a stored record shows what was checked and not merely
    what fired. Components outside the profile are absent rather than zero — a
    Canadian record has no `case_study: 0`, because there was never a case
    study to look for.
    """
    breakdown = {
        component: (weight if getattr(signals, component) else 0)
        for component, weight in profile.weights.items()
    }
    return ScoreResult(total=sum(breakdown.values()), breakdown=breakdown)


def assign_priority(
    score: int, has_named_decision_maker: bool,
    profile: ScoringProfile = CONEXUS_PROFILE,
) -> str:
    """Map a score to 'P1', 'P2' or 'P3'.

    P1 is reserved for prospects we can actually start a conversation with:
    a strong score is not enough without a named decision-maker and a contact
    path, because there is nobody to send the work to. A high-scoring prospect
    with no named contact therefore lands in P2 — worth the research time it
    takes to find the human, but not yet worth outreach effort.

    Thresholds as of 2026-08-09: P1 needs 3 or more AND a named decision-maker,
    P2 is 2 (or 3+ with nobody to write to), P3 is 1 or less. Both profiles
    currently use them. The rationale and the scale's history live in
    docs/SCORING.md.
    """
    if score >= profile.p1_min_score and has_named_decision_maker:
        return "P1"
    if score >= profile.p2_min_score:
        return "P2"
    return "P3"
