"""Tests for the retroactive floor sweep.

The floor deflated after the fact: artifacts already written and already through
their gates turned out to have been generated for companies that did not
qualify. The sweep's job is to withhold those without pretending they failed,
and to give them back when the evidence catches up.
"""

from __future__ import annotations

from lib.claims import Tier, as_derivation, make_claim
from tools.floor import main as floor


def claim(value, **extra):
    built = make_claim(value, Tier.T1, "https://hww.example/about")
    built.update(extra)
    return built


def prospect(facts: int, flag: bool = False):
    block1 = {
        f"fact_{i}": claim(f"a fact {i}", claimcheck="verbatim")
        for i in range(facts)
    }
    evidence = {"block1_what_they_make": {**block1,
                                          "self_description": claim("We make.")}}
    if flag:
        evidence["block2_grant_funded"] = {"flags": {"has_case_study": as_derivation(
            claim(True, claimcheck="verbatim"), "a scoring flag")}}
    return {"id": "p1", "company_name": "Hoosier Widget Works",
            "evidence_file": evidence}


def artifact(status="sendable", kind="analysis", gate_map=None, failures=None):
    return {"id": "a1", "prospect_id": "p1", "kind": kind, "status": status,
            "gate_map": gate_map if gate_map is not None else {"thin": False},
            "gate_failures": failures or []}


VERDICTS = ("verbatim",)


class TestHolding:
    def test_a_company_below_the_floor_has_its_artifact_held(self):
        to_hold, to_release = floor.plan(
            [prospect(facts=1)], [artifact()], VERDICTS)
        assert len(to_hold) == 1 and to_release == []
        assert "below evidence floor" in to_hold[0][2]

    def test_a_company_clearing_the_floor_is_left_alone(self):
        to_hold, to_release = floor.plan(
            [prospect(facts=3)], [artifact()], VERDICTS)
        assert to_hold == [] and to_release == []

    def test_a_flag_does_not_carry_a_company_over_the_floor(self):
        # The whole reason the sweep exists: two real facts plus a boolean of
        # ours used to count as three.
        to_hold, _release = floor.plan(
            [prospect(facts=2, flag=True)], [artifact()], VERDICTS)
        assert len(to_hold) == 1

    def test_a_blocked_artifact_is_also_held(self):
        # Its prose failed AND its company does not qualify. The second is the
        # more useful thing for an operator to be told.
        to_hold, _release = floor.plan(
            [prospect(facts=1)], [artifact(status="blocked")], VERDICTS)
        assert len(to_hold) == 1

    def test_a_superseded_artifact_is_left_where_it_is(self):
        # Holding one would overwrite a record of what happened with a record
        # of what we later thought.
        to_hold, _release = floor.plan(
            [prospect(facts=1)], [artifact(status="superseded")], VERDICTS)
        assert to_hold == []

    def test_every_outbound_kind_is_swept_not_just_analyses(self):
        kinds = ["email", "linkedin", "thesis", "brief", "analysis"]
        to_hold, _release = floor.plan(
            [prospect(facts=1)],
            [artifact(kind=k) for k in kinds], VERDICTS)
        assert {a.get("kind") for _p, a, _r in to_hold} == set(kinds)


class TestTheHoldRecordsWhatItCameFrom:
    def test_it_stores_the_previous_status_and_the_reason(self):
        row = artifact()
        held = {"from": row["status"], "reason": "below evidence floor: 1 fact"}
        row["gate_map"] = {**row["gate_map"], floor.HOLD_KEY: held}
        assert floor.hold_record(row) == held

    def test_an_artifact_with_no_hold_record_reports_none(self):
        assert floor.hold_record(artifact()) is None


