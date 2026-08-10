"""summary — a short orientation paragraph for the human who verifies the record.

The Verifier CLI shows one prospect at a time. Before a person starts checking
claims they need thirty seconds of orientation: what this company makes, what
the grant bought, the strongest reason we are looking at them, and the biggest
thing we still do not know. That is what this node writes into
``machine_summary``.

WHY IT ONLY READS T1 AND T2

The task calls for "verified-tier evidence only". Nothing is verified=True at
this point — verification is the human step this paragraph exists to prepare —
so the tier is what "verified-tier" can mean here: T1 (the company's own words
or a government record) and T2 (reputable secondary press). T3 aggregator
estimates and T4 inferences are deliberately withheld from the model. If a
headcount guess or a drive-time estimate is not in the prompt, it cannot end up
in the paragraph, and a summary that quietly launders a T4 guess into prose a
human then skims is precisely the failure this pipeline is built to prevent.

The prompt tells the model to state only what the evidence contains and to name
gaps rather than fill them. That instruction is necessary but not sufficient —
withholding the low-tier claims is what actually makes it true.

MODEL NOTE

claude-haiku-4-5-20251001 is a pre-4.6 model: it does not accept the `effort`
parameter or adaptive thinking, both of which error on it. A plain request is
correct here, and right for the job — this is short summarisation from supplied
text, the cheapest thing the API does.

Absent ANTHROPIC_API_KEY the node skips cleanly. A pipeline that cannot draft
prose is still a pipeline that has done its research; refusing to run over it
would be worse than the missing paragraph.
"""

from __future__ import annotations

from typing import Any, ClassVar

import anthropic

from lib.claims import Tier
from lib.config import settings
from lib.evidence import BLOCKS, FLAGS_KEY, read_block
from lib.nodes import Node, NodeResult, RunContext, register

MODEL = "claude-haiku-4-5-20251001"
MAX_WORDS = 120
MAX_TOKENS = 400
ASSERTABLE_TIERS = (int(Tier.T1), int(Tier.T2))
DEFAULT_PRIORITIES: tuple[str, ...] = ("P1", "P2")

SYSTEM_PROMPT = (
    "You write short orientation notes for a researcher who is about to verify a "
    "sales prospect's file by hand.\n\n"
    "Rules, in order of importance:\n"
    "1. State ONLY what the supplied evidence contains. Do not add industry "
    "knowledge, do not infer, and do not estimate. If you are tempted to write "
    "something the evidence does not say, leave it out.\n"
    "2. Name the gaps explicitly. Saying 'the file records no headcount and no "
    "named contact' is more useful than a confident paragraph that quietly omits "
    "them. Finish with the single biggest unknown.\n"
    "3. Quotations must be reproduced exactly or not at all. Never paraphrase a "
    "quote into a stronger claim.\n"
    f"4. One paragraph, at most {MAX_WORDS} words, plain prose. No headings, no "
    "bullet points, no preamble such as 'Here is a summary'.\n\n"
    "Cover, in this order: what the company makes, what the grant funded, the "
    "strongest signal in their favour, and the biggest unknown."
)


def _claim_lines(evidence: dict[str, Any]) -> list[str]:
    """Render every T1/T2 claim as one readable line, skipping flags and low tiers."""
    lines: list[str] = []
    for block in BLOCKS:
        for key, value in read_block(evidence, block).items():
            if key == FLAGS_KEY:
                continue
            for entry in value if isinstance(value, list) else [value]:
                if not isinstance(entry, dict) or "value" not in entry:
                    continue
                if entry.get("tier") not in ASSERTABLE_TIERS:
                    continue
                marker = " [verbatim quote]" if entry.get("quote") else ""
                lines.append(f"- {block}.{key} (T{entry.get('tier')}){marker}: {entry['value']}")
    return lines


def build_prompt(prospect: dict[str, Any]) -> tuple[str, list[str]]:
    """Return the user prompt and the list of fields the evidence does not carry."""
    evidence = prospect.get("evidence_file") or {}
    lines = _claim_lines(evidence)

    absent = [
        label
        for label, present in (
            ("grant amount", prospect.get("grant_amount")),
            ("employee count", prospect.get("employee_estimate")),
            ("city", prospect.get("city")),
            ("website", prospect.get("website")),
        )
        if not present
    ]

    header = [
        f"Company: {prospect.get('company_name')}",
        f"County: {prospect.get('county') or 'unknown'}",
        f"Signal score: {prospect.get('signal_score')} (priority {prospect.get('priority')})",
    ]
    body = [
        "\n".join(header),
        "",
        "Evidence (tier 1 and tier 2 only — lower-tier estimates are deliberately "
        "withheld and must not be guessed at):",
        "\n".join(lines) if lines else "- (no tier 1 or tier 2 claims recorded)",
    ]
    if absent:
        body += ["", f"The file records no: {', '.join(absent)}."]
    return "\n".join(body), absent


@register
class SummaryNode(Node):
    """Draft the one-paragraph orientation note for high-priority prospects."""

    name: ClassVar[str] = "summary"
    depends_on: ClassVar[tuple[str, ...]] = ("score",)

    #: Priorities this node runs for. Set to None to summarise every prospect;
    #: `python -m tools.runner --summarize-all` does exactly that.
    include_priorities: tuple[str, ...] | None = DEFAULT_PRIORITIES

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        if not settings.anthropic_api_key:
            return NodeResult(
                skipped=True,
                skip_reason=(
                    "ANTHROPIC_API_KEY is not set; drafting is unavailable. Every other "
                    "node's evidence is unaffected — set the key and re-run this node."
                ),
            )

        priority = prospect.get("priority")
        if self.include_priorities is not None and priority not in self.include_priorities:
            return NodeResult(
                skipped=True,
                skip_reason=(
                    f"priority {priority!r} is outside {list(self.include_priorities)}; "
                    f"run with --summarize-all to widen"
                ),
            )

        prompt, absent = build_prompt(prospect)
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        # No `effort` and no adaptive thinking: both error on this model, and
        # neither would help a summary written from text already in the prompt.
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        if response.stop_reason == "refusal":
            raise RuntimeError("model declined to summarise this record")
        text = " ".join(
            block.text.strip() for block in response.content if block.type == "text"
        ).strip()
        if not text:
            raise RuntimeError(f"model returned no text (stop_reason={response.stop_reason})")
        if response.stop_reason == "max_tokens":
            # The paragraph was cut mid-sentence by the token ceiling. Storing it
            # would file a half-finished thought as a finished one, and the half
            # that survives reads as complete — Decatur's summary was stored that
            # way. Fail instead: the runner retries, and a missing summary is
            # visible where a truncated one is not.
            raise RuntimeError(
                f"summary hit the {MAX_TOKENS}-token ceiling and was cut mid-sentence; "
                f"not stored (ended: ...{text[-60:]!r})"
            )

        words = len(text.split())
        notes = [
            f"summary drafted by {MODEL} from tier 1-2 evidence only ({words} words)",
            "lower-tier estimates were withheld from the prompt so they cannot reach the prose",
        ]
        if absent:
            notes.append(f"gaps stated to the model: {', '.join(absent)}")
        if words > MAX_WORDS:
            notes.append(f"summary ran to {words} words, over the {MAX_WORDS}-word target")

        return NodeResult(prospect_patch={"machine_summary": text}, notes=notes)
