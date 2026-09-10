"""Charts, drawn only from an evaluated model.

WHY EVERY FUNCTION HERE TAKES A ModelReport

A chart is the most persuasive thing in a document and the least examined. A
reader who would question a sentence will accept a line, because a line looks
like a measurement. So the only thing this module will draw is the output of a
`lib.finmodel` spec that has already been evaluated — which means every point on
every line traces back through the model to a claim, a benchmark or a labelled
assumption, and a chart that cannot do that does not get drawn. Asking for one
the report does not support raises rather than returning an empty axis.

WHAT THE CHARTS SHOW, AND WHY THOSE TWO

**Payback**, month by month, all three scenarios, each as a band rather than a
line. The band is the point: a single payback line is a forecast, and three
bands is an argument about which inputs matter. The zero crossing is marked
where it happens and is deliberately absent where it does not, because a
scenario that never pays back inside the horizon should look like one.

**Cost trajectory**, the escalating cost against the flat alternative. The
saving a proposal quotes is always measured against today's cost, and today's
cost is the one number certain to be wrong by the end of the engagement. Drawing
the two lines together is the honest way to say so, and the shaded area between
them is what the analysis is actually claiming.

THEY MUST READ IN GREY

Printed, photocopied, and read on a phone in a car park. So: distinguishable
line styles as well as colours, direct labels rather than a legend where it
fits, no fill that hides a line underneath, and font sizes that survive being
scaled into a page.
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402  (backend must be set first)
from matplotlib.ticker import FuncFormatter  # noqa: E402

from lib import finmodel  # noqa: E402

FIGURE_SIZE = (7.2, 3.6)
DPI = 160

SCENARIO_STYLE: dict[str, tuple[str, str, float]] = {
    finmodel.CONSERVATIVE: ("#8c8c8c", ":", 1.6),
    finmodel.TARGET: ("#1f4e79", "-", 2.2),
    finmodel.AGGRESSIVE: ("#2e7d32", "--", 1.6),
}
"""Colour, dash and width per scenario.

The dash pattern carries the meaning on its own, so the chart survives being
printed in grey — which is how a document taken to a meeting is usually read."""

GRID = {"color": "#d9d9d9", "linewidth": 0.6}
ZERO_LINE = {"color": "#b00020", "linewidth": 1.0, "linestyle": "-"}


class NoModelBehindIt(RuntimeError):
    """A chart was asked for that no evaluated model supports."""


def _money(value: float, _pos: int = 0) -> str:
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.1f}m"
    if abs(value) >= 1_000:
        return f"${value / 1_000:,.0f}k"
    return f"${value:,.0f}"


TITLE_LIMIT = 78
"""Characters a chart title gets before it is trimmed.

