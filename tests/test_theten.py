"""Tests for THE TEN — who is actually ready to be contacted.

The list is a filter over the pipeline's own ranking, not a second ranking, and
nothing about it loosens when it comes up short. So these tests are mostly about
the four requirements holding, and about the near misses coming back with
reasons an operator can act on.
"""

from __future__ import annotations

from lib import theten
from lib.claims import make_claim
from lib.evidence import BLOCK4_DIGITAL_FRONT_DOOR

SITE = "https://acmetool.test/"


def prospect(pid="p1", name="Acme Tool", score=4, drive=20, **overrides):
    row = {
        "id": pid, "company_name": name, "priority": "P1",
        "signal_score": score, "drive_minutes": drive, "website": SITE,
        "contacts": [{"kind": "email", "value": "sales@acmetool.test",
                      "email_class": "role_based", "source_url": SITE, "tier": 1}],
        "evidence_file": {BLOCK4_DIGITAL_FRONT_DOOR: {
            "phone_present": make_claim(False, 1, SITE)}},
    }
    row.update(overrides)
    return row


def artifact(kind, status="sendable", created="2026-09-08T10:00:00", **extra):
    row = {"id": f"{kind}-{created}", "prospect_id": "p1", "kind": kind,
           "status": status, "body": f"the {kind}", "created_at": created,
           "gate_map": {}, "gate_failures": []}
    row.update(extra)
    return row


def full_set(**overrides):
    rows = [artifact("analysis", gate_map={"thin": False, "approaches": [
                {"number": 1, "name": "Machine data report",
                 "annual_return": [8000, 21000]}]}),
            artifact("email"), artifact("linkedin")]
    rows = overrides.get("artifacts", rows)
    return {"p1": rows}


class TestWhatReadyMeans:
    def candidates(self, artifacts, row=None):
        return theten.build([row or prospect()], {"p1": artifacts})

    def test_a_company_with_all_four_qualifies(self):
        candidate = self.candidates(full_set()["p1"])[0]
        assert candidate.qualifies
        assert candidate.missing() == []

    def test_one_open_channel_is_enough(self):
        """A requirement a company is barred from meeting is not a standard.

        CASL forbids an email to a company that published no address, so
        requiring one made 41 of 43 ranked Canadian companies permanently
        unready while we held sendable letters and LinkedIn pairs for them.
        """
        rows = [a for a in full_set()["p1"] if a["kind"] != "email"]
        rows.append(artifact("email", status="blocked"))
        candidate = self.candidates(rows)[0]
        assert candidate.qualifies, candidate.missing()
        assert candidate.open_channels == ["linkedin"]

    def test_a_letter_alone_is_an_open_channel(self):
        rows = [a for a in full_set()["p1"]
                if a["kind"] not in ("email", "linkedin")]
        rows.append(artifact("letter", status="sendable"))
        candidate = self.candidates(rows)[0]
        assert candidate.qualifies, candidate.missing()
        assert candidate.open_channels == ["letter"]

    def test_no_channel_at_all_is_the_shortfall(self):
        rows = [a for a in full_set()["p1"]
                if a["kind"] not in ("email", "linkedin", "letter")]
        rows += [artifact("email", status="blocked"),
                 artifact("linkedin", status="blocked")]
        missing = self.candidates(rows)[0].missing()
        assert "nothing written that passed the gate on any open channel" in missing

    def test_a_blocked_channel_is_never_an_open_one(self):
        rows = [a for a in full_set()["p1"] if a["kind"] != "linkedin"]
        rows.append(artifact("linkedin", status="blocked"))
        assert "linkedin" not in self.candidates(rows)[0].open_channels

    def test_a_thin_analysis_is_not_a_scope_of_work(self):
        # Below the evidence floor there are no costed findings and no priced
        # approaches, so there is nothing to open a conversation with.
        rows = [a for a in full_set()["p1"] if a["kind"] != "analysis"]
        rows.append(artifact("analysis", gate_map={"thin": True, "approaches": []}))
        assert "no full scope-of-work analysis" in self.candidates(rows)[0].missing()

    def test_a_company_with_nothing_published_lacks_a_way_in(self):
        row = prospect(contacts=[])
        row["evidence_file"] = {}
        assert "no contact path an operator can act on" in \
            self.candidates(full_set()["p1"], row)[0].missing()

    def test_a_search_link_is_not_a_way_in(self):
        # It is a link the operator opens to begin looking. Counting it would
        # let a company that published nothing qualify on a URL we built.
        row = prospect(contacts=[{
            "kind": "linkedin_search", "value": "https://www.linkedin.com/search/x",
            "name": "Dale", "source_url": SITE, "tier": 4}])
        row["evidence_file"] = {}
        assert "no contact path an operator can act on" in \
            self.candidates(full_set()["p1"], row)[0].missing()


