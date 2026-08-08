"""Dependency-safety tests for the full Pass A node graph.

Three properties, all of which decide whether the pipeline finishes at all:

1. The real registered graph has an execution order — nine nodes, five of them
   feeding `score`, must topologically sort.
2. `score` cannot run before the evidence nodes it reads. Scoring a prospect
   whose evidence has not landed yet would produce a confident number from an
   empty file.
3. A prospect whose front_door failed still gets scored. Missing evidence must
   score zero, not raise and not block — otherwise one dead website costs us
   the whole record.

Property 3 is the one that bites in production: `case_study` legitimately skips
for the 502 companies that have no case study, and `front_door` skips a prospect
whose website could not be identified. If a skipped or exhausted dependency did
not satisfy the gate, `score` would never run for most of the pipeline.
"""

from __future__ import annotations

import pytest

import tools.harvester.nodes  # noqa: F401  (registers the real nodes)
from lib.nodes import NODE_REGISTRY
from lib.runner import _dependency_met, topological_order
from tests.conftest import run_quiet as run

EVIDENCE_NODES = ("case_study", "grant_news", "front_door", "job_postings", "people")


class TestRealGraph:
    def test_the_whole_registered_graph_sorts(self):
        order = topological_order(list(NODE_REGISTRY))
        assert len(order) == len(NODE_REGISTRY) == 9
        # Every node appears after all of its dependencies.
        for index, name in enumerate(order):
            for dependency in NODE_REGISTRY[name].depends_on:
                assert order.index(dependency) < index, f"{dependency} must precede {name}"

    def test_score_comes_after_every_evidence_node(self):
        order = topological_order(list(NODE_REGISTRY))
        for evidence_node in EVIDENCE_NODES:
            assert order.index(evidence_node) < order.index("score")

    def test_summary_comes_last(self):
        order = topological_order(list(NODE_REGISTRY))
        assert order[-1] == "summary"

    def test_requesting_only_score_still_orders_it_correctly(self):
        assert topological_order(["summary", "score"]) == ["score", "summary"]

    def test_no_node_depends_on_something_unregistered(self):
        for name, node in NODE_REGISTRY.items():
            for dependency in node.depends_on:
                assert dependency in NODE_REGISTRY, f"{name} depends on missing {dependency}"


class TestDependencyRule:
    @pytest.mark.parametrize("status", ["done", "skipped"])
    def test_finished_dependencies_are_met(self, status):
        row = {"node_name": "front_door", "status": status, "attempts": 0}
        assert _dependency_met(row) is True

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_unfinished_dependencies_are_not_met(self, status):
        row = {"node_name": "front_door", "status": status, "attempts": 0}
        assert _dependency_met(row) is False

    def test_a_failed_dependency_blocks_while_retries_remain(self):
        row = {"node_name": "front_door", "status": "failed", "attempts": 1}
        assert _dependency_met(row) is False

    def test_an_exhausted_dependency_stops_blocking(self):
        ceiling = NODE_REGISTRY["front_door"].max_attempts
        row = {"node_name": "front_door", "status": "failed", "attempts": ceiling}
        assert _dependency_met(row) is True


class TestScoreGating:
    def _prospect_with_deps(self, fake_db, statuses: dict[str, str], attempts: int = 0):
        fake_db.add_prospect("p1", evidence_file={}, drive_minutes=30)
        for name in EVIDENCE_NODES:
            fake_db.add_item("p1", name, status=statuses.get(name, "done"), attempts=attempts)
        fake_db.add_item("p1", "score", status="pending")

    def test_score_waits_for_unfinished_evidence(self, fake_db):
        self._prospect_with_deps(fake_db, {"front_door": "pending"})
        summary = run(["score"])
        assert summary.per_node["score"].done == 0
        assert summary.per_node["score"].pending == 1
        assert fake_db.item_for("p1", "score")["status"] == "pending"

    def test_score_runs_once_every_dependency_is_done(self, fake_db):
        self._prospect_with_deps(fake_db, {})
        summary = run(["score"])
        assert summary.per_node["score"].done == 1
        assert fake_db.prospects["p1"]["signal_score"] is not None

    def test_a_skipped_dependency_does_not_block_score(self, fake_db):
        # case_study skips for the 502 companies with no case study. If this
        # blocked, most of the pipeline would never be scored.
        self._prospect_with_deps(fake_db, {"case_study": "skipped", "front_door": "skipped"})
        summary = run(["score"])
        assert summary.per_node["score"].done == 1

    def test_a_failed_front_door_still_gets_scored_once_retries_are_exhausted(self, fake_db):
        ceiling = NODE_REGISTRY["front_door"].max_attempts
        self._prospect_with_deps(fake_db, {"front_door": "failed"}, attempts=ceiling)

        summary = run(["score"])
        assert summary.per_node["score"].done == 1, "an exhausted dependency must not block scoring"

        prospect = fake_db.prospects["p1"]
        breakdown = prospect["score_breakdown"]
        # The evidence front_door would have supplied scores zero — not an error,
        # and not a silently-omitted component.
        assert breakdown["weak_front_door"] == 0
        assert breakdown["clerical_posting"] == 0
        # The evidence that did arrive is still counted.
        assert breakdown["in_drive_radius"] == 1
        assert prospect["signal_score"] == 1
        assert prospect["priority"] == "P3"
        assert prospect["stage"] == "passA_done"

    def test_scoring_an_empty_evidence_file_does_not_raise(self, fake_db):
        self._prospect_with_deps(fake_db, {})
        summary = run(["score"])
        assert summary.per_node["score"].failed == 0
        assert set(fake_db.prospects["p1"]["score_breakdown"]) == {
            "clerical_posting", "data_gen_tech", "case_study", "weak_front_door",
            "friction_reviews", "decision_maker_found", "in_drive_radius",
            "too_big", "status_uncertain",
        }
