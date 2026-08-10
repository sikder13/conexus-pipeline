"""Regression tests for permanent versus transient skips.

Ten summary work items were skipped for a missing ANTHROPIC_API_KEY and became
permanently unreachable: the selector listed only pending/failed/running, plus
'done' under --force, so no combination of flags could ever pick a skipped item
back up. These tests pin the rule that replaced it.

The distinction is the node's to declare, not the runner's to infer from text:
a NodeResult carries a SkipKind, the runner stores it on the work item, and the
selector reads it back.
"""

from __future__ import annotations

import pytest

from lib.nodes import NodeResult, RobotsDisallowed, SkipKind
from lib.runner import _is_selectable
from tests.conftest import run_quiet as run
from tests.test_runner import make_node


def item(status="skipped", skip_kind=None, attempts=0):
    return {"id": "i1", "status": status, "attempts": attempts, "skip_kind": skip_kind}


class FakeNode:
    max_attempts = 3


NODE = FakeNode()


class TestSelectability:
    def test_a_transient_skip_is_selected_on_an_ordinary_run(self):
        # No --force required. Requiring a flag to retry a skip the node itself
        # called temporary is what stranded the summaries.
        assert _is_selectable(item(skip_kind="transient"), NODE, False, False) is True

    def test_a_legacy_skip_with_no_kind_is_treated_as_transient(self):
        # The ten stranded rows predate the column; null must not mean permanent.
        assert _is_selectable(item(skip_kind=None), NODE, False, False) is True

    def test_a_permanent_skip_is_left_alone_on_an_ordinary_run(self):
        assert _is_selectable(item(skip_kind="permanent"), NODE, False, False) is False

    def test_force_alone_does_not_re_run_a_permanent_skip(self):
        # --force is about redoing settled work, not about overriding a node's
        # judgment that a prospect will never be applicable.
        assert _is_selectable(item(skip_kind="permanent"), NODE, True, False) is False

    def test_permanent_skips_need_their_own_flag(self):
        assert _is_selectable(item(skip_kind="permanent"), NODE, False, True) is True

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_unfinished_items_are_always_selected(self, status):
        assert _is_selectable(item(status=status), NODE, False, False) is True

    def test_done_needs_force(self):
        assert _is_selectable(item(status="done"), NODE, False, False) is False
        assert _is_selectable(item(status="done"), NODE, True, False) is True

    def test_a_failed_item_is_selected_until_attempts_run_out(self):
        assert _is_selectable(item(status="failed", attempts=2), NODE, False, False) is True
        assert _is_selectable(item(status="failed", attempts=3), NODE, False, False) is False
        assert _is_selectable(item(status="failed", attempts=3), NODE, True, False) is True

    def test_no_item_is_unreachable_by_every_flag_combination(self):
        """The property the original bug violated: nothing is stranded."""
        combinations = [(f, p) for f in (False, True) for p in (False, True)]
        states = [
            item(status="pending"), item(status="running"),
            item(status="done"), item(status="failed", attempts=99),
            item(skip_kind=None), item(skip_kind="transient"), item(skip_kind="permanent"),
        ]
        for state in states:
            assert any(_is_selectable(state, NODE, f, p) for f, p in combinations), state


class TestRoundTrip:
    def test_a_transient_skip_is_recorded_and_re_run_next_time(self, registry, fake_db):
        calls = []

        def behaviour(prospect, ctx):
            calls.append(prospect["id"])
            if len(calls) == 1:
                return NodeResult(skipped=True, skip_reason="key missing")
            return NodeResult(prospect_patch={"machine_summary": "written"})

        make_node("drafter", behaviour=behaviour)
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "drafter")

        first = run(["drafter"])
        assert first.per_node["drafter"].skipped == 1
        stored = fake_db.item_for("p1", "drafter")
        assert stored["status"] == "skipped"
        assert stored["skip_kind"] == "transient"

        # The credential arrives; an ordinary run picks it up with no flag.
        second = run(["drafter"])
        assert second.per_node["drafter"].done == 1
        assert calls == ["p1", "p1"]
        assert fake_db.prospects["p1"]["machine_summary"] == "written"

    def test_a_permanent_skip_is_recorded_and_not_re_run(self, registry, fake_db):
        calls = []
        make_node(
            "only_some",
            behaviour=lambda p, c: calls.append(p["id"])
            or NodeResult(skipped=True, skip_kind=SkipKind.PERMANENT, skip_reason="no page"),
        )
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "only_some")

        run(["only_some"])
        assert fake_db.item_for("p1", "only_some")["skip_kind"] == "permanent"

        run(["only_some"])
        run(["only_some"], force=True)
        assert calls == ["p1"], "a permanent skip was re-attempted without its flag"

        run(["only_some"], include_permanent_skips=True)
        assert calls == ["p1", "p1"]

    def test_succeeding_clears_a_previous_skip_kind(self, registry, fake_db):
        make_node("flip", behaviour=lambda p, c: NodeResult())
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "flip", status="skipped", skip_kind="transient")

        run(["flip"])
        stored = fake_db.item_for("p1", "flip")
        assert stored["status"] == "done"
        assert stored["skip_kind"] is None

    def test_failing_clears_a_previous_skip_kind(self, registry, fake_db):
        make_node("boom", behaviour=lambda p, c: (_ for _ in ()).throw(ValueError("nope")))
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "boom", status="skipped", skip_kind="transient")

        run(["boom"])
        stored = fake_db.item_for("p1", "boom")
        assert stored["status"] == "failed"
        assert stored["skip_kind"] is None


