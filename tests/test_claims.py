"""Tests for lib/claims.py — the rules that keep unsourced facts out of the database.

These tests are the executable statement of DATA-1: what a claim must contain,
what may be asserted to a prospect, and what the validator must catch before a
write reaches Postgres. No network access, no database.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from lib.claims import (
    CLAIM_KEYS,
    Tier,
    is_assertable,
    make_claim,
    mark_verified,
    requires_attribution,
    validate_evidence_file,
)

GOOD_URL = "https://www.in.gov/iedc/programs/manufacturing-readiness-grants/"


def a_claim(tier: Tier = Tier.T1, value: object = "42 employees") -> dict:
    """A well-formed unverified claim, for tests that need one as a starting point."""
    return make_claim(value, tier, GOOD_URL, date_checked=date(2026, 8, 1))


class TestTier:
    def test_tiers_are_one_through_four(self):
        assert [int(tier) for tier in Tier] == [1, 2, 3, 4]

    def test_every_tier_documents_what_it_permits(self):
        for tier in Tier:
            assert tier.__doc__
            assert tier.outreach_rule == tier.__doc__

    def test_unknown_tier_is_rejected(self):
        with pytest.raises(ValueError):
            Tier(5)
        with pytest.raises(ValueError):
            Tier(0)


class TestMakeClaim:
    def test_builds_the_canonical_shape(self):
        claim = a_claim()
        assert set(claim) == set(CLAIM_KEYS)
        assert claim["value"] == "42 employees"
        assert claim["tier"] == 1
        assert claim["source_url"] == GOOD_URL
        assert claim["date_checked"] == "2026-08-01"
        assert claim["verified"] is False
        assert claim["verified_at"] is None

    def test_tier_is_stored_as_a_plain_int_for_jsonb(self):
        claim = a_claim(Tier.T3)
        assert type(claim["tier"]) is int
        assert claim["tier"] == 3

    def test_date_checked_defaults_to_today(self):
        claim = make_claim("x", Tier.T1, GOOD_URL)
        assert claim["date_checked"] == date.today().isoformat()

    def test_accepts_a_bare_int_tier(self):
        assert make_claim("x", 2, GOOD_URL)["tier"] == 2

    def test_falsy_values_are_still_claims(self):
        # 0 and False are facts; only None means "we do not know".
        assert make_claim(0, Tier.T1, GOOD_URL)["value"] == 0
        assert make_claim(False, Tier.T1, GOOD_URL)["value"] is False
        assert make_claim("", Tier.T1, GOOD_URL)["value"] == ""

    def test_none_value_is_rejected(self):
        with pytest.raises(ValueError, match="not a claim"):
            make_claim(None, Tier.T1, GOOD_URL)

    @pytest.mark.parametrize("bad_url", ["", "   ", None, 42])
    def test_empty_source_url_is_rejected(self, bad_url):
        with pytest.raises(ValueError, match="source_url is required"):
            make_claim("x", Tier.T1, bad_url)

    @pytest.mark.parametrize(
        "bad_url",
        [
            "example.com/page",
            "ftp://example.com/file",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "https://",
        ],
    )
    def test_non_http_source_url_is_rejected(self, bad_url):
        with pytest.raises(ValueError, match="http"):
            make_claim("x", Tier.T1, bad_url)

    def test_source_url_is_stripped(self):
        assert make_claim("x", Tier.T1, f"  {GOOD_URL}  ")["source_url"] == GOOD_URL

    def test_invalid_tier_is_rejected(self):
        with pytest.raises(ValueError):
            make_claim("x", 5, GOOD_URL)

    def test_date_checked_must_be_a_date(self):
        with pytest.raises(ValueError, match="datetime.date"):
            make_claim("x", Tier.T1, GOOD_URL, date_checked="2026-08-01")


class TestMarkVerified:
    def test_sets_verified_and_timestamp(self):
        verified = mark_verified(a_claim())
        assert verified["verified"] is True
        stamped = datetime.fromisoformat(verified["verified_at"])
        assert stamped.tzinfo is not None

    def test_returns_a_copy_and_leaves_the_original_alone(self):
        original = a_claim()
        verified = mark_verified(original)
        assert verified is not original
        assert original["verified"] is False
        assert original["verified_at"] is None

    def test_preserves_every_other_field(self):
        original = a_claim(Tier.T2, value="named in the Star Press")
        verified = mark_verified(original)
        for key in ("value", "tier", "source_url", "date_checked"):
            assert verified[key] == original[key]

    def test_refuses_a_malformed_claim(self):
        with pytest.raises(ValueError, match="missing keys"):
            mark_verified({"value": "x", "source_url": GOOD_URL})

    def test_refuses_a_non_dict(self):
        with pytest.raises(ValueError, match="must be a dict"):
            mark_verified("not a claim")


class TestAssertability:
    def test_only_verified_t1_is_assertable(self):
        assert is_assertable(mark_verified(a_claim(Tier.T1))) is True

    @pytest.mark.parametrize("tier", [Tier.T2, Tier.T3, Tier.T4])
    def test_verified_lower_tiers_are_not_assertable(self, tier):
        assert is_assertable(mark_verified(a_claim(tier))) is False

    @pytest.mark.parametrize("tier", list(Tier))
    def test_unverified_claims_are_never_assertable(self, tier):
        assert is_assertable(a_claim(tier)) is False

    def test_non_dict_is_not_assertable(self):
        assert is_assertable(None) is False

    def test_only_t2_requires_attribution(self):
        assert requires_attribution(a_claim(Tier.T2)) is True
        for tier in (Tier.T1, Tier.T3, Tier.T4):
            assert requires_attribution(a_claim(tier)) is False

    def test_attribution_does_not_depend_on_verification(self):
        assert requires_attribution(mark_verified(a_claim(Tier.T2))) is True


class TestValidateEvidenceFile:
    def test_well_formed_evidence_has_no_problems(self):
        evidence = {
            "block1": {"grant": a_claim(Tier.T1)},
            "block2": {"press": [a_claim(Tier.T2), mark_verified(a_claim(Tier.T1))]},
            "notes": "no contact path found for the plant manager",
        }
        assert validate_evidence_file(evidence) == []

    def test_none_evidence_is_acceptable(self):
        # Matches the trigger, which returns early when evidence_file is null.
        assert validate_evidence_file(None) == []

    def test_non_object_evidence_is_reported(self):
        problems = validate_evidence_file(["not", "an", "object"])
        assert len(problems) == 1
        assert "JSON object" in problems[0]

    @pytest.mark.parametrize("missing_key", ["tier", "source_url", "date_checked"])
    def test_each_trigger_required_key_is_caught(self, missing_key):
        claim = a_claim()
        del claim[missing_key]
        problems = validate_evidence_file({"block1": {"headcount": claim}})
        assert any(f"missing required key '{missing_key}'" in problem for problem in problems)

    def test_problem_names_the_path_to_the_bad_claim(self):
        claim = a_claim()
        del claim["tier"]
        problems = validate_evidence_file({"block1": {"headcount": claim}})
        assert all(problem.startswith("evidence_file.block1.headcount:") for problem in problems)

    def test_claims_nested_in_lists_are_checked(self):
        claim = a_claim()
        del claim["source_url"]
        problems = validate_evidence_file({"block2": {"press": [a_claim(), claim]}})
        assert any("evidence_file.block2.press[1]:" in problem for problem in problems)

    @pytest.mark.parametrize("bad_tier", [0, 5, 99, -1])
    def test_out_of_range_tier_is_caught(self, bad_tier):
        claim = a_claim()
        claim["tier"] = bad_tier
        problems = validate_evidence_file({"headcount": claim})
        assert any("tier must be 1-4" in problem for problem in problems)

    @pytest.mark.parametrize("bad_tier", ["1", 1.0, True, None])
    def test_non_integer_tier_is_caught(self, bad_tier):
        claim = a_claim()
        claim["tier"] = bad_tier
        problems = validate_evidence_file({"headcount": claim})
        assert any("tier must be an integer 1-4" in problem for problem in problems)

    @pytest.mark.parametrize(
        "bad_date", ["2026-8-1", "01/08/2026", "not-a-date", "20260801", 20260801]
    )
    def test_malformed_date_checked_is_caught(self, bad_date):
        claim = a_claim()
        claim["date_checked"] = bad_date
        problems = validate_evidence_file({"headcount": claim})
        assert any("date_checked must be a YYYY-MM-DD string" in problem for problem in problems)

    def test_well_formed_but_impossible_date_is_caught(self):
        claim = a_claim()
        claim["date_checked"] = "2026-13-45"
        problems = validate_evidence_file({"headcount": claim})
        assert any("not a real calendar date" in problem for problem in problems)

    def test_non_boolean_verified_is_caught(self):
        claim = a_claim()
        claim["verified"] = "yes"
        problems = validate_evidence_file({"headcount": claim})
        assert any("verified must be a boolean" in problem for problem in problems)

    def test_malformed_verified_at_is_caught(self):
        claim = mark_verified(a_claim())
        claim["verified_at"] = "last Tuesday"
        problems = validate_evidence_file({"headcount": claim})
        assert any("not an ISO timestamp" in problem for problem in problems)

    def test_verified_without_a_timestamp_is_caught(self):
        claim = a_claim()
        claim["verified"] = True
        problems = validate_evidence_file({"headcount": claim})
        assert any("verified_at is null" in problem for problem in problems)

    def test_bad_source_url_is_caught(self):
        claim = a_claim()
        claim["source_url"] = "example.com"
        problems = validate_evidence_file({"headcount": claim})
        assert any("source_url must be a non-empty http(s) URL" in problem for problem in problems)

    def test_every_problem_is_reported_not_just_the_first(self):
        claim = a_claim()
        del claim["tier"]
        claim["source_url"] = ""
        claim["date_checked"] = "nope"
        problems = validate_evidence_file({"headcount": claim})
        assert len(problems) == 3

    def test_plain_data_without_a_value_key_is_left_alone(self):
        evidence = {"machine_summary": "Family-owned extruder, 40 staff.", "checked": ["a", "b"]}
        assert validate_evidence_file(evidence) == []
