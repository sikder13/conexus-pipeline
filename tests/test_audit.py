"""Tests for the invariant audit.

Each check gets a clean case and a broken one. An audit that cannot fail is
worse than no audit: it is a green light with nothing behind it, which is the
exact failure mode the tool exists to catch.
"""

from __future__ import annotations

from lib.evidence import BLOCK7_PEOPLE
from tools.audit import (
    check_claim_shape,
    check_named_people_are_people,
    check_no_human_only_stage,
    check_no_unreachable_work_state,
    check_p1_has_a_human,
    check_queue_reconciles,
    check_score_arithmetic,
    check_score_evidence_matches,
    check_source_urls,
)


def claim(value="x", tier=1, url="https://example.test/a", checked="2026-08-09"):
    return {
        "value": value, "tier": tier, "source_url": url,
        "date_checked": checked, "verified": False, "verified_at": None,
    }


def prospect(pid="p1", **columns):
    base = {"id": pid, "company_name": f"Company {pid}", "stage": "extracted", "evidence_file": {}}
    return {**base, **columns}


def item(pid="p1", node="score", status="pending", skip_kind=None, attempts=0):
    return {
        "id": f"i-{pid}-{node}", "prospect_id": pid, "node_name": node,
        "status": status, "skip_kind": skip_kind, "attempts": attempts,
    }


class TestWorkQueueReachable:
    def test_ordinary_states_pass(self):
        items = [item(status="pending"), item(status="done"), item(status="skipped")]
        assert check_no_unreachable_work_state(items).passed

    def test_a_permanent_skip_is_still_reachable(self):
        stranded = [item(status="skipped", skip_kind="permanent")]
        assert check_no_unreachable_work_state(stranded).passed


class TestClaimShape:
    def test_a_well_formed_claim_passes(self):
        assert check_claim_shape([prospect(evidence_file={"b": {"k": claim()}})]).passed

    def test_a_claim_with_no_tier_fails(self):
        bad = {"value": "x", "source_url": "https://a.test", "date_checked": "2026-08-09"}
        result = check_claim_shape([prospect(evidence_file={"b": {"k": bad}})])
        assert not result.passed
        assert "missing tier" in result.failures[0]

    def test_an_out_of_range_tier_fails(self):
        result = check_claim_shape([prospect(evidence_file={"b": {"k": claim(tier=7)}})])
        assert not result.passed
        assert "not 1-4" in result.failures[0]

    def test_an_unparseable_date_fails(self):
        result = check_claim_shape([prospect(evidence_file={"b": {"k": claim(checked="soon")}})])
        assert not result.passed
        assert "does not parse" in result.failures[0]

    def test_claims_nested_in_lists_are_inspected(self):
        result = check_claim_shape([prospect(evidence_file={"b": {"k": [claim(), claim(tier=9)]}})])
        assert not result.passed
        assert "[1]" in result.failures[0]


class TestSourceUrls:
    def test_an_http_url_passes(self):
        assert check_source_urls([prospect(evidence_file={"b": {"k": claim()}})]).passed

    def test_a_bare_string_fails(self):
        bad = prospect(evidence_file={"b": {"k": claim(url="example.test")}})
        result = check_source_urls([bad])
        assert not result.passed
        assert "not an openable URL" in result.failures[0]


class TestP1HasAHuman:
    def _p1(self, block7):
        return prospect(priority="P1", evidence_file={"block7_people": block7})

    def test_a_p1_with_a_named_person_passes(self):
        good = {"named_people": [claim("Dale Whitmore — President")],
                "flags": {"named_decision_maker": claim(True)}}
        assert check_p1_has_a_human([self._p1(good)]).passed

    def test_a_p1_with_nobody_fails(self):
        result = check_p1_has_a_human([self._p1({})])
        assert not result.passed
        assert "no named" in result.failures[0]

    def test_non_p1_prospects_are_not_inspected(self):
        assert check_p1_has_a_human([prospect(priority="P2")]).inspected == 0


