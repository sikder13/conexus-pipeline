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

import time
from functools import lru_cache
from typing import Any

import httpx
from postgrest.exceptions import APIError
from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from lib.config import settings

PROSPECTS_TABLE = "prospects"
TOUCHES_TABLE = "outreach_touches"
SOURCE_ADAPTERS_TABLE = "source_adapters"
WORK_ITEMS_TABLE = "work_items"


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


TRANSIENT_ATTEMPTS = 3
TRANSIENT_BACKOFF_SECONDS = 0.5


def _run_query(build, operation: str, retryable: bool = True):
    """Execute a PostgREST call, retrying transient transport failures.

    A long node run makes thousands of small requests, and Supabase will
    occasionally close a pooled connection between two of them. That surfaces
    as httpx.RemoteProtocolError rather than an APIError, so it used to escape
    every handler and kill the whole run — 500 good records lost to one dropped
    socket. Transport failures are retried here; a database error is not, since
    a rejected claim or a bad column is an answer, not a hiccup.

    ``retryable=False`` is for plain inserts, where a dropped response leaves
    genuine doubt about whether the row landed and a blind retry could double
    it. Updates and upserts are idempotent and retry freely.
    """
    attempts = TRANSIENT_ATTEMPTS if retryable else 1
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return build()
        except APIError as exc:
            raise _wrap(exc, operation) from exc
        except httpx.HTTPError as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(TRANSIENT_BACKOFF_SECONDS * (2**attempt))
    raise PipelineDBError(
        operation, f"transport failure after {attempts} attempt(s): {type(last).__name__}: {last}"
    )


@lru_cache(maxsize=1)
def get_client() -> Client:
    """Return the process-wide Supabase client, constructing it on first use.

    Keep-alive connections are given a short expiry: a pooled socket that has
    been idle longer than the server's own timeout is the usual cause of the
    dropped-connection errors ``_run_query`` retries, and not reusing it is
    cheaper than recovering from it.
    """
    options = SyncClientOptions(
        httpx_client=httpx.Client(
            transport=httpx.HTTPTransport(retries=2),
            limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=15.0),
            timeout=httpx.Timeout(settings.request_timeout_seconds * 3),
        )
    )
    return create_client(settings.supabase_url, settings.supabase_service_role_key, options)


def insert_prospect(data: dict[str, Any]) -> dict[str, Any]:
    """Insert one prospect and return the stored row."""
    response = _run_query(
        lambda: get_client().table(PROSPECTS_TABLE).insert(data).execute(),
        f"insert_prospect({data.get('company_name')!r})",
        retryable=False,
    )
    if not response.data:
        raise PipelineDBError(
            f"insert_prospect({data.get('company_name')!r})",
            "insert returned no row",
        )
    return response.data[0]


