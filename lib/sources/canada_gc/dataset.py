"""Where the Canadian dataset lives, what its columns mean, and how to read it.

THE SOURCE

The Government of Canada publishes every grant and contribution awarded by an
institution subject to the Policy on Transfer Payments as one consolidated CSV,
under the Treasury Board proactive-disclosure regime. It is a government record
of the award, so everything read out of it is T1.

It is also 2.3 GB and 1.3 million rows, which is the single fact that shapes
this module. Nothing here ever holds the file: the CSV is parsed off the socket
a line at a time and every row is either turned into something small or thrown
away. `data/raw/canada_gc/` receives the download metadata and the filtered
rows, never the source file.

WHY THE DATA DICTIONARY IS FETCHED FIRST

The CSV's columns are coded — `recipient_type` is one letter, `recipient_province`
is a two-letter code — and the meanings live in a published dictionary rather
than in the file. Guessing them would be exactly the fabrication this pipeline
exists to prevent, and a column order that shifted under us would be worse: the
rows would still parse and every value would be wrong.

So the dictionary is fetched, the header is checked against it, and coded values
are expanded through the government's own labels. A header that no longer
matches stops the run instead of producing plausible nonsense.

AMENDMENTS

An agreement appears once per amendment, sharing a `ref_number`, and the latest
amendment supersedes the earlier ones — 40,669 of the 445,216 Ontario and
Alberta agreements carry at least one. Counting the rows rather than the
agreements would double-count them, and filtering on a superseded value would
filter on an amount that is no longer the amount. The reader therefore reports
rows, and the filter pipeline keeps only the highest amendment per reference.
"""

from __future__ import annotations

import csv
import re
from collections.abc import AsyncIterator, Iterable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

DATASET_ID = "432527ab-7aac-45b5-81d6-7597107a7013"

DATASET_URL = f"https://open.canada.ca/data/en/dataset/{DATASET_ID}"
"""The page a human opens to check any claim we make from this source.

Used as `source_url` on every claim. The search interface has per-award record
URLs, but they render client-side and return the empty search shell to a plain
fetch, so citing one would give a reader a page that does not show the award.
The reference number travels on the claim instead, which is what the search
form actually takes."""

CSV_URL = (
    f"https://open.canada.ca/data/dataset/{DATASET_ID}"
    f"/resource/1d15a62f-5656-49ad-8c88-f40ce689d831/download/grants.csv"
)
"""The consolidated CSV. Redirects to blob storage; the fetch follows it."""

DICTIONARY_URL = "https://open.canada.ca/data/recombinant-published-schema/grants.json"
"""The machine-readable data dictionary: field ids, labels, and coded values."""

SNAPSHOT_DIR = Path("data/raw/canada_gc")
"""Download metadata and the filtered rows. Gitignored — this is their data."""

RESOURCE_NAME = "grants"
"""Which resource inside the dictionary describes the consolidated CSV."""

MAX_FIELD_BYTES = 10_000_000
"""Some project descriptions run to tens of kilobytes; the csv module's default
limit of 128 KB is generous but not guaranteed, and a raised limit costs nothing."""

# The columns this adapter reads, and what each one is for. Every id here is
# checked against the published dictionary before a single row is parsed.
REQUIRED_FIELDS: tuple[str, ...] = (
    "ref_number",
    "amendment_number",
    "amendment_date",
    "agreement_type",
    "recipient_type",
    "recipient_legal_name",
    "recipient_operating_name",
    "recipient_country",
    "recipient_province",
    "recipient_city",
    "recipient_postal_code",
    "prog_name_en",
    "prog_purpose_en",
    "agreement_title_en",
    "agreement_number",
    "agreement_value",
    "agreement_start_date",
    "description_en",
    "naics_identifier",
    "expected_results_en",
)

# Present in the CSV and used, but added by the publishing platform rather than
# declared in the dictionary's field list, so they are checked separately.
PLATFORM_FIELDS: tuple[str, ...] = ("owner_org", "owner_org_title")

