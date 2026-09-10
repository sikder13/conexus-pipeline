"""Ecosystem rivals — who else in the region does this, and what do they advertise.

WHY THIS AMENDS AN EARLIER DECISION RATHER THAN REVERSING IT

`competitor_scan` was deliberately narrow, and its reasoning was right: "an
industry directory is a T3 aggregator whose list this pipeline may not repeat,
and our own guess from an industry keyword is a T4 inference dressed as a fact."
That argument is about **asserting that somebody is a competitor**, and it still
holds. Nothing here asserts that.

What changed is the separation of two things the old module treated as one:

* **Discovery** — how we came to look at a company. A directory listing, an
  association's member page, a targeted search, or our own dataset. This is a
  route, not a claim, and it is recorded as a route: "listed under fabricated
  metal in Indiana by X" is a statement about X's page, which is true.
* **Observation** — what that company's own site says. Advertised capabilities,
  published certifications, how a visitor asks for a quote, stated lead times,
  automation language. Every one of these is T1 for the same reason the
  prospect's own are: we fetched the page and read it.

So a directory may point us at a shop, and it may never tell us anything about
that shop. Every cell of the comparison is read off the rival's own site with the
URL and a verbatim snippet attached, and there is no cell anywhere that a
directory could fill.

WHAT IS STILL FORBIDDEN

No revenue. No market share. No headcount estimate. No ranking of who is
winning. Those have no public source and the finmodel is where exposure gets
expressed — as a scenario with its assumptions labelled, which a prospect can
argue with, rather than as a number about a third party who is not in the room.

THE VELOCITY METRIC SAYS WHAT IT COUNTED

"Nine of the twelve regional shops we could read publish a quality
certification; this prospect does not" is a defensible sentence, and the reason
it is defensible is the clause in the middle. It counts the companies we looked
at and could actually read. It is not a claim about the market, it is a claim
about a sample, and the sample travels with the number — including the discovery
routes that produced it, so a reader can judge whether the twelve were the right
twelve.

DISCOVERY IS HONEST ABOUT WHAT WORKS TODAY

Four channels are defined. Two of them work now: the competitors a prospect or
its coverage names, and the same-family, same-region companies already in our own
dataset, gathered the same way and openable one by one. The trade-association
member directories are curated and robots-checked, and most of them render their
member lists in JavaScript or put them behind a login, so today they return a
recorded absence — which is the correct output and is reported as one rather than
being quietly dropped. Targeted search needs a key and reports itself not
configured without one, exactly as the macro series do.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

from lib.evidence import CERTIFICATION_PATTERN

NAMED = "named"
DIRECTORY = "directory"
SEARCH = "search"
DATASET = "dataset"
CHANNELS: tuple[str, ...] = (NAMED, DATASET, DIRECTORY, SEARCH)
"""Discovery routes, in the order they are trusted.

A rival the prospect named is the strongest, because somebody with standing said
it out loud. Our own dataset is next, because every company in it was gathered
the way this one was and can be opened. A directory listing is a route and
nothing more. A search result is the weakest and the least reproducible."""

TARGET_RIVALS = (3, 5)
"""How many rivals a useful comparison needs.

Below three, a velocity metric is arithmetic over noise — the same reason
`lib/peers.py` widens its group under four. Above five, the observation pass
costs more requests to other people's servers than the finding is worth."""


# ------------------------------------------------------------- the features

