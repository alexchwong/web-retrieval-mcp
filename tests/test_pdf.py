"""PDF extraction with pypdfium2 and without the extra (Section 15, test 6).

The fixture PDFs in testdata/pdf/ are described in testdata/README.md.
"""
from __future__ import annotations

from dataclasses import replace
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import webspell
from webspell import extract, outbound, rank, search
from webspell.settings import DEFAULT_SETTINGS

from tests.helpers import LoopbackServer, loopback_documents, wait_idle
from tests.test_call import html_handler

PDFS = Path(__file__).with_name("testdata") / "pdf"
HAVE_PDF = importlib.util.find_spec("pypdfium2") is not None
FETCH = DEFAULT_SETTINGS.fetch


def pdf(name: str) -> outbound.Download:
    return outbound.Download("fetched", data=(PDFS / name).read_bytes(), kind="fetched_pdf",
                             final=outbound.parse_url(f"https://example.org/{name}"))


@unittest.skipUnless(HAVE_PDF, "needs the pdf extra")
class PdfTests(unittest.TestCase):
    def test_page_order_numbers_and_ranking(self):
        doc = extract.extract(pdf("basic.pdf"), FETCH)
        self.assertEqual(doc.status, "fetched")
        self.assertEqual(doc.pages_examined, 3)
        self.assertFalse(doc.truncated)
        self.assertEqual([page for page, _ in doc.units], [1, 2, 3])
        self.assertEqual([text for _, text in doc.units], ["alpha evidence", "", "omega evidence"])
        sources = search.supplied(["https://example.org/file.pdf"], 8)
        passages = rank.rank("omega", sources, [doc], DEFAULT_SETTINGS, 8)
        self.assertEqual({p["page"] for p in passages}, {1, 3})

    def test_multi_column_and_non_latin(self):
        columns = extract.extract(pdf("multicolumn.pdf"), FETCH)
        text = columns.units[0][1]
        for line in ("Left column line one", "Right column line three"):
            self.assertIn(line, text)
        self.assertNotIn("\r", text)
        scripts = extract.extract(pdf("nonlatin.pdf"), FETCH).units[0][1]
        for sample in ("中文证据测试", "Ελληνικά κείμενο", "Русский текст"):
            self.assertIn(sample, scripts)

    def test_unusable_pdfs_fail_extraction(self):
        for name in ("blank.pdf", "scanned.pdf", "encrypted.pdf", "malformed.pdf"):
            with self.subTest(name=name):
                doc = extract.extract(pdf(name), FETCH)
                self.assertEqual(doc.status, "extract_failed")
                self.assertNotIn("PDFium", doc.error)

    def test_page_and_character_limits(self):
        limited = extract.extract(pdf("fifty_pages.pdf"), FETCH)
        self.assertEqual(limited.pages_examined, 40)
        self.assertTrue(limited.truncated)
        self.assertEqual(limited.units[-1], (40, "page 40 evidence"))
        one = extract.extract(pdf("basic.pdf"), replace(FETCH, max_pdf_pages=1))
        self.assertTrue(one.truncated)
        self.assertEqual(one.pages_examined, 1)
        tiny = extract.extract(pdf("basic.pdf"), replace(FETCH, max_document_chars=5))
        self.assertTrue(tiny.truncated)
        self.assertEqual(tiny.units[0][1], "alpha")

    def test_six_concurrent_pdf_sources(self):
        names = ["basic.pdf", "multicolumn.pdf", "nonlatin.pdf", "fifty_pages.pdf", "basic.pdf", "nonlatin.pdf"]

        def handler(exchange):
            exchange.respond(200, (PDFS / exchange.path.rsplit("/", 1)[-1]).read_bytes(),
                             {"Content-Type": "application/pdf"})

        with LoopbackServer(handler) as fixture, loopback_documents():
            result = webspell.call("web_evidence_passages", {
                "query": "evidence column", "urls": [f"{fixture.url}/{i}/{name}" for i, name in enumerate(names)]},
                settings=webspell.Settings())
        self.assertEqual(result["status"], "complete")
        self.assertEqual([s["fetch_status"] for s in result["sources"]], ["fetched"] * 6)
        self.assertTrue(all(p["origin"] == "fetched_pdf" and p["page"] >= 1 for p in result["passages"]))
        self.assertFalse(any(wait_idle().values()))

    def test_import_does_not_pull_pillow_or_numpy(self):
        code = "import sys, pypdfium2; print(sorted(m for m in ('PIL', 'numpy') if m in sys.modules))"
        self.assertEqual(subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                                        text=True, check=True).stdout.strip(), "[]")


class WithoutPdfExtraTests(unittest.TestCase):
    def test_pdf_unavailable_and_html_unaffected(self):
        def handler(exchange):
            if exchange.path.endswith(".pdf"):
                exchange.respond(200, (PDFS / "basic.pdf").read_bytes(), {"Content-Type": "application/pdf"})
            else:
                html_handler(exchange)

        with LoopbackServer(handler) as fixture, loopback_documents(), patch.object(extract, "pdfium", lambda: None):
            result = webspell.call("web_evidence_passages", {
                "query": "evidence", "urls": [f"{fixture.url}/doc.pdf", f"{fixture.url}/page"]},
                settings=webspell.Settings())
            page = webspell.call("web_page_text", {"url": f"{fixture.url}/doc.pdf"}, settings=webspell.Settings())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["sources"][0]["fetch_status"], "pdf_unavailable")
        self.assertEqual(result["sources"][1]["fetch_status"], "fetched")
        self.assertTrue(result["passages"])
        self.assertEqual((page["status"], page["fetch_status"], page["error"]),
                         ("failed", "pdf_unavailable", "PDF support is not installed"))
        self.assertFalse(any(wait_idle().values()))


if __name__ == "__main__":
    unittest.main()
