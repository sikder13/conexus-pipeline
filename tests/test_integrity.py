"""Tests for the integrity dimension — fingerprints, tainting, and the gates.

The fixtures here are the real hijacked pages, trimmed but not tidied. A
detector tested against an imitation of the problem passes for reasons that have
nothing to do with the problem: the first version of this check was defeated by
a 586KB page because it assumed a placeholder would be small, and by 'die'
matching inside 'audience' because it used substring instead of word matching.
Both were invisible against synthetic input.

The false-positive tests carry equal weight. Roughly thirty of these companies
changed domains honestly, and a check that quarantines them is not safer than no
check — it just moves the damage.
"""

from __future__ import annotations

import re

import pytest
from bs4 import BeautifulSoup

from lib.claims import Tier, make_claim, origin_domain
from lib.evidence import BLOCK1_WHAT_THEY_MAKE, BLOCK4_DIGITAL_FRONT_DOOR, FLAGS_KEY
from lib.fingerprints import (
    assess,
    coherence,
    dominant_non_english,
    family_hits,
    looks_parked,
    name_in_context,
    redirect_targets,
    registrable_domain,
)
from lib.integrity import (
    backfill_derived_from,
    evidence_integrity,
    is_usable,
    taint_claims_from_domain,
)
from tests.conftest import fixture


def page(name: str) -> str:
    return fixture(name)


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" "))


DECATUR_INDUSTRY = (
    "Decatur Plastic Products LLC is based in North Vernon, provides high quality "
    "injection molding, flocking, assembly and finishing services."
)
ADDMAN_INDUSTRY = (
    "Addman Engineering is an additive manufacturing shop at the cutting edge of "
    "metal additive for aerospace, defense and other industries."
)


class TestRealHijacks:
    """Each of these was live in the queue as a scored prospect."""

    def test_the_decatur_togel_page_is_compromised(self):
        html = page("hijack_decatur_togel.html")
        verdict = assess(text_of(html), html, "http://www.decaturplastics.com/",
                         "https://www.savvycellar.com/", DECATUR_INDUSTRY)
        assert verdict["status"] == "compromised"
        assert any("gambling" in f for f in verdict["fingerprints"])

    def test_the_decatur_page_is_not_saved_by_its_size(self):
        # 586KB of stuffed content defeated a parking check with a 4000-char
        # ceiling. Size must not buy a page credibility.
        html = page("hijack_decatur_togel.html")
        assert not coherence(text_of(html), DECATUR_INDUSTRY)["coherent"]

    def test_the_addman_betting_page_is_compromised(self):
        html = page("hijack_addman_betting.html")
        verdict = assess(text_of(html), html, "https://print3d4u.com/",
                         "https://print3d4u.com/", ADDMAN_INDUSTRY)
        assert verdict["status"] == "compromised"

    def test_the_nextremity_casino_page_is_compromised(self):
        html = page("hijack_nextremity_casino.html")
        verdict = assess(text_of(html), html, "https://www.nextremity.com/",
                         "https://www.nextremity.com/", "medical device manufacturer")
        assert verdict["status"] == "compromised"

    def test_a_parked_page_is_not_found_rather_than_compromised(self):
        # The domain lapsed; nobody stole it. Calling both 'compromised' would
        # tell a reader the site was hijacked when it was merely sold.
        html = page("parked_hugedomains.html")
        verdict = assess(text_of(html), html, "https://thedirectpath.com/",
                         "https://www.hugedomains.com/x", "logistics")
        assert verdict["status"] == "not_found"
        assert any("parking" in f for f in verdict["fingerprints"])

    def test_every_quarantine_records_what_matched(self):
        html = page("hijack_decatur_togel.html")
        verdict = assess(text_of(html), html, "http://www.decaturplastics.com/",
                         "https://www.savvycellar.com/", DECATUR_INDUSTRY)
        assert verdict["fingerprints"], "a quarantine nobody can check is an assertion"


