"""normalize_identity — tidy the company's name and place it on the map.

The first thing every downstream node needs is a stable answer to "who is this
and where are they". The grant listing gives us a name typed by whoever filled
in the application and a county, and nothing else. This node normalises the
name, separates the trading name from the legal suffix, and converts the county
into an estimated drive time from Muncie.

It does no fetching. Everything here is a transformation of what the source
already told us, which is why it is the one node with no dependencies and no
network cost.

Where it cannot be confident — an unrecognised county, a name carrying two
companies — it sets stage='needs_review' rather than picking an answer. A
30-second human glance is cheaper than a wrong company researched for an hour.
"""

from __future__ import annotations

import re
from typing import ClassVar

from lib.claims import Tier, make_claim
from lib.geo import canonical_county, drive_minutes_from_muncie
from lib.nodes import Node, NodeResult, RunContext, register

CENSUS_GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/"
    "2023_Gaz_counties_national.zip"
)
"""Source of the county interior points behind every drive-time estimate."""

_LEGAL_SUFFIX = re.compile(
    r"[,\s]+(?:inc|llc|l\.l\.c|llp|lp|ltd|co|corp|corporation|incorporated|company|pbc|pc)\.?$",
    re.IGNORECASE,
)
_ALT_NAME_MARKER = re.compile(r"\s+(?:dba|d/b/a|d\.b\.a\.?)\s+|/", re.IGNORECASE)


COMMON_NAME_WORDS = frozenset({
    "and", "the", "of", "for", "tool", "tools", "die", "machine", "machining", "manufacturing",
    "industries", "industrial", "products", "company", "works", "steel", "metal", "metals",
    "plastics", "molding", "mold", "solutions", "technologies", "systems", "services",
    "fabrication", "welding", "engineering", "precision", "supply", "equipment", "design",
    "group", "enterprises", "brothers", "sons", "custom", "automation", "cast", "packaging",
    "tooling", "electric", "motor", "machinery", "foundry", "gear", "spring", "wire",
})
"""Ordinary words that appear in manufacturer names. Their presence is the
evidence that an all-capitals name is words rather than an initialism."""

_LEGAL_TOKENS = frozenset({"inc", "llc", "llp", "lp", "ltd", "co", "corp", "corporation", "pbc"})


def normalize_whitespace_and_case(name: str) -> str:
    """Collapse whitespace, and de-shout a name only when it is safe to.

    Casing is corrected only where there is positive evidence the name is made
    of words — at least one ordinary word from COMMON_NAME_WORDS. Of the 578
    companies in the Conexus listing, four are typed in capitals and every one
    of them (2NNS, EEMSCO, LOD, NISCO) is a coined name or an initialism, so
    title-casing by default would corrupt the company's own spelling far more
    often than it would tidy anything. Short tokens that are not ordinary words
    keep their capitals for the same reason.
    """
    cleaned = re.sub(r"\s+", " ", (name or "").strip())
    if not cleaned or cleaned != cleaned.upper() or len(cleaned) <= 4:
        return cleaned

    tokens = cleaned.split(" ")
    plain = [token.lower().strip(".,&") for token in tokens]
    if not any(word in COMMON_NAME_WORDS for word in plain):
        return cleaned

    return " ".join(
        token
        if len(token) <= 4 and word not in COMMON_NAME_WORDS and word not in _LEGAL_TOKENS
        else token.title()
        for token, word in zip(tokens, plain, strict=True)
    )


def strip_legal_suffix(name: str) -> str:
    """Remove trailing legal suffixes, repeatedly ('Foo Inc., LLC' -> 'Foo')."""
    previous, current = None, name.strip()
    while previous != current:
        previous = current
        current = _LEGAL_SUFFIX.sub("", current).strip(" ,")
    return current


def name_is_ambiguous(name: str) -> bool:
    """True when a name plausibly denotes more than one company, or nothing useful."""
    if len(strip_legal_suffix(name)) < 3:
        return True
    return bool(_ALT_NAME_MARKER.search(name))


@register
class NormalizeIdentity(Node):
    """Normalise name and county, and estimate drive time from Muncie."""

    name: ClassVar[str] = "normalize_identity"
    depends_on: ClassVar[tuple[str, ...]] = ()

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        raw_name = prospect.get("company_name") or ""
        clean_name = normalize_whitespace_and_case(raw_name)
        trading_name = strip_legal_suffix(clean_name)

        patch: dict = {}
        notes: list[str] = []
        evidence: dict = {}

        if clean_name and clean_name != raw_name:
            patch["company_name"] = clean_name
            notes.append(f"normalized company name from {raw_name!r} to {clean_name!r}")

        # Only record a trading name when it actually differs from the legal one.
        if trading_name and trading_name != clean_name and not prospect.get("dba_name"):
            patch["dba_name"] = trading_name
            notes.append(f"legal suffix stripped into dba_name: {trading_name!r}")

        raw_county = prospect.get("county")
        county = canonical_county(raw_county)
        needs_review = False

        if county is None:
            needs_review = True
            notes.append(
                f"county {raw_county!r} is not a recognised Indiana county; "
                f"drive_minutes left null and flagged for review"
            )
        else:
            if county != raw_county:
                patch["county"] = county
                notes.append(f"county normalized from {raw_county!r} to {county!r}")
            minutes = drive_minutes_from_muncie(county)
            patch["drive_minutes"] = minutes
            evidence["identity"] = {
                "drive_minutes_from_muncie": make_claim(
                    minutes, Tier.T4, CENSUS_GAZETTEER_URL
                )
            }
            notes.append(
                f"drive_minutes={minutes} is a T4 estimate from the {county} County "
                f"census centroid, not a routed journey time"
            )

        # A city we were never given is not a city we may invent (rule 9).
        if not prospect.get("city"):
            notes.append("no city published by the source; left null")

        if name_is_ambiguous(clean_name):
            needs_review = True
            notes.append(f"company name {clean_name!r} is ambiguous; flagged for review")

        if needs_review and prospect.get("stage") in (None, "extracted"):
            patch["stage"] = "needs_review"

        return NodeResult(prospect_patch=patch, evidence_patch=evidence, notes=notes)
