# web-retrieval-mcp

A small, local-first **web research MCP server** for AI clients that support
**Streamable HTTP**. It searches with SearxNG, retrieves HTML/PDF text, and
returns ranked passages with source URLs. No paid search API or API key is needed.

```text
AI client -- Streamable HTTP --> web-retrieval -- search --> SearxNG --> web
                                  |                 \
                                  +-- fetch HTML/PDF +-- trafilatura / PyMuPDF
                                  +-- chunk and BM25-rank passages
```

## Quickstart

Requires **Git, Bash, OpenSSL, Docker and Docker Compose**. Docker must be
running, and the first build needs internet access to pull images and Python
packages. On Windows, use a suitable Bash/WSL2 environment with Docker access.

```bash
git clone https://github.com/alexchwong/web-retrieval-mcp.git
cd web-retrieval-mcp
./setup.sh
```

`setup.sh` creates a private `.env` with a random SearxNG secret on first run;
later runs preserve it. It then starts the two-container Compose stack.

With the shipped defaults:

- **MCP endpoint:** `http://127.0.0.1:8765/mcp` (no trailing slash)
- **SearxNG:** `http://127.0.0.1:8888`

Check the services with `docker compose ps`. Then run the **[pass/fail connection
checks](docs/operations.md#verify-the-installation)** to confirm that SearxNG
answers and MCP advertises both tools. Initial SearxNG startup may take a moment.

## Connect an AI client

Configure a **Streamable HTTP** MCP server whose URL is
`http://127.0.0.1:8765/mcp`. The endpoint is shared, but **client configuration
formats differ** (some use `servers`, others `mcpServers`, and some use a UI).
See [client-specific examples](docs/clients.md) for Cline, VS Code, Cursor,
LM Studio, Claude Code and llama.cpp WebUI.

The URL must be reachable **from the client process**: `127.0.0.1` inside a
separate container or remote machine is not your host computer.

## Tools

| Tool | Use |
|---|---|
| `search_web(query, max_passages=None)` | Discover sources and obtain short BM25-ranked passages. Default **8** returned passages requested, configurable maximum **20**; actual count may be lower. |
| `read_url(url)` | Fetch **one** HTML/PDF URL and return a **capped** text extract (default maximum 40,000 characters and 40 PDF pages). |

`search_web` considers up to eight search-result URLs by default, downloads
eligible pages concurrently, chunks them (default 350 words with 50-word
overlap), and limits returned passages from any one source (default two).
`read_url` is for fuller inspection of a known URL, **not** unrestricted
full-document access. See [configuration and tool limits](docs/configuration.md).

## Configuration and safety

- `.env`: host ports/bind addresses and SearxNG secret; changing Compose
  settings generally requires **recreating** containers.
- `web-retrieval/settings.toml`: search, fetch, chunking and ranking options;
  changes are **checked** on each tool call (with [reload caveats](docs/configuration.md#live-reload)).
- **Keep this service local/private.** It has **no authentication** and
  `read_url` does **not** prevent requests to internal network addresses.
  Do not expose port 8765 publicly. Read [security](docs/security.md).

## Reference

- [Client setup](docs/clients.md)
- [Configuration and tool contracts](docs/configuration.md)
- [Operations, verification and troubleshooting](docs/operations.md)
- [Security and privacy](docs/security.md)

This project is not affiliated with SearxNG, llama.cpp or any MCP client vendor.
