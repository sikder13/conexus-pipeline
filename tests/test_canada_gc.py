"""Tests for the canada_gc adapter — the filters that decide who gets contacted.

Every assertion here is about a decision an operator would have to defend. The
filters remove 99.6% of the file, so the interesting question is never "did it
parse" but "did it drop the right things, and does it say why". Each stage is
tested for both answers: what it keeps and what it refuses, with the reason.

No network. The CSV is built in the test, the data dictionary is a literal, and
the streaming path is driven by a fake client that hands back bytes in awkward
chunk boundaries on purpose.
"""

from __future__ import annotations

import asyncio
import csv
import io

import pytest

from lib.evidence import BLOCK2_GRANT_FUNDED, FLAGS_KEY
from lib.nodes import RunContext
from lib.sources.canada_gc import adapter as adapter_module
from lib.sources.canada_gc.adapter import CanadaGCAdapter, as_raw_prospect
from lib.sources.canada_gc.dataset import (
    REQUIRED_FIELDS,
    DataDictionary,
    DictionaryMismatch,
    RecordAssembler,
    award_from_row,
    check_header,
    iter_rows,
    parse_dictionary,
    parse_money,
    split_lines,
)
from lib.sources.canada_gc.filters import (
    DEFAULTS,
    NO_CITY_NOTE,
    FilterRun,
    FilterSettings,
    ambiguous_cities,
    city_review_reason,
    entry_tally,
    family_counts,
    group_recipients,
    judge,
    province_counts,
    wave,
)
from lib.sources.canada_gc.industries import classify_industry
from lib.sources.canada_gc.programs import WHITELIST, match_program, normalise
from lib.sources.canada_gc.recipients import (
    classify_recipient,
    company_key,
    has_corporate_form,
)
from tests.conftest import FakeResponse

COLUMNS = (
    "ref_number", "amendment_number", "amendment_date", "agreement_type",
    "recipient_type", "recipient_business_number", "recipient_legal_name",
    "recipient_operating_name", "research_organization_name", "recipient_country",
    "recipient_province", "recipient_city", "recipient_postal_code",
    "federal_riding_name_en", "federal_riding_name_fr", "federal_riding_number",
    "prog_name_en", "prog_name_fr", "prog_purpose_en", "prog_purpose_fr",
    "agreement_title_en", "agreement_title_fr", "agreement_number",
    "agreement_value", "foreign_currency_type", "foreign_currency_value",
    "agreement_start_date", "agreement_end_date", "coverage", "description_en",
    "description_fr", "naics_identifier", "expected_results_en",
    "expected_results_fr", "additional_information_en", "additional_information_fr",
    "owner_org", "owner_org_title",
)

DICTIONARY = DataDictionary(
    labels={name: name.replace("_", " ").title() for name in REQUIRED_FIELDS},
    choices={
        "recipient_type": {
            "F": "For-profit organizations", "G": "Government",
            "N": "Not-for-profit organizations and charities", "S": "Academia",
            "P": "Individual or sole proprietorships", "A": "Indigenous recipients",
            "O": "Other", "I": "International (non-government)",
        },
        "recipient_province": {"ON": "Ontario", "AB": "Alberta", "QC": "Quebec"},
        "agreement_type": {"C": "Contribution", "G": "Grant"},
        "recipient_country": {"CA": "Canada"},
    },
)

IRAP = "Industrial Research Assistance Program – Contributions to Firms"
NRC = "National Research Council Canada | Conseil national de recherches Canada"


def row(**overrides) -> dict[str, str]:
    """One CSV row with sane defaults, overridden field by field."""
    base = dict.fromkeys(COLUMNS, "")
    base.update({
        "ref_number": "REF-1",
        "amendment_number": "0",
        "agreement_type": "C",
        "recipient_type": "F",
        "recipient_legal_name": "Acme Tool and Die Inc.",
        "recipient_country": "CA",
        "recipient_province": "ON",
        "recipient_city": "Kitchener",
        "prog_name_en": IRAP,
        "prog_purpose_en": "Funds research and development inside small firms.",
        "agreement_title_en": "Automated inspection cell",
        "agreement_value": "75000.00",
        "agreement_start_date": "2024-04-01",
        "description_en": "The company will install a machine vision inspection "
                          "cell on its CNC machining line.",
        "owner_org": "nrc-cnrc",
        "owner_org_title": NRC,
    })
    base.update({k: str(v) for k, v in overrides.items()})
    return base


