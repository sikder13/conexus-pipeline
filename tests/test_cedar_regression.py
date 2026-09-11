"""Cedar Valley Selections — the wrong-company resolution, kept from returning.

Cedar Valley Selections Inc. of Windsor, Ontario makes pita chips. Its website
resolved to cedar.com, a US healthcare-payments company; four of that company's
executives were read into its file; the summary's coherence verdict caught the
mismatch and withdrew the priority; a routine re-score two days later put the
priority back; and the company then ranked first among those ready to contact.

Every test here runs against `tests/fixtures/cedar/cedar_valley_record.json`,
which is the record exactly as it stood before any of this was repaired.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.fingerprints import full_name_present
from lib.integrity import is_tainted
from tools.harvester.nodes.summary import demotion_for

FIXTURE = Path(__file__).parent / "fixtures" / "cedar" / "cedar_valley_record.json"


@pytest.fixture
def record():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def prospect(record):
    return record["prospect"]


class TestTheStoredRecord:
    """What the fixture actually contains, so a later edit cannot quietly drift."""

    def test_it_is_the_wrong_company(self, prospect):
        assert prospect["company_name"] == "Cedar Valley Selections Inc."
        assert prospect["website"] == "https://www.cedar.com/"

    def test_the_verdict_had_already_found_the_mismatch(self, prospect):
        verdict = prospect["evidence_file"]["summary_verdict"]
        assert verdict["evidence_coherent"] is False
        assert any("pita chip" in issue for issue in verdict["issues"])

    def test_and_it_was_ranked_p1_anyway(self, prospect):
        assert prospect["priority"] == "P1"
        assert prospect["stage"] == "needs_review"


class TestTheVerdictDemotes:
    """1C: the verdict must withdraw the priority, on every adapter and path."""

    def test_the_stored_verdict_demotes_a_p1(self, prospect):
        patch = demotion_for(prospect, prospect["evidence_file"]["summary_verdict"])
        assert patch["priority"] is None
        assert patch["stage"] == "needs_review"
        assert "pita chip" in patch["needs_review_reason"]

    def test_it_demotes_a_p2_too(self, prospect):
        patch = demotion_for({**prospect, "priority": "P2"},
                             prospect["evidence_file"]["summary_verdict"])
        assert patch["priority"] is None

    def test_the_adapter_makes_no_difference(self, prospect):
        verdict = prospect["evidence_file"]["summary_verdict"]
        for adapter in ("canada_gc", "conexus_iedc", "some_future_source"):
            patch = demotion_for({**prospect, "source_adapter": adapter}, verdict)
            assert patch["priority"] is None, adapter

    def test_a_coherent_verdict_changes_nothing(self, prospect):
        assert demotion_for(prospect, {"evidence_coherent": True, "issues": []}) == {}

    def test_it_never_promotes(self, prospect):
        """A verdict may send a file to a human; it may never raise one."""
        patch = demotion_for({**prospect, "priority": "P3"},
                             prospect["evidence_file"]["summary_verdict"])
        assert patch == {}


class TestTheDemotionSurvivesAReScore:
    """1A-b: scoring recomputed the priority and undid the withdrawal."""

    def test_scoring_leaves_a_withdrawn_priority_alone(self, prospect):
        from tools.harvester.nodes.score import STAGES_TO_LEAVE_ALONE
        assert "needs_review" in STAGES_TO_LEAVE_ALONE

    def test_the_demoted_record_is_not_re_promoted(self, prospect):
        import asyncio

        from lib.nodes import RunContext
        from tools.harvester.nodes.score import ScoreNode

        demoted = {**prospect, **demotion_for(
            prospect, prospect["evidence_file"]["summary_verdict"])}
        result = asyncio.run(ScoreNode().run(demoted, RunContext(None, None)))
        assert "priority" not in result.prospect_patch
        assert "priority_set_by" not in result.prospect_patch

    def test_the_score_is_still_recorded(self, prospect):
        import asyncio

        from lib.nodes import RunContext
        from tools.harvester.nodes.score import ScoreNode

        demoted = {**prospect, **demotion_for(
            prospect, prospect["evidence_file"]["summary_verdict"])}
        result = asyncio.run(ScoreNode().run(demoted, RunContext(None, None)))
        assert "signal_score" in result.prospect_patch


class TestTheWrongWebsite:
    """1B: cedar.com must never be constructed, tried, or accepted again."""

    def test_one_token_of_the_name_is_not_the_name(self):
        assert full_name_present("Cedar is a healthcare payments platform.",
                                 "Cedar Valley Selections Inc.") is None


class TestTheQuarantinedPeople:
    """1D: the subject guard tainted them; everything downstream read them anyway."""

    def people(self, prospect):
        return prospect["evidence_file"]["block7_people"]["named_people"]

    def test_every_person_is_tainted(self, prospect):
        assert self.people(prospect)
        assert all(is_tainted(person) for person in self.people(prospect))

    def test_they_belong_to_the_other_company(self, prospect):
        assert any("Greg Feirn" in (p.get("value") or "") for p in self.people(prospect))


class TestNothingQuarantinedIsRendered:
    """1D: the audit check, proven against the record as it actually stood."""

    def swept(self, prospect):
        """Cedar with cedar.com withdrawn, which is what the repair does."""
        from lib.integrity import taint_claims_from_domain, taint_contacts_from_domain
        reason = "read from cedar.com, which is not this company"
        evidence, _ = taint_claims_from_domain(
            prospect["evidence_file"], "cedar.com", reason)
        rows, _ = taint_contacts_from_domain(prospect.get("contacts"), "cedar.com", reason)
        return {**prospect, "evidence_file": evidence, "contacts": rows, "website": None}

    def test_the_check_catches_the_record_as_it_stood(self, prospect):
        from tools.audit import check_no_quarantined_value_is_rendered
        # The people were already tainted in production; the contact rows that
        # repeat them were not, which is exactly how they reached the page.
        result = check_no_quarantined_value_is_rendered([prospect], [])
        assert result.failures, "the pre-repair record must fail this check"
        assert any("Greg Feirn" in f for f in result.failures), result.failures

    def test_the_swept_record_passes(self, prospect):
        from tools.audit import check_no_quarantined_value_is_rendered
        result = check_no_quarantined_value_is_rendered([self.swept(prospect)], [])
        assert not result.failures, result.failures

    def test_the_swept_record_offers_no_way_in(self, prospect):
        from lib import contacts
        assert contacts.contact_paths(self.swept(prospect)) == []
        assert contacts.verified_path(self.swept(prospect)) is False

    def test_a_sendable_artifact_repeating_a_withdrawn_name_fails(self, prospect):
        from tools.audit import check_no_quarantined_value_is_rendered
        artifact = {"id": "a1", "prospect_id": prospect["id"], "kind": "letter",
                    "status": "sendable", "body": "Dear Greg Feirn, I am writing..."}
        result = check_no_quarantined_value_is_rendered([self.swept(prospect)], [artifact])
        assert any("sendable letter" in f for f in result.failures), result.failures


class TestTheScoringFlagFalls:
    """1D: a flag computed from tainted people is itself tainted."""

    def test_the_flag_was_true_on_the_stored_record(self, prospect):
        from lib.evidence import flag_is_true
        assert flag_is_true(prospect["evidence_file"], "named_decision_maker") is True

    def test_recomputing_withdraws_it(self, prospect):
        from lib.evidence import flag_is_true
        from lib.integrity import recompute_derived_flags
        evidence, marked = recompute_derived_flags(
            prospect["evidence_file"], "read from a page about another company")
        assert marked == 1
        assert flag_is_true(evidence, "named_decision_maker") is False

    def test_recomputing_does_not_edit_the_caller_s_evidence(self, prospect):
        from lib.evidence import flag_is_true
        from lib.integrity import recompute_derived_flags
        recompute_derived_flags(prospect["evidence_file"], "whatever")
        assert flag_is_true(prospect["evidence_file"], "named_decision_maker") is True

    def test_a_tainted_flag_never_scores(self, prospect):
        from lib.evidence import flag_is_true
        evidence = {**prospect["evidence_file"]}
        block = dict(evidence["block7_people"])
        flags = dict(block["flags"])
        flags["named_decision_maker"] = {**flags["named_decision_maker"], "tainted": True}
        evidence["block7_people"] = {**block, "flags": flags}
        assert flag_is_true(evidence, "named_decision_maker") is False


class TestGenerationSpendsOnlyOnP1:
    """1E: selectors had drifted apart, and the drift was money."""

    def test_every_selector_reads_one_rule(self):
        from tools.drafter.main import GENERATION_PRIORITIES, writable_now
        assert GENERATION_PRIORITIES == ("P1",)
        assert writable_now({"priority": "P1"}) is True
        assert writable_now({"priority": "P2"}) is False
        assert writable_now({"priority": "P3"}) is False

    def test_a_withdrawn_priority_is_not_written_for(self, prospect):
        """The state the coherence verdict leaves a record in."""
        from tools.drafter.main import writable_now
        demoted = {**prospect, **demotion_for(
            prospect, prospect["evidence_file"]["summary_verdict"])}
        assert demoted["priority"] is None
        assert writable_now(demoted) is False


class TestTheBriefPromptAndTheGateAgree:
    """1E: the prompt taught a word the gate discards drafts for using."""

    def test_the_prompt_bans_every_word_the_gate_rejects(self):
        from tools.drafter.main import JARGON, PROSE_RULE
        banned = PROSE_RULE.lower()
        for word in JARGON:
            assert word in banned, word

    def test_the_prompt_no_longer_uses_the_banned_word_itself(self):
        from tools.drafter.main import PROSE_RULE
        assert "VERBATIM from the prose" not in PROSE_RULE
        assert "WORD FOR WORD from the prose" in PROSE_RULE


class TestTheRenderedCheckIsNotNoisy:
    """A check that cries wolf gets switched off."""

    def test_a_withdrawn_word_inside_a_longer_word_is_not_a_mention(self):
        from tools.audit import _mentions
        assert _mentions("visit electricmotorcoil.com today", "Electric") is False

    def test_but_the_word_itself_is(self):
        from tools.audit import _mentions
        assert _mentions("they run on electric motors", "Electric") is True

    def test_a_withdrawn_url_still_matches(self):
        from tools.audit import _mentions
        assert _mentions("our site is https://future.com and it works",
                         "https://future.com") is True

    def test_a_withdrawn_name_still_matches(self):
        from tools.audit import _mentions
        assert _mentions("dear greg feirn, i am writing", "Greg Feirn") is True


class TestCorroborationIsAboutThePerson:
    """The two-source test asked a question about the file, not the person."""

    def people(self):
        from lib.claims import Tier, make_claim
        gov = ("https://feddev-ontario.canada.ca/en/successes/"
               "family-recipe-national-favourite-cedar-valley-chips-away-success")
        own = "https://cedarvalleyselections.ca/pages/our-family-story"
        return [
            make_claim("Ameen Fadel — Co-founder", Tier.T1, gov),
            make_claim("Ameen Fadel — Founder", Tier.T1, own),
            make_claim("Surria Fadel — Founder", Tier.T1, own),
        ]

    def test_only_claims_about_the_same_person_count(self):
        from lib.persongate import claims_about
        people = self.people()
        assert len(claims_about(people[0], people)) == 2
        assert len(claims_about(people[2], people)) == 1

    def test_the_twice_sourced_person_is_confirmed(self):
        from lib.persongate import check_person, claims_about
        people = self.people()
        verdict = check_person(people[0], "Cedar Valley Selections Inc.",
                               claims_about(people[0], people))
        assert verdict.allowed, verdict.reasons

    def test_the_once_sourced_person_is_not(self):
        """Named once, on a page that also names somebody else twice."""
        from lib.persongate import check_person, claims_about
        people = self.people()
        verdict = check_person(people[2], "Cedar Valley Selections Inc.",
                               claims_about(people[2], people))
        assert not verdict.allowed

    def test_the_whole_pool_would_have_passed_her(self):
        """The bug, pinned: one well-sourced founder vouched for every name."""
        from lib.persongate import check_person
        people = self.people()
        assert check_person(people[2], "Cedar Valley Selections Inc.", people).allowed

    def test_a_second_source_is_written_as_a_second_claim(self):
        from tools.people_search.main import merge_people
        people = self.people()
        merged, added, corroborated = merge_people(people[:1], [people[1]])
        assert (added, corroborated) == (0, 1)
        assert len(merged) == 2

    def test_an_exact_repeat_is_not(self):
        from tools.people_search.main import merge_people
        people = self.people()
        merged, added, corroborated = merge_people(people[:1], [people[0]])
        assert (added, corroborated) == (0, 0)
        assert len(merged) == 1


class TestTheReviewHoldHasAReleasePath:
    """A hold with no release is a file nobody can ever use again."""

    def held(self, reason):
        return {"id": "x", "stage": "needs_review", "needs_review_reason": reason,
                "evidence_file": {}}

    def test_the_coherence_verdict_is_never_lifted_by_machine(self, prospect):
        from tools.release.main import releasable
        ok, why = releasable({**prospect, "needs_review_reason":
                              "summary coherence check failed: pita chip"})
        assert ok is False
        assert "only a person" in why

    def test_an_unrecognised_reason_is_left_alone(self):
        from tools.release.main import releasable
        ok, why = releasable(self.held("something nobody has seen before"))
        assert ok is False
        assert "left for a person" in why

    def test_a_website_hold_lifts_once_the_site_is_trusted(self):
        from tools.release.main import releasable
        held = self.held("website not accepted: example.com carries the name")
        assert releasable(held)[0] is False
        ok, _ = releasable({**held, "website": "https://x.ca", "website_confidence": 75})
        assert ok is True

    def test_an_integrity_hold_holds_while_the_evidence_is_withdrawn(self, prospect):
        from lib.integrity import taint_claims_from_domain
        from tools.release.main import releasable
        evidence, _ = taint_claims_from_domain(
            prospect["evidence_file"], "cedar.com", "not this company")
        held = {**prospect, "evidence_file": evidence,
                "needs_review_reason": "evidence integrity: every block1 claim"}
        assert releasable(held)[0] is False

    def test_and_lifts_once_it_is_repaired(self, prospect):
        """The stored record's own evidence passes, which is the repaired state."""
        from tools.release.main import releasable
        held = {**prospect, "needs_review_reason": "evidence integrity: every block1 claim"}
        ok, why = releasable(held)
        assert ok is True
        assert "passes again" in why

    def test_a_file_with_no_reason_is_not_released(self):
        from tools.release.main import cause_of, releasable
        assert cause_of(None) == "none"
        assert releasable(self.held(None))[0] is False
