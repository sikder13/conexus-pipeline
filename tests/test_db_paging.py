"""Tests for the paging helper in lib/db.py.

Supabase caps a PostgREST response at 1000 rows and does not mention it in the
body, so an unpaged read returns a plausible-looking short answer. This bit the
work queue at 1144 rows: `--status` reported 1000 items and looked fine. These
tests pin the paging so it cannot come back quietly.
"""

from __future__ import annotations

import pytest

from lib import db
from lib.db import PAGE_SIZE, _fetch_all


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    """Records the ranges asked for and serves slices of a fixed row list."""

    def __init__(self, rows, requests):
        self.rows = rows
        self.requests = requests
        self._start = 0
        self._end = 0

    def range(self, start, end):
        self._start, self._end = start, end
        return self

    def execute(self):
        self.requests.append((self._start, self._end))
        return FakeResponse(self.rows[self._start : self._end + 1])


def builder(rows, requests):
    return lambda: FakeQuery(rows, requests)


def rows_numbered(count):
    return [{"id": index} for index in range(count)]


class TestFetchAll:
    def test_a_short_result_takes_one_request(self):
        requests = []
        rows = _fetch_all(builder(rows_numbered(10), requests), "op")
        assert len(rows) == 10
        assert len(requests) == 1

    def test_an_exactly_full_page_is_followed_by_another_request(self):
        # The server returning a full page is indistinguishable from more data
        # existing, so we must ask again to find out.
        requests = []
        rows = _fetch_all(builder(rows_numbered(PAGE_SIZE), requests), "op")
        assert len(rows) == PAGE_SIZE
        assert len(requests) == 2

    def test_rows_past_the_cap_are_not_lost(self):
        requests = []
        rows = _fetch_all(builder(rows_numbered(1144), requests), "op")
        assert len(rows) == 1144
        assert [row["id"] for row in rows] == list(range(1144))
        assert requests == [(0, PAGE_SIZE - 1), (PAGE_SIZE, 2 * PAGE_SIZE - 1)]

    def test_an_empty_table_returns_nothing(self):
        requests = []
        assert _fetch_all(builder([], requests), "op") == []

    @pytest.mark.parametrize("limit", [1, 5, 999])
    def test_a_limit_smaller_than_a_page_is_one_request(self, limit):
        requests = []
        rows = _fetch_all(builder(rows_numbered(5000), requests), "op", limit=limit)
        assert len(rows) == limit
        assert requests == [(0, limit - 1)]

    def test_a_limit_larger_than_a_page_still_pages(self):
        requests = []
        rows = _fetch_all(builder(rows_numbered(5000), requests), "op", limit=1500)
        assert len(rows) == 1500
        assert len(requests) == 2

    def test_a_limit_of_zero_asks_for_nothing(self):
        requests = []
        assert _fetch_all(builder(rows_numbered(10), requests), "op", limit=0) == []
        assert requests == []

    def test_a_fresh_query_is_built_for_every_page(self):
        # PostgREST builders accumulate state; reusing one would stack ranges.
        built = []

        def build():
            query = FakeQuery(rows_numbered(1144), [])
            built.append(query)
            return query

        _fetch_all(build, "op")
        assert len(built) == 2


class TestControlCharacterScrubbing:
    """Postgres jsonb rejects \\u0000 outright — 22P05.

    One mis-encoded page cost a whole prospect's evidence file on the first
    full Pass A run. The scrub happens at the write gate so no caller has to
    remember it.
    """

    def test_a_nul_byte_is_removed_from_a_string(self):
        assert db.scrub_control_characters("Acme\x00 Tool") == "Acme Tool"

    def test_tabs_newlines_and_returns_survive(self):
        assert db.scrub_control_characters("a\tb\nc\rd") == "a\tb\nc\rd"

    def test_ordinary_text_is_returned_unchanged(self):
        text = "Accutech Mold & Machine — Muncie, Indiana. 60 employees."
        assert db.scrub_control_characters(text) == text

    def test_it_reaches_into_nested_evidence(self):
        evidence = {
            "block1_what_they_make": {
                "self_description": {"value": "We make\x00 molds", "tier": 1},
                "notes": ["clean", "dir\x01ty"],
            }
        }
        scrubbed = db.scrub_control_characters(evidence)
        block = scrubbed["block1_what_they_make"]
        assert block["self_description"]["value"] == "We make molds"
        assert block["notes"] == ["clean", "dirty"]

    def test_non_strings_pass_through_untouched(self):
        assert db.scrub_control_characters({"tier": 1, "verified": False, "at": None}) == {
            "tier": 1, "verified": False, "at": None
        }
