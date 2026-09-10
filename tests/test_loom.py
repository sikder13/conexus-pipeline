"""The ninety-second script: which beats it is built from, and what it refuses.

The generator narrates figures the engine already computed, so the checks here
are about the shape of the recording rather than about the prose — and about the
one refusal that matters, which is a dollar figure said out loud, on a
recording, for a company that has nothing to anchor it to.
"""

from __future__ import annotations

from lib import anchors
from tools.loom import main as loom

BODY = """## The business

They make things.

## Findings

1. The front door is measurably weak: no contact form exists on the main site
   [block4_digital_front_door.flags.weak_front_door], which is costly for a
   company selling to worried buyers.

2. A second finding that should not be picked up.

## Lead recommendation

Something else.
"""


def prospect(**kw):
    row = {"id": "p1", "company_name": "Acme Fabrication",
           "source_adapter": "conexus_iedc"}
    row.update(kw)
    return row


def analysis(kind=anchors.HEADCOUNT, sensitivities=("for payback within 12 months, "
                                                    "minutes per quote must be at "
                                                    "least 62 minutes",)):
    return {
        "body": BODY,
        "gate_map": {
            "approaches": [{"name": "Quote assembler", "annual_return": [41000, 88000],
                            "reading": "target"}],
            "scarcity": ["Nobody in the group of six publishes a price."],
            "case": {"anchor": {"kind": kind, "detail": "anchored on 62 people"},
                     "sensitivities": list(sensitivities)},
        },
    }


class TestTheBeats:
    def test_the_finding_is_the_first_one_with_our_notation_stripped(self):
        beats = loom.beats_for(prospect(), analysis())
        assert beats.finding.startswith("The front door is measurably weak")
        assert "[block4" not in beats.finding
        assert "second finding" not in beats.finding

    def test_the_hook_comes_from_the_scarcity_line(self):
        assert "Nobody in the group of six" in loom.beats_for(
            prospect(), analysis()).hook

    def test_the_slider_is_the_sensitivity_sentence(self):
        assert "62 minutes" in loom.beats_for(prospect(), analysis()).slider

    def test_the_figure_is_the_one_the_analysis_computed(self):
        assert loom.beats_for(prospect(), analysis()).figure == "$41,000-$88,000 USD a year"

    def test_an_unanchored_company_has_no_figure_on_screen(self):
        beats = loom.beats_for(prospect(), analysis(kind=anchors.NONE))
        assert beats.figure == ""
        assert beats.usable          # there is still a slider to move

    def test_no_sensitivity_means_no_recording(self):
        beats = loom.beats_for(prospect(), analysis(sensitivities=()))
        assert not beats.usable

    def test_a_canadian_company_is_quoted_in_its_own_currency(self):
        beats = loom.beats_for(prospect(source_adapter="canada_gc"), analysis())
        assert beats.currency == "CAD"


class TestTheRefusals:
    def test_a_script_that_runs_long_is_refused(self):
        assert loom.too_long("word " * (loom.WORD_CEILING + 20))

    def test_the_beat_prefixes_do_not_count_toward_the_length(self):
        text = "[HOOK · 10s] " + "word " * (loom.WORD_CEILING - 5)
        assert loom.too_long(text) == []

    def test_a_missing_beat_is_named(self):
        text = "[HOOK · 10s] a\n[FINDING · 20s] b\n[SLIDER · 30s] c"
        failures = loom.missing_beats(text)
        assert failures and "ASK" in failures[0]

    def test_a_figure_for_an_unanchored_company_is_refused(self):
        beats = loom.beats_for(prospect(), analysis(kind=anchors.NONE))
        said = "[ASK · 15s] It is worth about $40,000 a year to you."
        assert loom.stray_money(said, beats)

    def test_and_the_same_words_are_fine_when_there_is_an_anchor(self):
        beats = loom.beats_for(prospect(), analysis())
        said = "[ASK · 15s] It is worth about $40,000 a year to you."
        assert loom.stray_money(said, beats) == []

    def test_spelled_out_money_is_caught_too(self):
        beats = loom.beats_for(prospect(), analysis(kind=anchors.NONE))
        assert loom.stray_money("about forty thousand dollars a year", beats)


class TestTheHeader:
    def test_it_tells_the_operator_which_tab_to_open(self):
        header = loom.header(loom.beats_for(prospect(), analysis()))
        assert "Open this tab before recording:" in header
        assert "Acme Fabrication" in header

    def test_an_unanchored_recording_says_what_it_is_asking_for(self):
        header = loom.header(loom.beats_for(prospect(), analysis(kind=anchors.NONE)))
        assert "asks for the missing number" in header
