"""PDF dossier generator — the pipeline's findings on paper.

    python -m tools.report                     # top 10 P1s
    python -m tools.report --limit 25
    python -m tools.report --priority P1,P2
    python -m tools.report --company "Mursix"  # one dossier + a leave-behind
    python -m tools.report --out path.pdf

TWO AUDIENCES, TWO DOCUMENTS

The **dossier** is internal. It shows tiers, checker verdicts, quarantined
claims and blocked drafts, because the operator's job is to judge the evidence
and they cannot judge what they cannot see. It is read on a phone in a car park.

The **leave-behind** is for the company itself, printed and handed across a
front desk. It carries no tier badges, no verdicts, no internal vocabulary, and
only claims that are both low-tier-enough to assert and independently confirmed.
A prospect reading "T4 hypothesis" learns that we grade our guesses about them;
that is a true thing they should never have to read.

Everything renders offline. reportlab is pure Python, so a dossier generates the
same on a laptop with the network cable out as it does on a workstation.
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from rich.console import Console

from lib import db
from lib.claims import Tier
from lib.evidence import BLOCKS
from lib.integrity import evidence_integrity, is_killed, is_tainted, iter_all_claims

OUT_DIR = Path("reports")
MAX_CLAIMS_PER_BLOCK = 10

INK = colors.HexColor("#14181f")
DIM = colors.HexColor("#5b6675")
RULE = colors.HexColor("#c9d1da")
BAD = colors.HexColor("#b3261e")

TIER_WORD = {
    1: "T1 — their own words or a government record. May be stated as fact.",
    2: "T2 — press. May be stated only with the publication named.",
    3: "T3 — an aggregator's estimate. Internal filtering only; never said aloud.",
    4: "T4 — our inference. Must be labelled a hypothesis wherever it appears.",
}

BLOCK_TITLES = {
    "block1_what_they_make": "1 · What they make",
    "block2_grant_funded": "2 · Grant-funded work",
    "block3_hiring_signals": "3 · Hiring signals",
    "block4_digital_front_door": "4 · Digital front door",
    "block5_customer_friction": "5 · Customer friction",
    "block6_tech_stack": "6 · Tech stack",
    "block7_people": "7 · People",
    "block8_financial_scale": "8 · Financial scale",
    "block9_discovery": "9 · Open questions",
}

BLOCK_EXPLAIN = {
    "block1_what_they_make": "What the company says it makes, in its own words.",
    "block2_grant_funded": "What the grant paid for, from the case study and round"
                           " announcements.",
    "block3_hiring_signals": "Open roles on their own careers page.",
    "block4_digital_front_door": "How their site behaves for a buyer trying to reach"
                                 " them — measured, not judged.",
    "block5_customer_friction": "Verbatim customer complaints, entered by hand.",
    "block6_tech_stack": "Software and platforms detected on their site.",
    "block7_people": "Named people with stated roles.",
    "block8_financial_scale": "Size signals — headcount, grant money.",
    "block9_discovery": "Questions raised where two sources disagreed.",
}

SCORE_WORDS = {
    "clerical_posting": "an active clerical or coordination role is posted",
    "data_gen_tech": "the grant bought technology that generates data",
    "case_study": "Conexus published a case study on them",
    "weak_front_door": "their website is hard for a buyer to act on",
    "decision_maker_found": "a named decision-maker was found",
    "in_drive_radius": "within ninety minutes of Muncie",
    "too_big": "over 250 staff or enterprise-owned (penalty)",
    "status_uncertain": "business status could not be confirmed (penalty)",
}

SENDER = ("Nahl Technologies", "6902 Challenge Ln, Indianapolis IN 46250",
          "ud.sikder@gmail.com")


# --------------------------------------------------------------------- styles

def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    def make(name, **kw):
        return ParagraphStyle(name, parent=base["Normal"], **kw)

    return {
        "title": make("t", fontName="Helvetica-Bold", fontSize=24, leading=28,
                      textColor=INK, spaceAfter=6),
        "sub": make("s", fontSize=10.5, leading=15, textColor=DIM, spaceAfter=4),
        "h1": make("h1", fontName="Helvetica-Bold", fontSize=15, leading=19,
                   textColor=INK, spaceBefore=14, spaceAfter=5),
        "h2": make("h2", fontName="Helvetica-Bold", fontSize=10.5, leading=14,
                   textColor=INK, spaceBefore=10, spaceAfter=3),
        "body": make("b", fontSize=9.5, leading=13.5, textColor=INK, alignment=TA_LEFT,
                     spaceAfter=3),
        "note": make("n", fontSize=8, leading=11, textColor=DIM, spaceAfter=2),
        "claim": make("c", fontSize=9, leading=12.5, textColor=INK, spaceAfter=1),
        "src": make("u", fontSize=7.2, leading=9.5, textColor=DIM, spaceAfter=5),
        "bad": make("x", fontSize=8, leading=11, textColor=BAD, spaceAfter=4),
        "lead": make("l", fontSize=12, leading=17, textColor=INK, spaceAfter=8),
    }


def esc(text: Any, limit: int = 900) -> str:
    """Escape for reportlab's mini-HTML and trim runaway values."""
    out = str(text if text is not None else "")
    out = re.sub(r"\s+", " ", out).strip()
    if len(out) > limit:
        out = out[:limit].rstrip() + " […]"
    return out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(DIM)
    canvas.drawString(0.75 * inch, 0.5 * inch,
                      "Nahl Technologies · internal research dossier · public sources only")
    canvas.drawRightString(LETTER[0] - 0.75 * inch, 0.5 * inch, f"page {doc.page}")
    canvas.restoreState()


