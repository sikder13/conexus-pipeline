"""Tests for the resolve_website node.

Confidence is the whole product of this node: it decides whether a human looks
at the record before anyone speaks to the company. So every tier is pinned, and
so are the three "not a failure" outcomes — parked, social-only, and dead — that
a naive implementation would throw away.
"""

from __future__ import annotations

import asyncio

import pytest

from lib.nodes import RunContext
from tests.conftest import FakeClient, FakeResponse
from tools.harvester.nodes.website import (
    CONFIDENCE,
    MIN_TRUSTED_CONFIDENCE,
    ResolveWebsite,
    candidate_domains,
    distinctive_tokens,
    is_social,
    looks_parked,
)

COMPANY = "Accutech Mold & Machine, Inc."
SOURCE_URL = "https://conexusindiana.com/mrg-recipients/"


def claim(url: str) -> dict:
    return {
        "value": url,
        "tier": 1,
        "source_url": SOURCE_URL,
        "date_checked": "2026-08-08",
        "verified": False,
        "verified_at": None,
    }


REAL_PAGE = (
    "<html><body><h1>Accutech Mold &amp; Machine</h1>"
    "<p>Accutech is an injection molding and tooling shop in Muncie, Indiana, "
    "producing precision molds for automotive and medical manufacturers. "
    "ISO 9001 certified production and assembly.</p></body></html>"
)
"""A page that both names the company and reads like the stated industry.

The old fixtures were bare tags like '<html>Accutech Mold</html>'. Those passed
the old name check, which asked only whether a token appeared anywhere in the
DOM — the same check a hijacked page satisfied by mentioning a town."""


def prospect_with_site(url: str | None, name: str = COMPANY,
                       industry: str = "injection molding and tooling") -> dict:
    evidence = {"source": {"website": claim(url)}} if url else {"source": {}}
    return {"id": "p1", "company_name": name, "evidence_file": evidence,
            "industry_desc": industry}


def serve(pages: dict[str, FakeResponse], robots: str = ""):
    """Handler serving a fixed page map; anything unlisted is a 404."""

    def handler(url: str):
        if url.endswith("/robots.txt"):
            return FakeResponse(robots, 200 if robots else 404, url)
        return pages.get(url, FakeResponse("", 404, url))

    return handler


def run(prospect, handler, settings, node=None):
    ctx = RunContext(FakeClient(handler), settings)
    return asyncio.run((node or ResolveWebsite()).run(prospect, ctx))


class TestHelpers:
    def test_distinctive_tokens_drop_generic_industry_words(self):
        assert "accutech" in distinctive_tokens(COMPANY)
        assert "machine" not in distinctive_tokens(COMPANY)

    def test_a_wholly_generic_name_still_yields_tokens(self):
        assert distinctive_tokens("Precision Machine Company") != []

    def test_candidate_domains_are_built_most_specific_first(self):
        assert candidate_domains("Accutech Mold Machine") == [
            "https://accutechmoldmachine.com",
            "https://accutechmold.com",
        ]

    def test_the_first_word_alone_is_never_a_candidate(self):
        """It produced cedar.com for Cedar Valley Selections Inc."""
        assert "https://cedar.com" not in candidate_domains("Cedar Valley Selections Inc.")

    def test_a_canadian_company_gets_ca_tried_first(self):
        assert candidate_domains("Cedar Valley Selections Inc.", (".ca", ".com"))[0] == (
            "https://cedarvalleyselections.ca")

    def test_a_nameless_company_yields_no_candidates(self):
        assert candidate_domains("") == []

    @pytest.mark.parametrize(
        "url",
        ["https://facebook.com/acme", "https://www.facebook.com/acme", "https://linkedin.com/x"],
    )
    def test_social_urls_are_recognised(self, url):
        assert is_social(url) is True

    def test_an_owned_domain_is_not_social(self):
        assert is_social("https://accutech.com/about") is False

    def test_parking_pages_are_recognised(self):
        assert looks_parked("<html>This domain is for sale. Buy this domain.</html>") is True

    def test_a_real_page_is_not_parked(self):
        assert looks_parked("<html>" + "Accutech makes molds. " * 400 + "</html>") is False


