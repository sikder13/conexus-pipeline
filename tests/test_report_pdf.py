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
    Award,
    GrantFiguresDisagree,
    NoThesis,
    build_dossier,
    build_leave_behind,
    ends_with_a_thought,
    esc,
    grant_awards,
    grant_story,
    lead_sentence,
    leave_behind_paragraphs,
    presentable_claims,
    readable_to_a_stranger,
    speaks_to_the_reader,
    trim_to_sentence,
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


ANALYSIS = (
    "You assemble every quote by hand from the drawings a customer sends "
    "[block1_what_they_make.self_description]. If quotes run about 40 a month and "
    "each takes an hour of an estimator's time, that is roughly 480 hours a year "
    "spent on work that mostly repeats itself. The fix is a two-week job: a "
    "quoting sheet that carries your own standard rates forward so the estimator "
    "prices the exceptions rather than re-pricing everything."
)

THESIS = [{
    "kind": "thesis", "status": "sendable", "attempts": 1,
    "body": (f"## Diagnosis\n\nQuoting is assembled by hand "
             f"[block1_what_they_make.self_description].\n\n"
             f"## Opportunities, costed\n\n{ANALYSIS}\n\n"
             f"ANTI-PITCH: do not lecture them about ISO.\n\n"
             f"DISCOVERY QUESTIONS: how many quotes go out a week?"),
}]


def thesis_saying(text: str) -> list[dict]:
    """A thesis artifact whose analysis section is exactly ``text``."""
    return [{"kind": "thesis", "status": "sendable", "attempts": 1,
             "body": f"## Opportunities, costed\n\n{text}"}]


SECOND_PERSON = (
    "You price every job by hand from the drawings a customer sends, and the "
    "estimator carries the standard rates in their head rather than on the sheet. "
    "The fix is a two-week job that writes those rates down where the sheet can "
    "reach them."
)

THIRD_PERSON = (
    "Accutech has already committed real capital to tooling, and the estimating "
    "desk is where that investment meets a manual process. The fix is a two-week "
    "job that puts the standard rates on the sheet."
)

ORPHAN = (
    "Leadership has spoken at length about a legacy platform that has run for "
    "twenty years without ever being documented, and the knowledge of what it does "
    "sits with one or two people who have been there longest."
)


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


class TestSentenceTruncation:
    """A leave-behind may be shorter than the analysis. It may not be a fragment.

    The live failure printed "the figure scales proporti […]" on a page meant to
    be handed across a front desk. Cutting mid-word is the visible defect; the
    "[…]" is the second one, because it tells a stranger we ran out of room on
    their own letter.
    """

    PARA = ("You build injection molds in Muncie. The estimator prices every job "
            "by hand today. That is roughly 480 hours a year.")

    def test_short_text_is_untouched(self):
        assert trim_to_sentence(self.PARA, 500) == self.PARA

    def test_it_cuts_at_a_sentence_end(self):
        assert trim_to_sentence(self.PARA, 70) == (
            "You build injection molds in Muncie.")

    def test_it_never_cuts_mid_word(self):
        for limit in range(10, len(self.PARA) + 10):
            out = trim_to_sentence(self.PARA, limit)
            assert not out or self.PARA.startswith(out)
            assert not out or out[-1] in ".!?"

    def test_it_adds_no_marker(self):
        out = trim_to_sentence(self.PARA, 70)
        assert "…" not in out and "[" not in out and "..." not in out

    def test_a_paragraph_with_no_sentence_end_that_fits_is_dropped(self):
        # Better one fewer paragraph than half of one.
        assert trim_to_sentence("A single very long unbroken clause here", 10) == ""

    def test_it_handles_a_quoted_sentence_end(self):
        text = 'You call it "lights-out" running. Nobody watches it overnight.'
        assert trim_to_sentence(text, 40) == 'You call it "lights-out" running.'

    def test_the_internal_dossier_still_marks_its_cuts_but_keeps_words_whole(self):
        # esc() renders the operator's copy, where a visible marker is honest.
        out = esc("alpha beta gamma delta epsilon", 14)
        assert out.endswith("[…]") and "delt" not in out

    def test_no_analysis_paragraph_is_cut_mid_word_in_the_pdf(self, tmp_path):
        long_para = (" ".join(f"Sentence number {n} says something specific." 
                              for n in range(1, 60)))
        out = build_leave_behind(prospect(), thesis_saying(
            long_para + " The fix is a two-week job."), tmp_path / "l.pdf")
        body = text_of(out)
        assert "…" not in body and "[...]" not in body


