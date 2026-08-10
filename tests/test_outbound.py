"""Tests for the layers that replaced per-claim human verification.

These gates are load-bearing rather than advisory. Nobody reads the source
before a sentence goes out any more, so a hole here is not a style problem — it
is a false statement arriving at a real company with our name on it.

Each test names the failure it exists to prevent, and several are built from
errors this pipeline has actually made: the `Office Jared McGladdery` contact
that survived a correction, and the six honest manufacturers a substring match
once accused of selling pharmaceuticals.
"""

from __future__ import annotations

import asyncio

import pytest

from lib import canary, claimcheck, persongate
from lib.claims import make_claim
from lib.evidence import BLOCK1_WHAT_THEY_MAKE, BLOCK2_GRANT_FUNDED
from tools.drafter.main import (
    assertable_claims,
    factual_sentences,
    gate_artifact,
    hypothesis_claims,
)
from tools.harvester.nodes.corroborate import (
    corroborate_evidence,
    independent,
    rates_by_block,
    values_agree,
)

CASE_STUDY = "https://conexusindiana.com/case-study/acme/"
PRESS = "https://www.insideindianabusiness.com/articles/round-two"
SITE = "https://acmetool.com/about/"
SITE_TEAM = "https://acmetool.com/team/"


class FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeMessage:
    def __init__(self, text):
        self.content = [FakeBlock(text)]


class FakeMessages:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeMessage(self.reply)


class FakeAnthropic:
    """Stands in for the API. Tests make no network calls."""

    def __init__(self, reply):
        self.messages = FakeMessages(reply)


def claim(value, tier=1, url=SITE, **extra):
    base = make_claim(value, tier, url)
    base.update(extra)
    return base


# ------------------------------------------------------------- corroboration

class TestIndependence:
    def test_two_pages_of_one_site_are_not_independent(self):
        # One organisation saying one thing twice. If that domain is hijacked,
        # it is one attacker saying it twice.
        assert independent(claim("x", url=SITE), claim("x", url=SITE_TEAM)) is False

    def test_different_domains_are_independent(self):
        assert independent(claim("x", url=SITE), claim("x", url=PRESS)) is True

    def test_a_case_study_and_a_press_round_up_are_independent(self):
        assert independent(claim("x", url=CASE_STUDY), claim("x", url=PRESS)) is True


class TestValueAgreement:
    def test_formatting_differences_are_not_conflicts(self):
        assert values_agree("$102,000", "102000") is True

    def test_rounding_in_press_is_not_a_conflict(self):
        assert values_agree("$102,000", "$101,000") is True

    def test_a_genuinely_different_number_is_a_conflict(self):
        assert values_agree("$102,000", "$75,000") is False

    def test_two_unrelated_sentences_are_not_a_conflict(self):
        # Calling these a conflict would bury the real ones.
        assert values_agree("We make molds.", "Founded in 1994.") is None


class TestCorroborateNode:
    def test_agreement_marks_the_claim(self):
        evidence = {BLOCK2_GRANT_FUNDED: {
            "grant_amount": claim("$102,000", url=CASE_STUDY),
        }, BLOCK1_WHAT_THEY_MAKE: {
            "grant_amount": claim("$102,000", url=PRESS),
        }}
        updated, stats = corroborate_evidence(evidence)
        assert stats["corroborated"] == 2
        assert updated[BLOCK2_GRANT_FUNDED]["grant_amount"]["corroborated"] is True

    def test_a_conflict_keeps_both_values(self):
        evidence = {BLOCK2_GRANT_FUNDED: {"grant_amount": claim("$102,000", url=CASE_STUDY)},
                    BLOCK1_WHAT_THEY_MAKE: {"grant_amount": claim("$75,000", url=PRESS)}}
        updated, stats = corroborate_evidence(evidence)
        assert stats["conflicted"] == 2
        assert updated[BLOCK2_GRANT_FUNDED]["grant_amount"]["value"] == "$102,000"
        assert updated[BLOCK1_WHAT_THEY_MAKE]["grant_amount"]["value"] == "$75,000"

    def test_a_conflict_writes_a_discovery_question(self):
        evidence = {BLOCK2_GRANT_FUNDED: {"grant_amount": claim("$102,000", url=CASE_STUDY)},
                    BLOCK1_WHAT_THEY_MAKE: {"grant_amount": claim("$75,000", url=PRESS)}}
        updated, _ = corroborate_evidence(evidence)
        questions = updated["block9_discovery"]
        assert any(q.get("discovery_question") for q in questions.values())
        assert any("disagree" in str(q["value"]) for q in questions.values())

    def test_no_winner_is_chosen_on_conflict(self):
        evidence = {BLOCK2_GRANT_FUNDED: {"grant_amount": claim("$102,000", url=CASE_STUDY)},
                    BLOCK1_WHAT_THEY_MAKE: {"grant_amount": claim("$75,000", url=PRESS)}}
        updated, _ = corroborate_evidence(evidence)
        for block in (BLOCK2_GRANT_FUNDED, BLOCK1_WHAT_THEY_MAKE):
            assert updated[block]["grant_amount"]["conflict"] is True

    def test_single_source_is_a_state_not_a_failure(self):
        evidence = {BLOCK1_WHAT_THEY_MAKE: {"what": claim("We make molds.")}}
        updated, stats = corroborate_evidence(evidence)
        assert stats["single"] == 1
        assert "corroborated" not in updated[BLOCK1_WHAT_THEY_MAKE]["what"]

    def test_rates_by_block_counts_every_state(self):
        evidence = {BLOCK1_WHAT_THEY_MAKE: {
            "a": claim("x", corroborated=True), "b": claim("y"), "c": claim("z", conflict=True),
        }}
        rates = rates_by_block(evidence)[BLOCK1_WHAT_THEY_MAKE]
        assert rates == {"corroborated": 1, "conflict": 1, "single": 1}


