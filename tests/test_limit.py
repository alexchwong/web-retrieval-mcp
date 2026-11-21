"""The result byte limit (Section 15, test 8)."""
from __future__ import annotations

from dataclasses import replace
import json
import unittest
from unittest.mock import AsyncMock, patch

import webspell
from webspell import api, limit, outbound, search
from webspell.search import Source

from tests.helpers import settings

TITLE = "Title " + "標題😀" * 98            # 300 characters
SNIPPET = "Snippet " + "摘要🚀é" * 197 + "x"  # 600 characters


def cjk_emoji(key):
    """A distinct CJK, emoji and Latin document per URL (identical chunks would be deduplicated)."""
    tag = f"src{abs(hash(key)) % 100_000}"
    return " ".join(("証拠" if i % 3 else "evidence") + ("😀" if i % 5 == 0 else "") + f" {tag}{i % 7}"
                    for i in range(2000))


def search_sources(urls):
    return [Source(outbound.parse_url(url), {"title": TITLE, "snippet": SNIPPET, "origin": "search_snippet"})
            for url in urls]


def limited(max_result_bytes=None, ascii_json=False, **extra):
    return settings(search={"searxng_url": "http://127.0.0.1:9"},
                    output={"max_result_bytes": max_result_bytes, "ascii_json": ascii_json}, **extra)


def documents(texts):
    """Patch downloads so each URL's document text is ``texts(url)``."""
    async def download(url, *_args):
        return outbound.Download("fetched", final=url, kind="fetched_page", data=url.key.encode())

    def extract_text(html, **_kwargs):
        return texts(html)

    return patch.object(outbound, "download", side_effect=download), patch("trafilatura.extract", side_effect=extract_text)


