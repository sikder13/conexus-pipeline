"""The order an operator would work a list in, defined once.

WHY IT IS NOT JUST THE SCORE

The signal score answers "how interesting is this company". It does not answer
"which of these two do I open first", and on a list where most companies score
the same it answers nothing at all — the Indiana P1 set is twenty companies of
which sixteen score 3.

So the score is the first key and two tiebreakers follow it, in the order a
person would actually apply them:

1. **signal score** — how interesting.
2. **published contacts** — whether we can reach them at all. Between two equally
   interesting companies the one with a published address is the one to write
   first, and that is a fact about their site rather than a judgement about
   their business.
3. **evidence richness** — how much we hold. Weakest of the three and last for
   that reason: a thick file is often a company with a big website rather than a
   company worth more.

Name breaks the remaining ties so the order is stable between runs. A shortlist
that reshuffles when nothing changed is one nobody can check against yesterday's.

WHAT IT REFUSES BEFORE IT RANKS

The size gate and the integrity gate, both of which are about eligibility rather
than about interest. A company that failed the size gate without an override is
not ranked low, it is not ranked: the gate is about whether we should be selling
to them at all, and no amount of signal answers that.
"""

from __future__ import annotations

from typing import Any

from lib.integrity import evidence_integrity, is_usable, iter_all_claims


def published_contacts(prospect: dict[str, Any]) -> int:
    """How many addresses and numbers this company has published itself."""
    front = ((prospect.get("evidence_file") or {})
             .get("block4_digital_front_door") or {})
    total = 0
    for key in ("published_emails", "published_phones"):
        found = front.get(key)
        if isinstance(found, list):
            total += sum(1 for c in found if isinstance(c, dict) and is_usable(c))
    if not total:
        total = len([c for c in (prospect.get("contacts") or [])
                     if isinstance(c, dict)])
    return total


def evidence_richness(prospect: dict[str, Any]) -> int:
    """Usable claims in the file. The last tiebreaker, and the weakest."""
    return sum(1 for _path, claim in iter_all_claims(prospect.get("evidence_file") or {})
               if is_usable(claim))


def rank_key(prospect: dict[str, Any]) -> tuple:
    """The sort key. Negated where more is better, so a plain sort is ascending."""
    return (
        -(prospect.get("signal_score") or 0),
        -published_contacts(prospect),
        -evidence_richness(prospect),
        str(prospect.get("company_name")),
    )


def past_the_gates(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only the companies eligible to be ranked at all."""
    return [
        p for p in prospects
        if not (p.get("size_review") and not p.get("size_override"))
        and evidence_integrity(p).passing
    ]


def ranked(
    prospects: list[dict[str, Any]], priorities: tuple[str, ...] = ("P1", "P2"),
) -> list[dict[str, Any]]:
    """Eligible companies at these priorities, in working order.

    P1s ahead of P2s regardless of the keys below, because priority is a
    different question from ordering and mixing them would let a well-connected
    P2 sit above a P1 the operator has already decided is worth more.
    """
    eligible = past_the_gates(prospects)
    out: list[dict[str, Any]] = []
    for priority in priorities:
        out.extend(sorted(
            (p for p in eligible if p.get("priority") == priority), key=rank_key))
    return out