def _leave_behind_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(DIM)
    canvas.drawString(0.75 * inch, 0.5 * inch, " · ".join(SENDER))
    canvas.restoreState()


# ------------------------------------------------------------------ rendering

def _claim_line(path: str, claim: dict, st: dict) -> list:
    """One claim as a readable sentence with everything an operator must weigh."""
    label = path.split(".", 1)[1] if "." in path else path
    label = re.sub(r"\[\d+\]", "", label).replace("flags.", "flag: ").replace("_", " ")
    tier = claim.get("tier")
    value = esc(claim.get("value"))
    struck = is_tainted(claim) or is_killed(claim)
    if struck:
        value = f"<strike>{value}</strike>"

    marks = [f'<font color="#5b6675">[T{tier}]</font>']
    if claim.get("corroborated"):
        marks.append('<font color="#1b7f4d">✓ confirmed independently</font>')
    verdict = claim.get("claimcheck")
    if verdict == "verbatim":
        marks.append('<font color="#1b7f4d">checked</font>')
    elif verdict == "inferable":
        marks.append('<font color="#8a6100">implied, not stated</font>')
    elif verdict == "unsupported":
        marks.append('<font color="#b3261e"><b>UNSUPPORTED</b></font>')

    flow = [Paragraph(
        f"<b>{esc(label, 90)}</b> — {value} {' '.join(marks)}", st["claim"]
    )]
    if verdict == "unsupported":
        flow.append(Paragraph(
            f"Not in the cited source: {esc(claim.get('claimcheck_reason'), 220)}", st["bad"]))
    if claim.get("conflict"):
        flow.append(Paragraph(
            "Sources disagree on this. Recorded as an open question, not resolved.", st["bad"]))
    if is_tainted(claim):
        flow.append(Paragraph(f"Quarantined: {esc(claim.get('taint_reason'), 200)}", st["bad"]))
    if is_killed(claim):
        flow.append(Paragraph(f"Rejected: {esc(claim.get('killed_reason'), 200)}", st["bad"]))
    flow.append(Paragraph(esc(claim.get("source_url"), 130), st["src"]))
    return flow


def _kv_table(rows: list[tuple[str, str]], st: dict) -> Table:
    data = [[Paragraph(f"<b>{k}</b>", st["body"]), Paragraph(v, st["body"])] for k, v in rows]
    table = Table(data, colWidths=[1.5 * inch, 5.4 * inch])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def match_note(prospect: dict) -> str:
    amount = prospect.get("grant_amount")
    if not amount:
        return ""
    return (
        f"The Manufacturing Readiness Grant requires a 1:1 match, so this award means "
        f"they provably put at least ${amount:,.0f} of their own money in alongside it — "
        f"roughly ${amount * 2:,.0f} of capital deployed. This is a recorded fact, not "
        f"an estimate."
    )


