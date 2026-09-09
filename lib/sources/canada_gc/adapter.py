"""The canada_gc adapter: read the federal grants file, keep what we can sell to.

HOW A RUN GOES

1. Fetch the published data dictionary and check the CSV header against it. A
   column that has been renamed stops the run here, before a single value is
   read, because a shifted layout parses cleanly and means something else.
2. Stream the CSV. Rows go through the filter funnel one at a time and only a
   small verdict per agreement is kept; the file is never held.
3. Resolve amendments, regroup the surviving awards into companies, and write
   the snapshot: what was downloaded, and the rows that survived.

WHAT LANDS IN data/raw/canada_gc/

`download.json` — the URL, the byte count, the digest of what we actually read,
the header as published, and the counts. It is the answer to "was this the same
file as last week", which is a question about a dataset that is republished
quarterly and amended continuously.

`filtered_awards.csv` — one row per surviving award, with the columns we read.
Small enough to open in a spreadsheet, which is the point: an operator
disagreeing with the filters needs the rows, not the arguments.

`top_programs.csv` — the programme tally the whitelist is extended from.

The source file itself is never written. It is 2.3 GB, it is a government
publication that can be re-fetched at any time, and keeping a copy would make
the repository's data directory the second-largest thing on the machine.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, NamedTuple

import httpx

from lib.config import settings
from lib.nodes import RunContext
from lib.sources.base import RawProspect, SourceAdapter
from lib.sources.canada_gc.dataset import (
    CSV_URL,
    DATASET_URL,
    DICTIONARY_URL,
    SNAPSHOT_DIR,
    CanadaAward,
    DataDictionary,
    DictionaryMismatch,
    RecordAssembler,
    award_from_row,
    iter_rows,
    lines_from_stream,
    parse_dictionary,
    parse_records,
    read_header,
)
from lib.sources.canada_gc.filters import (
    DEFAULTS,
    CanadaRecipient,
    FilterRun,
    FilterSettings,
    group_recipients,
)

SNAPSHOT_ROWS = "filtered_awards.csv"
SNAPSHOT_PROGRAMS = "top_programs.csv"
SNAPSHOT_DOWNLOAD = "download.json"

BATCH_RECORDS = 2_000
"""How many assembled records are parsed at once. Batching amortises the csv
reader's setup without holding a meaningful amount of the file."""


class Download(NamedTuple):
    """What was actually read, so a later run can tell whether it changed."""

    source: str
    retrieved_at: str
    bytes_read: int
    digest: str
    rows_read: int
    agreements: int
    header: tuple[str, ...]
    last_modified: str | None = None
    content_length: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_url": DATASET_URL,
            "dictionary_url": DICTIONARY_URL,
            **self._asdict(),
            "header": list(self.header),
        }


class Extraction(NamedTuple):
    """Everything one run produced: the companies, the funnel, the download."""

    recipients: list[CanadaRecipient]
    run: FilterRun
    download: Download
    dictionary: DataDictionary


