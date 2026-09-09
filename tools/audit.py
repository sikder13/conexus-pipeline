"""Invariant audit — check that the pipeline's data means what it says.

Run from the repo root:

    python -m tools.audit

Three separate bugs in this project shared one shape: the system reported a
healthy status while operating on the wrong data. A queue said 1000 items when
it held 1144. A run said "10 pending" while the ten items it meant were
unreachable. A score cited a decision-maker who did not exist. None of them
raised an error, and none of them would have been caught by a test — they were
failures of the data, not of the code.

This tool is the standing check for that class of failure. Every assertion is
about the live database rather than about a function, it names the specific
offending rows rather than a count, and it exits non-zero so it can gate a
future CI run.

It is deliberately readable as a report. Someone who has never seen this code
should be able to run it against their own data and understand what is being
promised on their behalf.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from datetime import date
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import tools.harvester.nodes  # noqa: F401  (registers the nodes)
from lib import db, formula, icp, pricing
from lib.claimcheck import is_barred
from lib.claims import TRIGGER_REQUIRED_KEYS
from lib.evidence import (
    BLOCK7_PEOPLE,
    FLAGS_KEY,
    SCORE_EVIDENCE_KEY,
    SCORE_PROFILE_KEY,
)
from lib.nodes import FORBIDDEN_STAGES, NODE_REGISTRY
from lib.runner import _is_selectable
from tools.analyst import main as analyst
from tools.harvester.nodes.case_study import clean_person_name

MAX_SHOWN = 6


class CheckResult(BaseModel):
    """One invariant, whether it holds, and who broke it."""

    name: str
    promise: str
    inspected: int = 0
    failures: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def _walk_claims(node: Any, path: str):
    """Yield (path, claim) for every object in an evidence file carrying a value.

    Mirrors the database trigger's `$.** ? (exists(@.value))`, so what this
    audits and what the database enforces are the same set of objects.
    """
    if isinstance(node, dict):
        if "value" in node:
            yield path, node
        for key, child in node.items():
            yield from _walk_claims(child, f"{path}.{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from _walk_claims(child, f"{path}[{index}]")


def check_no_unreachable_work_state(items: list[dict]) -> CheckResult:
    """Every work-item state must be selectable by some combination of flags."""
    result = CheckResult(
        name="Work queue reachable",
        promise="no work_item state is stranded beyond every flag combination",
    )
    combinations = [(force, permanent) for force in (False, True) for permanent in (False, True)]
    states: dict[tuple, int] = Counter()
    for item in items:
        node = NODE_REGISTRY.get(item["node_name"])
        exhausted = (item.get("attempts") or 0) >= (node.max_attempts if node else 3)
        states[(item["status"], item.get("skip_kind"), exhausted)] += 1
    result.inspected = len(items)
    for (status, skip_kind, exhausted), count in states.items():
        probe = {"status": status, "skip_kind": skip_kind, "attempts": 99 if exhausted else 0}
        node = next(iter(NODE_REGISTRY.values()))
        if not any(_is_selectable(probe, node, f, p) for f, p in combinations):
            result.failures.append(
                f"{count} item(s) in state status={status} skip_kind={skip_kind} "
                f"exhausted={exhausted} cannot be selected by any flag"
            )
    return result


def check_claim_shape(prospects: list[dict]) -> CheckResult:
    """Every claim must carry value, tier, source_url and a parseable date."""
    result = CheckResult(
        name="Claim shape",
        promise="every claim has a value, a tier 1-4, a source URL and a real check date",
    )
    for prospect in prospects:
        evidence = prospect.get("evidence_file") or {}
        for path, claim in _walk_claims(evidence, "evidence_file"):
            result.inspected += 1
            missing = [key for key in TRIGGER_REQUIRED_KEYS if key not in claim]
            if missing:
                result.failures.append(
                    f"{prospect['id']} {path}: missing {', '.join(missing)}"
                )
                continue
            tier = claim.get("tier")
            if isinstance(tier, bool) or not isinstance(tier, int) or tier not in (1, 2, 3, 4):
                result.failures.append(f"{prospect['id']} {path}: tier is {tier!r}, not 1-4")
            checked = claim.get("date_checked")
            try:
                date.fromisoformat(str(checked))
            except (TypeError, ValueError):
                result.failures.append(
                    f"{prospect['id']} {path}: date_checked {checked!r} does not parse"
                )
    return result


def check_source_urls(prospects: list[dict]) -> CheckResult:
    """Every claim's source_url must be a URL a human can actually open."""
    result = CheckResult(
        name="Source URLs",
        promise="every claim cites an http(s) URL with a host",
    )
    for prospect in prospects:
        for path, claim in _walk_claims(prospect.get("evidence_file") or {}, "evidence_file"):
            if "source_url" not in claim:
                continue
            result.inspected += 1
            parsed = urlparse(str(claim.get("source_url") or ""))
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                result.failures.append(
                    f"{prospect['id']} {path}: source_url {claim.get('source_url')!r} is not "
                    f"an openable URL"
                )
    return result