def company_flow(prospect: dict, artifacts: list[dict], st: dict) -> list:
    """Everything for one company, as reportlab flowables."""
    flow: list = []
    evidence = prospect.get("evidence_file") or {}
    name = esc(prospect.get("company_name"), 120)

    flow.append(Paragraph(name, st["h1"]))
    flow.append(Paragraph(
        f"{esc(prospect.get('county') or 'county unknown')} County · "
        f"{prospect.get('drive_minutes') if prospect.get('drive_minutes') is not None else '—'}"
        f" min from Muncie · {esc(prospect.get('website') or 'no website')} "
        f"[{esc(prospect.get('website_status') or 'not assessed')}]", st["note"]))

    score = prospect.get("signal_score")
    rows = [("Score", f"{score if score is not None else 'not computable'} · "
                      f"priority {prospect.get('priority') or '—'}")]
    if prospect.get("grant_amount"):
        year = f" in {prospect['grant_year']}" if prospect.get("grant_year") else ""
        rows.append(("Grant", f"${prospect['grant_amount']:,.0f}{year}"))
        rows.append(("What that means", match_note(prospect)))
    else:
        rows.append(("Grant", "no award figure found in the listing, case study or "
                              "round announcements"))
    if prospect.get("tech_purchased"):
        rows.append(("Bought", esc(prospect["tech_purchased"], 400)))
    rows.append(("Size", f"{prospect['employee_estimate']} employees"
                 if prospect.get("employee_estimate") else "no headcount found"))
    rows.append(("Industry", esc(prospect.get("industry_desc"), 500) or "—"))
    flow.append(_kv_table(rows, st))

    # 3 — why this score
    flow.append(Paragraph("Why this score", st["h2"]))
    breakdown = prospect.get("score_breakdown") or {}
    fired = [SCORE_WORDS.get(k, k) for k, v in breakdown.items() if v]
    if fired:
        flow.append(Paragraph("· " + "<br/>· ".join(esc(f, 160) for f in fired), st["body"]))
    else:
        report = evidence_integrity(prospect)
        flow.append(Paragraph(
            "No score. " + esc("; ".join(report.failures) or "nothing fired.", 400), st["body"]))

    # 4 — summary
    flow.append(Paragraph("Machine summary", st["h2"]))
    flow.append(Paragraph(esc(prospect.get("machine_summary"), 2000)
                          or "No summary was drafted for this record.", st["body"]))
    verdict = (evidence.get("summary_verdict") or {})
    if verdict:
        coherent = verdict.get("evidence_coherent")
        if coherent:
            wording = "Coherence check: reads as one coherent company."
        else:
            issues = esc("; ".join(verdict.get("issues") or []), 300)
            wording = f"Coherence check: FLAGGED — {issues}"
        flow.append(Paragraph(wording, st["note"] if coherent else st["bad"]))

    # 5 — evidence
    flow.append(Paragraph("The evidence", st["h2"]))
    for block in (*BLOCKS, "block9_discovery"):
        claims = [
            (p.removeprefix("evidence_file."), c) for p, c in iter_all_claims(evidence)
            if p.removeprefix("evidence_file.").split(".")[0] == block
        ]
        flow.append(Paragraph(BLOCK_TITLES.get(block, block), st["h2"]))
        flow.append(Paragraph(BLOCK_EXPLAIN.get(block, ""), st["note"]))
        if not claims:
            flow.append(Paragraph("Nothing recorded for this block.", st["note"]))
            continue
        for path, claim in claims[:MAX_CLAIMS_PER_BLOCK]:
            flow.extend(_claim_line(path, claim, st))
        if len(claims) > MAX_CLAIMS_PER_BLOCK:
            flow.append(Paragraph(
                f"+{len(claims) - MAX_CLAIMS_PER_BLOCK} more in the database.", st["note"]))

    # 6 — analysis
    flow.append(Paragraph("The analysis", st["h2"]))
    thesis = next((a for a in artifacts if a.get("kind") == "thesis"), None)
    if thesis and thesis.get("body"):
        for chunk in re.split(r"\n(?=#{1,3} )", thesis["body"]):
            lines = chunk.strip().split("\n", 1)
            head = lines[0].lstrip("# ").strip()
            if head:
                flow.append(Paragraph(esc(head, 160), st["h2"]))
            if len(lines) > 1:
                for para in lines[1].split("\n\n"):
                    if para.strip():
                        flow.append(Paragraph(esc(para, 1800), st["body"]))
    else:
        flow.append(Paragraph(
            "Not yet drafted. A thesis is generated only for P1 companies whose "
            "evidence passes the integrity gate.", st["note"]))

    # 7 — outreach
    flow.append(Paragraph("Outreach", st["h2"]))
    drafts = [a for a in artifacts if a.get("kind") in ("email", "brief")]
    if drafts:
        for artifact in drafts:
            flow.append(Paragraph(
                f"{esc(artifact.get('kind'))} — <b>{esc(artifact.get('status'))}</b> "
                f"after {artifact.get('attempts')} attempt(s)", st["h2"]))
            if artifact.get("status") == "blocked":
                for failure in (artifact.get("gate_failures") or [])[:6]:
                    flow.append(Paragraph(f"Blocked: {esc(failure, 260)}", st["bad"]))
            else:
                flow.append(Paragraph(esc(artifact.get("body"), 3000), st["body"]))
    else:
        flow.append(Paragraph("None yet.", st["note"]))

    # 8 — notes
    notes = [n.get("note", "") for n in (evidence.get("notes") or [])]
    if notes:
        flow.append(Paragraph("Notes and gaps", st["h2"]))
        for note in notes[:18]:
            flow.append(Paragraph(f"· {esc(note, 300)}", st["note"]))
    return flow


