"""Every way we already know to reach a company, gathered in one place.

WHY THIS EXISTS

The operator sends every message by hand. That makes "who do I write to, and
where did that come from" the most-used question in the console, and until now
the answer was scattered across three evidence blocks and a prospect column.

This module only ever REPORTS. It performs no discovery, guesses no address
from a name and a domain, and infers nothing. Every path it returns is
something a node already recorded, carrying the URL where it was seen, so the
operator can open the source and confirm before typing anything. A company with
nothing published comes back empty and is told so plainly — an invented
address is worse than an honest gap, because the gap can be closed by looking
and the invention cannot be caught by anyone.

WHAT THE EVIDENCE ACTUALLY HOLDS

Mostly presence, not values. `phone_present` is a boolean: a node saw a phone
number on the page and recorded that it existed, not what it was. So a path is
often a pointer — "there is a number on this page, here is the page" — and the
panel says exactly that rather than implying we hold a number we do not.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from lib.claimcheck import is_barred
from lib.evidence import BLOCK4_DIGITAL_FRONT_DOOR, BLOCK7_PEOPLE
from lib.persongate import check_person

NO_CONTACT_NOTE = "no published contact found — check the site manually"


class ContactPath(NamedTuple):
    """One way to reach a company, and where we saw it."""

    kind: str          # person | phone | form | site | careers
    label: str         # what the operator reads
    detail: str        # the value, or what to look for when we hold no value
    source_url: str    # the page it came from, always openable
    caution: str = ""  # anything that must be read before using it


def _claim(block: dict[str, Any], key: str) -> dict[str, Any]:
    value = (block or {}).get(key)
    return value if isinstance(value, dict) else {}


def named_contacts(prospect: dict[str, Any]) -> list[ContactPath]:
    """People recorded for this company, each with its person-gate verdict.

    The gate's verdict travels with the name rather than filtering it out. An
    operator writing by hand may reasonably address a letter to someone the
    automated gate would not name, but they must know which case they are in —
    so a name that failed is shown, marked, and never presented as confirmed.
    """
    block = (prospect.get("evidence_file") or {}).get(BLOCK7_PEOPLE) or {}
    people = block.get("named_people") or []
    pool = [p for p in people if isinstance(p, dict)]
    out = []
    for claim in pool:
        value = str(claim.get("value") or "").strip()
        if not value:
            continue
        verdict = check_person(claim, prospect.get("company_name") or "", pool)
        if is_barred(claim):
            caution = "the checker could not find this person in the source — do not use"
        elif verdict.allowed:
            caution = ""
        else:
            caution = "; ".join(verdict.reasons) or "not confirmed by two sources"
        out.append(ContactPath(
            kind="person",
            label="Named person",
            detail=value,
            source_url=str(claim.get("source_url") or ""),
            caution=caution,
        ))
    return out


def contact_paths(prospect: dict[str, Any]) -> list[ContactPath]:
    """Every recorded way to reach this company, best-evidenced first."""
    evidence = prospect.get("evidence_file") or {}
    front = evidence.get(BLOCK4_DIGITAL_FRONT_DOOR) or {}
    site = str(prospect.get("website") or "").strip()
    paths: list[ContactPath] = list(named_contacts(prospect))

    form_url = str(_claim(front, "form_posts_to").get("value") or "").strip()
    destination = str(_claim(front, "form_destination").get("value") or "").strip()
    has_form = _claim(front, "has_contact_form").get("value") is True
    if form_url:
        paths.append(ContactPath(
            kind="form", label="Contact form posts to", detail=form_url,
            source_url=str(_claim(front, "form_posts_to").get("source_url") or site),
        ))
    elif has_form:
        paths.append(ContactPath(
            kind="form", label="Contact form", detail="a form exists on the site",
            source_url=_contact_page(front, site),
        ))
    if destination and destination != form_url:
        paths.append(ContactPath(
            kind="form", label="Form delivers to", detail=destination,
            source_url=str(_claim(front, "form_destination").get("source_url") or site),
        ))

    phone = _claim(front, "phone_present")
    if phone.get("value") is True:
        paths.append(ContactPath(
            kind="phone", label="Phone",
            detail="a number is published on the site — read it from the page",
            source_url=str(phone.get("source_url") or site),
        ))

    if site:
        paths.append(ContactPath(
            kind="site", label="Website", detail=site, source_url=site))
    careers = str(_claim(front, "careers_url").get("value") or "").strip()
    if careers:
        paths.append(ContactPath(
            kind="careers", label="Careers page", detail=careers, source_url=careers))
    return paths


def _contact_page(front: dict[str, Any], site: str) -> str:
    """The company's contact page if a node read one, else the site root."""
    pages = _claim(front, "pages_read").get("value") or []
    if isinstance(pages, list) and "contact" in [str(p).lower() for p in pages]:
        return f"{site.rstrip('/')}/contact" if site else ""
    return site


def reachable(prospect: dict[str, Any]) -> bool:
    """True when we hold at least one way in that is not merely the homepage."""
    return any(p.kind != "site" for p in contact_paths(prospect))
