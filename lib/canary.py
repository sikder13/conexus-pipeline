"""Canary protocol — the state machine that decides whether sending may continue.

THE POINT

Outreach now runs without a human checking each claim first. The human moved
from upstream (verifying inputs) to downstream (breaking the circuit on
outputs). That only works if the circuit-breaker is real: pre-registered rules,
written down before the first send, that cannot be argued with afterwards when
a batch is going well and stopping feels expensive.

The rules live in docs/CANARY.md with dates. This module is their mechanism.

THE DISTINCTION THAT MATTERS MOST

A reply correcting an ESTIMATE is a success. We publish conditional ranges
precisely so a prospect will say "actually it's about forty a month" — that is
the model working, and it improves the next draft.

A reply correcting a FACT is a failure of the whole safety model. It means
something we asserted with a source attached was wrong, and since no human
checked that source before it went out, every other unchecked assertion is now
suspect. One is enough to halt everything, pipeline-wide, until an operator
says otherwise.

Halting is cheap. Discovering on batch nine that batches one through eight were
all wrong is not.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

BATCH_SIZE = 10
CONSERVATIVE_BATCHES = 2
"""Batches 1-2 use only corroborated-or-verbatim claims and conservative ranges."""


class SendHalted(RuntimeError):
    """A send was attempted while the pipeline is halted.

    Raised, never returned as a status, so a caller cannot proceed by ignoring
    a return value. Every send path must let this propagate.
    """


class CanaryState(BaseModel):
    """The pipeline's send posture."""

    halted: bool = False
    halt_reason: str | None = None
    halted_at: str | None = None
    batches_sent: int = 0
    factual_corrections: int = 0
    inferable_eligible: bool = False

    @property
    def in_conservative_phase(self) -> bool:
        """True while the earliest batches must use only the strongest claims."""
        return self.batches_sent < CONSERVATIVE_BATCHES

    def allowed_verdicts(self) -> tuple[str, ...]:
        """Which claimcheck verdicts may be asserted outbound right now."""
        if self.inferable_eligible and not self.in_conservative_phase:
            return ("verbatim", "inferable")
        return ("verbatim",)


def read_state() -> CanaryState:
    """The current canary state from the database."""
    from lib import db

    row = db.canary_row()
    return CanaryState(
        halted=bool(row.get("halted")),
        halt_reason=row.get("halt_reason"),
        halted_at=str(row.get("halted_at")) if row.get("halted_at") else None,
        batches_sent=int(row.get("batches_sent") or 0),
        factual_corrections=int(row.get("factual_corrections") or 0),
        inferable_eligible=bool(row.get("inferable_eligible")),
    )


def assert_sendable(state: CanaryState | None = None) -> CanaryState:
    """The gate every send path must call first. Raises SendHalted when halted.

    There is no bypass parameter. A send path that wants to skip this has to
    delete the call, which is visible in review; a `force=True` argument would
    not be.
    """
    current = state or read_state()
    if current.halted:
        raise SendHalted(
            f"sending is halted pipeline-wide: {current.halt_reason or 'no reason recorded'}"
            f" (since {current.halted_at or 'unknown'}). Resumable only by explicit "
            f"operator command."
        )
    return current


def halt(reason: str) -> CanaryState:
    """Stop all sending, everywhere, now."""
    from lib import db

    db.update_canary({
        "halted": True,
        "halt_reason": reason[:1000],
        "halted_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    })
    return read_state()


def record_factual_correction(detail: str) -> CanaryState:
    """A prospect corrected a FACT. Halt everything and count it.

    Not a threshold and not a rate: one is enough. The claim carried a source
    and was still wrong, and nothing downstream of that source can be trusted
    until a person has looked.
    """
    from lib import db

    state = read_state()
    db.update_canary({"factual_corrections": state.factual_corrections + 1})
    return halt(f"factual correction received: {detail}")


def record_estimate_correction(detail: str) -> CanaryState:
    """A prospect corrected an ESTIMATE. Recorded, and explicitly not a halt.

    We invite these. A conditional range exists so somebody will replace it
    with their real number; treating that as a failure would push the drafter
    toward vaguer claims, which is the opposite of what we want.
    """
    from lib import db

    db.update_canary({"updated_at": datetime.now(UTC).isoformat()})
    return read_state()


def record_batch_sent(clean: bool) -> CanaryState:
    """Register a completed batch. Opens 'inferable' once two land clean.

    ``clean`` means the batch drew no factual-correction reply. Two consecutive
    clean batches is the pre-registered bar for widening what may be asserted.
    """
    from lib import db

    state = read_state()
    patch: dict[str, Any] = {
        "batches_sent": state.batches_sent + 1,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if (
        clean
        and state.factual_corrections == 0
        and state.batches_sent + 1 >= CONSERVATIVE_BATCHES
    ):
        patch["inferable_eligible"] = True
    db.update_canary(patch)
    return read_state()


def resume(operator_note: str) -> CanaryState:
    """Lift a halt. Requires a note, because a halt lifted without one is a shrug."""
    from lib import db

    if not (operator_note or "").strip():
        raise ValueError(
            "resuming requires an operator note saying what was checked and fixed"
        )
    db.update_canary({
        "halted": False,
        "halt_reason": f"resumed: {operator_note.strip()[:900]}",
        "updated_at": datetime.now(UTC).isoformat(),
    })
    return read_state()
