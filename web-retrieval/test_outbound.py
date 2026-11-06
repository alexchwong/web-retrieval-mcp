"""Offline URL policy and real local TLS transport checks."""
import asyncio
from dataclasses import replace
from pathlib import Path
import ssl
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import outbound
from settings import DEFAULT_SETTINGS

FIXTURES = Path(__file__).with_name("testdata")
FETCH = DEFAULT_SETTINGS.fetch


class OutboundTests(unittest.IsolatedAsyncioTestCase):
    def test_url_parser(self):
        for value in ("http://127.1", "http://0x7f.0.0.1", "http://2130706433",
                      "http://0177.0.0.1", "http://[fe80::1%25eth0]",
                      "http://exa mple.org", "http://%65xample.org", "http://a_b.example.org",
                      "http://[::1]bad", "http://example.org:", "file:///etc/passwd"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                outbound.parse_url(value)
        self.assertEqual(outbound.parse_url("HTTPS://Faß.DE.:443?q=%20#x").key,
                         "https://xn--fa-hia.de/?q=%20")
        self.assertEqual(outbound.parse_url("http://u:p@example.org").visible, "http://example.org")

    async def test_policy_blocks_before_request(self):
        for value in ("http://127.0.0.1", "http://[::1]", "http://169.254.169.254",
                      "http://168.63.129.16", "http://[::ffff:127.0.0.1]",
                      "http://100.100.100.200", "http://224.0.0.1",
                      "http://example.org:8080", "http://user@example.org"):
            with self.subTest(value=value), patch.object(outbound, "_request", new_callable=AsyncMock) as request:
                result = await outbound.download(outbound.parse_url(value), FETCH)
                self.assertEqual(result.status, "blocked")
                request.assert_not_called()

    async def test_mixed_dns_and_rebinding(self):
        url = outbound.parse_url("https://example.org")
        lookup = AsyncMock(return_value=["8.8.8.8", "127.0.0.1"])
        with patch.object(outbound, "_request", new_callable=AsyncMock) as request:
            self.assertEqual((await outbound.download(url, FETCH, _resolve=lookup)).status, "blocked")
            request.assert_not_called()

        lookup = AsyncMock(side_effect=[["8.8.8.8"], ["127.0.0.1"]])
        with patch.object(outbound, "_request", new_callable=AsyncMock, return_value="/next") as request:
            self.assertEqual((await outbound.download(url, FETCH, _resolve=lookup)).status, "blocked")
            self.assertEqual(request.await_args.args[1], "8.8.8.8")
            self.assertEqual(lookup.await_count, 2)

    async def test_redirects(self):
        url = outbound.parse_url("https://example.org/")
        for location in ("http://example.org/", "https://evil-example.org/",
                         "https://127.0.0.1/", "/", "", "https://user@example.org/"):
            with self.subTest(location=location), patch.object(
                outbound, "_request", new_callable=AsyncMock, return_value=location
            ) as request:
                result = await outbound.download(url, FETCH, ("example.org",),
                                                 _resolve=AsyncMock(return_value=["8.8.8.8"]))
                self.assertEqual(result.status, "blocked")
                request.assert_awaited_once()
        with patch.object(outbound, "_request", new_callable=AsyncMock,
                          side_effect=[f"/{i}" for i in range(6)]) as request:
            result = await outbound.download(url, FETCH, _resolve=AsyncMock(return_value=["8.8.8.8"]))
            self.assertEqual(result.error, "redirect limit exceeded")
            self.assertEqual(request.await_count, 6)

    async def test_fallback_only_uses_vetted_addresses(self):
        lookup = AsyncMock(return_value=["2001:4860:4860::8888", "8.8.8.8"])
        with patch.object(outbound, "_request", new_callable=AsyncMock,
                          side_effect=[httpx.ConnectTimeout("fixture"), outbound.Download("fetched")]) as request:
            result = await outbound.download(outbound.parse_url("https://good.test"), FETCH,
                                             _resolve=lookup)
        self.assertEqual(result.status, "fetched")
        self.assertEqual([call.args[1] for call in request.await_args_list],
                         ["8.8.8.8", "2001:4860:4860::8888"])
        lookup.assert_awaited_once()

    async def test_real_tls_pin_and_ip_identity(self):
        observed = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()

        def ignore_expected_handshake_reset(current_loop, context):
            if (context.get("message") == "Error on transport creation for incoming connection"
                    and isinstance(context.get("exception"), ConnectionResetError)):
                return
            if previous_handler:
                previous_handler(current_loop, context)
            else:
                current_loop.default_exception_handler(context)

        loop.set_exception_handler(ignore_expected_handshake_reset)
        self.addCleanup(loop.set_exception_handler, previous_handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(FIXTURES / "dns.pem", FIXTURES / "test-server-key.pem")
        tls.set_servername_callback(lambda _socket, name, _ctx: observed.append(name))

        async def respond(reader, writer):
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                observed.append(request)
                if b"GET /chunked " in request:
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                                 b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                                 b"2\r\nOK\r\n0\r\n\r\n")
                else:
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                                 b"Content-Length: 2\r\nConnection: close\r\n\r\nOK")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        listener = await asyncio.start_server(respond, "127.0.0.1", 0, ssl=tls)
        async with listener:
            port = listener.sockets[0].getsockname()[1]
            trusted = ssl.create_default_context(cafile=str(FIXTURES / "ca.pem"))
            trusted.verify_flags |= ssl.VERIFY_X509_STRICT
            lookup = AsyncMock(return_value=["127.0.0.1", "127.0.0.2"])
            options = dict(_resolve=lookup, _public=lambda ip: True, _allow_port=lambda p: p == port)
            with patch.dict("os.environ", {"HTTPS_PROXY": "http://127.0.0.1:1"}), patch.object(
                asyncio.get_running_loop(), "getaddrinfo", side_effect=AssertionError("unexpected DNS")
            ):
                url = outbound.parse_url(f"https://good.test:{port}/a/../b?q=%2f")
                result = await outbound.download(url, FETCH, _verify=trusted, **options)
                self.assertEqual(result.data, b"OK")
                self.assertEqual(observed[0], "good.test")
                self.assertIn(f"Host: good.test:{port}".encode(), observed[1])
                self.assertTrue(observed[1].startswith(b"GET /a/../b?q=%2f HTTP/1.1"))
                chunked = outbound.parse_url(f"https://good.test:{port}/chunked")
                result = await outbound.download(chunked, replace(FETCH, max_bytes=1),
                                                 _verify=trusted, **options)
                self.assertEqual(result.status, "too_large")
                result = await outbound.download(url, replace(FETCH, max_bytes=1), _verify=trusted, **options)
                self.assertEqual(result.status, "too_large")
                with patch.object(outbound, "_request", wraps=outbound._request) as request:
                    wrong = outbound.parse_url(f"https://wrong.test:{port}/")
                    result = await outbound.download(wrong, FETCH, _verify=trusted, **options)
                    self.assertEqual(result.error, "document TLS verification failed")
                    request.assert_awaited_once()
                self.assertEqual((await outbound.download(url, FETCH, **options)).status, "http_error")
                ip_url = outbound.parse_url(f"https://127.0.0.1:{port}/")
                self.assertEqual((await outbound.download(ip_url, FETCH, _verify=trusted, **options)).status,
                                 "http_error")
                tls.load_cert_chain(FIXTURES / "ip.pem", FIXTURES / "test-server-key.pem")
                self.assertEqual((await outbound.download(ip_url, FETCH, _verify=trusted, **options)).data, b"OK")

    async def test_owned_tasks_propagate_cancellation(self):
        entered = asyncio.Event()
        future = asyncio.get_running_loop().create_future()

        async def child():
            entered.set()
            return await future

        task = asyncio.create_task(child())
        parent = asyncio.create_task(outbound.wait_owned([task], 30))
        await entered.wait()
        loop = asyncio.get_running_loop()
        loop.call_soon(future.set_result, "complete")
        loop.call_soon(parent.cancel)
        with self.assertRaises(asyncio.CancelledError):
            await parent
        self.assertTrue(task.done())
