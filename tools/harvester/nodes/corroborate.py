"""corroborate — hunt for a second independent source for every claim.

WHY THIS EXISTS NOW

Under per-claim human verification, a single-sourced claim was fine: a person
opened the link before anything was said. That step is gone. What replaces it is
not a stricter single check but a second, independent one — the oldest idea in
journalism, and the cheapest safety this pipeline can buy, because the second
source is usually already sitting in the evidence file.

WHAT COUNTS AS INDEPENDENT

Two pages of the same website corroborate nothing. They are one organisation
saying one thing twice, and if that organisation's domain has been hijacked they
are one attacker saying it twice. Independence here means a different
registrable domain, OR a genuinely different document class — a Conexus case
study and a press round-up are independent even though both are third-party
write-ups, because neither was produced from the other.

THE THREE OUTCOMES, AND WHY 'NEITHER' IS NOT A FAILURE

* agreement -> the claim carries `corroborated: true` and the corroborating URL.
* conflict  -> BOTH values are kept, the claim is flagged, and a discovery
               question is written into the file. We do not pick a winner. Two
               sourced numbers that disagree is information about the company —
               often the interesting kind — and resolving it by choosing would
               throw that away and risk asserting the wrong one.
* neither   -> the claim stays single-source. That is a recorded state, not an
               error. Most true things about a small manufacturer are stated in
               exactly one place.

This node makes no network requests of its own in this version. Everything it
needs is already in the evidence file, gathered by the nodes before it; going
back out to the network for a second opinion on 48 claims per prospect would
cost hours of politeness delay for a small marginal gain. Claims that could be
corroborated only by a fresh fetch are left single-source and reported as such.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from lib.claims import Tier, make_claim
from lib.evidence import BLOCKS, FLAGS_KEY
from lib.integrity import is_usable, iter_all_claims, registrable_domain
from lib.nodes import Node, NodeResult, RunContext, register

DISCOVERY_BLOCK = "block9_discovery"
"""Where auto-generated discovery questions live.

Deliberately outside the eight evidence blocks: a question is not a finding, and
putting it among the findings would let it be read as one."""

NUMBER = re.compile(r"\$?\d[\d,]*\.?\d*")


def document_class(url: str) -> str:
    """A rough class for a source, so two pages of one site are not independent."""
    lowered = (url or "").lower()
    if "conexusindiana.com/case-study" in lowered:
        return "case_study"
    if "conexusindiana.com" in lowered:
        return "grant_listing"
    if any(k in lowered for k in ("insideindianabusiness", "cicpindiana")):
        return "press"
    if any(k in lowered for k in ("linkedin.com", "facebook.com", "twitter.com")):
        return "social"
    if any(k in lowered for k in ("/career", "/job", "indeed.", "/employment")):
        return "job_posting"
    return "company_site"


def independent(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True when two claims come from genuinely separate sources."""
    domain_a = a.get("derived_from") or registrable_domain(a.get("source_url"))
    domain_b = b.get("derived_from") or registrable_domain(b.get("source_url"))
    if domain_a and domain_b and domain_a != domain_b:
        return True
    return document_class(a.get("source_url", "")) != document_class(b.get("source_url", ""))


def _normalise(value: Any) -> str:
    """Compare values loosely enough that formatting differences are not conflicts."""
    text = str(value).strip().lower()
    text = re.sub(r"[\s,]+", " ", text)
    return text.strip(" .$")


def values_agree(a: Any, b: Any) -> bool | None:
    """True agree, False conflict, None not comparable.

    Numbers are compared as numbers so "$102,000" and "102000" agree. Free prose
    is only compared when one contains the other — two different sentences about
    the same subject are not a conflict, they are just two sentences, and
    calling that a conflict would bury the real ones.
    """
    left, right = _normalise(a), _normalise(b)
    if not left or not right:
        return None
    nums_a = [n.replace(",", "").lstrip("$") for n in NUMBER.findall(str(a))]
    nums_b = [n.replace(",", "").lstrip("$") for n in NUMBER.findall(str(b))]
    if nums_a and nums_b:
        try:
            first, second = float(nums_a[0]), float(nums_b[0])
        except ValueError:
            return None
        if first == second:
            return True
        # Within 2% is agreement: rounding in a press write-up is not a conflict.
        larger = max(abs(first), abs(second)) or 1.0
        return abs(first - second) / larger <= 0.02
    if isinstance(a, bool) or isinstance(b, bool):
        return _normalise(a) == _normalise(b)
    if left == right or left in right or right in left:
        return True
    return None


ENTITY_LISTS = frozenset({"named_people", "leadership_quotes"})
"""List keys whose entries are distinct entities rather than repeated records."""

KEY_ALIASES = {
    # The same fact is recorded under different keys by different nodes. Grant
    # money is the clearest case: the case study calls it grant_amount, the round
    # announcement writes grant_awards. Grouping by key alone meant the two best
    # independent sources in the whole file were never compared to each other.
    "grant_amount": "grant_money",
    "grant_awards": "grant_money",
    "grant_awards_total": "grant_money",
    "employee_count": "headcount",
    "company_size": "headcount",
    "self_description": "what_they_make",
    "grant_description": "what_they_make",
}


