"""Executes pending nodes across prospects and records what happened.

The runner is the only component that writes what a node learned. Nodes stay
pure; everything durable and everything dangerous happens here — dependency
ordering, retry accounting, evidence merging, and the refusal to let a node
promote a prospect past the point a human must sign off.

Two properties matter more than speed. First, one company's failure must never
end the run: 771 good records are not worth losing to one bad website. Second,
a re-run must be safe — work items are keyed per (prospect, node), evidence is
merged rather than appended, and notes are de-duplicated, so running the same
node twice changes nothing the second time.

Concurrency is per prospect, never per request to one host. The global
semaphore bounds total work in flight; RunContext's per-domain lock ensures we
still knock on any one door one at a time.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from lib import db
from lib.claims import validate_evidence_file
from lib.config import settings
from lib.nodes import NODE_REGISTRY, Node, NodeResult, RunContext, assert_stage_allowed


class CycleError(RuntimeError):
    """Node dependencies form a cycle, so no execution order exists."""


class NodeCounts(BaseModel):
    """Per-node tally for one run."""

    done: int = 0
    failed: int = 0
    skipped: int = 0
    pending: int = 0


class RunSummary(BaseModel):
    """What a run did, per node, and how long it took."""

    per_node: dict[str, NodeCounts] = Field(default_factory=dict)
    elapsed_seconds: float = 0.0


def _now() -> str:
    return datetime.now(UTC).isoformat()


def topological_order(node_names: list[str]) -> list[str]:
    """Order the requested nodes so dependencies run first. Raises on a cycle."""
    unknown = [name for name in node_names if name not in NODE_REGISTRY]
    if unknown:
        raise KeyError(f"unregistered node(s): {', '.join(sorted(unknown))}")

    indegree = dict.fromkeys(NODE_REGISTRY, 0)
    dependents: dict[str, list[str]] = {name: [] for name in NODE_REGISTRY}
    for name, node in NODE_REGISTRY.items():
        for dependency in node.depends_on:
            if dependency in NODE_REGISTRY:
                dependents[dependency].append(name)
                indegree[name] += 1

    ready = sorted(name for name, degree in indegree.items() if degree == 0)
    ordered: list[str] = []
    while ready:
        name = ready.pop(0)
        ordered.append(name)
        for dependent in dependents[name]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort()

    if len(ordered) < len(NODE_REGISTRY):
        cycle = sorted(set(NODE_REGISTRY) - set(ordered))
        raise CycleError(
            f"node dependency cycle among: {' -> '.join(cycle)} -> {cycle[0]}. "
            f"No execution order exists until one of those edges is removed."
        )

    rank = {name: index for index, name in enumerate(ordered)}
    return sorted(node_names, key=lambda name: rank[name])


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge an evidence patch into an evidence file, replacing whole claims.

    Nested blocks merge key by key, but a claim (anything carrying a 'value')
    is replaced outright — half-updating a claim would leave a value from one
    check date next to a source from another.
    """
    merged = dict(base or {})
    for key, value in (patch or {}).items():
        existing = merged.get(key)
        if isinstance(value, dict) and isinstance(existing, dict) and "value" not in value:
            merged[key] = deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def merge_notes(evidence: dict[str, Any], node_name: str, notes: list[str]) -> dict[str, Any]:
    """Record a node's notes alongside the evidence, without duplicating on re-run.

    Notes live in the evidence file because that is where they are useful: rule 9
    says a missing value is recorded as null with a note explaining the absence,
    and a note is worthless if a reviewer has to go somewhere else to find it.
    They carry no 'value' key, so the claim-shape trigger ignores them.
    """
    if not notes:
        return evidence
    merged = dict(evidence)
    existing = list(merged.get("notes") or [])
    seen = {(entry.get("node"), entry.get("note")) for entry in existing if isinstance(entry, dict)}
    for note in notes:
        if (node_name, note) not in seen:
            existing.append({"node": node_name, "note": note, "at": _now()})
            seen.add((node_name, note))
    merged["notes"] = existing
    return merged


def build_prospect_patch(prospect: dict[str, Any], node_name: str, result: NodeResult) -> dict:
    """Turn a NodeResult into the columns to write. Raises if the node overreached."""
    assert_stage_allowed(result.prospect_patch)
    patch = dict(result.prospect_patch)
    evidence = deep_merge(prospect.get("evidence_file") or {}, result.evidence_patch)
    evidence = merge_notes(evidence, node_name, result.notes)
    if evidence:
        problems = validate_evidence_file(evidence)
        if problems:
            raise ValueError("evidence would be invalid: " + "; ".join(problems[:3]))
        patch["evidence_file"] = evidence
    return patch


def _persist(node: Node, item: dict, prospect: dict, result: NodeResult) -> str:
    """Write one node's outcome. Returns the work status recorded."""
    if result.skipped:
        done = {"status": "skipped", "last_error": result.skip_reason}
    else:
        patch = build_prospect_patch(prospect, node.name, result)
        if patch:
            db.update_prospect(prospect["id"], patch)
        done = {"status": "done", "attempts": item["attempts"] + 1, "last_error": None}
    db.update_work_item(item["id"], {**done, "completed_at": _now()})
    return str(done["status"])