CAPABILITY_TERMS: dict[str, tuple[str, ...]] = {
    "laser cutting": ("laser cutting", "laser cut", "fiber laser", "fibre laser"),
    "waterjet": ("waterjet", "water jet"),
    "press brake forming": ("press brake", "brake forming", "bending and forming"),
    "stamping": ("stamping", "metal stamping", "progressive die"),
    "cnc machining": ("cnc machining", "cnc mill", "cnc turning", "machining center",
                      "machining centre"),
    "5-axis machining": ("5-axis", "five-axis", "5 axis"),
    "swiss turning": ("swiss turning", "swiss screw", "swiss machining"),
    "edm": ("wire edm", " edm ", "sinker edm"),
    "welding": ("welding", "mig", "tig", "robotic weld"),
    "powder coating": ("powder coat", "powder-coat"),
    "plating and anodising": ("anodiz", "anodis", "electroplat", " plating"),
    "injection moulding": ("injection molding", "injection moulding"),
    "assembly": ("assembly", "sub-assembly", "subassembly", "kitting"),
    "additive manufacturing": ("3d printing", "additive manufactur",
                               "rapid prototyp"),
    "tool and die": ("tool and die", "tooling", "die design"),
    "inspection and metrology": ("cmm", "coordinate measuring", "metrology",
                                 "first article"),
    "engineering support": ("design for manufactur", "dfm", "engineering support",
                            "cad support"),
}
"""What a shop advertises being able to do.

A closed vocabulary, because the comparison only works if the same words are
looked for on every site. An open one — take whatever nouns the page uses — would
produce a table where each column measures something different, which is a
comparison in layout only."""

AUTOMATION_TERMS: tuple[str, ...] = (
    "automation", "automated", "robot", "robotic", "cobot", "machine vision",
    "lights out", "lights-out", "industry 4.0", "iiot", "real-time monitoring",
    "digital twin", "mes", "shop floor data", "paperless",
)

AUTOMATION_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(term) for term in AUTOMATION_TERMS) + r")\b",
    re.IGNORECASE,
)
"""Matched on word boundaries, which the plain substring search was not.

"mes" as a substring is inside "times", so a shop saying "two-week lead times on
most jobs" was recorded as describing a manufacturing execution system."""

LEAD_TIME = re.compile(
    r"\b(?:"
    r"(?:same[- ]day|next[- ]day|24[- ]hour|48[- ]hour|72[- ]hour)\s+"
    r"(?:quotes?|quoting|turnarounds?|turn[- ]arounds?|delivery|response|shipping)"
    r"|(?:\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|twelve|"
    r"twenty[- ]four|forty[- ]eight)[- ](?:hour|day|week)s?\s+"
    r"(?:lead\s*times?|turnarounds?|turn[- ]arounds?|delivery|quotes?)"
    r"|lead\s*times?\s+(?:of|from|as\s+(?:little|short)\s+as)\s+[^.;]{2,40}"
    r"|quotes?\s+(?:in|within)\s+[^.;]{2,30}"
    r")\b",
    re.IGNORECASE,
)
"""A stated lead time or turnaround, captured verbatim.

Verbatim on purpose. "Two-week lead times" and "quotes within 24 hours" are
different promises and normalising them into a number would lose which one the
shop actually made."""

QUOTE_PORTAL = re.compile(
    r"\b(?:customer|client|quote|quoting|rfq|order)\s*(?:portal|login|log[- ]in|"
    r"account|dashboard)"
    r"|\bportal\b"
    r"|\binstant\s+quote\b|\bonline\s+quot(?:e|ing)\b|\bupload\s+(?:your\s+)?"
    r"(?:cad|drawing|files?|print)",
    re.IGNORECASE,
)
"""A quoting path better than a contact form: upload a drawing, get a number.

This is the single most useful column in the table for the work we sell, because
a shop that has one has already made the investment we would be proposing, and a
region where most shops have one is a region where not having one is a
disadvantage the prospect can feel."""

QUOTE_FORM = re.compile(
    r"request\s+(?:a\s+)?quote|get\s+(?:a\s+)?quote|rfq|quote\s+request",
    re.IGNORECASE,
)

QuotingPath = Literal["portal", "form", "email", "phone", "none"]

