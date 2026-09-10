"""Tests for the deterministic financial engine.

The engine's whole claim is that a figure in an analysis can be traced to
arithmetic somebody can re-run. That claim is worth exactly as much as this file
is thorough, so the tests are organised around the promises rather than around
the functions: provenance is a precondition, ranges never collapse to
midpoints, the spec is the only place logic lives, and a projection that cannot
be made is an error rather than an empty chart.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from lib import finmodel as fm
from lib.finmodel import Interval


def quoting_spec(**overrides) -> fm.ModelSpec:
    """A realistic model: a manual quoting desk, and what automating part of it does."""
    inputs = {
        "quotes_per_month": fm.make_input(
            "quotes_per_month", 120,
            fm.claim_source("block4_digital_front_door.quote_volume"),
            unit="quotes", description="quotes leaving the desk each month"),
        "minutes_per_quote": fm.make_input(
            "minutes_per_quote", (30, 45),
            fm.assumed("half an hour to three quarters per quote, to be checked"),
            unit="minutes", description="estimator minutes per quote"),
        "loaded_hourly_rate": fm.make_input(
            "loaded_hourly_rate", (55, 85),
            fm.benchmark_source("labour.loaded_multiplier.us_manufacturing"),
            unit="$/hr", description="fully loaded estimator cost per hour"),
        "deployment_fee": fm.make_input(
            "deployment_fee", (8_000, 20_000),
            fm.benchmark_source("ladder.scoped_build"),
            unit="$", description="the one-off build"),
        "automation_share": fm.make_input(
            "automation_share", (0.40, 0.60),
            fm.assumed("the share of quoting effort a draft tool removes"),
            unit="share", description="share of quoting effort removed"),
        "wage_escalation": fm.make_input(
            "wage_escalation", 0.038,
            fm.benchmark_source("macro.eci.manufacturing"),
            unit="a year", description="annual wage escalation"),
        "discount_rate": fm.make_input(
            "discount_rate", 0.12,
            fm.assumed("a twelve percent cost of capital"), unit="a year"),
        "volume_growth": fm.make_input(
            "volume_growth", (0.05, 0.12),
            fm.assumed("quote volume growth"), unit="a year"),
        "manual_capacity": fm.make_input(
            "manual_capacity", 160,
            fm.assumed("what one estimator can process by hand in a month"),
            unit="quotes"),
    }
    formulas = {
        "hours_per_month": fm.div(
            fm.mul(fm.ref("quotes_per_month"), fm.ref("minutes_per_quote")),
            fm.const(60, "minutes in an hour")),
        "annual_labour_cost": fm.mul(
            fm.ref("hours_per_month"), fm.ref("loaded_hourly_rate"),
            fm.const(12, "months in a year")),
        "monthly_saving": fm.mul(
            fm.ref("hours_per_month"), fm.ref("loaded_hourly_rate"),
            fm.ref("automation_share")),
        "annual_saving": fm.annualise(fm.ref("monthly_saving")),
    }
    spec = fm.ModelSpec(
        model_id="quoting_desk",
        title="Quoting desk effort",
        inputs=inputs,
        formulas=formulas,
        scenarios={
            fm.CONSERVATIVE: {"automation_share": Interval.span(0.25, 0.35)},
            fm.TARGET: {},
            fm.AGGRESSIVE: {"automation_share": Interval.span(0.60, 0.75)},
        },
        roles=fm.Roles(
            investment="deployment_fee", monthly_saving="monthly_saving",
            annual_saving="annual_saving", escalating_cost="annual_labour_cost",
            escalation_rate="wage_escalation", discount_rate="discount_rate",
            monthly_volume="quotes_per_month", volume_growth="volume_growth",
            manual_capacity="manual_capacity"),
        horizon_months=36,
        units={"hours_per_month": "hours", "annual_labour_cost": "$",
               "monthly_saving": "$", "annual_saving": "$"},
    )
    return spec.model_copy(update=overrides) if overrides else spec


class TestIntervalsAreTheOnlyNumericType:
    def test_a_reversed_range_is_ordered_not_rejected(self):
        # And the swap must not read back the value it just wrote, which is how
        # the first version turned (9, 3) into (9, 9).
        assert Interval.span(9, 3).as_pair() == (3.0, 9.0)

    def test_a_point_is_a_range_whose_ends_agree(self):
        assert Interval.of(5).is_point

    def test_reading_accepts_a_number_a_pair_or_an_interval(self):
        assert Interval.read(4).as_pair() == (4.0, 4.0)
        assert Interval.read((1, 2)).as_pair() == (1.0, 2.0)
        assert Interval.read(Interval.span(1, 2)).as_pair() == (1.0, 2.0)

    def test_a_pair_of_the_wrong_length_is_refused(self):
        with pytest.raises(fm.FinModelError, match="exactly two ends"):
            Interval.read((1, 2, 3))

    def test_addition_and_subtraction_widen_correctly(self):
        assert (Interval.span(1, 2) + Interval.span(10, 20)).as_pair() == (11.0, 22.0)
        # Subtraction pairs the low end against the other's HIGH end, or the
        # result would be narrower than the truth.
        assert (Interval.span(10, 20) - Interval.span(1, 2)).as_pair() == (8.0, 19.0)

    def test_multiplication_uses_all_four_corners(self):
        # A negative end flips which product is smallest; a two-corner shortcut
        # is wrong exactly here.
        assert (Interval.span(-2, 3) * Interval.span(-4, 5)).as_pair() == (-12.0, 15.0)

    def test_dividing_by_a_range_spanning_zero_is_refused(self):
        with pytest.raises(fm.FinModelError, match="spans zero"):
            Interval.of(1) / Interval.span(-1, 1)

    def test_negation_swaps_the_ends(self):
        assert (-Interval.span(2, 5)).as_pair() == (-5.0, -2.0)


class TestProvenanceIsAPrecondition:
    def test_a_claim_must_name_a_path(self):
        with pytest.raises(fm.ProvenanceMissing, match="claim path"):
            fm.claim_source("")

    def test_a_benchmark_must_name_an_id(self):
        with pytest.raises(fm.ProvenanceMissing, match="benchmark id"):
            fm.benchmark_source("  ")

    def test_an_assumption_must_carry_a_label(self):
        # An unlabelled assumption is indistinguishable from a fact, which is the
        # single failure mode the whole pipeline is built against.
        with pytest.raises(fm.ProvenanceMissing, match="reads as a fact"):
            fm.assumed("")

    def test_an_input_cannot_be_built_without_provenance(self):
        with pytest.raises(ValidationError):
            fm.Input(name="x", value=Interval.of(1))

    def test_a_model_with_a_sourceless_input_does_not_evaluate(self):
        spec = quoting_spec()
        broken = dict(spec.inputs)
        broken["minutes_per_quote"] = fm.Input(
            name="minutes_per_quote", value=Interval.of(30),
            provenance=fm.Provenance(kind="derived", label="computed"))
        with pytest.raises(fm.ProvenanceMissing, match="minutes_per_quote"):
            fm.evaluate(spec.model_copy(update={"inputs": broken}))

    def test_it_raises_before_returning_any_number(self):
        # The point of raising rather than flagging: a caller handed a float
        # will use it, whatever the flag said.
        spec = quoting_spec()
        broken = dict(spec.inputs)
        broken["loaded_hourly_rate"] = fm.Input(
            name="loaded_hourly_rate", value=Interval.of(70),
            provenance=fm.Provenance(kind="derived"))
        with pytest.raises(fm.ProvenanceMissing):
            fm.run(spec.model_copy(update={"inputs": broken}))


class TestTheSpecIsTheOnlyPlaceLogicLives:
    def test_a_spec_round_trips_through_json_unchanged(self):
        spec = quoting_spec()
        again = fm.ModelSpec.model_validate(json.loads(json.dumps(spec.as_json_dict())))
        assert again.as_json_dict() == spec.as_json_dict()

    def test_and_the_round_tripped_copy_computes_the_same_answer(self):
        # This is the guarantee the interactive calculator rests on. If it ever
        # fails, some logic has leaked into Python and the two will disagree
        # about the prospect's business.
        spec = quoting_spec()
        again = fm.ModelSpec.model_validate(json.loads(json.dumps(spec.as_json_dict())))
        for scenario in fm.SCENARIOS:
            assert (fm.evaluate(again, scenario).values
                    == fm.evaluate(spec, scenario).values)

    def test_a_const_must_say_what_it_is(self):
        with pytest.raises(fm.BadFormula, match="must carry a label"):
            fm.Expr(op="const", value=60)

    def test_an_operator_given_the_wrong_number_of_operands_is_refused(self):
        with pytest.raises(fm.BadFormula, match="takes 2..2"):
            fm.Expr(op="div", args=[fm.const(1, "one")])

    def test_a_formula_naming_something_undefined_is_refused(self):
        with pytest.raises(fm.UnknownReference, match="nonsuch"):
            fm.ModelSpec(model_id="m", title="t",
                         formulas={"x": fm.ref("nonsuch")})

    def test_a_scenario_overriding_something_undefined_is_refused(self):
        with pytest.raises(fm.UnknownReference, match="nonsuch"):
            fm.ModelSpec(model_id="m", title="t",
                         scenarios={"target": {"nonsuch": Interval.of(1)}})

    def test_a_role_naming_something_undefined_is_refused(self):
        with pytest.raises(fm.UnknownReference, match="nonsuch"):
            fm.ModelSpec(model_id="m", title="t",
                         roles=fm.Roles(investment="nonsuch"))

    def test_formulas_that_depend_on_each_other_in_a_cycle_are_caught(self):
        spec = fm.ModelSpec(
            model_id="m", title="t",
            formulas={"a": fm.add(fm.ref("b"), fm.const(1, "one")),
                      "b": fm.add(fm.ref("a"), fm.const(1, "one"))})
        with pytest.raises(fm.BadFormula, match="cycle"):
            fm.evaluate(spec)

    def test_formulas_are_ordered_so_each_ones_inputs_exist(self):
        # Declaration order must not matter; the reader of a JSON spec should
        # never have to topologically sort it in their head.
        spec = quoting_spec()
        assert fm.evaluate(spec).value("annual_saving").low > 0


class TestEvaluation:
    def test_the_arithmetic_is_what_a_reader_would_get_by_hand(self):
        result = fm.evaluate(quoting_spec(), fm.TARGET)
        # 120 quotes x 30..45 minutes / 60 = 60..90 hours
        assert result.value("hours_per_month").as_pair() == (60.0, 90.0)
        # 60..90 hours x $55..$85 x 12 = $39,600..$91,800 a year
        assert result.value("annual_labour_cost").as_pair() == (39_600.0, 91_800.0)
        # 60..90 x 55..85 x 0.40..0.60
        assert result.value("monthly_saving").as_pair() == (1_320.0, 4_590.0)

    def test_a_scenario_overrides_only_what_it_names(self):
        conservative = fm.evaluate(quoting_spec(), fm.CONSERVATIVE)
        assert conservative.value("automation_share").as_pair() == (0.25, 0.35)
        assert conservative.value("hours_per_month").as_pair() == (60.0, 90.0)

    def test_the_three_scenarios_are_ordered_conservative_target_aggressive(self):
        assert list(fm.evaluate_all(quoting_spec())) == list(fm.SCENARIOS)

    def test_a_scenario_the_spec_does_not_define_is_refused(self):
        with pytest.raises(fm.UnknownReference, match="heroic"):
            fm.evaluate(quoting_spec(), "heroic")

    def test_every_output_carries_the_full_set_of_sources_behind_it(self):
        result = fm.evaluate(quoting_spec())
        sources = result.sources_for("monthly_saving")
        assert {s.kind for s in sources} == {"claim", "assumption", "benchmark"}
        assert any(s.ref == "block4_digital_front_door.quote_volume" for s in sources)

    def test_the_same_source_is_not_listed_twice(self):
        result = fm.evaluate(quoting_spec())
        sources = result.sources_for("annual_labour_cost")
        keys = [(s.kind, s.ref, s.label) for s in sources]
        assert len(keys) == len(set(keys))

    def test_asking_for_a_figure_the_model_does_not_produce_is_an_error(self):
        with pytest.raises(fm.UnknownReference, match="not a figure"):
            fm.evaluate(quoting_spec()).value("gross_margin")

    def test_assumptions_are_listed_for_the_call(self):
        labels = [a.label for a in fm.evaluate(quoting_spec()).assumptions()]
        assert "the share of quoting effort a draft tool removes" in labels
        assert all(a.kind == "assumption" for a in fm.evaluate(quoting_spec()).assumptions())

    def test_compound_is_base_times_one_plus_rate_to_the_terms(self):
        spec = fm.ModelSpec(
            model_id="m", title="t",
            inputs={"base": fm.make_input("base", 100, fm.assumed("a base")),
                    "rate": fm.make_input("rate", 0.10, fm.assumed("a rate")),
                    "years": fm.make_input("years", 3, fm.assumed("a term"))},
            formulas={"grown": fm.compound(fm.ref("base"), fm.ref("rate"),
                                           fm.ref("years"))})
        assert fm.evaluate(spec).value("grown").low == pytest.approx(133.1)

    def test_a_growth_rate_of_minus_one_or_worse_does_not_compound(self):
        spec = fm.ModelSpec(
            model_id="m", title="t",
            inputs={"base": fm.make_input("base", 100, fm.assumed("a base")),
                    "rate": fm.make_input("rate", -1.5, fm.assumed("a rate")),
                    "years": fm.make_input("years", 2, fm.assumed("a term"))},
            formulas={"grown": fm.compound(fm.ref("base"), fm.ref("rate"),
                                           fm.ref("years"))})
        with pytest.raises(fm.FinModelError, match="total loss"):
            fm.evaluate(spec)


class TestCostTrajectory:
    def test_the_escalating_path_pulls_away_from_the_flat_one(self):
        spec = quoting_spec()
        trajectory = fm.cost_trajectory(spec, fm.evaluate(spec))
        assert trajectory.escalating.low[0] == pytest.approx(trajectory.flat.low[0])
        assert trajectory.escalating.low[-1] > trajectory.flat.low[-1]

    def test_the_cumulative_gap_only_grows(self):
        spec = quoting_spec()
        gap = fm.cost_trajectory(spec, fm.evaluate(spec)).cumulative_gap
        assert gap.low == sorted(gap.low)
        assert gap.low[0] == pytest.approx(0.0)

    def test_it_runs_to_the_declared_horizon(self):
        spec = quoting_spec()
        assert fm.cost_trajectory(spec, fm.evaluate(spec)).escalating.months[-1] == 36

    def test_a_model_with_no_escalation_rate_has_no_trajectory(self):
        spec = quoting_spec(roles=fm.Roles(escalating_cost="annual_labour_cost"))
        with pytest.raises(fm.UnknownReference, match="no trajectory"):
            fm.cost_trajectory(spec, fm.evaluate(spec))


class TestPaybackIsASeries:
    def test_the_worst_reading_is_the_biggest_bill_against_the_smallest_saving(self):
        spec = quoting_spec()
        payback = fm.payback_series(spec, fm.evaluate(spec))
        # $20,000 against $1,320 a month crosses in month 16.
        assert payback.slowest_month == 16
        # $8,000 against $4,590 a month crosses in month 2.
        assert payback.fastest_month == 2

    def test_the_series_is_month_by_month_and_not_one_number(self):
        spec = quoting_spec()
        payback = fm.payback_series(spec, fm.evaluate(spec))
        assert payback.cumulative.months == list(range(1, 37))
        assert len(payback.cumulative.low) == 36

    def test_a_model_that_never_pays_back_says_so_rather_than_returning_a_month(self):
        spec = quoting_spec()
        starved = dict(spec.inputs)
        starved["automation_share"] = fm.make_input(
            "automation_share", 0.001, fm.assumed("almost nothing is removed"))
        thin = spec.model_copy(update={"inputs": starved, "scenarios": {}})
        payback = fm.payback_series(thin, fm.evaluate(thin))
        assert payback.fastest_month is None and payback.slowest_month is None
        assert "does not pay back" in payback.describe()

    def test_a_one_sided_payback_is_described_as_one_sided(self):
        spec = quoting_spec()
        payback = fm.payback_series(spec, fm.evaluate(spec), months=6)
        assert payback.fastest_month == 2 and payback.slowest_month is None
        assert "at best" in payback.describe() and "at worst" in payback.describe()

    def test_the_description_never_hands_over_a_bare_number(self):
        spec = quoting_spec()
        assert "month" in fm.payback_series(spec, fm.evaluate(spec)).describe()

    def test_a_model_with_no_investment_role_has_no_payback(self):
        spec = quoting_spec(roles=fm.Roles(monthly_saving="monthly_saving"))
        with pytest.raises(fm.UnknownReference, match="no payback"):
            fm.payback_series(spec, fm.evaluate(spec))


class TestDiscounting:
    def test_the_monthly_rate_compounds_to_the_annual_one(self):
        assert (1 + fm.monthly_rate(0.12)) ** 12 == pytest.approx(1.12)

    def test_a_flow_a_month_from_now_is_discounted_once(self):
        assert fm.npv([100.0], 0.12) == pytest.approx(100 / (1 + fm.monthly_rate(0.12)))

    def test_irr_recovers_a_rate_the_flows_were_built_from(self):
        flows = [-1000.0] + [100.0] * 12
        found = fm.irr(flows)
        assert found is not None
        assert fm.npv(flows, found) == pytest.approx(0.0, abs=1e-5)

    def test_a_series_that_never_turns_positive_has_no_irr(self):
        assert fm.irr([-100.0, -100.0, -100.0]) is None

    def test_an_empty_series_has_no_irr(self):
        assert fm.irr([]) is None

    def test_the_rate_used_is_stated_on_the_result(self):
        spec = quoting_spec()
        result = fm.discounted(spec, fm.evaluate(spec))
        assert result.discount_rate_annual == 0.12
        assert result.npv_low < result.npv_high

    def test_an_absurd_rate_of_return_is_described_rather_than_printed(self):
        # A build paying for itself in seven weeks has an IRR in the hundreds of
        # per cent. That is arithmetically true and useless: nobody books it,
        # and a reader who sees it stops believing the figures around it.
        spec = quoting_spec()
        told = fm.discounted(spec, fm.evaluate(spec)).describe()
        assert "present value" in told
        assert "above 100% a year" in told

    def test_a_believable_rate_is_given_as_a_number(self):
        result = fm.Discounted(
            discount_rate_annual=0.12, npv_low=100.0, npv_high=200.0,
            irr_annual_low=0.18, irr_annual_high=0.24, horizon_months=36)
        assert "about 24% a year" in result.describe()

    def test_a_position_that_never_turns_positive_has_none_and_says_so(self):
        result = fm.Discounted(
            discount_rate_annual=0.12, npv_low=-100.0, npv_high=-50.0,
            irr_annual_low=None, irr_annual_high=None, horizon_months=36)
        assert "never turns positive" in result.describe()

    def test_an_npv_needs_a_rate_from_somewhere(self):
        spec = quoting_spec(roles=fm.Roles(
            investment="deployment_fee", monthly_saving="monthly_saving"))
        with pytest.raises(fm.UnknownReference, match="stated rate"):
            fm.discounted(spec, fm.evaluate(spec))


class TestSensitivity:
    def test_it_solves_for_the_value_that_brings_payback_inside_the_target(self):
        spec = quoting_spec()
        found = fm.sensitivity(spec, "minutes_per_quote", target_months=12)
        # 120 quotes x m minutes / 60 x $55 x 0.40 x 12 months >= $20,000
        assert found.threshold == pytest.approx(37.88, abs=0.5)
        assert found.direction == "at least"

    def test_it_says_so_in_a_sentence_an_operator_can_ask_on_the_call(self):
        spec = quoting_spec()
        sentence = fm.sensitivity(spec, "minutes_per_quote", 12).describe()
        assert "estimator minutes per quote" in sentence
        assert "at least" in sentence

    def test_it_reports_whether_the_target_already_holds(self):
        spec = quoting_spec()
        assert fm.sensitivity(spec, "minutes_per_quote", 12).already_holds is False
        assert fm.sensitivity(spec, "minutes_per_quote", 24).already_holds is True

    def test_a_target_nothing_in_range_achieves_returns_no_threshold(self):
        spec = quoting_spec()
        found = fm.sensitivity(spec, "minutes_per_quote", target_months=1,
                               search=(0.0, 1.0))
        assert found.threshold is None
        assert found.searched_high == 1.0
        assert "no value" in found.describe()

    def test_the_probe_is_not_overridden_by_the_scenario_it_runs_under(self):
        # The scenario override for the probed input has to be dropped, or the
        # search silently measures the scenario's own figure and returns a
        # threshold that is really just the declared value.
        spec = quoting_spec()
        loose = fm.sensitivity(spec, "automation_share", 12, scenario=fm.CONSERVATIVE)
        assert loose.threshold is not None
        assert loose.threshold > 0.35

    def test_a_value_the_model_cannot_evaluate_is_not_a_crash(self):
        # A capacity formula divides by hours per unit, so zero minutes has no
        # answer. The scan used to start at zero and fail on its own first probe.
        spec = quoting_spec()
        with_capacity = spec.model_copy(update={
            "formulas": {**spec.formulas, "capacity": fm.div(
                fm.const(160, "hours a month"), fm.ref("minutes_per_quote"))},
        })
        found = fm.sensitivity(with_capacity, "minutes_per_quote", 12)
        assert found.threshold is not None
        assert found.searched_low > 0

    def test_solving_for_an_input_the_spec_does_not_have_is_an_error(self):
        with pytest.raises(fm.UnknownReference, match="not an input"):
            fm.sensitivity(quoting_spec(), "moon_phase", 12)


class TestCapacityCollapse:
    def test_it_finds_the_month_demand_crosses_the_manual_rate(self):
        spec = quoting_spec()
        collapse = fm.capacity_collapse(spec, fm.evaluate(spec))
        # 120 a month growing at 5-12% a year against a rate of 160.
        assert collapse.crossing_month_earliest is not None
        assert collapse.crossing_month_earliest < collapse.horizon_months

    def test_headroom_today_is_reported_as_well_as_the_crossing(self):
        spec = quoting_spec()
        assert fm.capacity_collapse(spec, fm.evaluate(spec)).headroom_now.as_pair() \
            == (40.0, 40.0)

    def test_the_ends_are_paired_coherently_rather_than_crossed(self):
        # Capacity is usually derived from the same volume demand is, so the
        # busiest demand belongs against the busiest capacity. Crossing them
        # describes a world with more work and fewer people in it, and reported
        # a collapse in month one on every model that had one.
        spec = quoting_spec()
        wide = dict(spec.inputs)
        wide["quotes_per_month"] = fm.make_input(
            "quotes_per_month", (80, 120), fm.assumed("a range of volumes"),
            unit="quotes")
        wide["manual_capacity"] = fm.make_input(
            "manual_capacity", (88, 150), fm.assumed("ten to twenty-five per cent spare"),
            unit="quotes")
        probe = spec.model_copy(update={"inputs": wide, "scenarios": {}})
        collapse = fm.capacity_collapse(probe, fm.evaluate(probe))
        assert collapse.crossing_month_earliest is None or \
            collapse.crossing_month_earliest > 1
        assert collapse.headroom_now.low > 0

    def test_a_rate_nothing_crosses_is_said_plainly(self):
        spec = quoting_spec()
        roomy = dict(spec.inputs)
        roomy["manual_capacity"] = fm.make_input(
            "manual_capacity", 100_000, fm.assumed("an enormous manual rate"))
        collapse = fm.capacity_collapse(
            spec.model_copy(update={"inputs": roomy}),
            fm.evaluate(spec.model_copy(update={"inputs": roomy})))
        assert collapse.crosses_within_horizon is False
        assert "stays inside" in collapse.describe()

    def test_a_model_missing_any_of_the_three_roles_makes_no_projection(self):
        spec = quoting_spec(roles=fm.Roles(
            monthly_volume="quotes_per_month", volume_growth="volume_growth"))
        with pytest.raises(fm.UnknownReference, match="manual_capacity"):
            fm.capacity_collapse(spec, fm.evaluate(spec))


class TestTheWholeReport:
    def test_it_builds_every_projection_the_roles_support(self):
        report = fm.run(quoting_spec(), sensitivity_targets=(("minutes_per_quote", 12),))
        assert set(report.scenarios) == set(fm.SCENARIOS)
        assert set(report.payback) == set(fm.SCENARIOS)
        assert set(report.trajectory) == set(fm.SCENARIOS)
        assert set(report.collapse) == set(fm.SCENARIOS)
        assert set(report.discounting) == set(fm.SCENARIOS)
        assert len(report.sensitivities) == 1

    def test_a_model_without_capacity_roles_gets_no_collapse_chart(self):
        spec = quoting_spec(roles=fm.Roles(
            investment="deployment_fee", monthly_saving="monthly_saving"))
        report = fm.run(spec)
        assert report.collapse == {}
        assert report.discounting == {}
        assert set(report.payback) == set(fm.SCENARIOS)

    def test_the_traceable_figures_include_every_computed_value(self):
        report = fm.run(quoting_spec())
        figures = report.traceable_figures()
        assert 1_320.0 in figures and 4_590.0 in figures
        assert 39_600.0 in figures

    def test_a_number_the_model_never_produced_is_not_traceable(self):
        assert 123_456.789 not in fm.run(quoting_spec()).traceable_figures()

    def test_it_lists_the_claims_the_analysis_must_cite(self):
        assert fm.run(quoting_spec()).claim_paths() == {
            "block4_digital_front_door.quote_volume"}

    def test_it_lists_the_benchmarks_the_renderer_must_print_citations_for(self):
        assert "macro.eci.manufacturing" in fm.run(quoting_spec()).benchmark_ids()

    def test_it_lists_the_assumptions_that_must_be_checked_on_the_call(self):
        labels = [a.label for a in fm.run(quoting_spec()).assumptions()]
        assert "a twelve percent cost of capital" in labels

    def test_the_whole_report_serialises(self):
        # The dossier stores it and the calculator will read it.
        report = fm.run(quoting_spec(), sensitivity_targets=(("minutes_per_quote", 12),))
        assert json.loads(json.dumps(report.model_dump(mode="json")))


class TestNotEveryClaimIsAboutTheProspect:
    def test_an_unlabelled_claim_reads_as_their_own_record(self):
        assert fm.claim_source("block2.grant_amount").describe() == (
            "their own record [block2.grant_amount]")

    def test_a_labelled_one_says_what_it_actually_is(self):
        # The wage-escalation input is a claim built from a published national
        # series. Rendering it as "their own record" told the reader that the
        # Employment Cost Index is something this company published.
        described = fm.claim_source(
            "bls.eci.private.twelve_month",
            label="the published employment cost index").describe()
        assert described.startswith("the published employment cost index")
        assert "their own record" not in described
