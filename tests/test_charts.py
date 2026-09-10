"""Tests for the charts, which may only ever be drawn from an evaluated model.

A chart is the most persuasive thing in a document and the least examined, so the
rule that matters is the refusal: asking for a picture the model does not support
raises rather than producing an empty axis with a confident title on it.
"""

from __future__ import annotations

import pytest

from lib import charts, finmodel, offermodels

PROSPECT = {"company_name": "Hoosier Widget Works", "evidence_file": {}}


def report(**overrides):
    spec = offermodels.build_spec("quoting_velocity", PROSPECT, "scoped_build")
    if overrides:
        spec = spec.model_copy(update=overrides)
    return finmodel.run(spec)


class TestNoChartWithoutAModel:
    def test_a_model_with_no_payback_refuses_the_payback_chart(self):
        stripped = report(roles=finmodel.Roles())
        with pytest.raises(charts.NoModelBehindIt, match="no payback"):
            charts.payback_chart(stripped)

    def test_a_model_with_no_escalation_rate_refuses_the_trajectory(self):
        stripped = report(roles=finmodel.Roles(
            investment="deployment_fee", monthly_saving="monthly_saving"))
        with pytest.raises(charts.NoModelBehindIt, match="no cost trajectory"):
            charts.trajectory_chart(stripped)

    def test_charts_for_draws_what_is_supported_and_skips_the_rest(self):
        stripped = report(roles=finmodel.Roles(
            investment="deployment_fee", monthly_saving="monthly_saving"))
        drawn = charts.charts_for(stripped)
        assert set(drawn) == {"payback"}

    def test_a_full_model_gets_all_three(self):
        assert set(charts.charts_for(report())) == {
            "payback", "trajectory", "capacity"}


class TestTheChartsThemselves:
    def test_each_one_renders_a_png(self):
        for image in charts.charts_for(report()).values():
            assert image.startswith(b"\x89PNG")
            assert len(image) > 5_000

    def test_a_long_title_is_trimmed_rather_than_running_off_the_page(self):
        long = "A " * 120
        assert len(charts._trim(long)) <= charts.TITLE_LIMIT

    def test_a_short_title_is_left_alone(self):
        assert charts._trim("Quoting desk effort") == "Quoting desk effort"


class TestTheCaptionIsWhatMakesItCheckable:
    def test_it_names_the_model(self):
        caption = charts.caption_for(report(), "payback")
        assert "hoosier_widget_works.quoting_velocity.scoped_build" in caption

    def test_it_names_the_benchmarks_behind_the_lines(self):
        assert "labour.loaded_multiplier.us_manufacturing" in charts.caption_for(
            report(), "payback")

    def test_it_counts_the_assumptions_and_calls_them_questions(self):
        caption = charts.caption_for(report(), "payback")
        assert "labelled assumption" in caption
        assert "question for the call" in caption

    def test_every_scenario_is_accounted_for(self):
        assert "3 scenarios" in charts.caption_for(report(), "payback")
