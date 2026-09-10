"""The anchoring order, and the distinction the whole of it turns on.

The case that matters most is the third one: an award amount may size a model
about capital and may never size a model about people. A test that only checked
"the award anchor produces a number" would pass on the fabrication this order
exists to refuse.
"""

from __future__ import annotations

import pytest

from lib import anchors, casefile, finmodel, offermodels

QUOTING = offermodels.BY_PATTERN["quoting_velocity"]


def claim(value, path_tier=1, url="https://acme.test/about"):
    return {"value": value, "tier": path_tier, "source_url": url,
            "date_checked": "2026-09-10", "verified": False, "verified_at": None}


def prospect(**kw):
    base = {"company_name": "Acme Fabrication", "evidence_file": {}}
    base.update(kw)
    return base


class TestStatedVolumeWinsOutright:
    def test_a_monthly_rate_they_published(self):
        p = prospect(evidence_file={"block1_what_they_make": {
            "self_description_raw": claim("We turn around about 200 quotes a month.")}})
        anchor = anchors.anchor_for(p, QUOTING)
        assert anchor.kind == anchors.STATED
        assert anchor.volume == (200.0, 200.0)
        assert anchor.claim_path == "block1_what_they_make.self_description_raw"

    def test_a_weekly_rate_is_converted_and_says_so(self):
        p = prospect(evidence_file={"block1_what_they_make": {
            "x": claim("We send 50 RFQs per week.")}})
        anchor = anchors.anchor_for(p, QUOTING)
        assert anchor.kind == anchors.STATED
        assert anchor.volume[0] == pytest.approx(216.5)
        assert "on our conversion" in anchor.words

    def test_a_range_stays_a_range(self):
        p = prospect(evidence_file={"block1_what_they_make": {
            "x": claim("Between 40 and 60 quotes a month leave the desk.")}})
        anchor = anchors.anchor_for(p, QUOTING)
        assert anchor.volume == (40.0, 60.0)

    def test_it_beats_a_headcount_that_is_also_on_file(self):
        p = prospect(
            employee_estimate="171", employee_source="[T1] their about page",
            evidence_file={"block1_what_they_make": {
                "x": claim("About 200 quotes a month.")}})
        assert anchors.anchor_for(p, QUOTING).kind == anchors.STATED

    def test_a_volume_of_something_else_is_not_this_volume(self):
        # Freight invoices are a real monthly count and they are not quotes.
        p = prospect(evidence_file={"block1_what_they_make": {
            "x": claim("We check 300 freight invoices a month.")}})
        assert anchors.anchor_for(p, QUOTING).kind != anchors.STATED

    def test_money_a_month_is_not_a_volume(self):
        p = prospect(evidence_file={"block1_what_they_make": {
            "x": claim("Revenue of $40,000 a month.")}})
        assert anchors.anchor_for(p, QUOTING).kind != anchors.STATED

    def test_a_killed_claim_anchors_nothing(self):
        killed = claim("About 200 quotes a month.")
        killed["killed"] = True
        killed["killed_reason"] = "the operator could not confirm it"
        p = prospect(evidence_file={"block1_what_they_make": {"x": killed}})
        assert anchors.anchor_for(p, QUOTING).kind != anchors.STATED


class TestHeadcountIsSecond:
    def test_it_scales_the_band_and_names_the_scaling(self):
        p = prospect(employee_estimate="171", employee_source="[T1] their about page")
        anchor = anchors.anchor_for(p, QUOTING)
        assert anchor.kind == anchors.HEADCOUNT
        assert anchor.headcount == 171
        assert anchor.model_type == anchors.LABOUR_HOURS
        assert anchor.volume[1] > QUOTING.volume[1]
        assert "171" in anchor.words

    def test_it_beats_an_award_that_is_also_on_file(self):
        p = prospect(employee_estimate="60", employee_source="[T1] x",
                     grant_amount=250_000.0)
        assert anchors.anchor_for(p, QUOTING).kind == anchors.HEADCOUNT


