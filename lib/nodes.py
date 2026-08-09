"""The node contract: what a unit of research is, and how it reaches the web.

Research on 772 companies is not one long script. It is many small questions —
what is this company called, where is its website, who runs it — asked
independently per company, retried independently when they fail, and added to
over months. Each question is a NODE.

A node is deliberately powerless. It receives a prospect row and a RunContext,
and it returns a NodeResult describing what it learned. It does not touch the
database, it does not decide its own retries, and it cannot promote a prospect
through the pipeline. The runner does all of that. Nodes stay pure, which means
they are testable without a database and safe to re-run.

RunContext owns the only sanctioned way to reach the public web. Every fetch
goes through one politeness gate: robots.txt is honoured (including any
crawl-delay the site declares, which overrides our own default when it is
stricter), one request at a time per host, our real User-Agent attached, and
transient failures retried with exponential backoff. Nodes never construct
their own HTTP client, so there is no path around that gate.
"""

from __future__ import annotations

import asyncio
import re
import time
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import ClassVar
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, Field

from lib.config import Settings

FORBIDDEN_STAGES: frozenset[str] = frozenset(
    {"verified", "thesis_done", "contact_found", "outreach_active", "replied",
     "meeting", "proposal", "closed_won", "closed_lost"}
)
"""Stages no automated node may write. Only a human in the Verifier promotes
a record past passA_done (DATA-1 rule 7). 'dead' is absent on purpose: the
extractor may mark a company out of scope, which is an exclusion, not progress."""

RETRY_STATUS: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})
"""Transient by nature — worth another attempt. 403 and 404 are answers, not failures."""


class SkipKind(StrEnum):
    """Whether a skip is worth re-attempting.

    PERMANENT means nothing about this prospect will ever make the node
    applicable — case_study skipping a company that has no case-study page.
    Re-attempting it every run is waste, and the runner leaves it alone.

    TRANSIENT means the node could not run *this time*: a missing credential,
    an unreachable host, an upstream field not yet populated. Those must be
    re-attempted, and the runner picks them up on the next ordinary run.

    TRANSIENT is the default, deliberately. A skip wrongly marked transient
    costs one cheap re-check; a skip wrongly marked permanent strands the
    record silently, which is what happened when the two were indistinguishable.
    """

    PERMANENT = "permanent"
    TRANSIENT = "transient"


class NodeResult(BaseModel):
    """What a node learned. The runner decides what to do with it."""

    prospect_patch: dict = Field(default_factory=dict)
    evidence_patch: dict = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    skipped: bool = False
    skip_reason: str | None = None
    skip_kind: SkipKind = SkipKind.TRANSIENT


class StageViolation(RuntimeError):
    """A node tried to promote a prospect past the point a human must approve."""


class RobotsDisallowed(RuntimeError):
    """The site's robots.txt forbids this path. Not retryable, not an error to fix."""


