"""Run the adversarial claim checker over the claims that matter.

    python -m tools.claimcheck --dry-run       # what would be checked, and the cost
    python -m tools.claimcheck --limit 10

WHICH CLAIMS

Not all of them. Checking 800 claims per run would cost more than it protects,
and most never reach a prospect. Two sets are checked:

* every block7 person claim, regardless of anything else, because a wrong name
  is the one error that ends a conversation; and
* every T1 claim in the blocks a draft actually cites from, because those are
  the sentences that will be asserted as fact.

WHERE THE SOURCE TEXT COMES FROM

The cited page, fetched once per URL and reused across every claim citing it.
Fetching is politeness-limited exactly like every other outbound request, and a
page we may not fetch yields no verdict rather than a guessed one — an absent
verdict is honest, and Layer 4 already treats "unchecked" differently from
"failed".
"""

from __future__ import annotations

import argparse
import asyncio
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table

from lib import db
from lib.claimcheck import CheckResult, apply_verdict, check_claim
from lib.claims import Tier
from lib.config import settings
from lib.evidence import BLOCK1_WHAT_THEY_MAKE, BLOCK2_GRANT_FUNDED, BLOCK7_PEOPLE
from lib.integrity import evidence_integrity, is_usable, iter_all_claims
from lib.nodes import RunContext

CITED_BLOCKS = (BLOCK1_WHAT_THEY_MAKE, BLOCK2_GRANT_FUNDED, BLOCK7_PEOPLE)
"""Blocks a draft asserts from. block4 front-door observations and block6 tech
detections are used to reason, not quoted at a prospect, so they are not checked
here — checking them would triple the cost to protect sentences nobody sends."""

HAIKU_IN, HAIKU_OUT = 1.00 / 1_000_000, 5.00 / 1_000_000


def claims_to_check(prospect: dict[str, Any]) -> list[tuple[str, dict]]:
    """Person claims plus the T1 claims a draft would assert."""
    out = []
    for path, claim in iter_all_claims(prospect.get("evidence_file") or {}):
        trimmed = path.removeprefix("evidence_file.")
        block = trimmed.split(".")[0]
        if block not in CITED_BLOCKS or not is_usable(claim):
            continue
        if claim.get("claimcheck"):
            continue
        is_person = block == BLOCK7_PEOPLE and "named_people" in trimmed
        if is_person or claim.get("tier") == int(Tier.T1):
            out.append((trimmed, claim))
    return out


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(html or "", "html.parser").get_text(" "))


async def fetch_sources(urls: set[str], ctx: RunContext, console: Console) -> dict[str, str]:
    """Fetch each cited page once. A page we may not read yields no text."""
    pages: dict[str, str] = {}
    for url in sorted(urls):
        try:
            response = await ctx.fetch(url)
            pages[url] = _text(response.text) if response.status_code < 400 else ""
        except Exception as exc:
            console.print(f"  [dim]{type(exc).__name__} on {url[:60]}[/dim]")
            pages[url] = ""
    return pages


def _set_claim(evidence: dict, path: str, updated: dict) -> dict:
    def walk(node: Any, here: str) -> Any:
        if isinstance(node, dict):
            if "value" in node and here == path:
                return updated
            return {k: walk(v, f"{here}.{k}" if here else k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{here}[{i}]") for i, v in enumerate(node)]
        return node

    return walk(evidence, "")


async def _run(limit: int | None, dry_run: bool, console: Console) -> int:
    prospects = [
        p for p in db.list_prospects_full()
        if p.get("priority") == "P1" and evidence_integrity(p).passing
    ]
    prospects.sort(key=lambda p: (
        (p.get("drive_minutes") or 999) > 90,
        -(p.get("signal_score") or 0),
        p.get("drive_minutes") or 999,
    ))
    if limit:
        prospects = prospects[:limit]

    plan = [(p, claims_to_check(p)) for p in prospects]
    total = sum(len(c) for _p, c in plan)
    est = total * ((4000 * HAIKU_IN) + (120 * HAIKU_OUT))
    console.print(
        f"{len(prospects)} prospect(s), {total} unchecked claim(s), "
        f"estimated cost ${est:.2f}\n"
    )
    if dry_run:
        table = Table(title="Would check", title_justify="left")
        table.add_column("Company")
        table.add_column("Claims", justify="right")
        for prospect, claims in plan:
            table.add_row(str(prospect.get("company_name"))[:40], str(len(claims)))
        console.print(table)
        return 0

    if not settings.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY is not set.[/red]")
        return 1

    verdicts: dict[str, int] = {}
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds, follow_redirects=True,
        headers={"User-Agent": settings.user_agent},
    ) as http:
        ctx = RunContext(http, settings)
        for prospect, claims in plan:
            if not claims:
                continue
            console.print(f"[cyan]{prospect.get('company_name')}[/cyan] — {len(claims)} claim(s)")
            pages = await fetch_sources(
                {str(c.get("source_url")) for _p, c in claims}, ctx, console
            )
            evidence = prospect.get("evidence_file") or {}
            for path, claim in claims:
                source = pages.get(str(claim.get("source_url")), "")
                try:
                    result = await check_claim(claim, source, path)
                except Exception as exc:
                    result = CheckResult(
                        verdict="unsupported", reason=f"checker error: {type(exc).__name__}"
                    )
                evidence = _set_claim(evidence, path, apply_verdict(claim, result))
                verdicts[result.verdict] = verdicts.get(result.verdict, 0) + 1
            db.update_prospect(prospect["id"], {"evidence_file": evidence})
            console.print(f"  {verdicts}")

    console.print(f"\n[bold]verdicts:[/bold] {verdicts}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Adversarially check claims.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(_run(args.limit, args.dry_run, Console()))


if __name__ == "__main__":
    raise SystemExit(main())
