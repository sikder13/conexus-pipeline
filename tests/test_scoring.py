"""Tests for lib/scoring.py — the signal score and the P1/P2/P3 bands.

Scoring decides who gets scarce human research time, so every component is
tested in isolation and every priority boundary is pinned. The breakdown shape
is tested against the literal key set documented in migration 001, because a
silent rename there would make stored breakdowns un-recalibratable.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lib.scoring import (
    COMPONENT_WEIGHTS,
    SignalInputs,
    assign_priority,
    compute_score,
)

POSITIVE_COMPONENTS = (
    "clerical_posting",
    "data_gen_tech",
    "case_study",
    "weak_front_door",
    "friction_reviews",
    "decision_maker_found",
    "in_drive_radius",
)
NEGATIVE_COMPONENTS = ("too_big", "status_uncertain")

# The exact key set of the score_breakdown column, per migration 001.
MIGRATION_BREAKDOWN_KEYS = set(POSITIVE_COMPONENTS) | set(NEGATIVE_COMPONENTS)


class TestWeights:
    def test_weights_match_the_migration_breakdown_shape(self):
        assert set(COMPONENT_WEIGHTS) == MIGRATION_BREAKDOWN_KEYS

    def test_positive_signals_are_worth_one(self):
        for component in POSITIVE_COMPONENTS:
            assert COMPONENT_WEIGHTS[component] == 1

    def test_negative_signals_are_worth_minus_one(self):
        for component in NEGATIVE_COMPONENTS:
            assert COMPONENT_WEIGHTS[component] == -1


class TestComputeScore:
    def test_no_signals_scores_zero(self):
        result = compute_score(SignalInputs())
        assert result.total == 0
        assert set(result.breakdown) == MIGRATION_BREAKDOWN_KEYS
        assert set(result.breakdown.values()) == {0}

    @pytest.mark.parametrize("component", sorted(MIGRATION_BREAKDOWN_KEYS))
    def test_each_component_scores_alone(self, component):
        result = compute_score(SignalInputs(**{component: True}))
        expected = COMPONENT_WEIGHTS[component]
        assert result.total == expected
        assert result.breakdown[component] == expected
        others = {key: val for key, val in result.breakdown.items() if key != component}
        assert set(others.values()) == {0}

    def test_breakdown_always_carries_every_component(self):
        # Zeroes are recorded too: the file should show what was checked,
        # not only what fired.
        result = compute_score(SignalInputs(clerical_posting=True))
        assert set(result.breakdown) == MIGRATION_BREAKDOWN_KEYS

    def test_all_positive_signals_score_seven(self):
        result = compute_score(SignalInputs(**dict.fromkeys(POSITIVE_COMPONENTS, True)))
        assert result.total == 7

    def test_penalties_subtract_from_the_total(self):
        signals = SignalInputs(**dict.fromkeys(MIGRATION_BREAKDOWN_KEYS, True))
        result = compute_score(signals)
        assert result.total == 5
        assert result.breakdown["too_big"] == -1
        assert result.breakdown["status_uncertain"] == -1

    def test_penalties_alone_go_negative(self):
        result = compute_score(SignalInputs(too_big=True, status_uncertain=True))
        assert result.total == -2

    def test_total_equals_the_sum_of_the_breakdown(self):
        signals = SignalInputs(
            clerical_posting=True,
            data_gen_tech=True,
            in_drive_radius=True,
            too_big=True,
        )
        result = compute_score(signals)
        assert result.total == sum(result.breakdown.values()) == 2

    def test_signal_inputs_are_frozen(self):
        signals = SignalInputs()
        with pytest.raises(ValidationError):
            signals.clerical_posting = True


class TestAssignPriority:
    def test_p1_needs_four_points_and_a_named_decision_maker(self):
        assert assign_priority(4, True) == "P1"

    def test_high_score_without_a_named_decision_maker_lands_in_p2(self):
        # There is nobody to send the work to yet, so it is research, not outreach.
        assert assign_priority(4, False) == "P2"
        assert assign_priority(9, False) == "P2"

    @pytest.mark.parametrize("score", [5, 6, 7])
    def test_scores_above_four_stay_p1_when_a_decision_maker_is_named(self, score):
        assert assign_priority(score, True) == "P1"

    @pytest.mark.parametrize("has_decision_maker", [True, False])
    @pytest.mark.parametrize("score", [2, 3])
    def test_two_and_three_are_p2_either_way(self, score, has_decision_maker):
        assert assign_priority(score, has_decision_maker) == "P2"

    @pytest.mark.parametrize("has_decision_maker", [True, False])
    @pytest.mark.parametrize("score", [1, 0, -1, -2])
    def test_below_two_is_p3_either_way(self, score, has_decision_maker):
        assert assign_priority(score, has_decision_maker) == "P3"

    def test_the_boundaries_in_order(self):
        # 1 -> P3, 2 -> P2, 3 -> P2, 4 -> P1 (with a decision-maker).
        assert [assign_priority(score, True) for score in (1, 2, 3, 4)] == [
            "P3",
            "P2",
            "P2",
            "P1",
        ]
