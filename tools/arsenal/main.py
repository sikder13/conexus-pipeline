"""One company's whole arsenal, in one pass, when somebody is about to work it.

    python -m tools.arsenal --company "Mursix"
    python -m tools.arsenal --company "Mursix" --dry-run

WHY ON DEMAND RATHER THAN IN A BATCH

Outreach runs at about twenty companies a week. Pre-generating sixty-one
letters, sixty-one dashboards and a video script each buys nothing that could
not be bought the morning somebody needs it, and it spends real money on
artifacts for companies that may never be worked — and worse, it freezes them.
An artifact generated three weeks before the call quotes the evidence as it
stood three weeks ago; one generated the same morning quotes what we know now.

So there is no batch mode here, deliberately. One company, one command, four
files, about fifteen cents.

WHAT IT PRODUCES

1. the fragment letter, through the outbound gate (`tools/drafter`);
2. the FedEx one-pager, print-ready, with the QR code (`tools/report`);
3. the dashboard, compiled from the analysis's stored spec (`lib/dashboard`);
4. the ninety-second video script (`tools/loom`).

Each step is the code that already existed and is already tested. This module
sequences them for one company and reports what it could not do, rather than
being a fifth generator with its own rules.

WHAT IT REFUSES

A company with no live analysis. Three of the four artifacts quote that
analysis — the letter takes one computed figure from it, the dashboard compiles
its stored spec, the script narrates its sensitivity — so without one this
command would produce a page with nothing on it. It says so and stops.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import anthropic
from rich.console import Console

from lib import canary, dashboard, db, finmodel, pricing
from lib.config import settings
from tools.drafter import main as drafter
from tools.loom import main as loom
from tools.report import main as report

ONE_PAGER_DIR = report.ONE_PAGER_DIR
SCRIPT_DIR = loom.SCRIPT_DIR


class NoAnalysis(RuntimeError):
    """Three of the four artifacts quote an analysis. There is not one."""


def find_company(name: str) -> dict[str, Any]:
    """The one prospect this command is about, or a refusal naming the near misses."""
    needle = name.strip().lower()
    rows = [p for p in db.list_prospects_full()
            if needle in str(p.get("company_name") or "").lower()]
    if not rows:
        raise SystemExit(f"no company matching {name!r}")
    if len(rows) > 1:
        exact = [p for p in rows if str(p.get("company_name") or "").lower() == needle]
        if len(exact) != 1:
            names = ", ".join(str(p.get("company_name")) for p in rows[:6])
            raise SystemExit(f"{len(rows)} companies match {name!r}: {names}")
        rows = exact
    return rows[0]


def live_analysis(prospect_id: str) -> dict[str, Any] | None:
    """The analysis that currently counts for this company."""
    found = [a for a in db.artifacts_for(prospect_id)
             if a.get("kind") == "analysis" and a.get("status") in ("sendable", "held")]
    return max(found, key=lambda a: a["created_at"]) if found else None


def live_letter(prospect_id: str) -> dict[str, Any] | None:
    found = [a for a in db.artifacts_for(prospect_id)
             if a.get("kind") == "letter" and a.get("status") == "sendable"]
    return max(found, key=lambda a: a["created_at"]) if found else None


async def build(prospect: dict[str, Any], console: Console,
                force: bool = False) -> dict[str, str]:
    """Every piece, in the order each one depends on the last."""
    analysis = live_analysis(prospect["id"])
    if analysis is None:
        raise NoAnalysis(
            f"{prospect.get('company_name')} has no live analysis. The letter, the "
            f"dashboard and the script all quote one. Run "
            f"`python -m tools.analyst --company \"{prospect.get('company_name')}\"` "
            f"first.")

    made: dict[str, str] = {}
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    spend = drafter.Spend()

    # 1 — the letter, unless one already passed. Nothing is re-rolled: the
    # generator is not deterministic and a second pass over a sendable artifact
    # is a coin flip that can only lose.
    letter = live_letter(prospect["id"])
    if letter and not force:
        console.print("[dim]letter: already sendable, left alone[/dim]")
        made["letter"] = "existing"
    else:
        console.print("[cyan]letter[/cyan]")
        rc = await drafter._run_letters(
            1, False, console, only_blocked=not force,
            adapter=prospect.get("source_adapter"),
            company=str(prospect.get("company_name")))
        made["letter"] = "written" if rc == 0 else "failed"

    # 2 — the one-pager, which is the letter set for print. Needs step 1.
    try:
        path = report.build_one_pager(
            prospect, db.artifacts_for(prospect["id"]),
            ONE_PAGER_DIR / f"{dashboard.token_for(prospect)}.pdf")
        made["one_pager"] = str(path)
        console.print(f"[green]wrote[/green] {path}")
    except report.NoLetter as exc:
        made["one_pager"] = f"skipped: {exc}"
        console.print(f"[yellow]one-pager skipped:[/yellow] {exc}")

    # 3 — the dashboard, compiled from the spec the analysis stored. Free.
    specs = (analysis.get("gate_map") or {}).get("models") or []
    if specs:
        spec = finmodel.ModelSpec.model_validate(specs[0])
        footnote = ((analysis.get("gate_map") or {}).get("case") or {}).get(
            "anchor", {}).get("detail", "")
        path = dashboard.write(
            spec, prospect,
            currency=pricing.currency_for(prospect.get("source_adapter")),
            footnote=footnote)
        made["dashboard"] = str(path)
        console.print(f"[green]wrote[/green] {path}")
    else:
        made["dashboard"] = "skipped: the analysis stored no model spec"
        console.print("[yellow]dashboard skipped:[/yellow] no stored model spec")

    # 4 — the video script, narrated over that dashboard.
    beats = loom.beats_for(prospect, analysis)
    if not beats.usable:
        made["script"] = "skipped: no sensitivity recorded, so no slider to move"
        console.print("[yellow]script skipped:[/yellow] no slider to move")
    else:
        loom_spend = loom.Spend()
        text, failures, feedback = "", ["not attempted"], []
        for _attempt in range(2):
            text, failures = await loom.write_script(
                prospect, beats, client, loom_spend, feedback)
            if not failures:
                break
            feedback = failures
        if failures:
            made["script"] = f"skipped: {failures[0]}"
            console.print(f"[yellow]script skipped:[/yellow] {failures[0]}")
        else:
            path = loom.script_path(prospect)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(loom.header(beats) + text + "\n", encoding="utf-8")
            made["script"] = str(path)
            console.print(f"[green]wrote[/green] {path}")
        console.print(f"[dim]script spend: {loom_spend.line()}[/dim]")

    console.print(f"[dim]letter spend: {spend.line()}[/dim]")
    return made


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.arsenal", description=__doc__)
    parser.add_argument("--company", required=True,
                        help="the company to build the arsenal for")
    parser.add_argument("--force", action="store_true",
                        help="rewrite the letter even if one already passed")
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would be built; generate nothing")
    args = parser.parse_args()
    console = Console()

    prospect = find_company(args.company)
    console.print(f"[bold]{prospect.get('company_name')}[/bold] "
                  f"({prospect.get('source_adapter')})")
    analysis = live_analysis(prospect["id"])
    console.print(f"  analysis: {'yes' if analysis else 'NONE'} · "
                  f"letter: {'sendable' if live_letter(prospect['id']) else 'none'} · "
                  f"dashboard token: {dashboard.token_for(prospect)}")
    if args.dry_run:
        console.print("[dim]--dry-run: nothing generated.[/dim]")
        return 0
    if not settings.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY is not set.[/red]")
        return 1
    if canary.read_state().halted:
        console.print("[yellow]Pipeline is halted; artifacts may be built but "
                      "nothing may be sent.[/yellow]")

    try:
        made = asyncio.run(build(prospect, console, args.force))
    except NoAnalysis as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    console.print("\n[bold]Arsenal[/bold]")
    for key, value in made.items():
        console.print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