def check_p1_has_a_human(prospects: list[dict]) -> CheckResult:
    """A P1 is a company somebody is about to call. There must be somebody to call."""
    result = CheckResult(
        name="P1 has a named human",
        promise="every P1 prospect has a named decision-maker recorded in block7",
    )
    for prospect in prospects:
        if prospect.get("priority") != "P1":
            continue
        result.inspected += 1
        block7 = (prospect.get("evidence_file") or {}).get(BLOCK7_PEOPLE) or {}
        flag = (block7.get(FLAGS_KEY) or {}).get("named_decision_maker") or {}
        named = block7.get("named_people") or []
        if flag.get("value") is not True or not named:
            result.failures.append(
                f"{prospect['id']} {prospect.get('company_name')}: P1 with no named "
                f"decision-maker in block7"
            )
    return result


def check_named_people_are_people(prospects: list[dict]) -> CheckResult:
    """Every recorded contact must still pass the person-name rules.

    "P1 has a named human" only asks whether a name is present. It passed while
    nine P1 and P2 records carried a contact who does not exist — another
    prospect's company name, a machine-tool brand read as a surname, page
    furniture, and an unfilled "John Doe" template. Presence is not personhood,
    so this re-applies the validator to what is actually stored.

    It also catches the reverse problem: a name written by an older, looser
    version of the extractor that today's rules would reject.
    """
    result = CheckResult(
        name="Named contacts are people",
        promise="every stored contact passes the person-name rules that wrote it",
    )
    for prospect in prospects:
        block7 = (prospect.get("evidence_file") or {}).get(BLOCK7_PEOPLE) or {}
        for entry in block7.get("named_people") or []:
            if not isinstance(entry, dict):
                continue
            result.inspected += 1
            stated = str(entry.get("value") or "")
            # Claims read "Name — Role"; the validator judges the name alone.
            name = stated.split("—")[0].strip()
            if not clean_person_name(name, prospect.get("company_name")):
                result.failures.append(
                    f"{prospect['id']} {prospect.get('company_name')}: "
                    f"{stated!r} is not a person"
                )
    return result


def check_verified_has_a_session(prospects: list[dict], sessions: list[dict]) -> CheckResult:
    """Every verified prospect must carry a completed verification session.

    The console is the only code path that may set stage='verified', and it
    always completes a session when it does. Checking the pair here is what
    makes that guarantee survive a future edit to the console: a verified row
    with no session means the stage was set by something else, and this is how
    anyone would find out.
    """
    result = CheckResult(
        name="Verified rows have a session",
        promise="every stage='verified' prospect has a completed verification_session",
    )
    completed = {
        s["prospect_id"] for s in sessions if s.get("completed_at")
    }
    for prospect in prospects:
        if prospect.get("stage") != "verified":
            continue
        result.inspected += 1
        if prospect["id"] not in completed:
            result.failures.append(
                f"{prospect['id']} {prospect.get('company_name')}: stage='verified' with no "
                f"completed verification session — it was not set by the console"
            )
    return result


def check_needs_review_has_a_reason(prospects: list[dict]) -> CheckResult:
    """A row parked for a human must say why it is parked."""
    result = CheckResult(
        name="needs_review has a reason",
        promise="every needs_review prospect records why it needs one",
    )
    for prospect in prospects:
        if prospect.get("stage") != "needs_review":
            continue
        result.inspected += 1
        reason = (prospect.get("needs_review_reason") or "").strip()
        report = prospect.get("integrity_report") or {}
        notes = [
            n.get("note", "") for n in ((prospect.get("evidence_file") or {}).get("notes") or [])
        ]
        if not reason and not report.get("failures") and not any(
            "needs_review" in n or "below" in n for n in notes
        ):
            result.failures.append(
                f"{prospect['id']} {prospect.get('company_name')}: in needs_review with no "
                f"recorded reason"
            )
    return result


