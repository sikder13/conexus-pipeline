"""Thesis drafter and outbound artifacts — generation behind a structural gate.

    python -m tools.drafter --dry-run          # show what would be drafted
    python -m tools.drafter --limit 10         # draft the top 10 P1s

WHAT CHANGED, AND WHY THE GATE IS LOAD-BEARING

Outreach now runs without a human checking each claim first. Nobody reads the
source before the sentence goes out. Everything that used to depend on that
reading now depends on this file refusing to emit sentences it cannot map back
to a qualifying claim.

So the formula is not a style guide here; it is enforced by construction. The
generator is handed a pre-selected set of claims that already qualify, and then
an independent audit pass reads the finished text and maps every factual
sentence back to one. A sentence that maps to nothing blocks the artifact. Two
regeneration attempts, then it goes to the operator — never out.

THE TWO-STEP THESIS, AND WHY THEY ARE SEPARATED

Step 1 sees the evidence and no pattern library. It names the most expensive
frictions the evidence supports, whatever they are. Step 2 then costs what step
1 found, using the library as arithmetic.

Merging them would be cheaper and much worse: a model shown a list of things we
can build will find the nearest one and write a diagnosis backwards from it. The
separation is what keeps the diagnosis open — step 1 cannot pattern-match to a
menu it has not been shown.

WHAT IS DELIBERATELY NOT HERE

There is no send path. None may be built in this task, and the canary halt
exists so that when one is built it has something to check.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from typing import Any

import anthropic
from rich.console import Console
from rich.table import Table

from lib import canary, db
from lib.claimcheck import is_barred
from lib.claims import Tier
from lib.evidence import BLOCKS
from lib.integrity import evidence_integrity, is_usable, iter_all_claims
from lib.persongate import salutation_for
from lib.roi_patterns import applicable, as_prompt_block

THESIS_MODEL = "claude-sonnet-4-6"
TEMPERATURE = 0.2
MAX_ATTEMPTS = 2

SENDER_NAME = "Udaay Sikder"
SENDER_COMPANY = "Nahl Technologies"
SENDER_ADDRESS = "6902 Challenge Ln, Indianapolis IN 46250"
OPT_OUT = "Reply STOP and I will not contact you again."

CITATION = re.compile(r"\[([a-z0-9_]+(?:\.[a-z0-9_\[\]]+)+)\]")
"""A claim reference in generated text, e.g. [block2_grant_funded.grant_amount]."""

NUMBER_IN_TEXT = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?%?")

HYPOTHESIS_MARKERS = (
    "we think", "our hypothesis", "we suspect", "if that is right",
    "we would guess", "our guess", "we may be wrong",
)
"""Template language a T4 claim must be wrapped in. A hypothesis that does not
announce itself is an assertion."""


# ------------------------------------------------------------------ selection

def qualifying_claims(prospect: dict[str, Any]) -> list[tuple[str, dict]]:
    """Claims a draft may cite at all: usable, not barred, inside a real block."""
    out = []
    for path, claim in iter_all_claims(prospect.get("evidence_file") or {}):
        trimmed = path.removeprefix("evidence_file.")
        if trimmed.split(".")[0] not in BLOCKS:
            continue
        if not is_usable(claim) or is_barred(claim):
            continue
        out.append((trimmed, claim))
    return out


def assertable_claims(
    prospect: dict[str, Any], verdicts: tuple[str, ...]
) -> list[tuple[str, dict]]:
    """Claims strong enough to state as fact in outbound text.

    T1 AND (corroborated OR an allowed checker verdict). This is the DATA-1
    formula's "2-3 facts" pool, enforced by selection rather than by asking the
    model nicely.
    """
    return [
        (path, claim) for path, claim in qualifying_claims(prospect)
        if claim.get("tier") == int(Tier.T1)
        and (claim.get("corroborated") is True or claim.get("claimcheck") in verdicts)
    ]


def hypothesis_claims(prospect: dict[str, Any]) -> list[tuple[str, dict]]:
    """T4 inferences — exactly one of which may appear, clearly labelled."""
    return [
        (path, claim) for path, claim in qualifying_claims(prospect)
        if claim.get("tier") == int(Tier.T4)
    ]


def render_claims(claims: list[tuple[str, dict]]) -> str:
    """Claims as prompt lines, carrying everything the model must respect."""
    lines = []
    for path, claim in claims:
        marks = []
        if claim.get("corroborated"):
            marks.append(f"corroborated by {claim.get('corroborated_by')}")
        if claim.get("claimcheck"):
            marks.append(f"checker: {claim['claimcheck']}")
        if claim.get("conflict"):
            marks.append("CONFLICTED — sources disagree, do not assert")
        suffix = f"  [{'; '.join(marks)}]" if marks else ""
        lines.append(
            f"[{path}] (T{claim.get('tier')}) {claim.get('value')}"
            f"  <source: {claim.get('source_url')}>{suffix}"
        )
    return "\n".join(lines)


# -------------------------------------------------------------------- prompts

STEP1_SYSTEM = (
    "You are a manufacturing operations analyst reading a research file on one "
    "company. Name the most EXPENSIVE frictions this evidence supports.\n\n"
    "Rules:\n"
    "1. Diagnose from the evidence only. You have not been shown any list of "
    "services and must not guess at one. Whatever the evidence supports is the "
    "answer, even if it is unglamorous.\n"
    "2. Every factual sentence must end with a claim reference in square "
    "brackets, exactly as given to you, e.g. [block3_hiring_signals.open_roles]. "
    "A sentence with no reference will be discarded.\n"
    "3. Do not propose solutions. Not yet. Name problems and what they plausibly "
    "cost, and say which evidence makes you think so.\n"
    "4. If the evidence is thin, say so and name fewer frictions. Two well-"
    "evidenced frictions beat five speculative ones.\n\n"
    "Output 2-4 frictions. For each: the observation with citations, why it is "
    "expensive, and what you would need to know to size it."
)

STEP2_SYSTEM = (
    "You are costing frictions that have already been diagnosed, and scoping "
    "bounded fixes for them.\n\n"
    "You are given MATH TEMPLATES. They are arithmetic, not a menu of services. "
    "Use one only where the diagnosis already found the matching problem and the "
    "evidence carries the variables it needs. Each template lists what must be "
    "observed and when it must NOT be used — obey both.\n\n"
    "Rules:\n"
    "1. Use THEIR numbers first. A grant match amount is capital they have "
    "already committed and is a T1 floor on their own investment — the strongest "
    "anchor available. Industry averages are a last resort and must be labelled.\n"
    "2. Every figure is a conditional range with its assumptions stated inline: "
    "'if quotes run about 40 a month, then...'. Never a point estimate.\n"
    "3. Every factual sentence carries its claim reference in square brackets.\n"
    "4. Scoped fixes are two to four weeks of work. Not a platform, not a "
    "retainer, not a transformation.\n"
    "5. Confidence per opportunity: high / medium / low, with the reason.\n\n"
    "Then add:\n"
    "- ANTI-PITCH: what NOT to say to this company, drawn from the evidence "
    "(what they already do well, what would sound ignorant).\n"
    "- DISCOVERY QUESTIONS: the gaps, as questions to ask on a call."
)

EMAIL_SYSTEM = (
    "Write a first-contact email and a three-finding brief for a manufacturer.\n\n"
    "The email is short — under 150 words. It states two or three specific, "
    "sourced facts about their business, offers exactly one clearly-labelled "
    "hypothesis, and shows one piece of checkable arithmetic naming its sources "
    "inline. It asks for a short conversation. It does not pitch a service.\n\n"
    "Every factual sentence must carry its claim reference in square brackets. "
    "The hypothesis sentence must use hedging language ('we think', 'our "
    "hypothesis is', 'if that is right') so a reader cannot mistake it for a "
    "fact. There must be exactly ONE hypothesis in the whole email.\n\n"
    "Then a BRIEF: three findings, each with its evidence and citation, and the "
    "arithmetic laid out so the reader can check it.\n\n"
    "Output as JSON: {\"subject\": \"...\", \"email\": \"...\", \"brief\": \"...\"}"
)


async def _call(client: Any, system: str, prompt: str, max_tokens: int = 2000) -> str:
    response = await client.messages.create(
        model=THESIS_MODEL,
        max_tokens=max_tokens,
        temperature=TEMPERATURE,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return " ".join(
        b.text for b in response.content if getattr(b, "type", "") == "text"
    ).strip()


# --------------------------------------------------------------------- gate

def factual_sentences(text: str) -> list[str]:
    """Sentences that assert something about the world.

    Questions, headings and pure calls-to-action are not factual assertions and
    are not required to carry a citation. Everything else is.
    """
    out = []
    for raw in re.split(r"(?<=[.!?])\s+", text or ""):
        sentence = raw.strip()
        if not sentence or sentence.endswith("?"):
            continue
        if len(sentence.split()) < 4:
            continue
        if re.fullmatch(r"[A-Z0-9 \-—:]+", sentence):
            continue
        out.append(sentence)
    return out


def gate_artifact(
    text: str,
    allowed_paths: set[str],
    hypothesis_paths: set[str],
    person_allowed: bool,
    person_name: str | None,
) -> dict[str, Any]:
    """Map every factual sentence back to a qualifying claim. Returns the verdict.

    This is an independent pass over the FINISHED text. It does not trust the
    generator's intentions, only what the generator actually wrote — which is
    the whole point, because the generator is the thing that might be wrong.
    """
    failures: list[str] = []
    mapping: list[dict[str, Any]] = []
    hypothesis_count = 0

    for sentence in factual_sentences(text):
        cites = CITATION.findall(sentence)
        known = [c for c in cites if c in allowed_paths]
        entry = {"sentence": sentence[:300], "claims": known}
        is_hypothesis = any(m in sentence.lower() for m in HYPOTHESIS_MARKERS)
        if is_hypothesis:
            hypothesis_count += 1
            entry["hypothesis"] = True

        if not cites:
            # A sentence with a number and no citation is the exact failure this
            # gate exists for: a figure that looks sourced because everything
            # around it is.
            if NUMBER_IN_TEXT.search(sentence):
                failures.append(f"number with no source: {sentence[:120]!r}")
            elif not is_hypothesis:
                failures.append(f"unmappable factual sentence: {sentence[:120]!r}")
        else:
            unknown = [c for c in cites if c not in allowed_paths]
            if unknown:
                failures.append(
                    f"cites a claim that does not qualify ({', '.join(unknown[:2])}): "
                    f"{sentence[:100]!r}"
                )
        mapping.append(entry)

    if hypothesis_count > 1:
        failures.append(
            f"{hypothesis_count} hypotheses present; the formula allows exactly one"
        )

    cited_hypotheses = {
        c for entry in mapping for c in entry["claims"] if c in hypothesis_paths
    }
    if len(cited_hypotheses) > 1:
        failures.append(
            f"cites {len(cited_hypotheses)} T4 claims; only one hypothesis is allowed"
        )

    if (not person_allowed and person_name
            and re.search(rf"\b{re.escape(person_name.split()[0])}\b", text)):
        failures.append(
            f"uses a person's name that failed the person gate: {person_name!r}"
        )

    return {
        "passed": not failures,
        "failures": failures,
        "map": mapping,
        "sentences": len(mapping),
        "cited": sorted({c for e in mapping for c in e["claims"]}),
    }


# ------------------------------------------------------------------ generation

async def draft_prospect(
    prospect: dict[str, Any], client: Any, verdicts: tuple[str, ...]
) -> dict[str, Any]:
    """Produce the thesis, email and brief for one prospect, gated."""
    facts = assertable_claims(prospect, verdicts)
    hypotheses = hypothesis_claims(prospect)
    all_qualifying = qualifying_claims(prospect)
    allowed = {p for p, _ in all_qualifying}
    hypothesis_paths = {p for p, _ in hypotheses}

    salutation, gate_result = salutation_for(prospect)
    person_allowed = bool(gate_result and gate_result.allowed)
    person_name = gate_result.name if gate_result else None

    header = (
        f"COMPANY: {prospect.get('company_name')}\n"
        f"County: {prospect.get('county')} · about "
        f"{prospect.get('drive_minutes')} minutes from Muncie\n"
        f"Industry (from the grant listing, T1): {prospect.get('industry_desc')}\n"
    )
    evidence_block = render_claims(all_qualifying)

    step1 = await _call(
        client, STEP1_SYSTEM,
        f"{header}\nEVIDENCE:\n{evidence_block}",
        max_tokens=1600,
    )
    step2 = await _call(
        client, STEP2_SYSTEM,
        f"{header}\nEVIDENCE:\n{evidence_block}\n\n"
        f"DIAGNOSIS FROM STEP 1:\n{step1}\n\n"
        f"MATH TEMPLATES (arithmetic, not a service menu):\n"
        f"{as_prompt_block(applicable(evidence_block))}",
        max_tokens=2600,
    )
    thesis = f"## Diagnosis\n\n{step1}\n\n## Opportunities, costed\n\n{step2}"

    fact_lines = render_claims(facts[:8])
    hyp_lines = render_claims(hypotheses[:3])
    email_raw = await _call(
        client, EMAIL_SYSTEM,
        f"{header}\n"
        f"Greeting must address: {salutation}\n"
        f"(the person gate {'passed' if person_allowed else 'FAILED — do not use any name'})\n\n"
        f"FACTS YOU MAY ASSERT (all T1 and corroborated or checker-verbatim):\n"
        f"{fact_lines or '(none qualify — say less)'}\n\n"
        f"HYPOTHESES — choose exactly ONE, hedged:\n{hyp_lines or '(none available)'}\n\n"
        f"THESIS FOR CONTEXT:\n{thesis[:4000]}",
        max_tokens=1800,
    )

    subject, email_body, brief = _split_email(email_raw)
    email_body = _append_can_spam(email_body)

    email_gate = gate_artifact(email_body, allowed, hypothesis_paths,
                               person_allowed, person_name)
    brief_gate = gate_artifact(brief, allowed, hypothesis_paths,
                               person_allowed, person_name)

    return {
        "thesis": thesis,
        "subject": subject,
        "email": email_body,
        "brief": brief,
        "email_gate": email_gate,
        "brief_gate": brief_gate,
        "salutation": salutation,
        "person_allowed": person_allowed,
        "facts_available": len(facts),
    }


def _split_email(raw: str) -> tuple[str, str, str]:
    """Pull subject/email/brief out of the model's JSON, tolerating stray prose."""
    match = re.search(r"\{.*\}", raw or "", re.S)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return (
                str(parsed.get("subject") or "").strip(),
                str(parsed.get("email") or "").strip(),
                str(parsed.get("brief") or "").strip(),
            )
        except (ValueError, TypeError):
            pass
    return "", (raw or "").strip(), ""


