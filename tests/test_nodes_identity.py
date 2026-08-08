"""Tests for the normalize_identity node and the geo lookup behind it."""

from __future__ import annotations

import asyncio

import pytest

from lib.geo import (
    COUNTY_DRIVE_MINUTES,
    canonical_county,
    drive_minutes_from_muncie,
    within_drive_radius,
)
from lib.nodes import RunContext
from tests.conftest import FakeClient
from tools.harvester.nodes.identity import (
    NormalizeIdentity,
    name_is_ambiguous,
    normalize_whitespace_and_case,
    strip_legal_suffix,
)


def no_network(url):
    raise AssertionError("normalize_identity must not fetch anything")


@pytest.fixture
def ctx(settings_nodelay):
    return RunContext(FakeClient(no_network), settings_nodelay)


def run_node(prospect, ctx):
    return asyncio.run(NormalizeIdentity().run(prospect, ctx))


class TestNameNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  Acme   Tool  &  Die  ", "Acme Tool & Die"),
            ("Acme Tool\n& Die", "Acme Tool & Die"),
            ("Accutech Mold & Machine, Inc.", "Accutech Mold & Machine, Inc."),
        ],
    )
    def test_whitespace_is_collapsed(self, raw, expected):
        assert normalize_whitespace_and_case(raw) == expected

    def test_shouted_names_made_of_words_are_title_cased(self):
        assert normalize_whitespace_and_case("BATESVILLE TOOL AND DIE") == "Batesville Tool And Die"

    @pytest.mark.parametrize("name", ["2NNS LLC", "EEMSCO INC", "LOD LLC", "NISCO"])
    def test_coined_all_caps_names_are_left_alone(self, name):
        # Every all-capitals name in the real Conexus listing is one of these:
        # a coined name or an initialism. Title-casing would corrupt it.
        assert normalize_whitespace_and_case(name) == name

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ABC INDUSTRIES INC", "ABC Industries Inc"),
            ("AMPG MANUFACTURING", "AMPG Manufacturing"),
            ("CNC TOOL AND DIE", "CNC Tool And Die"),
        ],
    )
    def test_initialisms_keep_their_capitals(self, raw, expected):
        # A token with no vowel is an initialism, not a shouted word.
        assert normalize_whitespace_and_case(raw) == expected

    def test_mixed_case_names_are_left_alone(self):
        assert normalize_whitespace_and_case("CycleDyne LLC") == "CycleDyne LLC"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Accutech Mold & Machine, Inc.", "Accutech Mold & Machine"),
            ("CycleDyne LLC", "CycleDyne"),
            ("Kirby Risk Corporation", "Kirby Risk"),
            ("S.U.S. Cast Products, Inc", "S.U.S. Cast Products"),
            ("Lively Machine Company Inc.", "Lively Machine"),
            ("34 Lives, PBC", "34 Lives"),
        ],
    )
    def test_legal_suffixes_are_stripped(self, raw, expected):
        assert strip_legal_suffix(raw) == expected

    def test_a_name_with_no_suffix_is_unchanged(self):
        assert strip_legal_suffix("Addman Engineering") == "Addman Engineering"

    @pytest.mark.parametrize(
        "name", ["A & A Sheet Metal/Securall Products", "Foo Manufacturing dba Bar Tool", "AB"]
    )
    def test_ambiguous_names_are_detected(self, name):
        assert name_is_ambiguous(name) is True

    @pytest.mark.parametrize("name", ["Addman Engineering", "CycleDyne LLC", "Kirby Risk Corp."])
    def test_ordinary_names_are_not_flagged(self, name):
        assert name_is_ambiguous(name) is False

    def test_a_slash_in_a_real_name_is_a_deliberate_false_positive(self):
        # '80/20 Inc.' is one real company, but the slash rule flags it. That
        # costs a 30-second human glance; the opposite error costs an hour of
        # research on the wrong company, so the rule errs this way on purpose.
        assert name_is_ambiguous("80/20 Inc.") is True


