"""Tests for turning one company's evidence into a spec the engine can evaluate.

Two things matter here and they pull in opposite directions. A figure the company
published must be used and must arrive as a claim, because their own number is
worth more than everything else in the model. Everything else must arrive as a
labelled assumption with a range, because we do not know it and a narrower guess
would only be a more confident one.
"""

from __future__ import annotations

import pytest

from lib import finmodel, offermodels


def with_hiring(value: str) -> dict:
    return {
        "company_name": "Hoosier Widget Works",
        "evidence_file": {
            "block3_hiring_signals": {
                "open_roles": [{
                    "value": value, "tier": 1,
                    "source_url": "https://hww.example/careers",
                    "date_checked": "2026-09-09",
                }],
            },
        },
    }


class TestTheirOwnNumbersComeFirst:
    def test_an_hourly_rate_they_posted_becomes_a_claim(self):
        found = offermodels.wage_from_evidence(
            with_hiring("Machinist wanted, $28.50 an hour, second shift."))
        assert found is not None
        interval, provenance = found
        assert interval.low == pytest.approx(28.50)
        assert provenance.kind == finmodel.CLAIM
        assert "block3_hiring_signals" in provenance.ref

    def test_a_posted_salary_becomes_an_hourly_claim(self):
        found = offermodels.wage_from_evidence(
            with_hiring("Estimator, $62,000-$74,000 a year."))
        assert found is not None
        interval, provenance = found
        assert interval.low == pytest.approx(62_000 / 2080)
        assert interval.high == pytest.approx(74_000 / 2080)
        assert provenance.kind == finmodel.CLAIM

    def test_nothing_posted_means_a_labelled_assumption(self):
        assert offermodels.wage_from_evidence(with_hiring("We are hiring.")) is None

    def test_a_wage_is_never_inferred_from_something_that_is_not_a_wage(self):
        # A grant amount is money about the company and says nothing about
        # payroll. Reading one as a wage would be the fabrication the whole
        # pipeline is built against.
        prospect = {
            "company_name": "X",
            "evidence_file": {"block2_grant_funded": {"grant_amount": {
                "value": "$71,912 awarded", "tier": 1,
                "source_url": "https://iedc.example", "date_checked": "2026-09-09"}}},
        }
        assert offermodels.wage_from_evidence(prospect) is None

    def test_the_assumed_wage_reaches_the_spec_labelled(self):
        spec = offermodels.build_spec(
            "quoting_velocity", with_hiring("We are hiring."), "scoped_build")
        wage = spec.inputs["wage_rate"]
        assert wage.provenance.kind == finmodel.ASSUMPTION
        assert "twenty-two to thirty-four" in wage.provenance.label


class TestEscalationFollowsItsSource:
    def test_a_published_index_arrives_as_a_claim(self):
        claim = {"value": "Employment Cost Index ... stood at 3.3 percent for the "
                          "period beginning 2026-04-01"}
        found = offermodels.escalation_input(claim, "bls.eci.private.twelve_month")
        assert found.value.low == pytest.approx(0.033)
        assert found.provenance.kind == finmodel.CLAIM

    def test_without_one_it_is_a_labelled_range_of_ours(self):
        found = offermodels.escalation_input(None, "")
        assert found.provenance.kind == finmodel.ASSUMPTION
        assert not found.value.is_point

    def test_an_unreadable_claim_falls_back_rather_than_guessing_at_it(self):
        found = offermodels.escalation_input({"value": "no number here"}, "series")
        assert found.provenance.kind == finmodel.ASSUMPTION


class TestTheSpecItself:
    def spec(self):
        return offermodels.build_spec(
            "quoting_velocity", with_hiring("We are hiring."), "premium_scope")

    def test_the_deployment_fee_is_the_ladder_band_and_says_it_is_unconfirmed(self):
        fee = self.spec().inputs["deployment_fee"]
        assert fee.value.as_pair() == (15_000.0, 30_000.0)
        assert "not yet been reconciled" in fee.provenance.label

    def test_the_loaded_multiplier_is_a_benchmark_with_an_id(self):
        multiplier = self.spec().inputs["loaded_multiplier"]
        assert multiplier.provenance.kind == finmodel.BENCHMARK
        assert multiplier.provenance.ref == offermodels.LOADED_MULTIPLIER

    def test_capacity_is_anchored_to_today_rather_than_guessed(self):
        # An independent guess at available hours put the crossing in month one,
        # which is not a finding, it is two of our own assumptions disagreeing.
        spec = self.spec()
        assert "capacity_headroom" in spec.inputs
        assert "available_hours_per_month" not in spec.inputs

    def test_every_input_says_where_it_came_from(self):
        for model_input in self.spec().inputs.values():
            assert model_input.provenance.kind != finmodel.DERIVED

    def test_it_evaluates_and_serialises(self):
        import json
        spec = self.spec()
        assert finmodel.evaluate(spec).value("annual_saving").low > 0
        assert json.loads(json.dumps(spec.as_json_dict()))

    def test_the_model_id_carries_the_company_the_pattern_and_the_shape(self):
        assert self.spec().model_id == (
            "hoosier_widget_works.quoting_velocity.premium_scope")

    def test_every_work_unit_in_the_library_builds_and_evaluates(self):
        for unit in offermodels.WORK_UNITS:
            report = offermodels.run_for(
                unit.pattern_key, with_hiring("We are hiring."), "scoped_build")
            assert report.payback["target"].cumulative.months
            assert report.scenarios["target"].value("annual_saving").low > 0
