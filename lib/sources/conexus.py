"""Conexus Indiana / IEDC Manufacturing Readiness Grant recipient listing.

Two public pages make up this source, and they carry different things:

* https://conexusindiana.com/mrg-recipients/ is the authoritative list. Each
  recipient is a `div.grant-modal` carrying the company name, a narrative
  description, the county, and — for most — a link to the company website.
* https://conexusindiana.com/drive-industry-success/manufacturing-readiness-grants/
  renders the same companies as map markers, and is the only page that links
  the Conexus case-study subpages. Case-study existence is a scoring signal,
  so the adapter reads both and joins them on the normalised company name.

WHAT THIS SOURCE DOES NOT PUBLISH
The listing carries no grant amount, no grant round, no award year, and no
city. Those columns exist in the schema because press releases and IEDC
records carry them, but they are not on this page, so this adapter leaves
them null. It does not estimate them from the narrative and it does not
scrape them from news coverage — that would be a different source with a
different tier, and inventing them here would be exactly the fabrication the
pipeline exists to prevent.

`tech_purchased` is populated only when the description contains a verbatim
sentence about what the company is buying ("The company will purchase ..."),
which is a substring of the source text, not a summary of it. Where no such
sentence exists the field stays null and the full narrative remains in
`industry_desc`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import httpx
from bs4 import BeautifulSoup

from lib.config import settings
from lib.geo import canonical_county
from lib.nodes import RunContext
from lib.sources.base import RawProspect, SourceAdapter

RECIPIENTS_URL = "https://conexusindiana.com/mrg-recipients/"
GRANTS_URL = "https://conexusindiana.com/drive-industry-success/manufacturing-readiness-grants/"

SNAPSHOT_DIR = Path("data/raw/conexus")
"""Raw pages are kept so a parse can be reproduced or diffed after the site
changes. Gitignored — the snapshots are someone else's content, not ours."""

_LEGAL_NOISE = re.compile(
    r"\b(inc|llc|l\.l\.c|llp|ltd|co|corp|corporation|incorporated|company|the)\b"
)
_TECH_SENTENCE = re.compile(r"(?:^|(?<=[.!?])\s)(The company [^.!?]*[.!?])")


def normalize_name(name: str | None) -> str:
    """Reduce a company name to a comparison key.

    Used only to match the same company across the two Conexus pages and to
    spot duplicates in the extractor. Never written to the database — the
    original spelling is what the source said, and that is what we keep.

    Runs of single letters are joined back together so that a punctuated
    initialism and a bare one collapse to the same key ('S.U.S. Cast' and 'SUS
    Cast'). A lone initial keeps its space, so 'J. Jones Machine' does not
    become 'jjones machine' and collide with something else.
    """
    stripped = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    cleaned = re.sub(r"\s+", " ", _LEGAL_NOISE.sub("", stripped)).strip()
    return re.sub(r"\b(?:[a-z] ){1,}[a-z]\b", lambda m: m.group(0).replace(" ", ""), cleaned)


def _extract_tech_purchased(description: str) -> str | None:
    """Return the verbatim sentence naming what the grant funded, if present."""
    matches = _TECH_SENTENCE.findall(description or "")
    return " ".join(match.strip() for match in matches) if matches else None


def parse_case_study_urls(grants_html: str) -> dict[str, str]:
    """Map normalised company name -> Conexus case-study URL, from the map page."""
    soup = BeautifulSoup(grants_html, "html.parser")
    found: dict[str, str] = {}
    for marker in soup.select("div.acf-map div.marker"):
        link = marker.select_one("div.button-wrapper a[href]")
        if link:
            found[normalize_name(marker.get("data-grant-title"))] = link["href"].strip()
    return found


def parse_recipients(recipients_html: str, case_studies: dict[str, str] | None = None) -> list:
    """Parse the recipient listing into RawProspect records."""
    soup = BeautifulSoup(recipients_html, "html.parser")
    case_studies = case_studies or {}
    prospects: list[RawProspect] = []

    for modal in soup.select("div.grants > div.grant-modal"):
        heading = modal.select_one("h3")
        name = heading.get_text(" ", strip=True) if heading else ""
        if not name:
            continue

        content = modal.select_one("div.content")
        description = content.get_text(" ", strip=True) if content else ""
        county_link = modal.select_one("div.county a")
        county = canonical_county(county_link.get_text(" ", strip=True)) if county_link else None

        website_link = modal.select_one("a.conexus-btn[href]")
        website = website_link["href"].strip() if website_link else None

        prospects.append(
            RawProspect(
                company_name=name,
                source_url=RECIPIENTS_URL,
                county=county,
                city=None,  # not published on this page
                industry_desc=description or None,
                website=website or None,
                grant_amount=None,  # not published on this page
                grant_round=None,  # not published on this page
                grant_year=None,  # not published on this page
                tech_purchased=_extract_tech_purchased(description),
                case_study_url=case_studies.get(normalize_name(name)),
            )
        )
    return prospects


class ConexusAdapter(SourceAdapter):
    """Reads the Conexus Indiana MRG recipient listing."""

    adapter_id: ClassVar[str] = "conexus_iedc"

    def __init__(self, ctx: RunContext | None = None, save_snapshot: bool = True) -> None:
        self.ctx = ctx
        self.save_snapshot = save_snapshot

    async def _get(self, ctx: RunContext, url: str, filename: str) -> str:
        """Fetch one page through the politeness gate and snapshot it."""
        response = await ctx.fetch(url)
        html = response.text
        if self.save_snapshot:
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            (SNAPSHOT_DIR / filename).write_text(html, encoding="utf-8")
        return html

    async def extract(self) -> list[RawProspect]:
        """Fetch both Conexus pages and return every listed recipient."""
        if self.ctx is not None:
            return await self._extract_with(self.ctx)
        async with httpx.AsyncClient() as client:
            return await self._extract_with(RunContext(client, settings))

    async def _extract_with(self, ctx: RunContext) -> list[RawProspect]:
        recipients_html = await self._get(ctx, RECIPIENTS_URL, "mrg_recipients.html")
        grants_html = await self._get(ctx, GRANTS_URL, "mrg_page.html")
        return parse_recipients(recipients_html, parse_case_study_urls(grants_html))