class FetchError(RuntimeError):
    """A fetch failed permanently. Carries the status code when there was one."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def assert_stage_allowed(prospect_patch: dict) -> None:
    """Raise StageViolation if a patch tries to write a human-only stage."""
    stage = prospect_patch.get("stage")
    if stage in FORBIDDEN_STAGES:
        raise StageViolation(
            f"node tried to set stage={stage!r}; only the Verifier, driven by a human, "
            f"promotes a record past 'passA_done' (DATA-1 rule 7)"
        )


class RunContext:
    """Shared per-run state: the HTTP client, settings, and the politeness gate."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._last_fetch: dict[str, float] = {}
        self._robots: dict[str, tuple[RobotFileParser | None, float]] = {}

    def domain_lock(self, host: str) -> asyncio.Lock:
        """One lock per host, so we never open two connections to the same server."""
        return self._domain_locks.setdefault(host, asyncio.Lock())

    async def _robots_for(self, host: str, scheme: str) -> tuple[RobotFileParser | None, float]:
        """Fetch and cache robots.txt for a host, with its declared crawl-delay."""
        if host in self._robots:
            return self._robots[host]
        parser: RobotFileParser | None = None
        delay = 0.0
        try:
            response = await self.client.get(
                f"{scheme}://{host}/robots.txt",
                headers={"User-Agent": self.settings.user_agent},
                timeout=self.settings.request_timeout_seconds,
                follow_redirects=True,
            )
            # robots.txt is a request to their server like any other, so it
            # starts the politeness clock for this host too.
            self._last_fetch[host] = time.monotonic()
            if response.status_code == 200:
                parser = RobotFileParser()
                parser.parse(response.text.splitlines())
                # Some sites (Conexus among them) put Crawl-delay outside any
                # User-agent group, where the stdlib parser drops it. Honour it
                # anyway: a stated delay is a request we should not ignore on a
                # technicality.
                found = re.search(r"(?im)^\s*crawl-delay\s*:\s*([\d.]+)", response.text)
                delay = float(found.group(1)) if found else 0.0
        except httpx.HTTPError:
            # No robots.txt, or it is unreachable. Absence is permission, but we
            # still apply our own delay.
            parser = None
        self._robots[host] = (parser, delay)
        return parser, delay

    async def fetch(self, url: str) -> httpx.Response:
        """Fetch a public URL politely. The only sanctioned way for a node to browse.

        Serialises per host, waits out the larger of our configured delay and the
        site's declared crawl-delay, and retries transient failures with
        exponential backoff. Raises RobotsDisallowed or FetchError otherwise.
        """
        parts = urlparse(url)
        host, scheme = parts.netloc, parts.scheme or "https"
        if not host:
            raise FetchError(f"not a fetchable URL: {url!r}")

        async with self.domain_lock(host):
            parser, crawl_delay = await self._robots_for(host, scheme)
            if parser is not None and not parser.can_fetch(self.settings.user_agent, url):
                raise RobotsDisallowed(f"robots.txt disallows {url}")

            delay = max(self.settings.fetch_delay_seconds, crawl_delay)
            waited = time.monotonic() - self._last_fetch.get(host, 0.0)
            if waited < delay:
                await asyncio.sleep(delay - waited)

            last: Exception | None = None
            for attempt in range(3):
                try:
                    response = await self.client.get(
                        url,
                        headers={"User-Agent": self.settings.user_agent},
                        timeout=self.settings.request_timeout_seconds,
                        follow_redirects=True,
                    )
                    if response.status_code in RETRY_STATUS:
                        last = FetchError(f"HTTP {response.status_code} for {url}",
                                          response.status_code)
                    else:
                        self._last_fetch[host] = time.monotonic()
                        return response
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last = exc
                if attempt < 2:
                    await asyncio.sleep(delay * (2**attempt))
            self._last_fetch[host] = time.monotonic()
            status = getattr(last, "status", None)
            raise FetchError(f"giving up on {url} after 3 attempts: {last}", status)


class Node(ABC):
    """One idempotent unit of research about one prospect."""

    name: ClassVar[str]
    depends_on: ClassVar[tuple[str, ...]] = ()
    max_attempts: ClassVar[int] = 3

    @abstractmethod
    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        """Research this prospect and report what was learned.

        Must be idempotent: running twice produces the same result and never
        duplicates data. Raise to signal failure; the runner records it and
        moves on to the next prospect.
        """


NODE_REGISTRY: dict[str, Node] = {}


def register(node_class: type[Node]) -> type[Node]:
    """Class decorator that instantiates a node and puts it in the registry."""
    if not getattr(node_class, "name", None):
        raise ValueError(f"{node_class.__name__} must define a class-level name")
    if node_class.name in NODE_REGISTRY:
        raise ValueError(f"duplicate node name {node_class.name!r}")
    NODE_REGISTRY[node_class.name] = node_class()
    return node_class
