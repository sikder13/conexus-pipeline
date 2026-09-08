"""contact_discovery — every published way in, read off their own pages.

WHY THIS IS A SEPARATE NODE

front_door already answers "is there a phone number on this site" as a boolean,
because what it is measuring is whether a buyer could act. That is the right
question for scoring and the wrong one for an operator holding a phone. This
node answers the operator's question instead: what is the number, what is the
address, which page did it come from.

The distinction matters enough to justify a second pass over the same pages. A
flag that says a number exists is a research finding; a number is a contact.

WHAT IS NOT HERE, AND WILL NOT BE

No address is ever constructed. first.last@, flast@, and every other pattern a
directory would sell us is absent by design — a guessed address is a bounce, a
bounce burns the sending domain, and the burnt domain costs every company in
the queue rather than the one we guessed at. The people node has said so in its
own docstring since before this node existed.

So this reads published text and nothing else. Where a company publishes
nothing, the record says nothing was published and points at the contact form,
which is a real path in. An honest gap can be closed by a person looking; an
invented address cannot be caught by anyone.

LINKEDIN

LinkedIn's robots.txt forbids us, so nothing here fetches it. For a person who
has already cleared the person gate, we construct the people-search URL an
operator would otherwise type by hand and store it as a link for them to open.
It is labelled as ours rather than as evidence, because a search URL asserts
nothing about the company — it is a shortcut, and dressing a shortcut as a
finding is how tiers get inflated.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar
from urllib.parse import quote_plus, urlparse

from bs4 import BeautifulSoup

from lib.claims import Tier, make_claim
from lib.evidence import BLOCK4_DIGITAL_FRONT_DOOR, block_patch
from lib.nodes import FetchError, Node, NodeResult, RobotsDisallowed, RunContext, register
from lib.persongate import gate_evidence
from tools.harvester.nodes.front_door import (
    PHONE_PATTERN,
    describe_form,
    discover_pages,
)
from tools.harvester.nodes.people import find_team_links

MAX_PAGES = 6
MIN_WEBSITE_CONFIDENCE = 50
MAX_PHONES = 5
"""How many numbers are worth storing. Beyond this it is a directory."""

EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}\b"
)

ROLE_MAILBOXES: frozenset[str] = frozenset({
    "sales", "info", "contact", "contactus", "admin", "office", "hello",
    "support", "service", "customerservice", "inquiry", "inquiries",
    "enquiry", "enquiries", "rfq", "quote", "quotes", "quoting", "estimating",
    "orders", "order", "purchasing", "procurement", "hr", "humanresources",
    "careers", "jobs", "recruiting", "accounting", "accounts", "ap", "ar",
    "billing", "marketing", "webmaster", "help", "mail", "team", "general",
    "engineering", "quality", "shipping", "receiving", "reception",
    "frontdesk", "customercare", "newbusiness", "bids",
})
"""Mailboxes that reach a function rather than a person.

Worth separating because they change how the operator writes, not whether they
can write at all: a role mailbox is a real path in and often the only one a
small manufacturer publishes, but the message that lands there is read by
whoever is on that rota and cannot open with a name."""

NOT_A_CONTACT_DOMAIN: tuple[str, ...] = (
    "sentry.io", "wixpress.com", "sentry-next.wixpress.com", "example.com",
    "example.org", "domain.com", "yourdomain.com", "email.com", "sentry.wixpress.com",
    "godaddy.com", "squarespace.com", "wordpress.com", "w3.org", "schema.org",
    "googleapis.com", "gstatic.com", "cloudflare.com", "jquery.com",
)
"""Addresses that belong to the site's plumbing, not to the company.

Analytics and error-reporting SDKs embed addresses in page source, and a
placeholder from a template is worse than useless — it looks like a finding."""

NOT_A_CONTACT_LOCAL: tuple[str, ...] = (
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "postmaster", "abuse", "privacy@example", "your", "youremail", "name",
    "email", "user", "someone", "test",
)

FILE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")

NAMED_LOCAL = re.compile(r"^[a-z]{1,}[._-][a-z]{2,}$")
"""first.last, f.last, first_last — a person's mailbox as people write it.

The first part is allowed to be a single letter because "a.gilkey@" is as
plainly a person as "anna.gilkey@", and requiring two letters filed a whole
published staff list under "we cannot tell who reads this".