async def _run_one(
    node: Node,
    item: dict,
    prospect: dict,
    ctx: RunContext,
    semaphore: asyncio.Semaphore,
    counts: NodeCounts,
    progress: Progress,
    task_id: Any,
) -> None:
    """Run one node against one prospect, recording success or failure. Never raises."""
    async with semaphore:
        name = (prospect.get("company_name") or "?")[:38]
        progress.update(task_id, description=f"[cyan]{node.name}[/cyan] {name}")
        try:
            # Claiming the item is inside the try on purpose. It is a database
            # write like any other, and when it was outside, a single dropped
            # connection escaped this handler and ended the entire run.
            await asyncio.to_thread(
                db.update_work_item, item["id"], {"status": "running", "started_at": _now()}
            )
            result = await node.run(prospect, ctx)
            status = await asyncio.to_thread(_persist, node, item, prospect, result)
            setattr(counts, status, getattr(counts, status) + 1)
        except Exception as exc:
            counts.failed += 1
            # If we cannot even record the failure, the item stays 'running' and
            # the next run picks it up. Losing the rest of the batch over it
            # would be the worse outcome.
            with suppress(Exception):
                await asyncio.to_thread(
                    db.update_work_item,
                    item["id"],
                    {
                        "status": "failed",
                        "attempts": item["attempts"] + 1,
                        "last_error": f"{type(exc).__name__}: {exc}"[:2000],
                        "completed_at": _now(),
                    },
                )
        finally:
            progress.advance(task_id)


def _dependency_met(row: dict) -> bool:
    """True when a dependency has finished, one way or another.

    'done' is the obvious case. 'skipped' counts too, and must: case_study skips
    for the 502 companies with no case study, and front_door skips a prospect
    whose website could not be identified. If a skip did not satisfy the gate,
    score would be blocked forever for most of the pipeline.

    A 'failed' dependency counts only once its attempts are exhausted. Until
    then it is worth waiting for; after that it will never succeed, and blocking
    every downstream node on it would throw away the evidence that did arrive.
    """
    status = row.get("status")
    if status in ("done", "skipped"):
        return True
    if status != "failed":
        return False
    node = NODE_REGISTRY.get(row.get("node_name"))
    ceiling = node.max_attempts if node else 3
    return (row.get("attempts") or 0) >= ceiling


async def _select_items(node: Node, limit: int | None, force: bool, counts: NodeCounts) -> list:
    """Return the work items eligible to run, deferring any with unmet dependencies.

    'running' counts as eligible. There is no scheduler and no second runner, so
    an item still marked running is always the residue of a run that died, and
    leaving it out would strand it forever.
    """
    statuses = ["pending", "failed", "running"]
    if force:
        statuses.append("done")
    items = await asyncio.to_thread(db.list_work_items, node.name, statuses, limit)
    items = [i for i in items if force or i["attempts"] < node.max_attempts]
    if not node.depends_on or not items:
        return items

    ids = [i["prospect_id"] for i in items]
    rows = await asyncio.to_thread(
        db.list_work_items_for_prospects, ids, list(node.depends_on)
    )
    satisfied = {
        (row["prospect_id"], row["node_name"]) for row in rows if _dependency_met(row)
    }
    ready, blocked = [], []
    for item in items:
        deps_met = all((item["prospect_id"], dep) in satisfied for dep in node.depends_on)
        (ready if deps_met else blocked).append(item)
    for item in blocked:
        counts.pending += 1
        if item["status"] != "pending":
            # An unmet dependency is not this node's failure. Put it back in the
            # queue so it runs for free once the dependency lands.
            await asyncio.to_thread(
                db.update_work_item, item["id"], {"status": "pending", "last_error": None}
            )
    return ready


async def run_nodes(
    node_names: list[str],
    limit: int | None = None,
    concurrency: int = 8,
    force: bool = False,
    console: Console | None = None,
) -> RunSummary:
    """Run the named nodes over their pending prospects and report what happened."""
    console = console or Console()
    order = topological_order(node_names)
    summary = RunSummary(per_node={name: NodeCounts() for name in order})
    started = time.monotonic()
    semaphore = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)

    async with httpx.AsyncClient(limits=limits) as client:
        ctx = RunContext(client, settings)
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            for name in order:
                node = NODE_REGISTRY[name]
                counts = summary.per_node[name]
                items = await _select_items(node, limit, force, counts)
                if not items:
                    continue
                prospects = {
                    p["id"]: p
                    for p in await asyncio.to_thread(
                        db.get_prospects_by_ids, [i["prospect_id"] for i in items]
                    )
                }
                task_id = progress.add_task(f"[cyan]{name}", total=len(items))
                await asyncio.gather(
                    *(
                        _run_one(
                            node, item, prospects[item["prospect_id"]],
                            ctx, semaphore, counts, progress, task_id,
                        )
                        for item in items
                        if item["prospect_id"] in prospects
                    )
                )

    summary.elapsed_seconds = round(time.monotonic() - started, 2)
    return summary
