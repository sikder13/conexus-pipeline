"""Runner CLI — executes pending nodes across prospects.

Run from the repo root:

    python -m tools.runner --status                  # what is queued; writes nothing
    python -m tools.runner                           # run every registered node
    python -m tools.runner --nodes resolve_website --limit 50 --concurrency 8
    python -m tools.runner --nodes resolve_website --force

There is no scheduler and no daemon. Runs are started by a person, which is
deliberate: every run touches other people's servers, and the decision to do
that should be someone's, not a timer's.

`--status` is the first thing to reach for. It answers "what does the pipeline
still owe me" without doing any work or making any request.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from rich.console import Console
from rich.table import Table

# Importing the node package registers every node into NODE_REGISTRY.
import tools.harvester.nodes  # noqa: F401
from lib import db
from lib.nodes import NODE_REGISTRY
from lib.runner import RunSummary, run_nodes

STATUSES = ("pending", "running", "done", "failed", "skipped")


def render_summary(summary: RunSummary, console: Console) -> None:
    """Print the end-of-run table: per node, what landed where."""
    table = Table(title="Run summary", title_justify="left")
    table.add_column("Node", style="bold")
    for column in ("done", "failed", "skipped", "pending"):
        table.add_column(column.capitalize(), justify="right")
    for name, counts in summary.per_node.items():
        table.add_row(
            name,
            f"[green]{counts.done}[/green]" if counts.done else "0",
            f"[red]{counts.failed}[/red]" if counts.failed else "0",
            str(counts.skipped),
            str(counts.pending),
        )
    console.print(table)
    console.print(f"Elapsed: [bold]{summary.elapsed_seconds}s[/bold]")


def render_status(console: Console) -> int:
    """Print the queue state for every node and return a shell exit code."""
    rows = db.work_queue_snapshot()
    counts: Counter[tuple[str, str]] = Counter(
        (row["node_name"], row["status"]) for row in rows
    )
    node_names = sorted({row["node_name"] for row in rows} | set(NODE_REGISTRY))

    table = Table(title="Work queue", title_justify="left")
    table.add_column("Node", style="bold")
    for status in STATUSES:
        table.add_column(status.capitalize(), justify="right")
    table.add_column("Total", justify="right")

    for name in node_names:
        per_status = [counts[(name, status)] for status in STATUSES]
        label = name if name in NODE_REGISTRY else f"{name} [dim](unregistered)[/dim]"
        table.add_row(label, *(str(value) for value in per_status), str(sum(per_status)))

    console.print(table)
    console.print(f"{len(rows)} work item(s) across {len(node_names)} node(s).")
    if not rows:
        console.print("[yellow]Queue is empty. Run `python -m tools.extractor` first.[/yellow]")
    return 0


def parse_nodes(raw: str | None) -> list[str]:
    """Resolve the --nodes argument to a list of registered node names."""
    if not raw:
        return sorted(NODE_REGISTRY)
    names = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [name for name in names if name not in NODE_REGISTRY]
    if unknown:
        raise SystemExit(
            f"unknown node(s): {', '.join(unknown)}. "
            f"Registered: {', '.join(sorted(NODE_REGISTRY))}"
        )
    return names


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.runner", description=__doc__)
    parser.add_argument("--nodes", default=None, help="comma-separated node names (default: all)")
    parser.add_argument("--limit", type=int, default=None, help="process only N prospects per node")
    parser.add_argument(
        "--concurrency", type=int, default=8, help="prospects in flight (default 8)"
    )
    parser.add_argument("--force", action="store_true", help="re-run items already marked done")
    parser.add_argument("--status", action="store_true", help="print the queue state and exit")
    parser.add_argument(
        "--summarize-all",
        action="store_true",
        help="run the summary node for every priority, not just P1 and P2",
    )
    args = parser.parse_args()

    console = Console()
    if args.status:
        return render_status(console)

    if args.summarize_all:
        # The node reads this itself; widening it here keeps the decision in the
        # CLI, where the operator made it.
        NODE_REGISTRY["summary"].include_priorities = None
        console.print("[yellow]Summarising every priority, not just P1 and P2.[/yellow]")

    summary = asyncio.run(
        run_nodes(
            parse_nodes(args.nodes),
            limit=args.limit,
            concurrency=args.concurrency,
            force=args.force,
            console=console,
        )
    )
    render_summary(summary, console)
    failed = sum(counts.failed for counts in summary.per_node.values())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
