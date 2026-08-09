"""Tests for the results report.

The report is what someone reads instead of reading the database, so a wrong
number here is a wrong number in somebody's head. The ranking and the grant
tallies carry real logic; the rest is layout.
"""

from __future__ import annotations

from lib.evidence import BLOCK7_PEOPLE, BLOCK8_FINANCIAL_SCALE
from tools.report import _named_person, contact_tier_table, grant_table, top_table


def prospect(**overrides):
    base = {
        "id": "p1",
        "company_name": "Acme Tool",
        "priority": "P2",
        "signal_score": 2,
        "drive_minutes": 40.0,
        "grant_amount": None,
        "evidence_file": {},
    }
    base.update(overrides)
    return base


def cells(table):
    return [list(column._cells) for column in table.columns]


class TestRanking:
    def test_p1_outranks_a_higher_scoring_p2(self):
        # Priority is the decision; the score is only the tiebreak inside it.
        rows = cells(top_table([
            prospect(id="a", company_name="Low P1", priority="P1", signal_score=3),
            prospect(id="b", company_name="High P2", priority="P2", signal_score=6),
        ]))
        assert rows[1] == ["Low P1", "High P2"]

    def test_score_breaks_ties_inside_a_priority(self):
        rows = cells(top_table([
            prospect(id="a", company_name="Lower", signal_score=2),
            prospect(id="b", company_name="Higher", signal_score=4),
        ]))
        assert rows[1] == ["Higher", "Lower"]

    def test_a_closer_drive_breaks_a_score_tie(self):
        rows = cells(top_table([
            prospect(id="a", company_name="Far", drive_minutes=80.0),
            prospect(id="b", company_name="Near", drive_minutes=10.0),
        ]))
        assert rows[1] == ["Near", "Far"]

    def test_anything_past_ninety_minutes_is_excluded(self):
        rows = cells(top_table([
            prospect(id="a", company_name="Inside", drive_minutes=90.0),
            prospect(id="b", company_name="Outside", drive_minutes=90.1),
        ]))
        assert rows[1] == ["Inside"]

    def test_an_unknown_drive_time_is_excluded_rather_than_assumed_near(self):
        rows = cells(top_table([prospect(id="a", drive_minutes=None)]))
        assert rows[1] == []


class TestNamedPerson:
    def test_it_reads_the_name_and_role(self):
        p = prospect(evidence_file={BLOCK7_PEOPLE: {"named_people": [
            {"name": {"value": "Dale Whitmore"}, "role": {"value": "President"}}
        ]}})
        assert _named_person(p) == "Dale Whitmore, President"

    def test_a_name_with_no_role_still_shows(self):
        p = prospect(evidence_file={BLOCK7_PEOPLE: {"named_people": [
            {"name": {"value": "Dale Whitmore"}}
        ]}})
        assert _named_person(p) == "Dale Whitmore"

    def test_no_people_reads_as_a_dash_not_a_blank(self):
        assert _named_person(prospect()) == "—"


class TestGrantTally:
    def test_award_records_are_counted_by_tier(self):
        rows = cells(grant_table([
            prospect(id="a", grant_amount=90000.0, evidence_file={BLOCK8_FINANCIAL_SCALE: {
                "grant_awards": [
                    {"value": "$90,000 — round 6", "tier": 1},
                    {"value": "$50,000 — round 3", "tier": 2},
                ],
                "grant_award_count": {"value": 2},
            }}),
        ]))
        measures = dict(zip(rows[0], rows[1], strict=True))
        assert measures["award records, tier 1 (programme administrator)"] == "1"
        assert measures["award records, tier 2 (press)"] == "1"
        assert measures["companies with more than one award"] == "1"

    def test_a_disagreement_is_surfaced_not_swallowed(self):
        rows = cells(grant_table([
            prospect(id="a", evidence_file={BLOCK8_FINANCIAL_SCALE: {
                "grant_amount_disagreement": {"value": "two figures", "tier": 4},
            }}),
        ]))
        measures = dict(zip(rows[0], rows[1], strict=True))
        assert measures["amount disagreements recorded"] == "1"

    def test_a_prospect_with_no_grant_block_counts_as_no_coverage(self):
        rows = cells(grant_table([prospect(id="a"), prospect(id="b")]))
        measures = dict(zip(rows[0], rows[1], strict=True))
        assert measures["prospects with a grant amount"] == "0"
        assert measures["coverage"] == "0.0%"


class TestContactTiers:
    def test_an_absent_tier_is_named_rather_than_dropped(self):
        rows = cells(contact_tier_table([prospect()]))
        assert rows[0] == ["unset"]

    def test_the_tier_is_read_from_block7_not_a_column(self):
        # It is our inference from a headcount, so it lives with its evidence.
        p = prospect(evidence_file={BLOCK7_PEOPLE: {
            "contact_tier": {"value": "ops_first", "tier": 4},
        }})
        rows = cells(contact_tier_table([p]))
        assert rows[0] == ["ops_first"]
