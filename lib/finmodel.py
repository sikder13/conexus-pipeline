"""The deterministic financial engine — every number in every analysis, computed.

WHY THIS MODULE EXISTS

Until now the arithmetic in an analysis lived in two places that could not check
each other: a few figures computed in `tools/analyst`, and everything else
written by a language model that had been told to be careful. "Told to be
careful" is not a control. A generator having a bad day produces a plausible
number, and the operator repeats it on a phone call, and there is no artifact
anywhere that says where it came from.

So the arithmetic moves here, and the rule becomes structural: **the model does
not compute. It narrates outputs this module produced.** A figure that is not an
output of an evaluated spec, a claim, or a cited benchmark has no business in the
document, and the analyst's validation can now say so mechanically because there
is finally a list to check against.

THE SPEC IS THE LOGIC

A `ModelSpec` is data: named inputs, each carrying its provenance; formulas as an
explicit expression tree; scenarios as sets of input overrides. It serialises to
JSON and back without loss, and evaluating the round-tripped copy gives the same
answer — there is a test that asserts exactly that.

That constraint is not tidiness. The same spec is meant to compile to an
interactive calculator a prospect can open and argue with, and the moment any
logic lives in Python that does not live in the spec, the calculator and the
document disagree about the business. So: no formula in code. The functions here
walk the tree; they do not know what it means.

RANGES, NOT MIDPOINTS

Every value is an interval. A point figure is an interval whose ends agree, which
is a thing you have to say out loud rather than a thing you can slip in. Interval
arithmetic then carries the honesty through the whole calculation for free: a
payback computed from a range of costs and a range of savings comes out as worst
case against best case, because that is what the arithmetic does when you refuse
to collapse the inputs first. `docs/ANALYSIS.md` already required that of the one
payback figure the analyst computed by hand; here it is unavoidable.

PROVENANCE IS A PRECONDITION, NOT A DECORATION

Every input names where it came from: a claim path in the prospect's evidence
file, a benchmark id from `lib/benchmarks.py`, or a labelled assumption of ours.
`evaluate` walks the tree first and **raises** if anything reachable lacks it.
A model with an unsourced input does not return a number that somebody has to
remember to distrust. It does not return a number.

Every output carries the full transitive set of provenances behind it, so the
renderer can print, under any figure, the complete list of things a reader would
have to check to disagree with it.

NO I/O, NO MODEL CALLS, NO CLOCK

Nothing in this module reads a file, opens a socket, or asks the time. It is
arithmetic over data it is handed. That is what makes it exhaustively testable,
and being exhaustively testable is the only reason to trust the numbers it
produces more than the ones it replaced.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MONTHS_PER_YEAR = 12

CLAIM = "claim"
ASSUMPTION = "assumption"
BENCHMARK = "benchmark"
DERIVED = "derived"
PROVENANCE_KINDS = (CLAIM, ASSUMPTION, BENCHMARK, DERIVED)

CONSERVATIVE = "conservative"
TARGET = "target"
AGGRESSIVE = "aggressive"
SCENARIOS: tuple[str, ...] = (CONSERVATIVE, TARGET, AGGRESSIVE)
"""The three input sets, in the order they are always reported.

Three, not one, because a single evaluation invites the reader to treat it as a
forecast. Three sets of the same formulas make visible what the document is
actually claiming: that the answer depends on inputs the prospect knows better
than we do."""


class FinModelError(RuntimeError):
    """A spec cannot be evaluated as written."""


class ProvenanceMissing(FinModelError):
    """An input reachable from the formulas does not say where it came from.

    Raised rather than warned. A number whose origin is unknown is the single
    failure mode this whole pipeline is built against, and a caller that gets a
    float back will use it."""


class UnknownReference(FinModelError):
    """A formula refers to an input or formula that the spec does not define."""


class BadFormula(FinModelError):
    """An expression is not well formed — wrong arity, missing operand, cycle."""


# --------------------------------------------------------------- intervals


class Interval(BaseModel):
    """A value, or a range of values, and the only numeric type here.

    Ends are ordered on construction so that arithmetic never has to wonder. A
    point value is ``low == high``; there is no separate type for it, because
    the whole argument of this module is that a point is a special case of a
    range and not the other way round.
    """

    model_config = ConfigDict(frozen=True)

    low: float
    high: float

    @model_validator(mode="after")
    def _ordered(self) -> Interval:
        if self.low > self.high:
            low, high = self.high, self.low
            object.__setattr__(self, "low", low)
            object.__setattr__(self, "high", high)
        return self

    @classmethod
    def of(cls, value: float) -> Interval:
        """A point value."""
        return cls(low=float(value), high=float(value))

    @classmethod
    def span(cls, low: float, high: float) -> Interval:
        """A range."""
        return cls(low=float(low), high=float(high))

    @classmethod
    def read(cls, value: Any) -> Interval:
        """Whatever a caller has — a number, a pair, or an Interval."""
        if isinstance(value, Interval):
            return value
        if isinstance(value, list | tuple):
            if len(value) != 2:
                raise FinModelError(f"a range needs exactly two ends, got {value!r}")
            return cls.span(float(value[0]), float(value[1]))
        return cls.of(float(value))

    @property
    def is_point(self) -> bool:
        return self.low == self.high

    @property
    def mid(self) -> float:
        """The midpoint, for ordering and charting only.

        Never for reporting. Quoting the middle of a range is how a range
        becomes a point estimate with extra steps."""
        return (self.low + self.high) / 2

    def __add__(self, other: Interval) -> Interval:
        return Interval.span(self.low + other.low, self.high + other.high)

    def __sub__(self, other: Interval) -> Interval:
        return Interval.span(self.low - other.high, self.high - other.low)

    def __neg__(self) -> Interval:
        return Interval.span(-self.high, -self.low)

    def __mul__(self, other: Interval) -> Interval:
        # All four corners, because a negative end flips which product is the
        # smallest and a two-corner shortcut is wrong exactly when it matters.
        corners = (self.low * other.low, self.low * other.high,
                   self.high * other.low, self.high * other.high)
        return Interval.span(min(corners), max(corners))

    def __truediv__(self, other: Interval) -> Interval:
        if other.low <= 0 <= other.high:
            raise FinModelError(
                f"dividing by a range that spans zero ({other.low:g} to "
                f"{other.high:g}) has no bounded answer; constrain the input"
            )
        corners = (self.low / other.low, self.low / other.high,
                   self.high / other.low, self.high / other.high)
        return Interval.span(min(corners), max(corners))

    def scaled(self, factor: float) -> Interval:
        return self * Interval.of(factor)

    def as_pair(self) -> tuple[float, float]:
        return (self.low, self.high)


# -------------------------------------------------------------- provenance


class Provenance(BaseModel):
    """Where one input came from, in the terms the reader can act on.

    Four kinds, and the constraints differ because the obligations differ. A
    claim must name the path a human can open. A benchmark must name an id whose
    citation the renderer will print. An assumption must carry a label, because
    an assumption nobody labelled is indistinguishable from a fact.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["claim", "assumption", "benchmark", "derived"]
    ref: str = ""
    """The claim path or benchmark id. Empty for an assumption."""

    label: str = ""
    """How this input is described in prose. Mandatory for an assumption."""

    note: str = ""

    @model_validator(mode="after")
    def _complete(self) -> Provenance:
        if self.kind in (CLAIM, BENCHMARK) and not self.ref.strip():
            raise ProvenanceMissing(
                f"a {self.kind} provenance must name its "
                f"{'claim path' if self.kind == CLAIM else 'benchmark id'}"
            )
        if self.kind == ASSUMPTION and not self.label.strip():
            raise ProvenanceMissing(
                "an assumption must carry a label saying what is being assumed; "
                "an unlabelled assumption reads as a fact"
            )
        return self

    @property
    def is_assumption(self) -> bool:
        return self.kind == ASSUMPTION

    def describe(self) -> str:
        """One phrase naming the source, for printing under a figure.

        A claim carrying a label says what the label says. Not every claim is
        about the prospect: the wage-escalation input is a claim built from a
        published national series, and rendering it as "their own record" told
        a reader — and the generator — that the Employment Cost Index is
        something this company published about itself.
        """
        if self.kind == CLAIM:
            if self.label.strip():
                return f"{self.label} [{self.ref}]"
            return f"their own record [{self.ref}]"
        if self.kind == BENCHMARK:
            return f"benchmark {self.ref}"
        if self.kind == ASSUMPTION:
            return f"assumed: {self.label}"
        return self.label or "computed"


