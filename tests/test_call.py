"""``call``, Stop, deadlines, the search client and its bounds (Section 15, tests 4, 5,
5a, 5b, 5c, 7 and 8a). Loopback fixtures only; no network."""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import threading
import time
import tracemalloc
import unittest
from unittest.mock import patch

import webspell
from webspell import api, extract, limits, outbound, rank, search

from tests.helpers import (BlockingResolver, LoopbackServer, loopback_documents, resolver,
                           search_payload, settings, wait_idle)

HTML = b"<html><body><article><p>" + b"Evidence about the example domain. " * 30 + b"</p></article></body></html>"


def html_handler(exchange):
    exchange.respond(200, HTML, {"Content-Type": "text/html; charset=utf-8"})


class CallInThread:
    """Run ``webspell.call`` in a plain thread and time it."""

    def __init__(self, *args, **kwargs):
        self.result = None
        self.error = None
        self.returned_at = None
        self.thread = threading.Thread(target=self._run, args=args, kwargs=kwargs, daemon=True)
        self.thread.start()

    def _run(self, *args, **kwargs):
        try:
            self.result = webspell.call(*args, **kwargs)
        except BaseException as exc:  # pragma: no cover - reported by the test
            self.error = exc
        self.returned_at = time.monotonic()

    def join(self, timeout=10):
        self.thread.join(timeout)
        if self.error:
            raise self.error
        return self.result


