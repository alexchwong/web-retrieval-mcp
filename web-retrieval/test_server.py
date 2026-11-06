"""Offline MCP contracts, search filters, extraction and ranking."""
import asyncio
from dataclasses import replace
from datetime import date
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import pymupdf

import outbound
import server
from settings import DEFAULT_SETTINGS


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, tool, **arguments):
        result = await server.mcp.call_tool(tool, arguments)
        self.assertFalse(result.is_error, str(result))
        return result.structured_content

    async def test_expected_mcp_tools_registered(self):
        tools = await server.mcp.list_tools()
        self.assertEqual({tool.name for tool in tools},
                         {"web_search_results", "web_evidence_passages", "web_page_text"})

    async def test_page_text_invalid_and_blocked(self):
        result = await self.call("web_page_text", url="file:///etc/passwd")
        self.assertEqual(result["error"], "url must be http(s)")
        self.assertNotIn("fetch_status", result)
        result = await self.call("web_page_text", url="https://user:secret@example.org/")
        self.assertEqual(result["fetch_status"], "blocked")
        self.assertEqual(result["url"], "https://example.org/")

    def test_stateless_http_exposes_mcp_path(self):
        app = server.mcp.http_app(transport="streamable-http", stateless_http=True)
        self.assertIn("/mcp", {getattr(route, "path", None) for route in app.routes})

    def test_recency_and_domain_validation(self):
        cases = [
            ("1w", "week", "native", "2024-03-24"),
            ("7d", "week", "native", "2024-03-24"),
            ("1m", "month", "native", "2024-02-29"),
            ("28d", "month", "native_widened", "2024-03-03"),
            ("29d", "year", "native_widened", "2024-03-02"),
            ("31d", "year", "native_widened", "2024-02-29"),
            ("365d", "year", "native_widened", "2023-04-01"),
            ("12m", "year", "native", "2023-03-31"),
            ("2y", None, "local_dates_only", "2022-03-31"),
        ]
        for value, window, method, cutoff in cases:
            with self.subTest(value=value):
                search = server._search_input("query", value, [" Faß.DE. "], date(2024, 3, 31))
                self.assertEqual(search.query, "query site:xn--fa-hia.de")
                self.assertEqual(search.metadata["time_range"], window)
                self.assertEqual(search.metadata["recency_method"], method)
                self.assertEqual(search.metadata["cutoff_utc"], cutoff)
        for value in ("01w", "0w", "366d", "53w", "6y", "61m", "١w", "1W"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server._search_input("query", value, None, date(2024, 2, 29))
        for value in ("%77ho.int", "exa mple.com", "x_y.example.org", "1.2.3.4", "who.int.."):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server._search_input("query", None, [value], date(2024, 2, 29))
        search = server._search_input("query", "1y", None, date(2024, 2, 29))
        self.assertEqual(search.metadata["cutoff_utc"], "2023-02-28")

    async def test_discovery_filters_without_fetch_or_dns(self):
        payload = {"results": [
            {"url": "https://evil-example.org", "title": "outside"},
            {"url": "https://example.org/a", "publishedDate": "2000-01-01"},
            {"url": "https://example.org/a#ref", "publishedDate": "2099-01-01T02:30:00+03:00",
             "content": "s" * 900},
            {"url": "https://EXAMPLE.org:443/a", "title": "duplicate"},
            {"url": "https://sub.example.org/b", "publishedDate": "2099-01-01T02:00:00"},
            {"url": "https://user:secret@example.org/c"},
        ]}
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json=payload)

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.object(server.httpx, "AsyncClient", return_value=client), patch.object(
            outbound, "download", side_effect=AssertionError("unexpected fetch")
        ):
            result = await self.call("web_search_results", query="query", recency="1w",
                                     domains=["example.org"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.params["time_range"], "week")
        self.assertEqual(requests[0].url.params["q"], "query site:example.org")
        self.assertEqual([s["source_id"] for s in result["sources"]], ["s1", "s2"])
        self.assertEqual(result["sources"][0]["published_at"], "2098-12-31T23:30:00+00:00")
        self.assertEqual(result["sources"][1]["published_at"], "2099-01-01")
        self.assertEqual(len(result["sources"][0]["snippet"]), 600)
        self.assertTrue(all(s["fetch_status"] == "not_attempted" for s in result["sources"]))

    async def test_supplied_urls_order_deduplication_and_no_backend(self):
        async def download(url, *_args):
            if url.credentialed:
                return outbound.Download("blocked", "credentials prohibited")
            return outbound.Download("fetched", final=url, kind="fetched_page", data=url.path.encode())

        with patch.object(server, "_searxng", new_callable=AsyncMock) as search, patch.object(
            outbound, "download", side_effect=download
        ), patch.object(server.trafilatura, "extract", side_effect=lambda html, **_: f"research {html}"):
            result = await self.call("web_evidence_passages", query="research", urls=[
                "https://user:secret@example.org/a", "https://example.org/a",
                "https://EXAMPLE.org:443/a#duplicate", "https://example.org/b",
            ])
        search.assert_not_awaited()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["mode"], "supplied_urls")
        self.assertNotIn("search", result)
        self.assertEqual([s["fetch_status"] for s in result["sources"]], ["blocked", "fetched", "fetched"])
        self.assertTrue(all(s["origin"] == "supplied_url" and "snippet" not in s for s in result["sources"]))
        self.assertNotIn("secret", str(result))
        self.assertEqual({p["source_id"] for p in result["passages"]}, {"s2", "s3"})

    async def test_invalid_supplied_input_never_opens_connection(self):
        cases = [
            {"urls": ["https://example.org"] * 9},
            {"urls": ["https://example.org", "http://bad name"]},
            {"urls": ["https://example.org"], "recency": "1w"},
            {"urls": ["https://example.org"], "domains": ["example.org"]},
        ]
        with patch.object(server, "_searxng", new_callable=AsyncMock) as search, patch.object(
            outbound, "download", new_callable=AsyncMock
        ) as download:
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    result = await self.call("web_evidence_passages", query="query", **arguments)
                    self.assertEqual(result["reason"], "invalid_input")
                    self.assertEqual(result["sources"], [])
            search.assert_not_awaited()
            download.assert_not_awaited()

    async def test_empty_urls_discover_and_backend_failure(self):
        with patch.object(server, "_searxng", new_callable=AsyncMock, return_value=([], "no_results")) as search:
            for arguments in ({}, {"urls": None}, {"urls": []}):
                result = await self.call("web_evidence_passages", query="query", **arguments)
                self.assertEqual(result["mode"], "web_search")
                self.assertEqual(result["reason"], "no_results")
            self.assertEqual(search.await_count, 3)
            search.side_effect = ValueError("sensitive backend detail")
            result = await self.call("web_evidence_passages", query="query")
            self.assertEqual(result["reason"], "search_backend_failed")
            self.assertNotIn("sensitive", str(result))

    async def test_backend_timeout_and_no_evidence(self):
        with patch.object(server, "_searxng", side_effect=asyncio.TimeoutError):
            for tool in ("web_search_results", "web_evidence_passages"):
                result = await self.call(tool, query="query")
                self.assertEqual(result["reason"], "search_backend_failed")
                self.assertEqual(result["sources"], [])
        with patch.object(server, "_searxng", new_callable=AsyncMock) as search:
            result = await self.call("web_evidence_passages", query="query",
                                     urls=["https://127.0.0.1"])
        search.assert_not_awaited()
        self.assertEqual(result["reason"], "no_evidence")
        self.assertEqual(result["sources"][0]["fetch_status"], "blocked")
        self.assertNotIn("note", result)

    async def test_partial_deadline_and_cleanup(self):
        cleaned = []
        settings = replace(DEFAULT_SETTINGS, fetch=replace(DEFAULT_SETTINGS.fetch, timeout_seconds=0.05))

        async def fetch(source, *_args):
            if source.url.path == "/ok":
                return server.Document("fetched", origin="fetched_page", units=((None, "evidence text"),))
            try:
                await asyncio.Future()
            finally:
                cleaned.append(source.url.path)

        with patch.object(server.settings_manager, "get", return_value=settings), patch.object(
            server, "_fetch", side_effect=fetch
        ):
            result = await asyncio.wait_for(self.call(
                "web_evidence_passages", query="evidence", urls=[
                    "https://example.org/ok", "https://example.org/slow1", "https://example.org/slow2",
                ]
            ), timeout=2)
        self.assertEqual(result["status"], "partial")
        self.assertEqual([s["fetch_status"] for s in result["sources"]], ["fetched", "timeout", "timeout"])
        self.assertCountEqual(cleaned, ["/slow1", "/slow2"])
        self.assertEqual(result["passages"][0]["text"], "evidence text")

    async def test_extractor_exception_preserves_other_sources(self):
        async def download(url, *_args):
            return outbound.Download("fetched", final=url, kind="fetched_page", data=url.path.encode())

        def extract(html, **_):
            if html == "/bad":
                raise RuntimeError("private diagnostic")
            return "usable evidence"

        with patch.object(outbound, "download", side_effect=download), patch.object(
            server.trafilatura, "extract", side_effect=extract
        ):
            result = await self.call("web_evidence_passages", query="usable", urls=[
                "https://example.org/bad", "https://example.org/ok",
            ])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["sources"][0]["fetch_status"], "extract_failed")
        self.assertEqual(result["passages"][0]["source_id"], "s2")
        self.assertNotIn("private diagnostic", str(result))

    def test_ranking_backfills_and_handles_tokenless_corpus(self):
        settings = replace(DEFAULT_SETTINGS, chunk=replace(DEFAULT_SETTINGS.chunk, words=1, overlap=0))
        sources = server._supplied([f"https://example.org/{i}" for i in range(7)], 8)
        docs = [
            server.Document("fetched", origin="fetched_page", units=((None, text),))
            for text in ["one two three four five six seven eight", "nine", "ten", "eleven",
                         "twelve", "thirteen", "fourteen"]
        ]
        passages = server._rank("???", sources, docs, settings, 8)
        self.assertEqual(len(passages), 8)
        self.assertEqual(sum(p["source_id"] == "s1" for p in passages), 2)
        docs = [server.Document("fetched", origin="fetched_page", units=((None, "!!!"),))]
        passages = server._rank("???", sources[:1], docs, settings, 8)
        self.assertEqual(passages[0]["score"], 0)

    def test_final_url_aliases_share_cap_and_passage_deduplication(self):
        settings = replace(DEFAULT_SETTINGS, chunk=replace(DEFAULT_SETTINGS.chunk, words=1, overlap=0))
        sources = server._supplied(["https://example.org/a", "https://example.org/b",
                                    "https://example.net/c"], 8)
        canonical = outbound.parse_url("https://example.org/final")
        docs = [
            server.Document("fetched", final=canonical, origin="fetched_page",
                            units=((None, "one two"),)),
            server.Document("fetched", final=canonical, origin="fetched_page",
                            units=((None, "three"),)),
            server.Document("fetched", origin="fetched_page", units=((None, "one four"),)),
        ]
        passages = server._rank("???", sources, docs, settings, 8)
        self.assertEqual([p["text"] for p in passages], ["one", "two", "four"])
        self.assertEqual([p["source_id"] for p in passages], ["s1", "s1", "s3"])

    def test_pdf_page_numbers_and_limits(self):
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), "alpha evidence")
            pdf.new_page()
            pdf.new_page().insert_text((72, 72), "omega evidence")
            data = pdf.tobytes()
        download = outbound.Download("fetched", data=data, kind="fetched_pdf")
        result = server._extract(download, DEFAULT_SETTINGS.fetch)
        self.assertEqual(result.pages_examined, 3)
        self.assertFalse(result.truncated)
        sources = server._supplied(["https://example.org/file.pdf"], 8)
        passages = server._rank("omega", sources, [result], DEFAULT_SETTINGS, 8)
        self.assertEqual({p["page"] for p in passages}, {1, 3})
        limited = server._extract(download, replace(DEFAULT_SETTINGS.fetch, max_pdf_pages=1))
        self.assertTrue(limited.truncated)
        self.assertEqual(limited.pages_examined, 1)
        tiny = server._extract(download, replace(DEFAULT_SETTINGS.fetch, max_document_chars=5))
        self.assertTrue(tiny.truncated)
        self.assertEqual(tiny.units[0][1], "alpha")

    def test_html_input_and_output_caps_are_independent(self):
        download = outbound.Download("fetched", kind="fetched_page", data=b" " * 300_000)
        with patch.object(server.trafilatura, "extract", return_value="short text"):
            doc = server._extract(download, DEFAULT_SETTINGS.fetch)
        self.assertTrue(doc.input_capped)
        self.assertFalse(doc.truncated)
        with patch.object(server.trafilatura, "extract", return_value="abcdefgh"):
            doc = server._extract(outbound.Download("fetched", kind="fetched_page", data=b"<p>"),
                                  replace(DEFAULT_SETTINGS.fetch, max_document_chars=4))
        self.assertFalse(doc.input_capped)
        self.assertTrue(doc.truncated)
        self.assertEqual(doc.units[0][1], "abcd")
