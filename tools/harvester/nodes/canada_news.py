"""canada_news — what a government record or the Canadian trade press says about them.

The Canadian mirror of `grant_news`, and it is a mirror in structure rather than
in wording: the same three questions, asked of the sources that exist here.

SEARCH BACKENDS, AND WHAT EACH ONE TURNED OUT TO BE

(a) **Canada.ca advanced news search — NOT USABLE.** It takes a `keyword`
    parameter and ignores it. Two different company names returned pages that
    were identical byte for byte, both reporting the same 82,034 unfiltered
    results. The filtering happens in the browser, so driving it would mean
    fetching the whole federal news archive and searching it ourselves, three
    hundred times over.

(b) **The federal news API (`api.io.canada.ca`) — NOT USABLE for this.** It
    answers by department, and it does answer: `?dept=nationalresearchcouncil`
    returns a real feed. Every free-text parameter tried against it — `term`,
    `q`, `keyword`, `search`, `text` — returned zero entries, while the same
    request without one returned the feed. An unknown parameter is not an error
    there, it is an empty answer, which is the most dangerous shape a search
    backend can have: it looks like "no coverage of this company" and means
    "you asked the wrong question". Recorded here so nobody re-derives it.

(c) **NRC's own site search — NOT USABLE.** Same shape as (a): "2,155 search
    results for" whatever was asked, including for nothing.

(d) **The proactive-disclosure grants search — USED, Tier 1.**
    `search.open.canada.ca/grants/?search_text=` is server-rendered and
    honours its query. It reaches every federal award, not only the ones our
    six filters kept, so a company whose file holds one NRC contribution can
    turn out to hold three more from other departments. Those are government
    records: Tier 1.

    Its matching is loose — a search for "Trexo Robotics" returns a University
    of Toronto researcher, because it matched the word "Robotics". So the
    recipient's legal name on each record is matched on the normalised key
    before anything is written, exactly as `grant_news` does with the Indiana
    round announcements. A loose search plus a strict match is a usable
    backend; a loose search plus a loose match is a machine for attributing
    other people's money to our prospects.

(e) **Canadian trade press site search — USED, Tier 2.** Two publishers whose
    WordPress search is server-rendered and whose robots.txt permits it:
    Canadian Manufacturing and Food In Canada. The pair is chosen to cover the
    industries this expansion actually sells into — half of them are food and
    beverage, and a manufacturing title alone would find nothing on a winery.

WHAT IT REFUSES TO DO

The same refusal `grant_news` makes, for the same reason: a programme
announcement is about the programme. Its totals belong to the round and its
quotes are usually from a minister. A figure or a quote is attributed to this
company only when it appears in a sentence naming this company.

And it never reads a headcount out of a job-creation promise. "The project will
create 15 jobs" is the single most likely sentence on a federal funding page,
and `lib/headcount.py` refuses it by construction.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from lib import headcount as hc
from lib.claims import Tier, make_claim
from lib.evidence import (
    BLOCK2_GRANT_FUNDED,
    BLOCK7_PEOPLE,
    BLOCK8_FINANCIAL_SCALE,
    block_patch,
    make_quote,
    merge_patches,
)
from lib.nodes import (
    FetchError,
    Node,
    NodeResult,
    RobotsDisallowed,
    RunContext,
    SkipKind,
    register,
)
from lib.scoring import DEFAULT_ADAPTER
from lib.sources.canada_gc.recipients import company_key

RECORDS_SEARCH = "https://search.open.canada.ca/grants/?search_text={query}"
RECORDS_HOST = "https://search.open.canada.ca"
RECORD_LINK = "/grants/record/"

PUBLISHERS: tuple[tuple[str, str], ...] = (
    ("Canadian Manufacturing", "https://www.canadianmanufacturing.com/?s={query}"),
    ("Food In Canada", "https://www.foodincanada.com/?s={query}"),
)
"""The trade titles searched, with the name each is attributed by.

Attribution is not optional on a Tier 2 claim, so the publisher's name travels
with the URL rather than being reconstructed from the domain later."""

ARTICLE_LINK = re.compile(
    r"/(?:news|features|technology|manufacturing|financing|operations|"
    r"sustainability|products)/[a-z0-9][a-z0-9\-]{10,}", re.IGNORECASE)
"""What an article URL looks like on both publishers.

