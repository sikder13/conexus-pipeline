"""The fragment letter, and the four moves it is built out of.

The interesting rules are the refusals. A letter that quotes a figure for a
company with nothing to anchor it to is the same fabrication the analysis
avoided, printed on paper and posted.
"""

from __future__ import annotations

from lib import anchors
from lib.claims import Tier, operator_claim
from tools.drafter import main as drafter

SOURCE = "https://acme.test/about"


def prospect(**kw):
    row = {
        "id": "p1", "company_name": "Acme Fabrication",
        "source_adapter": "conexus_iedc",
        "evidence_file": {"block2_grant_funded": {
            "grant_amount": operator_claim("$250,000", Tier.T1,
                                           "https://conexus.test/round-4"),
        }},
    }
    row.update(kw)
    return row


def analysis(anchor_kind: str, band=(41000, 88000)):
    return {"gate_map": {
        "approaches": [{"name": "Quote assembler", "annual_return": list(band),
                        "reading": "target", "engagement": "diagnostic"}],
        "case": {"anchor": {"kind": anchor_kind, "detail": "anchored on 62 people",
                            "model_type": anchors.LABOUR_HOURS}},
    }}


class TestTheContextIsReadFromTheAnalysis:
    def test_the_figure_comes_from_the_document_on_the_desk(self):
        context = drafter.letter_context(prospect(), analysis(anchors.HEADCOUNT))
        assert context["figure"]["words"] == "$41,000-$88,000 USD a year"
        assert context["anchor_kind"] == anchors.HEADCOUNT

    def test_the_grant_is_the_anchor_and_carries_its_claim_path(self):
        context = drafter.letter_context(prospect(), analysis(anchors.HEADCOUNT))
        assert context["grant"][0] == "block2_grant_funded.grant_amount"

    def test_no_anchor_means_no_figure(self):
        context = drafter.letter_context(prospect(), analysis(anchors.NONE))
        assert context["figure"] is None
        assert context["pending"] == anchors.PENDING_HEADLINE

    def test_an_analysis_that_never_recorded_an_anchor_quotes_nothing(self):
        # Not the same as "no anchor". We cannot say what the figure rests on,
        # so we may not ask to have it corrected.
        stale = {"gate_map": {"approaches": [
            {"name": "x", "annual_return": [1000, 2000], "engagement": "diagnostic"}]}}
        assert drafter.letter_context(prospect(), stale)["figure"] is None

    def test_it_carries_the_dashboard_link(self):
        context = drafter.letter_context(prospect(), analysis(anchors.AWARD))
        assert context["dashboard"].endswith(
            __import__("lib.dashboard", fromlist=["x"]).token_for(prospect()))


GOOD = (
    "Your Manufacturing Readiness Grant record shows a $250,000 award in 2022. "
    "If your estimators spend somewhere near forty to sixty hours a month "
    "assembling quotes, that is about $41,000 to $88,000 a year of time going "
    "into paperwork rather than into jobs. I could not compute the one thing "
    "that decides it, which is how many quotes actually leave your desk in a "
    "month. Tell me that number and I will send the corrected version; if the "
    "range above is wrong, saying so is the most useful reply you could send. "
    "Everything we quote is a founding-client rate, locked 12 months."
)


class TestTheShapeRules:
    def test_a_good_letter_passes(self):
        context = drafter.letter_context(prospect(), analysis(anchors.HEADCOUNT))
        assert drafter.letter_failures(GOOD, context) == []

    def test_the_founding_client_line_is_required(self):
        context = drafter.letter_context(prospect(), analysis(anchors.HEADCOUNT))
        without = GOOD.replace(
            "Everything we quote is a founding-client rate, locked 12 months.", "")
        assert any("founding-client" in f
                   for f in drafter.letter_failures(without, context))

    def test_the_close_must_ask_to_be_corrected(self):
        context = drafter.letter_context(prospect(), analysis(anchors.HEADCOUNT))
        polite = (
            "Your grant record shows a $250,000 award. If quoting runs near forty "
            "hours a month that is about $41,000 to $88,000 a year. I would like "
            "to book twenty minutes to walk you through it in more detail than "
            "fits on a page like this one, at a time that suits you best. "
            "Everything we quote is a founding-client rate, locked 12 months. "
            "I look forward to speaking with you about it before the month is out."
        )
        assert any("asks to be corrected" in f
                   for f in drafter.letter_failures(polite, context))

    def test_a_letter_that_runs_past_a_page_is_refused(self):
        context = drafter.letter_context(prospect(), analysis(anchors.HEADCOUNT))
        long = GOOD + " " + ("A further sentence of padding. " * 120)
        assert any("one page holds" in f
                   for f in drafter.letter_failures(long, context))

    def test_an_unanchored_company_may_not_be_quoted_a_figure(self):
        """The refusal the whole anchoring order exists to make possible."""
        context = drafter.letter_context(prospect(), analysis(anchors.NONE))
        assert any("may not quote a figure" in f
                   for f in drafter.letter_failures(GOOD, context))

    def test_and_an_unanchored_letter_without_one_passes(self):
        context = drafter.letter_context(prospect(), analysis(anchors.NONE))
        asking = (
            "Your Manufacturing Readiness Grant record shows an award in 2022 "
            "against a purchase of production equipment. There is one number I "
            "could not find anywhere you publish, which is how many quotes leave "
            "your desk in a month, and it is the number that decides whether any "
            "of this is worth your time. Send me that one figure and I will send "
            "back the arithmetic; if I have the question itself wrong, tell me "
            "and that is just as useful. Everything we quote is a "
            "founding-client rate, locked 12 months."
        )
        assert drafter.letter_failures(asking, context) == []


class TestTheSignature:
    def test_it_names_us_and_says_which_law_governed(self):
        signed = drafter._append_signature("Body.", prospect())
        assert "Udaay Sikder" in signed
        assert "CAN-SPAM" in signed

    def test_a_canadian_letter_records_casl(self):
        signed = drafter._append_signature(
            "Body.", prospect(source_adapter="canada_gc"))
        assert "CASL" in signed

    def test_a_posted_letter_carries_no_unsubscribe_line(self):
        # There is no list to come off. Printing one would misdescribe what
        # this is.
        signed = drafter._append_signature("Body.", prospect())
        assert "STOP" not in signed