def update_prospect(prospect_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update to one prospect and return the stored row."""
    operation = f"update_prospect({prospect_id})"
    response = _run_query(lambda: (
            get_client().table(PROSPECTS_TABLE).update(data).eq("id", prospect_id).execute()
        ), operation)
    if not response.data:
        raise PipelineDBError(operation, "no row matched that id")
    return response.data[0]


def get_prospect(prospect_id: str) -> dict[str, Any] | None:
    """Return one prospect by id, or None if it does not exist."""
    operation = f"get_prospect({prospect_id})"
    response = _run_query(lambda: (
            get_client()
            .table(PROSPECTS_TABLE)
            .select("*")
            .eq("id", prospect_id)
            .limit(1)
            .execute()
        ), operation)
    return response.data[0] if response.data else None


def find_prospect_by_name(company_name: str) -> dict[str, Any] | None:
    """Return the first prospect with this exact company name, or None.

    Exact match by design: fuzzy company-name matching is a research decision
    for the Extractor to make explicitly, not something to hide in a lookup.
    """
    operation = f"find_prospect_by_name({company_name!r})"
    response = _run_query(lambda: (
            get_client()
            .table(PROSPECTS_TABLE)
            .select("*")
            .eq("company_name", company_name)
            .limit(1)
            .execute()
        ), operation)
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
    response = _run_query(lambda: (
            query.order("signal_score", desc=True, nullsfirst=False).limit(limit).execute()
        ), operation)
    return response.data or []


def log_touch(data: dict[str, Any]) -> dict[str, Any]:
    """Record one outreach touch and return the stored row."""
    operation = f"log_touch(prospect_id={data.get('prospect_id')})"
    response = _run_query(
        lambda: get_client().table(TOUCHES_TABLE).insert(data).execute(),
        operation,
        retryable=False,
    )
    if not response.data:
        raise PipelineDBError(operation, "insert returned no row")
    return response.data[0]


def count_rows(table: str) -> int:
    """Return the exact row count for a table. Used by health checks."""
    operation = f"count_rows({table})"
    response = _run_query(
        lambda: get_client().table(table).select("*", count="exact", head=True).execute(),
        operation,
    )
    return response.count or 0


def delete_prospects_by_name(company_name: str) -> int:
    """Delete every prospect with this exact company name; return how many went.

    Name-keyed deletion exists for test fixtures, which are identified by a
    reserved company name. Real prospects are never deleted — records move to
    stage 'dead' so the research and the reason survive.
    """
    operation = f"delete_prospects_by_name({company_name!r})"
    response = _run_query(lambda: (
            get_client().table(PROSPECTS_TABLE).delete().eq("company_name", company_name).execute()
        ), operation)
    return len(response.data or [])


def count_prospects_by_name(company_name: str) -> int:
    """Return how many prospects carry this exact company name."""
    operation = f"count_prospects_by_name({company_name!r})"
    response = _run_query(lambda: (
            get_client()
            .table(PROSPECTS_TABLE)
            .select("*", count="exact", head=True)
            .eq("company_name", company_name)
            .execute()
        ), operation)
    return response.count or 0


# ---------- WORK QUEUE (migration 002) ----------
#
# The runner reads and writes these. They are kept here rather than in
# lib/runner.py so that the rule "all database access goes through lib/db.py"
# survives contact with the node framework.


def enqueue_work_items(prospect_id: str, node_names: list[str]) -> int:
    """Create pending work items for a prospect, ignoring any that already exist.

    Idempotent by way of the unique (prospect_id, node_name) constraint: running
    the extractor again over a company already in the database re-enqueues
    nothing and loses no history.
    """
    if not node_names:
        return 0
    operation = f"enqueue_work_items({prospect_id})"
    rows = [{"prospect_id": prospect_id, "node_name": name} for name in node_names]
    response = _run_query(lambda: (
            get_client()
            .table(WORK_ITEMS_TABLE)
            .upsert(rows, on_conflict="prospect_id,node_name", ignore_duplicates=True)
            .execute()
        ), operation)
    return len(response.data or [])


def list_work_items(
    node_name: str, statuses: list[str], limit: int | None = None
) -> list[dict[str, Any]]:
    """Return work items for one node in any of the given statuses, oldest first."""
    return _fetch_all(
        lambda: (
            get_client()
            .table(WORK_ITEMS_TABLE)
            .select("*")
            .eq("node_name", node_name)
            .in_("status", statuses)
            .order("created_at")
            .order("id")
        ),
        f"list_work_items({node_name})",
        limit,
    )


ID_BATCH = 100
"""How many ids to put in one `in_` filter. PostgREST takes them in the query
string, so an unbounded list becomes an over-long URL and a confusing 414."""

PAGE_SIZE = 1000
"""Rows per request when paging through a large select.

Supabase caps a single PostgREST response at 1000 rows and does not say so in
the body. An unpaginated read therefore looks like a complete answer while
quietly losing everything past the first thousand — which, with 572 prospects
and one work item per node, starts happening at the second node. Every select
that can exceed a thousand rows goes through `_fetch_all`."""


def _fetch_all(build_query, operation: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Page through a select until the server stops returning full pages.

    ``build_query`` must return a FRESH query each call: PostgREST builders
    accumulate state, so reusing one would stack range headers.
    """
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        wanted = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - len(rows))
        if wanted <= 0:
            return rows
        response = _run_query(
            lambda start=offset, size=wanted: build_query()
            .range(start, start + size - 1)
            .execute(),
            operation,
        )
        page = response.data or []
        rows.extend(page)
        if len(page) < wanted:
            return rows
        offset += len(page)


