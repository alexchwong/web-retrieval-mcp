"""0.1.1 offline contracts, moved onto the library API: search filters, outcomes,
extraction and ranking. Assertions keep their 0.1.1 meaning (Section 15, test 1)."""
import asyncio
from dataclasses import replace
from datetime import date
import unittest
from unittest.mock import AsyncMock, patch

from webspell import api, extract, outbound, rank, search
from webspell.extract import Document
from webspell.settings import DEFAULT_SETTINGS

from tests.helpers import LoopbackServer, settings

# Search is opt-in in 0.2.0; tests that patch the client still need it configured.
CONFIGURED = settings(search={"searxng_url": "http://127.0.0.1:9"})


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, tool, settings=CONFIGURED, **arguments):
        return await api.run_tool(tool, arguments, settings)

    async def test_page_text_invalid_and_blocked(self):
        result = await self.call("web_page_text", url="file:///etc/passwd")
        self.assertEqual(result["error"], "url must be http(s)")
        self.assertNotIn("fetch_status", result)
        result = await self.call("web_page_text", url="https://user:secret@example.org/")
        self.assertEqual(result["fetch_status"], "blocked")
        self.assertEqual(result["url"], "https://example.org/")

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
                query = search.search_input("query", value, [" Faß.DE. "], date(2024, 3, 31))
                self.assertEqual(query.query, "query site:xn--fa-hia.de")
                self.assertEqual(query.metadata["time_range"], window)
                self.assertEqual(query.metadata["recency_method"], method)
                self.assertEqual(query.metadata["cutoff_utc"], cutoff)
        for value in ("01w", "0w", "366d", "53w", "6y", "61m", "١w", "1W"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                search.search_input("query", value, None, date(2024, 2, 29))
        for value in ("%77ho.int", "exa mple.com", "x_y.example.org", "1.2.3.4", "who.int.."):
            with self.subTest(value=value), self.assertRaises(ValueError):
                search.search_input("query", None, [value], date(2024, 2, 29))
        query = search.search_input("query", "1y", None, date(2024, 2, 29))
        self.assertEqual(query.metadata["cutoff_utc"], "2023-02-28")

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
        with LoopbackServer(lambda exchange: exchange.json(payload)) as fixture, patch.object(
            outbound, "download", side_effect=AssertionError("unexpected fetch")
        ):
            result = await self.call("web_search_results", settings(search={"searxng_url": fixture.url}),
                                     query="query", recency="1w", domains=["example.org"])
        self.assertEqual(len(fixture.requests), 1)
        params = dict(item.split("=", 1) for item in fixture.requests[0].target.split("?", 1)[1].split("&"))
        self.assertEqual(params["time_range"], "week")
        self.assertEqual(params["q"], "query+site%3Aexample.org")
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

        with patch.object(search, "searxng", new_callable=AsyncMock) as backend, patch.object(
            outbound, "download", side_effect=download
        ), patch("trafilatura.extract", side_effect=lambda html, **_: f"research {html}"):
            result = await self.call("web_evidence_passages", query="research", urls=[
                "https://user:secret@example.org/a", "https://example.org/a",
                "https://EXAMPLE.org:443/a#duplicate", "https://example.org/b",
            ])
        backend.assert_not_awaited()
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
        with patch.object(search, "searxng", new_callable=AsyncMock) as backend, patch.object(
            outbound, "download", new_callable=AsyncMock
        ) as download:
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    result = await self.call("web_evidence_passages", query="query", **arguments)
                    self.assertEqual(result["reason"], "invalid_input")
                    self.assertEqual(result["sources"], [])
            backend.assert_not_awaited()
            download.assert_not_awaited()

    async def test_empty_urls_discover_and_backend_failure(self):
        with patch.object(search, "searxng", new_callable=AsyncMock, return_value=([], "no_results")) as backend:
            for arguments in ({}, {"urls": None}, {"urls": []}):
                result = await self.call("web_evidence_passages", query="query", **arguments)
                self.assertEqual(result["mode"], "web_search")
                self.assertEqual(result["reason"], "no_results")
            self.assertEqual(backend.await_count, 3)
            backend.side_effect = ValueError("sensitive backend detail")
            result = await self.call("web_evidence_passages", query="query")
            self.assertEqual(result["reason"], "search_backend_failed")
            self.assertNotIn("sensitive", str(result))

    async def test_backend_timeout_and_no_evidence(self):
        with patch.object(search, "searxng", side_effect=asyncio.TimeoutError):
            for tool in ("web_search_results", "web_evidence_passages"):
                result = await self.call(tool, query="query")
                self.assertEqual(result["reason"], "search_backend_failed")
                self.assertEqual(result["sources"], [])
        with patch.object(search, "searxng", new_callable=AsyncMock) as backend:
            result = await self.call("web_evidence_passages", query="query",
                                     urls=["https://127.0.0.1"])
        backend.assert_not_awaited()
        self.assertEqual(result["reason"], "no_evidence")
        self.assertEqual(result["sources"][0]["fetch_status"], "blocked")
        self.assertNotIn("note", result)

    async def test_partial_deadline_and_cleanup(self):
        cleaned = []
        fast = replace(CONFIGURED, fetch=replace(DEFAULT_SETTINGS.fetch, timeout_seconds=0.05))

        async def fetch(source, *_args, **_kwargs):
            if source.url.path == "/ok":
                return Document("fetched", origin="fetched_page", units=((None, "evidence text"),))
            try:
                await asyncio.Future()
            finally:
                cleaned.append(source.url.path)

        with patch.object(api, "_fetch", side_effect=fetch):
            result = await asyncio.wait_for(self.call(
                "web_evidence_passages", fast, query="evidence", urls=[
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

        def extract_text(html, **_):
            if html == "/bad":
                raise RuntimeError("private diagnostic")
            return "usable evidence"

        with patch.object(outbound, "download", side_effect=download), patch(
            "trafilatura.extract", side_effect=extract_text
        ):
            result = await self.call("web_evidence_passages", query="usable", urls=[
                "https://example.org/bad", "https://example.org/ok",
            ])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["sources"][0]["fetch_status"], "extract_failed")
        self.assertEqual(result["passages"][0]["source_id"], "s2")
        self.assertNotIn("private diagnostic", str(result))

    def test_ranking_backfills_and_handles_tokenless_corpus(self):
        one_word = replace(DEFAULT_SETTINGS, chunk=replace(DEFAULT_SETTINGS.chunk, words=1, overlap=0))
        sources = search.supplied([f"https://example.org/{i}" for i in range(7)], 8)
        docs = [
            Document("fetched", origin="fetched_page", units=((None, text),))
            for text in ["one two three four five six seven eight", "nine", "ten", "eleven",
                         "twelve", "thirteen", "fourteen"]
        ]
        passages = rank.rank("???", sources, docs, one_word, 8)
        self.assertEqual(len(passages), 8)
        self.assertEqual(sum(p["source_id"] == "s1" for p in passages), 2)
        docs = [Document("fetched", origin="fetched_page", units=((None, "!!!"),))]
        passages = rank.rank("???", sources[:1], docs, one_word, 8)
        self.assertEqual(passages[0]["score"], 0)

    def test_final_url_aliases_share_cap_and_passage_deduplication(self):
        one_word = replace(DEFAULT_SETTINGS, chunk=replace(DEFAULT_SETTINGS.chunk, words=1, overlap=0))
        sources = search.supplied(["https://example.org/a", "https://example.org/b",
                                   "https://example.net/c"], 8)
        canonical = outbound.parse_url("https://example.org/final")
        docs = [
            Document("fetched", final=canonical, origin="fetched_page", units=((None, "one two"),)),
            Document("fetched", final=canonical, origin="fetched_page", units=((None, "three"),)),
            Document("fetched", origin="fetched_page", units=((None, "one four"),)),
        ]
        passages = rank.rank("???", sources, docs, one_word, 8)
        self.assertEqual([p["text"] for p in passages], ["one", "two", "four"])
        self.assertEqual([p["source_id"] for p in passages], ["s1", "s1", "s3"])

    def test_html_input_and_output_caps_are_independent(self):
        download = outbound.Download("fetched", kind="fetched_page", data=b" " * 300_000)
        with patch("trafilatura.extract", return_value="short text"):
            doc = extract.extract(download, DEFAULT_SETTINGS.fetch)
        self.assertTrue(doc.input_capped)
        self.assertFalse(doc.truncated)
        with patch("trafilatura.extract", return_value="abcdefgh"):
            doc = extract.extract(outbound.Download("fetched", kind="fetched_page", data=b"<p>"),
                                  replace(DEFAULT_SETTINGS.fetch, max_document_chars=4))
        self.assertFalse(doc.input_capped)
        self.assertTrue(doc.truncated)
        self.assertEqual(doc.units[0][1], "abcd")

    async def test_page_text_paging_joins_and_unpaged_result(self):
        text = "alpha beta gamma delta " * 50

        async def download(url, *_args):
            return outbound.Download("fetched", final=url, kind="fetched_page", data=b"<p>")

        with patch.object(outbound, "download", side_effect=download), patch(
            "trafilatura.extract", return_value=text
        ):
            whole = await self.call("web_page_text", url="https://example.org/")
            self.assertEqual(whole["text"], text)
            self.assertEqual((whole["chars"], whole["total_chars"], whole["start"], whole["next_start"]),
                             (len(text), len(text), 0, None))
            pieces, start = [], 0
            while start is not None:
                page = await self.call("web_page_text", url="https://example.org/", start=start,
                                       max_chars=97)
                self.assertEqual(page["start"], start)
                self.assertEqual(page["chars"], len(page["text"]))
                pieces.append(page["text"])
                start = page["next_start"]
            self.assertEqual("".join(pieces), whole["text"])
            beyond = await self.call("web_page_text", url="https://example.org/", start=10**6)
            self.assertEqual((beyond["text"], beyond["chars"], beyond["next_start"]), ("", 0, None))
