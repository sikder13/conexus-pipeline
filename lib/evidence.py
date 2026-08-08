"""The eight evidence blocks, and the only sanctioned way to write into them.

DATA-1 organises everything we learn about a prospect into eight named blocks.
The blocks are not decoration: the Verifier shows them one at a time, the
Drafter cites them by name, and a prospect who corrects us in a reply is
correcting a specific block. So the names have to be stable and identical
everywhere, which is why they are defined here once and nowhere else.

Nodes never build a block path by hand. They call ``block_patch`` with a block
constant, and it fails loudly on a name that is not one of the eight — a typo
that would otherwise create a ninth block nobody reads.

Two things live alongside the claims:

*Flags* are the booleans the score node consumes. Each one is stored as a real
claim, with a tier and a source URL, inside the block that produced it. That
costs nothing and means a score component can always be traced back to the
observation behind it rather than to a bare True.

*Quotes* are claims carrying ``quote: true``. Somebody's exact words are the
most persuasive thing in an outreach email and the easiest thing to accidentally
strengthen: "we're drowning in paperwork" becomes "they have a paperwork
problem" becomes "they need our product". The marker exists so downstream code
can refuse to paraphrase.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lib.claims import Tier, make_claim

BLOCK1_WHAT_THEY_MAKE = "block1_what_they_make"
BLOCK2_GRANT_FUNDED = "block2_grant_funded"
BLOCK3_HIRING_SIGNALS = "block3_hiring_signals"
BLOCK4_DIGITAL_FRONT_DOOR = "block4_digital_front_door"
BLOCK5_CUSTOMER_FRICTION = "block5_customer_friction"
BLOCK6_TECH_STACK = "block6_tech_stack"
BLOCK7_PEOPLE = "block7_people"
BLOCK8_FINANCIAL_SCALE = "block8_financial_scale"

BLOCKS: tuple[str, ...] = (
    BLOCK1_WHAT_THEY_MAKE,
    BLOCK2_GRANT_FUNDED,
    BLOCK3_HIRING_SIGNALS,
    BLOCK4_DIGITAL_FRONT_DOOR,
    BLOCK5_CUSTOMER_FRICTION,
    BLOCK6_TECH_STACK,
    BLOCK7_PEOPLE,
    BLOCK8_FINANCIAL_SCALE,
)

FLAGS_KEY = "flags"
"""Reserved key inside a block holding that block's scoring flags."""

SCORE_EVIDENCE_KEY = "score_evidence"
"""Reserved top-level key: why each score component landed where it did."""

FLAG_BLOCKS: dict[str, str] = {
    "has_clerical_posting": BLOCK3_HIRING_SIGNALS,
    "data_gen_tech": BLOCK2_GRANT_FUNDED,
    "has_case_study": BLOCK2_GRANT_FUNDED,
    "weak_front_door": BLOCK4_DIGITAL_FRONT_DOOR,
    "friction_reviews": BLOCK5_CUSTOMER_FRICTION,
    "named_decision_maker": BLOCK7_PEOPLE,
    "too_big": BLOCK8_FINANCIAL_SCALE,
    "status_uncertain": BLOCK1_WHAT_THEY_MAKE,
}
"""Which block owns each scoring flag. A flag lives with the evidence for it."""


class EvidenceFile(BaseModel):
    """The shape of a prospect's evidence_file.

    ``extra='allow'`` on purpose: the extractor writes a ``source`` block and
    normalize_identity writes an ``identity`` block, both of which predate the
    eight-block structure and are still the provenance for county, website and
    drive time. Rejecting them would mean rewriting history to satisfy a model.
    """

    model_config = ConfigDict(extra="allow")

    block1_what_they_make: dict[str, Any] = Field(default_factory=dict)
    block2_grant_funded: dict[str, Any] = Field(default_factory=dict)
    block3_hiring_signals: dict[str, Any] = Field(default_factory=dict)
    block4_digital_front_door: dict[str, Any] = Field(default_factory=dict)
    block5_customer_friction: dict[str, Any] = Field(default_factory=dict)
    block6_tech_stack: dict[str, Any] = Field(default_factory=dict)
    block7_people: dict[str, Any] = Field(default_factory=dict)
    block8_financial_scale: dict[str, Any] = Field(default_factory=dict)
    notes: list[dict[str, Any]] = Field(default_factory=list)