class TestNoFalsePositives:
    """The honest companies must survive. A check that flags them is not safer."""

    HONEST = (
        "Accutech Mold & Machine is a job shop in Muncie, Indiana. We build "
        "injection molds and tooling for automotive and medical manufacturers, "
        "with CNC machining, assembly and ISO 9001 certified production."
    )

    def test_a_manufacturer_page_is_ok(self):
        verdict = assess(self.HONEST, "", "https://accutech.com/",
                         "https://accutech.com/", "injection molding and tooling")
        assert verdict["status"] == "ok"

    def test_a_benign_rebrand_survives_domain_drift(self):
        # lci1.com -> lippert.com and Nowak -> Nexos Avian are real, legitimate
        # moves. Drift alone must never condemn a page.
        verdict = assess(self.HONEST, "", "https://lci1.com/",
                         "https://lippert.com/", "injection molding and tooling")
        assert verdict["status"] == "ok"
        assert verdict["domain_drift"] is True

    def test_specialist_does_not_match_cialis(self):
        # Substring matching flagged six honest manufacturers this way.
        assert "pharma" not in family_hits("Our specialists deliver quality parts.")

    def test_an_english_page_is_not_flagged_non_english(self):
        assert dominant_non_english(self.HONEST) == []

    def test_a_page_mentioning_a_casino_client_is_not_condemned_alone(self):
        # A firm that genuinely fits out casinos trips the keyword. Coherence is
        # what keeps it out of quarantine.
        text = self.HONEST + " We also fabricate fixtures for casino interiors."
        verdict = assess(text, "", "https://accutech.com/", "https://accutech.com/",
                         "injection molding and tooling")
        assert verdict["status"] == "ok"

    def test_a_rebrand_with_a_thin_landing_page_is_not_condemned(self):
        # Lippert Components (lci1.com -> lippert.com) was quarantined on drift
        # plus a page too thin to pass coherence. Failing coherence means "we
        # could not confirm this is theirs", not "somebody stole it".
        thin = "Lippert. Menu. Careers. Contact."
        verdict = assess(thin, "", "https://lci1.com/", "https://corporate.lippert.com/",
                         "components for recreational vehicles")
        assert verdict["status"] == "ok"
        assert verdict["domain_drift"] is True
        assert any("cross-domain" in f for f in verdict["fingerprints"]), \
            "the drift is still recorded for a reviewer, it just does not condemn"

    def test_a_redirect_to_the_companys_own_new_domain_is_not_compromise(self):
        # Fehrenbacher Cabinets: fci3.com -> fehrenbachercabinets.com.
        html = '<meta http-equiv="refresh" content="0; url=https://fehrenbachercabinets.com/">'
        verdict = assess("Redirecting...", html, "http://www.fci3.com/",
                         "http://www.fci3.com/", "custom cabinets")
        assert verdict["status"] == "ok"

    def test_movement_without_foreign_content_is_never_compromised(self):
        verdict = assess("", "", "https://old.test/", "https://new.test/", "machining")
        assert verdict["status"] != "compromised"

    def test_coming_soon_in_a_real_page_is_not_parking(self):
        text = "CCT Enterprises. ISO 9001 certified machining. AS9100 Coming Soon."
        assert assess(text, "", "https://cct.com/", "https://cct.com/",
                      "machining")["status"] == "ok"


class TestNameInContext:
    def test_a_name_alone_in_the_dom_is_not_enough(self):
        # The old check asked only whether a token appeared anywhere.
        stolen = "ALEXISTOGEL situs togel 4D. Decatur is a town in Indiana."
        assert name_in_context(stolen, ["decatur", "plastic"], DECATUR_INDUSTRY) is False

    def test_a_name_in_coherent_content_passes(self):
        real = ("Decatur Plastic Products provides injection molding, flocking and "
                "assembly services for automotive manufacturers in North Vernon.")
        assert name_in_context(real, ["decatur", "plastic"], DECATUR_INDUSTRY) is True

    def test_no_tokens_is_never_a_match(self):
        assert name_in_context("anything at all", [], "molding") is False


