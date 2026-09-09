"""Canada loader — the federal grants file in, Ontario and Alberta prospects out.

Run from the repo root:

    python -m tools.canada_gc --dry-run          # filter and report, write nothing
    python -m tools.canada_gc --dry-run --source-file data/raw/canada_gc/grants.csv
    python -m tools.canada_gc --limit 25         # write the first 25 companies

WHAT THE REPORT IS FOR

The filters decide who is contacted, so the run prints the funnel rather than a
total: how many agreements each stage removed, the commonest reason inside each
stage, and the thirty programmes reaching the most businesses in the two
provinces whether or not they are whitelisted. That last table is the one the
whitelist is extended from — it is the answer to "what are we not looking at".

WHAT IT WRITES, WHEN IT IS NOT A DRY RUN

One prospect per company, `source_adapter='canada_gc'`, with every award kept as
evidence in block 2 and the largest award filling the single-value grant
columns — the same shape the Indiana round loader settled on for companies that
won more than once.

DEDUPLICATION IS NOT MERGING

A Canadian company matching an existing `canada_gc` record is the same company
and is updated in place. A Canadian company matching a record from a different
adapter is almost certainly a different company that happens to share a name, so
it is neither merged nor inserted: it is reported for a human, because merging
two companies is not something a name match should be allowed to do.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

# Importing the node package populates NODE_REGISTRY, which is what the loader
# enqueues work items against.
import tools.harvester.nodes  # noqa: E402,F401
from lib import db
from lib.claims import Tier, make_claim
from lib.evidence import BLOCK2_GRANT_FUNDED, block_patch, flag_patch, merge_patches
from lib.nodes import NODE_REGISTRY
from lib.runner import deep_merge, merge_notes
from lib.scoring import DATA_GENERATING_TECH_TERMS, PROGRAM_RECENCY_YEAR
from lib.sources.canada_gc.adapter import CanadaGCAdapter, Extraction
from lib.sources.canada_gc.dataset import DATASET_URL, SNAPSHOT_DIR
from lib.sources.canada_gc.filters import (
    STAGES,
    CanadaRecipient,
    ambiguous_cities,
    city_review_reason,
    entry_tally,
    family_counts,
    province_counts,
    wave,
)
from lib.sources.canada_gc.industries import FAMILY_WORDS, classify_industry
from lib.sources.canada_gc.recipients import company_key

SOURCE_ADAPTER_ID = "canada_gc"

EXTERNAL_TECH_PHRASES: tuple[str, ...] = (
    "in partnership with", "in collaboration with", "partnering with",
    "collaborate with", "collaboration between", "with the support of",
    "systems integrator", "technology partner", "external consultant",
    "consulting firm", "third party provider", "third-party provider",
    "subcontract", "under contract with", "industrial technology advisor",
    "research institute", "university of", "college of", "polytechnic",
)
"""Phrases in the government record that say outside technical capability was
brought in. Matching one is our reading of their words, so the flag it sets is
T4 and carries the phrase that fired — a company that has already paid an
outsider to solve a technical problem has answered the hardest question in the
first call, and that is why the component exists."""

REASON_WORDS: dict[str, str] = {
    "nonprofit": "not-for-profit, charity or association",
    "no_corporate_form": "no legal form of incorporation in the name",
    "individual": "individual or sole proprietorship",
    "government": "government body",
    "academia": "university, college or school authority",
    "municipality": "municipality",
    "hospital": "hospital or health authority",
    "international": "international non-government body",
    "unknown_type": "a recipient type the dictionary does not define",
}
"""Plain words for the exclusion reason keys, for the report only."""


def _money(value: float | None) -> str:
    return f"${value:,.0f}" if value is not None else "—"


# ------------------------------------------------------------------- evidence

def data_generating_terms(text: str) -> list[str]:
    """Which data-generating technologies the government record names, if any."""
    lowered = (text or "").lower()
    return [term for term in DATA_GENERATING_TECH_TERMS if term.lower() in lowered]


def external_tech_phrases(text: str) -> list[str]:
    """Which phrases naming an outside technical partner the record uses."""
    lowered = (text or "").lower()
    return [phrase for phrase in EXTERNAL_TECH_PHRASES if phrase in lowered]


def _claim(value: Any, tier: Tier, **extra: Any) -> dict[str, Any]:
    """A dataset claim with its reference number attached.

    Every claim from this source cites the dataset page, because that is the
    page a human can open — the search interface's per-award URLs render
    client-side and return an empty shell to a fetch. The reference number
    travels as an extra field instead, which is what the search form takes, so
    a reader can still reach the individual award.
    """
    claim = make_claim(value, tier, DATASET_URL)
    claim.update({key: value for key, value in extra.items() if value is not None})
    return claim


def award_line(award: Any) -> str:
    """One award as a sentence: the money, the programme, the department, the year."""
    department = (award.department or "").split("|")[0].strip()
    parts = [f"{_money(award.amount)} — {award.program}"]
    if department:
        parts.append(f" ({department})")
    parts.append(f", {award.year if award.year else 'no start date published'}")
    return "".join(parts)


def build_evidence(recipient: CanadaRecipient) -> dict[str, Any]:
    """Block 2 for one company: every award, the purpose, and the two flags.

    Everything read straight from the dataset is T1 — it is a government record
    of the award, which is the definition CLAUDE.md gives for the tier. The two
    things that are not straight readings are marked as what they are: our sum
    across awards is T4 and says so in its own value, and the data-generating
    technology flag is our reading of their words, so it is T4 with the terms
    that fired attached.
    """
    largest = recipient.largest
    placement = classify_industry(largest.purpose_text, recipient.company_name)
    claims: dict[str, Any] = {
        "grant_awards": [
            _claim(award_line(award), Tier.T1,
                   ref_number=award.ref_number,
                   agreement_number=award.agreement_number,
                   amendment_number=award.amendment_number)
            for award in recipient.awards
        ],
        "grant_award_count": _claim(len(recipient.awards), Tier.T1),
        "grant_amount": _claim(_money(largest.amount), Tier.T1,
                               ref_number=largest.ref_number),
        "grant_program": _claim(largest.program, Tier.T1,
                                ref_number=largest.ref_number),
    }
    if largest.year is not None:
        claims["grant_year"] = _claim(largest.year, Tier.T1,
                                      ref_number=largest.ref_number)
    if largest.program_purpose:
        claims["program_purpose"] = _claim(
            largest.program_purpose, Tier.T1,
            ref_number=largest.ref_number, program=largest.program,
        )
    if largest.description:
        claims["agreement_description"] = _claim(
            largest.description, Tier.T1, ref_number=largest.ref_number)
    if largest.agreement_title:
        claims["agreement_title"] = _claim(
            largest.agreement_title, Tier.T1, ref_number=largest.ref_number)
    # Our placement of their business, so T4 and labelled with the words that
    # made it — a reader who disagrees can see exactly what to disagree with.
    claims["industry_family"] = _claim(
        placement.words, Tier.T4, basis=placement.basis,
        matched_terms=list(placement.matched) or None,
    )
    if len(recipient.awards) > 1:
        claims["grant_awards_total"] = _claim(
            f"{_money(recipient.total_awarded)} across {len(recipient.awards)} awards "
            f"— our sum, not a single award",
            Tier.T4,
        )

    patches = [block_patch(BLOCK2_GRANT_FUNDED, claims)]

    latest = recipient.latest_year
    if latest is not None:
        patches.append(flag_patch(
            "program_recency", latest >= PROGRAM_RECENCY_YEAR, Tier.T1, DATASET_URL,
            detail=f"most recent award starts {latest}; the cutoff is "
                   f"{PROGRAM_RECENCY_YEAR}",
        ))

    all_text = " ".join(award.purpose_text for award in recipient.awards)
    terms = data_generating_terms(all_text)
    if terms:
        patches.append(flag_patch(
            "purpose_names_data_generating_tech", True, Tier.T4, DATASET_URL,
            matched_terms=terms,
        ))
    partners = external_tech_phrases(all_text)
    if partners:
        patches.append(flag_patch(
            "external_tech_engagement", True, Tier.T4, DATASET_URL,
            matched_terms=partners,
        ))
    return merge_patches(*patches)


def absence_notes(recipient: CanadaRecipient) -> list[str]:
    """What this source does not publish, recorded rather than filled in."""
    notes = [
        "the Government of Canada grants file publishes no website, no contact "
        "and no headcount; those stay null until a node finds them",
    ]
    if not recipient.city:
        notes.append("no recipient city published for this company; left null")
    if len(recipient.provinces) > 1:
        notes.append(
            f"awards recorded in more than one province ({', '.join(recipient.provinces)}); "
            f"the largest award's province is used and both are kept in the awards"
        )
    return notes


def source_columns(recipient: CanadaRecipient) -> dict[str, Any]:
    """The prospect columns this source owns and may overwrite on a re-run."""
    largest = recipient.largest
    return {
        "city": recipient.city,
        "region": recipient.province,
        "industry_desc": largest.description or largest.agreement_title,
        "naics_guess": recipient.naics,
        "grant_amount": largest.amount,
        "grant_round": largest.program,
        "grant_year": largest.year,
    }


# --------------------------------------------------------------------- writing

def write_recipient(
    recipient: CanadaRecipient, existing: dict[str, Any] | None,
    review_reason: str | None = None,
) -> str:
    """Insert or update one company and queue its nodes. Returns the outcome.

    A company whose city cannot be placed from the record is inserted at
    `needs_review` rather than dropped. It is a real company with a real award;
    what we cannot do is tell a later node which town to look in, and a node
    that guesses will resolve somebody else's website with total confidence.
    """
    columns = source_columns(recipient)
    notes = absence_notes(recipient)
    if review_reason:
        notes = [*notes, f"needs review before research: {review_reason}"]

    if existing:
        current = db.get_prospect(existing["id"]) or {}
        evidence = deep_merge(current.get("evidence_file") or {}, build_evidence(recipient))
        evidence = merge_notes(evidence, "canada_gc", notes)
        patch: dict[str, Any] = {**columns, "evidence_file": evidence}
        # A stage a human moved is never walked back by a re-run; the review
        # flag is only set on a record still sitting where the loader left it.
        if review_reason and current.get("stage") == "extracted":
            patch["stage"] = "needs_review"
            patch["needs_review_reason"] = review_reason[:600]
        db.update_prospect(existing["id"], patch)
        prospect_id, outcome = existing["id"], "updated"
    else:
        row = {
            "company_name": recipient.company_name,
            "source_adapter": SOURCE_ADAPTER_ID,
            "stage": "needs_review" if review_reason else "extracted",
            "evidence_file": merge_notes(
                build_evidence(recipient), "canada_gc", notes
            ),
            **columns,
        }
        if review_reason:
            row["needs_review_reason"] = review_reason[:600]
        prospect_id, outcome = db.insert_prospect(row)["id"], "inserted"

    db.enqueue_work_items(prospect_id, sorted(NODE_REGISTRY))
    return outcome


def partition(
    recipients: list[CanadaRecipient], existing: list[dict[str, Any]]
) -> tuple[list[tuple[CanadaRecipient, dict | None]], list[tuple[CanadaRecipient, dict]]]:
    """Split companies into writable ones and cross-adapter name collisions."""
    index: dict[str, dict[str, Any]] = {}
    for row in existing:
        index.setdefault(company_key(row.get("company_name")), row)

    writable: list[tuple[CanadaRecipient, dict | None]] = []
    collisions: list[tuple[CanadaRecipient, dict]] = []
    for recipient in recipients:
        match = index.get(recipient.key)
        if match is None:
            writable.append((recipient, None))
        elif match.get("source_adapter") == SOURCE_ADAPTER_ID:
            writable.append((recipient, match))
        else:
            collisions.append((recipient, match))
    return writable, collisions


# ---------------------------------------------------------------------- report

def report(
    console: Console,
    extraction: Extraction,
    top_programs: int,
    inserted: int,
    updated: int,
    collisions: list[tuple[CanadaRecipient, dict]],
    dry_run: bool,
    limit: int | None = None,
    selected: list[CanadaRecipient] | None = None,
    review: dict[str, str] | None = None,
) -> None:
    """Print the whole account of the run: the funnel, the programmes, the result."""
    run, download = extraction.run, extraction.download

    summary = Table(title="Canada — extraction summary", title_justify="left")
    summary.add_column("Metric", style="bold")
    summary.add_column("Value", justify="right")
    summary.add_row("source", download.source)
    summary.add_row("retrieved", download.retrieved_at)
    summary.add_row("bytes read", f"{download.bytes_read:,}")
    summary.add_row("sha256", download.digest[:16] + "…")
    summary.add_row("rows read", f"{download.rows_read:,}")
    summary.add_row("agreements after amendments", f"{run.agreements:,}")
    summary.add_row("superseded rows collapsed", f"{run.rows_superseded:,}")
    summary.add_row("awards kept", f"{len(run.kept()):,}")
    summary.add_row("companies", f"{len(extraction.recipients):,}")
    if limit is not None:
        summary.add_row("wave one", f"{len(selected or []):,} in wave order")
    summary.add_row("inserted", "—" if dry_run else str(inserted))
    summary.add_row("updated (deduped)", "—" if dry_run else str(updated))
    summary.add_row("held for review", f"{len(review or {}):,}")
    console.print(summary)

    funnel = Table(title="\nFilter funnel — every exclusion charged to one stage",
                   title_justify="left")
    funnel.add_column("Stage", style="bold")
    funnel.add_column("Excluded", justify="right")
    funnel.add_column("Why")
    counts = run.stage_counts()
    for stage, words in STAGES:
        funnel.add_row(stage, f"{counts[stage]:,}", words)
    console.print(funnel)

    for stage in ("business_recipient", "amount", "award_year", "industry"):
        reasons = run.reason_counts(stage)
        if not reasons:
            continue
        table = Table(title=f"\n{stage} — by reason", title_justify="left")
        table.add_column("Reason")
        table.add_column("Agreements", justify="right")
        for reason, count in reasons[:12]:
            table.add_row(REASON_WORDS.get(reason, reason), f"{count:,}")
        console.print(table)

    whitelist = Table(title="\nWhitelist — what each entry caught", title_justify="left")
    whitelist.add_column("Entry")
    whitelist.add_column("On", justify="center")
    whitelist.add_column("Business agreements", justify="right")
    whitelist.add_column("Recipients", justify="right")
    whitelist.add_column("Survived every filter", justify="right")
    for row in entry_tally(run):
        whitelist.add_row(row.display, "yes" if row.enabled else "no",
                          f"{row.agreements:,}", f"{row.recipients:,}", f"{row.kept:,}")
    console.print(whitelist)

    programs = Table(
        title=f"\nTop {top_programs} programmes by business recipients in ON/AB",
        title_justify="left",
    )
    programs.add_column("#", justify="right")
    programs.add_column("Programme")
    programs.add_column("Department")
    programs.add_column("Recipients", justify="right")
    programs.add_column("Agreements", justify="right")
    programs.add_column("In band", justify="right")
    programs.add_column("Listed", justify="center")
    for rank, row in enumerate(run.program_tally(top_programs), 1):
        programs.add_row(str(rank), row.program[:58], row.department[:34],
                         f"{row.recipients:,}", f"{row.agreements:,}",
                         f"{row.in_band:,}", row.whitelist_words)
    console.print(programs)

    province = Table(title="\nCompanies by province", title_justify="left")
    province.add_column("Province")
    province.add_column("Companies", justify="right")
    for code, count in province_counts(extraction.recipients):
        province.add_row(code, f"{count:,}")
    console.print(province)

    family = Table(title="\nCompanies by industry family", title_justify="left")
    family.add_column("Family")
    family.add_column("Companies", justify="right")
    for key, count in family_counts(extraction.recipients):
        family.add_row(FAMILY_WORDS.get(key, key), f"{count:,}")
    console.print(family)

    if selected is not None:
        chosen = Table(title="\nWave one — by province and industry family",
                       title_justify="left")
        chosen.add_column("Family")
        for code, _count in province_counts(selected):
            chosen.add_column(code, justify="right")
        chosen.add_column("Total", justify="right")
        codes = [code for code, _c in province_counts(selected)]
        for key, _count in family_counts(selected):
            row = [FAMILY_WORDS.get(key, key)]
            for code in codes:
                row.append(str(sum(1 for r in selected
                                   if r.family == key and r.province == code)))
            row.append(str(sum(1 for r in selected if r.family == key)))
            chosen.add_row(*row)
        totals = ["all families"]
        for code in codes:
            totals.append(str(sum(1 for r in selected if r.province == code)))
        totals.append(str(len(selected)))
        chosen.add_row(*totals, style="bold")
        console.print(chosen)

    if review:
        reasons = Counter(
            "no city published" if "no recipient city" in words else "ambiguous city"
            for words in review.values()
        )
        table = Table(title="\nHeld at needs_review before research",
                      title_justify="left")
        table.add_column("Reason")
        table.add_column("Companies", justify="right")
        for reason, count in reasons.most_common():
            table.add_row(reason, str(count))
        console.print(table)
        console.print(
            "  Province is never inferred by this adapter: the dataset publishes "
            "it and a record without one is dropped by the province filter, so no "
            "company here carries an inferred province."
        )

    if collisions:
        console.print(
            "\n[bold]Name collisions with prospects from another adapter[/bold] "
            "— neither merged nor inserted:"
        )
        for recipient, match in collisions[:20]:
            console.print(
                f"  {recipient.company_name} ({recipient.province}) shares a "
                f"normalised name with {match.get('company_name')!r} "
                f"[{match.get('source_adapter')}]"
            )

    console.print(f"\nSnapshot written to {SNAPSHOT_DIR}/")


# ------------------------------------------------------------------------- CLI

async def run(
    dry_run: bool, limit: int | None, source_file: Path | None,
    top_programs: int, console: Console,
) -> int:
    """Extract, filter and (unless dry-run) write. Returns a shell exit code."""
    console.print("Reading the Government of Canada grants file …")
    adapter = CanadaGCAdapter(source_file=source_file, top_programs=top_programs)
    extraction = await adapter.extract_recipients()

    if not extraction.recipients:
        console.print("[red]No company survived the filters. Check the funnel below.[/red]")

    # --limit bounds what is WRITTEN, never what is reported. A funnel that
    # narrowed because the operator asked for twenty-five companies would say
    # something false about the dataset.
    to_write = wave(extraction.recipients, limit)

    ambiguous = ambiguous_cities(extraction.run.kept())
    review = {}
    for recipient in to_write:
        reason = city_review_reason(recipient, ambiguous)
        if reason:
            review[recipient.key] = reason

    inserted = updated = 0
    collisions: list[tuple[CanadaRecipient, dict]] = []
    if not dry_run:
        writable, collisions = partition(to_write, db.list_prospect_identities())
        for recipient, existing in writable:
            outcome = write_recipient(recipient, existing, review.get(recipient.key))
            inserted += outcome == "inserted"
            updated += outcome == "updated"

    report(console, extraction, top_programs, inserted, updated, collisions,
           dry_run, limit, to_write, review)
    if dry_run:
        console.print("\n[yellow]Dry run: nothing was written to the database.[/yellow]")
    return 0 if extraction.recipients else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.canada_gc", description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="filter and report without writing to the database")
    parser.add_argument("--limit", type=int, default=None,
                        help="write only the first N companies, in wave order: "
                             "which programme, then award recency, then amount")
    parser.add_argument("--source-file", type=Path, default=None,
                        help="read a local copy of the CSV instead of fetching it")
    parser.add_argument("--top-programs", type=int, default=30,
                        help="how many programmes to list in the report (default 30)")
    args = parser.parse_args()
    return asyncio.run(run(args.dry_run, args.limit, args.source_file,
                           args.top_programs, Console()))


if __name__ == "__main__":
    sys.exit(main())
