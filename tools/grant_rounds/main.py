"""Grant rounds — load award amounts from the round announcements, in bulk.

Run from the repo root:

    python -m tools.grant_rounds --dry-run    # parse, match, report; write nothing
    python -m tools.grant_rounds              # write the matched awards

Six fetches instead of 572 searches. The awards are published per round, not per
company, so this reads each round announcement once and matches its recipients
against the prospect list.

WHAT IT REFUSES TO DO

Match loosely. Only an exact normalised-name match assigns an award. A name that
matches two prospects, or none, is reported for review and written nowhere. A
grant amount on the wrong company is worse than a null: a null is visibly
missing and someone goes and looks, whereas a wrong number is quietly believed
and eventually said out loud to the company that did not receive it.

Pick a winner when sources disagree. Where a case study and a round announcement
state different amounts, BOTH are recorded with their own sources and the
disagreement is flagged as a finding. Deciding which is right is a human's job,
and the evidence for that decision is what this tool leaves behind.

Multiple awards per company are normal — several companies won in more than one
round — so every award is stored. The `grant_amount` column takes the largest
single award, never a sum: the column reads as "the grant amount", and a total
presented that way would describe an award that never happened.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import Counter
from typing import Any

from rich.console import Console
from rich.table import Table

from lib import db
from lib.claims import Tier, make_claim
from lib.evidence import BLOCK2_GRANT_FUNDED, BLOCK8_FINANCIAL_SCALE, block_patch, read_claim
from lib.runner import deep_merge, merge_notes
from lib.sources.grant_rounds import (
    AwardMatch,
    GrantAward,
    fetch_announcements,
    match_to_prospects,
    tier_for,
)

PROGRAM_URL = "https://conexusindiana.com/drive-industry-success/manufacturing-readiness-grants/"


def _money(value: float) -> str:
    return f"${value:,.0f}"


def case_study_amount(evidence: dict[str, Any]) -> float | None:
    """The grant amount the case study stated, if there is one, as a number."""
    claim = read_claim(evidence, BLOCK2_GRANT_FUNDED, "grant_amount")
    if not claim:
        return None
    digits = re.sub(r"[^\d.]", "", str(claim.get("value") or ""))
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def build_block8(match: AwardMatch, evidence: dict[str, Any]) -> tuple[dict, list[str]]:
    """Build the block8 claims for one company's awards, plus any notes."""
    notes: list[str] = []
    claims: dict[str, Any] = {
        "grant_awards": [
            make_claim(
                f"{award.amount_text} — round {award.round_id}, announced "
                f"{award.announced.isoformat()}",
                tier_for(award),
                award.source_url,
            )
            for award in match.awards
        ],
        "grant_award_count": make_claim(len(match.awards), Tier.T1, match.awards[0].source_url),
    }

    if len(match.awards) > 1:
        total = sum(award.amount for award in match.awards)
        # Our arithmetic over their figures, so T4 and labelled as a sum.
        claims["grant_awards_total"] = make_claim(
            f"{_money(total)} across {len(match.awards)} awards "
            f"(rounds {', '.join(a.round_id for a in match.awards)}) — our sum, not a single award",
            Tier.T4,
            match.awards[0].source_url,
        )
        notes.append(
            f"{len(match.awards)} awards found across rounds; all stored, none overwritten"
        )

    stated = case_study_amount(evidence)
    if stated is not None:
        announced_amounts = {award.amount for award in match.awards}
        if stated not in announced_amounts:
            listed = ", ".join(_money(a) for a in sorted(announced_amounts))
            claims["grant_amount_disagreement"] = make_claim(
                f"The Conexus case study states {_money(stated)}; the round "
                f"announcement(s) state {listed}. Both are recorded with their own "
                f"sources. This needs a human decision — it is not resolved here.",
                Tier.T4,
                PROGRAM_URL,
            )
            notes.append(
                f"DISAGREEMENT: case study {_money(stated)} vs announcement {listed}"
            )

    quotes = [a for a in match.awards if a.quote]
    if quotes:
        claims["announcement_quote"] = make_claim(
            quotes[0].quote, tier_for(quotes[0]), quotes[0].source_url
        )
    return claims, notes