class TestReleasing:
    def held_artifact(self, previous="sendable"):
        return artifact(
            status=floor.HELD,
            gate_map={"thin": False, floor.HOLD_KEY: {
                "from": previous, "reason": "below evidence floor: 1 fact"}},
            failures=["held: below evidence floor: 1 fact"])

    def test_a_company_that_clears_the_floor_gets_its_artifact_back(self):
        _hold, to_release = floor.plan(
            [prospect(facts=3)], [self.held_artifact()], VERDICTS)
        assert len(to_release) == 1
        assert to_release[0][2] == "sendable"

    def test_it_returns_to_exactly_what_it_was_not_to_a_default(self):
        _hold, to_release = floor.plan(
            [prospect(facts=3)], [self.held_artifact(previous="blocked")], VERDICTS)
        assert to_release[0][2] == "blocked"

    def test_a_company_still_below_the_floor_stays_held(self):
        to_hold, to_release = floor.plan(
            [prospect(facts=1)], [self.held_artifact()], VERDICTS)
        assert to_hold == [] and to_release == []

    def test_holding_and_releasing_are_the_same_sweep(self):
        # Two companies, opposite directions, one pass. A release that needed a
        # separate command is a release that happens late.
        below = prospect(facts=1)
        above = {**prospect(facts=3), "id": "p2"}
        rows = [artifact(), {**self.held_artifact(), "id": "a2", "prospect_id": "p2"}]
        to_hold, to_release = floor.plan([below, above], rows, VERDICTS)
        assert len(to_hold) == 1 and len(to_release) == 1


class TestRouting:
    """The floor's answer routes rather than terminating. A company with two
    facts is not one to forget — it is one we cannot yet write TO."""

    def test_three_facts_route_to_a_full_dossier(self):
        from lib import routing
        assert routing.route_for(prospect(facts=3), VERDICTS) == routing.FULL

    def test_below_the_floor_routes_to_call_first(self):
        from lib import routing
        assert routing.route_for(prospect(facts=2), VERDICTS) == routing.CALL_FIRST

    def test_a_flag_does_not_route_a_company_to_full(self):
        from lib import routing
        assert routing.route_for(
            prospect(facts=2, flag=True), VERDICTS) == routing.CALL_FIRST

    def test_the_reason_is_the_floor_reason_a_reader_can_act_on(self):
        from lib import routing
        assert "below evidence floor" in routing.reason_for(
            prospect(facts=1), VERDICTS)

    def test_a_full_company_has_no_routing_reason_to_print(self):
        from lib import routing
        assert routing.reason_for(prospect(facts=3), VERDICTS) == ""

    def test_written_claims_are_out_of_scope_for_call_first(self):
        from lib import routing
        assert routing.may_write_claims(prospect(facts=2), VERDICTS) is False
        assert routing.may_write_claims(prospect(facts=3), VERDICTS) is True

    def test_the_split_keeps_the_order_it_was_given(self):
        from lib import routing
        rows = [
            {**prospect(facts=3), "id": "a", "company_name": "A"},
            {**prospect(facts=1), "id": "b", "company_name": "B"},
            {**prospect(facts=4), "id": "c", "company_name": "C"},
        ]
        full, call_first = routing.split(rows, VERDICTS)
        assert [p["company_name"] for p in full] == ["A", "C"]
        assert [p["company_name"] for p in call_first] == ["B"]

    def test_routing_is_recomputed_and_never_cached(self):
        # A stored routing is a count that cannot change — the same failure as a
        # velocity sentence with a frozen denominator.
        from lib import routing
        thin = prospect(facts=2)
        assert routing.route_for(thin, VERDICTS) == routing.CALL_FIRST
        thin["evidence_file"]["block1_what_they_make"]["extra"] = claim(
            "another fact", claimcheck="verbatim")
        assert routing.route_for(thin, VERDICTS) == routing.FULL


class TestAThinAnalysisIsNotHeld:
    """A thin analysis is what a below-floor company is SUPPOSED to have.
    Holding one withdraws the correct artifact for the correct reason and leaves
    the company with nothing at all."""

    def test_a_thin_analysis_survives_the_sweep(self):
        to_hold, _release = floor.plan(
            [prospect(facts=1)],
            [artifact(gate_map={"thin": True})], VERDICTS)
        assert to_hold == []

    def test_a_full_analysis_for_the_same_company_is_held(self):
        to_hold, _release = floor.plan(
            [prospect(facts=1)], [artifact(gate_map={"thin": False})], VERDICTS)
        assert len(to_hold) == 1

    def test_an_analysis_with_no_gate_map_is_treated_as_full(self):
        # The conservative reading: if we cannot tell, we do not ship it.
        to_hold, _release = floor.plan(
            [prospect(facts=1)], [artifact(gate_map={})], VERDICTS)
        assert len(to_hold) == 1

    def test_outbound_kinds_are_held_thin_or_not(self):
        # Outreach asserts claims whatever the analysis beside it looks like.
        to_hold, _release = floor.plan(
            [prospect(facts=1)],
            [artifact(kind="email", gate_map={"thin": True})], VERDICTS)
        assert len(to_hold) == 1
