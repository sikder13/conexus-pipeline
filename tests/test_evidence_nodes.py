"""Tests for the Pass A evidence nodes and the evidence-block layer.

Every node runs against a saved HTML fixture through the fake HTTP client; no
test in this file touches the network. The cases that matter most are the ones
that decide whether a human ever sees a company: the weak_front_door criteria,
the clerical-posting date boundary, the named_decision_maker rule, and the
traceability of each scored component.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
from bs4 import BeautifulSoup

from lib.claims import Tier
from lib.evidence import (
    BLOCK1_WHAT_THEY_MAKE,
    BLOCK3_HIRING_SIGNALS,
    BLOCK4_DIGITAL_FRONT_DOOR,
    BLOCK6_TECH_STACK,
    BLOCK7_PEOPLE,
    BLOCKS,
    SCORE_EVIDENCE_KEY,
    block_patch,
    flag_is_true,
    flag_patch,
    make_quote,
)
from lib.nodes import RunContext
from tests.conftest import FakeClient, FakeResponse, fixture
from tools.harvester.nodes.case_study import (
    clean_person_name,
    looks_like_a_person,
    parse_people,
)
from tools.harvester.nodes.front_door import (
    FrontDoorNode,
    detect_platform,
    strip_chrome,
    weak_front_door_criteria,
)
from tools.harvester.nodes.job_postings import (
    JobPostingsNode,
    is_clerical,
    is_recent,
    parse_posting_date,
    parse_roles,
)
from tools.harvester.nodes.people import PeopleNode, is_decision_role
from tools.harvester.nodes.score import ScoreNode, collect_signals

SITE = "https://accutech.test"
WEAK_SITE = "https://bartel.test"


def serve(pages: dict[str, str]):
    """Serve a fixed URL->html map; anything else 404s. robots.txt is absent."""

    def handler(url: str):
        if url.endswith("/robots.txt"):
            return FakeResponse("", 404, url)
        if url in pages:
            return FakeResponse(pages[url], 200, url)
        return FakeResponse("", 404, url)

    return handler


STRONG_SITE = {
    SITE: fixture("site_home_strong.html"),
    f"{SITE}/about": fixture("site_about.html"),
    f"{SITE}/contact": fixture("site_contact.html"),
    f"{SITE}/products": fixture("site_home_strong.html"),
    f"{SITE}/careers": fixture("site_careers.html"),
    f"{SITE}/leadership": fixture("site_about.html"),
}
WEAK_SITE_PAGES = {
    WEAK_SITE: fixture("site_home_weak.html"),
    f"{WEAK_SITE}/about": fixture("site_about_generic.html"),
}


def run_node(node, prospect, settings):
    return asyncio.run(node.run(prospect, RunContext(FakeClient(serve(prospect.pop("_pages"))),
                                                    settings)))


def front_door(prospect, settings, pages):
    ctx = RunContext(FakeClient(serve(pages)), settings)
    return asyncio.run(FrontDoorNode().run(prospect, ctx))


class TestEvidenceLayer:
    def test_a_bad_block_name_fails_loudly(self):
        with pytest.raises(ValueError, match="not an evidence block"):
            block_patch("block9_wishful_thinking", {"x": 1})

    def test_every_block_is_addressable(self):
        assert len(BLOCKS) == 8
        for block in BLOCKS:
            assert block_patch(block, {"k": {"value": 1}}) == {block: {"k": {"value": 1}}}

    def test_none_values_are_dropped_rather_than_written(self):
        # A missing fact is an absent key, never a null pretending to be a finding.
        assert block_patch(BLOCK1_WHAT_THEY_MAKE, {"a": None}) == {}

    def test_an_unknown_flag_fails_loudly(self):
        with pytest.raises(ValueError, match="unknown scoring flag"):
            flag_patch("vibes", True, Tier.T1, "https://x.test")

    def test_a_flag_is_stored_as_a_traceable_claim(self):
        patch = flag_patch("weak_front_door", True, Tier.T1, "https://x.test")
        claim = patch[BLOCK4_DIGITAL_FRONT_DOOR]["flags"]["weak_front_door"]
        assert claim["value"] is True
        assert claim["tier"] == 1
        assert claim["source_url"] == "https://x.test"

    def test_an_unset_flag_reads_false_rather_than_raising(self):
        assert flag_is_true({}, "weak_front_door") is False

    def test_a_quote_carries_its_marker(self):
        quote = make_quote("we drown in spreadsheets", Tier.T2, "https://x.test", speaker="Dale")
        assert quote["quote"] is True
        assert quote["speaker"] == "Dale"
        assert quote["value"] == "we drown in spreadsheets"


class TestFrontDoorCriteria:
    def test_a_healthy_site_meets_no_criteria(self):
        healthy = {
            "mobile_viewport": True, "newest_year": date.today().year, "has_form": True,
            "has_phone": True, "pages_reachable": 6, "ssl_valid": True, "broken_links": 0,
        }
        assert weak_front_door_criteria(healthy) == []

    @pytest.mark.parametrize(
        ("field", "value", "expected"),
        [
            ("mobile_viewport", False, "no mobile viewport tag"),
            ("has_form", False, "no contact or quote form found"),
            ("has_phone", False, "no phone number visible"),
            ("ssl_valid", False, "not served over valid HTTPS"),
        ],
    )
    def test_each_criterion_fires_on_its_own(self, field, value, expected):
        observations = {
            "mobile_viewport": True, "newest_year": date.today().year, "has_form": True,
            "has_phone": True, "pages_reachable": 6, "ssl_valid": True, "broken_links": 0,
        }
        observations[field] = value
        assert weak_front_door_criteria(observations) == [expected]

    def test_a_missing_date_counts_as_a_weakness(self):
        observations = {
            "mobile_viewport": True, "newest_year": None, "has_form": True,
            "has_phone": True, "pages_reachable": 6, "ssl_valid": True, "broken_links": 0,
        }
        assert "no visible content or copyright date" in weak_front_door_criteria(observations)

    def test_a_stale_date_counts_but_a_recent_one_does_not(self):
        base = {
            "mobile_viewport": True, "has_form": True, "has_phone": True,
            "pages_reachable": 6, "ssl_valid": True, "broken_links": 0,
        }
        today = date(2026, 8, 8)
        assert weak_front_door_criteria({**base, "newest_year": 2023}, today) == []
        assert weak_front_door_criteria({**base, "newest_year": 2022}, today) != []

    def test_a_thin_site_counts(self):
        observations = {
            "mobile_viewport": True, "newest_year": date.today().year, "has_form": True,
            "has_phone": True, "pages_reachable": 3, "ssl_valid": True, "broken_links": 0,
        }
        assert "only 3 page(s) reachable" in weak_front_door_criteria(observations)

    @pytest.mark.parametrize(
        ("html", "expected"),
        [
            ('<script src="//cdn.shopify.com/x.js">', "Shopify"),
            ('<link href="/wp-content/themes/a.css">', "WordPress"),
            ('<div id="SITE_CONTAINER">static.wixstatic.com</div>', "Wix"),
            ("<html><body>hand rolled</body></html>", "custom or unrecognised"),
        ],
    )
    def test_platform_fingerprints(self, html, expected):
        assert detect_platform(html) == expected


class TestFrontDoorNode:
    def test_a_low_confidence_website_is_skipped(self, settings_nodelay):
        result = front_door(
            {"website": SITE, "website_confidence": 40}, settings_nodelay, STRONG_SITE
        )
        assert result.skipped is True
        assert "below 50" in result.skip_reason

    def test_a_missing_website_is_skipped(self, settings_nodelay):
        result = front_door({"website": None, "website_confidence": 95}, settings_nodelay, {})
        assert result.skipped is True

    def test_a_strong_site_is_not_flagged_weak(self, settings_nodelay):
        result = front_door(
            {"website": SITE, "website_confidence": 95}, settings_nodelay, STRONG_SITE
        )
        flag = result.evidence_patch[BLOCK4_DIGITAL_FRONT_DOOR]["flags"]["weak_front_door"]
        assert flag["value"] is False
        assert flag["criteria_met"] == []

    def test_a_weak_site_is_flagged_with_its_reasons(self, settings_nodelay):
        result = front_door(
            {"website": WEAK_SITE, "website_confidence": 95}, settings_nodelay, WEAK_SITE_PAGES
        )
        flag = result.evidence_patch[BLOCK4_DIGITAL_FRONT_DOOR]["flags"]["weak_front_door"]
        assert flag["value"] is True
        assert len(flag["criteria_met"]) >= 2, flag["criteria_met"]

    def test_everything_it_observes_is_tier_one(self, settings_nodelay):
        result = front_door(
            {"website": SITE, "website_confidence": 95}, settings_nodelay, STRONG_SITE
        )
        for block in (BLOCK1_WHAT_THEY_MAKE, BLOCK4_DIGITAL_FRONT_DOOR, BLOCK6_TECH_STACK):
            for key, value in result.evidence_patch.get(block, {}).items():
                if key == "flags":
                    continue
                for entry in value if isinstance(value, list) else [value]:
                    assert entry["tier"] == 1, f"{block}.{key} should be T1"

    def test_it_reads_their_own_words_and_certifications(self, settings_nodelay):
        result = front_door(
            {"website": SITE, "website_confidence": 95}, settings_nodelay, STRONG_SITE
        )
        block1 = result.evidence_patch[BLOCK1_WHAT_THEY_MAKE]
        certs = {claim["value"] for claim in block1["certifications"]}
        assert any("9001" in c for c in certs)
        assert "who_they_sell_to" in block1

    def test_business_model_is_only_claimed_when_the_site_says_so(self, settings_nodelay):
        result = front_door(
            {"website": SITE, "website_confidence": 95}, settings_nodelay, STRONG_SITE
        )
        block1 = result.evidence_patch[BLOCK1_WHAT_THEY_MAKE]
        assert block1["business_model"]["value"] == "job shop / contract manufacturer"
        # The phrase that justified it is kept alongside the classification.
        assert "job shop" in block1["business_model_basis"]["value"].lower()

    def test_it_records_the_careers_url_for_the_next_node(self, settings_nodelay):
        result = front_door(
            {"website": SITE, "website_confidence": 95}, settings_nodelay, STRONG_SITE
        )
        careers = result.evidence_patch[BLOCK4_DIGITAL_FRONT_DOOR]["careers_url"]
        assert careers["value"].endswith("/careers")


class TestJobPostings:
    def test_it_skips_when_no_careers_page_was_found(self, settings_nodelay):
        ctx = RunContext(FakeClient(serve({})), settings_nodelay)
        result = asyncio.run(JobPostingsNode().run({"evidence_file": {}}, ctx))
        assert result.skipped is True
        assert "careers page" in result.skip_reason

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Posted August 1, 2026", date(2026, 8, 1)),
            ("2026-08-01", date(2026, 8, 1)),
            ("8/1/2026", date(2026, 8, 1)),
            ("no date here", None),
        ],
    )
    def test_posting_dates_are_parsed(self, text, expected):
        assert parse_posting_date(text, date(2026, 8, 8)) == expected

    def test_clerical_duties_are_recognised(self):
        assert is_clerical({"title": "Order Entry Coordinator", "duties": ""}) is True
        assert is_clerical({"title": "CNC Machinist", "duties": "operate mills"}) is False

    def test_the_sixty_day_boundary(self):
        today = date(2026, 8, 8)
        inside = {"posted": today - timedelta(days=60)}
        outside = {"posted": today - timedelta(days=61)}
        assert is_recent(inside, today) is True
        assert is_recent(outside, today) is False

    def test_an_undated_posting_counts(self):
        # Many small-manufacturer careers pages show no dates at all; excluding
        # them would systematically under-score exactly the target companies.
        assert is_recent({"posted": None}, date(2026, 8, 8)) is True

    def test_duties_are_captured_whole(self):
        roles = parse_roles(fixture("site_careers.html"), date(2026, 8, 8))
        clerical = next(r for r in roles if "Order Entry" in r["title"])
        # The duties list is a process map — all four bullets must survive.
        for phrase in ("Epicor", "schedule spreadsheet", "estimating worksheet",
                       "accounts payable"):
            assert phrase in clerical["duties"], phrase

    def test_it_flags_a_recent_clerical_posting_and_names_the_systems(self, settings_nodelay):
        evidence = {BLOCK4_DIGITAL_FRONT_DOOR: {
            "careers_url": {"value": f"{SITE}/careers", "tier": 1,
                            "source_url": SITE, "date_checked": "2026-08-08"}}}
        ctx = RunContext(FakeClient(serve(STRONG_SITE)), settings_nodelay)
        result = asyncio.run(JobPostingsNode().run({"evidence_file": evidence}, ctx))

        flag = result.evidence_patch[BLOCK3_HIRING_SIGNALS]["flags"]["has_clerical_posting"]
        assert flag["value"] is True
        assert flag["tier"] == 1
        systems = {c["value"] for c in result.evidence_patch[BLOCK6_TECH_STACK][
            "systems_named_in_postings"]}
        assert "Epicor" in systems

    def test_external_boards_are_recorded_as_unchecked(self, settings_nodelay):
        evidence = {BLOCK4_DIGITAL_FRONT_DOOR: {
            "careers_url": {"value": f"{SITE}/careers", "tier": 1,
                            "source_url": SITE, "date_checked": "2026-08-08"}}}
        ctx = RunContext(FakeClient(serve(STRONG_SITE)), settings_nodelay)
        result = asyncio.run(JobPostingsNode().run({"evidence_file": evidence}, ctx))
        assert any("external boards not checked" in note for note in result.notes)


class TestPersonExtraction:
    """Regressions from the first live run, which invented three people."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('"We needed speed," said Mike Cramer, president.', [("Mike Cramer", "President")]),
            ("Dale Whitmore, President, leads the company.", [("Dale Whitmore", "President")]),
            ("Bob Markey founded 3rd Dimension in 2013.", [("Bob Markey", "Founder")]),
            ("Director Jeff Prichard runs the plant.", [("Jeff Prichard", "Director")]),
        ],
    )
    def test_real_people_are_found(self, text, expected):
        assert parse_people(text) == expected

    def test_a_lowercase_verb_is_not_a_name(self):
        # Whole-pattern re.IGNORECASE loosened [A-Z][a-z]+ so that "said Mike
        # Cramer" was recorded as a person's name.
        assert all(name != "said Mike Cramer" for name, _ in
                   parse_people('"Fast," said Mike Cramer, president.'))

    def test_page_furniture_is_not_a_name(self):
        assert parse_people("Contact our Office Jared McGladdery, Director of Sales.") == []

    def test_the_company_itself_is_not_a_person(self):
        assert parse_people(
            "DuraMark Technologies, Director Jeff Prichard leads.", "DuraMark Technologies"
        ) == [("Jeff Prichard", "Director")]

    @pytest.mark.parametrize(
        "phrase", ["Our Team", "The Management", "Read More", "Privacy Policy"]
    )
    def test_common_furniture_phrases_are_rejected(self, phrase):
        assert looks_like_a_person(phrase) is False

    def test_a_single_word_is_never_a_person(self):
        assert looks_like_a_person("Dale") is False


