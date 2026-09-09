"""Tests for the per-adapter scoring profiles.

The point of splitting the scale by source is that a component which cannot fire
must not sit in it — the lesson `docs/SCORING.md` records under
`friction_reviews`. So these tests are mostly about absence: that the Canadian
scale does not carry Indiana's drive time, that the Indiana scale is exactly
what it was, and that a source nobody has declared a scale for stops the run
instead of quietly borrowing one.
"""

from __future__ import annotations

import asyncio

import pytest

from lib.claims import Tier
from lib.evidence import (
    BLOCK1_WHAT_THEY_MAKE,
    SCORE_EVIDENCE_KEY,
    SCORE_PROFILE_KEY,
    flag_patch,
    make_claim,
)
from lib.nodes import RunContext
from lib.scoring import (
    CANADA_PROFILE,
    COMPONENT_WEIGHTS,
    CONEXUS_PROFILE,
    P1_MIN_SCORE,
    PROFILES,
    SignalInputs,
    UnknownScoringProfile,
    assign_priority,
    compute_score,
    profile_for,
)
from tests.conftest import FakeClient, FakeResponse
from tools.harvester.nodes.score import ScoreNode, collect_signals

GRANT = "https://open.canada.ca/data/en/dataset/432527ab-7aac-45b5-81d6-7597107a7013"

CANADA_ADDED = ("program_recency", "english_site",
                "purpose_names_data_generating_tech",
                "compliance_regime", "external_tech_engagement")
CANADA_DROPPED = ("in_drive_radius", "case_study", "data_gen_tech")


def block1() -> dict:
    return {BLOCK1_WHAT_THEY_MAKE: {
        "self_description": make_claim("Precision machining", Tier.T1, GRANT)}}


def serve_nothing(url: str) -> FakeResponse:
    return FakeResponse("", 404, url)


class TestProfileShapes:
    def test_the_indiana_scale_is_unchanged(self):
        assert CONEXUS_PROFILE.weights == COMPONENT_WEIGHTS
        assert CONEXUS_PROFILE.p1_min_score == P1_MIN_SCORE
        assert CONEXUS_PROFILE.ceiling == 6

    @pytest.mark.parametrize("component", CANADA_DROPPED)
    def test_canada_drops_the_components_that_cannot_fire_there(self, component):
        assert component not in CANADA_PROFILE.weights

    @pytest.mark.parametrize("component", CANADA_ADDED)
    def test_canada_adds_its_own(self, component):
        assert CANADA_PROFILE.weights[component] == 1

    @pytest.mark.parametrize(
        "component",
        ("clerical_posting", "weak_front_door", "decision_maker_found"),
    )
    def test_the_kept_components_are_kept(self, component):
        assert CANADA_PROFILE.weights[component] == COMPONENT_WEIGHTS[component]

    def test_both_deductions_survive(self):
        assert CANADA_PROFILE.weights["too_big"] == -1
        assert CANADA_PROFILE.weights["status_uncertain"] == -1

    def test_the_p1_rule_is_the_same_on_both_scales(self):
        assert CANADA_PROFILE.p1_min_score == CONEXUS_PROFILE.p1_min_score
        assert CANADA_PROFILE.p2_min_score == CONEXUS_PROFILE.p2_min_score

    def test_neither_scale_claims_to_be_calibrated(self):
        assert not any(p.calibrated for p in PROFILES.values())

    def test_the_canadian_scale_says_it_is_uncalibrated_in_words(self):
        assert "UNCALIBRATED" in CANADA_PROFILE.note

    def test_every_component_of_every_profile_has_a_field_to_read(self):
        fields = set(SignalInputs.model_fields)
        for profile in PROFILES.values():
            assert set(profile.weights) <= fields, profile.adapter_id


class TestProfileLookup:
    def test_each_adapter_finds_its_own(self):
        assert profile_for("conexus_iedc") is CONEXUS_PROFILE
        assert profile_for("canada_gc") is CANADA_PROFILE

    def test_an_absent_adapter_reads_as_the_column_default(self):
        # `source_adapter` is `not null default 'conexus_iedc'` in migration 001,
        # so a row that does not carry the field is an Indiana row.
        assert profile_for(None) is CONEXUS_PROFILE
        assert profile_for("") is CONEXUS_PROFILE

    def test_an_undeclared_adapter_stops_rather_than_borrowing_a_scale(self):
        with pytest.raises(UnknownScoringProfile) as caught:
            profile_for("feddev_ca")
        assert "feddev_ca" in str(caught.value)
        assert "docs/SCORING.md" in str(caught.value)


