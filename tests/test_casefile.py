"""Tests for the assembled case file and the figure-traceability gate.

The gate is the whole point of the rewrite: the generator no longer does
arithmetic, so a number in the finished prose that no evaluated model, claim or
benchmark produced is not a rounding disagreement, it is an invention. Most of
this file is about that check being neither too strict to allow good prose nor
too loose to catch one.
"""

from __future__ import annotations

import pytest

from lib import benchmarks, casefile, finmodel, offermodels, pricing, rivals


def prospect(**overrides):
    base = {
        "id": "p1",
        "company_name": "Hoosier Widget Works",
        "size_band": "core",
        "source_adapter": "conexus_iedc",
        "evidence_file": {
            "block1_what_they_make": {
                "self_description": {
                    "value": "We quote and fabricate sheet metal assemblies.",
                    "tier": 1, "source_url": "https://hww.example",
                    "date_checked": "2026-09-09",
                },
            },
        },
    }
    base.update(overrides)
    return base


def a_report():
    return offermodels.run_for("quoting_velocity", prospect(), "scoped_build")


class TestTraceability:
    def test_an_exact_computed_figure_is_traceable(self):
        assert casefile.traceable(45_419.0, {45_419.0})

    def test_a_figure_rounded_for_readability_is_traceable(self):
        # "about $45,000" for a computed $45,419 is the right way to write it.
        # Quoting the raw figure is a false precision the input never had.
        assert casefile.traceable(45_000.0, {45_419.0})

    def test_a_share_written_as_a_percentage_is_traceable(self):
        assert casefile.traceable(35.0, {0.35})

    def test_a_percentage_written_as_a_share_is_traceable(self):
        assert casefile.traceable(0.35, {35.0})

    def test_rounding_to_one_significant_digit_is_too_much(self):
        # $45,419 written as "$50,000" is a ten per cent overstatement, and the
        # reader repeats it as though we measured it.
        assert casefile.traceable(50_000.0, {45_419.0}) is False
        assert casefile.traceable(50_000.0, {49_800.0})

    def test_a_number_nothing_produced_is_not_traceable(self):
        assert casefile.traceable(87_311.0, {45_419.0, 1_320.0}) is False

    def test_a_plausible_neighbour_is_still_not_traceable(self):
        # The failure this catches is a generator writing a number that looks
        # like it belongs. Two per cent is the whole allowance.
        assert casefile.traceable(48_000.0, {45_419.0}) is False


class TestUntraceableFigures:
    FIGURES = {45_419.0, 1_320.0, 3.0, 22.0}

    def test_a_sentence_of_computed_figures_passes(self):
        text = "That runs about $45,000 a year and gives back roughly $1,320 a month."
        assert casefile.untraceable_figures(text, self.FIGURES) == []

    def test_an_invented_figure_is_caught_with_its_sentence(self):
        text = "That runs about $45,000 a year, against an industry average of $88,400."
        found = casefile.untraceable_figures(text, self.FIGURES)
        assert len(found) == 1
        written, sentence = found[0]
        assert "88,400" in written and "industry average" in sentence

    def test_a_token_is_not_a_figure_and_is_not_checked(self):
        # The numerals module decides what counts as a quantity; a phone number
        # reaching this check would be the same bug class in a new place.
        text = "Call the plant on (317) 555-0182 about the ISO 13485 line."
        assert casefile.untraceable_figures(text, set()) == []

    def test_it_stops_after_a_handful_so_the_retry_is_readable(self):
        text = " ".join(f"It costs ${n}0,000 a year." for n in range(1, 20))
        assert len(casefile.untraceable_figures(text, set())) == 6


class TestFigureSources:
    def test_the_ladder_bands_are_always_sayable(self):
        figures = casefile.ladder_figures()
        assert 600.0 in figures and 30_000.0 in figures

    def test_a_quotable_benchmark_is_sayable_and_an_aggregator_estimate_is_not(self):
        figures = benchmarks.figures()
        assert 0.85 in figures
        assert 0.537 not in figures

    def test_their_own_numbers_are_always_sayable(self):
        claims = [("block2.grant_amount", {"value": "$71,912 awarded in 2024"})]
        assert 71_912.0 in casefile.claim_figures(claims)

    def test_the_peer_table_is_read_across_all_three_of_its_columns(self):
        # The subject's own value is where the peer table puts a figure like
        # "about $300,000 counting the money they had to match". Reading only
        # the headline refused an analysis for quoting the table's own middle
        # column back at it.
        from lib import peers

        position = peers.Position(
            key="grant_capital", label="Capital deployed",
            subject_value="about $300,000 counting the money they had to match",
            headline="5 of the 13 we could measure are ahead of them",
            basis="the grant programme's own award records", comparable=True,
            peers_measured=13)

        class Group:
            size_of_group = 28

        figures = casefile.peer_figures([position], Group())
        assert 300_000.0 in figures
        assert 13.0 in figures and 28.0 in figures

    def test_a_case_file_gathers_every_source_into_one_set(self):
        case = casefile.CaseFile(
            company="X", tier=pricing.tier_for("core"),
            models=[casefile.ApproachModel(
                pattern_key="quoting_velocity", engagement_key="scoped_build",
                report=a_report())])
        figures = case.traceable_figures()
        assert 8_000.0 in figures            # the ladder band
        assert 0.85 in figures               # a quotable benchmark
        assert len(figures) > 50             # and the model's own outputs


