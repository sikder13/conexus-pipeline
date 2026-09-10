"""Macroeconomic series as claims — the only source for a headwind paragraph.

WHY THIS MODULE EXISTS

An analysis that says "labour costs are rising" has said nothing, and an analysis
that says "labour costs are rising about four percent a year" has said something
it cannot support. Both are the same failure: a statement about the wider economy
with no source, in a document whose entire argument is that every figure has one.

The fix is the fix used everywhere else here. A macro statement is a claim, built
by `lib/claims.py`, tiered T1 because it comes from a government statistical
agency, carrying the series id, the observation date, and a URL a reader can open
and see the same number on. The headwind paragraph in an analysis draws from
these and from nothing else — there is no path by which a general impression
about the economy reaches the page.

NOT CONFIGURED IS A REPORT, NOT A FAILURE

The keys are optional and most runs will not have them. A missing key produces a
section that says it is not configured, names the variable, and stops. It does
not raise, it does not retry, and it does not quietly substitute a guess. A run
that cannot reach FRED is a run with a shorter analysis, which is the correct
outcome and not an incident.

The same is true of a series that errors, 404s or comes back empty: it is
recorded as unavailable with the reason attached, beside the ones that worked.
Silently dropping it would leave the reader unable to tell a series we could not
get from a series we did not ask for.

THE BLS FALLBACK, STATED RATHER THAN HIDDEN

FRED needs a key and says so. The Bureau of Labor Statistics publishes an
unregistered public API alongside its registered one, capped at 25 queries a day,
so the BLS series work without a key and the status report says which of the two
is in use and what the cap is. A capability that quietly degrades is worse than
one that is absent, so it is named.

CACHED PER SERIES PER WEEK

These series are published monthly or quarterly. Fetching one twice in a week is
a request somebody else's server did not need to serve, and DATA-1 rule 8 makes
that our problem rather than theirs. The cache key is the series id and the ISO
week, and a cached read is reported as cached so an operator can tell how fresh
the paragraph is.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from lib.claims import Tier, make_claim
from lib.config import settings

FRED = "fred"
BLS = "bls"

CACHE_DIR = Path("data/cache/macro")
"""Where a week's answer for one series lives. Gitignored; it is somebody's data,
not ours, and re-fetchable."""

FRED_OBSERVATIONS = "https://api.stlouisfed.org/fred/series/observations"
FRED_PAGE = "https://fred.stlouisfed.org/series/{code}"
BLS_V1 = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
BLS_V2 = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_PAGE = "https://data.bls.gov/timeseries/{code}"

BLS_UNREGISTERED_DAILY_CAP = 25

HEADWIND_MONTHS = 36
"""The window a headwind paragraph covers.

Three years is long enough that a trend is not one quarter's noise and short
enough that the prospect was running the same business throughout it."""


class SeriesDef(BaseModel):
    """One published series, and how to read it."""

    model_config = ConfigDict(frozen=True)

    series_id: str
    """Our stable id. Analyses cite this, so it does not change."""

    provider: Literal["fred", "bls"]
    code: str
    """The provider's own identifier."""

    title: str
    unit: str
    geography: str
    reading: Literal["index", "level", "rate"]
    """How a number in this series is to be read.

    `index` — a level with no natural unit; only its change means anything.
    `level` — a count, in the stated unit.
    `rate`  — already a percentage change, so a change-over-window is nonsense
              and the latest observation is the statement."""

    means: str
    """What a rise in this series does to a manufacturer, in one clause."""

    @property
    def page_url(self) -> str:
        """The page a human opens to check the figure — never the API endpoint.

        An API URL with a key in it is not a citation. It is not openable by the
        reader, and pasting one into a document would publish a credential."""
        template = FRED_PAGE if self.provider == FRED else BLS_PAGE
        return template.format(code=self.code)


