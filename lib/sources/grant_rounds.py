"""Manufacturing Readiness Grant round announcements — the bulk grant source.

The per-company `grant_news` node had the wrong shape for this data. It searched
a publisher once per prospect, took about seventeen seconds each because every
request queues behind the same host lock, and found almost nothing: the awards
are not published per company at all.

They are published per ROUND. Each announcement lists every recipient of that
round in one page, in a rigidly consistent format:

    Acme Tool & Die (Delaware County; $75,000 grant award)

Six announcements cover the whole per-company record. Reading them costs six
fetches instead of 572 searches.

WHAT THIS SOURCE COVERS, AND WHAT IT DOES NOT

The verbatim per-company format appears in the 2020-2022 announcements and
stops there. Every later article from both publishers — the 2023 impact
studies, the 2024 regional features, the 2025 case-study pieces — reports the
programme in aggregate ("$23 million awarded in 2022") and names no per-company
amounts. That was checked directly rather than assumed: candidate articles
through 2026 were fetched and scanned for the award pattern, and every one
returned zero. So this adapter recovers the early rounds completely and the
later rounds not at all, and a company whose only award came after 2022 will
still have a null grant amount. A null is the correct answer there.

TIERS

CICP publishes these as the programme administrator's own announcement of IEDC
awards — a government record of the award, T1. Inside INdiana Business is
reputable secondary press reporting the same rounds, T2. Where both cover a
round, the T1 page is used and the T2 one is redundant.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict

from lib.claims import Tier
from lib.config import settings
from lib.nodes import RunContext
from lib.sources.conexus import normalize_name

SNAPSHOT_DIR = Path("data/raw/grant_rounds")

_CICP = "https://www.cicpindiana.com/"
_IIB = "https://www.insideindianabusiness.com/articles/"

ANNOUNCEMENTS: tuple[tuple[str, int], ...] = (
    # (url, tier). CICP is the administrator's own record of an IEDC award (T1);
    # Inside INdiana Business is press coverage of the same round (T2), used only
    # for the two rounds CICP does not carry.
    (_CICP + "state-conexus-indiana-award-20-manufacturing-readiness-grants"
     "-to-indiana-businesses/", 1),
    (_CICP + "second-round-of-manufacturing-readiness-grants-awarded"
     "-to-31-indiana-businesses/", 1),
    (_IIB + "state-awards-manufacturing-readiness-grants", 2),
    (_CICP + "manufacturing-readiness-grants-continue-securing-investments"
     "-in-smart-technology/", 1),
    (_IIB + "state-awards-more-manufacturing-readiness-grants", 2),
    (_CICP + "manufacturingreadinessgrantsindianaconexus/", 1),
)

AWARD_PATTERN = re.compile(
    r"([A-Z][A-Za-z0-9&.,'\-/ ]{2,60}?)\s*\(([A-Za-z. ]+?)\s+County;\s*\$([\d,]+)\s*grant award\)"
)
CORPORATE_ABBREVIATIONS = frozenset({
    "CO", "INC", "CORP", "LTD", "LLC", "LLP", "PLC", "BROS", "MFG", "MFRS",
    "ENT", "IND", "INTL", "ASSN", "DEPT", "UNIV", "ST", "AVE", "NO", "JR", "SR",
})
ROUND_WORDS = {
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
    "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
}
ROUND_PATTERN = re.compile(rf"\b({'|'.join(ROUND_WORDS)})\s+round\b", re.IGNORECASE)
JSONLD_DATE = re.compile(r'"datePublished"\s*:\s*"(\d{4})-(\d{2})-(\d{2})')
VISIBLE_DATE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|"
    r"December)\s+(\d{1,2}),\s+(20\d\d)\b"
)
MONTHS = {
    m: i for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July", "August",
         "September", "October", "November", "December"], start=1)
}
SPEAKER = re.compile(
    r"[”\"],?\s+(?:said|says|according to)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})"
)
QUOTED = re.compile(r"[“\"]([^“”\"]{40,400})[”\"]")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _is_abbreviation(token: str) -> bool:
    """True when a token ending in a full stop is an abbreviation, not a sentence end.

    "Mid-West Metal Products Co. Inc." is one company, not two sentences. Without
    this, splitting on every full stop reduced that name to "Inc." — and the same
    for "Dewig Bros. Packing Co. Inc." and the initialisms "D.A.S." and "L.O.F.".
    """
    word = token.rstrip(".")
    if word.upper() in CORPORATE_ABBREVIATIONS:
        return True
    # An initialism: single letters separated by stops, as in "D.A.S." or "L.O.F."
    return bool(re.fullmatch(r"(?:[A-Za-z]\.)+", token))


def clean_company_name(raw: str) -> str:
    """Trim the tail of the preceding sentence off a captured company name.

    The award pattern reads backwards from the parenthetical, and the name group
    accepts full stops (plenty of company names contain them: "Acme Tool Co."),
    so it can run back past a sentence boundary and capture
    "COVID-19 pandemic. OMR Automotive". Keeping only the text after the last
    real sentence break recovers the name. Names polluted this way match nothing
    and would otherwise be silently lost to the review pile.

    A full stop inside a company name is not a sentence break, so abbreviations
    and initialisms are stepped over rather than split on.
    """
    name = _clean(raw)
    tokens = name.split()
    start = 0
    for index, token in enumerate(tokens[:-1]):
        if token.endswith((".", "!", "?")) and not _is_abbreviation(token):
            start = index + 1
    tokens = tokens[start:]
    # A leading lowercase word means the capture still starts mid-sentence.
    while tokens and tokens[0][:1].islower():
        tokens.pop(0)
    return " ".join(tokens).strip(" ,;:")


class GrantAward(BaseModel):
    """One company's award in one round, exactly as the announcement states it."""

    model_config = ConfigDict(frozen=True)

    company_name: str
    county: str | None
    amount: float
    amount_text: str
    round_id: str
    announced: date
    source_url: str
    tier: int
    quote: str | None = None
    quote_speaker: str | None = None

    @property
    def year(self) -> int:
        return self.announced.year