class TestGrantFigures:
    """One arithmetic per page.

    The live failure put "$50,000" in the box and "$86,700 combined" in the
    prose beside it, for the same grant, and left the reader to guess which we
    meant.
    """

    def award_evidence(self, awards, **claim_extra):
        row = prospect(grant_amount=awards[0]["amount"], grant_year=awards[0].get("year"))
        row["evidence_file"][BLOCK2_GRANT_FUNDED] = {
            "grant_amount": claim(f"${awards[0]['amount']:,.0f}", url=CASE,
                                  corroborated=True, **claim_extra),
            "awards": awards,
        }
        return row

    def test_a_single_award_states_one_figure_and_its_match(self):
        assert grant_story([Award(102000.0, 2021)]) == (
            "The programme recorded an award of $102,000 in 2021. The grant requires "
            "you to match it one for one, so at least $204,000 of capital went into "
            "the work.")

    def test_several_awards_are_itemised_and_totalled(self):
        story = grant_story([Award(50000.0, 2020), Award(36700.0, 2021)])
        assert "two grants" in story
        assert "$50,000 in 2020" in story and "$36,700 in 2021" in story
        assert "$86,700 in total" in story
        assert "$173,400" in story, "the match applies to the total, not one award"

    def test_no_awards_tells_no_story(self):
        assert grant_story([]) == ""

    def test_conflicting_sources_withhold_the_figure_entirely(self):
        # The corroboration node picks no winner on a conflict. A page handed to
        # the company is the last place to start picking one.
        row = self.award_evidence([{"amount": 50000.0, "year": 2020}], conflict=True)
        assert grant_awards(row) == []

    def test_the_box_and_the_prose_tell_the_same_story(self, tmp_path):
        row = self.award_evidence([{"amount": 50000.0, "year": 2020},
                                   {"amount": 36700.0, "year": 2021}])
        out = build_leave_behind(row, thesis_saying(
            "Your two grants together came to $86,700, and matched one for one that "
            "is at least $173,400 of capital committed to the floor. The fix is a "
            "two-week job that reads what those machines already emit."
        ), tmp_path / "l.pdf")
        body = text_of(out)
        assert "$86,700" in body and "$173,400" in body
        # The single-award arithmetic must not survive anywhere on the page.
        assert "$100,000" not in body, "a per-award match total contradicts the page"

    def test_invented_grant_money_refuses_the_page(self, tmp_path):
        # The exact live failure: one $50,000 award on record, prose asserting
        # $86,700 across two rounds and $170,000 committed.
        row = self.award_evidence([{"amount": 50000.0, "year": 2020}])
        with pytest.raises(GrantFiguresDisagree) as caught:
            build_leave_behind(row, thesis_saying(
                "Across both rounds the combined grant funds reach roughly $86,700, "
                "so the floor on capital committed is at least $170,000. The fix is "
                "a two-week job."
            ), tmp_path / "l.pdf")
        assert "$86,700" in str(caught.value)

    def test_other_arithmetic_is_not_mistaken_for_a_grant_claim(self, tmp_path):
        # The analysis is supposed to carry costed ranges. Only money the prose
        # attaches to the grant is checked against the record.
        row = self.award_evidence([{"amount": 102000.0, "year": 2021}])
        out = build_leave_behind(row, thesis_saying(
            "Reprocessing those orders costs roughly $40,000 a year in rework. The "
            "fix is a two-week job."
        ), tmp_path / "l.pdf")
        assert "$40,000" in text_of(out)

    def test_the_match_total_is_allowed_in_prose(self, tmp_path):
        row = self.award_evidence([{"amount": 102000.0, "year": 2021}])
        out = build_leave_behind(row, thesis_saying(
            "Your $102,000 award was matched one for one, so at least $204,000 of "
            "capital went in. The fix is a two-week job."
        ), tmp_path / "l.pdf")
        assert "$204,000" in text_of(out)


