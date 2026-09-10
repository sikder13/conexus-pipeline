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
from collections import Counter
from typing import Any

import httpx
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table

from lib import adapters, db
from lib.claimcheck import CheckResult, apply_verdict, check_claim
from lib.claims import Tier, as_derivation, is_derivation
from lib.config import settings
from lib.evidence import (
    BLOCK1_WHAT_THEY_MAKE,
    BLOCK2_GRANT_FUNDED,
    BLOCK7_PEOPLE,
    is_derived_path,
)
from lib.integrity import evidence_integrity, is_usable, iter_all_claims
from lib.nodes import RunContext
from lib.persongate import WRONG_SUBJECT_REASON, source_subject_matches

CITED_BLOCKS = (BLOCK1_WHAT_THEY_MAKE, BLOCK2_GRANT_FUNDED, BLOCK7_PEOPLE)
"""Blocks a draft asserts from. block4 front-door observations and block6 tech
detections are used to reason, not quoted at a prospect, so they are not checked
here — checking them would triple the cost to protect sentences nobody sends."""

HAIKU_IN, HAIKU_OUT = 1.00 / 1_000_000, 5.00 / 1_000_000


def claims_to_check(
    prospect: dict[str, Any], force: bool = False
) -> list[tuple[str, dict]]:
    """Person claims plus the T1 claims a draft would assert.

    Derivations are excluded BY DESIGN, not filtered as noise. The checker
    answers one question — does this source text say this? — and our own
    computation over a page is not something the page says. Sixty-six Canadian
    flags and labels were refused for exactly that, correctly, and the fix is
    not to teach the checker about flags but to stop asking it.
    """
    out = []
    for path, claim in iter_all_claims(prospect.get("evidence_file") or {}):
        trimmed = path.removeprefix("evidence_file.")
        block = trimmed.split(".")[0]
        if block not in CITED_BLOCKS or not is_usable(claim):
            continue
        if is_derivation(claim) or is_derived_path(trimmed):
            continue
        if claim.get("claimcheck") and not (force and _needs_recheck(claim)):
            continue
        is_person = block == BLOCK7_PEOPLE and "named_people" in trimmed
        if is_person or claim.get("tier") == int(Tier.T1):
            out.append((trimmed, claim))
    return out


def _needs_recheck(claim: dict[str, Any]) -> bool:
    """Whether an already-checked claim deserves a second look.

    Two cases, and only two. A claim whose SOURCE was repaired was checked
    against the wrong page, so its verdict is about a page it no longer cites. A
    claim whose reply could not be READ was never checked at all; the verdict is
    a fail-closed default wearing a finding's clothes.

    Everything else keeps the verdict it earned. Re-checking a claim that was
    read correctly against the right page costs money to learn nothing.
    """
    return bool(claim.get("source_repaired") or claim.get("claimcheck_parse_failed"))


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


async def _run(limit: int | None, dry_run: bool, console: Console,
               adapter: str | None = None, priorities: tuple[str, ...] = ("P1",),
               force: bool = False) -> int:
    prospects = [
        p for p in db.list_prospects_full(adapter)
        if p.get("priority") in priorities and evidence_integrity(p).passing
    ]
    prospects.sort(key=lambda p: (
        (p.get("drive_minutes") or 999) > 90,
        -(p.get("signal_score") or 0),
        p.get("drive_minutes") or 999,
    ))
    if limit:
        prospects = prospects[:limit]

    plan = [(p, claims_to_check(p, force)) for p in prospects]
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