def csv_text(rows: list[dict[str, str]]) -> str:
    """Render rows as the published file does, header included."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(COLUMNS), lineterminator="\r\n")
    writer.writeheader()
    for entry in rows:
        writer.writerow(entry)
    return buffer.getvalue()


def award(**overrides):
    return award_from_row(row(**overrides), DICTIONARY)


# ---------------------------------------------------------------- the reader

class TestRecordAssembler:
    def test_a_plain_record_is_one_line(self):
        assembler = RecordAssembler()
        assert assembler.feed("a,b,c\n") == ["a,b,c\n"]

    def test_a_quoted_newline_holds_the_record_open(self):
        assembler = RecordAssembler()
        assert assembler.feed('a,"first\n') == []
        assert assembler.feed('second",c\n') == ['a,"first\nsecond",c\n']

    def test_a_doubled_quote_inside_a_field_does_not_reopen_it(self):
        assembler = RecordAssembler()
        assert assembler.feed('a,"he said ""hello""",c\n') == [
            'a,"he said ""hello""",c\n'
        ]

    def test_a_last_line_with_no_terminator_is_still_a_record(self):
        # feed() is handed one complete line at a time; the last line of a file
        # that ends without a newline is still a complete record.
        assembler = RecordAssembler()
        assert assembler.feed("a,b,c") == ["a,b,c"]

    def test_a_field_left_open_at_the_end_of_the_stream_is_flushed(self):
        assembler = RecordAssembler()
        assert assembler.feed('a,"unterminated\n') == []
        assert assembler.flush() == ['a,"unterminated\n']

    def test_an_embedded_newline_survives_into_the_parsed_value(self):
        text = csv_text([row(description_en="line one\nline two")])
        rows = list(iter_rows(io.StringIO(text, newline="")))
        assert rows[0]["description_en"] == "line one\nline two"


class TestSplitLines:
    def test_it_keeps_the_terminator(self):
        lines, tail = split_lines("a\nb\n", "")
        assert lines == ["a\n", "b\n"]
        assert tail == ""

    def test_an_incomplete_tail_is_carried_forward(self):
        lines, tail = split_lines("a\nbc", "")
        assert lines == ["a\n"]
        assert tail == "bc"
        lines, tail = split_lines("d\n", tail)
        assert lines == ["bcd\n"]

    def test_a_form_feed_is_not_a_line_break(self):
        # str.splitlines would break here; the descriptions in this dataset
        # contain control characters and a split there corrupts a field.
        lines, tail = split_lines("a\x0cb\n", "")
        assert lines == ["a\x0cb\n"]


class TestHeaderAndDictionary:
    def test_a_missing_column_stops_the_run(self):
        with pytest.raises(DictionaryMismatch) as caught:
            check_header([c for c in COLUMNS if c != "agreement_value"])
        assert "agreement_value" in str(caught.value)

    def test_the_published_header_is_accepted(self):
        assert check_header(COLUMNS) == list(COLUMNS)

    def test_a_dictionary_without_the_grants_resource_is_refused(self):
        with pytest.raises(DictionaryMismatch):
            parse_dictionary({"resources": [{"resource_name": "grants-nil"}]})

    def test_a_dictionary_missing_a_field_we_read_is_refused(self):
        fields = [{"id": name, "label": {"en": name}} for name in REQUIRED_FIELDS
                  if name != "recipient_province"]
        with pytest.raises(DictionaryMismatch) as caught:
            parse_dictionary({"resources": [{"resource_name": "grants", "fields": fields}]})
        assert "recipient_province" in str(caught.value)

    def test_codes_are_expanded_through_the_published_words(self):
        assert DICTIONARY.decode("recipient_type", "F") == "For-profit organizations"

    def test_an_undefined_code_decodes_to_nothing_rather_than_itself(self):
        # A letter the dictionary does not define is not a fact about the
        # recipient, and passing it through would let it read as one.
        assert DICTIONARY.decode("recipient_type", "Z") is None


class TestParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("75000.00", 75000.0), ("$1,200", 1200.0), ("-500.0", -500.0),
         ("", None), ("n/a", None)],
    )
    def test_money(self, raw, expected):
        assert parse_money(raw) == expected

    def test_a_zero_value_is_a_figure_and_an_empty_one_is_not(self):
        # An amendment can cancel an agreement, so zero is a real published
        # amount; collapsing "nothing published" into it would invent a finding.
        assert parse_money("0.0") == 0.0
        assert parse_money(None) is None

    def test_the_award_year_comes_from_the_start_date_and_is_never_invented(self):
        assert award(agreement_start_date="2024-04-01").year == 2024
        assert award(agreement_start_date="").year is None
        assert award(agreement_start_date="not a date").year is None

    def test_the_operating_name_is_preferred_over_a_numbered_legal_name(self):
        record = award(recipient_legal_name="1389066 Ontario Ltd.",
                       recipient_operating_name="Riverbend Machining")
        assert record.company_name == "Riverbend Machining"
        assert record.recipient_legal_name == "1389066 Ontario Ltd."


# ------------------------------------------------------------- the recipients

