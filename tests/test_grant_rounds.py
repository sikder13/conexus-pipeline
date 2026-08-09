"""Tests for the bulk grant-round adapter.

Parsing runs against a slice of a real CICP announcement, so these fail if the
announcement format changes — which is when someone needs to know.

The matching tests carry the most weight. A grant amount on the wrong company is
worse than a null, so every case where the adapter is *not* sure has to end in
the review pile rather than in the database.
"""

from __future__ import annotations

from datetime import date

import pytest

from lib.claims import Tier
from lib.sources.grant_rounds import (
    GrantAward,
    clean_company_name,
    dedupe,
    match_to_prospects,
    parse_announcement,
    parse_round_id,
    tier_for,
)
from tests.conftest import fixture
from tools.grant_rounds.main import build_block8, case_study_amount

ANNOUNCEMENT = fixture("grant_round_announcement.html")
SOURCE = "https://www.cicpindiana.com/second-round/"


def award(name="Acme Tool", amount=1000.0, round_id="2", tier=1, day=1):
    return GrantAward(
        company_name=name, county="Delaware", amount=amount, amount_text=f"${amount:,.0f}",
        round_id=round_id, announced=date(2021, 1, day), source_url=SOURCE, tier=tier,
    )


class TestParsing:
    def test_it_reads_the_awards(self):
        awards = parse_announcement(ANNOUNCEMENT, SOURCE, 1)
        assert len(awards) == 3
        names = {a.company_name for a in awards}
        assert "American Quality Molds" in names

    def test_amounts_and_counties_are_verbatim(self):
        awards = parse_announcement(ANNOUNCEMENT, SOURCE, 1)
        acme = next(a for a in awards if a.company_name == "American Quality Molds")
        assert acme.amount == 53500.0
        assert acme.amount_text == "$53,500"
        assert acme.county == "Wayne"

    def test_the_round_and_date_come_from_the_page(self):
        awards = parse_announcement(ANNOUNCEMENT, SOURCE, 1)
        assert awards[0].round_id == "2"
        assert awards[0].announced == date(2020, 10, 30)
        assert awards[0].year == 2020

    def test_a_page_with_no_date_yields_nothing(self):
        # An award with no date cannot be placed in a round, and a round-less
        # award is not worth the risk of mis-filing.
        assert parse_announcement("<html><body>Acme (Foo County; $1 grant award)</body></html>",
                                  SOURCE, 1) == []

    def test_an_unrelated_page_yields_nothing(self):
        assert parse_announcement("<html><body>No awards here.</body></html>", SOURCE, 1) == []

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("COVID-19 pandemic. OMR Automotive", "OMR Automotive"),
            ("Akron. Sugar Creek Bottling Company", "Sugar Creek Bottling Company"),
            ("MAC System, in early 2021. Semiconductor Test Supply LLC",
             "Semiconductor Test Supply LLC"),
            ("Acme Tool Co.", "Acme Tool Co."),
        ],
    )
    def test_sentence_bleed_is_trimmed_from_names(self, raw, expected):
        # The pattern reads backwards from the parenthetical and company names
        # legitimately contain full stops, so it can run past a sentence break.
        assert clean_company_name(raw) == expected

    def test_a_round_that_names_itself_wins_over_the_date(self):
        assert parse_round_id("the fourth round of grants", date(2021, 9, 9)) == "4"

    def test_the_launch_announcement_is_round_one(self):
        # It predates the "Nth round" habit; the next one calls itself the second.
        assert parse_round_id("award 20 grants", date(2020, 8, 20)) == "1"

    def test_an_unnamed_round_falls_back_to_its_date(self):
        assert parse_round_id("some grants", date(2099, 1, 1)) == "2099-01-01"


class TestTiers:
    def test_the_administrators_announcement_is_tier_one(self):
        assert tier_for(award(tier=1)) is Tier.T1

    def test_press_coverage_is_tier_two(self):
        assert tier_for(award(tier=2)) is Tier.T2


class TestDedupe:
    def test_the_same_award_from_two_publishers_collapses(self):
        both = [award(tier=2), award(tier=1)]
        assert len(dedupe(both)) == 1

    def test_the_tier_one_source_survives(self):
        assert dedupe([award(tier=2), award(tier=1)])[0].tier == 1

    def test_two_rounds_for_one_company_are_both_kept(self):
        kept = dedupe([award(round_id="2", amount=1.0), award(round_id="6", amount=2.0)])
        assert len(kept) == 2