def cover_flow(count: int, st: dict, scope: str) -> list:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return [
        Paragraph("Prospect research dossier", st["title"]),
        Paragraph(f"{scope} · {count} compan{'y' if count == 1 else 'ies'} · {stamp}",
                  st["sub"]),
        Spacer(1, 18),
        Paragraph("How to read this", st["h1"]),
        Paragraph(
            "Every fact here is a <b>claim</b>: a value, the source it came from, the "
            "tier of that source, and the date someone looked. Nothing was bought and "
            "nothing behind a login was read.", st["body"]),
        Spacer(1, 6),
        *[Paragraph(f"· {TIER_WORD[t]}", st["body"]) for t in (1, 2, 3, 4)],
        Spacer(1, 8),
        Paragraph(
            "Claims are also checked by a separate model asked one question: does the "
            "cited source actually say this? Its verdicts appear beside each claim.",
            st["body"]),
        Paragraph("· <b>checked</b> — the source states it in these terms.", st["body"]),
        Paragraph("· <b>implied, not stated</b> — supported, but the reader must take a "
                  "step.", st["body"]),
        Paragraph('· <b><font color="#b3261e">UNSUPPORTED</font></b> — the source does '
                  "not carry it. Barred from all outreach.", st["body"]),
        Spacer(1, 8),
        Paragraph(
            "A <b>✓</b> means a second, independent source agreed. Struck-through text "
            "is quarantined — usually because the domain it came from turned out not to "
            "be the company's. It is kept, never deleted, so the record still shows what "
            "was believed and when it stopped being true.", st["body"]),
        PageBreak(),
    ]


def index_flow(prospects: list[dict], st: dict) -> list:
    head = ["#", "Company", "County", "Drive", "Score", "Pri", "Site"]
    data = [[Paragraph(f"<b>{h}</b>", st["note"]) for h in head]]
    for i, p in enumerate(prospects, 1):
        data.append([
            Paragraph(str(i), st["note"]),
            Paragraph(esc(p.get("company_name"), 60), st["note"]),
            Paragraph(esc(p.get("county")), st["note"]),
            Paragraph(f"{p.get('drive_minutes') if p.get('drive_minutes') is not None else '—'}",
                      st["note"]),
            Paragraph(f"{p.get('signal_score') if p.get('signal_score') is not None else '—'}",
                      st["note"]),
            Paragraph(esc(p.get("priority")), st["note"]),
            Paragraph(esc(p.get("website_status") or "—"), st["note"]),
        ])
    widths = [w * inch for w in (0.3, 2.7, 0.9, 0.5, 0.5, 0.4, 1.0)]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return [Paragraph("Index", st["h1"]), table, PageBreak()]


