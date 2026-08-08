"""Tests for the Conexus source adapter and the extractor's classification rules.

Parsing runs against a saved fixture cut from the real recipient listing, so
these tests fail if the site's structure changes under us — which is exactly
when someone needs to know.

The fixture deliberately includes the awkward cases: a company whose
description is wrapped in a table, one with no website link, one with a
case-study subpage, and one whose text says it is a division of another firm.
"""

from __future__ import annotations

from lib.sources.conexus import (
    RECIPIENTS_URL,
    normalize_name,
    parse_case_study_urls,
    parse_recipients,
)
from tests.conftest import fixture
from tools.extractor.main import (
    absence_notes,
    build_evidence,
    classify_exclusion,
    deduplicate,
    source_columns,
)

RECIPIENTS_HTML = fixture("conexus_recipients.html")
MAP_HTML = fixture("conexus_grants_map.html")


def parsed():
    return parse_recipients(RECIPIENTS_HTML, parse_case_study_urls(MAP_HTML))


def by_name(name: str):
    return next(record for record in parsed() if record.company_name == name)


class TestParsing:
    def test_every_company_in_the_fixture_is_found(self):
        names = {record.company_name for record in parsed()}
        assert names == {
            "2NNS LLC",
            "Addman Engineering",
            "Advance Machine Works Corp",
            "Bethlehem Die Cutting",
        }

    def test_the_county_is_read_and_canonicalised(self):
        assert by_name("Addman Engineering").county == "Hamilton"
        assert by_name("2NNS LLC").county == "Marion"

    def test_a_description_wrapped_in_a_table_is_still_read(self):
        # The site marks up some descriptions as a one-cell table.
        description = by_name("2NNS LLC").industry_desc
        assert description and "induction stove" in description

    def test_the_published_website_is_captured(self):
        assert by_name("2NNS LLC").website == "http://www.newrcompany.com"

    def test_a_company_with_no_published_website_gets_none(self):
        assert by_name("Advance Machine Works Corp").website is None

    def test_the_case_study_url_is_joined_from_the_map_page(self):
        assert by_name("Addman Engineering").case_study_url == (
            "https://conexusindiana.com/case-study/addman-engineering/"
        )

    def test_a_company_without_a_case_study_gets_none(self):
        assert by_name("2NNS LLC").case_study_url is None

    def test_every_record_is_traceable_to_a_source_url(self):
        assert all(record.source_url == RECIPIENTS_URL for record in parsed())

    def test_tech_purchased_is_a_verbatim_substring_of_the_description(self):
        record = by_name("Addman Engineering")
        assert record.tech_purchased
        assert record.tech_purchased in record.industry_desc

    def test_tech_purchased_is_none_when_the_source_does_not_state_it(self):
        # No "The company will..." sentence, so nothing verbatim to extract.
        assert by_name("Advance Machine Works Corp").tech_purchased is None

    def test_fields_the_source_never_publishes_stay_none(self):
        for record in parsed():
            assert record.grant_amount is None
            assert record.grant_round is None
            assert record.grant_year is None
            assert record.city is None

    def test_missing_fields_are_reported(self):
        missing = by_name("Addman Engineering").missing_fields()
        assert {"city", "grant_amount", "grant_round", "grant_year"} <= set(missing)

    def test_an_empty_page_parses_to_nothing_rather_than_raising(self):
        assert parse_recipients("<html><body></body></html>") == []


class TestNameNormalisationForMatching:
    def test_legal_variants_collapse_to_the_same_key(self):
        assert normalize_name("Kirby Risk Corp.") == normalize_name("Kirby Risk Corporation")

    def test_punctuation_and_case_do_not_matter(self):
        assert normalize_name("S.U.S. Cast Products, Inc") == normalize_name("SUS Cast Products")

    def test_different_companies_keep_different_keys(self):
        assert normalize_name("Addman Engineering") != normalize_name("Advance Machine Works")


class TestDeduplication:
    def test_duplicates_are_collapsed_and_reported(self):
        records = parsed()
        doubled = records + [records[0]]
        kept, collapsed = deduplicate(doubled)
        assert len(kept) == len(records)
        assert len(collapsed) == 1
        assert collapsed[0][1] == [records[0].company_name, records[0].company_name]

    def test_nothing_is_reported_when_there_are_no_duplicates(self):
        kept, collapsed = deduplicate(parsed())
        assert collapsed == []
        assert len(kept) == 4

    def test_legal_form_variants_are_treated_as_one_company(self):
        records = parsed()
        variant = records[0].model_copy(update={"company_name": records[0].company_name + ", LLC"})
        kept, collapsed = deduplicate([records[0], variant])
        assert len(kept) == 1
        assert len(collapsed) == 1


class TestExclusionRules:
    def test_an_enterprise_owned_company_is_excluded_with_the_source_sentence(self):
        reason = classify_exclusion(by_name("Bethlehem Die Cutting"))
        assert reason is not None
        assert "enterprise-owned" in reason
        assert "is a division of Kyana Packaging" in reason

    def test_ordinary_companies_are_kept(self):
        for name in ("Addman Engineering", "2NNS LLC", "Advance Machine Works Corp"):
            assert classify_exclusion(by_name(name)) is None

    def test_supplying_large_customers_is_not_grounds_for_exclusion(self):
        # A company that serves Fortune 500 clients is not itself a giant. An
        # earlier draft of this rule excluded exactly such a company.
        record = by_name("Addman Engineering").model_copy(
            update={
                "industry_desc": "Bruce Fox Inc. produces recognition products for "
                "Fortune 500 companies. The privately owned company is upgrading."
            }
        )
        assert classify_exclusion(record) is None

    def test_a_stated_closure_is_grounds_for_exclusion(self):
        record = by_name("2NNS LLC").model_copy(
            update={"industry_desc": "Acme Tool ceased operations in 2025."}
        )
        reason = classify_exclusion(record)
        assert reason and "confirmed closed" in reason

    def test_a_stated_headcount_over_the_ceiling_is_grounds_for_exclusion(self):
        record = by_name("2NNS LLC").model_copy(
            update={"industry_desc": "Acme Tool employs 1200 employees across three plants."}
        )
        reason = classify_exclusion(record)
        assert reason and "headcount above 500" in reason

    def test_a_small_headcount_is_not(self):
        record = by_name("2NNS LLC").model_copy(
            update={"industry_desc": "Acme Tool employs 120 employees."}
        )
        assert classify_exclusion(record) is None


class TestEvidenceConstruction:
    def test_source_claims_are_tier_one_and_carry_the_listing_url(self):
        evidence = build_evidence(by_name("Addman Engineering"))
        for claim in evidence["source"].values():
            assert claim["tier"] == 1
            assert claim["source_url"] == RECIPIENTS_URL
            assert claim["verified"] is False

    def test_only_fields_the_source_published_become_claims(self):
        evidence = build_evidence(by_name("Advance Machine Works Corp"))
        assert "website" not in evidence["source"]
        assert "county" in evidence["source"]

    def test_absent_fields_are_explained_in_a_note(self):
        notes = absence_notes(by_name("Addman Engineering"))
        assert len(notes) == 1
        assert "grant_amount" in notes[0] and "left null rather than estimated" in notes[0]

    def test_the_case_study_flag_follows_the_url(self):
        assert source_columns(by_name("Addman Engineering"))["has_case_study"] is True
        assert source_columns(by_name("2NNS LLC"))["has_case_study"] is False
