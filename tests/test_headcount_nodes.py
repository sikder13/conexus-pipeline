"""The two nodes that gather size evidence, and the sources they refuse.

Every fetch here goes through the fake client; nothing touches the network. The
cases that carry the weight are the refusals — an aggregator, a job-creation
promise on a government page, and a federal record that matched the search but
not the recipient.
"""

from __future__ import annotations

import asyncio

import pytest

from lib.claims import Tier
from lib.evidence import (
    BLOCK2_GRANT_FUNDED,
    BLOCK3_HIRING_SIGNALS,
    BLOCK8_FINANCIAL_SCALE,
)
from lib.nodes import RunContext, SkipKind
from tests.conftest import FakeClient, FakeResponse
from tools.harvester.nodes import canada_news, headcount_harvest
from tools.harvester.nodes.canada_news import CanadaNewsNode
from tools.harvester.nodes.headcount_harvest import HeadcountHarvestNode

SITE = "https://acmefab.test"

HOME = """
<html><body>
  <nav><a href="/about">About</a><a href="/careers">Careers</a></nav>
  <h1>Acme Fabrication</h1>
  <p>Precision sheet metal since 1994. We run three press brakes.</p>
  <footer>ISO 9001 certified</footer>
</body></html>
"""

ABOUT = """
<html><body>
  <h1>About us</h1>
  <p>Acme Fabrication employs 62 people across two shifts in Muncie, Indiana.</p>
</body></html>
"""

CAREERS_WITH_A_PROMISE = """
<html><body>
  <h1>Careers</h1>
  <p>Our expansion will create 20 new jobs over the next two years.</p>
</body></html>
"""

CASE_STUDY = """
<html><body><p>Acme Fabrication, a 45-person shop, bought a laser cutter.</p></body></html>
"""


def serve(pages: dict[str, str]):
    def handler(url: str):
        if url.endswith("/robots.txt"):
            return FakeResponse("", 404, url)
        if url in pages:
            return FakeResponse(pages[url], 200, url)
        return FakeResponse("", 404, url)

    return handler


def run(node, prospect, settings, pages):
    ctx = RunContext(FakeClient(serve(pages)), settings)
    return asyncio.run(node.run(prospect, ctx))


class TestHeadcountFromTheirOwnPages:
    def test_reads_the_about_page_as_tier_one(self, settings_nodelay):
        result = run(HeadcountHarvestNode(), {"website": SITE}, settings_nodelay,
                     {SITE: HOME, f"{SITE}/about": ABOUT})
        claim = result.evidence_patch[BLOCK8_FINANCIAL_SCALE]["employee_count"]
        assert claim["tier"] == int(Tier.T1)
        assert "62" in str(claim["value"])
        assert claim["source_url"] == f"{SITE}/about"

    def test_fills_the_size_columns_only_when_they_are_empty(self, settings_nodelay):
        result = run(HeadcountHarvestNode(), {"website": SITE}, settings_nodelay,
                     {SITE: HOME, f"{SITE}/about": ABOUT})
        assert result.prospect_patch["employee_estimate"] == "62"
        assert "[T1]" in result.prospect_patch["employee_source"]

        kept = run(HeadcountHarvestNode(),
                   {"website": SITE, "employee_estimate": "171"}, settings_nodelay,
                   {SITE: HOME, f"{SITE}/about": ABOUT})
        assert kept.prospect_patch == {}

    def test_a_job_creation_promise_is_not_a_headcount(self, settings_nodelay):
        result = run(HeadcountHarvestNode(), {"website": SITE}, settings_nodelay,
                     {SITE: HOME, f"{SITE}/careers": CAREERS_WITH_A_PROMISE})
        assert BLOCK8_FINANCIAL_SCALE not in result.evidence_patch
        assert any("headcount stays null" in n for n in result.notes)

    def test_falls_back_to_the_case_study_at_tier_two(self, settings_nodelay):
        prospect = {"website": SITE, "case_study_url": "https://conexus.test/acme"}
        result = run(HeadcountHarvestNode(), prospect, settings_nodelay,
                     {SITE: HOME, "https://conexus.test/acme": CASE_STUDY})
        claim = result.evidence_patch[BLOCK8_FINANCIAL_SCALE]["company_size"]
        assert claim["tier"] == int(Tier.T2)
        assert "45" in str(claim["value"])

    def test_their_own_page_outranks_the_case_study(self, settings_nodelay):
        prospect = {"website": SITE, "case_study_url": "https://conexus.test/acme"}
        result = run(HeadcountHarvestNode(), prospect, settings_nodelay,
                     {SITE: HOME, f"{SITE}/about": ABOUT,
                      "https://conexus.test/acme": CASE_STUDY})
        block = result.evidence_patch[BLOCK8_FINANCIAL_SCALE]
        assert "employee_count" in block and "company_size" not in block

    def test_no_website_is_a_skip_not_a_failure(self, settings_nodelay):
        result = run(HeadcountHarvestNode(), {}, settings_nodelay, {})
        assert result.skipped and result.skip_kind == SkipKind.TRANSIENT


