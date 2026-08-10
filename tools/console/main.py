"""Verifier console — the human gate, and the only door to stage='verified'.

Run it from the repo root:

    python -m tools.console            # http://127.0.0.1:8000

Everything upstream of this screen is a machine's opinion. Nothing the pipeline
gathered may be said to a prospect until a person has looked at the source and
agreed. That is the whole purpose of the tool, and it is why the floor check
below refuses rather than warns.

LOCAL ONLY. It binds 127.0.0.1, carries no authentication, and uses the service
role key from .env exactly as the other tools do. It is not deployed and must
not be — there is no user model here because there is one user.

WHY GET IS READ-ONLY

A verification session is created lazily, by the first POST that changes
something, not by rendering the verify screen. Opening a file to look at it is
not starting work on it, and a GET that writes would fill the IN PROGRESS list
with files nobody touched — which would then hide the ones somebody did. Any
real action (opening a source, disposing a claim, recording a gap) opens the
session, so a half-done file is still never lost.

NO EXTERNAL RESOURCES

Every byte the browser loads is served from this process: one stylesheet, one
inline script, no fonts, no CDN, no build step. The console works with the
network cable out, which matters because it is also shown to prospects.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from lib import canary, db, persongate
from lib.claims import Tier, make_claim, mark_verified
from lib.evidence import BLOCK5_CUSTOMER_FRICTION, BLOCK7_PEOPLE, BLOCKS, FLAGS_KEY
from lib.integrity import (
    STALE_AFTER_DAYS,
    evidence_integrity,
    is_killed,
    is_stale,
    is_tainted,
    iter_all_claims,
)

HERE = Path(__file__).parent
HOST = "127.0.0.1"
PORT = 8000

MIN_APPROVED_T1 = 3
"""The floor check's minimum. Below three verified first-party facts there is
not enough to write an opening line that a prospect would recognise as true."""

BLOCK_TITLES = {
    "block1_what_they_make": "1 · What they make",
    "block2_grant_funded": "2 · Grant-funded work",
    "block3_hiring_signals": "3 · Hiring signals",
    "block4_digital_front_door": "4 · Digital front door",
    "block5_customer_friction": "5 · Customer friction",
    "block6_tech_stack": "6 · Tech stack",
    "block7_people": "7 · People",
    "block8_financial_scale": "8 · Financial scale",
}

BLOCK_EXPLAIN = {
    "block1_what_they_make": "What the company says it makes, in its own words, "
                             "taken from its website and the grant listing.",
    "block2_grant_funded": "What the Manufacturing Readiness Grant paid for, from "
                           "the Conexus case study and the round announcements.",
    "block3_hiring_signals": "Open roles on their own careers page. A clerical "
                             "opening is a request for hours back.",
    "block4_digital_front_door": "How their website behaves for a buyer trying to "
                                 "reach them — measured, not judged.",
    "block5_customer_friction": "Verbatim customer complaints, entered by hand. "
                                "Never paraphrased.",
    "block6_tech_stack": "Software and platforms detected on their site.",
    "block7_people": "Named people with stated roles, and whether each name has "
                     "cleared the gate for use in outreach.",
    "block8_financial_scale": "Size signals: headcount, grant money, anything that "
                              "bounds how big they are.",
    "block9_discovery": "Questions raised where two sources disagreed. We record "
                        "both figures and ask rather than pick one.",
}

BLOCK_EMPTY = {
    "block1_what_they_make": "their website could not be read, so nothing records "
                             "what they make",
    "block2_grant_funded": "no Conexus case study exists for this company, and no "
                           "round announcement named them",
    "block3_hiring_signals": "no careers page was found, or it listed no roles. "
                             "External job boards disallow crawling, so an empty "
                             "page here does not mean they are not hiring",
    "block4_digital_front_door": "their site was not readable when we looked",
    "block5_customer_friction": "the manual reviews check has not been run for this "
                                "company yet",
    "block6_tech_stack": "no recognisable platform was detected",
    "block7_people": "no name with a stated role was found on their site or in the "
                     "case study",
    "block8_financial_scale": "no headcount or grant figure was found",
    "block9_discovery": "no two sources disagreed — there is nothing to ask about",
}

SOURCE_NOTE = (
    "Everything below was gathered from public sources: the company's own website, "
    "the Conexus Indiana grant listing and case studies, and press coverage of the "
    "grant rounds. Nothing was bought, and nothing behind a login was read."
)

TIER_LABEL = {
    1: "T1 own words / government record",
    2: "T2 press — attribute the publication",
    3: "T3 aggregator — never assertable",
    4: "T4 our inference — a hypothesis",
}

app = FastAPI(title="Conexus Verifier console", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=str(HERE / "templates"))


# ---------------------------------------------------------------- claim paths

def claim_at(evidence: dict[str, Any], path: str) -> dict[str, Any] | None:
    """Resolve a dotted claim path such as 'block7_people.named_people[0]'."""
    node: Any = evidence
    for part in _split_path(path):
        if isinstance(part, int):
            if not isinstance(node, list) or part >= len(node):
                return None
            node = node[part]
        else:
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
    return node if isinstance(node, dict) and "value" in node else None


def set_claim_at(evidence: dict[str, Any], path: str, claim: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``evidence`` with the claim at ``path`` replaced."""
    parts = _split_path(path)

    def descend(node: Any, remaining: list) -> Any:
        head, rest = remaining[0], remaining[1:]
        if isinstance(head, int):
            copied = list(node)
            copied[head] = claim if not rest else descend(copied[head], rest)
            return copied
        copied = dict(node)
        copied[head] = claim if not rest else descend(copied.get(head), rest)
        return copied

    return descend(evidence, parts)


