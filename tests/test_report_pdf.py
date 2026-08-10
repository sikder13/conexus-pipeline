"""Tests for the PDF dossier generator.

Every assertion reads the generated PDF back with pdfplumber rather than
checking the code that was supposed to write it. A renderer that silently drops
a section still returns a valid PDF object, so testing the call is testing
nothing.

The leave-behind tests carry the most weight. That document is handed to the
company itself, and a leaked tier badge or an UNSUPPORTED verdict on a page a
prospect is holding tells them we grade our guesses about them.
"""

from __future__ import annotations

import pdfplumber
import pytest

from lib.claims import make_claim
from lib.evidence import BLOCK1_WHAT_THEY_MAKE, BLOCK2_GRANT_FUNDED, BLOCK7_PEOPLE
from tools.report.main import (
    NoThesis,
    build_dossier,
    build_leave_behind,
    presentable_claims,
)

SITE = "https://accutechmold.test/about/"
CASE = "https://conexusindiana.com/case-study/accutech/"


def claim(value, tier=1, url=SITE, **extra):
    base = make_claim(value, tier, url)
    base.update(extra)
    return base


def prospect(**overrides):
    row = {
        "id": "p1",
        "company_name": "Accutech Mold & Machine",
        "county": "Delaware",
        "drive_minutes": 3,
        "signal_score": 4,
        "priority": "P1",
        "stage": "passA_done",
        "website": "https://accutechmold.test/",
        "website_status": "ok",
        "grant_amount": 102000.0,
        "grant_year": 2021,
        "tech_purchased": "a robotic flocking line",
        "employee_estimate": 60,
        "industry_desc": "Injection moulding and tooling for automotive customers.",
        "machine_summary": "A job shop in Muncie building injection molds.",
        "score_breakdown": {"case_study": 1, "in_drive_radius": 1, "too_big": 0},
        "evidence_file": {
            BLOCK1_WHAT_THEY_MAKE: {
                "self_description": claim("We build injection molds in Muncie.",
                                          corroborated=True, claimcheck="verbatim"),
            },
            BLOCK2_GRANT_FUNDED: {
                "grant_amount": claim("$102,000", url=CASE, corroborated=True),
            },
            BLOCK7_PEOPLE: {
                "named_people": [claim("Dale Whitmore — President", claimcheck="verbatim")],
            },
            "notes": [{"node": "front_door", "note": "read 4 pages"}],
        },
    }
    row.update(overrides)
    return row


def text_of(path) -> str:
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def pages(path) -> int:
    with pdfplumber.open(str(path)) as pdf:
        return len(pdf.pages)


THESIS = [{
    "kind": "thesis", "status": "draft", "attempts": 1,
    "body": ("## Diagnosis\n\nQuoting is assembled by hand "
             "[block1_what_they_make.self_description].\n\n"
             "## Opportunities, costed\n\nIf quotes run about 40 a month, the desk "
             "costs roughly $30,000 a year [block2_grant_funded.grant_amount].\n\n"
             "ANTI-PITCH: do not lecture them about ISO.\n\n"
             "DISCOVERY QUESTIONS: how many quotes go out a week?"),
}]


