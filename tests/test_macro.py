"""Tests for the macro series, run entirely offline.

Two promises are being tested. The first is that a missing key produces a shorter
analysis rather than an incident: nothing in this module may raise out of a run.
The second is that a macro sentence is as checkable as any other sentence here —
it names its series, it carries both observation dates, and the URL on the claim
is a page a human opens, never the API endpoint the key travels in.
"""

from __future__ import annotations

from datetime import date

import pytest

from lib import macro
from lib.claims import Tier


def observations(pairs) -> list[macro.Observation]:
    return [macro.Observation(observed=d, value=v) for d, v in pairs]


LEVEL = macro.SeriesDef(
    series_id="test.level", provider="bls", code="CES3000000001",
    title="All Employees, Manufacturing", unit="thousands of persons",
    geography="United States", reading="level",
    means="how many people are available to do this work")

RATE = macro.SeriesDef(
    series_id="test.rate", provider="bls", code="CIU2010000000000A",
    title="Employment Cost Index, 12-month percent change", unit="percent",
    geography="United States", reading="rate",
    means="the rate wage bills are climbing at")

FRED_SERIES = macro.SeriesDef(
    series_id="test.fred", provider="fred", code="ECIWAG",
    title="Employment Cost Index: Wages and Salaries", unit="index",
    geography="United States", reading="index", means="what an hour costs")


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(macro, "CACHE_DIR", tmp_path / "macro")
    return tmp_path / "macro"


@pytest.fixture
def no_delay(monkeypatch):
    monkeypatch.setattr(
        macro, "settings", macro.settings.model_copy(update={"fetch_delay_seconds": 0}))


class TestNotConfiguredIsAReportNotAFailure:
    def test_a_missing_fred_key_makes_the_series_unavailable_not_an_error(
            self, isolated_cache, monkeypatch):
        monkeypatch.setattr(
            macro, "settings", macro.settings.model_copy(update={"fred_api_key": None}))
        results = macro.fetch([FRED_SERIES])
        assert results[0].available is False
        assert "FRED_API_KEY" in results[0].error

    def test_the_status_line_names_the_variable_to_set(self, monkeypatch):
        monkeypatch.setattr(
            macro, "settings", macro.settings.model_copy(
                update={"fred_api_key": None, "bls_api_key": None}))
        assert "FRED_API_KEY is unset" in macro.status().report()

    def test_the_unregistered_bls_cap_is_stated_rather_than_hidden(self, monkeypatch):
        monkeypatch.setattr(
            macro, "settings", macro.settings.model_copy(update={"bls_api_key": None}))
        assert "25 queries a day" in macro.status().report()

    def test_a_provider_that_throws_becomes_a_reason_not_an_exception(
            self, isolated_cache):
        def angry(_definition):
            raise TimeoutError("the agency did not answer")
        results = macro.fetch([LEVEL], fetcher=angry)
        assert results[0].available is False
        assert "TimeoutError" in results[0].error

    def test_an_empty_answer_is_unavailable_rather_than_an_empty_series(
            self, isolated_cache):
        results = macro.fetch([LEVEL], fetcher=lambda _d: [])
        assert results[0].available is False
        assert "no usable observations" in results[0].error


class TestCachedPerSeriesPerWeek:
    def test_a_second_read_in_the_same_week_does_not_ask_again(self, isolated_cache):
        calls = []

        def counting(definition):
            calls.append(definition.series_id)
            return observations([("2026-08-01", 12_638.0)])

        when = date(2026, 9, 9)
        macro.fetch([LEVEL], fetcher=counting, when=when)
        second = macro.fetch([LEVEL], fetcher=counting, when=when)
        assert calls == ["test.level"]
        assert second[0].cached is True
        assert second[0].latest.value == 12_638.0

    def test_a_different_week_asks_again(self, isolated_cache):
        calls = []

        def counting(definition):
            calls.append(definition.series_id)
            return observations([("2026-08-01", 12_638.0)])

        macro.fetch([LEVEL], fetcher=counting, when=date(2026, 9, 9))
        macro.fetch([LEVEL], fetcher=counting, when=date(2026, 9, 17))
        assert len(calls) == 2

    def test_an_unavailable_series_is_not_cached(self, isolated_cache):
        macro.fetch([LEVEL], fetcher=lambda _d: [], when=date(2026, 9, 9))
        assert not list(isolated_cache.glob("*.json")) or all(
            "test.level" not in p.name for p in isolated_cache.glob("*.json"))

    def test_the_cache_can_be_turned_off(self, isolated_cache):
        calls = []

        def counting(definition):
            calls.append(1)
            return observations([("2026-08-01", 1.0)])

        macro.fetch([LEVEL], fetcher=counting, when=date(2026, 9, 9), use_cache=False)
        macro.fetch([LEVEL], fetcher=counting, when=date(2026, 9, 9), use_cache=False)
        assert len(calls) == 2