def wait_for(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class CallTests(unittest.TestCase):
    def tearDown(self):
        self.assertFalse(any(wait_idle().values()), "process-wide slots were not released")

    # ---- test 4 ------------------------------------------------------------ #

    def test_call_from_plain_thread(self):
        with LoopbackServer(html_handler) as fixture, loopback_documents():
            result = webspell.call("web_page_text", {"url": f"{fixture.url}/page"},
                                   settings=webspell.Settings())
            self.assertEqual(result["status"], "complete")
            self.assertIn("Evidence about the example domain.", result["text"])
            self.assertEqual(webspell.call("web_page_text", {"url": 5}, settings=webspell.Settings())["reason"],
                             "invalid_input")
            self.assertEqual(webspell.call("no_such_tool", {}, settings=webspell.Settings()),
                             {"status": "failed", "reason": "invalid_input", "error": "unknown tool"})
            threads = [CallInThread("web_evidence_passages",
                                    {"query": "example evidence", "urls": [f"{fixture.url}/{i}"]},
                                    settings=webspell.Settings()) for i in range(2)]
            results = [thread.join() for thread in threads]
        self.assertTrue(all(r["status"] == "complete" and r["passages"] for r in results))

    def test_call_refuses_a_running_loop(self):
        async def inside():
            webspell.call("web_page_text", {"url": "https://example.org"}, settings=webspell.Settings())

        with self.assertRaises(RuntimeError):
            asyncio.run(inside())
        with self.assertRaises(TypeError):
            webspell.call("web_page_text", {"url": "x"}, settings={})
        with self.assertRaises(ValueError):
            webspell.call("web_page_text", {"url": "x"}, settings=webspell.Settings(), deadline_s=0)

    # ---- test 5 ------------------------------------------------------------ #

    def test_5a_cancel_during_stalled_fetch(self):
        with LoopbackServer(lambda exchange: exchange.stall()) as fixture, loopback_documents():
            cancel = threading.Event()
            running = CallInThread("web_page_text", {"url": f"{fixture.url}/stall"},
                                   settings=webspell.Settings(), cancel=cancel)
            self.assertTrue(wait_for(lambda: fixture.requests))
            cancelled_at = time.monotonic()
            cancel.set()
            self.assertEqual(running.join(), {"status": "cancelled"})
            self.assertLess(running.returned_at - cancelled_at, 1.0)
            self.assertTrue(wait_for(lambda: fixture.closed == ["/stall"], 2))

    def test_5b_cancel_during_stalled_dns(self):
        fake = BlockingResolver({"stall.test": "127.0.0.1"})
        with resolver(fake):
            cancel = threading.Event()
            running = CallInThread("web_page_text", {"url": "https://stall.test/"},
                                   settings=webspell.Settings(), cancel=cancel)
            self.assertTrue(wait_for(lambda: fake.inside == 1))
            cancelled_at = time.monotonic()
            cancel.set()
            self.assertEqual(running.join(), {"status": "cancelled"})
            self.assertLess(running.returned_at - cancelled_at, 1.0)
            self.assertEqual(fake.inside, 1, "the resolver is still blocked")
            fake.release.set()

    def test_5c_cancel_during_slow_extraction(self):
        entered = []
        release = threading.Event()
        real = extract.extract

        def slow(download, fetch_settings):
            entered.append(download.final.path)
            release.wait(30)
            return real(download, fetch_settings)

        one_at_a_time = settings(fetch={"concurrency": 1})
        with LoopbackServer(html_handler) as fixture, loopback_documents(), patch.object(
                extract, "extract", slow):
            cancel = threading.Event()
            running = CallInThread("web_evidence_passages",
                                   {"query": "evidence", "urls": [f"{fixture.url}/a", f"{fixture.url}/b"]},
                                   settings=one_at_a_time, cancel=cancel)
            self.assertTrue(wait_for(lambda: entered))
            cancelled_at = time.monotonic()
            cancel.set()
            self.assertEqual(running.join(), {"status": "cancelled"})
            self.assertLess(running.returned_at - cancelled_at, 1.0)
            requests, parsers = len(fixture.requests), len(entered)
            release.set()
            time.sleep(0.3)
            self.assertEqual((len(fixture.requests), len(entered)), (requests, parsers),
                             "no further work starts after Stop")

    def test_5d_deadline_shorter_than_budget(self):
        with LoopbackServer(lambda exchange: exchange.stall()) as searxng:
            configured = settings(search={"searxng_url": searxng.url})
            for tool, arguments in (("web_search_results", {"query": "q"}),
                                    ("web_evidence_passages", {"query": "q"})):
                with self.subTest(tool=tool):
                    started = time.monotonic()
                    result = webspell.call(tool, arguments, settings=configured, deadline_s=0.4)
                    self.assertLess(time.monotonic() - started, 1.4)
                    self.assertEqual(result["reason"], "deadline_exceeded")
                    self.assertEqual(result["error"], "deadline expired before search completed")
                    self.assertIn("search", result)
                    self.assertEqual(result["sources"], [])
        with LoopbackServer(lambda exchange: exchange.stall()) as fixture, loopback_documents():
            result = webspell.call("web_evidence_passages", {"query": "q", "urls": [f"{fixture.url}/s"]},
                                   settings=webspell.Settings(), deadline_s=0.4)
            self.assertEqual(result["sources"][0]["fetch_status"], "timeout")
        release = threading.Event()

        def slow(download, fetch_settings):
            release.wait(30)
            return extract.Document("fetched", origin="fetched_page", units=((None, "late"),))

        with LoopbackServer(html_handler) as fixture, loopback_documents(), patch.object(
                extract, "extract", slow):
            started = time.monotonic()
            result = webspell.call("web_page_text", {"url": f"{fixture.url}/p"},
                                   settings=webspell.Settings(), deadline_s=0.4)
            self.assertLess(time.monotonic() - started, 1.4)
            self.assertEqual(result["fetch_status"], "timeout")
            release.set()

    def test_5e_stalled_searxng_without_deadline(self):
        with LoopbackServer(lambda exchange: exchange.stall()) as searxng:
            configured = settings(search={"searxng_url": searxng.url, "timeout_seconds": 0.3})
            for tool in ("web_search_results", "web_evidence_passages"):
                with self.subTest(tool=tool):
                    self.assertEqual(webspell.call(tool, {"query": "q"}, settings=configured)["reason"],
                                     "search_backend_failed")

    def test_5f_lookup_slots_held_without_deadline(self):
        fake = BlockingResolver({"searx.test": "127.0.0.1"})
        with resolver(fake):
            holders = [CallInThread("web_page_text", {"url": f"https://held{i}.test/"},
                                    settings=webspell.Settings(), cancel=threading.Event())
                       for i in range(limits.LOOKUP_LIMIT)]
            self.assertTrue(wait_for(lambda: fake.inside == limits.LOOKUP_LIMIT))
            configured = settings(search={"searxng_url": "http://searx.test:9", "timeout_seconds": 0.5})
            for tool in ("web_search_results", "web_evidence_passages"):
                with self.subTest(tool=tool):
                    started = time.monotonic()
                    result = webspell.call(tool, {"query": "q"}, settings=configured)
                    self.assertEqual(result["reason"], "search_backend_failed")
                    self.assertLess(time.monotonic() - started, 0.5 + 1.0)
            self.assertNotIn("searx.test", fake.calls, "the waiting lookup never starts")
            fake.release.set()
            for holder in holders:
                holder.join()
            self.assertNotIn("searx.test", fake.calls)

    # ---- test 5a: synchronous blocks ---------------------------------------- #

    def _timed(self, target, name, store):
        real = getattr(target, name)

        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                return real(*args, **kwargs)
            finally:
                store.append(time.perf_counter() - started)
        return patch.object(target, name, wrapper)

    def test_5a_search_block(self):
        duplicates = {"results": [{"url": "https://a.org/x"}] * 72_000}
        long_hosts = {"results": [{"url": f"https://{'a' * 3990}{i:05d}.example/", "title": "t"}
                                  for i in range(200)]}
        too_long = {"results": [{"url": "https://example.org/" + "p" * 4100, "title": "t"}] * 300}
        for name, payload, expected in (("duplicates", duplicates, 1), ("long hosts", long_hosts, 0),
                                        ("long urls", too_long, 0)):
            durations, parsed = [], []
            real_parse = outbound.parse_url

            def counting(value):
                parsed.append(1)
                return real_parse(value)

            body = json.dumps(payload, separators=(",", ":")).encode()
            if name == "duplicates":
                self.assertTrue(1_800_000 < len(body) < 2_000_000)

            def respond(exchange, body=body):
                exchange.respond(200, body, {"Content-Type": "application/json"})

            with self.subTest(case=name), LoopbackServer(respond) as searxng, \
                    self._timed(search, "_sources", durations), patch.object(outbound, "parse_url", counting):
                result = webspell.call("web_search_results", {"query": "q"},
                                       settings=settings(search={"searxng_url": searxng.url}))
                print(f"\n  search block ({name}, {len(searxng.requests)} request): {durations[0] * 1000:.1f} ms")
                self.assertLess(durations[0], 1.0)
                self.assertEqual(len(result["sources"]), expected)
                if name == "duplicates":
                    self.assertEqual(len(parsed), 200, "only the first 200 entries are inspected")
                if name == "long urls":
                    self.assertEqual(result["reason"], "no_eligible_results")
                    self.assertEqual(parsed, [], "URLs over 4,096 characters are skipped unparsed")

    def test_5a_finalization_block(self):
        latin = " ".join(f"word{i % 997} evidence" for i in range(4000))[:40_000]
        cjk = " ".join(chr(0x4E00 + (i % 3000)) + chr(0x4E00 + ((i * 7) % 3000)) for i in range(13_400))[:40_000]
        query = " ".join(f"word{i}" for i in range(200))[:1000]
        limited = settings(output={"max_result_bytes": 4096, "ascii_json": True})

        for name, text in (("latin", latin), ("cjk", cjk)):
            durations = []

            async def download(url, *_args):
                await asyncio.sleep(0.6)
                return outbound.Download("fetched", final=url, kind="fetched_page", data=b"<p>")

            urls = [f"https://example{i}.org/" for i in range(8)]
            with self.subTest(case=name), patch.object(outbound, "download", side_effect=download), \
                    patch("trafilatura.extract", return_value=text), \
                    self._timed(rank, "rank", durations), self._timed(api.limit, "apply", durations):
                result = webspell.call("web_evidence_passages",
                                       {"query": query, "urls": urls, "max_passages": 20},
                                       settings=limited, deadline_s=0.75)
                total = sum(durations)
                print(f"\n  finalization block ({name}): {total * 1000:.1f} ms")
                self.assertLess(total, 1.0)
                self.assertIn(result["status"], ("complete", "partial"))
                self.assertTrue(result["passages"], "ranked result returned, not a timeout")
                self.assertLessEqual(webspell.result_bytes(result, ascii=True), 4096)

    def test_5a_cancel_set_during_a_block(self):
        payload = search_payload("https://example.org/a")
        for target, name, tool, arguments in (
                (search, "_sources", "web_search_results", {"query": "q"}),
                (rank, "rank", "web_evidence_passages", {"query": "q", "urls": ["https://example.org/a"]})):
            cancel = threading.Event()
            real = getattr(target, name)

            def setting_cancel(*args, _real=real, _cancel=cancel, **kwargs):
                _cancel.set()
                return _real(*args, **kwargs)

            async def download(url, *_args):
                return outbound.Download("fetched", final=url, kind="fetched_page", data=b"<p>")

            with self.subTest(block=name), LoopbackServer(lambda ex: ex.json(payload)) as searxng, \
                    patch.object(target, name, setting_cancel), \
                    patch.object(outbound, "download", side_effect=download), \
                    patch("trafilatura.extract", return_value="evidence text"):
                result = webspell.call(tool, arguments, cancel=cancel,
                                       settings=settings(search={"searxng_url": searxng.url}))
                self.assertEqual(result, {"status": "cancelled"})

    def test_5a_argument_lengths(self):
        s = webspell.Settings()
        self.assertEqual(webspell.call("web_search_results", {"query": "q" * 1001}, settings=s)["reason"],
                         "invalid_input")
        self.assertEqual(webspell.call("web_page_text", {"url": "https://example.org/" + "p" * 4077},
                                       settings=s)["reason"], "invalid_input")
        self.assertEqual(webspell.call("web_evidence_passages",
                                       {"query": "q", "urls": ["https://example.org/" + "p" * 4077]},
                                       settings=s)["reason"], "invalid_input")

    # ---- test 5b: search response cap -------------------------------------- #

    def test_5b_search_response_cap(self):
        big = b'{"results": [' + b'{"url": "https://example.org/x"}, ' * 90_000 + b"{}]}"
        self.assertGreater(len(big), 3_000_000)
        big = big[:3_000_000]
        bomb = gzip.compress(b" " * 60_000_000, 9)
        self.assertLess(len(bomb), 100_000)
        nested = b"[" * 100_000 + b"]" * 100_000

        def with_length(exchange):
            exchange.respond(200, big, {"Content-Type": "application/json"}, pause=0.002)

        def without_length(exchange):
            exchange.send_head(200, {"Content-Type": "application/json"})
            exchange.send_body(big, pause=0.002)

        def gzipped(exchange):
            exchange.respond(200, bomb, {"Content-Type": "application/json", "Content-Encoding": "gzip"})

        def deep(exchange):
            exchange.respond(200, nested, {"Content-Type": "application/json"})

        for name, handler in (("content-length", with_length), ("no content-length", without_length),
                              ("gzip", gzipped), ("nested", deep)):
            for tool in ("web_search_results", "web_evidence_passages"):
                with self.subTest(case=name, tool=tool), LoopbackServer(handler) as searxng:
                    configured = settings(search={"searxng_url": searxng.url})
                    if name == "gzip":
                        tracemalloc.start()
                        baseline = tracemalloc.get_traced_memory()[0]
                    result = webspell.call(tool, {"query": "q"}, settings=configured)
                    if name == "gzip":
                        peak = tracemalloc.get_traced_memory()[1] - baseline
                        tracemalloc.stop()
                        self.assertLess(peak, 10_000_000)
                    self.assertEqual(result["reason"], "search_backend_failed")
                    self.assertEqual(searxng.requests[0].headers.get("accept-encoding"), "identity")
                    if name in ("content-length", "no content-length"):
                        self.assertTrue(wait_for(lambda: searxng.requests[0].aborted, 5),
                                        "connection closed before the body was fully sent")
                        self.assertLess(searxng.requests[0].sent, len(big))

    # ---- test 5c: search host resolution ------------------------------------ #

    def test_5c_search_host_resolved_as_configured(self):
        payload = search_payload("https://example.org/a")
        fake = BlockingResolver({"localhost": "127.0.0.1", "doc.test": "127.0.0.1"}, block=False)
        with LoopbackServer(lambda ex: ex.json(payload)) as searxng:
            local = settings(search={"searxng_url": f"http://localhost:{searxng.port}"})
            with resolver(fake):
                result = webspell.call("web_search_results", {"query": "q"}, settings=local)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(fake.calls, ["localhost"])
            self.assertEqual(searxng.requests[0].headers["host"], f"localhost:{searxng.port}")
            # And with the operating system's real resolver: localhost resolves, localhost. may not.
            result = webspell.call("web_search_results", {"query": "q"}, settings=local)
            self.assertEqual(result["status"], "complete")
        with resolver(fake):
            webspell.call("web_page_text", {"url": "https://doc.test/"}, settings=webspell.Settings())
        self.assertEqual(fake.calls[-1], "doc.test.")

    # ---- test 7 ------------------------------------------------------------ #

    def test_7_search_not_configured(self):
        with patch.object(search, "searxng", side_effect=AssertionError("no request")), \
                patch.object(limits, "_getaddrinfo", side_effect=AssertionError("no lookup")):
            for tool in ("web_search_results", "web_evidence_passages"):
                with self.subTest(tool=tool):
                    result = webspell.call(tool, {"query": "q"}, settings=webspell.Settings())
                    self.assertEqual((result["status"], result["reason"]), ("failed", "search_not_configured"))
                    self.assertEqual(result["sources"], [])
                    self.assertIn("search", result)
        with LoopbackServer(html_handler) as fixture, loopback_documents():
            self.assertEqual(webspell.call("web_page_text", {"url": f"{fixture.url}/p"},
                                           settings=webspell.Settings())["status"], "complete")
            self.assertEqual(webspell.call("web_evidence_passages",
                                           {"query": "evidence", "urls": [f"{fixture.url}/p"]},
                                           settings=webspell.Settings())["status"], "complete")

    # ---- test 8a ----------------------------------------------------------- #

    def test_8a_search_ignores_environment_proxies(self):
        payload = search_payload("https://example.org/a")
        with LoopbackServer(lambda ex: ex.json(payload)) as searxng, \
                LoopbackServer(lambda ex: ex.respond(502)) as proxy:
            variables = {name: proxy.url for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                                                      "http_proxy", "https_proxy", "all_proxy")}
            configured = settings(search={"searxng_url": searxng.url})
            with patch.dict(os.environ, variables):
                for tool in ("web_search_results", "web_evidence_passages"):
                    blocked = outbound.Download("blocked", "fixture")
                    with self.subTest(tool=tool), patch.object(outbound, "download", return_value=blocked):
                        result = webspell.call(tool, {"query": "q"}, settings=configured)
                        self.assertNotEqual(result.get("reason"), "search_backend_failed")
            self.assertEqual(len(searxng.requests), 2)
            self.assertEqual(proxy.requests, [])


class AsyncCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_host_cancels_as_a_task(self):
        with LoopbackServer(lambda exchange: exchange.stall()) as fixture, loopback_documents():
            task = asyncio.create_task(webspell.page_text(f"{fixture.url}/s", settings=webspell.Settings()))
            while not fixture.requests:
                await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            end = time.monotonic() + 2
            while fixture.closed != ["/s"] and time.monotonic() < end:
                await asyncio.sleep(0.01)
            self.assertEqual(fixture.closed, ["/s"])


if __name__ == "__main__":
    unittest.main()
