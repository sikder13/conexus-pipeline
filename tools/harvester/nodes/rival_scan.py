"""rival_scan — read the regional shops a prospect is actually measured against.

WHAT THIS ADDS TO competitor_scan

`competitor_scan` finds the rivals a company or its coverage NAMES. That is the
strongest evidence there is and it is rare: most companies name nobody, and a
comparison built only from named rivals exists for a handful of prospects.

This widens the discovery without widening what may be claimed. Rivals come from
three routes, each recorded on the observation:

* the names `competitor_scan` already resolved — strongest, somebody said it;
* the same-industry, same-adapter companies already in our own dataset, chosen
  by `lib/peers.py` so the group and its widening rung are the ones the peer
  table already reports;
* the curated trade directories in `lib/rivals.py`, which today yield nothing
  and say so.

Every cell of the resulting table is read off the rival's own public site by us.
A directory may point at a shop and may never describe one. There is no revenue
figure, no share estimate and no headcount guess anywhere in here, because there
is no public source for any of them.

ONE FETCH PER RIVAL, AND ONLY RIVALS WE ALREADY RESOLVED

The dataset rivals already carry a website this pipeline resolved and verified,
so there is no candidate-domain search to redo — one polite request each. That
matters: this node runs across a batch, and a comparison that costs five
requests per rival would be a crawl of the regional industry rather than a look
at it.
"""

from __future__ import annotations

from typing import Any, ClassVar

from lib import peers, rivals
from lib.claims import Tier, make_claim
from lib.evidence import BLOCK10_COMPETITORS, block_patch
from lib.integrity import evidence_integrity
from lib.nodes import FetchError, Node, NodeResult, RobotsDisallowed, RunContext, register

RIVAL_SCAN_KEY = "rival_scan"
"""Reserved non-claim key inside block10 holding the structured comparison.

Structured rather than prose because the counts in a velocity sentence are
computed at render time from these observations. Storing the sentence instead
would freeze a count that the next rival read should change, and a stale
denominator is the one thing that sentence cannot survive."""

MAX_RIVALS = rivals.TARGET_RIVALS[1]


def _readable(prospect: dict[str, Any]) -> bool:
    return bool(prospect.get("website")) and evidence_integrity(prospect).passing


def choose_rivals(
    subject: dict[str, Any],
    others: list[dict[str, Any]],
    named: list[tuple[str, str]] | None = None,
) -> tuple[list[tuple[str, str, str, str]], str]:
    """Up to five rivals as (name, url, channel, how we came to look), plus a basis.

    Named rivals first, then the peer group, because a rival somebody named out
    loud outranks one we selected by industry keyword however good the keyword.
    """
    chosen: list[tuple[str, str, str, str]] = []
    seen: set[str] = {(subject.get("website") or "").lower().rstrip("/")}

    for name, url in named or []:
        key = (url or "").lower().rstrip("/")
        if not url or key in seen:
            continue
        seen.add(key)
        chosen.append((name, url, rivals.NAMED,
                       "named as a competitor on their own site or in coverage"))

    group = peers.peer_group(subject, others)
    for member in group.members:
        if len(chosen) >= MAX_RIVALS:
            break
        url = member.get("website") or ""
        key = url.lower().rstrip("/")
        if not _readable(member) or key in seen:
            continue
        seen.add(key)
        chosen.append((
            str(member.get("company_name") or "").strip(), url, rivals.DATASET,
            f"in our own dataset, {group.basis}",
        ))
    return chosen[:MAX_RIVALS], group.basis