class LimitTests(unittest.TestCase):
    def check_invariants(self, result, unlimited, size_limit, ascii_json):
        encoded = json.dumps(result, ensure_ascii=ascii_json)
        self.assertLessEqual(webspell.result_bytes(result, ascii=ascii_json), size_limit)
        self.assertEqual(json.loads(encoded), result)
        encoded.encode("utf-8")  # strict: no lone surrogates
        if result.get("reason") == "output_limit":
            self.assertEqual(result, limit.output_limit())
            return
        if result != unlimited:
            self.assertTrue(result["output_capped"])
        for field in ("mode", "search", "status", "reason", "note"):
            if field in unlimited:
                self.assertEqual(result[field], unlimited[field])
        urls = {source["url"] for source in unlimited.get("sources", [])}
        sources = {source["source_id"]: source for source in result.get("sources", [])}
        for source in sources.values():
            self.assertIn("fetch_status", source)
            self.assertIn(source["url"], urls, "a URL is never shortened")
        self.assertEqual(len(result.get("sources", [])) + result.get("sources_omitted", 0),
                         len(unlimited.get("sources", [])))
        if "passages" in unlimited:
            self.assertEqual(len(result["passages"]) + result.get("passages_omitted", 0),
                             len(unlimited["passages"]))
            original = {(p["source_id"], p["text"][:50]): p for p in unlimited["passages"]}
            for passage in result["passages"]:
                self.assertIn(passage["source_id"], sources, "a retained passage keeps its source")
                full = original[(passage["source_id"], passage["text"][:50])]
                self.assertEqual((passage["url"], passage.get("page")), (full["url"], full.get("page")))
                if passage.get("text_capped"):
                    self.assertGreaterEqual(len(passage["text"]), 200)
                    self.assertTrue(full["text"].startswith(passage["text"]))
                else:
                    self.assertEqual(passage["text"], full["text"])

    def run_tool(self, tool, arguments, output_settings):
        return webspell.call(tool, arguments, settings=output_settings)

    def test_every_tool_within_limit(self):
        urls = [f"https://example{i}.org/" + "path/" * (i * 40) for i in range(8)]
        download, extract = documents(cjk_emoji)
        outcome = search.SearchOutcome(search_sources(urls), None, {})
        with patch.object(search, "searxng", AsyncMock(return_value=outcome)), download, extract:
            for size_limit in (4096, 32768):
                for ascii_json in (False, True):
                    cases = {
                        "web_search_results": {"query": "evidence 証拠"},
                        "web_evidence_passages": {"query": "evidence 証拠", "max_passages": 20},
                        "web_evidence_passages urls": {"query": "evidence 証拠", "urls": urls,
                                                       "max_passages": 20},
                        "web_page_text": {"url": urls[-1]},
                    }
                    for name, arguments in cases.items():
                        tool = name.split()[0]
                        with self.subTest(limit=size_limit, ascii=ascii_json, case=name):
                            unlimited = self.run_tool(tool, arguments, limited())
                            if tool == "web_evidence_passages":
                                self.assertEqual(len(unlimited["passages"]), 16)
                            result = self.run_tool(tool, arguments, limited(size_limit, ascii_json))
                            self.check_invariants(result, unlimited, size_limit, ascii_json)
                            self.assertNotEqual(result.get("reason"), "output_limit")

    def test_eight_sources_at_minimum_limit_keep_a_passage(self):
        urls = [f"https://example{i}.org/article" for i in range(8)]
        download, extract = documents(cjk_emoji)
        outcome = search.SearchOutcome(search_sources(urls), None, {})
        with patch.object(search, "searxng", AsyncMock(return_value=outcome)), download, extract:
            for ascii_json in (False, True):
                result = self.run_tool("web_evidence_passages", {"query": "evidence"}, limited(4096, ascii_json))
                self.assertGreaterEqual(len(result["passages"]), 1)
                self.assertNotEqual(result.get("reason"), "output_limit")

    def test_top_passage_with_huge_url_is_skipped(self):
        long_url = "https://example.org/" + "é" * 2300  # 2,320 characters, over 4,500 bytes
        self.assertGreater(len(long_url.encode()), 4500)
        short_url = "https://example.net/short"
        other_url = "https://example.com/other"
        # A third document keeps BM25's IDF for "target" positive, so the long URL ranks first.
        texts = {outbound.parse_url(long_url).key: "target " * 40,
                 outbound.parse_url(short_url).key: "target other " * 20,
                 outbound.parse_url(other_url).key: "unrelated words " * 20}
        download, extract = documents(lambda key: texts[key])
        arguments = {"query": "target", "urls": [long_url, short_url, other_url]}
        with download, extract:
            unlimited = self.run_tool("web_evidence_passages", arguments, limited())
            self.assertEqual([p["url"] for p in unlimited["passages"]], [long_url, short_url, other_url])
            result = self.run_tool("web_evidence_passages", arguments, limited(4096))
        self.check_invariants(result, unlimited, 4096, False)
        self.assertEqual(result["passages"][0]["url"], short_url)
        self.assertEqual(result["passages"][0]["text"], unlimited["passages"][1]["text"])
        self.assertNotIn("text_capped", result["passages"][0])
        self.assertNotIn(long_url, [s["url"] for s in result["sources"]])
        self.assertEqual((result["passages_omitted"], result["sources_omitted"]), (1, 1))

    def test_passage_cut_to_at_least_200_characters(self):
        url = "https://example.org/long"
        download, extract = documents(lambda key: "evidence " * 2000)
        wide = limited(4096, chunk={"words": 2000, "overlap": 0})
        with download, extract:
            unlimited = self.run_tool("web_evidence_passages", {"query": "evidence", "urls": [url]}, replace(
                wide, output=replace(wide.output, max_result_bytes=None)))
            result = self.run_tool("web_evidence_passages", {"query": "evidence", "urls": [url]}, wide)
        self.check_invariants(result, unlimited, 4096, False)
        self.assertTrue(result["passages"][0]["text_capped"])
        self.assertGreater(len(result["passages"][0]["text"]), 3000)

    def test_search_results_skip_a_source_with_a_huge_url(self):
        urls = ["https://example.org/" + "é" * 2300] + [f"https://example{i}.org/" for i in range(7)]
        plain = [Source(outbound.parse_url(url), {"title": "A title", "snippet": "A snippet " * 30,
                                                  "origin": "search_snippet"}) for url in urls]
        with patch.object(search, "searxng", AsyncMock(return_value=search.SearchOutcome(plain, None, {}))):
            unlimited = self.run_tool("web_search_results", {"query": "q"}, limited())
            result = self.run_tool("web_search_results", {"query": "q"}, limited(4096))
        self.check_invariants(result, unlimited, 4096, False)
        self.assertEqual(result["sources_omitted"], 1)
        self.assertEqual([s["url"] for s in result["sources"]], urls[1:])

    def test_every_url_too_long_is_output_limit(self):
        urls = [f"https://example{i}.org/" + "é" * 2300 for i in range(3)]
        outcome = search.SearchOutcome(search_sources(urls), None, {})
        with patch.object(search, "searxng", AsyncMock(return_value=outcome)):
            self.assertEqual(self.run_tool("web_search_results", {"query": "q"}, limited(4096)),
                             limit.output_limit())
        download, extract = documents(lambda key: "evidence " * 50)
        with download, extract:
            self.assertEqual(self.run_tool("web_evidence_passages", {"query": "evidence", "urls": urls},
                                           limited(4096)), limit.output_limit())

    def test_page_text_must_advance(self):
        text = "数据 evidence 😀 " * 400
        download, extract = documents(lambda key: text)

        def page(url, start, ascii_json):
            return self.run_tool("web_page_text", {"url": url, "start": start}, limited(4096, ascii_json))

        with download, extract:
            no_room = "https://example.org/" + "é" * 2100
            self.assertEqual(page(no_room, 0, False), limit.output_limit())
            for ascii_json in (False, True):
                # The longest URL that still leaves room for some text: only a few characters fit.
                low, high = 0, 4000
                while low < high:
                    middle = (low + high + 1) // 2
                    if page("https://example.org/" + "é" * middle, 0, ascii_json)["status"] == "complete":
                        low = middle
                    else:
                        high = middle - 1
                self.assertEqual(page("https://example.org/" + "é" * (low + 1), 0, ascii_json),
                                 limit.output_limit())
                # A few characters' margin, so later pages (whose offsets take more digits)
                # still have room for at least one character.
                url = "https://example.org/" + "é" * (low - 8)
                pieces, start, pages = [], 0, 0
                while start is not None:
                    result = page(url, start, ascii_json)
                    self.assertEqual(result["status"], "complete", result)
                    self.assertTrue(result.get("output_capped") or result["next_start"] is None)
                    self.assertLessEqual(webspell.result_bytes(result, ascii=ascii_json), 4096)
                    if result["next_start"] is not None:
                        self.assertGreater(result["next_start"], start)
                    pieces.append(result["text"])
                    start = result["next_start"]
                    pages += 1
                    self.assertLess(pages, len(text) + 1)
                self.assertGreater(pages, 100)
                self.assertLess(len(pieces[0]), 40, "only a few characters fit per page")
                self.assertEqual("".join(pieces), text)

    def test_page_text_pieces_join_with_and_without_limit(self):
        text = "Ünïcödé 数据 😀 evidence " * 1500
        download, extract = documents(lambda key: text)
        with download, extract:
            whole = self.run_tool("web_page_text", {"url": "https://example.org/"}, limited())
            self.assertEqual(set(whole) - {"total_chars", "start", "next_start"},
                             {"url", "fetch_status", "truncated", "status", "source_id", "origin", "text", "chars"})
            self.assertNotIn("output_capped", whole)
            for output in (limited(), limited(8192), limited(8192, True)):
                pieces, start = [], 0
                while start is not None:
                    page = self.run_tool("web_page_text", {"url": "https://example.org/", "start": start,
                                                           "max_chars": 5000}, output)
                    pieces.append(page["text"])
                    start = page["next_start"]
                self.assertEqual("".join(pieces), whole["text"])

    def test_no_passages_keeps_sources_in_order(self):
        urls = [f"https://example{i}.org/" + "p" * 900 for i in range(8)]
        failed = outbound.Download("http_error", "document server returned an HTTP error")
        with patch.object(search, "searxng", AsyncMock(return_value=search.SearchOutcome(search_sources(urls), None, {}))), \
                patch.object(outbound, "download", AsyncMock(return_value=failed)):
            unlimited = self.run_tool("web_evidence_passages", {"query": "q"}, limited())
            result = self.run_tool("web_evidence_passages", {"query": "q"}, limited(4096))
        self.assertEqual((unlimited["reason"], unlimited["note"]), ("no_evidence", "no pages fetched; snippets only"))
        self.check_invariants(result, unlimited, 4096, False)
        self.assertGreater(result["sources_omitted"], 0)
        self.assertEqual([s["url"] for s in result["sources"]], urls[:len(result["sources"])])

    def test_measure_is_the_published_definition(self):
        value = {"a": "é😀", "b": [1, 2]}
        self.assertEqual(webspell.result_bytes(value), len(json.dumps(value, ensure_ascii=False).encode()))
        self.assertEqual(webspell.result_bytes(value, ascii=True), len(json.dumps(value).encode()))
        self.assertEqual(limit.apply("web_page_text", {"x": "y" * 10_000}, limited().output), {"x": "y" * 10_000})


if __name__ == "__main__":
    unittest.main()