class TestConfidenceTiers:
    def test_a_published_domain_naming_the_company_scores_highest(self, settings_nodelay):
        pages = {"https://accutech.com": FakeResponse(
            "<html>Accutech Mold &amp; Machine, Muncie Indiana</html>", 200,
            "https://accutech.com")}
        result = run(prospect_with_site("https://accutech.com"), serve(pages), settings_nodelay)
        assert result.prospect_patch["website_confidence"] == CONFIDENCE["source_verified"]
        assert result.prospect_patch["website"] == "https://accutech.com"
        assert "stage" not in result.prospect_patch

    def test_a_published_domain_not_naming_the_company_scores_lower(self, settings_nodelay):
        pages = {"https://accutech.com": FakeResponse(
            "<html>Welcome to our site</html>", 200, "https://accutech.com")}
        result = run(prospect_with_site("https://accutech.com"), serve(pages), settings_nodelay)
        assert result.prospect_patch["website_confidence"] == CONFIDENCE["source_unverified"]

    def test_a_constructed_domain_naming_the_company_is_trusted(self, settings_nodelay):
        pages = {"https://accutechmoldmachine.com": FakeResponse(
            "<html>Accutech Mold and Machine</html>", 200, "https://accutechmoldmachine.com")}
        result = run(prospect_with_site(None), serve(pages), settings_nodelay)
        assert result.prospect_patch["website_confidence"] == CONFIDENCE["constructed_verified"]

    def test_a_constructed_domain_not_naming_the_company_needs_review(self, settings_nodelay):
        pages = {"https://accutechmoldmachine.com": FakeResponse(
            "<html>Under new ownership</html>", 200, "https://accutechmoldmachine.com")}
        result = run(prospect_with_site(None), serve(pages), settings_nodelay)
        assert result.prospect_patch["website_confidence"] == CONFIDENCE["constructed_unverified"]
        assert result.prospect_patch["stage"] == "needs_review"

    def test_the_best_candidate_wins_not_the_last_tried(self, settings_nodelay):
        # Second guess is a live but unverified site; third is dead. The live one
        # must survive.
        pages = {"https://accutechmold.com": FakeResponse(
            "<html>Some other business</html>", 200, "https://accutechmold.com")}
        result = run(prospect_with_site(None), serve(pages), settings_nodelay)
        assert result.prospect_patch["website_confidence"] == CONFIDENCE["constructed_unverified"]
        assert result.prospect_patch["website"] == "https://accutechmold.com"


class TestNotFailures:
    def test_a_facebook_only_presence_is_recorded(self, settings_nodelay):
        result = run(
            prospect_with_site("https://facebook.com/accutech"), serve({}), settings_nodelay
        )
        assert result.prospect_patch["website_confidence"] == CONFIDENCE["social_only"]
        assert result.prospect_patch["website"] == "https://facebook.com/accutech"
        assert result.prospect_patch["stage"] == "needs_review"
        assert any("social profile" in note for note in result.notes)

    def test_a_redirect_to_a_social_profile_is_recorded(self, settings_nodelay):
        pages = {"https://accutech.com": FakeResponse(
            "<html>Accutech</html>", 200, "https://www.facebook.com/accutech")}
        result = run(prospect_with_site("https://accutech.com"), serve(pages), settings_nodelay)
        assert result.prospect_patch["website_confidence"] == CONFIDENCE["social_only"]

    def test_a_parked_domain_is_recorded(self, settings_nodelay):
        pages = {"https://accutech.com": FakeResponse(
            "<html>This domain is for sale</html>", 200, "https://accutech.com")}
        result = run(prospect_with_site("https://accutech.com"), serve(pages), settings_nodelay)
        assert result.prospect_patch["website_confidence"] == CONFIDENCE["parked"]
        assert result.prospect_patch["stage"] == "needs_review"
        assert any("parked" in note for note in result.notes)

    def test_a_dead_domain_scores_zero_and_writes_no_website(self, settings_nodelay):
        result = run(prospect_with_site("https://accutech.com"), serve({}), settings_nodelay)
        assert result.prospect_patch["website_confidence"] == 0
        assert "website" not in result.prospect_patch
        assert result.prospect_patch["stage"] == "needs_review"

    def test_a_company_with_no_findable_domain_is_not_an_error(self, settings_nodelay):
        result = run(prospect_with_site(None, name=""), serve({}), settings_nodelay)
        assert result.prospect_patch["website_confidence"] == 0
        assert result.prospect_patch["stage"] == "needs_review"
        assert any("no website found" in note for note in result.notes)


