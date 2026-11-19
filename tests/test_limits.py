"""Process-wide limits on lookups, documents and parsers (Section 15, test 6a)."""
from __future__ import annotations

import asyncio
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

import webspell
from webspell import extract, limits

from tests.helpers import BlockingResolver, LoopbackServer, loopback_documents, resolver, search_payload, settings, slots_in_use, wait_idle
from tests.test_call import CallInThread, html_handler, wait_for

PDF = (Path(__file__).with_name("testdata") / "pdf" / "basic.pdf").read_bytes()


def pdf_handler(exchange):
    exchange.respond(200, PDF, {"Content-Type": "application/pdf"})


class Peak:
    """Sample a value in a background thread and keep its maximum."""

    def __init__(self, sample):
        self.sample = sample
        self.peak = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            self.peak = max(self.peak, self.sample())
            time.sleep(0.002)

    def stop(self):
        self._stop.set()
        self._thread.join()
        return self.peak


def threads_named(name):
    return sum(thread.name == name for thread in threading.enumerate())


class BlockedParser:
    def __init__(self):
        self.release = threading.Event()
        self.entered = []
        self._lock = threading.Lock()

    def __call__(self, download, fetch_settings):
        with self._lock:
            self.entered.append(download.final.path if download.final else "")
        self.release.wait(30)
        return extract.Document("fetched", final=download.final, origin=download.kind,
                                units=((1, "evidence"),))


