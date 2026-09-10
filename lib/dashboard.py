"""Compile one finmodel spec into a calculator a prospect can open and argue with.

WHY THE SPEC COMPILES AND THE LOGIC DOES NOT

`lib/finmodel.py` says it out loud: "the same spec is meant to compile to an
interactive calculator a prospect can open and argue with, and the moment any
logic lives in Python that does not live in the spec, the calculator and the
document disagree about the business." This module is the other end of that
promise, and it keeps it the only way it can be kept — by shipping the spec's
own JSON into the page and walking it with a generic evaluator.

There is exactly one JavaScript file here and every company gets the same one.
Nothing in it knows what a quote is, what a wage is, or what this company does.
It reads `op` and it applies the operator, in the same order over the same
interval arithmetic as `_evaluate_expr` does in Python. A per-company line of
JavaScript would be a second implementation of the business, and the second
implementation is always the one that is wrong.

WHY THE SLIDERS ARE THE POINT

Every assumption in the model is a range we published so it could be corrected.
On paper that correction takes a phone call. Here it takes a drag: the prospect
moves "minutes per quote" to what it actually is, and every figure on the page
follows. That converts the document from a claim into a question, which is the
same argument `docs/CANARY.md` makes about publishing conditional ranges — a
reply correcting an estimate is a success and explicitly does not halt.

Claims are NOT sliders. A wage they published and an award their government
record states are theirs, and a page that let somebody drag them would be
inviting a prospect to edit the evidence.

NO EXTERNAL RESOURCES, AND NOT ONLY FOR PRIVACY

One file, no fonts, no CDN, no build step — the same rule the console keeps. A
page a prospect opens must not phone home about it: a request to a third party
tells that third party which company opened our calculator and when, and it does
it from the prospect's own network. It also means the file works from a USB
stick, from an email attachment, and from a printer's laptop, which is where a
leave-behind actually ends up.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path
from typing import Any

from lib import benchmarks, finmodel

DASHBOARD_DIR = Path("reports/dashboards")
"""Where a compiled dashboard is written. Hosting is somebody else's task."""

TOKEN_SALT = "conexus-dashboard-v1"
"""Fixed so a company's token is stable across runs.

A token that changed every build would invalidate every QR code already printed
on a FedEx one-pager, which is the one artifact we cannot re-issue after it has
been posted."""

TOKEN_LENGTH = 10


def token_for(prospect: dict[str, Any]) -> str:
    """This company's dashboard slug: a readable name plus a stable digest.

    Readable so an operator reading a URL aloud on a call knows whose it is, and
    digested so the slug cannot be guessed from the company name alone. Neither
    half is a security control — the page carries nothing private — but a URL
    somebody can enumerate is a URL that will be enumerated.
    """
    name = re.sub(r"[^a-z0-9]+", "-",
                  str(prospect.get("company_name") or "company").lower()).strip("-")
    seed = f"{TOKEN_SALT}:{prospect.get('id') or name}"
    digest = hashlib.blake2s(seed.encode("utf-8")).hexdigest()[:TOKEN_LENGTH]
    return f"{name[:32].strip('-') or 'company'}-{digest}"


def dashboard_url(prospect: dict[str, Any], base: str | None = None) -> str:
    """The address the QR code points at.

    The base comes from the environment through `lib/config.py`, and a
    placeholder is allowed: hosting is a later task in the website repo, and a
    one-pager printed before then should carry a URL that is obviously a
    placeholder rather than one that looks real and 404s.
    """
    from lib.config import settings

    root = (base or settings.dashboard_base_url or "").rstrip("/")
    return f"{root}/{token_for(prospect)}"


def output_path(prospect: dict[str, Any], directory: Path | None = None) -> Path:
    return (directory or DASHBOARD_DIR) / f"{token_for(prospect)}.html"


# ------------------------------------------------------------------ the page


