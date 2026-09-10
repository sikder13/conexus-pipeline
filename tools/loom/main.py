"""Ninety-second screen-share scripts, read aloud over the company's dashboard.

    python -m tools.loom --dry-run          # the beats, computed; nothing generated
    python -m tools.loom --limit 10
    python -m tools.loom --adapter canada_gc

WHY A SCRIPT AND NOT A VIDEO

Because the operator is the one on the recording and always will be. What this
produces is a beat sheet with the words in it: no face, no slides, one browser
tab showing their own dashboard, and ninety seconds of somebody talking through
a number that is about them.

WHY NINETY SECONDS

It is the length at which a Loom sent cold is watched rather than skimmed, and
it is short enough that the script has to choose. Four beats fit and a fifth
does not:

1. **The hook.** A scarcity or velocity line — something true about their market
   that they are inside and cannot see from inside it. Ten seconds.
2. **One finding.** Theirs, sourced, on screen. Twenty seconds.
3. **The slider move.** The point of the whole recording: drag the assumption we
   are least sure of and let the figure move on camera. It demonstrates in four
   seconds what a paragraph cannot say at all — that the number is theirs to
   change. Thirty seconds.
4. **The ask.** Correct the slider, or tell us what it really is. Fifteen.

THE GENERATOR NARRATES, IT DOES NOT COMPUTE

Same rule as the analyst, for the same reason: every figure is handed over
already computed by `lib/finmodel.py` through the stored analysis, and the model
writes the sentence around it. A script that invented a number would be a number
said out loud, on a recording, in our own voice.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path
from typing import Any, NamedTuple

import anthropic
from rich.console import Console
from rich.table import Table

from lib import adapters, anchors, dashboard, db, icp, pricing, routing, shortlist, triggers
from lib.canary import read_state
from lib.config import settings

SCRIPT_DIR = Path("reports/scripts")
MODEL = "claude-sonnet-5"
PRICE_PER_MTOK = (2.00, 10.00)
"""Beside the model name, so changing one without the other is a visible edit."""

MAX_TOKENS = 1400
TARGET_SECONDS = 90
WORDS_PER_SECOND = 2.4
"""How fast somebody reads a script aloud without sounding rushed.

Ninety seconds is therefore about 215 words. The cap is enforced rather than
suggested: a script that runs long is a recording the operator has to do twice."""

WORD_CEILING = int(TARGET_SECONDS * WORDS_PER_SECOND)

BEATS: tuple[tuple[str, int, str], ...] = (
    ("HOOK", 10, "something true about their market they cannot see from inside it"),
    ("FINDING", 20, "one thing we read in their own record, on screen"),
    ("SLIDER", 30, "drag the assumption and let the figure move"),
    ("ASK", 15, "correct the slider, or tell us the real number"),
)
"""The four beats and their seconds. The remaining fifteen are pauses."""


class Beats(NamedTuple):
    """Everything the script is built out of, all of it already computed."""

    company: str
    hook: str
    finding: str
    slider: str
    figure: str
    anchor_kind: str
    url: str
    currency: str

    @property
    def usable(self) -> bool:
        """A script needs something on screen to point at.

        Without a slider there is no dashboard, and without a dashboard this is
        a voicemail with extra steps.
        """
        return bool(self.slider)


FINDING_NUMBER = re.compile(r"(?:^|\n)\s*1[.)]\s+(.{60,600}?)(?=\s+2[.)]\s|\Z)", re.S)
CITATION = re.compile(r"\[[a-z0-9_.\[\]]+\]")


def first_finding(body: str) -> str:
    """The analysis's first numbered finding, with our notation stripped out."""
    section = ""
    for chunk in re.split(r"\n##\s+", body or ""):
        if chunk.lower().startswith("findings"):
            section = chunk
            break
    found = FINDING_NUMBER.search(section or body or "")
    text = found.group(1) if found else ""
    return re.sub(r"\s+", " ", CITATION.sub("", text)).strip()[:600]


def beats_for(prospect: dict[str, Any], analysis: dict[str, Any] | None) -> Beats:
    """The four beats, read off the analysis the operator already holds."""
    meta = (analysis or {}).get("gate_map") or {}
    case = meta.get("case") or {}
    approaches = meta.get("approaches") or []
    lead = approaches[0] if approaches else {}
    anchor = case.get("anchor") or {}
    kind = anchor.get("kind") or anchors.NONE
    currency = pricing.currency_for(prospect.get("source_adapter"))

    hook = next(iter((meta.get("scarcity") or []) + (meta.get("velocity") or [])), "")
    if not hook:
        hook = next(iter(case.get("scarcity") or []), "") or next(
            iter(case.get("velocity") or []), "")

    figure = ""
    if kind != anchors.NONE and lead.get("annual_return"):
        low, high = lead["annual_return"][0], lead["annual_return"][1]
        figure = f"{currency}{low:,}-{high:,} a year"

    return Beats(
        company=str(prospect.get("company_name") or ""),
        hook=hook,
        finding=first_finding((analysis or {}).get("body") or ""),
        slider=next(iter(case.get("sensitivities") or []), ""),
        figure=figure,
        anchor_kind=kind,
        url=dashboard.dashboard_url(prospect),
        currency=currency,
    )