class TestEvidenceAndReasoning:
    def test_a_trusted_site_is_recorded_as_a_t1_claim(self, settings_nodelay):
        pages = {"https://accutech.com": FakeResponse(
            REAL_PAGE, 200, "https://accutech.com")}
        result = run(prospect_with_site("https://accutech.com"), serve(pages), settings_nodelay)
        assert result.evidence_patch["identity"]["website"]["tier"] == 1

    def test_an_untrusted_site_is_recorded_as_a_t4_hypothesis(self, settings_nodelay):
        pages = {"https://accutech.com": FakeResponse(
            "<html>This domain is for sale</html>", 200, "https://accutech.com")}
        result = run(prospect_with_site("https://accutech.com"), serve(pages), settings_nodelay)
        assert result.evidence_patch["identity"]["website"]["tier"] == 4

    def test_every_outcome_explains_its_reasoning(self, settings_nodelay):
        pages = {"https://accutech.com": FakeResponse(
            REAL_PAGE, 200, "https://accutech.com")}
        result = run(prospect_with_site("https://accutech.com"), serve(pages), settings_nodelay)
        assert result.notes, "a reviewer must be able to see why the machine believed this"
        assert any("accutech.com" in note for note in result.notes)

    def test_robots_disallow_does_not_kill_the_record(self, settings_nodelay):
        handler = serve(
            {"https://accutech.com": FakeResponse("<html>Accutech</html>", 200,
                                                  "https://accutech.com")},
            robots="User-agent: *\nDisallow: /\n",
        )
        result = run(prospect_with_site("https://accutech.com"), handler, settings_nodelay)
        assert result.prospect_patch["website_confidence"] == CONFIDENCE["source_unverified"]
        assert any("robots.txt" in note for note in result.notes)

    @pytest.mark.parametrize("key", ["source_unverified", "constructed_unverified",
                                     "social_only", "parked", "compromised", "not_found"])
    def test_every_unconfirmed_outcome_sits_below_the_trust_floor(self, key):
        # source_unverified used to be 80, above a floor of 70: a page that
        # failed the name check still produced a trusted T1 claim and no review
        # flag. That is exactly how Decatur became a P1 on a gambling site.
        assert CONFIDENCE[key] < MIN_TRUSTED_CONFIDENCE

    @pytest.mark.parametrize("key", ["source_verified", "constructed_verified"])
    def test_only_a_confirmed_match_reaches_the_trust_floor(self, key):
        assert CONFIDENCE[key] >= MIN_TRUSTED_CONFIDENCE

    def test_it_never_writes_a_human_only_stage(self, settings_nodelay):
        result = run(prospect_with_site("https://accutech.com"), serve({}), settings_nodelay)
        assert result.prospect_patch.get("stage") in (None, "needs_review")


class TestNoCrossProspectLeakage:
    """One node object serves every prospect in a run.

    An earlier version stashed the page verdict on `self`, and two companies
    with no website at all ended up carrying Decatur's gambling fingerprints —
    the node had simply kept the last verdict it computed. Shared mutable state
    on a node is a correctness bug, not a style one.
    """

    def test_the_node_keeps_no_verdict_between_prospects(self, settings_nodelay):
        node = ResolveWebsite()
        pages = {"https://accutech.com": FakeResponse(
            REAL_PAGE, 200, "https://accutech.com")}
        run(prospect_with_site("https://accutech.com"), serve(pages), settings_nodelay,
            node=node)
        leaked = [a for a in vars(node) if "verdict" in a or "fingerprint" in a]
        assert not leaked, f"node retained per-prospect state: {leaked}"

    def test_a_prospect_with_no_site_gets_no_fingerprints(self, settings_nodelay):
        node = ResolveWebsite()
        hijacked = {"https://evil.com": FakeResponse(
            "<html>situs togel 4D bandar casino yang dengan untuk permainan daftar</html>",
            200, "https://evil.com")}
        run(prospect_with_site("https://evil.com"), serve(hijacked), settings_nodelay,
            node=node)
        clean = run(prospect_with_site(None, name="Nowhere Ltd"), serve({}),
                    settings_nodelay, node=node)
        assert not clean.prospect_patch.get("website_fingerprints")