def _subject(path: str, claim: dict[str, Any] | None = None) -> str:
    """What the claim is ABOUT, which is not always its key.

    Two corrections learned from a run that corroborated nothing:

    * A list of people is not one subject. Stripping `[0]` collapsed every
      named person into a single group, so Dale Whitmore from the company site
      was compared against Karen Ruiz from the case study and found
      incomparable. Person claims are keyed by the person.
    * The same fact is written under different keys by different nodes, so a
      key alias table maps them onto a common subject.
    """
    tail = path.split(".", 1)[1] if "." in path else path
    key = re.sub(r"\[\d+\]", "", tail)
    base = key.split(".")[-1]
    subject = KEY_ALIASES.get(base, key)
    # Only lists of distinct ENTITIES are keyed by the entity. named_people is
    # several different humans; grant_awards is several records of one company's
    # grant money, and keying those by value would stop the case study's figure
    # ever meeting the round announcement's — the single most valuable
    # corroboration pair in the whole file.
    if claim is not None and "[" in tail and base in ENTITY_LISTS:
        head = re.split(r"[—–\-:,]", str(claim.get("value") or ""), maxsplit=1)[0]
        head = re.sub(r"[^a-z0-9 ]+", "", head.lower()).strip()
        if head:
            subject = f"{subject}:{head[:40]}"
    return subject


def corroborate_evidence(evidence: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (updated evidence, stats). Pure: no network, no database."""
    claims = [
        (path.removeprefix("evidence_file."), claim)
        for path, claim in iter_all_claims(evidence)
        if is_usable(claim)
    ]
    by_subject: dict[str, list[tuple[str, dict]]] = {}
    for path, claim in claims:
        block = path.split(".")[0]
        if block not in BLOCKS:
            continue
        by_subject.setdefault(_subject(path, claim), []).append((path, claim))

    updates: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    stats = {"corroborated": 0, "conflicted": 0, "single": 0}

    for subject, group in by_subject.items():
        for path, claim in group:
            partner = None
            verdict = None
            for other_path, other in group:
                if other_path == path or not independent(claim, other):
                    continue
                agreement = values_agree(claim.get("value"), other.get("value"))
                if agreement is True:
                    partner, verdict = other, True
                    break
                if agreement is False and partner is None:
                    partner, verdict = other, False
            if verdict is True:
                updates[path] = {
                    "corroborated": True,
                    "corroborated_by": partner.get("source_url"),
                }
                stats["corroborated"] += 1
            elif verdict is False:
                updates[path] = {"conflict": True, "conflicts_with": partner.get("source_url")}
                stats["conflicted"] += 1
                conflicts.append({
                    "subject": subject,
                    "ours": claim.get("value"),
                    "theirs": partner.get("value"),
                    "our_source": claim.get("source_url"),
                    "their_source": partner.get("source_url"),
                })
            else:
                stats["single"] += 1

    updated = _apply(evidence, updates)
    if conflicts:
        updated = dict(updated)
        block = dict(updated.get(DISCOVERY_BLOCK) or {})
        for conflict in conflicts:
            key = f"conflict_{re.sub(r'[^a-z0-9]+', '_', conflict['subject'].lower())}"
            if key in block:
                continue
            question = make_claim(
                f"sources disagree on {conflict['subject']}: "
                f"{conflict['our_source']} says {conflict['ours']!r} while "
                f"{conflict['their_source']} says {conflict['theirs']!r} — ask",
                Tier.T4,
                conflict["our_source"],
            )
            question["discovery_question"] = True
            block[key] = question
        updated[DISCOVERY_BLOCK] = block
    return updated, stats


def _apply(evidence: dict[str, Any], updates: dict[str, dict]) -> dict[str, Any]:
    """Merge per-path flags into the evidence file without touching anything else."""
    if not updates:
        return evidence

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            trimmed = path.removeprefix("evidence_file.")
            if "value" in node and trimmed in updates:
                return {**node, **updates[trimmed]}
            return {key: walk(child, f"{path}.{key}") for key, child in node.items()}
        if isinstance(node, list):
            return [walk(child, f"{path}[{index}]") for index, child in enumerate(node)]
        return node

    return walk(evidence, "evidence_file")


def rates_by_block(evidence: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Corroboration counts per block, for the run summary."""
    out: dict[str, dict[str, int]] = {}
    for path, claim in iter_all_claims(evidence):
        trimmed = path.removeprefix("evidence_file.")
        block = trimmed.split(".")[0]
        if block not in BLOCKS or not is_usable(claim) or FLAGS_KEY in trimmed:
            continue
        bucket = out.setdefault(block, {"corroborated": 0, "conflict": 0, "single": 0})
        if claim.get("corroborated"):
            bucket["corroborated"] += 1
        elif claim.get("conflict"):
            bucket["conflict"] += 1
        else:
            bucket["single"] += 1
    return out


@register
class CorroborateNode(Node):
    """Find a second independent source for every claim that has one."""

    name: ClassVar[str] = "corroborate"
    depends_on: ClassVar[tuple[str, ...]] = (
        "case_study", "grant_news", "front_door", "job_postings", "people",
    )

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        evidence = prospect.get("evidence_file") or {}
        if not evidence:
            return NodeResult(
                skipped=True,
                skip_reason="no evidence to corroborate yet",
            )
        updated, stats = corroborate_evidence(evidence)
        total = sum(stats.values()) or 1
        notes = [
            f"corroborated {stats['corroborated']}/{total} claim(s) against an "
            f"independent source; {stats['conflicted']} conflict(s); "
            f"{stats['single']} single-source",
            "two pages of one website are not independent and were not counted",
        ]
        if stats["conflicted"]:
            notes.append(
                f"{stats['conflicted']} conflict(s) recorded as discovery questions; "
                f"no winner was chosen"
            )
        return NodeResult(evidence_patch=updated, notes=notes)
