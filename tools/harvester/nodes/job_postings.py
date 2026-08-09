"""job_postings — read the company's own careers page.

An open clerical or coordination role is the strongest cheap signal this
pipeline has. A small manufacturer hiring someone to key in orders, chase
schedules, or build quotes by hand is telling you exactly which process is
currently done by a person and could be done by software. The duties list in
such a posting is a process map somebody wrote for us, which is why this node
stores it whole rather than summarising it. A summary of "handles order entry
and scheduling" is worth far less at the drafting stage than the actual bullet
list of the fourteen things that person does every morning.

WHERE IT WILL AND WILL NOT LOOK

Only the company's own careers page, discovered by front_door and read from
block4. Indeed and LinkedIn both disallow crawling in robots.txt, so they are
not fetched and not attempted; the absence is recorded as a note rather than
left to look like "this company is not hiring". A company can easily be hiring
on a board while showing nothing on its own site, and the note is what stops a
later reader from drawing the wrong conclusion from an empty block.

Named business systems in a posting are direct evidence of what they run. A
company asking for "3+ years with Epicor" runs Epicor; that is T1 and goes into
the tech stack block.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, ClassVar

from bs4 import BeautifulSoup

from lib.claims import Tier, make_claim
from lib.evidence import (
    BLOCK3_HIRING_SIGNALS,
    BLOCK4_DIGITAL_FRONT_DOOR,
    BLOCK6_TECH_STACK,
    block_patch,
    flag_patch,
    merge_patches,
    read_claim,
)
from lib.nodes import Node, NodeResult, RunContext, register
from lib.scoring import JOB_POSTING_MAX_AGE_DAYS

EXTERNAL_BOARDS_NOTE = (
    "external boards not checked: Indeed and LinkedIn disallow crawling in robots.txt, "
    "so an empty careers page here does not mean the company is not hiring"
)

CLERICAL_TERMS = (
    "order entry", "order processing", "scheduling", "scheduler", "estimating", "estimator",
    "quoting", "quote", "accounts payable", "accounts receivable", "customer service",
    "data entry", "coordination", "coordinator", "administrative", "office manager",
    "purchasing", "expediter", "expeditor", "inside sales", "billing", "clerk",
)

JOB_TITLE_HINTS = (
    "manager", "engineer", "operator", "machinist", "welder", "technician", "assembler",
    "coordinator", "clerk", "specialist", "supervisor", "estimator", "scheduler",
    "representative", "associate", "assistant", "administrator", "planner", "buyer",
    "inspector", "programmer", "designer", "analyst", "director", "lead", "apprentice",
)

BUSINESS_SYSTEMS = (
    "Epicor", "SAP", "NetSuite", "Infor", "JobBOSS", "JobBoss", "E2 Shop", "Global Shop",
    "Fishbowl", "QuickBooks", "Sage", "Made2Manage", "ProShop", "Odoo", "Dynamics 365",
    "Microsoft Dynamics", "Salesforce", "HubSpot", "SyteLine", "Acumatica", "Plex",
    "IQMS", "Visual Manufacturing", "Shoptech", "M1 ERP", "Genius ERP", "Realtrac",
    "Paradigm", "SolidWorks PDM", "Mastercam",
)

DATE_PATTERNS = (
    re.compile(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|"
        r"December)\s+(\d{1,2}),\s*(20\d\d)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(20\d\d)-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d\d)\b"),
)
MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July", "August",
         "September", "October", "November", "December"], start=1
    )
}
RELATIVE_DAYS = re.compile(r"\b(?:posted\s+)?(\d{1,3})\s+days?\s+ago\b", re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def looks_like_job_title(text: str) -> bool:
    """True when a heading reads like a job title rather than page furniture."""
    cleaned = _clean(text)
    if not 3 < len(cleaned) <= 90:
        return False
    lowered = cleaned.lower()
    if any(skip in lowered for skip in ("apply", "benefits", "why work", "our culture", "careers")):
        return False
    return any(hint in lowered for hint in JOB_TITLE_HINTS)


def parse_posting_date(text: str, today: date | None = None) -> date | None:
    """Return a posting date stated in the text, or None when none is shown."""
    today = today or date.today()
    relative = RELATIVE_DAYS.search(text)
    if relative:
        return today - timedelta(days=int(relative.group(1)))
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            if pattern is DATE_PATTERNS[0]:
                month, day, year = match.group(1), int(match.group(2)), int(match.group(3))
                return date(year, MONTHS[month.lower()], day)
            if pattern is DATE_PATTERNS[1]:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            return date(int(match.group(3)), int(match.group(1)), int(match.group(2)))
        except ValueError:
            continue
    return None


def parse_roles(html: str, today: date | None = None) -> list[dict[str, Any]]:
    """Return every open role on the page, with its duties text kept whole."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    headings = [h for h in soup.find_all(["h1", "h2", "h3", "h4", "h5"])
                if looks_like_job_title(h.get_text())]
    roles: list[dict[str, Any]] = []
    for heading in headings:
        duties: list[str] = []
        for sibling in heading.find_all_next():
            if sibling in headings:
                break
            if sibling.name in ("p", "li"):
                text = _clean(sibling.get_text())
                if text and text not in duties:
                    duties.append(text)
            if len(duties) >= 60:
                break
        body = " ".join(duties)
        roles.append(
            {
                "title": _clean(heading.get_text()),
                "duties": body,
                "posted": parse_posting_date(f"{heading.get_text()} {body}", today),
            }
        )
    return roles


