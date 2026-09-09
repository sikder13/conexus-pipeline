"""Gather market context for the industry families our prospects sit in.

    python -m tools.market --dry-run        # what would be fetched, and for whom
    python -m tools.market                  # gather every family a P1 sits in
    python -m tools.market --family metal_fabrication
    python -m tools.market --force          # re-gather families already cached

WHY THIS IS A TOOL AND NOT A NODE

Nodes research one prospect. This researches a segment, and the answer is the
same for every company in it, so running it per prospect would make one request
per company for one answer. It is keyed by family, cached, and reused.

WHAT MAKES IT SAFE TO USE

Everything the model returns must carry a verbatim quote from a page we
actually fetched, and the quote is checked against that page before anything is
stored. A statement whose quote is not found is discarded and the discard is
recorded. That check is what lets a market paragraph appear in a document beside
sourced company facts without lowering the standard of the page it sits on.

A family whose sources yield nothing gets the recorded absence, and the analysis
is told to say so rather than reach for what it already knows about the segment.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import anthropic
import httpx
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table

from lib import adapters, db, market, peers
from lib.config import settings
from lib.nodes import FetchError, RobotsDisallowed, RunContext
from tools.analyst.main import ANALYST_MODEL, PRICE_PER_MTOK, Spend
from tools.drafter import main as drafter

EXTRACT_TOKENS = 4000
"""No reasoning budget needed here — the task is quotation, not analysis."""

CEILING = 10.00
"""What one gathering run may cost before it stops and asks."""


def families_in_play(priorities: tuple[str, ...] = ("P1",),
                    adapter: str | None = None) -> list[str]:
    """The families our prospects actually sit in, so nothing else is fetched.

    Gathering context for a segment nobody in the queue belongs to is a request
    to somebody's server that buys us nothing.
    """
    seen: dict[str, int] = {}
    for prospect in db.list_prospects_full(adapter):
        if prospect.get("priority") not in priorities:
            continue
        family = peers.family_of(prospect).key
        seen[family] = seen.get(family, 0) + 1
    # 'unclassified' is deliberately excluded: a company we could not place has
    # no segment, and inventing one for it is the market equivalent of comparing
    # it against the other companies we could not place.
    return sorted(k for k in seen
                  if k != "unclassified" and market.sources_for(k, adapter))


def _text_of(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    return soup.get_text(" ")


async def fetch_sources(
    family: str, ctx: RunContext, console: Console, adapter: str | None = None
) -> tuple[list[tuple[market.Source, str]], dict[str, str], list[str]]:
    """Read every source for one family, keeping what came back and what did not."""
    fetched: list[tuple[market.Source, str]] = []
    texts: dict[str, str] = {}
    failures: list[str] = []
    for source in market.sources_for(family, adapter):
        try:
            response = await ctx.fetch(source.url)
        except (FetchError, RobotsDisallowed) as exc:
            failures.append(f"{source.url}: {type(exc).__name__}")
            continue
        if response.status_code >= 400:
            failures.append(f"{source.url}: HTTP {response.status_code}")
            continue
        text = market.normalise(_text_of(response.text))
        fetched.append((source, text))
        texts[source.url] = text
    return fetched, texts, failures


async def extract(client: Any, prompt: str, spend: Spend) -> list[dict[str, Any]]:
    """Ask for the entries, and refuse a reply we cannot read."""
    response = await client.messages.create(
        model=ANALYST_MODEL,
        max_tokens=EXTRACT_TOKENS,
        system=market.SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    if getattr(response, "usage", None) is not None:
        spend.record(response.usage)
    raw = " ".join(b.text for b in response.content
                   if getattr(b, "type", "") == "text").strip()
    blocks = drafter.parse_delimited(raw)
    for block in blocks:
        if block.kind == "MAP":
            parsed = drafter._parse_json(block.body, "the market entries")
            entries = parsed.get("entries")
            if not isinstance(entries, list):
                raise drafter.ProseRejected("the reply carried no entries list")
            return entries
    raise drafter.ProseRejected("the reply carried no <<<MAP market>>> block")


async def gather_family(
    family: str, ctx: RunContext, client: Any, spend: Spend, console: Console,
    adapter: str | None = None,
) -> dict[str, Any]:
    """Fetch, read, check and store one family's context."""
    sources = market.sources_for(family, adapter)
    fetched, texts, failures = await fetch_sources(family, ctx, console, adapter)
    for failure in failures:
        console.print(f"  [yellow]source unread[/yellow] {failure}")

    if not fetched:
        record = market.summarise(family, [], failures, sources)
    else:
        words = peers.FAMILY_WORDS.get(family, family)
        try:
            entries = await extract(
                client, market.build_prompt(family, words, fetched), spend)
        except drafter.ProseRejected as exc:
            console.print(f"  [yellow]reply unreadable:[/yellow] {exc}")
            entries = []
        kept, dropped = market.verify(entries, texts, sources)
        record = market.summarise(family, kept, dropped + failures, sources)

    db.upsert_market_context({
        "family": market.context_key(family, adapter),
        "statements": record["statements"],
        "sources_read": record["sources_read"],
        "discarded": record["discarded"],
        "note": record["note"] or None,
        "model": ANALYST_MODEL,
    })
    return record


