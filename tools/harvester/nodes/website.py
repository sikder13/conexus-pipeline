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
from lib.fingerprints import assess, full_name_present
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
    "incoherent": 15,
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
threshold that exists to catch it.

`incoherent` is the page that carries the company's full name but describes a
different business. It sits below every other live value because it is the most
misleading of them: a page that names them reads as confirmation, and this is
the one state where the name is present and the site is still not theirs."""

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


ADAPTER_TLDS: dict[str, tuple[str, ...]] = {
    "canada_gc": (".ca", ".com"),
    "conexus_iedc": (".com",),
}
DEFAULT_TLDS: tuple[str, ...] = (".com",)
"""Which top-level domains a constructed guess is worth trying.

Canadian companies register .ca at least as often as .com, and trying only .com
cost us the real Cedar Valley Selections site — cedarvalleyselections.ca — while
the same run accepted cedar.com. Guessing a TLD is cheap; the acceptance rule
below is what decides whether a guess is believed."""

MAX_CANDIDATES = 4


def candidate_domains(name: str, tlds: tuple[str, ...] = DEFAULT_TLDS) -> list[str]:
    """Obvious domain constructions for a company name, most specific first.

    The first-token-only stem is deliberately absent. It produced cedar.com for
    Cedar Valley Selections Inc., and a single leading word is not an
    abbreviation of a company name — it is usually somebody else's company.
    Whole name first, then the first two words, both of which still identify.
    """
    tokens = [t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if t]
    tokens = [t for t in tokens if t not in {"inc", "llc", "ltd", "co", "corp", "the"}]
    if not tokens:
        return []
    stems = ["".join(tokens)]
    if len(tokens) > 2:
        stems.append("".join(tokens[:2]))
    seen, urls = set(), []
    for stem in stems:
        if len(stem) < 4:
            continue
        for tld in tlds:
            url = f"https://{stem}{tld}"
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls[:MAX_CANDIDATES]


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


def _attempted(url: str, outcome: tuple[str, int, dict]) -> dict:
    """One candidate and what looking at it showed.

    Kept for every candidate, accepted or not, because the useful question after
    a wrong resolution is "what else did it try, and what did that look like" —
    and that was unanswerable for Cedar Valley.
    """
    final_url, confidence, verdict = outcome
    match = (verdict or {}).get("name_match") or {}
    coh = (verdict or {}).get("coherence") or {}
    return {
        "candidate": url,
        "resolved_to": final_url,
        "confidence": confidence,
        "status": (verdict or {}).get("status"),
        "full_name_matched": match.get("name"),
        "match_context": (match.get("context") or "")[:300] or None,
        "shared_words_with_award": coh.get("overlap") or [],
        "industry_vocabulary": coh.get("industry_hints") or [],
    }


@register
class ResolveWebsite(Node):
    """Find the company's website and score how sure we are it is theirs."""

    name: ClassVar[str] = "resolve_website"
    depends_on: ClassVar[tuple[str, ...]] = ("normalize_identity",)

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        company = prospect.get("company_name") or ""
        notes: list[str] = []

        published = source_website(prospect)
        tried: list[dict] = []
        if published:
            method = "published by the source"
            outcome = await self._check(ctx, published, company, "source", notes, prospect)
            tried.append(_attempted(published, outcome))
        else:
            method = "constructed from the company name"
            notes.append("grant listing published no website; trying constructed domains")
            outcome = None
            tlds = ADAPTER_TLDS.get(prospect.get("source_adapter"), DEFAULT_TLDS)
            for candidate in candidate_domains(company, tlds):
                attempt = await self._check(
                    ctx, candidate, company, "constructed", notes, prospect
                )
                tried.append(_attempted(candidate, attempt))
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

        # How this was decided, kept on the row. Not a claim — no `value` key —
        # so the database's claim trigger leaves it alone, the same arrangement
        # score_evidence uses for its working.
        match = verdict.get("name_match") or {}
        evidence["website_resolution"] = {
            "method": method,
            "candidates_tried": tried,
            # Two different questions, and conflating them is how a row reads as
            # confirmed when it is merely recorded: "stored" is what landed in
            # the website column, "trusted" is whether anything may be asserted
            # from it.
            "stored": None if status == "incoherent" or confidence == 0 else url,
            "trusted": (url if confidence >= MIN_TRUSTED_CONFIDENCE
                        and status != "incoherent" else None),
            "full_name_matched": match.get("name"),
            "match_context": (match.get("context") or "")[:300] or None,
            "coherence": verdict.get("coherence"),
            "checked_at": _today(),
        }
        # Always written, null when there is nothing to record. Setting it only
        # when non-empty leaves a previous run's markers in place, and a stale
        # fingerprint accuses a company of something that is no longer true.
        patch["website_fingerprints"] = [
            {"marker": marker, "url": url, "checked_at": _today()}
            for marker in verdict.get("fingerprints", [])
        ] or None

        if status == "incoherent":
            # The name is right and the business is wrong. Storing the URL would
            # hand every downstream node another company's pages to read from,
            # which is exactly how a pita-chip manufacturer acquired a US
            # healthcare firm's executives. The URL survives in the resolution
            # record above, so a human can look at what was rejected.
            patch["website"] = None
            reason = (
                f"website not accepted: {url} carries the company's name but does "
                f"not describe the business the award describes"
            )
            patch["stage"] = "needs_review"
            patch["needs_review_reason"] = reason[:600]
            notes.append(reason)
            return NodeResult(prospect_patch=patch, evidence_patch=evidence, notes=notes)

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
                   "; the company's full name is not on this page")
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
            # We are not permitted to look, so we cannot say the site is theirs
            # or anyone else's. 'unreachable' is the honest state; 'ok' would
            # claim a check that never happened.
            notes.append(f"{url} disallows crawling in robots.txt; not verified")
            return url, CONFIDENCE[f"{origin}_unverified"], {
                "status": "unreachable",
                "fingerprints": [f"robots.txt disallows fetching {url}; not assessable"],
            }
        except FetchError as exc:
            status = getattr(exc, "status", None)
            notes.append(f"{url} could not be fetched ({status or 'no response'}); treated as dead")
            return url, CONFIDENCE["not_found"], {
                "status": "unreachable",
                "fingerprints": [f"{url} could not be fetched ({status or 'no response'})"],
            }

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

        # Two conditions, both required, neither able to excuse the other.
        #
        # The FULL name must be on the page. Not a token of it: "Cedar" matched
        # cedar.com — a US healthcare-payments firm — for Cedar Valley
        # Selections Inc. of Windsor, Ontario, and that page then cleared
        # coherence on two generic words. A partial name is a different company.
        #
        # And the page must describe the business the award describes. This is
        # BLOCKING now rather than a score reduction: a page carrying the right
        # name and the wrong business is the most convincing way to be wrong,
        # because the name reads as the confirmation.
        match = full_name_present(page_text, company, prospect.get("dba_name"))
        coherent = bool((verdict.get("coherence") or {}).get("coherent"))
        verdict["name_match"] = match

        if match and coherent:
            notes.append(
                f"{final_url} fetched OK and names the company in full in content "
                f"that matches the stated industry ({origin} domain, verified)"
            )
            return final_url, CONFIDENCE[f"{origin}_verified"], verdict

        if match and not coherent:
            overlap = (verdict.get("coherence") or {}).get("overlap") or []
            hints = (verdict.get("coherence") or {}).get("industry_hints") or []
            verdict["status"] = "incoherent"
            verdict.setdefault("fingerprints", []).append(
                f"names the company but describes another business "
                f"(shared words with the award: {', '.join(overlap) or 'none'}; "
                f"industry vocabulary: {', '.join(hints) or 'none'})"
            )
            notes.append(
                f"{final_url} names the company but does not describe the business "
                f"the award describes; not accepted"
            )
            return final_url, CONFIDENCE["incoherent"], verdict

        notes.append(
            f"{final_url} fetched OK but does not carry the company's full name "
            f"({origin} domain, unverified — below the trust floor)"
        )
        return final_url, CONFIDENCE[f"{origin}_unverified"], verdict