class TestGainShareIsConditional:
    def test_it_is_refused_when_no_system_is_named(self):
        models = [casefile.ApproachModel(
            pattern_key="quoting_velocity", engagement_key="scoped_build",
            report=a_report())]
        share, reason = casefile._gain_share(models, {"named_systems": []})
        assert share is None
        assert "computed from a memory" in reason

    def test_it_is_offered_when_a_system_produces_the_metric(self):
        models = [casefile.ApproachModel(
            pattern_key="quoting_velocity", engagement_key="scoped_build",
            report=a_report())]
        share, reason = casefile._gain_share(models, {"named_systems": ["Epicor"]})
        assert share is not None
        assert "Epicor" in reason
        assert share.metric

    def test_the_offer_states_its_conditions(self):
        share = pricing.gain_share_for("scoped_build", "estimator hours")
        assert len(share.requirements) == 5
        assert any("BEFORE anything is changed" in r for r in share.requirements)

    def test_the_share_is_capped(self):
        share = pricing.gain_share_for("scoped_build", "estimator hours")
        assert share.cap_dollars() == (8_000, 20_000)


class TestOfferTierRouting:
    def test_a_core_company_leads_with_the_small_rung(self):
        assert pricing.tier_for("core").lead == "starter_automation"
        assert "600" in pricing.tier_for("core").money_words

    def test_the_core_money_words_describe_what_is_actually_offered(self):
        # The three shapes a core company is offered run to $15,000 at the top
        # rung, so saying "$600-$8,000" alone would be describing two of them.
        tier = pricing.tier_for("core")
        chosen = casefile.engagements_for(tier)
        top = max(pricing.BY_KEY[k].band[1] for k in chosen)
        assert top > 8_000
        assert "pilot from $6,000" in tier.money_words

    def test_a_growth_company_leads_with_the_operations_scope(self):
        assert pricing.tier_for("growth").lead == "premium_scope"
        assert "VP of Operations" in pricing.tier_for("growth").audience

    def test_a_size_we_do_not_hold_routes_to_the_cheaper_mistake(self):
        assert pricing.tier_for(None).band == "core"
        assert pricing.tier_for("enormous").band == "core"

    def test_the_engagements_offered_start_with_the_tier_lead(self):
        chosen = casefile.engagements_for(pricing.tier_for("growth"))
        assert chosen[0] == "premium_scope"
        assert len(chosen) == 3
        assert len(set(chosen)) == 3

    def test_positioning_is_stated_once_and_says_what_it_is(self):
        assert "without capital expenditure" in pricing.POSITIONING
        assert "auditor, not a vendor" in pricing.POSITIONING


class TestTiebreakersAreRecordedNotWeighted:
    def test_an_analyst_posting_is_read_off_the_flag(self):
        found = casefile.read_tiebreakers(prospect(evidence_file={
            "block3_hiring_signals": {"flags": {"data_role_posting": {
                "value": True, "tier": 1, "source_url": "https://hww.example/careers",
                "date_checked": "2026-09-09",
                "matched_roles": ["Operations Analyst"]}}}}))
        assert found["data_role_posting"] is True
        assert found["data_role_titles"] == ["Operations Analyst"]

    def test_named_systems_are_gathered_from_the_postings(self):
        found = casefile.read_tiebreakers(prospect(evidence_file={
            "block6_tech_stack": {"systems_named_in_postings": [
                {"value": "Epicor", "tier": 1, "source_url": "https://hww.example",
                 "date_checked": "2026-09-09"},
                {"value": "Manhattan Associates", "tier": 1,
                 "source_url": "https://hww.example", "date_checked": "2026-09-09"},
            ]}}))
        assert found["named_systems"] == ["Epicor", "Manhattan Associates"]

    def test_no_scoring_weight_moved(self):
        # The whole point of a tiebreaker: it is noticed, not weighted.
        from lib.scoring import COMPONENT_WEIGHTS
        assert "data_role_posting" not in COMPONENT_WEIGHTS