class TestRecipientFilter:
    @pytest.mark.parametrize(
        ("code", "reason"),
        [("G", "government"), ("N", "nonprofit"), ("S", "academia"),
         ("P", "individual"), ("I", "international")],
    )
    def test_the_published_code_settles_it(self, code, reason):
        verdict = classify_recipient("Somebody", None, code)
        assert verdict.excluded
        assert verdict.reason == reason

    def test_a_for_profit_code_is_a_business(self):
        assert classify_recipient("Acme Tool and Die Inc.", None, "F").is_business

    def test_an_institution_name_overrides_a_for_profit_code(self):
        # Miscoding happens, and a school board coded F would otherwise be
        # prospected as a manufacturer.
        verdict = classify_recipient("Waterloo Region District School Board", None, "F")
        assert verdict.excluded
        assert verdict.reason == "academia"

    @pytest.mark.parametrize(
        ("name", "reason"),
        [("City of Ottawa", "municipality"),
         ("The Corporation of the Town of Oakville", "municipality"),
         ("Ville de Gatineau", "municipality"),
         ("University of Guelph", "academia"),
         ("London Health Sciences Centre", "hospital"),
         ("Ontario Ginseng Growers Association", "nonprofit"),
         ("Government of Alberta", "government")],
    )
    def test_institution_names_are_refused_with_the_kind_that_matched(self, name, reason):
        verdict = classify_recipient(name, None, "")
        assert verdict.excluded
        assert verdict.reason == reason
        assert "matched" in verdict.detail

    @pytest.mark.parametrize(
        "name",
        ["Town Square Brewing Inc.", "Village Juicery Inc.", "Fresh City Farms, Inc."],
    )
    def test_a_common_word_is_not_a_municipality(self, name):
        # These three are real food businesses that an earlier, blunter
        # municipality pattern removed on the words "town", "village" and "city".
        assert classify_recipient(name, None, "F").is_business

    def test_an_unclassified_recipient_needs_a_legal_form_in_its_name(self):
        assert classify_recipient("Riverbend Machining Ltd.", None, "").is_business
        refused = classify_recipient("Greg Stewart", None, "")
        assert refused.excluded
        assert refused.reason == "no_corporate_form"

    def test_an_indigenous_coded_recipient_is_judged_on_its_name_not_its_code(self):
        # The code covers band councils and Indigenous-owned corporations alike.
        assert classify_recipient("Northern Forge Manufacturing Ltd.", None, "A").is_business
        assert classify_recipient("Piikani Nation", None, "A").excluded

    @pytest.mark.parametrize(
        "name",
        ["Acme Inc.", "Acme Ltd", "Acme Limited", "Fabrication Nadeau Ltée",
         "Acme Holdings ULC", "Acme Corp.", "1389066 Ontario Limited"],
    )
    def test_legal_forms_are_recognised_in_both_languages(self, name):
        assert has_corporate_form(name)

    def test_a_bare_trade_name_is_not_a_legal_form(self):
        assert not has_corporate_form("Bear Bait Honey")


class TestCompanyKey:
    def test_canadian_legal_forms_collapse(self):
        assert company_key("Fabrication Nadeau Ltée") == company_key(
            "Fabrication Nadeau Limitée")

    def test_it_agrees_with_the_extractor_on_an_ordinary_name(self):
        from lib.sources.conexus import normalize_name
        assert company_key("Acme Tool Inc.") == normalize_name("Acme Tool Inc.")


# ---------------------------------------------------------------- programmes

class TestProgramWhitelist:
    def test_every_irap_programme_name_matches_one_entry(self):
        for name in (
            "Industrial Research Assistance Program – Contributions to Firms",
            "Industrial Research Assistance Program - AI Assist",
            "Innovation Assistance Program",
        ):
            assert match_program(name, NRC).key == "nrc_irap"

    def test_a_dash_variant_does_not_change_the_answer(self):
        assert normalise("Program – Firms") == normalise("Program - Firms")

    def test_the_regional_agencies_match_on_their_department(self):
        assert match_program("Some Small Programme",
                             "Prairies Economic Development Canada | X").key == "prairies_can"
        assert match_program(
            "Business Scale-up and Productivity (BSUP)",
            "Federal Economic Development Agency for Southern Ontario | X",
        ).key == "feddev_ontario"

    def test_western_economic_diversification_is_matched_as_prairiescan(self):
        # The department was renamed in 2021 and the historical records keep the
        # old name; matching only the new one loses Alberta's pre-2022 half.
        assert match_program(
            "Regional Relief and Recovery Fund (RRRF) – Direct",
            "Western Economic Diversification Canada | X",
        ).key == "prairies_can"

    def test_an_unlisted_programme_matches_nothing(self):
        assert match_program("Youth - Canada Summer Jobs",
                             "Employment and Social Development Canada | X") is None

    def test_a_disabled_entry_stops_matching(self, monkeypatch):
        from lib.sources.canada_gc import programs
        off = tuple(e._replace(enabled=False) if e.key == "nrc_irap" else e
                    for e in programs.WHITELIST)
        monkeypatch.setattr(programs, "WHITELIST", off)
        assert programs.match_program(IRAP, NRC) is None

    def test_every_entry_carries_a_rationale(self):
        for entry in WHITELIST:
            assert entry.rationale.strip(), entry.key


# ---------------------------------------------------------------- industries