class TestVoiceAndDignity:
    def test_a_paragraph_naming_the_company_is_dropped(self):
        kept = leave_behind_paragraphs(
            "## Opportunities\n\n" + THIRD_PERSON + "\n\n" + SECOND_PERSON,
            "Accutech Mold & Machine")
        assert kept == [SECOND_PERSON]

    def test_a_bare_surname_form_still_counts_as_third_person(self):
        # "Polaris has already committed real capital" — the live failure.
        assert not speaks_to_the_reader(
            "Polaris has already committed real capital to robotics.",
            "Polaris Laboratories LLC")

    def test_the_suffix_alone_is_not_a_match(self):
        # Otherwise every letter to an LLC loses every paragraph saying "limited".
        assert speaks_to_the_reader(
            "Your capacity is limited by the estimating desk.", "Acme LLC")

    def test_the_letter_never_names_the_company_in_the_analysis(self, tmp_path):
        out = build_leave_behind(prospect(), THESIS, tmp_path / "l.pdf")
        body = text_of(out)
        after = body.split("What we think that means", 1)
        assert len(after) == 2
        assert "Accutech" not in after[1], "the analysis addresses you, not a file"

    def test_an_empty_findings_section_is_dropped_not_apologised_for(self, tmp_path):
        row = prospect()
        row["evidence_file"][BLOCK1_WHAT_THEY_MAKE] = {}
        row["evidence_file"][BLOCK2_GRANT_FUNDED] = {}
        body = text_of(build_leave_behind(row, THESIS, tmp_path / "l.pdf"))
        assert "could not confirm" not in body
        assert "What we read about you" not in body

    def test_a_trailing_problem_with_no_thought_is_cut(self):
        kept = leave_behind_paragraphs(
            "## Opportunities\n\n" + SECOND_PERSON + "\n\n" + ORPHAN, "Accutech")
        assert kept == [SECOND_PERSON], "the letter must not end on an open wound"

    def test_a_thesis_that_is_all_orphans_refuses(self, tmp_path):
        with pytest.raises(NoThesis):
            build_leave_behind(prospect(), thesis_saying(ORPHAN), tmp_path / "l.pdf")

    def test_a_paragraph_that_offers_something_survives(self):
        assert ends_with_a_thought(SECOND_PERSON)
        assert not ends_with_a_thought(ORPHAN)


def test_the_cli_reports_a_grant_disagreement_instead_of_crashing():
    # The refusal is a verdict the operator must read, not a stack trace.
    import inspect

    from tools.report import main as report_main
    source = inspect.getsource(report_main.main)
    assert "GrantFiguresDisagree" in source, (
        "build_leave_behind can raise it, so the CLI must catch it")


class TestThesisMustHavePassed:
    """The leave-behind is the one artifact a company physically holds.

    The thesis used to sit at 'draft' forever — never passed, never failed —
    so this page was assembled from prose no gate had ever judged. It read
    well precisely because it carried the unsourced reasoning the gate would
    have caught.
    """

    def ungated(self, status):
        return [{"kind": "thesis", "status": status, "attempts": 2,
                 "body": f"## Opportunities, costed\n\n{SECOND_PERSON}"}]

    @pytest.mark.parametrize("status", ["draft", "blocked", "superseded"])
    def test_a_thesis_that_did_not_pass_is_refused(self, status, tmp_path):
        with pytest.raises(NoThesis) as caught:
            build_leave_behind(prospect(), self.ungated(status), tmp_path / "l.pdf")
        assert "did not pass the gate" in str(caught.value)
        assert status in str(caught.value)

    def test_a_passed_thesis_is_used(self, tmp_path):
        out = build_leave_behind(prospect(), self.ungated("sendable"),
                                 tmp_path / "l.pdf")
        assert "price every job by hand" in text_of(out)

    def test_a_passed_thesis_is_preferred_over_an_earlier_blocked_one(self, tmp_path):
        artifacts = self.ungated("blocked") + self.ungated("sendable")
        out = build_leave_behind(prospect(), artifacts, tmp_path / "l.pdf")
        assert pages(out) >= 1

    def test_no_thesis_at_all_still_says_flyer(self, tmp_path):
        # The older refusal must not be swallowed by the new one.
        with pytest.raises(NoThesis) as caught:
            build_leave_behind(prospect(), [], tmp_path / "l.pdf")
        assert "flyer" in str(caught.value)

    def test_readable_to_a_stranger_still_screens_non_prose(self):
        # Unchanged by the gate work: booleans, nav chrome and bare titles are
        # true, sourced, and still not sentences.
        assert readable_to_a_stranger("We machine aluminium housings for pumps.")
        assert not readable_to_a_stranger(True)
        assert not readable_to_a_stranger("About Us | Accutech Mold & Machine Inc")
        assert not readable_to_a_stranger("Login Careers Sitemap English Espanol")
        assert not readable_to_a_stranger("Molds.")