def write_match(match: AwardMatch) -> tuple[str, list[str]]:
    """Write one company's awards. Returns (outcome, notes)."""
    prospect = db.get_prospect(match.prospect_id)
    if prospect is None:
        return "missing", []
    evidence = prospect.get("evidence_file") or {}

    claims, notes = build_block8(match, evidence)
    largest = max(match.awards, key=lambda a: a.amount)
    patch: dict[str, Any] = {
        "grant_amount": largest.amount,
        "grant_round": largest.round_id,
        "grant_year": largest.year,
    }
    merged = deep_merge(evidence, block_patch(BLOCK8_FINANCIAL_SCALE, claims))
    patch["evidence_file"] = merge_notes(merged, "grant_rounds", notes)
    db.update_prospect(match.prospect_id, patch)
    return "written", notes


def _report(
    console: Console,
    awards: list[GrantAward],
    matches: list[AwardMatch],
    unresolved: list[tuple[GrantAward, str]],
    written: int,
    disagreements: list[str],
    dry_run: bool,
) -> None:
    table = Table(title="Grant round extraction", title_justify="left")
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    rounds = sorted({a.round_id for a in awards})
    table.add_row("announcements read", str(len({a.source_url for a in awards})))
    table.add_row("rounds covered", f"{len(rounds)} ({', '.join(rounds)})")
    table.add_row("award records parsed", str(len(awards)))
    table.add_row("companies matched", str(len(matches)))
    table.add_row("awards assigned", str(sum(len(m.awards) for m in matches)))
    table.add_row("unresolved (reported, not written)", str(len(unresolved)))
    table.add_row("source disagreements", str(len(disagreements)))
    table.add_row("prospects written", "-" if dry_run else str(written))
    console.print(table)

    tiers = Counter(f"T{a.tier}" for a in awards)
    console.print(f"\n[bold]Award tiers[/bold]: {dict(sorted(tiers.items()))}")

    multi = [m for m in matches if len(m.awards) > 1]
    if multi:
        console.print(f"\n[bold]Companies with more than one award[/bold] ({len(multi)})")
        for m in multi:
            rounds_won = ", ".join(f"r{a.round_id} {a.amount_text}" for a in m.awards)
            console.print(f"  {m.company_name}: {rounds_won}")

    if disagreements:
        console.print(f"\n[bold yellow]Source disagreements[/bold yellow] ({len(disagreements)})")
        for line in disagreements:
            console.print(f"  {line}")

    if unresolved:
        console.print(f"\n[bold]Unresolved — reported for review, written nowhere[/bold] "
                      f"({len(unresolved)})")
        reasons = Counter(reason.split(":")[0] for _award, reason in unresolved)
        for reason, count in reasons.most_common():
            console.print(f"  {count:>3}  {reason}")
        for award, reason in unresolved[:15]:
            console.print(f"    {award.company_name} ({award.amount_text}, r{award.round_id})"
                          f" — {reason[:70]}")
        if len(unresolved) > 15:
            console.print(f"    … and {len(unresolved) - 15} more")


async def run(dry_run: bool, console: Console) -> int:
    console.print("Reading round announcements …")
    awards = await fetch_announcements()
    if not awards:
        console.print("[red]No award records parsed. The announcement format has changed.[/red]")
        return 1

    prospects = db.list_prospect_identities()
    matches, unresolved = match_to_prospects(awards, prospects)

    written = 0
    disagreements: list[str] = []
    if dry_run:
        for match in matches:
            prospect = db.get_prospect(match.prospect_id) or {}
            _claims, notes = build_block8(match, prospect.get("evidence_file") or {})
            disagreements += [f"{match.company_name}: {n}" for n in notes if n.startswith("DIS")]
    else:
        for match in matches:
            outcome, notes = write_match(match)
            written += outcome == "written"
            disagreements += [f"{match.company_name}: {n}" for n in notes if n.startswith("DIS")]

    _report(console, awards, matches, unresolved, written, disagreements, dry_run)
    if dry_run:
        console.print("\n[yellow]Dry run: nothing was written.[/yellow]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.grant_rounds", description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="parse and report without writing")
    args = parser.parse_args()
    return asyncio.run(run(args.dry_run, Console()))


if __name__ == "__main__":
    sys.exit(main())
