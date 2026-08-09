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
from lib import db
from lib.claims import TRIGGER_REQUIRED_KEYS
from lib.evidence import BLOCK7_PEOPLE, FLAGS_KEY, SCORE_EVIDENCE_KEY
from lib.nodes import FORBIDDEN_STAGES, NODE_REGISTRY
from lib.runner import _is_selectable

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

    for name in sorted(NODE_REGISTRY):
        if per_node.get(name, 0) != len(prospect_ids):
            result.failures.append(
                f"node {name}: {per_node.get(name, 0)} work items for "
                f"{len(prospect_ids)} prospects"
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
        scored = {name for name, points in breakdown.items() if points}
        if set(evidence) != scored:
            missing = scored - set(evidence)
            stale = set(evidence) - scored
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

    checks = [
        check_no_unreachable_work_state(items),
        check_claim_shape(prospects),
        check_source_urls(prospects),
        check_p1_has_a_human(prospects),
        check_no_human_only_stage(prospects),
        check_queue_reconciles(prospects, items),
        check_score_arithmetic(prospects),
        check_score_evidence_matches(prospects),
    ]
    return 0 if render(console, checks) else 1


if __name__ == "__main__":
    sys.exit(main())