class TestDossier:
    def test_it_generates_a_readable_pdf(self, tmp_path):
        out = build_dossier([prospect()], {"p1": []}, tmp_path / "d.pdf", "P1")
        assert pages(out) > 0
        assert "Accutech Mold & Machine" in text_of(out)

    def test_the_cover_explains_the_tiers(self, tmp_path):
        out = build_dossier([prospect()], {"p1": []}, tmp_path / "d.pdf", "P1")
        body = text_of(out)
        for phrase in ("their own words or a government record", "UNSUPPORTED",
                       "Barred from all outreach"):
            assert phrase in body

    def test_the_match_is_explained_in_plain_words(self, tmp_path):
        out = build_dossier([prospect()], {"p1": []}, tmp_path / "d.pdf", "P1")
        body = text_of(out)
        assert "1:1 match" in body
        assert "$204,000" in body, "the deployed-capital floor must be spelled out"

    def test_the_score_is_explained_not_just_numbered(self, tmp_path):
        out = build_dossier([prospect()], {"p1": []}, tmp_path / "d.pdf", "P1")
        assert "Conexus published a case study on them" in text_of(out)

    def test_an_index_appears_for_multiple_companies(self, tmp_path):
        rows = [prospect(), prospect(id="p2", company_name="Bravo Tool")]
        out = build_dossier(rows, {"p1": [], "p2": []}, tmp_path / "d.pdf", "P1")
        body = text_of(out)
        assert "Index" in body and "Bravo Tool" in body

    def test_a_tainted_claim_is_shown_with_its_reason(self, tmp_path):
        row = prospect()
        row["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["self_description"].update(
            {"tainted": True, "taint_reason": "the domain serves a gambling site"})
        out = build_dossier([row], {"p1": []}, tmp_path / "d.pdf", "P1")
        assert "the domain serves a gambling site" in text_of(out)

    def test_an_unsupported_claim_shows_the_checkers_reason(self, tmp_path):
        row = prospect()
        row["evidence_file"][BLOCK7_PEOPLE]["named_people"][0].update(
            {"claimcheck": "unsupported",
             "claimcheck_reason": "the source lists him as General Manager"})
        out = build_dossier([row], {"p1": []}, tmp_path / "d.pdf", "P1")
        body = text_of(out)
        assert "UNSUPPORTED" in body
        assert "General Manager" in body

    def test_an_empty_block_says_so(self, tmp_path):
        row = prospect(evidence_file={BLOCK1_WHAT_THEY_MAKE: {
            "self_description": claim("We build molds.")}})
        out = build_dossier([row], {"p1": []}, tmp_path / "d.pdf", "P1")
        assert "Nothing recorded for this block." in text_of(out)

    def test_a_missing_thesis_says_not_yet_drafted(self, tmp_path):
        out = build_dossier([prospect()], {"p1": []}, tmp_path / "d.pdf", "P1")
        assert "Not yet drafted" in text_of(out)

    def test_a_thesis_is_rendered_in_sections(self, tmp_path):
        out = build_dossier([prospect()], {"p1": THESIS}, tmp_path / "d.pdf", "P1")
        body = text_of(out)
        assert "Opportunities, costed" in body
        assert "ANTI-PITCH" in body and "DISCOVERY QUESTIONS" in body

    def test_a_blocked_artifact_shows_why(self, tmp_path):
        artifacts = [*THESIS, {
            "kind": "email", "status": "blocked", "attempts": 2,
            "body": "Some text.", "gate_failures": ["number with no source"],
        }]
        out = build_dossier([prospect()], {"p1": artifacts}, tmp_path / "d.pdf", "P1")
        body = text_of(out)
        assert "blocked" in body and "number with no source" in body

    def test_a_sendable_email_is_rendered(self, tmp_path):
        artifacts = [{"kind": "email", "status": "sendable", "attempts": 1,
                      "body": "You build injection molds in Muncie.", "gate_failures": []}]
        out = build_dossier([prospect()], {"p1": artifacts}, tmp_path / "d.pdf", "P1")
        assert "You build injection molds in Muncie." in text_of(out)

    def test_an_unscored_company_still_renders(self, tmp_path):
        row = prospect(signal_score=None, priority=None, score_breakdown=None,
                       machine_summary=None, grant_amount=None)
        out = build_dossier([row], {"p1": []}, tmp_path / "d.pdf", "P1")
        assert pages(out) > 0
        assert "no award figure" in text_of(out)

    def test_a_claim_cap_is_declared_not_silent(self, tmp_path):
        many = {f"fact_{i}": claim(f"fact number {i}") for i in range(15)}
        row = prospect(evidence_file={BLOCK1_WHAT_THEY_MAKE: many})
        out = build_dossier([row], {"p1": []}, tmp_path / "d.pdf", "P1")
        assert "more in the database" in text_of(out)


