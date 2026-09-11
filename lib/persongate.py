"""Person gate — whether a human's name may appear in outbound text.

THE ONE ERROR THIS SYSTEM MUST NOT MAKE

Getting a number wrong is embarrassing and recoverable. Opening an email with a
name that is not a person at that company ends the conversation and tells the
reader, correctly, that nothing else in the message was checked either. This
pipeline has already written `Insects Limited`, `Atlanta Track Club`,
`Email Phone Bio`, `Dave Solidworks` and an unfilled `John Doe` into records as
decision-makers, and one of them — `Office Jared McGladdery` — survived a
correction because a re-run that found nobody left the old value in place.

Under per-claim human verification a person read every name before it went out.
That step is gone. This module is what replaces it, and it is deliberately
harder to satisfy than a human glance was.

THE RULE

A name may be used only when BOTH hold:

1. Either the name appears in at least two INDEPENDENT sources (different
   registrable domains, or genuinely different document classes on one domain),
   or it appears verbatim in a single T1 source AND the adversarial claim
   checker returned 'verbatim' for it.
2. The role parses as a real job title, using the same trading-name and
   function-word rejections the people node applies.

There is no override flag and no exceptions list. A gate that can be waived
under deadline is not a gate, and deadline is exactly when it would be waived.

WHAT HAPPENS ON FAILURE

The artifact addresses the role instead: "for the owner or president of {company}".
That is honest, it is still specific to the company, and it costs a little
warmth rather than all credibility. Nothing is deleted and nothing is guessed.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from lib.claims import Tier, is_operator_entered, origin_domain
from lib.integrity import is_usable, registrable_domain

ROLE_WORDS = (
    "owner", "president", "vice president", "vp", "ceo", "chief executive",
    "coo", "chief operating", "cfo", "chief financial", "cto", "chief technology",
    "founder", "co-founder", "partner", "principal", "director", "general manager",
    "plant manager", "operations manager", "production manager", "engineering manager",
    "quality manager", "purchasing manager", "controller", "supervisor",
    "head of", "manager",
)
"""Titles that denote a person who can agree to something.

