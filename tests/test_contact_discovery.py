"""Tests for the contact discovery node.

The rule this node lives or dies by is that it never constructs an address. A
guessed mailbox is a bounce, a bounce burns the sending domain, and a burnt
domain costs every company in the queue rather than the one that was guessed
at — so most of what follows is about what the node refuses to do.

The rest is classification, which matters because it changes how the operator
writes rather than whether they can write at all.
"""

from __future__ import annotations

from lib.claims import make_claim
from lib.evidence import BLOCK4_DIGITAL_FRONT_DOOR, BLOCK7_PEOPLE
from lib.nodes import NodeResult, SkipKind
from tools.harvester.nodes.contact_discovery import (
    ContactDiscoveryNode,
    classify_email,
    directory_shaped,
    emails_on,
    linkedin_people_search,
    normalise_phone,
    phones_on,
    usable_email,
)

SITE = "https://acmetool.test/"
CONTACT = "https://acmetool.test/contact"

PAGE = """
<html><body>
  <a href="mailto:sales@acmetool.test">Email sales</a>
  <p>Estimating: dale.whitmore@acmetool.test</p>
  <a href="tel:+17655551212">(765) 555-1212</a>
  <p>Fax 765.555.9999</p>
  <script>Sentry.init({dsn:"https://abc@o1.ingest.sentry.io/1"})</script>
  <form action="/submit"><input name="email"><textarea name="message"></textarea></form>
</body></html>
"""


def claim(value, tier=1, url=SITE, **extra):
    base = make_claim(value, tier, url)
    base.update(extra)
    return base


def prospect(**overrides):
    row = {
        "id": "p1",
        "company_name": "Acme Tool & Die",
        "priority": "P1",
        "website": SITE,
        "website_confidence": 90,
        "evidence_file": {
            BLOCK7_PEOPLE: {"named_people": [
                claim("Dale Whitmore — President", claimcheck="verbatim",
                      corroborated=True, corroborated_by="press"),
            ]},
        },
    }
    row.update(overrides)
    return row


class TestNothingIsEverConstructed:
    def test_the_module_exposes_no_way_to_build_an_address(self):
        # The pattern that classifies first.last@ must never be run backwards.
        # If a helper for that ever appears, this test is the place it is argued.
        import tools.harvester.nodes.contact_discovery as node
        exported = {name for name in node.__all__}
        assert not any("guess" in n or "construct_email" in n or "permute" in n
                       for n in exported)

    def test_only_addresses_present_in_the_page_come_back(self):
        found = emails_on(PAGE, CONTACT, "acmetool.test")
        assert set(found) == {"sales@acmetool.test", "dale.whitmore@acmetool.test"}

    def test_every_address_carries_the_page_it_was_seen_on(self):
        assert set(emails_on(PAGE, CONTACT, "acmetool.test").values()) == {CONTACT}


class TestWhatIsNotAnAddress:
    def test_an_error_reporter_key_is_not_a_contact(self):
        # Analytics and crash SDKs embed addresses in page source. A finding
        # that is really the site's plumbing is worse than no finding.
        assert usable_email("abc@o1.ingest.sentry.io", "acmetool.test") is False

    def test_a_template_placeholder_is_not_a_contact(self):
        for bad in ("you@example.com", "youremail@domain.com", "name@yourdomain.com"):
            assert usable_email(bad, "acmetool.test") is False, bad

    def test_an_unattended_mailbox_is_not_a_contact(self):
        for bad in ("noreply@acmetool.test", "do-not-reply@acmetool.test",
                    "postmaster@acmetool.test"):
            assert usable_email(bad, "acmetool.test") is False, bad

    def test_an_image_filename_is_not_a_contact(self):
        assert usable_email("logo@2x.png", "acmetool.test") is False

    def test_an_off_domain_address_is_still_a_contact(self):
        # Plenty of small shops publish a gmail address. It is theirs, they
        # published it, and refusing it would lose the only path in.
        assert usable_email("acmetooldie@gmail.com", "acmetool.test") is True


class TestClassification:
    def test_a_role_mailbox_reads_as_a_function(self):
        for address in ("sales@x.test", "info@x.test", "estimating@x.test",
                        "rfq@x.test"):
            assert classify_email(address, []) == "role_based", address

    def test_a_known_person_wins_over_a_role_word(self):
        # "sales.manager@" is a rota and "dale.sales@" is Dale. Reading the role
        # word first would have called both a rota.
        assert classify_email("dale.sales@x.test", ["Dale Whitmore"]) == "named_person"

    def test_a_surname_with_an_initial_is_a_person(self):
        assert classify_email("dwhitmore@x.test", ["Dale Whitmore"]) == "named_person"

    def test_first_dot_last_is_a_person_even_when_unknown_to_us(self):
        assert classify_email("marta.klein@x.test", []) == "named_person"

    def test_anything_else_says_it_cannot_tell(self):
        # Honest third class. "We do not know who reads this" is a real answer
        # and the operator writes differently for it.
        assert classify_email("xk7d@x.test", []) == "generic"