class TestCedarValley:
    """The wrong-company resolution, pinned against the stored record.

    Cedar Valley Selections Inc. of Windsor, Ontario makes pita chips. The
    resolver constructed cedar.com from the first word of its name, found a US
    healthcare-payments company there, matched the single token "Cedar", cleared
    coherence on two generic industry words, and stored the result as a verified
    Tier 1 website. The company then ranked first among those ready to contact.
    """

    NAME = "Cedar Valley Selections Inc."
    AWARD = ("Scale-up automation, quality assurance / food safety implementations, "
             "and map processing to increase efficiency, quality, and production "
             "capacity of our pita chip manufacturing facility.")
    # The shape of what cedar.com serves: names "Cedar", describes healthcare
    # payments, and carries enough generic vocabulary to pass the old check.
    WRONG_PAGE = (
        "<html>Cedar is the leading healthcare payments and engagement platform. "
        "Cedar helps medical providers improve billing quality and increase "
        "production of patient statements. Automation for health systems.</html>")

    def prospect(self):
        return {"id": "cedar", "company_name": self.NAME,
                "dba_name": "Cedar Valley Selections",
                "source_adapter": "canada_gc",
                "evidence_file": {"source": {}}, "industry_desc": self.AWARD}

    def test_cedar_com_is_never_even_constructed(self):
        assert "https://cedar.com" not in candidate_domains(self.NAME, (".ca", ".com"))

    def test_a_single_token_no_longer_matches(self):
        from lib.fingerprints import full_name_present
        assert full_name_present(self.WRONG_PAGE, self.NAME) is None

    def test_the_wrong_company_page_is_not_accepted(self, settings_nodelay):
        pages = {"https://cedarvalleyselections.ca": FakeResponse(
            self.WRONG_PAGE, 200, "https://cedarvalleyselections.ca")}
        patch = run(self.prospect(), serve(pages), settings_nodelay).prospect_patch
        assert patch["website_confidence"] < MIN_TRUSTED_CONFIDENCE
        assert patch["stage"] == "needs_review"

    def test_the_right_page_is_accepted(self, settings_nodelay):
        page = ("<html>Cedar Valley Selections manufactures pita chips in Windsor, "
                "Ontario. Our food production facility runs to the highest quality "
                "and food safety standards.</html>")
        pages = {"https://cedarvalleyselections.ca": FakeResponse(
            page, 200, "https://cedarvalleyselections.ca")}
        patch = run(self.prospect(), serve(pages), settings_nodelay).prospect_patch
        assert patch["website"] == "https://cedarvalleyselections.ca"
        assert patch["website_confidence"] == CONFIDENCE["constructed_verified"]

    def test_the_right_name_on_the_wrong_business_nulls_the_site(self, settings_nodelay):
        """The name is theirs and the business is not. This is the dangerous one."""
        page = ("<html>Cedar Valley Selections is a boutique law firm advising on "
                "estate planning, probate and family mediation.</html>")
        pages = {"https://cedarvalleyselections.ca": FakeResponse(
            page, 200, "https://cedarvalleyselections.ca")}
        patch = run(self.prospect(), serve(pages), settings_nodelay).prospect_patch
        assert patch["website"] is None
        assert patch["website_status"] == "not_found"
        assert patch["stage"] == "needs_review"
        assert "does not describe the business" in patch["needs_review_reason"]
        assert any("describes another business" in f["marker"]
                   for f in patch["website_fingerprints"])

    def test_every_candidate_tried_is_recorded(self, settings_nodelay):
        pages = {"https://cedarvalleyselections.ca": FakeResponse(
            self.WRONG_PAGE, 200, "https://cedarvalleyselections.ca")}
        result = run(self.prospect(), serve(pages), settings_nodelay)
        record = result.evidence_patch["website_resolution"]
        assert record["method"] == "constructed from the company name"
        assert record["trusted"] is None
        assert record["stored"] == "https://cedarvalleyselections.ca"
        assert [c["candidate"] for c in record["candidates_tried"]]