Only used to CLASSIFY an address we already read off a page. Nothing here ever
runs the pattern the other way to build one."""

INITIAL_SURNAME = re.compile(r"^[a-z][a-z]{3,}$")
"""jbarr, afunkhouser — an initial run together with a surname, no separator.

Ambiguous on its own: "quality@" and "shipping@" have the same shape, and so
does any ordinary word. It is only read as a person when SEVERAL addresses on
the same domain share the shape, because one such mailbox is a word and a dozen
of them is a staff directory. That inference is about the set, not the address,
which is why it needs the siblings passed in."""

DIRECTORY_MINIMUM = 3
"""How many same-shaped mailboxes make a staff directory rather than a word."""

BUSINESS_WORDS: frozenset[str] = frozenset({
    "production", "operations", "maintenance", "warehouse", "logistics",
    "safety", "training", "tooling", "planning", "scheduling", "finance",
    "payroll", "legal", "media", "press", "news", "events", "store", "shop",
    "parts", "tech", "techsupport", "webteam", "web", "design", "studio",
    "office365", "email", "contactform", "feedback", "subscribe", "unsubscribe",
})
"""Words that name a function but are not on the role list. Kept separate so the
role list stays a list of mailboxes an operator would recognise as a rota."""

CLASS_WORDS = {
    "named_person": "reaches a named person",
    "role_based": "reaches a function, not a person",
    "generic": "published, but we cannot tell who reads it",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def usable_email(address: str, company_domain: str) -> bool:
    """Whether an address read off a page is plausibly a way to reach them."""
    lowered = address.lower().strip(" .,;:()<>[]\"'")
    if "@" not in lowered or lowered.endswith(FILE_SUFFIXES):
        return False
    local, _, domain = lowered.partition("@")
    if not local or not domain or ".." in domain:
        return False
    if any(domain == bad or domain.endswith("." + bad) for bad in NOT_A_CONTACT_DOMAIN):
        return False
    if any(local.startswith(bad) for bad in NOT_A_CONTACT_LOCAL):
        return False
    # A bare '@example' with no dot, or an address whose domain is a version
    # string picked out of a script, is markup rather than a mailbox.
    return "." in domain and not re.fullmatch(r"[\d.]+", domain)


def _local(address: str) -> str:
    return address.lower().partition("@")[0]


def directory_shaped(addresses: list[str]) -> set[str]:
    """Locals that look like a staff directory rather than a set of functions.

    A company that publishes jbarr@, tmurray@ and scarlock@ on its own domain
    has published a staff list. Any one of those, alone, is a word. The evidence
    for "person" is the pattern across the set, so it is computed once over the
    whole page's worth of addresses and handed to the classifier.
    """
    candidates = {
        _local(a) for a in addresses
        if INITIAL_SURNAME.match(_local(a))
        and _local(a) not in ROLE_MAILBOXES
        and _local(a) not in BUSINESS_WORDS
    }
    return candidates if len(candidates) >= DIRECTORY_MINIMUM else set()


def classify_email(
    address: str, known_people: list[str], directory: set[str] | None = None
) -> str:
    """Which of the three kinds of published address this is.

    The order matters. A person's name wins over a role word, because
    "sales.manager@" is a rota and "dale.sales@" is Dale — and reading the role
    word first would have called both a rota.
    """
    local = _local(address)
    stripped = re.sub(r"[^a-z]", "", local)
    for person in known_people:
        parts = [p for p in re.split(r"[^a-z]+", person.lower()) if len(p) > 2]
        if not parts:
            continue
        if all(part in local for part in parts):
            return "named_person"
        # dwhitmore / whitmored — a surname plus one initial is still a person.
        if len(parts) >= 2 and parts[-1] in stripped and len(stripped) <= len(parts[-1]) + 2:
            return "named_person"
    if stripped in ROLE_MAILBOXES or local.replace(".", "") in ROLE_MAILBOXES:
        return "role_based"
    if NAMED_LOCAL.match(local):
        return "named_person"
    if local in (directory or set()):
        return "named_person"
    return "generic"


def normalise_phone(raw: str) -> str:
    """One shape for a US number, so the same line is not stored twice."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return _clean(raw)
    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"