def claim_source(path: str, label: str = "", note: str = "") -> Provenance:
    """Provenance for a figure read out of the prospect's evidence file."""
    return Provenance(kind=CLAIM, ref=path, label=label, note=note)


def assumed(label: str, note: str = "") -> Provenance:
    """Provenance for a figure we are supplying so it can be corrected."""
    return Provenance(kind=ASSUMPTION, label=label, note=note)


def benchmark_source(benchmark_id: str, label: str = "", note: str = "") -> Provenance:
    """Provenance for a figure taken from the sourced benchmark library."""
    return Provenance(kind=BENCHMARK, ref=benchmark_id, label=label, note=note)


# ------------------------------------------------------------------ inputs


class Input(BaseModel):
    """One named quantity a spec computes from."""

    model_config = ConfigDict(frozen=True)

    name: str
    value: Interval
    unit: str = ""
    provenance: Provenance
    description: str = ""
    """What this input is, in words, for the prose that must name it."""

    @property
    def in_words(self) -> str:
        return self.description or self.name.replace("_", " ")


def make_input(
    name: str,
    value: Any,
    provenance: Provenance,
    unit: str = "",
    description: str = "",
) -> Input:
    """Build an input, reading ``value`` as a number, a pair, or an Interval."""
    return Input(
        name=name, value=Interval.read(value), unit=unit,
        provenance=provenance, description=description,
    )


# ------------------------------------------------------------- expressions

OPS: tuple[str, ...] = (
    "ref", "const", "add", "sub", "mul", "div", "neg", "min", "max",
    "compound", "annualise", "monthlyise",
)
"""The complete operator set, and it is deliberately small.

Every operator is total over intervals and has one obvious meaning to somebody
reading the JSON. `compound` is here because ``base x (1 + rate) ** terms`` is
the one piece of finance that cannot be spelled with products and sums, and
leaving it out would push it into Python — which is the one place it may not
live, because the calculator compiled from this JSON would then be computing
something else."""

ARITY: dict[str, tuple[int, int]] = {
    "ref": (0, 0), "const": (0, 0), "neg": (1, 1),
    "add": (2, 99), "mul": (2, 99), "min": (2, 99), "max": (2, 99),
    "sub": (2, 2), "div": (2, 2),
    "compound": (3, 3), "annualise": (1, 1), "monthlyise": (1, 1),
}


