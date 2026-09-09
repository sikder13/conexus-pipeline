"""Which source a command is scoped to, and how every tool asks for it.

WHY THIS EXISTS

Two datasets share one database now, and a command that says nothing about the
source acts on both. For an audit that is right — the invariants are about every
row. For almost everything else it is wrong, and wrong in a way that is hard to
see afterwards: a drafting run that reaches across sources writes to an Ontario
company under the Indiana profile's assumptions, and the artifact it produces
looks exactly like one that was scoped correctly.

So the flag is defined once, spelled the same everywhere, and validated against
the same list the scoring profiles are keyed by. A source with no scoring
profile cannot be scoped to, which is the same rule stated from the other side:
if we do not know how to score a source we do not know how to work it either.

WHAT THE DEFAULT MEANS

No flag means every source. That is deliberate for the tools that report — the
console and the audit describe the database as it is — and it is why the tools
that WRITE print the scope they are running under before they start. A run whose
scope nobody stated is a run nobody can repeat.
"""

from __future__ import annotations

from typing import Any

from lib.scoring import PROFILES

CHOICES: tuple[str, ...] = tuple(sorted(PROFILES))
"""Every source a command may be scoped to: one per declared scoring profile."""

FLAG = "--adapter"

HELP = (
    "restrict this run to one source adapter "
    f"({', '.join(CHOICES)}); default is every source"
)


def add_argument(parser: Any) -> None:
    """Add the --adapter flag to a tool's parser. One spelling, one help line."""
    parser.add_argument(FLAG, choices=CHOICES, default=None, help=HELP)


def words(adapter: str | None) -> str:
    """How a run announces its own scope, for the line printed before it starts."""
    if not adapter:
        return "every source"
    profile = PROFILES.get(adapter)
    return f"{adapter} ({profile.display_name})" if profile else adapter


def scope(rows: list[dict[str, Any]], adapter: str | None) -> list[dict[str, Any]]:
    """Keep only the prospects belonging to one source, or all of them.

    Rows missing the column are treated as the schema's default, exactly as
    `lib.scoring.profile_for` does: migration 001 declares `source_adapter` as
    `not null default 'conexus_iedc'`, so an absent value is not an unknown.
    """
    if not adapter:
        return rows
    from lib.scoring import DEFAULT_ADAPTER

    return [r for r in rows
            if (r.get("source_adapter") or DEFAULT_ADAPTER) == adapter]
