"""Tests for the scope-of-work analyst.

The analyst is freed from sentence typing, so almost everything here is about
the rules that replaced it. Two of them carry the document: a figure is a range
or it names its source, and three approaches are three approaches rather than
one build at three prices. Both are gates, and a gate that is not tested is a
gate that quietly stops firing.
"""

from __future__ import annotations

import pytest

from lib import pricing
from tools.analyst import main as analyst

ALLOWED = {"block2_grant_funded.grant_amount", "block1_what_they_make.self_description"}
CITE = "[block2_grant_funded.grant_amount]"


def approach(number=1, **overrides):
    meta = {
        "name": "Quote assembler",
        "pitch": "Turn a two-day quote into a two-hour one.",
        "core_build": "a quoting draft tool reading past jobs and material prices",
        "attacks": "slow quote turnaround",
        "engagement": "scoped_build",
        # Distinct per approach by default: two approaches attacking different
        # problems do not cost the same by coincidence, and a fixture that says
        # they do would hide the rule that says so.
        "annual_return": [24_000 + number * 1_000, 60_000 + number * 1_000],
    }
    meta.update(overrides)
    return analyst.read_approach(number, meta, overrides.pop("prose", "prose here"))


class TestFiguresMustBeSourced:
    def test_a_range_needs_no_source(self):
        text = "If quoting runs somewhere between $25,000 and $40,000 a year."
        assert analyst.unsourced_figures(text, ALLOWED) == []

    def test_a_percentage_range_is_a_range(self):
        # "between 10% and 20%" was read as two point figures because the span
        # pattern stopped at the percent sign, so a sentence doing exactly what
        # was asked came back refused.
        text = "If that capital returns somewhere between 10% and 20% a year."
        assert analyst.unsourced_figures(text, ALLOWED) == []

    def test_a_point_figure_with_a_claim_behind_it_is_allowed(self):
        text = f"They were awarded $71,912 through the programme {CITE}."
        assert analyst.unsourced_figures(text, ALLOWED) == []

    def test_a_point_figure_may_name_its_source_in_words(self):
        text = "The grant record puts the award at $71,912."
        assert analyst.unsourced_figures(text, ALLOWED) == []

    def test_a_bare_point_figure_is_refused(self):
        text = "Their quoting desk costs about $30,000 a year."
        failures = analyst.unsourced_figures(text, ALLOWED)
        assert len(failures) == 1
        assert "$30,000" in failures[0]

    def test_the_refusal_says_how_to_fix_it(self):
        # The retry is handed these verbatim. A message that names a problem the
        # reader cannot act on costs a whole diagnostic cycle.
        failure = analyst.unsourced_figures("It costs $30,000 a year.", ALLOWED)[0]
        assert "write it as a range" in failure

    def test_a_citation_that_does_not_qualify_is_caught(self):
        text = "They run three shifts [block9_invented.thing]."
        assert analyst.unknown_citations(text, ALLOWED)

    def test_small_numbers_are_not_treated_as_claims(self):
        # "2 shifts" and "40 hours" are scaffolding a reader supplies for
        # themselves. Gating them blocked drafts over ordinals once already.
        text = "They run 2 shifts and the week is 40 hours."
        assert analyst.unsourced_figures(text, ALLOWED) == []


class TestApproachesRestOnTheLadder:
    def test_the_price_and_duration_come_from_the_ladder(self):
        built = approach()
        assert built.price == pricing.BY_KEY["scoped_build"].band
        assert built.weeks == pricing.BY_KEY["scoped_build"].weeks

    def test_a_price_that_is_not_on_the_ladder_is_refused(self):
        with pytest.raises(analyst.AnalysisRejected, match="Copy the band"):
            approach(price=[9_999, 11_111])

    def test_an_invented_engagement_shape_is_refused(self):
        with pytest.raises(analyst.AnalysisRejected, match="not on the ladder"):
            approach(engagement="transformation_programme")

    def test_a_single_return_figure_is_refused(self):
        with pytest.raises(analyst.AnalysisRejected, match="not a range"):
            approach(annual_return=[40_000, 40_000])

    def test_payback_is_computed_here_and_never_asked_for(self):
        built = approach(annual_return=[24_000, 60_000])
        assert built.payback == pricing.payback_months((8_000, 20_000), (24_000, 60_000))

    def test_payback_is_worst_case_against_best_case(self):
        # Quoting the midpoint of each band would produce one flattering number,
        # which is a point estimate wearing a division sign.
        fastest, slowest = approach().payback
        assert fastest < slowest

    def test_the_money_line_states_every_figure_as_a_range(self):
        line = approach().roi_line
        assert "$8,000-$20,000" in line and "2-4 weeks" in line
        assert "pays back in" in line

    def test_a_missing_field_names_what_is_missing(self):
        with pytest.raises(analyst.AnalysisRejected, match="core_build"):
            approach(core_build="")


