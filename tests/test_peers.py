"""Tests for in-dataset benchmarking.

Every comparison this module makes ends up in a document an operator reads out
loud, so the tests are about two things: that a group is built from something a
reader could disagree with, and that a position is never computed from evidence
too weak to carry it. A benchmark that cannot be traced is worse than no
benchmark, because it sounds like research.
"""

from __future__ import annotations

from lib import peers
from lib.claims import make_claim
from lib.evidence import (
    BLOCK1_WHAT_THEY_MAKE,
    BLOCK3_HIRING_SIGNALS,
    BLOCK4_DIGITAL_FRONT_DOOR,
)

SITE = "https://acmetool.test/"
GRANT = "https://conexusindiana.com/mrg/"


def claim(value, tier=1, url=SITE, **extra):
    base = make_claim(value, tier, url)
    base.update(extra)
    return base


def company(name="Acme Tool & Die", ident="p1", industry=None, **overrides):
    row = {
        "id": ident,
        "company_name": name,
        "industry_desc": industry if industry is not None else (
            "Acme Tool & Die is a precision machining and stamping supplier."),
        "grant_amount": 50_000,
        "tech_purchased": "The company is investing in a machine vision system.",
        "evidence_file": {
            BLOCK1_WHAT_THEY_MAKE: {"self_description": claim("Precision machining")},
            BLOCK3_HIRING_SIGNALS: {"open_roles_found": claim(2)},
            BLOCK4_DIGITAL_FRONT_DOOR: {
                "ssl_valid": claim(True),
                "mobile_viewport": claim(True),
                "has_contact_form": claim(True),
                "phone_present": claim(True),
                "address_present": claim(True),
                "broken_internal_links": claim(0),
            },
        },
    }
    row.update(overrides)
    return row


def sized(count, tier=1, key="employee_count", **overrides):
    row = company(**overrides)
    row["evidence_file"]["block8_financial_scale"] = {
        key: claim(count, tier=tier, url=SITE if tier == 1 else GRANT)}
    return row


class TestPlacingACompany:
    def test_the_family_carries_the_words_that_placed_it(self):
        # A placement nobody can argue with is a placement nobody can correct.
        family = peers.family_of(company())
        assert family.key == "metal_fabrication"
        assert family.matched
        assert "placed by" in family.basis

    def test_the_name_outweighs_a_subordinate_clause(self):
        # Hoosier Crane was filed under laboratory services because its listing
        # mentions the testing services it also sells, and that phrase is longer
        # than the word "crane".
        row = company(
            name="Hoosier Crane Service Company",
            industry=("Hoosier Crane Service Company manufactures and services "
                      "custom overhead cranes and provides engineering, training "
                      "and testing services."))
        row["evidence_file"][BLOCK1_WHAT_THEY_MAKE] = {}
        row["tech_purchased"] = ""
        assert peers.family_of(row).key == "machinery_equipment"

    def test_a_company_we_cannot_place_says_so(self):
        row = company(name="Quiet Holdings", industry="A company.")
        row["evidence_file"][BLOCK1_WHAT_THEY_MAKE] = {}
        row["tech_purchased"] = ""
        family = peers.family_of(row)
        assert family.key == "unclassified"
        assert "nothing" in family.basis

    def test_placement_is_stable_across_runs(self):
        row = company()
        assert peers.family_of(row) == peers.family_of(row)


class TestSize:
    def test_a_headcount_they_publish_beats_one_the_press_reported(self):
        row = sized(80)
        row["evidence_file"]["block8_financial_scale"]["company_size"] = claim(
            400, tier=2, url=GRANT)
        assert peers.size_of(row).headcount == 80
        assert peers.size_of(row).band == "mid"

    def test_an_aggregator_estimate_groups_but_never_ranks(self):
        # CLAUDE.md rule 6: an estimate is for internal filtering. Grouping is
        # filtering; publishing a ranking computed from one is not.
        row = company(employee_estimate="21-50", employee_source="salary.com [T3]")
        size = peers.size_of(row)
        assert size.band == "small"
        assert size.rankable is False
        assert "never rank" in size.basis

    def test_a_company_with_no_headcount_is_not_guessed_at(self):
        size = peers.size_of(company())
        assert size.headcount is None
        assert size.band == "unknown"