A title that runs off the right edge is worse than a shorter one, and a model
title carries the whole description of the build."""


def _trim(title: str) -> str:
    text = " ".join((title or "").split())
    return text if len(text) <= TITLE_LIMIT else text[:TITLE_LIMIT - 1].rstrip(" ,;-") + "\u2026"


def _frame(title: str, ylabel: str):
    figure, axes = plt.subplots(figsize=FIGURE_SIZE, dpi=DPI)
    axes.set_title(_trim(title), fontsize=11, loc="left", pad=10)
    axes.set_xlabel("month", fontsize=9)
    axes.set_ylabel(ylabel, fontsize=9)
    axes.grid(True, **GRID)
    axes.set_axisbelow(True)
    for spine in ("top", "right"):
        axes.spines[spine].set_visible(False)
    axes.tick_params(labelsize=8)
    axes.yaxis.set_major_formatter(FuncFormatter(_money))
    return figure, axes


def _render(figure) -> bytes:
    buffer = io.BytesIO()
    figure.tight_layout()
    figure.savefig(buffer, format="png", dpi=DPI)
    plt.close(figure)
    return buffer.getvalue()


def payback_chart(report: finmodel.ModelReport, title: str = "") -> bytes:
    """Cumulative net position by month, one band per scenario.

    Bands rather than lines because the model's inputs are ranges, and a line
    down the middle of a range is the point estimate this whole engine exists to
    avoid drawing.
    """
    if not report.payback:
        raise NoModelBehindIt(
            f"{report.spec.model_id} has no payback projection, so there is no "
            f"payback chart to draw"
        )
    figure, axes = _frame(
        title or f"What it costs and what comes back — {report.spec.title}",
        "cumulative position",
    )
    for name in finmodel.SCENARIOS:
        payback = report.payback.get(name)
        if payback is None:
            continue
        colour, dash, width = SCENARIO_STYLE[name]
        months = payback.cumulative.months
        axes.fill_between(months, payback.cumulative.low, payback.cumulative.high,
                          color=colour, alpha=0.12, linewidth=0)
        axes.plot(months, payback.cumulative.high, color=colour, linestyle=dash,
                  linewidth=width, label=f"{name} (best reading)")
        axes.plot(months, payback.cumulative.low, color=colour, linestyle=dash,
                  linewidth=width * 0.6, alpha=0.75)
        if payback.fastest_month is not None:
            axes.plot([payback.fastest_month], [0], marker="o", markersize=5,
                      color=colour, zorder=5)
    axes.axhline(0, **ZERO_LINE)
    axes.legend(fontsize=8, frameon=False, loc="upper left")
    return _render(figure)


def trajectory_chart(report: finmodel.ModelReport,
                     scenario: str = finmodel.TARGET,
                     title: str = "") -> bytes:
    """The escalating cost of doing it by hand against the flat alternative."""
    trajectory = report.trajectory.get(scenario)
    if trajectory is None:
        raise NoModelBehindIt(
            f"{report.spec.model_id} has no cost trajectory for {scenario!r}; it "
            f"declares no escalating cost and escalation rate"
        )
    figure, axes = _frame(
        title or f"Doing it by hand, month by month — {report.spec.title}",
        "monthly cost",
    )
    months = trajectory.escalating.months
    axes.fill_between(months, trajectory.flat.high, trajectory.escalating.high,
                      color="#b00020", alpha=0.10, linewidth=0,
                      label="what escalation adds")
    axes.plot(months, trajectory.escalating.high, color="#b00020", linewidth=2.0,
              label="cost as wages escalate")
    axes.plot(months, trajectory.flat.high, color="#1f4e79", linewidth=1.6,
              linestyle="--", label="cost held flat at today's rate")
    axes.plot(months, trajectory.escalating.low, color="#b00020", linewidth=1.0,
              alpha=0.6)
    axes.plot(months, trajectory.flat.low, color="#1f4e79", linewidth=0.9,
              linestyle="--", alpha=0.6)
    axes.legend(fontsize=8, frameon=False, loc="upper left")
    return _render(figure)


def capacity_chart(report: finmodel.ModelReport,
                   scenario: str = finmodel.TARGET,
                   title: str = "") -> bytes:
    """Growing demand against a manual rate that is not growing."""
    collapse = report.collapse.get(scenario)
    if collapse is None:
        raise NoModelBehindIt(
            f"{report.spec.model_id} has no capacity projection for {scenario!r}"
        )
    unit = collapse.demand.unit or "units"
    figure, axes = _frame(
        title or f"Demand against the manual rate — {report.spec.title}",
        f"{unit} a month",
    )
    axes.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:,.0f}"))
    months = collapse.demand.months
    axes.fill_between(months, collapse.demand.low, collapse.demand.high,
                      color="#1f4e79", alpha=0.12, linewidth=0)
    axes.plot(months, collapse.demand.high, color="#1f4e79", linewidth=2.0,
              label="demand, busier reading")
    axes.plot(months, collapse.demand.low, color="#1f4e79", linewidth=1.0,
              linestyle=":", label="demand, quieter reading")
    axes.axhline(collapse.capacity.high, color="#b00020", linewidth=1.4,
                 linestyle="--", label="what the manual process can take")
    if collapse.crossing_month_earliest is not None:
        axes.axvline(collapse.crossing_month_earliest, color="#b00020",
                     linewidth=0.8, alpha=0.6)
    axes.legend(fontsize=8, frameon=False, loc="upper left")
    return _render(figure)


def caption_for(report: finmodel.ModelReport, kind: str) -> str:
    """The line printed under a chart, naming the model and what is behind it.

    A chart without this is a picture. With it, a reader can go and find every
    input, which is the only reason to put one in a document we sign.
    """
    claims = sorted(report.claim_paths())
    marks = sorted(report.benchmark_ids())
    assumptions = report.assumptions()
    parts = [
        f"Drawn from model {report.spec.model_id}, evaluated across "
        f"{len(report.scenarios)} scenarios."
    ]
    if claims:
        parts.append(f"Rests on their own record at {', '.join(claims)}.")
    if marks:
        parts.append(f"Benchmarks: {', '.join(marks)}.")
    if assumptions:
        parts.append(
            f"{len(assumptions)} labelled assumption"
            f"{'' if len(assumptions) == 1 else 's'}, listed with the model — "
            f"every one of them is a question for the call."
        )
    parts.append(f"({kind})")
    return " ".join(parts)


CHART_KINDS: tuple[tuple[str, str], ...] = (
    ("payback", "month-by-month payback, three scenarios"),
    ("trajectory", "cost of doing it by hand as wages escalate"),
    ("capacity", "demand against the manual processing rate"),
)


def charts_for(report: finmodel.ModelReport) -> dict[str, bytes]:
    """Every chart this model actually supports, and no placeholders.

    A model without an escalation rate gets no trajectory chart. That is not a
    gap to fill with an empty axis — it is the document declining to draw
    something it cannot source.
    """
    out: dict[str, bytes] = {}
    for kind, _words in CHART_KINDS:
        try:
            if kind == "payback":
                out[kind] = payback_chart(report)
            elif kind == "trajectory":
                out[kind] = trajectory_chart(report)
            else:
                out[kind] = capacity_chart(report)
        except NoModelBehindIt:
            continue
    return out