class TestRefusedSources:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.linkedin.com/company/acme",
            "https://www.zoominfo.com/c/acme/123",
            "https://uk.indeed.com/cmp/acme",
        ],
    )
    def test_an_aggregator_or_a_job_board_is_never_read(self, url):
        assert headcount_harvest.banned(url)

    def test_their_own_domain_is_not_banned(self):
        assert not headcount_harvest.banned(SITE)

    def test_a_banned_website_is_a_permanent_skip(self, settings_nodelay):
        result = run(HeadcountHarvestNode(),
                     {"website": "https://www.linkedin.com/company/acme"},
                     settings_nodelay, {})
        assert result.skipped and result.skip_kind == SkipKind.PERMANENT


class TestHiringActivityIsRecordedApart:
    def test_open_roles_land_in_block_three_and_say_what_they_are_not(
        self, settings_nodelay
    ):
        prospect = {
            "website": SITE,
            "evidence_file": {
                BLOCK3_HIRING_SIGNALS: {"open_roles": [
                    {"value": "Order Entry Coordinator", "tier": 1,
                     "source_url": f"{SITE}/careers", "date_checked": "2026-09-10"},
                    {"value": "CNC Machinist", "tier": 1,
                     "source_url": f"{SITE}/careers", "date_checked": "2026-09-10"},
                ]},
            },
        }
        result = run(HeadcountHarvestNode(), prospect, settings_nodelay, {SITE: HOME})
        claim = result.evidence_patch[BLOCK3_HIRING_SIGNALS]["hiring_activity"]
        assert "2 open roles" in str(claim["value"])
        assert "NOT a headcount" in str(claim["value"])
        assert claim["derivation"]
        # And it never reaches the block a headcount is read from.
        assert BLOCK8_FINANCIAL_SCALE not in result.evidence_patch


# ------------------------------------------------------------------ canada_news

RECORD_SEARCH = (
    '<html><body>'
    '<a href="/grants/record/nrc-cnrc,172-2025-1,current">one</a>'
    '<a href="/grants/record/cihr-irsc,236-2018-2,current">two</a>'
    '</body></html>'
)
OUR_RECORD = (
    "<html><body><p>Recipient's Legal Name: Booch Kombucha Inc.&nbsp;&nbsp;"
    "Agreement Value: $72,600&nbsp;&nbsp;"
    "Program Name: Industrial Research Assistance Program&nbsp;&nbsp;"
    "Agreement Start Date: 2025-04-01&nbsp;&nbsp;"
    "Description: Development of a shelf-stable kombucha line. The firm employs "
    "18 people at its London facility.&nbsp;&nbsp;</p></body></html>"
)
SOMEBODY_ELSE = (
    "<html><body><p>Recipient's Legal Name: Saunderson, Shane&nbsp;&nbsp;"
    "Agreement Value: $50,000&nbsp;&nbsp;Program Name: Doctoral Awards"
    "&nbsp;&nbsp;</p></body></html>"
)


class TestCanadaNewsMatching:
    def test_the_recipient_must_be_our_company(self):
        assert canada_news.is_the_same_company("Booch Kombucha Inc.", "Booch Kombucha")
        assert not canada_news.is_the_same_company("Saunderson, Shane", "Booch Kombucha")

    def test_a_shared_word_is_not_a_match(self):
        assert not canada_news.is_the_same_company(
            "University of Toronto Robotics Lab", "Trexo Robotics")

    def test_the_query_carries_the_programme_and_the_province(self):
        url = canada_news.press_query(
            "https://x.test/?s={query}", "Booch Kombucha",
            "Industrial Research Assistance Program – Contributions to Firms", "ON")
        assert "Booch+Kombucha" in url
        assert "Ontario" in url
        assert "Industrial+Research+Assistance+Program" in url


class TestCanadaNewsScope:
    def test_an_indiana_company_is_a_permanent_skip(self, settings_nodelay):
        result = run(CanadaNewsNode(),
                     {"source_adapter": "conexus_iedc", "priority": "P1"},
                     settings_nodelay, {})
        assert result.skipped and result.skip_kind == SkipKind.PERMANENT

    def test_a_p2_waits_rather_than_failing(self, settings_nodelay):
        result = run(CanadaNewsNode(),
                     {"source_adapter": "canada_gc", "priority": "P2"},
                     settings_nodelay, {})
        assert result.skipped and result.skip_kind == SkipKind.TRANSIENT

    def test_only_queued_for_p1(self):
        assert CanadaNewsNode.priorities == ("P1",)