def parse_announced_date(html: str, text: str) -> date | None:
    """Return the announcement date from JSON-LD, else a visible dateline."""
    found = JSONLD_DATE.search(html)
    if found:
        return date(int(found.group(1)), int(found.group(2)), int(found.group(3)))
    visible = VISIBLE_DATE.search(text)
    if visible:
        return date(int(visible.group(3)), MONTHS[visible.group(1)], int(visible.group(2)))
    return None


ROUND_BY_DATE = {date(2020, 8, 20): "1"}
"""Rounds whose announcement does not name itself.

Only the launch announcement needs this. It says "award 20 Manufacturing
Readiness Grants" with no ordinal, and the next announcement calls itself the
second round — so this one is the first. That is read off the sequence, not
assumed."""


def parse_round_id(text: str, announced: date | None) -> str:
    """Return the round this announcement covers.

    The announcements say "second round", "fourth round" and so on in prose.
    Where one does not, ROUND_BY_DATE names it; failing that the date stands in,
    which is unambiguous because no two rounds were announced on the same day.
    """
    found = ROUND_PATTERN.search(text)
    if found:
        return ROUND_WORDS[found.group(1).lower()]
    if announced in ROUND_BY_DATE:
        return ROUND_BY_DATE[announced]
    return announced.isoformat() if announced else "unknown"


def parse_company_quotes(text: str, company: str) -> tuple[str | None, str | None]:
    """Return a quote attributed to somebody at THIS company, or (None, None).

    A round announcement quotes the IEDC and Conexus officials who ran the
    programme, not the recipients. Attributing the programme's words to a
    recipient would be a fabrication that reads perfectly plausibly, so a quote
    counts only when the speaker's attribution sits in the same sentence that
    names the company. In practice that almost never happens, and returning
    nothing is the correct outcome.
    """
    head = normalize_name(company).split(" ")[0]
    if not head:
        return None, None
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if head not in normalize_name(sentence):
            continue
        speaker, quoted = SPEAKER.search(sentence), QUOTED.search(sentence)
        if speaker and quoted:
            return _clean(quoted.group(1)), speaker.group(1)
    return None, None