class TestGeo:
    def test_every_indiana_county_is_present(self):
        assert len(COUNTY_DRIVE_MINUTES) == 92

    @pytest.mark.parametrize(
        ("given", "expected"),
        [("st-joseph", "St. Joseph"), ("dekalb", "DeKalb"), ("Delaware County", "Delaware"),
         ("  marion ", "Marion"), ("LaGrange", "LaGrange")],
    )
    def test_county_spellings_and_slugs_resolve(self, given, expected):
        assert canonical_county(given) == expected

    @pytest.mark.parametrize("given", [None, "", "Cook", "Nonesuch"])
    def test_unrecognised_counties_return_none(self, given):
        assert canonical_county(given) is None
        assert drive_minutes_from_muncie(given) is None
        assert within_drive_radius(given) is False

    def test_muncies_own_county_is_closest(self):
        assert drive_minutes_from_muncie("Delaware") == min(COUNTY_DRIVE_MINUTES.values())

    def test_nearby_counties_are_inside_the_radius(self):
        for county in ("Delaware", "Madison", "Henry", "Randolph", "Marion", "Hamilton"):
            assert within_drive_radius(county) is True

    def test_far_counties_are_outside_the_radius(self):
        for county in ("Lake", "Vanderburgh", "Posey", "Elkhart"):
            assert within_drive_radius(county) is False


class TestNormalizeIdentityNode:
    def test_drive_minutes_is_set_from_the_county(self, ctx):
        result = run_node({"company_name": "Addman Engineering", "county": "Hamilton"}, ctx)
        assert result.prospect_patch["drive_minutes"] == drive_minutes_from_muncie("Hamilton")

    def test_the_drive_estimate_is_recorded_as_a_t4_claim(self, ctx):
        result = run_node({"company_name": "Addman Engineering", "county": "Hamilton"}, ctx)
        claim = result.evidence_patch["identity"]["drive_minutes_from_muncie"]
        assert claim["tier"] == 4, "a centroid estimate must never be presented as fact"
        assert claim["source_url"].startswith("https://")
        assert claim["verified"] is False

    def test_a_county_slug_is_normalised(self, ctx):
        result = run_node({"company_name": "Some Co", "county": "st-joseph"}, ctx)
        assert result.prospect_patch["county"] == "St. Joseph"

    def test_an_unrecognised_county_goes_to_review(self, ctx):
        result = run_node({"company_name": "Some Co", "county": "Nonesuch"}, ctx)
        assert result.prospect_patch["stage"] == "needs_review"
        assert "drive_minutes" not in result.prospect_patch
        assert any("not a recognised Indiana county" in note for note in result.notes)

    def test_a_missing_county_goes_to_review(self, ctx):
        result = run_node({"company_name": "Some Co", "county": None}, ctx)
        assert result.prospect_patch["stage"] == "needs_review"

    def test_an_ambiguous_name_goes_to_review(self, ctx):
        result = run_node(
            {"company_name": "A & A Sheet Metal/Securall Products", "county": "Allen"}, ctx
        )
        assert result.prospect_patch["stage"] == "needs_review"
        assert any("ambiguous" in note for note in result.notes)

    def test_a_clean_record_is_not_flagged(self, ctx):
        result = run_node({"company_name": "Addman Engineering", "county": "Hamilton"}, ctx)
        assert "stage" not in result.prospect_patch

    def test_a_trading_name_is_recorded_separately(self, ctx):
        result = run_node({"company_name": "CycleDyne LLC", "county": "Hendricks"}, ctx)
        assert result.prospect_patch["dba_name"] == "CycleDyne"

    def test_an_existing_dba_is_not_overwritten(self, ctx):
        result = run_node(
            {"company_name": "CycleDyne LLC", "county": "Hendricks", "dba_name": "Cycle"}, ctx
        )
        assert "dba_name" not in result.prospect_patch

    def test_a_missing_city_is_noted_rather_than_invented(self, ctx):
        result = run_node({"company_name": "Addman Engineering", "county": "Hamilton"}, ctx)
        assert any("no city published" in note for note in result.notes)
        assert "city" not in result.prospect_patch

    def test_it_never_promotes_a_human_only_stage(self, ctx):
        result = run_node({"company_name": "X/Y", "county": "Nonesuch"}, ctx)
        assert result.prospect_patch["stage"] == "needs_review"

    def test_a_human_set_stage_is_left_alone(self, ctx):
        result = run_node(
            {"company_name": "X/Y", "county": "Nonesuch", "stage": "passA_done"}, ctx
        )
        assert "stage" not in result.prospect_patch

    def test_running_twice_produces_the_same_result(self, ctx):
        prospect = {"company_name": "BATESVILLE TOOL AND DIE", "county": "Ripley"}
        first = run_node(prospect, ctx)
        settled = {**prospect, **first.prospect_patch}
        second = run_node(settled, ctx)
        assert second.prospect_patch.get("company_name", settled["company_name"]) == (
            settled["company_name"]
        )
        assert second.prospect_patch["drive_minutes"] == first.prospect_patch["drive_minutes"]