def _slider_inputs(spec: finmodel.ModelSpec) -> list[dict[str, Any]]:
    """Every input the reader may move, with the range it may move inside.

    An assumption is a slider. A claim and a benchmark are not: a figure they
    published about themselves is evidence, and a page that let a reader drag it
    would be offering to edit the record rather than to correct our guess.

    The travel is the widest reading any scenario declares, stretched by half
    again, because a prospect correcting us is usually correcting us outside the
    range we guessed — that is what makes the correction worth having.
    """
    out: list[dict[str, Any]] = []
    for name, model_input in spec.inputs.items():
        if not model_input.provenance.is_assumption:
            continue
        lows = [model_input.value.low]
        highs = [model_input.value.high]
        for overrides in spec.scenarios.values():
            if name in overrides:
                lows.append(overrides[name].low)
                highs.append(overrides[name].high)
        low, high = min(lows), max(highs)
        span = (high - low) or abs(high) or 1.0
        out.append({
            "name": name,
            "label": model_input.in_words,
            "unit": model_input.unit,
            "low": low,
            "high": high,
            "min": max(0.0, low - span * 0.5),
            "max": high + span * 0.5,
            "assumed": model_input.provenance.label,
        })
    return out


def _fixed_inputs(spec: finmodel.ModelSpec) -> list[dict[str, Any]]:
    """The inputs that are theirs or somebody's published figure, with the source."""
    return [
        {
            "name": name,
            "label": model_input.in_words,
            "unit": model_input.unit,
            "low": model_input.value.low,
            "high": model_input.value.high,
            "source": model_input.provenance.describe(),
        }
        for name, model_input in spec.inputs.items()
        if not model_input.provenance.is_assumption
    ]


def _reported(spec: finmodel.ModelSpec) -> list[dict[str, str]]:
    """Which formulas the page shows, in the order it shows them."""
    return [
        {"name": name, "unit": spec.unit_of(name),
         "label": spec.words_for(name)}
        for name in spec.formulas
    ]


def citations(spec: finmodel.ModelSpec) -> list[str]:
    """Everything a reader would have to check to disagree with the page.

    The claim paths are deliberately NOT here. A path is an internal address and
    means nothing to a prospect; what belongs on their page is the source line
    the provenance describes, which names the thing they can go and read.
    """
    lines: list[str] = []
    benchmark_ids: list[str] = []
    for model_input in spec.inputs.values():
        provenance = model_input.provenance
        if provenance.kind == finmodel.BENCHMARK:
            benchmark_ids.append(provenance.ref)
        elif provenance.kind == finmodel.CLAIM:
            label = provenance.label or "their own record"
            if label not in lines:
                lines.append(label)
    lines.extend(benchmarks.citations_for(set(benchmark_ids)))
    return lines


def page_data(spec: finmodel.ModelSpec, company: str, currency: str = "$") -> dict[str, Any]:
    """Everything the page needs, as one JSON object. No logic, only the spec."""
    return {
        "company": company,
        "title": spec.title,
        "model_id": spec.model_id,
        "currency": currency,
        "horizon_months": spec.horizon_months,
        "scenarios": {name: {k: [v.low, v.high] for k, v in overrides.items()}
                      for name, overrides in spec.scenarios.items()},
        "scenario_order": list(finmodel.SCENARIOS),
        "inputs": {name: {"low": i.value.low, "high": i.value.high,
                          "unit": i.unit, "label": i.in_words}
                   for name, i in spec.inputs.items()},
        "formulas": {name: expr.model_dump(mode="json")
                     for name, expr in spec.formulas.items()},
        "sliders": _slider_inputs(spec),
        "fixed": _fixed_inputs(spec),
        "reported": _reported(spec),
        "roles": spec.roles.model_dump(mode="json"),
        "notes": list(spec.notes),
        "citations": citations(spec),
    }


