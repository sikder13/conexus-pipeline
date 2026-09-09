"""The filter pipeline: one pass over the dataset, and an account of every drop.

WHAT THIS IS FOR

A filter chain that returns a number is not reviewable. The operator's question
is never "how many companies", it is "why is that company not on the list", and
answering it after the fact means re-running the whole 2.3 GB. So the run counts
every exclusion at the stage that caused it, keeps the sub-reason, and prints
both. Each agreement is charged to exactly one stage — the first it failed — so
the counts add up to the total and can be read as a funnel.

THE ORDER OF THE STAGES IS A DECISION

province → business recipient → programme → award year → amount → industry.

Province first because it is free and drops two thirds of the file. Business
recipient before programme so that the top-thirty programme report is a report
about businesses, which is the only version of it that helps extend the
whitelist. Industry last because it is the only stage that reads the long text
fields, and by then there are a few thousand rows rather than half a million.

AMENDMENTS ARE RESOLVED BEFORE ANYTHING IS COUNTED

An agreement appears once per amendment and the latest supersedes the rest, so
the pipeline holds one verdict per reference number and replaces it when a later
amendment arrives. Counting rows instead would report the same award several
times, and filtering on a superseded amount would filter on a figure that is no
longer true. The verdict held per reference is small by construction: the full
award record is retained only for rows that are still passing.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict

from lib.sources.canada_gc.dataset import CanadaAward
from lib.sources.canada_gc.industries import UNCLASSIFIED, classify_industry
from lib.sources.canada_gc.programs import (
    WHITELIST,
    department_english,
    match_program,
)
from lib.sources.canada_gc.recipients import classify_recipient, company_key


class FilterSettings(BaseModel):
    """Every threshold this expansion filters on, in one reviewable object.

    Frozen, because a filter that a tool can change at runtime is a filter whose
    reported counts mean nothing.
    """

    model_config = ConfigDict(frozen=True)

    provinces: tuple[str, ...] = ("ON", "AB")
    min_amount: float = 25_000.0
    max_amount: float = 500_000.0
    min_award_year: int = 2020

    @property
    def band_words(self) -> str:
        return f"${self.min_amount:,.0f}-${self.max_amount:,.0f}"


DEFAULTS = FilterSettings()

_EPOCH = date(1900, 1, 1)
"""Sort floor for an award the record gives no date at all."""

STAGES: tuple[tuple[str, str], ...] = (
    ("province", "recipient is not in Ontario or Alberta (counted in rows)"),
    ("business_recipient", "recipient is not a business"),
    ("program", "programme is not on the whitelist"),
    ("award_year", "award is older than the cutoff, or carries no start date"),
    ("amount", "award is outside the SMB band"),
    ("industry", "award text names none of the four industries"),
)
"""The funnel, in order. Every excluded agreement is charged to exactly one."""

STAGE_WORDS = dict(STAGES)


class Verdict(NamedTuple):
    """What the pipeline decided about one agreement at its latest amendment.

    Deliberately small. One of these is held for every Ontario and Alberta
    agreement in the file — 445,216 of them on the 2026-09-08 data — so the full
    award record travels only on a verdict that is still passing.
    """

    order: tuple[int, str]
    stage: str
    reason: str
    program: str
    department: str
    recipient: str
    amount: float | None
    year: int | None
    family: str
    award: CanadaAward | None

    @property
    def kept(self) -> bool:
        return not self.stage

    @property
    def business(self) -> bool:
        """True once the recipient has cleared the business filter."""
        return self.stage not in ("province", "business_recipient")


def judge(award: CanadaAward, settings: FilterSettings = DEFAULTS) -> Verdict:
    """Run one award through the whole funnel and report where it stopped."""
    order = (award.amendment_number, award.amendment_date.isoformat()
             if award.amendment_date else "")
    program = award.program or "(no programme name published)"
    department = department_english(award.department)
    recipient = award.company_name

    def stop(stage: str, reason: str, family: str = UNCLASSIFIED) -> Verdict:
        return Verdict(order, stage, reason, program, department, recipient,
                       award.amount, award.year, family, None)

    if (award.province_code or "") not in settings.provinces:
        return stop("province", award.province_code or "(no province published)")

    recipient_verdict = classify_recipient(
        award.recipient_legal_name, award.recipient_operating_name,
        award.recipient_type_code,
    )
    if recipient_verdict.excluded:
        return stop("business_recipient", recipient_verdict.reason)

    if match_program(award.program, award.department) is None:
        return stop("program", program)

    if award.year is None or award.year < settings.min_award_year:
        return stop("award_year", str(award.year) if award.year else "no start date")

    if award.amount is None:
        return stop("amount", "no agreement value published")
    if not (settings.min_amount <= award.amount <= settings.max_amount):
        return stop(
            "amount",
            "below the floor" if award.amount < settings.min_amount else "above the ceiling",
        )

    placement = classify_industry(award.purpose_text, recipient)
    if not placement.classified:
        return stop("industry", UNCLASSIFIED)

    return Verdict(order, "", "", program, department, recipient,
                   award.amount, award.year, placement.family, award)


class ProgramTally(NamedTuple):
    """One row of the top-programmes report the operator extends the list from."""

    program: str
    department: str
    agreements: int
    recipients: int
    in_band: int
    listed: int
    unlisted: int

    @property
    def whitelisted(self) -> bool:
        """True when at least one agreement under this name is on the whitelist."""
        return self.listed > 0

    @property
    def whitelist_words(self) -> str:
        """yes, some, or nothing — because a programme name is not a programme.

        The dataset carries agreements with no programme name at all, and they
        collapse under one row here whatever department awarded them. Some of
        those departments are whitelisted and some are not, so a two-state
        column would have to answer a question the row cannot answer. "some"
        says that instead of picking.
        """
        if self.listed and self.unlisted:
            return "some"
        return "yes" if self.listed else "—"


class FilterRun:
    """One pass over the dataset, holding a verdict per agreement.

    Rows are handed in one at a time as they come off the socket; nothing here
    keeps the file, and the only thing that grows with the dataset is one small
    verdict per Ontario or Alberta agreement.
    """

    def __init__(self, settings: FilterSettings = DEFAULTS) -> None:
        self.settings = settings
        self.rows_read = 0
        self.rows_superseded = 0
        self.rows_out_of_province = 0
        self._verdicts: dict[str, Verdict] = {}
        # Which provinces each city name is used in, over every in-province row
        # rather than only the surviving ones. Whether "Cochrane" is ambiguous
        # is a property of the dataset, and asking only the 2,371 awards that
        # passed six filters would answer it from a sample too small to see the
        # collision.
        self.city_provinces: dict[str, Counter[str]] = defaultdict(Counter)

    def consider(self, award: CanadaAward) -> None:
        """Judge one row, keeping it only if it supersedes what we hold."""
        self.rows_read += 1
        verdict = judge(award, self.settings)
        if verdict.stage == "province":
            # Dropped without a verdict being held. Out-of-province rows are
            # two thirds of the file and holding one each would cost more
            # memory than every other stage put together — so this stage alone
            # is counted in rows rather than in agreements, and the report says
            # so rather than quietly mixing the two units.
            self.rows_out_of_province += 1
            return
        if award.city and award.province_code:
            self.city_provinces[_fold(award.city)][award.province_code] += 1
        held = self._verdicts.get(award.ref_number)
        if held is not None:
            self.rows_superseded += 1
            if verdict.order < held.order:
                return
        self._verdicts[award.ref_number] = verdict

    # ------------------------------------------------------------- reporting

    @property
    def agreements(self) -> int:
        """Distinct in-province agreements, after amendments are resolved."""
        return len(self._verdicts)

    def kept(self) -> list[CanadaAward]:
        """Every award that survived the whole funnel."""
        return [v.award for v in self._verdicts.values() if v.award is not None]

    def stage_counts(self) -> dict[str, int]:
        """How many each stage excluded, in funnel order.

        Every stage but the first counts agreements at their latest amendment.
        The province stage counts rows, because a row from another province
        never enters the agreement set at all — see ``consider``.
        """
        counts = Counter(v.stage for v in self._verdicts.values() if v.stage)
        counts["province"] = self.rows_out_of_province
        return {stage: counts.get(stage, 0) for stage, _words in STAGES}

    def reason_counts(self, stage: str) -> list[tuple[str, int]]:
        """The sub-reasons within one stage, commonest first."""
        counts = Counter(
            v.reason for v in self._verdicts.values() if v.stage == stage
        )
        return counts.most_common()

    def program_tally(self, limit: int = 30) -> list[ProgramTally]:
        """Programmes ranked by how many business recipients they reach here.

        Counted over agreements that cleared the business-recipient filter, so
        it answers the question the whitelist is actually extended from: which
        programmes are giving money to businesses in Ontario and Alberta, and
        how much of that lands inside the band we care about.
        """
        agreements: Counter[str] = Counter()
        in_band: Counter[str] = Counter()
        listed: Counter[str] = Counter()
        unlisted: Counter[str] = Counter()
        recipients: dict[str, set[str]] = defaultdict(set)
        departments: dict[str, Counter[str]] = defaultdict(Counter)

        for verdict in self._verdicts.values():
            if not verdict.business:
                continue
            program = verdict.program
            agreements[program] += 1
            recipients[program].add(company_key(verdict.recipient))
            departments[program][verdict.department] += 1
            if verdict.stage == "program":
                unlisted[program] += 1
            else:
                listed[program] += 1
            in_the_band = (
                verdict.year is not None
                and verdict.year >= self.settings.min_award_year
                and verdict.amount is not None
                and self.settings.min_amount <= verdict.amount <= self.settings.max_amount
            )
            if in_the_band:
                in_band[program] += 1

        ranked = sorted(
            agreements,
            key=lambda p: (-len(recipients[p]), -agreements[p], p),
        )[:limit]
        return [
            ProgramTally(
                program=program,
                department=(departments[program].most_common(1) or [("", 0)])[0][0],
                agreements=agreements[program],
                recipients=len(recipients[program]),
                in_band=in_band.get(program, 0),
                listed=listed.get(program, 0),
                unlisted=unlisted.get(program, 0),
            )
            for program in ranked
        ]


class EntryTally(NamedTuple):
    """What one whitelist entry actually caught in this run."""

    key: str
    display: str
    enabled: bool
    agreements: int
    recipients: int
    kept: int


def entry_tally(run: FilterRun) -> list[EntryTally]:
    """Per whitelist entry: what it matched, and what survived the rest of the funnel.

    Recomputed from the held verdicts rather than counted during the pass, so
    that turning an entry off and re-reporting does not require re-reading two
    gigabytes. An entry matching nothing is still listed — NGen matching nothing
    is the finding, and a row that disappears when it is empty cannot say so.
    """
    agreements: Counter[str] = Counter()
    kept: Counter[str] = Counter()
    recipients: dict[str, set[str]] = defaultdict(set)
    for verdict in run._verdicts.values():  # noqa: SLF001 — same module's own type
        if not verdict.business:
            continue
        match = match_program(verdict.program, verdict.department)
        if match is None:
            continue
        agreements[match.key] += 1
        recipients[match.key].add(company_key(verdict.recipient))
        if verdict.kept:
            kept[match.key] += 1
    return [
        EntryTally(entry.key, entry.display, entry.enabled, agreements[entry.key],
                   len(recipients[entry.key]), kept[entry.key])
        for entry in WHITELIST
    ]


# ------------------------------------------------------- companies, not awards

class CanadaRecipient(BaseModel):
    """One company and every award of its that survived the filters.

    The dataset is a ledger of agreements and we prospect companies, so the last
    step of the pipeline is a regrouping. Every award is kept: a company that
    took IRAP money three years running is a more interesting company than one
    that took it once, and picking a single award would throw that away.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    company_name: str
    legal_name: str
    province: str
    city: str | None
    postal_code: str | None
    naics: str | None
    family: str
    awards: tuple[CanadaAward, ...]

    @property
    def largest(self) -> CanadaAward:
        """The biggest award, which populates the single-value grant columns."""
        return max(self.awards, key=lambda a: (a.amount or 0.0))

    @property
    def total_awarded(self) -> float:
        return sum(a.amount or 0.0 for a in self.awards)

    @property
    def latest_year(self) -> int | None:
        years = [a.year for a in self.awards if a.year is not None]
        return max(years) if years else None

    @property
    def provinces(self) -> tuple[str, ...]:
        return tuple(sorted({a.province_code for a in self.awards if a.province_code}))


