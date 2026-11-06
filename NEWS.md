# Changes

## 0.1.1

- Adds `web_search_results` for discovery without opening result pages.
- Renames `search_web` to `web_evidence_passages` and `read_url` to `web_page_text`. Update existing callers and refresh their tool lists.
- Adds supplied-URL evidence retrieval, search filters, source outcomes and PDF page provenance.
- Validates public document destinations and redirects. Proxy-only document egress is no longer supported.
- Adds document and evidence collection deadlines. Completed evidence can be returned when other sources stall.
- Includes CLI smoke checks for all three tools and both evidence modes.

## 0.1.0

- Initial versioned release of the local SearxNG-backed, stateless Streamable HTTP MCP service.
- Provides `search_web` for BM25-ranked passages and `read_url` for extracted HTML/PDF text.
- Installs as a two-container Docker Compose stack with local-only host port defaults.
- Adds Linux/macOS CI on Python 3.10 and 3.13 and an installable GitHub release ZIP.
- Pins FastMCP to the verified 4.1.6 API rather than leaving an unsupported lower bound.
- Supports Python 3.10 via the conditional `tomli` parser backport and stabilizes the settings reload test.