@register
class RivalScanNode(Node):
    """Read the regional shops this company is measured against, and compare."""

    name: ClassVar[str] = "rival_scan"
    depends_on: ClassVar[tuple[str, ...]] = ("front_door",)
    priorities: ClassVar[tuple[str, ...] | None] = ("P1", "P2")

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        if self.priorities and prospect.get("priority") not in self.priorities:
            return NodeResult(
                skipped=True,
                skip_reason=(f"priority={prospect.get('priority')}; rivals are read "
                             f"for {', '.join(self.priorities)} only"),
            )
        home = prospect.get("website")
        if not home:
            return NodeResult(skipped=True, skip_reason="no resolved website to compare from")

        others = self._dataset(prospect)
        candidates, basis = choose_rivals(prospect, others, self._named(prospect))
        if not candidates:
            return NodeResult(
                skipped=True,
                skip_reason=f"no readable regional rival found ({basis})",
            )

        subject = await self._observe(
            ctx, str(prospect.get("company_name") or ""), home, rivals.DATASET,
            "the prospect's own site, read the same way")
        observed: list[rivals.RivalObservation] = []
        unreachable: list[str] = []
        for name, url, channel, route in candidates:
            found = await self._observe(ctx, name, url, channel, route)
            if found is None:
                unreachable.append(f"{name} ({url})")
                continue
            observed.append(found)

        if not observed:
            return NodeResult(
                skipped=True,
                skip_reason=f"no rival site could be read: {', '.join(unreachable[:3])}",
            )

        shortfall = ""
        if len(observed) < rivals.TARGET_RIVALS[0]:
            shortfall = (
                f"only {len(observed)} of a wanted {rivals.TARGET_RIVALS[0]} rivals "
                f"could be read, so any count below is over a small sample"
            )
        table = rivals.build_gap_table(
            str(prospect.get("company_name") or ""), subject, observed, shortfall)

        claims: dict[str, Any] = {
            RIVAL_SCAN_KEY: {
                "basis": table.basis,
                "channels": table.channels_used,
                "shortfall": shortfall,
                "unreachable": unreachable,
                "search": rivals.search_status(None).line,
                "directories": [
                    d.source_id for d in rivals.directories_for(
                        "IN", peers.family_of(prospect).key)
                ],
                "subject": subject.model_dump() if subject else None,
                "rivals": [o.model_dump() for o in observed],
            }
        }
        for index, found in enumerate(observed, start=1):
            claims[f"rival_{index}"] = make_claim(
                f"{found.name} — {_words(found)}", Tier.T1, found.url)

        return NodeResult(
            evidence_patch=block_patch(BLOCK10_COMPETITORS, claims),
            notes=[
                f"read {len(observed)} regional rival(s); "
                f"{len(table.gaps())} feature gap(s)"
            ],
        )

    def _dataset(self, prospect: dict[str, Any]) -> list[dict[str, Any]]:
        from lib import db
        return [
            other for other in db.list_prospects_full(prospect.get("source_adapter"))
            if other.get("id") != prospect.get("id")
        ]

    def _named(self, prospect: dict[str, Any]) -> list[tuple[str, str]]:
        """Competitors competitor_scan already resolved to a site."""
        block = (prospect.get("evidence_file") or {}).get(BLOCK10_COMPETITORS) or {}
        out = []
        for key, claim in block.items():
            if not key.startswith("competitor_") or not isinstance(claim, dict):
                continue
            url = str(claim.get("source_url") or "")
            name = str(claim.get("value") or "").split("—")[0].strip()
            if url and name:
                out.append((name, url))
        return out

    async def _observe(
        self, ctx: RunContext, name: str, url: str, channel: str, route: str,
    ) -> rivals.RivalObservation | None:
        try:
            response = await ctx.fetch(url)
        except (FetchError, RobotsDisallowed) as exc:
            # A rival we may not read, or cannot, is one the comparison does not
            # count. It is reported in `unreachable` so the denominator stays
            # honest rather than quietly shrinking.
            del exc
            return None
        except Exception:
            return None
        if response.status_code >= 400:
            return None
        return rivals.observe_site(
            response.text, str(response.url), name, channel, route)


def _words(found: rivals.RivalObservation) -> str:
    """One sentence a person reads, built only from what the site said."""
    caps = ", ".join(found.capabilities[:4]) or "no capability we could name"
    certs = ", ".join(found.certifications[:3]) or "no published certification"
    path = {
        "portal": "takes quote requests through a portal or drawing upload",
        "form": "takes quote requests through a form",
        "email": "publishes an address to email",
        "phone": "publishes a phone number only",
        "none": "publishes no visible way to ask for a quote",
    }[found.quoting_path]
    lead = f"; states {found.lead_times[0]}" if found.lead_times else ""
    auto = (f"; describes {', '.join(found.automation_terms[:3])}"
            if found.automation_terms else "")
    return f"advertises {caps}; publishes {certs}; {path}{lead}{auto}"
