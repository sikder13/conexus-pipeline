"""Single source of environment configuration for every pipeline tool.

This module exists so that no other module ever reads ``os.environ``. Secrets
and tunables are resolved once, validated once, and exposed as a frozen
``Settings`` object. Freezing matters: the outbound User-Agent and the fetch
delay are compliance controls (DATA-1 rule 8), not preferences, so no tool
should be able to mutate them at runtime.

Missing required variables raise ``ConfigError`` at import time, naming the
variable. Failing at import is deliberate — a tool that starts without
credentials and discovers it mid-crawl has already wasted the crawl.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

load_dotenv()


class ConfigError(RuntimeError):
    """A required environment variable is missing or unusable."""


class Settings(BaseModel):
    """Resolved, immutable configuration for the pipeline."""

    model_config = ConfigDict(frozen=True)

    supabase_url: str = Field(description="Supabase project API URL.")
    supabase_service_role_key: str = Field(
        description="Service role key. Server/CLI-side only; the schema has RLS on "
        "with no policies, so nothing else can read these tables."
    )
    anthropic_api_key: str | None = Field(
        default=None, description="Optional until the Drafter tool lands."
    )
    crawlmouse_api_key: str | None = Field(
        default=None, description="Optional; Crawlmouse audits are run manually."
    )
    user_agent: str = Field(
        description="Sent on every outbound fetch. Identifies the client honestly "
        "and gives site owners a way to reach us."
    )
    request_timeout_seconds: int = Field(default=20, gt=0)
    fetch_delay_seconds: float = Field(default=2.0, ge=0)


def _require(name: str) -> str:
    """Return the environment variable ``name`` or raise a naming ConfigError."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"Required environment variable {name} is missing or empty. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _optional(name: str) -> str | None:
    """Return the environment variable ``name``, or None when unset or blank."""
    value = os.environ.get(name, "").strip()
    return value or None


def _int_env(name: str, default: int) -> int:
    """Return an int environment variable, raising ConfigError if unparseable."""
    raw = _optional(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer, got {raw!r}.") from exc


def _float_env(name: str, default: float) -> float:
    """Return a float environment variable, raising ConfigError if unparseable."""
    raw = _optional(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be a number, got {raw!r}.") from exc


def _build_user_agent() -> str:
    """Compose the outbound User-Agent, or use USER_AGENT if set verbatim.

    CONTACT_EMAIL is required because DATA-1 rule 8 obliges us to identify the
    client honestly and leave a reachable contact on every request we make.
    """
    override = _optional("USER_AGENT")
    if override:
        return override
    contact_email = _require("CONTACT_EMAIL")
    return (
        f"NahlTechnologies-ConexusPipeline/0.1 "
        f"(prospect research; contact {contact_email})"
    )


def load_settings() -> Settings:
    """Read the environment and build a validated Settings object."""
    return Settings(
        supabase_url=_require("SUPABASE_URL"),
        supabase_service_role_key=_require("SUPABASE_SERVICE_ROLE_KEY"),
        anthropic_api_key=_optional("ANTHROPIC_API_KEY"),
        crawlmouse_api_key=_optional("CRAWLMOUSE_API_KEY"),
        user_agent=_build_user_agent(),
        request_timeout_seconds=_int_env("REQUEST_TIMEOUT_SECONDS", 20),
        fetch_delay_seconds=_float_env("FETCH_DELAY_SECONDS", 2.0),
    )


settings = load_settings()

OPTIONAL_KEYS: tuple[str, ...] = ("anthropic_api_key", "crawlmouse_api_key")
"""Fields a tool may legitimately find unset. Used for start-up reporting."""


def missing_optional_keys(current: Settings | None = None) -> list[str]:
    """Return the names of optional settings that are unset.

    Tools report these at start-up so an operator knows up front which
    capabilities are unavailable this run.
    """
    resolved = current if current is not None else settings
    return [name for name in OPTIONAL_KEYS if getattr(resolved, name) is None]
