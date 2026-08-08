"""front_door — read the company's own site and judge how findable they are.

This node does two different jobs on one set of pages. It reads what the
company says about itself (block1), and it measures the site as an artefact
(block4, block6): is it secure, is it mobile-ready, has anyone touched it in
three years, can a customer actually reach them.

Both are T1. Their own words are T1 by definition, and the measurements are our
own direct observation of a public page — we are not repeating anyone's claim
about the site, we fetched it.

WHAT COUNTS AS A WEAK FRONT DOOR

Seven criteria, each worth one point; two or more makes `weak_front_door` true:

1. No mobile viewport meta tag.
2. Newest visible content or copyright year is more than three years old, or
   no date appears anywhere.
3. No contact or quote form on any page fetched.
4. No phone number visible.
5. Three or fewer pages reachable from the home page.
6. Site not served over valid HTTPS.
7. One or more broken internal links among the pages fetched.

The threshold is two rather than one because any single criterion has an
innocent explanation — plenty of good small-manufacturer sites take enquiries
by phone and have no form. Two independent weaknesses is the point where the
site is plausibly costing them enquiries, which is the thing we would actually
be talking to them about. The criteria that fired are stored on the flag, so
the judgment is auditable rather than a bare boolean.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, ClassVar
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from lib.claims import Tier, make_claim
from lib.evidence import (
    BLOCK1_WHAT_THEY_MAKE,
    BLOCK4_DIGITAL_FRONT_DOOR,
    BLOCK6_TECH_STACK,
    block_patch,
    flag_patch,
    merge_patches,
)
from lib.nodes import FetchError, Node, NodeResult, RobotsDisallowed, RunContext, register

MIN_WEBSITE_CONFIDENCE = 50
MAX_PAGES = 8
STALE_YEARS = 3
WEAK_THRESHOLD = 2

PAGE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "about": ("about", "our story", "who we are", "company", "history"),
    "contact": ("contact", "get in touch", "reach us", "locations"),
    "products": ("products", "services", "what we do", "solutions", "industries"),
    "careers": ("careers", "career", "jobs", "employment", "join our team", "work with us"),
    "capabilities": ("capabilities", "quote", "rfq", "request a quote", "estimating"),
}

PLATFORM_FINGERPRINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Shopify", ("cdn.shopify.com", "shopify.theme", "myshopify.com")),
    ("Wix", ("wix.com", "wixstatic.com", "_wixcssimports")),
    ("Squarespace", ("squarespace.com", "static.squarespace_context")),
    ("Weebly", ("weebly.com", "weeblycloud")),
    ("Duda", ("dudamobile.com", "d.dudacdn.com")),
    ("GoDaddy Website Builder", ("godaddysites.com", "img1.wsimg.com")),
    ("WordPress", ("/wp-content/", "/wp-includes/", "wp-json")),
)

EMBED_FINGERPRINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Google Analytics", ("google-analytics.com", "gtag/js", "googletagmanager.com")),
    ("Meta Pixel", ("connect.facebook.net", "fbevents.js")),
    ("HubSpot", ("js.hs-scripts.com", "hubspot")),
    ("Mailchimp", ("chimpstatic.com", "list-manage.com")),
    ("Calendly", ("calendly.com",)),
    ("Intercom", ("intercom.io", "intercomcdn")),
    ("Tawk.to", ("tawk.to",)),
    ("YouTube embed", ("youtube.com/embed", "youtu.be/")),
    ("reCAPTCHA", ("recaptcha",)),
)

CERTIFICATION_PATTERN = re.compile(
    r"\b(ISO\s?9001(?::\d{4})?|ISO\s?13485|ISO\s?14001|AS\s?9100[A-D]?|IATF\s?16949|"
    r"TS\s?16949|NADCAP|ITAR|FDA[- ]registered|UL\s?listed|CE\s?marked)\b",
    re.IGNORECASE,
)
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?!\d)")
ADDRESS_PATTERN = re.compile(
    r"\d{2,6}\s+[A-Z][A-Za-z.'\- ]{2,40}\s+"
    r"(?:St|Street|Rd|Road|Ave|Avenue|Dr|Drive|Blvd|Boulevard|Ln|Lane|Way|Hwy|Highway|"
    r"Pkwy|Parkway|Ct|Court|Pl|Place|Cir|Circle|Ter|Terrace|Trail|Pike)\b",
)
COPYRIGHT_PATTERN = re.compile(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(20[0-2]\d)", re.I)
VISIBLE_DATE_PATTERN = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|"
    r"December)\s+\d{1,2},\s+(20[0-2]\d)\b"
)
JOB_SHOP_PHRASES = (
    "job shop", "contract manufacturing", "contract manufacturer", "made to order",
    "made-to-order", "custom fabrication", "build to print", "build-to-print",
    "your design", "to your specifications",
)
PROPRIETARY_PHRASES = (
    "our products", "product line", "our catalog", "shop our", "product catalog",
    "our brands", "patented",
)
CUSTOMER_PHRASES = (
    "industries we serve", "markets we serve", "we serve", "our customers include",
    "customers include", "serving the",
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def classify_link(text: str, href: str) -> str | None:
    """Return which kind of page a link points at, or None if uninteresting."""
    haystack = f"{text} {href}".lower()
    for kind, keywords in PAGE_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return kind
    return None


def discover_pages(home_url: str, html: str) -> dict[str, str]:
    """Return {kind: absolute_url} for the internal pages worth reading."""
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(home_url).netloc.lower().removeprefix("www.")
    found: dict[str, str] = {}
    for anchor in soup.select("a[href]"):
        href = anchor["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(home_url, href)
        if urlparse(absolute).netloc.lower().removeprefix("www.") != host:
            continue
        kind = classify_link(_clean(anchor.get_text()), href)
        if kind and kind not in found:
            found[kind] = absolute.split("#")[0]
    return found


def detect_platform(html: str) -> str:
    """Fingerprint the site platform from its markup."""
    lowered = html.lower()
    for platform, markers in PLATFORM_FINGERPRINTS:
        if any(marker in lowered for marker in markers):
            return platform
    return "custom or unrecognised"


def detect_embeds(html: str) -> list[str]:
    """Return the third-party services visibly embedded in the markup."""
    lowered = html.lower()
    return [name for name, markers in EMBED_FINGERPRINTS if any(m in lowered for m in markers)]


def newest_year(text: str, html: str) -> int | None:
    """Return the most recent year visible as a copyright or dateline."""
    years = [int(y) for y in COPYRIGHT_PATTERN.findall(html)]
    years += [int(y) for y in VISIBLE_DATE_PATTERN.findall(text)]
    return max(years) if years else None


def describe_form(soup: BeautifulSoup, page_url: str) -> dict[str, Any] | None:
    """Return a description of the first contact-like form on a page."""
    for form in soup.find_all("form"):
        names = " ".join(
            str(field.get("name") or field.get("id") or "") for field in form.find_all(
                ("input", "textarea", "select")
            )
        ).lower()
        looks_contact = any(
            token in names for token in ("email", "message", "comment", "phone", "inquiry", "name")
        ) or form.find("textarea") is not None
        if not looks_contact:
            continue
        action = (form.get("action") or "").strip()
        target = urljoin(page_url, action) if action else page_url
        if action.startswith("mailto:"):
            target = action
        host = urlparse(target).netloc.lower().removeprefix("www.")
        page_host = urlparse(page_url).netloc.lower().removeprefix("www.")
        return {
            "posts_to": target,
            "destination": (
                "email link" if target.startswith("mailto:")
                else "same site" if host == page_host
                else f"third party ({host})"
            ),
        }
    return None


def find_verbatim(text: str, phrases: tuple[str, ...]) -> str | None:
    """Return the first sentence containing one of these phrases, verbatim."""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        lowered = sentence.lower()
        if any(phrase in lowered for phrase in phrases) and 25 < len(sentence) < 400:
            return _clean(sentence)
    return None


def weak_front_door_criteria(observations: dict[str, Any], today: date | None = None) -> list[str]:
    """Return the names of the weakness criteria this site meets."""
    today = today or date.today()
    failed: list[str] = []
    if not observations.get("mobile_viewport"):
        failed.append("no mobile viewport tag")
    year = observations.get("newest_year")
    if year is None:
        failed.append("no visible content or copyright date")
    elif today.year - year > STALE_YEARS:
        failed.append(f"newest visible date is {year}, more than {STALE_YEARS} years old")
    if not observations.get("has_form"):
        failed.append("no contact or quote form found")
    if not observations.get("has_phone"):
        failed.append("no phone number visible")
    if observations.get("pages_reachable", 0) <= 3:
        failed.append(f"only {observations.get('pages_reachable', 0)} page(s) reachable")
    if not observations.get("ssl_valid"):
        failed.append("not served over valid HTTPS")
    if observations.get("broken_links", 0) > 0:
        failed.append(f"{observations['broken_links']} broken internal link(s)")
    return failed


@register
class FrontDoorNode(Node):
    """Read the company's own site: what they make, and how findable they are."""

    name: ClassVar[str] = "front_door"
    depends_on: ClassVar[tuple[str, ...]] = ("resolve_website",)

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        confidence = prospect.get("website_confidence")
        website = prospect.get("website")
        if not website or (confidence or 0) < MIN_WEBSITE_CONFIDENCE:
            return NodeResult(
                skipped=True,
                skip_reason=(
                    f"website_confidence={confidence} is below {MIN_WEBSITE_CONFIDENCE}; "
                    f"reading the wrong company's site is worse than reading none"
                ),
            )

        notes: list[str] = []
        pages: dict[str, tuple[str, str]] = {}
        broken = 0

        home = await ctx.fetch(website)
        if home.status_code >= 400:
            raise RuntimeError(f"home page {website} returned HTTP {home.status_code}")
        home_url = str(home.url)
        pages["home"] = (home_url, home.text)
        ssl_valid = home_url.lower().startswith("https://")

        discovered = discover_pages(home_url, home.text)
        for kind, url in list(discovered.items())[: MAX_PAGES - 1]:
            try:
                response = await ctx.fetch(url)
            except (FetchError, RobotsDisallowed) as exc:
                broken += 1
                notes.append(f"{kind} page {url} could not be read ({type(exc).__name__})")
                continue
            if response.status_code >= 400:
                broken += 1
                notes.append(f"{kind} page {url} returned HTTP {response.status_code}")
                continue
            pages[kind] = (str(response.url), response.text)

        combined_html = " ".join(html for _url, html in pages.values())
        texts: dict[str, str] = {}
        form: dict[str, Any] | None = None
        mobile_viewport = False
        for kind, (url, html) in pages.items():
            soup = BeautifulSoup(html, "html.parser")
            if soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}):
                mobile_viewport = True
            form = form or describe_form(soup, url)
            for tag in soup(["script", "style"]):
                tag.decompose()
            texts[kind] = _clean(soup.get_text(" "))
        all_text = " ".join(texts.values())

        observations = {
            "mobile_viewport": mobile_viewport,
            "newest_year": newest_year(all_text, combined_html),
            "has_form": form is not None,
            "has_phone": bool(PHONE_PATTERN.search(all_text)),
            "pages_reachable": len(pages),
            "ssl_valid": ssl_valid,
            "broken_links": broken,
        }
        failed = weak_front_door_criteria(observations)
        is_weak = len(failed) >= WEAK_THRESHOLD

        return NodeResult(
            evidence_patch=merge_patches(
                block_patch(BLOCK1_WHAT_THEY_MAKE, self._block1(texts, all_text, home_url)),
                block_patch(
                    BLOCK4_DIGITAL_FRONT_DOOR,
                    self._block4(observations, form, pages, home_url, discovered),
                ),
                block_patch(BLOCK6_TECH_STACK, self._block6(combined_html, home_url)),
                flag_patch(
                    "weak_front_door", is_weak, Tier.T1, home_url,
                    criteria_met=failed, threshold=WEAK_THRESHOLD,
                ),
            ),
            notes=notes
            + [
                f"read {len(pages)} page(s): {', '.join(sorted(pages))}",
                f"weak_front_door={is_weak} on {len(failed)}/{7} criteria: "
                f"{'; '.join(failed) if failed else 'none met'}",
            ],
        )

    def _block1(self, texts: dict[str, str], all_text: str, url: str) -> dict[str, Any]:
        """What the company says it makes and who it says it sells to."""
        claims: dict[str, Any] = {}
        blurb = texts.get("about") or texts.get("home") or ""
        opening = _clean(blurb[:600])
        if opening:
            claims["self_description"] = make_claim(opening, Tier.T1, url)

        customers = find_verbatim(all_text, CUSTOMER_PHRASES)
        if customers:
            claims["who_they_sell_to"] = make_claim(customers, Tier.T1, url)

        # Only classify when the site says so itself, and keep the phrase that
        # justified it. A guess about business model would be a T4 hypothesis
        # dressed as an observation.
        job_shop = find_verbatim(all_text, JOB_SHOP_PHRASES)
        proprietary = find_verbatim(all_text, PROPRIETARY_PHRASES)
        if job_shop and not proprietary:
            claims["business_model"] = make_claim("job shop / contract manufacturer", Tier.T1, url)
            claims["business_model_basis"] = make_claim(job_shop, Tier.T1, url)
        elif proprietary and not job_shop:
            claims["business_model"] = make_claim("proprietary product company", Tier.T1, url)
            claims["business_model_basis"] = make_claim(proprietary, Tier.T1, url)

        certifications = sorted({_clean(c) for c in CERTIFICATION_PATTERN.findall(all_text)})
        if certifications:
            claims["certifications"] = [make_claim(c, Tier.T1, url) for c in certifications]
        return claims

    def _block4(
        self,
        observations: dict[str, Any],
        form: dict[str, Any] | None,
        pages: dict[str, tuple[str, str]],
        url: str,
        discovered: dict[str, str],
    ) -> dict[str, Any]:
        """The site measured as an artefact."""
        claims: dict[str, Any] = {
            "ssl_valid": make_claim(observations["ssl_valid"], Tier.T1, url),
            "mobile_viewport": make_claim(observations["mobile_viewport"], Tier.T1, url),
            "has_contact_form": make_claim(observations["has_form"], Tier.T1, url),
            "phone_present": make_claim(observations["has_phone"], Tier.T1, url),
            "pages_reachable": make_claim(observations["pages_reachable"], Tier.T1, url),
            "broken_internal_links": make_claim(observations["broken_links"], Tier.T1, url),
            "pages_read": make_claim(sorted(pages), Tier.T1, url),
        }
        if observations["newest_year"] is not None:
            claims["newest_visible_year"] = make_claim(observations["newest_year"], Tier.T1, url)
        if form:
            claims["form_posts_to"] = make_claim(form["posts_to"], Tier.T1, url)
            claims["form_destination"] = make_claim(form["destination"], Tier.T1, url)
        address = ADDRESS_PATTERN.search(" ".join(t for _u, t in pages.values()))
        claims["address_present"] = make_claim(bool(address), Tier.T1, url)
        # job_postings reads this rather than re-crawling the site to find it.
        if discovered.get("careers"):
            claims["careers_url"] = make_claim(discovered["careers"], Tier.T1, url)
        return claims

    def _block6(self, html: str, url: str) -> dict[str, Any]:
        """Platform fingerprint and visible third-party embeds."""
        claims: dict[str, Any] = {
            "site_platform": make_claim(detect_platform(html), Tier.T1, url)
        }
        embeds = detect_embeds(html)
        if embeds:
            claims["third_party_embeds"] = [make_claim(e, Tier.T1, url) for e in embeds]
        return claims