def group_recipients(awards: list[CanadaAward]) -> list[CanadaRecipient]:
    """Collapse surviving awards into one record per company.

    Companies are matched on the same normalised key the extractor uses against
    existing prospects, so a company deduplicated here is a company that will
    also be recognised as already present in the database rather than inserted
    twice under a different spelling.

    Awards are sorted newest first inside each company, and the industry family
    is taken from the largest award: where a company's grants straddle two
    families, the biggest one is the one the analysis will be about.
    """
    grouped: dict[str, list[CanadaAward]] = defaultdict(list)
    for award in awards:
        grouped[company_key(award.company_name)].append(award)

    recipients: list[CanadaRecipient] = []
    for key, group in grouped.items():
        group.sort(key=lambda a: (a.start_date or a.amendment_date or _EPOCH), reverse=True)
        largest = max(group, key=lambda a: (a.amount or 0.0))
        placement = classify_industry(largest.purpose_text, largest.company_name)
        recipients.append(CanadaRecipient(
            key=key,
            company_name=largest.company_name,
            legal_name=largest.recipient_legal_name,
            province=largest.province_code or "",
            city=largest.city,
            postal_code=largest.postal_code,
            naics=next((a.naics for a in group if a.naics), None),
            family=placement.family,
            awards=tuple(group),
        ))
    recipients.sort(key=lambda r: (-r.total_awarded, r.company_name))
    return recipients