class TestThreeApproachesAreThree:
    def test_the_same_build_twice_is_refused(self):
        one = approach(1)
        two = approach(2, name="Quoting tool",
                       core_build="a quoting draft tool reading past jobs and prices",
                       engagement="extended_build")
        failures = analyst.distinctness_failures([one, two])
        assert failures and "same build at two prices" in failures[0]

    def test_the_same_problem_in_the_same_shape_is_refused(self):
        one = approach(1)
        two = approach(2, core_build="a scheduling board fed by machine output",
                       attacks="slow quote turnaround times")
        failures = analyst.distinctness_failures([one, two])
        assert failures and "same engagement shape" in failures[0]

    def test_the_same_problem_in_a_different_shape_is_allowed(self):
        # A pilot and a build against the same friction are a real choice for
        # the operator to offer, not a discount ladder.
        one = approach(1)
        two = approach(2, core_build="a four-week measured trial on one cell",
                       attacks="slow quote turnaround times",
                       engagement="pilot_then_build")
        assert analyst.distinctness_failures([one, two]) == []

    def test_three_genuinely_different_approaches_pass(self):
        built = [
            approach(1),
            approach(2, core_build="a weekly report off the press monitoring output",
                     attacks="unread machine data", engagement="diagnostic"),
            approach(3, core_build="capability pages a buyer's assistant can read",
                     attacks="invisible to search", engagement="pilot_then_build"),
        ]
        assert analyst.distinctness_failures(built) == []

    def test_shared_filler_words_do_not_make_two_builds_alike(self):
        one = approach(1, core_build="building a working tool for the quoting team")
        two = approach(2, core_build="building a working report for the press line",
                       attacks="unread machine data", engagement="diagnostic")
        assert analyst.distinctness_failures([one, two]) == []


class TestTransport:
    def wrap(self, label, body):
        return f"<<<PROSE {label}>>>\n{body}\n<<<END>>>\n"

    def full_reply(self):
        parts = [self.wrap("s1_business", "They machine parts. " * 10),
                 self.wrap("s2_findings", "1. First finding. 2. Second finding. " * 5)]
        for n in (1, 2, 3):
            parts.append(self.wrap(f"approach={n}", f"Scope {n}. " * 10))
            parts.append(
                f'<<<MAP approach={n}>>>{{"name": "A{n}", "pitch": "p", '
                f'"core_build": "build number {n} of a distinct kind here", '
                f'"attacks": "problem {n}", "engagement": "scoped_build", '
                f'"annual_return": [10000, 20000]}}<<<END>>>\n')
        parts += [self.wrap("lead", "Open with one. " * 8),
                  self.wrap("s4_standing", "They sit mid-group. " * 8),
                  self.wrap("s5_technical", "They run a hosted site. " * 8),
                  self.wrap("s6_questions", "What is your volume? " * 8)]
        return "".join(parts)

    def test_a_full_reply_parses_into_sections_and_approaches(self):
        prose, metadata = analyst.parse_analysis(self.full_reply(), thin=False)
        assert set(analyst.FULL_SECTIONS) <= set(prose)
        assert [n for n, _m in metadata] == [1, 2, 3]

    def test_a_missing_section_is_named(self):
        reply = self.full_reply().replace(
            self.wrap("s5_technical", "They run a hosted site. " * 8), "")
        with pytest.raises(analyst.AnalysisRejected, match="s5_technical"):
            analyst.parse_analysis(reply, thin=False)

    def test_only_two_approaches_is_refused(self):
        reply = self.full_reply().replace('<<<PROSE approach=3>>>', '<<<PROSE spare>>>')
        reply = reply.replace('<<<MAP approach=3>>>', '<<<MAP approach=9>>>')
        with pytest.raises(analyst.AnalysisRejected):
            analyst.parse_analysis(reply, thin=False)

    def test_a_thin_reply_needs_only_the_sections_its_evidence_carries(self):
        reply = (self.wrap("s1_business", "They machine parts. " * 10)
                 + self.wrap("s4_standing", "They sit mid-group. " * 8)
                 + self.wrap("s6_questions", "What is your volume? " * 8))
        prose, metadata = analyst.parse_analysis(reply, thin=True)
        assert set(prose) == set(analyst.THIN_SECTIONS)
        assert metadata == []

    def test_a_thin_reply_is_not_asked_for_priced_approaches(self):
        # Below the floor there is nothing to cost. Generating three priced
        # approaches from two facts is the failure the floor exists to prevent.
        assert "approach" not in analyst.format_rule(thin=True)
        assert "price" not in analyst.format_rule(thin=True)


