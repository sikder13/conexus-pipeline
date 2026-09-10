"""Tests for ecosystem rival discovery and the feature-gap comparison.

The module amends an earlier decision — that an industry directory is a T3
aggregator this pipeline may not repeat — by separating discovery from
observation. So the tests are mostly about keeping that separation intact: a
directory may say where to look and may never fill a cell, every cell carries
the page it was read from, and the velocity sentence states the denominator it
counted rather than implying a claim about the market.
"""

from __future__ import annotations

import pytest

from lib import rivals

FULL = """
<html><head><meta name="viewport" content="width=device-width"></head><body>
<h1>Acme Fabrication</h1>
<p>We offer laser cutting, press brake forming, CNC machining and robotic welding
to AS9100D and ISO 9001:2015. Two-week lead times on most jobs.</p>
<p>Use our customer portal to upload your CAD files and get an instant quote.</p>
</body></html>
"""

BARE = """
<html><body><h1>Quiet Metal Works</h1>
<p>We have been making parts since 1974. Call us to discuss your project.</p>
<a href="tel:3175550100">Call</a></body></html>
"""

FORMY = """
<html><head><meta name="viewport" content="x"></head><body>
<p>ISO 9001 certified machining. Request a quote below.</p>
<form><input name="email"></form></body></html>
"""


def observe(html, url="https://example.com", name="Example", **kw):
    return rivals.observe_site(html, url, name, **kw)


class TestObservationIsReadOffTheirOwnSite:
    def test_capabilities_come_from_a_closed_vocabulary(self):
        found = observe(FULL)
        assert "laser cutting" in found.capabilities
        assert "cnc machining" in found.capabilities
        assert "welding" in found.capabilities

    def test_a_site_that_advertises_nothing_yields_nothing(self):
        assert observe(BARE).capabilities == []

    def test_certifications_are_captured_as_written(self):
        assert observe(FULL).certifications == ["AS9100D", "ISO 9001:2015"]

    def test_a_lead_time_is_captured_verbatim_not_normalised(self):
        # "Two-week lead times" and "quotes within 24 hours" are different
        # promises; turning both into a number loses which one was made.
        assert observe(FULL).lead_times == ["Two-week lead times"]

    @pytest.mark.parametrize("text,expected", [
        ("Two-week lead times on most jobs.", "Two-week lead times"),
        ("24-hour quote turnaround.", "24-hour quote"),
        ("Lead times of 3 to 5 days.", "Lead times of 3 to 5 days"),
        ("same-day quotes", "same-day quotes"),
    ])
    def test_the_shapes_a_lead_time_is_actually_written_in(self, text, expected):
        assert expected in [m.group(0) for m in rivals.LEAD_TIME.finditer(text)]

    def test_automation_words_are_matched_on_boundaries(self):
        # "mes" sits inside "times", so a shop saying "two-week lead times" was
        # recorded as describing a manufacturing execution system.
        assert "mes" not in observe(
            "<p>Two-week lead times on most jobs.</p>").automation_terms

    def test_a_portal_beats_a_form_beats_a_phone(self):
        assert observe(FULL).quoting_path == "portal"
        assert observe(FORMY).quoting_path == "form"
        assert observe(BARE).quoting_path == "phone"

    def test_every_feature_carries_the_page_it_was_read_from(self):
        found = observe(FULL, url="https://acme.example/capabilities")
        for evidence in found.features.values():
            assert evidence.source_url == "https://acme.example/capabilities"

    def test_a_present_feature_carries_the_words_that_say_so(self):
        found = observe(FULL)
        assert "portal" in found.features["quoting_portal"].quote.lower()
        assert found.features["published_certification"].quote

    def test_the_discovery_route_travels_with_the_observation(self):
        found = observe(FULL, channel=rivals.DIRECTORY,
                        discovered_via="listed by the association")
        assert found.channel == rivals.DIRECTORY
        assert found.discovered_via == "listed by the association"