# -------------------------------------------------------------- claim checker

class TestClaimChecker:
    def test_an_unreadable_verdict_fails_closed(self):
        # An unparseable reply must never read as approval.
        assert claimcheck._parse("who knows").verdict == "unsupported"

    def test_an_unknown_verdict_fails_closed(self):
        assert claimcheck._parse('{"verdict": "probably"}').verdict == "unsupported"

    def test_a_verbatim_verdict_is_read(self):
        result = claimcheck._parse('{"verdict":"verbatim","reason":"stated","quote":"q"}')
        assert result.verdict == "verbatim" and result.usable_in_outbound

    def test_inferable_is_not_outbound_usable_by_default(self):
        assert claimcheck._parse('{"verdict":"inferable"}').usable_in_outbound is False

    def test_no_source_text_is_unsupported(self):
        result = asyncio.run(claimcheck.check_claim(claim("x"), "", client=object()))
        assert result.verdict == "unsupported"

    def test_the_prompt_tells_the_model_it_is_auditing(self):
        # The framing is doing real work: "verify this" produces verification.
        assert "find its claims" in claimcheck.SYSTEM_PROMPT.lower()
        assert "wrong" in claimcheck.SYSTEM_PROMPT.lower()

    def test_it_catches_the_mcgladdery_fabrication(self):
        """The real case, end to end with a stubbed model.

        'Office Jared McGladdery' was written into a live P2 record as a
        decision-maker, assembled out of page chrome — the source page says
        'Our Office' and never names a Jared at all. Tests here make no network
        calls, so the model's reply is stubbed; what is under test is that an
        'unsupported' verdict actually reaches the claim and bars it.
        """
        source = (
            "Copper Mountain Technologies designs vector network analyzers in "
            "Indianapolis. Our Office. Careers. Contact us for support."
        )
        assert "Jared" not in source, "the fixture must not contain the fabricated name"
        person = claim("Office Jared McGladdery — Director", url=SITE)

        prompt = claimcheck.build_prompt(person, source, "block7_people.named_people[0]")
        assert "Jared McGladdery" in prompt and "vector network analyzers" in prompt

        stub = FakeAnthropic(
            '{"verdict":"unsupported","reason":"the text never names this person",'
            '"quote":""}'
        )
        result = asyncio.run(claimcheck.check_claim(person, source, client=stub))
        assert result.verdict == "unsupported"

        checked = claimcheck.apply_verdict(person, result)
        assert claimcheck.is_barred(checked) is True

        gate = persongate.check_person(checked, "Copper Mountain", [checked])
        assert gate.allowed is False
        assert gate.salutation == "the owner or president of Copper Mountain"

    def test_a_barred_claim_is_recognised(self):
        assert claimcheck.is_barred(claim("x", claimcheck="unsupported")) is True
        assert claimcheck.is_barred(claim("x", tainted=True)) is True
        assert claimcheck.is_barred(claim("x", killed=True)) is True

    def test_an_unchecked_claim_is_not_barred_by_absence(self):
        # Conflating "not yet checked" with "failed" would silently bar
        # everything the checker has not reached.
        assert claimcheck.is_barred(claim("x")) is False

    def test_the_verdict_is_stored_flat_on_the_claim(self):
        result = claimcheck.CheckResult(verdict="verbatim", reason="stated", quote="q")
        checked = claimcheck.apply_verdict(claim("x"), result)
        assert checked["claimcheck"] == "verbatim"
        assert checked["claimcheck_quote"] == "q"
        assert checked["value"] == "x", "the claim itself is untouched"