def _by(values: list[Any]) -> list[tuple[Any, int]]:
    return Counter(values).most_common()


def province_counts(recipients: list[CanadaRecipient]) -> list[tuple[str, int]]:
    return _by([r.province for r in recipients])


def family_counts(recipients: list[CanadaRecipient]) -> list[tuple[str, int]]:
    return _by([r.family for r in recipients])


# ----------------------------------------------------------------- wave order

MIN_RECORDS_FOR_A_SECOND_TOWN = 3
MIN_SHARE_FOR_A_SECOND_TOWN = 0.10
"""When a city name is used by two provinces rather than mistyped by one.

Both tests, because either alone gets it wrong. A flat count of one condemned
Calgary — ten Ontario returns out of thirty-four thousand carry it, which is a
typing error rather than an Ontario town, and reading those as evidence held 148
of the first 300 companies. Raising the count to three still condemned Toronto,
Calgary and Edmonton, because at that scale even the error rate clears any
constant.

So the minority side must also be a real share of the majority. Toronto is
3 Alberta records against 81,458 Ontario ones — four thousandths of a percent.
Cochrane is 404 against 526, and there genuinely is a Cochrane in each province.
Ten percent separates them, and it leaves Cochrane, Stirling, Coleman and
St Isidore flagged while Toronto, Calgary, Edmonton and Kingston are not."""

