# webSpell

**Version 0.2.0.** Local-first web research tools for AI agents: SearXNG search,
HTML/PDF text extraction and BM25-ranked evidence passages with source URLs. No
paid search API is needed. webSpell was called **web-retrieval-mcp** up to 0.1.1;
the repository was renamed and its history kept.

webSpell comes in two forms that run the same code and are equally supported:

1. **A Python library.** `import webspell` gives three async tool functions, a
   threaded `call()` entry point and a tool catalogue (`TOOLS`). A host application
   installs a pinned wheel and calls it in-process. See [the library guide](docs/library.md).
2. **A standalone MCP server.** The same wheel with the `server` extra, run directly
   (`python -m webspell`) or through the Docker Compose stack, which also runs
   SearXNG. Any client that speaks **Streamable HTTP** can use it.

```text
AI client -- Streamable HTTP --> webSpell MCP server ---+
host app  -- import webspell ------------------------+  |
                                                     v  v
                         webspell library -- search --> SearXNG --> web
                           |-- fetch HTML/PDF (trafilatura / pypdfium2)
                           +-- chunk and BM25-rank passages
```

webSpell is distributed from GitHub releases only; **it is not on PyPI**, and a
PyPI package of the same name would not be this project.

## Install the MCP server from a GitHub release ZIP (recommended)

Requires **Bash, OpenSSL, unzip (or a ZIP extractor), Docker and Docker Compose**.
Docker must be running; the first setup needs internet access for images and Python
packages. On Windows, use WSL2 with Docker access.

