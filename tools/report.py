"""Pass A results report — what the run actually produced.

Run from the repo root:

    python -m tools.report

Prints the priority distribution, the drive-time cut, the top prospects inside
ninety minutes, and the grant coverage. It reads and never writes, so it is safe
to run against a live database at any time.
"""

from __future__ import annotations

from collections import Counter

from rich.console import Console
from rich.table import Table

from lib import db
from lib.evidence import BLOCK7_PEOPLE, BLOCK8_FINANCIAL_SCALE

DRIVE_LIMIT_MINUTES = 90
TOP_N = 30


def _contact_tier(prospect: dict) -> str:
    """Read the routing tier from block7, where the people node records it.

    It is a claim rather than a column: it is our inference from a headcount
    (T4), and it carries the headcount it was derived from, so a reviewer can
    see why a company was routed to operations instead of the owner.
    """
    block7 = (prospect.get("evidence_file") or {}).get(BLOCK7_PEOPLE) or {}
    value = (block7.get("contact_tier") or {}).get("value")
    return str(value) if value else "unset"


def _named_person(prospect: dict) -> str:
    block7 = (prospect.get("evidence_file") or {}).get(BLOCK7_PEOPLE) or {}
    people = block7.get("named_people") or []
    if not people:
        return "—"
    first = people[0]
    name = (first.get("name") or {}).get("value") if isinstance(first, dict) else None
    role = (first.get("role") or {}).get("value") if isinstance(first, dict) else None
    if not name:
        return "—"
    return f"{name}, {role}" if role else str(name)


def priority_table(prospects: list[dict], title: str) -> Table:
    counts = Counter(p.get("priority") or "unscored" for p in prospects)
    table = Table(title=title, title_justify="left")
    table.add_column("Priority", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Share", justify="right")
    total = len(prospects) or 1
    for key in ("P1", "P2", "P3", "unscored"):
        if counts.get(key):
            table.add_row(key, f"{counts[key]:,}", f"{counts[key] / total:.1%}")
    table.add_row("[bold]total[/bold]", f"[bold]{len(prospects):,}[/bold]", "")
    return table


def top_table(prospects: list[dict]) -> Table:
    inside = [
        p for p in prospects
        if p.get("drive_minutes") is not None
        and p["drive_minutes"] <= DRIVE_LIMIT_MINUTES
    ]
    ranked = sorted(
        inside,
        key=lambda p: (
            {"P1": 0, "P2": 1, "P3": 2}.get(p.get("priority"), 3),
            -(p.get("signal_score") or 0),
            p.get("drive_minutes") or 999,
        ),
    )[:TOP_N]

    table = Table(
        title=f"Top {TOP_N} prospects within {DRIVE_LIMIT_MINUTES} minutes",
        title_justify="left",
    )
    table.add_column("#", justify="right")
    table.add_column("Company", overflow="fold")
    table.add_column("Pri", justify="center")
    table.add_column("Score", justify="right")
    table.add_column("Drive", justify="right")
    table.add_column("Tier", no_wrap=True)
    table.add_column("Named contact", overflow="fold")
    table.add_column("Grant", justify="right")

    for rank, p in enumerate(ranked, 1):
        grant = p.get("grant_amount")
        table.add_row(
            str(rank),
            str(p.get("company_name") or "?"),
            str(p.get("priority") or "—"),
            str(p.get("signal_score") if p.get("signal_score") is not None else "—"),
            f"{p['drive_minutes']:.0f}m",
            _contact_tier(p),
            _named_person(p),
            f"${grant:,.0f}" if grant else "—",
        )
    return table


def grant_table(prospects: list[dict]) -> Table:
    with_grant = [p for p in prospects if p.get("grant_amount")]
    tiers: Counter = Counter()
    disagreements = 0
    multi = 0
    for p in prospects:
        block8 = (p.get("evidence_file") or {}).get(BLOCK8_FINANCIAL_SCALE) or {}
        for entry in block8.get("grant_awards") or []:
            # Each award is itself a claim: the value states the amount and the
            # round, and the tier says whether it came from the programme
            # administrator or from press coverage of the same round.
            if isinstance(entry, dict) and "tier" in entry:
                tiers[entry["tier"]] += 1
        if block8.get("grant_amount_disagreement"):
            disagreements += 1
        count = (block8.get("grant_award_count") or {}).get("value")
        if isinstance(count, int) and count > 1:
            multi += 1

    table = Table(title="Grant coverage", title_justify="left")
    table.add_column("Measure", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("prospects with a grant amount", f"{len(with_grant):,}")
    table.add_row("coverage", f"{len(with_grant) / (len(prospects) or 1):.1%}")
    table.add_row("award records, tier 1 (programme administrator)", f"{tiers.get(1, 0):,}")
    table.add_row("award records, tier 2 (press)", f"{tiers.get(2, 0):,}")
    table.add_row("companies with more than one award", f"{multi:,}")
    table.add_row("amount disagreements recorded", f"{disagreements:,}")
    return table


def contact_tier_table(prospects: list[dict]) -> Table:
    counts = Counter(_contact_tier(p) for p in prospects)
    table = Table(title="Contact routing", title_justify="left")
    table.add_column("Tier", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Meaning", overflow="fold")
    meanings = {
        "owner_direct": "under 100 staff, or unknown — approach the owner",
        "ops_first": "100-250 staff — approach operations, not the owner",
        "unset": "no headcount signal and no default applied",
    }
    for tier, count in counts.most_common():
        table.add_row(tier, f"{count:,}", meanings.get(tier, ""))
    return table


def main() -> int:
    console = Console(width=150)
    prospects = db.list_prospects_full()
    inside = [
        p for p in prospects
        if p.get("drive_minutes") is not None
        and p["drive_minutes"] <= DRIVE_LIMIT_MINUTES
    ]

    console.print(priority_table(prospects, f"Priority across all {len(prospects)} prospects"))
    console.print()
    console.print(priority_table(inside, f"Priority within {DRIVE_LIMIT_MINUTES} minutes"))
    console.print()
    console.print(contact_tier_table(prospects))
    console.print()
    console.print(grant_table(prospects))
    console.print()
    console.print(top_table(prospects))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