class TestIndustryFamilies:
    @pytest.mark.parametrize(
        ("text", "name", "family"),
        [
            ("machine vision on the CNC machining line", "Acme Tool", "manufacturing"),
            ("a new bottling line for the winery", "Estate Winery", "food_processing"),
            ("greenhouse growing and irrigation", "Zwart Farms", "agri_food"),
            ("", "Redline Trucking Ltd.", "distribution"),
        ],
    )
    def test_placements(self, text, name, family):
        assert classify_industry(text, name).family == family

    def test_the_words_that_placed_it_travel_with_the_placement(self):
        placement = classify_industry("installs a CNC machining centre", "Acme")
        assert placement.classified
        assert "machining" in placement.matched
        assert "machining" in placement.basis

    def test_nothing_matching_is_unclassified_rather_than_a_residue(self):
        placement = classify_industry("a mobile application for dentists", "Toothy Inc.")
        assert not placement.classified
        assert placement.matched == ()

    def test_a_word_is_never_found_inside_a_longer_word(self):
        # "scanning" contains "canning"; matching it put three software
        # companies in food processing.
        assert not classify_industry("document scanning software", "Acme").classified

    def test_greenhouse_gas_is_not_a_greenhouse(self):
        assert not classify_industry(
            "reduces greenhouse gas emissions from the fleet", "Clean Energy Inc."
        ).classified

    def test_one_ambiguous_word_does_not_place_a_distributor(self):
        # "warehouse" appears in what software does for somebody else's
        # warehouse; distribution needs two words or the name.
        assert not classify_industry(
            "software that talks to a customer warehouse", "Plane Sciences Inc."
        ).classified

    def test_the_name_alone_is_enough(self):
        assert classify_industry("", "Sidhu Freightlines Ltd.").family == "distribution"


# -------------------------------------------------------------- the funnel

class TestJudge:
    def test_an_ontario_irap_manufacturer_survives(self):
        verdict = judge(award())
        assert verdict.kept
        assert verdict.family == "manufacturing"

    @pytest.mark.parametrize(
        ("overrides", "stage"),
        [
            ({"recipient_province": "QC"}, "province"),
            ({"recipient_type": "N"}, "business_recipient"),
            ({"prog_name_en": "Youth - Canada Summer Jobs",
              "owner_org_title": "Employment and Social Development Canada | X"},
             "program"),
            ({"agreement_start_date": "2019-04-01"}, "award_year"),
            ({"agreement_value": "12000"}, "amount"),
            ({"agreement_value": "900000"}, "amount"),
            ({"recipient_legal_name": "Toothy Software Inc.", "agreement_title_en": "",
              "description_en": "a mobile app for dentists"}, "industry"),
        ],
    )
    def test_each_stage_refuses_what_it_is_for(self, overrides, stage):
        assert judge(award(**overrides)).stage == stage

    def test_an_award_with_no_start_date_is_refused_not_assumed(self):
        assert judge(award(agreement_start_date="")).stage == "award_year"

    def test_the_band_is_inclusive_at_both_ends(self):
        assert judge(award(agreement_value="25000")).kept
        assert judge(award(agreement_value="500000")).kept

    def test_the_thresholds_are_configurable(self):
        loose = FilterSettings(min_amount=1000, max_amount=10_000_000,
                               min_award_year=2010, provinces=("ON", "AB", "QC"))
        assert judge(award(recipient_province="QC", agreement_value="12000"),
                     loose).kept


class TestFilterRun:
    def _run(self, rows, settings=DEFAULTS) -> FilterRun:
        run = FilterRun(settings)
        for entry in rows:
            run.consider(award_from_row(entry, DICTIONARY))
        return run

    def test_a_later_amendment_supersedes_an_earlier_one(self):
        run = self._run([
            row(ref_number="R1", amendment_number="0", agreement_value="75000"),
            row(ref_number="R1", amendment_number="1", agreement_value="900000"),
        ])
        assert run.agreements == 1
        assert run.rows_superseded == 1
        assert run.kept() == []
        assert run.stage_counts()["amount"] == 1

    def test_an_amendment_arriving_out_of_order_does_not_win(self):
        run = self._run([
            row(ref_number="R1", amendment_number="2", agreement_value="75000"),
            row(ref_number="R1", amendment_number="1", agreement_value="900000"),
        ])
        assert len(run.kept()) == 1

    def test_an_amendment_can_bring_an_award_into_the_band(self):
        run = self._run([
            row(ref_number="R1", amendment_number="0", agreement_value="9000"),
            row(ref_number="R1", amendment_number="1", agreement_value="75000"),
        ])
        assert len(run.kept()) == 1

    def test_out_of_province_rows_are_counted_in_rows_not_agreements(self):
        run = self._run([
            row(ref_number="Q1", recipient_province="QC"),
            row(ref_number="Q1", recipient_province="QC", amendment_number="1"),
            row(ref_number="R1"),
        ])
        assert run.agreements == 1
        assert run.stage_counts()["province"] == 2

    def test_every_exclusion_is_charged_to_exactly_one_stage(self):
        rows = [
            row(ref_number="R1"),
            row(ref_number="R2", recipient_type="N"),
            row(ref_number="R3", agreement_value="9000"),
            row(ref_number="R4", agreement_start_date="2018-01-01"),
        ]
        run = self._run(rows)
        counts = run.stage_counts()
        excluded = sum(v for k, v in counts.items() if k != "province")
        assert excluded + len(run.kept()) == run.agreements

    def test_the_reason_within_a_stage_is_kept(self):
        run = self._run([row(ref_number="R2", recipient_type="N")])
        assert run.reason_counts("business_recipient") == [("nonprofit", 1)]

    def test_the_programme_tally_counts_businesses_whether_listed_or_not(self):
        run = self._run([
            row(ref_number="R1"),
            row(ref_number="R2", prog_name_en="Youth - Canada Summer Jobs",
                owner_org_title="Employment and Social Development Canada | X"),
        ])
        tally = {t.program: t for t in run.program_tally()}
        assert tally[IRAP].whitelisted is True
        assert tally["Youth - Canada Summer Jobs"].whitelisted is False
        assert tally[IRAP].in_band == 1

    def test_an_entry_matching_nothing_still_appears(self):
        # NGen matching nothing is the finding; a row that vanishes when empty
        # cannot report it.
        run = self._run([row()])
        keys = {entry.key for entry in entry_tally(run)}
        assert "ngen" in keys
        assert next(e for e in entry_tally(run) if e.key == "ngen").agreements == 0