class TestConservativeMatching:
    def test_an_exact_normalised_name_matches(self):
        prospects = [{"id": "p1", "company_name": "Acme Tool, Inc."}]
        matches, unresolved = match_to_prospects([award(name="Acme Tool Inc")], prospects)
        assert len(matches) == 1
        assert matches[0].prospect_id == "p1"
        assert unresolved == []

    def test_an_unknown_company_is_reported_not_written(self):
        matches, unresolved = match_to_prospects([award(name="Nobody Ltd")], [])
        assert matches == []
        assert len(unresolved) == 1
        assert "no prospect" in unresolved[0][1]

    def test_an_ambiguous_name_is_reported_not_guessed(self):
        # Two prospects normalise to the same key. Picking one would be a
        # coin flip that ends with a grant on the wrong company.
        prospects = [
            {"id": "p1", "company_name": "Acme Tool Inc"},
            {"id": "p2", "company_name": "Acme Tool, LLC"},
        ]
        matches, unresolved = match_to_prospects([award(name="Acme Tool")], prospects)
        assert matches == []
        assert "ambiguous" in unresolved[0][1]

    def test_a_partial_name_does_not_match(self):
        prospects = [{"id": "p1", "company_name": "Acme Tool and Die Company"}]
        matches, unresolved = match_to_prospects([award(name="Acme")], prospects)
        assert matches == []
        assert len(unresolved) == 1

    def test_several_awards_group_under_one_prospect(self):
        prospects = [{"id": "p1", "company_name": "Acme Tool"}]
        awards = [award(round_id="2", amount=1.0), award(round_id="6", amount=2.0)]
        matches, _ = match_to_prospects(awards, prospects)
        assert len(matches) == 1
        assert len(matches[0].awards) == 2


class TestBlockEight:
    def _match(self, *awards):
        prospects = [{"id": "p1", "company_name": awards[0].company_name}]
        matches, _ = match_to_prospects(list(awards), prospects)
        return matches[0]

    def test_every_award_is_stored(self):
        claims, _notes = build_block8(
            self._match(award(round_id="2", amount=98000.0), award(round_id="5", amount=102000.0)),
            {},
        )
        assert len(claims["grant_awards"]) == 2
        assert claims["grant_award_count"]["value"] == 2

    def test_a_multi_award_total_is_labelled_as_our_sum(self):
        claims, _ = build_block8(
            self._match(award(round_id="2", amount=98000.0), award(round_id="5", amount=102000.0)),
            {},
        )
        total = claims["grant_awards_total"]
        assert total["tier"] == 4, "a sum we computed is our inference, not their figure"
        assert "not a single award" in total["value"]
        assert "$200,000" in total["value"]

    def test_a_single_award_gets_no_total(self):
        claims, _ = build_block8(self._match(award()), {})
        assert "grant_awards_total" not in claims

    def test_a_disagreement_records_both_figures_and_resolves_neither(self):
        evidence = {
            "block2_grant_funded": {
                "grant_amount": {
                    "value": "$116,837", "tier": 2,
                    "source_url": "https://conexusindiana.com/case-study/x/",
                    "date_checked": "2026-08-09",
                }
            }
        }
        claims, notes = build_block8(self._match(award(amount=90000.0)), evidence)
        finding = claims["grant_amount_disagreement"]
        assert "$116,837" in finding["value"]
        assert "$90,000" in finding["value"]
        assert "human decision" in finding["value"]
        assert finding["tier"] == 4
        assert any(n.startswith("DISAGREEMENT") for n in notes)

    def test_agreeing_sources_raise_no_finding(self):
        evidence = {
            "block2_grant_funded": {
                "grant_amount": {
                    "value": "$90,000", "tier": 2,
                    "source_url": "https://conexusindiana.com/case-study/x/",
                    "date_checked": "2026-08-09",
                }
            }
        }
        claims, notes = build_block8(self._match(award(amount=90000.0)), evidence)
        assert "grant_amount_disagreement" not in claims
        assert notes == []

    def test_the_case_study_amount_parses_out_of_its_claim(self):
        evidence = {
            "block2_grant_funded": {
                "grant_amount": {
                    "value": "$116,837", "tier": 2, "source_url": "https://x.test",
                    "date_checked": "2026-08-09",
                }
            }
        }
        assert case_study_amount(evidence) == 116837.0
        assert case_study_amount({}) is None