SYSTEM = (
    "Write a ninety-second script for a faceless screen-share recording. One "
    "person talks over one browser tab; there is no face, no slide and no "
    "music. A colleague will read this aloud exactly as written.\n\n"
    f"HARD LIMIT {WORD_CEILING} words. Count before you emit. A script that "
    "runs long is a recording somebody has to make twice.\n\n"
    "FOUR BEATS, in this order, each on its own line prefixed with the beat "
    "name and its seconds — the format below, exactly:\n"
    + "\n".join(f"[{name} · {seconds}s] {what}" for name, seconds, what in BEATS)
    + "\n\nRULES.\n"
    "1. YOU MAY NOT COMPUTE. Every figure you are given is already calculated. "
    "Say it as it is written, or round it the way a person speaks — 'about "
    "forty thousand' for $40,412 is right; a different number is not.\n"
    "2. The slider beat has to describe a PHYSICAL ACTION on screen: which "
    "control is dragged, which way, and what moves when it does. That is the "
    "whole recording — it shows in four seconds that the number belongs to "
    "them.\n"
    "3. Second person throughout. You are talking to the person who runs the "
    "place, not describing them to somebody else.\n"
    "4. No jargon, no internal vocabulary, no claim ids, no tier language.\n"
    "5. The ask is a correction, not a meeting. We want the real number more "
    "than we want the call, and asking for the smaller thing is what gets it.\n"
    "6. Plain text. No markdown, no headings beyond the four beat lines, no "
    "stage directions in brackets other than the beat prefixes."
)


def build_prompt(beats: Beats) -> str:
    """Everything the writer may use, and nothing it may add to."""
    lines = [
        f"COMPANY: {beats.company}",
        f"THEIR DASHBOARD, which is what is on screen: {beats.url}",
        "",
        f"HOOK MATERIAL (use it or drop the beat, do not invent one): "
        f"{beats.hook or '(nothing scarce or fast is recorded for their market)'}",
        f"THE FINDING, from their own record: {beats.finding or '(none recorded)'}",
        f"THE SLIDER, and what it would have to be: {beats.slider}",
    ]
    if beats.figure:
        lines.append(
            f"THE FIGURE ON SCREEN, already computed: {beats.figure}. Say this "
            f"one and no other.")
    else:
        lines.append(
            "THERE IS NO FIGURE. Nothing they have published sizes this work, so "
            "the recording is about the missing number: name it, say what it "
            "would let us work out, and ask for it. Do NOT say a dollar amount "
            "of any kind.")
    return "\n".join(lines)


def too_long(text: str) -> list[str]:
    words = len(re.sub(r"\[[A-Z]+ · \d+s\]", " ", text or "").split())
    if words <= WORD_CEILING:
        return []
    return [f"the script runs {words} words; ninety seconds holds {WORD_CEILING}"]


def missing_beats(text: str) -> list[str]:
    absent = [name for name, _s, _w in BEATS if f"[{name}" not in (text or "")]
    return [f"missing beat(s): {', '.join(absent)}"] if absent else []


def stray_money(text: str, beats: Beats) -> list[str]:
    """A recording may not say a figure the company has nothing to anchor it to."""
    if beats.figure:
        return []
    found = re.findall(r"[$£€]\s?\d|\b\d[\d,]*\s*(?:dollars|thousand|k)\b", text or "",
                       re.IGNORECASE)
    if not found:
        return []
    return [f"this company has no anchor, so the script may quote no figure; "
            f"it quotes {len(found)}"]


