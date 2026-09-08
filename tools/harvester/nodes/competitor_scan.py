"""competitor_scan — the competitors a company or its press coverage names.

WHY ONLY THE ONES THEY NAME

Naming a competitor is an assertion about a market we have not studied, and the
honest sources for it are expensive: an industry directory is a T3 aggregator
whose list this pipeline may not repeat, and our own guess from an industry
keyword is a T4 inference dressed as a fact. Neither belongs in a document an
operator reads out loud.

What is defensible is much narrower and much better: a competitor the company
itself names on its own site, or one a journalist names in coverage of them.
That is somebody with standing making the claim, and the sentence they made it
in travels with the name.

So the yield here is small by construction. Most companies name nobody, and the
correct output for those is nothing at all. The peer group in lib/peers.py is
the comparison that does the work; this adds the handful of cases where a real
rival was named out loud.

WHAT WE THEN OBSERVE

Once a name is found, the site is resolved the same way the prospect's own was —
candidate domains from the name, fetched, and believed only when the page says
the company's name back. On a verified site we make the same observations we
make on a prospect: how the front door behaves, what certifications are
published, whether automation is described. Those are T1 because we fetched the
page ourselves, exactly as they are for a prospect.

Nothing is estimated. There is no revenue figure, no headcount guess and no
market share here, because we have no source for any of them.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from bs4 import BeautifulSoup

from lib.claims import Tier, make_claim
from lib.evidence import (
    BLOCK1_WHAT_THEY_MAKE,
    BLOCK2_GRANT_FUNDED,
    BLOCK7_PEOPLE,
    BLOCK10_COMPETITORS,
    block_patch,
)
from lib.nodes import FetchError, Node, NodeResult, RobotsDisallowed, RunContext, register
from tools.harvester.nodes.front_door import CERTIFICATION_PATTERN, describe_form
from tools.harvester.nodes.website import candidate_domains, page_mentions_company

MAX_COMPETITORS = 3
MIN_NAME_WORDS = 1

COMPETITOR_PHRASES: tuple[re.Pattern[str], ...] = (
    re.compile(r"competitors?\s+(?:such as|like|including)\s+([A-Z][^.;]{2,80})"),
    re.compile(r"compet(?:e|es|ed|ing)\s+(?:with|against)\s+([A-Z][^.;]{2,80})", re.I),
    re.compile(r"unlike\s+([A-Z][A-Za-z0-9&.,'\- ]{2,60})\s*,", re.I),
    re.compile(r"compared\s+(?:to|with)\s+([A-Z][A-Za-z0-9&.,'\- ]{2,60})", re.I),
    re.compile(r"competitors?,\s+([A-Z][^.;]{2,80})", re.I),
    re.compile(r"rivals?\s+([A-Z][A-Za-z0-9&.,'\- ]{2,60})", re.I),
)
"""How a competitor gets named in prose people actually write.

The cue word is matched case-insensitively because a sentence may start with it;
the captured NAME still has to begin with a capital, because the thing being
named has to look like a company rather than like the rest of the sentence. The
first version missed "We compete with…" twice over — it required "competes" and
it required a lower-case "our".

Deliberately narrow otherwise: a loose pattern here would harvest the phrase
after every "unlike" in the language and file it as a rival."""

NOT_A_COMPANY = re.compile(
    r"^(the|our|their|other|many|most|some|any|all|we|us|they|it|this|that|these|"
    r"those|new|old|large|small|traditional|conventional|standard|typical)\b",
    re.IGNORECASE,
)

AUTOMATION_WORDS = (
    "automation", "automated", "robot", "robotic", "cobot", "machine vision",
    "cnc", "lights out", "lights-out", "industry 4.0", "iiot", "sensors",
)


CORPORATE_TAIL = frozenset({
    "inc", "inc.", "llc", "llc.", "ltd", "ltd.", "co", "co.", "corp", "corp.",
    "corporation", "company", "group", "industries", "manufacturing", "mfg",
    "mfg.", "&",
})
"""Words that belong to a company name even though they are not capitalised."""


def leading_name(text: str) -> str:
    """The company name at the front of a captured span, and nothing after it.

    "Bristol Tooling on the shorter runs" names one company and then keeps
    talking. A name is a run of capitalised words, so the run is where it stops:
    the first ordinary lower-case word ends it. Without this the stored rival
    was a fragment of a sentence, which resolves to no site and reads as noise.
    """
    out: list[str] = []
    for token in text.split():
        bare = token.strip(",.;:\"'").lower()
        if token[:1].isupper() or bare in CORPORATE_TAIL:
            out.append(token)
            continue
        break
    return " ".join(out).strip(" ,.;:'\"")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _claim_texts(prospect: dict[str, Any]) -> list[tuple[str, str]]:
    """(text, source_url) for everything a competitor name could be named in."""
    evidence = prospect.get("evidence_file") or {}
    out: list[tuple[str, str]] = []

    def take(block: str, key: str) -> None:
        value = (evidence.get(block) or {}).get(key)
        if isinstance(value, dict) and value.get("value"):
            out.append((str(value["value"]), str(value.get("source_url") or "")))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get("value"):
                    out.append((str(item["value"]), str(item.get("source_url") or "")))

    take(BLOCK1_WHAT_THEY_MAKE, "self_description_raw")
    take(BLOCK1_WHAT_THEY_MAKE, "self_description")
    take(BLOCK1_WHAT_THEY_MAKE, "business_model_basis")
    take(BLOCK2_GRANT_FUNDED, "what_the_grant_funded")
    take(BLOCK7_PEOPLE, "leadership_quotes")
    if prospect.get("industry_desc"):
        out.append((str(prospect["industry_desc"]), "the grant listing"))
    return out


def named_competitors(prospect: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(name, the sentence it was named in, source_url), for names they published.

    The sentence travels with the name because "a competitor" is a claim, and
    the only thing that makes it checkable is being able to read who said it and
    in what breath.
    """
    company = _clean(str(prospect.get("company_name") or "")).lower()
    found: dict[str, tuple[str, str]] = {}
    for text, source in _claim_texts(prospect):
        cleaned = _clean(text)
        for pattern in COMPETITOR_PHRASES:
            for match in pattern.finditer(cleaned):
                raw = _clean(match.group(1)).strip(" ,.;:'\"")
                # Cut a list at its first separator: "Acme and Beta" names two,
                # and taking the whole span would resolve neither.
                raw = leading_name(re.split(r"\s+(?:and|or)\s+|,", raw)[0])
                if not raw or NOT_A_COMPANY.match(raw):
                    continue
                if len(raw.split()) < MIN_NAME_WORDS or len(raw) < 3:
                    continue
                if raw.lower() in company or company in raw.lower():
                    continue
                sentence = next(
                    (s for s in re.split(r"(?<=[.!?])\s+", cleaned) if raw in s),
                    cleaned[:300],
                )
                found.setdefault(raw, (sentence[:300], source))
    return [(name, *rest) for name, rest in list(found.items())[:MAX_COMPETITORS]]