EVALUATOR_JS = r"""
// One evaluator, shared by every company. It knows the operators and nothing
// about the business — the business is entirely in MODEL.formulas, which is the
// same JSON the Python engine evaluates. If these two ever disagree, the page
// and the document are describing different companies.
function iv(low, high) { return { low: Math.min(low, high), high: Math.max(low, high) }; }
function point(v) { return iv(v, v); }

function mul(a, b) {
  var c = [a.low * b.low, a.low * b.high, a.high * b.low, a.high * b.high];
  return iv(Math.min.apply(null, c), Math.max.apply(null, c));
}
function div(a, b) {
  if (b.low <= 0 && 0 <= b.high) { return null; }   // no bounded answer
  var c = [a.low / b.low, a.low / b.high, a.high / b.low, a.high / b.high];
  return iv(Math.min.apply(null, c), Math.max.apply(null, c));
}
function compound(base, rate, terms) {
  if (rate.low <= -1) { return null; }
  var out = [];
  [base.low, base.high].forEach(function (b) {
    [rate.low, rate.high].forEach(function (r) {
      [terms.low, terms.high].forEach(function (t) {
        out.push(b * Math.pow(1 + r, t));
      });
    });
  });
  return iv(Math.min.apply(null, out), Math.max.apply(null, out));
}

function evalExpr(expr, values) {
  if (expr.op === "const") { return point(expr.value || 0); }
  if (expr.op === "ref") {
    var found = values[expr.ref];
    return found === undefined ? null : found;
  }
  var parts = [];
  for (var i = 0; i < expr.args.length; i++) {
    var part = evalExpr(expr.args[i], values);
    if (part === null) { return null; }
    parts.push(part);
  }
  var result = parts[0];
  switch (expr.op) {
    case "add":
      for (var a = 1; a < parts.length; a++) {
        result = iv(result.low + parts[a].low, result.high + parts[a].high);
      }
      return result;
    case "sub":
      return iv(parts[0].low - parts[1].high, parts[0].high - parts[1].low);
    case "mul":
      for (var m = 1; m < parts.length; m++) { result = mul(result, parts[m]); }
      return result;
    case "div":
      return div(parts[0], parts[1]);
    case "neg":
      return iv(-parts[0].high, -parts[0].low);
    case "min":
      return iv(Math.min.apply(null, parts.map(function (p) { return p.low; })),
                Math.min.apply(null, parts.map(function (p) { return p.high; })));
    case "max":
      return iv(Math.max.apply(null, parts.map(function (p) { return p.low; })),
                Math.max.apply(null, parts.map(function (p) { return p.high; })));
    case "compound":
      return compound(parts[0], parts[1], parts[2]);
    case "annualise":
      return iv(parts[0].low * 12, parts[0].high * 12);
    case "monthlyise":
      return iv(parts[0].low / 12, parts[0].high / 12);
    default:
      return null;
  }
}

// Formulas may refer to earlier formulas, so this resolves in dependency order
// by repetition rather than by a topological sort. The number of formulas in a
// spec is single digits, so the simplest thing that provably terminates wins.
function evaluateAll(values) {
  var out = {};
  for (var k in values) { out[k] = values[k]; }
  var names = Object.keys(MODEL.formulas);
  for (var pass = 0; pass < names.length + 1; pass++) {
    var moved = false;
    names.forEach(function (name) {
      if (out[name] !== undefined) { return; }
      var value = evalExpr(MODEL.formulas[name], out);
      if (value !== null) { out[name] = value; moved = true; }
    });
    if (!moved) { break; }
  }
  return out;
}
"""

