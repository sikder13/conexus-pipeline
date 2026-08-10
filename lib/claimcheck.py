"""Adversarial claim checker — a second model whose job is to find us wrong.

WHY A SEPARATE CALL

The model that writes a thesis is invested in it. Asking the same call to also
police itself produces agreement, because the check is downstream of the
argument it is checking. This is a fresh call with no knowledge of the draft, no
knowledge of why the claim matters, and exactly one question in front of it:
does this source text support this sentence?

The prompt tells it that it is auditing, that finding the claim wrong is the
useful outcome, and that "close enough" is not support. That framing is doing
real work — the same model asked to "verify" a claim will verify it.

THE THREE VERDICTS

* ``verbatim``   — the source states this, in these terms. Quotable as fact.
* ``inferable``  — the source supports it but a reader has to take a small step.
                   Usable, but never as a quoted fact and (until the canary
                   protocol opens it up) never in outbound text at all.
* ``unsupported``— the source does not carry this. Structurally barred from
                   every outbound artifact.

WHAT IT CANNOT DO

It cannot tell us a claim is true — only whether *this text* supports it. A
hijacked page confidently supports a claim about a gambling site; that is a job
for the integrity gate, which runs first. And it sees a truncated slice of long
pages, so 'unsupported' on a very long source can mean "not in the part I was
shown". The verdict is recorded with the byte range checked so that is legible.
"""

from __future__ import annotations

import json
import re
from typing import Any

import anthropic
from pydantic import BaseModel

from lib.config import settings

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 300
MAX_SOURCE_CHARS = 14000
"""How much source text the checker sees.

Long enough for a normal page, short enough to keep the check cheap — this runs
per claim. The slice actually examined is recorded on the verdict, because
'unsupported' means something different when only part of a document was read."""

VERDICTS = ("verbatim", "inferable", "unsupported")

SYSTEM_PROMPT = (
    "You are auditing a sales-research pipeline. Your job is to find its claims "
    "WRONG.\n\n"
    "You will be given one claim and the text of the single source it cites. "
    "Answer one question: does this source text support this claim?\n\n"
    "You are not helping to write anything. You are not judging whether the "
    "claim is plausible, or true in the world, or useful. Only whether THIS TEXT "
    "says it. A claim that is probably true but absent from the text is "
    "unsupported, and saying so is the useful answer.\n\n"
    "Verdicts:\n"
    "- verbatim: the source states this in these terms. A reader would find the "
    "same fact in the same words or an unambiguous paraphrase.\n"
    "- inferable: the source supports it but the reader must take a step — "
    "combining two sentences, or reading an implication.\n"
    "- unsupported: the text does not carry this claim. Includes claims that are "
    "close but differ in a detail that matters (a different number, a different "
    "person, a different year), and claims about things the text never mentions.\n\n"
    "'Close enough' is unsupported. A number that differs is unsupported. A name "
    "the text does not contain is unsupported.\n\n"
    "Reply with one line of JSON and nothing else:\n"
    '{"verdict": "verbatim|inferable|unsupported", "reason": "one short sentence", '
    '"quote": "the supporting span from the source, or empty"}'
)


class CheckResult(BaseModel):
    """One adversarial verdict on one claim."""

    verdict: str = "unsupported"
    reason: str = ""
    quote: str = ""
    checked_chars: int = 0
    model: str = MODEL

    @property
    def usable_in_outbound(self) -> bool:
        """Only 'verbatim' may be asserted outbound before the canary opens up."""
        return self.verdict == "verbatim"

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()


class CheckerUnavailable(RuntimeError):
    """No API key, so no adversarial check is possible."""


def _parse(text: str) -> CheckResult:
    """Read the model's reply. An unreadable reply is 'unsupported', not a crash.

    Failing closed is the only safe default here: an unparseable verdict must
    not be allowed to read as approval.
    """
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return CheckResult(verdict="unsupported", reason="checker returned no verdict")
    try:
        parsed = json.loads(match.group(0))
    except (ValueError, TypeError):
        return CheckResult(verdict="unsupported", reason="checker verdict was unreadable")
    verdict = str(parsed.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        return CheckResult(
            verdict="unsupported", reason=f"checker returned unknown verdict {verdict!r}"
        )
    return CheckResult(
        verdict=verdict,
        reason=str(parsed.get("reason") or "")[:400],
        quote=str(parsed.get("quote") or "")[:600],
    )


def build_prompt(claim: dict[str, Any], source_text: str, label: str = "") -> str:
    """The user half of the checker prompt."""
    body = (source_text or "")[:MAX_SOURCE_CHARS]
    return (
        f"CLAIM ({label or 'unlabelled'}, tier {claim.get('tier')}):\n"
        f"{claim.get('value')!r}\n\n"
        f"CITED SOURCE: {claim.get('source_url')}\n"
        f"SOURCE TEXT (may be truncated):\n{body}"
    )


async def check_claim(
    claim: dict[str, Any],
    source_text: str,
    label: str = "",
    client: Any = None,
) -> CheckResult:
    """Ask an independent model whether ``source_text`` supports ``claim``."""
    if not settings.anthropic_api_key and client is None:
        raise CheckerUnavailable("ANTHROPIC_API_KEY is not set")
    if not (source_text or "").strip():
        return CheckResult(
            verdict="unsupported", reason="no source text was available to check against"
        )

    api = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await api.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(claim, source_text, label)}],
    )
    text = " ".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    result = _parse(text)
    result.checked_chars = min(len(source_text or ""), MAX_SOURCE_CHARS)
    return result


def apply_verdict(claim: dict[str, Any], result: CheckResult) -> dict[str, Any]:
    """Return a copy of ``claim`` carrying the checker's verdict.

    Stored flat on the claim so every downstream reader — the drafter, the gate,
    the console, the audit — sees the same verdict without needing to know how
    it was produced.
    """
    checked = dict(claim)
    checked["claimcheck"] = result.verdict
    checked["claimcheck_reason"] = result.reason
    checked["claimcheck_model"] = result.model
    if result.quote:
        checked["claimcheck_quote"] = result.quote
    return checked


def is_barred(claim: Any) -> bool:
    """True when a claim may not appear in any outbound artifact.

    Barred for any of: an 'unsupported' verdict, taint, or a human kill. The
    absence of a verdict is NOT a bar here — Layer 4 decides separately which
    claims require a verdict before use, and conflating the two would silently
    bar everything the checker has not reached yet.
    """
    from lib.integrity import is_killed, is_tainted

    if not isinstance(claim, dict):
        return True
    return (
        claim.get("claimcheck") == "unsupported"
        or is_tainted(claim)
        or is_killed(claim)
    )