class CanadaGCAdapter(SourceAdapter):
    """Government of Canada Grants & Contributions, filtered to Ontario and Alberta.

    ``extract`` satisfies the SourceAdapter contract and returns one RawProspect
    per company. ``extract_recipients`` is what the tool actually calls, because
    a company here holds several awards and RawProspect carries one — the same
    shape the Indiana grant-round loader settled on, where the largest award
    fills the single-value columns and every award is kept as evidence.
    """

    adapter_id: ClassVar[str] = "canada_gc"

    def __init__(
        self,
        ctx: RunContext | None = None,
        filters: FilterSettings = DEFAULTS,
        source_file: Path | str | None = None,
        save_snapshot: bool = True,
        top_programs: int = 30,
    ) -> None:
        self.ctx = ctx
        self.filters = filters
        self.source_file = Path(source_file) if source_file else None
        self.save_snapshot = save_snapshot
        self.top_programs = top_programs

    # ------------------------------------------------------------------ entry

    async def extract(self) -> list[RawProspect]:
        """Every surviving company, in the base adapter's own record shape."""
        extraction = await self.extract_recipients()
        return [as_raw_prospect(recipient) for recipient in extraction.recipients]

    async def extract_recipients(self) -> Extraction:
        """Run the whole funnel and return the companies with all their awards."""
        dictionary = await self._dictionary()
        run = FilterRun(self.filters)
        if self.source_file is not None:
            download = self._read_file(dictionary, run)
        else:
            download = await self._read_network(dictionary, run)

        recipients = group_recipients(run.kept())
        extraction = Extraction(recipients, run, download, dictionary)
        if self.save_snapshot:
            write_snapshot(extraction, self.top_programs)
        return extraction

    # ------------------------------------------------------------- the fetch

    async def _dictionary(self) -> DataDictionary:
        """The published data dictionary, through the politeness gate."""
        if self.ctx is not None:
            response = await self.ctx.fetch(DICTIONARY_URL)
            return parse_dictionary(response.json())
        async with httpx.AsyncClient() as client:
            ctx = RunContext(client, settings)
            response = await ctx.fetch(DICTIONARY_URL)
            return parse_dictionary(response.json())

    def _read_file(self, dictionary: DataDictionary, run: FilterRun) -> Download:
        """Parse a local copy of the CSV. Used for re-runs and for tests.

        The network path is the real one; this exists so that a filter change
        can be re-measured against the same bytes without asking the Government
        of Canada for two more gigabytes.
        """
        digest = hashlib.sha256()
        rows = 0
        header: list[str] = []
        path = self.source_file
        assert path is not None
        with path.open("rb") as raw:
            for chunk in iter(lambda: raw.read(1 << 20), b""):
                digest.update(chunk)
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
            for row in iter_rows(handle):
                rows += 1
                run.consider(award_from_row(row, dictionary))
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
            header = read_header([next(iter(handle))])
        return Download(
            source=str(path),
            retrieved_at=datetime.now(UTC).isoformat(),
            bytes_read=path.stat().st_size,
            digest=digest.hexdigest(),
            rows_read=rows,
            agreements=run.agreements,
            header=tuple(header),
        )

    async def _read_network(self, dictionary: DataDictionary, run: FilterRun) -> Download:
        """Stream the CSV off the socket, filtering as it arrives."""
        if self.ctx is not None:
            return await self._stream_with(self.ctx, dictionary, run)
        async with httpx.AsyncClient() as client:
            return await self._stream_with(RunContext(client, settings), dictionary, run)

    async def _stream_with(
        self, ctx: RunContext, dictionary: DataDictionary, run: FilterRun
    ) -> Download:
        digest = hashlib.sha256()
        assembler = RecordAssembler()
        header: list[str] | None = None
        batch: list[str] = []
        rows = 0
        seen_bytes = 0
        last_modified: str | None = None
        content_length: int | None = None

        async with ctx.stream(CSV_URL) as response:
            last_modified = response.headers.get("last-modified")
            declared = response.headers.get("content-length")
            content_length = int(declared) if declared and declared.isdigit() else None

            async def chunks():
                nonlocal seen_bytes
                async for chunk in response.aiter_bytes():
                    seen_bytes += len(chunk)
                    digest.update(chunk)
                    yield chunk

            async for line in lines_from_stream(chunks()):
                for record in assembler.feed(line):
                    if header is None:
                        header = read_header([record])
                        continue
                    batch.append(record)
                if len(batch) >= BATCH_RECORDS and header is not None:
                    rows += self._drain(batch, header, dictionary, run)
                    batch = []

        for record in assembler.flush():
            if header is None:
                header = read_header([record])
            else:
                batch.append(record)
        if batch and header is not None:
            rows += self._drain(batch, header, dictionary, run)

        if header is None:
            raise DictionaryMismatch(f"{CSV_URL} produced no CSV header at all")

        return Download(
            source=CSV_URL,
            retrieved_at=datetime.now(UTC).isoformat(),
            bytes_read=seen_bytes,
            digest=digest.hexdigest(),
            rows_read=rows,
            agreements=run.agreements,
            header=tuple(header),
            last_modified=last_modified,
            content_length=content_length,
        )

    @staticmethod
    def _drain(
        batch: list[str], header: list[str], dictionary: DataDictionary, run: FilterRun
    ) -> int:
        rows = parse_records(batch, header)
        for row in rows:
            run.consider(award_from_row(row, dictionary))
        return len(rows)


# ------------------------------------------------------------ the record shape

def as_raw_prospect(recipient: CanadaRecipient) -> RawProspect:
    """One company in the base adapter's record shape.

    `county` stays null because Canada publishes none, and `case_study_url`
    because this source has no case studies — both are Indiana columns, and a
    null with a reason beats a value with none. The single-value grant columns
    take the largest award, exactly as the Indiana round loader does; every
    other award reaches the database as evidence rather than as a column.
    """
    largest = recipient.largest
    return RawProspect(
        company_name=recipient.company_name,
        source_url=DATASET_URL,
        county=None,
        city=recipient.city,
        industry_desc=largest.description or largest.agreement_title,
        website=None,
        grant_amount=largest.amount,
        grant_round=largest.program,
        grant_year=largest.year,
        tech_purchased=None,
        case_study_url=None,
    )


# ----------------------------------------------------------------- the snapshot

SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "ref_number", "amendment_number", "recipient_legal_name",
    "recipient_operating_name", "recipient_type_code", "recipient_type_words",
    "province_code", "city", "postal_code", "program", "department",
    "amount", "start_date", "naics", "agreement_title", "description",
)


def _award_row(award: CanadaAward) -> list[str]:
    """One award as snapshot columns. Only a missing value renders as empty.

    `or ""` would have written amendment number 0 and an agreement value of
    zero as blanks — the two figures in this dataset where zero is a real,
    published answer and blank means the column was never filled in.
    """
    values = {
        **award.model_dump(),
        "start_date": award.start_date.isoformat() if award.start_date else None,
    }
    return [
        "" if values.get(column) is None else str(values[column])
        for column in SNAPSHOT_COLUMNS
    ]


def write_snapshot(extraction: Extraction, top_programs: int = 30) -> Path:
    """Write the download record, the surviving rows and the programme tally."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    (SNAPSHOT_DIR / SNAPSHOT_DOWNLOAD).write_text(
        json.dumps(extraction.download.as_dict(), indent=2) + "\n", encoding="utf-8"
    )

    with (SNAPSHOT_DIR / SNAPSHOT_ROWS).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([*SNAPSHOT_COLUMNS, "company_key", "industry_family"])
        for recipient in extraction.recipients:
            for award in recipient.awards:
                writer.writerow([*_award_row(award), recipient.key, recipient.family])

    with (SNAPSHOT_DIR / SNAPSHOT_PROGRAMS).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["rank", "program", "department", "business_recipients",
             "agreements", "in_band_agreements", "whitelisted"]
        )
        for rank, row in enumerate(extraction.run.program_tally(top_programs), 1):
            writer.writerow([rank, row.program, row.department, row.recipients,
                             row.agreements, row.in_band, row.whitelist_words])
    return SNAPSHOT_DIR