AMBIGUOUS_CITY_NOTE = (
    "the recipient city is recorded under more than one province in this "
    "dataset, so the company cannot be placed from the record alone"
)
NO_CITY_NOTE = "no recipient city is published for this company"


def ambiguous_cities(
    awards: list[CanadaAward] | None = None,
    city_provinces: dict[str, Counter[str]] | None = None,
) -> set[str]:
    """City names the dataset itself records in more than one province.

    Read off the data rather than from a gazetteer, and deliberately so: the
    question is not whether two places share a name somewhere in the world, it
    is whether THIS file uses the name for more than one place. Ontario and
    Alberta both have a Wellington, a Milton and a Hanover, and a downstream
    node that searches for "Hanover" plus a company name can resolve the wrong
    company's website with complete confidence.

    Comparison is on the folded name, because the file writes the same city
    with and without accents and in both cases.
    """
    provinces: dict[str, Counter[str]] = defaultdict(Counter)
    if city_provinces:
        for city, seen in city_provinces.items():
            provinces[city].update(seen)
    for award in awards or ():
        if award.city and award.province_code:
            provinces[_fold(award.city)][award.province_code] += 1
    ambiguous = set()
    for city, seen in provinces.items():
        counts = sorted(seen.values(), reverse=True)
        if len(counts) < 2:
            continue
        if (counts[1] >= MIN_RECORDS_FOR_A_SECOND_TOWN
                and counts[1] >= counts[0] * MIN_SHARE_FOR_A_SECOND_TOWN):
            ambiguous.add(city)
    return ambiguous


def _fold(city: str | None) -> str:
    return " ".join((city or "").lower().replace("-", " ").split())


def city_review_reason(
    recipient: CanadaRecipient, ambiguous: set[str]
) -> str | None:
    """Why this company needs a human glance before research, or None.

    Province is never inferred by this adapter — the dataset publishes it, and a
    record without one never reaches here because the province filter drops it.
    The city is the field that can be genuinely ambiguous, and an ambiguous city
    is a research hazard rather than a reason to exclude: the company is real,
    we simply cannot say from the record which town it is in.
    """
    if not recipient.city:
        return NO_CITY_NOTE
    if _fold(recipient.city) in ambiguous:
        return f"{AMBIGUOUS_CITY_NOTE}: {recipient.city!r}"
    return None


def wave_rank(recipient: CanadaRecipient) -> tuple[Any, ...]:
    """The order wave one is drawn in, most promising first.

    Three keys, in the order the brief names them:

    1. **Which programme.** Every surviving company matched the whitelist, so
       this ranks by WHICH entry it matched, in the order `programs.WHITELIST`
       declares — IRAP before a regional agency before an agri-food programme.
       That order is a judgement about how much the award tells us: an IRAP
       contribution says a company chose to spend on its own R&D, a relief-fund
       payment says it was open in 2020.
    2. **Recency.** The most recent award year, newest first. A machine bought
       last year is still being learned; one bought in 2020 is furniture.
    3. **Amount.** The largest award inside the band, largest first. Within the
       SMB band a bigger award is a bigger commitment, and the band is what
       stops that from selecting enterprises.
    """
    from lib.sources.canada_gc.programs import WHITELIST

    order = {entry.key: index for index, entry in enumerate(WHITELIST)}
    largest = recipient.largest
    match = match_program(largest.program, largest.department)
    return (
        order.get(match.key if match else "", len(order)),
        -(recipient.latest_year or 0),
        -(largest.amount or 0.0),
        recipient.company_name,
    )


def wave(recipients: list[CanadaRecipient], size: int | None) -> list[CanadaRecipient]:
    """The first N companies to research, in wave order."""
    ranked = sorted(recipients, key=wave_rank)
    return ranked[:size] if size else ranked