class TestAwardIsThirdAndIsADifferentModel:
    def test_it_routes_to_the_capital_model(self):
        p = prospect(grant_amount=250_000.0)
        anchor = anchors.anchor_for(p, QUOTING)
        assert anchor.kind == anchors.AWARD
        assert anchor.model_type == anchors.CAPITAL_UTILISATION
        assert anchor.capital == 250_000.0

    def test_the_capital_model_contains_no_wage_and_no_volume(self):
        """The test of whether the distinction is real rather than a label.

        A grant size may say how much capital was committed. It may never say
        how many people work somewhere, and a model that reached a headcount
        through an award would have a wage in it.
        """
        p = prospect(grant_amount=250_000.0)
        anchor = anchors.anchor_for(p, QUOTING)
        spec = offermodels.build_spec(
            offermodels.CAPITAL_PATTERN, p, "diagnostic", anchor=anchor)
        names = set(spec.inputs) | set(spec.formulas)
        assert not any("wage" in n for n in names if n != "wage_escalation")
        assert not any("volume" in n or "minutes" in n or "hours" in n for n in names)
        assert "capital_deployed" in spec.inputs

    def test_the_award_is_sourced_to_their_own_record(self):
        p = prospect(
            grant_amount=250_000.0,
            evidence_file={"block2_grant_funded": {
                "grant_amount": claim("$250,000", 1,
                                      "https://search.open.canada.ca/grants/record/x")}})
        anchor = anchors.anchor_for(p, QUOTING)
        spec = offermodels.build_spec(
            offermodels.CAPITAL_PATTERN, p, "diagnostic", anchor=anchor)
        provenance = spec.inputs["capital_deployed"].provenance
        assert provenance.kind == finmodel.CLAIM
        assert "grant_amount" in provenance.ref

    def test_idle_capital_is_never_negative(self):
        """Interval subtraction of two correlated quantities produced minus $1,694."""
        p = prospect(grant_amount=250_000.0)
        anchor = anchors.anchor_for(p, QUOTING)
        report = offermodels.run_for(
            offermodels.CAPITAL_PATTERN, p, "diagnostic", anchor=anchor)
        for scenario in finmodel.SCENARIOS:
            idle = report.scenarios[scenario].value("idle_capital_a_year")
            assert idle.low >= 0, scenario

    def test_an_award_too_small_to_say_anything_but_no_is_no_anchor(self):
        floor = offermodels.award_floor()
        p = prospect(grant_amount=floor / 2)
        anchor = anchors.anchor_for(p, QUOTING)
        assert anchor.kind == anchors.NONE
        assert "below the" in anchor.detail

    def test_the_floor_is_derived_from_the_ladder_not_typed(self):
        from lib import pricing

        cheapest = min(e.band[0] for e in pricing.LADDER)
        assert offermodels.award_floor() == pytest.approx(
            (cheapest / offermodels.HORIZON_YEARS)
            * offermodels.ASSET_LIFE[1]
            / offermodels.thirds(*offermodels.UTILISATION_GAIN)[
                finmodel.CONSERVATIVE].low)


class TestNoAnchorAtAll:
    def test_nothing_on_file_means_no_anchor(self):
        anchor = anchors.anchor_for(prospect(), QUOTING)
        assert anchor.kind == anchors.NONE
        assert not anchor.headline_available

    def test_the_headline_asks_for_the_number(self):
        anchor = anchors.anchor_for(prospect(), QUOTING)
        assert anchors.headline_words(anchor, "$16,653-$67,445/yr") == (
            anchors.PENDING_HEADLINE)

    def test_an_anchored_document_keeps_its_figure(self):
        anchor = anchors.anchor_for(
            prospect(employee_estimate="60", employee_source="[T1] x"), QUOTING)
        assert anchors.headline_words(anchor, "$40,000/yr") == "$40,000/yr"


class TestTheOrderIsRecorded:
    def test_the_record_carries_everything_a_reader_would_ask(self):
        p = prospect(employee_estimate="60", employee_source="[T1] x")
        record = anchors.anchor_for(p, QUOTING).as_record()
        assert record["kind"] == anchors.HEADCOUNT
        assert record["headcount"] == 60
        assert record["detail"]

    def test_every_kind_has_a_writing_rule(self):
        assert set(casefile.ANCHOR_RULES) == set(anchors.ORDER)


class TestOnlyTheLeadApproachBecomesTheCapitalModel:
    def test_one_capital_model_not_three(self):
        award = anchors.Anchor(kind=anchors.AWARD,
                               model_type=anchors.CAPITAL_UTILISATION,
                               capital=250_000.0)
        chosen = casefile.model_patterns(
            ["quoting_velocity", "machine_data_analysis"], award, 3)
        assert chosen[0] == offermodels.CAPITAL_PATTERN
        assert chosen.count(offermodels.CAPITAL_PATTERN) == 1

    def test_the_second_approach_does_not_repeat_the_first_work_unit(self):
        award = anchors.Anchor(kind=anchors.AWARD,
                               model_type=anchors.CAPITAL_UTILISATION,
                               capital=250_000.0)
        chosen = casefile.model_patterns(
            ["quoting_velocity", "machine_data_analysis"], award, 3)
        assert len(set(chosen)) == len(chosen)

    def test_an_unanchored_company_keeps_the_labour_models(self):
        none = anchors.Anchor(kind=anchors.NONE, model_type=anchors.LABOUR_HOURS)
        chosen = casefile.model_patterns(["quoting_velocity"], none, 3)
        assert offermodels.CAPITAL_PATTERN not in chosen
