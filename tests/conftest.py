"""Shared test scaffolding.

Two jobs. First, pin placeholder credentials into the environment before
anything imports lib.config, so the suite runs identically on a machine with no
.env and can never accidentally reach the real Supabase project. python-dotenv
does not override variables that are already set, so these win over a developer's
real .env.

Second, provide a fake HTTP client. No test in this suite is allowed to touch
the network; every node and every fetch is exercised against these stubs.
"""

from __future__ import annotations

import asyncio
import io
import os
import time
from pathlib import Path

os.environ.setdefault("SUPABASE_URL", "https://test-project.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("CONTACT_EMAIL", "tests@example.com")

import pytest  # noqa: E402  (must follow the environment pinning above)
from rich.console import Console  # noqa: E402

from lib import db  # noqa: E402
from lib.config import Settings  # noqa: E402
from lib.nodes import NODE_REGISTRY  # noqa: E402
from lib.runner import run_nodes  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    """Read a saved HTML fixture."""
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    """The parts of an httpx.Response the pipeline actually uses."""

    def __init__(self, text: str = "", status_code: int = 200, url: str = "https://example.com/"):
        self.text = text
        self.status_code = status_code
        self.url = url


class FakeClient:
    """An httpx.AsyncClient stand-in driven by a handler function.

    Records the URL and the wall-clock window of every request, which is how the
    politeness tests prove that same-host requests do not overlap.
    """

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[str] = []
        self.windows: list[tuple[str, float, float]] = []

    async def get(self, url, **kwargs):
        started = time.monotonic()
        self.calls.append(url)
        result = self.handler(url)
        if asyncio.iscoroutine(result):
            result = await result
        self.windows.append((url, started, time.monotonic()))
        return result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def no_robots(url: str) -> FakeResponse:
    """Handler helper: every robots.txt is missing, so nothing is disallowed."""
    return FakeResponse("", status_code=404, url=url)


@pytest.fixture
def settings_fast() -> Settings:
    """Settings with a short delay, so politeness tests finish quickly."""
    return Settings(
        supabase_url="https://test-project.supabase.co",
        supabase_service_role_key="test-key",
        user_agent="ConexusPipelineTests/0.1 (contact tests@example.com)",
        request_timeout_seconds=5,
        fetch_delay_seconds=0.2,
    )


@pytest.fixture
def settings_nodelay(settings_fast: Settings) -> Settings:
    """Settings with no politeness delay, for tests about other behaviour."""
    return settings_fast.model_copy(update={"fetch_delay_seconds": 0.0})


# --- Shared runner scaffolding -------------------------------------------------
#
# FakeDB and its fixtures live here rather than in a test module so that both
# test_runner.py and test_node_graph.py can use them without importing each
# other's fixtures, which pytest treats as a redefinition.

QUIET = Console(file=io.StringIO())


class FakeDB:
    """An in-memory stand-in for the prospects and work_items tables."""

    def __init__(self) -> None:
        self.prospects: dict[str, dict] = {}
        self.items: list[dict] = []
        self._next = 0

    def add_prospect(self, prospect_id: str, **columns) -> dict:
        row = {"id": prospect_id, "company_name": prospect_id, "stage": "extracted", **columns}
        self.prospects[prospect_id] = row
        return row

    def add_item(
        self, prospect_id: str, node_name: str, status="pending", attempts=0, skip_kind=None
    ) -> dict:
        self._next += 1
        item = {
            "id": f"item-{self._next}",
            "prospect_id": prospect_id,
            "node_name": node_name,
            "status": status,
            "attempts": attempts,
            "skip_kind": skip_kind,
            "created_at": f"2026-08-08T00:00:{self._next:02d}Z",
        }
        self.items.append(item)
        return item

    def item_for(self, prospect_id: str, node_name: str) -> dict:
        return next(
            i for i in self.items if i["prospect_id"] == prospect_id and i["node_name"] == node_name
        )

    # --- the lib.db surface the runner uses ---

    def list_work_items(self, node_name, statuses, limit=None):
        rows = [
            dict(i) for i in self.items if i["node_name"] == node_name and i["status"] in statuses
        ]
        rows.sort(key=lambda r: r["created_at"])
        return rows[:limit] if limit is not None else rows

    def list_work_items_for_prospects(self, prospect_ids, node_names):
        # Mirrors the real select, attempts included — the dependency rule needs
        # it to tell a retryable failure from an exhausted one.
        return [
            {
                "prospect_id": i["prospect_id"],
                "node_name": i["node_name"],
                "status": i["status"],
                "attempts": i["attempts"],
            }
            for i in self.items
            if i["prospect_id"] in prospect_ids and i["node_name"] in node_names
        ]

    def update_work_item(self, item_id, data):
        item = next(i for i in self.items if i["id"] == item_id)
        item.update(data)
        return item

    def get_prospects_by_ids(self, prospect_ids):
        return [dict(self.prospects[i]) for i in prospect_ids if i in self.prospects]

    def update_prospect(self, prospect_id, data):
        self.prospects[prospect_id].update(data)
        return self.prospects[prospect_id]


@pytest.fixture
def fake_db(monkeypatch) -> FakeDB:
    """Point every lib.db call the runner makes at an in-memory fake."""
    fake = FakeDB()
    for name in (
        "list_work_items",
        "list_work_items_for_prospects",
        "update_work_item",
        "get_prospects_by_ids",
        "update_prospect",
    ):
        monkeypatch.setattr(db, name, getattr(fake, name))
    return fake


@pytest.fixture
def registry():
    """Give a test an empty node registry, restoring the real one afterwards."""
    saved = dict(NODE_REGISTRY)
    NODE_REGISTRY.clear()
    yield NODE_REGISTRY
    NODE_REGISTRY.clear()
    NODE_REGISTRY.update(saved)


def run_quiet(names, **kwargs):
    """Execute run_nodes with no console output and return the summary."""
    return asyncio.run(run_nodes(names, console=QUIET, **kwargs))