FEATURES: tuple[tuple[str, str, str], ...] = (
    ("quoting_portal", "a quoting portal or drawing upload",
     "offer a quoting portal or a drawing upload"),
    ("published_certification", "a published quality certification",
     "publish a quality certification"),
    ("stated_lead_time", "a stated lead time or turnaround",
     "state a lead time or a turnaround"),
    ("automation_language", "automation described on the site",
     "describe automation on their own site"),
    ("secure_site", "a secure site", "serve their site securely"),
    ("mobile_ready", "a site that works on a phone",
     "have a site that works on a phone"),
)
"""The rows of the comparison, and every one of them is a yes/no a reader can
check by opening the same page we opened.

Each row carries two phrasings because the sentence needs a verb and the table
needs a noun. Writing one and bending it into the other produced "3 of 3 shops
advertise a secure site", which is not a thing anybody advertises."""


# ------------------------------------------------------------ observations


class Evidence(BaseModel):
    """One observed fact, with the page and the words it was read from."""

    model_config = ConfigDict(frozen=True)

    present: bool
    source_url: str
    quote: str = ""
    """The verbatim span. Empty only where the observation is structural — a page
    is served over https or it is not, and there is no sentence to quote."""


class RivalObservation(BaseModel):
    """What one company's own site says, read the way a prospect's is read."""

    model_config = ConfigDict(frozen=True)

    name: str
    url: str
    channel: str
    discovered_via: str
    """The route, in words. 'named on the prospect's own capabilities page',
    'listed under fabricated metal in Indiana by X', 'in our own dataset'."""

    capabilities: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    lead_times: list[str] = Field(default_factory=list)
    automation_terms: list[str] = Field(default_factory=list)
    quoting_path: QuotingPath = "none"
    features: dict[str, Evidence] = Field(default_factory=dict)

    def has(self, feature: str) -> bool:
        found = self.features.get(feature)
        return bool(found and found.present)