class TestClaims:
    def test_an_observation_becomes_a_tier_one_claim(self, isolated_cache):
        result = macro.fetch(
            [LEVEL], fetcher=lambda _d: observations([("2026-08-01", 12_638.0)]),
            when=date(2026, 9, 9))[0]
        claim = macro.as_claim(result)
        assert claim["tier"] == int(Tier.T1)
        assert claim["date_checked"] == "2026-09-09"

    def test_the_claim_carries_the_series_id_and_the_observation_date(
            self, isolated_cache):
        result = macro.fetch(
            [LEVEL], fetcher=lambda _d: observations([("2026-08-01", 12_638.0)]),
            when=date(2026, 9, 9))[0]
        value = macro.as_claim(result)["value"]
        assert "CES3000000001" in value and "2026-08-01" in value

    def test_a_published_level_is_written_the_way_the_agency_writes_it(
            self, isolated_cache):
        # 12,638 came out as 1.264e+04, which is the right number in a form
        # nobody would recognise on the page they were told to open.
        result = macro.fetch(
            [LEVEL], fetcher=lambda _d: observations([("2026-08-01", 12_638.0)]),
            when=date(2026, 9, 9))[0]
        assert "12,638" in macro.as_claim(result)["value"]

    def test_the_source_url_is_a_page_a_human_opens_and_not_the_api(self):
        # The API URL carries the key. Putting one in a claim would publish a
        # credential in a document.
        assert LEVEL.page_url == "https://data.bls.gov/timeseries/CES3000000001"
        assert FRED_SERIES.page_url == "https://fred.stlouisfed.org/series/ECIWAG"
        assert "api_key" not in FRED_SERIES.page_url

    def test_a_series_with_nothing_in_it_cannot_become_a_claim(self):
        empty = macro.SeriesResult(definition=LEVEL)
        with pytest.raises(ValueError, match="no observation"):
            macro.as_claim(empty)


class TestTheHeadwindParagraph:
    def make(self, pairs, definition=LEVEL):
        return macro.SeriesResult(
            definition=definition, observations=observations(pairs),
            retrieved="2026-09-09")

    def test_an_index_states_its_change_and_both_endpoints(self):
        result = self.make([("2023-08-01", 100.0), ("2026-08-01", 112.5)])
        found = macro.headwind([result], months=36, today=date(2026, 9, 9))
        sentence = found.lines[0].sentence
        assert "rose 12.5 percent" in sentence
        assert "2023-08-01" in sentence and "2026-08-01" in sentence

    def test_a_fall_is_said_as_a_fall(self):
        result = self.make([("2023-08-01", 100.0), ("2026-08-01", 98.2)])
        assert "fell 1.8 percent" in macro.headwind(
            [result], months=36, today=date(2026, 9, 9)).lines[0].sentence

    def test_a_rate_series_states_its_latest_reading_instead(self):
        result = self.make([("2026-04-01", 3.3)], definition=RATE)
        line = macro.headwind([result], today=date(2026, 9, 9)).lines[0]
        assert "running at 3.3 percent" in line.sentence
        assert line.from_observed == line.to_observed

    def test_a_series_too_short_for_the_window_is_reported_not_computed(self):
        result = self.make([("2025-08-01", 100.0), ("2026-08-01", 112.5)])
        found = macro.headwind([result], months=36, today=date(2026, 9, 9))
        assert found.lines == []
        assert "cannot be computed" in found.unavailable[0]

    def test_with_nothing_available_the_paragraph_is_the_status_line(self):
        found = macro.headwind([macro.SeriesResult(
            definition=FRED_SERIES, error="FRED_API_KEY is not set")])
        assert found.configured is False
        assert "Macro context:" in found.paragraph()
        assert "FRED_API_KEY" in found.unavailable[0]

    def test_the_paragraph_can_only_contain_figures_the_series_produced(self):
        result = self.make([("2023-08-01", 100.0), ("2026-08-01", 112.5)])
        found = macro.headwind([result], months=36, today=date(2026, 9, 9))
        assert 12.5 in found.figures() and 36.0 in found.figures()
        assert 4.2 not in found.figures()

    def test_the_window_is_thirty_six_months_by_default(self):
        assert macro.HEADWIND_MONTHS == 36
        assert macro.headwind([]).window_months == 36


class TestSeriesDefinitions:
    def test_every_shipped_series_has_a_stable_id_and_an_openable_page(self):
        for definition in macro.NATIONAL:
            assert definition.series_id and definition.code
            assert definition.page_url.startswith("https://")

    def test_ids_are_unique(self):
        ids = [d.series_id for d in macro.NATIONAL]
        assert len(ids) == len(set(ids))

    def test_a_state_series_follows_the_verified_naming(self):
        assert macro.state_series("in").code == "INMFG"
        assert macro.state_series("OH").series_id == "employment.manufacturing.oh"

    def test_something_that_is_not_a_state_code_is_refused(self):
        with pytest.raises(ValueError, match="two letters"):
            macro.state_series("Indiana")

    def test_the_absence_of_a_provincial_series_is_recorded(self):
        # A national figure standing in for a provincial one would be a
        # fabrication with a real source attached, which is the worst kind.
        assert "Statistics Canada" in macro.NO_PROVINCE_SERIES