class TestBuildingTheGroup:
    def test_a_full_group_is_not_widened(self):
        subject = sized(80, ident="subject")
        universe = [subject] + [
            sized(80, ident=f"p{i}", name=f"Peer Machining {i}") for i in range(6)]
        group = peers.peer_group(subject, universe)
        assert group.widened == "family_and_size"
        assert group.size_of_group == 6
        assert group.caveat == ""

    def test_too_few_at_the_same_size_widens_to_the_family_and_says_so(self):
        subject = sized(80, ident="subject")
        universe = [subject] + [
            sized(900, ident=f"p{i}", name=f"Peer Machining {i}") for i in range(6)]
        group = peers.peer_group(subject, universe)
        assert group.widened == "family"
        assert "regardless of size" in group.caveat

    def test_too_few_in_the_family_reaches_into_related_industries(self):
        subject = sized(80, ident="subject")
        universe = [subject] + [
            sized(80, ident=f"p{i}", name=f"Peer Machinery {i}",
                  industry="A manufacturer of industrial equipment and conveyors.")
            for i in range(6)]
        for row in universe[1:]:
            row["evidence_file"][BLOCK1_WHAT_THEY_MAKE] = {}
            row["tech_purchased"] = ""
        group = peers.peer_group(subject, universe)
        assert group.widened == "neighbours"
        assert "weaker comparison" in group.caveat

    def test_a_group_never_contains_the_company_it_is_about(self):
        subject = sized(80, ident="subject")
        universe = [subject] + [
            sized(80, ident=f"p{i}", name=f"Peer Machining {i}") for i in range(6)]
        group = peers.peer_group(subject, universe)
        assert all(m["id"] != "subject" for m in group.members)

    def test_the_group_names_itself_in_words(self):
        subject = sized(80, ident="subject")
        universe = [subject] + [
            sized(80, ident=f"p{i}", name=f"Peer Machining {i}") for i in range(6)]
        basis = peers.peer_group(subject, universe).basis
        assert "metal fabrication" in basis and "50 to 100 people" in basis


class TestPositions:
    def group_of(self, subject, peers_list):
        return peers.peer_group(subject, [subject, *peers_list])

    def test_a_shared_trait_reads_as_one_of_n_of_m(self):
        subject = sized(80, ident="subject")
        others = [sized(80, ident=f"p{i}", name=f"Peer Machining {i}")
                  for i in range(5)]
        for row in others[2:]:
            row["tech_purchased"] = "The company is investing in a bigger press."
        positions = peers.compare(self.group_of(subject, others))
        automation = next(p for p in positions if p.key == "automation")
        assert automation.headline.startswith("one of 2 of 5")
        assert "grant record" in automation.basis

    def test_a_trait_they_lack_is_still_a_row(self):
        # "Eight of eleven publish a certification and you publish none" is a
        # finding. Dropping the row because the subject has nothing would hide it.
        subject = sized(80, ident="subject")
        others = [sized(80, ident=f"p{i}", name=f"Peer Machining {i}")
                  for i in range(5)]
        for row in others:
            row["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["certifications"] = [
                claim("ISO 9001")]
        positions = peers.compare(self.group_of(subject, others))
        certs = next(p for p in positions if p.key == "certifications")
        assert "not among the 5 of 5" in certs.headline

    def test_a_dimension_we_cannot_answer_reports_itself_as_uncomparable(self):
        subject = sized(80, ident="subject")
        subject["evidence_file"][BLOCK4_DIGITAL_FRONT_DOOR] = {}
        others = [sized(80, ident=f"p{i}", name=f"Peer Machining {i}")
                  for i in range(5)]
        front = next(p for p in peers.compare(self.group_of(subject, others))
                     if p.key == "front_door")
        assert front.comparable is False
        assert "not compared" in front.headline

    def test_capital_deployed_counts_the_matching_money(self):
        subject = sized(80, ident="subject")
        others = [sized(80, ident=f"p{i}", name=f"Peer Machining {i}")
                  for i in range(5)]
        capital = next(p for p in peers.compare(self.group_of(subject, others))
                       if p.key == "grant_capital")
        assert "$100,000" in capital.subject_value
        assert "matching" in capital.basis

    def test_a_certification_from_an_estimate_is_not_counted(self):
        # Certifications are their own words or they are nothing. A directory
        # asserting one on their behalf is exactly the tier that may not be
        # published, so it does not become a position.
        subject = sized(80, ident="subject")
        subject["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["certifications"] = [
            claim("ISO 9001", tier=3, url=GRANT)]
        assert peers.certifications(subject) == []

    def test_every_position_carries_its_basis(self):
        subject = sized(80, ident="subject")
        others = [sized(80, ident=f"p{i}", name=f"Peer Machining {i}")
                  for i in range(5)]
        for position in peers.compare(self.group_of(subject, others)):
            assert position.basis
            assert position.label


