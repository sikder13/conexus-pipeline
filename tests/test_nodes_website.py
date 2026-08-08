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


def prospect_with_site(url: str | None, name: str = COMPANY) -> dict:
    evidence = {"source": {"website": claim(url)}} if url else {"source": {}}
    return {"id": "p1", "company_name": name, "evidence_file": evidence}


def serve(pages: dict[str, FakeResponse], robots: str = ""):
    """Handler serving a fixed page map; anything unlisted is a 404."""

    def handler(url: str):
        if url.endswith("/robots.txt"):
            return FakeResponse(robots, 200 if robots else 404, url)
        return pages.get(url, FakeResponse("", 404, url))

    return handler


def run(prospect, handler, settings):
    ctx = RunContext(FakeClient(handler), settings)
    return asyncio.run(ResolveWebsite().run(prospect, ctx))


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
            "https://accutech.com",
        ]

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
            "<html>Accutech Mold</html>", 200, "https://accutech.com")}
        result = run(prospect_with_site("https://accutech.com"), serve(pages), settings_nodelay)
        assert result.evidence_patch["identity"]["website"]["tier"] == 1

    def test_an_untrusted_site_is_recorded_as_a_t4_hypothesis(self, settings_nodelay):
        pages = {"https://accutech.com": FakeResponse(
            "<html>This domain is for sale</html>", 200, "https://accutech.com")}
        result = run(prospect_with_site("https://accutech.com"), serve(pages), settings_nodelay)
        assert result.evidence_patch["identity"]["website"]["tier"] == 4

    def test_every_outcome_explains_its_reasoning(self, settings_nodelay):
        pages = {"https://accutech.com": FakeResponse(
            "<html>Accutech Mold</html>", 200, "https://accutech.com")}
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

    def test_anything_below_the_threshold_goes_to_review(self, settings_nodelay):
        for score in CONFIDENCE.values():
            assert (score < MIN_TRUSTED_CONFIDENCE) == (
                score in (CONFIDENCE["constructed_unverified"], CONFIDENCE["social_only"],
                          CONFIDENCE["parked"], CONFIDENCE["not_found"])
            )

    def test_it_never_writes_a_human_only_stage(self, settings_nodelay):
        result = run(prospect_with_site("https://accutech.com"), serve({}), settings_nodelay)
        assert result.prospect_patch.get("stage") in (None, "needs_review")
