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
import json
import re

import pytest

from lib import canary, claimcheck, persongate
from lib.claims import make_claim
from lib.evidence import BLOCK1_WHAT_THEY_MAKE, BLOCK2_GRANT_FUNDED
from tools.drafter.main import (
    ProseRejected,
    Spend,
    _sentences_of,
    assertable_claims,
    below_floor,
    factual_sentences,
    gate_artifact,
    gate_prose,
    hypothesis_claims,
    opportunity_sections,
    parse_delimited,
    parse_sections,
    section_named,
    validate_prose,
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


class TestProseValidation:
    """The generator used to emit notation instead of writing.

    Headings and bracketed ids with little text between them starved the gate of
    sentences to map, and left the leave-behind with nothing once notation was
    stripped. These rules are the fix, checked after generation rather than
    hoped for in the prompt.
    """

    GOOD = ("You run a job shop in Muncie building injection molds. The state's "
            "announcement of your grant records an award of $102,000 in 2021.")

    def test_real_prose_passes(self):
        validate_prose(self.GOOD, "x")

    def test_bracket_notation_is_rejected(self):
        with pytest.raises(ProseRejected) as caught:
            validate_prose("You build molds [block1_what_they_make.self_description]. "
                           "That is a real business.", "x")
        assert "bracket notation" in str(caught.value)

    @pytest.mark.parametrize("word", ["tier", "corroborated", "verdict", "tainted"])
    def test_internal_vocabulary_is_rejected(self, word):
        with pytest.raises(ProseRejected) as caught:
            validate_prose(f"You build molds. The {word} is good here.", "x")
        assert "internal vocabulary" in str(caught.value)

    def test_a_single_sentence_is_rejected(self):
        with pytest.raises(ProseRejected) as caught:
            validate_prose("You build injection molds in Muncie.", "x")
        assert "under two sentences" in str(caught.value)

    def test_empty_prose_is_rejected(self):
        with pytest.raises(ProseRejected):
            validate_prose("   ", "x")


class TestPromptExamples:
    """The worked example outranks the sentence naming the sections.

    A single shared example reading 'opportunity=1' made the email step emit
    opportunity blocks in six of ten attempts across a live batch, and once
    produced 'opportunity="subject"' — the model splitting the difference
    between an example and an instruction that disagreed. Every prompt now
    shows a label it actually wants.
    """

    def test_the_email_prompt_never_shows_an_opportunity_label(self):
        from tools.drafter.main import EMAIL_SYSTEM
        assert "opportunity" not in EMAIL_SYSTEM
        assert "<<<PROSE subject>>>" in EMAIL_SYSTEM

    def test_the_analysis_prompt_shows_an_opportunity_label(self):
        from tools.drafter.main import STEP2_SYSTEM
        assert "<<<PROSE opportunity=1>>>" in STEP2_SYSTEM

    def test_every_prompt_illustrates_a_section_it_asks_for(self):
        from tools.drafter.main import EMAIL_SYSTEM, STEP2_SYSTEM
        # Concrete labels only — "<<<PROSE ...>>>" is the instructions talking
        # about the shape in general, not illustrating a section.
        shown = re.compile(r"<<<PROSE ([a-z][a-z0-9_]*(?:=\d+)?)>>>")
        for name, prompt in (("email", EMAIL_SYSTEM), ("step2", STEP2_SYSTEM)):
            for label in shown.findall(prompt):
                assert label in prompt.replace(f"<<<PROSE {label}>>>", ""), (
                    f"{name} illustrates {label!r} but never asks for it")

    def test_the_map_rule_asks_for_short_keys(self):
        # Full-sentence keys broke a live map 3000 characters into one line.
        from tools.drafter.main import PROSE_RULE
        assert "six to ten words" in PROSE_RULE

    def test_a_prefix_key_still_satisfies_the_gate(self):
        # Short keys are only safe because the gate matches by containment.
        prose = "You build injection molds in Muncie for automotive customers."
        smap = [{"text": "You build injection molds",
                 "claims": ["block1_what_they_make.what"]}]
        verdict = gate_prose(prose + " It is a real shop here.", smap,
                             {"block1_what_they_make.what"}, set(), True, None)
        assert verdict["map"][0]["claims"] == ["block1_what_they_make.what"]


class TestSpend:
    """A batch that regenerates costs more than the happy path. Count it."""

    class FakeUsage:
        def __init__(self, input_tokens, output_tokens):
            self.input_tokens = input_tokens
            self.output_tokens = output_tokens

    def test_it_totals_calls_and_tokens(self):
        spend = Spend()
        spend.record(self.FakeUsage(1000, 500))
        spend.record(self.FakeUsage(2000, 250))
        assert (spend.calls, spend.input_tokens, spend.output_tokens) == (2, 3000, 750)

    def test_it_prices_input_and_output_separately(self):
        # Output costs five times input at this model's list price; a total
        # that ignores that under-reports every drafting run.
        spend = Spend()
        spend.record(self.FakeUsage(1_000_000, 1_000_000))
        assert spend.dollars == pytest.approx(18.00)

    def test_a_response_with_no_usage_still_counts_the_call(self):
        spend = Spend()
        spend.record(object())
        assert spend.calls == 1 and spend.dollars == 0


class TestDelimitedTransport:
    """The reply format, and every way a model can get it wrong.

    Prose used to travel inside a JSON string. Past roughly twelve kilobytes —
    four opportunities of real paragraphs — a raw newline or an unescaped quote
    would appear inside a string literal and invalidate the whole document,
    including the paragraphs that were fine. Prose now travels as text between
    markers and only the sentence map is JSON.

    Every test here is a decision about what the parser does with a reply it
    cannot read cleanly. The answer is always the same: refuse it and
    regenerate, never guess. A guess here ships a truncated paragraph to a real
    company.
    """

    def pair(self, label: str, prose: str, mapping: dict) -> str:
        return (
            f"<<<PROSE {label}>>>\n{prose}\n<<<END>>>\n"
            f"<<<MAP>>>{json.dumps(mapping)}<<<END>>>\n"
        )

    def reply(self) -> str:
        return (
            self.pair("opportunity=1", "You build injection molds. It shows.",
                      {"You build injection molds.": ["block1_what_they_make.what"]})
            + self.pair("opportunity=2", "The grant was $102,000. That is real money.",
                        {"The grant was $102,000.":
                         ["block2_grant_funded.grant_amount"]})
        )

    # --- the happy path ---

    def test_a_well_formed_reply_parses(self):
        sections = parse_sections(self.reply())
        assert [s.label for s in sections] == ["opportunity=1", "opportunity=2"]
        assert sections[0].prose == "You build injection molds. It shows."
        assert sections[0].sentence_map == [
            {"text": "You build injection molds.", "type": "fact",
             "claims": ["block1_what_they_make.what"]}
        ], "a bare list is the short spelling of the default type"

    def test_whitespace_around_delimiters_is_insignificant(self):
        spaced = (
            "  <<< PROSE opportunity=1 >>>   \n\n"
            "You build injection molds. It shows.\n\n"
            "   <<<END>>>\n\n\n"
            "<<<MAP>>>\n"
            '  {"You build injection molds.": ["block1_what_they_make.what"]}  \n'
            "<<<END>>>   \n"
        )
        sections = parse_sections(spaced)
        assert sections[0].label == "opportunity=1"
        assert sections[0].prose == "You build injection molds. It shows."
        assert sections[0].sentence_map[0]["claims"] == ["block1_what_they_make.what"]

    def test_opportunities_come_back_in_number_order(self):
        scrambled = (
            self.pair("opportunity=2", "Second thing. It matters.", {})
            + self.pair("anti_pitch", "Do not mention their website. It is good.", {})
            + self.pair("opportunity=1", "First thing. It matters more.", {})
        )
        ordered = opportunity_sections(parse_sections(scrambled))
        assert [s.prose.split(".")[0] for s in ordered] == ["First thing", "Second thing"]

    def test_a_named_section_is_found_and_a_missing_one_names_what_arrived(self):
        sections = parse_sections(self.pair("email", "Hello there. Read this.", {}))
        assert section_named(sections, "email").prose == "Hello there. Read this."
        with pytest.raises(ProseRejected) as caught:
            section_named(sections, "brief")
        assert "got email" in str(caught.value)

    def test_a_duplicated_section_is_refused(self):
        doubled = (self.pair("email", "One version. Of the email.", {})
                   + self.pair("email", "Another version. Of the email.", {}))
        with pytest.raises(ProseRejected) as caught:
            section_named(parse_sections(doubled), "email")
        assert "expected one" in str(caught.value)

    # --- what prose is now allowed to contain ---

    def test_unescaped_quotes_survive_verbatim(self):
        # The exact character that used to invalidate a whole reply.
        prose = ('They call it "lights-out" running, and the owner\'s note says '
                 '"we quote everything by hand". That is the whole problem.')
        sections = parse_sections(self.pair("email", prose, {}))
        assert sections[0].prose == prose

    def test_prose_may_talk_about_json(self):
        # Nothing in the prose is parsed as data, so the word is just a word.
        prose = ("Your quoting sheet exports JSON that nobody reads. A brace or "
                 'a quote in it, like {"a": 1}, is text here and nothing more.')
        assert parse_sections(self.pair("email", prose, {}))[0].prose == prose

    def test_paragraph_breaks_survive(self):
        prose = "First paragraph here.\n\nSecond paragraph here."
        assert parse_sections(self.pair("email", prose, {}))[0].prose == prose

    def test_a_draft_too_big_for_the_old_transport_parses(self):
        # The regression this format exists for: four opportunities of real
        # paragraphs, with quotes, well past the size where prose-in-JSON broke.
        paragraph = (
            'The state\'s announcement calls it a "readiness" award, and your own '
            "capabilities page describes work that a hand-built quote cannot keep "
            "up with.\n\nIf that is right, the cost is not the quoting itself.\n\n"
        )
        big = "".join(
            self.pair(f"opportunity={n}", paragraph * 20, {"x. y.": []})
            for n in range(1, 5)
        )
        assert len(big) > 12_000, "the fixture must exceed the size that broke JSON"
        sections = parse_sections(big)
        assert len(sections) == 4
        assert '"readiness"' in sections[0].prose

    # --- the escape rule ---

    def test_an_escaped_end_marker_becomes_literal_text(self):
        prose = ("Our parser closes a section on \\<<<END>>> and nothing else. "
                 "That is the whole rule.")
        parsed = parse_sections(self.pair("email", prose, {}))[0].prose
        assert parsed == ("Our parser closes a section on <<<END>>> and nothing "
                          "else. That is the whole rule.")

    def test_an_unescaped_end_marker_inside_prose_is_refused(self):
        # It closes the block early, which strands the rest of the paragraph
        # outside any block. Refusing is the point: the alternative is silently
        # delivering half a sentence to a stranger.
        stray = ("<<<PROSE email>>>\nWe close a section on <<<END>>> exactly. "
                 "And this half of the paragraph would vanish.\n<<<END>>>\n"
                 "<<<MAP>>>{}<<<END>>>\n")
        with pytest.raises(ProseRejected) as caught:
            parse_sections(stray)
        assert "text between blocks" in str(caught.value)

    def test_an_escaped_open_marker_is_also_literal(self):
        prose = "A section opens with \\<<<PROSE email>>> and nothing else does."
        sections = parse_sections(self.pair("email", prose, {}))
        assert len(sections) == 1, "the escaped opener must not start a block"
        assert sections[0].prose == (
            "A section opens with <<<PROSE email>>> and nothing else does.")

    # --- malformed replies ---

    def test_an_unterminated_block_is_refused(self):
        with pytest.raises(ProseRejected) as caught:
            parse_delimited("<<<PROSE email>>>\nHalf a draft. It stops here.")
        assert "unterminated" in str(caught.value)

    def test_a_block_opened_inside_another_is_refused(self):
        nested = ("<<<PROSE email>>>\nOne. <<<PROSE brief>>> Two.\n<<<END>>>\n"
                  "<<<MAP>>>{}<<<END>>>")
        with pytest.raises(ProseRejected) as caught:
            parse_delimited(nested)
        assert "opened inside" in str(caught.value)

    def test_a_reply_with_no_blocks_at_all_is_refused(self):
        with pytest.raises(ProseRejected) as caught:
            parse_delimited("I would rather write this as an essay.")
        assert "no delimited blocks" in str(caught.value)

    def test_a_preamble_before_the_first_block_is_tolerated(self):
        chatty = "Here is the draft you asked for:\n\n" + self.pair(
            "email", "You build injection molds. It shows.", {})
        assert parse_sections(chatty)[0].label == "email"

    def test_a_closing_remark_after_the_last_block_is_refused(self):
        # Symmetry with the preamble would be nice and would be wrong. Text
        # after the last block is where a prematurely closed paragraph lands.
        trailing = self.pair("email", "You build molds. It shows.", {}) + "Hope that helps!"
        with pytest.raises(ProseRejected) as caught:
            parse_sections(trailing)
        assert "after the last block" in str(caught.value)

    def test_prose_without_a_map_is_refused(self):
        with pytest.raises(ProseRejected) as caught:
            parse_sections("<<<PROSE email>>>\nOne. Two.\n<<<END>>>")
        assert "no <<<MAP>>> block after prose 'email'" in str(caught.value)

    def test_a_map_with_no_prose_before_it_is_refused(self):
        with pytest.raises(ProseRejected) as caught:
            parse_sections("<<<MAP>>>{}<<<END>>>")
        assert "no prose before it" in str(caught.value)

    def test_an_unlabelled_prose_block_is_refused(self):
        with pytest.raises(ProseRejected) as caught:
            parse_sections("<<<PROSE>>>\nOne. Two.\n<<<END>>>\n<<<MAP>>>{}<<<END>>>")
        assert "no label" in str(caught.value)

    # --- the map itself ---

    def test_unreadable_map_json_is_refused_and_names_the_section(self):
        broken = ("<<<PROSE email>>>\nOne. Two.\n<<<END>>>\n"
                  '<<<MAP>>>{"One.": [oops]}<<<END>>>')
        with pytest.raises(ProseRejected) as caught:
            parse_sections(broken)
        assert "the map for email was unreadable" in str(caught.value)

    def test_a_map_that_is_not_an_object_is_refused(self):
        listed = ("<<<PROSE email>>>\nOne. Two.\n<<<END>>>\n"
                  '<<<MAP>>>[{"text": "One."}]<<<END>>>')
        with pytest.raises(ProseRejected):
            parse_sections(listed)

    def test_a_map_value_that_is_not_a_list_is_refused(self):
        wrong = ("<<<PROSE email>>>\nOne. Two.\n<<<END>>>\n"
                 '<<<MAP>>>{"One.": "block1_what_they_make.what"}<<<END>>>')
        with pytest.raises(ProseRejected) as caught:
            parse_sections(wrong)
        assert "not a list of claim ids" in str(caught.value)

    def test_a_line_break_copied_into_a_map_key_is_recovered(self):
        # Keys are copied out of prose, so a model still occasionally brings a
        # line break with them. That is a control character, not a lost draft.
        wrapped = ('<<<PROSE email>>>\nOne two. Three.\n<<<END>>>\n'
                   '<<<MAP>>>{"One\ntwo.": ["block1_what_they_make.what"]}<<<END>>>')
        assert parse_sections(wrapped)[0].sentence_map[0]["text"] == "One\ntwo."

    def test_an_empty_map_is_valid(self):
        # The subject line asserts nothing, so its accounting is empty.
        assert parse_sections(self.pair("subject", "A subject line", {}))[0].sentence_map == []

    def test_the_parsed_map_feeds_the_gate_unchanged(self):
        # End to end: the transport's only job is to hand the gate the same
        # shape it always took. Nothing about the gate was loosened for it.
        prose = "You build injection molds. The grant recorded $102,000 in 2021."
        reply = self.pair("email", prose, {
            "You build injection molds.": ["block1_what_they_make.what"],
            "The grant recorded $102,000 in 2021.": [
                "block2_grant_funded.grant_amount"],
        })
        section = parse_sections(reply)[0]
        verdict = gate_prose(section.prose, section.sentence_map,
                             TestStructuredGate.ALLOWED, set(), True, None)
        assert verdict["passed"] is True
        assert verdict["sentences"] == 2

    def test_a_sentence_left_out_of_the_map_still_blocks(self):
        # The loophole is unchanged by the new transport: the gate walks the
        # prose, not the map.
        reply = self.pair(
            "email",
            "You build injection molds. Your competitors are all automating.",
            {"You build injection molds.": ["block1_what_they_make.what"]},
        )
        section = parse_sections(reply)[0]
        verdict = gate_prose(section.prose, section.sentence_map,
                             TestStructuredGate.ALLOWED, set(), True, None)
        assert verdict["passed"] is False
        assert any("unmapped sentence" in f for f in verdict["failures"])


class TestStructuredGate:
    ALLOWED = {"block1_what_they_make.what", "block2_grant_funded.grant_amount"}

    def test_a_fully_mapped_draft_passes(self):
        prose = "You build injection molds. The grant recorded $102,000 in 2021."
        smap = [
            {"text": "You build injection molds.",
             "claims": ["block1_what_they_make.what"]},
            {"text": "The grant recorded $102,000 in 2021.",
             "claims": ["block2_grant_funded.grant_amount"]},
        ]
        assert gate_prose(prose, smap, self.ALLOWED, set(), True, None)["passed"]

    def test_a_sentence_missing_from_the_map_is_unmapped(self):
        # The loophole this closes: write the assertion in the prose, leave it
        # out of the JSON, and hope the gate only reads the JSON.
        prose = ("You build injection molds. Your competitors are all automating "
                 "their estimating desks already.")
        smap = [{"text": "You build injection molds.",
                 "claims": ["block1_what_they_make.what"]}]
        verdict = gate_prose(prose, smap, self.ALLOWED, set(), True, None)
        assert verdict["passed"] is False
        assert any("unmapped sentence" in f for f in verdict["failures"])

    def test_an_unmapped_number_is_named_as_such(self):
        prose = "You build injection molds. That wastes about $40,000 a year."
        smap = [{"text": "You build injection molds.",
                 "claims": ["block1_what_they_make.what"]}]
        verdict = gate_prose(prose, smap, self.ALLOWED, set(), True, None)
        assert any("number with no source" in f for f in verdict["failures"])

    def test_a_map_citing_a_non_qualifying_claim_is_rejected(self):
        prose = "You build injection molds. It is a real shop."
        smap = [{"text": "You build injection molds.",
                 "claims": ["block4_digital_front_door.secret"]}]
        verdict = gate_prose(prose, smap, self.ALLOWED, set(), True, None)
        assert any("do not qualify" in f for f in verdict["failures"])

    def test_the_signature_is_still_exempt(self):
        prose = ("You build injection molds. The grant recorded $102,000.\n\n--\n"
                 "Udaay Sikder\n6902 Challenge Ln, Indianapolis IN 46250")
        smap = [
            {"text": "You build injection molds.",
             "claims": ["block1_what_they_make.what"]},
            {"text": "The grant recorded $102,000.",
             "claims": ["block2_grant_funded.grant_amount"]},
        ]
        assert gate_prose(prose, smap, self.ALLOWED, set(), True, None)["passed"]

    def test_an_ungated_name_still_blocks(self):
        prose = "Dale mentioned the new line. It runs well now."
        smap = [{"text": "Dale mentioned the new line.",
                 "claims": ["block1_what_they_make.what"]},
                {"text": "It runs well now.",
                 "claims": ["block1_what_they_make.what"]}]
        verdict = gate_prose(prose, smap, self.ALLOWED, set(), False, "Dale Whitmore")
        assert any("person gate" in f for f in verdict["failures"])


ALLOWED_T = {"block1_what_they_make.what", "block2_grant_funded.grant_amount"}


def typed(text, kind="fact", claims=()):
    return {"text": text, "type": kind, "claims": list(claims)}


def gate(prose, entries, company=None):
    return gate_prose(prose, entries, ALLOWED_T, set(), True, None, company)


class TestTypedAccounting:
    """The DATA-1 formula is three-part; the gate used to enforce one part.

    Facts must be claim-mapped exactly as before. Assumptions and about-us
    sentences are not exemptions — each carries its own burden, and each can
    fail in ways a fact cannot.
    """

    INVITE = " Check that against your own payroll figures and correct me."
    INVITE_ENTRY = {"text": "Check that against your own", "type": "about_us",
                    "claims": []}

    # --- facts are unchanged ---

    def test_a_mapped_fact_passes(self):
        v = gate("You build injection molds here.",
                 [typed("You build injection molds", claims=["block1_what_they_make.what"])])
        assert v["passed"] and v["map"][0]["type"] == "fact"

    def test_an_unmapped_fact_still_blocks(self):
        v = gate("Your competitors are all automating their desks.", [])
        assert not v["passed"]
        assert any("unmapped sentence" in f for f in v["failures"])

    def test_an_untyped_quantity_still_blocks(self):
        v = gate("That wastes about $40,000 every single year.", [])
        assert any("number with no source" in f for f in v["failures"])

    def test_a_quantity_typed_as_fact_but_unmapped_blocks(self):
        v = gate("That wastes about $40,000 every single year.",
                 [typed("That wastes about $40,000")])
        assert any("number with no source" in f for f in v["failures"])

    # --- assumptions ---

    def test_a_conditional_range_assumption_passes(self):
        prose = ("If your estimating time runs somewhere between $80 and $120 an hour, "
                 "the desk is expensive." + self.INVITE)
        v = gate(prose, [typed("If your estimating time runs", "assumption"),
                         self.INVITE_ENTRY])
        assert v["passed"], v["failures"]

    def test_an_assumption_with_no_conditional_language_blocks(self):
        prose = ("Your estimating time costs between $80 and $120 an hour today."
                 + self.INVITE)
        v = gate(prose, [typed("Your estimating time costs", "assumption"),
                         self.INVITE_ENTRY])
        assert any("nothing conditional" in f for f in v["failures"])

    def test_an_assumption_stating_a_point_figure_blocks(self):
        # A point estimate reads as knowledge however it is hedged.
        prose = "If your desk costs about $30,000 a year, that is real money." + self.INVITE
        v = gate(prose, [typed("If your desk costs about", "assumption"),
                         self.INVITE_ENTRY])
        assert any("point figure" in f for f in v["failures"])

    def test_an_artifact_with_assumptions_must_invite_correction(self):
        prose = ("If your estimating time runs somewhere between $80 and $120 an hour, "
                 "the desk is expensive.")
        v = gate(prose, [typed("If your estimating time runs", "assumption")])
        assert any("never asks to be corrected" in f for f in v["failures"])

    def test_an_artifact_without_assumptions_needs_no_invitation(self):
        v = gate("You build injection molds here.",
                 [typed("You build injection molds", claims=["block1_what_they_make.what"])])
        assert v["passed"]

    # --- about_us ---

    def test_a_salutation_typed_as_ours_passes(self):
        v = gate("I am writing to the owner or president of this shop.",
                 [typed("I am writing to the owner", "about_us")])
        assert v["passed"], v["failures"]

    def test_research_provenance_typed_as_ours_passes(self):
        v = gate("I came across the case study and read your capabilities page.",
                 [typed("I came across the case study", "about_us")])
        assert v["passed"], v["failures"]

    def test_an_about_us_sentence_carrying_a_quantity_blocks(self):
        v = gate("I read all 12,000 words of your capabilities page.",
                 [typed("I read all", "about_us")])
        assert any("asserts something about them" in f for f in v["failures"])

    def test_an_about_us_sentence_asserting_about_them_blocks(self):
        # The obvious smuggling route: label a claim as commentary.
        v = gate("Your line runs three shifts a day.",
                 [typed("Your line runs three shifts", "about_us")])
        assert any("asserts something about them" in f for f in v["failures"])

    def test_naming_the_company_in_a_salutation_is_allowed(self):
        v = gate("I am writing to the owner or president of Acme Tool.",
                 [typed("I am writing to the owner", "about_us")], company="Acme Tool")
        assert v["passed"], v["failures"]

    def test_describing_the_company_by_name_blocks(self):
        v = gate("Acme runs a second shift on Saturdays.",
                 [typed("Acme runs a second", "about_us")], company="Acme Tool")
        assert any("asserts something about them" in f for f in v["failures"])

    def test_an_unknown_type_blocks(self):
        v = gate("You build injection molds here.", [typed("You build", "guess")])
        assert any("unknown sentence type" in f for f in v["failures"])

    # --- derived arithmetic ---

    def test_arithmetic_from_established_inputs_passes(self):
        prose = (
            "If two engineers spend somewhere between 20 and 40 percent of a 40 hour "
            "week on undocumented code, at a loaded cost of $80 to $120 an hour, the "
            "range is wide. "
            "The arithmetic: 2 x 0.20-0.40 x 40 x 40 x $80-$120 = $51,200-$153,600. "
            "Check that against your own payroll figures and correct me.")
        v = gate(prose, [
            typed("If two engineers spend somewhere between", "assumption"),
            typed("The arithmetic", "assumption"),
            typed("Check that against your own", "about_us"),
        ])
        assert v["passed"], v["failures"]

    def test_arithmetic_with_an_invented_input_blocks(self):
        # The result is a range and the shape is right, but 0.55 was never
        # established anywhere in the artifact.
        prose = (
            "If two engineers spend somewhere between 20 and 40 percent of a 40 hour "
            "week on undocumented code, at a loaded cost of $80 to $120 an hour, the "
            "range is wide. "
            "The arithmetic: 2 x 0.55 x 40 x 40 x $80-$120 = $140,800-$211,200. "
            "Check that against your own payroll figures and correct me.")
        v = gate(prose, [
            typed("If two engineers spend somewhere between", "assumption"),
            typed("The arithmetic", "assumption"),
            typed("Check that against your own", "about_us"),
        ])
        assert any("never establishes" in f and "0.55" in f for f in v["failures"])

    def test_arithmetic_resolving_to_a_point_blocks(self):
        prose = ("If two engineers spend somewhere between 20 and 40 percent of a 40 "
                 "hour week, the range is wide. "
                 "The arithmetic: 2 x 0.20-0.40 x 40 = 24.0 engineer-weeks. "
                 "Check that against your own payroll figures and correct me.")
        v = gate(prose, [typed("If two engineers spend somewhere between", "assumption"),
                         typed("The arithmetic", "assumption"),
                         typed("Check that against your own", "about_us")])
        assert any("resolves to a point" in f for f in v["failures"])


class TestSalutationSplit:
    """The exact Circle City weld, pinned.

    The salutation ends in a comma, so collapsing newlines fused it to the
    first real sentence and produced a unit that appeared in no map — blocking
    a properly sourced sentence along with it.
    """

    EMAIL = ("To the owner or president of Circle City Sonorans,\n\n"
             "Your operation caught our attention for a specific reason. "
             "According to the state's announcement, you are expanding.")

    def test_the_salutation_is_its_own_unit(self):
        assert _sentences_of(self.EMAIL)[0] == (
            "To the owner or president of Circle City Sonorans,")

    def test_the_first_real_sentence_survives_intact(self):
        assert _sentences_of(self.EMAIL)[1] == (
            "Your operation caught our attention for a specific reason.")

    def test_the_weld_is_gone(self):
        assert not any("Sonorans,  Your" in s for s in _sentences_of(self.EMAIL))

    def test_a_typed_salutation_now_clears_the_gate(self):
        v = gate(self.EMAIL, [
            typed("To the owner or president", "about_us"),
            typed("Your operation caught our attention", "about_us"),
            typed("According to the states announcement",
                  claims=["block1_what_they_make.what"]),
        ])
        assert v["passed"], v["failures"]

    def test_ordinary_paragraphs_are_unaffected(self):
        prose = ("You build injection molds. You ship them every week.\n\n"
                 "That is a real business here.")
        assert _sentences_of(prose) == [
            "You build injection molds.", "You ship them every week.",
            "That is a real business here."]


class TestTypeGuidance:
    """A fact with no claim is the commonest way a live draft dies.

    In the first typed batch every sentence was mapped and all three types
    were used, yet artifacts still blocked: the model defaulted framing lines
    like "Three findings from your public record" to 'fact' and then had no
    CLAIM_ID to give them. Coverage was never the problem; choosing the type
    was.
    """

    def test_the_prompt_says_a_claimless_fact_is_rejected(self):
        from tools.drafter.main import TYPE_RULE
        assert "A FACT WITH NO CLAIM_ID IS ALWAYS REJECTED" in TYPE_RULE

    def test_the_prompt_names_framing_lines_as_about_us(self):
        from tools.drafter.main import TYPE_RULE
        for phrase in ("Three findings from your public record", "Two things stood out"):
            assert phrase in TYPE_RULE

    def test_the_prompt_forbids_label_prefixed_sentences(self):
        from tools.drafter.main import TYPE_RULE
        assert "DO NOT PREFIX A SENTENCE WITH A LABEL" in TYPE_RULE

    def test_the_prompt_calls_a_percentage_a_quantity(self):
        from tools.drafter.main import TYPE_RULE
        assert "25%" in TYPE_RULE and "between 20 and 30 percent" in TYPE_RULE

    def test_a_claimless_fact_really_does_block(self):
        # The rule the guidance is teaching.
        v = gate("Three findings from your public record are set out below.",
                 [typed("Three findings from your public record")])
        assert not v["passed"]

    def test_the_same_sentence_typed_about_us_passes(self):
        v = gate("Three findings from your public record are set out below.",
                 [typed("Three findings from your public record", "about_us")])
        assert v["passed"], v["failures"]


class TestInference:
    """The formula's middle third, which the gate had no way to express.

    Nine of sixteen brief sentences in the first typed batch were reasoning
    from a fact: about the prospect, so not about_us; carrying no figure or
    condition, so not an assumption; restating nothing, so not a fact. Both
    halves of an inference are load-bearing — the anchor is what the reader
    checks, the marker is what tells them the rest is ours.
    """

    ANCHOR = ["block2_grant_funded.grant_amount"]
    VALUES = {"block2_grant_funded.grant_amount": "$102,000 grant award in 2021"}

    def infer(self, prose, claims=None, kind="brief", values=None):
        return gate_prose(prose, [typed(prose[:28], "inference",
                                        self.ANCHOR if claims is None else claims)],
                          ALLOWED_T, set(), True, None, None, kind,
                          values if values is not None else self.VALUES)

    def test_an_anchored_marked_inference_passes(self):
        v = self.infer("That award tells me speed of delivery is a buying criterion.")
        assert v["passed"], v["failures"]

    def test_an_inference_with_no_anchor_blocks(self):
        v = self.infer("That award tells me speed of delivery is a buying criterion.",
                       claims=[])
        assert any("nothing to reason from" in f for f in v["failures"])

    def test_an_inference_with_no_reasoning_language_blocks(self):
        # Without the marker it reads as their own record, not our reading.
        v = self.infer("Speed of delivery is a buying criterion for your customers.")
        assert any("does not show it is reasoning" in f for f in v["failures"])

    def test_an_inference_may_quote_a_figure_from_the_claim_it_cites(self):
        v = self.infer("That $102,000 award signals real commitment to the line.")
        assert v["passed"], v["failures"]

    def test_an_inference_may_not_introduce_a_new_figure(self):
        v = self.infer("That award implies about $250,000 of committed capital.")
        assert any("neither a range nor in the claims" in f for f in v["failures"])

    def test_a_range_inside_an_inference_is_allowed(self):
        v = self.infer("That award suggests somewhere between $80 and $120 an hour.")
        assert v["passed"], v["failures"]

    @pytest.mark.parametrize("marker", [
        "suggests", "tells me", "signals", "implies", "which means",
        "points to", "indicates",
    ])
    def test_each_documented_marker_is_recognised(self, marker):
        from lib import formula
        assert formula.reasons_aloud(f"That award {marker} something about you.")


class TestHypothesisScope:
    """One hypothesis is the cold-touch rule. It binds the email and nothing else."""

    ANCHOR = ["block1_what_they_make.what"]

    def two_inferences(self, kind):
        prose = ("That page tells me you price by hand. "
                 "That backlog suggests the estimator is the constraint.")
        entries = [typed("That page tells me", "inference", self.ANCHOR),
                   typed("That backlog suggests", "inference", self.ANCHOR)]
        return gate_prose(prose, entries, ALLOWED_T, set(), True, None, None, kind, {})

    def test_two_inferences_block_an_email(self):
        v = self.two_inferences("email")
        assert any("allows exactly one hypothesis" in f for f in v["failures"])

    @pytest.mark.parametrize("kind", ["brief", "thesis"])
    def test_two_inferences_are_fine_in_a_longer_document(self, kind):
        v = self.two_inferences(kind)
        assert v["passed"], v["failures"]

    def test_one_inference_is_fine_in_an_email(self):
        prose = "That page tells me you price every job by hand today."
        v = gate_prose(prose, [typed("That page tells me", "inference", self.ANCHOR)],
                       ALLOWED_T, set(), True, None, None, "email", {})
        assert v["passed"], v["failures"]

    def test_an_unanchored_inference_still_blocks_a_brief(self):
        # The limit is lifted; the burden is not.
        prose = "That backlog suggests the estimator is the real constraint here."
        v = gate_prose(prose, [typed("That backlog suggests", "inference", [])],
                       ALLOWED_T, set(), True, None, None, "brief", {})
        assert any("nothing to reason from" in f for f in v["failures"])

    def test_a_hedged_sentence_still_counts_against_the_email_budget(self):
        prose = ("That page tells me you price by hand. "
                 "We think the estimator is the constraint here.")
        v = gate_prose(prose, [typed("That page tells me", "inference", self.ANCHOR),
                               typed("We think the estimator", "about_us")],
                       ALLOWED_T, set(), True, None, None, "email", {})
        assert any("allows exactly one hypothesis" in f for f in v["failures"])


class TestEvidenceFloor:
    """CASE-1 section 6, enforced by machine instead of by intention."""

    def prospect_with(self, n):
        from lib.evidence import BLOCK1_WHAT_THEY_MAKE
        facts = {f"f{i}": claim(f"fact number {i}", corroborated=True) for i in range(n)}
        return {"id": "p1", "company_name": "Acme Tool",
                "evidence_file": {BLOCK1_WHAT_THEY_MAKE: facts}}

    def test_three_facts_clears_the_floor(self):
        assert below_floor(self.prospect_with(3), ("verbatim",)) is None

    def test_two_facts_is_held_back_with_a_countable_reason(self):
        reason = below_floor(self.prospect_with(2), ("verbatim",))
        assert reason == "below evidence floor: 2 assertable facts, 3 required"

    def test_one_fact_reads_as_singular(self):
        assert "1 assertable fact," in below_floor(self.prospect_with(1), ("verbatim",))

    def test_no_facts_is_held_back(self):
        assert below_floor(self.prospect_with(0), ("verbatim",)) is not None

    def test_the_floor_counts_assertable_facts_not_all_claims(self):
        # Unsourced claims are exactly what the floor exists to discount.
        from lib.evidence import BLOCK1_WHAT_THEY_MAKE
        row = self.prospect_with(1)
        row["evidence_file"][BLOCK1_WHAT_THEY_MAKE].update(
            {f"weak{i}": claim(f"unconfirmed {i}") for i in range(5)})
        assert below_floor(row, ("verbatim",)) is not None
