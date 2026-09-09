"""Tests for source scoping — the flag that says which dataset a run acts on.

Two datasets share one database, so a run that says nothing about the source
acts on both. These tests pin the three things that has to mean: the flag only
accepts sources we have declared a scoring profile for, an absent value reads as
the schema's default rather than as unknown, and the work queue is narrowed
before the limit is applied rather than after.
"""

from __future__ import annotations

import argparse

import pytest

from lib import adapters, db
from lib.nodes import NodeResult
from lib.scoring import PROFILES
from tests.conftest import run_quiet
from tests.test_runner import make_node


class TestChoices:
    def test_every_declared_profile_is_scopeable(self):
        assert set(adapters.CHOICES) == set(PROFILES)

    def test_an_undeclared_source_is_rejected_by_the_parser(self):
        parser = argparse.ArgumentParser()
        adapters.add_argument(parser)
        with pytest.raises(SystemExit):
            parser.parse_args(["--adapter", "feddev_ca"])

    def test_the_flag_is_optional_rather_than_positional(self):
        # `add_argument("adapter", ...)` reads as a required positional and
        # makes every tool that adopts it refuse to run without one.
        parser = argparse.ArgumentParser()
        adapters.add_argument(parser)
        assert parser.parse_args(["--adapter", "canada_gc"]).adapter == "canada_gc"

    def test_the_flag_defaults_to_every_source(self):
        parser = argparse.ArgumentParser()
        adapters.add_argument(parser)
        assert parser.parse_args([]).adapter is None
        assert adapters.words(None) == "every source"

    def test_the_scope_line_names_the_scale_as_well_as_the_key(self):
        assert "canada_gc" in adapters.words("canada_gc")
        assert "Government of Canada" in adapters.words("canada_gc")


class TestScope:
    ROWS = [
        {"id": "a", "source_adapter": "conexus_iedc"},
        {"id": "b", "source_adapter": "canada_gc"},
        {"id": "c"},
    ]

    def test_no_adapter_keeps_everything(self):
        assert adapters.scope(self.ROWS, None) == self.ROWS

    def test_a_row_without_the_column_reads_as_the_schema_default(self):
        # migration 001: `source_adapter text not null default 'conexus_iedc'`.
        kept = [r["id"] for r in adapters.scope(self.ROWS, "conexus_iedc")]
        assert kept == ["a", "c"]

    def test_scoping_to_canada_leaves_indiana_alone(self):
        assert [r["id"] for r in adapters.scope(self.ROWS, "canada_gc")] == ["b"]


class TestRunnerScoping:
    """The queue is narrowed before the limit, not after.

    `--limit 10 --adapter canada_gc` has to mean ten Canadian items. Filtering
    after the limit would take the ten oldest items of any source and then throw
    most of them away, which is a run that does nothing and reports that it did.
    """

    def _queue(self, fake_db):
        for pid, adapter in (("in1", "conexus_iedc"), ("in2", "conexus_iedc"),
                             ("ca1", "canada_gc"), ("ca2", "canada_gc")):
            fake_db.add_prospect(pid, source_adapter=adapter)
            fake_db.add_item(pid, "probe")

    def test_only_the_scoped_source_runs(self, fake_db, registry, monkeypatch):
        seen: list[str] = []
        make_node("probe", behaviour=lambda p, c: seen.append(p["id"]) or NodeResult())
        self._queue(fake_db)
        monkeypatch.setattr(
            db, "list_prospect_identities",
            lambda source_adapter=None: [
                {"id": pid} for pid, row in fake_db.prospects.items()
                if row.get("source_adapter") == source_adapter
            ],
        )
        run_quiet(["probe"], adapter="canada_gc")
        assert sorted(seen) == ["ca1", "ca2"]

    def test_no_adapter_runs_every_source(self, fake_db, registry):
        seen: list[str] = []
        make_node("probe", behaviour=lambda p, c: seen.append(p["id"]) or NodeResult())
        self._queue(fake_db)
        run_quiet(["probe"])
        assert sorted(seen) == ["ca1", "ca2", "in1", "in2"]

    def test_the_limit_counts_scoped_items(self, fake_db, registry, monkeypatch):
        seen: list[str] = []
        make_node("probe", behaviour=lambda p, c: seen.append(p["id"]) or NodeResult())
        self._queue(fake_db)
        monkeypatch.setattr(
            db, "list_prospect_identities",
            lambda source_adapter=None: [
                {"id": pid} for pid, row in fake_db.prospects.items()
                if row.get("source_adapter") == source_adapter
            ],
        )
        run_quiet(["probe"], adapter="canada_gc", limit=1)
        assert len(seen) == 1 and seen[0].startswith("ca")