NATIONAL: tuple[SeriesDef, ...] = (
    SeriesDef(
        series_id="eci.wages.private",
        provider=FRED, code="ECIWAG",
        title="Employment Cost Index: Wages and Salaries, Private Industry Workers",
        unit="index", geography="United States", reading="index",
        means="what an hour of the same work costs an employer, before benefits",
    ),
    SeriesDef(
        series_id="eci.compensation.civilian",
        provider=FRED, code="ECIALLCIV",
        title="Employment Cost Index: Total Compensation, All Civilian Workers",
        unit="index", geography="United States", reading="index",
        means="the same, with benefits counted",
    ),
    SeriesDef(
        series_id="ppi.fabricated_metal",
        provider=FRED, code="PCU332332",
        title="Producer Price Index by Industry: Fabricated Metal Product Manufacturing",
        unit="index", geography="United States", reading="index",
        means="what fabricators are able to charge for their output",
    ),
    SeriesDef(
        series_id="ppi.metals_commodity",
        provider=FRED, code="WPU10",
        title="Producer Price Index by Commodity: Metals and Metal Products",
        unit="index", geography="United States", reading="index",
        means="what the metal going into the job costs",
    ),
    SeriesDef(
        series_id="ppi.total_manufacturing",
        provider=FRED, code="PCUOMFGOMFG",
        title="Producer Price Index by Industry: Total Manufacturing Industries",
        unit="index", geography="United States", reading="index",
        means="output prices across manufacturing as a whole",
    ),
    SeriesDef(
        series_id="employment.manufacturing.national",
        provider=FRED, code="MANEMP",
        title="All Employees, Manufacturing",
        unit="thousands of persons", geography="United States", reading="level",
        means="how many people are available to do this work at all",
    ),
    SeriesDef(
        series_id="production.manufacturing",
        provider=FRED, code="IPMAN",
        title="Industrial Production: Manufacturing (NAICS)",
        unit="index", geography="United States", reading="index",
        means="how much manufacturing is actually happening",
    ),
    SeriesDef(
        series_id="bls.employment.manufacturing",
        provider=BLS, code="CES3000000001",
        title="All Employees, Manufacturing (Current Employment Statistics)",
        unit="thousands of persons", geography="United States", reading="level",
        means="the same count, read straight from the agency that publishes it",
    ),
    SeriesDef(
        series_id="bls.eci.private.twelve_month",
        provider=BLS, code="CIU2010000000000A",
        title=(
            "Employment Cost Index: Total Compensation, Private Industry, "
            "12-month percent change"
        ),
        unit="percent", geography="United States", reading="rate",
        means="the rate wage bills are climbing at, as published",
    ),
)

STATE_SERIES_CODE = "{state}MFG"
"""FRED's naming for state manufacturing employment — INMFG, OHMFG, ILMFG.

Verified against the published series pages for Indiana, Ohio, Illinois,
Michigan, Kentucky, California and Texas on 2026-09-09. A state whose series does
not exist comes back unavailable with the code that was tried, rather than
silently missing."""

NO_PROVINCE_SERIES = (
    "No provincial manufacturing employment series was found in FRED in the "
    "2026-09-09 pass. Canadian provincial employment is published by Statistics "
    "Canada and would need its own adapter; until one exists this reports "
    "unavailable rather than substituting a national figure for a provincial one."
)


