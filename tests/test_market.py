"""Tests for per-family market context and named-competitor observation.

The market module makes one promise: nothing is stored that is not pinned to a
verbatim quote from a page we fetched. That promise is mechanical rather than
aspirational, so most of what follows is about the check that enforces it and
about what happens to the statements that fail it.
"""

from __future__ import annotations

from lib import market
from lib.claims import Tier
from lib.evidence import BLOCK1_WHAT_THEY_MAKE, BLOCK7_PEOPLE
from tools.harvester.nodes.competitor_scan import (
    named_competitors,
    observation_words,
    observe,
)

PAGE = (
    "Total employment in fabricated metal product manufacturing has been edging "
    "upward over the latest reported months, and average weekly hours were "
    "little changed from a year earlier."
)
SOURCES = (
    market.Source("https://www.bls.gov/iag/tgs/iag332.htm", int(Tier.T1),
                  "the Bureau of Labor Statistics industry page"),
    market.Source("https://www.federalreserve.gov/releases/g17/current/default.htm",
                  int(Tier.T1), "the Federal Reserve's industrial production release"),
)


def entry(**overrides):
    row = {
        "dimension": "labour",
        "statement": "Employment in this segment has been edging up.",
        "quote": "employment in fabricated metal product manufacturing has been edging upward",
        "source_url": SOURCES[0].url,
    }
    row.update(overrides)
    return row


class TestTheQuoteCheck:
    def test_a_quote_that_is_in_the_page_passes(self):
        assert market.quote_is_in(
            "employment in fabricated metal product manufacturing has been edging",
            PAGE) is True

    def test_whitespace_and_case_do_not_defeat_it(self):
        assert market.quote_is_in(
            "  EMPLOYMENT in Fabricated Metal   Product Manufacturing has been  ",
            PAGE) is True

    def test_a_quote_that_is_not_there_fails(self):
        assert market.quote_is_in(
            "demand for machined parts is expected to grow strongly", PAGE) is False

    def test_a_short_quote_is_refused_outright(self):
        # Three words match almost any page, which would turn the check into a
        # formality rather than a guarantee.
        assert market.quote_is_in("has been edging", PAGE) is False


class TestVerification:
    def test_a_sound_entry_survives_and_carries_its_source(self):
        kept, dropped = market.verify([entry()], {SOURCES[0].url: PAGE}, SOURCES)
        assert dropped == []
        assert kept[0].dimension == "labour"
        assert kept[0].tier == int(Tier.T1)
        assert "Bureau of Labor Statistics" in kept[0].what

    def test_an_invented_quote_is_discarded_and_the_discard_is_recorded(self):
        # A market section that quietly shrinks teaches nobody anything, and on
        # a first run the discards are the most informative half.
        kept, dropped = market.verify(
            [entry(quote="demand is expected to grow strongly through next year")],
            {SOURCES[0].url: PAGE}, SOURCES)
        assert kept == []
        assert dropped and "not in" in dropped[0]

    def test_a_source_we_did_not_read_is_refused(self):
        kept, dropped = market.verify(
            [entry(source_url="https://example.invalid/report")],
            {SOURCES[0].url: PAGE}, SOURCES)
        assert kept == [] and "was not read" in dropped[0]

    def test_a_fourth_question_is_refused(self):
        kept, dropped = market.verify(
            [entry(dimension="market_size")], {SOURCES[0].url: PAGE}, SOURCES)
        assert kept == [] and "not one of the three questions" in dropped[0]

    def test_only_one_answer_per_question_is_kept(self):
        kept, dropped = market.verify(
            [entry(), entry(statement="A second reading of the same thing.")],
            {SOURCES[0].url: PAGE}, SOURCES)
        assert len(kept) == 1 and "one is enough" in dropped[0]

    def test_a_statement_becomes_a_claim_like_any_other_fact(self):
        kept, _ = market.verify([entry()], {SOURCES[0].url: PAGE}, SOURCES)
        claim = kept[0].as_claim()
        assert claim["tier"] == int(Tier.T1)
        assert claim["source_url"] == SOURCES[0].url
        assert claim["quote"]


class TestWhenThereIsNothing:
    def test_a_family_with_nothing_records_the_absence(self):
        record = market.summarise("lab_services", [], ["all sources unread"], SOURCES)
        assert record["note"] == market.NO_CONTEXT
        assert record["statements"] == []

    def test_the_prompt_block_forbids_substituting_general_knowledge(self):
        block = market.as_prompt_block(None)
        assert "must NOT substitute general knowledge" in block

    def test_the_prompt_block_hands_over_conclusions_not_a_licence(self):
        record = market.summarise(
            "metal_fabrication",
            market.verify([entry()], {SOURCES[0].url: PAGE}, SOURCES)[0], [], SOURCES)
        block = market.as_prompt_block(record)
        assert "ONLY market statements" in block
        assert "Do not add a trend" in block


class TestSources:
    def test_a_government_statistics_page_is_a_government_record(self):
        # CLAUDE.md rule 6: government records are T1. Filing one as T2 because
        # it arrived over the web would understate what we may say about it.
        for source in market.sources_for("metal_fabrication"):
            if "bls.gov" in source.url or "federalreserve.gov" in source.url:
                assert source.tier == int(Tier.T1), source.url

    def test_no_source_is_ever_an_aggregator_estimate(self):
        for family in market.FAMILY_SOURCES:
            for source in market.sources_for(family):
                assert source.tier in (int(Tier.T1), int(Tier.T2)), source.url

    def test_a_company_we_could_not_place_has_no_segment(self):
        assert market.sources_for("unclassified") == ()

    def test_every_source_says_what_it_is_for_attribution(self):
        for family in market.FAMILY_SOURCES:
            for source in market.sources_for(family):
                assert len(source.what.split()) >= 4, source.url


