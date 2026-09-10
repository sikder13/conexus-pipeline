"""Which of two conversations a company is ready for.

WHY THERE ARE TWO

The drafting floor is a yes/no about whether we may write claims about a
company: three Tier-1 facts, or nothing ships. That answer has always been
correct and it has always been unhelpful on its own, because "no" was the end of
the sentence. A company with two assertable facts is not a company to forget —
it is a company we cannot yet write TO, which is a different thing.

So the floor's answer routes rather than terminating:

``full``       — three or more assertable facts. A full dossier, costed
                 approaches, and written outreach are all in scope.
``call_first`` — below the floor after everything we know how to gather. The
                 dossier is the sections the evidence can carry — who they are,
                 where they stand, and what the first call must establish — and
                 the first move is a conversation rather than a letter.

WHAT CALL-FIRST IS NOT

It is not a lower tier of prospect. Several call-first companies score higher
than several full ones; what they lack is not interest, it is *checkable
published evidence*, which is a fact about their website rather than about their
business. A shop with no team page and no press coverage may be the best
prospect in the set and we would have no way to know it from outside.

WHAT IT FORBIDS

Written outreach that asserts claims. Not because the claims would be wrong, but
because there are not enough of them to write the DATA-1 formula's two-or-three
facts without padding — and padding is what the floor exists to prevent. A call
is exempt for the obvious reason: on a call you ask rather than assert, and the
answers become the evidence.

WHY IT IS COMPUTED AND NEVER STORED

The routing follows the evidence, and the evidence moves. A stored routing is a
count that cannot change — the same failure as a stored velocity sentence with a
frozen denominator. Ask this module; do not cache its answer.
"""

from __future__ import annotations

from typing import Any

FULL = "full"
CALL_FIRST = "call_first"

CALL_FIRST_WORDS = "call-first prospect"
CALL_FIRST_EXPLANATION = (
    "Below the evidence floor after every enrichment pass we have. The dossier "
    "carries what the evidence can hold and no costed approaches; the first "
    "move is a call, not a letter. Written outreach that asserts claims is out "
    "of scope until a call or new evidence lifts them."
)


def route_for(prospect: dict[str, Any], verdicts: tuple[str, ...]) -> str:
    """Which conversation this company is ready for, right now."""
    from tools.drafter import main as drafter

    return CALL_FIRST if drafter.below_floor(prospect, verdicts) else FULL


def reason_for(prospect: dict[str, Any], verdicts: tuple[str, ...]) -> str:
    """Why, in the words the dossier prints. Empty for a full-dossier company."""
    from tools.drafter import main as drafter

    return drafter.below_floor(prospect, verdicts) or ""


def split(
    prospects: list[dict[str, Any]], verdicts: tuple[str, ...]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The prospects in two lists, full first, each keeping its given order."""
    full = [p for p in prospects if route_for(p, verdicts) == FULL]
    call_first = [p for p in prospects if route_for(p, verdicts) == CALL_FIRST]
    return full, call_first


def may_write_claims(prospect: dict[str, Any], verdicts: tuple[str, ...]) -> bool:
    """Whether written outreach asserting claims is in scope for this company."""
    return route_for(prospect, verdicts) == FULL