class TestScoringOnAProfile:
    def test_a_breakdown_holds_only_its_own_profiles_components(self):
        result = compute_score(SignalInputs(), CANADA_PROFILE)
        assert set(result.breakdown) == set(CANADA_PROFILE.weights)
        assert "case_study" not in result.breakdown

    def test_the_same_signals_score_differently_on_the_two_scales(self):
        signals = SignalInputs(in_drive_radius=True, program_recency=True)
        assert compute_score(signals, CONEXUS_PROFILE).total == 1
        assert compute_score(signals, CANADA_PROFILE).total == 1
        assert compute_score(signals, CONEXUS_PROFILE).breakdown["in_drive_radius"] == 1
        assert compute_score(signals, CANADA_PROFILE).breakdown["program_recency"] == 1

    def test_a_canadian_prospect_is_never_penalised_for_being_far_from_muncie(self):
        far = SignalInputs(program_recency=True, english_site=True,
                           compliance_regime=True)
        assert compute_score(far, CANADA_PROFILE).total == 3
        assert assign_priority(3, True, CANADA_PROFILE) == "P1"

    def test_the_priority_rule_is_unchanged(self):
        assert assign_priority(3, True, CANADA_PROFILE) == "P1"
        assert assign_priority(3, False, CANADA_PROFILE) == "P2"
        assert assign_priority(2, True, CANADA_PROFILE) == "P2"
        assert assign_priority(1, True, CANADA_PROFILE) == "P3"


class TestCollectSignals:
    def _canadian(self, **columns) -> dict:
        evidence = block1()
        for flag, value in columns.pop("flags", {}).items():
            for block, claims in flag_patch(flag, value, Tier.T1, GRANT).items():
                evidence.setdefault(block, {}).setdefault("flags", {}).update(
                    claims["flags"])
        return {"id": "c1", "company_name": "Riverbend Machining Ltd.",
                "source_adapter": "canada_gc", "evidence_file": evidence, **columns}

    def test_drive_time_is_not_read_for_a_canadian_prospect(self):
        prospect = self._canadian(drive_minutes=10)
        signals, basis = collect_signals(prospect)
        assert signals.in_drive_radius is False
        assert "in_drive_radius" not in basis

    def test_the_canadian_flags_are_read(self):
        prospect = self._canadian(flags={"program_recency": True,
                                         "english_site": True})
        signals, basis = collect_signals(prospect)
        assert signals.program_recency is True
        assert signals.english_site is True
        assert basis["program_recency"]["source_url"] == GRANT

    def test_status_uncertain_still_applies_in_canada(self):
        prospect = self._canadian(website_confidence=20)
        signals, _ = collect_signals(prospect)
        assert signals.status_uncertain is True


class TestScoreNodeUsesTheProfile:
    def _run(self, prospect, settings):
        ctx = RunContext(FakeClient(serve_nothing), settings)
        return asyncio.run(ScoreNode().run(prospect, ctx))

    def test_a_canadian_prospect_is_scored_on_the_canadian_scale(self, settings_nodelay):
        evidence = block1()
        for flag in ("program_recency", "english_site", "named_decision_maker"):
            for block, claims in flag_patch(flag, True, Tier.T1, GRANT).items():
                evidence.setdefault(block, {}).setdefault("flags", {}).update(
                    claims["flags"])
        result = self._run(
            {"id": "c1", "company_name": "Riverbend Machining Ltd.",
             "source_adapter": "canada_gc", "evidence_file": evidence,
             "stage": "extracted"},
            settings_nodelay,
        )
        breakdown = result.prospect_patch["score_breakdown"]
        assert set(breakdown) == set(CANADA_PROFILE.weights)
        assert result.prospect_patch["signal_score"] == 3
        assert result.prospect_patch["priority"] == "P1"

    def test_the_scale_is_recorded_beside_the_working(self, settings_nodelay):
        result = self._run(
            {"id": "c1", "company_name": "Riverbend Machining Ltd.",
             "source_adapter": "canada_gc", "evidence_file": block1(),
             "stage": "extracted"},
            settings_nodelay,
        )
        profile = result.evidence_patch[SCORE_EVIDENCE_KEY][SCORE_PROFILE_KEY]
        assert profile["adapter"] == "canada_gc"
        assert profile["ceiling"] == 8
        assert profile["calibrated"] is False

    def test_the_run_says_out_loud_that_the_scale_is_uncalibrated(self, settings_nodelay):
        result = self._run(
            {"id": "c1", "company_name": "Riverbend Machining Ltd.",
             "source_adapter": "canada_gc", "evidence_file": block1(),
             "stage": "extracted"},
            settings_nodelay,
        )
        assert any("uncalibrated" in note for note in result.notes)

    def test_an_indiana_prospect_is_unaffected(self, settings_nodelay):
        result = self._run(
            {"id": "i1", "company_name": "Acme Tool", "source_adapter": "conexus_iedc",
             "evidence_file": block1(), "drive_minutes": 30, "stage": "extracted"},
            settings_nodelay,
        )
        breakdown = result.prospect_patch["score_breakdown"]
        assert set(breakdown) == set(COMPONENT_WEIGHTS)
        assert breakdown["in_drive_radius"] == 1