def build_dossier(prospects: list[dict], artifacts_by: dict, out: Path, scope: str) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    st = _styles()
    doc = SimpleDocTemplate(
        str(out), pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.7 * inch, bottomMargin=0.75 * inch,
        title="Prospect research dossier", author="Nahl Technologies",
    )
    flow = cover_flow(len(prospects), st, scope)
    if len(prospects) > 1:
        flow += index_flow(prospects, st)
    for index, prospect in enumerate(prospects):
        flow += company_flow(prospect, artifacts_by.get(prospect["id"], []), st)
        if index < len(prospects) - 1:
            flow.append(PageBreak())
    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer)
    return out


# ------------------------------------------------------------- leave-behind

class NoThesis(RuntimeError):
    """A leave-behind without analysis is a flyer. We do not print flyers."""


def presentable_claims(prospect: dict) -> list[tuple[str, dict]]:
    """Claims we may show the company itself.

    Tier 1 or 2, untainted, unkilled, and either independently corroborated or
    confirmed verbatim by the checker. Everything else is ours to reason with,
    not theirs to be shown.
    """
    out = []
    for path, claim in iter_all_claims(prospect.get("evidence_file") or {}):
        trimmed = path.removeprefix("evidence_file.")
        if trimmed.split(".")[0] not in BLOCKS:
            continue
        if is_tainted(claim) or is_killed(claim):
            continue
        if claim.get("tier") not in (int(Tier.T1), int(Tier.T2)):
            continue
        if not (claim.get("corroborated") is True or claim.get("claimcheck") == "verbatim"):
            continue
        if not readable_to_a_stranger(claim.get("value")):
            continue
        out.append((trimmed, claim))
    return out


NAV_WORDS = ("login", "careers", "sitemap", "skip to", "menu", "search",
             "privacy policy", "cookie", "subscribe", "toggle")


def readable_to_a_stranger(value: Any) -> bool:
    """True when a claim value is a sentence a company would recognise as about them.

    Three things reach here that are true, sourced, and useless on a page handed
    across a front desk:

    * booleans — a flag reads as the single word "True";
    * scraped navigation — "Login Careers English Español ..." is what a page
      says, not what a company does;
    * bare page titles — "About Us | Acme" tells the reader nothing.

    None of these is wrong. They are simply not sentences, and a leave-behind is
    prose or it is nothing.
    """
    if isinstance(value, bool) or not isinstance(value, str):
        return False
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) < 30 or len(text.split()) < 5:
        return False
    lowered = text.lower()
    if sum(word in lowered for word in NAV_WORDS) >= 2:
        return False
    return "|" not in text[:60]


