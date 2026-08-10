"""Evidence integrity — the second dimension, separate from signal strength.

A score answers "how interesting is this company". It cannot answer "is this
evidence about that company at all". Decatur Plastic Products scored 4 and sat
at P1 while its `self_description` held the text of an Indonesian gambling site:
its domain had expired, been re-registered, and now redirected through two other
hosts to a togel page. Three of its four points came from the genuine Conexus
case study; the fourth, the one that crossed the P1 threshold, was measured
against the hijacked page.

Nothing in a one-dimensional pipeline could catch that, because every individual
claim was correctly formed — sourced, tiered, dated. The file was internally
valid and externally false. This module is the second dimension.

WHAT IS AND IS NOT ENFORCED HERE

These are gates on *use*, never on data. Nothing in this module deletes a claim
or a prospect. A failure marks the record and routes it to a human; the evidence
stays exactly as gathered, because the thing that looks like noise today is the
thing that explains the mistake tomorrow.

The taint markers this reads (`tainted`, `killed`, `derived_from`) are optional
claim fields. Claims written before they existed simply do not carry them and
are treated as untainted, which is correct: absence of a taint marker means
nobody has found a reason to distrust the claim, not that one is hidden.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from lib.evidence import BLOCK1_WHAT_THEY_MAKE, BLOCKS, FLAGS_KEY

COMPROMISED = "compromised"
"""website_status for a domain serving content that is not the company's."""

STALE_AFTER_DAYS = 14
"""Beyond this, freshness must be re-established before anything is said aloud."""


class IntegrityReport(BaseModel):
    """Whether an evidence file may be scored and acted on, and why not."""

    passing: bool = True
    failures: list[str] = Field(default_factory=list)
    checked_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()


