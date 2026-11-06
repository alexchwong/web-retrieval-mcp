"""Offline MCP registration/transport smoke checks for supported Python versions."""

from __future__ import annotations

import unittest

import server


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_expected_mcp_tools_registered(self) -> None:
        tools = await server.mcp.list_tools()
        self.assertEqual({tool.name for tool in tools}, {"search_web", "read_url"})

    async def test_read_url_rejects_non_http_schemes_without_fetching(self) -> None:
        result = await server.mcp.call_tool(
            "read_url", {"url": "file:///etc/passwd"}
        )
        self.assertFalse(result.is_error)
        self.assertIn("url must be http(s)", str(result))

    def test_stateless_http_exposes_mcp_path(self) -> None:
        app = server.mcp.http_app(transport="streamable-http", stateless_http=True)
        self.assertIn("/mcp", {getattr(route, "path", None) for route in app.routes})


if __name__ == "__main__":
    unittest.main()