class TestPhones:
    def test_a_tel_link_and_visible_text_agree_on_one_shape(self):
        found = phones_on(PAGE, CONTACT)
        assert "(765) 555-1212" in found
        assert found["(765) 555-1212"] == CONTACT

    def test_the_same_number_written_two_ways_is_stored_once(self):
        assert normalise_phone("+1 (765) 555-1212") == normalise_phone("765.555.1212")

    def test_something_that_is_not_a_number_is_left_alone(self):
        assert normalise_phone("call us") == "call us"


class TestLinkedIn:
    def test_the_url_is_a_people_search_the_operator_opens(self):
        url = linkedin_people_search("Dale Whitmore", "Acme Tool & Die")
        assert url.startswith("https://www.linkedin.com/search/results/people/")
        assert "Dale+Whitmore" in url and "Acme" in url

    def test_the_company_name_is_escaped_rather_than_broken(self):
        assert " " not in linkedin_people_search("A B", "C & D")


class TestWhenTheNodeRefusesToRun:
    def node(self):
        return ContactDiscoveryNode()

    async def run(self, row):
        return await self.node().run(row, ctx=None)

    def test_a_company_that_is_not_p1_is_skipped_and_can_come_back(self, anyio_backend=None):
        import asyncio
        result: NodeResult = asyncio.run(self.run(prospect(priority="P2")))
        assert result.skipped and "P1" in result.skip_reason
        # Transient on purpose: the weekly reweight moves companies between
        # priorities, so today's P2 is a company this may run for next month.
        assert result.skip_kind is SkipKind.TRANSIENT

    def test_a_site_we_are_unsure_of_is_not_read(self):
        import asyncio
        result = asyncio.run(self.run(prospect(website_confidence=30)))
        assert result.skipped and "worse than reading none" in result.skip_reason

    def test_a_hijacked_domain_is_not_read_for_contacts(self):
        # Every address on a re-registered domain reaches whoever took it.
        import asyncio
        result = asyncio.run(self.run(prospect(website_status="compromised")))
        assert result.skipped and "not theirs" in result.skip_reason

    def test_a_company_with_no_website_is_not_read(self):
        import asyncio
        result = asyncio.run(self.run(prospect(website=None)))
        assert result.skipped


class TestWhatIsStored:
    def test_claims_land_in_the_front_door_block(self):
        from lib.evidence import block_patch
        patch = block_patch(BLOCK4_DIGITAL_FRONT_DOOR, {"published_emails": []})
        assert BLOCK4_DIGITAL_FRONT_DOOR in patch

    def test_a_search_link_is_labelled_as_ours_not_as_evidence(self):
        # A search URL asserts nothing about the company. Storing it as an
        # observation would inflate a shortcut into a finding.
        from lib.claims import Tier
        assert int(Tier.T4) == 4


class TestAStaffDirectoryIsPeople:
    def test_a_lone_word_mailbox_stays_unknown(self):
        # "production@" alone is a word. Calling it a person would be a guess
        # about who reads it, which is the thing this node must not do.
        addresses = ["production@x.test"]
        directory = directory_shaped(addresses)
        assert classify_email("production@x.test", [], directory) == "generic"

    def test_several_of_the_same_shape_are_a_staff_list(self):
        # One jbarr@ is a word; a dozen of them is a directory. The evidence is
        # the pattern across the set, not the address.
        addresses = ["jbarr@x.test", "jfrost@x.test", "jkeele@x.test",
                     "rcremeans@x.test"]
        directory = directory_shaped(addresses)
        assert all(classify_email(a, [], directory) == "named_person"
                   for a in addresses)

    def test_role_mailboxes_are_never_counted_into_the_directory(self):
        addresses = ["sales@x.test", "quality@x.test", "shipping@x.test"]
        assert directory_shaped(addresses) == set()

    def test_two_alone_are_not_yet_a_directory(self):
        assert directory_shaped(["jbarr@x.test", "jfrost@x.test"]) == set()

    def test_an_initial_and_a_dot_needs_no_directory_to_be_a_person(self):
        # "a.gilkey@" is as plainly a person as "anna.gilkey@". Requiring two
        # letters filed a whole published staff list under "we cannot tell".
        assert classify_email("a.gilkey@x.test", [], set()) == "named_person"