class TestWhatIsHandedToTheGenerator:
    def test_the_prompt_block_forbids_inventing_a_comparison(self):
        subject = sized(80, ident="subject")
        others = [sized(80, ident=f"p{i}", name=f"Peer Machining {i}")
                  for i in range(5)]
        group = peers.peer_group(subject, [subject, *others])
        block = peers.as_prompt_block(group, peers.compare(group))
        assert "ONLY comparative statements" in block
        assert "Do not invent a percentile" in block

    def test_a_widened_group_carries_its_caveat_into_the_prompt(self):
        subject = sized(80, ident="subject")
        others = [sized(900, ident=f"p{i}", name=f"Peer Machining {i}")
                  for i in range(5)]
        group = peers.peer_group(subject, [subject, *others])
        block = peers.as_prompt_block(group, peers.compare(group))
        assert "CARRY THIS CAVEAT" in block

    def test_the_summary_is_plain_data(self):
        subject = sized(80, ident="subject")
        others = [sized(80, ident=f"p{i}", name=f"Peer Machining {i}")
                  for i in range(5)]
        group = peers.peer_group(subject, [subject, *others])
        summary = peers.summarise(group, peers.compare(group))
        assert summary["family"] == "metal_fabrication"
        assert summary["group_size"] == 5
        assert len(summary["positions"]) == 5
        assert all("headline" in p for p in summary["positions"])


class TestAGroupThatIsNotReallyAGroup:
    def test_an_unclassified_company_is_told_its_group_means_nothing(self):
        # Companies we could not place get grouped with the other companies we
        # could not place. That is large enough to pass the size check and
        # means nothing, which is the worst combination a benchmark can have.
        def blank(ident):
            row = company(name=f"Holdings {ident}", ident=ident, industry="A company.")
            row["evidence_file"][BLOCK1_WHAT_THEY_MAKE] = {}
            row["tech_purchased"] = ""
            return row

        subject = blank("subject")
        group = peers.peer_group(subject, [subject] + [blank(f"p{i}") for i in range(6)])
        assert group.family.key == "unclassified"
        assert "not an industry group" in group.caveat

    def test_a_placed_company_keeps_the_ordinary_caveat(self):
        subject = sized(80, ident="subject")
        universe = [subject] + [
            sized(80, ident=f"p{i}", name=f"Peer Machining {i}") for i in range(6)]
        assert peers.peer_group(subject, universe).caveat == ""


class TestSourceScoping:
    """A peer group holds companies from one source, or it holds nothing.

    The comparison's whole claim is that every company in it was gathered the
    same way. Two adapters are not the same way: an Indiana record carries a
    drive time and a Conexus case study and a Canadian one carries neither, so a
    mixed group would report the difference between two datasets as a difference
    in industrial practice.
    """

    def _universe(self):
        indiana = [
            company(name=f"Indiana Machining {i}", ident=f"in{i}",
                    source_adapter="conexus_iedc")
            for i in range(6)
        ]
        canadian = [
            company(name=f"Ontario Machining {i}", ident=f"ca{i}",
                    source_adapter="canada_gc")
            for i in range(6)
        ]
        return indiana, canadian

    def test_a_group_never_reaches_across_adapters(self):
        indiana, canadian = self._universe()
        group = peers.peer_group(canadian[0], indiana + canadian)
        assert {p["source_adapter"] for p in group.members} == {"canada_gc"}
        assert group.size_of_group == 5

    def test_the_indiana_side_is_unchanged_by_the_canadian_rows(self):
        indiana, canadian = self._universe()
        with_canada = peers.peer_group(indiana[0], indiana + canadian)
        alone = peers.peer_group(indiana[0], indiana)
        assert with_canada.size_of_group == alone.size_of_group
        assert with_canada.widened == alone.widened

    def test_a_lone_canadian_company_gets_an_empty_group_not_indiana_peers(self):
        indiana, _ = self._universe()
        lonely = company(name="Riverbend Machining Ltd.", ident="ca9",
                         source_adapter="canada_gc")
        group = peers.peer_group(lonely, [*indiana, lonely])
        assert group.members == []
        assert group.widened == "all"

    def test_the_basis_names_the_right_territory(self):
        indiana, canadian = self._universe()
        assert "Ontario and Alberta" in peers.peer_group(
            canadian[0], canadian).basis
        assert "Indiana" in peers.peer_group(indiana[0], indiana).basis