def _split_path(path: str) -> list:
    parts: list = []
    for chunk in path.split("."):
        match = re.fullmatch(r"([^\[\]]+)((?:\[\d+\])*)", chunk)
        if not match:
            raise HTTPException(400, f"unreadable claim path: {path!r}")
        parts.append(match.group(1))
        parts.extend(int(n) for n in re.findall(r"\[(\d+)\]", match.group(2)))
    return parts


def is_person_claim(path: str) -> bool:
    """True for a block7 claim that names a human.

    These carry the source-open rule. A wrong number in an email is an
    embarrassment; a wrong person's name in the greeting ends the conversation
    and deserves the extra friction.
    """
    return path.startswith(f"{BLOCK7_PEOPLE}.") and "named_people" in path


def disposition_of(claim: dict[str, Any]) -> str | None:
    """What a human already decided about this claim, if anything."""
    if is_tainted(claim):
        return "tainted"
    if is_killed(claim):
        return "killed"
    if claim.get("discovery_question") is True:
        return "questioned"
    if claim.get("verified") is True:
        return "edited" if "original_value" in claim else "approved"
    return None


def worklist(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Every claim in block order, with everything the screen needs to show it."""
    rows: list[dict[str, Any]] = []
    order = {block: index for index, block in enumerate(BLOCKS)}
    for path, claim in iter_all_claims(evidence):
        trimmed = path.removeprefix("evidence_file.")
        block = trimmed.split(".")[0]
        if block not in order:
            continue
        rows.append({
            "path": trimmed,
            "block": block,
            "block_title": BLOCK_TITLES.get(block, block),
            "label": _label_for(trimmed),
            "claim": claim,
            "tier": claim.get("tier"),
            "tier_label": TIER_LABEL.get(claim.get("tier"), "unknown tier"),
            "disposition": disposition_of(claim),
            "is_person": is_person_claim(trimmed),
            "tainted": is_tainted(claim),
            "killed": is_killed(claim),
        })
    rows.sort(key=lambda r: (order.get(r["block"], 99), r["path"]))
    return rows


def _label_for(path: str) -> str:
    tail = path.split(".", 1)[1] if "." in path else path
    tail = tail.replace(f"{FLAGS_KEY}.", "flag: ")
    return tail.replace("_", " ")


# ------------------------------------------------------------------- sessions

def _require_session(prospect_id: str) -> dict[str, Any]:
    """The open session for this prospect, creating one on first real action."""
    return db.start_session(prospect_id)


def _opened_sources(session: dict[str, Any] | None) -> set[str]:
    raw = (session or {}).get("opened_sources") or []
    return set(raw) if isinstance(raw, list) else set()


def _prospect_or_404(prospect_id: str) -> dict[str, Any]:
    prospect = db.get_prospect(prospect_id)
    if not prospect:
        raise HTTPException(404, f"no prospect {prospect_id}")
    return prospect


def _named_person(prospect: dict[str, Any]) -> str:
    block7 = (prospect.get("evidence_file") or {}).get(BLOCK7_PEOPLE) or {}
    people = [p for p in (block7.get("named_people") or []) if not is_tainted(p)]
    if not people:
        return "—"
    return str(people[0].get("value") or "—")


# --------------------------------------------------------------------- queue

@app.get("/verify", response_class=HTMLResponse)
def queue(request: Request):
    """The legacy per-claim verification queue.

    Kept reachable but off the main navigation. Verification used to be the
    spine of this tool; the automated gate layers superseded it, and a screen
    that is optional should not look like the way in.
    """
    prospects = db.list_prospects_full()
    sessions = {s["prospect_id"]: s for s in db.open_sessions()}

    ready, blocked = [], []
    for p in prospects:
        if p.get("stage") != "passA_done" or p.get("priority") != "P1":
            continue
        if p["id"] in sessions:
            continue
        report = evidence_integrity(p)
        row = {
            "p": p,
            "person": _named_person(p),
            "stale": is_stale(p.get("freshness_date")),
            "report": report,
        }
        (ready if report.passing else blocked).append(row)

    ready.sort(key=lambda r: (-(r["p"].get("signal_score") or 0),
                              r["p"].get("drive_minutes") or 9999))

    in_progress = []
    for pid, session in sessions.items():
        p = next((x for x in prospects if x["id"] == pid), None)
        if p:
            in_progress.append({"p": p, "session": session,
                                "started": session.get("started_at", "")[:16].replace("T", " ")})

    review = [p for p in prospects if p.get("stage") == "needs_review"]
    review.sort(key=lambda p: ({"P1": 0, "P2": 1, "P3": 2}.get(p.get("priority"), 3),
                               -(p.get("signal_score") or 0)))

    return templates.TemplateResponse(request, "queue.html", {
        "ready": ready,
        "integrity_blocked": blocked,
        "in_progress": in_progress,
        "review": review,
        "verified_total": sum(1 for p in prospects if p.get("stage") == "verified"),
        "review_total": len(review),
        "stale_days": STALE_AFTER_DAYS,
    })


# -------------------------------------------------------------------- verify

@app.get("/verify/{prospect_id}", response_class=HTMLResponse)
def verify(request: Request, prospect_id: str):
    """The claim worklist. Renders only — the session opens on the first action."""
    prospect = _prospect_or_404(prospect_id)
    evidence = prospect.get("evidence_file") or {}
    session = db.open_session(prospect_id)
    rows = worklist(evidence)
    opened = _opened_sources(session)
    for row in rows:
        row["source_opened"] = row["path"] in opened
        row["approve_blocked"] = row["is_person"] and not row["source_opened"]

    verdict = (evidence.get("summary_verdict") or {}) if isinstance(evidence, dict) else {}
    return templates.TemplateResponse(request, "verify.html", {
        "p": prospect,
        "rows": rows,
        "pending": [r for r in rows if r["disposition"] is None],
        "session": session,
        "report": evidence_integrity(prospect),
        "coherence_issues": verdict.get("issues") or [],
        "coherent": verdict.get("evidence_coherent"),
        "stale": is_stale(prospect.get("freshness_date")),
        "block5_done": _block5_performed(evidence),
        "reviews_url": _reviews_url(prospect),
        "floor": floor_check(prospect, session),
        "stale_days": STALE_AFTER_DAYS,
    })


@app.get("/evidence/{prospect_id}", response_class=HTMLResponse)
def evidence_view(request: Request, prospect_id: str):
    """Read-only rendering of the whole file. No write actions anywhere on it."""
    prospect = _prospect_or_404(prospect_id)
    evidence = prospect.get("evidence_file") or {}
    blocks = []
    for block in BLOCKS:
        rows = [r for r in worklist(evidence) if r["block"] == block]
        if rows:
            blocks.append({"key": block, "title": BLOCK_TITLES.get(block, block), "rows": rows})
    return templates.TemplateResponse(request, "evidence.html", {
        "p": prospect,
        "blocks": blocks,
        "report": evidence_integrity(prospect),
        "notes": evidence.get("notes") or [],
    })


# --------------------------------------------------------------- dispositions

def _write_claim(prospect: dict[str, Any], path: str, updated: dict[str, Any]) -> None:
    evidence = set_claim_at(prospect.get("evidence_file") or {}, path, updated)
    db.update_prospect(prospect["id"], {"evidence_file": evidence})


def _load_for_disposition(prospect_id: str, path: str) -> tuple[dict, dict, dict]:
    prospect = _prospect_or_404(prospect_id)
    claim = claim_at(prospect.get("evidence_file") or {}, path)
    if claim is None:
        raise HTTPException(404, f"no claim at {path!r}")
    if is_tainted(claim):
        raise HTTPException(
            409,
            "this claim is tainted and is read-only: it derives from evidence that "
            "is not this company's. Quarantined, not deleted.",
        )
    return prospect, claim, _require_session(prospect_id)


async def _form(request: Request) -> dict[str, str]:
    """Read an urlencoded form body.

    Deliberately neither ``Form(...)`` nor ``request.form()``: both route through
    Starlette's multipart parser, which hard-requires python-multipart, and the
    stack in CLAUDE.md is locked. Every form on these pages posts
    application/x-www-form-urlencoded, which the standard library already parses,
    so the dependency would buy nothing.
    """
    body = (await request.body()).decode("utf-8", "replace")
    return {key: value for key, value in parse_qsl(body, keep_blank_values=True)}


def _required(form: dict[str, str], key: str) -> str:
    value = (form.get(key) or "").strip()
    if not value:
        raise HTTPException(400, f"{key} is required")
    return value


@app.post("/verify/{prospect_id}/source-opened")
async def source_opened(request: Request, prospect_id: str):
    path = _required(await _form(request), "path")
    """Record that the verifier opened this claim's source link.

    Server-side on purpose. The block7 approval rule is only worth having if it
    cannot be satisfied by a client that says it was.
    """
    _prospect_or_404(prospect_id)
    session = _require_session(prospect_id)
    opened = _opened_sources(session)
    opened.add(path)
    db.update_session(session["id"], {"opened_sources": sorted(opened)})
    return JSONResponse({"ok": True, "opened": sorted(opened)})


@app.post("/verify/{prospect_id}/approve")
async def approve(request: Request, prospect_id: str):
    path = _required(await _form(request), "path")
    prospect, claim, session = _load_for_disposition(prospect_id, path)
    if is_person_claim(path) and path not in _opened_sources(session):
        raise HTTPException(
            409,
            "open this person's source link before approving. A name we cannot "
            "see on its source is the one error this system must never make.",
        )
    _write_claim(prospect, path, mark_verified(claim))
    return RedirectResponse(f"/verify/{prospect_id}", status_code=303)


@app.post("/verify/{prospect_id}/edit")
async def edit(request: Request, prospect_id: str):
    """Correct a value, keeping the original alongside it.

    The source does not change: the claim still came from where it came from,
    and a correction that quietly rewrote provenance would be worse than the
    error it fixed. Editing also approves — a human who has rewritten the value
    has decided what it should say.
    """
    form = await _form(request)
    path, value = _required(form, "path"), _required(form, "value")
    prospect, claim, _session = _load_for_disposition(prospect_id, path)
    corrected = dict(claim)
    corrected.setdefault("original_value", claim.get("value"))
    corrected["value"] = value
    corrected["edited_at"] = datetime.now(UTC).isoformat()
    _write_claim(prospect, path, mark_verified(corrected))
    return RedirectResponse(f"/verify/{prospect_id}", status_code=303)


@app.post("/verify/{prospect_id}/kill")
async def kill(request: Request, prospect_id: str):
    """Reject a claim. It stays in the file, marked, and is used nowhere."""
    form = await _form(request)
    path, reason = _required(form, "path"), form.get("reason", "")
    prospect, claim, _session = _load_for_disposition(prospect_id, path)
    killed = dict(claim)
    killed["killed"] = True
    killed["killed_reason"] = reason.strip() or "rejected by the verifier"
    killed["killed_at"] = datetime.now(UTC).isoformat()
    _write_claim(prospect, path, killed)
    return RedirectResponse(f"/verify/{prospect_id}", status_code=303)


@app.post("/verify/{prospect_id}/ask")
async def ask_on_call(request: Request, prospect_id: str):
    """Park a claim as something to ask the prospect rather than assert at them."""
    form = await _form(request)
    path, question = _required(form, "path"), form.get("question", "")
    prospect, claim, _session = _load_for_disposition(prospect_id, path)
    asked = dict(claim)
    asked["discovery_question"] = True
    if question.strip():
        asked["question"] = question.strip()
    _write_claim(prospect, path, asked)
    return RedirectResponse(f"/verify/{prospect_id}", status_code=303)


# -------------------------------------------------------- manual evidence steps

def _block5_performed(evidence: dict[str, Any]) -> bool:
    """True once the reviews check has been done, whether or not it found anything.

    Performed-and-empty is a finding. Not-performed is a hole. Collapsing the
    two is how a gap becomes an unnoticed assumption.
    """
    body = (evidence or {}).get(BLOCK5_CUSTOMER_FRICTION) or {}
    return bool(body.get("check_performed"))


def _reviews_url(prospect: dict[str, Any]) -> str:
    from urllib.parse import quote_plus

    parts = [prospect.get("company_name") or "", prospect.get("city") or "",
             prospect.get("county") or "", "Indiana reviews"]
    return "https://www.google.com/search?q=" + quote_plus(" ".join(p for p in parts if p))


@app.post("/verify/{prospect_id}/block5")
async def record_friction(request: Request, prospect_id: str):
    """Record verbatim review quotes, or that the check ran and found nothing."""
    form = await _form(request)
    quote, source_url = form.get("quote", ""), form.get("source_url", "")
    none_found = form.get("none_found", "")
    prospect = _prospect_or_404(prospect_id)
    _require_session(prospect_id)
    evidence = dict(prospect.get("evidence_file") or {})
    block = dict(evidence.get(BLOCK5_CUSTOMER_FRICTION) or {})

    if none_found:
        marker_url = _reviews_url(prospect)
        block["check_performed"] = make_claim(
            "reviews checked by hand; no friction quotes found", Tier.T1, marker_url
        )
        block["check_performed"] = mark_verified(block["check_performed"])
    else:
        text = quote.strip()
        if not text:
            raise HTTPException(400, "a quote is required, or use 'none found'")
        if not source_url.strip():
            raise HTTPException(
                400, "source_url is required: a quote with no source is not a claim"
            )
        quotes = list(block.get("review_quotes") or [])
        quotes.append(mark_verified(make_claim(text, Tier.T1, source_url.strip())))
        block["review_quotes"] = quotes
        block["check_performed"] = mark_verified(
            make_claim("reviews checked by hand", Tier.T1, source_url.strip())
        )

    evidence[BLOCK5_CUSTOMER_FRICTION] = block
    db.update_prospect(prospect_id, {"evidence_file": evidence})
    return RedirectResponse(f"/verify/{prospect_id}", status_code=303)


@app.post("/verify/{prospect_id}/freshness")
async def record_freshness(request: Request, prospect_id: str):
    """Re-establish freshness, recording what was actually re-checked."""
    form = await _form(request)
    site_live, new_postings = form.get("site_live", ""), form.get("new_postings", "")
    recent_news, note = form.get("recent_news", ""), form.get("note", "")
    prospect = _prospect_or_404(prospect_id)
    _require_session(prospect_id)
    checked = [
        label for label, on in (
            ("site still live and unchanged in character", site_live),
            ("checked for new job postings", new_postings),
            ("checked for news this month", recent_news),
        ) if on
    ]
    if not checked:
        raise HTTPException(400, "tick at least one item: an empty freshness pass is not a pass")
    evidence = dict(prospect.get("evidence_file") or {})
    notes = list(evidence.get("notes") or [])
    detail = "; ".join(checked) + (f" — {note.strip()}" if note.strip() else "")
    notes.append({"node": "console:freshness", "note": detail,
                  "at": datetime.now(UTC).isoformat()})
    evidence["notes"] = notes
    db.update_prospect(prospect_id, {
        "evidence_file": evidence,
        "freshness_date": date.today().isoformat(),
    })
    return RedirectResponse(f"/verify/{prospect_id}", status_code=303)


@app.post("/verify/{prospect_id}/gaps")
async def record_gaps(request: Request, prospect_id: str):
    """Record what we still do not know and intend to ask on the call."""
    gaps = (await _form(request)).get("gaps", "")
    _prospect_or_404(prospect_id)
    session = _require_session(prospect_id)
    db.update_session(session["id"], {"gaps": gaps.strip() or None})
    return RedirectResponse(f"/verify/{prospect_id}", status_code=303)


# ---------------------------------------------------------------- floor check

def floor_check(prospect: dict[str, Any], session: dict[str, Any] | None) -> dict[str, Any]:
    """Every condition that must hold before a file may be called verified.

    Returns the full picture rather than the first failure: a verifier fixing
    one thing at a time, told only about the next problem each round, is being
    made to do the tool's work.
    """
    evidence = prospect.get("evidence_file") or {}
    rows = worklist(evidence)
    failures: list[str] = []

    pending = [r for r in rows if r["disposition"] is None]
    if pending:
        failures.append(
            f"{len(pending)} claim(s) still have no disposition: "
            + ", ".join(r["path"] for r in pending[:4])
            + ("…" if len(pending) > 4 else "")
        )

    # The block5 marker records that we looked, not anything about the company.
    # Counting it would let two real facts plus a procedural tick clear a floor
    # that exists to guarantee three things worth saying out loud.
    approved_t1 = [
        r for r in rows
        if r["disposition"] in ("approved", "edited")
        and r["tier"] == int(Tier.T1)
        and not r["path"].endswith("check_performed")
    ]
    if len(approved_t1) < MIN_APPROVED_T1:
        failures.append(
            f"only {len(approved_t1)} approved T1 claim(s); {MIN_APPROVED_T1} are required "
            f"before there is enough first-party fact to write from"
        )

    people = [r for r in rows if r["is_person"] and r["disposition"] in ("approved", "edited")]
    if not people:
        failures.append(
            "no approved person claim in block7: there is nobody confirmed to address"
        )

    questioned = [r for r in rows if r["disposition"] == "questioned"]
    if not questioned and not ((session or {}).get("gaps") or "").strip():
        failures.append(
            "no recorded gap: mark a claim ask-on-call or write what you still do not know"
        )

    if not _block5_performed(evidence):
        failures.append(
            "the block5 customer-friction check has not been performed "
            "(record findings, or 'none found')"
        )

    report = evidence_integrity(prospect)
    if not report.passing:
        failures.extend(f"evidence integrity: {f}" for f in report.failures)

    verdict = evidence.get("summary_verdict") or {}
    if verdict.get("evidence_coherent") is False:
        issues = "; ".join(verdict.get("issues") or []) or "unspecified"
        failures.append(f"unresolved coherence issues from the summary check: {issues}")

    return {
        "passing": not failures,
        "failures": failures,
        "counts": {
            "total": len(rows),
            "approved": sum(1 for r in rows if r["disposition"] == "approved"),
            "edited": sum(1 for r in rows if r["disposition"] == "edited"),
            "killed": sum(1 for r in rows if r["disposition"] == "killed"),
            "questioned": len(questioned),
            "approved_t1": len(approved_t1),
        },
    }


@app.post("/verify/{prospect_id}/mark-verified")
async def mark_prospect_verified(request: Request, prospect_id: str):
    """The only code path in this repository that may set stage='verified'.

    The runner refuses the value outright (lib.nodes.FORBIDDEN_STAGES), so no
    node can reach it however it is written. Here it is reachable only behind
    the floor check, and only with a completed session recorded — which
    tools/audit.py then re-checks against the database, so the guarantee
    survives changes to this file.
    """
    prospect = _prospect_or_404(prospect_id)
    session = db.open_session(prospect_id)
    result = floor_check(prospect, session)
    if not result["passing"]:
        return templates.TemplateResponse(
            request, "blocked.html",
            {"p": prospect, "failures": result["failures"]}, status_code=422,
        )

    session = _require_session(prospect_id)
    started = session.get("started_at")
    seconds = None
    if started:
        began = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        seconds = max(0, int((datetime.now(UTC) - began).total_seconds()))

    counts = result["counts"]
    db.update_session(session["id"], {
        "completed_at": datetime.now(UTC).isoformat(),
        "claims_total": counts["total"],
        "claims_approved": counts["approved"],
        "claims_killed": counts["killed"],
        "claims_edited": counts["edited"],
        "claims_questioned": counts["questioned"],
        "duration_seconds": seconds,
    })
    minutes = (prospect.get("research_minutes") or 0) + round((seconds or 0) / 60)
    db.update_prospect(prospect_id, {
        "stage": "verified",
        "freshness_date": date.today().isoformat(),
        "research_minutes": min(minutes, 32000),
        "priority_set_by": "human",
    })
    return RedirectResponse("/", status_code=303)


# ------------------------------------------------------------------ screens

def _funnel(prospects: list[dict], artifacts: list[dict], touches: list[dict]) -> list[dict]:
    """The pipeline as a funnel, each stage linked to the list behind it."""
    drafted = {a["prospect_id"] for a in artifacts}
    sendable = {a["prospect_id"] for a in artifacts if a.get("status") == "sendable"}
    sent = {t["prospect_id"] for t in touches if t.get("direction") == "outbound"}
    replied = {
        t["prospect_id"] for t in touches
        if t.get("direction") == "inbound" or t.get("response") not in (None, "none")
    }
    counts = Counter(p.get("priority") for p in prospects)
    return [
        {"label": "companies loaded", "n": len(prospects), "href": "/companies",
         "note": "every grant recipient the extractor found"},
        {"label": "scored", "n": sum(1 for p in prospects if p.get("signal_score") is not None),
         "href": "/companies?scored=yes",
         "note": "passed the evidence-integrity gate, so a score could be computed"},
        {"label": "P1", "n": counts.get("P1", 0), "href": "/companies?priority=P1",
         "note": "score 3+ and a named decision-maker — the call list"},
        {"label": "P2", "n": counts.get("P2", 0), "href": "/companies?priority=P2",
         "note": "worth research, not yet worth a call"},
        {"label": "P3", "n": counts.get("P3", 0), "href": "/companies?priority=P3",
         "note": "parked"},
        {"label": "drafted", "n": len(drafted), "href": "/outreach",
         "note": "a thesis and outreach have been generated"},
        {"label": "sendable", "n": len(sendable), "href": "/outreach",
         "note": "passed the outbound gate: every sentence maps to a claim"},
        {"label": "sent", "n": len(sent), "href": "/outreach",
         "note": "no send path exists yet, so this is zero by design"},
        {"label": "replied", "n": len(replied), "href": "/outreach",
         "note": "any response, including corrections"},
    ]


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    """Everything the pipeline knows, in one screen, in plain language."""
    prospects = db.list_prospects_full()
    artifacts = db.all_artifacts()
    touches = db.all_touches()

    review = [p for p in prospects if p.get("stage") == "needs_review"]
    reasons = Counter()
    for p in review:
        reason = (p.get("needs_review_reason") or "no reason recorded")
        reasons[reason.split(":")[0][:70]] += 1

    ready = [
        p for p in prospects
        if p.get("priority") == "P1" and (p.get("drive_minutes") or 999) <= 90
        and evidence_integrity(p).passing
    ]
    ready.sort(key=lambda p: (-(p.get("signal_score") or 0), p.get("drive_minutes") or 999))

    state = canary.read_state()
    return templates.TemplateResponse(request, "dashboard.html", {
        "funnel": _funnel(prospects, artifacts, touches),
        "integrity": Counter(p.get("website_status") or "not assessed" for p in prospects),
        "review_total": len(review),
        "review_reasons": reasons.most_common(5),
        "canary": state,
        "verdicts": ", ".join(state.allowed_verdicts()),
        "queue": ready[:10],
        "total": len(prospects),
    })


@app.get("/companies", response_class=HTMLResponse)
def companies(request: Request):
    """Filterable table of every company the pipeline has touched."""
    params = request.query_params
    rows = db.list_prospects_full()
    artifacts = Counter(a["prospect_id"] for a in db.all_artifacts())
    touches = Counter(t["prospect_id"] for t in db.all_touches())

    def keep(p: dict) -> bool:
        if params.get("priority") and p.get("priority") != params["priority"]:
            return False
        if params.get("stage") and p.get("stage") != params["stage"]:
            return False
        if params.get("county") and (p.get("county") or "") != params["county"]:
            return False
        if params.get("website_status") and (p.get("website_status") or "") != \
                params["website_status"]:
            return False
        if params.get("scored") == "yes" and p.get("signal_score") is None:
            return False
        if params.get("within"):
            try:
                if (p.get("drive_minutes") or 9999) > int(params["within"]):
                    return False
            except ValueError:
                pass
        query = (params.get("q") or "").strip().lower()
        return not (query and query not in (p.get("company_name") or "").lower())

    rows = [p for p in rows if keep(p)]
    sort = params.get("sort", "score")
    reverse = sort in ("score", "drafts")
    keys = {
        "score": lambda p: (p.get("signal_score") is not None, p.get("signal_score") or 0),
        "name": lambda p: (p.get("company_name") or "").lower(),
        "drive": lambda p: p.get("drive_minutes") or 9999,
        "county": lambda p: (p.get("county") or ""),
        "drafts": lambda p: artifacts.get(p["id"], 0),
    }
    rows.sort(key=keys.get(sort, keys["score"]), reverse=reverse)

    return templates.TemplateResponse(request, "companies.html", {
        "rows": rows,
        "artifacts": artifacts,
        "touches": touches,
        "counties": sorted({p.get("county") for p in db.list_prospects_full() if p.get("county")}),
        "stages": sorted({p.get("stage") for p in db.list_prospects_full() if p.get("stage")}),
        "statuses": ["ok", "not_found", "unreachable", "compromised"],
        "params": params,
        "sort": sort,
    })


def _thesis_sections(body: str) -> list[dict[str, str]]:
    """Split a generated thesis into readable sections rather than one wall."""
    if not body:
        return []
    parts = re.split(r"\n(?=#{1,3} )", body)
    out = []
    for part in parts:
        lines = part.strip().split("\n", 1)
        heading = lines[0].lstrip("# ").strip()
        out.append({"heading": heading or "Analysis",
                    "body": (lines[1] if len(lines) > 1 else "").strip()})
    return out


@app.get("/company/{prospect_id}", response_class=HTMLResponse)
def company_file(request: Request, prospect_id: str):
    """One company, told as a story. The heart of the console."""
    prospect = _prospect_or_404(prospect_id)
    evidence = prospect.get("evidence_file") or {}
    artifacts = db.artifacts_for(prospect_id)

    blocks = []
    for block in (*BLOCKS, "block9_discovery"):
        rows = [r for r in worklist(evidence) if r["block"] == block]
        blocks.append({
            "key": block,
            "title": BLOCK_TITLES.get(block, "9 · Open questions"),
            "explain": BLOCK_EXPLAIN.get(block, "Questions raised by conflicting sources."),
            "rows": rows,
            "empty_reason": BLOCK_EMPTY.get(block, "nothing was found for this block"),
        })

    thesis = next((a for a in artifacts if a["kind"] == "thesis"), None)
    return templates.TemplateResponse(request, "company.html", {
        "p": prospect,
        "report": evidence_integrity(prospect),
        "blocks": blocks,
        "people": persongate.gate_evidence(prospect),
        "thesis_sections": _thesis_sections((thesis or {}).get("body", "")),
        "thesis": thesis,
        "artifacts": [a for a in artifacts if a["kind"] in ("email", "brief")],
        "touches": db.touches_for(prospect_id),
        "grant_match_note": (
            f"The Manufacturing Readiness Grant requires a 1:1 match, so an award of "
            f"${prospect['grant_amount']:,.0f} means this company provably put at least "
            f"${prospect['grant_amount']:,.0f} of its own money in alongside it — "
            f"about ${prospect['grant_amount'] * 2:,.0f} of capital deployed."
            if prospect.get("grant_amount") else None
        ),
        "source_note": SOURCE_NOTE,
    })


@app.get("/outreach", response_class=HTMLResponse)
def outreach(request: Request):
    """The dispatch desk: what is ready, what is due, and what the canary allows."""
    artifacts = db.all_artifacts()
    prospects = {p["id"]: p for p in db.list_prospects_full()}
    sendable = [a for a in artifacts if a.get("status") == "sendable" and a["kind"] == "email"]
    for a in sendable:
        a["company"] = (prospects.get(a["prospect_id"]) or {}).get("company_name")
    batches = [sendable[i:i + canary.BATCH_SIZE]
               for i in range(0, len(sendable), canary.BATCH_SIZE)]

    touches = db.all_touches()
    today = date.today().isoformat()
    due = [
        {**t, "company": (prospects.get(t["prospect_id"]) or {}).get("company_name")}
        for t in touches
        if t.get("next_action_date") and str(t["next_action_date"]) <= today
    ]
    blocked = [a for a in artifacts if a.get("status") == "blocked" and a["kind"] == "email"]
    for a in blocked:
        a["company"] = (prospects.get(a["prospect_id"]) or {}).get("company_name")

    return templates.TemplateResponse(request, "outreach.html", {
        "batches": batches,
        "sendable_total": len(sendable),
        "blocked": blocked,
        "due": due,
        "recent": [
            {**t, "company": (prospects.get(t["prospect_id"]) or {}).get("company_name")}
            for t in touches[:15]
        ],
        "canary": canary.read_state(),
        "companies": sorted(
            ((p["id"], p.get("company_name") or "?") for p in prospects.values()),
            key=lambda pair: pair[1],
        ),
        "batch_size": canary.BATCH_SIZE,
    })


@app.post("/outreach/touch")
async def log_touch(request: Request):
    """Record a touch. The only write on the new screens.

    This feeds the calibration loop: corrections tell us which claims we get
    wrong, and quoted-back blocks tell us which research earns its keep.
    """
    form = await _form(request)
    prospect_id = _required(form, "prospect_id")
    _prospect_or_404(prospect_id)
    corrections = (form.get("corrections") or "").strip()
    payload = {
        "prospect_id": prospect_id,
        "channel": form.get("channel") or "email",
        "direction": form.get("direction") or "outbound",
        "content_summary": (form.get("summary") or "").strip() or None,
        "response": form.get("response") or "none",
        "corrections": [{"note": corrections}] if corrections else None,
        "quoted_back_blocks": _split_list(form.get("quoted_back")),
        "objections": _split_list(form.get("objections")),
        "next_action_date": (form.get("next_action_date") or "").strip() or None,
    }
    db.log_touch({k: v for k, v in payload.items() if v is not None})
    return RedirectResponse("/outreach", status_code=303)


def _split_list(raw: str | None) -> list[str] | None:
    items = [part.strip() for part in (raw or "").split(",") if part.strip()]
    return items or None


def main() -> None:
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
