"""Construction and validation of evidence claims — the DATA-1 compliance layer.

Every factual statement the pipeline records about a prospect is a *claim*: a
value plus the source it came from, the tier of that source, and the date we
looked. The database enforces a shape backstop with a trigger; this module is
the front door, and it exists so that the correct thing is also the easy thing.

Two rules drive the whole design:

* A claim with no source is worse than no claim at all, because it looks like
  knowledge. ``make_claim`` therefore refuses to build one.
* Tiers must never be inflated. Only a *verified* T1 claim may be asserted to a
  prospect as fact; ``is_assertable`` is the single place that decides.

Never hand-build a claim dict elsewhere in the codebase. Use these helpers.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import IntEnum
from typing import Any
from urllib.parse import urlparse

CLAIM_KEYS: tuple[str, ...] = (
    "value",
    "tier",
    "source_url",
    "date_checked",
    "verified",
    "verified_at",
    "derived_from",
)
"""The canonical claim shape, in the order ``make_claim`` writes it."""

TRIGGER_REQUIRED_KEYS: tuple[str, ...] = ("tier", "source_url", "date_checked")
"""Keys the database trigger ``validate_evidence_claims()`` demands alongside ``value``."""

_DATE_CHECKED_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Tier(IntEnum):
    """Provenance tier of a claim's source, and what it permits in outreach.

    Tiers are assigned from where a fact came from, never from how confident we
    feel about it. Inflating a tier converts an estimate into an assertion, and
    that is the failure mode this pipeline exists to prevent.
    """

    def __new__(cls, value: int, outreach_rule: str) -> Tier:
        member = int.__new__(cls, value)
        member._value_ = value
        member.__doc__ = outreach_rule
        member.outreach_rule = outreach_rule
        return member

    T1 = (
        1,
        "The company's own words or a government record. Assertable to the "
        "prospect as plain fact once verified.",
    )
    T2 = (
        2,
        "Reputable secondary press. Assertable only WITH attribution naming the "
        "publication.",
    )
    T3 = (
        3,
        "Aggregator estimate (headcount and revenue sites). NEVER assertable to "
        "a prospect; internal filtering and prioritisation only.",
    )
    T4 = (
        4,
        "Our own inference. Must be labelled as a hypothesis whenever it is "
        "surfaced, inside or outside the company.",
    )

    outreach_rule: str


def _validate_source_url(source_url: Any) -> str:
    """Return a usable http(s) source URL or raise ValueError explaining why not."""
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError(
            "source_url is required: a claim without a source is not a claim. "
            "Record a note instead if the fact is unsourced."
        )
    cleaned = source_url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(
            f"source_url must be an http(s) URL that a human can open and check, got {cleaned!r}."
        )
    return cleaned


def origin_domain(url: str | None) -> str:
    """The registrable domain a fetch came from: 'www.a.example.com' -> 'example.com'.

    This is what ``derived_from`` records, and what tainting matches on. No
    public-suffix list, so 'co.uk' groups as 'co.uk' — that over-groups a few
    hosts rather than under-grouping them, and for a quarantine sweep catching
    one domain too many is the safe direction.
    """
    host = (urlparse(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [p for p in host.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def make_claim(
    value: Any,
    tier: Tier | int,
    source_url: str,
    date_checked: date | None = None,
    derived_from: str | None = None,
) -> dict[str, Any]:
    """Build the canonical claim dict for ``value``.

    ``date_checked`` defaults to today. The claim starts unverified: only a
    human working in the Verifier CLI may promote it, via ``mark_verified``.

    ``derived_from`` names the domain the content was fetched from, defaulting to
    the source URL's own domain. It is separate from ``source_url`` because the
    two can differ: when a fetch redirects, the claim cites the page a human can
    open while the origin records where the bytes actually came from. Tainting a
    hijacked domain sweeps on this field, so a claim gathered through a redirect
    is still caught.

    Raises ValueError if ``value`` is None, if ``source_url`` is empty or not
    http(s), or if ``tier`` is not one of the four defined tiers.
    """
    if value is None:
        raise ValueError(
            "value is None: a missing fact is not a claim. Omit the key or record "
            "a note describing what could not be found (DATA-1 rule 9)."
        )
    resolved_tier = Tier(tier)
    resolved_url = _validate_source_url(source_url)
    checked = date_checked if date_checked is not None else date.today()
    if not isinstance(checked, date):
        raise ValueError(f"date_checked must be a datetime.date, got {type(checked).__name__}.")
    return {
        "value": value,
        "tier": int(resolved_tier),
        "source_url": resolved_url,
        "date_checked": checked.isoformat(),
        "verified": False,
        "verified_at": None,
        "derived_from": derived_from or origin_domain(resolved_url),
    }


def mark_verified(claim: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``claim`` marked verified as of now.

    This is the ONLY function permitted to set ``verified=True``. It is called
    by the Verifier CLI on a human's decision; nothing else should call it.
    """
    if not isinstance(claim, dict):
        raise ValueError(f"claim must be a dict, got {type(claim).__name__}.")
    missing = [key for key in ("value", *TRIGGER_REQUIRED_KEYS) if key not in claim]
    if missing:
        raise ValueError(
            f"cannot verify a malformed claim; missing keys: {', '.join(missing)}. "
            f"Rebuild it with make_claim."
        )
    verified_claim = dict(claim)
    verified_claim["verified"] = True
    verified_claim["verified_at"] = datetime.now(UTC).isoformat()
    return verified_claim


