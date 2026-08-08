"""Tests for the fetch politeness gate in RunContext.

The concurrency model only works if it is concurrent ACROSS hosts and strictly
serial WITHIN one. These tests pin both halves, because getting it backwards
either hammers a small manufacturer's web server or makes a 772-company run
take all day.
"""

from __future__ import annotations

import asyncio

import pytest

from lib.nodes import FetchError, RobotsDisallowed, RunContext
from tests.conftest import FakeClient, FakeResponse


def overlaps(first: tuple[str, float, float], second: tuple[str, float, float]) -> bool:
    """True when two request windows were open at the same time."""
    return first[1] < second[2] and second[1] < first[2]


def page_handler(delay: float = 0.05):
    """A handler where robots.txt is absent and every page takes `delay` to serve."""

    async def handler(url: str):
        if url.endswith("/robots.txt"):
            return FakeResponse("", status_code=404, url=url)
        await asyncio.sleep(delay)
        return FakeResponse("<html>ok</html>", 200, url)

    return handler


def page_windows(client: FakeClient) -> list[tuple[str, float, float]]:
    """Only the real page fetches, not the robots.txt lookups."""
    return [window for window in client.windows if not window[0].endswith("/robots.txt")]


class TestPerDomainSerialisation:
    def test_two_requests_to_the_same_domain_are_serialised(self, settings_nodelay):
        client = FakeClient(page_handler())
        ctx = RunContext(client, settings_nodelay)

        async def scenario():
            await asyncio.gather(
                ctx.fetch("https://example.com/one"),
                ctx.fetch("https://example.com/two"),
            )

        asyncio.run(scenario())
        first, second = page_windows(client)
        assert not overlaps(first, second), "same-host requests overlapped"

    def test_two_requests_to_different_domains_run_concurrently(self, settings_nodelay):
        client = FakeClient(page_handler())
        ctx = RunContext(client, settings_nodelay)

        async def scenario():
            await asyncio.gather(
                ctx.fetch("https://alpha.example/page"),
                ctx.fetch("https://beta.example/page"),
            )

        asyncio.run(scenario())
        first, second = page_windows(client)
        assert overlaps(first, second), "different-host requests were serialised"

    def test_the_delay_is_per_domain_not_global(self, settings_fast):
        # settings_fast carries a 0.2s delay. Two different hosts must still
        # start together: a global delay would stagger them.
        client = FakeClient(page_handler())
        ctx = RunContext(client, settings_fast)

        async def scenario():
            await asyncio.gather(
                ctx.fetch("https://alpha.example/page"),
                ctx.fetch("https://beta.example/page"),
            )

        asyncio.run(scenario())
        first, second = page_windows(client)
        assert overlaps(first, second)

    def test_the_robots_lookup_also_starts_the_politeness_clock(self, settings_fast):
        # robots.txt is a request to their server too, so the page fetch that
        # follows it must still wait out the delay rather than firing instantly.
        def handler(url: str):
            return FakeResponse("User-agent: *\nDisallow:\n", 200, url)

        client = FakeClient(handler)
        ctx = RunContext(client, settings_fast)
        asyncio.run(ctx.fetch("https://example.com/one"))

        robots_window = client.windows[0]
        page_window = client.windows[1]
        assert robots_window[0].endswith("/robots.txt")
        gap = page_window[1] - robots_window[2]
        assert gap >= settings_fast.fetch_delay_seconds * 0.9

    def test_a_second_request_to_one_host_waits_out_the_delay(self, settings_fast):
        client = FakeClient(page_handler(delay=0.01))
        ctx = RunContext(client, settings_fast)

        async def scenario():
            await ctx.fetch("https://example.com/one")
            await ctx.fetch("https://example.com/two")

        asyncio.run(scenario())
        first, second = page_windows(client)
        assert second[1] - first[2] >= settings_fast.fetch_delay_seconds * 0.9


class TestRobots:
    def test_a_disallowed_path_is_refused_without_fetching_it(self, settings_nodelay):
        def handler(url: str):
            if url.endswith("/robots.txt"):
                return FakeResponse("User-agent: *\nDisallow: /private\n", 200, url)
            return FakeResponse("<html>secret</html>", 200, url)

        client = FakeClient(handler)
        ctx = RunContext(client, settings_nodelay)
        with pytest.raises(RobotsDisallowed):
            asyncio.run(ctx.fetch("https://example.com/private/page"))
        assert page_windows(client) == []

    def test_an_allowed_path_is_fetched(self, settings_nodelay):
        def handler(url: str):
            if url.endswith("/robots.txt"):
                return FakeResponse("User-agent: *\nDisallow: /private\n", 200, url)
            return FakeResponse("<html>fine</html>", 200, url)

        ctx = RunContext(FakeClient(handler), settings_nodelay)
        response = asyncio.run(ctx.fetch("https://example.com/public/page"))
        assert response.status_code == 200

    def test_a_declared_crawl_delay_overrides_our_shorter_one(self, settings_nodelay):
        # Conexus declares Crawl-delay: 10 outside any User-agent group. A site
        # asking us to slow down gets to, even when our own default is faster.
        def handler(url: str):
            if url.endswith("/robots.txt"):
                return FakeResponse("Crawl-delay: 0.3\nUser-agent: *\nDisallow:\n", 200, url)
            return FakeResponse("<html>ok</html>", 200, url)

        client = FakeClient(handler)
        ctx = RunContext(client, settings_nodelay)

        async def scenario():
            await ctx.fetch("https://example.com/one")
            await ctx.fetch("https://example.com/two")

        asyncio.run(scenario())
        first, second = page_windows(client)
        assert second[1] - first[2] >= 0.25


class TestRetry:
    def test_transient_errors_are_retried(self, settings_nodelay):
        attempts = {"n": 0}

        def handler(url: str):
            if url.endswith("/robots.txt"):
                return FakeResponse("", 404, url)
            attempts["n"] += 1
            if attempts["n"] < 3:
                return FakeResponse("", 503, url)
            return FakeResponse("<html>ok</html>", 200, url)

        ctx = RunContext(FakeClient(handler), settings_nodelay)
        response = asyncio.run(ctx.fetch("https://example.com/flaky"))
        assert response.status_code == 200
        assert attempts["n"] == 3

    @pytest.mark.parametrize("status", [403, 404])
    def test_definitive_answers_are_not_retried(self, settings_nodelay, status):
        attempts = {"n": 0}

        def handler(url: str):
            if url.endswith("/robots.txt"):
                return FakeResponse("", 404, url)
            attempts["n"] += 1
            return FakeResponse("", status, url)

        ctx = RunContext(FakeClient(handler), settings_nodelay)
        response = asyncio.run(ctx.fetch("https://example.com/gone"))
        assert response.status_code == status
        assert attempts["n"] == 1, "a 403/404 is an answer, not a transient failure"

    def test_persistent_transient_failure_raises(self, settings_nodelay):
        def handler(url: str):
            return FakeResponse("", 404 if url.endswith("/robots.txt") else 500, url)

        ctx = RunContext(FakeClient(handler), settings_nodelay)
        with pytest.raises(FetchError):
            asyncio.run(ctx.fetch("https://example.com/broken"))