def linkedin_people_search(name: str, company: str) -> str:
    """The people-search URL an operator would otherwise type by hand.

    Constructed, never fetched: LinkedIn's robots.txt forbids us and that is the
    end of the matter. This is a link, not a lookup — we do not know whether the
    person has a profile and the stored record does not claim we do.
    """
    return ("https://www.linkedin.com/search/results/people/?keywords="
            + quote_plus(f"{name} {company}".strip()))


def emails_on(html: str, page_url: str, company_domain: str) -> dict[str, str]:
    """{address: where_seen} for every usable address on one page.

    mailto: links first because they are unambiguous, then visible text. Source
    is recorded per address rather than per page, so an operator opening the
    link lands where the address actually is.
    """
    found: dict[str, str] = {}
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.select("a[href^=mailto i]"):
        raw = str(anchor.get("href") or "")[7:].split("?")[0].strip()
        address = raw.lower().strip(" .,;:()<>[]\"'")
        if usable_email(address, company_domain):
            found.setdefault(address, page_url)
    for tag in soup(["script", "style"]):
        tag.decompose()
    for match in EMAIL_PATTERN.finditer(soup.get_text(" ")):
        address = match.group(0).lower().strip(" .,;:")
        if usable_email(address, company_domain):
            found.setdefault(address, page_url)
    return found


def phones_on(html: str, page_url: str) -> dict[str, str]:
    """{number: where_seen} for every phone number on one page."""
    found: dict[str, str] = {}
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.select("a[href^=tel i]"):
        number = normalise_phone(str(anchor.get("href") or "")[4:])
        if len(re.sub(r"\D", "", number)) >= 10:
            found.setdefault(number, page_url)
    for tag in soup(["script", "style"]):
        tag.decompose()
    for match in PHONE_PATTERN.finditer(soup.get_text(" ")):
        found.setdefault(normalise_phone(match.group(0)), page_url)
    return found


