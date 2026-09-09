"""Tests for the outbound compliance profiles — CAN-SPAM and CASL.

The failure this exists to prevent is silent: `first.last@company.ca` looks
exactly like a published address in a finished draft, and the only place the
difference lives is the evidence file. So the tests are about what is in the
evidence rather than what is in the prose, and the central one is that an email
addressed to an address nobody published is refused rather than flagged.
"""

from __future__ import annotations

import pytest

from lib import compliance
from lib.claims import Tier, make_claim
from lib.evidence import BLOCK4_DIGITAL_FRONT_DOOR

SITE = "https://riverbendmachining.ca/contact"
IDENTIFICATION = (
    "\n\n--\nUdaay Sikder\nNahl Technologies Inc.\n"
    "6902 Challenge Ln, Indianapolis IN 46250, USA\n"
    "Reply STOP and I will not contact you again."
)


def body(text: str = "I read your capabilities page.", signed: bool = True) -> str:
    return text + (IDENTIFICATION if signed else "")


def prospect(adapter: str = "canada_gc", contacts=None, **extra) -> dict:
    return {
        "id": "c1",
        "company_name": "Riverbend Machining Ltd.",
        "source_adapter": adapter,
        "contacts": contacts if contacts is not None else [
            {"kind": "email", "value": "info@riverbendmachining.ca",
             "email_class": "role_based", "source_url": SITE,
             "email_confidence": 100, "tier": 1},
        ],
        **extra,
    }


class TestProfiles:
    def test_indiana_is_can_spam_and_canada_is_casl(self):
        assert compliance.profile_for("conexus_iedc").regime == "CAN-SPAM"
        assert compliance.profile_for("canada_gc").regime == "CASL"

    def test_only_casl_requires_a_published_address(self):
        assert compliance.CASL.requires_published_address
        assert not compliance.CAN_SPAM.requires_published_address

    def test_an_undeclared_source_stops_rather_than_taking_the_laxer_regime(self):
        with pytest.raises(compliance.UnknownComplianceProfile):
            compliance.profile_for("feddev_ca")

    def test_the_identification_block_is_the_one_the_law_asks_for(self):
        block = compliance.CASL.identification_block
        assert "Nahl Technologies Inc." in block
        assert "6902 Challenge Ln, Indianapolis IN 46250, USA" in block
        assert "STOP" in block

    def test_the_casl_basis_states_both_conditions(self):
        assert "conspicuously published" in compliance.CASL.basis
        assert "role" in compliance.CASL.basis


class TestPublishedAddresses:
    def test_an_address_read_off_a_page_counts(self):
        [found] = compliance.published_addresses(prospect())
        assert found.address == "info@riverbendmachining.ca"
        assert found.source_url == SITE

    def test_an_address_with_no_source_url_is_not_evidence_of_publication(self):
        rows = compliance.published_addresses(prospect(contacts=[
            {"kind": "email", "value": "guess@riverbendmachining.ca",
             "email_class": "named_person", "source_url": ""},
        ]))
        assert rows == []

    def test_a_named_person_is_preferred_over_a_rota(self):
        chosen = compliance.choose_target(prospect(contacts=[
            {"kind": "email", "value": "info@riverbendmachining.ca",
             "email_class": "role_based", "source_url": SITE},
            {"kind": "email", "value": "d.whitmore@riverbendmachining.ca",
             "email_class": "named_person", "source_url": SITE},
        ]))
        assert chosen.address == "d.whitmore@riverbendmachining.ca"

    def test_a_company_with_nothing_published_has_no_target(self):
        assert compliance.choose_target(prospect(contacts=[])) is None

    def test_the_block_four_claims_are_readable_for_a_cross_check(self):
        evidence = {BLOCK4_DIGITAL_FRONT_DOOR: {"published_emails": [
            make_claim("info@riverbendmachining.ca — reaches a function, not a person",
                       Tier.T1, SITE),
        ]}}
        assert compliance.published_in_evidence(
            prospect(evidence_file=evidence)) == ["info@riverbendmachining.ca"]