def observe(html: str, url: str) -> dict[str, Any]:
    """The same observations we make on a prospect, made on a competitor's site.

    T1 by the same argument: we are not repeating anyone's claim about this
    site, we fetched it. Nothing here is estimated, because nothing here can be.
    """
    soup = BeautifulSoup(html, "html.parser")
    mobile = bool(soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}))
    form = describe_form(soup, url)
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = _clean(soup.get_text(" "))
    lowered = text.lower()
    certifications = sorted({_clean(m.group(0)) for m in CERTIFICATION_PATTERN.finditer(text)})
    automation = sorted({word for word in AUTOMATION_WORDS if word in lowered})
    return {
        "front_door": {
            "https": url.lower().startswith("https://"),
            "mobile_viewport": mobile,
            "contact_form": form is not None,
        },
        "certifications_published": certifications,
        "automation_language": automation,
    }


def observation_words(found: dict[str, Any]) -> str:
    """The observation as one sentence an operator reads, not a dict."""
    front = found["front_door"]
    door = ", ".join(
        label for label, ok in (
            ("secure", front["https"]),
            ("mobile-ready", front["mobile_viewport"]),
            ("a contact form", front["contact_form"]),
        ) if ok
    ) or "none of the basics"
    certs = ", ".join(found["certifications_published"]) or "no certification we could see"
    auto = (", ".join(found["automation_language"])
            if found["automation_language"] else "no automation language")
    return f"site has {door}; publishes {certs}; describes {auto}"


@register
class CompetitorScanNode(Node):
    """Resolve and observe the competitors a company or its press names."""

    name: ClassVar[str] = "competitor_scan"
    depends_on: ClassVar[tuple[str, ...]] = ("front_door",)
    priorities: ClassVar[tuple[str, ...] | None] = ("P1",)

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        if self.priorities and prospect.get("priority") not in self.priorities:
            return NodeResult(
                skipped=True,
                skip_reason=(f"priority={prospect.get('priority')}; competitors are "
                             f"scanned for {', '.join(self.priorities)} only"),
            )

        named = named_competitors(prospect)
        notes: list[str] = []
        if not named:
            # Not a failure and not a gap to fill later: most companies name
            # nobody, and the peer group is the comparison that does the work.
            return NodeResult(
                evidence_patch=block_patch(BLOCK10_COMPETITORS, {
                    "named_by_them": make_claim(
                        "no competitor is named on their site or in coverage of them",
                        Tier.T1, prospect.get("website") or "https://example.invalid"),
                }),
                notes=["no competitor named in their own words or in press coverage; "
                       "none was inferred, and the peer comparison stands alone"],
            )

        claims: dict[str, Any] = {}
        observed: list[dict[str, Any]] = []
        for name, sentence, source in named:
            claims.setdefault("named_by_them", []).append(
                make_claim(f"{name} — named as a competitor: “{sentence}”",
                           Tier.T1 if source else Tier.T2,
                           source or (prospect.get("website") or "https://example.invalid"))
            )
            site, html = await self._resolve(name, ctx)
            if not site:
                notes.append(f"{name}: named, but no site of theirs could be verified")
                continue
            found = observe(html, site)
            observed.append({"name": name, "url": site, **found})
            claims.setdefault("observed", []).append(
                make_claim(f"{name}: {observation_words(found)}", Tier.T1, site)
            )
            notes.append(f"{name}: observed at {site}")

        return NodeResult(
            evidence_patch=block_patch(BLOCK10_COMPETITORS, claims),
            notes=notes or ["competitors named but none verifiable"],
        )

    async def _resolve(self, name: str, ctx: RunContext) -> tuple[str, str]:
        """Find a competitor's own site, or admit we could not.

        Same standard the prospect's own site is held to: a domain that resolves
        is not a domain that belongs to this company, so the page has to say the
        name back before we read anything off it.
        """
        for url in candidate_domains(name)[:4]:
            try:
                response = await ctx.fetch(url)
            except (FetchError, RobotsDisallowed):
                continue
            if response.status_code >= 400:
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            if page_mentions_company(_clean(soup.get_text(" ")), name):
                return str(response.url), response.text
        return "", ""


__all__ = [
    "CompetitorScanNode",
    "leading_name",
    "named_competitors",
    "observation_words",
    "observe",
]