def _batched(values: list[str]) -> list[list[str]]:
    """Split a list of ids into URL-safe batches."""
    return [values[i : i + ID_BATCH] for i in range(0, len(values), ID_BATCH)]


def list_work_items_for_prospects(
    prospect_ids: list[str], node_names: list[str]
) -> list[dict[str, Any]]:
    """Return the work items for these prospects and nodes, for dependency checks."""
    if not prospect_ids or not node_names:
        return []
    operation = "list_work_items_for_prospects"
    rows: list[dict[str, Any]] = []
    for batch in _batched(prospect_ids):
        response = _run_query(
            lambda ids=batch: (
                get_client()
                .table(WORK_ITEMS_TABLE)
                .select("prospect_id,node_name,status,attempts")
                .in_("prospect_id", ids)
                .in_("node_name", node_names)
                .execute()
            ),
            operation,
        )
        rows.extend(response.data or [])
    return rows


def update_work_item(item_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update to one work item and return the stored row."""
    operation = f"update_work_item({item_id})"
    response = _run_query(lambda: (
            get_client().table(WORK_ITEMS_TABLE).update(data).eq("id", item_id).execute()
        ), operation)
    if not response.data:
        raise PipelineDBError(operation, "no work item matched that id")
    return response.data[0]


def get_prospects_by_ids(prospect_ids: list[str]) -> list[dict[str, Any]]:
    """Return full prospect rows for a batch of ids."""
    if not prospect_ids:
        return []
    operation = "get_prospects_by_ids"
    rows: list[dict[str, Any]] = []
    for batch in _batched(prospect_ids):
        response = _run_query(
            lambda ids=batch: (
                get_client().table(PROSPECTS_TABLE).select("*").in_("id", ids).execute()
            ),
            operation,
        )
        rows.extend(response.data or [])
    return rows


def work_queue_snapshot() -> list[dict[str, Any]]:
    """Return every work item's node and status, for the queue status report.

    Deliberately a full scan rather than a grouped aggregate: PostgREST has no
    group-by, the table has one row per prospect per node, and counting in
    Python keeps this honest and dependency-free at the scale this pipeline runs.
    Paged, because the table passes a thousand rows at two nodes per prospect.
    """
    return _fetch_all(
        lambda: get_client().table(WORK_ITEMS_TABLE).select("node_name,status").order("id"),
        "work_queue_snapshot",
    )


def list_prospect_identities(source_adapter: str | None = None) -> list[dict[str, Any]]:
    """Return id and names for existing prospects, for duplicate detection.

    The extractor matches on a normalised name rather than an exact one, because
    the normalize_identity node rewrites company_name after extraction. Matching
    exactly would make a second extraction run insert duplicates of every record
    a node had already tidied.
    """
    def build():
        query = (
            get_client().table(PROSPECTS_TABLE).select("id,company_name,dba_name,stage").order("id")
        )
        return query.eq("source_adapter", source_adapter) if source_adapter else query

    return _fetch_all(build, "list_prospect_identities")


def list_prospects_full() -> list[dict[str, Any]]:
    """Return every prospect row in full. Used by the invariant audit.

    Paged, because the table passes a thousand rows and an unpaged read would
    audit a subset while reporting on the whole — which is precisely the class
    of failure the audit exists to catch.
    """
    return _fetch_all(
        lambda: get_client().table(PROSPECTS_TABLE).select("*").order("id"),
        "list_prospects_full",
    )


def all_work_items() -> list[dict[str, Any]]:
    """Return every work item in full, for reconciliation checks."""
    return _fetch_all(
        lambda: get_client().table(WORK_ITEMS_TABLE).select("*").order("id"),
        "all_work_items",
    )
