"""revalidate — re-test every stored website against today's acceptance rule.

WHY THIS EXISTS

The Canadian federal grants dataset publishes no websites, so every Canadian
domain in this pipeline was CONSTRUCTED from the company name and then believed
on a check that accepted any one token of that name as a substring. Cedar Valley
Selections Inc. of Windsor, Ontario — a pita-chip manufacturer — was stored
against cedar.com, a US healthcare-payments firm, at confidence 75, and ranked
first among the companies ready to contact.

Tightening the rule fixes the next resolution. It does nothing for the 181
Canadian records already stored under the old one. This re-runs the check
against what is actually served today and writes down what it finds.

WHAT IT DOES TO A RECORD THAT FAILS

Nothing is deleted. A website that no longer passes is nulled on the row and the
company goes to a human with the reason; every claim read from that domain is
tainted, in the evidence file and in the contacts column both, so that nothing
downstream keeps reading a page that turned out to belong to somebody else. The
URL and the full attempt survive in the resolution record.

It re-fetches, because no page snapshots were ever kept — there is no cache of
what these pages said when they were first accepted, and a verdict on remembered
content would be a verdict on nothing.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import httpx
from rich.console import Console
from rich.table import Table

from lib import db
from lib.config import settings
from lib.integrity import (
    registrable_domain,
    taint_claims_from_domain,
    taint_contacts_from_domain,
)
from lib.nodes import RunContext
from tools.harvester.nodes.website import ResolveWebsite

CONFIRMED = "confirmed"
RECOVERED = "recovered"
NULLED = "nulled"
UNCHANGED = "unchanged"

TRUST_FLOOR = 70

TAINT_REASON = (
    "read from {domain}, which no longer passes the resolver's acceptance rule "
    "for this company: the page does not carry the company's full name in "
    "content that describes the business the award describes"
)


def verdict_for(before: dict[str, Any], patch: dict[str, Any]) -> str:
    """What re-running the resolver did to this record.

    "Recovered" is kept separate from "confirmed" because it is a different
    event and a more interesting one: the stored domain was wrong AND the right
    one was found without a search. Cedar Valley Selections is the case —
    cedar.com out, cedarvalleyselections.ca in, purely from trying .ca before
    .com for a Canadian company. Folding that into "nulled" would report a
    repair as a loss.
    """
    was = str(before.get("website") or "")
    now = patch.get("website", was)
    trusted = patch.get("website_confidence", 0) >= TRUST_FLOOR
    if not was:
        return UNCHANGED
    if not now or not trusted:
        return NULLED
    if registrable_domain(now) != registrable_domain(was):
        return RECOVERED
    return CONFIRMED


DEFAULT_CONCURRENCY = 8
"""How many companies to resolve at once.