def build_leave_behind(prospect: dict, artifacts: list[dict], out: Path) -> Path:
    """Two pages, prospect-facing. No tiers, no verdicts, no internal words."""
    thesis = next((a for a in artifacts if a.get("kind") == "thesis" and a.get("body")), None)
    if not thesis:
        raise NoThesis(
            f"{prospect.get('company_name')} has no thesis, so there is nothing to leave "
            f"behind but a flyer. Generate the analysis first."
        )
    st = _styles()
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out), pagesize=LETTER,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.9 * inch, bottomMargin=0.8 * inch,
        title=f"Notes for {prospect.get('company_name')}", author=SENDER[0],
    )
    name = esc(prospect.get("company_name"), 120)
    flow = [
        Paragraph(f"Notes for {name}", st["title"]),
        Paragraph(datetime.now(UTC).strftime("%d %B %Y"), st["sub"]),
        Spacer(1, 14),
        Paragraph(
            "We research Indiana manufacturers who have taken a Manufacturing Readiness "
            "Grant, and we write up what we think the next bottleneck is. This is what "
            "we found about you, from public sources. If we have something wrong, we "
            "would genuinely like to know.", st["lead"]),
        Paragraph("What we read about you", st["h1"]),
    ]
    shown = presentable_claims(prospect)
    if shown:
        for _path, claim in shown[:8]:
            flow.append(Paragraph(f"· {esc(claim.get('value'), 320)}", st["body"]))
            flow.append(Paragraph(esc(claim.get("source_url"), 120), st["src"]))
    else:
        flow.append(Paragraph(
            "Only what the grant listing publishes — we could not confirm much from "
            "your site.", st["body"]))

    if prospect.get("grant_amount"):
        flow.append(Paragraph("Your grant", st["h1"]))
        flow.append(Paragraph(
            f"The programme recorded an award of ${prospect['grant_amount']:,.0f}"
            f"{(' in ' + str(prospect['grant_year'])) if prospect.get('grant_year') else ''}. "
            f"Because the grant requires a matching investment, that means at least "
            f"${prospect['grant_amount'] * 2:,.0f} went into the floor.", st["body"]))

    first = re.split(r"\n(?=#{1,3} )", thesis["body"])
    body = next((c for c in first if c.strip() and not c.strip().startswith("## Diagnosis")),
                first[0])
    cleaned = re.sub(r"\[[a-z0-9_.\[\]]+\]", "", body)       # strip claim ids
    cleaned = re.sub(r"[#*]+", "", cleaned)
    # Drop our own internal headings — "Most Expensive Frictions" is how we talk
    # about a company, not how we talk to one.
    cleaned = "\n\n".join(
        para for para in cleaned.split("\n\n")
        if para.strip() and len(para.split()) > 12
    )
    paragraphs = [p for p in cleaned.split("\n\n") if p.strip()][:4]
    if not paragraphs:
        # The thesis exists but is all headings and citations once the internal
        # vocabulary is stripped. Printing the section empty would be worse than
        # refusing: it promises analysis and delivers a blank.
        raise NoThesis(
            f"{prospect.get('company_name')}'s thesis has no prose that survives "
            f"stripping our internal notation, so the leave-behind would carry an "
            f"empty analysis section. Redraft the thesis first."
        )
    flow.append(Paragraph("What we think that means", st["h1"]))
    for para in paragraphs:
        flow.append(Paragraph(esc(para, 900), st["body"]))

    flow += [
        Spacer(1, 16),
        Paragraph("If we have this wrong", st["h1"]),
        Paragraph(
            "Tell us and we will correct it. We would rather be corrected than be "
            "polite about a number we got wrong.", st["body"]),
        Spacer(1, 10),
        Paragraph(f"<b>{SENDER[0]}</b><br/>{SENDER[1]}<br/>{SENDER[2]}", st["body"]),
    ]
    doc.build(flow, onFirstPage=_leave_behind_footer, onLaterPages=_leave_behind_footer)
    return out


# ------------------------------------------------------------------------ CLI

def select(args) -> list[dict]:
    rows = db.list_prospects_full()
    if args.company:
        needle = args.company.lower()
        rows = [p for p in rows if needle in (p.get("company_name") or "").lower()]
        if not rows:
            raise SystemExit(f"no company matching {args.company!r}")
        return rows[:1]
    wanted = [p.strip().upper() for p in (args.priority or "P1").split(",") if p.strip()]
    rows = [p for p in rows if p.get("priority") in wanted]
    rows.sort(key=lambda p: (
        (p.get("drive_minutes") or 999) > 90,
        -(p.get("signal_score") or 0),
        p.get("drive_minutes") or 999,
    ))
    return rows[: args.limit]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate PDF prospect dossiers.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--priority", default="P1")
    parser.add_argument("--company", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    console = Console()
    prospects = select(args)
    artifacts_by: dict[str, list] = {}
    for prospect in prospects:
        artifacts_by[prospect["id"]] = db.artifacts_for(prospect["id"])

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    if args.company:
        slug = re.sub(r"[^a-z0-9]+", "-", prospects[0]["company_name"].lower()).strip("-")
        out = Path(args.out) if args.out else OUT_DIR / f"{slug}-{stamp}.pdf"
        scope = "Single-company dossier"
    else:
        out = Path(args.out) if args.out else OUT_DIR / f"dossier-{stamp}.pdf"
        scope = f"Priority {args.priority}"

    build_dossier(prospects, artifacts_by, out, scope)
    console.print(f"[green]wrote[/green] {out}  ({len(prospects)} compan"
                  f"{'y' if len(prospects) == 1 else 'ies'})")

    if args.company:
        leave = out.with_name(out.stem + "-leave-behind.pdf")
        try:
            build_leave_behind(prospects[0], artifacts_by[prospects[0]["id"]], leave)
            console.print(f"[green]wrote[/green] {leave}")
        except NoThesis as exc:
            console.print(f"[yellow]no leave-behind:[/yellow] {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