def state_series(state_code: str) -> SeriesDef:
    """The manufacturing employment series for a US state, by two-letter code."""
    code = (state_code or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError(f"a state code is two letters, got {state_code!r}")
    return SeriesDef(
        series_id=f"employment.manufacturing.{code.lower()}",
        provider=FRED, code=STATE_SERIES_CODE.format(state=code),
        title=f"All Employees: Manufacturing in {code}",
        unit="thousands of persons", geography=code, reading="level",
        means="how many people do this work in their own labour market",
    )


BY_ID: dict[str, SeriesDef] = {s.series_id: s for s in NATIONAL}


# ------------------------------------------------------------------- status


class MacroStatus(BaseModel):
    """What is reachable this run, in the words the analysis prints."""

    model_config = ConfigDict(frozen=True)

    fred_configured: bool
    bls_mode: Literal["registered", "unregistered", "unavailable"]
    lines: list[str] = Field(default_factory=list)

    @property
    def any_source(self) -> bool:
        return self.fred_configured or self.bls_mode != "unavailable"

    def report(self) -> str:
        """The one line an unconfigured macro section is allowed to print."""
        if not self.any_source:
            return "Macro context: not configured (FRED_API_KEY is unset)."
        return "Macro context: " + "; ".join(self.lines)


def status() -> MacroStatus:
    """What the macro section can do right now. Never raises."""
    fred = bool(settings.fred_api_key)
    bls_mode = "registered" if settings.bls_api_key else "unregistered"
    lines = []
    lines.append(
        "FRED configured" if fred
        else "FRED not configured (FRED_API_KEY is unset), so its series are "
             "unavailable this run"
    )
    lines.append(
        "BLS registered" if settings.bls_api_key
        else f"BLS unregistered, capped at {BLS_UNREGISTERED_DAILY_CAP} queries a day"
    )
    return MacroStatus(fred_configured=fred, bls_mode=bls_mode, lines=lines)


# ---------------------------------------------------------------- fetching


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True)
    observed: str
    """YYYY-MM-DD, the first day of the period the figure describes."""
    value: float


class SeriesResult(BaseModel):
    """One series as we have it, or the reason we do not."""

    model_config = ConfigDict(frozen=True)

    definition: SeriesDef
    observations: list[Observation] = Field(default_factory=list)
    retrieved: str = ""
    cached: bool = False
    error: str = ""

    @property
    def available(self) -> bool:
        return not self.error and len(self.observations) >= 1

    @property
    def latest(self) -> Observation | None:
        return self.observations[-1] if self.observations else None

    def observation_on_or_before(self, cutoff: str) -> Observation | None:
        earlier = [o for o in self.observations if o.observed <= cutoff]
        return earlier[-1] if earlier else None


def _cache_path(series_id: str, when: date) -> Path:
    year, week, _day = when.isocalendar()
    return CACHE_DIR / f"{series_id}.{year}-W{week:02d}.json"


def _read_cache(definition: SeriesDef, when: date) -> SeriesResult | None:
    path = _cache_path(definition.series_id, when)
    if not path.exists():
        return None
    try:
        stored = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return SeriesResult(
        definition=definition,
        observations=[Observation(**o) for o in stored.get("observations", [])],
        retrieved=stored.get("retrieved", ""),
        cached=True,
    )


def _write_cache(result: SeriesResult, when: date) -> None:
    if not result.available:
        return
    path = _cache_path(result.definition.series_id, when)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "series_id": result.definition.series_id,
            "code": result.definition.code,
            "retrieved": result.retrieved,
            "observations": [o.model_dump() for o in result.observations],
        }, indent=1))
    except OSError:
        # A cache that cannot be written is a slower run, not a failed one.
        pass


def _fred_observations(definition: SeriesDef, client: httpx.Client) -> list[Observation]:
    response = client.get(FRED_OBSERVATIONS, params={
        "series_id": definition.code,
        "api_key": settings.fred_api_key,
        "file_type": "json",
        "observation_start": "2015-01-01",
    })
    response.raise_for_status()
    out = []
    for row in response.json().get("observations", []):
        raw = str(row.get("value", "."))
        if raw in (".", ""):
            continue
        out.append(Observation(observed=str(row.get("date")), value=float(raw)))
    return out


BLS_PERIOD_MONTH = {f"M{m:02d}": m for m in range(1, 13)}
BLS_PERIOD_QUARTER = {"Q01": 1, "Q02": 4, "Q03": 7, "Q04": 10}


