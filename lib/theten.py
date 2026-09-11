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

from lib import anchors, contacts, icp

REQUIREMENTS: tuple[tuple[str, str], ...] = (
    ("analysis", "no full scope-of-work analysis"),
    ("contact", "no contact path an operator can act on"),
    ("outreach", "nothing written that passed the gate on any open channel"),
)
"""What a company must have, and how each absence is named.

One wording per reason, used for every company, so the counts at the bottom of
the report mean something. Reasons phrased per company cannot be added up.

WHY THE THIRD ONE IS A CHANNEL RATHER THAN A CHANNEL LIST

It used to require an email AND a LinkedIn pair, both named separately. That
definition was written when email was the channel and everything else was a
follow-up, and it became wrong the moment the Canadian set arrived: CASL
forbids a commercial email to a company that has published no address, so 41 of
43 ranked Canadian companies could never satisfy an email requirement however
good their research was. The dossier reported none of them ready while holding
41 sendable analyses, 33 sendable letters and 24 sendable LinkedIn pairs for
them.

A requirement a company is structurally barred from meeting is not a standard,
it is an accounting error. So the test is now: is there SOMETHING we may
lawfully send them that has passed the gate? One artifact on one open channel
is a company an operator can work this morning.

Nothing loosened about the gate. Every artifact counted here passed exactly the
checks it always had to."""

OUTREACH_KINDS: tuple[str, ...] = ("linkedin", "email", "letter")
"""Kinds that count as a way to reach them, in the order they are reported.

A thesis and a brief are absent on purpose: the thesis is the operator's own
reasoning and the brief is its companion, and neither is sent to anybody."""

TARGET = 10


class Candidate(NamedTuple):
    """One ranked company and what it does or does not have."""

    prospect: dict[str, Any]
    analysis: dict[str, Any] | None
    email: dict[str, Any] | None
    linkedin: dict[str, Any] | None
    paths: list[contacts.ContactPath]
    letter: dict[str, Any] | None = None
    """The fragment letter, if one passed the gate. One of the open channels."""

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

    @property
    def open_channels(self) -> list[str]:
        """Every kind we may lawfully send this company that passed the gate."""
        held = {"linkedin": self.linkedin, "email": self.email, "letter": self.letter}
        return [kind for kind in OUTREACH_KINDS if held.get(kind)]

    @property
    def email_is_shut(self) -> bool:
        """Whether this company's regime forbids an email to them at all.

        Asked so the report can say WHY an absent email is not a shortfall. It
        reads the same compliance module the drafter does rather than repeating
        the rule, because a second copy of a legal test is a second thing to get
        wrong.
        """
        from tools.drafter.main import email_impossible

        return bool(email_impossible(self.prospect))

    def missing(self) -> list[str]:
        """Exactly what this company lacks, in the shared wording."""
        held = {
            "analysis": bool(self.analysis and self.analysis.get("body")
                             and not (self.analysis.get("gate_map") or {}).get("thin")),
            "contact": bool(self.actionable_paths),
            "outreach": bool(self.open_channels),
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
            letter=newest_live(artifacts, "letter"),
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


def anchor_of(candidate: Candidate) -> dict[str, Any]:
    """What the analysis recorded as the thing it was sized to.

    An analysis written before anchors were recorded has none, and that is not
    the same as having no anchor — it is a document we cannot say either way
    about. It keeps its figure, and regenerating it is what produces an answer.
    """
    case = ((candidate.analysis or {}).get("gate_map") or {}).get("case") or {}
    return case.get("anchor") or {}


def roi_words(candidate: Candidate) -> str:
    """The lead offer's return band, or the sentence that asks for one number.

    An unanchored analysis has real arithmetic in it and nothing that ties the
    arithmetic to this company, so its ranges belong in the body as hypotheses
    and must not be printed at the top of a summary table as though they were a
    finding. What goes there instead is the question: one number from them turns
    the whole document into a statement about their business.
    """
    if anchor_of(candidate).get("kind") == anchors.NONE:
        return anchors.PENDING_HEADLINE
    offer = candidate.lead_offer
    if not offer or not offer.get("annual_return"):
        return "—"
    low, high = offer["annual_return"][0], offer["annual_return"][1]
    return f"${low:,}-${high:,}/yr"


def dashboard_token(candidate: Candidate) -> str:
    """The slug this company's calculator is published under.

    Derived rather than stored, and stable for the life of the company, so a QR
    code already printed on a posted one-pager keeps working. See
    `lib/dashboard.py`.
    """
    from lib import dashboard

    return dashboard.token_for(candidate.prospect)


def arsenal_of(candidate: Candidate) -> dict[str, str]:
    """What exists for this company beyond the four readiness tests."""
    from pathlib import Path

    token = dashboard_token(candidate)
    built = Path("reports/dashboards") / f"{token}.html"
    return {
        # "on demand" rather than a dash. The arsenal stopped being
        # pre-generated on 2026-09-10: outreach runs about twenty companies a
        # week, and an artifact built three weeks before the call quotes the
        # evidence as it stood three weeks ago. A dash reads as a gap in the
        # pipeline; this reads as what it is, one command away.
        "letter": "sendable" if candidate.letter else "arsenal on demand",
        "dashboard": token if built.exists() else f"{token} (on demand)",
    }