class TestCanadaNewsRecords:
    def _pages(self):
        return {
            canada_news.records_query("Booch Kombucha"): RECORD_SEARCH,
            "https://search.open.canada.ca/grants/record/nrc-cnrc,172-2025-1,current":
                OUR_RECORD,
            "https://search.open.canada.ca/grants/record/cihr-irsc,236-2018-2,current":
                SOMEBODY_ELSE,
            "https://www.canadianmanufacturing.com/?s=Booch+Kombucha": "<html></html>",
            "https://www.foodincanada.com/?s=Booch+Kombucha": "<html></html>",
        }

    def test_reads_our_record_and_ignores_the_other(self, settings_nodelay):
        prospect = {"source_adapter": "canada_gc", "priority": "P1",
                    "company_name": "Booch Kombucha"}
        result = run(CanadaNewsNode(), prospect, settings_nodelay, self._pages())
        award = result.evidence_patch[BLOCK2_GRANT_FUNDED]["federal_award"]
        assert "72,600" in str(award["value"])
        assert award["tier"] == int(Tier.T1)
        assert any("1 of 2 federal record(s)" in n for n in result.notes)

    def test_a_headcount_on_a_government_record_is_tier_one(self, settings_nodelay):
        prospect = {"source_adapter": "canada_gc", "priority": "P1",
                    "company_name": "Booch Kombucha"}
        result = run(CanadaNewsNode(), prospect, settings_nodelay, self._pages())
        claim = result.evidence_patch[BLOCK8_FINANCIAL_SCALE]["employee_count"]
        assert "18" in str(claim["value"])
        assert claim["tier"] == int(Tier.T1)

    def test_the_unusable_backends_are_recorded_every_run(self, settings_nodelay):
        prospect = {"source_adapter": "canada_gc", "priority": "P1",
                    "company_name": "Booch Kombucha"}
        result = run(CanadaNewsNode(), prospect, settings_nodelay, self._pages())
        joined = " ".join(result.notes)
        assert "ignores its own keyword parameter" in joined
        assert "filters by department only" in joined


REAL_RECORD = (
    "<html><body><p>Recipient Business Number: 731809331 Recipient Type: "
    "For-profit organization Recipient's Legal Name: Kimia Analytics Inc. "
    "Federal Riding Name: Markham--Thornhill Program: Industrial Research "
    "Assistance Program – Contributions to Firms Program Purpose: The purpose "
    "of IRAP Contributions to Firms is to support research. Agreement Title: "
    "Fluorine Detection for Environmental Applications Agreement Number: "
    "1034449 Agreement Value: $120,000.00 Agreement Date: Aug 1, 2025 - Jul 31, "
    "2026 Description: Fluorine is a key marker in environmental analysis, but "
    "current testing methods are slow. The firm employs 18 people. Organization: "
    "National Research Council Canada</p></body></html>"
)


class TestTheRecordIsReadAsLabelledFields:
    """The page runs its labels together with single spaces.

    A regex per field terminating on whitespace swallowed the next field or gave
    up, and five of forty-six records came back with anything on them at all.
    """

    def _fields(self):
        return canada_news.record_fields(canada_news.page_text(REAL_RECORD))

    def test_a_value_does_not_swallow_the_next_label(self):
        assert self._fields()["Agreement Value"] == "$120,000.00"

    def test_the_longest_label_wins(self):
        # 'Program Purpose' must not be read as 'Program' plus stray text.
        fields = self._fields()
        assert fields["Program"].startswith("Industrial Research Assistance")
        assert fields["Program Purpose"].startswith("The purpose of IRAP")

    def test_the_description_is_the_project_not_the_programme(self):
        assert self._fields()["Description"].startswith("Fluorine is a key marker")

    def test_the_award_claim_carries_the_amount_the_programme_and_the_year(self):
        claims = CanadaNewsNode()._record_claims(
            canada_news.page_text(REAL_RECORD), "https://search.open.canada.ca/x")
        value = str(claims["federal_award"]["value"])
        assert "120,000.00" in value
        assert "Industrial Research Assistance" in value
        assert "2025" in value

    def test_a_headcount_is_read_from_the_project_description_only(self):
        # Not from the programme boilerplate, which is identical on every IRAP
        # record and would give every company the same number.
        reading = __import__("lib.headcount", fromlist=["x"]).best(
            self._fields()["Description"])
        assert reading is not None and reading.low == 18
