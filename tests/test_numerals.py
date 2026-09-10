"""Tests for numeral classification — the module that inverted the default.

Three false refusals reached production before this module existed, each fixed
by adding one exclusion to a rule that read every run of three or more digits as
an asserted quantity: a standard's designation, a calendar year and a peer
count, and then a telephone area code. The fourth was not added. The default was
inverted instead.

So the first half of this file is the old exclusions, re-stated as claims about
the new rule. Every one of them must pass *without* an exclusion existing for
it — a year is a token because "dates to 2019" carries no quantity context, not
because a year pattern was carved out. That is the whole difference, and it is
what stops the fifth identifier shape from needing its own commit.

The second half is the other side of the bargain: the inversion must not let a
real figure through. A rule that classifies nothing as a quantity would pass
every test above and destroy the product.
"""

from __future__ import annotations

import pytest

from lib import numerals

AREA_CODE_SENTENCE = (
    "Do the five listed phone numbers and five email addresses route to one "
    "location or several, including the (678) number?"
)
"""Verbatim from the discovery questions of Trifecta Medical's analysis, which
was blocked on 2026-09-09 for stating an unsourced figure of 678."""


def reasons(text: str) -> dict[str, str]:
    return {n.text: n.reason for n in numerals.classify(text)}


class TestTheFourthExclusionThatWasNotAdded:
    def test_a_telephone_area_code_is_not_a_figure(self):
        assert numerals.point_quantities(AREA_CODE_SENTENCE) == []

    def test_and_it_is_reported_as_a_phone_number(self):
        # The reason is part of the contract: an operator reading a gate result
        # needs to know the gate understood what it was looking at.
        assert reasons(AREA_CODE_SENTENCE)["678"] == "phone number"

    def test_a_full_telephone_number_is_not_three_figures(self):
        text = "Reach the plant on (317) 555-0182 or the mobile 317-555-0199."
        assert numerals.point_quantities(text) == []

    def test_the_sentence_that_carried_it_still_gates_its_real_figures(self):
        # Same shape as the blocked sentence, plus a figure that IS a claim.
        text = AREA_CODE_SENTENCE[:-1] + ", against quoting costs of $30,000 a year?"
        assert numerals.point_quantities(text) == ["$30,000"]


class TestPortedExclusionStandards:
    """Ported from 75488ba — 'read a standard's designation as a name'."""

    def test_a_certification_number_is_a_name(self):
        text = "A shop of this size certified to ISO 13485 and IATF 16949."
        assert numerals.point_quantities(text) == []

    def test_every_designation_prefix_we_have_seen(self):
        for text in ("ISO 9001", "AS9100 D", "IATF 16949", "NADCAP 7108",
                     "ASTM 1234", "CSA 22.2", "SOC 2"):
            assert numerals.point_quantities(text) == [], text

    def test_a_real_figure_beside_a_standard_is_still_caught(self):
        text = "They hold ISO 9001 and their quoting desk costs $30,000 a year."
        assert numerals.point_quantities(text) == ["$30,000"]


class TestPortedExclusionYearsAndPeerCounts:
    """Ported from 5677ffb — 'stop reading a year or a peer count as a figure'."""

    def test_a_year_dates_something_rather_than_measuring_it(self):
        text = "Their newest visible content dates to 2019 and no form exists."
        assert numerals.point_quantities(text) == []

    def test_a_dollar_amount_that_looks_like_a_year_is_still_money(self):
        # The guard must not outrank a currency symbol, or the inversion would
        # have bought safety from false refusals with a hole in the real rule.
        assert numerals.point_quantities("That works out at $2,019 a year.") == ["$2,019"]

    def test_a_peer_count_is_not_a_quantity_about_the_prospect(self):
        text = "73 of 176 comparable peers are ahead of them on the front door."
        assert numerals.point_quantities(text) == []

    def test_a_peer_count_needs_no_source_phrase_to_survive_now(self):
        # It used to pass only because "comparable" named a source in words.
        # Under the inversion the count is a token on its own terms.
        text = "That puts 73 of 176 ahead of them on the front door."
        assert numerals.point_quantities(text) == []


class TestPortedExclusionOrdinalsAndScaffolding:
    """Ported from the QUANTITY docstring — 'FINDING 1' and the street number."""

    def test_a_numbered_heading_is_not_a_figure(self):
        assert numerals.point_quantities("FINDING 1. Quoting is the constraint.") == []

    def test_the_can_spam_signature_address_is_not_a_figure(self):
        text = "Nahl Technologies Inc., 6902 Challenge Ln, Indianapolis IN 46250, USA"
        assert numerals.point_quantities(text) == []

    def test_a_small_count_beside_a_unit_is_scaffolding(self):
        # Kept exactly where the old three-digit pattern drew it, so that this
        # change alters which numerals are examined and not which counts count.
        text = "They run 2 shifts and the week is 40 hours."
        assert numerals.point_quantities(text) == []

    def test_but_a_large_count_beside_a_unit_is_a_claim(self):
        assert numerals.point_quantities("They ship 1,200 orders a month.") == ["1,200"]