class TestLeaveBehind:
    def test_it_refuses_without_a_thesis(self, tmp_path):
        with pytest.raises(NoThesis) as caught:
            build_leave_behind(prospect(), [], tmp_path / "l.pdf")
        assert "flyer" in str(caught.value)

    def test_it_generates_with_a_thesis(self, tmp_path):
        out = build_leave_behind(prospect(), THESIS, tmp_path / "l.pdf")
        assert pages(out) > 0
        assert "Accutech Mold & Machine" in text_of(out)

    def test_it_is_two_pages_at_most(self, tmp_path):
        out = build_leave_behind(prospect(), THESIS, tmp_path / "l.pdf")
        assert pages(out) <= 2

    def test_it_carries_no_internal_jargon(self, tmp_path):
        out = build_leave_behind(prospect(), THESIS, tmp_path / "l.pdf")
        body = text_of(out)
        for jargon in ("T1", "T4", "UNSUPPORTED", "claimcheck", "verbatim",
                       "inferable", "tainted", "P1", "signal_score", "block1"):
            assert jargon not in body, f"leaked internal vocabulary: {jargon!r}"

    def test_claim_ids_are_stripped_from_the_analysis(self, tmp_path):
        out = build_leave_behind(prospect(), THESIS, tmp_path / "l.pdf")
        assert "block2_grant_funded" not in text_of(out)

    def test_it_carries_our_contact_details(self, tmp_path):
        out = build_leave_behind(prospect(), THESIS, tmp_path / "l.pdf")
        body = text_of(out)
        assert "Nahl Technologies" in body
        assert "6902 Challenge Ln" in body

    def test_it_invites_correction(self, tmp_path):
        # The whole point of handing it over is to be told what we got wrong.
        assert "correct it" in text_of(
            build_leave_behind(prospect(), THESIS, tmp_path / "l.pdf"))

    def test_only_confirmed_low_tier_claims_are_shown(self):
        row = prospect()
        row["evidence_file"][BLOCK1_WHAT_THEY_MAKE].update({
            "guess": claim("we think they run three shifts", tier=4, corroborated=True),
            "unconfirmed": claim("they run two lines"),
            "aggregator": claim("about 60 staff", tier=3, corroborated=True),
        })
        shown = {c["value"] for _p, c in presentable_claims(row)}
        assert "We build injection molds in Muncie." in shown
        assert "we think they run three shifts" not in shown, "T4 must never be shown"
        assert "they run two lines" not in shown, "unconfirmed must never be shown"
        assert "about 60 staff" not in shown, "T3 must never leave the building"

    def test_a_tainted_claim_is_never_shown(self):
        row = prospect()
        row["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["self_description"]["tainted"] = True
        shown = {c["value"] for _p, c in presentable_claims(row)}
        assert "We build injection molds in Muncie." not in shown

    def test_a_killed_claim_is_never_shown(self):
        row = prospect()
        row["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["self_description"]["killed"] = True
        shown = {c["value"] for _p, c in presentable_claims(row)}
        assert "We build injection molds in Muncie." not in shown

    def test_a_boolean_flag_is_never_shown(self):
        # A flag renders as the single word "True", which is true, sourced and
        # meaningless to the company reading it.
        row = prospect()
        row["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["flag"] = claim(
            True, corroborated=True)
        assert not any(c.get("value") is True for _p, c in presentable_claims(row))

    def test_scraped_navigation_is_never_shown(self):
        row = prospect()
        row["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["nav"] = claim(
            "About Us Login Careers English Español Search Menu Subscribe Sitemap",
            corroborated=True)
        shown = {c["value"] for _p, c in presentable_claims(row)}
        assert not any("Login Careers" in s for s in shown)

    def test_a_bare_page_title_is_never_shown(self):
        row = prospect()
        row["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["title"] = claim(
            "About Us | Accutech Mold and Machine Incorporated", corroborated=True)
        shown = {c["value"] for _p, c in presentable_claims(row)}
        assert not any(s.startswith("About Us |") for s in shown)

    def test_a_real_sentence_survives_the_filter(self):
        shown = {c["value"] for _p, c in presentable_claims(prospect())}
        assert "We build injection molds in Muncie." in shown