class TestStageDiscipline:
    def test_allowed_stages_pass(self):
        assert check_no_human_only_stage([prospect(stage="passA_done")]).passed

    def test_a_human_only_stage_fails(self):
        result = check_no_human_only_stage([prospect(stage="verified")])
        assert not result.passed
        assert "only a human may set" in result.failures[0]


class TestQueueReconciles:
    def test_an_orphan_item_fails(self):
        result = check_queue_reconciles([prospect("p1")], [item("ghost", node="score")])
        assert not result.passed
        assert any("missing prospect" in f for f in result.failures)

    def test_a_duplicated_pair_fails(self):
        items = [item("p1", "score"), item("p1", "score")]
        result = check_queue_reconciles([prospect("p1")], items)
        assert any("duplicated" in f for f in result.failures)

    def test_a_short_count_fails(self):
        result = check_queue_reconciles([prospect("p1"), prospect("p2")], [item("p1", "score")])
        assert any("score" in f for f in result.failures)


class TestScoreArithmetic:
    def test_a_consistent_score_passes(self):
        p = prospect(signal_score=2, score_breakdown={"a": 1, "b": 1, "c": 0})
        assert check_score_arithmetic([p]).passed

    def test_a_score_that_disagrees_with_its_breakdown_fails(self):
        p = prospect(signal_score=5, score_breakdown={"a": 1, "b": 1})
        result = check_score_arithmetic([p])
        assert not result.passed
        assert "sums to 2" in result.failures[0]

    def test_an_unscored_prospect_is_skipped(self):
        assert check_score_arithmetic([prospect()]).inspected == 0


class TestScoreTraceability:
    def test_matching_evidence_passes(self):
        p = prospect(
            score_breakdown={"case_study": 1, "too_big": 0},
            evidence_file={"score_evidence": {"case_study": {"points": 1}}},
        )
        assert check_score_evidence_matches([p]).passed

    def test_a_scoring_component_with_no_justification_fails(self):
        p = prospect(score_breakdown={"case_study": 1}, evidence_file={"score_evidence": {}})
        result = check_score_evidence_matches([p])
        assert not result.passed
        assert "unjustified" in result.failures[0]

    def test_a_stale_justification_fails(self):
        # The exact shape of an earlier bug: a component dropped to zero but its
        # reason stayed behind in the evidence file.
        p = prospect(
            score_breakdown={"case_study": 0},
            evidence_file={"score_evidence": {"case_study": {"points": 1}}},
        )
        result = check_score_evidence_matches([p])
        assert not result.passed
        assert "stale" in result.failures[0]


class TestNamedContactsAreVerified:
    """Presence is not personhood.

    "P1 has a named human" passed while nine records carried a contact who does
    not exist. This check re-applies the person-name rules to what is stored, so
    a name written by a looser earlier extractor is caught rather than trusted.
    """

    def _prospect(self, value):
        return {
            "id": "p1",
            "company_name": "Catalyst Product Development Inc.",
            "evidence_file": {BLOCK7_PEOPLE: {"named_people": [
                {"value": value, "tier": 1, "source_url": "https://x.test",
                 "date_checked": "2026-08-09"}
            ]}},
        }

    def test_another_companys_name_is_caught(self):
        result = check_named_people_are_people([self._prospect("Insects Limited — Vice President")])
        assert not result.passed
        assert "is not a person" in result.failures[0]

    def test_page_chrome_is_caught(self):
        result = check_named_people_are_people([self._prospect("Email Phone Bio — Ceo")])
        assert not result.passed

    def test_a_placeholder_is_caught(self):
        result = check_named_people_are_people([self._prospect("John Doe — Ceo")])
        assert not result.passed

    def test_a_real_person_passes(self):
        result = check_named_people_are_people([self._prospect("Jeffrey White — Chief Executive")])
        assert result.passed
        assert result.inspected == 1

    def test_a_prospect_with_no_contacts_is_not_a_failure(self):
        result = check_named_people_are_people([{"id": "p1", "company_name": "X",
                                                 "evidence_file": {}}])
        assert result.passed
        assert result.inspected == 0