def registrable_domain(url: str | None) -> str:
    """The registrable part of a URL's host, e.g. 'www.a.example.com' -> 'example.com'.

    Deliberately simple: no public-suffix list, so a multi-part TLD such as
    'co.uk' yields 'co.uk'. That over-groups a handful of hosts rather than
    under-grouping them, which is the safe direction for a taint check — it can
    catch one domain too many, never one too few.
    """
    host = (urlparse(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [p for p in host.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def is_tainted(claim: Any) -> bool:
    """True when a claim has been quarantined as deriving from bad evidence."""
    return isinstance(claim, dict) and claim.get("tainted") is True


def is_killed(claim: Any) -> bool:
    """True when a human rejected this claim in the console."""
    return isinstance(claim, dict) and claim.get("killed") is True


def is_usable(claim: Any) -> bool:
    """True when a claim may feed scoring, a summary, or a thesis.

    Tainted and killed claims are excluded everywhere downstream but are never
    removed from the file. Quarantine, not destruction.
    """
    return isinstance(claim, dict) and "value" in claim and not is_tainted(claim) \
        and not is_killed(claim)


def iter_all_claims(evidence: dict[str, Any] | None):
    """Yield (path, claim) for every claim in an evidence file.

    Mirrors the database trigger's `$.** ? (exists(@.value))` so that what this
    module reasons about and what the database enforces are the same objects.
    """

    def walk(node: Any, path: str):
        if isinstance(node, dict):
            if "value" in node:
                yield path, node
            for key, child in node.items():
                yield from walk(child, f"{path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                yield from walk(child, f"{path}[{index}]")

    yield from walk(evidence or {}, "evidence_file")


def block_claims(evidence: dict[str, Any] | None, block: str) -> list[dict[str, Any]]:
    """Every claim inside one block, flags included."""
    body = (evidence or {}).get(block) or {}
    return [claim for _path, claim in iter_all_claims({block: body})]


def substantive_block1(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Block 1 claims that are not flags — what the company says it makes.

    Flags are our own derived booleans; they cannot stand in for a description
    of the business. A block1 holding only flags is an empty block1.
    """
    body = (evidence or {}).get(BLOCK1_WHAT_THEY_MAKE) or {}
    return [
        claim for key, claim in body.items()
        if key != FLAGS_KEY and isinstance(claim, dict) and "value" in claim
    ]


def evidence_integrity(prospect: dict[str, Any]) -> IntegrityReport:
    """Decide whether this prospect's evidence may be scored and acted on.

    Fails when:
      * the resolved website is flagged compromised — everything read from it
        describes somebody else's site;
      * block1 is empty, or holds only tainted or killed claims — with no
        untainted account of what the company makes, there is nothing to be
        interested in;
      * any scoring input would come from a tainted claim.

    A failure is not a verdict about the company. It says the file cannot be
    scored as it stands, and a human should look. The caller nulls the score
    rather than zeroing it: zero is a finding, null is "not computable".
    """
    failures: list[str] = []
    evidence = prospect.get("evidence_file") or {}

    if prospect.get("website_status") == COMPROMISED:
        failures.append(
            f"website_status is 'compromised' ({prospect.get('website') or 'no url'}): "
            f"content fetched from this domain is not this company's"
        )

    block1 = substantive_block1(evidence)
    if not block1:
        failures.append(
            "block1_what_they_make holds no substantive claim: nothing records what "
            "this company makes"
        )
    elif all(is_tainted(c) or is_killed(c) for c in block1):
        failures.append(
            "every block1_what_they_make claim is tainted or killed: no untainted "
            "account of what this company makes survives"
        )

    tainted_scoring = sorted({
        path for path, claim in iter_all_claims(evidence)
        if is_tainted(claim) and _feeds_scoring(path)
    })
    if tainted_scoring:
        failures.append(
            "a scoring input is tainted: " + ", ".join(tainted_scoring[:5])
        )

    return IntegrityReport(
        passing=not failures,
        failures=failures,
        checked_at=datetime.now(UTC).isoformat(),
    )


def _feeds_scoring(path: str) -> bool:
    """True for claim paths a scoring component reads.

    Scoring reads block flags and the score_evidence trail, so a tainted claim
    anywhere in those paths means the score was computed from evidence we no
    longer trust.
    """
    return f".{FLAGS_KEY}." in path or ".score_evidence" in path


def is_stale(freshness_date: Any, today: Any = None) -> bool:
    """True when freshness is absent or older than the staleness window."""
    from datetime import date as _date

    if not freshness_date:
        return True
    if isinstance(freshness_date, str):
        try:
            freshness_date = _date.fromisoformat(freshness_date[:10])
        except ValueError:
            return True
    reference = today or _date.today()
    return (reference - freshness_date).days > STALE_AFTER_DAYS


def taint_claims_from_domain(
    evidence: dict[str, Any] | None, domain: str, reason: str
) -> tuple[dict[str, Any], int]:
    """Mark every claim sourced from ``domain`` as tainted. Returns (evidence, count).

    Marks in place on a copy; deletes nothing. A tainted claim keeps its value,
    its source and its date, and gains only the reason it is no longer trusted,
    so the record still shows what was believed and why it stopped being true.
    """
    target = registrable_domain(f"https://{domain}" if "//" not in domain else domain)
    marked = 0

    def walk(node: Any) -> Any:
        nonlocal marked
        if isinstance(node, dict):
            if "value" in node:
                origin = node.get("derived_from") or registrable_domain(node.get("source_url"))
                if origin and origin == target and not node.get("tainted"):
                    node = dict(node)
                    node["tainted"] = True
                    node["taint_reason"] = reason
                    marked += 1
                    return node
            return {key: walk(child) for key, child in node.items()}
        if isinstance(node, list):
            return [walk(child) for child in node]
        return node

    return walk(dict(evidence or {})), marked


def usable_blocks(evidence: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Blocks in canonical order, keeping only claims that may be used downstream."""
    out: dict[str, dict[str, Any]] = {}
    for block in BLOCKS:
        body = (evidence or {}).get(block) or {}
        kept = {k: v for k, v in body.items() if not isinstance(v, dict) or is_usable(v)}
        if kept:
            out[block] = kept
    return out


def taint_prospect_claims(prospect_id: str, domain: str, reason: str) -> int:
    """Quarantine every claim on one prospect that came from ``domain``.

    Returns how many claims were marked. Nothing is deleted: a tainted claim
    keeps its value, source, tier and date and gains only the reason it is no
    longer trusted, so the record still shows what was believed and when it
    stopped being true. That matters more than tidiness — the claim that looks
    like noise today is what explains the mistake tomorrow.

    Reads and writes through lib.db like everything else in the pipeline.
    """
    from lib import db

    prospect = db.get_prospect(prospect_id)
    if not prospect:
        raise ValueError(f"no prospect {prospect_id}")
    evidence, marked = taint_claims_from_domain(
        prospect.get("evidence_file") or {}, domain, reason
    )
    if marked:
        db.update_prospect(prospect_id, {"evidence_file": evidence})
    return marked


def backfill_derived_from(evidence: dict[str, Any] | None) -> tuple[dict[str, Any], int]:
    """Add ``derived_from`` to claims written before the field existed.

    Only where the source URL makes the origin obvious, which is every claim
    carrying an http(s) source — that is what the field means. Claims already
    carrying one are left alone, because an explicit origin may differ from the
    source URL's domain (a fetch that redirected) and must not be overwritten by
    a guess.
    """
    filled = 0

    def walk(node: Any) -> Any:
        nonlocal filled
        if isinstance(node, dict):
            if "value" in node and "derived_from" not in node:
                origin = registrable_domain(node.get("source_url"))
                if origin:
                    node = dict(node)
                    node["derived_from"] = origin
                    filled += 1
                    return node
            return {key: walk(child) for key, child in node.items()}
        if isinstance(node, list):
            return [walk(child) for child in node]
        return node

    return walk(dict(evidence or {})), filled