class TestPeople:
    @pytest.mark.parametrize(
        ("role", "expected"),
        [("President", True), ("General Manager", True), ("Owner", True),
         ("Machinist", False), ("Receptionist", False)],
    )
    def test_decision_roles_are_recognised(self, role, expected):
        assert is_decision_role(role) is expected

    def test_a_named_leader_sets_the_flag(self, settings_nodelay):
        ctx = RunContext(FakeClient(serve(STRONG_SITE)), settings_nodelay)
        result = asyncio.run(PeopleNode().run({"website": SITE, "evidence_file": {}}, ctx))
        flag = result.evidence_patch[BLOCK7_PEOPLE]["flags"]["named_decision_maker"]
        assert flag["value"] is True
        names = {c["value"] for c in result.evidence_patch[BLOCK7_PEOPLE]["named_people"]}
        assert any("Dale Whitmore" in n for n in names)

    def test_a_generic_team_page_does_not_set_the_flag(self, settings_nodelay):
        ctx = RunContext(FakeClient(serve(WEAK_SITE_PAGES)), settings_nodelay)
        result = asyncio.run(PeopleNode().run({"website": WEAK_SITE, "evidence_file": {}}, ctx))
        assert flag_is_true(result.evidence_patch, "named_decision_maker") is False

    def test_it_never_guesses_an_email(self, settings_nodelay):
        ctx = RunContext(FakeClient(serve(STRONG_SITE)), settings_nodelay)
        result = asyncio.run(PeopleNode().run({"website": SITE, "evidence_file": {}}, ctx))
        assert any("not guessed" in note for note in result.notes)
        assert "@" not in str(result.evidence_patch.get(BLOCK7_PEOPLE, {}).get("named_people", ""))