def estimate(count: int) -> float:
    """What a gathering run costs. Reading is cheap; there is no reasoning here."""
    per_family = (20_000 * PRICE_PER_MTOK[0] + 900 * PRICE_PER_MTOK[1]) / 1_000_000
    return round(count * per_family * 1.2, 2)


async def _run(args: argparse.Namespace, console: Console) -> int:
    if args.family:
        families = [args.family]
        if not market.sources_for(args.family, args.adapter):
            console.print(f"[red]no sources are curated for {args.family!r}.[/red] "
                          f"Add them to lib/market.py — deliberately a visible edit.")
            return 1
    else:
        families = families_in_play(adapter=args.adapter)

    cached = {row["family"] for row in db.all_market_context()}
    if not args.force:
        skipped = [f for f in families if f in cached]
        families = [f for f in families if f not in cached]
        for family in skipped:
            console.print(f"[dim]{family}: already gathered; --force to re-read[/dim]")

    table = Table(title=f"{len(families)} family/families to gather",
                  title_justify="left")
    for column in ("Family", "Sources"):
        table.add_column(column)
    for family in families:
        table.add_row(family, str(len(market.sources_for(family, args.adapter))))
    console.print(table)

    projected = estimate(len(families))
    console.print(f"\nEstimated spend: [bold]${projected:.2f}[/bold] "
                  f"(ceiling ${CEILING:.2f})")
    if projected > CEILING:
        console.print("[red]Stopping: over the ceiling.[/red] Use --family to "
                      "take it a segment at a time.")
        return 1
    if args.dry_run or not families:
        console.print("\n[dim]nothing fetched, nothing written.[/dim]")
        return 0
    if not settings.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY is not set.[/red]")
        return 1

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    spend = Spend()
    async with httpx.AsyncClient(follow_redirects=True) as http:
        ctx = RunContext(http, settings)
        for family in families:
            console.print(f"\n[cyan]{family}[/cyan]")
            record = await gather_family(family, ctx, client, spend, console,
                                         args.adapter)
            if record["note"]:
                console.print(f"  [yellow]{record['note']}[/yellow]")
            for entry in record["statements"]:
                console.print(f"  [green]{entry['dimension']}[/green]: "
                              f"{entry['statement'][:110]}")
            for reason in record["discarded"][:3]:
                console.print(f"  [dim]discarded: {reason[:110]}[/dim]")
    console.print(f"\n[dim]API spend: {spend.line()}[/dim]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gather per-family market context from curated sources.")
    parser.add_argument("--family", default=None, help="gather one family only")
    adapters.add_argument(parser)
    parser.add_argument("--force", action="store_true",
                        help="re-read families already cached")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be fetched; write nothing")
    args = parser.parse_args()
    return asyncio.run(_run(args, Console()))


if __name__ == "__main__":
    raise SystemExit(main())