def check_summaries_are_whole(prospects: list[dict]) -> CheckResult:
    """No stored summary may end mid-sentence.

    Decatur's did: the model hit its token ceiling and the node stored the
    fragment as if it were finished. A truncated paragraph reads as a complete
    one, which is what makes it dangerous.
    """
    result = CheckResult(
        name="Summaries are whole",
        promise="no machine_summary ends mid-sentence",
    )
    for prospect in prospects:
        text = (prospect.get("machine_summary") or "").rstrip()
        if not text:
            continue
        result.inspected += 1
        if not text.endswith((".", "!", "?", '"', "\u201d", ")")):
            result.failures.append(
                f"{prospect['id']} {prospect.get('company_name')}: summary ends "
                f"mid-sentence: ...{text[-60:]!r}"
            )
    return result


def check_no_compromised_in_the_queue(prospects: list[dict]) -> CheckResult:
    """No prospect anyone is about to contact may sit on a hijacked domain."""
    result = CheckResult(
        name="No compromised P1/P2",
        promise="no P1 or P2 prospect has website_status='compromised'",
    )
    for prospect in prospects:
        if prospect.get("priority") not in ("P1", "P2"):
            continue
        result.inspected += 1
        if prospect.get("website_status") == "compromised":
            result.failures.append(
                f"{prospect['id']} {prospect.get('company_name')}: {prospect.get('priority')} "
                f"on a compromised site ({prospect.get('website')})"
            )
    return result


def check_no_tainted_scoring_input(prospects: list[dict]) -> CheckResult:
    """A score must never trace back to a quarantined claim."""
    result = CheckResult(
        name="No tainted scoring input",
        promise="no scored prospect draws a scoring input from a tainted claim",
    )
    for prospect in prospects:
        if prospect.get("signal_score") is None:
            continue
        result.inspected += 1
        evidence = prospect.get("evidence_file") or {}
        bad = [
            path for path, claim in _walk_claims(evidence, "evidence_file")
            if claim.get("tainted") is True
            and (f".{FLAGS_KEY}." in path or SCORE_EVIDENCE_KEY in path)
        ]
        if bad:
            result.failures.append(
                f"{prospect['id']} {prospect.get('company_name')}: scored with tainted "
                f"input(s) {', '.join(bad[:3])}"
            )
    return result


def check_compromised_has_a_fingerprint(prospects: list[dict]) -> CheckResult:
    """A quarantine must be arguable against evidence, not just asserted."""
    result = CheckResult(
        name="Compromised sites cite a fingerprint",
        promise="every website_status='compromised' records at least one matched marker",
    )
    for prospect in prospects:
        if prospect.get("website_status") != "compromised":
            continue
        result.inspected += 1
        if not (prospect.get("website_fingerprints") or []):
            result.failures.append(
                f"{prospect['id']} {prospect.get('company_name')}: quarantined with no "
                f"recorded fingerprint — nobody can check the decision"
            )
    return result


OUTBOUND_KINDS = ("thesis", "email", "brief")
"""Artifact kinds held to the sentence-typing gate.

The analysis is not one of them. It is internal, is never shown to the company,
and answers to a different set of rules — every figure a range or sourced, three
approaches that are genuinely three. Auditing it against the outbound gate would
read its records as malformed rather than as a different kind of record, so each
kind is checked against the standard it was actually generated under.

What both kinds still share is the claims they rest on: a barred claim is barred
everywhere, so the clean-claims check below covers every artifact."""