class TestGrouping:
    def test_a_company_keeps_every_award(self):
        awards = [
            award(ref_number="R1", agreement_value="75000",
                  agreement_start_date="2024-04-01"),
            award(ref_number="R2", agreement_value="120000",
                  agreement_start_date="2023-04-01"),
        ]
        [recipient] = group_recipients(awards)
        assert len(recipient.awards) == 2
        assert recipient.total_awarded == 195000
        assert recipient.largest.amount == 120000
        assert recipient.latest_year == 2024

    def test_two_spellings_of_one_company_collapse(self):
        awards = [
            award(ref_number="R1", recipient_legal_name="Nadeau Fabrication Ltée"),
            award(ref_number="R2", recipient_legal_name="Nadeau Fabrication Limitée"),
        ]
        assert len(group_recipients(awards)) == 1

    def test_the_counts_by_province_and_family_add_up(self):
        awards = [
            award(ref_number="R1"),
            award(ref_number="R2", recipient_province="AB",
                  recipient_legal_name="Prairie Freight Ltd.",
                  description_en="expands its trucking and freight operation"),
        ]
        recipients = group_recipients(awards)
        assert dict(province_counts(recipients)) == {"ON": 1, "AB": 1}
        assert dict(family_counts(recipients)) == {
            "manufacturing": 1, "distribution": 1}

    def test_the_base_record_shape_leaves_indiana_columns_null(self):
        [recipient] = group_recipients([award()])
        raw = as_raw_prospect(recipient)
        assert raw.county is None
        assert raw.case_study_url is None
        assert raw.grant_amount == 75000
        assert raw.grant_year == 2024
        assert "county" in raw.missing_fields()


# ----------------------------------------------------------------- the adapter

class StreamingClient:
    """A fake httpx client that serves the dictionary and streams the CSV.

    Chunk boundaries are deliberately awkward: the body is handed over in small
    slices that fall in the middle of fields, of multi-byte characters and of
    quoted newlines, because that is what a socket does and it is the only way
    the incremental decoder is actually exercised.
    """

    def __init__(self, dictionary_json: dict, body: bytes, chunk: int = 7):
        self.dictionary_json = dictionary_json
        self.body = body
        self.chunk = chunk
        self.calls: list[str] = []

    async def get(self, url, **kwargs):
        self.calls.append(url)
        if url.endswith("robots.txt"):
            return FakeResponse("", status_code=404, url=url)
        return _JsonResponse(self.dictionary_json, url)

    def stream(self, method, url, **kwargs):
        self.calls.append(url)
        return _Stream(self.body, self.chunk, url)


class _JsonResponse(FakeResponse):
    def __init__(self, payload, url):
        super().__init__("", status_code=200, url=url)
        self._payload = payload
        self.headers = {}

    def json(self):
        return self._payload


class _Stream:
    def __init__(self, body: bytes, chunk: int, url: str):
        self.body, self.chunk, self.url = body, chunk, url
        self.status_code = 200
        self.headers = {"last-modified": "Tue, 08 Sep 2026 06:53:14 GMT",
                        "content-length": str(len(body))}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_bytes(self):
        for start in range(0, len(self.body), self.chunk):
            yield self.body[start:start + self.chunk]


DICTIONARY_JSON = {
    "resources": [{
        "resource_name": "grants",
        "fields": [
            {"id": name, "label": {"en": name}} for name in REQUIRED_FIELDS
        ] + [
            {"id": "recipient_type", "label": {"en": "Recipient Type"},
             "choices": {"F": {"en": "For-profit organizations"},
                         "N": {"en": "Not-for-profit organizations and charities"}}},
            {"id": "recipient_province", "label": {"en": "Province"},
             "choices": {"ON": {"en": "Ontario"}, "AB": {"en": "Alberta"}}},
        ],
    }]
}