def _text_of(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def _quote_around(text: str, needle: str, width: int = 160) -> str:
    index = text.lower().find(needle.lower())
    if index < 0:
        return ""
    start = max(0, index - width // 3)
    return text[start:start + width].strip()


def quoting_path(text: str, soup: BeautifulSoup) -> tuple[QuotingPath, str]:
    """How a visitor asks this shop for a price, and the words that say so."""
    portal = QUOTE_PORTAL.search(text)
    if portal:
        return "portal", _quote_around(text, portal.group(0))
    form = QUOTE_FORM.search(text)
    if form or soup.find("form"):
        marker = form.group(0) if form else "a form on the page"
        return "form", _quote_around(text, marker) if form else ""
    if soup.find("a", href=re.compile(r"^mailto:", re.I)):
        return "email", ""
    if soup.find("a", href=re.compile(r"^tel:", re.I)):
        return "phone", ""
    return "none", ""


def observe_site(
    html: str,
    url: str,
    name: str,
    channel: str = DATASET,
    discovered_via: str = "",
) -> RivalObservation:
    """Read one company's public site into the comparison's own vocabulary.

    T1 throughout: we are not repeating anybody's description of this company, we
    fetched the page. Nothing is estimated because nothing here can be.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    mobile = bool(soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}))
    text = _text_of(soup)
    lowered = text.lower()

    capabilities = sorted({
        label for label, terms in CAPABILITY_TERMS.items()
        if any(term in lowered for term in terms)
    })
    certifications = sorted({
        re.sub(r"\s+", " ", m.group(0)).strip()
        for m in CERTIFICATION_PATTERN.finditer(text)
    })
    lead_times = sorted({
        re.sub(r"\s+", " ", m.group(0)).strip()
        for m in LEAD_TIME.finditer(text)
    })[:4]
    automation = sorted({m.group(0).lower() for m in AUTOMATION_PATTERN.finditer(text)})
    path, path_quote = quoting_path(text, soup)

    features = {
        "quoting_portal": Evidence(
            present=path == "portal", source_url=url, quote=path_quote),
        "published_certification": Evidence(
            present=bool(certifications), source_url=url,
            quote=_quote_around(text, certifications[0]) if certifications else ""),
        "stated_lead_time": Evidence(
            present=bool(lead_times), source_url=url,
            quote=lead_times[0] if lead_times else ""),
        "automation_language": Evidence(
            present=bool(automation), source_url=url,
            quote=_quote_around(text, automation[0]) if automation else ""),
        "secure_site": Evidence(
            present=url.lower().startswith("https://"), source_url=url),
        "mobile_ready": Evidence(present=mobile, source_url=url),
    }
    return RivalObservation(
        name=name, url=url, channel=channel,
        discovered_via=discovered_via or f"discovered by {channel}",
        capabilities=capabilities, certifications=certifications,
        lead_times=lead_times, automation_terms=automation,
        quoting_path=path, features=features,
    )


# ------------------------------------------------------------- the comparison


class GapRow(BaseModel):
    """One feature, the prospect's answer, and every rival's."""

    model_config = ConfigDict(frozen=True)

    feature: str
    words: str
    verb: str = ""
    """The phrasing the velocity sentence uses. See FEATURES."""

    prospect: Evidence | None
    rivals: dict[str, Evidence] = Field(default_factory=dict)

    @property
    def rivals_with(self) -> int:
        return sum(1 for e in self.rivals.values() if e.present)

    @property
    def rivals_measured(self) -> int:
        return len(self.rivals)

    @property
    def prospect_has(self) -> bool:
        return bool(self.prospect and self.prospect.present)

    @property
    def is_gap(self) -> bool:
        """A gap is the prospect lacking something most readable rivals show."""
        if self.prospect_has or self.rivals_measured < 2:
            return False
        return self.rivals_with * 2 > self.rivals_measured


class VelocityLine(BaseModel):
    """The sentence a gap becomes, with its denominator in it."""

    model_config = ConfigDict(frozen=True)

    feature: str
    sentence: str
    rivals_with: int
    rivals_measured: int
    source_urls: list[str] = Field(default_factory=list)


class GapTable(BaseModel):
    """The whole comparison, and the basis it rests on."""

    model_config = ConfigDict(frozen=True)

    company: str
    rows: list[GapRow] = Field(default_factory=list)
    observations: list[RivalObservation] = Field(default_factory=list)
    basis: str = ""
    channels_used: list[str] = Field(default_factory=list)
    shortfall: str = ""
    """Why there are fewer rivals than wanted, when there are."""

    @property
    def usable(self) -> bool:
        return len(self.observations) >= TARGET_RIVALS[0]

    def gaps(self) -> list[GapRow]:
        return [row for row in self.rows if row.is_gap]

    def velocity(self) -> list[VelocityLine]:
        """One sentence per gap, each carrying what it counted."""
        lines = []
        for row in self.gaps():
            lines.append(VelocityLine(
                feature=row.feature,
                sentence=(
                    f"{row.rivals_with} of the {row.rivals_measured} regional "
                    f"shops we could read {row.verb or 'advertise ' + row.words}; "
                    f"{self.company} does not."
                ),
                rivals_with=row.rivals_with,
                rivals_measured=row.rivals_measured,
                source_urls=[
                    e.source_url for e in row.rivals.values() if e.present
                ],
            ))
        return lines

    def scarcity(self) -> list[VelocityLine]:
        """Features almost nobody in the sample has, prospect included.

        The velocity metric only fires when the prospect is behind. The first
        live batch found the opposite shape far more often and it is the more
        useful one: three of ninety-nine regional shops read offered a quoting
        portal, and none of the twenty prospects did. "Nobody in this market has
        one" is a stronger thing to open a call with than "you are behind",
        because it is an opening rather than a criticism.

        Reported separately from the gaps and never mixed with them, because the
        two sentences mean opposite things about the same number.
        """
        lines = []
        for row in self.rows:
            if row.rivals_measured < 2 or row.prospect_has:
                continue
            if row.rivals_with * 4 > row.rivals_measured:
                continue
            lines.append(VelocityLine(
                feature=row.feature,
                # Phrased with the noun rather than the verb so the sentence
                # does not have to agree with a count that is sometimes one.
                sentence=(
                    f"{row.words.capitalize()} appears on only "
                    f"{row.rivals_with} of the {row.rivals_measured} regional "
                    f"sites we could read, and not on "
                    f"{self.company.rstrip('.')}'s."
                ),
                rivals_with=row.rivals_with,
                rivals_measured=row.rivals_measured,
                source_urls=[e.source_url for e in row.rivals.values()],
            ))
        return lines

    def figures(self) -> set[float]:
        """Every number the comparison is allowed to put in the prose."""
        out: set[float] = set()
        for row in self.rows:
            out.add(float(row.rivals_with))
            out.add(float(row.rivals_measured))
        return out


def build_gap_table(
    company: str,
    prospect: RivalObservation | None,
    rivals: list[RivalObservation],
    shortfall: str = "",
) -> GapTable:
    """The feature-gap table, with its basis line composed from what was used."""
    rows = []
    for feature, words, verb in FEATURES:
        rows.append(GapRow(
            feature=feature,
            words=words,
            verb=verb,
            prospect=prospect.features.get(feature) if prospect else None,
            rivals={r.name: r.features[feature] for r in rivals if feature in r.features},
        ))
    channels = sorted({r.channel for r in rivals})
    basis = (
        f"Compared against {len(rivals)} regional companies whose own sites we "
        f"read directly, found by {_and_list(channels)}. Every cell is an "
        f"observation of that company's public site, not a directory's "
        f"description of it. No revenue, share or headcount is estimated here."
    ) if rivals else (
        "No regional rival could be read, so there is no comparison to make."
    )
    return GapTable(
        company=company, rows=rows, observations=rivals,
        basis=basis, channels_used=channels, shortfall=shortfall,
    )


def _and_list(items: list[str]) -> str:
    words = {
        NAMED: "the competitors this company names itself",
        DATASET: "companies already in our own dataset",
        DIRECTORY: "a trade directory listing",
        SEARCH: "a targeted search",
    }
    spelled = [words.get(item, item) for item in items]
    if not spelled:
        return "nothing"
    if len(spelled) == 1:
        return spelled[0]
    return ", ".join(spelled[:-1]) + " and " + spelled[-1]


# --------------------------------------------------------------- directories


class DirectorySource(BaseModel):
    """A curated place regional shops list themselves, and what it is good for."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    url: str
    publisher: str
    regions: tuple[str, ...]
    families: tuple[str, ...] = ()
    """Empty means it lists manufacturers of every kind."""

    note: str = ""

    def covers(self, region: str, family: str) -> bool:
        region_ok = not self.regions or region.upper() in self.regions
        family_ok = not self.families or family in self.families
        return region_ok and family_ok


DIRECTORIES: tuple[DirectorySource, ...] = (
    DirectorySource(
        source_id="imai.members",
        url="https://www.indianamfg.com/membership/member-directory/",
        publisher="Indiana Manufacturers Association",
        regions=("IN",),
        note=(
            "robots.txt permits it. The member list itself is rendered by a "
            "third-party membership platform in JavaScript, so a fetch returns "
            "the page furniture and no members. Kept here because the source is "
            "the right one and the day it renders server-side this starts "
            "working; until then it produces a recorded absence."
        ),
    ),
    DirectorySource(
        source_id="ntma.members",
        url="https://www.ntma.org/",
        publisher="National Tooling and Machining Association",
        regions=(),
        families=("metal_fabrication", "machinery_equipment", "automotive",
                  "aerospace_defense"),
        note=(
            "robots.txt permits the site. The member directory is behind a "
            "login, and DATA-1 rule 8 forbids going behind one, so this is a "
            "route we may not take rather than one we have not built."
        ),
    ),
    DirectorySource(
        source_id="pma.members",
        url="https://www.pma.org/",
        publisher="Precision Metalforming Association",
        regions=(),
        families=("metal_fabrication", "automotive"),
        note="Same shape as the NTMA entry: member list behind a login.",
    ),
    DirectorySource(
        source_id="fma.members",
        url="https://www.fmamfg.org/membership/meet-our-members",
        publisher="Fabricators and Manufacturers Association",
        regions=(),
        families=("metal_fabrication",),
        note="robots.txt permits it; the list is paginated and JavaScript-rendered.",
    ),
)
"""Where regional shops list themselves, curated rather than searched.

Curated for the reason `lib/market.py` gives about its own sources: a search
result is a different page every month, and a section that silently changes its
sources is a section nobody can check. Adding one here is a visible edit with a
commit message attached.

Every entry carries the truth about whether it can actually be read today. Three
of the four cannot, for two different reasons — JavaScript rendering, and a
login this pipeline may not go behind — and saying so here is more useful than a
registry that looks fuller than it is."""


READABLE_TODAY: tuple[str, ...] = ()
"""Directory sources that currently yield members to an identified crawler.

Empty, and that is the honest state of it as of 2026-09-09. Trade associations
put their member lists behind membership platforms and logins, which is their
right. The dataset channel is what makes the comparison work today."""


def directories_for(region: str, family: str) -> list[DirectorySource]:
    """The curated sources that cover this region and family."""
    return [source for source in DIRECTORIES if source.covers(region, family)]


def company_links(html: str, page_url: str, publisher_host: str = "") -> list[tuple[str, str]]:
    """Outbound company links on a listing page, as (name, url).

    A generic extractor rather than one parser per directory: a listing page is
    a list of links to other people's sites, and the shape that identifies a
    member is a link that leaves the publisher's own domain carrying a name.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    host = publisher_host or (urlparse(page_url).hostname or "")
    seen: dict[str, str] = {}
    for anchor in soup.find_all("a", href=True):
        href = urljoin(page_url, anchor["href"])
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        if _same_site(parsed.hostname or "", host) or _is_social(parsed.hostname or ""):
            continue
        name = re.sub(r"\s+", " ", anchor.get_text(" ")).strip()
        if len(name) < 3 or len(name) > 80 or not re.search(r"[A-Za-z]{3}", name):
            continue
        seen.setdefault(name, f"{parsed.scheme}://{parsed.netloc}")
    return list(seen.items())


SOCIAL = (
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "instagram.com",
    "youtube.com", "tiktok.com", "pinterest.com", "google.com", "apple.com",
    "adobe.com", "wordpress.org", "eventbrite.com", "mailchimp.com",
)


def _is_social(host: str) -> bool:
    host = host.lower().removeprefix("www.")
    return any(host == s or host.endswith("." + s) for s in SOCIAL)


def _same_site(host: str, publisher: str) -> bool:
    host, publisher = host.lower().removeprefix("www."), publisher.lower().removeprefix("www.")
    if not publisher:
        return False
    return host == publisher or host.endswith("." + publisher) or publisher.endswith("." + host)


# ------------------------------------------------------------------- search


class SearchStatus(BaseModel):
    """Whether targeted search is available, in the words the section prints."""

    model_config = ConfigDict(frozen=True)

    configured: bool
    line: str


def search_status(api_key: str | None) -> SearchStatus:
    """Targeted search is key-gated and says so when it is not configured.

    There is no keyless fallback on purpose. The obvious one is to scrape a
    search engine's results page, and every engine's robots.txt forbids exactly
    that — so the keyless path is not "harder", it is a rule this pipeline does
    not break. Absent a key, discovery runs on the channels that are allowed.
    """
    if api_key:
        return SearchStatus(configured=True, line="targeted search configured")
    return SearchStatus(
        configured=False,
        line=(
            "targeted search not configured (SEARCH_API_KEY is unset); rival "
            "discovery ran on named competitors and our own dataset"
        ),
    )


def search_terms(product_category: str, region: str) -> list[str]:
    """The queries a configured search would run, in the brief's own shape."""
    category = (product_category or "").replace("_", " ").strip()
    place = (region or "").strip()
    if not category or not place:
        return []
    return [
        f"{category} {place}",
        f"{category} companies {place}",
        f"{category} shop {place} request a quote",
    ]
