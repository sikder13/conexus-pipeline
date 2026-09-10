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
import io
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image as RLImage,
)
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from rich.console import Console

from lib import (
    adapters,
    canary,
    casefile,
    charts,
    contacts,
    db,
    finmodel,
    routing,
    shortlist,
    theten,
)
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
    "block10_competitors": "10 · Competitors they name",
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
    "block10_competitors": "Rivals the company or its press coverage names,"
                           " and what we saw on their sites. Nobody is"
                           " inferred into this block.",
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


def _mini_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def esc(text: Any, limit: int = 900) -> str:
    """Escape for reportlab's mini-HTML and trim runaway values at a word.

    The marker stays because this renders the internal dossier, where a visible
    "[…]" tells the operator the value continues. It cuts at a space rather
    than a character so the last word is never sawn in half.
    """
    out = re.sub(r"\s+", " ", str(text if text is not None else "")).strip()
    if len(out) > limit:
        head = out[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—-")
        out = (head or out[:limit].rstrip()) + " […]"
    return _mini_html(out)


SENTENCE_END = re.compile(r"[.!?][\"'’”)\]]?(?=\s|$)")


def trim_to_sentence(text: Any, limit: int) -> str:
    """Shorten prospect-facing prose to a whole number of sentences.

    A page handed across a front desk cannot carry "the figure scales
    proporti […]". Two rules follow from that, and both are absolute here:
    the cut lands on a sentence end, and nothing is appended to mark it — an
    ellipsis is us telling a stranger we ran out of room on their own letter.

    Returns "" when not even the first sentence fits, because a paragraph that
    cannot end on a period does not belong on the page at all. The caller drops
    it rather than printing a fragment.
    """
    out = re.sub(r"\s+", " ", str(text if text is not None else "")).strip()
    if not out:
        return ""
    if len(out) <= limit:
        return out
    ends = [m.end() for m in SENTENCE_END.finditer(out) if m.end() <= limit]
    return out[:ends[-1]].strip() if ends else ""


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


# --------------------------------------------------- the analysis, as a document

ANALYSIS_HEADING = re.compile(r"(?m)^(#{2,3})\s+(.*)$")
CLAIM_REFERENCE = re.compile(r"\[([a-z0-9_]+(?:\.[a-z0-9_\[\]]+)+)\]")


class Passage(NamedTuple):
    """One heading from the analysis and the prose under it."""

    level: int
    heading: str
    body: str


def analysis_passages(body: str) -> list[Passage]:
    """Split a stored analysis into the headed passages it was written as.

    The analysis is stored as one document rather than as a bag of fields
    because that is what the operator reads, and a document that has to be
    reassembled from columns before anyone can read it is a document nobody
    reads. Splitting it back out for layout is this function's problem alone.
    """
    out: list[Passage] = []
    matches = list(ANALYSIS_HEADING.finditer(body or ""))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        out.append(Passage(len(match.group(1)), match.group(2).strip(),
                           body[match.end():end].strip()))
    return out


def cited(text: Any, limit: int = 2400) -> str:
    """Prose with its claim references kept but pushed into the background.

    The references are why an operator can check a sentence at all, so removing
    them for tidiness would remove the point. Setting them small and grey keeps
    the paragraph readable and keeps the audit trail on the page, which is the
    trade this document exists to make.
    """
    escaped = esc(text, limit)
    return CLAIM_REFERENCE.sub(
        lambda m: f'<font size="6" color="#8792a2">[{m.group(1)}]</font>', escaped)


def peer_table(peer: dict, st: dict) -> list:
    """The peer comparison as a table, with the group it was computed over."""
    positions = peer.get("positions") or []
    if not positions:
        return [Paragraph("No comparable companies were found.", st["note"])]
    header = [Paragraph(f"<b>{h}</b>", st["claim"])
              for h in ("Measure", "Them", "Against the group")]
    data = [header]
    for position in positions:
        data.append([
            Paragraph(esc(position.get("label"), 60), st["claim"]),
            Paragraph(esc(position.get("subject_value"), 200), st["claim"]),
            Paragraph(esc(position.get("headline"), 220),
                      st["claim"] if position.get("comparable") else st["note"]),
        ])
    table = Table(data, colWidths=[1.1 * inch, 2.5 * inch, 3.3 * inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    flow = [table, Paragraph(esc(peer.get("basis"), 300), st["note"])]
    if peer.get("caveat"):
        flow.append(Paragraph(esc(peer["caveat"], 400), st["note"]))
    return flow


def approach_box(passage: Passage, st: dict) -> Table:
    """One approach, boxed, ending on the line that says what it is worth.

    Boxed because the operator is choosing between three of these on a phone
    call and needs to see where one stops and the next begins. The money line is
    last and separated, because it is the line they will read first and the one
    they must not read without the scope above it.
    """
    paragraphs = [p.strip() for p in passage.body.split("\n\n") if p.strip()]
    money = paragraphs.pop() if paragraphs and " · " in paragraphs[-1] else ""
    inner: list = [Paragraph(esc(passage.heading, 120), st["h2"])]
    for para in paragraphs:
        inner.append(Paragraph(cited(para, 1800), st["body"]))
    if money:
        inner.append(Paragraph(f"<b>{esc(money, 300)}</b>", st["body"]))
    table = Table([[inner]], colWidths=[6.9 * inch])
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def highlight_box(heading: str, body: str, st: dict) -> Table:
    """The lead recommendation, set apart because it is the one decision here."""
    table = Table([[[Paragraph(esc(heading, 80), st["h2"]),
                     Paragraph(cited(body, 1200), st["body"])]]],
                  colWidths=[6.9 * inch])
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.0, INK),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f2f5f9")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def model_charts(artifact: dict, st: dict) -> list:
    """The charts, redrawn from the specs the analysis stored.

    Redrawn rather than stored as pixels, and that is the point of the spec
    being serialisable. A picture in the dossier is the most persuasive thing on
    the page and the least examined, so every line on it has to be re-derivable
    from named inputs — which means storing the model and drawing from it, not
    storing an image nobody can check against anything.
    """
    specs = (artifact.get("gate_map") or {}).get("models") or []
    if not specs:
        return []
    flow: list = [Paragraph("The arithmetic, drawn", st["h2"])]
    flow.append(Paragraph(
        "Every line below comes from a model whose inputs each name a claim, a "
        "cited benchmark or a labelled assumption. A chart with no model behind "
        "it is not drawn.", st["note"]))
    for raw in specs[:2]:
        try:
            spec = finmodel.ModelSpec.model_validate(raw)
            report = finmodel.run(spec)
            drawn = charts.charts_for(report)
        except Exception as exc:
            flow.append(Paragraph(
                f"A chart could not be redrawn from its model: "
                f"{type(exc).__name__}. The prose figures still stand; the "
                f"picture does not.", st["bad"]))
            continue
        for kind, words in charts.CHART_KINDS:
            if kind not in drawn:
                continue
            flow.append(RLImage(io.BytesIO(drawn[kind]),
                                width=6.6 * inch, height=3.3 * inch))
            flow.append(Paragraph(
                esc(f"{words}. {charts.caption_for(report, kind)}", 700),
                st["note"]))
        flow.append(Spacer(1, 8))
    return flow


def case_notes(artifact: dict, st: dict) -> list:
    """What the analysis assumed, what it cited, and what it refused to offer."""
    case = (artifact.get("gate_map") or {}).get("case") or {}
    if not case:
        return []
    flow: list = [Paragraph("What this rests on", st["h2"])]
    if case.get("positioning"):
        flow.append(Paragraph(esc(case["positioning"], 700), st["note"]))
    assumptions = case.get("assumptions") or []
    if assumptions:
        flow.append(Paragraph(
            "Assumed, and therefore a question for the call:", st["body"]))
        for item in assumptions:
            flow.append(Paragraph(f"• {esc(item.get('label'), 260)}", st["body"]))
    if case.get("gain_share"):
        share = case["gain_share"]
        flow.append(Paragraph(
            f"Gain share available: {esc(share.get('metric'), 160)} is the one "
            f"agreed metric. Conditions: "
            f"{esc('; '.join(share.get('requirements') or []), 900)}", st["body"]))
    elif case.get("gain_share_reason"):
        flow.append(Paragraph(
            f"Gain share NOT offered: {esc(case['gain_share_reason'], 400)}",
            st["body"]))
    for citation in (case.get("citations") or [])[:6]:
        flow.append(Paragraph(esc(citation, 900), st["note"]))
    if case.get("macro_status"):
        flow.append(Paragraph(esc(case["macro_status"], 300), st["note"]))
    return flow


def analysis_flow(artifact: dict, st: dict, prospect: dict | None = None) -> list:
    """The scope-of-work analysis, laid out as the working document it is."""
    meta = artifact.get("gate_map") or {}
    thin = bool(meta.get("thin"))
    flow: list = [Paragraph("Scope of work — internal only", st["h2"])]
    flow.append(Paragraph(
        "Written for the person making the call, never shown to the company. "
        "Figures are ranges or name their source; anything marked as assumed is "
        "a question for the call, not a finding.", st["note"]))
    if thin:
        flow.append(Paragraph(
            "THIN EVIDENCE. This company sits below the drafting floor, so there "
            "are no costed findings and no priced approaches here — only what "
            "the evidence can carry and what the first call must establish. "
            "Anything more would be invented.", st["bad"]))
    if artifact.get("status") != "sendable":
        flow.append(Paragraph(
            "This analysis did not pass its own checks. Read it, but check every "
            "figure against the evidence above before repeating one.", st["bad"]))
        for failure in (artifact.get("gate_failures") or [])[:6]:
            flow.append(Paragraph(f"Held back: {esc(failure, 300)}", st["bad"]))

    peer = meta.get("peer") or {}
    for passage in analysis_passages(artifact.get("body") or ""):
        if passage.level == 3 and passage.heading.lower().startswith("lead"):
            flow.append(highlight_box(passage.heading, passage.body, st))
            continue
        if passage.level == 3:
            flow.append(approach_box(passage, st))
            continue
        flow.append(Paragraph(esc(passage.heading, 120), st["h2"]))
        for para in passage.body.split("\n\n"):
            if para.strip():
                flow.append(Paragraph(cited(para, 2400), st["body"]))
        if peer and passage.heading.lower().startswith("where they stand"):
            flow.extend(peer_table(peer, st))
            flow.extend(rival_table(meta, st, prospect))
    flow.extend(model_charts(artifact, st))
    flow.extend(case_notes(artifact, st))
    return flow


def rival_table(meta: dict, st: dict, prospect: dict | None = None) -> list:
    """The named-rival comparison, with the basis it was counted on.

    The gaps come from the record the analysis stored, because those are the
    sentences it actually wrote. The scarcity lines are RECOMPUTED from the
    evidence, so a dossier printed today reflects every rival read since the
    analysis was written — the same argument the harvester makes for storing
    observations rather than sentences: a count that cannot move is a count that
    goes quietly stale.
    """
    case = meta.get("case") or {}
    lines = case.get("velocity") or []
    scarce = list(case.get("scarcity") or [])
    if prospect is not None:
        table = casefile.read_gap_table(prospect)
        if table is not None:
            scarce = [line.sentence for line in table.scarcity()]
    if not lines and not scarce:
        return []
    flow: list = [Paragraph("Against the named rivals", st["h3"] if "h3" in st
                            else st["body"])]
    if case.get("rival_basis"):
        flow.append(Paragraph(esc(case["rival_basis"], 600), st["note"]))
    for line in lines[:8]:
        flow.append(Paragraph(f"• {esc(line, 300)}", st["body"]))
    for line in scarce[:4]:
        flow.append(Paragraph(f"• {esc(line, 300)}", st["body"]))
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


# ------------------------------------------------------- the grant, told once

class Award(NamedTuple):
    """One grant award the page is entitled to state."""

    amount: float
    year: int | None


class GrantFiguresDisagree(RuntimeError):
    """The analysis states grant money the record does not support."""


def grant_awards(prospect: dict) -> list[Award]:
    """The awards this page may state, or nothing.

    Returns EMPTY when our own sources disagree about the amount. The
    corroboration node deliberately picks no winner on a conflict, and a page
    handed to the company is the last place to start picking one — they know
    what they were awarded, and a confidently wrong figure is the fastest way
    to prove we do not.
    """
    block = (prospect.get("evidence_file") or {}).get("block2_grant_funded") or {}
    claim = block.get("grant_amount") or {}
    if claim.get("conflict") or is_tainted(claim) or is_killed(claim):
        return []

    recorded = block.get("awards")
    if isinstance(recorded, list) and recorded:
        out = []
        for entry in recorded:
            amount = (entry or {}).get("amount")
            if isinstance(amount, (int, float)) and amount > 0:
                out.append(Award(float(amount), (entry or {}).get("year")))
        if out:
            return sorted(out, key=lambda a: (a.year or 0, a.amount))

    amount = prospect.get("grant_amount")
    if not isinstance(amount, (int, float)) or amount <= 0:
        return []
    return [Award(float(amount), prospect.get("grant_year"))]


def _dollars(amount: float) -> str:
    return f"${amount:,.0f}"


def _with_year(award: Award) -> str:
    return f"{_dollars(award.amount)}{f' in {award.year}' if award.year else ''}"


def grant_story(awards: list[Award]) -> str:
    """The grant, in one telling that the rest of the page must not contradict.

    Every figure on the page comes from here. The leave-behind used to state a
    single award in a box while the analysis beside it totalled two, so a reader
    met $50,000 and $86,700 for the same thing and had no way to tell which we
    meant. Whether it itemises or not, it commits to one arithmetic.
    """
    if not awards:
        return ""
    total = sum(a.amount for a in awards)
    committed = total * 2
    if len(awards) == 1:
        return (
            f"The programme recorded an award of {_with_year(awards[0])}. The grant "
            f"requires you to match it one for one, so at least {_dollars(committed)} "
            f"of capital went into the work."
        )
    listed = ", ".join(_with_year(a) for a in awards[:-1])
    return (
        f"The programme recorded {_count_word(len(awards))} grants: {listed} and "
        f"{_with_year(awards[-1])} — {_dollars(total)} in total. The grant requires "
        f"you to match each one for one, so at least {_dollars(committed)} of capital "
        f"went into the work."
    )


COUNT_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def _count_word(n: int) -> str:
    return COUNT_WORDS.get(n, str(n))


GRANT_WORDS = ("grant", "award", "match", "matched", "committed", "capital")
MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")


def _money_in(sentence: str) -> set[int]:
    out = set()
    for raw in MONEY.findall(sentence):
        try:
            out.add(int(round(float(raw.replace(",", "")))))
        except ValueError:
            continue
    return out


def grant_money_claimed(text: str) -> set[int]:
    """Dollar figures the prose attaches to grant money, in whole dollars.

    Scoped to sentences that actually talk about the grant. The analysis is
    supposed to contain other money — rework, hours, a conditional range — and
    treating every dollar sign as a grant claim would reject good arithmetic.
    """
    claimed: set[int] = set()
    for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
        lowered = sentence.lower()
        if any(word in lowered for word in GRANT_WORDS):
            claimed |= _money_in(sentence)
    return claimed


PLURAL_GRANTS = re.compile(
    r"\b(?:grants|awards|both\s+(?:grants|awards|rounds)|multiple\s+(?:grants|awards)|"
    r"each\s+(?:grant|award)|several\s+(?:grants|awards))\b", re.IGNORECASE
)
"""Prose asserting more than one award.

Withholding a disputed amount and then writing "the grant awards you have
received" trades a wrong number for a wrong count. The company knows exactly
how many it got, and being confidently wrong about that costs the same
credibility the withheld figure was protecting."""


def reconcile_grant_count(text: str, awards: list[Award]) -> None:
    """Refuse prose claiming several awards when the record shows one or none."""
    if len(awards) > 1:
        return
    found = PLURAL_GRANTS.findall(text or "")
    if found:
        held = (f"the record shows {len(awards)} award"
                if awards else "no award is recorded at all")
        raise GrantFiguresDisagree(
            f"the analysis says {found[0].strip()!r} but {held}"
        )


def reconcile_grant_money(text: str, awards: list[Award]) -> None:
    """Refuse to print analysis that invents grant money.

    This is the check that would have caught the live failure: the record held
    one $50,000 award and the analysis asserted $86,700 across two rounds and
    $170,000 committed, none of which is anywhere in the evidence. Printing it
    would have handed a company a made-up account of their own finances.

    Raising rather than quietly dropping the sentence is deliberate. A
    fabricated figure means the draft is wrong, not merely too long, and the
    operator needs to see that before anything else is printed for it.
    """
    allowed = {int(round(a.amount)) for a in awards}
    allowed |= {int(round(sum(a.amount for a in awards)))}
    allowed |= {int(round(sum(a.amount for a in awards) * 2))}
    allowed.discard(0)
    invented = sorted(f for f in grant_money_claimed(text) if f not in allowed)
    if invented:
        raise GrantFiguresDisagree(
            "the analysis states grant money the record does not support: "
            + ", ".join(_dollars(f) for f in invented[:4])
            + (f" (recorded: {', '.join(_dollars(a) for a in sorted(allowed))})"
               if allowed else " (no award is recorded at all)")
        )


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


def company_flow(prospect: dict, artifacts: list[dict], st: dict,
                 route: str = routing.FULL, route_reason: str = "") -> list:
    """Everything for one company, as reportlab flowables."""
    flow: list = []
    evidence = prospect.get("evidence_file") or {}
    name = esc(prospect.get("company_name"), 120)

    flow.append(Paragraph(name, st["h1"]))
    if route == routing.CALL_FIRST:
        flow.append(Paragraph(
            f"CALL FIRST — {esc(route_reason, 200)}. Written outreach asserting "
            f"claims is out of scope for this company until a call or new "
            f"evidence lifts it.", st["bad"]))
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

    # 6 — analysis. The scope of work supersedes the thesis where one exists:
    # they answer the same question, and the analysis answers it with costed
    # approaches and a peer position instead of a paragraph of reasoning. The
    # thesis is still printed when no analysis has been written, because a
    # company with neither is a company the operator walks into cold.
    analysis = next((a for a in artifacts if a.get("kind") == "analysis"), None)
    if analysis and analysis.get("body"):
        flow.extend(analysis_flow(analysis, st, prospect))
        return flow + _contact_and_log_flow(prospect, artifacts, evidence, st)

    thesis = next((a for a in artifacts if a.get("kind") == "thesis"), None)
    blocked_thesis = bool(thesis) and thesis.get("status") != "sendable"
    if blocked_thesis:
        # The operator writes by hand from these now. A thesis that failed the
        # gate is still the best briefing anybody has on the company — it just
        # may not be handed to them, so it is labelled rather than withheld.
        flow.append(Paragraph(
            "Internal analysis — not cleared for prospect-facing use", st["h2"]))
        flow.append(Paragraph(
            "This did not pass the outbound gate and must not be sent or printed "
            "for the company. Read it as briefing for an email you write "
            "yourself, and check any figure against the evidence above.",
            st["note"]))
        for failure in (thesis.get("gate_failures") or [])[:8]:
            flow.append(Paragraph(f"Blocked: {esc(failure, 400)}", st["bad"]))
    else:
        flow.append(Paragraph("The analysis", st["h2"]))
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

    return flow + _contact_and_log_flow(prospect, artifacts, evidence, st)


def _contact_and_log_flow(
    prospect: dict, artifacts: list[dict], evidence: dict, st: dict
) -> list:
    """How to reach them, what has been drafted, and what is still missing.

    The tail of every company page, split out because the analysis and the older
    thesis are two ways of filling the middle and both need the same ending. An
    operator who reaches the bottom of one and not the other loses the contact
    paths, which is the half of the page they actually act on.
    """
    flow: list = []

    # 7 — how to reach them, for an operator sending by hand
    flow.append(Paragraph("Contact", st["h2"]))
    paths = contacts.contact_paths(prospect)
    if paths:
        for path in paths:
            line = f"<b>{esc(path.label, 60)}</b> — {esc(path.detail, 220)}"
            flow.append(Paragraph(line, st["body"]))
            if path.caution:
                flow.append(Paragraph(esc(path.caution, 220), st["bad"]))
            if path.source_url:
                flow.append(Paragraph(esc(path.source_url, 150), st["src"]))
    if not contacts.reachable(prospect):
        site = esc(prospect.get("website"), 150)
        flow.append(Paragraph(
            f"{contacts.NO_CONTACT_NOTE}{(' — ' + site) if site else ''}", st["note"]))

    # 8 — outreach
    flow.append(Paragraph("Outreach", st["h2"]))
    drafts = [a for a in artifacts if a.get("kind") in ("email", "brief", "linkedin")
              and a.get("status") in ("sendable", "blocked")]
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


def ten_summary_table(ten: list, st: dict) -> Table:
    """The ranked table an operator reads before opening anything."""
    header = [Paragraph(f"<b>{h}</b>", st["claim"]) for h in
              ("#", "Company", "Score", "Way in", "Lead offer", "Expected return")]
    data = [header]
    for index, candidate in enumerate(ten, 1):
        path = candidate.best_path
        offer = candidate.lead_offer or {}
        data.append([
            Paragraph(str(index), st["claim"]),
            Paragraph(esc(candidate.name, 60), st["claim"]),
            Paragraph(f"{candidate.score} · {candidate.drive}m", st["claim"]),
            Paragraph(esc(path.detail if path else "—", 90), st["claim"]),
            Paragraph(esc(offer.get("name") or "—", 70), st["claim"]),
            Paragraph(esc(theten.roi_words(candidate), 40), st["claim"]),
        ])
    table = Table(data, colWidths=[0.25 * inch, 1.5 * inch, 0.65 * inch,
                                   1.9 * inch, 1.5 * inch, 1.1 * inch],
                  repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def near_miss_table(misses: list, st: dict) -> Table:
    """Everything that ranked and did not qualify, and exactly what it lacks."""
    header = [Paragraph(f"<b>{h}</b>", st["claim"])
              for h in ("Company", "Score", "What it still needs")]
    data = [header]
    for candidate in misses:
        data.append([
            Paragraph(esc(candidate.name, 60), st["claim"]),
            Paragraph(f"{candidate.score}", st["claim"]),
            Paragraph(esc("; ".join(candidate.missing()), 220), st["claim"]),
        ])
    table = Table(data, colWidths=[2.0 * inch, 0.5 * inch, 4.4 * inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def build_ten(candidates: list, artifacts_by: dict, out: Path,
              target: int = theten.TARGET, title: str = "The Ten") -> Path:
    """One document: the ranked list, then every ready company in full.

    ``target`` is how many the list would like to hold, not how many it will
    contain. Fifty is the same document over a longer queue — the qualification
    is unchanged, and a shortfall is reported rather than filled.
    """
    ten = theten.the_ten(candidates, target)
    misses = theten.near_misses(candidates, target)
    counts = theten.reason_counts(candidates)
    st = _styles()
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    flow: list = [
        Paragraph(title, st["title"]),
        Paragraph(f"{len(ten)} compan{'y' if len(ten) == 1 else 'ies'} ready to "
                  f"contact · {stamp}", st["sub"]),
        Paragraph(
            "Ready means four things at once: a full scope of work, a way in an "
            "operator can act on today, an email that passed the outbound gate, "
            "and a LinkedIn pair that passed the same gate. A company missing any "
            "one of them is listed below the fold with exactly what it lacks. "
            f"Nothing is loosened to reach {target} — {target} is what we would "
            f"like, and this is how many are actually ready.", st["body"]),
        Spacer(1, 10),
    ]
    if ten:
        flow.append(ten_summary_table(ten, st))
    else:
        flow.append(Paragraph("None yet.", st["note"]))

    flow.append(Paragraph("What the rest are still missing", st["h1"]))
    flow.append(Paragraph(
        "Counted across every ranked company, so the totals say where the next "
        "hour is best spent rather than which company is closest.", st["note"]))
    for words, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        flow.append(Paragraph(f"· {count} — {esc(words, 120)}", st["body"]))
    if misses:
        flow.append(Spacer(1, 8))
        flow.append(near_miss_table(misses, st))

    for candidate in ten:
        flow.append(PageBreak())
        flow.extend(company_flow(candidate.prospect,
                                 artifacts_by.get(candidate.prospect["id"], []), st))

    out.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out), pagesize=LETTER, title="The Ten",
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.9 * inch,
    )
    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer)
    return out


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


CONSERVATIVE_VERDICTS: tuple[str, ...] = ("verbatim",)
"""What routing assumes when the canary state cannot be read.

The narrowest reading, on purpose. It routes MORE companies to call-first, not
fewer, so a database we could not reach costs us reach rather than restraint."""


def build_dossier(prospects: list[dict], artifacts_by: dict, out: Path, scope: str,
                  verdicts: tuple[str, ...] | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    st = _styles()
    doc = SimpleDocTemplate(
        str(out), pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.7 * inch, bottomMargin=0.75 * inch,
        title="Prospect research dossier", author="Nahl Technologies",
    )
    verdicts = verdicts or CONSERVATIVE_VERDICTS
    full, call_first = routing.split(prospects, verdicts)

    ordered = full + call_first
    flow = cover_flow(len(prospects), st, scope)
    if len(prospects) > 1:
        # The index has to be in the order the body is in. Built from the
        # pre-split list it numbered Trifecta first and then printed it in the
        # call-first section at the back, which is an index that sends a reader
        # to the wrong page.
        flow += index_flow(ordered, st)

    for index, prospect in enumerate(ordered):
        # The call-first companies are a section, not a footnote, and they are
        # introduced once rather than annotated one by one.
        if call_first and prospect is call_first[0]:
            flow.append(PageBreak())
            flow.extend(call_first_header(len(call_first), st))
        elif index:
            flow.append(PageBreak())
        flow += company_flow(prospect, artifacts_by.get(prospect["id"], []), st,
                             route=routing.route_for(prospect, verdicts),
                             route_reason=routing.reason_for(prospect, verdicts))
    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer)
    return out


def call_first_header(count: int, st: dict) -> list:
    """The one page that explains what the rest of the document is."""
    return [
        Paragraph("Call-first prospects", st["h1"]),
        Paragraph(
            f"{count} compan{'y' if count == 1 else 'ies'} below the evidence "
            f"floor after every enrichment pass we have.", st["body"]),
        Paragraph(esc(routing.CALL_FIRST_EXPLANATION, 900), st["body"]),
        Paragraph(
            "These are NOT weaker prospects. Several of them score higher than "
            "companies in the section above. What they lack is checkable "
            "published evidence — a team page, a certification listing, press "
            "coverage — which is a fact about their website and not about their "
            "business. A shop with none of those may be the best prospect here "
            "and we would have no way to know it from outside.", st["note"]),
        Paragraph(
            "Each carries the sections its evidence can hold and the questions "
            "a first call must answer. There are no costed approaches, because "
            "there is nothing yet to cost.", st["note"]),
        Spacer(1, 10),
    ]


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


def lead_sentence(has_findings: bool) -> str:
    """The opening paragraph, written from the sections that actually follow.

    Dropping the apologetic findings section was right, but the lead kept
    promising "this is what we found about you" and then showed nothing — a
    seam a reader notices immediately. The lead is generated from what the page
    contains rather than from what it usually contains.
    """
    opening = (
        "We research Indiana manufacturers who have taken a Manufacturing Readiness "
        "Grant, and we write up what we think the next bottleneck is. "
    )
    middle = (
        "This is what we read about you in public sources, and what we think it means. "
        if has_findings else
        "What follows is our reading of your situation, from the public record of "
        "your grant. "
    )
    return opening + middle + (
        "If we have something wrong, we would genuinely like to know."
    )


THOUGHT_MARKERS = (
    "we would", "we could", "we think", "our hypothesis", "if that is right",
    "the work is", "worth", "question", "a week", "two weeks", "three weeks",
    "four weeks", "no new", "would take", "would cost", "a short call",
    "the fix", "a bounded", "starts with", "we can", "we'd",
)
"""Language that turns a stated problem into a thought about it.

The last paragraph of a two-page letter is the one a reader finishes on. The
live failure ended on a description of the company's own legacy-software fear
with nothing attached — we told them their problem and then stopped, which
reads as either a threat or a shrug."""


def ends_with_a_thought(para: str) -> bool:
    """True when a paragraph offers something, not just names a difficulty."""
    return any(marker in para.lower() for marker in THOUGHT_MARKERS)


def company_words(company_name: str | None) -> list[str]:
    """The forms of a company's name that read as third person in a letter."""
    raw = (company_name or "").strip()
    if not raw:
        return []
    bare = re.sub(
        r"\b(inc|llc|ltd|corp|corporation|company|co|plc|group|limited)\b\.?",
        "", raw, flags=re.I,
    )
    bare = re.sub(r"[^\w\s&'-]", " ", bare)
    forms = {raw.lower(), bare.strip().lower()}
    first = bare.strip().split()
    if first and len(first[0]) > 3:
        forms.add(first[0].lower())
    return sorted({f for f in forms if len(f) > 3}, key=len, reverse=True)


def speaks_to_the_reader(para: str, company_name: str | None) -> bool:
    """True when a paragraph addresses the company rather than describing it.

    A letter that switches to "Polaris has already committed real capital"
    mid-page stops being a letter and becomes a file someone forgot to
    anonymise. Paragraphs that do this are dropped rather than rewritten: the
    company name sits in subject position, so swapping in "you" leaves the verb
    behind it wrong, and "You has already committed" is worse than one fewer
    paragraph.
    """
    lowered = para.lower()
    return not any(
        re.search(rf"\b{re.escape(word)}\b", lowered) for word in company_words(company_name)
    )


def leave_behind_paragraphs(
    thesis_body: str, company_name: str | None, limit: int = 4,
) -> list[str]:
    """The analysis as a stranger should receive it.

    Four filters, in order: strip our notation, drop anything too short to be
    prose, drop anything that talks about the reader in the third person, and
    then refuse to end on an unanswered problem.
    """
    first = re.split(r"\n(?=#{1,3} )", thesis_body or "")
    body = next((c for c in first if c.strip() and not c.strip().startswith("## Diagnosis")),
                first[0] if first else "")
    cleaned = re.sub(r"\[[a-z0-9_.\[\]]+\]", "", body)       # strip claim ids
    cleaned = re.sub(r"[#*]+", "", cleaned)

    kept = [
        para.strip() for para in cleaned.split("\n\n")
        if para.strip() and len(para.split()) > 12
        and speaks_to_the_reader(para, company_name)
    ][:limit]

    # Trailing problem statements are cut, not padded. Better a shorter letter
    # than one that ends by naming a difficulty and walking away from it.
    while kept and not ends_with_a_thought(kept[-1]):
        kept.pop()
    return kept


def build_leave_behind(prospect: dict, artifacts: list[dict], out: Path) -> Path:
    """Two pages, prospect-facing. No tiers, no verdicts, no internal words."""
    available = [a for a in artifacts if a.get("kind") == "thesis" and a.get("body")]
    if not available:
        raise NoThesis(
            f"{prospect.get('company_name')} has no thesis, so there is nothing to leave "
            f"behind but a flyer. Generate the analysis first."
        )
    # The leave-behind is the one artifact a company physically holds, so it
    # may only be built from analysis that actually cleared the gate. A thesis
    # used to sit at 'draft' forever, which meant this page was assembled from
    # prose nothing had ever passed or failed.
    thesis = next((a for a in available if a.get("status") == "sendable"), None)
    if not thesis:
        statuses = ", ".join(sorted({str(a.get("status")) for a in available}))
        raise NoThesis(
            f"{prospect.get('company_name')}'s thesis did not pass the gate "
            f"(status: {statuses}), so it may not be handed to them. Redraft it."
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
    # An empty section is better than an apologetic one. "We could not confirm
    # much from your site" tells a company we researched them and came up
    # short, on the page that is supposed to show we did the work.
    shown = presentable_claims(prospect)
    flow = [
        Paragraph(f"Notes for {name}", st["title"]),
        Paragraph(datetime.now(UTC).strftime("%d %B %Y"), st["sub"]),
        Spacer(1, 14),
        Paragraph(lead_sentence(bool(shown)), st["lead"]),
    ]
    if shown:
        flow.append(Paragraph("What we read about you", st["h1"]))
        for _path, claim in shown[:8]:
            value = trim_to_sentence(claim.get("value"), 320)
            if not value:
                continue
            flow.append(Paragraph(_mini_html(value), st["body"]))
            flow.append(Paragraph(esc(claim.get("source_url"), 120), st["src"]))

    awards = grant_awards(prospect)
    story = grant_story(awards)
    if story:
        flow.append(Paragraph("Your grant" if len(awards) == 1 else "Your grants", st["h1"]))
        flow.append(Paragraph(_mini_html(story), st["body"]))

    paragraphs = leave_behind_paragraphs(thesis["body"], prospect.get("company_name"))
    reconcile_grant_money(" ".join(paragraphs), awards)
    reconcile_grant_count(" ".join(paragraphs), awards)
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
        trimmed = trim_to_sentence(para, 900)
        if trimmed:
            flow.append(Paragraph(_mini_html(trimmed), st["body"]))

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
    rows = db.list_prospects_full(getattr(args, "adapter", None))
    if args.company:
        needle = args.company.lower()
        rows = [p for p in rows if needle in (p.get("company_name") or "").lower()]
        if not rows:
            raise SystemExit(f"no company matching {args.company!r}")
        return rows[:1]
    wanted = tuple(
        p.strip().upper() for p in (args.priority or "P1").split(",") if p.strip())
    if getattr(args, "ranked", False):
        # The same order the analyst worked them in. A dossier whose order
        # disagreed with the run that produced it would be two answers to one
        # question, and the reader would have no way to know which was current.
        return shortlist.ranked(rows, wanted)[: args.limit]
    rows = [p for p in rows if p.get("priority") in wanted]
    rows.sort(key=lambda p: (
        (p.get("drive_minutes") or 999) > 90,
        -(p.get("signal_score") or 0),
        p.get("drive_minutes") or 999,
    ))
    return rows[: args.limit]


SHORTLIST_TITLES = {10: "The Ten", 50: "The Fifty"}


def build_the_ten(args, console: Console, target: int = theten.TARGET) -> int:
    """Assemble the shortlist and say plainly how many were actually ready."""
    prospects = db.list_prospects_full(getattr(args, "adapter", None))
    wanted = {p["id"] for p in prospects}
    artifacts_by: dict[str, list] = {}
    for artifact in db.all_artifacts():
        if artifact["prospect_id"] in wanted:
            artifacts_by.setdefault(artifact["prospect_id"], []).append(artifact)
    for rows in artifacts_by.values():
        rows.sort(key=lambda a: a["created_at"], reverse=True)

    title = SHORTLIST_TITLES.get(target, f"The top {target}")
    candidates = theten.build(prospects, artifacts_by)
    ten = theten.the_ten(candidates, target)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    slug = title.lower().replace(" ", "-")
    out = Path(args.out) if args.out else OUT_DIR / f"{slug}-{stamp}.pdf"
    build_ten(candidates, artifacts_by, out, target, title)

    console.print(f"[green]wrote[/green] {out}")
    console.print(f"Scope: [bold]{adapters.words(getattr(args, 'adapter', None))}"
                  f"[/bold] · {len(candidates)} ranked candidate(s)")
    if len(ten) < target:
        console.print(
            f"[yellow]{len(ten)} of {target} companies are ready.[/yellow] "
            f"Nothing was loosened to reach {target}.")
    else:
        console.print(f"[green]{len(ten)} companies are ready.[/green]")
    for words, count in sorted(theten.reason_counts(candidates).items(),
                               key=lambda kv: -kv[1]):
        console.print(f"  {count:>3} — {words}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate PDF prospect dossiers.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--priority", default="P1")
    parser.add_argument("--company", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--ranked", action="store_true",
                        help="order companies the way the analyst worked them: "
                             "signal, then reachability, then evidence")
    parser.add_argument("--ten", action="store_true",
                        help="the ranked list of companies that are ready to "
                             "contact, then each of them in full")
    parser.add_argument("--fifty", action="store_true",
                        help="the same document over a longer queue: fifty "
                             "rather than ten, qualification unchanged")
    adapters.add_argument(parser)
    args = parser.parse_args()

    console = Console()
    if args.ten or args.fifty:
        return build_the_ten(args, console, 50 if args.fifty else theten.TARGET)
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

    build_dossier(prospects, artifacts_by, out, scope,
                  canary.read_state().allowed_verdicts())
    console.print(f"[green]wrote[/green] {out}  ({len(prospects)} compan"
                  f"{'y' if len(prospects) == 1 else 'ies'})")

    if args.company:
        leave = out.with_name(out.stem + "-leave-behind.pdf")
        try:
            build_leave_behind(prospects[0], artifacts_by[prospects[0]["id"]], leave)
            console.print(f"[green]wrote[/green] {leave}")
        except (NoThesis, GrantFiguresDisagree) as exc:
            console.print(f"[yellow]no leave-behind:[/yellow] {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
