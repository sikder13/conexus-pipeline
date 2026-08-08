"""Extractor — turns the Conexus recipient listing into prospect rows.

Run from the repo root:

    python -m tools.extractor --dry-run     # parse and report, write nothing
    python -m tools.extractor --limit 25    # write the first 25 records
    python -m tools.extractor               # write them all

Three behaviours are worth knowing before you run it.

It is safely re-runnable. Companies are matched on a normalised name rather
than an exact one, because the normalize_identity node rewrites company_name
after extraction; matching exactly would insert a duplicate of every record a
node had already tidied. Existing companies are updated in place, and only in
the columns this source owns — a re-run never clobbers a website a node
resolved or a stage a human set.

It excludes almost nothing, and it deletes nothing. A company is marked
stage='dead' with the reason in outcome_notes only when the source text itself
says it is closed or enterprise-owned. Everything else stays. We want the audit
trail of what we chose not to pursue more than we want a short list.

It records what the source does NOT say. This listing publishes no grant
amount, no round, no award year and no city. Those columns stay null and the
gap is written into the evidence file as a note, because a null with an
explanation is a research task and a null without one is a mystery.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import Counter, defaultdict
from typing import Any

from rich.console import Console
from rich.table import Table

# Importing the node package populates NODE_REGISTRY, which is what the
# extractor enqueues work items against.
import tools.harvester.nodes  # noqa: E402,F401
from lib import db
from lib.claims import Tier, make_claim
from lib.nodes import NODE_REGISTRY
from lib.runner import deep_merge, merge_notes
from lib.sources.base import RawProspect
from lib.sources.conexus import RECIPIENTS_URL, ConexusAdapter, normalize_name

# Deliberately narrow, subject-anchored patterns. An earlier draft matched
# "Fortune 500" and would have excluded a company whose description says it
# supplies Fortune 500 clients and is "privately owned" — the opposite of the
# intent. Only statements about the company itself count.
ENTERPRISE_PATTERNS = (
    r"\bis a (?:wholly[- ]owned )?(?:division|subsidiary|business unit) of\b",
    r"\bis owned by\b",
)
CLOSURE_PATTERNS = (
    r"\bceased operations\b",
    r"\bno longer in business\b",
    r"\bpermanently closed\b",
    r"\bout of business\b",
)
HEADCOUNT_PATTERN = r"\b(\d{3,5})\s*(?:\+\s*)?(?:employees|team members|associates)\b"
HEADCOUNT_CEILING = 500

SOURCE_ADAPTER_ID = "conexus_iedc"


def _sentence_containing(text: str, match: re.Match) -> str:
    """Return the verbatim sentence a regex matched inside, for the audit trail."""
    start = text.rfind(".", 0, match.start()) + 1
    end = text.find(".", match.end())
    return text[start : end + 1 if end != -1 else len(text)].strip()


def classify_exclusion(record: RawProspect) -> str | None:
    """Return a verbatim-backed reason to exclude this company, or None to keep it."""
    description = record.industry_desc or ""
    for pattern in CLOSURE_PATTERNS:
        found = re.search(pattern, description, re.I)
        if found:
            return f"confirmed closed per source text: {_sentence_containing(description, found)!r}"
    for pattern in ENTERPRISE_PATTERNS:
        found = re.search(pattern, description, re.I)
        if found:
            return (
                "enterprise-owned per source text: "
                f"{_sentence_containing(description, found)!r}"
            )
    found = re.search(HEADCOUNT_PATTERN, description, re.I)
    if found and int(found.group(1)) > HEADCOUNT_CEILING:
        return (
            f"headcount above {HEADCOUNT_CEILING} per source text: "
            f"{_sentence_containing(description, found)!r}"
        )
    return None


Collapses = list[tuple[str, list[str]]]


def deduplicate(records: list[RawProspect]) -> tuple[list[RawProspect], Collapses]:
    """Collapse records sharing a normalised name. Returns the kept list and the collapses.

    The first record wins. Where the source lists a company twice it is because
    the company received more than one grant, and this listing carries nothing
    that distinguishes the awards, so a second row would be a duplicate company
    rather than a second fact.
    """
    grouped: dict[str, list[RawProspect]] = defaultdict(list)
    for record in records:
        grouped[normalize_name(record.company_name)].append(record)
    kept = [group[0] for group in grouped.values()]
    collapsed = [
        (key, [record.company_name for record in group])
        for key, group in grouped.items()
        if len(group) > 1
    ]
    return kept, collapsed


def build_evidence(record: RawProspect) -> dict[str, Any]:
    """Build the source-derived claim block. Everything here is T1 from the listing."""
    claims: dict[str, Any] = {}
    if record.county:
        claims["county"] = make_claim(record.county, Tier.T1, record.source_url)
    if record.industry_desc:
        claims["grant_description"] = make_claim(record.industry_desc, Tier.T1, record.source_url)
    if record.website:
        claims["website"] = make_claim(record.website, Tier.T1, record.source_url)
    if record.case_study_url:
        claims["case_study_url"] = make_claim(record.case_study_url, Tier.T1, record.source_url)
    return {"source": claims}


def absence_notes(record: RawProspect) -> list[str]:
    """Notes naming the fields this source does not publish (DATA-1 rule 9)."""
    absent = [
        field
        for field in ("grant_amount", "grant_round", "grant_year", "city")
        if getattr(record, field) is None
    ]
    if not absent:
        return []
    return [
        f"the Conexus recipient listing publishes no {', '.join(absent)}; "
        f"left null rather than estimated"
    ]


def source_columns(record: RawProspect) -> dict[str, Any]:
    """The prospect columns this source owns and may overwrite on a re-run."""
    return {
        "county": record.county,
        "industry_desc": record.industry_desc,
        "tech_purchased": record.tech_purchased,
        "case_study_url": record.case_study_url,
        "has_case_study": bool(record.case_study_url),
    }


def _write(record: RawProspect, existing: dict | None, exclusion: str | None) -> str:
    """Insert or update one prospect and enqueue its nodes. Returns 'inserted'/'updated'."""
    columns = source_columns(record)
    if existing:
        # Deep-merge so a re-run refreshes this source's claims without
        # discarding evidence or notes that later nodes have since added.
        current = db.get_prospect(existing["id"]) or {}
        evidence = deep_merge(current.get("evidence_file") or {}, build_evidence(record))
        evidence = merge_notes(evidence, "extractor", absence_notes(record))
        db.update_prospect(existing["id"], {**columns, "evidence_file": evidence})
        prospect_id, outcome = existing["id"], "updated"
    else:
        row = {
            "company_name": record.company_name,
            "source_adapter": SOURCE_ADAPTER_ID,
            "stage": "extracted",
            "evidence_file": merge_notes(
                build_evidence(record), "extractor", absence_notes(record)
            ),
            **columns,
        }
        if exclusion:
            row["stage"] = "dead"
            row["outcome_notes"] = exclusion
        prospect_id, outcome = db.insert_prospect(row)["id"], "inserted"

    db.enqueue_work_items(prospect_id, sorted(NODE_REGISTRY))
    return outcome


def _report(
    console: Console,
    parsed: int,
    kept: list[RawProspect],
    collapsed: list[tuple[str, list[str]]],
    excluded: list[tuple[RawProspect, str]],
    inserted: int,
    updated: int,
    dry_run: bool,
) -> None:
    """Print the extraction summary."""
    table = Table(title="Extraction summary", title_justify="left")
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("parsed from source", str(parsed))
    # Counted from the collapse groups, not from parsed-minus-kept: --limit also
    # shrinks the kept list, and charging that to deduplication would misreport
    # a truncated run as a heavily duplicated source.
    table.add_row("duplicates collapsed", str(sum(len(names) - 1 for _, names in collapsed)))
    table.add_row("companies selected", str(len(kept)))
    table.add_row("excluded (marked dead)", str(len(excluded)))
    table.add_row("inserted", "-" if dry_run else str(inserted))
    table.add_row("updated", "-" if dry_run else str(updated))
    console.print(table)

    if collapsed:
        console.print("\n[bold]Duplicates collapsed[/bold]")
        for key, names in collapsed:
            console.print(f"  {key}: {' | '.join(names)}")

    if excluded:
        console.print("\n[bold]Excluded, with reason[/bold]")
        for record, reason in excluded:
            console.print(f"  {record.company_name}: {reason}")

    counties = Counter(record.county or "(unknown)" for record in kept)
    console.print(f"\n[bold]By county[/bold] ({len(counties)} distinct)")
    county_table = Table(show_header=True)
    county_table.add_column("County")
    county_table.add_column("Companies", justify="right")
    for county, count in counties.most_common():
        county_table.add_row(county, str(count))
    console.print(county_table)


async def run(dry_run: bool, limit: int | None, console: Console) -> int:
    """Extract, classify and (unless dry-run) write. Returns a shell exit code."""
    console.print(f"Fetching {RECIPIENTS_URL} …")
    records = await ConexusAdapter().extract()
    parsed = len(records)
    if not records:
        console.print("[red]No records parsed. The page structure has probably changed.[/red]")
        return 1

    kept, collapsed = deduplicate(records)
    if limit is not None:
        kept = kept[:limit]

    classified = [(record, classify_exclusion(record)) for record in kept]
    excluded = [(record, reason) for record, reason in classified if reason]

    inserted = updated = 0
    if not dry_run:
        existing_by_name = {
            normalize_name(row["company_name"]): row
            for row in db.list_prospect_identities(SOURCE_ADAPTER_ID)
        }
        for record, reason in classified:
            match = existing_by_name.get(normalize_name(record.company_name))
            outcome = _write(record, match, reason)
            inserted += outcome == "inserted"
            updated += outcome == "updated"

    _report(console, parsed, kept, collapsed, excluded, inserted, updated, dry_run)
    if dry_run:
        console.print("\n[yellow]Dry run: nothing was written.[/yellow]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.extractor", description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="parse and report without writing")
    parser.add_argument("--limit", type=int, default=None, help="process only the first N records")
    args = parser.parse_args()
    return asyncio.run(run(args.dry_run, args.limit, Console()))


if __name__ == "__main__":
    sys.exit(main())
