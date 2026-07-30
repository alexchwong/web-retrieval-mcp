# web-retrieval-mcp

A small, stateless web-research backend for an LLM using llama.cpp's WebUI.
The model calls MCP tools to search through SearxNG, download useful HTML pages
or PDFs, extract their text, and return only the most relevant passages.

```text
Browser ─▶ llama-server (WebUI + MCP client + CORS proxy)
                        │  Streamable HTTP, stateless
                        ▼
              web-retrieval ─▶ SearxNG ─▶ open web
                  │       └─▶ trafilatura / PyMuPDF
                  └─▶ BM25 ranking (in memory, per call)
```

## Services

Docker Compose starts two services:

- **SearxNG** provides private metasearch and JSON search results.
- **web-retrieval** exposes the `search_web` and `read_url` MCP tools through the
  `web-retrieval` MCP server identity.

No research index or cache is retained between calls. Compose creates its
standard project network automatically, and both host ports bind to loopback by
default.

## Prerequisites

- Docker with Docker Compose
- llama.cpp built after **6 March 2026** (PR #18655, MCP client in WebUI)

Verify llama.cpp support with:

```bash
./llama-server --help | grep -i webui-mcp-proxy
```

## Configuration

The committed runtime files contain generic local defaults. Each runtime config
also has a committed template:

| Runtime file | Template | Purpose |
|---|---|---|
| `.env` | `.env.example` | Ignored local ports, secret, and startup-only identity settings |
| `docker-compose.yml` | `docker-compose.yml.example` | Container topology and environment wiring |
| `web-retrieval/settings.toml` | `web-retrieval/settings.toml.example` | Live-reloadable research policy and tuning |
| `searxng/settings.yml` | `searxng/settings.yml.example` | Minimal SearxNG overrides |
| `searxng/limiter.toml` | `searxng/limiter.toml.example` | Optional limiter overrides |

`.env` is intentionally ignored. Do not commit it.

SearxNG uses `use_default_settings: true`; this repository overrides only the
search output formats because `web-retrieval` requires JSON. Secrets and public
URLs are supplied through official SearxNG environment variables rather than
being embedded in YAML.

### Initial setup

Run the setup script from the repository root:

```bash
./setup.sh
```

On the first run, it creates the ignored `.env` from `.env.example`, generates a
fresh 32-byte SearxNG secret, restricts the file permissions, and starts the
Compose stack. Later runs preserve the existing `.env` and its secret.

To perform the same setup manually:

```bash
cp .env.example .env
sed -i "s/replace-with-a-random-secret/$(openssl rand -hex 32)/" .env
chmod 600 .env
docker compose up -d --build
```

The generic defaults expose:

- SearxNG at `http://localhost:8888`
- MCP at `http://localhost:8765/mcp`

To use different ports or bind addresses, edit `.env`. If the SearxNG host port
changes, update `SEARXNG_BASE_URL` to match it.

## Connect llama.cpp WebUI

Start llama-server on the host. The model path and runtime options are specific
to your machine:

```bash
./llama-server \
    -m /path/to/model.gguf \
    --host 127.0.0.1 --port 8080 \
    --jinja \
    --webui-mcp-proxy
```

Then:

1. Open `http://localhost:8080`.
2. Go to **Settings → MCP servers → Add server**.
3. Select **Streamable HTTP**.
4. Enter `http://localhost:8765/mcp` (or your `MCP_HOST_PORT`).
5. Enable **Use llama-server proxy**.
6. Enable the agentic loop and select the MCP tools.

## Tools

### `search_web(query, max_passages=None)`

Searches SearxNG, fetches useful results concurrently, extracts HTML/PDF text,
chunks it, ranks passages with BM25, and returns passages with source URLs. At
most two passages are returned from any one URL by default for light source
diversity. The calling LLM can optionally select `max_passages`; omitting it uses
`ranking.default_max_passages` from the current settings file. The server always
bounds it by the operator-controlled `ranking.max_passages`.

`search.num_results` and `max_passages` apply at different stages:

- `search.num_results` is the number of SearxNG result URLs considered and
  controls network breadth, latency, and resource use. It is operator-controlled.
- `max_passages` is the number of ranked chunks returned to the LLM and controls
  response/context size. It is the only per-call tuning argument exposed to the
  LLM.

### `read_url(url)`

Fetches one HTTP(S) HTML page or PDF and returns cleaned text. It is intended as
an escape hatch when one research result needs fuller inspection. The LLM only
supplies `url`. The configured `fetch.max_bytes` caps the download,
`fetch.max_document_chars` caps the returned extracted text, and
`fetch.max_pdf_pages` additionally caps PDF extraction. The character limit is
not an exact word limit; 40,000 English characters are roughly 6,000–8,000 words.

## Sanity checks

With the default ports:

```bash
curl -fsS 'http://localhost:8888/search?q=test&format=json' \
  | jq '.results[0].url'

curl -fsS -X POST http://localhost:8765/mcp \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}' \
  | head -c 400
echo
```

For non-default ports, substitute the values from `.env`.

## Tuning

Edit `web-retrieval/settings.toml` to tune runtime behavior. Compose bind-mounts
this file read-only inside the container; it remains editable on the host. The
MCP checks the file at the start of each tool call. A valid change applies to
that next call without a restart. Each in-flight call keeps one immutable
settings snapshot, so old and new values are never mixed within a request.

If an edit is invalid TOML or fails validation, the error is logged and the MCP
continues using its last known-good settings. Correct and save the file again to
reload it. The `MCP_SETTINGS_FILE` environment variable can select a different
path when running outside the provided Compose setup.

| TOML setting | Default | Effect |
|---|---:|---|
| `search.searxng_url` | `http://searxng:8080` | SearxNG endpoint |
| `search.num_results` | 8 | Search-result URLs considered |
| `search.timeout_seconds` | 10.0 | SearxNG request timeout |
| `search.language` | `en` | Search language sent to SearxNG |
| `search.safesearch` | 0 | SearxNG safe-search level (`0`, `1`, or `2`) |
| `fetch.concurrency` | 6 | Concurrent page downloads during research |
| `fetch.timeout_seconds` | 12.0 | Per-page timeout in seconds |
| `fetch.max_bytes` | 20000000 | Maximum download size per URL |
| `fetch.max_document_chars` | 40000 | Extracted characters retained/returned per document |
| `fetch.max_pdf_pages` | 40 | Maximum PDF pages extracted |
| `fetch.user_agent` | `web-retrieval/1.0` | HTTP user agent for page downloads |
| `chunk.words` | 350 | Approximate words per ranked passage |
| `chunk.overlap` | 50 | Words shared by adjacent passages |
| `ranking.default_max_passages` | 8 | Passages returned when the LLM omits the argument |
| `ranking.max_passages` | 20 | Maximum value the LLM may request |
| `ranking.max_passages_per_source` | 2 | Returned passages allowed per source URL |

Listener settings (`MCP_HOST`, `MCP_PORT`, the host port/bind address, and
`MCP_SERVER_NAME`) are startup-only because they construct the socket and MCP
server identity. Restart `web-retrieval` after changing them:

```bash
docker compose restart web-retrieval
```

## Limitations

- JavaScript-heavy pages are not rendered; fetching uses plain HTTP.
- Scanned image-only PDFs have no OCR step.
- Ranking is lexical BM25 rather than embedding-based semantic ranking.
- Repeated queries refetch their sources; there is no application cache.
- `robots.txt` is not enforced. Keep this private/local unless that and the
  wider security model are addressed.
- Before public exposure, review secrets, authentication, trusted proxies,
  rate limiting, TLS, and network controls.