Both are WordPress sites that put a section and a long slug in the path. A
minimum slug length keeps section index pages — /news/ — out of the candidates."""

PROVINCE_TERMS: dict[str, str] = {"ON": "Ontario", "AB": "Alberta"}

MAX_RECORDS = 6
MAX_ARTICLES = 2
"""Per backend, per company. The records search is cheap and the press search is
another company's server, so the press gets the smaller number."""

SPEAKER_PATTERN = re.compile(
    r"[”\"],?\s+(?:said|says|according to)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})")
QUOTE_PATTERN = re.compile(r"[“\"]([^“”\"]{40,400})[”\"]")
MONEY = re.compile(r"\$[\d.,]+\s*(?:million|billion|M|B)?")

RECORD_LABELS: tuple[str, ...] = (
    "Recipient Business Number", "Recipient Type", "Recipient's Legal Name",
    "Recipient Legal Name", "Recipient Operating Name", "Research Organization",
    "Federal Riding Name", "Federal Riding Number", "Program Name", "Program Purpose",
    "Program", "Coverage", "NAICS Code", "Agreement Title", "Agreement Number",
    "Agreement Value", "Agreement Type", "Agreement Date", "Agreement Start Date",
    "Agreement End Date", "Description", "Expected Results", "Additional Information",
    "Organization", "Amendment Number", "Location", "Country", "Province, Territory",
    "City", "Postal Code", "Foreign Currency Type", "Foreign Currency Value",
)
"""Every field label the proactive-disclosure record puts on the page.

Read as a LABEL SET rather than with a regex per field, because the page runs
its labels together with single spaces — "Agreement Value: $120,000.00 Agreement
Date: Aug 1, 2025" — and a pattern that terminates on whitespace swallows the
next field or gives up. Splitting on the labels themselves is the only reading
that does not depend on how the markup happens to collapse.

Ordered longest-first when the alternation is built, so 'Program Name' is tried
before 'Program' and 'Agreement Start Date' before 'Agreement Date'."""

_LABEL_ALTERNATION = "|".join(
    re.escape(label) for label in sorted(RECORD_LABELS, key=len, reverse=True))
LABEL_SPLIT = re.compile(rf"\b({_LABEL_ALTERNATION}):\s*")

AMOUNT = re.compile(r"\$?\s?([\d,]+(?:\.\d{2})?)")
RECORD_YEAR = re.compile(r"\b(20\d\d)\b")


def record_fields(text: str) -> dict[str, str]:
    """The record as a label -> value map.

    Everything after a label belongs to it until the next label starts. A field
    the page does not carry is simply absent, which is what lets the caller tell
    "this record has no value" from "we could not read the value".
    """
    parts = LABEL_SPLIT.split(_clean(text))
    fields: dict[str, str] = {}
    for index in range(1, len(parts) - 1, 2):
        label, value = parts[index], _clean(parts[index + 1])
        if value and label not in fields:
            fields[label] = value
    return fields


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def page_text(html: str) -> str:
    """The readable text of a page, with the site furniture removed."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "form", "noscript"]):
        tag.decompose()
    return _clean(soup.get_text("  "))


def records_query(company: str) -> str:
    """The government-records search URL for one company."""
    return RECORDS_SEARCH.format(query=quote_plus(company))


def press_query(template: str, company: str, program: str | None, region: str | None) -> str:
    """One publisher's search URL: the company, its programme and its province.

    The programme and the province are in the query because a company name on
    its own returns whatever shares a word with it, and because the coverage we
    are looking for is coverage OF THE FUNDING — a press release about a federal
    contribution names all three.
    """
    terms = [company]
    if program:
        terms.append(re.split(r"[–—-]", program)[0].strip())
    if region and region in PROVINCE_TERMS:
        terms.append(PROVINCE_TERMS[region])
    return template.format(query=quote_plus(" ".join(t for t in terms if t)))


def article_links(html: str, base: str) -> list[str]:
    """Candidate article URLs from a publisher's search page, in order."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[str] = []
    for anchor in soup.select("a[href]"):
        href = (anchor.get("href") or "").strip()
        if not ARTICLE_LINK.search(href):
            continue
        absolute = urljoin(base, href).split("#")[0].split("?")[0]
        if absolute not in out:
            out.append(absolute)
    return out


