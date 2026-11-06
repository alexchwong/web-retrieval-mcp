# Changes

## 0.1.0

- Initial versioned release of the local SearxNG-backed, stateless Streamable HTTP MCP service.
- Provides `search_web` for BM25-ranked passages and `read_url` for extracted HTML/PDF text.
- Installs as a two-container Docker Compose stack with local-only host port defaults.
- Adds Linux/macOS CI on Python 3.10 and 3.13 and an installable GitHub release ZIP.
- Pins FastMCP to the verified 4.1.6 API rather than leaving an unsupported lower bound.
- Supports Python 3.10 via the conditional `tomli` parser backport and stabilizes the settings reload test.
