"""Tests for the engagement ladder.

The ladder exists so a price cannot be invented one company at a time. These
tests hold it to that: bands are ranges, payback is worst case against best
case, and while the numbers are unconfirmed every document that quotes them has
to say so.
"""

from __future__ import annotations

import pytest

from lib import pricing


class TestTheLadderIsWellFormed:
    def test_every_band_is_a_range_and_never_a_point(self):
        for engagement in pricing.LADDER:
            low, high = engagement.band
            assert low < high, f"{engagement.key} quotes a single price"
            assert engagement.weeks[0] <= engagement.weeks[1]

    def test_every_shape_says_when_it_must_not_be_quoted(self):
        # A shape with no refusal condition is a shape that fits everything,
        # which is how three approaches collapse into one.
        for engagement in pricing.LADDER:
            assert engagement.refuses_when, f"{engagement.key} refuses nothing"

    def test_the_keys_are_unique(self):
        keys = [e.key for e in pricing.LADDER]
        assert len(keys) == len(set(keys))

    def test_band_words_state_both_axes_as_ranges(self):
        words = pricing.band_words("scoped_build")
        assert "2-4 weeks" in words and "$8,000-$20,000" in words


class TestPayback:
    def test_it_is_worst_case_against_best_case(self):
        # Midpoint against midpoint would give one flattering number, which is a
        # point estimate with a division sign in front of it.
        fastest, slowest = pricing.payback_months((8_000, 20_000), (24_000, 60_000))
        assert fastest == pytest.approx(8_000 / 60_000 * 12, abs=0.05)
        assert slowest == pytest.approx(20_000 / 24_000 * 12, abs=0.05)
        assert fastest < slowest

    def test_a_return_that_cannot_be_costed_is_refused(self):
        with pytest.raises(ValueError, match="positive at both ends"):
            pricing.payback_months((8_000, 20_000), (0, 10_000))

    def test_a_bigger_return_pays_back_sooner(self):
        modest = pricing.payback_months((8_000, 20_000), (10_000, 20_000))
        strong = pricing.payback_months((8_000, 20_000), (50_000, 90_000))
        assert strong[1] < modest[1]


class TestDistinctness:
    def test_a_shape_is_not_distinct_from_itself(self):
        assert pricing.distinct("scoped_build", "scoped_build") is False

    def test_a_diagnostic_and_a_build_are_different_engagements(self):
        assert pricing.distinct("diagnostic", "scoped_build") is True

    def test_the_relation_reads_the_same_both_ways(self):
        for first in pricing.BY_KEY:
            for second in pricing.BY_KEY:
                assert pricing.distinct(first, second) == pricing.distinct(second, first)


class TestUnconfirmedBandsAnnounceThemselves:
    def test_the_prompt_block_carries_the_caveat_while_any_rung_is_unconfirmed(self):
        block = pricing.as_prompt_block()
        if pricing.unconfirmed_among([e.key for e in pricing.LADDER]):
            assert pricing.CAVEAT in block
        assert "never as a single number" in block

    def test_the_caveat_is_silent_when_only_confirmed_rungs_are_quoted(self):
        # A caveat attached to a settled price teaches a reader to discount
        # every price, including the ones that are settled.
        assert pricing.caveat_for(
            ["starter_automation", "diagnostic", "scoped_build"]) == ""

    def test_and_speaks_up_when_an_unconfirmed_one_is(self):
        told = pricing.caveat_for(["starter_automation", "pilot_then_build"])
        assert pricing.CAVEAT in told and "pilot_then_build" in told

    def test_the_four_signed_off_bands_are_exactly_these(self):
        confirmed = {e.key: e.band for e in pricing.LADDER if e.confirmed}
        assert confirmed == {
            "starter_automation": (600, 2_500),
            "diagnostic": (2_500, 8_000),
            "scoped_build": (8_000, 20_000),
            "premium_scope": (15_000, 30_000),
            "care_plan": (500, 1_500),
        }

    def test_a_quote_names_its_currency_and_its_framing(self):
        assert pricing.band_words("premium_scope", "canada_gc") == (
            "4-8 weeks, $15,000-$30,000 CAD — founding-client rate, locked 12 months")
        assert "USD" in pricing.band_words("premium_scope", "conexus_iedc")

    def test_the_numerals_do_not_move_between_currencies(self):
        # A converted figure would imply a precision the bands do not have, and
        # a Canadian prospect quoted an odd number would ask what it came from.
        for key in pricing.BY_KEY:
            usd = pricing.band_words(key, "conexus_iedc").replace("USD", "")
            cad = pricing.band_words(key, "canada_gc").replace("CAD", "")
            assert usd == cad

    def test_every_routed_rung_is_confirmed(self):
        # An ordinary analysis should print no caveat at all.
        from lib import casefile
        for band in ("core", "growth"):
            tier = pricing.tier_for(band)
            assert pricing.caveat_for(casefile.engagements_for(tier)) == ""

    def test_the_prompt_block_names_every_shape_and_its_refusal(self):
        block = pricing.as_prompt_block()
        for engagement in pricing.LADDER:
            assert engagement.key in block
            assert "DO NOT QUOTE WHEN" in block

    def test_the_ladder_is_readable_as_plain_data(self):
        rows = pricing.as_dicts()
        assert len(rows) == len(pricing.LADDER)
        assert all("band" in row and "weeks" in row for row in rows)