class TestAdapter:
    def _extract(self, rows, settings_nodelay, chunk=7, tmp_path=None, monkeypatch=None):
        body = csv_text(rows).encode("utf-8-sig")
        client = StreamingClient(DICTIONARY_JSON, body, chunk)
        ctx = RunContext(client, settings_nodelay)
        if tmp_path is not None and monkeypatch is not None:
            monkeypatch.setattr(adapter_module, "SNAPSHOT_DIR", tmp_path)
        adapter = CanadaGCAdapter(ctx=ctx, save_snapshot=tmp_path is not None)
        return asyncio.run(adapter.extract_recipients()), client

    def test_it_streams_and_filters_without_holding_the_file(self, settings_nodelay):
        rows = [
            row(ref_number="R1"),
            row(ref_number="R2", recipient_type="N",
                recipient_legal_name="Kitchener Arts Society"),
            row(ref_number="R3", recipient_province="QC"),
        ]
        extraction, client = self._extract(rows, settings_nodelay)
        assert [r.company_name for r in extraction.recipients] == ["Acme Tool and Die Inc."]
        assert extraction.download.rows_read == 3
        assert extraction.run.stage_counts()["province"] == 1
        assert any("grants.csv" in call for call in client.calls)

    def test_a_quoted_newline_survives_an_awkward_chunk_boundary(self, settings_nodelay):
        rows = [row(description_en='machine vision\non the "CNC" line')]
        for chunk in (1, 3, 13, 512):
            extraction, _ = self._extract(rows, settings_nodelay, chunk=chunk)
            assert extraction.recipients[0].largest.description == (
                'machine vision\non the "CNC" line')

    def test_the_digest_and_byte_count_describe_what_was_read(self, settings_nodelay):
        rows = [row()]
        body = csv_text(rows).encode("utf-8-sig")
        extraction, _ = self._extract(rows, settings_nodelay)
        assert extraction.download.bytes_read == len(body)
        assert len(extraction.download.digest) == 64
        assert extraction.download.last_modified

    def test_the_snapshot_holds_the_download_and_the_surviving_rows(
        self, settings_nodelay, tmp_path, monkeypatch
    ):
        extraction, _ = self._extract([row()], settings_nodelay,
                                      tmp_path=tmp_path, monkeypatch=monkeypatch)
        assert (tmp_path / "download.json").exists()
        rows_csv = (tmp_path / "filtered_awards.csv").read_text(encoding="utf-8")
        assert "Acme Tool and Die Inc." in rows_csv
        assert (tmp_path / "top_programs.csv").exists()
        assert extraction.recipients

    def test_a_local_file_produces_the_same_companies(self, settings_nodelay, tmp_path):
        path = tmp_path / "grants.csv"
        path.write_bytes(csv_text([row(), row(ref_number="R2", recipient_type="P")])
                         .encode("utf-8-sig"))
        client = StreamingClient(DICTIONARY_JSON, b"")
        adapter = CanadaGCAdapter(ctx=RunContext(client, settings_nodelay),
                                  source_file=path, save_snapshot=False)
        extraction = asyncio.run(adapter.extract_recipients())
        assert [r.company_name for r in extraction.recipients] == ["Acme Tool and Die Inc."]
        assert extraction.download.source == str(path)


# -------------------------------------------------------------------- evidence

class TestEvidence:
    def test_the_purpose_becomes_a_tier_one_block_two_claim(self):
        from tools.canada_gc.main import build_evidence

        [recipient] = group_recipients([award()])
        block = build_evidence(recipient)[BLOCK2_GRANT_FUNDED]
        purpose = block["program_purpose"]
        assert purpose["value"].startswith("Funds research and development")
        assert purpose["tier"] == 1
        assert purpose["source_url"].startswith("https://open.canada.ca/data/en/dataset/")
        assert purpose["verified"] is False

    def test_every_award_is_stored_not_just_the_largest(self):
        from tools.canada_gc.main import build_evidence

        [recipient] = group_recipients([
            award(ref_number="R1", agreement_value="75000"),
            award(ref_number="R2", agreement_value="120000"),
        ])
        block = build_evidence(recipient)[BLOCK2_GRANT_FUNDED]
        assert len(block["grant_awards"]) == 2
        assert block["grant_award_count"]["value"] == 2
        assert "our sum" in block["grant_awards_total"]["value"]
        assert block["grant_awards_total"]["tier"] == 4

    def test_the_recency_flag_reads_the_latest_award(self):
        from tools.canada_gc.main import build_evidence

        [recent] = group_recipients([award(agreement_start_date="2024-04-01")])
        [older] = group_recipients([award(agreement_start_date="2021-04-01")])
        flags = build_evidence(recent)[BLOCK2_GRANT_FUNDED][FLAGS_KEY]
        assert flags["program_recency"]["value"] is True
        assert build_evidence(older)[BLOCK2_GRANT_FUNDED][FLAGS_KEY][
            "program_recency"]["value"] is False

    def test_reading_their_words_is_our_inference_and_is_tiered_that_way(self):
        from tools.canada_gc.main import build_evidence

        [recipient] = group_recipients([award()])
        flags = build_evidence(recipient)[BLOCK2_GRANT_FUNDED][FLAGS_KEY]
        flag = flags["purpose_names_data_generating_tech"]
        assert flag["value"] is True
        assert flag["tier"] == 4
        assert "vision" in flag["matched_terms"]

    def test_the_evidence_file_passes_the_database_shape_check(self):
        from lib.claims import validate_evidence_file
        from lib.runner import merge_notes
        from tools.canada_gc.main import absence_notes, build_evidence

        [recipient] = group_recipients([award()])
        evidence = merge_notes(build_evidence(recipient), "canada_gc",
                               absence_notes(recipient))
        assert validate_evidence_file(evidence) == []

    def test_what_the_source_does_not_publish_is_written_down(self):
        from tools.canada_gc.main import absence_notes

        [recipient] = group_recipients([award()])
        assert any("no website" in note for note in absence_notes(recipient))


