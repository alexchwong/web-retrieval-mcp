# web-retrieval-mcp

**Version 0.1.1.** A small, local-first web research MCP server for AI clients
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
   page and download the **attached** `web-retrieval-mcp-0.1.1.zip` asset for
   this version (not GitHub's automatically generated source-code ZIP).
2. From the directory containing the downloaded ZIP, run:

   ```bash
   unzip web-retrieval-mcp-0.1.1.zip
   cd web-retrieval-mcp-0.1.1
   ./setup.sh
   ```

   If you extracted the ZIP using a graphical file manager, open a terminal
   in the extracted `web-retrieval-mcp-0.1.1` folder and run `./setup.sh`.

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

## Exported MCP tools

| Tool | When to call it |
|---|---|
| `web_search_results(query, recency=None, domains=None)` | Preview search hits and snippets when choosing or refining a query. It opens no result pages. |
| `web_evidence_passages(query, max_passages=None, recency=None, domains=None, urls=None)` | Retrieve ranked evidence passages from the supplied URLs or search the web to find sources. Defaults request eight passages, up to 20, with at most two per source URL. |
| `web_page_text(url)` | Read a known public HTML/PDF URL. Defaults cap the document at 40,000 characters and inspect up to 40 PDF pages. |

On the discovery path, both search tools accept `recency` values such as `1w`, `6m` or `2y` as upstream search preferences with local checks on known publication dates. For a targeted search use `domains=["who.int"]` to restrict the source host and its subdomains. Multiple domains filter the first general result page and may miss relevant sites. Use fetched passages for evidence and treat snippets as discovery material. Check `status`, individual `fetch_status` values, `truncated` and `html_input_capped` when interpreting sparse results.

To retrieve passages from selected sources, call `web_evidence_passages` with arguments such as `{"query":"example domains","urls":["https://example.com/"],"max_passages":2}`. An empty URL list uses web search. With supplied URLs, omit `recency` and `domains`. Read the `final_url` field when a safe redirect leads to a different page.

## Upgrade from 0.1.0

Update saved prompts, workflows and direct tool calls from `search_web` to `web_evidence_passages` and from `read_url` to `web_page_text`. Refresh cached tool lists by reconnecting or refreshing the MCP server in the client. The server URL is unchanged. `web_search_results` is the new discovery-only tool.

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

For the latest published version, download the attached versioned ZIP from [GitHub Releases](https://github.com/alexchwong/web-retrieval-mcp/releases). Extract to a new directory, copy the old `.env` securely, and merge custom TOML and SearXNG configuration into the new defaults. Stop the old stack from its old directory before running `./setup.sh` in the new directory. Run the sanity checks before removing the old install. To roll back, stop the new stack and start the old directory with its own settings and image rebuilt if needed.

For a clone tracking `devel`, inspect `git status -sb`, preserve local config edits, run `git pull --ff-only` on the intended branch, then run `docker compose up -d --build`. `devel` tracks development and can be ahead of the published release. To refresh the SearXNG image separately, run `docker compose pull searxng` followed by `docker compose up -d`. A simple restart reuses the existing container configuration. If an editor replaced the bind-mounted settings file and reload cannot see the edit, run `docker compose up -d --force-recreate web-retrieval`.

## Configuration and safety

- `.env` controls host ports and SearxNG secrets; Compose changes generally
  require recreating containers.
- `web-retrieval/settings.toml` controls search, fetch and ranking behaviour;
  edits are checked at each call with [known reload caveats](docs/configuration.md#live-reload).
- **Private/local use only.** There is no MCP authentication, and outbound document checks are one layer of protection. Do not publicly expose port 8765.
  Review [security](docs/security.md) before changing network access.

## CLI sanity checks

From the installation directory, after the stack starts:

```bash
bash scripts/smoke_mcp.sh
bash scripts/smoke_mcp.sh web_search_results
bash scripts/smoke_mcp.sh web_evidence_passages
bash scripts/smoke_mcp.sh web_evidence_passages_urls
bash scripts/smoke_mcp.sh web_page_text
```

These live checks call the actual Streamable HTTP endpoint from inside the MCP container, using its installed FastMCP client. Host Python, Node and an LLM are unnecessary. The optional argument selects one check plus registration. The default runs all three tools and checks both evidence modes. `web_evidence_passages_urls` is a script selector for the supplied-URL check, which can run with SearXNG offline. Override `SMOKE_QUERY` and `SMOKE_URL` for local search settings or when upstream services block the defaults. Each check reports PASS or FAIL and a failing check exits nonzero. Allow SearXNG to finish starting before retrying. Live search and public page availability can affect the outcome.

## Documentation

- [Client setup](docs/clients.md)
- [Configuration and tool contracts](docs/configuration.md)
- [Operations, releases and troubleshooting](docs/operations.md)
- [Security and privacy](docs/security.md)
- [Changes](NEWS.md)
- [Development rules](DEVEL.md)

Not affiliated with SearxNG, llama.cpp or any MCP client vendor.
