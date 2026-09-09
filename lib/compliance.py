"""Which anti-spam law governs an outbound message, and what it requires of it.

TWO REGIMES, ONE GATE

Indiana prospects are American and CAN-SPAM applies: identify yourself, give a
real postal address, and honour an opt-out. Ontario and Alberta prospects are
Canadian and CASL applies, and CASL is a different shape of law. It is
consent-first: a commercial electronic message needs consent before it is sent,
and the exemption we rely on is narrow and factual.

THE EXEMPTION WE RELY ON, STATED PLAINLY

CASL does not apply to a commercial electronic message sent to an electronic
address that the recipient has **conspicuously published**, where the
publication is not accompanied by a statement that they do not wish to receive
unsolicited messages, and where **the message is relevant to the recipient's
business role, functions or duties**.

Every clause of that is a testable condition, so every clause is tested:

* *conspicuously published* — the address must be one `contact_discovery` read
  off the company's own pages, and the claim must carry the URL it was read
  from. An address nobody published is not covered by the exemption, and this
  module will not let one be used.
* *not accompanied by a refusal* — recorded as a check on the page the address
  came from.
* *relevant to their role* — the basis line states the relevance, and the
  operator can read it beside the draft.

WHY THIS IS A GATE AND NOT A CHECKLIST

Because the interesting failure is silent. Nothing about a guessed address looks
different in a draft: `first.last@company.ca` reads exactly like a published
one, and the difference — whether a person could open the page it came from — is
in the evidence file rather than in the prose. So the check reads the evidence,
not the text, and the outbound gate refuses the artifact rather than warning
about it.

`contact_discovery` already refuses to construct an address. This module is the
second half of that promise: the first half is that we never build one, and the
second is that we cannot send to one we did not read.

LINKEDIN IS NOT EMAIL

CASL's electronic-message rules govern email and equivalent electronic messages.
A LinkedIn artifact is drafted for a human to send by hand inside a platform
with its own rules, so it is not held to the published-address test — the
operator is the sender and the platform is the channel. It still carries the
identification block, because saying who we are is not a legal technicality.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from lib.evidence import BLOCK4_DIGITAL_FRONT_DOOR
from lib.scoring import DEFAULT_ADAPTER

SENDER_NAME = "Udaay Sikder"
SENDER_COMPANY = "Nahl Technologies Inc."
SENDER_ADDRESS = "6902 Challenge Ln, Indianapolis IN 46250, USA"
OPT_OUT = "Reply STOP and I will not contact you again."
"""The identification block. One physical address, one working opt-out, our real
name. CAN-SPAM requires it and CASL requires it; the wording is ours."""

EMAIL_KIND = "email"
"""The artifact kind the published-address rule binds. LinkedIn is not it."""

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}\b")

CASL_BASIS = (
    "conspicuously published business contact address, message relevant to the "
    "recipient's role"
)
"""The CASL basis recorded on every Canadian email artifact.

Stored on the artifact rather than asserted in the abstract, because the basis
is a claim about one message to one address and has to be checkable against that
message and that address."""


class ComplianceProfile(NamedTuple):
    """One source's outbound rules: the regime, and what it demands."""

    adapter_id: str
    regime: str
    requires_published_address: bool
    basis: str
    identification: tuple[str, ...]

    @property
    def identification_block(self) -> str:
        """The signature every email carries, as it is appended."""
        return "\n".join(("--", *self.identification))


IDENTIFICATION = (SENDER_NAME, SENDER_COMPANY, SENDER_ADDRESS, OPT_OUT)

CAN_SPAM = ComplianceProfile(
    adapter_id="conexus_iedc",
    regime="CAN-SPAM",
    requires_published_address=False,
    basis=(
        "commercial message to a business contact, with sender identification, "
        "a physical address and a working opt-out"
    ),
    identification=IDENTIFICATION,
)

CASL = ComplianceProfile(
    adapter_id="canada_gc",
    regime="CASL",
    requires_published_address=True,
    basis=CASL_BASIS,
    identification=IDENTIFICATION,
)