def _bls_observations(definition: SeriesDef, client: httpx.Client) -> list[Observation]:
    this_year = datetime.now(UTC).year
    payload: dict[str, Any] = {
        "seriesid": [definition.code],
        "startyear": str(this_year - 6),
        "endyear": str(this_year),
    }
    url = BLS_V1
    if settings.bls_api_key:
        payload["registrationkey"] = settings.bls_api_key
        url = BLS_V2
    response = client.post(url, json=payload)
    response.raise_for_status()
    body = response.json()
    if body.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(
            f"BLS refused the request: {'; '.join(body.get('message') or []) or body.get('status')}"
        )
    out = []
    for series in body.get("Results", {}).get("series", []):
        for row in series.get("data", []):
            month = _bls_month(str(row.get("period", "")))
            if month is None:
                continue
            try:
                value = float(str(row.get("value")).replace(",", ""))
            except ValueError:
                continue
            out.append(Observation(
                observed=f"{int(row['year']):04d}-{month:02d}-01", value=value))
    return sorted(out, key=lambda o: o.observed)


def _bls_month(period: str) -> int | None:
    if period in BLS_PERIOD_MONTH:
        return BLS_PERIOD_MONTH[period]
    return BLS_PERIOD_QUARTER.get(period)


Fetcher = Callable[[SeriesDef], list[Observation]]


def fetch(
    definitions: list[SeriesDef],
    fetcher: Fetcher | None = None,
    when: date | None = None,
    use_cache: bool = True,
) -> list[SeriesResult]:
    """Every series asked for, each either with its data or with its reason.

    Never raises. A key that is not set, a series that does not exist, a provider
    that is down: all of them come back as an unavailable result carrying the
    reason, because a macro paragraph that is missing a line should say which
    line it is missing.
    """
    today = when or date.today()
    results: list[SeriesResult] = []
    client: httpx.Client | None = None
    try:
        for definition in definitions:
            cached = _read_cache(definition, today) if use_cache else None
            if cached is not None:
                results.append(cached)
                continue

            reason = _unreachable(definition)
            if reason:
                results.append(SeriesResult(definition=definition, error=reason))
                continue

            if fetcher is None and client is None:
                client = httpx.Client(
                    timeout=settings.request_timeout_seconds,
                    headers={"User-Agent": settings.user_agent},
                    follow_redirects=True,
                )
            try:
                if fetcher is not None:
                    observations = fetcher(definition)
                else:
                    reader = (
                        _fred_observations if definition.provider == FRED
                        else _bls_observations
                    )
                    observations = reader(definition, client)
                    # Rule 8: rate-limit everything we send outward, including
                    # to an agency that would not notice.
                    time.sleep(settings.fetch_delay_seconds)
                result = SeriesResult(
                    definition=definition,
                    observations=observations,
                    retrieved=today.isoformat(),
                )
                if not observations:
                    result = SeriesResult(
                        definition=definition,
                        error="the provider returned no usable observations",
                    )
                results.append(result)
                if use_cache:
                    _write_cache(result, today)
            except Exception as exc:
                results.append(SeriesResult(
                    definition=definition,
                    error=f"{type(exc).__name__}: {exc}"[:200],
                ))
    finally:
        if client is not None:
            client.close()
    return results


def _unreachable(definition: SeriesDef) -> str:
    if definition.provider == FRED and not settings.fred_api_key:
        return "FRED_API_KEY is not set, so this series is not configured"
    return ""


# ------------------------------------------------------------------- claims


def as_claim(result: SeriesResult, observation: Observation | None = None) -> dict[str, Any]:
    """One observation as a T1 claim, carrying the series id and its date.

    T1 because it is a government statistical record, which is the same footing
    as a grant award. The value is written as a sentence rather than a bare
    number so that a reader who opens the claim knows what they are looking at
    without having to hold the series definition in their head.
    """
    chosen = observation or result.latest
    if chosen is None:
        raise ValueError(
            f"{result.definition.series_id} has no observation to build a claim from"
        )
    definition = result.definition
    return make_claim(
        value=(
            f"{definition.title} ({definition.provider.upper()} series "
            f"{definition.code}) stood at {_figure(chosen.value)} {definition.unit} "
            f"for the period beginning {chosen.observed}"
        ),
        tier=Tier.T1,
        source_url=definition.page_url,
        date_checked=date.fromisoformat(result.retrieved) if result.retrieved else None,
    )