RENDER_JS = r"""
var state = {};
var scenario = "target";

function currentInputs() {
  var values = {};
  Object.keys(MODEL.inputs).forEach(function (name) {
    var base = MODEL.inputs[name];
    var override = (MODEL.scenarios[scenario] || {})[name];
    var pair = override ? { low: override[0], high: override[1] }
                        : { low: base.low, high: base.high };
    if (state[name] !== undefined) {
      pair = { low: state[name], high: state[name] };
    }
    values[name] = iv(pair.low, pair.high);
  });
  return values;
}

function money(v) {
  var sign = MODEL.currency;
  var abs = Math.abs(v);
  if (abs >= 1000) { return sign + Math.round(v).toLocaleString(); }
  return sign + v.toFixed(0);
}

function show(value, unit) {
  if (value === null || value === undefined) { return "—"; }
  var fmt = function (v) {
    if (unit === "$") { return money(v); }
    if (unit === "share" || unit === "a year") { return (v * 100).toFixed(1) + "%"; }
    if (Math.abs(v) >= 1000) { return Math.round(v).toLocaleString(); }
    return (Math.round(v * 100) / 100).toString();
  };
  if (Math.abs(value.high - value.low) < 1e-9) { return fmt(value.low); }
  return fmt(value.low) + " – " + fmt(value.high);
}

function cumulative(values) {
  // The one picture worth drawing: money out at month zero, money back every
  // month after it, at both ends of the range. Where the band crosses zero is
  // the payback, and where it stays below is the honest answer that it does not.
  var fee = values[MODEL.roles.investment];
  var monthly = values[MODEL.roles.monthly_saving];
  if (!fee || !monthly) { return null; }
  var series = [];
  for (var m = 0; m <= MODEL.horizon_months; m++) {
    series.push({
      month: m,
      low: monthly.low * m - fee.high,
      high: monthly.high * m - fee.low
    });
  }
  return series;
}

function svgLine(series) {
  if (!series) { return "<p class='muted'>This model has no payback curve.</p>"; }
  var W = 640, H = 240, PAD = 44;
  var lows = series.map(function (p) { return p.low; });
  var highs = series.map(function (p) { return p.high; });
  var minY = Math.min.apply(null, lows.concat([0]));
  var maxY = Math.max.apply(null, highs.concat([0]));
  var spanY = (maxY - minY) || 1;
  var x = function (m) { return PAD + (m / MODEL.horizon_months) * (W - PAD - 12); };
  var y = function (v) { return H - PAD - ((v - minY) / spanY) * (H - PAD - 16); };
  var band = series.map(function (p) { return x(p.month) + "," + y(p.high); })
      .concat(series.slice().reverse().map(function (p) {
        return x(p.month) + "," + y(p.low); })).join(" ");
  var mid = series.map(function (p) {
    return x(p.month) + "," + y((p.low + p.high) / 2); }).join(" ");
  var zero = y(0);
  return "" +
    "<svg viewBox='0 0 " + W + " " + H + "' role='img' aria-label='cumulative position'>" +
    "<polygon points='" + band + "' class='band'/>" +
    "<polyline points='" + mid + "' class='mid'/>" +
    "<line x1='" + PAD + "' y1='" + zero + "' x2='" + (W - 12) + "' y2='" + zero +
      "' class='axis'/>" +
    "<text x='" + PAD + "' y='" + (H - 14) + "' class='tick'>month 0</text>" +
    "<text x='" + (W - 90) + "' y='" + (H - 14) + "' class='tick'>month " +
      MODEL.horizon_months + "</text>" +
    "<text x='6' y='" + (zero - 6) + "' class='tick'>break even</text>" +
    "</svg>";
}

function svgBars(values) {
  var rows = MODEL.reported.filter(function (r) { return r.unit === "$"; }).slice(0, 4);
  if (!rows.length) { return ""; }
  var max = 0;
  rows.forEach(function (r) {
    var v = values[r.name];
    if (v) { max = Math.max(max, Math.abs(v.high)); }
  });
  if (!max) { return ""; }
  var W = 640, rowH = 40, H = rows.length * rowH + 12;
  var bars = rows.map(function (r, i) {
    var v = values[r.name];
    if (!v) { return ""; }
    var yTop = i * rowH + 6;
    var scale = function (n) { return 210 + (Math.abs(n) / max) * (W - 230); };
    return "<rect x='210' y='" + yTop + "' width='" +
      Math.max(1, scale(v.high) - 210) + "' height='18' class='bar-high'/>" +
      "<rect x='210' y='" + yTop + "' width='" +
      Math.max(1, scale(v.low) - 210) + "' height='18' class='bar-low'/>" +
      "<text x='0' y='" + (yTop + 14) + "' class='barlabel'>" + r.label + "</text>" +
      "<text x='" + (scale(v.high) + 6) + "' y='" + (yTop + 14) + "' class='tick'>" +
      show(v, "$") + "</text>";
  }).join("");
  return "<svg viewBox='0 0 " + W + " " + H + "' role='img' aria-label='annual figures'>" +
    bars + "</svg>";
}

function render() {
  var values = evaluateAll(currentInputs());
  var rows = MODEL.reported.map(function (r) {
    return "<tr><th>" + r.label + "</th><td>" + show(values[r.name], r.unit) +
      "</td></tr>";
  }).join("");
  document.getElementById("outputs").innerHTML = rows;
  document.getElementById("chart").innerHTML = svgLine(cumulative(values));
  document.getElementById("bars").innerHTML = svgBars(values);
  MODEL.sliders.forEach(function (s) {
    var readout = document.getElementById("read-" + s.name);
    if (readout) {
      readout.textContent = show(values[s.name], s.unit);
    }
  });
}

function build() {
  var host = document.getElementById("sliders");
  MODEL.sliders.forEach(function (s) {
    var wrap = document.createElement("div");
    wrap.className = "slider";
    var step = (s.max - s.min) / 200 || 0.01;
    wrap.innerHTML =
      "<label for='in-" + s.name + "'>" + s.label +
      " <span class='readout' id='read-" + s.name + "'></span></label>" +
      "<input type='range' id='in-" + s.name + "' min='" + s.min + "' max='" + s.max +
      "' step='" + step + "' value='" + ((s.low + s.high) / 2) + "'>" +
      "<p class='muted'>We assumed " + s.assumed + ". Move it to what it really is.</p>";
    host.appendChild(wrap);
    var control = wrap.querySelector("input");
    control.addEventListener("input", function () {
      state[s.name] = parseFloat(control.value);
      render();
    });
  });
  var picker = document.getElementById("scenario");
  MODEL.scenario_order.forEach(function (name) {
    var button = document.createElement("button");
    button.textContent = name;
    button.className = name === scenario ? "on" : "";
    button.addEventListener("click", function () {
      scenario = name;
      state = {};
      document.querySelectorAll("#sliders input").forEach(function (el) {
        var key = el.id.replace("in-", "");
        var override = (MODEL.scenarios[scenario] || {})[key];
        var base = MODEL.inputs[key];
        var pair = override || [base.low, base.high];
        el.value = (pair[0] + pair[1]) / 2;
      });
      Array.prototype.forEach.call(picker.children, function (b) {
        b.className = b.textContent === name ? "on" : "";
      });
      render();
    });
    picker.appendChild(button);
  });
  render();
}

document.addEventListener("DOMContentLoaded", build);
"""

STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; font: 16px/1.55 -apple-system, Segoe UI, Roboto, Helvetica, sans-serif;
       color: #1b1b1b; background: #f7f6f3; }
main { max-width: 900px; margin: 0 auto; padding: 32px 20px 80px; }
header { border-bottom: 2px solid #1b1b1b; padding-bottom: 14px; margin-bottom: 24px; }
h1 { font-size: 26px; margin: 0 0 4px; }
.prepared { font-size: 13px; letter-spacing: .08em; text-transform: uppercase;
            color: #6a6a6a; margin: 0; }
h2 { font-size: 17px; margin: 32px 0 10px; }
p.muted, .muted { color: #6a6a6a; font-size: 13px; margin: 4px 0 0; }
table { width: 100%; border-collapse: collapse; margin-top: 8px; }
th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid #e2e0da;
         font-size: 15px; vertical-align: top; }
th { font-weight: 600; width: 58%; }
td { font-variant-numeric: tabular-nums; }
.slider { margin: 0 0 18px; }
.slider label { display: block; font-weight: 600; font-size: 14px; }
.slider input { width: 100%; }
.readout { float: right; font-weight: 400; font-variant-numeric: tabular-nums; }
#scenario { display: flex; gap: 8px; margin: 8px 0 18px; }
#scenario button { border: 1px solid #1b1b1b; background: #fff; padding: 6px 14px;
                   font: inherit; font-size: 14px; cursor: pointer; border-radius: 2px; }
#scenario button.on { background: #1b1b1b; color: #fff; }
svg { width: 100%; height: auto; background: #fff; border: 1px solid #e2e0da; }
.band { fill: #c8d8cf; opacity: .8; }
.mid { fill: none; stroke: #1f5c46; stroke-width: 2; }
.axis { stroke: #a8a49b; stroke-dasharray: 4 3; }
.tick, .barlabel { font-size: 11px; fill: #4a4a4a; }
.bar-high { fill: #c8d8cf; }
.bar-low { fill: #1f5c46; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 28px; }
footer { margin-top: 40px; border-top: 1px solid #cfccc4; padding-top: 14px;
         font-size: 12.5px; color: #55534d; }
footer li { margin-bottom: 5px; }
@media (max-width: 720px) { .cols { grid-template-columns: 1fr; } }
"""


def _json_for_script(data: dict[str, Any]) -> str:
    """The page data as JSON that cannot break out of its own script tag.

    A company called `</script><script>alert(1)</script>` is not a hypothetical
    attack from outside — company names come from government listings and from
    pages we crawl, and one of them will eventually contain angle brackets by
    accident. Escaping the three characters an HTML parser looks for inside a
    script element is the whole fix, and it leaves valid JSON: `\u003c` is the
    same string to a JSON reader and is invisible to an HTML parser.
    """
    blob = json.dumps(data, indent=None, separators=(",", ":"))
    return (blob.replace("<", "\\u003c")
                .replace(">", "\\u003e")
                .replace("&", "\\u0026"))


def compile_page(
    spec: finmodel.ModelSpec, company: str, currency: str = "$",
    footnote: str = "",
) -> str:
    """One self-contained HTML file for one model. No network, no build step."""
    data = page_data(spec, company, currency)
    payload = _json_for_script(data)
    fixed_rows = "".join(
        f"<tr><th>{html.escape(row['label'])}</th>"
        f"<td>{_fixed_words(row, currency)}<div class='muted'>"
        f"{html.escape(row['source'])}</div></td></tr>"
        for row in data["fixed"]
    ) or "<tr><td class='muted'>Every input on this page is ours.</td></tr>"
    citation_items = "".join(
        f"<li>{html.escape(line)}</li>" for line in data["citations"])
    note_items = "".join(f"<li>{html.escape(note)}</li>" for note in data["notes"])
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(company)} — {html.escape(spec.title)}</title>
<style>{STYLE}</style>
</head><body><main>
<header>
  <p class="prepared">Prepared for {html.escape(company)}</p>
  <h1>{html.escape(spec.title)}</h1>
  <p class="muted">Every figure below is computed from the inputs on the left.
     The ones we guessed are sliders. Move them to what they really are and
     everything follows — that is what this page is for.</p>
</header>

<div class="cols">
  <section>
    <h2>What we assumed</h2>
    <div id="scenario"></div>
    <div id="sliders"></div>
  </section>
  <section>
    <h2>What that gives</h2>
    <table><tbody id="outputs"></tbody></table>
    <h2>What is theirs, not ours</h2>
    <table><tbody>{fixed_rows}</tbody></table>
  </section>
</div>

<h2>Cumulative position</h2>
<div id="chart"></div>
<p class="muted">The band is the whole range, worst case to best. Where it crosses
   the dotted line the build has paid for itself.</p>

<h2>The annual figures</h2>
<div id="bars"></div>

<footer>
  <p><b>Where these numbers come from.</b> {html.escape(footnote)}</p>
  <ul>{note_items}{citation_items}</ul>
  <p>Prepared by Nahl Technologies. Nothing on this page was measured at
     {html.escape(company)} — the sliders are exactly the things we do not know,
     and correcting one of them is more useful to both of us than anything else
     you could send back.</p>
</footer>
</main>
<script>var MODEL = {payload};</script>
<script>{EVALUATOR_JS}</script>
<script>{RENDER_JS}</script>
</body></html>
"""


def _fixed_words(row: dict[str, Any], currency: str) -> str:
    def one(value: float) -> str:
        if row["unit"] == "$":
            return f"{currency}{value:,.0f}"
        if row["unit"] in ("share", "a year"):
            return f"{value * 100:.1f}%"
        return f"{value:,.4g}"

    if abs(row["high"] - row["low"]) < 1e-9:
        return html.escape(one(row["low"]))
    return html.escape(f"{one(row['low'])} – {one(row['high'])}")


def write(
    spec: finmodel.ModelSpec, prospect: dict[str, Any], currency: str = "$",
    footnote: str = "", directory: Path | None = None,
) -> Path:
    """Compile and write one company's dashboard. Returns the path written."""
    path = output_path(prospect, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        compile_page(spec, str(prospect.get("company_name") or "this company"),
                     currency, footnote),
        encoding="utf-8")
    return path