Each company is a different host, and `RunContext` holds one lock and one
crawl-delay per host, so politeness is per-server however many coroutines are
in flight. Serially this run took 25 seconds a company and was killed by its
own timeout at 210 of 238 — almost all of it spent waiting on one server at a
time while 237 others sat idle."""


async def revalidate(
    prospects: list[dict[str, Any]], console: Console, apply: bool,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[dict[str, Any]]:
    """Re-resolve each company and return one row per outcome, in input order."""
    node = ResolveWebsite()
    rows: list[dict[str, Any] | None] = [None] * len(prospects)
    done = 0
    total = len(prospects)
    gate = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()

    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds, follow_redirects=True,
        headers={"User-Agent": settings.user_agent},
    ) as http:
        ctx = RunContext(http, settings)

        async def one(index: int, prospect: dict[str, Any]) -> None:
            nonlocal done
            name = prospect.get("company_name") or prospect["id"]
            old_url = prospect.get("website")
            async with gate:
                try:
                    result = await node.run(prospect, ctx)
                except Exception as exc:                  # noqa: BLE001
                    rows[index] = {"id": prospect["id"], "company": name,
                                   "old": old_url, "new": None, "verdict": "error",
                                   "reason": f"{type(exc).__name__}: {exc}"[:200]}
                    console.print(f"[red]{name}: {type(exc).__name__}: {exc}[/red]")
                    return
            patch = dict(result.prospect_patch)
            verdict = verdict_for(prospect, patch)
            record = (result.evidence_patch or {}).get("website_resolution") or {}
            rows[index] = {
                "id": prospect["id"],
                "company": name,
                "old": old_url,
                "new": patch.get("website"),
                "verdict": verdict,
                "matched": record.get("full_name_matched"),
                "coherent": bool((record.get("coherence") or {}).get("coherent")),
                "reason": patch.get("needs_review_reason"),
                "tried": [c["candidate"] for c in record.get("candidates_tried", [])],
            }
            # Serialised: one company's row is one write, and two coroutines
            # writing whole evidence files at once would each overwrite the
            # other's with its own pre-read copy.
            async with lock:
                if apply:
                    _write(prospect, patch, result.evidence_patch or {}, verdict, old_url)
                done += 1
                moved = verdict in (NULLED, RECOVERED)
                console.print(
                    f"[dim]{done}/{total}[/dim] {name}: [bold]{verdict}[/bold]"
                    + (f" {old_url} -> {patch.get('website')}" if moved else "")
                )

        await asyncio.gather(*(one(i, p) for i, p in enumerate(prospects)))
    return [r for r in rows if r is not None]


def _write(
    prospect: dict[str, Any], patch: dict[str, Any], evidence_patch: dict[str, Any],
    verdict: str, old_url: str | None,
) -> None:
    """Persist one re-validation, quarantining what the old domain produced."""
    evidence = prospect.get("evidence_file") or {}
    contacts = prospect.get("contacts") or []
    if verdict in (NULLED, RECOVERED) and old_url:
        domain = registrable_domain(old_url)
        reason = TAINT_REASON.format(domain=domain)
        evidence, _ = taint_claims_from_domain(evidence, domain, reason)
        contacts, marked = taint_contacts_from_domain(contacts, domain, reason)
        if marked:
            patch = {**patch, "contacts": contacts}
    for key, value in evidence_patch.items():
        evidence = {**evidence, key: value}
    db.update_prospect(prospect["id"], {**patch, "evidence_file": evidence})


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-test stored websites against the current acceptance rule.")
    parser.add_argument("--adapter", default="canada_gc")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--company")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--apply", action="store_true",
                        help="write the verdicts; without it nothing is stored")
    args = parser.parse_args()
    console = Console()

    prospects = db.list_prospects_full(args.adapter)
    if args.company:
        prospects = [p for p in prospects
                     if args.company.lower() in (p.get("company_name") or "").lower()]
    else:
        prospects = [p for p in prospects if p.get("website")]
    prospects.sort(key=lambda p: -(p.get("signal_score") or 0))
    if args.limit:
        prospects = prospects[:args.limit]

    console.print(f"re-validating {len(prospects)} {args.adapter} website(s)"
                  + ("" if args.apply else " [yellow](dry run)[/yellow]"))
    rows = asyncio.run(revalidate(prospects, console, args.apply, args.concurrency))

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    table = Table(title="website re-validation")
    table.add_column("verdict")
    table.add_column("companies", justify="right")
    for verdict, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        table.add_row(verdict, str(count))
    console.print(table)

    wrong = [r for r in rows if r["verdict"] in (NULLED, RECOVERED)]
    if wrong:
        detail = Table(title=f"{len(wrong)} website(s) withdrawn or replaced")
        for column in ("company", "old URL", "replaced by",
                       "full name on the page", "coherent"):
            detail.add_column(column, overflow="fold")
        for row in wrong[:200]:
            detail.add_row(str(row["company"])[:34], str(row["old"] or "")[:40],
                           str(row.get("new") or "—")[:40],
                           str(row.get("matched") or "no"),
                           "yes" if row.get("coherent") else "no")
        console.print(detail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
