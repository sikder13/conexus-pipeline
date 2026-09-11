"""withdraw — take back what was written from evidence we no longer stand behind.

WHY THIS EXISTS

Quarantining a claim stops it being READ. It does nothing about what was already
WRITTEN from it. When the Cedar-class repair withdrew a hundred Canadian
domains, the analyses, summaries and outbound drafts built on those pages stayed
exactly where they were, still marked sendable — and the audit found 153 places
where a value the record had withdrawn was still being rendered. Future Fields
Biomanufacturing was still offering https://future.com as its website and still
naming Okta's co-founder as its person.

So a taint sweep has a second half, and this is it.

WHAT IT DOES

Three surfaces, one rule: if it repeats a value the record has withdrawn, it is
withdrawn too.

* **The website column.** Nulled when the identity claim behind it is tainted.
  The URL is not lost — it stays in the resolution record, which is where a
  human goes to see what was rejected.
* **The prose columns.** `machine_summary` and `ai_thesis` are moved into the
  evidence file under `withdrawn/` and the columns cleared. Moved rather than
  deleted: the paragraph is the record of what the machine believed, and a
  reader arguing with this decision needs to see it.
* **Live artifacts.** Held through the same reversible mechanism the evidence
  floor uses, remembering the status each came from. When the evidence is
  repaired the hold comes off and the artifact returns to what it was.

Nothing here decides a claim was wrong. That already happened. This only makes
the rest of the record agree with it.
"""

from __future__ import annotations

import argparse
from typing import Any

from rich.console import Console
from rich.table import Table

from lib import db
from lib.integrity import is_tainted
from tools.audit import quarantined_values
from tools.floor.main import HELD, apply_hold

RULE = "the evidence under it was withdrawn (Cedar-class repair, 2026-09-11)"
WITHDRAWN_KEY = "withdrawn"
LIVE = ("sendable", "blocked", "draft")
PROSE_COLUMNS = ("machine_summary", "ai_thesis")


def repeats_withdrawn(text: str, withdrawn: list[tuple[str, str]]) -> str | None:
    """The first withdrawn value this text repeats, or None."""
    lowered = (text or "").lower()
    for _path, value in withdrawn:
        if value.lower() in lowered:
            return value
    return None


def website_is_withdrawn(prospect: dict[str, Any]) -> bool:
    """True when the claim behind the website column has been quarantined."""
    identity = (prospect.get("evidence_file") or {}).get("identity") or {}
    return is_tainted(identity.get("website"))


def plan_for(
    prospect: dict[str, Any], artifacts: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], str]]]:
    """The column patch and the artifacts to hold, without touching anything."""
    withdrawn = quarantined_values(prospect)
    patch: dict[str, Any] = {}
    evidence = prospect.get("evidence_file") or {}
    kept = dict(evidence.get(WITHDRAWN_KEY) or {})

    if prospect.get("website") and website_is_withdrawn(prospect):
        kept["website"] = prospect["website"]
        patch["website"] = None

    for column in PROSE_COLUMNS:
        text = prospect.get(column)
        if text and repeats_withdrawn(str(text), withdrawn):
            kept[column] = text
            patch[column] = None

    if patch:
        patch["evidence_file"] = {**evidence, WITHDRAWN_KEY: kept}

    hold: list[tuple[dict[str, Any], str]] = []
    for artifact in artifacts:
        if artifact.get("status") not in LIVE:
            continue
        found = repeats_withdrawn(str(artifact.get("body") or ""), withdrawn)
        if found:
            hold.append((artifact, f"repeats the withdrawn value {found[:80]!r}"))
    return patch, hold


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Withdraw what was written from evidence we have taken back.")
    parser.add_argument("--adapter")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    console = Console()

    prospects = db.list_prospects_full(args.adapter)
    wanted = {p["id"] for p in prospects}
    by_prospect: dict[str, list[dict[str, Any]]] = {}
    for artifact in db.all_artifacts():
        if artifact.get("prospect_id") in wanted:
            by_prospect.setdefault(artifact["prospect_id"], []).append(artifact)

    table = Table(title="withdrawn")
    for column in ("company", "what", "detail"):
        table.add_column(column, overflow="fold")
    sites = prose = held = 0

    for prospect in prospects:
        patch, hold = plan_for(prospect, by_prospect.get(prospect["id"], []))
        name = str(prospect.get("company_name") or "")[:34]
        if "website" in patch:
            sites += 1
            table.add_row(name, "website", str(prospect.get("website"))[:44])
        for column in PROSE_COLUMNS:
            if column in patch:
                prose += 1
                table.add_row(name, column, "moved to evidence_file.withdrawn")
        if patch and args.apply:
            db.update_prospect(prospect["id"], patch)
        for artifact, reason in hold:
            held += 1
            table.add_row(name, f"{artifact.get('kind')} ({artifact.get('status')})",
                          reason[:44])
            if args.apply:
                apply_hold(artifact, reason, RULE)

    console.print(table)
    console.print(
        f"{sites} website(s) nulled · {prose} paragraph(s) withdrawn · "
        f"{held} artifact(s) held to {HELD!r}"
        + ("" if args.apply else " [yellow](dry run — nothing written)[/yellow]"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
