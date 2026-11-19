# PDF engine side-by-side report (0.2.0)

One-off report attached to the 0.2.0 release notes (specification Section 9). For each
fixture in `tests/testdata/pdf/` it shows the text that web-retrieval-mcp 0.1.1's PyMuPDF
path produced (`page.get_text("text")`, PyMuPDF 2.1) next to the text webSpell 0.2.0
produces with pypdfium2 5.15.2 (`get_text_bounded()`, line endings normalised to `\n`).
The gate for the engine change is the fixture tests (`tests/test_pdf.py`), not parity.

`«…»` is one page, `⏎` separates pages and `↵` is a line break inside a page.
"Text" compares the pages after removing PyMuPDF's trailing line break.

| Fixture | Content | 0.1.1 (PyMuPDF) | 0.2.0 (pypdfium2) | Outcome 0.1.1 → 0.2.0 | Text |
| --- | --- | --- | --- | --- | --- |
| `basic.pdf` | Three pages: text, blank, text (page provenance). | «alpha evidence↵» ⏎ «» ⏎ «omega evidence↵» | «alpha evidence» ⏎ «» ⏎ «omega evidence» | fetched → fetched | same |
| `multicolumn.pdf` | Two columns of three lines each. | «Left column line one↵Right column line one↵Left column line two↵Right column line two↵Left column line three↵Right column line three↵» | «Left column line one Right column line one↵Left column line two Right column line two↵Left column line three Right column line three» | fetched → fetched | differs |
| `nonlatin.pdf` | Chinese, Greek and Cyrillic lines (embedded fonts). | «中文证据测试↵Ελληνικά κείμενο↵Русский текст↵» | «中文证据测试↵Ελληνικά κείμενο↵Русский текст» | fetched → fetched | same |
| `blank.pdf` | One empty page. | «» | extract_failed: document has no extractable text | extract_failed → extract_failed | same |
| `scanned.pdf` | One page holding only an image (no text layer). | «» | extract_failed: document has no extractable text | extract_failed → extract_failed | same |
| `encrypted.pdf` | AES-256, user password required. | encrypted PDF | extract_failed: document extraction failed | extract_failed → extract_failed | same |
| `fifty_pages.pdf` | Fifty one-line pages (page-limit truncation; first three shown). | «page 1 evidence↵» ⏎ «page 2 evidence↵» ⏎ «page 3 evidence↵» | «page 1 evidence» ⏎ «page 2 evidence» ⏎ «page 3 evidence» | fetched → fetched | same |
| `malformed.pdf` | Truncated, not a valid PDF. | FileDataError | extract_failed: document extraction failed | extract_failed → extract_failed | same |

## Differences found

- **Trailing line break.** PyMuPDF ends every non-empty page with a line break; pypdfium2
  does not. Passages are built from whitespace-split words, so ranking input is unchanged.
  `web_page_text` output has one fewer line break per page.
- **Multi-column reading order.** PyMuPDF emits each text line in drawing order (a left-column
  line, then the right-column line, alternating). PDFium joins text that shares a baseline, so a
  left-column line and the right-column line beside it become one line. The words are the same;
  their order across columns differs. This is the reading-order change Section 9 anticipated.
- **Spacing.** No other spacing difference on these fixtures.
- **Failures.** Blank, image-only, encrypted and malformed PDFs give `extract_failed` with both
  engines. 0.1.1 reported the encrypted case as an extraction failure too.

Fixture construction is described in `tests/testdata/README.md`. PyMuPDF is not a webSpell
dependency; it was installed in a throwaway environment only to produce the left-hand column.
