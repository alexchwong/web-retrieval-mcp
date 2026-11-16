"""The MCP adapter and its request guard (Section 15, tests 10 and 10a, direct run).

The Compose half of 10a runs against the wheel-built image in the MCP smoke workflow.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import webspell

HAVE_SERVER = importlib.util.find_spec("fastmcp") is not None
ROOT = Path(__file__).resolve().parents[1]

if HAVE_SERVER:
    import httpx
    from fastmcp import Client, FastMCP
    import uvicorn

    from webspell import api, server


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for_port(port: int, timeout: float = 20) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError("server did not start")


INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@unittest.skipUnless(HAVE_SERVER, "needs the server extra")
class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mcp = server.build_server(server.SettingsManager(None))

    async def test_advertises_exactly_tools(self):
        async with Client(self.mcp) as client:
            tools = await client.list_tools()
        advertised = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                      for t in tools]
        self.assertEqual(advertised, [dict(entry) for entry in webspell.TOOLS])
        self.assertEqual({t.name for t in tools},
                         {"web_search_results", "web_evidence_passages", "web_page_text"})
        self.assertTrue(all(t.output_schema == server.OUTPUT_SCHEMA for t in tools))

    async def test_calls_go_through_the_library_validator(self):
        async with Client(self.mcp) as client:
            result = await client.call_tool("web_page_text", {"url": "file:///etc/passwd"})
            self.assertEqual(result.structured_content["error"], "url must be http(s)")
            wrong = await client.call_tool("web_evidence_passages", {"query": "q", "max_passages": "3"},
                                           raise_on_error=False)
            self.assertFalse(wrong.is_error)
            self.assertEqual(wrong.structured_content["reason"], "invalid_input")
            searched = await client.call_tool("web_search_results", {"query": "q"})
            self.assertEqual(searched.structured_content["reason"], "search_not_configured")

    def test_stateless_http_exposes_mcp_path(self):
        app = self.mcp.http_app(transport="streamable-http", stateless_http=True)
        self.assertIn("/mcp", {getattr(route, "path", None) for route in app.routes})

    def test_main_defaults(self):
        import logging

        root = logging.getLogger()
        handlers, level = list(root.handlers), root.level
        self.addCleanup(lambda: (setattr(root, "handlers", handlers), root.setLevel(level)))
        captured = {}
        with patch.object(FastMCP, "run", lambda self, **kwargs: captured.update(kwargs, name=self.name)):
            server.main({})
        self.assertEqual(captured, {"host": "127.0.0.1", "port": 8765, "transport": "streamable-http",
                                    "stateless_http": True, "host_origin_protection": True,
                                    "allowed_hosts": None, "allowed_origins": None, "name": "webspell"})
        with patch.object(FastMCP, "run", lambda self, **kwargs: captured.update(kwargs, name=self.name)):
            server.main({"MCP_HOST": "0.0.0.0", "MCP_ALLOWED_HOSTS": "web-retrieval, other",
                         "MCP_ALLOWED_ORIGINS": "http://ui.lan:3000", "MCP_SERVER_NAME": "web-retrieval"})
        self.assertEqual((captured["host"], captured["allowed_hosts"], captured["allowed_origins"],
                          captured["name"]),
                         ("0.0.0.0", ["web-retrieval", "other"], ["http://ui.lan:3000"], "web-retrieval"))


@unittest.skipUnless(HAVE_SERVER, "needs the server extra")
class GuardTests(unittest.TestCase):
    """The guard runs in front of tool dispatch, with the options ``main`` uses."""

    @classmethod
    def setUpClass(cls):
        cls.calls = []
        real = api.run_tool

        async def counting(*args, **kwargs):
            cls.calls.append(args[0])
            return await real(*args, **kwargs)

        cls.patcher = patch.object(api, "run_tool", counting)
        cls.patcher.start()
        mcp = server.build_server(server.SettingsManager(None))
        options = server.http_options({"MCP_ALLOWED_HOSTS": "web-retrieval"})
        app = mcp.http_app(**options)
        cls.port = free_port()
        cls.uvicorn = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cls.port, log_level="warning",
                                                    lifespan="on"))
        cls.thread = threading.Thread(target=cls.uvicorn.run, daemon=True)
        cls.thread.start()
        wait_for_port(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.uvicorn.should_exit = True
        cls.thread.join(10)
        cls.patcher.stop()

    def post(self, body, **headers):
        return httpx.post(f"http://127.0.0.1:{self.port}/mcp", json=body, headers={**HEADERS, **headers},
                          timeout=10, trust_env=False)

    def tool_call(self):
        return {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "web_page_text", "arguments": {"url": "file:///x"}}}

    def test_loopback_and_allowlisted_hosts_are_accepted(self):
        before = len(self.calls)
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}",
                     f"web-retrieval:{self.port}"):
            with self.subTest(host=host):
                self.assertEqual(self.post(self.tool_call(), Host=host).status_code, 200)
        self.assertEqual(self.post(self.tool_call(), Origin=f"http://localhost:{self.port}").status_code, 200)
        self.assertEqual(len(self.calls), before + 5)

        async def client_call():
            async with Client(f"http://127.0.0.1:{self.port}/mcp") as client:
                return (await client.call_tool("web_page_text", {"url": "file:///x"})).structured_content

        self.assertEqual(asyncio.run(client_call())["reason"], "invalid_input")

    def test_foreign_host_and_origin_are_refused_before_dispatch(self):
        before = len(self.calls)
        self.assertEqual(self.post(self.tool_call(), Host="evil.example").status_code, 421)
        self.assertEqual(self.post(self.tool_call(), Origin="http://evil.example").status_code, 403)
        self.assertEqual(self.post(INITIALIZE, Host="evil.example").status_code, 421)
        self.assertEqual(len(self.calls), before, "no tool ran")


@unittest.skipUnless(HAVE_SERVER, "needs the server extra")
class DirectRunTests(unittest.TestCase):
    """``python -m webspell`` with no configuration: loopback, guard on."""

    def test_direct_run(self):
        port = free_port()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("MCP_", "FASTMCP_")) and key.lower() not in (
                   "http_proxy", "https_proxy", "all_proxy")}
        env.update(MCP_PORT=str(port), PYTHONPATH=str(ROOT))
        process = subprocess.Popen([sys.executable, "-m", "webspell"], env=env, cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            wait_for_port(port)
            url = f"http://127.0.0.1:{port}/mcp"

            async def client_call():
                async with Client(url) as client:
                    names = {tool.name for tool in await client.list_tools()}
                    result = await client.call_tool("web_search_results", {"query": "q"})
                    return names, result.structured_content

            names, result = asyncio.run(client_call())
            self.assertEqual(names, {"web_search_results", "web_evidence_passages", "web_page_text"})
            self.assertEqual(result["reason"], "search_not_configured")
            post = lambda **headers: httpx.post(url, json=INITIALIZE, headers={**HEADERS, **headers},
                                                timeout=10, trust_env=False)
            self.assertEqual(post(Host="evil.example").status_code, 421)
            self.assertEqual(post(Origin="http://evil.example").status_code, 403)
            self.assertEqual(post().status_code, 200)
        finally:
            process.terminate()
            output = process.communicate(timeout=10)[0].decode(errors="replace")
        self.assertIn(f"127.0.0.1:{port}", output)


class NoServerExtraTests(unittest.TestCase):
    def test_main_explains_missing_extra(self):
        code = ("import sys, builtins\n"
                "real = builtins.__import__\n"
                "def guard(name, *a, **k):\n"
                "    if name.split('.')[0] == 'fastmcp': raise ModuleNotFoundError(name=name)\n"
                "    return real(name, *a, **k)\n"
                "builtins.__import__ = guard\n"
                "import runpy; runpy.run_module('webspell', run_name='__main__')\n")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
                                env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertEqual(result.returncode, 2)
        self.assertIn("webSpell[server]", result.stderr)


if __name__ == "__main__":
    unittest.main()