class TestLimitPicksRunnableItems:
    def test_limit_counts_items_that_will_actually_run(self, registry, fake_db):
        """--limit N must mean N items that run, not the N oldest rows.

        The reported symptom: `--limit 10` took the ten oldest summary rows,
        all of whose score dependency was still pending, and the run did
        nothing while ten genuinely ready items sat behind them.
        """
        make_node("upstream")
        ran = []
        make_node(
            "downstream",
            deps=("upstream",),
            behaviour=lambda p, c: ran.append(p["id"]) or NodeResult(),
        )
        # Two blocked prospects created first, then two ready ones.
        for name in ("blocked1", "blocked2"):
            fake_db.add_prospect(name)
            fake_db.add_item(name, "upstream", status="pending")
            fake_db.add_item(name, "downstream", status="pending")
        for name in ("ready1", "ready2"):
            fake_db.add_prospect(name)
            fake_db.add_item(name, "upstream", status="done")
            fake_db.add_item(name, "downstream", status="pending")

        run(["downstream"], limit=2)
        assert sorted(ran) == ["ready1", "ready2"]


class TestRobotsIsNotAFailure:
    """Declining to fetch a disallowed URL is the tool working, not breaking.

    Five sites in the first full Pass A run disallowed us. Counting those as
    errors made a compliant run look like a broken one, and left the items
    retrying a request that must never be made.
    """

    def _run(self, fake_db, registry, exc):
        def raises(prospect, ctx):
            raise exc

        make_node("blocked", behaviour=raises)
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "blocked", status="pending")
        return run(["blocked"])

    def test_it_is_recorded_as_a_skip_not_a_failure(self, fake_db, registry):
        counts = self._run(
            fake_db, registry, RobotsDisallowed("robots.txt disallows https://x.test/")
        ).per_node["blocked"]
        assert counts.failed == 0
        assert counts.skipped == 1

    def test_the_skip_is_permanent(self, fake_db, registry):
        self._run(fake_db, registry, RobotsDisallowed("robots.txt disallows https://x.test/"))
        item = fake_db.item_for("p1", "blocked")
        assert item["status"] == "skipped"
        assert item["skip_kind"] == str(SkipKind.PERMANENT)

    def test_the_reason_names_robots_so_an_operator_can_tell_why(self, fake_db, registry):
        self._run(fake_db, registry, RobotsDisallowed("robots.txt disallows https://x.test/"))
        assert "robots.txt" in fake_db.item_for("p1", "blocked")["last_error"]

    def test_an_ordinary_error_is_still_a_failure(self, fake_db, registry):
        counts = self._run(
            fake_db, registry, RuntimeError("home page returned HTTP 403")
        ).per_node["blocked"]
        assert counts.failed == 1
        assert counts.skipped == 0
        assert fake_db.item_for("p1", "blocked")["status"] == "failed"


class TestOpenSessionsAreNeverTouched:
    """A file a human has open in the console is off limits to every node.

    The verifier disposes claims one at a time against what is on screen.
    Rewriting the evidence underneath them would invalidate decisions already
    made, and do it invisibly — the screen would still show the old values.
    """

    def test_a_prospect_with_an_open_session_is_not_selected(self, fake_db, registry):
        make_node("touchy")
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "touchy", status="pending")
        fake_db.open_session_ids = {"p1"}
        counts = run(["touchy"]).per_node["touchy"]
        assert counts.done == 0
        assert counts.pending == 1
        assert fake_db.item_for("p1", "touchy")["status"] == "pending"

    def test_other_prospects_still_run(self, fake_db, registry):
        make_node("touchy")
        for pid in ("p1", "p2"):
            fake_db.add_prospect(pid)
            fake_db.add_item(pid, "touchy", status="pending")
        fake_db.open_session_ids = {"p1"}
        counts = run(["touchy"]).per_node["touchy"]
        assert counts.done == 1
        assert fake_db.item_for("p2", "touchy")["status"] == "done"

    def test_no_open_session_means_business_as_usual(self, fake_db, registry):
        make_node("touchy")
        fake_db.add_prospect("p1")
        fake_db.add_item("p1", "touchy", status="pending")
        assert run(["touchy"]).per_node["touchy"].done == 1
