"""Bespoke offers: shared patterns are allowed, shared sentences are not."""

from __future__ import annotations

from lib import differentiation as d

GRANT = "block2_grant_funded.what_the_grant_funded"
CERT = "block1_what_they_make.certifications[0]"
STACK = "block6_tech_stack.site_platform"
FORM = "block4_digital_front_door.has_contact_form"

CLAIMS = [
    (GRANT, {"value": "two Karlville sleever lines and a thermal printer"}),
    (CERT, {"value": "ISO 9001:2015"}),
    (STACK, {"value": "WordPress"}),
    (FORM, {"value": True}),
    ("block2_grant_funded.program_purpose",
     {"value": "The purpose of IRAP Contributions to Firms is to support research"}),
    ("block2_grant_funded.flags.program_recency", {"value": True}),
]
EVIDENCE = d.specific_evidence(CLAIMS)
NAMES = ["Acme Packaging Inc.", ""]


class TestTheCitationPatternCannotDrift:
    def test_it_is_the_drafters_pattern(self):
        from tools.drafter.main import CITATION
        assert d.CITATION.pattern == CITATION.pattern


class TestWhatCountsAsTheirs:
    def test_the_rules_kinds_count(self):
        assert d.kind_of(GRANT) == "grant purchase"
        assert d.kind_of(CERT) == "certification"
        assert d.kind_of(STACK) == "stack"
        assert d.kind_of("block7_people.named_people[1]") == "person"
        assert d.kind_of("block3_hiring_signals.open_roles[0]") == "posting"

    def test_what_every_company_has_does_not(self):
        assert d.kind_of(FORM) is None
        assert d.kind_of("block2_grant_funded.program_purpose") is None
        assert d.kind_of("block2_grant_funded.grant_amount") is None

    def test_a_flag_never_counts(self):
        assert d.kind_of("block6_tech_stack.flags.weak_front_door") is None

    def test_a_boolean_value_is_not_a_noun(self):
        assert FORM not in EVIDENCE.paths
        assert EVIDENCE.paths == frozenset({GRANT, CERT, STACK})


class TestRuleOneBinding:
    def test_two_of_their_claims_bind_a_scope(self):
        prose = f"Their sleever lines run daily [{GRANT}]. The shop holds ISO [{CERT}]."
        assert d.binding_failures(1, prose, EVIDENCE) == []

    def test_one_is_not_enough(self):
        prose = f"Their sleever lines run daily [{GRANT}]. Quotes are slow everywhere."
        failures = d.binding_failures(1, prose, EVIDENCE)
        assert failures and "Quotes are slow everywhere" in failures[0]

    def test_a_contact_form_does_not_bind_anything(self):
        prose = f"They have a contact form [{FORM}]. And a website [{FORM}]."
        assert d.binding_failures(1, prose, EVIDENCE)

    def test_the_same_claim_twice_is_one_claim(self):
        prose = f"Sleevers [{GRANT}]. Sleevers again [{GRANT}]."
        assert d.binding_failures(1, prose, EVIDENCE)

    def test_a_thin_file_is_held_to_what_it_has(self):
        """Asking for a second claim that does not exist is asking for an invention."""
        thin = d.specific_evidence([(GRANT, {"value": "one lathe"})])
        assert d.binding_failures(1, f"The lathe [{GRANT}].", thin) == []


class TestRuleThreeAnchor:
    def test_opening_on_their_fact_passes(self):
        prose = f"The grant bought two sleever lines [{GRANT}]. A record would show idle time."
        assert d.anchor_failures(1, prose, EVIDENCE) == []

    def test_opening_on_the_pattern_fails(self):
        prose = f"Manual quoting costs time. Their sleever lines [{GRANT}]."
        assert d.anchor_failures(1, prose, EVIDENCE)

    def test_a_citation_after_the_full_stop_still_belongs_to_the_sentence(self):
        prose = f"The grant bought two sleever lines. [{GRANT}] Then the build."
        assert d.anchor_failures(1, prose, EVIDENCE) == []

    def test_a_scope_label_is_not_the_fact(self):
        prose = f"Scope: the grant bought two sleever lines [{GRANT}]. More."
        assert d.anchor_failures(1, prose, EVIDENCE) == []


class TestRuleTwoNaming:
    def test_a_bare_pattern_name_fails(self):
        assert d.title_failures(1, "Quote Assembler", NAMES, EVIDENCE)

    def test_their_equipment_in_the_title_passes(self):
        assert d.title_failures(1, "Sleever-line utilisation record", NAMES, EVIDENCE) == []

    def test_a_plural_or_inflection_still_finds_their_noun(self):
        assert d.title_nouns("Sleevers utilisation record", NAMES, EVIDENCE) == ["sleevers"]

    def test_their_certification_counts(self):
        assert d.title_failures(1, "ISO 9001 inspection packet builder", NAMES, EVIDENCE) == []

    def test_their_company_name_does_not(self):
        """Stamping a name on an archetype is the cheapest fake of difference."""
        assert d.title_failures(1, "Acme quote assembler", NAMES, EVIDENCE)

    def test_a_noun_they_do_not_have_does_not(self):
        assert d.title_failures(1, "Wide-format sign quote assembler", NAMES, EVIDENCE)

    def test_the_fallback_says_whose_pattern_it_is(self):
        assert d.fallback_title("Quote assembler", "Acme Packaging Inc.") == (
            "Quote assembler — Acme Packaging")