class TestTheNewestArtifactIsTheOneThatCounts:
    def test_a_newer_refusal_overrides_an_older_pass(self):
        rows = [a for a in full_set()["p1"]
                if a["kind"] not in ("email", "linkedin", "letter")]
        rows += [artifact("email", status="sendable", created="2026-09-01T10:00:00"),
                 artifact("email", status="blocked", created="2026-09-08T10:00:00")]
        candidate = theten.build([prospect()], {"p1": rows})[0]
        assert candidate.open_channels == []
        assert "nothing written that passed the gate on any open channel" in (
            candidate.missing())

    def test_a_superseded_row_is_not_the_newest(self):
        # Superseded means "not current", so it must not outrank the live row.
        rows = [a for a in full_set()["p1"] if a["kind"] != "email"]
        rows += [artifact("email", status="sendable", created="2026-09-01T10:00:00"),
                 artifact("email", status="superseded", created="2026-09-08T10:00:00")]
        assert theten.build([prospect()], {"p1": rows})[0].qualifies


class TestRankingAndReasons:
    def rows(self):
        return [prospect("p1", "High Score", score=5, drive=90),
                prospect("p2", "Close By", score=3, drive=10),
                prospect("p3", "Far Away", score=3, drive=200)]

    def test_score_leads_and_drive_time_breaks_the_tie(self):
        names = [c.name for c in theten.build(self.rows(), {})]
        assert names == ["High Score", "Close By", "Far Away"]

    def test_qualifying_is_a_filter_and_not_a_second_ranking(self):
        # A company does not climb by being reachable; the ones above it drop
        # off by not being. Close By ends up first here because High Score is
        # gone, not because being ready promoted it.
        artifacts = {"p1": [], "p2": full_set()["p1"], "p3": []}
        for row in artifacts["p2"]:
            row["prospect_id"] = "p2"
        candidates = theten.build(self.rows(), artifacts)
        assert [c.name for c in candidates] == ["High Score", "Close By", "Far Away"]
        assert [c.name for c in theten.the_ten(candidates)] == ["Close By"]

    def test_near_misses_come_back_with_what_they_lack(self):
        candidates = theten.build(self.rows(), {})
        misses = theten.near_misses(candidates)
        assert len(misses) == 3
        assert all(m.missing() for m in misses)

    def test_the_reasons_are_countable_because_the_wording_is_shared(self):
        counts = theten.reason_counts(theten.build(self.rows(), {}))
        assert counts["nothing written that passed the gate on any open channel"] == 3
        assert set(counts) == {words for _key, words in theten.REQUIREMENTS}

    def test_the_list_is_capped_but_never_padded(self):
        candidates = theten.build(self.rows(), {})
        assert theten.the_ten(candidates, target=10) == []


class TestWhatTheSummaryRowShows:
    def test_a_named_person_beats_a_rota_as_the_way_in(self):
        row = prospect(contacts=[
            {"kind": "email", "value": "sales@acmetool.test",
             "email_class": "role_based", "source_url": SITE, "tier": 1},
            {"kind": "email", "value": "dale.whitmore@acmetool.test",
             "email_class": "named_person", "source_url": SITE, "tier": 1}])
        candidate = theten.build([row], full_set())[0]
        assert candidate.best_path.detail == "dale.whitmore@acmetool.test"

    def test_the_return_prints_as_a_band(self):
        candidate = theten.build([prospect()], full_set())[0]
        assert theten.roi_words(candidate) == "$8,000-$21,000/yr"

    def test_a_company_with_no_costed_offer_says_so(self):
        rows = [a for a in full_set()["p1"] if a["kind"] != "analysis"]
        rows.append(artifact("analysis", gate_map={"thin": False, "approaches": []}))
        assert theten.roi_words(theten.build([prospect()], {"p1": rows})[0]) == "—"
