"""resolve_website — find and verify the company's official web presence.

Almost every later node depends on this one: you cannot read a company's own
words, find a named human, or judge a digital front door without first knowing
where the front door is.

The node prefers the domain Conexus itself published for the grant recipient,
because that came from the company on a government-administered application.
Failing that, it tries the obvious domain constructions from the company name.
Either way the candidate is fetched and the page is checked for the company's
own name before we believe it — a domain that resolves is not the same thing as
a domain that belongs to this company.

Three outcomes get recorded rather than treated as failure, because for small
Indiana manufacturers all three are common and all three are useful to know:
a parked domain, a business whose only web presence is a Facebook page, and a
domain that has simply died. Each lands with a low confidence and a note, and
anything under 70 goes to a human.

Every score is paired with a note naming the method, so a reviewer can see why
the machine believed what it believed instead of having to re-derive it.
"""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from lib.claims import Tier, make_claim
from lib.fingerprints import assess, name_in_context
from lib.nodes import FetchError, Node, NodeResult, RobotsDisallowed, RunContext, register


def _today() -> str:
    from datetime import date as _date
    return _date.today().isoformat()


MIN_TRUSTED_CONFIDENCE = 70
"""Below this a human looks at it before anything is said to the company."""

CONFIDENCE = {
    "source_verified": 95,
    "source_unverified": 40,
    "constructed_verified": 75,
    "constructed_unverified": 30,
    "social_only": 40,
    "parked": 20,
    "compromised": 10,
    "not_found": 0,
}
"""How the match was made determines the score. Nothing else moves it.

The two *_unverified values sit deliberately below MIN_TRUSTED_CONFIDENCE. They
used to be 80 and 50, and 80 is above the trust floor — so a page that failed
the name check outright still produced a trusted T1 website claim and no review
flag. That is how Decatur Plastic Products came to be a P1 built on an
Indonesian gambling site, and how 58 other records were stored on domains whose
verification had explicitly failed. A failed check must never outrank the
threshold that exists to catch it."""

SOCIAL_HOSTS = (
    "facebook.com", "linkedin.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
)

PARKED_MARKERS = (
    "this domain is for sale", "buy this domain", "domain may be for sale",
    "parked domain", "parkingcrew", "sedoparking", "godaddy.com/domainsearch",
    "the domain you are looking for", "under construction", "coming soon",
)

GENERIC_TOKENS = frozenset({
    "industries", "industrial", "manufacturing", "products", "solutions", "technologies",
    "technology", "group", "machine", "machining", "tool", "tools", "systems", "services",
    "company", "enterprises", "international", "national", "american", "precision",
    "engineering", "fabrication", "welding", "design", "custom", "quality", "advanced",
})


def distinctive_tokens(name: str) -> list[str]:
    """Tokens from a company name that would identify it on its own web page."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if len(t) >= 4]
    distinctive = [t for t in tokens if t not in GENERIC_TOKENS]
    return distinctive or tokens


def candidate_domains(name: str) -> list[str]:
    """Obvious domain constructions for a company name, most specific first."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if t]
    tokens = [t for t in tokens if t not in {"inc", "llc", "ltd", "co", "corp", "the"}]
    if not tokens:
        return []
    candidates = ["".join(tokens)]
    if len(tokens) > 2:
        candidates.append("".join(tokens[:2]))
    if len(tokens) > 1:
        candidates.append(tokens[0])
    seen, urls = set(), []
    for stem in candidates:
        if len(stem) >= 4 and stem not in seen:
            seen.add(stem)
            urls.append(f"https://{stem}.com")
    return urls[:3]


def is_social(url: str) -> bool:
    """True when the URL points at a social profile rather than an owned site."""
    host = (urlparse(url).netloc or "").lower().removeprefix("www.")
    return any(host == social or host.endswith("." + social) for social in SOCIAL_HOSTS)


def looks_parked(page_text: str) -> bool:
    """True when a page is a domain-parking placeholder rather than a business site."""
    lowered = page_text.lower()
    return any(marker in lowered for marker in PARKED_MARKERS) and len(lowered) < 4000


def page_mentions_company(page_text: str, company_name: str) -> bool:
    """True when the page carries a distinctive part of the company's name."""
    lowered = page_text.lower()
    return any(token in lowered for token in distinctive_tokens(company_name))


def _visible_text(html: str) -> str:
    """Readable text from a page, for the fingerprint and coherence checks."""
    return re.sub(r"\s+", " ", BeautifulSoup(html or "", "html.parser").get_text(" "))


def source_website(prospect: dict) -> str | None:
    """The website the grant listing published for this company, if any."""
    evidence = prospect.get("evidence_file") or {}
    claim = (evidence.get("source") or {}).get("website")
    return claim.get("value") if isinstance(claim, dict) else None


