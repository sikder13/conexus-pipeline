"""The compiled calculator, and the one property that makes it worth shipping.

`lib/finmodel.py` says the spec is the logic because otherwise "the calculator
and the document disagree about the business". That is not a claim you can make
in a docstring and leave there, so the central test here runs the page's own
JavaScript evaluator under Node against the same spec Python evaluated and
requires the two to agree to the cent.

Where Node is not installed the test skips rather than passing quietly — a
correctness property that silently stops being checked is worse than one nobody
claimed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from lib import anchors, dashboard, finmodel, offermodels

NODE = shutil.which("node")

PROSPECT = {
    "id": "11111111-2222-3333-4444-555555555555",
    "company_name": "Accutech Mold & Machine",
    "employee_estimate": "62",
    "employee_source": "[T1] their about page",
}


def spec_for(pattern: str = "quoting_velocity", engagement: str = "diagnostic"):
    anchor = anchors.anchor_for(PROSPECT, offermodels.BY_PATTERN[pattern])
    return offermodels.build_spec(pattern, PROSPECT, engagement, anchor=anchor)


class TestTheTokenIsStableAndReadable:
    def test_the_same_company_always_gets_the_same_token(self):
        assert dashboard.token_for(PROSPECT) == dashboard.token_for(dict(PROSPECT))

    def test_it_carries_the_company_name(self):
        assert dashboard.token_for(PROSPECT).startswith("accutech-mold-machine-")

    def test_two_companies_do_not_collide(self):
        other = {**PROSPECT, "id": "99999999-2222-3333-4444-555555555555"}
        assert dashboard.token_for(other) != dashboard.token_for(PROSPECT)

    def test_the_placeholder_base_cannot_resolve(self):
        from lib.config import DASHBOARD_PLACEHOLDER

        # RFC 2606 reserves .invalid, so a QR code built on the default fails
        # visibly rather than pointing at somebody else's future domain.
        assert DASHBOARD_PLACEHOLDER.endswith(".invalid")
        assert dashboard.dashboard_url(PROSPECT).startswith(DASHBOARD_PLACEHOLDER)


class TestThePageIsSelfContained:
    def test_no_external_resource_of_any_kind(self):
        page = dashboard.compile_page(spec_for(), "Accutech Mold & Machine")
        assert not re.findall(r'(?:src|href)="(?!#)[^"]*"', page)
        assert "http://" not in page.replace("http://www.w3.org", "")

    def test_it_says_who_it_was_prepared_for(self):
        page = dashboard.compile_page(spec_for(), "Accutech Mold & Machine")
        assert "Prepared for Accutech Mold &amp; Machine" in page

    def test_the_company_name_cannot_break_out_of_the_page(self):
        """Company names come from listings and crawled pages, not from us."""
        hostile = '</script><script>alert(1)</script>'
        page = dashboard.compile_page(spec_for(), hostile)
        assert "<script>alert(1)</script>" not in page
        assert "&lt;/script&gt;" in page          # escaped in the visible HTML
        assert "\\u003c/script" in page           # escaped inside the JSON too

    def test_the_escaped_payload_is_still_valid_json(self):
        page = dashboard.compile_page(spec_for(), '<b>Acme & Sons</b>')
        blob = re.search(r"var MODEL = (\{.*?\});", page, re.S).group(1)
        assert json.loads(blob)["company"] == "<b>Acme & Sons</b>"

    def test_the_footer_carries_the_citations(self):
        spec = spec_for()
        page = dashboard.compile_page(spec, "Accutech Mold & Machine")
        for line in dashboard.citations(spec):
            assert line[:40] in page

    def test_a_claim_path_never_reaches_the_prospect(self):
        # An evidence path is an internal address. It means nothing to a reader
        # and tells them we keep a file on them.
        page = dashboard.compile_page(spec_for(), "Accutech Mold & Machine")
        assert "block8_financial_scale" not in page
        assert "evidence_file" not in page


class TestSlidersAreAssumptionsOnly:
    def test_every_slider_is_something_we_guessed(self):
        spec = spec_for()
        names = {s["name"] for s in dashboard._slider_inputs(spec)}
        for name in names:
            assert spec.inputs[name].provenance.is_assumption, name

    def test_a_figure_they_published_is_not_draggable(self):
        # A wage they posted is evidence. A page that let a reader drag it would
        # be offering to edit the record rather than to correct our guess.
        wage_spec = spec_for()
        fixed = {row["name"] for row in dashboard._fixed_inputs(wage_spec)}
        sliders = {s["name"] for s in dashboard._slider_inputs(wage_spec)}
        assert fixed and not (fixed & sliders)

    def test_the_travel_goes_beyond_the_range_we_guessed(self):
        for slider in dashboard._slider_inputs(spec_for()):
            assert slider["max"] > slider["high"]

    def test_every_assumption_gets_one(self):
        spec = spec_for()
        assumed = {n for n, i in spec.inputs.items() if i.provenance.is_assumption}
        assert {s["name"] for s in dashboard._slider_inputs(spec)} == assumed


class TestThereIsNoPerCompanyJavaScript:
    def test_the_evaluator_is_identical_for_every_company(self):
        one = dashboard.compile_page(spec_for(), "One")
        two = dashboard.compile_page(
            spec_for("qa_documentation_labour", "scoped_build"), "Two")
        assert dashboard.EVALUATOR_JS in one
        assert dashboard.EVALUATOR_JS in two

    def test_the_only_thing_that_differs_is_the_data(self):
        one = dashboard.compile_page(spec_for(), "One")
        script_blocks = re.findall(r"<script>(.*?)</script>", one, re.S)
        # Three blocks: the data, the shared evaluator, the shared renderer.
        assert len(script_blocks) == 3
        assert script_blocks[0].startswith("var MODEL = {")
        assert script_blocks[1].strip() == dashboard.EVALUATOR_JS.strip()
        assert script_blocks[2].strip() == dashboard.RENDER_JS.strip()

    def test_the_shared_code_reaches_a_name_only_through_a_role(self):
        """The role indirection is what keeps the renderer company-agnostic.

        `MODEL.roles.monthly_saving` is a role, declared in the spec, and the
        renderer reading it is the mechanism working. What it may never do is
        name a quantity directly, because that is a line of JavaScript about one
        company's business.
        """
        spec = spec_for()
        shared = dashboard.EVALUATOR_JS + re.sub(
            r"MODEL\.roles\.\w+", "", dashboard.RENDER_JS)
        for name in list(spec.inputs) + list(spec.formulas):
            assert name not in shared, name


@pytest.mark.skipif(NODE is None, reason="node is not installed")
class TestTheCalculatorAgreesWithTheEngine:
    """The property the whole design rests on, checked rather than asserted."""

    def _js_values(self, spec, scenario, tmp_path: Path) -> dict[str, list[float]]:
        data = dashboard.page_data(spec, "Accutech Mold & Machine")
        script = tmp_path / "run.js"
        script.write_text(
            f"var MODEL = {json.dumps(data)};\n"
            + dashboard.EVALUATOR_JS
            + textwrap.dedent(f"""
            var values = {{}};
            Object.keys(MODEL.inputs).forEach(function (name) {{
              var base = MODEL.inputs[name];
              var over = (MODEL.scenarios[{scenario!r}] || {{}})[name];
              var pair = over ? over : [base.low, base.high];
              values[name] = iv(pair[0], pair[1]);
            }});
            var out = evaluateAll(values);
            var flat = {{}};
            Object.keys(out).forEach(function (k) {{
              flat[k] = [out[k].low, out[k].high];
            }});
            console.log(JSON.stringify(flat));
            """),
            encoding="utf-8")
        result = subprocess.run(
            [NODE, str(script)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    @pytest.mark.parametrize("scenario", finmodel.SCENARIOS)
    @pytest.mark.parametrize(
        "pattern,engagement",
        [("quoting_velocity", "diagnostic"),
         ("qa_documentation_labour", "scoped_build"),
         (offermodels.CAPITAL_PATTERN, "diagnostic")],
    )
    def test_every_formula_matches_to_the_cent(
        self, pattern, engagement, scenario, tmp_path
    ):
        if pattern == offermodels.CAPITAL_PATTERN:
            row = {**PROSPECT, "employee_estimate": None, "employee_source": None,
                   "grant_amount": 250_000.0}
            anchor = anchors.anchor_for(
                row, offermodels.BY_PATTERN["quoting_velocity"])
            spec = offermodels.build_spec(pattern, row, engagement, anchor=anchor)
        else:
            spec = spec_for(pattern, engagement)

        js = self._js_values(spec, scenario, tmp_path)
        evaluated = finmodel.evaluate(spec, scenario)
        for name in spec.formulas:
            expected = evaluated.value(name)
            low, high = js[name]
            assert low == pytest.approx(expected.low, rel=1e-9, abs=1e-6), name
            assert high == pytest.approx(expected.high, rel=1e-9, abs=1e-6), name

    def test_a_moved_slider_moves_the_answer_the_same_way(self, tmp_path):
        """A prospect dragging an input must get what Python would compute."""
        spec = spec_for()
        moved = 99.0
        data = dashboard.page_data(spec, "Accutech Mold & Machine")
        script = tmp_path / "moved.js"
        script.write_text(
            f"var MODEL = {json.dumps(data)};\n"
            + dashboard.EVALUATOR_JS
            + textwrap.dedent(f"""
            var values = {{}};
            Object.keys(MODEL.inputs).forEach(function (name) {{
              var base = MODEL.inputs[name];
              values[name] = iv(base.low, base.high);
            }});
            values["volume_per_month"] = iv({moved}, {moved});
            var out = evaluateAll(values);
            console.log(JSON.stringify([out.annual_saving.low, out.annual_saving.high]));
            """),
            encoding="utf-8")
        result = subprocess.run(
            [NODE, str(script)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        low, high = json.loads(result.stdout)

        overridden = spec.model_copy(update={"inputs": {
            **spec.inputs,
            "volume_per_month": spec.inputs["volume_per_month"].model_copy(
                update={"value": finmodel.Interval.of(moved)}),
        }})
        expected = finmodel.evaluate(
            overridden.model_copy(update={"scenarios": {}})).value("annual_saving")
        assert low == pytest.approx(expected.low, rel=1e-9)
        assert high == pytest.approx(expected.high, rel=1e-9)


class TestWriting:
    def test_it_writes_where_the_token_says(self, tmp_path):
        path = dashboard.write(spec_for(), PROSPECT, directory=tmp_path)
        assert path.name == f"{dashboard.token_for(PROSPECT)}.html"
        assert path.read_text(encoding="utf-8").startswith("<!doctype html>")