class TestGrantCount:
    def test_plural_prose_with_one_award_refuses(self, tmp_path):
        # The defect that survived the last round: we withheld a disputed
        # figure and then asserted a count we had never established.
        row = prospect()
        with pytest.raises(GrantFiguresDisagree) as caught:
            build_leave_behind(row, thesis_saying(
                "The grant awards you have received set a floor on your own capital. "
                "The fix is a two-week job."), tmp_path / "l.pdf")
        assert "record shows 1 award" in str(caught.value)

    def test_plural_prose_with_no_recorded_award_refuses(self, tmp_path):
        row = prospect(grant_amount=None, grant_year=None)
        row["evidence_file"][BLOCK2_GRANT_FUNDED] = {}
        with pytest.raises(GrantFiguresDisagree) as caught:
            build_leave_behind(row, thesis_saying(
                "Both rounds together raised the floor on the capital you have "
                "already committed to the line. The fix is a two-week job."
            ), tmp_path / "l.pdf")
        assert "no award is recorded" in str(caught.value)

    def test_plural_prose_is_fine_when_the_record_has_several(self, tmp_path):
        row = prospect()
        row["evidence_file"][BLOCK2_GRANT_FUNDED] = {
            "grant_amount": claim("$50,000", url=CASE, corroborated=True),
            "awards": [{"amount": 50000.0, "year": 2020},
                       {"amount": 36700.0, "year": 2021}],
        }
        out = build_leave_behind(row, thesis_saying(
            "Your two grants together set the floor. The fix is a two-week job."
        ), tmp_path / "l.pdf")
        assert "two grants" in text_of(out)

    def test_count_neutral_wording_passes_a_conflicted_record(self, tmp_path):
        # Sources disagree, so we state neither an amount nor a count.
        row = prospect()
        row["evidence_file"][BLOCK2_GRANT_FUNDED] = {
            "grant_amount": claim("$50,000", url=CASE, conflict=True)}
        out = build_leave_behind(row, thesis_saying(
            "The programme requires you to match public money one for one, so your "
            "own commitment is at least as large. The fix is a two-week job."
        ), tmp_path / "l.pdf")
        body = text_of(out)
        assert "Your grant" not in body, "a disputed award states no figure"
        assert "match public money" in body


class TestAdaptiveLead:
    def test_the_lead_promises_findings_when_there_are_findings(self):
        assert "what we read about you" in lead_sentence(True).lower()

    def test_the_lead_promises_nothing_when_the_section_was_dropped(self):
        text = lead_sentence(False)
        assert "what we read about you" not in text.lower()
        assert "public record of your grant" in text

    def test_both_forms_invite_correction(self):
        for has in (True, False):
            assert "wrong" in lead_sentence(has)

    def test_the_page_lead_matches_the_page(self, tmp_path):
        bare = prospect()
        bare["evidence_file"][BLOCK1_WHAT_THEY_MAKE] = {}
        bare["evidence_file"][BLOCK2_GRANT_FUNDED] = {}
        body = text_of(build_leave_behind(bare, THESIS, tmp_path / "l.pdf"))
        assert "What we read about you" not in body
        assert "what we read about you" not in body.lower()