def check_sendable_artifacts_are_clean(
    prospects: list[dict], artifacts: list[dict]
) -> CheckResult:
    """No sendable artifact may cite a tainted, killed or unsupported claim."""
    result = CheckResult(
        name="Sendable artifacts cite clean claims",
        promise="no sendable artifact cites a tainted, killed or unsupported claim",
    )
    by_id = {p["id"]: p for p in prospects}
    for artifact in artifacts:
        if artifact.get("status") != "sendable":
            continue
        result.inspected += 1
        prospect = by_id.get(artifact.get("prospect_id"))
        if not prospect:
            result.failures.append(f"artifact {artifact['id']}: prospect is missing")
            continue
        lookup = {
            path.removeprefix("evidence_file."): claim
            for path, claim in _walk_claims(prospect.get("evidence_file") or {}, "evidence_file")
        }
        for cited in artifact.get("claims_cited") or []:
            claim = lookup.get(cited)
            if claim is None:
                result.failures.append(
                    f"artifact {artifact['id']} cites {cited} which no longer exists"
                )
            elif is_barred(claim):
                result.failures.append(
                    f"artifact {artifact['id']} is sendable but cites barred claim {cited}"
                )
    return result


def check_sendable_passed_the_gate(artifacts: list[dict]) -> CheckResult:
    """Every sendable artifact must carry a gate map and no recorded failures."""
    result = CheckResult(
        name="Sendable artifacts passed the gate",
        promise="every sendable artifact has a sentence-to-claim map and no gate failures",
    )
    for artifact in artifacts:
        if artifact.get("status") != "sendable":
            continue
        if artifact.get("kind") not in OUTBOUND_KINDS:
            continue
        result.inspected += 1
        if not artifact.get("gate_map"):
            result.failures.append(
                f"artifact {artifact['id']} is sendable with no gate map — it was never audited"
            )
        if artifact.get("gate_failures"):
            result.failures.append(
                f"artifact {artifact['id']} is sendable with recorded gate failures"
            )
    return result


def check_sendable_arithmetic_is_typed(artifacts: list[dict]) -> CheckResult:
    """No sendable artifact carries an untyped figure or an uncorrectable one.

    The gate enforces this at generation time. This checks the same thing from
    the stored record, so a rule that changes later cannot quietly leave older
    artifacts sitting at 'sendable' under a standard nobody applied to them.
    See docs/GATE.md for the ruling this rests on.
    """
    result = CheckResult(
        name="Sendable arithmetic is typed and correctable",
        promise="every figure in a sendable artifact is sourced or openly assumed, "
                "and any artifact reasoning from assumptions asks to be corrected",
    )
    for artifact in artifacts:
        if artifact.get("status") != "sendable":
            continue
        if artifact.get("kind") not in OUTBOUND_KINDS:
            continue
        result.inspected += 1
        entries = artifact.get("gate_map") or []
        assumed = False
        for entry in entries:
            sentence = entry.get("sentence") or ""
            kind = entry.get("type") or formula.DEFAULT_TYPE
            if kind == formula.ASSUMPTION:
                assumed = True
                points = formula.point_quantities(sentence)
                if points:
                    result.failures.append(
                        f"artifact {artifact['id']} is sendable with an assumption "
                        f"stating a point figure ({', '.join(points[:2])})"
                    )
            elif not entry.get("claims") and formula.QUANTITY.search(sentence):
                result.failures.append(
                    f"artifact {artifact['id']} is sendable with an untyped, "
                    f"unsourced quantity: {sentence[:70]!r}"
                )
        if assumed and not formula.invites_correction(artifact.get("body") or ""):
            result.failures.append(
                f"artifact {artifact['id']} is sendable, reasons from assumptions, "
                f"and never invites correction"
            )
    return result


def check_inferences_are_anchored(artifacts: list[dict]) -> CheckResult:
    """Every inference in a sendable artifact names the fact it reasons from.

    The anchor is the half a reader can check. Without it an inference is our
    conclusion in the same voice as their own published words, which is the
    exact confusion the tier system exists to prevent. The email's
    one-hypothesis budget is checked here too, because that limit is the
    cold-touch formula and nothing but the email is bound by it.
    See docs/GATE.md.
    """
    result = CheckResult(
        name="Sendable inferences are anchored and budgeted",
        promise="every inference in a sendable artifact cites a parent claim and "
                "shows its reasoning, and no email carries more than one",
    )
    for artifact in artifacts:
        if artifact.get("status") != "sendable":
            continue
        if artifact.get("kind") not in OUTBOUND_KINDS:
            continue
        result.inspected += 1
        reasoning = 0
        for entry in artifact.get("gate_map") or []:
            if (entry.get("type") or formula.DEFAULT_TYPE) != formula.INFERENCE:
                continue
            reasoning += 1
            sentence = entry.get("sentence") or ""
            if not entry.get("claims"):
                result.failures.append(
                    f"artifact {artifact['id']} is sendable with an unanchored "
                    f"inference: {sentence[:70]!r}"
                )
            if not formula.reasons_aloud(sentence):
                result.failures.append(
                    f"artifact {artifact['id']} is sendable with an inference that "
                    f"does not show its reasoning: {sentence[:70]!r}"
                )
        if artifact.get("kind") == "email" and reasoning > 1:
            result.failures.append(
                f"artifact {artifact['id']} is a sendable email carrying "
                f"{reasoning} inferences; the formula allows one"
            )
    return result


