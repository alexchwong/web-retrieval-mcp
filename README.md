# web-retrieval-mcp

**Version 0.1.0.** A small, local-first web research MCP server for AI clients
supporting **Streamable HTTP**. It uses SearxNG search, extracts HTML/PDF text,
and returns BM25-ranked passages with source URLs. No paid search API is needed.

```text
AI client -- Streamable HTTP --> web-retrieval -- search --> SearxNG --> web
                                  |                 \
                                  +-- fetch HTML/PDF +-- trafilatura / PyMuPDF
                                  +-- chunk and BM25-rank passages
```

## Install from a GitHub release ZIP (recommended)

Requires **Bash, OpenSSL, unzip (or a ZIP extractor), Docker and Docker Compose**. Docker must be running;
the first setup needs internet access for images and Python packages. On Windows,
use WSL2 with Docker access.

1. Open the [GitHub Releases](https://github.com/alexchwong/web-retrieval-mcp/releases)
   page and download the **attached** `web-retrieval-mcp-0.1.0.zip` asset for
   this version (not GitHub's automatically generated source-code ZIP).
2. Extract it, open a terminal in the extracted directory, and run:

   ```bash
   unzip web-retrieval-mcp-0.1.0.zip
   cd web-retrieval-mcp-0.1.0
   ./setup.sh
   ```

   If you extracted the ZIP using a graphical file manager, skip `unzip` and
   `cd` and run `./setup.sh` from the extracted project folder.

`setup.sh` generates a local `.env` containing a SearxNG secret on the first
run and starts the two-container Compose stack. Re-running it preserves the
existing `.env`. See [operations](docs/operations.md) for updates and removal.

### Alternative: clone the development branch

Requires **Git** in addition to the above prerequisites:

```bash
git clone --branch devel https://github.com/alexchwong/web-retrieval-mcp.git
cd web-retrieval-mcp
./setup.sh
```

Version numbers are recorded in [`release/VERSION`](release/VERSION) and
[`NEWS.md`](NEWS.md). Merges into `main` publish a versioned release ZIP when
this version has not already been released. Published releases are immutable;
a change to bundled files requires a version bump.

## Verify and connect

Default addresses:

- **MCP:** `http://127.0.0.1:8765/mcp` (no trailing slash)
- **SearxNG:** `http://127.0.0.1:8888`

Check service status with `docker compose ps`, then run the
[pass/fail connection checks](docs/operations.md#verify-the-installation).
Initial SearxNG startup can take a moment.

In your AI client, configure a **Streamable HTTP** MCP server at
`http://127.0.0.1:8765/mcp`. Client configurations differ: see
[examples for Cline, VS Code, Cursor, LM Studio, Claude Code and llama.cpp](docs/clients.md).
`127.0.0.1` in a separate container or on another device is **not** your host.

## Tools

| Tool | Use |
|---|---|
| `search_web(query, max_passages=None)` | Find and rank short source passages (default 8 requested, maximum 20; actual results may be fewer). |
| `read_url(url)` | Extract text from one HTML/PDF URL (default maximum 40,000 characters and 40 PDF pages). |

Search examines up to eight result URLs by default, ranks roughly 350-word
chunks with BM25 and returns at most two passages per URL. See
[configuration and tool contracts](docs/configuration.md).

## Configuration and safety

- `.env` controls host ports and SearxNG secrets; Compose changes generally
  require recreating containers.
- `web-retrieval/settings.toml` controls search, fetch and ranking behaviour;
  edits are checked at each call with [known reload caveats](docs/configuration.md#live-reload).
- **Private/local use only.** There is no MCP authentication, and `read_url`
  does not block internal network addresses. Do not publicly expose port 8765.
  Review [security](docs/security.md) before changing network access.

## Documentation

- [Client setup](docs/clients.md)
- [Configuration and tool contracts](docs/configuration.md)
- [Operations, releases and troubleshooting](docs/operations.md)
- [Security and privacy](docs/security.md)
- [Changes](NEWS.md)

Not affiliated with SearxNG, llama.cpp or any MCP client vendor.