CODED_FIELDS: tuple[str, ...] = (
    "agreement_type",
    "recipient_type",
    "recipient_country",
    "recipient_province",
)
"""Fields whose values are codes the dictionary expands into words."""


class DictionaryMismatch(RuntimeError):
    """The published dictionary or the CSV header is not what this adapter reads.

    Raised rather than worked around. A renamed column does not make the parse
    fail — it makes every value silently wrong, which is the failure mode with no
    symptoms.
    """


class DataDictionary(BaseModel):
    """The published meaning of every column this adapter reads.

    `labels` is the English label of each field, and `choices` maps each coded
    field to the government's own words for its codes. Both are used rather than
    hardcoded so that a claim says "For-profit organizations" because the
    dictionary says so, not because we decided that is what "F" means.
    """

    model_config = ConfigDict(frozen=True)

    labels: dict[str, str]
    choices: dict[str, dict[str, str]]
    source_url: str = DICTIONARY_URL

    def label(self, field: str) -> str:
        """The dictionary's English label for a column."""
        return self.labels.get(field, field)

    def decode(self, field: str, code: str | None) -> str | None:
        """The dictionary's words for a coded value, or None when it has none.

        An unknown code returns None rather than the raw letter: a code the
        dictionary does not define is not a fact about the recipient, and
        passing it through would let it be read as one.
        """
        table = self.choices.get(field) or {}
        return table.get((code or "").strip().upper())


def parse_dictionary(payload: dict[str, Any]) -> DataDictionary:
    """Read the published JSON schema into the labels and codes we rely on."""
    resources = payload.get("resources") or []
    resource = next(
        (r for r in resources if r.get("resource_name") == RESOURCE_NAME), None
    )
    if resource is None:
        raise DictionaryMismatch(
            f"the data dictionary at {DICTIONARY_URL} has no {RESOURCE_NAME!r} "
            f"resource; it describes {[r.get('resource_name') for r in resources]}"
        )

    labels: dict[str, str] = {}
    choices: dict[str, dict[str, str]] = {}
    for field in resource.get("fields") or []:
        field_id = field.get("id")
        if not field_id:
            continue
        label = field.get("label")
        labels[field_id] = (
            label.get("en", field_id) if isinstance(label, dict) else str(label or field_id)
        )
        if field.get("choices"):
            choices[field_id] = {
                str(code).upper(): (words or {}).get("en", str(code))
                if isinstance(words, dict) else str(words)
                for code, words in field["choices"].items()
            }

    missing = [name for name in REQUIRED_FIELDS if name not in labels]
    if missing:
        raise DictionaryMismatch(
            f"the data dictionary no longer describes {', '.join(missing)}. The "
            f"adapter reads those columns by name; check {DICTIONARY_URL} before "
            f"changing anything here."
        )
    return DataDictionary(labels=labels, choices=choices)


def check_header(header: Iterable[str] | None) -> list[str]:
    """Raise unless the CSV carries every column this adapter reads.

    Returns the header as read, so a caller can record what the file actually
    offered alongside what we took from it.
    """
    columns = list(header or [])
    present = set(columns)
    missing = [
        name for name in (*REQUIRED_FIELDS, *PLATFORM_FIELDS) if name not in present
    ]
    if missing:
        raise DictionaryMismatch(
            f"the CSV header is missing {', '.join(missing)}; it carries "
            f"{len(columns)} column(s). The published layout has changed and the "
            f"field mapping must be re-checked against {DICTIONARY_URL}."
        )
    return columns


# ------------------------------------------------------------------- one award

_DIGITS = re.compile(r"-?\d+(?:\.\d+)?")


def parse_money(raw: str | None) -> float | None:
    """The agreement value as a number, or None when the field is unusable.

    Returns None rather than zero for an empty or unparseable value. Zero is a
    real figure in this dataset — an amendment can cancel an agreement — and
    collapsing "no value published" into it would turn a gap into a finding.
    """
    text = (raw or "").strip().replace(",", "").replace("$", "")
    if not text:
        return None
    found = _DIGITS.search(text)
    return float(found.group(0)) if found else None