class TestModelAndPartDesignations:
    """The fifth shape, found by re-gating the artifact the fourth one blocked."""

    def test_a_machine_model_beside_its_noun_is_a_name(self):
        # 'press' is a unit noun, so the model number sitting in front of it was
        # read as a quantity even after the area code was fixed.
        assert numerals.point_quantities("Turn the AE2510 press data into a report") == []

    @pytest.mark.parametrize("text", [
        "a HAAS VF2 mill runs the second shift",
        "the MK4 line was rebuilt",
        "an Amada HG1003 brake",
    ])
    def test_a_designation_is_a_token_whatever_follows_it(self, text):
        assert numerals.point_quantities(text) == []

    def test_a_currency_code_glued_to_digits_is_still_money(self):
        # Otherwise the guard would eat the compact Canadian form.
        assert numerals.point_quantities("CAD1200 a month on the licence") == ["CAD1200"]


class TestIdentifierShapesNobodyHasHadToExcludeYet:
    """The point of inverting: these never needed a commit of their own."""

    @pytest.mark.parametrize("text", [
        "The plant is at 46250-1234 and the office at K1A 0B1.",
        "Ticket 4471 is still open with the vendor.",
        "Part number 88231 appears on three of the drawings.",
        "Docket 20194 was filed with the state.",
        "Cited as block4_digital_front_door.published_phones[4] in the file.",
        "Their NAICS code is 332710 on the award record.",
    ])
    def test_an_identifier_written_in_digits_is_a_token(self, text):
        assert numerals.point_quantities(text) == []


class TestTheInversionStillCatchesFigures:
    """The other half of the bargain. Classify nothing and every test above passes."""

    @pytest.mark.parametrize("text,expected", [
        ("Their quoting desk costs about $30,000 a year.", ["$30,000"]),
        ("Fully loaded engineering runs 95 dollars an hour.", ["95"]),
        ("Utilisation sits at 55% on the newer press.", ["55%"]),
        ("Roughly 30 percent of the estimator's week goes on it.", ["30"]),
        ("They employ 140 people across two plants.", ["140"]),
        ("The line produces 3,500 parts a month.", ["3,500"]),
        ("Scrap runs 260 defects per month.", ["260"]),
        ("The building is 42,000 sq ft.", ["42,000"]),
        ("An award of 71,912 was made under the programme.", ["71,912"]),
        ("The factor applied was 0.35 of the loaded rate.", ["0.35"]),
    ])
    def test_a_figure_with_quantity_context_is_a_quantity(self, text, expected):
        assert numerals.point_quantities(text) == expected

    def test_a_range_is_not_a_point(self):
        text = "If quoting runs somewhere between $25,000 and $40,000 a year."
        assert numerals.point_quantities(text) == []

    def test_a_percentage_range_is_not_two_points(self):
        text = "If that capital returns somewhere between 10% and 20% a year."
        assert numerals.point_quantities(text) == []

    def test_both_ends_of_a_range_are_still_quantities(self):
        # Being in a range is about whether it is a POINT, not about whether it
        # measures anything. Losing that distinction would break arithmetic.
        assert len(numerals.quantities("between $25,000 and $40,000")) == 2


class TestCalculationMembership:
    def test_an_operand_is_a_quantity_however_small(self):
        found = numerals.classify("2 x 0.20-0.40 x 40 x $80-$120 = $51,200-$153,600")
        assert found[0].kind == numerals.QUANTITY
        assert found[0].reason == "calculation"

    def test_a_declared_map_input_is_a_quantity_wherever_it_appears(self):
        text = "The estimator handles 40 of them."
        assert numerals.point_quantities(text) == []
        assert numerals.point_quantities(text, declared=[40]) == ["40"]

    def test_a_result_that_is_not_a_range_is_still_a_point(self):
        # This is what a decimal buys: 24.0 sits under the scaffolding floor and
        # would have read as a range if the unit alone had decided it.
        assert numerals.point_quantities("24.0 engineer-weeks") == ["24.0"]


class TestTheReasonIsAlwaysGiven:
    def test_every_numeral_carries_why(self):
        text = "ISO 13485 shop at 46250 spending $30,000 a year since 2019."
        assert all(n.reason for n in numerals.classify(text))

    def test_a_token_says_which_guard_or_that_there_was_no_context(self):
        assert reasons("Docket 20194 was filed.")["20194"] == "no quantity context"
        assert reasons("dates to 2019")["2019"] == "calendar year"