def is_clerical(role: dict[str, Any]) -> bool:
    """True when a role is clerical or coordination work."""
    haystack = f"{role.get('title', '')} {role.get('duties', '')}".lower()
    return any(term in haystack for term in CLERICAL_TERMS)


def is_recent(role: dict[str, Any], today: date | None = None) -> bool:
    """True when a role has no date, or a date inside the window.

    A posting with no date counts. Many small-manufacturer careers pages show no
    dates at all, and excluding them would systematically under-score exactly
    the low-tech companies this pipeline is looking for.
    """
    today = today or date.today()
    posted = role.get("posted")
    if posted is None:
        return True
    return (today - posted).days <= JOB_POSTING_MAX_AGE_DAYS


def find_business_systems(text: str) -> list[str]:
    """Return the named business systems a posting mentions."""
    found: list[str] = []
    for system in BUSINESS_SYSTEMS:
        if re.search(rf"\b{re.escape(system)}\b", text, re.IGNORECASE) and system not in found:
            found.append(system)
    return found


@register
class JobPostingsNode(Node):
    """Read open roles from the company's own careers page."""

    name: ClassVar[str] = "job_postings"
    depends_on: ClassVar[tuple[str, ...]] = ("front_door",)

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        evidence = prospect.get("evidence_file") or {}
        careers = read_claim(evidence, BLOCK4_DIGITAL_FRONT_DOOR, "careers_url")
        careers_url = careers.get("value") if careers else None
        if not careers_url:
            return NodeResult(
                skipped=True,
                # Transient: front_door may discover a careers page on a later
                # pass, and companies add them.
                skip_reason="front_door found no careers page on the company's own site",
            )

        response = await ctx.fetch(careers_url)
        if response.status_code >= 400:
            raise RuntimeError(f"careers page {careers_url} returned HTTP {response.status_code}")

        today = date.today()
        roles = parse_roles(response.text, today)
        notes = [EXTERNAL_BOARDS_NOTE]

        if not roles:
            notes.append(f"no open roles recognisable on {careers_url}")
            return NodeResult(
                evidence_patch=block_patch(
                    BLOCK3_HIRING_SIGNALS,
                    {"open_roles_found": make_claim(0, Tier.T1, careers_url)},
                ),
                notes=notes,
            )

        role_claims = [
            make_claim(
                {
                    "title": role["title"],
                    "posted": role["posted"].isoformat() if role["posted"] else None,
                    "duties": role["duties"],
                },
                Tier.T1,
                careers_url,
            )
            for role in roles
        ]
        clerical = [r for r in roles if is_clerical(r) and is_recent(r, today)]
        has_clerical = bool(clerical)

        systems = find_business_systems(" ".join(f"{r['title']} {r['duties']}" for r in roles))
        patches = [
            block_patch(
                BLOCK3_HIRING_SIGNALS,
                {
                    "open_roles_found": make_claim(len(roles), Tier.T1, careers_url),
                    "open_roles": role_claims,
                },
            ),
            flag_patch(
                "has_clerical_posting", has_clerical, Tier.T1, careers_url,
                matched_roles=[r["title"] for r in clerical],
                window_days=JOB_POSTING_MAX_AGE_DAYS,
            ),
        ]
        if systems:
            patches.append(
                block_patch(
                    BLOCK6_TECH_STACK,
                    {
                        "systems_named_in_postings": [
                            make_claim(system, Tier.T1, careers_url) for system in systems
                        ]
                    },
                )
            )
            notes.append(f"postings name business systems in use: {', '.join(systems)}")

        undated = sum(1 for role in roles if role["posted"] is None)
        notes.append(
            f"{len(roles)} open role(s); {len(clerical)} clerical/coordination within "
            f"{JOB_POSTING_MAX_AGE_DAYS} days; {undated} posting(s) show no date and were counted"
        )
        return NodeResult(evidence_patch=merge_patches(*patches), notes=notes)