def record_links(html: str) -> list[str]:
    """Candidate government record URLs from the grants search page."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[str] = []
    for anchor in soup.select("a[href]"):
        href = (anchor.get("href") or "").strip()
        if RECORD_LINK not in href:
            continue
        absolute = href if href.startswith("http") else RECORDS_HOST + href
        if absolute not in out:
            out.append(absolute)
    return out


def is_the_same_company(listed: str | None, ours: str | None) -> bool:
    """Whether a record's recipient is the company whose file we are filling.

    Normalised-key equality, or one key wholly containing the other where the
    shorter is long enough not to be a coincidence. The same test `grant_news`
    applies to an Indiana round announcement, run through the Canadian
    normaliser so that Ltée and Limitée are one company.
    """
    mine, theirs = company_key(ours), company_key(listed)
    if not mine or not theirs:
        return False
    if mine == theirs:
        return True
    shorter, longer = sorted((mine, theirs), key=len)
    return len(shorter) >= 8 and shorter in longer


def company_sentences(text: str, company: str) -> list[str]:
    """The sentences that actually name this company."""
    mine = company_key(company)
    head = mine.split(" ")[0] if mine else ""
    if not head or len(head) < 4:
        return []
    return [
        _clean(sentence)
        for sentence in re.split(r"(?<=[.!?])\s+", text or "")
        if head in company_key(sentence)
    ]


@register
class CanadaNewsNode(Node):
    """Read the federal record and the Canadian trade press for this company."""

    name: ClassVar[str] = "canada_news"
    depends_on: ClassVar[tuple[str, ...]] = ("normalize_identity",)
    max_attempts: ClassVar[int] = 3

    source_adapters: ClassVar[tuple[str, ...]] = ("canada_gc",)
    """The source whose awards these backends report on.

    The mirror of the rule `grant_news` states from the other side: searching a
    Canadian trade title for an Indiana Manufacturing Readiness Grant is a
    request to somebody's server with a known answer."""

    priorities: ClassVar[tuple[str, ...]] = ("P1",)
    """Queued only for the companies somebody is about to call.

    `lib/nodes.nodes_for` reads this, so a P2 never gets a work item that could
    only ever record a skip. The bulk of the Canadian award data already arrives
    through the loader; this is a targeted top-up, and it is worth three fetches
    for a company an operator is about to phone and not for one they are not."""

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        adapter = prospect.get("source_adapter") or DEFAULT_ADAPTER
        if adapter not in self.source_adapters:
            return NodeResult(
                skipped=True, skip_kind=SkipKind.PERMANENT,
                skip_reason=(
                    f"source_adapter={adapter}; the backends this node reads cover "
                    f"Canadian federal funding only"),
            )
        if prospect.get("priority") != "P1":
            return NodeResult(
                skipped=True,
                skip_reason=(
                    f"priority is {prospect.get('priority')!r}; this node is a "
                    f"P1-only top-up over the bulk loader"),
            )

        company = str(prospect.get("company_name") or "")
        notes: list[str] = [
            "canada.ca news search not used: it ignores its own keyword parameter "
            "and returns the whole unfiltered archive",
            "the federal news API not used: it filters by department only, and "
            "answers an unknown parameter with an empty feed rather than an error",
        ]
        patches: list[dict[str, Any]] = []

        record_patches, record_notes = await self._read_records(prospect, company, ctx)
        patches.extend(record_patches)
        notes.extend(record_notes)

        press_patches, press_notes = await self._read_press(prospect, company, ctx)
        patches.extend(press_patches)
        notes.extend(press_notes)

        return NodeResult(evidence_patch=merge_patches(*patches), notes=notes)

    async def _read_records(
        self, prospect: dict, company: str, ctx: RunContext
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Federal award records naming this company. Government pages, Tier 1."""
        notes: list[str] = []
        try:
            results = await ctx.fetch(records_query(company))
        except (FetchError, RobotsDisallowed) as exc:
            return [], [f"the federal records search was unavailable ({exc})"]

        candidates = record_links(results.text)
        if not candidates:
            return [], [f"the federal records search returned no records for {company!r}"]

        patches: list[dict[str, Any]] = []
        matched = 0
        for url in candidates[:MAX_RECORDS]:
            try:
                page = await ctx.fetch(url)
            except (FetchError, RobotsDisallowed):
                continue
            text = page_text(page.text)
            fields = record_fields(text)
            listed = (fields.get("Recipient's Legal Name")
                      or fields.get("Recipient Legal Name")
                      or fields.get("Recipient Operating Name"))
            if not listed or not is_the_same_company(listed, company):
                continue
            matched += 1
            claims = self._record_claims(text, str(page.url))
            if claims:
                patches.append(block_patch(BLOCK2_GRANT_FUNDED, claims))
            reading = hc.best(fields.get("Description", ""))
            if reading:
                patches.append(block_patch(BLOCK8_FINANCIAL_SCALE, {
                    "employee_count": make_claim(hc.claim_value(reading), Tier.T1,
                                                 str(page.url)),
                }))
                notes.append(f"headcount {reading.words} stated on the federal record")
        notes.append(
            f"{matched} of {len(candidates[:MAX_RECORDS])} federal record(s) read "
            f"named this company; the rest matched the search and not the recipient")
        return patches, notes

    def _record_claims(self, text: str, url: str) -> dict[str, Any]:
        """What one federal record states, as claims. Everything here is Tier 1."""
        fields = record_fields(text)
        claims: dict[str, Any] = {}

        amount = AMOUNT.search(fields.get("Agreement Value", ""))
        program = fields.get("Program Name") or fields.get("Program") or fields.get(
            "Agreement Title")
        year = RECORD_YEAR.search(
            fields.get("Agreement Start Date") or fields.get("Agreement Date", ""))
        if amount and program:
            claims["federal_award"] = make_claim(
                f"${amount.group(1)} — {program}"
                + (f", {year.group(1)}" if year else ""),
                Tier.T1, url)

        # The project description is the most useful sentence on the page and
        # the one nothing else in the pipeline holds: it is the company saying,
        # to a funder, what it is actually trying to build. 'Expected Results'
        # is boilerplate about the programme on every IRAP record, so it is the
        # fallback rather than the first choice.
        description = fields.get("Description") or fields.get("Expected Results")
        if description and len(description) > 40:
            claims["federal_award_purpose"] = make_claim(
                description[:600], Tier.T1, url)

        title = fields.get("Agreement Title")
        if title and len(title) > 12:
            claims["federal_award_title"] = make_claim(title[:300], Tier.T1, url)
        return claims

    async def _read_press(
        self, prospect: dict, company: str, ctx: RunContext
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Canadian trade coverage naming this company. Press, Tier 2."""
        notes: list[str] = []
        patches: list[dict[str, Any]] = []
        program = prospect.get("grant_round")
        region = prospect.get("region")

        for publisher, template in PUBLISHERS:
            url = press_query(template, company, program, region)
            try:
                results = await ctx.fetch(url)
            except (FetchError, RobotsDisallowed) as exc:
                notes.append(f"{publisher} search unavailable ({exc})")
                continue
            candidates = article_links(results.text, str(results.url))
            if not candidates:
                notes.append(f"{publisher} returned no articles naming {company!r}")
                continue
            for article in candidates[:MAX_ARTICLES]:
                try:
                    page = await ctx.fetch(article)
                except (FetchError, RobotsDisallowed):
                    continue
                text = page_text(page.text)
                sentences = company_sentences(text, company)
                if not sentences:
                    continue
                found = self._press_claims(sentences, str(page.url), publisher)
                for block, claims in found.items():
                    if claims:
                        patches.append(block_patch(block, claims))
                if found.get(BLOCK8_FINANCIAL_SCALE) or found.get(BLOCK7_PEOPLE):
                    notes.append(f"{publisher} coverage read: {page.url}")
                break
        return patches, notes

    def _press_claims(
        self, sentences: list[str], url: str, publisher: str
    ) -> dict[str, dict[str, Any]]:
        """What the sentences naming this company support. Tier 2, attributed."""
        people: dict[str, Any] = {}
        scale: dict[str, Any] = {}
        body = " ".join(sentences)

        reading = hc.best(body)
        if reading:
            scale["company_size"] = make_claim(
                f"{hc.claim_value(reading)} — reported by {publisher}",
                Tier.T2, url)

        for sentence in sentences:
            speaker = SPEAKER_PATTERN.search(sentence)
            quote = QUOTE_PATTERN.search(sentence)
            if speaker and quote and "press_quote" not in people:
                people["press_quote"] = make_quote(
                    _clean(quote.group(1)), Tier.T2, url, speaker=speaker.group(1))
            money = MONEY.search(sentence)
            if money and "announced_investment" not in scale:
                scale["announced_investment"] = make_claim(
                    f"{_clean(sentence)} (reported by {publisher})", Tier.T2, url)
        return {BLOCK7_PEOPLE: people, BLOCK8_FINANCIAL_SCALE: scale}