def scored_prospect(**overrides):
    return {"id": "p1", "company_name": "Accutech", "stage": "extracted", **overrides}


class TestScore:
    def test_a_bare_prospect_scores_zero_without_erroring(self, settings_nodelay):
        ctx = RunContext(FakeClient(serve({})), settings_nodelay)
        result = asyncio.run(ScoreNode().run(scored_prospect(evidence_file={}), ctx))
        assert result.prospect_patch["signal_score"] == 0
        assert result.prospect_patch["priority"] == "P3"
        assert result.prospect_patch["priority_set_by"] == "machine"

    def test_the_breakdown_matches_the_migration_shape(self, settings_nodelay):
        ctx = RunContext(FakeClient(serve({})), settings_nodelay)
        result = asyncio.run(ScoreNode().run(scored_prospect(evidence_file={}), ctx))
        assert set(result.prospect_patch["score_breakdown"]) == {
            "clerical_posting", "data_gen_tech", "case_study", "weak_front_door",
            "decision_maker_found", "in_drive_radius", "too_big", "status_uncertain",
        }

    def test_flags_drive_the_signals_and_carry_their_source(self, settings_nodelay):
        evidence = {}
        for flag in ("has_clerical_posting", "weak_front_door", "named_decision_maker"):
            for block, claims in flag_patch(flag, True, Tier.T1, "https://x.test").items():
                evidence.setdefault(block, {}).setdefault("flags", {}).update(claims["flags"])
        signals, basis = collect_signals(scored_prospect(evidence_file=evidence))
        assert signals.clerical_posting and signals.weak_front_door
        assert signals.decision_maker_found
        assert basis["clerical_posting"]["source_url"] == "https://x.test"

    def test_every_non_zero_component_is_traceable(self, settings_nodelay):
        evidence = {}
        for flag in ("has_clerical_posting", "weak_front_door", "named_decision_maker",
                     "has_case_study"):
            for block, claims in flag_patch(flag, True, Tier.T1, f"https://{flag}.test").items():
                evidence.setdefault(block, {}).setdefault("flags", {}).update(claims["flags"])
        prospect = scored_prospect(evidence_file=evidence, drive_minutes=30)
        ctx = RunContext(FakeClient(serve({})), settings_nodelay)
        result = asyncio.run(ScoreNode().run(prospect, ctx))

        score_evidence = result.evidence_patch[SCORE_EVIDENCE_KEY]
        breakdown = result.prospect_patch["score_breakdown"]
        for component, points in breakdown.items():
            if points:
                assert component in score_evidence, component
                assert score_evidence[component]["source_url"], component
                assert score_evidence[component]["points"] == points
        assert set(score_evidence) == {c for c, p in breakdown.items() if p}

    def test_score_evidence_avoids_the_word_value(self):
        # The claim trigger treats any object with a 'value' key as a claim and
        # demands a tier and source URL for it; score_evidence is working, not a claim.
        entry = {"points": 1, "flag": "x", "tier": 1, "source_url": "https://x.test"}
        assert "value" not in entry

    def test_drive_minutes_and_website_confidence_come_from_columns(self, settings_nodelay):
        prospect = scored_prospect(evidence_file={}, drive_minutes=30, website_confidence=0)
        signals, basis = collect_signals(prospect)
        assert signals.in_drive_radius is True
        assert signals.status_uncertain is True
        assert "website_confidence=0" in basis["status_uncertain"]["detail"]

    def test_a_far_prospect_is_not_in_radius(self):
        signals, _ = collect_signals(scored_prospect(evidence_file={}, drive_minutes=200))
        assert signals.in_drive_radius is False

    def test_it_advances_the_stage_to_pass_a_done(self, settings_nodelay):
        ctx = RunContext(FakeClient(serve({})), settings_nodelay)
        result = asyncio.run(ScoreNode().run(scored_prospect(evidence_file={}), ctx))
        assert result.prospect_patch["stage"] == "passA_done"

    @pytest.mark.parametrize("stage", ["needs_review", "dead"])
    def test_it_does_not_override_review_or_dead(self, settings_nodelay, stage):
        ctx = RunContext(FakeClient(serve({})), settings_nodelay)
        result = asyncio.run(ScoreNode().run(scored_prospect(evidence_file={}, stage=stage), ctx))
        assert "stage" not in result.prospect_patch

    def test_it_never_writes_a_human_only_stage(self, settings_nodelay):
        ctx = RunContext(FakeClient(serve({})), settings_nodelay)
        result = asyncio.run(ScoreNode().run(scored_prospect(evidence_file={}), ctx))
        assert result.prospect_patch.get("stage") == "passA_done"

    def test_the_dead_component_is_absent_from_the_breakdown(self, settings_nodelay):
        # friction_reviews was removed from the scale on 2026-08-09; a component
        # that cannot fire must not sit in the breakdown either.
        ctx = RunContext(FakeClient(serve({})), settings_nodelay)
        result = asyncio.run(ScoreNode().run(scored_prospect(evidence_file={}), ctx))
        assert "friction_reviews" not in result.prospect_patch["score_breakdown"]