def _assert_block(block: str) -> str:
    """Raise on anything that is not one of the eight blocks."""
    if block not in BLOCKS:
        raise ValueError(
            f"{block!r} is not an evidence block. Expected one of: {', '.join(BLOCKS)}"
        )
    return block


def block_patch(block: str, claims: dict[str, Any]) -> dict[str, Any]:
    """Build a node's evidence_patch for one block, dropping empty keys.

    Keys whose value is None are removed rather than written: an absent fact is
    recorded as an absent key plus a note, never as a null pretending to be a
    finding (DATA-1 rule 9).
    """
    _assert_block(block)
    populated = {key: value for key, value in claims.items() if value is not None}
    return {block: populated} if populated else {}


def merge_patches(*patches: dict[str, Any]) -> dict[str, Any]:
    """Combine several block patches into one evidence_patch."""
    merged: dict[str, Any] = {}
    for patch in patches:
        for block, claims in patch.items():
            merged.setdefault(block, {}).update(claims)
    return merged


def flag_patch(flag: str, value: bool, tier: Tier, source_url: str, **extra: Any) -> dict[str, Any]:
    """Build the patch that records a scoring flag as a traceable claim."""
    if flag not in FLAG_BLOCKS:
        raise ValueError(
            f"unknown scoring flag {flag!r}. Expected one of: {', '.join(FLAG_BLOCKS)}"
        )
    claim = make_claim(value, tier, source_url)
    claim.update(extra)
    return {FLAG_BLOCKS[flag]: {FLAGS_KEY: {flag: claim}}}


def make_quote(
    text: str,
    tier: Tier,
    source_url: str,
    speaker: str | None = None,
    role: str | None = None,
    date_checked: date | None = None,
) -> dict[str, Any]:
    """Build a verbatim-quote claim, marked so nothing downstream may reword it."""
    claim = make_claim(text, tier, source_url, date_checked=date_checked)
    claim["quote"] = True
    if speaker:
        claim["speaker"] = speaker
    if role:
        claim["role"] = role
    return claim


def read_block(evidence: dict[str, Any] | None, block: str) -> dict[str, Any]:
    """Return one block's claims, or an empty dict when it was never written."""
    _assert_block(block)
    return (evidence or {}).get(block) or {}


def read_claim(evidence: dict[str, Any] | None, block: str, key: str) -> dict[str, Any] | None:
    """Return one claim from a block, or None."""
    claim = read_block(evidence, block).get(key)
    return claim if isinstance(claim, dict) else None


def read_flag_claim(evidence: dict[str, Any] | None, flag: str) -> dict[str, Any] | None:
    """Return the claim behind a scoring flag, or None if no node set it."""
    if flag not in FLAG_BLOCKS:
        raise ValueError(f"unknown scoring flag {flag!r}")
    flags = read_block(evidence, FLAG_BLOCKS[flag]).get(FLAGS_KEY) or {}
    claim = flags.get(flag)
    return claim if isinstance(claim, dict) else None


def flag_is_true(evidence: dict[str, Any] | None, flag: str) -> bool:
    """True only when a node actually set this flag true.

    A flag no node reached is False, not an error. That is what lets a prospect
    whose front_door failed still be scored on the evidence that did arrive.
    """
    claim = read_flag_claim(evidence, flag)
    return bool(claim and claim.get("value") is True)


def iter_claims(evidence: dict[str, Any] | None, block: str):
    """Yield (key, claim) for every real claim in a block, skipping the flags."""
    for key, value in read_block(evidence, block).items():
        if key == FLAGS_KEY:
            continue
        if isinstance(value, dict) and "value" in value:
            yield key, value
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict) and "value" in item:
                    yield f"{key}[{index}]", item
