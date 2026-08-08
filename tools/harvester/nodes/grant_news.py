"""grant_news — recover the grant amount, round and year the listing omits.

The Conexus recipient listing publishes none of these. Press coverage does, in
a very regular form: the award roundups name each recipient as
"Acme Tool (Delaware County; $75,000 grant award)". That parenthetical is the
single most useful thing this node extracts, and it is verbatim, so it is worth
more than any amount of clever inference.

SEARCH BACKENDS, IN THE ORDER THE TASK SPECIFIES

(a) IEDC newsroom search — NOT USABLE. The newsroom search at iedc.in.gov is an
    ASP.NET WebForms control that posts __VIEWSTATE; it ignores a query string
    entirely (?search= and ?q= return the unfiltered page, /search returns 404).
    Driving it would mean replaying view state against a government site, which
    is well past "direct fetch", so this backend is skipped rather than faked.

(b) Inside INdiana Business site search — USED. Its robots.txt permits
    everything except wp-admin and cart parameters, and its WordPress search
    accepts ?s=, so a site-restricted query needs no credentials and no search
    engine. Results are T2: reputable secondary press, assertable only with
    attribution.

(c) Neither working leaves the block unavailable, with a note saying so.

WHAT THIS NODE REFUSES TO DO

A roundup article is about the programme, not about one company. Its totals
("$2.8 million awarded", "$17.4 million in investment") belong to the round,
and its quotes are usually from Conexus staff, not from the recipient. So a
figure or a quote is only attributed to this company when it appears inside the
sentence naming this company. Everything else is left out. Attributing the
round's total to one small manufacturer would be a fabrication that reads
entirely plausibly, which is exactly the kind this pipeline exists to prevent.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar
from urllib.parse import quote_plus

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
from lib.nodes import FetchError, Node, NodeResult, RobotsDisallowed, RunContext, register
from lib.scoring import DATA_GENERATING_TECH_TERMS
from lib.sources.conexus import GRANTS_URL, normalize_name

IEDC_NEWSROOM = "https://iedc.in.gov/events/news"
PUBLISHER_SEARCH = "https://www.insideindianabusiness.com/?s={query}"
PUBLISHER_HOST = "www.insideindianabusiness.com"
PROGRAM_NAME = "Manufacturing Readiness Grant"
MAX_ARTICLES = 3

AWARD_PATTERN = re.compile(
    r"([A-Z][A-Za-z0-9&.,'\-/ ]{2,60}?)\s*\(([A-Za-z. ]+?)\s+County;\s*\$([\d,]+)\s*grant award\)"
)
ROUND_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
ROUND_PATTERN = re.compile(
    rf"\b({'|'.join(ROUND_WORDS)})\s+round\b|\bround\s+(\d{{1,2}})\b", re.IGNORECASE
)
DATE_META = (
    ("meta", {"property": "article:published_time"}, "content"),
    ("meta", {"name": "publish-date"}, "content"),
    ("meta", {"itemprop": "datePublished"}, "content"),
)
YEAR_PATTERN = re.compile(r"\b(20[12]\d)\b")
NAME_PATTERN = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b")
SPEAKER_PATTERN = re.compile(
    r"[”\"],?\s+(?:said|says|according to)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})"
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def search_url(company_name: str) -> str:
    """The publisher search URL for one company."""
    return PUBLISHER_SEARCH.format(query=quote_plus(f"{company_name} {PROGRAM_NAME}"))


def parse_search_results(html: str) -> list[str]:
    """Return candidate article URLs from a publisher search page, in order."""
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for anchor in soup.select("a[href]"):
        href = anchor["href"].strip()
        if "/articles/" not in href:
            continue
        absolute = href if href.startswith("http") else f"https://{PUBLISHER_HOST}{href}"
        if absolute not in urls:
            urls.append(absolute)
    return urls


def article_text(html: str) -> str:
    """Return the readable text of an article page."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "form"]):
        tag.decompose()
    return _clean(soup.get_text(" "))