class TestTheGate:
    def sections(self, **overrides):
        base = {
            "s1_business": ("They machine stamped parts for vehicle assemblers "
                            f"{CITE}. That suggests volume matters more than mix. "
                            * 12),
            "s2_findings": (
                f"1. Quoting is manual {CITE}. If it runs somewhere between "
                "$25,000 and $40,000 a year, that is real money. "
                "2. Machine output goes unread, which implies the capital is "
                f"only partly returning {CITE}. " * 8),
            "s4_standing": ("They sit behind 3 of the 11 comparable companies we "
                            "hold, which suggests the gap is practice, not scale. "
                            * 12),
            "s5_technical": ("They run a hosted site with no visible integration "
                             "surface, which implies a file drop rather than an "
                             "interface. " * 10),
            "s6_questions": ("How many quotes go out a month? What does the press "
                             "actually log? Who owns the scheduling board? " * 10),
            "lead": "Open with the quoting work, which suggests the fastest proof. " * 3,
        }
        base.update(overrides)
        return base

    def three(self):
        return [
            approach(1, prose=(f"Reads past jobs {CITE}. Assumed: 30 to 50 quotes "
                               "a month, verify on the call. " * 4)),
            approach(2, core_build="a weekly report off the press monitoring output",
                     attacks="unread machine data", engagement="diagnostic",
                     prose=(f"Reads the press output {CITE}. Assumed: the controller "
                            "exports, verify on the call. " * 4)),
            approach(3, core_build="capability pages a buyer's assistant can read",
                     attacks="invisible to search", engagement="pilot_then_build",
                     prose=(f"Rewrites the pages {CITE}. Assumed: they can edit the "
                            "site, verify on the call. " * 4)),
        ]

    def test_a_sound_analysis_passes(self):
        verdict = analyst.gate_analysis(self.sections(), self.three(), ALLOWED, False)
        assert verdict["passed"], verdict["failures"]

    def test_internal_vocabulary_is_refused(self):
        sections = self.sections()
        sections["s1_business"] += " This claim is corroborated at tier 1."
        verdict = analyst.gate_analysis(sections, self.three(), ALLOWED, False)
        assert any("internal vocabulary" in f for f in verdict["failures"])

    def test_a_claim_reference_is_not_mistaken_for_vocabulary(self):
        # The citations contain "block2" and stay in the document on purpose,
        # so the readability checks have to run with them taken out.
        assert analyst.jargon_in(f"The award was recorded {CITE}.") == []

    def test_unnumbered_findings_are_refused(self):
        sections = self.sections(
            s2_findings=f"Quoting is manual {CITE} and costs money. " * 8)
        verdict = analyst.gate_analysis(sections, self.three(), ALLOWED, False)
        assert any("not numbered" in f for f in verdict["failures"])

    def test_an_approach_that_never_labels_an_assumption_is_refused(self):
        built = self.three()
        built[0] = approach(1, prose=f"Reads past jobs and drafts a quote {CITE}. " * 4)
        verdict = analyst.gate_analysis(self.sections(), built, ALLOWED, False)
        assert any("never labels an assumption" in f for f in verdict["failures"])

    def test_a_standing_section_that_ignores_the_comparison_is_refused(self):
        sections = self.sections(
            s4_standing="They are a good business with a tidy site. " * 6)
        verdict = analyst.gate_analysis(sections, self.three(), ALLOWED, False)
        assert any("never refers to the comparison" in f for f in verdict["failures"])

    def test_an_analysis_with_no_reasoning_is_refused(self):
        sections = {k: v.replace("suggests", "is").replace("implies", "is")
                    for k, v in self.sections().items()}
        built = [a._replace(prose=a.prose.replace("suggests", "is"))
                 for a in self.three()]
        verdict = analyst.gate_analysis(sections, built, ALLOWED, False)
        assert any("reads as reasoning" in f for f in verdict["failures"])

    def test_a_thin_analysis_is_not_held_to_the_full_shape(self):
        thin = {k: v for k, v in self.sections().items()
                if k in analyst.THIN_SECTIONS}
        verdict = analyst.gate_analysis(thin, [], ALLOWED, True)
        assert verdict["passed"], verdict["failures"]

    def test_the_verdict_records_what_was_cited(self):
        verdict = analyst.gate_analysis(self.sections(), self.three(), ALLOWED, False)
        assert "block2_grant_funded.grant_amount" in verdict["cited"]