class TestDeduplication:
    def test_an_existing_canadian_record_is_updated_not_duplicated(self):
        from tools.canada_gc.main import partition

        [recipient] = group_recipients([award()])
        existing = [{"id": "p1", "company_name": "Acme Tool and Die Inc.",
                     "source_adapter": "canada_gc"}]
        writable, collisions = partition([recipient], existing)
        assert collisions == []
        assert writable[0][1]["id"] == "p1"

    def test_a_name_shared_with_another_adapter_is_reported_never_merged(self):
        from tools.canada_gc.main import partition

        [recipient] = group_recipients([award()])
        existing = [{"id": "p1", "company_name": "Acme Tool and Die Inc.",
                     "source_adapter": "conexus_iedc"}]
        writable, collisions = partition([recipient], existing)
        assert writable == []
        assert collisions[0][1]["source_adapter"] == "conexus_iedc"

    def test_an_unseen_company_is_an_insert(self):
        from tools.canada_gc.main import partition

        [recipient] = group_recipients([award()])
        writable, collisions = partition([recipient], [])
        assert writable == [(recipient, None)]
        assert collisions == []


class TestProgrammeTallyHonesty:
    """A programme name is not a programme, and the tally has to say so.

    The dataset carries agreements with no programme name at all. They collapse
    under one row here whatever department awarded them, and some of those
    departments are whitelisted while others are not — so the row cannot answer
    "is this listed" with a yes or a no.
    """

    def _run(self, rows) -> FilterRun:
        run = FilterRun(DEFAULTS)
        for entry in rows:
            run.consider(award_from_row(entry, DICTIONARY))
        return run

    def test_a_name_shared_by_a_listed_and_an_unlisted_department_reads_some(self):
        run = self._run([
            row(ref_number="R1", prog_name_en="",
                owner_org_title="Federal Economic Development Agency for "
                                "Southern Ontario | X"),
            row(ref_number="R2", prog_name_en="",
                owner_org_title="Environment and Climate Change Canada | X"),
        ])
        blank = next(t for t in run.program_tally()
                     if t.program == "(no programme name published)")
        assert blank.whitelist_words == "some"
        assert blank.listed == 1
        assert blank.unlisted == 1

    def test_a_wholly_listed_programme_reads_yes(self):
        run = self._run([row(ref_number="R1")])
        assert next(t for t in run.program_tally() if t.program == IRAP
                    ).whitelist_words == "yes"

    def test_a_wholly_unlisted_programme_reads_as_nothing(self):
        run = self._run([
            row(ref_number="R1", prog_name_en="Youth - Canada Summer Jobs",
                owner_org_title="Employment and Social Development Canada | X"),
        ])
        assert next(t for t in run.program_tally()
                    if t.program == "Youth - Canada Summer Jobs"
                    ).whitelist_words == "—"


class TestSnapshotRows:
    """Zero is a published figure in this dataset and must not render as blank."""

    def test_amendment_zero_and_a_zero_value_survive_the_snapshot(self):
        from lib.sources.canada_gc.adapter import SNAPSHOT_COLUMNS, _award_row

        record = award(amendment_number="0", agreement_value="0.0")
        cells = dict(zip(SNAPSHOT_COLUMNS, _award_row(record), strict=True))
        assert cells["amendment_number"] == "0"
        assert cells["amount"] == "0.0"

    def test_a_column_the_record_does_not_carry_is_blank(self):
        from lib.sources.canada_gc.adapter import SNAPSHOT_COLUMNS, _award_row

        record = award(recipient_postal_code="", agreement_start_date="")
        cells = dict(zip(SNAPSHOT_COLUMNS, _award_row(record), strict=True))
        assert cells["postal_code"] == ""
        assert cells["start_date"] == ""


class TestExternalTechEngagement:
    """A company that has already paid an outsider to solve a technical problem
    has answered the hardest question in the first call."""

    def test_a_named_collaboration_sets_the_flag(self):
        from tools.canada_gc.main import build_evidence

        [recipient] = group_recipients([award(
            description_en="The company will develop the cell in partnership with "
                           "a systems integrator.")])
        flag = build_evidence(recipient)[BLOCK2_GRANT_FUNDED][FLAGS_KEY][
            "external_tech_engagement"]
        assert flag["value"] is True
        assert flag["tier"] == 4
        assert "in partnership with" in flag["matched_terms"]

    def test_no_named_partner_writes_no_flag(self):
        from tools.canada_gc.main import build_evidence

        [recipient] = group_recipients([award(
            description_en="The company will install a CNC machining centre.")])
        flags = build_evidence(recipient)[BLOCK2_GRANT_FUNDED].get(FLAGS_KEY, {})
        assert "external_tech_engagement" not in flags

    def test_the_phrase_list_is_read_across_every_award(self):
        from tools.canada_gc.main import external_tech_phrases

        assert external_tech_phrases(
            "delivered under contract with a research institute") == [
            "under contract with", "research institute"]
        assert external_tech_phrases("nothing external here") == []


