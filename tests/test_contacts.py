"""Tests for contact surfacing.

The operator sends every message by hand, so "who do I write to, and where did
that come from" is the console's most-used question. This module only reports
what a node already recorded: it performs no discovery and guesses no address,
because an invented contact cannot be caught by anyone downstream while an
honest gap can be closed by looking.
"""

from __future__ import annotations

from lib.claims import make_claim
from lib.contacts import (
    NO_CONTACT_NOTE,
    contact_paths,
    named_contacts,
    reachable,
)
from lib.evidence import BLOCK4_DIGITAL_FRONT_DOOR, BLOCK7_PEOPLE

SITE = "https://acmetool.test/"
CASE = "https://conexusindiana.com/case-study/acme/"
PRESS = "https://www.insideindianabusiness.com/articles/round-two"


def claim(value, tier=1, url=SITE, **extra):
    base = make_claim(value, tier, url)
    base.update(extra)
    return base


def prospect(**overrides):
    row = {
        "id": "p1", "company_name": "Acme Tool", "website": SITE,
        "evidence_file": {
            BLOCK4_DIGITAL_FRONT_DOOR: {
                "phone_present": claim(True),
                "pages_read": claim(["home", "contact"]),
            },
            BLOCK7_PEOPLE: {"named_people": [
                claim("Dale Whitmore — President", url=SITE, claimcheck="verbatim"),
            ]},
        },
    }
    row.update(overrides)
    return row


class TestWhatIsSurfaced:
    def test_a_named_person_appears_with_its_source(self):
        paths = named_contacts(prospect())
        assert paths[0].detail == "Dale Whitmore — President"
        assert paths[0].source_url == SITE

    def test_a_phone_is_reported_as_a_pointer_not_a_number(self):
        # The evidence records that a number exists, not what it is. Saying
        # otherwise would imply we hold something we do not.
        phone = next(p for p in contact_paths(prospect()) if p.kind == "phone")
        assert "read it from the page" in phone.detail
        assert phone.source_url == SITE

    def test_a_contact_form_url_is_surfaced(self):
        row = prospect()
        row["evidence_file"][BLOCK4_DIGITAL_FRONT_DOOR]["form_posts_to"] = claim(
            "https://acmetool.test/contact")
        form = next(p for p in contact_paths(row) if p.kind == "form")
        assert form.detail == "https://acmetool.test/contact"

    def test_a_form_with_no_url_still_points_at_the_contact_page(self):
        row = prospect()
        row["evidence_file"][BLOCK4_DIGITAL_FRONT_DOOR]["has_contact_form"] = claim(True)
        form = next(p for p in contact_paths(row) if p.kind == "form")
        assert form.source_url.endswith("/contact")

    def test_the_website_is_always_offered(self):
        assert any(p.kind == "site" for p in contact_paths(prospect()))

    def test_every_path_carries_an_openable_source(self):
        for path in contact_paths(prospect()):
            assert path.source_url.startswith("http"), path


class TestPersonGateStatusTravels:
    def test_a_confirmed_person_carries_no_caution(self):
        row = prospect()
        row["evidence_file"][BLOCK7_PEOPLE]["named_people"] = [
            claim("Dale Whitmore — President", url=SITE),
            claim("Dale Whitmore — President", url=PRESS),
        ]
        assert named_contacts(row)[0].caution == ""

    def test_an_unconfirmed_person_is_shown_but_marked(self):
        # An operator writing by hand may still use it; they may not be
        # misled about which case they are in.
        row = prospect()
        row["evidence_file"][BLOCK7_PEOPLE]["named_people"] = [
            claim("Dale Whitmore — President", url=SITE)]
        person = named_contacts(row)[0]
        assert person.detail.startswith("Dale Whitmore")
        assert person.caution

    def test_a_person_the_checker_could_not_find_says_do_not_use(self):
        row = prospect()
        row["evidence_file"][BLOCK7_PEOPLE]["named_people"] = [
            claim("Office Jared McGladdery — Director", claimcheck="unsupported")]
        assert "do not use" in named_contacts(row)[0].caution


class TestNothingIsInvented:
    def test_no_email_is_ever_produced(self):
        # No discovery, no guessing a pattern from a name and a domain.
        for path in contact_paths(prospect()):
            assert "@" not in path.detail

    def test_a_company_with_nothing_published_is_not_reachable(self):
        bare = prospect(website=None, evidence_file={})
        assert reachable(bare) is False
        assert contact_paths(bare) == []

    def test_a_site_alone_does_not_count_as_reachable(self):
        # A homepage is where you go to look, not a way in.
        only_site = prospect(evidence_file={})
        assert [p.kind for p in contact_paths(only_site)] == ["site"]
        assert reachable(only_site) is False

    def test_the_note_tells_the_operator_to_look_manually(self):
        assert "check the site manually" in NO_CONTACT_NOTE


class TestWhatDiscoveryFound:
    """Rows read off the company's own pages, which hold values not pointers."""

    def discovered(self, **overrides):
        row = prospect()
        row["contacts"] = overrides.pop("contacts", [
            {"kind": "email", "value": "sales@acmetool.test",
             "email_class": "role_based", "source_url": SITE, "tier": 1},
            {"kind": "email", "value": "dale.whitmore@acmetool.test",
             "email_class": "named_person", "source_url": SITE, "tier": 1},
            {"kind": "phone", "value": "(765) 555-1212", "source_url": SITE, "tier": 1},
            {"kind": "linkedin_search", "name": "Dale Whitmore",
             "value": "https://www.linkedin.com/search/results/people/?keywords=x",
             "source_url": SITE, "tier": 4},
        ])
        row.update(overrides)
        return row

    def test_an_address_reaches_the_panel_with_its_class(self):
        paths = contact_paths(self.discovered())
        labels = [p.label for p in paths if p.kind == "email"]
        assert "Email — a named person" in labels
        assert "Email — a role mailbox" in labels

    def test_a_role_mailbox_warns_against_opening_with_a_name(self):
        role = next(p for p in contact_paths(self.discovered())
                    if p.label == "Email — a role mailbox")
        assert "do not open with a name" in role.caution

    def test_a_real_number_replaces_the_pointer_to_it(self):
        # A row saying "a number exists somewhere on this page" sitting above
        # the number is noise.
        paths = contact_paths(self.discovered())
        phones = [p for p in paths if p.kind == "phone"]
        assert len(phones) == 1
        assert phones[0].detail == "(765) 555-1212"

    def test_the_pointer_survives_when_discovery_found_nothing(self):
        row = self.discovered(contacts=[])
        phone = next(p for p in contact_paths(row) if p.kind == "phone")
        assert "read it from the page" in phone.detail

    def test_a_search_link_says_it_is_a_link_and_not_a_lookup(self):
        link = next(p for p in contact_paths(self.discovered()) if p.kind == "linkedin")
        assert "not a profile we found" in link.caution
        assert "robots.txt forbids it" in link.caution

    def test_discovered_rows_come_before_the_older_pointers(self):
        paths = contact_paths(self.discovered())
        assert paths[0].kind == "email"

    def test_a_company_with_nothing_published_is_still_told_plainly(self):
        row = self.discovered(contacts=[])
        row["evidence_file"][BLOCK4_DIGITAL_FRONT_DOOR] = {}
        row["evidence_file"][BLOCK7_PEOPLE] = {"named_people": []}
        assert reachable(row) is False