# --------------------------------------------------------------- person gate

class TestPersonGate:
    def two_sources(self):
        return [
            claim("Dale Whitmore — President", url=SITE),
            claim("Dale Whitmore — President", url=CASE_STUDY),
        ]

    def test_two_independent_sources_pass(self):
        pool = self.two_sources()
        result = persongate.check_person(pool[0], "Accutech Mold", pool)
        assert result.allowed is True
        assert result.name == "Dale Whitmore"

    def test_one_t1_source_with_a_verbatim_check_passes(self):
        only = claim("Dale Whitmore — President", url=SITE, claimcheck="verbatim")
        result = persongate.check_person(only, "Accutech Mold", [only])
        assert result.allowed is True

    def test_one_source_without_a_check_fails(self):
        only = claim("Dale Whitmore — President", url=SITE)
        result = persongate.check_person(only, "Accutech Mold", [only])
        assert result.allowed is False
        assert any("checker has not run" in r for r in result.reasons)

    def test_two_pages_of_one_site_do_not_count_as_two_sources(self):
        pool = [claim("Dale Whitmore — President", url=SITE),
                claim("Dale Whitmore — President", url=SITE_TEAM)]
        assert persongate.check_person(pool[0], "Accutech Mold", pool).allowed is False

    def test_a_fabricated_name_fails_however_many_sources(self):
        pool = [claim("Office Jared McGladdery — Director", url=SITE),
                claim("Office Jared McGladdery — Director", url=PRESS)]
        result = persongate.check_person(pool[0], "Copper Mountain", pool)
        assert result.allowed is False
        assert any("does not parse as a person" in r for r in result.reasons)

    def test_a_non_role_fails(self):
        pool = [claim("Dale Whitmore — Enthusiast", url=SITE),
                claim("Dale Whitmore — Enthusiast", url=PRESS)]
        result = persongate.check_person(pool[0], "Accutech", pool)
        assert result.allowed is False
        assert any("job title" in r for r in result.reasons)

    def test_a_failure_falls_back_to_the_role(self):
        only = claim("Dale Whitmore — President", url=SITE)
        result = persongate.check_person(only, "Accutech Mold", [only])
        assert result.salutation == "the owner or president of Accutech Mold"
        assert result.name is None

    def test_a_tainted_person_claim_fails(self):
        pool = [claim("Dale Whitmore — President", url=SITE, tainted=True),
                claim("Dale Whitmore — President", url=PRESS)]
        assert persongate.check_person(pool[0], "Accutech", pool).allowed is False

    def test_there_is_no_override(self):
        import inspect
        signature = inspect.signature(persongate.check_person)
        assert not any(
            word in p for p in signature.parameters for word in ("force", "override", "skip")
        )


# -------------------------------------------------------------- outbound gate

ALLOWED = {"block1_what_they_make.what", "block2_grant_funded.grant_amount",
           "block8_financial_scale.headcount"}
HYPOTHESES = {"block8_financial_scale.headcount"}


