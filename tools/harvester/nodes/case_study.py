"""case_study — read the Conexus case-study subpage, where one exists.

About seventy of the 572 recipients have a case study on the Conexus site.
They are the richest single source in the pipeline: Conexus interviewed the
company, so the page carries the grant amount, the award date, the headcount
at the time, what the money bought, and — most valuable of all — the owner
saying in their own words what was wrong before.

Everything here is T2. Conexus administers the programme and interviewed the
subject, which makes it reputable secondary press: assertable to a prospect
*with attribution* ("Conexus reported that...") and never as our own finding.
The temptation is to call an owner's quote T1 because they said it; resist it.
We did not hear them say it, Conexus did, and Conexus edited the page.

Quotes are stored verbatim with the quote marker so nothing downstream can
soften "we were drowning in spreadsheets" into a claim about their spreadsheets.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from bs4 import BeautifulSoup

from lib.claims import Tier, make_claim
from lib.evidence import (
    BLOCK2_GRANT_FUNDED,
    BLOCK7_PEOPLE,
    BLOCK8_FINANCIAL_SCALE,
    block_patch,
    flag_patch,
    make_quote,
    merge_patches,
)
from lib.nodes import Node, NodeResult, RunContext, SkipKind, register
from lib.scoring import EMPLOYEE_CEILING

LEADERSHIP_ROLES = (
    "chief executive officer", "president", "owner", "founder", "co-founder",
    "general manager", "plant manager", "operations manager", "vice president",
    "chief operating officer", "chief technology officer", "managing director",
    "ceo", "coo", "cto", "cfo", "gm", "director",
)

_NAME = r"[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){1,2}"
_ROLE_ALT = "|".join(sorted((re.escape(r) for r in LEADERSHIP_ROLES), key=len, reverse=True))

# Only patterns where the page itself states the role. Nothing is inferred from
# proximity: a name near the word "president" is not a president.
#
# The case-insensitivity is scoped to the ROLE alternation with (?i:...) and must
# stay that way. A whole-pattern re.IGNORECASE also loosens the name pattern's
# [A-Z][a-z]+ to match lowercase words, which produced "people" called
# "said Mike Cramer" — a wrong name is worse than no name.
NAME_ROLE_PATTERNS = (
    re.compile(rf"({_NAME}),\s+(?:the\s+)?((?i:{_ROLE_ALT}))\b"),
    re.compile(rf"\b((?i:{_ROLE_ALT}))\s+({_NAME})\b"),
    re.compile(rf"({_NAME})\s+(founded)\b"),
)

ROLE_TOKENS = frozenset(
    token for role in LEADERSHIP_ROLES for token in role.split()
) | {"vp", "svp", "evp", "manager", "executive", "officer", "chief", "principal"}

NON_NAME_TOKENS = frozenset({
    "the", "our", "your", "office", "contact", "about", "team", "home", "welcome",
    "read", "view", "learn", "more", "all", "new", "said", "says", "meet", "join",
    "why", "how", "what", "we", "us", "company", "careers", "news", "blog",
    "privacy", "terms", "copyright", "quality", "products", "services", "industry",
    "industries", "affiliations", "technologies", "technology", "corporation",
    "solutions", "systems", "group", "inc", "llc", "sales", "marketing",
    "engineering", "manufacturing", "customer", "support", "leadership",
}) | ROLE_TOKENS
"""Tokens that never appear in a person's name in this dataset.

