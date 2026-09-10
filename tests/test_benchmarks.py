"""Tests for the benchmark library.

The library's whole value is what it refuses to contain, so most of this file is
about the refusals: an entry that cannot say where it came from does not build, a
Tier 3 aggregator estimate cannot be printed, and an id cannot quietly change
because stored analyses cite these by id.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

import pytest

from lib import benchmarks, finmodel

SHIPPED_IDS = {
    "labour.loaded_multiplier.us_manufacturing",
    "labour.loaded_multiplier.us_private",
    "labour.benefit_share.us_manufacturing",
    "oee.world_class.nakajima",
    "oee.typical.discrete_manufacturing",
    "oee.typical.metal_fabrication",
    "sales.lead_response.qualification_odds",
}


class TestEveryEntryCarriesItsCitation:
    @pytest.mark.parametrize("entry", benchmarks.REGISTRY, ids=lambda e: e.benchmark_id)
    def test_the_url_is_one_a_human_can_open(self, entry):
        parsed = urlparse(entry.source_url)
        assert parsed.scheme in ("http", "https") and parsed.netloc

    @pytest.mark.parametrize("entry", benchmarks.REGISTRY, ids=lambda e: e.benchmark_id)
    def test_the_retrieval_date_is_a_real_date_and_not_in_the_future(self, entry):
        assert date.fromisoformat(entry.retrieved) <= date.today()

    @pytest.mark.parametrize("entry", benchmarks.REGISTRY, ids=lambda e: e.benchmark_id)
    def test_it_says_what_it_is_good_for_and_how_it_was_made(self, entry):
        assert entry.applies_to.strip() and entry.method.strip()

    @pytest.mark.parametrize("entry", benchmarks.REGISTRY, ids=lambda e: e.benchmark_id)
    def test_every_unassertable_entry_says_so_in_its_caution(self, entry):
        if not entry.assertable:
            assert "TIER 3" in entry.caution or "never" in entry.caution.lower()

    def test_a_benchmark_with_no_method_does_not_build(self):
        with pytest.raises(benchmarks.BenchmarkError, match="folklore"):
            benchmarks.Benchmark(
                benchmark_id="x", label="x", value=finmodel.Interval.of(1),
                tier=1, applies_to="everything", publisher="somebody",
                source_title="something", source_url="https://example.com",
                retrieved="2026-09-09", method="  ")

    def test_a_benchmark_with_an_unopenable_url_does_not_build(self):
        with pytest.raises(ValueError, match="http"):
            benchmarks.Benchmark(
                benchmark_id="x", label="x", value=finmodel.Interval.of(1),
                tier=1, applies_to="everything", publisher="somebody",
                source_title="something", source_url="see the report",
                retrieved="2026-09-09", method="counted them")


class TestIdsAreStable:
    def test_the_library_holds_exactly_what_is_recorded_here(self):
        # Analyses cite benchmarks by id and store the citation. Renaming one
        # orphans every document that used it, so a rename is a migration.
        assert set(benchmarks.BY_ID) == SHIPPED_IDS

    def test_an_unknown_id_raises_and_says_what_there_is(self):
        with pytest.raises(benchmarks.UnknownBenchmark, match="oee.world_class.nakajima"):
            benchmarks.get("oee.world_class.job_shop")


class TestTiersAreEnforcedNotRemembered:
    def test_a_government_record_may_be_stated_as_fact(self):
        assert benchmarks.assert_quotable("labour.loaded_multiplier.us_manufacturing")

    def test_a_named_published_work_may_be_stated_with_attribution(self):
        entry = benchmarks.assert_quotable("oee.world_class.nakajima")
        assert entry.needs_attribution
        assert "Nakajima" in entry.in_words()

    @pytest.mark.parametrize("benchmark_id", [
        "oee.typical.discrete_manufacturing",
        "oee.typical.metal_fabrication",
    ])
    def test_an_aggregator_estimate_may_never_be_printed(self, benchmark_id):
        with pytest.raises(benchmarks.NotAssertable, match="tier 3"):
            benchmarks.assert_quotable(benchmark_id)

    def test_but_it_may_still_shape_a_scenario(self):
        # Internal filtering is exactly what T3 is for; the bar is on printing.
        model_input = benchmarks.as_input("oee.typical.metal_fabrication")
        assert model_input.value.as_pair() == (0.537, 0.712)

    def test_the_traceable_figures_exclude_everything_unassertable(self):
        figures = benchmarks.figures()
        assert 0.85 in figures and 85.0 in figures
        assert 0.537 not in figures and 0.712 not in figures


class TestUsableAsModelInputs:
    def test_a_benchmark_becomes_an_input_carrying_its_id(self):
        model_input = benchmarks.as_input(
            "labour.loaded_multiplier.us_manufacturing", name="loaded_multiplier")
        assert model_input.name == "loaded_multiplier"
        assert model_input.provenance.kind == finmodel.BENCHMARK
        assert model_input.provenance.ref == "labour.loaded_multiplier.us_manufacturing"

    def test_a_model_built_on_benchmarks_evaluates_and_reports_them(self):
        spec = finmodel.ModelSpec(
            model_id="m", title="t",
            inputs={
                "wage": finmodel.make_input(
                    "wage", 28.0, finmodel.claim_source("block7_people.wage")),
                "multiplier": benchmarks.as_input(
                    "labour.loaded_multiplier.us_manufacturing", name="multiplier"),
            },
            formulas={"loaded_rate": finmodel.mul(
                finmodel.ref("wage"), finmodel.ref("multiplier"))})
        report = finmodel.run(spec)
        assert report.benchmark_ids() == {"labour.loaded_multiplier.us_manufacturing"}
        assert report.scenarios["target"].value("loaded_rate").low == pytest.approx(41.888)

    def test_the_renderer_gets_a_citation_line_for_every_id_used(self):
        lines = benchmarks.citations_for(["oee.world_class.nakajima"])
        assert len(lines) == 1
        assert "books.google.com" in lines[0] and "2026-09-09" in lines[0]
        assert "0.85" in lines[0]


class TestTheArithmeticMatchesTheCitation:
    def test_the_manufacturing_multiplier_is_what_its_own_method_describes(self):
        # The recorded value and the sentence explaining it must not drift apart;
        # a citation that no longer produces the number is worse than none.
        entry = benchmarks.get("labour.loaded_multiplier.us_manufacturing")
        assert entry.value.low == pytest.approx(48.62 / 32.50, abs=0.001)

    def test_the_private_multiplier_likewise(self):
        entry = benchmarks.get("labour.loaded_multiplier.us_private")
        assert entry.value.low == pytest.approx(46.89 / 32.82, abs=0.001)

    def test_the_benefit_share_likewise(self):
        entry = benchmarks.get("labour.benefit_share.us_manufacturing")
        assert entry.value.low == pytest.approx(16.12 / 48.62, abs=0.001)


class TestWhatDidNotShip:
    def test_the_world_class_metal_fabrication_figure_is_absent(self):
        assert "oee.world_class.metal_fabrication" not in benchmarks.BY_ID

    def test_and_the_module_records_why_rather_than_going_quiet(self):
        withheld = dict(benchmarks.WITHHELD)
        assert "oee.world_class.metal_fabrication" in withheld
        assert "71.2" in withheld["oee.world_class.metal_fabrication"]

    def test_the_vendor_five_minute_figure_is_absent_too(self):
        withheld = dict(benchmarks.WITHHELD)
        assert "sales.lead_response.five_minute_rule" in withheld
        assert "vendor" in withheld["sales.lead_response.five_minute_rule"]

    def test_the_lead_response_entry_says_it_is_not_about_win_rate(self):
        # The brief asked for response speed against WIN RATE. The verifiable
        # study measures qualification, and letting that drift is how a sourced
        # number becomes a folklore one.
        entry = benchmarks.get("sales.lead_response.qualification_odds")
        assert "not of winning the work" in entry.applies_to
        assert "win rate" in entry.caution