class Spend:
    """Token usage for one run, priced for MODEL."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0

    def add(self, usage: Any) -> None:
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0

    @property
    def dollars(self) -> float:
        return (self.input_tokens * PRICE_PER_MTOK[0]
                + self.output_tokens * PRICE_PER_MTOK[1]) / 1_000_000

    def line(self) -> str:
        return (f"{self.input_tokens:,} in / {self.output_tokens:,} out "
                f"= ${self.dollars:.2f}")


def script_path(prospect: dict[str, Any], directory: Path | None = None) -> Path:
    return (directory or SCRIPT_DIR) / f"{dashboard.token_for(prospect)}.txt"


def header(beats: Beats) -> str:
    """What the operator needs in front of them before they press record."""
    return (
        f"{beats.company} — 90-second screen share\n"
        f"Open this tab before recording: {beats.url}\n"
        f"Anchor: {beats.anchor_kind}"
        + (f" · on-screen figure {beats.figure}" if beats.figure else
           " · no figure — this recording asks for the missing number")
        + "\n" + "-" * 64 + "\n\n"
    )


async def write_script(
    prospect: dict[str, Any], beats: Beats, client: Any, spend: Spend,
    feedback: list[str] | None = None,
) -> tuple[str, list[str]]:
    """One script, and whatever is wrong with it."""
    prompt = build_prompt(beats)
    if feedback:
        prompt += "\n\nTHE LAST ATTEMPT WAS REFUSED:\n" + "\n".join(
            f"  - {f}" for f in feedback)
    response = await client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM,
        messages=[{"role": "user", "content": prompt}])
    spend.add(getattr(response, "usage", None))
    text = "\n".join(
        block.text for block in response.content
        if getattr(block, "type", "") == "text").strip()
    return text, too_long(text) + missing_beats(text) + stray_money(text, beats)


def candidates(limit: int, adapter: str | None) -> list[dict[str, Any]]:
    """The top of the list, by the pipeline's own ranking, per source.

    Ranked by `lib/shortlist.py` rather than by trigger freshness: a recording
    is made once and watched later, so what matters is which companies are worth
    the operator's time, not whose week it is.
    """
    verdicts = read_state().allowed_verdicts()
    rows = [p for p in db.list_prospects_full(adapter)
            if icp.outreach_eligible(p) and routing.may_write_claims(p, verdicts)]
    return shortlist.ranked(rows)[:limit]


def newest_analysis() -> dict[str, dict[str, Any]]:
    newest: dict[str, dict[str, Any]] = {}
    for artifact in db.all_artifacts():
        if (artifact.get("kind") != "analysis"
                or artifact.get("status") not in ("sendable", "held")):
            continue
        current = newest.get(artifact["prospect_id"])
        if current is None or artifact["created_at"] > current["created_at"]:
            newest[artifact["prospect_id"]] = artifact
    return newest


async def _run(limit: int, dry_run: bool, console: Console,
               adapter: str | None, directory: Path | None = None) -> int:
    rows = candidates(limit, adapter)
    analyses = newest_analysis()
    plan = [(p, beats_for(p, analyses.get(p["id"]))) for p in rows]

    table = Table(title=f"{len(plan)} script(s)", title_justify="left")
    for column in ("Company", "Trigger", "Anchor", "On screen", "Slider"):
        table.add_column(column)
    for prospect, beats in plan:
        table.add_row(
            beats.company[:30], triggers.trigger_for(prospect).display()[:26],
            beats.anchor_kind, beats.figure or "—",
            "yes" if beats.slider else "[red]none[/red]")
    console.print(table)

    skipped = [b for _p, b in plan if not b.usable]
    if skipped:
        console.print(f"[yellow]{len(skipped)} company(ies) have no sensitivity "
                      f"recorded, so there is no slider to move and no recording "
                      f"to make. Regenerate their analysis first.[/yellow]")
    if dry_run:
        console.print("\n[dim]--dry-run: nothing generated, nothing written.[/dim]")
        return 0
    if not settings.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY is not set.[/red]")
        return 1

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    spend = Spend()
    written = 0
    for prospect, beats in plan:
        if not beats.usable:
            continue
        console.print(f"\n[cyan]{beats.company}[/cyan]")
        text, failures, feedback = "", ["not attempted"], []
        for attempt in range(1, 3):
            text, failures = await write_script(
                prospect, beats, client, spend, feedback)
            if not failures:
                break
            feedback = failures
            console.print(f"  [yellow]attempt {attempt}:[/yellow] "
                          + "; ".join(f[:80] for f in failures[:2]))
        if failures:
            console.print(f"  [red]not written[/red] — {failures[0][:120]}")
            continue
        path = script_path(prospect, directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header(beats) + text + "\n", encoding="utf-8")
        written += 1
        console.print(f"  [green]{path}[/green] · running spend {spend.line()}")

    console.print(f"\n[bold]{written}[/bold] script(s) written · "
                  f"[dim]API spend: {spend.line()}[/dim]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.loom", description=__doc__)
    parser.add_argument("--limit", type=int, default=10,
                        help="how many companies from the top of the list "
                             "(default 10)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the beats that would be used; generate nothing")
    adapters.add_argument(parser)
    args = parser.parse_args()
    console = Console()
    console.print(f"Scope: [bold]{adapters.words(args.adapter)}[/bold]")
    return asyncio.run(_run(args.limit, args.dry_run, console, args.adapter))


if __name__ == "__main__":
    raise SystemExit(main())