def mark_derivations(console: Console, adapter: str | None, dry_run: bool) -> int:
    """Mark every stored derivation, and drop the verdict it should never have had.

    Two things at once, and they belong together: a claim that is a derivation
    gets the marker so it is never checked again, and any verdict it already
    carries is removed — because that verdict is an answer to a question nobody
    should have asked, and leaving it in place would keep it barring the claim
    from outbound for a reason that is not true.
    """
    counts: Counter = Counter()

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            if "value" in node:
                if is_derived_path(path) and not is_derivation(node):
                    counts["marked"] += 1
                    cleaned = {
                        k: v for k, v in node.items()
                        if not k.startswith("claimcheck")
                    }
                    if len(cleaned) != len(node):
                        counts["verdict dropped"] += 1
                    return as_derivation(
                        cleaned, "our computation, marked in a backfill after the "
                                 "adversarial checker was found refusing it")
                return node
            return {k: walk(v, f"{path}.{k}") for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        return node

    prospects = db.list_prospects_full(adapter)
    changed = 0
    for prospect in prospects:
        before = counts["marked"]
        updated = walk(prospect.get("evidence_file") or {}, "")
        if counts["marked"] > before:
            changed += 1
            if not dry_run:
                db.update_prospect(prospect["id"], {"evidence_file": updated})
    console.print(
        f"[green]{counts['marked']}[/green] derivation(s) marked across {changed} "
        f"of {len(prospects)} companies · {counts['verdict dropped']} carried a "
        f"verdict that has been dropped"
        + (" [yellow](dry run: nothing written)[/yellow]" if dry_run else ""))
    return 0


async def sweep_people(
    console: Console, adapter: str | None, dry_run: bool
) -> int:
    """Taint every person claim read from a page about somebody else.

    Two stages, in this order, because the cheap one settles most of it:

    1. Does the source domain carry the company's name? Most person claims come
       off the company's own site and pass here without a request.
    2. If not — press coverage and directory pages legitimately do not — fetch
       the page once and ask whether it names the company.

    Only a claim that fails both is tainted, with the reason recorded. Nothing
    is deleted: `lib/integrity.py` is explicit that quarantine is not
    destruction, and the thing that looks like noise today is what explains the
    mistake tomorrow.
    """
    prospects = db.list_prospects_full(adapter)
    counts: Counter = Counter()
    to_fetch: dict[str, list] = {}

    for prospect in prospects:
        company = prospect.get("company_name")
        for path, claim in iter_all_claims(prospect.get("evidence_file") or {}):
            trimmed = path.removeprefix("evidence_file.")
            if "named_people" not in trimmed or not is_usable(claim):
                continue
            counts["people"] += 1
            matched, _how, _why = source_subject_matches(
                company, claim.get("source_url"))
            if matched:
                counts["passed on domain"] += 1
                continue
            to_fetch.setdefault(str(claim.get("source_url")), []).append(
                (prospect["id"], trimmed, company))

    console.print(
        f"{counts['people']} person claim(s) · {counts['passed on domain']} "
        f"confirmed by domain · {len(to_fetch)} page(s) to read")

    pages: dict[str, str] = {}
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds, follow_redirects=True,
        headers={"User-Agent": settings.user_agent},
    ) as http:
        ctx = RunContext(http, settings)
        pages = await fetch_sources(set(to_fetch), ctx, console)

    tainted: dict[str, list[tuple[str, str]]] = {}
    for url, entries in to_fetch.items():
        text = pages.get(url, "")
        for prospect_id, path, company in entries:
            matched, _how, why = source_subject_matches(company, url, text or None)
            if matched:
                counts["passed in text"] += 1
                continue
            counts["tainted"] += 1
            tainted.setdefault(prospect_id, []).append((path, why))

    for prospect in prospects:
        marks = tainted.get(prospect["id"])
        if not marks or dry_run:
            continue
        evidence = prospect.get("evidence_file") or {}
        for path, why in marks:
            current = _claim_at(evidence, path)
            if current is None:
                continue
            evidence = _set_claim(evidence, path, {
                **current, "tainted": True,
                "taint_reason": WRONG_SUBJECT_REASON.format(why=why),
            })
        db.update_prospect(prospect["id"], {"evidence_file": evidence})

    console.print(
        f"[green]{counts['passed on domain'] + counts['passed in text']}[/green] "
        f"confirmed · [red]{counts['tainted']}[/red] tainted as read from a page "
        f"about another company"
        + (" [yellow](dry run: nothing written)[/yellow]" if dry_run else ""))
    return 0


def _claim_at(evidence: dict, path: str) -> dict | None:
    for found_path, claim in iter_all_claims(evidence):
        if found_path.removeprefix("evidence_file.") == path:
            return claim
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Adversarially check claims.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--priorities", default="P1",
                        help="comma-separated priorities to check (default P1)")
    parser.add_argument("--force", action="store_true",
                        help="re-check claims whose source was repaired or whose "
                             "reply could not be read; leaves the rest alone")
    parser.add_argument("--sweep-people", action="store_true",
                        help="taint person claims read from a page about "
                             "another company")
    parser.add_argument("--mark-derivations", action="store_true",
                        help="mark stored derivations so they are never checked, "
                             "and drop any verdict they already carry")
    adapters.add_argument(parser)
    args = parser.parse_args()
    console = Console()
    console.print(f"Scope: [bold]{adapters.words(args.adapter)}[/bold]")
    if args.mark_derivations:
        return mark_derivations(console, args.adapter, args.dry_run)
    if args.sweep_people:
        return asyncio.run(sweep_people(console, args.adapter, args.dry_run))
    priorities = tuple(
        p.strip().upper() for p in str(args.priorities).split(",") if p.strip())
    return asyncio.run(_run(args.limit, args.dry_run, console, args.adapter,
                            priorities, args.force))


if __name__ == "__main__":
    raise SystemExit(main())