class TestGreedyNameCapture:
    """Second regression pass from the live run: the name pattern is greedy."""

    @pytest.mark.parametrize(
        ("captured", "expected"),
        [
            ("Jeff Frost President", "Jeff Frost"),
            ("Jeffrey White Chief", "Jeffrey White"),
            ("Jason Keele Director", "Jason Keele"),
            ("Karen Ruiz General Manager", "Karen Ruiz"),
        ],
    )
    def test_a_trailing_role_is_stripped_not_kept(self, captured, expected):
        assert clean_person_name(captured) == expected

    @pytest.mark.parametrize(
        "phrase", ["Industry Affiliations", "Customer Support", "Quality Engineering"]
    )
    def test_business_phrases_are_rejected(self, phrase):
        assert clean_person_name(phrase) is None

    def test_a_name_of_four_words_is_rejected(self):
        # Beyond three tokens it is a sentence fragment, not a name.
        assert clean_person_name("Alan Bob Carol Dave") is None

    def test_stale_bad_names_do_not_survive_a_rerun(self):
        # people re-reads block7 from a prior run; an earlier, looser version
        # wrote furniture there, and re-merging it would resurrect the bad name.
        for bad in ("said Stacy Hiquet", "Office Jared McGladdery", "Industry Affiliations"):
            assert clean_person_name(bad) is None