JSONLD_DATE = re.compile(r'"datePublished"\s*:\s*"(\d{4})-\d{2}-\d{2}')
VISIBLE_DATE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|"
    r"December)\s+\d{1,2},\s+(20[12]\d)\b"
)


def parse_published_year(html: str, text: str) -> int | None:
    """Return the article's publication year.

    Publishers disagree about where the date goes. Inside INdiana Business puts
    it in JSON-LD and in the visible byline but publishes no date meta tag, so
    all three are tried before giving up. A guessed year would silently
    mis-date a grant by a whole round, so an unparseable date yields None.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag, attrs, key in DATE_META:
        found = soup.find(tag, attrs=attrs)
        if found and found.get(key):
            year = YEAR_PATTERN.search(str(found[key]))
            if year:
                return int(year.group(1))
    time_tag = soup.find("time")
    if time_tag is not None:
        year = YEAR_PATTERN.search(str(time_tag.get("datetime") or time_tag.get_text()))
        if year:
            return int(year.group(1))
    jsonld = JSONLD_DATE.search(html)
    if jsonld:
        return int(jsonld.group(1))
    visible = VISIBLE_DATE.search(text)
    return int(visible.group(1)) if visible else None


def parse_round(text: str) -> str | None:
    """Return the grant round the article names, as a plain number string."""
    match = ROUND_PATTERN.search(text)
    if not match:
        return None
    word, digits = match.group(1), match.group(2)
    return str(ROUND_WORDS[word.lower()]) if word else digits


def find_award(text: str, company_name: str) -> tuple[str, str] | None:
    """Return (amount, matched_name) for THIS company's award, or None.

    Matching is on the normalised name, so 'Kirby Risk Corp.' in our database
    finds 'Kirby Risk Corporation' in the article — but a different company that
    merely shares a word does not.
    """
    ours = normalize_name(company_name)
    if not ours:
        return None
    for listed, _county, amount in AWARD_PATTERN.findall(text):
        theirs = normalize_name(listed)
        if theirs and (theirs == ours or _one_contains_the_other(ours, theirs)):
            return f"${amount}", _clean(listed)
    return None


def _one_contains_the_other(ours: str, theirs: str) -> bool:
    """True when one normalised name contains the other and the overlap is substantial."""
    shorter, longer = sorted((ours, theirs), key=len)
    return len(shorter) >= 8 and shorter in longer


def company_sentences(text: str, company_name: str) -> list[str]:
    """Return the sentences that actually name this company."""
    ours = normalize_name(company_name)
    if not ours:
        return []
    head = ours.split(" ")[0]
    return [
        _clean(sentence)
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if head and head in normalize_name(sentence)
    ]


@register
class GrantNewsNode(Node):
    """Search public press for this company's grant award details."""

    name: ClassVar[str] = "grant_news"
    depends_on: ClassVar[tuple[str, ...]] = ("normalize_identity",)

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        company = prospect.get("company_name") or ""
        notes: list[str] = [
            "IEDC newsroom search not used: it is a WebForms POST that ignores a "
            "query string, so it cannot be fetched directly"
        ]
        patches: list[dict[str, Any]] = [self._tech_flag(prospect)]
        prospect_patch: dict[str, Any] = {}

        try:
            results = await ctx.fetch(search_url(company))
        except (FetchError, RobotsDisallowed) as exc:
            notes.append(f"publisher search unavailable ({exc}); block2 left unpopulated")
            return NodeResult(
                evidence_patch=merge_patches(*patches), notes=notes
            )

        candidates = parse_search_results(results.text)
        if not candidates:
            notes.append(f"publisher search returned no articles for {company!r}")
            return NodeResult(evidence_patch=merge_patches(*patches), notes=notes)

        for url in candidates[:MAX_ARTICLES]:
            try:
                page = await ctx.fetch(url)
            except (FetchError, RobotsDisallowed):
                continue
            text = article_text(page.text)
            award = find_award(text, company)
            if not award:
                continue

            amount, listed_name = award
            claims, extra_notes = self._claims_from_article(
                text, page.text, url, company, amount, listed_name
            )
            notes.extend(extra_notes)
            patches.append(block_patch(BLOCK2_GRANT_FUNDED, claims["grant"]))
            patches.append(block_patch(BLOCK7_PEOPLE, claims["people"]))
            patches.append(block_patch(BLOCK8_FINANCIAL_SCALE, claims["scale"]))
            prospect_patch["grant_amount"] = float(amount.replace("$", "").replace(",", ""))
            notes.append(f"grant details recovered from {url} (T2 press coverage)")
            break
        else:
            notes.append(
                f"no article named {company!r} with an award amount; "
                f"grant amount, round and year remain unknown"
            )

        return NodeResult(
            prospect_patch=prospect_patch,
            evidence_patch=merge_patches(*patches),
            notes=notes,
        )

    def _tech_flag(self, prospect: dict) -> dict[str, Any]:
        """Set data_gen_tech from the grant description the extractor already stored."""
        description = " ".join(
            str(prospect.get(field) or "") for field in ("industry_desc", "tech_purchased")
        ).lower()
        matched = [term for term in DATA_GENERATING_TECH_TERMS if term.lower() in description]
        if not matched:
            return {}
        return flag_patch(
            "data_gen_tech", True, Tier.T4, GRANTS_URL, matched_terms=matched
        )

    def _claims_from_article(
        self, text: str, html: str, url: str, company: str, amount: str, listed_name: str
    ) -> tuple[dict[str, dict], list[str]]:
        """Build the claims this article supports for THIS company."""
        notes: list[str] = []
        grant: dict[str, Any] = {"grant_amount": make_claim(amount, Tier.T2, url)}

        round_number = parse_round(text)
        if round_number:
            grant["grant_round"] = make_claim(round_number, Tier.T2, url)
        else:
            notes.append("article names no grant round; omitted")

        year = parse_published_year(html, text)
        if year:
            grant["grant_year"] = make_claim(year, Tier.T2, url)
        else:
            notes.append("article carries no publication date; grant_year omitted")

        sentences = company_sentences(text, company)
        for sentence in sentences:
            if listed_name in sentence and len(sentence) > len(listed_name) + 40:
                grant["tech_purchased"] = make_claim(sentence, Tier.T2, url)
                break

        # The programme's own rule, applied to a number we found. Labelled as our
        # derivation (T4) and phrased as a floor, never as a total.
        grant["capital_deployed_floor"] = make_claim(
            f"Manufacturing Readiness Grants require a minimum 1:1 company match, so the "
            f"{amount} award implies at least {amount} of the company's own capital "
            f"alongside it. Treat {amount} as a FLOOR on capital deployed, not a total.",
            Tier.T4,
            GRANTS_URL,
        )

        people: dict[str, Any] = {}
        scale: dict[str, Any] = {}
        for sentence in sentences:
            speaker = SPEAKER_PATTERN.search(sentence)
            quote = re.search(r"[“\"]([^“”\"]{40,400})[”\"]", sentence)
            if speaker and quote:
                people["press_quote"] = make_quote(
                    _clean(quote.group(1)), Tier.T2, url, speaker=speaker.group(1)
                )
            investment = re.search(r"\$[\d.,]+\s*(?:million|billion)?", sentence)
            if investment and "grant award" not in sentence:
                scale["announced_investment"] = make_claim(
                    _clean(sentence), Tier.T2, url
                )
        if not people:
            notes.append(
                "no quote from a named person appears in a sentence about this company; "
                "programme-level quotes were not attributed to it"
            )
        return {"grant": grant, "people": people, "scale": scale}, notes