class TestTheGapTable:
    def build(self, rival_count=3):
        subject = observe(BARE, url="http://quiet.example", name="Quiet Metal Works")
        others = [
            observe(FULL, url=f"https://rival{i}.example", name=f"Rival {i}")
            for i in range(rival_count)
        ]
        return rivals.build_gap_table("Quiet Metal Works", subject, others)

    def test_a_gap_is_something_most_readable_rivals_have_and_the_prospect_lacks(self):
        gaps = {row.feature for row in self.build().gaps()}
        assert "quoting_portal" in gaps
        assert "published_certification" in gaps

    def test_something_the_prospect_also_has_is_not_a_gap(self):
        table = self.build()
        secure = next(r for r in table.rows if r.feature == "secure_site")
        assert secure.prospect_has is False
        https_row = next(r for r in table.rows if r.feature == "mobile_ready")
        assert https_row.is_gap

    def test_one_rival_is_not_enough_to_call_something_a_gap(self):
        # A velocity metric over a sample of one is arithmetic over noise.
        assert self.build(rival_count=1).gaps() == []

    def test_the_velocity_sentence_states_what_it_counted(self):
        line = next(v for v in self.build().velocity() if v.feature == "quoting_portal")
        assert "3 of the 3 regional shops we could read" in line.sentence
        assert line.rivals_measured == 3

    def test_the_sentence_reads_as_english_for_every_feature(self):
        for line in self.build().velocity():
            assert " advertise a secure site" not in line.sentence
            assert line.sentence.endswith("does not.")

    def test_every_velocity_line_carries_the_pages_behind_it(self):
        for line in self.build().velocity():
            assert line.source_urls
            assert all(u.startswith("http") for u in line.source_urls)

    def test_the_basis_names_the_discovery_routes(self):
        assert "our own dataset" in self.build().basis

    def test_the_basis_says_that_nothing_is_estimated(self):
        assert "No revenue, share or headcount is estimated" in self.build().basis

    def test_a_comparison_with_no_rivals_says_so_rather_than_comparing(self):
        table = rivals.build_gap_table("Alone Ltd", None, [])
        assert table.usable is False
        assert "no comparison to make" in table.basis.lower()
        assert table.velocity() == []

    def test_a_thin_comparison_records_its_own_shortfall(self):
        table = rivals.build_gap_table(
            "Thin Co", observe(BARE), [observe(FULL)], shortfall="only 1 of a wanted 3")
        assert "only 1" in table.shortfall

    def test_the_figures_are_only_the_counts_it_computed(self):
        figures = self.build().figures()
        assert 3.0 in figures
        assert 47.0 not in figures


class TestDiscoveryIsHonestAboutWhatWorks:
    def test_every_curated_directory_says_whether_it_can_be_read(self):
        for source in rivals.DIRECTORIES:
            assert source.note.strip()
            assert source.url.startswith("https://")

    def test_a_login_gated_directory_says_that_is_why_it_is_not_used(self):
        gated = [d for d in rivals.DIRECTORIES if "login" in d.note]
        assert gated, "the associations that gate their member lists are recorded"

    def test_none_of_them_yields_members_today_and_that_is_recorded(self):
        assert rivals.READABLE_TODAY == ()

    def test_a_directory_is_selected_by_region_and_family(self):
        found = rivals.directories_for("IN", "metal_fabrication")
        assert any(d.source_id == "imai.members" for d in found)
        assert not rivals.directories_for("ON", "food_beverage")

    def test_search_without_a_key_reports_itself_not_configured(self):
        status = rivals.search_status(None)
        assert status.configured is False
        assert "SEARCH_API_KEY" in status.line

    def test_there_is_no_keyless_search_fallback_on_purpose(self):
        # The obvious one is scraping a results page, which every engine's
        # robots.txt forbids. Not building it is the rule, not a gap.
        assert "not configured" in rivals.search_status(None).line

    def test_the_queries_a_configured_search_would_run_take_the_briefs_shape(self):
        terms = rivals.search_terms("metal_fabrication", "Indiana")
        assert "metal fabrication Indiana" in terms

    def test_no_query_is_built_without_both_halves(self):
        assert rivals.search_terms("", "Indiana") == []
        assert rivals.search_terms("metal fabrication", "") == []


class TestDirectoryExtraction:
    LISTING = """
    <html><body><ul>
      <li><a href="https://memberone.example/">Member One Manufacturing</a></li>
      <li><a href="https://membertwo.example/about">Member Two Fabrication</a></li>
      <li><a href="https://www.facebook.com/assoc">Follow us</a></li>
      <li><a href="/members/page/2">Next page</a></li>
    </ul></body></html>
    """

    def test_it_takes_the_links_that_leave_the_publishers_own_site(self):
        found = dict(rivals.company_links(
            self.LISTING, "https://assoc.example/members"))
        assert found["Member One Manufacturing"] == "https://memberone.example"
        assert found["Member Two Fabrication"] == "https://membertwo.example"

    def test_it_leaves_the_publishers_own_pages_alone(self):
        found = dict(rivals.company_links(
            self.LISTING, "https://assoc.example/members"))
        assert "Next page" not in found

    def test_it_leaves_social_links_alone(self):
        found = dict(rivals.company_links(
            self.LISTING, "https://assoc.example/members"))
        assert "Follow us" not in found

    def test_a_page_with_no_members_yields_nothing_rather_than_noise(self):
        assert rivals.company_links(
            "<html><body><p>Members log in here.</p></body></html>",
            "https://assoc.example/members") == []
