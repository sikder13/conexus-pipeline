"""Tests for the execution loop.

These are the tests that let the pipeline be run unattended against 772
companies. They pin the properties that make an unattended run trustworthy:
dependencies run first, a broken company cannot end the run, a re-run does not
redo settled work, and no node can promote a record past the human check.

The database is faked in memory. Nothing here touches Supabase or the network.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from lib.claims import Tier, make_claim
from lib.nodes import Node, NodeResult, RunContext, register
from lib.runner import CycleError, deep_merge, merge_notes, topological_order
from tests.conftest import run_quiet


def make_node(node_name: str, deps=(), behaviour=None, attempts_allowed=3) -> type[Node]:
    """Define and register a test node with the given behaviour."""

    class TestNode(Node):
        name: ClassVar[str] = node_name
        depends_on: ClassVar[tuple[str, ...]] = deps
        max_attempts: ClassVar[int] = attempts_allowed

        async def run(self, prospect, ctx):
            if behaviour is None:
                return NodeResult(prospect_patch={"machine_summary": f"{node_name} ran"})
            return behaviour(prospect, ctx)

    TestNode.__name__ = f"Node_{node_name}"
    return register(TestNode)


def run(names, **kwargs):
    """Execute run_nodes quietly and return the summary."""
    return run_quiet(names, **kwargs)


class TestTopologicalOrder:
    def test_dependencies_come_first(self, registry):
        make_node("a")
        make_node("b", deps=("a",))
        make_node("c", deps=("b",))
        assert topological_order(["c", "a", "b"]) == ["a", "b", "c"]

    def test_a_subset_keeps_relative_order(self, registry):
        make_node("a")
        make_node("b", deps=("a",))
        make_node("c", deps=("b",))
        assert topological_order(["c", "a"]) == ["a", "c"]

    def test_independent_nodes_are_ordered_stably(self, registry):
        make_node("zebra")
        make_node("alpha")
        assert topological_order(["zebra", "alpha"]) == ["alpha", "zebra"]

    def test_a_cycle_is_detected_and_named(self, registry):
        make_node("x", deps=("y",))
        make_node("y", deps=("x",))
        with pytest.raises(CycleError) as caught:
            topological_order(["x", "y"])
        message = str(caught.value)
        assert "x" in message and "y" in message
        assert "cycle" in message.lower()

    def test_an_unregistered_node_is_rejected(self, registry):
        make_node("a")
        with pytest.raises(KeyError, match="nope"):
            topological_order(["a", "nope"])


class TestDependencyGating:
    def test_a_node_waits_for_an_unmet_dependency(self, registry, fake_db):
        make_node("first")
        ran = []
        make_node(
            "second",
            deps=("first",),
            behaviour=lambda p, c: ran.append(p["id"]) or NodeResult(),
        )
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "first", status="pending")
        fake_db.add_item("p1", "second", status="pending")

        summary = run(["second"])
        assert ran == [], "second ran before its dependency completed"
        assert summary.per_node["second"].pending == 1
        assert summary.per_node["second"].done == 0
        assert fake_db.item_for("p1", "second")["status"] == "pending"

    def test_a_blocked_item_is_recorded_pending_not_failed(self, registry, fake_db):
        make_node("first")
        make_node("second", deps=("first",))
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "first", status="failed", attempts=1)
        fake_db.add_item("p1", "second", status="failed", attempts=1)

        run(["second"])
        assert fake_db.item_for("p1", "second")["status"] == "pending"

    def test_a_met_dependency_lets_the_node_run(self, registry, fake_db):
        make_node("first")
        make_node("second", deps=("first",))
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "first", status="done")
        fake_db.add_item("p1", "second", status="pending")

        summary = run(["second"])
        assert summary.per_node["second"].done == 1
        assert fake_db.item_for("p1", "second")["status"] == "done"

    def test_running_both_nodes_together_satisfies_the_dependency(self, registry, fake_db):
        make_node("first")
        make_node("second", deps=("first",))
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "first", status="pending")
        fake_db.add_item("p1", "second", status="pending")

        summary = run(["second", "first"])
        assert summary.per_node["first"].done == 1
        assert summary.per_node["second"].done == 1


class TestFailureIsolation:
    def test_one_failure_does_not_stop_the_others(self, registry, fake_db):
        def behaviour(prospect, ctx):
            if prospect["id"] == "p2":
                raise RuntimeError("this one company is broken")
            return NodeResult(prospect_patch={"machine_summary": "ok"})

        make_node("flaky", behaviour=behaviour)
        for name in ("p1", "p2", "p3"):
            fake_db.add_prospect(name)
            fake_db.add_item(name, "flaky")

        summary = run(["flaky"])
        assert summary.per_node["flaky"].done == 2
        assert summary.per_node["flaky"].failed == 1
        assert fake_db.prospects["p1"]["machine_summary"] == "ok"
        assert fake_db.prospects["p3"]["machine_summary"] == "ok"

    def test_the_failure_reason_is_recorded(self, registry, fake_db):
        make_node("boom", behaviour=lambda p, c: (_ for _ in ()).throw(ValueError("no website")))
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "boom")

        run(["boom"])
        item = fake_db.item_for("p1", "boom")
        assert item["status"] == "failed"
        assert "no website" in item["last_error"]
        assert item["attempts"] == 1

    def test_attempts_accumulate_across_runs(self, registry, fake_db):
        make_node("boom", behaviour=lambda p, c: (_ for _ in ()).throw(ValueError("nope")))
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "boom")

        run(["boom"])
        run(["boom"])
        assert fake_db.item_for("p1", "boom")["attempts"] == 2

    def test_max_attempts_stops_further_retries(self, registry, fake_db):
        calls = []
        make_node(
            "boom",
            behaviour=lambda p, c: calls.append(1) or (_ for _ in ()).throw(ValueError("nope")),
            attempts_allowed=2,
        )
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "boom", status="failed", attempts=2)

        summary = run(["boom"])
        assert calls == [], "an exhausted item was retried"
        assert summary.per_node["boom"].done == 0
        assert summary.per_node["boom"].failed == 0


class TestIdempotency:
    def test_a_rerun_without_force_does_nothing(self, registry, fake_db):
        calls = []
        make_node("once", behaviour=lambda p, c: calls.append(p["id"]) or NodeResult())
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "once")

        run(["once"])
        run(["once"])
        assert calls == ["p1"], "a completed item was re-run without --force"

    def test_an_item_stranded_as_running_is_picked_up_again(self, registry, fake_db):
        # A run that dies mid-flight leaves items marked 'running'. With no
        # scheduler and no second runner, that is always stale, and skipping it
        # would strand the company permanently.
        calls = []
        make_node("once", behaviour=lambda p, c: calls.append(p["id"]) or NodeResult())
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "once", status="running", attempts=0)

        summary = run(["once"])
        assert calls == ["p1"]
        assert summary.per_node["once"].done == 1

    def test_force_re_runs_completed_items(self, registry, fake_db):
        calls = []
        make_node("once", behaviour=lambda p, c: calls.append(p["id"]) or NodeResult())
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "once", status="done")

        run(["once"], force=True)
        assert calls == ["p1"]

    def test_limit_caps_the_work(self, registry, fake_db):
        calls = []
        make_node("many", behaviour=lambda p, c: calls.append(p["id"]) or NodeResult())
        for index in range(5):
            fake_db.add_prospect(f"p{index}")
            fake_db.add_item(f"p{index}", "many")

        run(["many"], limit=2)
        assert len(calls) == 2

    def test_a_skipped_result_is_recorded_as_skipped(self, registry, fake_db):
        make_node(
            "skipper",
            behaviour=lambda p, c: NodeResult(skipped=True, skip_reason="no website to check"),
        )
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "skipper")

        summary = run(["skipper"])
        assert summary.per_node["skipper"].skipped == 1
        item = fake_db.item_for("p1", "skipper")
        assert item["status"] == "skipped"
        assert item["last_error"] == "no website to check"


class TestStageGuard:
    @pytest.mark.parametrize("stage", ["verified", "thesis_done", "contact_found", "closed_won"])
    def test_a_node_cannot_promote_past_the_human_check(self, registry, fake_db, stage):
        make_node("overreach", behaviour=lambda p, c: NodeResult(prospect_patch={"stage": stage}))
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "overreach")

        summary = run(["overreach"])
        assert summary.per_node["overreach"].failed == 1
        assert fake_db.prospects["p1"]["stage"] == "extracted", "the forbidden stage was written"
        assert "StageViolation" in fake_db.item_for("p1", "overreach")["last_error"]

    @pytest.mark.parametrize("stage", ["needs_review", "passA_done", "dead"])
    def test_permitted_stages_are_written(self, registry, fake_db, stage):
        make_node("fine", behaviour=lambda p, c: NodeResult(prospect_patch={"stage": stage}))
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "fine")

        run(["fine"])
        assert fake_db.prospects["p1"]["stage"] == stage


class TestEvidenceMerging:
    def test_nested_blocks_merge_rather_than_replace(self):
        base = {"source": {"county": {"value": "Delaware"}}, "other": 1}
        patch = {"source": {"website": {"value": "https://x.test"}}}
        merged = deep_merge(base, patch)
        assert set(merged["source"]) == {"county", "website"}
        assert merged["other"] == 1

    def test_a_claim_is_replaced_whole(self):
        base = {"block": {"headcount": {"value": 10, "tier": 3, "date_checked": "2026-01-01"}}}
        patch = {"block": {"headcount": {"value": 20, "tier": 1, "date_checked": "2026-08-08"}}}
        merged = deep_merge(base, patch)
        assert merged["block"]["headcount"] == patch["block"]["headcount"]

    def test_notes_do_not_duplicate_on_rerun(self):
        first = merge_notes({}, "node_a", ["could not find a city"])
        second = merge_notes(first, "node_a", ["could not find a city"])
        assert len(second["notes"]) == 1

    def test_notes_from_different_nodes_accumulate(self):
        merged = merge_notes(merge_notes({}, "a", ["one"]), "b", ["two"])
        assert len(merged["notes"]) == 2

    def test_the_runner_merges_evidence_into_the_prospect(self, registry, fake_db):
        claim = make_claim("Delaware", Tier.T1, "https://conexusindiana.com/mrg-recipients/")
        make_node(
            "adds_evidence",
            behaviour=lambda p, c: NodeResult(
                evidence_patch={"identity": {"county": claim}}, notes=["county confirmed"]
            ),
        )
        fake_db.add_prospect("p1", evidence_file={"source": {"existing": {"value": 1, "tier": 1,
                                                   "source_url": "https://x.test",
                                                   "date_checked": "2026-08-08"}}})
        fake_db.add_item("p1", "adds_evidence")

        run(["adds_evidence"])
        evidence = fake_db.prospects["p1"]["evidence_file"]
        assert evidence["identity"]["county"]["value"] == "Delaware"
        assert evidence["source"]["existing"]["value"] == 1, "existing evidence was clobbered"
        assert evidence["notes"][0]["note"] == "county confirmed"

    def test_invalid_evidence_is_refused(self, registry, fake_db):
        # A claim with no tier must never reach the database; the runner catches
        # it before the write, mirroring the trigger in migration 001.
        make_node(
            "bad_evidence",
            behaviour=lambda p, c: NodeResult(
                evidence_patch={"identity": {"headcount": {"value": 40}}}
            ),
        )
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "bad_evidence")

        summary = run(["bad_evidence"])
        assert summary.per_node["bad_evidence"].failed == 1
        assert "evidence_file" not in fake_db.prospects["p1"]


class TestRunContextIsShared:
    def test_all_prospects_share_one_context(self, registry, fake_db):
        seen: list[RunContext] = []
        make_node("peek", behaviour=lambda p, c: seen.append(c) or NodeResult())
        for name in ("p1", "p2"):
            fake_db.add_prospect(name)
            fake_db.add_item(name, "peek")

        run(["peek"])
        assert len(seen) == 2
        assert seen[0] is seen[1], "each prospect got its own client and politeness state"
