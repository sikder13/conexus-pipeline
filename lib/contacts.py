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

Two kinds of row, and the difference is worth knowing. The older evidence blocks
hold presence, not values: `phone_present` is a boolean saying a node saw a
number, not what the number was, so those rows are pointers — "there is a number
on this page, here is the page" — and the panel says exactly that rather than
implying we hold something we do not.

The contact_discovery node holds values. It reads the addresses and numbers
themselves off the company's own pages, so those rows are the answer rather than
directions to it, and they sort first. Where discovery has read a real number,
the older pointer to it is suppressed: a row saying "a number exists somewhere
on this page" sitting above the number is noise.

Neither kind is ever constructed. No address is derived from a name and a
domain, here or in the node that fills this — see tools/harvester/nodes/
contact_discovery.py for why that line is not negotiable.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from lib.claimcheck import is_barred
from lib.evidence import BLOCK4_DIGITAL_FRONT_DOOR, BLOCK7_PEOPLE
from lib.integrity import is_killed, is_tainted
from lib.persongate import check_person

NO_CONTACT_NOTE = "no published contact found — check the site manually"


class ContactPath(NamedTuple):
    """One way to reach a company, and where we saw it."""

    kind: str          # person | email | phone | form | linkedin | site | careers
    label: str         # what the operator reads
    detail: str        # the value, or what to look for when we hold no value
    source_url: str    # the page it came from, always openable
    caution: str = ""  # anything that must be read before using it


def _claim(block: dict[str, Any], key: str) -> dict[str, Any]:
    """One claim from a block, or nothing if it has been quarantined.

    A quarantined claim is not a weaker claim, it is one whose source turned out
    not to be the company's. Returning an empty dict means every caller below
    treats it as absent without each of them having to remember to ask.
    """
    value = (block or {}).get(key)
    if not isinstance(value, dict) or is_tainted(value) or is_killed(value):
        return {}
    return value


def named_contacts(prospect: dict[str, Any]) -> list[ContactPath]:
    """People recorded for this company, each with its person-gate verdict.

    The gate's verdict travels with the name rather than filtering it out. An
    operator writing by hand may reasonably address a letter to someone the
    automated gate would not name, but they must know which case they are in —
    so a name that failed is shown, marked, and never presented as confirmed.
    """
    block = (prospect.get("evidence_file") or {}).get(BLOCK7_PEOPLE) or {}
    people = block.get("named_people") or []
    # A tainted person is not a doubtful person — it is somebody who works for a
    # different company. Four executives of cedar.com, a US healthcare-payments
    # firm, were offered as the way in to a pita-chip manufacturer in Windsor,
    # Ontario, each one already carrying the reason it was not theirs.
    pool = [p for p in people
            if isinstance(p, dict) and not is_tainted(p) and not is_killed(p)]
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


EMAIL_LABELS = {
    "named_person": "Email — a named person",
    "role_based": "Email — a role mailbox",
    "generic": "Email — published, reader unknown",
}
"""How each class of published address is introduced to the operator.

The class changes how they write rather than whether they can: a role mailbox
is read by whoever is on that rota and cannot be opened with a name, and an
address we cannot place should not be opened with one either."""


def discovered_paths(prospect: dict[str, Any]) -> list[ContactPath]:
    """Ways in that contact discovery read off the company's own pages.

    These come first in the panel because they are the only rows an operator can
    act on without opening anything: an address is an address, where the older
    rows are mostly pointers saying a number exists somewhere on a page.
    """
    out: list[ContactPath] = []
    for entry in prospect.get("contacts") or []:
        if not isinstance(entry, dict) or not entry.get("value"):
            continue
        # Discovery writes to its own column, which the evidence-file taint sweep
        # never walks. So a withdrawn website left its contact rows standing:
        # cedar.com's contact form and a LinkedIn search built from cedar.com's
        # CEO were still offered as the way in to a company in Windsor, Ontario
        # after every claim read from that domain had been quarantined.
        if is_tainted(entry) or is_killed(entry):
            continue
        kind = entry.get("kind")
        value, source = str(entry["value"]), str(entry.get("source_url") or "")
        if kind == "email":
            out.append(ContactPath(
                kind="email",
                label=EMAIL_LABELS.get(str(entry.get("email_class")), "Email"),
                detail=value, source_url=source,
                caution=("" if entry.get("email_class") == "named_person" else
                         "whoever is on this rota reads it — do not open with a name"
                         if entry.get("email_class") == "role_based" else
                         "we could not tell who reads this one"),
            ))
        elif kind == "phone":
            out.append(ContactPath(kind="phone", label="Phone", detail=value,
                                   source_url=source))
        elif kind == "form":
            out.append(ContactPath(
                kind="form", label="Contact form posts to", detail=value,
                source_url=source,
                caution=str(entry.get("detail") or ""),
            ))
        elif kind == "linkedin_search":
            out.append(ContactPath(
                kind="linkedin",
                label=f"LinkedIn search — {entry.get('name') or 'person'}",
                detail=value, source_url=source,
                caution=("a search link we built from their name, not a profile we "
                         "found. We do not read LinkedIn; their robots.txt forbids it"),
            ))
    return out


def contact_paths(prospect: dict[str, Any]) -> list[ContactPath]:
    """Every recorded way to reach this company, best-evidenced first."""
    evidence = prospect.get("evidence_file") or {}
    front = evidence.get(BLOCK4_DIGITAL_FRONT_DOOR) or {}
    site = str(prospect.get("website") or "").strip()
    discovered = discovered_paths(prospect)
    paths: list[ContactPath] = [*discovered, *named_contacts(prospect)]
    have_number = any(p.kind == "phone" for p in discovered)
    have_form = any(p.kind == "form" for p in discovered)

    form_url = str(_claim(front, "form_posts_to").get("value") or "").strip()
    destination = str(_claim(front, "form_destination").get("value") or "").strip()
    has_form = _claim(front, "has_contact_form").get("value") is True
    if form_url and not have_form:
        paths.append(ContactPath(
            kind="form", label="Contact form posts to", detail=form_url,
            source_url=str(_claim(front, "form_posts_to").get("source_url") or site),
        ))
    elif has_form and not have_form:
        paths.append(ContactPath(
            kind="form", label="Contact form", detail="a form exists on the site",
            source_url=_contact_page(front, site),
        ))
    if destination and destination != form_url:
        paths.append(ContactPath(
            kind="form", label="Form delivers to", detail=destination,
            source_url=str(_claim(front, "form_destination").get("source_url") or site),
        ))

    # Once discovery has read the number itself, the older "a number exists on
    # this page" row is noise sitting above the answer.
    phone = _claim(front, "phone_present")
    if phone.get("value") is True and not have_number:
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


VERIFIED_KINDS: tuple[str, ...] = ("email", "phone", "form")
"""A way in that does not depend on a name having been confirmed.

A published role mailbox, a contact form, or a phone number: each is something
`contact_discovery` read off the company's own pages, and each reaches the
company without anybody having to be right about who works there.

The 'person' kind is deliberately absent. It is derived from a name in block 7,
and the two places that ask this question — P1 admission and the drafting floor
— already test the name separately. Counting it here would let an unconfirmed
name satisfy the condition twice and make the floor's approval step decorative.

'site' is absent for the older reason: the homepage is where we started looking.
"""


def verified_path(prospect: dict[str, Any]) -> bool:
    """True when this company can be reached without naming anybody."""
    return any(p.kind in VERIFIED_KINDS for p in contact_paths(prospect))
