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
import os
import time
from pathlib import Path

os.environ.setdefault("SUPABASE_URL", "https://test-project.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("CONTACT_EMAIL", "tests@example.com")

import pytest  # noqa: E402  (must follow the environment pinning above)

from lib.config import Settings  # noqa: E402

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