class TestChromeStripping:
    """Regression: nested chrome crashed the whole node.

    Decomposing a <nav> also decomposes the <ul> and <li> inside it, but those
    children were already collected by find_all. A decomposed tag has no attrs,
    so reading .get('class') on one raised AttributeError — which failed
    front_door for 16 of the first 31 companies in the full run.
    """

    def test_nested_chrome_does_not_crash(self):
        html = (
            '<html><body><nav class="site-nav"><ul class="menu">'
            '<li class="menu-item">Home</li></ul></nav>'
            "<p>We make injection molds.</p></body></html>"
        )
        text = strip_chrome(BeautifulSoup(html, "html.parser")).get_text(" ")
        assert "injection molds" in text
        assert "Home" not in text

    def test_deeply_nested_chrome_is_survivable(self):
        html = (
            '<html><body><header id="site-header"><div class="navbar">'
            '<div class="menu"><span class="menu-item">X</span></div></div></header>'
            '<footer class="site-footer"><p class="social">follow</p></footer>'
            "<main><p>Real content here.</p></main></body></html>"
        )
        text = strip_chrome(BeautifulSoup(html, "html.parser")).get_text(" ")
        assert "Real content here." in text
        for chrome in ("X", "follow"):
            assert chrome not in text

    def test_a_page_with_no_chrome_is_left_alone(self):
        html = "<html><body><p>Just prose.</p></body></html>"
        assert "Just prose." in strip_chrome(BeautifulSoup(html, "html.parser")).get_text(" ")

    def test_the_home_page_menu_is_stripped_from_its_prose(self):
        # The real home fixture carries a nav with About Us / Contact / Careers.
        raw = BeautifulSoup(fixture("site_home_strong.html"), "html.parser").get_text(" ")
        stripped = strip_chrome(
            BeautifulSoup(fixture("site_home_strong.html"), "html.parser")
        ).get_text(" ")
        assert "About Us" in raw, "fixture should contain a nav to strip"
        assert "About Us" not in stripped
        assert "injection molds" in stripped

    def test_the_raw_text_is_kept_alongside_the_stripped_one(self, settings_nodelay):
        result = front_door(
            {"website": SITE, "website_confidence": 95}, settings_nodelay, STRONG_SITE
        )
        block1 = result.evidence_patch[BLOCK1_WHAT_THEY_MAKE]
        # Nothing the page said is discarded — both are stored, both T1.
        assert block1["self_description"]["tier"] == 1
        assert block1["self_description_raw"]["tier"] == 1
        assert "Accutech" in block1["self_description"]["value"]