class TestThePromptCarriesTheArithmetic:
    def case(self, **kw):
        return casefile.CaseFile(
            company="Hoosier Widget Works", tier=pricing.tier_for("core"),
            models=[casefile.ApproachModel(
                pattern_key="quoting_velocity", engagement_key="scoped_build",
                report=a_report())],
            **kw)

    def test_it_forbids_computing_in_terms(self):
        block = casefile.prompt_block(self.case())
        assert "YOU MAY NOT COMPUTE" in block

    def test_it_names_every_input_and_where_it_came_from(self):
        block = casefile.prompt_block(self.case())
        assert "assumed:" in block
        assert "benchmark labour.loaded_multiplier.us_manufacturing" in block

    def test_it_states_who_the_document_is_for(self):
        assert "owner or president" in casefile.prompt_block(self.case())

    def test_it_says_plainly_when_a_gain_share_may_not_be_offered(self):
        block = casefile.prompt_block(self.case(
            gain_share_reason="no business system is named"))
        assert "GAIN SHARE IS NOT AVAILABLE" in block

    def test_it_hands_over_the_velocity_sentences_to_be_used_as_written(self):
        table = rivals.build_gap_table(
            "Hoosier Widget Works",
            rivals.observe_site("<p>We make things.</p>", "http://a", "Hoosier"),
            [rivals.observe_site(
                "<p>ISO 9001. Use our customer portal to upload CAD.</p>",
                f"https://r{i}", f"Rival {i}") for i in range(3)])
        block = casefile.prompt_block(self.case(gap_table=table))
        assert "as written or not at all" in block
        assert "3 of the 3 regional shops" in block

    def test_a_thin_comparison_is_not_offered_at_all(self):
        table = rivals.build_gap_table("X", None, [])
        assert "NAMED RIVALS" not in casefile.prompt_block(self.case(gap_table=table))


class TestScenariosAreInputSets:
    def test_the_three_readings_do_not_overlap(self):
        bands = offermodels.thirds(40, 160)
        assert bands["conservative"].as_pair() == (40.0, 80.0)
        assert bands["target"].as_pair() == (80.0, 120.0)
        assert bands["aggressive"].as_pair() == (120.0, 160.0)

    def test_they_move_only_the_inputs_the_prospect_settles(self):
        spec = offermodels.build_spec("quoting_velocity", prospect(), "scoped_build")
        for overrides in spec.scenarios.values():
            assert set(overrides) == set(offermodels.SCENARIO_INPUTS)

    def test_no_reading_claims_a_build_removes_everything(self):
        share = offermodels.scenario_share((0.6, 0.99), finmodel.AGGRESSIVE)
        assert share.high <= offermodels.AGGRESSIVE_CEILING

    def test_the_columns_come_out_in_order(self):
        report = a_report()
        low = report.scenarios["conservative"].value("annual_saving")
        high = report.scenarios["aggressive"].value("annual_saving")
        assert low.high < high.high

    def test_a_narrow_column_is_the_whole_reason_for_the_split(self):
        # One wide band on every input multiplied the widths together and gave
        # an annual cost spanning twenty-three times itself.
        target = a_report().scenarios["target"].value("annual_labour_cost")
        assert target.high / target.low < 5


class TestPatternChoice:
    def test_the_evidence_decides_which_arithmetic_applies(self):
        chosen = casefile.choose_patterns(prospect(), "quotes are assembled by hand")
        assert chosen

    def test_only_patterns_that_can_actually_be_modelled_are_offered(self):
        # A pattern with no work unit would be chosen and then fail to build,
        # which is a crash in the middle of a batch rather than a decision.
        chosen = casefile.choose_patterns(prospect(), "quotes are assembled by hand")
        assert all(key in offermodels.BY_PATTERN for key in chosen)

    def test_and_the_fallback_is_a_pattern_every_manufacturer_has(self, monkeypatch):
        monkeypatch.setattr("lib.roi_patterns.applicable", lambda _text: [])
        assert casefile.choose_patterns(prospect(), "") == ["quoting_velocity"]

    def test_a_pattern_with_no_work_unit_cannot_be_modelled(self):
        with pytest.raises(KeyError, match="no work unit"):
            offermodels.build_spec("invented_pattern", prospect(), "scoped_build")
