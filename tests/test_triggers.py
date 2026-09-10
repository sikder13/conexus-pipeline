"""What makes this week the week, and what is only a fact about our crawler."""

from __future__ import annotations

from datetime import date, timedelta

from lib import triggers
from lib.evidence import BLOCK2_GRANT_FUNDED, BLOCK3_HIRING_SIGNALS, BLOCK8_FINANCIAL_SCALE

TODAY = date(2026, 9, 10)


def role(posted: str | None):
    return {"value": {"title": "Order Entry Coordinator", "posted": posted},
            "tier": 1, "source_url": "https://acme.test/careers",
            "date_checked": "2026-09-10"}


class TestWhatCounts:
    def test_a_dated_posting_is_the_trigger(self):
        row = {"evidence_file": {BLOCK3_HIRING_SIGNALS: {
            "open_roles": [role("2026-08-30"), role(None)]}}}
        found = triggers.trigger_for(row)
        assert found.kind == triggers.POSTING
        assert found.when == date(2026, 8, 30)
        assert "11 days ago" in found.display(TODAY)

    def test_an_undated_posting_is_no_trigger(self):
        row = {"evidence_file": {BLOCK3_HIRING_SIGNALS: {"open_roles": [role(None)]}}}
        assert triggers.trigger_for(row).kind == triggers.NONE

    def test_the_award_year_is_a_trigger_at_year_precision(self):
        found = triggers.trigger_for({"grant_year": 2023})
        assert found.kind == triggers.AWARD
        assert found.precision == triggers.YEAR_ONLY
        assert found.display(TODAY) == "took the award in 2023"

    def test_a_current_year_award_never_reads_as_today(self):
        # Capping the year end at today is right for ordering and wrong to print:
        # "0 days ago" would tell an operator something happened this morning.
        found = triggers.trigger_for({"grant_year": 2026})
        assert "this year" in found.display(TODAY)
        assert "days ago" not in found.display(TODAY)

    def test_press_is_dated_by_its_publisher(self):
        row = {"evidence_file": {BLOCK8_FINANCIAL_SCALE: {
            "announced_investment": {
                "value": "On 2026-07-04 the company announced a $2M line.",
                "tier": 2, "source_url": "https://press.test/a",
                "date_checked": "2026-09-10"}}}}
        found = triggers.trigger_for(row)
        assert found.kind == triggers.NEWS
        assert found.when == date(2026, 7, 4)

    def test_when_we_looked_is_never_the_trigger(self):
        """`date_checked` is a fact about our crawler, not about them."""
        row = {"evidence_file": {BLOCK2_GRANT_FUNDED: {
            "tech_purchased": {"value": "a five-axis mill", "tier": 2,
                               "source_url": "https://press.test/b",
                               "date_checked": date.today().isoformat()}}}}
        assert triggers.trigger_for(row).kind == triggers.NONE

    def test_the_freshest_of_the_three_wins(self):
        row = {"grant_year": 2021, "evidence_file": {BLOCK3_HIRING_SIGNALS: {
            "open_roles": [role("2026-09-01")]}}}
        assert triggers.trigger_for(row).kind == triggers.POSTING

    def test_a_killed_posting_does_not_trigger(self):
        killed = role("2026-09-01")
        killed["killed"] = True
        killed["killed_reason"] = "the page was hijacked"
        row = {"evidence_file": {BLOCK3_HIRING_SIGNALS: {"open_roles": [killed]}}}
        assert triggers.trigger_for(row).kind == triggers.NONE


class TestTheOrder:
    def _rows(self):
        recent = {"company_name": "Recent", "signal_score": 1,
                  "evidence_file": {BLOCK3_HIRING_SIGNALS: {
                      "open_roles": [role((TODAY - timedelta(days=3)).isoformat())]}}}
        older = {"company_name": "Older", "signal_score": 9,
                 "evidence_file": {BLOCK3_HIRING_SIGNALS: {
                     "open_roles": [role((TODAY - timedelta(days=200)).isoformat())]}}}
        undated = {"company_name": "Undated", "signal_score": 12, "evidence_file": {}}
        return [undated, older, recent]

    def test_freshness_beats_score(self):
        names = [r["company_name"] for r in triggers.order(self._rows(), TODAY)]
        assert names == ["Recent", "Older", "Undated"]

    def test_score_breaks_a_tie(self):
        same = (TODAY - timedelta(days=5)).isoformat()
        rows = [
            {"company_name": "Low", "signal_score": 2, "evidence_file": {
                BLOCK3_HIRING_SIGNALS: {"open_roles": [role(same)]}}},
            {"company_name": "High", "signal_score": 8, "evidence_file": {
                BLOCK3_HIRING_SIGNALS: {"open_roles": [role(same)]}}},
        ]
        assert [r["company_name"] for r in triggers.order(rows, TODAY)] == ["High", "Low"]

    def test_a_company_with_nothing_dated_says_so(self):
        assert triggers.trigger_for({}).display(TODAY) == triggers.NO_TRIGGER