def check_analysis_is_sourced_and_distinct(artifacts: list[dict]) -> CheckResult:
    """Every usable analysis keeps its own two promises, read from the record.

    The analyst's gate enforces these at generation time. This reads the same
    thing back out of storage, so a rule that changes later cannot quietly leave
    older analyses sitting at 'sendable' under a standard nobody applied to
    them — the same argument as the outbound checks above, aimed at a different
    standard. See docs/ANALYSIS.md.
    """
    result = CheckResult(
        name="Analyses are sourced and their approaches are distinct",
        promise="every figure in a usable analysis is a range or names its source, "
                "and no two of its approaches are the same build at two prices",
    )
    for artifact in artifacts:
        if artifact.get("status") != "sendable" or artifact.get("kind") != "analysis":
            continue
        result.inspected += 1
        allowed = set(artifact.get("claims_cited") or [])
        for failure in analyst.unsourced_figures(artifact.get("body") or "", allowed):
            result.failures.append(f"analysis {artifact['id']}: {failure[:150]}")

        meta = artifact.get("gate_map") or {}
        if not isinstance(meta, dict):
            result.failures.append(
                f"analysis {artifact['id']} stores no peer comparison or approaches")
            continue
        if meta.get("thin"):
            # Below the evidence floor there is nothing to cost, so an analysis
            # with no approaches is the correct shape rather than a gap.
            continue
        approaches = meta.get("approaches") or []
        if len(approaches) != 3:
            result.failures.append(
                f"analysis {artifact['id']} is usable with {len(approaches)} "
                f"approach(es); the deliverable is three")
            continue
        builds = [a.get("core_build") or "" for a in approaches]
        for index, first in enumerate(builds):
            for second in builds[index + 1:]:
                if analyst.similarity(first, second) > analyst.SAME_BUILD:
                    result.failures.append(
                        f"analysis {artifact['id']} offers the same build twice: "
                        f"{first[:60]!r} against {second[:60]!r}")
        returns = [tuple(a.get("annual_return") or ()) for a in approaches]
        for index, first in enumerate(returns):
            for second in returns[index + 1:]:
                if first and first == second:
                    result.failures.append(
                        f"analysis {artifact['id']} claims the same return for two "
                        f"approaches ({first}); each one has to be worked out from "
                        f"its own evidence")
        for entry in approaches:
            price = tuple(entry.get("price") or ())
            if price not in {e.band for e in pricing.LADDER}:
                result.failures.append(
                    f"analysis {artifact['id']} quotes {price}, which is not a band "
                    f"on the engagement ladder")
    return result