def parse_announcement(html: str, source_url: str, tier: int) -> list[GrantAward]:
    """Parse one round announcement into its award records."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = _clean(soup.get_text(" "))

    announced = parse_announced_date(html, text)
    if announced is None:
        return []
    round_id = parse_round_id(text, announced)

    awards: list[GrantAward] = []
    for raw_name, county, amount in AWARD_PATTERN.findall(text):
        name = clean_company_name(raw_name)
        if len(name) < 3:
            continue
        quote, speaker = parse_company_quotes(text, name)
        awards.append(
            GrantAward(
                company_name=name,
                county=_clean(county) or None,
                amount=float(amount.replace(",", "")),
                amount_text=f"${amount}",
                round_id=round_id,
                announced=announced,
                source_url=source_url,
                tier=tier,
                quote=quote,
                quote_speaker=speaker,
            )
        )
    return awards


def dedupe(awards: list[GrantAward]) -> list[GrantAward]:
    """Collapse the same award reported by two publishers, preferring T1.

    Two rounds are covered by both CICP and Inside INdiana Business. The same
    company, round and amount from both is one award, not two.
    """
    best: dict[tuple[str, str, float], GrantAward] = {}
    for award in awards:
        key = (normalize_name(award.company_name), award.round_id, award.amount)
        current = best.get(key)
        if current is None or award.tier < current.tier:
            best[key] = award
    return sorted(best.values(), key=lambda a: (a.announced, a.company_name))


class AwardMatch(BaseModel):
    """An award matched to exactly one prospect."""

    prospect_id: str
    company_name: str
    awards: list[GrantAward]


def match_to_prospects(
    awards: list[GrantAward], prospects: list[dict]
) -> tuple[list[AwardMatch], list[tuple[GrantAward, str]]]:
    """Match awards to prospects on the normalised name, conservatively.

    Only an exact normalised-name match assigns. Anything else — a name that
    matches two prospects, or none — is returned for review rather than guessed
    at. A grant amount attached to the wrong company is worse than a null: the
    null is visibly missing, the wrong number is quietly believed.
    """
    by_name: dict[str, list[dict]] = {}
    for prospect in prospects:
        by_name.setdefault(normalize_name(prospect["company_name"]), []).append(prospect)

    grouped: dict[str, list[GrantAward]] = {}
    unresolved: list[tuple[GrantAward, str]] = []
    for award in awards:
        key = normalize_name(award.company_name)
        candidates = by_name.get(key, [])
        if len(candidates) == 1:
            grouped.setdefault(candidates[0]["id"], []).append(award)
        elif not candidates:
            unresolved.append((award, "no prospect with this normalised name"))
        else:
            names = ", ".join(sorted(c["company_name"] for c in candidates))
            unresolved.append((award, f"ambiguous: matches {len(candidates)} prospects ({names})"))

    matches = [
        AwardMatch(
            prospect_id=pid,
            company_name=items[0].company_name,
            awards=sorted(items, key=lambda a: a.announced),
        )
        for pid, items in grouped.items()
    ]
    return matches, unresolved


def tier_for(award: GrantAward) -> Tier:
    """The claim tier this award's publisher earns."""
    return Tier.T1 if award.tier == 1 else Tier.T2


async def fetch_announcements(ctx: RunContext | None = None, save: bool = True) -> list[GrantAward]:
    """Fetch every known announcement and return the deduplicated award records."""
    if ctx is None:
        async with httpx.AsyncClient() as client:
            return await fetch_announcements(RunContext(client, settings), save)

    collected: list[GrantAward] = []
    for url, tier in ANNOUNCEMENTS:
        response = await ctx.fetch(url)
        if response.status_code >= 400:
            continue
        if save:
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^a-z0-9]+", "_", url.rstrip("/").split("/")[-1])[:70]
            (SNAPSHOT_DIR / f"{name}.html").write_text(response.text, encoding="utf-8")
        collected.extend(parse_announcement(response.text, url, tier))
    return dedupe(collected)