class TestOutboundGate:
    def test_a_clean_artifact_passes(self):
        text = ("You make injection molds in Muncie [block1_what_they_make.what]. "
                "The 2021 grant was $102,000 [block2_grant_funded.grant_amount].")
        verdict = gate_artifact(text, ALLOWED, HYPOTHESES, True, "Dale Whitmore")
        assert verdict["passed"] is True
        assert verdict["sentences"] == 2

    def test_a_number_with_no_source_blocks_it(self):
        # The exact failure this gate exists for: a figure that looks sourced
        # because everything around it is.
        text = ("You make molds [block1_what_they_make.what]. "
                "That is roughly $40,000 a year in rework.")
        verdict = gate_artifact(text, ALLOWED, HYPOTHESES, True, None)
        assert verdict["passed"] is False
        assert any("number with no source" in f for f in verdict["failures"])

    def test_a_second_hypothesis_blocks_it(self):
        text = ("We think quoting takes a while [block8_financial_scale.headcount]. "
                "We suspect scheduling does too [block1_what_they_make.what].")
        verdict = gate_artifact(text, ALLOWED, HYPOTHESES, True, None)
        assert verdict["passed"] is False
        assert any("hypothes" in f for f in verdict["failures"])

    def test_an_ungated_name_blocks_it(self):
        text = "Dale mentioned the new line runs well [block1_what_they_make.what]."
        verdict = gate_artifact(text, ALLOWED, HYPOTHESES, False, "Dale Whitmore")
        assert verdict["passed"] is False
        assert any("person gate" in f for f in verdict["failures"])

    def test_an_unmappable_sentence_blocks_it(self):
        text = "Your competitors are already automating their estimating desks."
        verdict = gate_artifact(text, ALLOWED, HYPOTHESES, True, None)
        assert verdict["passed"] is False
        assert any("unmappable" in f for f in verdict["failures"])

    def test_a_citation_to_a_non_qualifying_claim_blocks_it(self):
        text = "You run three shifts [block4_digital_front_door.shifts]."
        verdict = gate_artifact(text, ALLOWED, HYPOTHESES, True, None)
        assert verdict["passed"] is False
        assert any("does not qualify" in f for f in verdict["failures"])

    def test_questions_need_no_citation(self):
        text = "How many quotes go out in a week? Would a short call be useful?"
        assert gate_artifact(text, ALLOWED, HYPOTHESES, True, None)["passed"] is True

    def test_the_map_records_every_sentence(self):
        text = ("You make molds [block1_what_they_make.what]. "
                "The grant was $102,000 [block2_grant_funded.grant_amount].")
        verdict = gate_artifact(text, ALLOWED, HYPOTHESES, True, None)
        assert len(verdict["map"]) == 2
        assert verdict["map"][0]["claims"] == ["block1_what_they_make.what"]

    def test_headings_are_not_treated_as_claims(self):
        assert factual_sentences("THREE FINDINGS") == []


class TestClaimSelection:
    def prospect(self, **claims):
        return {"id": "p1", "evidence_file": {BLOCK1_WHAT_THEY_MAKE: claims}}

    def test_only_t1_corroborated_or_verbatim_is_assertable(self):
        row = self.prospect(
            good=claim("a", corroborated=True),
            checked=claim("b", claimcheck="verbatim"),
            bare=claim("c"),
            press=claim("d", tier=2, corroborated=True),
        )
        paths = {p for p, _ in assertable_claims(row, ("verbatim",))}
        assert paths == {f"{BLOCK1_WHAT_THEY_MAKE}.good", f"{BLOCK1_WHAT_THEY_MAKE}.checked"}

    def test_inferable_is_excluded_in_the_conservative_phase(self):
        row = self.prospect(maybe=claim("a", claimcheck="inferable"))
        assert assertable_claims(row, ("verbatim",)) == []

    def test_inferable_becomes_assertable_once_opened(self):
        row = self.prospect(maybe=claim("a", claimcheck="inferable"))
        assert len(assertable_claims(row, ("verbatim", "inferable"))) == 1

    def test_a_tainted_claim_is_never_assertable(self):
        row = self.prospect(bad=claim("a", corroborated=True, tainted=True))
        assert assertable_claims(row, ("verbatim",)) == []

    def test_an_unsupported_claim_is_never_assertable(self):
        row = self.prospect(bad=claim("a", corroborated=True, claimcheck="unsupported"))
        assert assertable_claims(row, ("verbatim",)) == []

    def test_hypotheses_are_the_t4_claims(self):
        row = self.prospect(guess=claim("a", tier=4), fact=claim("b"))
        assert len(hypothesis_claims(row)) == 1


# -------------------------------------------------------------------- canary

class FakeCanaryDB:
    def __init__(self, **overrides):
        self.row = {
            "halted": False, "halt_reason": None, "halted_at": None,
            "batches_sent": 0, "factual_corrections": 0, "inferable_eligible": False,
        }
        self.row.update(overrides)

    def canary_row(self):
        return dict(self.row)

    def update_canary(self, data):
        self.row.update(data)
        return dict(self.row)


@pytest.fixture
def fake_canary(monkeypatch):
    fake = FakeCanaryDB()
    monkeypatch.setattr(canary, "db", fake, raising=False)
    import lib.db as real_db
    monkeypatch.setattr(real_db, "canary_row", fake.canary_row)
    monkeypatch.setattr(real_db, "update_canary", fake.update_canary)
    return fake