# --------------------------------------------------------------- competitors

def prospect(**overrides):
    row = {
        "id": "p1", "company_name": "Acme Tool & Die",
        "website": "https://acmetool.test/",
        "industry_desc": "Acme Tool & Die is a precision machining supplier.",
        "evidence_file": {
            BLOCK1_WHAT_THEY_MAKE: {},
            BLOCK7_PEOPLE: {"leadership_quotes": []},
        },
    }
    row.update(overrides)
    return row


def with_quote(text):
    row = prospect()
    row["evidence_file"][BLOCK7_PEOPLE]["leadership_quotes"] = [{
        "value": text, "tier": 2, "source_url": "https://press.test/story",
        "date_checked": "2026-09-08", "verified": False, "verified_at": None,
    }]
    return row


class TestOnlyCompetitorsTheyName:
    def test_a_competitor_named_in_a_quote_is_found_with_its_sentence(self):
        row = with_quote(
            "We compete with Bristol Tooling on the shorter runs. "
            "That has been true for years.")
        found = named_competitors(row)
        assert found and found[0][0] == "Bristol Tooling"
        assert "compete with Bristol Tooling" in found[0][1]

    def test_the_sentence_travels_with_the_name(self):
        # "A competitor" is a claim. The only thing that makes it checkable is
        # being able to read who said it and in what breath.
        row = with_quote("Our competitors, Midwest Stamping, price differently.")
        assert "Midwest Stamping" in named_competitors(row)[0][1]

    def test_a_company_that_names_nobody_yields_nothing(self):
        assert named_competitors(prospect()) == []

    def test_a_pronoun_after_the_cue_is_not_a_company(self):
        assert named_competitors(with_quote("Unlike our competitors, we ship fast.")) == []

    def test_the_company_does_not_compete_with_itself(self):
        row = with_quote("We compete with Acme Tool & Die on nothing at all.")
        assert named_competitors(row) == []

    def test_a_list_is_cut_at_its_first_separator(self):
        row = with_quote("We compete with Bristol Tooling and Midwest Stamping daily.")
        assert named_competitors(row)[0][0] == "Bristol Tooling"


class TestWhatWeObserveOnACompetitor:
    HTML = ('<html><head><meta name="viewport" content="width=device-width">'
            '</head><body><p>ISO 9001 certified. Our robotic welding cells run '
            'lights-out.</p><form><textarea name="message"></textarea></form>'
            '</body></html>')

    def test_the_observations_are_the_same_ones_we_make_on_a_prospect(self):
        found = observe(self.HTML, "https://bristol.test/")
        assert found["front_door"]["https"] is True
        assert found["front_door"]["mobile_viewport"] is True
        assert found["front_door"]["contact_form"] is True
        assert "ISO 9001" in " ".join(found["certifications_published"])
        assert "robotic" in found["automation_language"]

    def test_nothing_is_estimated(self):
        # No revenue, no headcount, no share. We have no source for any of them.
        found = observe(self.HTML, "https://bristol.test/")
        assert set(found) == {"front_door", "certifications_published",
                              "automation_language"}

    def test_the_observation_reads_as_a_sentence_not_a_dict(self):
        words = observation_words(observe(self.HTML, "https://bristol.test/"))
        assert "site has" in words and "publishes" in words

    def test_a_bare_site_says_so_rather_than_going_quiet(self):
        words = observation_words(observe("<html><body>Hi</body></html>", "http://x.test/"))
        assert "none of the basics" in words
        assert "no certification we could see" in words


class TestCanadianSources:
    """A Canadian company's segment is answered by a Canadian government record.

    The failure this prevents is quiet and plausible: a Bureau of Labor
    Statistics page about Indiana metal fabrication reads perfectly well
    underneath an Ontario machine shop, and every word of it is about a
    different country.
    """

    def test_canada_reads_canadian_records(self):
        for source in market.sources_for("metal_fabrication", "canada_gc"):
            assert "canada.ca" in source.url or "statcan.gc.ca" in source.url

    def test_indiana_is_unchanged(self):
        assert market.sources_for("metal_fabrication") == market.FAMILY_SOURCES[
            "metal_fabrication"]
        assert market.sources_for("metal_fabrication", "conexus_iedc") == (
            market.FAMILY_SOURCES["metal_fabrication"])

    def test_every_canadian_family_has_a_source_and_a_tier(self):
        for family, sources in market.CANADA_FAMILY_SOURCES.items():
            assert sources, family
            for source in sources:
                assert source.tier in (int(Tier.T1), int(Tier.T2)), family
                assert source.what.strip(), family

    def test_both_tables_cover_the_same_families(self):
        # A family one country answers and the other does not would give an
        # Ontario company a recorded absence where an Indiana one gets a
        # paragraph, for no reason to do with either company.
        assert set(market.CANADA_FAMILY_SOURCES) == set(market.FAMILY_SOURCES)

    def test_an_uncovered_family_still_returns_nothing(self):
        assert market.sources_for("unclassified", "canada_gc") == ()


class TestContextKey:
    def test_indiana_keeps_the_bare_family_name(self):
        # The eleven rows already gathered are stored under it.
        assert market.context_key("metal_fabrication") == "metal_fabrication"
        assert market.context_key("metal_fabrication", "conexus_iedc") == (
            "metal_fabrication")

    def test_another_source_is_namespaced(self):
        assert market.context_key("metal_fabrication", "canada_gc") == (
            "canada_gc:metal_fabrication")

    def test_two_markets_never_share_a_cache_row(self):
        assert market.context_key("food_beverage") != market.context_key(
            "food_beverage", "canada_gc")