PROFILES: dict[str, ComplianceProfile] = {
    CAN_SPAM.adapter_id: CAN_SPAM,
    CASL.adapter_id: CASL,
}
"""One profile per source adapter, keyed exactly as the scoring profiles are."""


class UnknownComplianceProfile(KeyError):
    """A prospect names a source adapter with no outbound rules declared.

    Raised rather than defaulted to the laxer regime. A new source is a new
    jurisdiction until somebody says otherwise, and defaulting would answer that
    question by accident.
    """


def profile_for(source_adapter: str | None) -> ComplianceProfile:
    """The outbound rules for one source. Raises if the source has none."""
    key = (source_adapter or "").strip() or DEFAULT_ADAPTER
    if key not in PROFILES:
        raise UnknownComplianceProfile(
            f"no compliance profile for source_adapter {source_adapter!r}. Declare "
            f"one in lib/compliance.py and document it in docs/GATE.md before "
            f"drafting anything for this source; known profiles: "
            f"{', '.join(sorted(PROFILES))}"
        )
    return PROFILES[key]


# ------------------------------------------------------ what was published

class PublishedAddress(NamedTuple):
    """One address a node read off the company's own pages, and where."""

    address: str
    source_url: str
    email_class: str


def published_addresses(prospect: dict[str, Any]) -> list[PublishedAddress]:
    """Every email address recorded as read from a page, with that page's URL.

    Reads `contacts`, which `contact_discovery` fills from the company's own
    pages and from nowhere else. An entry with no source URL is not evidence
    that anything was published, so it is not returned — the URL is the whole
    substance of "conspicuously published".
    """
    out: list[PublishedAddress] = []
    for entry in prospect.get("contacts") or []:
        if not isinstance(entry, dict) or entry.get("kind") != EMAIL_KIND:
            continue
        address = str(entry.get("value") or "").strip().lower()
        source_url = str(entry.get("source_url") or "").strip()
        if not address or not source_url:
            continue
        out.append(PublishedAddress(
            address, source_url, str(entry.get("email_class") or "unclassified")
        ))
    return out


def published_in_evidence(prospect: dict[str, Any]) -> list[str]:
    """Addresses named in the block 4 `published_emails` claims, for a cross-check.

    The claim list and the `contacts` column are written by the same node in the
    same result, so they should agree. Where they do not, the column is the one
    the drafter reads and this is how a divergence becomes visible instead of
    quietly deciding a send.
    """
    block = (prospect.get("evidence_file") or {}).get(BLOCK4_DIGITAL_FRONT_DOOR) or {}
    claims = block.get("published_emails") or []
    found: list[str] = []
    for claim in claims if isinstance(claims, list) else []:
        if isinstance(claim, dict):
            found += [m.lower() for m in EMAIL_PATTERN.findall(str(claim.get("value") or ""))]
    return sorted(set(found))


def addresses_in(text: str) -> list[str]:
    """Every email address written in an artifact's text, lowercased."""
    return sorted({match.lower() for match in EMAIL_PATTERN.findall(text or "")})


# -------------------------------------------------------------- the verdict

class ComplianceVerdict(NamedTuple):
    """Whether one artifact may be sent under its source's regime, and why not."""

    passed: bool
    regime: str
    basis: str
    failures: tuple[str, ...]
    target: str | None
    published: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """The record stored on the artifact, so the basis survives the run."""
        return {
            "regime": self.regime,
            "basis": self.basis,
            "passed": self.passed,
            "failures": list(self.failures),
            "target_address": self.target,
            "published_addresses": list(self.published),
        }


def choose_target(prospect: dict[str, Any]) -> PublishedAddress | None:
    """The published address an email would go to: a named person's, if there is one.

    Nothing is constructed and nothing is preferred on grounds of how the
    address looks. The order is the order `contact_discovery` already
    classified: an address it could tie to a named person, then a role mailbox,
    then one it could not place.
    """
    ranking = {"named_person": 0, "role_based": 1, "generic": 2}
    candidates = published_addresses(prospect)
    if not candidates:
        return None
    return min(candidates, key=lambda c: (ranking.get(c.email_class, 3), c.address))