def as_claims(results: list[SeriesResult]) -> dict[str, dict[str, Any]]:
    """A claim per available series, keyed by series id."""
    return {
        result.definition.series_id: as_claim(result)
        for result in results if result.available
    }


# ----------------------------------------------------------------- headwind


class HeadwindLine(BaseModel):
    """One sentence about the wider economy, and the series that produced it."""

    model_config = ConfigDict(frozen=True)

    series_id: str
    sentence: str
    change_percent: float | None
    from_observed: str
    to_observed: str
    source_url: str


class Headwind(BaseModel):
    """The macro paragraph, or the reason there is not one."""

    model_config = ConfigDict(frozen=True)

    window_months: int
    lines: list[HeadwindLine] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)
    status_line: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.lines)

    def paragraph(self) -> str:
        """The prose, drawn from these series and from nothing else."""
        if not self.lines:
            return self.status_line or "Macro context: not configured."
        return " ".join(line.sentence for line in self.lines)

    def figures(self) -> set[float]:
        """Every number this paragraph is allowed to contain."""
        out: set[float] = {float(self.window_months)}
        for line in self.lines:
            if line.change_percent is not None:
                out.add(round(line.change_percent, 1))
                out.add(line.change_percent)
        return out


def headwind(
    results: list[SeriesResult],
    months: int = HEADWIND_MONTHS,
    today: date | None = None,
) -> Headwind:
    """Turn the series into the sentences an analysis may print, and no others.

    A `rate` series states its latest reading. Everything else states the change
    across the window, with both observation dates in the sentence, because a
    percentage without its endpoints is not checkable.
    """
    now = today or date.today()
    cutoff = _months_before(now, months)

    lines: list[HeadwindLine] = []
    unavailable: list[str] = []
    for result in results:
        definition = result.definition
        if not result.available:
            unavailable.append(f"{definition.series_id}: {result.error}")
            continue
        latest = result.latest
        if latest is None:
            unavailable.append(f"{definition.series_id}: no observations")
            continue

        if definition.reading == "rate":
            lines.append(HeadwindLine(
                series_id=definition.series_id,
                sentence=(
                    f"{definition.title} was running at {_figure(latest.value)} percent "
                    f"as of {latest.observed}, which is {definition.means}."
                ),
                change_percent=latest.value,
                from_observed=latest.observed,
                to_observed=latest.observed,
                source_url=definition.page_url,
            ))
            continue

        earlier = result.observation_on_or_before(cutoff)
        if earlier is None or earlier.value == 0:
            unavailable.append(
                f"{definition.series_id}: no observation on or before {cutoff}, "
                f"so a {months}-month change cannot be computed"
            )
            continue
        change = (latest.value - earlier.value) / earlier.value * 100
        direction = "rose" if change >= 0 else "fell"
        lines.append(HeadwindLine(
            series_id=definition.series_id,
            sentence=(
                f"{definition.title} {direction} {abs(change):,.1f} percent between "
                f"{earlier.observed} and {latest.observed} — {definition.means}."
            ),
            change_percent=change,
            from_observed=earlier.observed,
            to_observed=latest.observed,
            source_url=definition.page_url,
        ))

    return Headwind(
        window_months=months,
        lines=lines,
        unavailable=unavailable,
        status_line=status().report(),
    )


def _figure(value: float) -> str:
    """A published figure, written the way the agency writes it.

    Not ``:,.4g``: an employment level of 12,638 thousand came out as 1.264e+04,
    which is a correct number in a form nobody would recognise as the one on the
    page they were told to open.
    """
    return f"{value:,.10g}"


def _months_before(when: date, months: int) -> str:
    total = (when.year * 12 + when.month - 1) - months
    return date(total // 12, total % 12 + 1, 1).isoformat()