class TestCaslEmail:
    def test_a_published_address_passes(self):
        verdict = compliance.check_email(prospect(), body())
        assert verdict.passed
        assert verdict.regime == "CASL"
        assert verdict.target == "info@riverbendmachining.ca"
        assert "conspicuously published" in verdict.basis

    def test_a_guessed_address_is_refused(self):
        verdict = compliance.check_email(
            prospect(), body(), target="dave.whitmore@riverbendmachining.ca")
        assert not verdict.passed
        assert any("not one contact discovery read" in f for f in verdict.failures)

    def test_a_guessed_address_written_into_the_draft_is_refused(self):
        verdict = compliance.check_email(
            prospect(),
            body("Write back to dave.whitmore@riverbendmachining.ca if that is easier."),
        )
        assert not verdict.passed
        assert any("nobody published" in f for f in verdict.failures)

    def test_a_company_with_no_published_address_may_not_be_emailed_at_all(self):
        verdict = compliance.check_email(prospect(contacts=[]), body())
        assert not verdict.passed
        assert any("Nothing may be guessed" in f for f in verdict.failures)

    def test_a_missing_identification_block_is_refused(self):
        verdict = compliance.check_email(prospect(), body(signed=False))
        assert not verdict.passed
        assert any("identification block" in f for f in verdict.failures)

    def test_the_verdict_records_what_was_available(self):
        stored = compliance.check_email(prospect(), body()).as_dict()
        assert stored["regime"] == "CASL"
        assert stored["published_addresses"] == ["info@riverbendmachining.ca"]
        assert stored["target_address"] == "info@riverbendmachining.ca"
        assert stored["passed"] is True


class TestCanSpamEmail:
    def test_indiana_does_not_need_a_published_address(self):
        verdict = compliance.check_email(
            prospect(adapter="conexus_iedc", contacts=[]), body())
        assert verdict.passed
        assert verdict.regime == "CAN-SPAM"

    def test_indiana_still_needs_the_identification_block(self):
        verdict = compliance.check_email(
            prospect(adapter="conexus_iedc", contacts=[]), body(signed=False))
        assert not verdict.passed


class TestOtherArtifacts:
    @pytest.mark.parametrize("kind", ["thesis", "brief", "linkedin"])
    def test_casl_email_rules_do_not_bind_other_artifacts(self, kind):
        # A LinkedIn message is sent by hand inside a platform with its own
        # rules; it is not an electronic message to an address we hold.
        verdict = compliance.check_artifact(
            prospect(contacts=[]), kind, "no signature here")
        assert verdict.passed
        assert verdict.regime == "CASL"
        assert kind in verdict.basis

    def test_an_email_is_the_one_kind_that_is_checked(self):
        verdict = compliance.check_artifact(prospect(contacts=[]), "email", body())
        assert not verdict.passed

    def test_an_artifact_that_was_never_drafted_records_the_regime_and_no_verdict(self):
        record = compliance.not_drafted(prospect(), "email")
        assert record["regime"] == "CASL"
        assert record["passed"] is None
        assert record["failures"] == []


class TestIdentification:
    def test_it_is_appended_once(self):
        once = compliance.append_identification("Hello.", compliance.CASL)
        twice = compliance.append_identification(once, compliance.CASL)
        assert once == twice
        assert once.count("6902 Challenge Ln") == 1


class TestTheOutboundGateBlocksAGuessedAddress:
    """The end-to-end assertion: a guessed address blocks the artifact.

    `gate_prose` reads the prose and cannot see the difference. The drafter runs
    the compliance verdict as a second, independent refusal and folds its
    failures into the same gate result, which is what turns a `sendable` email
    into a `blocked` one.
    """

    def _gate(self, prospect_row, email_body):
        from tools.drafter.main import gate_prose

        gate = gate_prose(email_body, [{"text": email_body, "type": "about_us",
                                        "claims": []}],
                          set(), set(), False, None, prospect_row["company_name"],
                          "email", {})
        verdict = compliance.check_artifact(prospect_row, "email", email_body)
        if not verdict.passed:
            gate = {**gate, "passed": False,
                    "failures": [*gate["failures"],
                                 *(f"{verdict.regime}: {r}" for r in verdict.failures)]}
        return gate

    def test_a_clean_email_to_a_published_address_is_not_blocked_by_compliance(self):
        gate = self._gate(prospect(), body("I read your capabilities page."))
        assert not any("CASL" in f for f in gate["failures"])

    def test_an_email_naming_a_guessed_address_is_blocked(self):
        gate = self._gate(
            prospect(),
            body("I read your capabilities page. "
                 "Reply to dave.whitmore@riverbendmachining.ca if easier."),
        )
        assert gate["passed"] is False
        assert any("CASL" in f and "nobody published" in f for f in gate["failures"])

    def test_an_email_to_a_company_with_no_published_address_is_blocked(self):
        gate = self._gate(prospect(contacts=[]), body("I read your capabilities page."))
        assert gate["passed"] is False
        assert any("Nothing may be guessed" in f for f in gate["failures"])
