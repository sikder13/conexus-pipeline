"""End-to-end check that config, database and claim enforcement all work.

Run from the repo root with `python -m tools.smoke_test` after setting up .env.
It is the first thing to run on a new machine and the first thing to run when
something has started behaving strangely.

The interesting step is step 4. It deliberately writes a claim with no tier and
expects the database to refuse it. If that write *succeeds*, the claim-shape
backstop in migration 001 is not doing its job, orphan claims can reach the
evidence file, and the pipeline's core guarantee is broken — so a successful
write there is reported as a failure of this test, loudly.

The test writes one row named `__SMOKE_TEST__` and removes it again, including
when an earlier step has already failed.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lib import db
from lib.claims import Tier, make_claim, validate_evidence_file
from lib.config import missing_optional_keys, settings

SMOKE_TEST_NAME = "__SMOKE_TEST__"


class SmokeTestFailure(AssertionError):
    """A smoke-test step did not observe what it required."""


@dataclass
class StepResult:
    """Outcome of one step, collected for the closing summary."""

    name: str
    passed: bool
    detail: str


def _step_load_config() -> str:
    """Step 1: confirm configuration loaded and report absent optional keys."""
    absent = missing_optional_keys()
    absent_note = ", ".join(absent) if absent else "none"
    return (
        f"supabase_url={settings.supabase_url} · "
        f"user_agent={settings.user_agent!r} · "
        f"timeout={settings.request_timeout_seconds}s · "
        f"delay={settings.fetch_delay_seconds}s · "
        f"optional keys absent: {absent_note}"
    )


def _step_count_rows() -> str:
    """Step 2: connect to Supabase and count rows in the two seeded tables."""
    prospects = db.count_rows(db.PROSPECTS_TABLE)
    adapters = db.count_rows(db.SOURCE_ADAPTERS_TABLE)
    if adapters < 1:
        raise SmokeTestFailure(
            "source_adapters is empty; migration 001 seeds 'conexus_iedc' and should not be."
        )
    return f"prospects={prospects} rows · source_adapters={adapters} rows"


def _step_insert_and_read_back(state: dict[str, Any]) -> str:
    """Step 3: write a two-claim evidence file and confirm it round-trips intact."""
    evidence = {
        "grant_award": make_claim(
            f"{SMOKE_TEST_NAME} synthetic grant record",
            Tier.T1,
            "https://www.in.gov/iedc/",
        ),
        "press_mention": make_claim(
            f"{SMOKE_TEST_NAME} synthetic press mention",
            Tier.T2,
            "https://example.com/press/smoke-test",
        ),
    }
    problems = validate_evidence_file(evidence)
    if problems:
        raise SmokeTestFailure(
            "claims built by lib/claims.py failed our own validator: " + "; ".join(problems)
        )

    inserted = db.insert_prospect(
        {
            "company_name": SMOKE_TEST_NAME,
            "city": "Muncie",
            "county": "Delaware",
            "stage": "extracted",
            "evidence_file": evidence,
        }
    )
    state["prospect_id"] = inserted["id"]

    read_back = db.get_prospect(inserted["id"])
    if read_back is None:
        raise SmokeTestFailure("inserted row could not be read back by id")
    if read_back["evidence_file"] != evidence:
        raise SmokeTestFailure(
            "evidence_file did not round-trip intact.\n"
            f"  wrote: {evidence}\n"
            f"  read : {read_back['evidence_file']}"
        )

    found = db.find_prospect_by_name(SMOKE_TEST_NAME)
    if found is None or found["id"] != inserted["id"]:
        raise SmokeTestFailure("find_prospect_by_name did not return the row we just inserted")
    return f"id={inserted['id']} · 2 claims (T1, T2) written and read back byte-identical"


def _step_malformed_claim_rejected(state: dict[str, Any]) -> str:
    """Step 4: confirm the database refuses a claim with no tier.

    This is the ONE place in the codebase that hand-builds a claim dict instead
    of using lib/claims.py, and it does so precisely because the claim must be
    invalid. Do not copy this pattern.
    """
    prospect_id = state.get("prospect_id")
    if prospect_id is None:
        raise SmokeTestFailure("no test row to update; step 3 did not complete")

    malformed_evidence = {
        "grant_award": {
            "value": f"{SMOKE_TEST_NAME} claim with no provenance",
            "source_url": "https://www.in.gov/iedc/",
            "date_checked": "2026-08-08",
            "verified": False,
            "verified_at": None,
        }
    }

    our_problems = validate_evidence_file(malformed_evidence)
    if not our_problems:
        raise SmokeTestFailure(
            "validate_evidence_file accepted a claim with no tier; it no longer "
            "mirrors the database trigger."
        )

    try:
        db.update_prospect(prospect_id, {"evidence_file": malformed_evidence})
    except db.EvidenceClaimRejected as exc:
        state["rejection_message"] = str(exc)
        return f"database rejected it, verbatim: {str(exc)!r} (our validator also caught it)"

    raise SmokeTestFailure(
        "THE DATABASE ACCEPTED A CLAIM WITH NO TIER. The claim-shape backstop in "
        "migration 001 (trg_validate_evidence / validate_evidence_claims) is not "
        "active on this project. Orphan claims can reach evidence_file. Do not run "
        "the pipeline against this database until the trigger is restored."
    )


def _step_cleanup() -> str:
    """Step 5: delete the test row and confirm none remain."""
    deleted = db.delete_prospects_by_name(SMOKE_TEST_NAME)
    remaining = db.count_prospects_by_name(SMOKE_TEST_NAME)
    if remaining != 0:
        raise SmokeTestFailure(
            f"{remaining} row(s) named {SMOKE_TEST_NAME} still present after cleanup"
        )
    return f"deleted {deleted} row(s) · 0 remain"


def _run(results: list[StepResult], name: str, step: Callable[[], str]) -> bool:
    """Run one step, record its outcome, and never raise."""
    try:
        detail = step()
    except Exception as exc:
        results.append(StepResult(name, False, f"{type(exc).__name__}: {exc}"))
        return False
    results.append(StepResult(name, True, detail))
    return True


def _render(console: Console, results: list[StepResult]) -> bool:
    """Print the per-step summary. Returns True when every step passed."""
    table = Table(title="Smoke test", show_lines=True, title_justify="left")
    table.add_column("Step", style="bold", no_wrap=True)
    table.add_column("Result", no_wrap=True)
    table.add_column("Detail", overflow="fold")

    for result in results:
        marker = "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
        table.add_row(result.name, marker, result.detail)

    console.print(table)

    passed = sum(1 for result in results if result.passed)
    all_passed = passed == len(results)
    summary = f"{passed}/{len(results)} steps passed"
    console.print(
        Panel(
            summary,
            style="green" if all_passed else "red",
            title="PASS" if all_passed else "FAIL",
        )
    )
    return all_passed


def main() -> int:
    """Run every step, always clean up, and return a shell exit code."""
    console = Console()
    results: list[StepResult] = []
    state: dict[str, Any] = {}

    try:
        _run(results, "1. Load configuration", _step_load_config)
        _run(results, "2. Connect and count rows", _step_count_rows)
        wrote_claims = _run(
            results, "3. Write and read back claims", lambda: _step_insert_and_read_back(state)
        )
        if wrote_claims:
            _run(
                results,
                "4. Malformed claim rejected",
                lambda: _step_malformed_claim_rejected(state),
            )
        else:
            results.append(
                StepResult("4. Malformed claim rejected", False, "skipped: step 3 did not complete")
            )
    finally:
        _run(results, "5. Clean up test row", _step_cleanup)
        all_passed = _render(console, results)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