def is_assertable(claim: dict[str, Any]) -> bool:
    """True only for a verified T1 claim — the one case we may state as fact.

    T2 needs attribution, T3 may never leave the building, and T4 is a
    hypothesis. All three return False here.
    """
    if not isinstance(claim, dict):
        return False
    return claim.get("verified") is True and claim.get("tier") == int(Tier.T1)


def requires_attribution(claim: dict[str, Any]) -> bool:
    """True for T2 claims, which may be used only with the publication named."""
    if not isinstance(claim, dict):
        return False
    return claim.get("tier") == int(Tier.T2)


def _looks_like_claim(node: Any) -> bool:
    """Mirror the trigger's ``exists(@.value)`` test: any object carrying a value key."""
    return isinstance(node, dict) and "value" in node


def _check_claim(node: dict[str, Any], path: str, problems: list[str]) -> None:
    """Append human-readable problems found in the claim at ``path``."""
    for key in TRIGGER_REQUIRED_KEYS:
        if key not in node:
            problems.append(
                f"{path}: missing required key '{key}' (the database trigger rejects this)"
            )

    tier = node.get("tier")
    if "tier" in node:
        if isinstance(tier, bool) or not isinstance(tier, int):
            problems.append(f"{path}: tier must be an integer 1-4, got {tier!r}")
        elif tier not in (1, 2, 3, 4):
            problems.append(f"{path}: tier must be 1-4, got {tier!r}")

    if "source_url" in node:
        try:
            _validate_source_url(node["source_url"])
        except ValueError:
            problems.append(
                f"{path}: source_url must be a non-empty http(s) URL, got {node['source_url']!r}"
            )

    if "date_checked" in node:
        raw_date = node["date_checked"]
        if not isinstance(raw_date, str) or not _DATE_CHECKED_PATTERN.match(raw_date):
            problems.append(f"{path}: date_checked must be a YYYY-MM-DD string, got {raw_date!r}")
        else:
            try:
                date.fromisoformat(raw_date)
            except ValueError:
                problems.append(f"{path}: date_checked is not a real calendar date: {raw_date!r}")

    if "verified" in node and not isinstance(node["verified"], bool):
        problems.append(f"{path}: verified must be a boolean, got {node['verified']!r}")

    verified_at = node.get("verified_at")
    if verified_at is not None:
        if not isinstance(verified_at, str):
            problems.append(
                f"{path}: verified_at must be an ISO timestamp string or null, got {verified_at!r}"
            )
        else:
            try:
                datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
            except ValueError:
                problems.append(f"{path}: verified_at is not an ISO timestamp: {verified_at!r}")

    if node.get("verified") is True and verified_at is None:
        problems.append(f"{path}: verified is True but verified_at is null")


def _walk(node: Any, path: str, problems: list[str]) -> None:
    """Recurse exactly as the trigger's ``$.**`` does, checking every claim found."""
    if _looks_like_claim(node):
        _check_claim(node, path, problems)
    if isinstance(node, dict):
        for key, child in node.items():
            _walk(child, f"{path}.{key}", problems)
    elif isinstance(node, list):
        for index, child in enumerate(node):
            _walk(child, f"{path}[{index}]", problems)


def validate_evidence_file(evidence: dict[str, Any]) -> list[str]:
    """Return every problem found in an ``evidence_file``; empty list means valid.

    Mirrors the database trigger so a tool can check its work before writing,
    and reports more than the trigger does (bad tier numbers, malformed dates)
    so the operator sees all the problems at once instead of one per round trip.
    """
    if evidence is None:
        return []
    if not isinstance(evidence, dict):
        return [f"evidence_file must be a JSON object, got {type(evidence).__name__}"]
    problems: list[str] = []
    _walk(evidence, "evidence_file", problems)
    return problems