class TestSpend:
    def test_the_estimate_is_priced_for_the_model_the_analyst_uses(self):
        # The drafter's counter prices a different model. Sharing it without
        # overriding would report a total that is quietly wrong.
        spend = analyst.Spend()
        spend.input_tokens, spend.output_tokens = 1_000_000, 1_000_000
        assert spend.dollars == pytest.approx(sum(analyst.PRICE_PER_MTOK))

    def test_the_estimate_grows_with_the_batch(self):
        assert analyst.estimate(16) > analyst.estimate(1)

    def test_a_realistic_batch_sits_under_the_ceiling(self):
        assert analyst.estimate(21) < analyst.CEILING


class TestReturnsAreWorkedOutSeparately:
    def test_two_approaches_claiming_the_same_return_are_refused(self):
        # A diagnostic that returns exactly what the build returns has not been
        # costed; its arithmetic was copied from the approach beside it.
        one = approach(1)
        two = approach(2, core_build="a weekly report off the press output",
                       attacks="unread machine data", engagement="diagnostic",
                       annual_return=one.annual_return)
        failures = analyst.distinctness_failures([one, two])
        assert failures and "same return to the dollar" in failures[0]

    def test_returns_worked_out_separately_pass(self):
        one = approach(1)
        two = approach(2, core_build="a weekly report off the press output",
                       attacks="unread machine data", engagement="diagnostic",
                       annual_return=[9_000, 22_000])
        assert analyst.distinctness_failures([one, two]) == []


class TestCitationsSideBySide:
    def test_two_references_in_a_row_are_read_as_two(self):
        # Written loosely the pattern merged them into one id that exists
        # nowhere, so a sentence citing two claims correctly was refused for
        # citing one that does not qualify.
        text = ("They run a hosted site "
                "[block4_digital_front_door.has_contact_form]"
                "[block6_tech_stack.site_platform].")
        assert analyst.CITATION.findall(text) == [
            "block4_digital_front_door.has_contact_form",
            "block6_tech_stack.site_platform"]

    def test_an_indexed_claim_still_reads_as_one(self):
        text = "As their vice president put it [block7_people.leadership_quotes[12]]."
        assert analyst.CITATION.findall(text) == [
            "block7_people.leadership_quotes[12]"]

    def test_side_by_side_references_do_not_trip_the_unknown_check(self):
        allowed = {"block4_digital_front_door.has_contact_form",
                   "block6_tech_stack.site_platform"}
        text = ("They run a hosted site "
                "[block4_digital_front_door.has_contact_form]"
                "[block6_tech_stack.site_platform].")
        assert analyst.unknown_citations(text, allowed) == []
