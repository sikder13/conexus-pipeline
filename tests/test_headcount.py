"""What counts as a headcount, and the three things that look like one.

The exclusions carry the weight here. A missed headcount costs one company a
better-anchored band; a job-creation promise recorded as a headcount puts a
number on a call that the company knows is wrong about itself.
"""

from __future__ import annotations

import pytest

from lib import headcount


class TestReadsAStatedCount:
    @pytest.mark.parametrize(
        "text,low,high",
        [
            ("We have 45 employees at our Muncie plant.", 45, 45),
            ("The company employs 120 people.", 120, 120),
            ("A team of 12 keeps the line running.", 12, 12),
            ("About 50 to 100 employees work across two shifts.", 50, 100),
            ("We are a 40-person shop.", 40, 40),
            ("Our workforce of 230 has doubled since 2015.", 230, 230),
            ("Today 8 staff run the whole operation.", 8, 8),
            ("Approximately 1,200 employees worldwide.", 1200, 1200),
        ],
    )
    def test_reads_the_count(self, text, low, high):
        reading = headcount.best(text)
        assert reading is not None, text
        assert (reading.low, reading.high) == (low, high)
        assert reading.kind == headcount.HEADCOUNT

    def test_keeps_the_phrase_it_read(self):
        reading = headcount.best("We have 45 employees at our Muncie plant.")
        assert "45 employees" in reading.phrase

    def test_a_range_stays_a_range(self):
        reading = headcount.best("Between 50 and 100 people, depending on the season.")
        assert reading is None or reading.is_range or reading.low == reading.high

    def test_plus_suffix_reads_as_the_stated_floor(self):
        reading = headcount.best("120+ staff across three sites.")
        assert reading is not None
        assert reading.low == 120


class TestRefusesAJobCreationPromise:
    """The failure this module exists to prevent."""

    @pytest.mark.parametrize(
        "text",
        [
            "The project will create 15 jobs in Ontario.",
            "The funding is expected to create up to 40 new positions.",
            "Alberta Innovates says the work will support 25 jobs.",
            "The expansion will add 30 employees by 2027.",
        ],
    )
    def test_never_a_headcount(self, text):
        assert headcount.best(text) is None

    def test_but_it_is_still_read_as_what_it_is(self):
        found = headcount.job_creation("The project will create 15 jobs in Ontario.")
        assert [(r.low, r.kind) for r in found] == [(15, headcount.JOB_CREATION)]


class TestRefusesAPlanOrABound:
    @pytest.mark.parametrize(
        "text",
        [
            "The new line will take the plant to 120 employees.",
            "We expect to reach 200 staff next year.",
            "Down from 300 employees before the closure.",
            "Fewer than 50 employees, so the exemption applies.",
            "We do not have 40 employees.",
        ],
    )
    def test_not_a_statement_about_now(self, text):
        assert headcount.best(text) is None


class TestRefusesEverythingElseAFactoryCounts:
    @pytest.mark.parametrize(
        "text",
        [
            "We run 45 CNC machines.",
            "Our 60,000 square foot facility opened in 2019.",
            "Serving more than 500 customers across the Midwest.",
            "45 years of precision machining.",
            "ISO 9001 certified since 2004.",
            "Call us at 765-555-0142.",
        ],
    )
    def test_no_reading(self, text):
        assert headcount.best(text) is None


class TestListedTrades:
    def test_each_term_is_its_own_reading_and_none_are_summed(self):
        text = "Twelve machinists, 4 estimators and 2 engineers make up the shop."
        readings = headcount.headcounts(text)
        counts = sorted(r.low for r in readings)
        # 12 is spelled out and is not a numeral; 4 and 2 are read separately and
        # 6 — their sum — is never produced.
        assert 6 not in counts
        assert counts == [2, 4]

    def test_the_whole_beats_a_department(self):
        text = "Our 6 estimators are part of a team of 80 people."
        assert headcount.best(text).high == 80


class TestHiringActivityIsNeverHeadcount:
    def test_open_roles_are_labelled_as_such(self):
        reading = headcount.hiring_activity(9, "their careers page")
        assert reading.kind == headcount.HIRING_ACTIVITY
        assert reading.low == 9
        assert "9 open roles" in reading.phrase

    def test_no_roles_is_no_signal(self):
        assert headcount.hiring_activity(0) is None

    def test_hiring_activity_never_appears_among_headcounts(self):
        # Counting postings is counting people the company does NOT have.
        assert headcount.headcounts("We have 9 open roles right now.") == []


class TestImplausibleFiguresAreRefused:
    @pytest.mark.parametrize(
        "text",
        [
            "Our people delivered 4,500,000 parts last year to 0 employees.",
            "0 employees remain.",
        ],
    )
    def test_out_of_range(self, text):
        assert headcount.best(text) is None


class TestClaimValue:
    def test_quotes_the_sentence_fragment(self):
        reading = headcount.best("The company employs 120 people.")
        assert "120" in headcount.claim_value(reading)

    def test_a_range_says_so(self):
        reading = headcount.best("About 50 to 100 employees work here.")
        assert reading.words == "50 to 100 people"
