"""The only door to Supabase. Every read and write in the pipeline goes here.

Deliberately thin: no ORM, no caching layer, no clever query builder. It exists
for three reasons.

1. One place constructs the client, so the service role key is handled once.
   Migration 001 turns RLS on with no policies, which means the service role is
   the only way in and there is no browser client to leak it to.
2. One place turns Supabase failures into ``PipelineDBError`` with enough
   context to say which operation failed and on what.
3. One place recognises the evidence-claim rejection raised by the
   ``validate_evidence_claims()`` trigger and re-raises it with the database's
   own wording intact. That rejection is not a nuisance to be smoothed over —
   it means a tool tried to write a claim with no provenance, which is the
   exact failure this system is built to prevent. Callers must see it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from postgrest.exceptions import APIError
from supabase import Client, create_client

from lib.config import settings

PROSPECTS_TABLE = "prospects"
TOUCHES_TABLE = "outreach_touches"
SOURCE_ADAPTERS_TABLE = "source_adapters"


class PipelineDBError(RuntimeError):
    """A Supabase operation failed. Carries the operation and the database's reply."""

    def __init__(self, operation: str, detail: str, raw: dict[str, Any] | None = None) -> None:
        super().__init__(f"{operation} failed: {detail}")
        self.operation = operation
        self.detail = detail
        self.raw = raw or {}


class EvidenceClaimRejected(PipelineDBError):
    """The database refused an ``evidence_file`` containing claims without provenance.

    ``str(...)`` on this exception is the database's message verbatim, because
    the message names how many claims were bad and which rule they broke. Do not
    reword it when reporting to an operator.
    """

    def __init__(self, operation: str, db_message: str, raw: dict[str, Any] | None = None) -> None:
        RuntimeError.__init__(self, db_message)
        self.operation = operation
        self.detail = db_message
        self.db_message = db_message
        self.raw = raw or {}


def _is_claim_rejection(error: APIError) -> bool:
    """True when ``error`` is the evidence-claim trigger firing, not a generic failure."""
    message = error.message or ""
    return error.code == "P0001" and "evidence_file contains" in message


def _wrap(error: APIError, operation: str) -> PipelineDBError:
    """Translate a PostgREST error into the right pipeline exception."""
    raw = error.json() if hasattr(error, "json") else {}
    if _is_claim_rejection(error):
        return EvidenceClaimRejected(operation, error.message or "", raw)
    parts = [part for part in (error.message, error.details, error.hint) if part]
    detail = " | ".join(parts) or repr(error)
    if error.code:
        detail = f"[{error.code}] {detail}"
    return PipelineDBError(operation, detail, raw)


@lru_cache(maxsize=1)
def get_client() -> Client:
    """Return the process-wide Supabase client, constructing it on first use."""
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def insert_prospect(data: dict[str, Any]) -> dict[str, Any]:
    """Insert one prospect and return the stored row."""
    try:
        response = get_client().table(PROSPECTS_TABLE).insert(data).execute()
    except APIError as exc:
        raise _wrap(exc, f"insert_prospect({data.get('company_name')!r})") from exc
    if not response.data:
        raise PipelineDBError(
            f"insert_prospect({data.get('company_name')!r})",
            "insert returned no row",
        )
    return response.data[0]


def update_prospect(prospect_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update to one prospect and return the stored row."""
    operation = f"update_prospect({prospect_id})"
    try:
        response = (
            get_client().table(PROSPECTS_TABLE).update(data).eq("id", prospect_id).execute()
        )
    except APIError as exc:
        raise _wrap(exc, operation) from exc
    if not response.data:
        raise PipelineDBError(operation, "no row matched that id")
    return response.data[0]


def get_prospect(prospect_id: str) -> dict[str, Any] | None:
    """Return one prospect by id, or None if it does not exist."""
    operation = f"get_prospect({prospect_id})"
    try:
        response = (
            get_client()
            .table(PROSPECTS_TABLE)
            .select("*")
            .eq("id", prospect_id)
            .limit(1)
            .execute()
        )
    except APIError as exc:
        raise _wrap(exc, operation) from exc
    return response.data[0] if response.data else None


def find_prospect_by_name(company_name: str) -> dict[str, Any] | None:
    """Return the first prospect with this exact company name, or None.

    Exact match by design: fuzzy company-name matching is a research decision
    for the Extractor to make explicitly, not something to hide in a lookup.
    """
    operation = f"find_prospect_by_name({company_name!r})"
    try:
        response = (
            get_client()
            .table(PROSPECTS_TABLE)
            .select("*")
            .eq("company_name", company_name)
            .limit(1)
            .execute()
        )
    except APIError as exc:
        raise _wrap(exc, operation) from exc
    return response.data[0] if response.data else None


def list_prospects(
    stage: str | None = None,
    priority: str | None = None,
    min_score: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return prospects matching the given filters, highest score first."""
    operation = "list_prospects"
    query = get_client().table(PROSPECTS_TABLE).select("*")
    if stage is not None:
        query = query.eq("stage", stage)
    if priority is not None:
        query = query.eq("priority", priority)
    if min_score is not None:
        query = query.gte("signal_score", min_score)
    try:
        response = (
            query.order("signal_score", desc=True, nullsfirst=False).limit(limit).execute()
        )
    except APIError as exc:
        raise _wrap(exc, operation) from exc
    return response.data or []


def log_touch(data: dict[str, Any]) -> dict[str, Any]:
    """Record one outreach touch and return the stored row."""
    operation = f"log_touch(prospect_id={data.get('prospect_id')})"
    try:
        response = get_client().table(TOUCHES_TABLE).insert(data).execute()
    except APIError as exc:
        raise _wrap(exc, operation) from exc
    if not response.data:
        raise PipelineDBError(operation, "insert returned no row")
    return response.data[0]


def count_rows(table: str) -> int:
    """Return the exact row count for a table. Used by health checks."""
    operation = f"count_rows({table})"
    try:
        response = get_client().table(table).select("*", count="exact", head=True).execute()
    except APIError as exc:
        raise _wrap(exc, operation) from exc
    return response.count or 0


def delete_prospects_by_name(company_name: str) -> int:
    """Delete every prospect with this exact company name; return how many went.

    Name-keyed deletion exists for test fixtures, which are identified by a
    reserved company name. Real prospects are never deleted — records move to
    stage 'dead' so the research and the reason survive.
    """
    operation = f"delete_prospects_by_name({company_name!r})"
    try:
        response = (
            get_client().table(PROSPECTS_TABLE).delete().eq("company_name", company_name).execute()
        )
    except APIError as exc:
        raise _wrap(exc, operation) from exc
    return len(response.data or [])


def count_prospects_by_name(company_name: str) -> int:
    """Return how many prospects carry this exact company name."""
    operation = f"count_prospects_by_name({company_name!r})"
    try:
        response = (
            get_client()
            .table(PROSPECTS_TABLE)
            .select("*", count="exact", head=True)
            .eq("company_name", company_name)
            .execute()
        )
    except APIError as exc:
        raise _wrap(exc, operation) from exc
    return response.count or 0