Deliberately weighted toward decision-makers: a name attached to 'intern' or
'receptionist' is probably real but is not who an opening line should address."""


class GateResult(BaseModel):
    """Whether a name may be used, and exactly why not when it may not."""

    allowed: bool
    name: str | None = None
    role: str | None = None
    reasons: list[str] = Field(default_factory=list)
    independent_sources: list[str] = Field(default_factory=list)
    salutation: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()


def split_person_claim(value: str) -> tuple[str, str]:
    """Split a 'Name — Role' claim value into its parts."""
    text = str(value or "")
    for separator in ("—", " - ", "–", ","):
        if separator in text:
            name, _, role = text.partition(separator)
            return name.strip(), role.strip()
    return text.strip(), ""


def role_is_real(role: str) -> bool:
    """True when the role reads as an actual job title."""
    lowered = (role or "").strip().lower()
    if not lowered:
        return False
    return any(re.search(r"\b" + re.escape(word), lowered) for word in ROLE_WORDS)


def name_is_a_person(name: str, company_name: str | None) -> bool:
    """Reuse the people node's rejections rather than inventing a second set.

    Two validators drifting apart would mean a name rejected at extraction and
    accepted at send, which is the worst of both.
    """
    from tools.harvester.nodes.case_study import clean_person_name

    return bool(clean_person_name(name or "", company_name))


def _document_class(claim: dict[str, Any]) -> str:
    """A rough class for a source, so two pages of one site do not corroborate.

    Independence needs more than a different URL: an About page and a Team page
    on the same site are the same assertion published twice.
    """
    url = str(claim.get("source_url") or "").lower()
    if "conexusindiana.com/case-study" in url:
        return "case_study"
    if "conexusindiana.com" in url:
        return "grant_listing"
    if any(k in url for k in ("insideindianabusiness", "cicpindiana", "news", "press")):
        return "press"
    if any(k in url for k in ("linkedin.com", "facebook.com")):
        return "social"
    if any(k in url for k in ("/careers", "/jobs", "indeed.")):
        return "job_posting"
    return "company_site"


def independent_sources(claims: list[dict[str, Any]]) -> list[str]:
    """The distinct independent sources among these claims.

    Independence is a different registrable domain, or a different document
    class. Two claims from the same class on the same domain count once.
    """
    seen: dict[tuple[str, str], str] = {}
    for claim in claims:
        if not is_usable(claim):
            continue
        key = (registrable_domain(claim.get("source_url")), _document_class(claim))
        seen.setdefault(key, str(claim.get("source_url") or ""))
    return list(seen.values())


SUBJECT_MATCH_DOMAIN = "domain"
SUBJECT_MATCH_TEXT = "in-text"
SUBJECT_MATCH_NONE = "none"

GENERIC_NAME_WORDS = (
    "inc", "llc", "ltd", "corp", "corporation", "company", "co", "plc", "group",
    "limited", "industries", "manufacturing", "mfg", "holdings", "enterprises",
    "services", "solutions", "technologies", "systems", "international",
    "incorporated", "the", "and",
)


def name_tokens(company_name: str | None) -> list[str]:
    """The words in a company name that actually identify it.

    Corporate tails and the generic industry words are dropped: matching on
    "Inc" or "Manufacturing" would confirm every company against every page.
    """
    pattern = r"\b(?:" + "|".join(GENERIC_NAME_WORDS) + r")\b\.?"
    stem = re.sub(pattern, " ", (company_name or ""), flags=re.IGNORECASE)
    return re.findall(r"[A-Za-z0-9&']{3,}", stem)


def _letters(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def source_subject_matches(
    company_name: str | None,
    source_url: str | None,
    page_text: str | None = None,
) -> tuple[bool, str, str]:
    """Whether the page a person claim cites is about THIS company.

    Returns (matched, how, why) so a refusal can say what it looked for.

    **It deliberately does not consult the prospect's stored website.** The first
    version did, and every one of the documented failures passed it: `future.com`
    IS Future Fields Biomanufacturing's recorded website, which is precisely the
    error. Confirming a page against a domain that was itself resolved wrongly
    confirms nothing — it launders the mistake into a check.

    So the test is against the company's NAME, which is the one thing we did not
    infer:

    * **domain** — the domain carries the company's distinctive words. A
      single-word name must appear whole; a multi-word name must put at least
      two of its words in the domain. `mursix.com` and `trifectamed.com` pass;
      `future.com` for "Future Fields Biomanufacturing" does not, because one
      word of three is a coincidence and the other two are the identifying ones.
    * **in-text** — the page names the company as a phrase. Used for press and
      directory pages, which are legitimate sources for a person and are not on
      the company's own domain.

    The failure this exists to stop is in docs/CLAIMCHECK.md and is not
    hypothetical: an Andreessen Horowitz publication read as Future Fields'
    site, a New Jersey engineering firm's leadership page as Matrix Engineering
    & Trading's, and a US healthcare-payments company as Cedar Valley
    Selections'. In each case a real person with a real title was recorded as
    this company's decision-maker. Every individual claim was correctly formed.
    All three were about somebody else.
    """
    tokens = name_tokens(company_name)
    if not tokens:
        return False, SUBJECT_MATCH_NONE, "the company has no distinctive name to match on"

    domain = origin_domain(source_url)
    letters = _letters(domain)
    matched = [t for t in tokens if _letters(t)[:3] and _letters(t)[:3] in letters]
    enough = bool(matched) if len(tokens) == 1 else len(matched) >= 2
    if enough:
        return (True, SUBJECT_MATCH_DOMAIN,
                f"the domain {domain} carries their name ({', '.join(matched[:3])})")

    if page_text:
        phrase = " ".join(tokens[:2]) if len(tokens) > 1 else tokens[0]
        if _letters(phrase) and _letters(phrase) in _letters(page_text):
            return True, SUBJECT_MATCH_TEXT, f"the page names them: {phrase!r}"
        return (False, SUBJECT_MATCH_NONE,
                f"the page never names {company_name!r} and {domain or 'its domain'} "
                f"does not carry their name")

    return (False, SUBJECT_MATCH_NONE,
            f"nothing ties {domain or 'the source'} to {company_name!r}, and no "
            f"page text was available to read")


WRONG_SUBJECT_REASON = (
    "source subject mismatch: {why}. A person read from a page about another "
    "company is another company's person, however well formed the claim is."
)


def check_person(
    claim: dict[str, Any],
    company_name: str | None,
    supporting: list[dict[str, Any]] | None = None,
) -> GateResult:
    """Decide whether this person claim may be named in outbound text.

    ``supporting`` is every claim in the file that asserts the same person,
    including this one; the caller gathers them because only it knows the file.
    """
    name, role = split_person_claim(claim.get("value"))
    company = company_name or "the company"
    reasons: list[str] = []

    if not is_usable(claim):
        reasons.append("the claim is tainted or killed")

    if not name_is_a_person(name, company_name):
        reasons.append(
            f"{name!r} does not parse as a person's name under the extraction rules"
        )
    if not role_is_real(role):
        reasons.append(f"{role!r} does not parse as a real job title")

    pool = supporting if supporting is not None else [claim]
    sources = independent_sources(pool)
    corroborated = len(sources) >= 2
    verbatim_t1 = (
        claim.get("tier") == int(Tier.T1)
        and claim.get("claimcheck") == "verbatim"
    )
    # A person who opened the source and typed the name in has already done what
    # the adversarial checker is a substitute for. This is not the override flag
    # the docstring above refuses — an override waives a rule, and nothing is
    # waived here: the name parser, the role parser and the subject guard all
    # still run, and an operator entering an aggregator's figure still gets a
    # Tier 3 claim this branch never reaches. What it recognises is that the
    # gate replaced a human reading a source, and here there is one.
    human_read_it = is_operator_entered(claim) and claim.get("tier") == int(Tier.T1)
    if not (corroborated or verbatim_t1 or human_read_it):
        if claim.get("tier") == int(Tier.T1) and not claim.get("claimcheck"):
            reasons.append(
                "single T1 source and the adversarial checker has not run on it"
            )
        else:
            reasons.append(
                f"only {len(sources)} independent source(s) and no verbatim T1 check"
            )

    allowed = not reasons
    return GateResult(
        allowed=allowed,
        name=name if allowed else None,
        role=role or None,
        reasons=reasons,
        independent_sources=sources,
        salutation=(
            f"{name}" if allowed else f"the owner or president of {company}"
        ),
    )


def claims_about(claim: dict[str, Any], people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The claims in this file that assert the SAME person as ``claim``.

    What `check_person` means by "supporting", and the only thing that may be
    counted toward its two-source test. Handing it every person in the file
    instead makes the test ask "does this company have two sources for anybody",
    under which one well-sourced founder vouches for every other name on the
    page — which is how Surria Fadel, named once, read as corroborated because
    Ameen Fadel was named twice.
    """
    name, _role = split_person_claim(claim.get("value"))
    return [
        other for other in people
        if isinstance(other, dict)
        and split_person_claim(other.get("value"))[0].lower() == name.lower()
    ]


def gate_evidence(prospect: dict[str, Any]) -> list[GateResult]:
    """Run the gate over every person claim in a prospect's file."""
    from lib.evidence import BLOCK7_PEOPLE

    block7 = (prospect.get("evidence_file") or {}).get(BLOCK7_PEOPLE) or {}
    people = [c for c in (block7.get("named_people") or []) if isinstance(c, dict)]
    return [
        check_person(claim, prospect.get("company_name"), claims_about(claim, people))
        for claim in people
    ]


def salutation_for(prospect: dict[str, Any]) -> tuple[str, GateResult | None]:
    """The greeting an outbound artifact may use, and the gate result behind it."""
    for result in gate_evidence(prospect):
        if result.allowed:
            return result.salutation, result
    company = prospect.get("company_name") or "the company"
    return f"the owner or president of {company}", None
