"""people — find the human who can say yes.

For a manufacturer under a hundred people that is almost always the owner,
president, or general manager. There is no procurement committee; there is one
person whose signature matters and whose name is usually on the About page.

Whether a name is found is worth a point in scoring, and more importantly it is
the difference between a P1 and a P2 — a strong prospect with nobody to write
to is research, not outreach. So the flag is deliberately strict:
`named_decision_maker` is true only when a specific human name is attached to a
leadership role the page states. "Our experienced team", "the management", and
info@ mailboxes are all false. Inflating this flag would put companies into the
outreach queue that nobody can actually be contacted at, which wastes the
scarcest thing in the pipeline, which is human attention.

Sources, in the order the task specifies: the company's own about, team or
leadership page (T1, their own words), then names already recovered by
case_study or grant_news (T2, someone else reporting them), then the footer or
contact page (T1). LinkedIn is not touched. Email addresses are not guessed —
contact discovery is a later stage with its own verification, and a guessed
address is a bounce that burns the domain's reputation.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from bs4 import BeautifulSoup

from lib.claims import Tier, make_claim
from lib.evidence import (
    BLOCK7_PEOPLE,
    block_patch,
    flag_patch,
    merge_patches,
    read_block,
)
from lib.nodes import FetchError, Node, NodeResult, RobotsDisallowed, RunContext, register

# One definition of "a name attached to a stated role", shared with case_study.
from tools.harvester.nodes.case_study import LEADERSHIP_ROLES, parse_people
from tools.harvester.nodes.front_door import discover_pages

TEAM_KEYWORDS = ("team", "leadership", "management", "our people", "staff", "meet the")
DECISION_ROLES = (
    "president", "owner", "founder", "co-founder", "chief executive officer", "ceo",
    "general manager", "gm", "managing director", "chief operating officer", "coo",
    "vice president", "plant manager", "operations manager", "director",
)
GENERIC_NAMES = re.compile(
    r"^(our team|the team|management|leadership|staff|contact us|customer service)$", re.IGNORECASE
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def is_decision_role(role: str) -> bool:
    """True when a role title implies authority to buy."""
    lowered = (role or "").lower()
    return any(candidate in lowered for candidate in DECISION_ROLES)


def find_team_links(home_url: str, html: str) -> list[str]:
    """Return internal links that look like a team or leadership page."""
    soup = BeautifulSoup(html, "html.parser")
    from urllib.parse import urljoin, urlparse

    host = urlparse(home_url).netloc.lower().removeprefix("www.")
    found: list[str] = []
    for anchor in soup.select("a[href]"):
        href = anchor["href"].strip()
        if href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        haystack = f"{_clean(anchor.get_text())} {href}".lower()
        if not any(keyword in haystack for keyword in TEAM_KEYWORDS):
            continue
        absolute = urljoin(home_url, href).split("#")[0]
        if urlparse(absolute).netloc.lower().removeprefix("www.") == host and absolute not in found:
            found.append(absolute)
    return found[:2]


def people_from_evidence(evidence: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return (name, role, source_url) already recovered by earlier nodes."""
    recovered: list[tuple[str, str, str]] = []
    for key, claim in read_block(evidence, BLOCK7_PEOPLE).items():
        if key == "flags":
            continue
        entries = claim if isinstance(claim, list) else [claim]
        for entry in entries:
            if not isinstance(entry, dict) or "value" not in entry:
                continue
            value = entry.get("value")
            if isinstance(value, str) and "—" in value:
                name, _, role = value.partition("—")
                recovered.append((_clean(name), _clean(role), entry.get("source_url", "")))
    return recovered


@register
class PeopleNode(Node):
    """Find a named decision-maker from the company's own site and prior evidence."""

    name: ClassVar[str] = "people"
    depends_on: ClassVar[tuple[str, ...]] = ("front_door",)

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        website = prospect.get("website")
        evidence = prospect.get("evidence_file") or {}
        notes: list[str] = []
        found: dict[str, tuple[str, str, Tier]] = {}

        if website:
            try:
                home = await ctx.fetch(website)
                pages = {"home": (str(home.url), home.text)}
                targets = find_team_links(str(home.url), home.text)
                targets += [
                    url
                    for kind, url in discover_pages(str(home.url), home.text).items()
                    if kind in ("about", "contact")
                ]
                for url in list(dict.fromkeys(targets))[:3]:
                    try:
                        page = await ctx.fetch(url)
                    except (FetchError, RobotsDisallowed):
                        continue
                    if page.status_code < 400:
                        pages[url] = (str(page.url), page.text)

                for url, html in pages.values():
                    soup = BeautifulSoup(html, "html.parser")
                    for tag in soup(["script", "style"]):
                        tag.decompose()
                    for name, role in parse_people(_clean(soup.get_text(" "))):
                        if not GENERIC_NAMES.match(name):
                            found.setdefault(name, (role, url, Tier.T1))
            except (FetchError, RobotsDisallowed) as exc:
                notes.append(f"company site unreadable for people ({type(exc).__name__}: {exc})")
        else:
            notes.append("no website resolved; company site not searched for people")

        for name, role, source in people_from_evidence(evidence):
            if name and not GENERIC_NAMES.match(name):
                found.setdefault(name, (role, source, Tier.T2))

        notes.append("LinkedIn not consulted; email addresses not guessed at this stage")

        if not found:
            return NodeResult(
                evidence_patch=flag_patch(
                    "named_decision_maker", False, Tier.T1, website or "https://example.invalid"
                ),
                notes=notes + ["no named person with a stated role found"],
            )

        claims = {
            "named_people": [
                make_claim(f"{name} — {role}", tier, source, )
                for name, (role, source, tier) in found.items()
                if source.startswith("http")
            ]
        }
        decision_makers = {
            name: value for name, value in found.items() if is_decision_role(value[0])
        }
        has_decision_maker = bool(decision_makers)
        first_source = next(
            (v[1] for v in found.values() if v[1].startswith("http")), website or ""
        )
        flag_source = (
            next(iter(decision_makers.values()))[1] if decision_makers else first_source
        )

        notes.append(
            f"found {len(found)} named person/people; "
            + (
                f"decision-maker(s): {', '.join(f'{n} ({found[n][0]})' for n in decision_makers)}"
                if decision_makers
                else "none holds a stated leadership role, so named_decision_maker is false"
            )
        )
        return NodeResult(
            evidence_patch=merge_patches(
                block_patch(BLOCK7_PEOPLE, claims),
                flag_patch(
                    "named_decision_maker",
                    has_decision_maker,
                    Tier.T1 if has_decision_maker else Tier.T1,
                    flag_source or "https://example.invalid",
                    people=list(decision_makers),
                ),
            ),
            notes=notes,
        )


__all__ = ["PeopleNode", "LEADERSHIP_ROLES", "is_decision_role", "find_team_links"]
