"""release — return a file to the queue once the reason it was held is gone.

WHY THIS EXISTS

`tools/floor` holds artifacts and releases them again: "the same sweep that
holds also releases". Nothing did that for a PROSPECT. A record sent to
needs_review stayed there even after the condition that sent it had been
repaired, and scoring — correctly — refuses to touch a file waiting on a human,
so the record could never come back on its own.

That became load-bearing the moment the Cedar-class repair sent 192 Canadian
companies to review for evidence integrity and the re-harvest then gave 13 of
them clean evidence from the correct website. Without a release they would have
sat at priority None forever, holding good evidence nobody was allowed to use.

WHAT IT WILL AND WILL NOT LIFT

Only holds with a MECHANICAL cause, and only by re-testing that cause now:

* **evidence integrity** — released when `evidence_integrity` passes again.
* **website confidence** — released when the site is resolved and trusted.

The summary's coherence verdict is never lifted here. A model may send a file to
a human and may never clear its own alarm; that rule is the whole reason the
verdict is worth anything, and a release sweep that could overrule it would be
the same mistake as a scoring run that overwrites a withdrawn priority. A file
held on coherence is listed, with its reason, for a person to decide.

Anything the classifier does not recognise is left alone. An unfamiliar reason
is a reason to ask somebody, not to assume it has expired.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from typing import Any

from rich.console import Console
from rich.table import Table

from lib import db
from lib.integrity import evidence_integrity

HELD = "needs_review"
RELEASED_TO = "passA_done"
RELEASE_KEY = "review_released"
TRUST_FLOOR = 70

INTEGRITY = "evidence integrity"
COHERENCE = "summary coherence"


def cause_of(reason: str | None) -> str:
    """Which kind of hold this is, from the reason the holder wrote."""
    text = str(reason or "").strip().lower()
    if not text:
        return "none"
    if text.startswith(INTEGRITY):
        return "integrity"
    if text.startswith(COHERENCE):
        return "coherence"
    if "website" in text[:48]:
        return "website"
    return "unrecognised"


def releasable(prospect: dict[str, Any]) -> tuple[bool, str]:
    """Whether this hold may lift now, and the sentence explaining either way."""
    cause = cause_of(prospect.get("needs_review_reason"))
    if cause == "integrity":
        report = evidence_integrity(prospect)
        if report.passing:
            return True, "evidence integrity passes again"
        return False, "; ".join(report.failures)[:90] or "evidence integrity still fails"
    if cause == "website":
        if prospect.get("website") and (prospect.get("website_confidence") or 0) >= TRUST_FLOOR:
            return True, f"website resolved and trusted at {prospect['website_confidence']}"
        return False, "website still unresolved or below the trust floor"
    if cause == "coherence":
        return False, "held on the coherence verdict — only a person lifts this"
    return False, f"{cause} hold: left for a person"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Return files to the queue whose review reason no longer holds.")
    parser.add_argument("--adapter")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    console = Console()

    held = [p for p in db.list_prospects_full(args.adapter) if p.get("stage") == HELD]
    table = Table(title="review holds")
    for column in ("company", "cause", "verdict", "why"):
        table.add_column(column, overflow="fold")
    released = 0
    still: dict[str, int] = {}

    for prospect in held:
        cause = cause_of(prospect.get("needs_review_reason"))
        ok, why = releasable(prospect)
        if not ok:
            still[cause] = still.get(cause, 0) + 1
            continue
        released += 1
        table.add_row(str(prospect.get("company_name"))[:32], cause,
                      "[green]released[/green]", why[:46])
        if args.apply:
            evidence = prospect.get("evidence_file") or {}
            history = list(evidence.get(RELEASE_KEY) or [])
            history.append({
                "from": HELD, "cause": cause, "why": why,
                "was": prospect.get("needs_review_reason"),
                "at": datetime.now(UTC).isoformat(),
            })
            db.update_prospect(prospect["id"], {
                "stage": RELEASED_TO,
                "needs_review_reason": None,
                "evidence_file": {**evidence, RELEASE_KEY: history},
            })

    if released:
        console.print(table)
    console.print(f"{len(held)} held · [green]{released}[/green] released"
                  + ("" if args.apply else " [yellow](dry run)[/yellow]"))
    for cause, count in sorted(still.items(), key=lambda kv: -kv[1]):
        console.print(f"   [dim]{count:4} still held — {cause}[/dim]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
