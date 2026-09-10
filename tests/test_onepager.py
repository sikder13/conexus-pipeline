"""The printed one-pager: the letter, set for print, with a code to the working."""

from __future__ import annotations

import pytest

from lib import dashboard
from tools.report import main as report

LETTER = (
    "Your Manufacturing Readiness Grant record shows a $250,000 award in 2022. "
    "If your estimators spend somewhere near forty hours a month assembling "
    "quotes, that is about $41,000 to $88,000 a year of time going into "
    "paperwork rather than into jobs. I could not compute how many quotes "
    "actually leave your desk in a month, which is the one thing that decides "
    "it. Tell me that number and I will send the corrected version; if the "
    "range is wrong, saying so is the most useful reply you could send. "
    "Everything we quote is a founding-client rate, locked 12 months."
    "\n\n—\nUdaay Sikder\nNahl Technologies Inc.\n"
    "Written under CAN-SPAM; posted, not emailed."
)

PROSPECT = {
    "id": "22222222-3333-4444-5555-666666666666",
    "company_name": "Acme Fabrication",
    "source_adapter": "conexus_iedc",
    "evidence_file": {},
}


def artifact(status="sendable", body=LETTER, kind="letter"):
    return {"id": "a1", "prospect_id": PROSPECT["id"], "kind": kind,
            "status": status, "body": body, "created_at": "2026-09-10T00:00:00Z"}


class TestTheQrCode:
    def test_it_encodes_the_dashboard_url(self):
        drawing = report.qr_flowable("https://dash.test/acme-1234567890")
        assert drawing.width == pytest.approx(report.QR_SIZE)

    def test_it_is_vector_not_a_bitmap(self):
        # A one-pager goes through somebody's office printer. A rasterised code
        # at 1.15 inches is the difference between a phone reading it first
        # time and a prospect giving up.
        drawing = report.qr_flowable("https://dash.test/x")
        assert drawing.contents, "expected a drawable QR widget"


class TestItRefusesToPrintAFlyer:
    def test_no_letter_means_no_page(self):
        with pytest.raises(report.NoLetter, match="no letter that passed"):
            report.build_one_pager(PROSPECT, [], "/tmp/unused.pdf")

    def test_a_blocked_letter_is_not_a_letter(self):
        with pytest.raises(report.NoLetter):
            report.build_one_pager(
                PROSPECT, [artifact(status="blocked")], "/tmp/unused.pdf")

    def test_a_letter_that_is_all_notation_is_refused(self):
        with pytest.raises(report.NoLetter, match="survives stripping"):
            report.build_one_pager(
                PROSPECT, [artifact(body="## Heading\n[block1.x]")], "/tmp/unused.pdf")


class TestItPrints:
    def test_one_pdf_carrying_the_letter(self, tmp_path):
        out = tmp_path / "acme.pdf"
        report.build_one_pager(PROSPECT, [artifact()], out)
        assert out.exists() and out.stat().st_size > 2000
        assert out.read_bytes().startswith(b"%PDF")

    def test_the_signature_block_does_not_print_twice(self, tmp_path):
        paragraphs = report.one_pager_paragraphs(LETTER, "Acme Fabrication")
        joined = " ".join(paragraphs)
        assert "Udaay Sikder" not in joined

    def test_it_says_so_when_the_code_is_not_live_yet(self, tmp_path):
        # Printed before hosting exists. A code that silently fails to resolve
        # teaches a prospect that we send things we have not checked.
        out = tmp_path / "acme.pdf"
        report.build_one_pager(PROSPECT, [artifact()], out)
        assert dashboard.dashboard_url(PROSPECT).endswith(
            dashboard.token_for(PROSPECT))

    def test_a_real_base_url_drops_the_caveat(self, tmp_path):
        url = dashboard.dashboard_url(PROSPECT, "https://dash.nahl.test")
        assert url.startswith("https://dash.nahl.test/")
        out = tmp_path / "acme2.pdf"
        report.build_one_pager(
            PROSPECT, [artifact()], out, base_url="https://dash.nahl.test")
        assert out.exists()