class TestRuleFourAcrossTheBook:
    def test_a_title_another_company_holds_is_refused(self):
        failures = d.title_collisions(["Quote Assembler"], {"quote assembler": "Beta Ltd"})
        assert failures and "Beta Ltd" in failures[0]

    def test_case_and_punctuation_do_not_make_a_title_new(self):
        assert d.title_collisions(["QUOTE-assembler!"], {"quote assembler": "Beta Ltd"})

    SCOPE = ("a quote assembler that drafts from past job files and current material "
             "prices, sitting next to whatever they use today, gives the estimator back "
             "twenty to sixty hours a month on the target reading")

    def test_a_copied_scope_is_near_identical(self):
        assert d.shared_content(self.SCOPE, self.SCOPE) == 1.0
        assert d.near_copies([self.SCOPE], [("Beta Ltd", 2, self.SCOPE)])

    def test_the_same_pattern_in_their_own_words_is_not(self):
        theirs = ("their two Karlville sleever lines [x.y] produce run logs nobody reads, "
                  "so a utilisation record built on those logs returns 12 to 30 hours a "
                  "month on the target reading")
        assert d.shared_content(self.SCOPE, theirs) < d.NEAR_IDENTICAL
        assert d.near_copies([theirs], [("Beta Ltd", 1, self.SCOPE)]) == []

    def test_a_short_paragraph_is_not_called_a_copy(self):
        assert d.shared_content("a b c d", "a b c d") == 0.0


class TestReadingTheBookBack:
    def body(self):
        from tools.analyst.main import Analysis, Approach
        approach = Approach(
            number=1, name="Sleever-line utilisation record", pitch="One line.",
            core_build="a record", attacks="idle time", engagement="starter_automation",
            weeks=(1, 2), price=(600, 2500), annual_return=(1000, 2000),
            payback=(1.0, 4.0), reading="target", currency="USD",
            closes_peer_gap=None, prose=f"The grant bought sleevers [{GRANT}].\n\nMore.")
        return Analysis(
            sections={"s1_business": "Business.", "s2_findings": "1. One.",
                      "lead": "Open with one.", "s4_standing": "Standing.",
                      "s5_technical": "Tech.", "s6_questions": "Why?"},
            approaches=[approach], peer={}, thin=False, words=0).body()

    def test_offers_parse_back_out_of_a_stored_body(self):
        [(number, name, pitch, prose)] = d.parse_offers(self.body())
        assert (number, name, pitch) == (1, "Sleever-line utilisation record", "One line.")
        assert prose == f"The grant bought sleevers [{GRANT}].\n\nMore."

    def test_sections_parse_back_as_the_inverse_of_writing_them(self):
        from tools.analyst.main import sections_from_body
        sections = sections_from_body(self.body())
        assert sections["s2_findings"] == "1. One."
        assert sections["lead"] == "Open with one."
        assert sections["s6_questions"] == "Why?"

    def test_the_pattern_is_read_out_of_the_model_id(self):
        assert d.pattern_of("acme_inc.quoting_velocity.starter_automation") == (
            "quoting_velocity")


class TestTheBookReport:
    def offer(self, company, number, name, prose, pattern="quoting_velocity"):
        return d.Offer(company_id=company, company=company, country="Indiana",
                       number=number, name=name, pitch="", prose=prose, pattern=pattern)

    def test_shared_titles_are_counted_and_patterns_are_not_a_failure(self):
        offers = [self.offer("A", 1, "Quote Assembler", f"x [{GRANT}]"),
                  self.offer("B", 1, "Quote Assembler", f"y [{GRANT}]")]
        report = d.book_report(offers, {"A": EVIDENCE, "B": EVIDENCE},
                               {"A": NAMES, "B": NAMES})
        assert report["distinct_lead_titles"] == 1
        assert report["titles_shared_across_companies"] == 1
        assert report["patterns"] == [("quoting_velocity", 2)]


class TestTheGuard:
    """A rejected regeneration falls back to the shared pattern — never to a disguise."""

    def analysis(self, prose):
        from tools.analyst.main import Analysis, Approach
        approach = Approach(
            number=1, name="Quote Assembler", pitch="p", core_build="b", attacks="a",
            engagement="starter_automation", weeks=(1, 2), price=(600, 2500),
            annual_return=(1, 2), payback=(1.0, 2.0), reading="target",
            currency="USD", closes_peer_gap=None, prose=prose)
        return Analysis(sections={}, approaches=[approach], peer={}, thin=False, words=0)

    def test_a_generic_scope_is_not_rescued_by_a_new_title(self):
        from tools.analyst.main import with_fallback_titles
        analysis = self.analysis("Manual quoting costs every shop time.")
        failures = (d.binding_failures(1, analysis.approaches[0].prose, EVIDENCE)
                    + d.title_failures(1, "Quote Assembler", NAMES, EVIDENCE))
        verdict = {"failures": failures, "evidence": EVIDENCE, "allowed": set(),
                   "case_obj": None}
        prospect = {"company_name": "Acme Packaging Inc.", "dba_name": ""}
        assert with_fallback_titles(analysis, verdict, prospect, {}) is None
