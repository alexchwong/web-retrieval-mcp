"""HTML and PDF text extraction.

trafilatura is imported on the first HTML document (it reads its configuration file
when imported) and pypdfium2 on the first PDF, so ``import webspell`` opens no file.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib
from typing import Any


from . import outbound
from .settings import FetchSettings

_pdfium: Any = None
_pdfium_checked = False


@dataclass(frozen=True)
class Document:
    status: str
    error: str = ""
    final: outbound.URL | None = None
    origin: str = ""
    units: tuple[tuple[int | None, str], ...] = ()
    truncated: bool = False
    input_capped: bool = False
    pages_examined: int | None = None

    def metadata(self, initial: outbound.URL) -> dict[str, Any]:
        result: dict[str, Any] = {"fetch_status": self.status}
        if self.status == "fetched":
            result["truncated"] = self.truncated
            if self.final and self.final.key != initial.key:
                result["final_url"] = self.final.visible
        if self.input_capped:
            result["html_input_capped"] = True
        return result


TIMEOUT = Document("timeout", "document retrieval timed out")
PDF_UNAVAILABLE = Document("pdf_unavailable", "PDF support is not installed")


def pdfium() -> Any:
    """Return the pypdfium2 module, or None when the ``pdf`` extra is not installed."""
    global _pdfium, _pdfium_checked
    if not _pdfium_checked:
        try:
            _pdfium = importlib.import_module("pypdfium2")
        except ImportError:
            _pdfium = None
        _pdfium_checked = True
    return _pdfium


def pdf_available() -> bool:
    return pdfium() is not None


def _pdf_units(data: bytes, settings: FetchSettings) -> tuple[list[tuple[int | None, str]], bool, int]:
    """Extract up to ``max_pdf_pages`` pages and ``max_document_chars`` characters.

    Must run in the single PDF parser slot: PDFium is not safe for concurrent use.
    Text pages, pages and the document are closed explicitly, in that order.
    """
    module = pdfium()
    if module is None:  # pragma: no cover - guarded by the caller
        raise RuntimeError("PDF support is not installed")
    cap = settings.max_document_chars
    remaining = cap
    pages = 0
    units: list[tuple[int | None, str]] = []
    truncated = False
    pdf = module.PdfDocument(data)
    try:
        count = len(pdf)
        for index in range(min(count, settings.max_pdf_pages)):
            if remaining <= 0:
                break
            page = pdf[index]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_bounded()
                finally:
                    textpage.close()
            finally:
                page.close()
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            pages += 1
            remaining -= bool(units)
            units.append((index + 1, text[:remaining]))
            if len(text) > remaining:
                truncated = True
            remaining -= len(text)
        truncated = truncated or pages < count
    finally:
        pdf.close()
    return units, truncated, pages


def extract(download: outbound.Download, settings: FetchSettings) -> Document:
    """Extract text from a fetched document. Runs in a parser thread."""
    cap = settings.max_document_chars
    capped = False
    pages = None
    units: list[tuple[int | None, str]] = []
    try:
        if download.kind == "fetched_pdf":
            units, truncated, pages = _pdf_units(download.data, settings)
        else:
            import trafilatura

            html = download.data.decode(download.encoding, errors="replace")
            capped = len(html) > cap * 4
            text = trafilatura.extract(html[:cap * 4], include_comments=False,
                                       include_tables=True, favor_recall=False, no_fallback=False) or ""
            truncated = len(text) > cap
            units.append((None, text[:cap]))
    except Exception:
        return Document("extract_failed", "document extraction failed", input_capped=capped)
    if not any(text.strip() for _, text in units):
        return Document("extract_failed", "document has no extractable text", input_capped=capped)
    return Document("fetched", final=download.final, origin=download.kind, units=tuple(units),
                    truncated=truncated, input_capped=capped, pages_examined=pages)