class TestRedirectsAndDomains:
    @pytest.mark.parametrize(("url", "expected"), [
        ("https://www.savvycellar.com/", "savvycellar.com"),
        ("http://decaturplastics.com/index.html", "decaturplastics.com"),
        ("https://a.b.example.com/x", "example.com"),
    ])
    def test_registrable_domain(self, url, expected):
        assert registrable_domain(url) == expected

    def test_a_meta_refresh_offsite_is_found(self):
        html = '<meta http-equiv="refresh" content="0; url=https://evil.test/x">'
        assert redirect_targets(html, "https://good.test/") == ["evil.test"]

    def test_a_js_redirect_offsite_is_found(self):
        html = '<script>window.location.href = "https://evil.test/x";</script>'
        assert redirect_targets(html, "https://good.test/") == ["evil.test"]

    def test_a_same_domain_redirect_is_not_reported(self):
        html = '<meta http-equiv="refresh" content="0; url=https://good.test/other">'
        assert redirect_targets(html, "https://good.test/") == []


class TestParkingHasNoLengthCeiling:
    def test_a_huge_parked_page_is_still_parked(self):
        padded = "lorem ipsum " * 60000 + " This domain is for sale on HugeDomains."
        assert looks_parked(padded)


class TestTainting:
    def evidence(self):
        return {
            BLOCK1_WHAT_THEY_MAKE: {
                "self_description": make_claim("togel text", Tier.T1,
                                               "https://www.savvycellar.com/"),
                "from_case_study": make_claim("real molds", Tier.T1,
                                              "https://conexusindiana.com/case-study/x/"),
            }
        }

    def test_it_marks_only_the_matching_domain(self):
        tainted, count = taint_claims_from_domain(
            self.evidence(), "savvycellar.com", "hijacked domain")
        block = tainted[BLOCK1_WHAT_THEY_MAKE]
        assert count == 1
        assert block["self_description"]["tainted"] is True
        assert "tainted" not in block["from_case_study"]

    def test_it_records_the_reason(self):
        tainted, _ = taint_claims_from_domain(
            self.evidence(), "savvycellar.com", "serves an Indonesian gambling site")
        claim = tainted[BLOCK1_WHAT_THEY_MAKE]["self_description"]
        assert claim["taint_reason"] == "serves an Indonesian gambling site"

    def test_it_never_deletes_the_value_or_the_source(self):
        tainted, _ = taint_claims_from_domain(
            self.evidence(), "savvycellar.com", "hijacked")
        claim = tainted[BLOCK1_WHAT_THEY_MAKE]["self_description"]
        assert claim["value"] == "togel text"
        assert claim["source_url"] == "https://www.savvycellar.com/"
        assert claim["tier"] == 1

    def test_a_tainted_claim_is_not_usable(self):
        tainted, _ = taint_claims_from_domain(
            self.evidence(), "savvycellar.com", "hijacked")
        assert is_usable(tainted[BLOCK1_WHAT_THEY_MAKE]["self_description"]) is False
        assert is_usable(tainted[BLOCK1_WHAT_THEY_MAKE]["from_case_study"]) is True

    def test_tainting_twice_does_not_double_count(self):
        once, first = taint_claims_from_domain(self.evidence(), "savvycellar.com", "x")
        _twice, second = taint_claims_from_domain(once, "savvycellar.com", "x")
        assert (first, second) == (1, 0)


