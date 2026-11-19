# Changes

## 0.2.0

web-retrieval-mcp is now **webSpell**: the same retrieval engine, delivered as a Python
library (`import webspell`) and as the standalone MCP server, both from one wheel. The
repository was renamed to `alexchwong/webSpell`; the old URL redirects.

- **Library.** A public API (interface version 1): `search_results`, `evidence_passages`,
  `page_text`, `call` (threaded, with `cancel` and `deadline_s`), `TOOLS` and `result_bytes`.
  Settings are passed in (`Settings.from_mapping`, `load_settings`); importing has no side
  effects. The MCP server is a thin adapter over it and advertises exactly `TOOLS`.
- **Packaging.** A `webspell-0.2.0-py3-none-any.whl` wheel, MIT licensed, attached to the
  GitHub release with its sha256 next to the ZIP. Not published on PyPI. Core requirements
  are `httpx` and `trafilatura`; extras `pdf` (pypdfium2) and `server` (fastmcp). The
  server's Python packages fall from about 376 MB to about 137 MB.
- **PDF engine.** pypdfium2 replaces PyMuPDF. Text can differ in spacing and multi-column
  reading order; see `docs/pdf_engine_report_0.2.0.md`. Without the `pdf` extra a PDF
  source reports `pdf_unavailable`.
- **Ranking.** A built-in BM25 replaces rank-bm25 (and NumPy) with bit-identical scores.
- **`web_page_text`** accepts `start` and `max_chars` and returns `total_chars`, `start` and
  `next_start`.
- **Search is opt-in.** `search.searxng_url` has no default; without it both search paths
  return `search_not_configured`. The Compose settings set it, so the stack behaves as before.
- **Deadlines and Stop.** `deadline_s` lowers a call's budget (new outcome
  `deadline_exceeded` when it expires before search answers); `call` stops network and DNS
  work promptly when `cancel` is set and returns `{"status": "cancelled"}`. `web_page_text`
  now bounds waiting and extraction.
- **Process-wide limits.** At most 8 DNS lookups, 16 documents in memory, six HTML parsers
  and one PDF parser are active at a time in a process. A single request is unaffected.
- **Result size.** Optional `output.max_result_bytes` (and `output.ascii_json`) bound every
  result in bytes; reduced results carry `output_capped`, and `output_limit` is returned only
  when required fields leave no room.
- **Bounds on one call.** `query` is at most 1,000 characters and each URL at most 4,096
  (`invalid_input` otherwise). The search request asks for an uncompressed response; a
  compressed one, or one over 2,000,000 bytes, gives `search_backend_failed`; only the first
  200 entries are considered.
- **Security.** FastMCP's Host/Origin guard is on, in strict mode, for direct runs and the
  Compose stack. `MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` add to the loopback defaults;
  Compose adds its service name. The search request no longer follows `HTTP_PROXY`,
  `HTTPS_PROXY`, `ALL_PROXY`, `SSL_CERT_FILE` or `SSL_CERT_DIR`.
- **Server defaults.** Run directly (`python -m webspell`) the server binds `127.0.0.1` and,
  with no settings file, does not assume SearXNG at `localhost:8888`. The default server
  name is `webspell`; `MCP_SERVER_NAME=web-retrieval` restores the old one.
- The default `fetch.user_agent` is `webSpell/0.2`. Arguments of the wrong type are
  rejected with `invalid_input` by webSpell's own validator.
- Compose settings moved from `web-retrieval/settings.toml` to `docker/settings.toml`; the
  Compose service keeps the name `web-retrieval`. The image is built from the wheel.

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