def check_email(
    prospect: dict[str, Any], body: str, target: str | None = None
) -> ComplianceVerdict:
    """Decide whether this email may be sent to this company under its regime.

    Three ways to fail, and all three are about the address rather than the
    prose:

    * the regime requires a published address and the company has none recorded;
    * the draft is addressed to something nobody published — the guessed-address
      case, which is the one this exists for;
    * the draft names an address that is not the one it is being sent to and
      that nobody published either, which is the same failure wearing a
      signature block.

    A profile that does not require a published address (CAN-SPAM) skips those
    and checks only that the identification block is present, which is what that
    law asks for.
    """
    profile = profile_for(prospect.get("source_adapter"))
    published = published_addresses(prospect)
    allowed = {entry.address for entry in published}
    failures: list[str] = []

    resolved = (target or "").strip().lower() or None
    if resolved is None and published:
        chosen = choose_target(prospect)
        resolved = chosen.address if chosen else None

    if profile.requires_published_address:
        if not allowed:
            failures.append(
                "no published email address is recorded for this company, so there "
                "is no address CASL's conspicuous-publication exemption covers. "
                "Nothing may be guessed"
            )
        elif resolved is None:
            failures.append("no recipient address was chosen for this email")
        elif resolved not in allowed:
            failures.append(
                f"the recipient address {resolved!r} is not one contact discovery "
                f"read from this company's own pages. Published: "
                f"{', '.join(sorted(allowed))}"
            )
        stray = [
            address for address in addresses_in(body)
            if address not in allowed and not address.endswith("nahltechnologies.com")
        ]
        if stray:
            failures.append(
                f"the draft names {', '.join(stray[:3])}, which nobody published"
            )

    if SENDER_ADDRESS not in (body or ""):
        failures.append(
            f"the identification block is missing: {profile.regime} requires our "
            f"name, a physical address and a working opt-out on every message"
        )

    return ComplianceVerdict(
        passed=not failures,
        regime=profile.regime,
        basis=profile.basis,
        failures=tuple(failures),
        target=resolved,
        published=tuple(sorted(allowed)),
    )


def check_artifact(
    prospect: dict[str, Any], kind: str, body: str, target: str | None = None
) -> ComplianceVerdict:
    """Apply the regime to one artifact. Only `email` carries the address rules.

    A thesis, a brief and a LinkedIn message are not electronic messages sent to
    an address, so the published-address test does not apply to them and is not
    pretended to. The regime is still recorded on each, because "which law
    governed this" is a fact about the artifact whatever the answer.
    """
    profile = profile_for(prospect.get("source_adapter"))
    if kind == EMAIL_KIND:
        return check_email(prospect, body, target)
    return ComplianceVerdict(
        passed=True,
        regime=profile.regime,
        basis=f"{kind} artifact — not an electronic message under {profile.regime}",
        failures=(),
        target=None,
        published=tuple(sorted(e.address for e in published_addresses(prospect))),
    )


def not_drafted(prospect: dict[str, Any], kind: str = EMAIL_KIND) -> dict[str, Any]:
    """The compliance record for an artifact that was never written.

    A company held below the evidence floor still gets a row, and that row still
    has to say which regime would have governed it — but it must not claim a
    message was checked. There is no message.
    """
    profile = profile_for(prospect.get("source_adapter"))
    return {
        "regime": profile.regime,
        "basis": f"{kind} was never drafted, so no message was checked",
        "passed": None,
        "failures": [],
        "target_address": None,
        "published_addresses": [e.address for e in published_addresses(prospect)],
    }


def append_identification(body: str, profile: ComplianceProfile) -> str:
    """Add the identification block to an email that does not already carry it."""
    if SENDER_ADDRESS in (body or ""):
        return body
    return f"{body}\n\n{profile.identification_block}"
