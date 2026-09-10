"""headcount_harvest — how many people work there, from sources that may say so.

WHY A NODE OF ITS OWN

Headcount stopped being a nice-to-have the moment `lib/anchors.py` began sizing
every volume band in an analysis to it. Before that, a missing headcount cost a
sentence; now it costs the whole document its anchor, and twenty-five Canadian
analyses came out quoting the same annual figure because twenty-five companies
had no size on file and therefore got the same default band.

So the question is asked directly rather than picked up in passing by nodes that
were looking for something else.

WHERE A HEADCOUNT MAY COME FROM, AND WHERE IT MAY NOT

May: the company's own pages — about, team, careers, home — which are their own
words about their own payroll and are Tier 1. May: a case study, which is
somebody else reporting them, and is Tier 2.

May NOT: LinkedIn, ZoomInfo, Owler, Crunchbase, RocketReach, Apollo, or any of
the other places a headcount is easiest to get. Two reasons, and the second is
the one that actually binds. The first is that those figures are Tier 3 —
aggregator estimates, never assertable, and an anchor we may not say out loud is
an anchor we cannot correct on a call. The second is DATA-1 rule 8: public
sources only, nothing behind a login. LinkedIn's headcount is behind one.

The blocklist below is belt and braces over both. `ctx.fetch` already honours
robots.txt, and every aggregator on the list disallows this, so in practice the
fetch would refuse first. The list exists so that a future change to somebody
else's robots.txt cannot quietly make an aggregator readable.

WHAT IS DELIBERATELY NOT A HEADCOUNT

Open roles. A company advertising nine jobs is telling you about its year, not
about its payroll, and `lib/headcount.py` keeps the two apart by construction.
This node records the posting count as a hiring-activity signal, in block 3
where the postings themselves live, and it is marked as our count of their
pages rather than as anything they said.

Job creation promised to a funder is not a headcount either — "the project will
create 15 jobs" would put fifteen people into a six-person shop with a
government citation attached. That refusal lives in `lib/headcount.py`, tested
there, and this node inherits it.
"""

from __future__ import annotations

from typing import Any, ClassVar

from bs4 import BeautifulSoup