class TestWaveOrder:
    """Which companies are researched first, and why those.

    Three keys in the order the brief names them: which programme matched, then
    how recent the award is, then how large it is inside the band. Each is a
    judgement worth being able to argue with, so each is pinned here.
    """

    def _recipients(self, awards):
        return group_recipients(awards)

    def test_irap_outranks_an_agri_food_programme(self):
        irap = award(ref_number="R1", recipient_legal_name="Alpha Machining Inc.")
        agri = award(ref_number="R2", recipient_legal_name="Beta Farms Ltd.",
                     prog_name_en="AgriInnovate Program",
                     owner_org_title="Agriculture and Agri-Food Canada | X",
                     description_en="expands the farm's grain handling")
        ordered = wave(self._recipients([irap, agri]), None)
        assert [r.company_name for r in ordered] == [
            "Alpha Machining Inc.", "Beta Farms Ltd."]

    def test_recency_breaks_a_tie_within_one_programme(self):
        older = award(ref_number="R1", recipient_legal_name="Alpha Machining Inc.",
                      agreement_start_date="2021-01-01", agreement_value="400000")
        newer = award(ref_number="R2", recipient_legal_name="Beta Machining Inc.",
                      agreement_start_date="2025-01-01", agreement_value="30000")
        ordered = wave(self._recipients([older, newer]), None)
        assert ordered[0].company_name == "Beta Machining Inc."

    def test_amount_breaks_a_tie_within_one_year(self):
        small = award(ref_number="R1", recipient_legal_name="Alpha Machining Inc.",
                      agreement_value="30000")
        large = award(ref_number="R2", recipient_legal_name="Beta Machining Inc.",
                      agreement_value="400000")
        ordered = wave(self._recipients([small, large]), None)
        assert ordered[0].company_name == "Beta Machining Inc."

    def test_the_wave_is_the_first_n_of_that_order(self):
        awards = [award(ref_number=f"R{i}",
                        recipient_legal_name=f"Company {i} Machining Inc.",
                        agreement_start_date=f"202{i}-01-01")
                  for i in range(1, 5)]
        assert len(wave(self._recipients(awards), 2)) == 2

    def test_the_order_is_stable_for_identical_records(self):
        awards = [award(ref_number="R1", recipient_legal_name="B Machining Inc."),
                  award(ref_number="R2", recipient_legal_name="A Machining Inc.")]
        assert [r.company_name for r in wave(self._recipients(awards), None)] == [
            "A Machining Inc.", "B Machining Inc."]


class TestCityReview:
    """An ambiguous city is a research hazard, not a reason to exclude."""

    def test_a_city_in_two_provinces_is_ambiguous(self):
        awards = [
            award(ref_number="R1", recipient_city="Hanover", recipient_province="ON"),
            award(ref_number="R2", recipient_city="Hanover", recipient_province="AB",
                  recipient_legal_name="Prairie Machining Ltd."),
        ]
        assert ambiguous_cities(awards) == {"hanover"}

    def test_accents_and_hyphens_do_not_hide_a_collision(self):
        awards = [
            award(ref_number="R1", recipient_city="Grande-Prairie",
                  recipient_province="ON"),
            award(ref_number="R2", recipient_city="grande prairie",
                  recipient_province="AB", recipient_legal_name="Beta Ltd."),
        ]
        assert ambiguous_cities(awards) == {"grande prairie"}

    def test_a_city_in_one_province_is_not(self):
        awards = [award(ref_number="R1", recipient_city="Kitchener")]
        assert ambiguous_cities(awards) == set()

    def test_an_ambiguous_city_is_flagged_for_review_with_the_name(self):
        [recipient] = group_recipients([award(recipient_city="Hanover")])
        reason = city_review_reason(recipient, {"hanover"})
        assert reason and "Hanover" in reason and "more than one province" in reason

    def test_a_missing_city_is_flagged_too(self):
        [recipient] = group_recipients([award(recipient_city="")])
        assert city_review_reason(recipient, set()) == NO_CITY_NOTE

    def test_an_unambiguous_company_is_not_held(self):
        [recipient] = group_recipients([award(recipient_city="Kitchener")])
        assert city_review_reason(recipient, {"hanover"}) is None

    def test_a_held_company_is_inserted_at_needs_review_rather_than_dropped(self):
        from tools.canada_gc.main import write_recipient

        [recipient] = group_recipients([award(recipient_city="")])
        written: dict = {}
        queued: list = []

        class FakeDB:
            @staticmethod
            def insert_prospect(row):
                written.update(row)
                return {"id": "p1"}

            @staticmethod
            def enqueue_work_items(pid, nodes):
                queued.append((pid, nodes))
                return len(nodes)

        import tools.canada_gc.main as loader
        real_db = loader.db
        loader.db = FakeDB
        try:
            outcome = write_recipient(recipient, None, NO_CITY_NOTE)
        finally:
            loader.db = real_db

        assert outcome == "inserted"
        assert written["stage"] == "needs_review"
        assert written["needs_review_reason"] == NO_CITY_NOTE
        assert queued and queued[0][0] == "p1"
        notes = " ".join(n["note"] for n in written["evidence_file"]["notes"])
        assert "needs review before research" in notes