class TestTradeNames:
    """A record that carries two names is a company reachable under either.

    Twenty-five Indiana companies are recorded as "<legal entity> dba <trade
    name>", and the site is normally under the trade name. Read as one string
    the name becomes a word sequence that appears on no page anywhere:
    "Transfoam LLC d.b.a. Ourobio" asks for transfoam, llc, d, b, a, ourobio.
    The Indiana spot-check is what surfaced it — no Canadian record carries a
    d/b/a at all.
    """

    VARIANTS = (
        "Transfoam LLC d.b.a. Ourobio",
        "Transfoam LLC dba Ourobio",
        "Transfoam LLC d/b/a Ourobio",
        "Transfoam LLC D.B.A. Ourobio",
        "Transfoam LLC doing business as Ourobio",
        "Transfoam LLC operating as Ourobio",
        "Transfoam LLC o/a Ourobio",
    )

    def test_a_dba_string_is_two_names(self):
        from lib.fingerprints import name_variants
        assert len(name_variants("Transfoam LLC d.b.a. Ourobio")) == 2

    def test_every_written_form_splits_the_same_way(self):
        """One company writes it seven ways; all seven are the same two names."""
        from lib.fingerprints import matchable_name, name_variants
        for written in self.VARIANTS:
            assert [matchable_name(v) for v in name_variants(written)] == [
                ["transfoam"], ["ourobio"]], written

    def test_a_name_with_no_marker_is_left_whole(self):
        from lib.fingerprints import name_variants
        assert name_variants("Cedar Valley Selections Inc.") == [
            "Cedar Valley Selections Inc."]

    def test_either_name_matches_the_page(self):
        from lib.fingerprints import full_name_present
        name = "Transfoam LLC d.b.a. Ourobio"
        assert full_name_present("Ourobio is a biomaterials company.", name)
        assert full_name_present("Transfoam makes foam products.", name)

    def test_the_longer_marker_wins(self):
        """'dba as' before 'dba', or the trade name starts with the word 'as'."""
        from lib.fingerprints import matchable_name, name_variants
        variants = name_variants(
            "SERVICE SPECIALTIES OF ELKHART INCORPORATED (DBA as Liftco Inc.)")
        assert matchable_name(variants[-1]) == ["liftco"]

    def test_a_suffix_inside_the_name_is_dropped(self):
        from lib.fingerprints import matchable_name
        assert matchable_name("Transfoam LLC") == ["transfoam"]

    def test_but_never_the_first_word(self):
        """A company called "Limited Brands" is identified by "Limited"."""
        from lib.fingerprints import matchable_name
        assert matchable_name("Limited Brands Inc.") == ["limited", "brands"]

    def test_the_cedar_rule_is_untouched(self):
        from lib.fingerprints import full_name_present
        assert full_name_present("Cedar is a healthcare payments platform.",
                                 "Cedar Valley Selections Inc.") is None


class TestInitials:
    """A company writes its initials with periods; its website writes them without.

    Nineteen companies across the two sets are recorded with adjacent initials —
    B-D Industries, S.U.S. Cast Products, D&M Tool, K&K, M&C Tech. Asking for
    u, b, klem on a page that says "UB Klem" never matches, and refusing a
    company's own site over a punctuation convention is the same error as
    refusing it over "&" against "and".
    """

    KLEM = "Rudeck, LLC dba U.B. Klem Furniture Company"

    def test_the_page_may_drop_the_periods(self):
        from lib.fingerprints import full_name_present
        assert full_name_present("Welcome to UB Klem Furniture, makers of chairs.",
                                 self.KLEM)

    def test_or_keep_them(self):
        from lib.fingerprints import full_name_present
        assert full_name_present("Welcome to U.B. Klem Furniture Company.", self.KLEM)

    def test_a_longer_run_joins_too(self):
        from lib.fingerprints import full_name_present
        assert full_name_present("SUS Cast Products is an Indiana foundry.",
                                 "S.U.S. Cast Products, Inc")

    def test_an_ampersand_pair_joins(self):
        from lib.fingerprints import full_name_present
        assert full_name_present("DM Tool Corporation serves the automakers.",
                                 "D&M Tool Corporation")

    def test_it_does_not_weaken_the_cedar_rule(self):
        from lib.fingerprints import full_name_present
        assert full_name_present("Cedar is a healthcare payments platform.",
                                 "Cedar Valley Selections Inc.") is None

    def test_ordinary_names_are_untouched(self):
        from lib.fingerprints import full_name_present
        assert full_name_present("Accutech Mold and Machine",
                                 "Accutech Mold & Machine, Inc.")