@register
class ContactDiscoveryNode(Node):
    """Read the company's own pages for the ways in they have published."""

    name: ClassVar[str] = "contact_discovery"
    depends_on: ClassVar[tuple[str, ...]] = ("front_door",)
    priorities: ClassVar[tuple[str, ...] | None] = ("P1",)

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        priority = prospect.get("priority")
        if self.priorities and priority not in self.priorities:
            return NodeResult(
                skipped=True,
                # Transient: the weekly reweight moves companies between
                # priorities, so today's P2 is a company this may run for later.
                skip_reason=(
                    f"priority={priority}; contact discovery is run for "
                    f"{', '.join(self.priorities)} only"),
            )
        website = prospect.get("website")
        confidence = prospect.get("website_confidence")
        if not website or (confidence or 0) < MIN_WEBSITE_CONFIDENCE:
            return NodeResult(
                skipped=True,
                skip_reason=(
                    f"website_confidence={confidence} is below "
                    f"{MIN_WEBSITE_CONFIDENCE}; reading the wrong company's "
                    f"contact page is worse than reading none"),
            )
        if prospect.get("website_status") == "compromised":
            return NodeResult(
                skipped=True,
                skip_reason=("the domain serves content that is not theirs; any "
                             "address on it reaches whoever took the domain"),
            )

        notes: list[str] = []
        company = str(prospect.get("company_name") or "")
        company_domain = urlparse(website).netloc.lower().removeprefix("www.")

        try:
            home = await ctx.fetch(website)
        except (FetchError, RobotsDisallowed) as exc:
            return NodeResult(
                skipped=True,
                skip_reason=f"home page unreadable ({type(exc).__name__}: {exc})",
            )
        if home.status_code >= 400:
            return NodeResult(
                skipped=True,
                skip_reason=f"home page returned HTTP {home.status_code}",
            )

        home_url = str(home.url)
        pages: dict[str, str] = {home_url: home.text}
        targets = [
            url for kind, url in discover_pages(home_url, home.text).items()
            if kind in ("contact", "about", "careers", "capabilities")
        ]
        targets += find_team_links(home_url, home.text)
        for url in list(dict.fromkeys(targets))[: MAX_PAGES - 1]:
            if url in pages:
                continue
            try:
                page = await ctx.fetch(url)
            except (FetchError, RobotsDisallowed) as exc:
                notes.append(f"{url} could not be read ({type(exc).__name__})")
                continue
            if page.status_code < 400:
                pages[str(page.url)] = page.text

        emails: dict[str, str] = {}
        phones: dict[str, str] = {}
        form: dict[str, Any] | None = None
        for url, html in pages.items():
            for address, seen in emails_on(html, url, company_domain).items():
                emails.setdefault(address, seen)
            for number, seen in phones_on(html, url).items():
                phones.setdefault(number, seen)
            form = form or describe_form(BeautifulSoup(html, "html.parser"), url)

        known_people = [g.name for g in gate_evidence(prospect) if g.name]
        directory = directory_shaped(list(emails))
        classified = {a: classify_email(a, known_people, directory) for a in emails}

        # A page that lists seventeen numbers is a supplier directory, not a
        # switchboard. Keep the ones on the pages most likely to be about them,
        # cap the rest, and say what was dropped rather than storing a list the
        # operator would have to sift.
        ranked = sorted(
            phones.items(),
            key=lambda pair: (
                0 if pair[1] == home_url else 1 if "contact" in pair[1].lower() else 2,
                pair[0],
            ),
        )
        dropped = max(0, len(ranked) - MAX_PHONES)
        phones = dict(ranked[:MAX_PHONES])
        if dropped:
            notes.append(
                f"{dropped} further number(s) appeared on those pages and were not "
                f"stored; a page listing this many is usually other companies' "
                f"numbers, so the shortest path is the site itself"
            )

        contacts: list[dict[str, Any]] = []
        email_claims = []
        for address, seen in sorted(emails.items()):
            kind = classified[address]
            email_claims.append(make_claim(f"{address} — {CLASS_WORDS[kind]}",
                                           Tier.T1, seen))
            contacts.append({
                "kind": "email", "value": address, "email_class": kind,
                "email_confidence": 100, "source_url": seen, "tier": int(Tier.T1),
                "name": None, "role": None,
            })
        phone_claims = []
        for number, seen in sorted(phones.items()):
            phone_claims.append(make_claim(number, Tier.T1, seen))
            contacts.append({
                "kind": "phone", "value": number, "source_url": seen,
                "tier": int(Tier.T1), "name": None, "role": None,
            })
        if form:
            contacts.append({
                "kind": "form", "value": form["posts_to"],
                "detail": form["destination"],
                "source_url": form["posts_to"], "tier": int(Tier.T1),
                "name": None, "role": None,
            })

        # A search link for anyone the person gate cleared. Constructed by us and
        # labelled that way — it says nothing about the company, and a company
        # with no cleared person simply gets none rather than a guess.
        cleared = [g for g in gate_evidence(prospect) if g.allowed and g.name]
        for person in cleared:
            contacts.append({
                "kind": "linkedin_search",
                "value": linkedin_people_search(person.name, company),
                "name": person.name, "role": person.role,
                "source_url": (person.independent_sources or [""])[0],
                "tier": int(Tier.T4),
            })
        notes.append(
            "LinkedIn was not fetched — their robots.txt forbids it. Any LinkedIn "
            "link stored here is a search URL we built for the operator to open."
        )

        by_class = {
            name: sum(1 for k in classified.values() if k == name)
            for name in CLASS_WORDS
        }
        notes.append(
            f"read {len(pages)} page(s): {len(emails)} published address(es) "
            f"({by_class['named_person']} named, {by_class['role_based']} role, "
            f"{by_class['generic']} unclear), {len(phones)} phone number(s), "
            f"{'a contact form' if form else 'no contact form'}, "
            f"{len(cleared)} search link(s) built"
        )
        if not emails:
            notes.append(
                "no address is published on the pages we read; the contact form "
                "or the phone is the path in. Nothing was guessed."
            )

        claims: dict[str, Any] = {}
        if email_claims:
            claims["published_emails"] = email_claims
        if phone_claims:
            claims["published_phones"] = phone_claims
        if form:
            claims["contact_form_url"] = make_claim(form["posts_to"], Tier.T1, home_url)
        claims["contact_pages_read"] = make_claim(sorted(pages), Tier.T1, home_url)

        return NodeResult(
            prospect_patch={"contacts": contacts},
            evidence_patch=block_patch(BLOCK4_DIGITAL_FRONT_DOOR, claims),
            notes=notes,
        )


__all__ = [
    "ContactDiscoveryNode",
    "classify_email",
    "directory_shaped",
    "emails_on",
    "linkedin_people_search",
    "normalise_phone",
    "phones_on",
    "usable_email",
]