Role tokens are included because the name pattern is greedy: given
"Jeff Frost President, CEO" it captures "Jeff Frost President" as the name.
Trailing role words are stripped before validation, and any that remain inside
the name disqualify it."""

QUOTE_PATTERN = re.compile(r"[“\"]([^“”\"]{40,600})[”\"]")
SPEAKER_AFTER = re.compile(rf"[”\"],?\s+(?:said|says|explains|explained|adds|noted)\s+({_NAME})")
STAT_LABELS = {
    "grant amount": "grant_amount",
    "award date": "award_date",
    "company size": "company_size",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def parse_key_stats(soup: BeautifulSoup) -> dict[str, str]:
    """Read the labelled 'Key Stats' panel into {canonical_label: value}."""
    stats: dict[str, str] = {}
    for entry in soup.select("div.single-stat"):
        value = entry.select_one(".stat")
        label = entry.select_one(".stat-subtitle")
        if not value or not label:
            continue
        canonical = STAT_LABELS.get(_clean(label.get_text()).lower())
        if canonical:
            stats[canonical] = _clean(value.get_text())
    return stats


def parse_section(soup: BeautifulSoup, heading: str) -> str | None:
    """Return the prose immediately under a named heading, verbatim."""
    target = soup.find(
        lambda tag: tag.name in ("h2", "h3")
        and _clean(tag.get_text()).lower() == heading.lower()
    )
    if not target:
        return None
    collected: list[str] = []
    for sibling in target.find_all_next():
        if sibling.name == "h2" and sibling is not target:
            break
        # Conexus wraps heading text in its own <p>, so a naive walk picks the
        # heading back up as the first paragraph of its own section.
        if sibling.name == "p" and not sibling.find_parent(["h1", "h2", "h3", "h4"]):
            text = _clean(sibling.get_text())
            if text:
                collected.append(text)
        if len(collected) >= 2:
            break
    return " ".join(collected) or None


def parse_quotes(text: str) -> list[tuple[str, str | None]]:
    """Return (quote, speaker or None) for every substantial quotation on the page."""
    found: list[tuple[str, str | None]] = []
    for match in QUOTE_PATTERN.finditer(text):
        quote = _clean(match.group(1))
        trailing = text[match.end() - 1 : match.end() + 120]
        speaker_match = SPEAKER_AFTER.search(trailing)
        found.append((quote, speaker_match.group(1) if speaker_match else None))
    return found


def _simplify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def clean_person_name(name: str, company_name: str | None = None) -> str | None:
    """Return a usable person name, or None when the phrase is not one.

    Three things reach here that are not people, and each would be recorded as a
    named decision-maker — worth a scoring point, and enough to promote a company
    to P1 on somebody who does not exist:

    * page furniture that happens to be capitalised ("Our Office Jared ...",
      "Industry Affiliations"),
    * the company's own name ("DuraMark Technologies"),
    * a real name with the role glued on, because the name pattern is greedy
      ("Jeff Frost President"). That last one is salvageable — the trailing role
      words are stripped and the person kept.

    Accuracy beats coverage here: a name we throw away costs one scoring point,
    a name we invent costs the reader's trust in the whole file.
    """
    tokens = [token for token in _clean(name).split() if token]
    while tokens and tokens[-1].lower().strip(".,") in ROLE_TOKENS:
        tokens.pop()
    if not 2 <= len(tokens) <= 3:
        return None
    if any(token.lower().strip(".,") in NON_NAME_TOKENS for token in tokens):
        return None
    cleaned = " ".join(tokens)
    if company_name:
        theirs, ours = _simplify(cleaned), _simplify(company_name)
        if theirs and (theirs in ours or ours.startswith(theirs)):
            return None
    return cleaned


def looks_like_a_person(name: str, company_name: str | None = None) -> bool:
    """True when the phrase yields a usable person name."""
    return clean_person_name(name, company_name) is not None


def parse_people(text: str, company_name: str | None = None) -> list[tuple[str, str]]:
    """Return (name, role) pairs the page states explicitly."""
    people: dict[str, str] = {}
    for pattern in NAME_ROLE_PATTERNS:
        for match in pattern.finditer(text):
            first, second = match.group(1), match.group(2)
            if second.lower() == "founded":
                name, role = first, "Founder"
            elif first.lower() in LEADERSHIP_ROLES:
                name, role = second, first
            else:
                name, role = first, second
            cleaned = clean_person_name(name, company_name)
            if cleaned:
                people.setdefault(cleaned, _clean(role).title())
    return list(people.items())


CAPACITY_PATTERNS = (
    # Only sentences carrying an actual number and a production unit. Prose about
    # how well the project went is not a capacity figure, however encouraging.
    re.compile(r"[^.]*\b\d[\d,.]*\s*(?:%|percent)[^.]*\b(?:capacity|throughput|lead time|"
               r"output|productivity|scrap|downtime|cycle time)[^.]*\.", re.IGNORECASE),
    re.compile(r"[^.]*\b\d[\d,.]*\s*(?:parts|units|pieces|tons|pounds|lbs)\b[^.]*"
               r"\b(?:per|a|an|each)\s+(?:hour|day|week|month|year|shift)[^.]*\.", re.IGNORECASE),
)


def parse_capacity_figures(text: str) -> list[str]:
    """Return verbatim sentences that state a production or capacity number."""
    figures: list[str] = []
    for pattern in CAPACITY_PATTERNS:
        for match in pattern.finditer(text):
            sentence = _clean(match.group(0))
            if sentence not in figures:
                figures.append(sentence)
    return figures[:5]


def parse_employee_count(value: str | None) -> int | None:
    """Read a headcount from the Company Size stat, or None if it is not a number."""
    if not value:
        return None
    digits = re.search(r"\d[\d,]*", value)
    return int(digits.group(0).replace(",", "")) if digits else None


@register
class CaseStudyNode(Node):
    """Extract the Conexus case study for the companies that have one."""

    name: ClassVar[str] = "case_study"
    depends_on: ClassVar[tuple[str, ...]] = ("normalize_identity",)

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        url = prospect.get("case_study_url")
        if not url:
            return NodeResult(
                skipped=True,
                # Permanent: the Conexus listing will not grow a case study for
                # a company it does not have one for, so re-checking the other
                # 502 companies on every run buys nothing.
                skip_kind=SkipKind.PERMANENT,
                skip_reason="no Conexus case study exists for this company",
            )

        response = await ctx.fetch(url)
        if response.status_code >= 400:
            raise RuntimeError(f"case study {url} returned HTTP {response.status_code}")

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = _clean(soup.get_text(" "))

        notes: list[str] = []
        stats = parse_key_stats(soup)

        grant_claims: dict[str, Any] = {}
        for key in ("grant_amount", "award_date"):
            if stats.get(key):
                grant_claims[key] = make_claim(stats[key], Tier.T2, url)
        funded = parse_section(soup, "The Project")
        if funded:
            grant_claims["what_the_grant_funded"] = make_claim(funded, Tier.T2, url)
        else:
            notes.append("case study has no 'The Project' section; what_the_grant_funded omitted")

        scale_claims: dict[str, Any] = {}
        prospect_patch: dict[str, Any] = {}
        headcount = parse_employee_count(stats.get("company_size"))
        if headcount is not None:
            scale_claims["company_size"] = make_claim(headcount, Tier.T2, url)
            # The column feeds contact strategy, so it carries its own provenance.
            prospect_patch["employee_estimate"] = str(headcount)
            prospect_patch["employee_source"] = f"Conexus case study [T2] {url}"
        elif stats.get("company_size"):
            scale_claims["company_size"] = make_claim(stats["company_size"], Tier.T2, url)
        figures = parse_capacity_figures(text)
        if figures:
            scale_claims["capacity_figures"] = [
                make_claim(figure, Tier.T2, url) for figure in figures
            ]
        else:
            notes.append("case study states no production or capacity figure; omitted")

        people_claims: dict[str, Any] = {}
        named = parse_people(text, prospect.get("company_name"))
        if named:
            people_claims["named_people"] = [
                make_claim(f"{name} — {role}", Tier.T2, url) for name, role in named
            ]
        quotes = parse_quotes(text)
        if quotes:
            people_claims["leadership_quotes"] = [
                make_quote(quote, Tier.T2, url, speaker=speaker) for quote, speaker in quotes
            ]
            notes.append(f"captured {len(quotes)} verbatim quote(s); never paraphrase these")
        if not named:
            notes.append("case study states no name with an explicit role; named_people omitted")

        patches = [
            block_patch(BLOCK2_GRANT_FUNDED, grant_claims),
            block_patch(BLOCK8_FINANCIAL_SCALE, scale_claims),
            block_patch(BLOCK7_PEOPLE, people_claims),
            # The page's existence is the signal, and we just fetched it ourselves.
            flag_patch("has_case_study", True, Tier.T1, url),
        ]
        if headcount is not None and headcount > EMPLOYEE_CEILING:
            patches.append(flag_patch("too_big", True, Tier.T2, url))
            notes.append(
                f"case study states {headcount} employees, "
                f"above the {EMPLOYEE_CEILING} ceiling"
            )

        return NodeResult(
            prospect_patch=prospect_patch,
            evidence_patch=merge_patches(*patches),
            notes=notes,
        )
