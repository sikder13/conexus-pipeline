"""Tests for the Verifier console — the human gate.

The floor check carries most of the weight here. It is the only thing standing
between a machine-gathered guess and a sentence said to a real company, so every
one of its conditions is tested failing on its own, not just the happy path.

The block7 source-open rule is tested in both directions for the same reason:
a rule that can be satisfied by asking nicely is not a rule.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from lib.evidence import (
    BLOCK1_WHAT_THEY_MAKE,
    BLOCK5_CUSTOMER_FRICTION,
    BLOCK7_PEOPLE,
    FLAGS_KEY,
)
from tools.console import main as console

SOURCE = "https://accutechmold.test/about/"


def claim(value, tier=1, url=SOURCE, **extra):
    base = {
        "value": value, "tier": tier, "source_url": url,
        "date_checked": date.today().isoformat(), "verified": False, "verified_at": None,
    }
    base.update(extra)
    return base


def evidence(**overrides):
    ev = {
        BLOCK1_WHAT_THEY_MAKE: {
            "self_description": claim("We build injection molds in Muncie."),
            "products": claim("Molds, dies, fixtures."),
        },
        BLOCK7_PEOPLE: {
            "named_people": [claim("Dale Whitmore — President")],
            FLAGS_KEY: {"named_decision_maker": claim(True)},
        },
    }
    ev.update(overrides)
    return ev


def prospect(pid="p1", **overrides):
    row = {
        "id": pid,
        "company_name": "Accutech Mold & Machine",
        "county": "Delaware",
        "drive_minutes": 3,
        "signal_score": 4,
        "priority": "P1",
        "stage": "passA_done",
        "freshness_date": None,
        "machine_summary": "A job shop in Muncie.",
        "score_breakdown": {"case_study": 1, "too_big": 0},
        "evidence_file": evidence(),
        "research_minutes": None,
        "website": "https://accutechmold.test/",
    }
    row.update(overrides)
    return row


class FakeConsoleDB:
    """In-memory stand-in for lib.db, holding prospects and sessions."""

    def __init__(self, prospects=None):
        self.prospects = {p["id"]: dict(p) for p in (prospects or [prospect()])}
        self.sessions: dict[str, dict] = {}
        self._n = 0
        self.updates: list[tuple[str, dict]] = []

    def list_prospects_full(self):
        return [dict(p) for p in self.prospects.values()]

    def get_prospect(self, pid):
        row = self.prospects.get(pid)
        return dict(row) if row else None

    def update_prospect(self, pid, data):
        self.updates.append((pid, data))
        self.prospects[pid].update(data)
        return dict(self.prospects[pid])

    def open_session(self, pid):
        for s in self.sessions.values():
            if s["prospect_id"] == pid and s["completed_at"] is None:
                return dict(s)
        return None

    def start_session(self, pid):
        existing = self.open_session(pid)
        if existing:
            return existing
        self._n += 1
        s = {
            "id": f"s{self._n}", "prospect_id": pid,
            "started_at": datetime.now(UTC).isoformat(),
            "completed_at": None, "opened_sources": [], "gaps": None,
        }
        self.sessions[s["id"]] = s
        return dict(s)

    def update_session(self, sid, data):
        self.sessions[sid].update(data)
        return dict(self.sessions[sid])

    def open_sessions(self):
        return [dict(s) for s in self.sessions.values() if s["completed_at"] is None]

    # --- v2 screens read these; the fake returns whatever a test set on it ---
    def all_artifacts(self):
        return [dict(a) for a in getattr(self, "artifacts", [])]

    def artifacts_for(self, prospect_id):
        return [dict(a) for a in getattr(self, "artifacts", [])
                if a["prospect_id"] == prospect_id]

    def all_touches(self):
        return [dict(t) for t in getattr(self, "touches", [])]

    def touches_for(self, prospect_id):
        return [dict(t) for t in getattr(self, "touches", [])
                if t["prospect_id"] == prospect_id]

    def log_touch(self, data):
        row = {"id": f"t{len(getattr(self, 'touches', []))}", **data}
        self.touches = [*getattr(self, "touches", []), row]
        return row


@pytest.fixture
def fake(monkeypatch):
    db = FakeConsoleDB()
    db.artifacts, db.touches = [], []
    monkeypatch.setattr(console, "db", db)
    import lib.canary as canary_mod
    monkeypatch.setattr(canary_mod, "read_state", lambda: canary_mod.CanaryState())
    return db


@pytest.fixture
def client(fake):
    return TestClient(console.app)


# ------------------------------------------------------------------ the queue

class TestQueue:
    """The legacy verification queue, now at /verify rather than the root."""

    def test_a_ready_p1_appears(self, client):
        body = client.get("/verify").text
        assert "Accutech Mold &amp; Machine" in body
        assert "Verify" in body

    def test_a_p2_is_not_in_the_queue(self, fake, client):
        fake.prospects["p1"]["priority"] = "P2"
        assert "Accutech" not in client.get("/verify").text.split("Needs review")[0]

    def test_a_verified_prospect_is_not_re_queued(self, fake, client):
        fake.prospects["p1"]["stage"] = "verified"
        assert "Verify</a>" not in client.get("/verify").text

    def test_ordering_is_score_desc_then_drive_asc(self, monkeypatch):
        rows = [
            prospect("a", company_name="Alpha", signal_score=3, drive_minutes=10),
            prospect("b", company_name="Bravo", signal_score=5, drive_minutes=90),
            prospect("c", company_name="Charlie", signal_score=3, drive_minutes=5),
        ]
        db = FakeConsoleDB(rows)
        db.artifacts, db.touches = [], []
        monkeypatch.setattr(console, "db", db)
        body = TestClient(console.app).get("/verify").text
        assert body.index("Bravo") < body.index("Charlie") < body.index("Alpha")

    def test_a_failing_integrity_file_is_held_back_not_queued(self, fake, client):
        fake.prospects["p1"]["evidence_file"] = evidence(**{BLOCK1_WHAT_THEY_MAKE: {}})
        body = client.get("/verify").text
        assert "Held back by the integrity gate" in body
        assert "no substantive claim" in body

    def test_a_compromised_site_is_held_back(self, fake, client):
        fake.prospects["p1"]["website_status"] = "compromised"
        assert "compromised" in client.get("/verify").text

    def test_in_progress_shows_a_started_session(self, fake, client):
        fake.start_session("p1")
        body = client.get("/verify").text
        assert "Resume" in body

    def test_needs_review_shows_its_recorded_reason(self, fake, client):
        fake.prospects["p1"]["stage"] = "needs_review"
        fake.prospects["p1"]["needs_review_reason"] = "website confidence below 70"
        assert "website confidence below 70" in client.get("/verify").text

    def test_a_stale_freshness_date_is_badged(self, fake, client):
        fake.prospects["p1"]["freshness_date"] = (date.today() - timedelta(days=40)).isoformat()
        assert "stale" in client.get("/verify").text


# ----------------------------------------------------------------- dispositions

class TestDispositions:
    def test_approve_marks_the_claim_verified(self, fake, client):
        client.post("/verify/p1/approve",
                    data={"path": f"{BLOCK1_WHAT_THEY_MAKE}.products"}, follow_redirects=False)
        c = fake.prospects["p1"]["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["products"]
        assert c["verified"] is True and c["verified_at"]

    def test_edit_keeps_the_original_and_the_source(self, fake, client):
        client.post("/verify/p1/edit",
                    data={"path": f"{BLOCK1_WHAT_THEY_MAKE}.products", "value": "Molds only"},
                    follow_redirects=False)
        c = fake.prospects["p1"]["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["products"]
        assert c["value"] == "Molds only"
        assert c["original_value"] == "Molds, dies, fixtures."
        assert c["source_url"] == SOURCE, "an edit must not rewrite provenance"

    def test_kill_records_a_reason_and_does_not_delete(self, fake, client):
        client.post("/verify/p1/kill",
                    data={"path": f"{BLOCK1_WHAT_THEY_MAKE}.products", "reason": "out of date"},
                    follow_redirects=False)
        c = fake.prospects["p1"]["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["products"]
        assert c["killed"] is True
        assert c["killed_reason"] == "out of date"
        assert c["value"] == "Molds, dies, fixtures.", "killing must quarantine, not destroy"

    def test_ask_marks_a_discovery_question(self, fake, client):
        client.post("/verify/p1/ask",
                    data={"path": f"{BLOCK1_WHAT_THEY_MAKE}.products", "question": "still true?"},
                    follow_redirects=False)
        c = fake.prospects["p1"]["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["products"]
        assert c["discovery_question"] is True
        assert c["question"] == "still true?"

    def test_a_tainted_claim_cannot_be_disposed(self, fake, client):
        ev = fake.prospects["p1"]["evidence_file"]
        ev[BLOCK1_WHAT_THEY_MAKE]["products"]["tainted"] = True
        ev[BLOCK1_WHAT_THEY_MAKE]["products"]["taint_reason"] = "hijacked domain"
        r = client.post("/verify/p1/approve",
                        data={"path": f"{BLOCK1_WHAT_THEY_MAKE}.products"}, follow_redirects=False)
        assert r.status_code == 409
        assert "read-only" in r.json()["detail"]

    def test_a_disposition_opens_a_session(self, fake, client):
        assert fake.open_session("p1") is None
        client.post("/verify/p1/approve",
                    data={"path": f"{BLOCK1_WHAT_THEY_MAKE}.products"}, follow_redirects=False)
        assert fake.open_session("p1") is not None

    def test_rendering_the_verify_screen_writes_nothing(self, fake, client):
        client.get("/verify/p1")
        assert fake.open_session("p1") is None, "a GET must not start work"
        assert fake.updates == []


# ------------------------------------------------- the person source-open rule

class TestPersonClaimRequiresSourceClick:
    PATH = f"{BLOCK7_PEOPLE}.named_people[0]"

    def test_approve_is_refused_before_the_source_is_opened(self, client):
        r = client.post("/verify/p1/approve", data={"path": self.PATH}, follow_redirects=False)
        assert r.status_code == 409
        assert "open this person's source link" in r.json()["detail"].lower()

    def test_the_claim_is_untouched_after_refusal(self, fake, client):
        client.post("/verify/p1/approve", data={"path": self.PATH}, follow_redirects=False)
        person = fake.prospects["p1"]["evidence_file"][BLOCK7_PEOPLE]["named_people"][0]
        assert person["verified"] is False

    def test_approve_succeeds_once_the_source_has_been_opened(self, fake, client):
        client.post("/verify/p1/source-opened", data={"path": self.PATH})
        r = client.post("/verify/p1/approve", data={"path": self.PATH}, follow_redirects=False)
        assert r.status_code == 303
        person = fake.prospects["p1"]["evidence_file"][BLOCK7_PEOPLE]["named_people"][0]
        assert person["verified"] is True

    def test_opening_one_source_does_not_unlock_another_person(self, fake, client):
        fake.prospects["p1"]["evidence_file"][BLOCK7_PEOPLE]["named_people"].append(
            claim("Karen Ruiz — General Manager")
        )
        client.post("/verify/p1/source-opened", data={"path": self.PATH})
        r = client.post("/verify/p1/approve",
                        data={"path": f"{BLOCK7_PEOPLE}.named_people[1]"}, follow_redirects=False)
        assert r.status_code == 409

    def test_the_open_is_recorded_server_side(self, fake, client):
        client.post("/verify/p1/source-opened", data={"path": self.PATH})
        assert self.PATH in fake.open_session("p1")["opened_sources"]

    def test_a_non_person_claim_needs_no_click(self, client):
        r = client.post("/verify/p1/approve",
                        data={"path": f"{BLOCK1_WHAT_THEY_MAKE}.products"}, follow_redirects=False)
        assert r.status_code == 303


# ------------------------------------------------------------------- block 5

class TestCustomerFriction:
    def test_a_quote_is_stored_as_a_verified_t1_claim(self, fake, client):
        client.post("/verify/p1/block5",
                    data={"quote": "They never answered the phone.",
                          "source_url": "https://maps.google.test/r/1"}, follow_redirects=False)
        block = fake.prospects["p1"]["evidence_file"][BLOCK5_CUSTOMER_FRICTION]
        quote = block["review_quotes"][0]
        assert quote["value"] == "They never answered the phone."
        assert quote["tier"] == 1 and quote["verified"] is True

    def test_multiple_quotes_accumulate(self, fake, client):
        for n in (1, 2):
            client.post("/verify/p1/block5",
                        data={"quote": f"quote {n}", "source_url": f"https://x.test/{n}"},
                        follow_redirects=False)
        assert len(fake.prospects["p1"]["evidence_file"][BLOCK5_CUSTOMER_FRICTION]
                   ["review_quotes"]) == 2

    def test_none_found_still_records_that_the_check_ran(self, fake, client):
        client.post("/verify/p1/block5", data={"none_found": "1"}, follow_redirects=False)
        block = fake.prospects["p1"]["evidence_file"][BLOCK5_CUSTOMER_FRICTION]
        assert block["check_performed"]["value"].endswith("no friction quotes found")
        assert "review_quotes" not in block

    def test_performed_and_empty_differs_from_not_performed(self, fake, client):
        assert console._block5_performed(fake.prospects["p1"]["evidence_file"]) is False
        client.post("/verify/p1/block5", data={"none_found": "1"}, follow_redirects=False)
        assert console._block5_performed(fake.prospects["p1"]["evidence_file"]) is True

    def test_a_quote_without_a_source_is_refused(self, client):
        r = client.post("/verify/p1/block5", data={"quote": "bad service"},
                        follow_redirects=False)
        assert r.status_code == 400
        assert "source_url is required" in r.json()["detail"]


class TestFreshness:
    def test_completing_the_pass_sets_todays_date(self, fake, client):
        client.post("/verify/p1/freshness", data={"site_live": "1"}, follow_redirects=False)
        assert fake.prospects["p1"]["freshness_date"] == date.today().isoformat()

    def test_it_records_what_was_rechecked(self, fake, client):
        client.post("/verify/p1/freshness",
                    data={"site_live": "1", "recent_news": "1"}, follow_redirects=False)
        notes = fake.prospects["p1"]["evidence_file"]["notes"]
        assert any("news this month" in n["note"] for n in notes)

    def test_an_empty_pass_is_refused(self, client):
        r = client.post("/verify/p1/freshness", data={}, follow_redirects=False)
        assert r.status_code == 400


# --------------------------------------------------------------- floor check

def fully_disposed(fake, client, *, gaps=True, block5=True, person=True):
    """Drive a prospect to a state where every floor condition holds."""
    ev = fake.prospects["p1"]["evidence_file"]
    ev[BLOCK1_WHAT_THEY_MAKE]["extra1"] = claim("ISO 9001 certified")
    ev[BLOCK1_WHAT_THEY_MAKE]["extra2"] = claim("Founded 1994")
    for path in (f"{BLOCK1_WHAT_THEY_MAKE}.self_description",
                 f"{BLOCK1_WHAT_THEY_MAKE}.extra1",
                 f"{BLOCK1_WHAT_THEY_MAKE}.extra2",
                 f"{BLOCK7_PEOPLE}.{FLAGS_KEY}.named_decision_maker"):
        client.post("/verify/p1/approve", data={"path": path}, follow_redirects=False)
    client.post("/verify/p1/ask", data={"path": f"{BLOCK1_WHAT_THEY_MAKE}.products",
                                        "question": "still accurate?"}, follow_redirects=False)
    if person:
        client.post("/verify/p1/source-opened", data={"path": f"{BLOCK7_PEOPLE}.named_people[0]"})
        client.post("/verify/p1/approve", data={"path": f"{BLOCK7_PEOPLE}.named_people[0]"},
                    follow_redirects=False)
    else:
        client.post("/verify/p1/kill", data={"path": f"{BLOCK7_PEOPLE}.named_people[0]",
                                             "reason": "not confirmable"}, follow_redirects=False)
    if block5:
        client.post("/verify/p1/block5", data={"none_found": "1"}, follow_redirects=False)
    if gaps:
        client.post("/verify/p1/gaps", data={"gaps": "which line the grant funded"},
                    follow_redirects=False)
    return fake.prospects["p1"]


def failures_for(fake):
    return console.floor_check(fake.prospects["p1"], fake.open_session("p1"))["failures"]


class TestFloorCheckRejections:
    def test_pending_claims_block_it(self, fake, client):
        fully_disposed(fake, client)
        fake.prospects["p1"]["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["late"] = claim("new fact")
        assert any("no disposition" in f for f in failures_for(fake))

    def test_too_few_approved_t1_claims_block_it(self, fake, client):
        fully_disposed(fake, client)
        ev = fake.prospects["p1"]["evidence_file"]
        # Leave two approved T1 claims: one below the floor of three.
        for key in ("extra1", "extra2", "self_description"):
            ev[BLOCK1_WHAT_THEY_MAKE][key]["verified"] = False
            ev[BLOCK1_WHAT_THEY_MAKE][key]["discovery_question"] = True
        assert any("approved T1 claim" in f for f in failures_for(fake))

    def test_no_person_and_no_contact_path_blocks_it(self, fake, client):
        """CASE-1 §6 asks that the file be shippable, not that a name exists.

        A name was standing in for contactability. Where neither a name nor a
        way in exists there is genuinely nobody to address, and the floor still
        refuses.
        """
        fake.prospects["p1"]["contacts"] = None
        fake.prospects["p1"]["website"] = None
        fully_disposed(fake, client, person=False)
        assert any("nobody to address" in f for f in failures_for(fake))

    def test_a_contact_path_stands_in_for_a_name(self, fake, client):
        # Six Indiana companies scoring three or more were held at P2 as "worth
        # the research time it takes to find the human" while the human was
        # already reachable through the front desk.
        fake.prospects["p1"]["contacts"] = [{
            "kind": "email", "value": "info@accutechmold.test",
            "source_url": "https://accutechmold.test/contact",
            "email_class": "role_based"}]
        fully_disposed(fake, client, person=False)
        assert not any("nobody to address" in f for f in failures_for(fake))

    def test_no_recorded_gap_blocks_it(self, fake, client):
        fully_disposed(fake, client, gaps=False)
        ev = fake.prospects["p1"]["evidence_file"]
        q = ev[BLOCK1_WHAT_THEY_MAKE]["products"]
        q.pop("discovery_question", None)
        q["killed"] = True
        q["killed_reason"] = "stale"
        assert any("no recorded gap" in f for f in failures_for(fake))

    def test_a_missing_block5_check_blocks_it(self, fake, client):
        fully_disposed(fake, client, block5=False)
        assert any("block5" in f for f in failures_for(fake))

    def test_failing_integrity_blocks_it(self, fake, client):
        fully_disposed(fake, client)
        fake.prospects["p1"]["website_status"] = "compromised"
        assert any("evidence integrity" in f for f in failures_for(fake))

    def test_an_incoherent_summary_verdict_blocks_it(self, fake, client):
        fully_disposed(fake, client)
        fake.prospects["p1"]["evidence_file"]["summary_verdict"] = {
            "evidence_coherent": False, "issues": ["describes a gambling site"],
        }
        assert any("coherence" in f for f in failures_for(fake))

    def test_the_endpoint_refuses_and_changes_no_stage(self, fake, client):
        r = client.post("/verify/p1/mark-verified", follow_redirects=False)
        assert r.status_code == 422
        assert fake.prospects["p1"]["stage"] == "passA_done"

    def test_the_refusal_names_every_failure_at_once(self, client):
        body = client.post("/verify/p1/mark-verified", follow_redirects=False).text
        assert "no disposition" in body
        assert "block5" in body


class TestFloorCheckSuccess:
    def test_all_conditions_met_passes(self, fake, client):
        fully_disposed(fake, client)
        assert console.floor_check(fake.prospects["p1"], fake.open_session("p1"))["passing"]

    def test_marking_verified_sets_the_stage_and_freshness(self, fake, client):
        fully_disposed(fake, client)
        r = client.post("/verify/p1/mark-verified", follow_redirects=False)
        assert r.status_code == 303
        assert fake.prospects["p1"]["stage"] == "verified"
        assert fake.prospects["p1"]["freshness_date"] == date.today().isoformat()

    def test_the_session_is_completed_with_its_counts(self, fake, client):
        fully_disposed(fake, client)
        client.post("/verify/p1/mark-verified", follow_redirects=False)
        session = next(iter(fake.sessions.values()))
        assert session["completed_at"] is not None
        assert session["claims_approved"] >= 3
        assert session["claims_questioned"] == 1
        assert session["duration_seconds"] is not None

    def test_research_minutes_accumulate(self, fake, client):
        fake.prospects["p1"]["research_minutes"] = 12
        fully_disposed(fake, client)
        client.post("/verify/p1/mark-verified", follow_redirects=False)
        assert fake.prospects["p1"]["research_minutes"] >= 12

    def test_priority_is_recorded_as_human_set(self, fake, client):
        fully_disposed(fake, client)
        client.post("/verify/p1/mark-verified", follow_redirects=False)
        assert fake.prospects["p1"]["priority_set_by"] == "human"


class TestVerifiedStageExclusivity:
    def test_only_the_console_endpoint_writes_the_verified_stage(self, fake, client):
        fully_disposed(fake, client)
        client.post("/verify/p1/mark-verified", follow_redirects=False)
        writes = [d for _pid, d in fake.updates if d.get("stage") == "verified"]
        assert len(writes) == 1

    def test_no_other_console_endpoint_touches_stage(self, fake, client):
        client.post("/verify/p1/approve", data={"path": f"{BLOCK1_WHAT_THEY_MAKE}.products"},
                    follow_redirects=False)
        client.post("/verify/p1/block5", data={"none_found": "1"}, follow_redirects=False)
        client.post("/verify/p1/freshness", data={"site_live": "1"}, follow_redirects=False)
        assert not any("stage" in d for _pid, d in fake.updates)

    def test_the_runner_still_forbids_the_verified_stage(self):
        from lib.nodes import FORBIDDEN_STAGES, StageViolation, assert_stage_allowed
        assert "verified" in FORBIDDEN_STAGES
        with pytest.raises(StageViolation):
            assert_stage_allowed({"stage": "verified"})


# ------------------------------------------------------------- evidence view

class TestEvidenceView:
    def test_it_renders_claims_with_tiers_and_sources(self, client):
        body = client.get("/evidence/p1").text
        assert "injection molds" in body
        assert "T1" in body and SOURCE in body

    def test_a_tainted_claim_is_struck_and_explained(self, fake, client):
        c = fake.prospects["p1"]["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["products"]
        c["tainted"] = True
        c["taint_reason"] = "domain serves a gambling site"
        body = client.get("/evidence/p1").text
        assert "struck" in body and "gambling site" in body

    def test_a_killed_claim_is_struck(self, fake, client):
        c = fake.prospects["p1"]["evidence_file"][BLOCK1_WHAT_THEY_MAKE]["products"]
        c["killed"] = True
        c["killed_reason"] = "superseded"
        assert "superseded" in client.get("/evidence/p1").text

    def test_it_has_no_write_controls(self, client):
        assert "<form" not in client.get("/evidence/p1").text

    def test_a_missing_prospect_is_a_404(self, client):
        assert client.get("/evidence/nope").status_code == 404


class TestOfflineSafety:
    def test_no_page_loads_an_external_resource(self, fake, client):
        fake.start_session("p1")
        for url in ("/", "/verify", "/companies", "/company/p1", "/outreach",
                    "/verify/p1", "/evidence/p1"):
            body = client.get(url).text
            for marker in ("//cdn", "http://fonts", "https://fonts", "cdnjs", "unpkg",
                           "jsdelivr", "googleapis"):
                assert marker not in body, f"{url} reaches out to {marker}"

    def test_the_stylesheet_is_served_locally(self, client):
        assert client.get("/static/console.css").status_code == 200

    def test_the_stylesheet_imports_nothing(self):
        css = (console.HERE / "static" / "console.css").read_text()
        assert "@import" not in css and "url(http" not in css


class TestConsoleV2:
    """Every v2 screen must render, including on the ugly cases.

    A console that only survives a healthy record is a demo, not a tool — the
    records an operator most needs to read are the broken ones.
    """

    def test_the_dashboard_renders(self, client):
        body = client.get("/").text
        assert "companies loaded" in body and "Canary" in body

    def test_the_dashboard_explains_every_funnel_step(self, client):
        # Numbers without an explanation are how an operator learns to distrust
        # a screen.
        body = client.get("/").text
        assert "passed the evidence-integrity gate" in body
        assert "no send path exists yet" in body

    def test_the_company_browser_renders(self, client):
        assert "Accutech" in client.get("/companies").text

    def test_the_browser_filters_by_priority(self, fake, client):
        fake.prospects["p1"]["priority"] = "P3"
        assert "Accutech" not in client.get("/companies?priority=P1").text

    def test_the_browser_searches_by_name(self, client):
        assert "Accutech" in client.get("/companies?q=accu").text
        assert "Accutech" not in client.get("/companies?q=zzzz").text

    def test_the_company_file_renders_every_section(self, client):
        body = client.get("/company/p1").text
        for heading in ("Who they are", "The grant", "What we found", "The people",
                        "Scope of work", "Earlier analysis", "The outreach",
                        "The log"):
            assert heading in body, f"missing section: {heading}"

    def test_a_company_with_no_scope_of_work_says_so(self, client):
        # The scope of work supersedes the thesis, so its absence has to be
        # stated rather than leaving the page looking like the thesis is current.
        assert "No scope of work yet" in client.get("/company/p1").text

    def test_an_empty_section_says_why_rather_than_vanishing(self, client):
        body = client.get("/company/p1").text
        assert "no Conexus case study exists" in body or "No award figure" in body

    def test_a_compromised_company_explains_the_status(self, fake, client):
        fake.prospects["p1"]["website_status"] = "compromised"
        body = client.get("/company/p1").text
        assert "lapsed and was re-registered" in body

    def test_a_company_with_no_thesis_says_so(self, client):
        assert "No thesis yet" in client.get("/company/p1").text

    def test_a_blocked_artifact_shows_its_refusal(self, fake, client):
        fake.artifacts = [{
            "id": "a1", "prospect_id": "p1", "kind": "email", "status": "blocked",
            "body": "Some text.", "gate_failures": ["number with no source"],
            "gate_map": [{"sentence": "Some text.", "claims": []}],
            "claims_cited": [], "attempts": 2,
        }]
        body = client.get("/company/p1").text
        assert "Refused" in body and "number with no source" in body

    def test_the_gate_map_is_viewable(self, fake, client):
        fake.artifacts = [{
            "id": "a1", "prospect_id": "p1", "kind": "email", "status": "sendable",
            "body": "You make molds.", "gate_failures": [],
            "gate_map": [{"sentence": "You make molds.",
                          "claims": ["block1_what_they_make.self_description"]}],
            "claims_cited": ["block1_what_they_make.self_description"], "attempts": 1,
        }]
        body = client.get("/company/p1").text
        assert "Why we are allowed to say this" in body
        assert "block1_what_they_make.self_description" in body

    def scope_of_work(self, **overrides):
        row = {
            "id": "a9", "prospect_id": "p1", "kind": "analysis",
            "status": "sendable", "attempts": 1, "claims_cited": [],
            "gate_failures": [],
            "body": (
                "## The business\n\nThey build molds, which suggests volume "
                "matters.\n\n"
                "## Three approaches\n\n"
                "### 1. Quote assembler\n\nReads past jobs.\n\n"
                "2-4 weeks · $8,000-$20,000 · pays back in 1.6-10 months\n\n"
                "### Lead recommendation\n\nOpen with the quote assembler.\n\n"
                "## Where they stand\n\nThey sit mid-group."),
            "gate_map": {"thin": False, "approaches": [], "peer": {
                "basis": "11 companies in plastics and rubber",
                "caveat": "",
                "positions": [{
                    "label": "Front door",
                    "subject_value": "passes 4 of 6 checks on their own site",
                    "headline": "3 of the 11 we could measure are ahead of them",
                    "basis": "read from their site directly",
                    "comparable": True}]}},
        }
        row.update(overrides)
        return row

    def test_the_scope_of_work_renders_with_its_peer_table(self, fake, client):
        fake.artifacts = [self.scope_of_work()]
        body = client.get("/company/p1").text
        assert "Quote assembler" in body
        assert "pays back in 1.6-10 months" in body
        assert "Against the group" in body
        assert "3 of the 11 we could measure are ahead of them" in body

    def test_the_lead_recommendation_is_set_apart(self, fake, client):
        fake.artifacts = [self.scope_of_work()]
        assert "lead-recommendation" in client.get("/company/p1").text

    def test_a_thin_scope_of_work_says_why_it_is_thin(self, fake, client):
        fake.artifacts = [self.scope_of_work(
            gate_map={"thin": True, "approaches": [], "peer": {}})]
        assert "Thin evidence" in client.get("/company/p1").text

    def test_a_blocked_scope_of_work_warns_before_it_is_read(self, fake, client):
        fake.artifacts = [self.scope_of_work(
            status="blocked", gate_failures=["the figure '$30,000' has no source"])]
        body = client.get("/company/p1").text
        assert "did not pass its own checks" in body
        assert "$30,000" in body

    def test_the_person_gate_outcome_is_in_plain_words(self, client):
        body = client.get("/company/p1").text
        assert "Role-only" in body or "Cleared for outreach" in body

    def test_the_outreach_desk_renders(self, client):
        assert "Log a touch" in client.get("/outreach").text

    def test_the_outreach_desk_cannot_release_a_halt(self, fake, client, monkeypatch):
        import lib.canary as canary_mod
        monkeypatch.setattr(canary_mod, "read_state",
                            lambda: canary_mod.CanaryState(halted=True, halt_reason="a fact"))
        body = client.get("/outreach").text
        assert "HALTED" in body
        assert "cannot release the halt" in body
        assert "canary.resume" in body, "it must show the CLI command, not offer a button"

    def test_logging_a_touch_writes_one_row(self, fake, client):
        client.post("/outreach/touch", data={
            "prospect_id": "p1", "channel": "email", "response": "positive",
            "summary": "asked for a call",
        }, follow_redirects=False)
        assert len(fake.touches) == 1
        assert fake.touches[0]["response"] == "positive"

    def test_a_touch_needs_a_company(self, client):
        assert client.post("/outreach/touch", data={"channel": "email"},
                           follow_redirects=False).status_code == 400

    def test_every_write_route_is_one_somebody_decided_to_add(self):
        # Enforced by route inventory. Each entry below is a deliberate write
        # and the list is the record of that decision; a route that appears
        # without one shows up here as a failure.
        #
        #   /outreach/touch                  the calibration loop
        #   /verify/...                      the legacy per-claim flow
        #   /company/{id}/evidence           operator evidence entry, added
        #                                    2026-09-10 so a human can close the
        #                                    two gaps nothing automated can: a
        #                                    P1 with no named officer, and a
        #                                    company with no size on file
        allowed = {"/outreach/touch", "/company/{prospect_id}/evidence"}
        posts = {
            r.path for r in console.app.routes
            if getattr(r, "methods", None) and "POST" in r.methods
        }
        stray = {p for p in posts
                 if p not in allowed and not p.startswith("/verify/")}
        assert not stray, f"unexpected write routes: {stray}"


# ------------------------------------------------------- operator evidence entry

REGISTRY = "https://bsd.sos.in.gov/publicbusinesssearch/accutech-mold-machine"


class TestOperatorEvidenceEntry:
    """The panel that lets a human close what nothing automated can.

    The refusals matter more than the happy path. An operator typing a real
    person off the wrong company's page is the same fabrication a crawler makes,
    and it reaches a prospect the same way.
    """

    def _post(self, client, **fields):
        payload = {"kind": "person", "value": "Dana Whitmore — President",
                   "source_url": REGISTRY, "tier": "1",
                   "source_line": "ACCUTECH MOLD & MACHINE INC — President: "
                                  "Dana Whitmore"}
        payload.update(fields)
        return client.post("/company/p1/evidence", data=payload,
                           follow_redirects=False)

    def test_a_person_is_recorded_verified_at_entry(self, fake, client):
        assert self._post(client).status_code == 303
        people = fake.prospects["p1"]["evidence_file"][BLOCK7_PEOPLE]["named_people"]
        added = people[-1]
        assert added["value"] == "Dana Whitmore — President"
        assert added["verified"] is True and added["verified_at"]
        assert added["origin"] == "operator"

    def test_it_sets_the_flag_the_audit_reads(self, fake, client):
        fake.prospects["p1"]["evidence_file"][BLOCK7_PEOPLE] = {}
        self._post(client)
        block7 = fake.prospects["p1"]["evidence_file"][BLOCK7_PEOPLE]
        assert block7[FLAGS_KEY]["named_decision_maker"]["value"] is True

    def test_the_subject_guard_still_runs(self, client):
        # A real person with a real title on somebody else's page.
        response = self._post(
            client, source_url="https://a16z.com/team/",
            source_line="Future Fields Biomanufacturing — leadership")
        assert response.status_code == 422
        assert "does not look like it is about" in response.text

    def test_a_registry_url_needs_the_line_that_names_them(self, client):
        # bsd.sos.in.gov carries no company name, so the URL alone can never
        # satisfy the guard — which is why the panel asks for the line.
        response = self._post(client, source_line="")
        assert response.status_code == 422
        assert "paste the line you read it on" in response.text

    def test_a_name_that_is_not_a_person_is_refused(self, client):
        response = self._post(client, value="Our Team — President")
        assert response.status_code == 422

    def test_a_role_that_is_not_a_title_is_refused(self, client):
        response = self._post(client, value="Dana Whitmore — Solidworks")
        assert response.status_code == 422

    def test_a_source_that_is_not_a_url_is_refused(self, client):
        response = self._post(client, source_url="the INBiz search")
        assert response.status_code == 422

    def test_every_problem_is_reported_at_once(self, client):
        response = self._post(client, value="Our Team — Solidworks",
                              source_url="not a url")
        assert response.status_code == 422
        assert len(response.json()["failures"]) >= 3

    def test_a_duplicate_is_refused_rather_than_appended(self, client):
        self._post(client)
        again = self._post(client)
        assert again.status_code == 422
        assert "already holds" in again.text

    def test_a_headcount_lands_where_size_is_read_from(self, fake, client):
        response = client.post("/company/p1/evidence", data={
            "kind": "headcount", "value": "about 62 employees across two shifts",
            "source_url": "https://accutechmold.test/about/", "tier": "1",
        }, follow_redirects=False)
        assert response.status_code == 303
        block8 = fake.prospects["p1"]["evidence_file"]["block8_financial_scale"]
        assert "62" in str(block8["employee_count"]["value"])

    def test_an_aggregator_figure_keeps_its_tier_and_stays_unassertable(
        self, fake, client
    ):
        from lib.claims import is_assertable

        client.post("/company/p1/evidence", data={
            "kind": "headcount", "value": "50-100 employees",
            "source_url": "https://www.zoominfo.com/c/accutech/1", "tier": "3",
        }, follow_redirects=False)
        block8 = fake.prospects["p1"]["evidence_file"]["block8_financial_scale"]
        # Verified, because a person read it. Still Tier 3, so still unsayable.
        assert block8["employee_count"]["verified"] is True
        assert not is_assertable(block8["employee_count"])

    def test_the_panel_is_on_the_company_file(self, client):
        body = client.get("/company/p1").text
        assert "Add evidence" in body
        assert 'action="/company/p1/evidence"' in body


class TestOperatorEntryReachesTheGates:
    def test_an_operator_person_passes_the_person_gate(self):
        from lib import persongate
        from lib.claims import Tier, operator_claim

        entered = operator_claim("Dana Whitmore — President", Tier.T1, REGISTRY)
        result = persongate.check_person(entered, "Accutech Mold & Machine")
        assert result.allowed, result.reasons

    def test_an_operator_fact_counts_toward_the_drafting_floor(self):
        from lib.claims import Tier, operator_claim
        from tools.drafter import main as drafter

        row = {"company_name": "Accutech", "evidence_file": {BLOCK1_WHAT_THEY_MAKE: {
            f"fact{n}": operator_claim(f"fact {n}", Tier.T1, SOURCE)
            for n in range(3)}}}
        assert len(drafter.assertable_claims(row, ("verbatim",))) == 3
        assert drafter.below_floor(row, ("verbatim",)) is None

    def test_a_tier_three_operator_fact_does_not(self):
        from lib.claims import Tier, operator_claim
        from tools.drafter import main as drafter

        row = {"company_name": "Accutech", "evidence_file": {BLOCK1_WHAT_THEY_MAKE: {
            f"fact{n}": operator_claim(f"fact {n}", Tier.T3, SOURCE)
            for n in range(3)}}}
        assert drafter.assertable_claims(row, ("verbatim",)) == []