def parse_date(raw: str | None) -> date | None:
    """An ISO date from the CSV, or None. Partial and malformed dates are None."""
    text = (raw or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_int(raw: str | None) -> int:
    """An amendment number. Anything unparseable sorts as the original record."""
    found = _DIGITS.search((raw or "").strip())
    return int(float(found.group(0))) if found else 0


class CanadaAward(BaseModel):
    """One agreement, as the dataset describes it at its latest amendment.

    Every field is a column of the published record. Nothing is derived here
    except the parsing of a number out of a string, and a column the dataset
    leaves empty stays None — the award year of an agreement with no start date
    is not guessed from its reference number.
    """

    model_config = ConfigDict(frozen=True)

    ref_number: str
    amendment_number: int
    amendment_date: date | None

    recipient_legal_name: str
    recipient_operating_name: str | None
    recipient_type_code: str | None
    recipient_type_words: str | None
    province_code: str | None
    province_words: str | None
    city: str | None
    postal_code: str | None

    program: str
    program_purpose: str | None
    agreement_title: str | None
    agreement_number: str | None
    agreement_type_words: str | None
    department: str | None
    owner_org: str | None

    amount: float | None
    start_date: date | None
    description: str | None
    expected_results: str | None
    naics: str | None

    @property
    def year(self) -> int | None:
        """The award year: the year the agreement starts, or None if unpublished."""
        return self.start_date.year if self.start_date else None

    @property
    def company_name(self) -> str:
        """The name to carry forward: the operating name where one is published.

        A recipient's legal name is often a numbered company; the operating name
        is what is on the building. Where both exist the operating name is used
        and the legal name is kept beside it, because the two together are how a
        human confirms they have found the right business.
        """
        return (self.recipient_operating_name or self.recipient_legal_name).strip()

    @property
    def purpose_text(self) -> str:
        """Everything the record says about what the money is for, in one string.

        This is the text the industry classifier reads. It is assembled here
        rather than in the classifier so that "the stated purpose" means one
        thing across the adapter, the report and the evidence file.
        """
        return " ".join(
            part for part in (
                self.program,
                self.program_purpose,
                self.agreement_title,
                self.description,
                self.expected_results,
            ) if part
        )


def _text(row: dict[str, str], key: str) -> str | None:
    value = (row.get(key) or "").strip()
    return value or None


def award_from_row(row: dict[str, str], dictionary: DataDictionary) -> CanadaAward:
    """Map one CSV row onto the published field meanings."""
    return CanadaAward(
        ref_number=(row.get("ref_number") or "").strip(),
        amendment_number=parse_int(row.get("amendment_number")),
        amendment_date=parse_date(row.get("amendment_date")),
        recipient_legal_name=(row.get("recipient_legal_name") or "").strip(),
        recipient_operating_name=_text(row, "recipient_operating_name"),
        recipient_type_code=(row.get("recipient_type") or "").strip().upper() or None,
        recipient_type_words=dictionary.decode("recipient_type", row.get("recipient_type")),
        province_code=(row.get("recipient_province") or "").strip().upper() or None,
        province_words=dictionary.decode("recipient_province", row.get("recipient_province")),
        city=_text(row, "recipient_city"),
        postal_code=_text(row, "recipient_postal_code"),
        program=(row.get("prog_name_en") or "").strip(),
        program_purpose=_text(row, "prog_purpose_en"),
        agreement_title=_text(row, "agreement_title_en"),
        agreement_number=_text(row, "agreement_number"),
        agreement_type_words=dictionary.decode("agreement_type", row.get("agreement_type")),
        department=_text(row, "owner_org_title"),
        owner_org=_text(row, "owner_org"),
        amount=parse_money(row.get("agreement_value")),
        start_date=parse_date(row.get("agreement_start_date")),
        description=_text(row, "description_en"),
        expected_results=_text(row, "expected_results_en"),
        naics=_text(row, "naics_identifier"),
    )


# --------------------------------------------------------------- reading rows

class RecordAssembler:
    """Collects CSV lines into complete records, honouring quoted newlines.

    A record is complete when the quotes seen so far balance. That is the whole
    rule for RFC 4180 CSV, because a quote inside a quoted field is written
    doubled, so an odd count can only mean a field is still open.

    The alternative — handing the line stream straight to ``csv.reader`` —
    works only if every line still carries its terminator, and getting that
    wrong does not raise: it silently welds two paragraphs of a project
    description into one word. Counting quotes states the invariant instead of
    depending on one.
    """

    def __init__(self) -> None:
        self._pending = ""
        self._inside_quotes = False

    def feed(self, line: str) -> list[str]:
        """Add one line; return the complete record(s) it finished, if any."""
        self._pending += line
        if line.count('"') % 2:
            self._inside_quotes = not self._inside_quotes
        if self._inside_quotes:
            return []
        record, self._pending = self._pending, ""
        return [record] if record else []

    def flush(self) -> list[str]:
        """Whatever is left when the stream ends — a file with no final newline."""
        record, self._pending = self._pending, ""
        self._inside_quotes = False
        return [record] if record.strip() else []


def parse_records(records: list[str], header: list[str]) -> list[dict[str, str]]:
    """Turn complete CSV records into dicts keyed by the header.

    Short rows are padded rather than dropped. A row missing its trailing
    optional columns is a row the publisher wrote that way, and refusing it
    would silently lose awards; a row with more columns than the header is a
    parse we do not understand, and it is skipped and counted by the caller.
    """
    csv.field_size_limit(MAX_FIELD_BYTES)
    width = len(header)
    rows: list[dict[str, str]] = []
    for values in csv.reader(records):
        if not values:
            continue
        if len(values) < width:
            values = [*values, *([""] * (width - len(values)))]
        rows.append(dict(zip(header, values, strict=False)))
    return rows


def read_header(records: list[str]) -> list[str]:
    """Check and return the header from the first assembled record."""
    csv.field_size_limit(MAX_FIELD_BYTES)
    first = next(iter(csv.reader(records[:1])), None)
    return check_header(first)


def iter_rows(lines: Iterator[str]) -> Iterator[dict[str, str]]:
    """Parse a stream of CSV lines into rows, checking the header first.

    The synchronous half of the reader, used when the CSV is a file on disk.
    The network path assembles records the same way against the socket.
    """
    assembler = RecordAssembler()
    header: list[str] | None = None
    for line in lines:
        for record in assembler.feed(line):
            if header is None:
                header = read_header([record])
                continue
            yield from parse_records([record], header)
    for record in assembler.flush():
        if header is None:
            header = read_header([record])
            continue
        yield from parse_records([record], header)


def split_lines(chunk: str, buffered: str) -> tuple[list[str], str]:
    """Split decoded text into complete lines, keeping the terminator.

    Returns the complete lines and whatever tail is still incomplete. Splitting
    on "\\n" alone rather than with ``str.splitlines`` is deliberate: splitlines
    also breaks on form feeds and Unicode separators, and those appear inside
    the free-text description fields of this dataset.
    """
    parts = (buffered + chunk).split("\n")
    tail = parts.pop()
    return [part + "\n" for part in parts], tail


async def lines_from_stream(chunks: AsyncIterator[bytes]) -> AsyncIterator[str]:
    """Decode a byte stream into CSV lines without buffering the whole file.

    The file is UTF-8 with a byte-order mark and a multi-byte character may
    straddle a chunk boundary, so decoding is incremental. Undecodable bytes are
    replaced rather than fatal: one bad byte in 2.3 GB should cost one character,
    not the run.
    """
    import codecs

    decoder = codecs.getincrementaldecoder("utf-8-sig")(errors="replace")
    buffered = ""
    async for chunk in chunks:
        lines, buffered = split_lines(decoder.decode(chunk), buffered)
        for line in lines:
            yield line
    tail = buffered + decoder.decode(b"", True)
    if tail:
        yield tail