1. Open the [GitHub Releases](https://github.com/alexchwong/webSpell/releases) page
   and download the **attached** `webSpell-0.2.0.zip` asset (not GitHub's
   automatically generated source-code ZIP).
2. From the directory containing the downloaded ZIP, run:

   ```bash
   unzip webSpell-0.2.0.zip
   cd webSpell-0.2.0
   ./setup.sh
   ```

`setup.sh` generates a local `.env` containing a SearXNG secret on the first run and
starts the two-container Compose stack. The ZIP contains the webSpell wheel, and the
image installs it with the `server` and `pdf` extras. Re-running `setup.sh` preserves
the existing `.env`. See [operations](docs/operations.md) for updates and removal.

### Alternative: clone the development branch

Requires **Git** in addition to the above prerequisites. The image builds the wheel
from the checked-out source:

```bash
git clone --branch devel https://github.com/alexchwong/webSpell.git
cd webSpell
./setup.sh
```

## Install the library

Install the wheel attached to the release, pinned by its sha256 (published next to
it on the release page):

```bash
pip install "webSpell @ https://github.com/alexchwong/webSpell/releases/download/v0.2.0/webspell-0.2.0-py3-none-any.whl"
pip install "webSpell[pdf] @ https://github.com/alexchwong/webSpell/releases/download/v0.2.0/webspell-0.2.0-py3-none-any.whl"
```

| Install | Adds | Use |
|---|---|---|
| `webSpell` | `httpx`, `trafilatura` | HTML pages, search, passages. PDF sources report `pdf_unavailable`. |
| `webSpell[pdf]` | `pypdfium2` | PDF text extraction. |
| `webSpell[server]` | `fastmcp` | `python -m webspell`, the MCP server. |

Python 3.10 to 3.13 on Linux and macOS. Search needs a SearXNG instance with JSON
output enabled and its URL in the settings; without one, page text and supplied-URL
passages still work and the search paths return `search_not_configured`.

```python
import webspell

settings = webspell.Settings.from_mapping({"search": {"searxng_url": "http://localhost:8888"}})
result = webspell.call("web_evidence_passages", {"query": "example domains",
                       "urls": ["https://example.com/"]}, settings=settings, deadline_s=20)
```

## Verify and connect the MCP server

Default addresses of the Compose stack:

- **MCP:** `http://127.0.0.1:8765/mcp` (no trailing slash)
- **SearXNG:** `http://127.0.0.1:8888`

Check service status with `docker compose ps`, then run the
[pass/fail connection checks](docs/operations.md#verify-the-installation). Initial
SearXNG startup can take a moment.

In your AI client, configure a **Streamable HTTP** MCP server at
`http://127.0.0.1:8765/mcp`. Client configurations differ: see
[examples for Cline, VS Code, Cursor, LM Studio, Claude Code and llama.cpp](docs/clients.md).
`127.0.0.1` in a separate container or on another device is **not** your host.

Run directly with the `server` extra, the server binds `127.0.0.1:8765`, uses no
settings file (so search is not configured until you add one) and is named
`webspell`:

```bash
MCP_SETTINGS_FILE=./settings.toml python -m webspell
```

## Tools

| Tool | When to call it |
|---|---|
| `web_search_results(query, recency=None, domains=None)` | Preview search hits and snippets when choosing or refining a query. It opens no result pages. |
| `web_evidence_passages(query, max_passages=None, recency=None, domains=None, urls=None)` | Retrieve ranked evidence passages from the supplied URLs or search the web to find sources. Defaults request eight passages, up to 20, with at most two per source URL. |
| `web_page_text(url, start=0, max_chars=None)` | Read a known public HTML/PDF URL. Defaults cap the document at 40,000 characters and inspect up to 40 PDF pages. `start` and `max_chars` select a slice; follow `next_start` to read on. |

On the discovery path, both search tools accept `recency` values such as `1w`, `6m` or `2y` as upstream search preferences with local checks on known publication dates. For a targeted search use `domains=["who.int"]` to restrict the source host and its subdomains. Multiple domains filter the first general result page and may miss relevant sites. Use fetched passages for evidence and treat snippets as discovery material. Check `status`, individual `fetch_status` values, `truncated` and `html_input_capped` when interpreting sparse results.

To retrieve passages from selected sources, call `web_evidence_passages` with arguments such as `{"query":"example domains","urls":["https://example.com/"],"max_passages":2}`. An empty URL list uses web search. With supplied URLs, omit `recency` and `domains`. Read the `final_url` field when a safe redirect leads to a different page.

A `query` is at most 1,000 characters and a URL at most 4,096. Result fields and
outcomes are described in [configuration and tool contracts](docs/configuration.md).

## Upgrade from web-retrieval-mcp 0.1.1

Same port, same `/mcp` path, same tool names and result shapes. The repository URL
changed to `https://github.com/alexchwong/webSpell` (the old one redirects), the
server's default name is now `webspell` (set `MCP_SERVER_NAME=web-retrieval` to keep
the old one), the research settings moved from `web-retrieval/settings.toml` to
`docker/settings.toml`, and the Host/Origin guard is now on. Every observable change
is listed in [NEWS](NEWS.md) and the [client migration note](docs/clients.md#migrating-from-web-retrieval-mcp-011).

## Start, stop, restart and update

Run from the install directory containing `docker-compose.yml`.

| Operation | Command |
|---|---|
| First install or regenerate a missing local configuration | `./setup.sh` |
| Start the existing stack | `docker compose up -d` |
| Stop only the MCP server | `docker compose stop web-retrieval` |
| Restart only the MCP server | `docker compose restart web-retrieval` |
| Stop and remove the whole stack, keeping local files | `docker compose down` |
| Inspect service status and MCP logs | `docker compose ps` and `docker compose logs --tail=80 web-retrieval` |
| Apply updated source, dependency or Compose configuration | `docker compose up -d --build` |

The Compose service keeps the name `web-retrieval` in 0.2.0 so that containers
addressing it by name keep working.

For the latest published version, download the attached versioned ZIP from [GitHub Releases](https://github.com/alexchwong/webSpell/releases). Extract to a new directory, copy the old `.env` securely, and merge custom TOML and SearXNG configuration into the new defaults. Stop the old stack from its old directory before running `./setup.sh` in the new directory. Run the sanity checks before removing the old install. To roll back, stop the new stack and start the old directory with its own settings and image rebuilt if needed.

For a clone tracking `devel`, inspect `git status -sb`, preserve local config edits, run `git pull --ff-only` on the intended branch, then run `docker compose up -d --build`. `devel` tracks development and can be ahead of the published release. To refresh the SearXNG image separately, run `docker compose pull searxng` followed by `docker compose up -d`. A simple restart reuses the existing container configuration. If an editor replaced the bind-mounted settings file and reload cannot see the edit, run `docker compose up -d --force-recreate web-retrieval`.

## Configuration and safety

- `.env` controls host ports, the SearXNG secret, the server name and the request
  guard's allowlists; Compose changes generally require recreating containers.
- `docker/settings.toml` controls search, fetch, ranking and result-size behaviour;
  edits are checked at each call with [known reload caveats](docs/configuration.md#live-reload).
- **Private/local use only.** There is no MCP authentication. The Host/Origin guard
  stops other web pages reaching the server through DNS rebinding, but any local
  process can still call it. Do not publicly expose port 8765.
  Review [security](docs/security.md) before changing network access.

## CLI sanity checks

From the installation directory, after the stack starts:

```bash
bash scripts/smoke_mcp.sh
bash scripts/smoke_mcp.sh web_search_results
bash scripts/smoke_mcp.sh web_evidence_passages
bash scripts/smoke_mcp.sh web_evidence_passages_urls
bash scripts/smoke_mcp.sh web_page_text
bash scripts/guard_check.sh
```

The smoke checks call the actual Streamable HTTP endpoint from inside the MCP container, using its installed FastMCP client. Host Python, Node and an LLM are unnecessary. The optional argument selects one check plus registration. The default runs all three tools and checks both evidence modes. `web_evidence_passages_urls` is a script selector for the supplied-URL check, which can run with SearXNG offline. Override `SMOKE_QUERY` and `SMOKE_URL` for local search settings or when upstream services block the defaults. Each check reports PASS or FAIL and a failing check exits nonzero. Allow SearXNG to finish starting before retrying. Live search and public page availability can affect the outcome. `guard_check.sh` checks that loopback, in-container and service-name requests are accepted and that foreign `Host` and `Origin` headers are refused before any tool runs.

## Documentation

- [Library guide for host applications](docs/library.md)
- [Client setup](docs/clients.md)
- [Configuration and tool contracts](docs/configuration.md)
- [Operations, releases and troubleshooting](docs/operations.md)
- [Security and privacy](docs/security.md)
- [PDF engine side-by-side report](docs/pdf_engine_report_0.2.0.md)
- [Changes](NEWS.md)
- [Development rules](DEVEL.md)

MIT licensed; see [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md).
Not affiliated with SearXNG, llama.cpp or any MCP client vendor.