def check_halt_flag_is_honoured() -> CheckResult:
    """The canary halt must exist, and every send path must check it.

    There are currently no send paths, so this check passes trivially — and that
    is the point of writing it now. It will start doing real work the moment one
    is added, rather than being remembered afterwards.
    """
    import pathlib

    result = CheckResult(
        name="Halt flag exists and is honoured",
        promise="canary_state exists and every send path calls assert_sendable first",
    )
    try:
        db.canary_row()
        result.inspected += 1
    except Exception as exc:
        result.failures.append(f"canary_state is unreadable: {exc}")
        return result

    root = pathlib.Path(__file__).resolve().parent.parent
    senders = []
    for path in list((root / "lib").rglob("*.py")) + list((root / "tools").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\bdef send[_a-z]*\(|smtplib|sendgrid|postmark|resend\.", text):
            senders.append(path)
    for path in senders:
        text = path.read_text(encoding="utf-8", errors="replace")
        result.inspected += 1
        if "assert_sendable" not in text:
            result.failures.append(
                f"{path.relative_to(root)} looks like a send path but never calls "
                f"canary.assert_sendable()"
            )
    return result


def check_no_human_only_stage(prospects: list[dict]) -> CheckResult:
    """No automated tool may promote a record past the human check."""
    result = CheckResult(
        name="Stage discipline",
        promise="no prospect sits in 'verified' or later — no human tool exists yet",
    )
    result.inspected = len(prospects)
    for prospect in prospects:
        if prospect.get("stage") in FORBIDDEN_STAGES:
            result.failures.append(
                f"{prospect['id']} {prospect.get('company_name')}: stage is "
                f"{prospect.get('stage')!r}, which only a human may set"
            )
    return result


def check_queue_reconciles(prospects: list[dict], items: list[dict]) -> CheckResult:
    """Every prospect has exactly one work item per node, and no item is an orphan."""
    result = CheckResult(
        name="Queue reconciles",
        promise="work_items = prospects x nodes exactly, with no orphans and no duplicates",
    )
    result.inspected = len(items)
    prospect_ids = {p["id"] for p in prospects}
    per_node = Counter(item["node_name"] for item in items)

    # A node may declare the priorities it runs for. contact_discovery and
    # competitor_scan read other people's sites for companies we are about to
    # contact, so enqueuing them for all 572 would be 550 requests nobody wanted
    # and 550 rows that only ever record a skip. Their expected count is the
    # number of prospects at those priorities.
    by_priority = Counter(p.get("priority") for p in prospects)
    for name in sorted(NODE_REGISTRY):
        wanted = getattr(NODE_REGISTRY[name], "priorities", None)
        expected = (sum(by_priority[p] for p in wanted) if wanted
                    else len(prospect_ids))
        if per_node.get(name, 0) != expected:
            scope = f" at priority {', '.join(wanted)}" if wanted else ""
            result.failures.append(
                f"node {name}: {per_node.get(name, 0)} work items for "
                f"{expected} prospects{scope}"
            )
    for extra in sorted(set(per_node) - set(NODE_REGISTRY)):
        result.failures.append(f"node {extra}: {per_node[extra]} items for an unregistered node")

    orphans = [item for item in items if item["prospect_id"] not in prospect_ids]
    if orphans:
        result.failures.append(f"{len(orphans)} work item(s) reference a missing prospect")

    pairs = Counter((item["prospect_id"], item["node_name"]) for item in items)
    duplicates = [pair for pair, count in pairs.items() if count > 1]
    if duplicates:
        result.failures.append(f"{len(duplicates)} duplicated (prospect, node) pair(s)")
    return result


def check_size_ceiling(prospects: list[dict]) -> CheckResult:
    """Nobody reachable is outside the ICP by size without a human saying so.

    The check `too_big` could not be: a scoring component is worth a point, and
    Batesville Tool & Die held P1 at score 4 with 1,358 employees in its own
    evidence. This asks the eligibility question instead — of every prospect
    that could still receive a message, does any carry trusted evidence of being
    too big, unoverridden?
    """
    result = CheckResult(
        name="Size ceiling holds",
        promise=(
            f"no outreach-eligible prospect carries T1/T2 evidence of more than "
            f"{icp.GROWTH_MAX} employees without an operator override"
        ),
    )
    for prospect in prospects:
        if prospect.get("priority") not in ("P1", "P2") or prospect.get("stage") == "dead":
            continue
        if not icp.outreach_eligible(prospect):
            continue
        result.inspected += 1
        verdict = icp.size_verdict(prospect)
        if verdict.outcome == icp.OK:
            continue
        if icp.is_overridden(prospect):
            continue
        result.failures.append(
            f"{prospect['id']} {prospect.get('company_name')} "
            f"[{prospect.get('priority')}]: {verdict.reason}"
        )
    return result


def check_score_arithmetic(prospects: list[dict]) -> CheckResult:
    """The stored score must equal the sum of its own breakdown."""
    result = CheckResult(
        name="Score arithmetic",
        promise="signal_score equals the sum of score_breakdown for every scored prospect",
    )
    for prospect in prospects:
        breakdown = prospect.get("score_breakdown")
        if prospect.get("signal_score") is None or not isinstance(breakdown, dict):
            continue
        result.inspected += 1
        recomputed = sum(int(v) for v in breakdown.values())
        if recomputed != prospect["signal_score"]:
            result.failures.append(
                f"{prospect['id']} {prospect.get('company_name')}: stored "
                f"{prospect['signal_score']}, breakdown sums to {recomputed}"
            )
    return result


def check_score_evidence_matches(prospects: list[dict]) -> CheckResult:
    """Every scoring component that fired must carry its justification, and vice versa."""
    result = CheckResult(
        name="Score traceability",
        promise="score_evidence names exactly the components that scored, each with a source",
    )
    for prospect in prospects:
        breakdown = prospect.get("score_breakdown")
        if not isinstance(breakdown, dict):
            continue
        result.inspected += 1
        evidence = (prospect.get("evidence_file") or {}).get(SCORE_EVIDENCE_KEY) or {}
        # `_profile` names the scale the score was computed on. It is the label
        # on the working rather than part of it, which is why it is underscored
        # and why it is excluded here.
        justified = set(evidence) - {SCORE_PROFILE_KEY}
        scored = {name for name, points in breakdown.items() if points}
        if justified != scored:
            missing = scored - justified
            stale = justified - scored
            detail = []
            if missing:
                detail.append(f"unjustified: {', '.join(sorted(missing))}")
            if stale:
                detail.append(f"stale: {', '.join(sorted(stale))}")
            result.failures.append(f"{prospect['id']}: {'; '.join(detail)}")
    return result


def render(console: Console, checks: list[CheckResult]) -> bool:
    """Print the report. Returns True when everything passed."""
    table = Table(title="Pipeline invariant audit", title_justify="left", show_lines=True)
    table.add_column("Check", style="bold", no_wrap=True)
    table.add_column("Result", no_wrap=True)
    table.add_column("Inspected", justify="right")
    table.add_column("What is being promised", overflow="fold")

    for check in checks:
        marker = (
            "[green]PASS[/green]" if check.passed
            else f"[red]FAIL ({len(check.failures)})[/red]"
        )
        table.add_row(check.name, marker, f"{check.inspected:,}", check.promise)
    console.print(table)

    for check in checks:
        if check.passed:
            continue
        console.print(f"\n[red bold]{check.name} — {len(check.failures)} failure(s)[/red bold]")
        for failure in check.failures[:MAX_SHOWN]:
            console.print(f"  • {failure}")
        if len(check.failures) > MAX_SHOWN:
            console.print(f"  … and {len(check.failures) - MAX_SHOWN} more")

    passed = sum(1 for check in checks if check.passed)
    everything = passed == len(checks)
    console.print(
        Panel(
            f"{passed}/{len(checks)} checks passed · "
            f"{sum(len(c.failures) for c in checks)} finding(s)",
            style="green" if everything else "red",
            title="PASS" if everything else "FAIL",
        )
    )
    return everything


def main() -> int:
    console = Console()
    console.print("Reading the live database …\n")
    prospects = db.list_prospects_full()
    items = db.all_work_items()
    sessions = db.all_sessions()
    artifacts = db.all_artifacts()

    checks = [
        check_no_unreachable_work_state(items),
        check_claim_shape(prospects),
        check_source_urls(prospects),
        check_p1_has_a_human(prospects),
        check_no_compromised_in_the_queue(prospects),
        check_no_tainted_scoring_input(prospects),
        check_compromised_has_a_fingerprint(prospects),
        check_sendable_artifacts_are_clean(prospects, artifacts),
        check_sendable_passed_the_gate(artifacts),
        check_sendable_arithmetic_is_typed(artifacts),
        check_inferences_are_anchored(artifacts),
        check_analysis_is_sourced_and_distinct(artifacts),
        check_halt_flag_is_honoured(),
        check_named_people_are_people(prospects),
        check_no_human_only_stage(prospects),
        check_verified_has_a_session(prospects, sessions),
        check_needs_review_has_a_reason(prospects),
        check_summaries_are_whole(prospects),
        check_queue_reconciles(prospects, items),
        check_size_ceiling(prospects),
        check_score_arithmetic(prospects),
        check_score_evidence_matches(prospects),
    ]
    return 0 if render(console, checks) else 1


if __name__ == "__main__":
    sys.exit(main())