class ProcessLimitTests(unittest.TestCase):
    def tearDown(self):
        self.assertFalse(any(wait_idle().values()), slots_in_use())

    def test_no_loop_getaddrinfo_or_default_executor(self):
        reached = []

        def forbidden(name):
            def fail(self, *args, **kwargs):
                reached.append(name)
                raise AssertionError(f"library reached {name}")
            return fail

        payload = search_payload("https://example.org/a")
        fake = BlockingResolver({"doc.test": "127.0.0.1", "searx.test": "127.0.0.1"}, block=False)
        with LoopbackServer(html_handler) as documents, LoopbackServer(lambda ex: ex.json(payload)) as searxng, \
                loopback_documents(), resolver(fake), \
                patch.object(asyncio.BaseEventLoop, "getaddrinfo", forbidden("loop.getaddrinfo")), \
                patch.object(asyncio.BaseEventLoop, "run_in_executor", forbidden("run_in_executor")):
            configured = settings(search={"searxng_url": f"http://searx.test:{searxng.port}"})
            results = [
                webspell.call("web_page_text", {"url": f"http://doc.test:{documents.port}/p"}, settings=configured),
                webspell.call("web_evidence_passages",
                              {"query": "evidence", "urls": [f"http://doc.test:{documents.port}/q"]},
                              settings=configured),
                webspell.call("web_search_results", {"query": "q"}, settings=configured),
                webspell.call("web_evidence_passages", {"query": "q"}, settings=configured),
            ]
        self.assertEqual(reached, [])
        self.assertEqual([r["status"] for r in results[:3]], ["complete"] * 3)
        self.assertEqual(results[3]["mode"], "web_search")

    def test_lookups(self):
        fake = BlockingResolver()
        monitor = Peak(lambda: threads_named("webspell-lookup"))
        with resolver(fake):
            for index in range(20):
                cancel = threading.Event()
                if index % 2:
                    running = CallInThread("web_search_results", {"query": "q"}, cancel=cancel,
                                           settings=settings(search={"searxng_url": f"http://searx{index}.test:9"}))
                else:
                    running = CallInThread("web_page_text", {"url": f"https://doc{index}.test/"},
                                           cancel=cancel, settings=webspell.Settings())
                time.sleep(0.08)
                cancelled_at = time.monotonic()
                cancel.set()
                self.assertEqual(running.join(), {"status": "cancelled"})
                self.assertLess(running.returned_at - cancelled_at, 1.0)
            entered = list(fake.calls)
            self.assertLessEqual(len(entered), limits.LOOKUP_LIMIT)
            self.assertEqual(len(entered), limits.LOOKUP_LIMIT)
            self.assertEqual(entered[:2], ["doc0.test.", "searx1.test"])
            fake.release.set()
            self.assertTrue(wait_for(lambda: threads_named("webspell-lookup") == 0))
            time.sleep(0.3)
            self.assertEqual(fake.calls, entered, "no cancelled waiter's lookup runs")
        self.assertLessEqual(monitor.stop(), limits.LOOKUP_LIMIT)
        self.assertLessEqual(fake.max_inside, limits.LOOKUP_LIMIT)
        self.assertEqual(limits.lookups.in_use, 0)

    def test_documents(self):
        parser = BlockedParser()
        quick = settings(fetch={"timeout_seconds": 0.5})
        held = Peak(lambda: limits.documents.in_use)
        with LoopbackServer(pdf_handler) as fixture, loopback_documents(), \
                patch.object(extract, "extract", parser), patch.object(extract, "pdf_available", lambda: True):
            requests = Peak(lambda: len(fixture.requests))
            calls = [CallInThread("web_evidence_passages",
                                  {"query": "evidence", "urls": [f"{fixture.url}/c{c}/{i}.pdf" for i in range(8)]},
                                  settings=quick) for c in range(6)]
            results = [call.join(15) for call in calls]
            self.assertLessEqual(requests.stop(), limits.DOCUMENT_LIMIT,
                                 "never more than 16 document requests started and not released")
            self.assertEqual(len(parser.entered), 1)
            self.assertTrue(all(r["status"] == "failed" for r in results))
            # Calls that timed out returned their document slots at once, leaving the
            # one document that is inside the parser.
            self.assertEqual((limits.documents.in_use, limits.pdf_parsers.in_use), (1, 1))
            parser.release.set()
        self.assertLessEqual(held.stop(), limits.DOCUMENT_LIMIT)

    def test_pdf_parser(self):
        parser = BlockedParser()
        threads = Peak(lambda: threads_named("webspell-parser"))
        with LoopbackServer(pdf_handler) as fixture, loopback_documents(), \
                patch.object(extract, "extract", parser), patch.object(extract, "pdf_available", lambda: True):
            for index in range(10):
                cancel = threading.Event()
                running = CallInThread("web_page_text", {"url": f"{fixture.url}/{index}.pdf"},
                                       settings=webspell.Settings(), cancel=cancel)
                self.assertTrue(wait_for(lambda: len(fixture.requests) > index))
                time.sleep(0.1)
                cancel.set()
                self.assertEqual(running.join(), {"status": "cancelled"})
            timed_out = webspell.call("web_page_text", {"url": f"{fixture.url}/late.pdf"},
                                      settings=webspell.Settings(), deadline_s=0.3)
            self.assertEqual(timed_out["fetch_status"], "timeout")
            self.assertEqual(parser.entered, ["/0.pdf"])
            parser.release.set()
            self.assertTrue(wait_for(lambda: limits.pdf_parsers.in_use == 0))
            time.sleep(0.3)
            self.assertEqual(parser.entered, ["/0.pdf"], "no cancelled or timed-out waiter enters later")
        self.assertLessEqual(threads.stop(), limits.HTML_PARSER_LIMIT + limits.PDF_PARSER_LIMIT)

    def test_html_parsers(self):
        releases = {f"/{i}": threading.Event() for i in range(7)}
        entered = []

        def blocking(download, fetch_settings):
            entered.append(download.final.path)
            releases[download.final.path].wait(30)
            return extract.Document("fetched", final=download.final, origin="fetched_page",
                                    units=((None, "evidence"),))

        with LoopbackServer(html_handler) as fixture, loopback_documents(), patch.object(extract, "extract", blocking):
            calls = []
            for index in range(6):
                calls.append(CallInThread("web_page_text", {"url": f"{fixture.url}/{index}"},
                                          settings=webspell.Settings()))
            self.assertTrue(wait_for(lambda: len(entered) == 6))
            calls.append(CallInThread("web_page_text", {"url": f"{fixture.url}/6"}, settings=webspell.Settings()))
            self.assertTrue(wait_for(lambda: len(fixture.requests) == 7))
            time.sleep(0.3)
            self.assertNotIn("/6", entered, "a seventh HTML extraction waits")
            releases["/0"].set()
            self.assertTrue(wait_for(lambda: "/6" in entered), "and proceeds when a slot is freed")
            for event in releases.values():
                event.set()
            self.assertTrue(all(call.join()["status"] == "complete" for call in calls))


if __name__ == "__main__":
    unittest.main()