class TestCanary:
    def test_a_clean_state_permits_sending(self, fake_canary):
        assert canary.assert_sendable().halted is False

    def test_a_halt_blocks_a_hypothetical_send_path(self, fake_canary):
        canary.halt("a prospect said we named the wrong person")

        def hypothetical_send(recipients):
            canary.assert_sendable()
            return f"sent to {len(recipients)}"

        with pytest.raises(canary.SendHalted) as caught:
            hypothetical_send(["a@b.test"])
        assert "wrong person" in str(caught.value)

    def test_a_factual_correction_halts_everything(self, fake_canary):
        state = canary.record_factual_correction("we don't do injection moulding")
        assert state.halted is True
        assert state.factual_corrections == 1

    def test_one_correction_is_enough(self, fake_canary):
        # Not a threshold, not a rate. A sourced claim was wrong, so every other
        # unread assertion is suspect.
        assert canary.record_factual_correction("wrong year").halted is True

    def test_an_estimate_correction_does_not_halt(self, fake_canary):
        # We invite these. Treating them as failures pushes the drafter vaguer.
        assert canary.record_estimate_correction("closer to 40 a month").halted is False

    def test_the_conservative_phase_allows_only_verbatim(self, fake_canary):
        assert canary.read_state().allowed_verdicts() == ("verbatim",)

    def test_two_clean_batches_open_inferable(self, fake_canary):
        canary.record_batch_sent(clean=True)
        state = canary.record_batch_sent(clean=True)
        assert state.inferable_eligible is True
        assert state.allowed_verdicts() == ("verbatim", "inferable")

    def test_a_dirty_batch_does_not_open_inferable(self, fake_canary):
        canary.record_batch_sent(clean=True)
        canary.record_batch_sent(clean=False)
        assert canary.read_state().inferable_eligible is False

    def test_resuming_requires_a_note(self, fake_canary):
        canary.halt("something went wrong")
        with pytest.raises(ValueError):
            canary.resume("")

    def test_resuming_with_a_note_lifts_the_halt(self, fake_canary):
        canary.halt("something went wrong")
        state = canary.resume("re-checked every block7 claim by hand")
        assert state.halted is False
        assert "re-checked" in (state.halt_reason or "")

    def test_assert_sendable_takes_no_bypass_argument(self):
        import inspect
        params = inspect.signature(canary.assert_sendable).parameters
        assert not any(w in p for p in params for w in ("force", "override", "bypass"))


class TestRoiPatterns:
    def test_every_pattern_states_when_it_must_not_be_used(self):
        from lib.roi_patterns import PATTERNS
        for pattern in PATTERNS:
            assert pattern.refuses_when.strip(), f"{pattern.key} has no refusal condition"

    def test_every_pattern_names_what_must_be_observed(self):
        from lib.roi_patterns import PATTERNS
        for pattern in PATTERNS:
            assert pattern.variables, f"{pattern.key} needs no evidence — that is a menu item"

    def test_the_prompt_block_carries_the_refusals(self):
        from lib.roi_patterns import as_prompt_block
        assert "DO NOT USE WHEN" in as_prompt_block()

    def test_the_library_is_not_a_service_list(self):
        # Every entry must lead with a friction, not an offering.
        from lib.roi_patterns import PATTERNS
        for pattern in PATTERNS:
            assert pattern.friction and pattern.math


class TestGateFalsePositives:
    """The gate must block false claims, not correct boilerplate.

    Both of these blocked every artifact in the first live run. A gate that
    refuses everything protects nothing, because it will be switched off.
    """

    def test_the_can_spam_signature_does_not_trip_the_number_check(self):
        text = ("You make molds [block1_what_they_make.what].\n\n--\n"
                "Udaay Sikder\nNahl Technologies\n6902 Challenge Ln, Indianapolis IN 46250\n"
                "Reply STOP and I will not contact you again.")
        assert gate_artifact(text, ALLOWED, HYPOTHESES, True, None)["passed"] is True

    def test_a_heading_numeral_is_not_a_quantity(self):
        text = "FINDING 1 — you make injection molds [block1_what_they_make.what]."
        assert gate_artifact(text, ALLOWED, HYPOTHESES, True, None)["passed"] is True

    def test_a_real_quantity_still_needs_a_source(self):
        for quantity in ("$8,750 a day", "roughly 3,200 hours", "about 45% of orders"):
            text = f"That is {quantity} of avoidable work."
            verdict = gate_artifact(text, ALLOWED, HYPOTHESES, True, None)
            assert verdict["passed"] is False, f"{quantity!r} should need a source"

    def test_an_invented_external_source_is_still_blocked(self):
        # The real failure: the model cited 'BLS May 2023 SOC 19-4099' and an
        # ASTM standard, neither of which is anywhere in the evidence file.
        text = ("Prep runs $35.00/hour [BLS May 2023 Occupational Employment Survey].")
        assert gate_artifact(text, ALLOWED, HYPOTHESES, True, None)["passed"] is False