class TestUntainting:
    """A wrong quarantine is itself a data-integrity problem."""

    def tainted(self):
        from lib.integrity import taint_claims_from_domain as taint
        evidence = {BLOCK1_WHAT_THEY_MAKE: {
            "a": make_claim("v", Tier.T1, "https://lippert.com/x"),
        }}
        return taint(evidence, "lippert.com", "wrongly quarantined")[0]

    def test_it_lifts_the_markers(self):
        from lib.integrity import untaint_claims_from_domain
        lifted, count = untaint_claims_from_domain(self.tainted(), "lippert.com")
        claim = lifted[BLOCK1_WHAT_THEY_MAKE]["a"]
        assert count == 1
        assert "tainted" not in claim and "taint_reason" not in claim

    def test_it_leaves_the_claim_itself_untouched(self):
        from lib.integrity import untaint_claims_from_domain
        lifted, _ = untaint_claims_from_domain(self.tainted(), "lippert.com")
        claim = lifted[BLOCK1_WHAT_THEY_MAKE]["a"]
        assert claim["value"] == "v" and claim["tier"] == 1
        assert claim["source_url"] == "https://lippert.com/x"

    def test_it_does_not_touch_another_domains_taint(self):
        from lib.integrity import untaint_claims_from_domain
        lifted, count = untaint_claims_from_domain(self.tainted(), "savvycellar.com")
        assert count == 0
        assert lifted[BLOCK1_WHAT_THEY_MAKE]["a"]["tainted"] is True


class TestDerivedFrom:
    def test_make_claim_records_the_origin(self):
        claim = make_claim("v", Tier.T1, "https://www.example.com/a")
        assert claim["derived_from"] == "example.com"

    def test_an_explicit_origin_survives_a_redirect(self):
        claim = make_claim("v", Tier.T1, "https://shown.test/a", derived_from="fetched.test")
        assert claim["derived_from"] == "fetched.test"

    def test_backfill_fills_only_what_is_missing(self):
        evidence = {BLOCK1_WHAT_THEY_MAKE: {
            "a": {"value": 1, "tier": 1, "source_url": "https://x.test/1",
                  "date_checked": "2026-08-09"},
            "b": {"value": 2, "tier": 1, "source_url": "https://y.test/2",
                  "date_checked": "2026-08-09", "derived_from": "kept.test"},
        }}
        filled, count = backfill_derived_from(evidence)
        assert count == 1
        assert filled[BLOCK1_WHAT_THEY_MAKE]["a"]["derived_from"] == "x.test"
        assert filled[BLOCK1_WHAT_THEY_MAKE]["b"]["derived_from"] == "kept.test"

    def test_origin_domain_of_nothing_is_empty(self):
        assert origin_domain(None) == ""


class TestIntegrityGate:
    def sound(self):
        return {"id": "p1", "evidence_file": {
            BLOCK1_WHAT_THEY_MAKE: {"self_description": make_claim(
                "We build molds.", Tier.T1, "https://accutech.test/")}
        }}

    def test_a_sound_file_passes(self):
        assert evidence_integrity(self.sound()).passing is True

    def test_a_compromised_site_fails(self):
        row = self.sound() | {"website_status": "compromised"}
        report = evidence_integrity(row)
        assert report.passing is False
        assert any("compromised" in f for f in report.failures)

    def test_an_empty_block1_fails(self):
        assert evidence_integrity({"id": "p1", "evidence_file": {}}).passing is False

    def test_a_block1_of_only_tainted_claims_fails(self):
        row = self.sound()
        row["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["self_description"]["tainted"] = True
        report = evidence_integrity(row)
        assert report.passing is False
        assert any("tainted or killed" in f for f in report.failures)

    def test_a_tainted_scoring_flag_fails(self):
        row = self.sound()
        row["evidence_file"][BLOCK4_DIGITAL_FRONT_DOOR] = {FLAGS_KEY: {
            "weak_front_door": {**make_claim(True, Tier.T1, "https://x.test/"),
                                "tainted": True},
        }}
        report = evidence_integrity(row)
        assert report.passing is False
        assert any("scoring input is tainted" in f for f in report.failures)

    def test_flags_alone_do_not_count_as_block1(self):
        row = {"id": "p1", "evidence_file": {BLOCK1_WHAT_THEY_MAKE: {
            FLAGS_KEY: {"x": make_claim(True, Tier.T1, "https://x.test/")}}}}
        assert evidence_integrity(row).passing is False

    def test_the_report_carries_a_timestamp(self):
        assert evidence_integrity(self.sound()).checked_at
