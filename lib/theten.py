"""THE TEN — the companies that are actually ready to be contacted.

WHAT "READY" MEANS HERE

Four things at once, and a company missing any one of them is not ready however
good the other three are:

1. a full scope-of-work analysis, not a thin one — something to talk about;
2. at least one way in that an operator can act on today;
3. an email that passed the outbound gate;
4. the LinkedIn pair that passed the same gate.

The ranking is the pipeline's own — signal score first, then drive time — and
the qualification is a filter applied after it, not a second ranking. A company
does not climb the list by being reachable; it drops off by not being.

WHY THE NEAR MISSES ARE PART OF THE OUTPUT

A list of ten with nothing behind it tells an operator what to do this week and
nothing about what to fix. So every company that ranked but did not qualify
comes back with exactly what it lacks, in the same words for every company, and
the counts of those reasons are the most useful line in the whole report: they
say whether the bottleneck is research, contact discovery, or the gate.

Nothing here loosens when the list comes up short. Ten is what we would like;
the answer is however many are actually ready.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from lib import contacts, icp

REQUIREMENTS: tuple[tuple[str, str], ...] = (
    ("analysis", "no full scope-of-work analysis"),
    ("contact", "no contact path an operator can act on"),
    ("email", "no email that passed the gate"),
    ("linkedin", "no LinkedIn pair that passed the gate"),
)
"""What a company must have, and how each absence is named.

One wording per reason, used for every company, so the counts at the bottom of
the report mean something. Reasons phrased per company cannot be added up."""

TARGET = 10


class Candidate(NamedTuple):
    """One ranked company and what it does or does not have."""

    prospect: dict[str, Any]
    analysis: dict[str, Any] | None
    email: dict[str, Any] | None
    linkedin: dict[str, Any] | None
    paths: list[contacts.ContactPath]

    @property
    def name(self) -> str:
        return str(self.prospect.get("company_name") or "")

    @property
    def score(self) -> int:
        return int(self.prospect.get("signal_score") or 0)

    @property
    def drive(self) -> int | None:
        value = self.prospect.get("drive_minutes")
        return int(value) if value is not None else None

    @property
    def actionable_paths(self) -> list[contacts.ContactPath]:
        """Paths that are a way in rather than a pointer at one.

        The website on its own is not a contact — it is where we started. A
        search link is not one either: it is a link the operator opens to begin
        looking, and counting it would let a company with nothing published
        qualify on the strength of a URL we built ourselves.
        """
        return [p for p in self.paths if p.kind in ("email", "phone", "form", "person")]

    def missing(self) -> list[str]:
        """Exactly what this company lacks, in the shared wording."""
        held = {
            "analysis": bool(self.analysis and self.analysis.get("body")
                             and not (self.analysis.get("gate_map") or {}).get("thin")),
            "contact": bool(self.actionable_paths),
            "email": bool(self.email),
            "linkedin": bool(self.linkedin),
        }
        return [words for key, words in REQUIREMENTS if not held[key]]

    @property
    def qualifies(self) -> bool:
        return not self.missing()

    @property
    def lead_offer(self) -> dict[str, Any] | None:
        """The approach the analysis said to open with, or its first."""
        approaches = (self.analysis or {}).get("gate_map", {}).get("approaches") or []
        return approaches[0] if approaches else None

    @property
    def best_path(self) -> contacts.ContactPath | None:
        """The one way in to print in a summary row.

        Ordered by how little work it leaves the operator: a named person's
        address beats a rota, a rota beats a phone, and a form is the last thing
        anybody wants but is still a way in.
        """
        order = ("Email — a named person", "Email — a role mailbox",
                 "Email — published, reader unknown")
        for label in order:
            for path in self.actionable_paths:
                if path.label == label:
                    return path
        for kind in ("person", "phone", "form"):
            for path in self.actionable_paths:
                if path.kind == kind:
                    return path
        return None


def newest_live(artifacts: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    """The artifact of this kind that currently counts, if it passed.

    Newest live, then required to be sendable — not "the newest sendable". The
    difference matters: a company whose latest draft was refused should not keep
    being counted as ready on the strength of an older one nothing points at.
    """
    live = [a for a in artifacts
            if a.get("kind") == kind
            and a.get("status") in ("sendable", "blocked", "draft")]
    if not live:
        return None
    current = max(live, key=lambda a: a["created_at"])
    return current if current.get("status") == "sendable" else None


def build(prospects: list[dict[str, Any]],
          artifacts_by: dict[str, list[dict[str, Any]]]) -> list[Candidate]:
    """Every P1, ranked the pipeline's way, with what each one holds."""
    rows = [p for p in prospects
            if p.get("priority") == "P1" and icp.outreach_eligible(p)]
    rows.sort(key=lambda p: (-(p.get("signal_score") or 0),
                             p.get("drive_minutes") if p.get("drive_minutes") is not None
                             else 999,
                             str(p.get("company_name") or "")))
    out: list[Candidate] = []
    for prospect in rows:
        artifacts = artifacts_by.get(prospect["id"], [])
        live_analysis = [a for a in artifacts
                         if a.get("kind") == "analysis"
                         and a.get("status") in ("sendable", "blocked")]
        analysis = max(live_analysis, key=lambda a: a["created_at"]) if live_analysis else None
        out.append(Candidate(
            prospect=prospect,
            analysis=analysis if (analysis or {}).get("status") == "sendable" else None,
            email=newest_live(artifacts, "email"),
            linkedin=newest_live(artifacts, "linkedin"),
            paths=contacts.contact_paths(prospect),
        ))
    return out


def the_ten(candidates: list[Candidate], target: int = TARGET) -> list[Candidate]:
    """The ready ones, in rank order, up to the target."""
    return [c for c in candidates if c.qualifies][:target]


def near_misses(candidates: list[Candidate], target: int = TARGET) -> list[Candidate]:
    """Ranked companies that did not qualify, best first.

    Returned whole rather than truncated to the shortfall: an operator deciding
    where to spend the next hour wants the whole queue and its reasons, not the
    handful that would have rounded the list up to ten.
    """
    return [c for c in candidates if not c.qualifies]


def reason_counts(candidates: list[Candidate]) -> dict[str, int]:
    """How many ranked companies each missing thing is costing us.

    The most useful line in the report: it says whether the bottleneck is
    research, contact discovery, or the gate — which is the difference between
    reading more sites and rewriting a prompt.
    """
    counts = {words: 0 for _key, words in REQUIREMENTS}
    for candidate in candidates:
        for words in candidate.missing():
            counts[words] += 1
    return counts


def roi_words(candidate: Candidate) -> str:
    """The lead offer's return band, as the summary table prints it."""
    offer = candidate.lead_offer
    if not offer or not offer.get("annual_return"):
        return "—"
    low, high = offer["annual_return"][0], offer["annual_return"][1]
    return f"${low:,}-${high:,}/yr"