@register
class ResolveWebsite(Node):
    """Find the company's website and score how sure we are it is theirs."""

    name: ClassVar[str] = "resolve_website"
    depends_on: ClassVar[tuple[str, ...]] = ("normalize_identity",)

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        company = prospect.get("company_name") or ""
        notes: list[str] = []

        published = source_website(prospect)
        if published:
            outcome = await self._check(ctx, published, company, "source", notes, prospect)
        else:
            notes.append("grant listing published no website; trying constructed domains")
            outcome = None
            for candidate in candidate_domains(company):
                attempt = await self._check(
                    ctx, candidate, company, "constructed", notes, prospect
                )
                # Keep the best candidate, not the last one tried: a dead third
                # guess must not discard a live second one.
                if outcome is None or attempt[1] > outcome[1]:
                    outcome = attempt
                if attempt[1] >= MIN_TRUSTED_CONFIDENCE:
                    break

        if outcome is None:
            # No published site and no constructible domain. A web search would be
            # the next move; no search backend is available without a paid API, so
            # this is recorded honestly as unresolved rather than guessed at.
            notes.append(
                "no website found: nothing published by the source and no domain "
                "could be constructed from the company name"
            )
            return NodeResult(
                prospect_patch={
                    "website_confidence": 0,
                    # not_found, not null: null means nobody has looked yet, and a
                    # record we searched and failed to place must not read the same
                    # as one we never checked.
                    "website_status": "not_found",
                    "stage": "needs_review",
                    "needs_review_reason": (
                        "no website found: nothing published by the source and no "
                        "domain could be constructed from the company name"
                    ),
                },
                notes=notes,
            )

        url, confidence, verdict = outcome
        patch: dict = {"website_confidence": confidence}
        evidence: dict = {}

        status = verdict.get("status") or ("not_found" if confidence == 0 else "ok")
        patch["website_status"] = status
        # Always written, null when there is nothing to record. Setting it only
        # when non-empty leaves a previous run's markers in place, and a stale
        # fingerprint accuses a company of something that is no longer true.
        patch["website_fingerprints"] = [
            {"marker": marker, "url": url, "checked_at": _today()}
            for marker in verdict.get("fingerprints", [])
        ] or None

        if confidence > 0:
            patch["website"] = url
            # A verified company site is the company's own words (T1). Anything we
            # could not confirm is our inference and must not be asserted (T4).
            tier = Tier.T1 if confidence >= MIN_TRUSTED_CONFIDENCE else Tier.T4
            evidence["identity"] = {"website": make_claim(url, tier, url)}

        if confidence < MIN_TRUSTED_CONFIDENCE:
            reason = (
                f"website_confidence={confidence} is below {MIN_TRUSTED_CONFIDENCE}"
                + (f"; site {status}: " + "; ".join(verdict.get("fingerprints", [])[:2])
                   if status != "ok" else
                   "; the company is not named in coherent content on this page")
            )
            patch["stage"] = "needs_review"
            patch["needs_review_reason"] = reason[:600]
            notes.append(reason)
        return NodeResult(prospect_patch=patch, evidence_patch=evidence, notes=notes)

    async def _check(
        self, ctx: RunContext, url: str, company: str, origin: str, notes: list[str],
        prospect: dict | None = None
    ) -> tuple[str, int, dict]:
        """Fetch one candidate, score it, and return what the page looked like.

        The verdict is RETURNED rather than stored on the node. One node object
        serves every prospect in a run, so an attribute on self is shared state:
        stashing the verdict there leaked one company's fingerprints onto two
        others, which is precisely the confusion this whole module exists to
        prevent.
        """
        prospect = prospect or {}
        if is_social(url):
            notes.append(
                f"{url} is a social profile, not an owned site; recorded as the "
                f"company's only known web presence"
            )
            return url, CONFIDENCE["social_only"], {}

        try:
            response = await ctx.fetch(url)
        except RobotsDisallowed:
            notes.append(f"{url} disallows crawling in robots.txt; not verified")
            return url, CONFIDENCE[f"{origin}_unverified"], {}
        except FetchError as exc:
            status = getattr(exc, "status", None)
            notes.append(f"{url} could not be fetched ({status or 'no response'}); treated as dead")
            return url, CONFIDENCE["not_found"], {}

        if response.status_code >= 400:
            notes.append(f"{url} returned HTTP {response.status_code}; treated as dead")
            return url, CONFIDENCE["not_found"], {}

        text = response.text
        final_url = str(response.url)
        if is_social(final_url):
            notes.append(f"{url} redirects to the social profile {final_url}")
            return final_url, CONFIDENCE["social_only"], {}
        page_text = _visible_text(text)
        verdict = assess(page_text, text, url, final_url, prospect.get("industry_desc"))

        if verdict["status"] == "not_found":
            notes.append(
                f"{final_url} is a parked or for-sale page, not a business site: "
                + "; ".join(verdict["fingerprints"][:2])
            )
            return final_url, CONFIDENCE["parked"], verdict

        if verdict["status"] == "compromised":
            notes.append(
                f"{final_url} does not serve this company's content: "
                + "; ".join(verdict["fingerprints"][:3])
            )
            return final_url, CONFIDENCE["compromised"], verdict

        # The name must appear inside content that also coheres with the stated
        # industry, not merely somewhere in the DOM. A stolen page that happens
        # to mention the town, or a hidden link, used to satisfy the old check.
        if name_in_context(page_text, distinctive_tokens(company),
                           prospect.get("industry_desc")):
            notes.append(
                f"{final_url} fetched OK and names the company in content that "
                f"matches the stated industry ({origin} domain, verified)"
            )
            return final_url, CONFIDENCE[f"{origin}_verified"], verdict

        notes.append(
            f"{final_url} fetched OK but the company is not named in coherent "
            f"content ({origin} domain, unverified — below the trust floor)"
        )
        return final_url, CONFIDENCE[f"{origin}_unverified"], verdict