from lib import headcount as hc
from lib.claims import Tier, as_derivation, make_claim, origin_domain
from lib.evidence import (
    BLOCK3_HIRING_SIGNALS,
    BLOCK8_FINANCIAL_SCALE,
    block_patch,
    merge_patches,
    read_block,
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
from tools.harvester.nodes.front_door import discover_pages

BANNED_DOMAINS: frozenset[str] = frozenset({
    "linkedin.com", "zoominfo.com", "owler.com", "crunchbase.com",
    "rocketreach.co", "apollo.io", "lusha.com", "dnb.com", "glassdoor.com",
    "indeed.com", "ziprecruiter.com", "manta.com", "buzzfile.com",
    "leadiq.com", "signalhire.com", "clearbit.com", "pitchbook.com",
})
"""Places a headcount is easy to get and may not be taken from.

Some are aggregators, whose figures are Tier 3 and may never be asserted. Some
are job boards, whose counts are of postings rather than of people. All of them
disallow crawling, so this list should never fire — it is here so that if one of
them changes its robots.txt tomorrow, this node still declines."""

OWN_PAGE_KINDS: tuple[str, ...] = ("about", "careers", "contact", "products")
"""Pages on a company's own site worth reading for a size sentence.

'about' and 'careers' carry almost all of them — "we are a team of 40", "join
our 120 people". 'contact' and 'products' are read because a small shop often
has no about page and puts its story on whichever page it does have."""

MAX_PAGES = 4
"""How many pages of one site this node will read.

front_door already fetched up to eight and this is a second visit to the same
host, serialised behind the same per-host lock. Four is enough to reach the
about and careers pages and cheap enough to run across 872 companies."""

EMPLOYEE_COUNT = "employee_count"
COMPANY_SIZE = "company_size"
HIRING_ACTIVITY_KEY = "hiring_activity"
"""`peers.size_of` reads the first two, in that order, and prefers the first.

The names are its names rather than better ones: a headcount written under a
third key would be invisible to the one function that decides company size, and
a size fact nothing reads is a fact we paid to gather and did not use."""


def banned(url: str | None) -> bool:
    """Whether this URL is one of the places a headcount may not come from."""
    return origin_domain(url) in BANNED_DOMAINS


def page_text(html: str) -> str:
    """The readable text of a page, with the furniture removed.

    Navigation and footers are stripped because a site-wide footer saying "800
    employees strong" on every page of a franchise site would otherwise be read
    once per page and look like four independent sources for one number.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "form", "noscript"]):
        tag.decompose()
    return soup.get_text(" ")


def open_role_count(evidence: dict[str, Any]) -> int:
    """How many roles `job_postings` recorded, as a count of postings.

    Read from the evidence rather than re-fetched: the careers page was already
    parsed by the node whose job that is, and counting its output twice from two
    readings of the same page is how two numbers about one thing appear.
    """
    roles = read_block(evidence, BLOCK3_HIRING_SIGNALS).get("open_roles")
    return len(roles) if isinstance(roles, list) else 0


@register
class HeadcountHarvestNode(Node):
    """Read how many people work at this company, from sources allowed to say."""

    name: ClassVar[str] = "headcount_harvest"
    depends_on: ClassVar[tuple[str, ...]] = ("front_door",)
    max_attempts: ClassVar[int] = 3

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        website = prospect.get("website")
        evidence = prospect.get("evidence_file") or {}
        notes: list[str] = []
        patches: list[dict[str, Any]] = []

        posted = open_role_count(evidence)
        if posted:
            careers = self._careers_url(evidence) or website
            signal = hc.hiring_activity(posted, "their own careers page")
            patches.append(block_patch(BLOCK3_HIRING_SIGNALS, {
                HIRING_ACTIVITY_KEY: as_derivation(
                    make_claim(
                        f"{signal.phrase} — a hiring-activity signal, NOT a headcount: "
                        f"it counts roles they are advertising, which are people they "
                        f"do not yet have",
                        Tier.T1, careers),
                    "our count of the open roles job_postings read from their own "
                    "careers page"),
            }))
            notes.append(f"{posted} open role(s) recorded as hiring activity, not headcount")

        if not website:
            notes.append("no website resolved, so no page of theirs could be read")
            return NodeResult(
                evidence_patch=merge_patches(*patches), notes=notes,
                skipped=not patches,
                skip_reason="no website resolved yet" if not patches else None,
            )
        if banned(website):
            return NodeResult(
                skipped=True, skip_kind=SkipKind.PERMANENT,
                skip_reason=f"{origin_domain(website)} is not a source a headcount "
                            f"may be taken from",
            )

        found, read_notes = await self._read_own_pages(website, ctx)
        notes.extend(read_notes)

        if found is not None:
            reading, url = found
            patches.append(block_patch(BLOCK8_FINANCIAL_SCALE, {
                EMPLOYEE_COUNT: make_claim(hc.claim_value(reading), Tier.T1, url),
            }))
            notes.append(
                f"headcount {reading.words} read from their own page {url} "
                f"(rule: {reading.rule})")
            return NodeResult(
                prospect_patch=self._size_patch(prospect, reading, Tier.T1),
                evidence_patch=merge_patches(*patches), notes=notes)

        case_found, case_notes = await self._read_case_study(prospect, ctx)
        notes.extend(case_notes)
        if case_found is not None:
            reading, url = case_found
            patches.append(block_patch(BLOCK8_FINANCIAL_SCALE, {
                COMPANY_SIZE: make_claim(hc.claim_value(reading), Tier.T2, url),
            }))
            notes.append(f"headcount {reading.words} read from the case study {url}")
            return NodeResult(
                prospect_patch=self._size_patch(prospect, reading, Tier.T2),
                evidence_patch=merge_patches(*patches), notes=notes)

        notes.append(
            "no page of theirs and no case study states how many people work "
            "there; headcount stays null and the analysis says so rather than "
            "assuming a size")
        return NodeResult(evidence_patch=merge_patches(*patches), notes=notes)

    def _careers_url(self, evidence: dict[str, Any]) -> str | None:
        claim = read_block(evidence, "block4_digital_front_door").get("careers_url")
        return str(claim.get("value")) if isinstance(claim, dict) else None

    def _size_patch(self, prospect: dict, reading: hc.Reading, tier: Tier) -> dict[str, Any]:
        """The prospect columns a headcount fills, and only when they are empty.

        `employee_estimate` is written only where nothing is there. The column is
        also filled by the loaders from the source listing, and overwriting a
        figure a source published with one we parsed off a page would replace a
        provenance the extractor owns with one it does not know about.
        """
        if prospect.get("employee_estimate"):
            return {}
        return {
            "employee_estimate": str(reading.high),
            "employee_source": f"[T{int(tier)}] {reading.phrase}"[:200],
        }

    async def _read_own_pages(
        self, website: str, ctx: RunContext
    ) -> tuple[tuple[hc.Reading, str] | None, list[str]]:
        """The company's own words about its own size, best reading first."""
        notes: list[str] = []
        try:
            home = await ctx.fetch(website)
        except RobotsDisallowed as exc:
            return None, [f"their site may not be read: {exc}"]
        except FetchError as exc:
            return None, [f"their home page could not be read ({exc})"]

        pages: list[tuple[str, str]] = [(str(home.url), home.text)]
        discovered = discover_pages(str(home.url), home.text)
        for kind in OWN_PAGE_KINDS:
            url = discovered.get(kind)
            if not url or len(pages) >= MAX_PAGES or banned(url):
                continue
            try:
                page = await ctx.fetch(url)
            except (FetchError, RobotsDisallowed):
                notes.append(f"their {kind} page could not be read")
                continue
            pages.append((str(page.url), page.text))

        best: tuple[hc.Reading, str] | None = None
        for url, html in pages:
            reading = hc.best(page_text(html))
            if reading and (best is None or reading.high > best[0].high):
                best = (reading, url)
        notes.append(f"read {len(pages)} of their own page(s) for a size sentence")
        return best, notes

    async def _read_case_study(
        self, prospect: dict, ctx: RunContext
    ) -> tuple[tuple[hc.Reading, str] | None, list[str]]:
        """A case study somebody else wrote about them. Tier 2, and read second."""
        url = prospect.get("case_study_url")
        if not url or banned(url):
            return None, []
        try:
            page = await ctx.fetch(url)
        except (FetchError, RobotsDisallowed) as exc:
            return None, [f"the case study could not be read ({exc})"]
        reading = hc.best(page_text(page.text))
        return ((reading, str(page.url)) if reading else None), []
