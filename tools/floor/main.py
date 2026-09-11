"""Hold what was built on an inflated floor, and release it when evidence catches up.

    python -m tools.floor --dry-run          # what would move, and why
    python -m tools.floor                    # hold and release
    python -m tools.floor --adapter canada_gc

WHY THIS EXISTS

The drafting floor asks for three Tier-1 facts about the company. Until
2026-09-10 the pool it counted included our own derivations — scoring flags and
labels we computed — so a company could clear the floor on claims that could
never fail a check, because they were never checkable. Trifecta Medical cleared
it on two real claims and `has_case_study`, a boolean of ours.

Correcting the pool deflated the floor RETROACTIVELY. Artifacts already written,
already through their gates, turned out to have been generated for companies
that did not qualify. Nothing built on the inflated floor may ship.

WHY 'HELD' RATHER THAN 'BLOCKED'

Those artifacts are not wrong in the way `blocked` means — their prose passed
every check the gate applies. They are not `skipped` either, because they were
attempted and finished. `held` is the third thing: written, sound, and withheld
because the company behind it does not have the evidence to justify having been
written for.

The distinction is not bookkeeping. `blocked` tells the operator the generator
failed and the fix is a rewrite; `held` tells them the generator was fine and the
fix is more evidence. Those route to different work.

RELEASE IS AUTOMATIC AND SYMMETRIC

The same sweep that holds also releases. An artifact records the status it was
held FROM, so when enrichment lifts a company back over the floor the hold comes
off and the artifact returns to exactly what it was — rather than to somebody's
recollection of what it was.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from rich.console import Console
from rich.table import Table

from lib import adapters, canary, db
from tools.drafter import main as drafter

HELD = "held"
HOLD_KEY = "hold"
"""Where the hold record lives inside `gate_map`: the status it came from, the
reason, and when. Kept on the artifact rather than in a side table so that a row
read on its own still explains itself."""

LIVE_STATUSES = ("sendable", "blocked")
"""Statuses a hold may be applied to.

`superseded` and `skipped` are already out of play, and holding one would
overwrite a record of what happened with a record of what we later thought."""


def built_on_the_floor(artifact: dict[str, Any]) -> bool:
    """Whether this artifact needed the company to CLEAR the floor to exist.

    A thin analysis is what a below-floor company is supposed to have: it
    carries the sections the evidence can hold and no costed approaches. Holding
    one would withdraw the correct artifact for the correct reason and leave the
    company with nothing at all, which is worse than the state we were fixing.

    Everything else — a full analysis, and every outbound kind — asserts claims
    the floor exists to license.
    """
    if artifact.get("kind") != "analysis":
        return True
    meta = artifact.get("gate_map")
    return not (isinstance(meta, dict) and meta.get("thin"))


def hold_record(artifact: dict[str, Any]) -> dict[str, Any] | None:
    meta = artifact.get("gate_map")
    return (meta or {}).get(HOLD_KEY) if isinstance(meta, dict) else None


def floor_reason(prospect: dict[str, Any], verdicts: tuple[str, ...]) -> str | None:
    """Why this company is below the floor now, or None."""
    return drafter.below_floor(prospect, verdicts)


def plan(
    prospects: list[dict[str, Any]], artifacts: list[dict[str, Any]],
    verdicts: tuple[str, ...],
) -> tuple[list[tuple[dict, dict, str]], list[tuple[dict, dict, str]]]:
    """What to hold and what to release, without touching anything."""
    by_id = {p["id"]: p for p in prospects}
    to_hold: list[tuple[dict, dict, str]] = []
    to_release: list[tuple[dict, dict, str]] = []

    for artifact in artifacts:
        prospect = by_id.get(artifact.get("prospect_id"))
        if prospect is None:
            continue
        reason = floor_reason(prospect, verdicts)
        held = hold_record(artifact)

        if (artifact.get("status") in LIVE_STATUSES and reason
                and built_on_the_floor(artifact)):
            to_hold.append((prospect, artifact, reason))
        elif artifact.get("status") == HELD and held and not reason:
            to_release.append((prospect, artifact, str(held.get("from") or "blocked")))
    return to_hold, to_release


FLOOR_RULE = "assertable facts exclude our own derivations (2026-09-10)"


def apply_hold(artifact: dict[str, Any], reason: str, rule: str = FLOOR_RULE) -> None:
    """Withdraw one artifact, remembering what it was and why it went.

    The rule is a parameter because the floor is no longer the only thing that
    withdraws an artifact: so does a domain turning out not to be the company's.
    Both need the same reversible shape, and two shapes would mean two release
    paths and one of them eventually forgotten.
    """
    meta = artifact.get("gate_map")
    meta = dict(meta) if isinstance(meta, dict) else {}
    meta[HOLD_KEY] = {
        "from": artifact.get("status"),
        "reason": reason,
        "at": datetime.now(UTC).isoformat(),
        "rule": rule,
    }
    failures = list(artifact.get("gate_failures") or [])
    note = f"held: {reason}"
    if note not in failures:
        failures.append(note)
    db.update_artifact(artifact["id"], {
        "status": HELD, "gate_map": meta, "gate_failures": failures,
    })


def apply_release(artifact: dict[str, Any], previous: str) -> None:
    meta = artifact.get("gate_map")
    meta = dict(meta) if isinstance(meta, dict) else {}
    meta.pop(HOLD_KEY, None)
    failures = [f for f in (artifact.get("gate_failures") or [])
                if not str(f).startswith("held: ")]
    db.update_artifact(artifact["id"], {
        "status": previous, "gate_map": meta, "gate_failures": failures,
    })


def _run(console: Console, adapter: str | None, dry_run: bool) -> int:
    verdicts = canary.read_state().allowed_verdicts()
    prospects = db.list_prospects_full(adapter)
    wanted = {p["id"] for p in prospects}
    artifacts = [a for a in db.all_artifacts() if a.get("prospect_id") in wanted]

    to_hold, to_release = plan(prospects, artifacts, verdicts)

    kinds: Counter = Counter()
    table = Table(title="Holding", title_justify="left")
    table.add_column("Company")
    table.add_column("Kind")
    table.add_column("Was")
    table.add_column("Why")
    for prospect, artifact, reason in to_hold:
        kinds[artifact.get("kind")] += 1
        table.add_row(str(prospect.get("company_name"))[:32],
                      str(artifact.get("kind")), str(artifact.get("status")),
                      reason[:56])
    if to_hold:
        console.print(table)
    for prospect, artifact, previous in to_release:
        console.print(f"[green]release[/green] {prospect.get('company_name')} "
                      f"{artifact.get('kind')} -> {previous}")

    if not dry_run:
        for _prospect, artifact, reason in to_hold:
            apply_hold(artifact, reason)
        for _prospect, artifact, previous in to_release:
            apply_release(artifact, previous)

    console.print(
        f"\n[bold]{len(to_hold)}[/bold] artifact(s) held "
        f"({', '.join(f'{k}×{v}' for k, v in sorted(kinds.items())) or 'none'}) · "
        f"[bold]{len(to_release)}[/bold] released"
        + (" [yellow](dry run: nothing written)[/yellow]" if dry_run else ""))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.floor", description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    adapters.add_argument(parser)
    args = parser.parse_args()
    console = Console()
    console.print(f"Scope: [bold]{adapters.words(args.adapter)}[/bold]")
    return _run(console, args.adapter, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