def _append_can_spam(body: str) -> str:
    """Real identity, a physical address and a working opt-out on every email."""
    if SENDER_ADDRESS in body:
        return body
    return (
        f"{body}\n\n--\n{SENDER_NAME}\n{SENDER_COMPANY}\n{SENDER_ADDRESS}\n{OPT_OUT}"
    )


# ------------------------------------------------------------------------ CLI

def eligible_prospects(limit: int | None) -> list[dict[str, Any]]:
    """P1 prospects passing integrity, nearest first, never one under verification."""
    locked = {s["prospect_id"] for s in db.open_sessions()}
    rows = [
        p for p in db.list_prospects_full()
        if p.get("priority") == "P1"
        and p["id"] not in locked
        and evidence_integrity(p).passing
    ]
    rows.sort(key=lambda p: (
        (p.get("drive_minutes") or 999) > 90,
        -(p.get("signal_score") or 0),
        p.get("drive_minutes") or 999,
    ))
    return rows[:limit] if limit else rows


async def _run(limit: int | None, dry_run: bool, console: Console) -> int:
    state = canary.read_state()
    if state.halted:
        console.print(f"[red]Pipeline is halted: {state.halt_reason}[/red]")
        console.print("Drafting is allowed while halted, but nothing may be sent.")
    verdicts = state.allowed_verdicts()
    console.print(
        f"Canary: batch {state.batches_sent}, assertable verdicts {verdicts}, "
        f"halted={state.halted}\n"
    )

    rows = eligible_prospects(limit)
    table = Table(title=f"{len(rows)} prospect(s) eligible for drafting",
                  title_justify="left")
    for column in ("Company", "Score", "Drive", "Assertable facts", "Person gate"):
        table.add_column(column)
    for p in rows:
        facts = assertable_claims(p, verdicts)
        _sal, gate = salutation_for(p)
        table.add_row(
            str(p.get("company_name"))[:38],
            str(p.get("signal_score")),
            f"{p.get('drive_minutes')}m",
            str(len(facts)),
            "named" if (gate and gate.allowed) else "role only",
        )
    console.print(table)

    if dry_run:
        console.print("\n[dim]--dry-run: nothing generated, nothing written.[/dim]")
        return 0

    from lib.config import settings
    if not settings.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY is not set; cannot draft.[/red]")
        return 1

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    for prospect in rows:
        console.print(f"\n[cyan]drafting {prospect.get('company_name')}[/cyan]")
        attempt, result = 1, None
        while attempt <= MAX_ATTEMPTS:
            result = await draft_prospect(prospect, client, verdicts)
            if result["email_gate"]["passed"] and result["brief_gate"]["passed"]:
                break
            console.print(
                f"  [yellow]attempt {attempt} blocked:[/yellow] "
                + "; ".join(result["email_gate"]["failures"][:2]
                            + result["brief_gate"]["failures"][:2])
            )
            attempt += 1
        passed = result["email_gate"]["passed"] and result["brief_gate"]["passed"]
        status = "sendable" if passed else "blocked"
        for kind, body, gate in (
            ("thesis", result["thesis"], None),
            ("email", result["email"], result["email_gate"]),
            ("brief", result["brief"], result["brief_gate"]),
        ):
            db.insert_artifact({
                "prospect_id": prospect["id"],
                "kind": kind,
                "status": status if kind != "thesis" else "draft",
                "body": body,
                "gate_map": (gate or {}).get("map"),
                "gate_failures": (gate or {}).get("failures"),
                "claims_cited": (gate or {}).get("cited"),
                "attempts": attempt if attempt <= MAX_ATTEMPTS else MAX_ATTEMPTS,
                "model": THESIS_MODEL,
            })
        console.print(
            f"  [{'green' if passed else 'red'}]{status}[/] after {min(attempt, MAX_ATTEMPTS)} "
            f"attempt(s)"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Draft theses and gated outbound artifacts for P1 prospects."
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be drafted; generate nothing")
    args = parser.parse_args()
    return asyncio.run(_run(args.limit, args.dry_run, Console()))


if __name__ == "__main__":
    raise SystemExit(main())