class Expr(BaseModel):
    """One node of a formula, as data.

    Nothing here knows what a cost is. The tree is walked by `_evaluate_expr`
    and rendered by the analyst; both read the same structure, which is why a
    figure in the prose and a figure in the chart cannot disagree.
    """

    model_config = ConfigDict(frozen=True)

    op: Literal[
        "ref", "const", "add", "sub", "mul", "div", "neg", "min", "max",
        "compound", "annualise", "monthlyise",
    ]
    ref: str = ""
    """For ``op='ref'``: the name of an input or an earlier formula."""

    value: float | None = None
    """For ``op='const'``: the literal."""

    label: str = ""
    """Required on a const, so a bare number in the JSON says what it is."""

    args: list[Expr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _well_formed(self) -> Expr:
        low, high = ARITY[self.op]
        if not low <= len(self.args) <= high:
            raise BadFormula(
                f"{self.op} takes {low}..{high} operands, got {len(self.args)}"
            )
        if self.op == "ref" and not self.ref.strip():
            raise BadFormula("a ref must name something")
        if self.op == "const":
            if self.value is None:
                raise BadFormula("a const must carry a value")
            if not self.label.strip():
                raise BadFormula(
                    f"the const {self.value:g} must carry a label; an unexplained "
                    f"number in a formula is the thing this engine exists to stop"
                )
        return self


Expr.model_rebuild()


def ref(name: str) -> Expr:
    """Refer to an input or an earlier formula by name."""
    return Expr(op="ref", ref=name)


def const(value: float, label: str) -> Expr:
    """A structural literal — months in a year, working weeks. Must be labelled."""
    return Expr(op="const", value=float(value), label=label)


def add(*args: Expr) -> Expr:
    return Expr(op="add", args=list(args))


def sub(left: Expr, right: Expr) -> Expr:
    return Expr(op="sub", args=[left, right])


def mul(*args: Expr) -> Expr:
    return Expr(op="mul", args=list(args))


def div(numerator: Expr, denominator: Expr) -> Expr:
    return Expr(op="div", args=[numerator, denominator])


def neg(arg: Expr) -> Expr:
    return Expr(op="neg", args=[arg])


def lowest(*args: Expr) -> Expr:
    return Expr(op="min", args=list(args))


def highest(*args: Expr) -> Expr:
    return Expr(op="max", args=list(args))


def compound(base: Expr, rate: Expr, terms: Expr) -> Expr:
    """``base x (1 + rate) ** terms`` — the one node that is not a product."""
    return Expr(op="compound", args=[base, rate, terms])


def annualise(monthly: Expr) -> Expr:
    return Expr(op="annualise", args=[monthly])


def monthlyise(annual: Expr) -> Expr:
    return Expr(op="monthlyise", args=[annual])


# ------------------------------------------------------------------- roles


class Roles(BaseModel):
    """Which named quantity plays which part, so callers need no hardcoded names.

    A projection needs to know which figure is the investment and which is the
    monthly saving. Naming them in the spec rather than in the calling code keeps
    the spec self-describing — and keeps the future JavaScript calculator able to
    build the same charts from the same JSON without being told anything extra.
    """

    model_config = ConfigDict(frozen=True)

    investment: str = ""
    monthly_saving: str = ""
    annual_saving: str = ""
    escalating_cost: str = ""
    escalation_rate: str = ""
    discount_rate: str = ""
    monthly_volume: str = ""
    volume_growth: str = ""
    manual_capacity: str = ""


# -------------------------------------------------------------------- spec


class ModelSpec(BaseModel):
    """A complete, serialisable statement of one piece of arithmetic.

    Round-trips through JSON without loss. That is asserted in the tests rather
    than assumed, because the whole design rests on it: the interactive
    calculator a prospect will open is compiled from this object, and any logic
    that is not in here is logic the two of them will disagree about.
    """

    model_config = ConfigDict(frozen=True)

    model_id: str
    title: str
    inputs: dict[str, Input] = Field(default_factory=dict)
    formulas: dict[str, Expr] = Field(default_factory=dict)
    scenarios: dict[str, dict[str, Interval]] = Field(default_factory=dict)
    """Per scenario, the inputs it overrides. An input not overridden is shared."""

    roles: Roles = Field(default_factory=Roles)
    horizon_months: int = 36
    units: dict[str, str] = Field(default_factory=dict)
    """Unit per formula name. Inputs carry their own."""

    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _names_resolve(self) -> ModelSpec:
        for name, expr in self.formulas.items():
            for referenced in _refs_of(expr):
                if referenced not in self.inputs and referenced not in self.formulas:
                    raise UnknownReference(
                        f"formula {name!r} refers to {referenced!r}, which this "
                        f"spec does not define"
                    )
        for scenario, overrides in self.scenarios.items():
            unknown = sorted(set(overrides) - set(self.inputs))
            if unknown:
                raise UnknownReference(
                    f"scenario {scenario!r} overrides inputs this spec does not "
                    f"define: {', '.join(unknown)}"
                )
        for name in (
            self.roles.investment, self.roles.monthly_saving,
            self.roles.annual_saving, self.roles.escalating_cost,
            self.roles.escalation_rate, self.roles.discount_rate,
            self.roles.monthly_volume, self.roles.volume_growth,
            self.roles.manual_capacity,
        ):
            if name and name not in self.inputs and name not in self.formulas:
                raise UnknownReference(
                    f"a role names {name!r}, which this spec does not define"
                )
        if self.horizon_months < 1:
            raise BadFormula("a horizon of less than one month projects nothing")
        return self

    def unit_of(self, name: str) -> str:
        if name in self.inputs:
            return self.inputs[name].unit
        return self.units.get(name, "")

    def words_for(self, name: str) -> str:
        """How to refer to a quantity in prose."""
        if name in self.inputs:
            return self.inputs[name].in_words
        return name.replace("_", " ")

    def as_json_dict(self) -> dict[str, Any]:
        """The spec as plain JSON-safe data — the form the calculator compiles."""
        return self.model_dump(mode="json")


def _refs_of(expr: Expr) -> set[str]:
    found: set[str] = set()
    if expr.op == "ref":
        found.add(expr.ref)
    for arg in expr.args:
        found |= _refs_of(arg)
    return found


# -------------------------------------------------------------- evaluation


class Evaluated(BaseModel):
    """One scenario's answer, with everything behind every figure."""

    model_config = ConfigDict(frozen=True)

    model_id: str
    title: str
    scenario: str
    values: dict[str, Interval]
    provenance: dict[str, list[Provenance]]
    """Per name, the full transitive set of sources behind it."""

    units: dict[str, str] = Field(default_factory=dict)

    def value(self, name: str) -> Interval:
        if name not in self.values:
            raise UnknownReference(f"{name!r} is not a figure this model produces")
        return self.values[name]

    def sources_for(self, name: str) -> list[Provenance]:
        return self.provenance.get(name, [])

    def assumptions(self) -> list[Provenance]:
        """Every labelled assumption in the model, deduplicated, in order.

        The analyst prints these as the list of things that must be checked on
        the call, which is the half of a feasibility statement that collapses."""
        seen: dict[str, Provenance] = {}
        for sources in self.provenance.values():
            for source in sources:
                if source.is_assumption and source.label not in seen:
                    seen[source.label] = source
        return list(seen.values())

    def figures(self) -> set[float]:
        """Every number this evaluation legitimately produces.

        Handed to the analyst's validation, which refuses any quantity in the
        prose that is not in here, in a claim, or in a cited benchmark."""
        out: set[float] = set()
        for interval in self.values.values():
            out.add(interval.low)
            out.add(interval.high)
        return out


def _sources_of_input(spec: ModelSpec, name: str) -> list[Provenance]:
    return [spec.inputs[name].provenance]


def assert_provenance(spec: ModelSpec) -> None:
    """Raise unless every input reachable from the formulas says where it is from.

    Reachability matters: a spec may carry an input for a scenario that this
    model does not use, and refusing to evaluate over an unused field would be
    pedantry. An input a formula actually reads is a different matter.
    """
    reachable: set[str] = set()
    for expr in spec.formulas.values():
        reachable |= _refs_of(expr)
    for role in spec.roles.model_dump().values():
        if role:
            reachable.add(str(role))

    pending = list(reachable)
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in spec.formulas:
            pending.extend(_refs_of(spec.formulas[name]))

    missing = [
        name for name in sorted(seen)
        if name in spec.inputs and spec.inputs[name].provenance.kind == DERIVED
    ]
    if missing:
        raise ProvenanceMissing(
            f"{spec.model_id}: these inputs are used but say only that they were "
            f"computed, which is not a source: {', '.join(missing)}"
        )


def _evaluate_expr(
    expr: Expr,
    values: dict[str, Interval],
    sources: dict[str, list[Provenance]],
) -> tuple[Interval, list[Provenance]]:
    if expr.op == "const":
        return Interval.of(float(expr.value or 0.0)), []
    if expr.op == "ref":
        if expr.ref not in values:
            raise UnknownReference(f"{expr.ref!r} has no value yet")
        return values[expr.ref], list(sources.get(expr.ref, []))

    parts = [_evaluate_expr(arg, values, sources) for arg in expr.args]
    collected: list[Provenance] = []
    for _value, provenances in parts:
        collected.extend(provenances)
    operands = [value for value, _p in parts]

    if expr.op == "add":
        result = operands[0]
        for operand in operands[1:]:
            result = result + operand
    elif expr.op == "sub":
        result = operands[0] - operands[1]
    elif expr.op == "mul":
        result = operands[0]
        for operand in operands[1:]:
            result = result * operand
    elif expr.op == "div":
        result = operands[0] / operands[1]
    elif expr.op == "neg":
        result = -operands[0]
    elif expr.op == "min":
        result = Interval.span(
            min(o.low for o in operands), min(o.high for o in operands))
    elif expr.op == "max":
        result = Interval.span(
            max(o.low for o in operands), max(o.high for o in operands))
    elif expr.op == "compound":
        result = _compound(*operands)
    elif expr.op == "annualise":
        result = operands[0].scaled(MONTHS_PER_YEAR)
    elif expr.op == "monthlyise":
        result = operands[0].scaled(1 / MONTHS_PER_YEAR)
    else:  # pragma: no cover - the Literal and ARITY tables make this dead
        raise BadFormula(f"unknown operator {expr.op!r}")

    return result, collected


def _compound(base: Interval, rate: Interval, terms: Interval) -> Interval:
    """``base x (1 + rate) ** terms`` over intervals, by corners.

    All eight corners rather than two, because a negative base, a shrinking rate
    and a fractional term each reverse which combination is the smallest, and a
    shortcut is wrong exactly in the cases somebody would want to check.
    """
    if rate.low <= -1:
        raise FinModelError(
            f"a growth rate of {rate.low:g} is a total loss or worse and does not "
            f"compound; constrain the input"
        )
    corners = [
        b * ((1 + r) ** t)
        for b in (base.low, base.high)
        for r in (rate.low, rate.high)
        for t in (terms.low, terms.high)
    ]
    return Interval.span(min(corners), max(corners))


def evaluate(spec: ModelSpec, scenario: str = TARGET) -> Evaluated:
    """Compute every formula in ``spec`` under one scenario's inputs.

    Raises before computing anything if an input reachable from the formulas has
    no source. A number with an unknown origin is worse than no number, because
    a caller who receives a float will use it.
    """
    assert_provenance(spec)
    if scenario not in spec.scenarios and scenario != TARGET:
        raise UnknownReference(
            f"{spec.model_id} has no scenario {scenario!r}; it defines "
            f"{', '.join(sorted(spec.scenarios)) or 'none'}"
        )

    overrides = spec.scenarios.get(scenario, {})
    values: dict[str, Interval] = {}
    sources: dict[str, list[Provenance]] = {}
    for name, declared in spec.inputs.items():
        values[name] = overrides.get(name, declared.value)
        sources[name] = _sources_of_input(spec, name)

    for name in _formula_order(spec):
        result, collected = _evaluate_expr(spec.formulas[name], values, sources)
        values[name] = result
        sources[name] = _dedupe(collected)

    return Evaluated(
        model_id=spec.model_id,
        title=spec.title,
        scenario=scenario,
        values=values,
        provenance=sources,
        units={name: spec.unit_of(name) for name in values},
    )


def evaluate_all(spec: ModelSpec) -> dict[str, Evaluated]:
    """Every declared scenario, in the fixed reporting order."""
    order = [s for s in SCENARIOS if s in spec.scenarios]
    order += [s for s in sorted(spec.scenarios) if s not in SCENARIOS]
    if not order:
        return {TARGET: evaluate(spec, TARGET)}
    return {name: evaluate(spec, name) for name in order}


def _formula_order(spec: ModelSpec) -> list[str]:
    """Formulas in an order where each one's references already have values."""
    remaining = dict(spec.formulas)
    resolved: list[str] = []
    available = set(spec.inputs)
    while remaining:
        ready = [
            name for name, expr in remaining.items()
            if _refs_of(expr) <= available
        ]
        if not ready:
            raise BadFormula(
                f"{spec.model_id}: these formulas depend on each other in a "
                f"cycle: {', '.join(sorted(remaining))}"
            )
        for name in sorted(ready):
            resolved.append(name)
            available.add(name)
            del remaining[name]
    return resolved


def _dedupe(sources: list[Provenance]) -> list[Provenance]:
    seen: dict[tuple[str, str, str], Provenance] = {}
    for source in sources:
        seen.setdefault((source.kind, source.ref, source.label), source)
    return list(seen.values())


# -------------------------------------------------------------- projections


class Series(BaseModel):
    """A month-by-month quantity, as parallel arrays so it charts and serialises."""

    model_config = ConfigDict(frozen=True)

    name: str
    unit: str = ""
    months: list[int]
    low: list[float]
    high: list[float]
    provenance: list[Provenance] = Field(default_factory=list)

    def at(self, month: int) -> Interval:
        index = self.months.index(month)
        return Interval.span(self.low[index], self.high[index])

    @property
    def final(self) -> Interval:
        return Interval.span(self.low[-1], self.high[-1])


class Trajectory(BaseModel):
    """A compounding cost against a flat alternative, and the gap between them.

    The point of the pair is that a saving quoted against today's cost
    understates itself every year the underlying cost escalates. Showing the flat
    line beside it is what makes that visible without anybody having to assert
    it."""

    model_config = ConfigDict(frozen=True)

    escalating: Series
    flat: Series
    cumulative_gap: Series
    escalation_rate: Interval
    provenance: list[Provenance] = Field(default_factory=list)


def cost_trajectory(
    spec: ModelSpec,
    evaluated: Evaluated,
    months: int | None = None,
) -> Trajectory:
    """The escalating-cost path, the flat path, and the cumulative difference.

    Reads `roles.escalating_cost` as an ANNUAL cost and `roles.escalation_rate`
    as an annual rate. Both must be declared; a trajectory drawn without a
    sourced escalation rate would be our own guess about someone's payroll,
    dressed as a projection.
    """
    if not spec.roles.escalating_cost or not spec.roles.escalation_rate:
        raise UnknownReference(
            f"{spec.model_id} declares no escalating_cost and escalation_rate "
            f"roles, so there is no trajectory to draw"
        )
    horizon = months or spec.horizon_months
    annual = evaluated.value(spec.roles.escalating_cost)
    rate = evaluated.value(spec.roles.escalation_rate)
    sources = _dedupe(
        evaluated.sources_for(spec.roles.escalating_cost)
        + evaluated.sources_for(spec.roles.escalation_rate)
    )
    unit = spec.unit_of(spec.roles.escalating_cost)

    month_list = list(range(1, horizon + 1))
    esc_low, esc_high, flat_low, flat_high = [], [], [], []
    gap_low, gap_high = [], []
    running_low = running_high = 0.0
    for month in month_list:
        years = (month - 1) / MONTHS_PER_YEAR
        grown_low = (annual.low / MONTHS_PER_YEAR) * ((1 + rate.low) ** years)
        grown_high = (annual.high / MONTHS_PER_YEAR) * ((1 + rate.high) ** years)
        base_low = annual.low / MONTHS_PER_YEAR
        base_high = annual.high / MONTHS_PER_YEAR
        esc_low.append(grown_low)
        esc_high.append(grown_high)
        flat_low.append(base_low)
        flat_high.append(base_high)
        running_low += grown_low - base_low
        running_high += grown_high - base_high
        gap_low.append(running_low)
        gap_high.append(running_high)

    return Trajectory(
        escalating=Series(
            name=f"{spec.roles.escalating_cost}_escalating", unit=unit,
            months=month_list, low=esc_low, high=esc_high, provenance=sources),
        flat=Series(
            name=f"{spec.roles.escalating_cost}_flat", unit=unit,
            months=month_list, low=flat_low, high=flat_high, provenance=sources),
        cumulative_gap=Series(
            name=f"{spec.roles.escalating_cost}_cumulative_gap", unit=unit,
            months=month_list, low=gap_low, high=gap_high, provenance=sources),
        escalation_rate=rate,
        provenance=sources,
    )


class Payback(BaseModel):
    """Payback as the series it is, and only then as the month it crosses.

    A single payback figure is the most repeated number in a sales conversation
    and the least examined. The series is the deliverable: it shows the month the
    line crosses zero under the worst reading of the inputs and under the best,
    and it shows plainly when the worst reading never crosses at all — which a
    single number reports as a large integer, or not at all.
    """

    model_config = ConfigDict(frozen=True)

    cumulative: Series
    investment: Interval
    monthly_saving: Interval
    fastest_month: int | None
    """First month cumulative net turns positive under the best reading."""

    slowest_month: int | None
    """First month it turns positive under the worst reading. None means it does
    not, inside the horizon."""

    horizon_months: int
    provenance: list[Provenance] = Field(default_factory=list)

    @property
    def pays_back_within_horizon(self) -> bool:
        return self.slowest_month is not None

    def describe(self) -> str:
        """The one sentence a reader should get, and never a bare number."""
        if self.fastest_month is None:
            return (
                f"does not pay back inside {self.horizon_months} months on any "
                f"reading of the inputs"
            )
        if self.slowest_month is None:
            return (
                f"pays back in month {self.fastest_month} at best, and not "
                f"inside {self.horizon_months} months at worst"
            )
        if self.fastest_month == self.slowest_month:
            return f"pays back in month {self.fastest_month} on either reading"
        return (
            f"pays back somewhere between month {self.fastest_month} and month "
            f"{self.slowest_month}"
        )


def payback_series(
    spec: ModelSpec,
    evaluated: Evaluated,
    months: int | None = None,
) -> Payback:
    """Month-by-month cumulative net position, and where it crosses zero.

    The investment lands in month one rather than being spread, because the
    engagement ladder bills bounded work and a reader who is told "month four"
    will assume the money left on day one.
    """
    if not spec.roles.investment or not spec.roles.monthly_saving:
        raise UnknownReference(
            f"{spec.model_id} declares no investment and monthly_saving roles, "
            f"so there is no payback to compute"
        )
    horizon = months or spec.horizon_months
    investment = evaluated.value(spec.roles.investment)
    saving = evaluated.value(spec.roles.monthly_saving)
    sources = _dedupe(
        evaluated.sources_for(spec.roles.investment)
        + evaluated.sources_for(spec.roles.monthly_saving)
    )

    month_list = list(range(1, horizon + 1))
    low, high = [], []
    fastest = slowest = None
    for month in month_list:
        # Worst reading: the biggest bill against the smallest saving.
        worst = saving.low * month - investment.high
        best = saving.high * month - investment.low
        low.append(worst)
        high.append(best)
        if best >= 0 and fastest is None:
            fastest = month
        if worst >= 0 and slowest is None:
            slowest = month

    return Payback(
        cumulative=Series(
            name="cumulative_net", unit=spec.unit_of(spec.roles.investment),
            months=month_list, low=low, high=high, provenance=sources),
        investment=investment,
        monthly_saving=saving,
        fastest_month=fastest,
        slowest_month=slowest,
        horizon_months=horizon,
        provenance=sources,
    )


# -------------------------------------------------------------- discounting


def monthly_rate(annual_rate: float) -> float:
    """The monthly rate equivalent to an annual one, compounded."""
    return (1 + annual_rate) ** (1 / MONTHS_PER_YEAR) - 1


def npv(cashflows: list[float], annual_rate: float) -> float:
    """Net present value of monthly cashflows, month one discounted once.

    Month one is discounted rather than taken at face value because the money
    moves at the end of the month it is earned in, and the alternative
    convention flatters every projection by one period.
    """
    rate = monthly_rate(annual_rate)
    return sum(flow / ((1 + rate) ** (index + 1)) for index, flow in enumerate(cashflows))


def irr(cashflows: list[float], guess_low: float = -0.95, guess_high: float = 100.0,
        tolerance: float = 1e-7, iterations: int = 200) -> float | None:
    """The annual rate at which these monthly cashflows have zero present value.

    Bisected rather than solved, because a closed form does not exist and
    Newton's method wanders off a series that changes sign more than once.
    Returns None when no rate sets the value to zero — which is the honest
    answer for a series that never turns positive, and better than a number.
    """
    if not cashflows or all(flow >= 0 for flow in cashflows) or all(
        flow <= 0 for flow in cashflows
    ):
        return None
    low, high = guess_low, guess_high
    value_low, value_high = npv(cashflows, low), npv(cashflows, high)
    if value_low * value_high > 0:
        return None
    for _ in range(iterations):
        middle = (low + high) / 2
        value = npv(cashflows, middle)
        if abs(value) < tolerance:
            return middle
        if value * value_low > 0:
            low, value_low = middle, value
        else:
            high, value_high = middle, value
    return (low + high) / 2


class Discounted(BaseModel):
    """NPV and IRR for one scenario, with the rate that produced them stated."""

    model_config = ConfigDict(frozen=True)

    discount_rate_annual: float
    npv_low: float
    npv_high: float
    irr_annual_low: float | None
    irr_annual_high: float | None
    horizon_months: int
    provenance: list[Provenance] = Field(default_factory=list)

    IMPLAUSIBLE_IRR: ClassVar[float] = 1.0
    """Above this annual rate an IRR stops describing a return and starts
    describing a very short payback with a division sign in front of it.

    A build that pays for itself in seven weeks has an internal rate of return
    in the hundreds of per cent, which is arithmetically true and useless in a
    document: nobody books it, and a reader who sees it stops believing the
    figures around it."""

    @property
    def npv_interval(self) -> Interval:
        return Interval.span(self.npv_low, self.npv_high)

    def describe(self) -> str:
        """Present value and rate of return, said in a way a reader can use."""
        rate = f"{self.discount_rate_annual:.0%}"
        value = (f"${self.npv_low:,.0f} to ${self.npv_high:,.0f}")
        line = (f"over {self.horizon_months} months at {rate}, the present value "
                f"is {value}")
        best = self.irr_annual_high if self.irr_annual_high is not None \
            else self.irr_annual_low
        if best is None:
            return line + "; no rate of return is defined, because the position "\
                          "never turns positive on this reading"
        if best > self.IMPLAUSIBLE_IRR:
            return (line + "; the internal rate of return is above 100% a year, "
                    "which is what a payback measured in weeks does to that "
                    "arithmetic rather than a return anybody books")
        return line + f"; the internal rate of return is about {best:.0%} a year"


def discounted(
    spec: ModelSpec,
    evaluated: Evaluated,
    annual_rate: float | None = None,
    months: int | None = None,
) -> Discounted:
    """Present value and internal rate of return over the payback cashflows.

    The rate is stated on the result rather than buried, because an NPV without
    its discount rate is a number nobody can check.
    """
    horizon = months or spec.horizon_months
    if not spec.roles.investment or not spec.roles.monthly_saving:
        raise UnknownReference(
            f"{spec.model_id} declares no investment and monthly_saving roles, "
            f"so there are no cashflows to discount"
        )
    if annual_rate is None:
        if not spec.roles.discount_rate:
            raise UnknownReference(
                f"{spec.model_id} declares no discount_rate role and none was "
                f"passed; an NPV needs a stated rate"
            )
        annual_rate = evaluated.value(spec.roles.discount_rate).mid

    investment = evaluated.value(spec.roles.investment)
    saving = evaluated.value(spec.roles.monthly_saving)
    worst = [saving.low - investment.high] + [saving.low] * (horizon - 1)
    best = [saving.high - investment.low] + [saving.high] * (horizon - 1)

    return Discounted(
        discount_rate_annual=float(annual_rate),
        npv_low=npv(worst, annual_rate),
        npv_high=npv(best, annual_rate),
        irr_annual_low=irr(worst),
        irr_annual_high=irr(best),
        horizon_months=horizon,
        provenance=_dedupe(
            evaluated.sources_for(spec.roles.investment)
            + evaluated.sources_for(spec.roles.monthly_saving)
        ),
    )


# --------------------------------------------------------------- sensitivity


class Sensitivity(BaseModel):
    """What one input would have to be for a target to hold.

    The sentence this produces — "for payback inside twelve months, the quoting
    hours saved must be at least six a week" — is the most useful thing an
    analysis can hand an operator, because it converts a projection into one
    question to ask on the call.
    """

    model_config = ConfigDict(frozen=True)

    input_name: str
    input_words: str
    unit: str = ""
    target_months: int
    threshold: float | None
    """The value at which the target starts to hold. None: no value inside the
    searched range achieves it."""

    direction: Literal["at least", "at most"]
    searched_low: float
    searched_high: float
    already_holds: bool
    provenance: list[Provenance] = Field(default_factory=list)

    def describe(self) -> str:
        if self.threshold is None:
            return (
                f"no value of {self.input_words} inside the range searched brings "
                f"payback within {self.target_months} months"
            )
        return (
            f"for payback within {self.target_months} months, {self.input_words} "
            f"must be {self.direction} {self.threshold:,.4g}"
            + (f" {self.unit}" if self.unit else "")
        )


def sensitivity(
    spec: ModelSpec,
    input_name: str,
    target_months: int,
    scenario: str = TARGET,
    search: tuple[float, float] | None = None,
    steps: int = 240,
) -> Sensitivity:
    """Solve for the value of one input at which payback reaches ``target_months``.

    Scanned and then bisected rather than inverted algebraically: the spec's
    formulas are arbitrary, so there is no expression to rearrange, and a scan
    over a declared range cannot silently return an answer from the wrong branch
    of a non-monotone function. The range searched is reported either way, so a
    "no value works" answer says what was actually tried.
    """
    if input_name not in spec.inputs:
        raise UnknownReference(f"{input_name!r} is not an input of {spec.model_id}")
    declared = spec.inputs[input_name].value
    span = search or _search_range(declared)

    def worst_payback_month(candidate: float) -> int | None:
        probe = _with_input(spec, input_name, Interval.of(candidate))
        try:
            result = payback_series(probe, evaluate(probe, scenario), target_months)
        except FinModelError:
            # A spec need not be defined at every value of every input: zero
            # minutes of work per unit makes a capacity formula divide by zero.
            # A value the model cannot evaluate is a value that does not meet
            # the target, which is the honest reading and keeps the search from
            # crashing on the edge of its own range.
            return None
        return result.slowest_month

    holds_now = worst_payback_month(declared.low) is not None
    low, high = span
    increasing = _target_direction(worst_payback_month, low, high, target_months)
    threshold = _first_value_where(
        worst_payback_month, low, high, steps, increasing
    )
    return Sensitivity(
        input_name=input_name,
        input_words=spec.words_for(input_name),
        unit=spec.unit_of(input_name),
        target_months=target_months,
        threshold=threshold,
        direction="at least" if increasing else "at most",
        searched_low=low,
        searched_high=high,
        already_holds=holds_now,
        provenance=[spec.inputs[input_name].provenance],
    )


SEARCH_FLOOR_FRACTION = 0.01
"""How far below the declared low a search reaches, as a fraction of it.

Not zero. A model is frequently undefined at zero — no minutes of work per unit
means no capacity, and capacity is a division — and starting the scan there made
the search crash on its own first probe rather than on anything to do with the
question being asked."""


def _search_range(declared: Interval) -> tuple[float, float]:
    """A range around the declared value wide enough to find a threshold in.

    Twenty times the declared high at the top, because the question is usually
    "how much worse could this be and still work" and an input can legitimately
    be an order of magnitude off a first guess. The bottom is a small fraction of
    the declared low rather than zero, for the reason above."""
    top = max(abs(declared.high), 1.0) * 20
    bottom = max(abs(declared.low), 1.0) * SEARCH_FLOOR_FRACTION
    return (bottom, top)


def _target_direction(probe, low: float, high: float, target: int) -> bool:
    """True when raising the input is what brings payback forward."""
    at_low, at_high = probe(low), probe(high)
    if at_high is not None and at_low is None:
        return True
    if at_low is not None and at_high is None:
        return False
    if at_low is not None and at_high is not None:
        return at_high <= at_low
    return True


def _first_value_where(probe, low: float, high: float, steps: int,
                       increasing: bool) -> float | None:
    """Scan for the first value satisfying the target, then narrow it."""
    width = (high - low) / steps
    order = range(steps + 1) if increasing else range(steps, -1, -1)
    found: float | None = None
    previous: float | None = None
    for index in order:
        candidate = low + width * index
        if probe(candidate) is not None:
            found = candidate
            break
        previous = candidate
    if found is None:
        return None
    if previous is None:
        return found
    # Narrow between the last failing value and the first passing one.
    lower, upper = (previous, found) if increasing else (found, previous)
    for _ in range(40):
        middle = (lower + upper) / 2
        if probe(middle) is not None:
            if increasing:
                upper = middle
            else:
                lower = middle
        else:
            if increasing:
                lower = middle
            else:
                upper = middle
    return upper if increasing else lower


def _with_input(spec: ModelSpec, name: str, value: Interval) -> ModelSpec:
    """A copy of the spec with one input's value replaced, provenance intact.

    The scenario overrides for that input are dropped as well. Without that the
    probe silently evaluated the scenario's own figure and the search returned
    the declared value dressed as a threshold — a wrong answer that looks
    plausible, which is the worst kind this module can produce.
    """
    replaced = dict(spec.inputs)
    original = replaced[name]
    replaced[name] = Input(
        name=original.name, value=value, unit=original.unit,
        provenance=original.provenance, description=original.description,
    )
    scenarios = {
        scenario: {k: v for k, v in overrides.items() if k != name}
        for scenario, overrides in spec.scenarios.items()
    }
    return spec.model_copy(update={"inputs": replaced, "scenarios": scenarios})


# ---------------------------------------------------------- capacity collapse


class CapacityCollapse(BaseModel):
    """When growing demand crosses a processing rate that is not growing.

    The finding this produces is the strongest one an analysis can carry, because
    it is not about cost at all: a manual step that copes today stops coping on a
    date, and the date follows from two numbers the prospect already knows. It is
    also the finding most easily faked, which is why the growth rate must be
    sourced or labelled like everything else.
    """

    model_config = ConfigDict(frozen=True)

    demand: Series
    capacity: Interval
    crossing_month_earliest: int | None
    """Month the busy reading of demand passes the busy reading of capacity."""

    crossing_month_latest: int | None
    """The same for the quiet reading. None means it does not, inside the
    horizon."""
    horizon_months: int
    headroom_now: Interval
    provenance: list[Provenance] = Field(default_factory=list)

    @property
    def crosses_within_horizon(self) -> bool:
        return self.crossing_month_earliest is not None

    def describe(self) -> str:
        if self.crossing_month_earliest is None:
            return (
                f"demand stays inside the manual processing rate for the whole "
                f"{self.horizon_months} months on either reading"
            )
        if self.crossing_month_latest is None:
            return (
                f"demand crosses the manual processing rate in month "
                f"{self.crossing_month_earliest} on the faster reading, and not "
                f"inside {self.horizon_months} months on the slower one"
            )
        if self.crossing_month_earliest == self.crossing_month_latest:
            return (
                f"demand crosses the manual processing rate in month "
                f"{self.crossing_month_earliest} on either reading"
            )
        return (
            f"demand crosses the manual processing rate somewhere between month "
            f"{self.crossing_month_earliest} and month {self.crossing_month_latest}"
        )


def capacity_collapse(
    spec: ModelSpec,
    evaluated: Evaluated,
    months: int | None = None,
) -> CapacityCollapse:
    """Project volume growth against a fixed manual rate and find the crossing.

    Needs all three roles. A collapse projection missing the capacity figure
    would be a growth chart with an alarming headline, which is the opposite of
    what this is for.
    """
    for role in ("monthly_volume", "volume_growth", "manual_capacity"):
        if not getattr(spec.roles, role):
            raise UnknownReference(
                f"{spec.model_id} declares no {role} role, so there is no "
                f"capacity projection to make"
            )
    horizon = months or spec.horizon_months
    volume = evaluated.value(spec.roles.monthly_volume)
    growth = evaluated.value(spec.roles.volume_growth)
    capacity = evaluated.value(spec.roles.manual_capacity)
    sources = _dedupe(
        evaluated.sources_for(spec.roles.monthly_volume)
        + evaluated.sources_for(spec.roles.volume_growth)
        + evaluated.sources_for(spec.roles.manual_capacity)
    )

    # Ends are paired coherently — the busy reading of demand against the busy
    # reading of capacity, the quiet against the quiet — rather than the highest
    # demand against the lowest capacity. Capacity is usually derived from the
    # same volume the demand is: a shop with more work has more people doing it,
    # so crossing the two ends describes a world that does not exist, and it
    # reported the collapse as month one on every model that had one.
    month_list = list(range(1, horizon + 1))
    low, high = [], []
    earliest = latest = None
    for month in month_list:
        years = (month - 1) / MONTHS_PER_YEAR
        slow = volume.low * ((1 + growth.low) ** years)
        fast = volume.high * ((1 + growth.high) ** years)
        low.append(slow)
        high.append(fast)
        if earliest is None and fast > capacity.high:
            earliest = month
        if latest is None and slow > capacity.low:
            latest = month

    return CapacityCollapse(
        demand=Series(
            name="monthly_demand", unit=spec.unit_of(spec.roles.monthly_volume),
            months=month_list, low=low, high=high, provenance=sources),
        capacity=capacity,
        crossing_month_earliest=earliest,
        crossing_month_latest=latest,
        horizon_months=horizon,
        headroom_now=Interval.span(capacity.low - volume.low,
                                   capacity.high - volume.high),
        provenance=sources,
    )


# ----------------------------------------------------------------- reporting


class ModelReport(BaseModel):
    """Everything one spec produces, across all three scenarios.

    This is the object the analyst is handed. It is also the object the prose is
    checked against: `traceable_figures` is the complete list of numbers the
    document is allowed to contain from this model, and anything else in the
    prose is either a claim, a benchmark, or a rejection.
    """

    model_config = ConfigDict(frozen=True)

    spec: ModelSpec
    scenarios: dict[str, Evaluated]
    payback: dict[str, Payback] = Field(default_factory=dict)
    trajectory: dict[str, Trajectory] = Field(default_factory=dict)
    collapse: dict[str, CapacityCollapse] = Field(default_factory=dict)
    discounting: dict[str, Discounted] = Field(default_factory=dict)
    sensitivities: list[Sensitivity] = Field(default_factory=list)

    def traceable_figures(self) -> set[float]:
        """Every number this model legitimately puts in front of a reader."""
        out: set[float] = set()
        for evaluated in self.scenarios.values():
            out |= evaluated.figures()
        for payback in self.payback.values():
            out |= {float(payback.horizon_months)}
            for month in (payback.fastest_month, payback.slowest_month):
                if month is not None:
                    out.add(float(month))
        for collapse in self.collapse.values():
            out |= {collapse.headroom_now.low, collapse.headroom_now.high,
                    float(collapse.horizon_months)}
            for month in (collapse.crossing_month_earliest,
                          collapse.crossing_month_latest):
                if month is not None:
                    out.add(float(month))
        for discount in self.discounting.values():
            out |= {discount.npv_low, discount.npv_high, discount.discount_rate_annual}
            for rate in (discount.irr_annual_low, discount.irr_annual_high):
                if rate is not None:
                    out.add(rate)
        for item in self.sensitivities:
            if item.threshold is not None:
                out.add(item.threshold)
            out.add(float(item.target_months))
        for trajectory in self.trajectory.values():
            out |= {trajectory.cumulative_gap.final.low, trajectory.cumulative_gap.final.high}
        return out

    def assumptions(self) -> list[Provenance]:
        seen: dict[str, Provenance] = {}
        for evaluated in self.scenarios.values():
            for source in evaluated.assumptions():
                seen.setdefault(source.label, source)
        return list(seen.values())

    def claim_paths(self) -> set[str]:
        """Every claim this model rests on, for the analyst's citation list."""
        return {
            source.ref
            for evaluated in self.scenarios.values()
            for sources in evaluated.provenance.values()
            for source in sources
            if source.kind == CLAIM and source.ref
        }

    def benchmark_ids(self) -> set[str]:
        return {
            source.ref
            for evaluated in self.scenarios.values()
            for sources in evaluated.provenance.values()
            for source in sources
            if source.kind == BENCHMARK and source.ref
        }


def run(
    spec: ModelSpec,
    sensitivity_targets: tuple[tuple[str, int], ...] = (),
    discount_rate: float | None = None,
) -> ModelReport:
    """Evaluate a spec every way it supports, and return the whole result.

    What a spec supports is decided by which roles it declares, not by a flag: a
    model with no capacity roles gets no collapse projection, and asking for one
    is an error rather than an empty chart.
    """
    scenarios = evaluate_all(spec)
    payback: dict[str, Payback] = {}
    trajectory: dict[str, Trajectory] = {}
    collapse: dict[str, CapacityCollapse] = {}
    discounting: dict[str, Discounted] = {}

    has_payback = bool(spec.roles.investment and spec.roles.monthly_saving)
    has_trajectory = bool(spec.roles.escalating_cost and spec.roles.escalation_rate)
    has_collapse = bool(
        spec.roles.monthly_volume and spec.roles.volume_growth
        and spec.roles.manual_capacity
    )
    has_discount = has_payback and bool(spec.roles.discount_rate or discount_rate)

    for name, evaluated in scenarios.items():
        if has_payback:
            payback[name] = payback_series(spec, evaluated)
        if has_trajectory:
            trajectory[name] = cost_trajectory(spec, evaluated)
        if has_collapse:
            collapse[name] = capacity_collapse(spec, evaluated)
        if has_discount:
            discounting[name] = discounted(spec, evaluated, discount_rate)

    sensitivities = [
        sensitivity(spec, input_name, target_months)
        for input_name, target_months in sensitivity_targets
    ] if has_payback else []

    return ModelReport(
        spec=spec, scenarios=scenarios, payback=payback, trajectory=trajectory,
        collapse=collapse, discounting=discounting, sensitivities=sensitivities,
    )
