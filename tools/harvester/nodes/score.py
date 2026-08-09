"""score — turn the evidence into a number, and show the working.

There is no human triage step ahead of this. The score decides which companies
a person ever looks at, so a component that scores 1 without justification is
worse than one that scores 0: it silently promotes a company past the ones that
deserved the attention.

Hence `score_evidence`. For every component that scored, it records the flag
that fired, the tier of the observation behind it, and the URL a human can open
to check. Auditing a P1 means reading one object, not re-reading the whole
evidence file.

WHERE EACH SIGNAL COMES FROM

Six of the nine come from flags that evidence nodes set, each already carrying
its own source URL. Two are read from prospect columns instead:

* `in_drive_radius` from `drive_minutes`, which normalize_identity computed and
  recorded as a T4 claim in the identity block.
* `status_uncertain` from `website_confidence`, because front_door deliberately
  skips a prospect whose website could not be identified, and a skipped node
  writes no flag. Reading the column is how a company we could not find on the
  web still gets marked uncertain instead of quietly scoring zero.

`friction_reviews` has no automated source at all — block5 is filled in by hand
— so it scores 0 here until a human works the record.

A flag no node reached counts as False, never as an error. That is what lets a
prospect whose front_door failed still be scored on the evidence that arrived.

Note that score_evidence entries deliberately use `points` rather than `value`:
the database trigger treats any object carrying a `value` key as a claim and
demands a tier and source URL for it, and these are working, not claims.
"""

from __future__ import annotations

from typing import Any, ClassVar

from lib.claims import Tier
from lib.evidence import (
    SCORE_EVIDENCE_KEY,
    flag_is_true,
    read_flag_claim,
)
from lib.geo import DRIVE_RADIUS_MINUTES
from lib.nodes import Node, NodeResult, RunContext, register
from lib.scoring import SignalInputs, assign_priority, compute_score
from tools.harvester.nodes.identity import CENSUS_GAZETTEER_URL

# Scoring component -> the evidence flag that decides it.
COMPONENT_FLAGS: dict[str, str] = {
    "clerical_posting": "has_clerical_posting",
    "data_gen_tech": "data_gen_tech",
    "case_study": "has_case_study",
    "weak_front_door": "weak_front_door",
    "decision_maker_found": "named_decision_maker",
    "too_big": "too_big",
}

STAGES_TO_LEAVE_ALONE = ("needs_review", "dead")
MIN_CONFIDENT_WEBSITE = 50


def collect_signals(prospect: dict) -> tuple[SignalInputs, dict[str, Any]]:
    """Read the evidence flags into SignalInputs, keeping the justification for each."""
    evidence = prospect.get("evidence_file") or {}
    values: dict[str, bool] = {}
    basis: dict[str, dict[str, Any]] = {}

    for component, flag in COMPONENT_FLAGS.items():
        values[component] = flag_is_true(evidence, flag)
        claim = read_flag_claim(evidence, flag)
        if values[component] and claim:
            basis[component] = {
                "flag": flag,
                "tier": claim.get("tier"),
                "source_url": claim.get("source_url"),
                "checked": claim.get("date_checked"),
            }
            for extra in ("criteria_met", "matched_roles", "matched_terms", "people"):
                if claim.get(extra):
                    basis[component]["detail"] = claim[extra]

    drive_minutes = prospect.get("drive_minutes")
    values["in_drive_radius"] = drive_minutes is not None and drive_minutes <= DRIVE_RADIUS_MINUTES
    if values["in_drive_radius"]:
        # 'identity' predates the eight-block structure — normalize_identity has
        # written there since before this layer existed — so it is read directly
        # rather than through read_claim, which only accepts the eight.
        identity = evidence.get("identity")
        claim = identity.get("drive_minutes_from_muncie") if isinstance(identity, dict) else None
        claim = claim if isinstance(claim, dict) else {}
        basis["in_drive_radius"] = {
            "flag": "drive_minutes column",
            "tier": claim.get("tier", int(Tier.T4)),
            # Fall back to the gazetteer the estimate actually comes from, so the
            # component stays traceable even on a record written before
            # normalize_identity started recording the claim.
            "source_url": claim.get("source_url") or CENSUS_GAZETTEER_URL,
            "detail": f"{drive_minutes} minutes, within the {DRIVE_RADIUS_MINUTES} minute radius",
        }

    confidence = prospect.get("website_confidence")
    values["status_uncertain"] = confidence is not None and confidence < MIN_CONFIDENT_WEBSITE
    if values["status_uncertain"]:
        basis["status_uncertain"] = {
            "flag": "website_confidence column",
            "tier": 4,
            "source_url": prospect.get("website"),
            "detail": (
                f"website_confidence={confidence}; the company could not be confidently "
                f"located on the web"
            ),
        }

    return SignalInputs(**values), basis


@register
class ScoreNode(Node):
    """Compute the signal score, the priority, and the audit trail for both."""

    name: ClassVar[str] = "score"
    depends_on: ClassVar[tuple[str, ...]] = (
        "case_study",
        "grant_news",
        "front_door",
        "job_postings",
        "people",
    )

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        signals, basis = collect_signals(prospect)
        result = compute_score(signals)
        named = signals.decision_maker_found
        priority = assign_priority(result.total, named)

        score_evidence: dict[str, Any] = {}
        for component, points in result.breakdown.items():
            if points == 0:
                continue
            entry = {"points": points, **basis.get(component, {})}
            entry.setdefault("flag", component)
            score_evidence[component] = entry

        patch: dict[str, Any] = {
            "signal_score": result.total,
            "score_breakdown": result.breakdown,
            "priority": priority,
            "priority_set_by": "machine",
        }
        stage = prospect.get("stage")
        if stage not in STAGES_TO_LEAVE_ALONE:
            patch["stage"] = "passA_done"

        zeroed = sorted(c for c, points in result.breakdown.items() if points == 0)
        notes = [
            f"score {result.total} -> {priority} "
            f"(named decision-maker: {'yes' if named else 'no'})",
            f"components scoring zero: {', '.join(zeroed) if zeroed else 'none'}",
        ]
        if stage in STAGES_TO_LEAVE_ALONE:
            notes.append(f"stage left at {stage!r}; scoring does not override it")

        return NodeResult(
            prospect_patch=patch,
            evidence_patch={SCORE_EVIDENCE_KEY: score_evidence},
            notes=notes,
        )
