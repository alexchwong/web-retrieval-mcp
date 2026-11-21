# Operations and troubleshooting

[Back to README](../README.md)

Run commands from the repository root unless otherwise stated.

## Start, stop, logs and update

```bash
./setup.sh                              # create .env if missing; build/start stack
docker compose ps                       # service status and published ports
docker compose logs --tail=80 searxng   # SearxNG startup/search errors
docker compose logs --tail=80 web-retrieval
docker compose down                     # stop/remove containers; keep local files
```

Start again with the existing `.env` using `docker compose up -d`. Rebuild
Python dependencies after updating source with `docker compose up -d --build`.
The 0.2.1 Compose files pin the upstream SearXNG image by digest. An existing
`SEARXNG_IMAGE` in `.env` takes precedence. Review that override, then run
`docker compose pull searxng` and `docker compose up -d --build`. To confirm
the running version and image, use the SearXNG `/config` endpoint and
`docker inspect "$(docker compose ps -q searxng)" --format '{{.Config.Image}} {{.Image}}'`.

**GitHub release ZIP installations:** download a **new** attached versioned ZIP
from [Releases](https://github.com/alexchwong/webSpell/releases),
extract into a new directory, and copy your previous `.env` and any custom
`docker/settings.toml` changes into the new folder before running
`./setup.sh`. Do not overwrite newly released defaults without checking your
changes. Stop the old stack with `docker compose down` from its old directory
before starting the new one, to avoid host-port conflicts. The two folders
should not be run at the same time with default ports.

**Git clone installations:** inspect local modifications with `git status`,
then update with your normal `git pull` procedure and run
`docker compose up -d --build`.

Upgrading from web-retrieval-mcp 0.1.1: the research settings file moved from
`web-retrieval/settings.toml` to `docker/settings.toml`; merge your edits into the
new file. `.env` gains `MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` (both optional)
and the default `MCP_SERVER_NAME` is now `webspell`.

There is no complete Python dependency lockfile: rebuilds can resolve newer
versions of dependencies other than the pinned FastMCP. `setup.sh` keeps your
existing secret, while the packaged `.env.example` does not contain one.

## Verify the installation

From the installation directory run `docker compose ps`, then
`bash scripts/smoke_mcp.sh`. The checks cover all three tools, including both
evidence modes. `bash scripts/guard_check.sh` checks the request guard: calls
from the host, from inside the container and from a second container using the
service name succeed, and `Host: evil.example` (421) and
`Origin: http://evil.example` (403) are refused before any tool runs. For individual checks, environment overrides and lifecycle
commands, see the [README](../README.md#cli-sanity-checks).

To isolate search-backend startup or configuration problems, check
`docker compose logs --tail=80 searxng` and verify that the configured
SearxNG `/search` endpoint returns a JSON object with a `results` array
for the `q=test&format=json` query.

## Verify general web search

Run the normal MCP smoke and then the general search cohort. The cohort checks
known hosts for ordinary unfiltered queries and checks a separate dated query
for hits and date diagnostics. Run it from the machine that will send production
queries because upstream behavior can vary with the deployment.

```bash
bash scripts/smoke_mcp.sh
bash scripts/smoke_mcp.sh search_general
SMOKE_CATEGORY=science SMOKE_QUERY="Attention Is All You Need arXiv" bash scripts/smoke_mcp.sh web_search_results
SMOKE_CATEGORY=it SMOKE_QUERY="git log GitHub git/git" bash scripts/smoke_mcp.sh web_search_results
SMOKE_CATEGORY=news SMOKE_RECENCY="1 month" SMOKE_QUERY="latest world news" bash scripts/smoke_mcp.sh web_search_results
```

Set `SMOKE_RECENT_QUERY` to change the dated query in the general cohort. A
dated probe checks the search and freshness metadata. Review source dates
separately before relying on freshness. Category probes depend on the enabled
engines and are optional. To isolate the failure from MCP, inspect SearXNG's
JSON using the same category, language and filters. This example uses the
default host port, language and safe-search settings. Add
`--data-urlencode 'time_range=month'` to compare the filtered response.

```bash
curl -fsSG --max-time 15 http://127.0.0.1:8888/search \
  --data-urlencode 'q=Python documentation pathlib Path' \
  --data-urlencode 'format=json' --data-urlencode 'categories=general' \
  --data-urlencode 'language=en' --data-urlencode 'safesearch=0' |
  python3 -c 'import json, sys; d=json.load(sys.stdin); print(len(d["results"]), d.get("unresponsive_engines"))'
```

If general searches remain inconclusive, check which engines report rate
limits, CAPTCHA, or parsing errors. Use the SearXNG engine preferences or probe
one engine at a time from the same machine. Keep healthy general engines
enabled in `searxng/settings.yml` and re-run the cohort after changes. Repeatedly
resubmitting the same request during an engine suspension will not restore it.

## Troubleshooting

| Symptom | First check / action |
|---|---|
| `docker compose` unavailable | Install Docker Compose plugin and start the Docker daemon. |
| `SEARXNG_SECRET` missing | Run `./setup.sh`; do not paste `.env` secrets into logs or issues. |
| Port 8765 or 8888 in use | Stop the previous stack or change host ports in `.env`, then `docker compose up -d`. |
| MCP connection fails / tools missing | Use `/mcp` without a trailing slash; inspect `docker compose logs web-retrieval`; check the client's network path. |
| HTTP 421 Misdirected Request | The client used a `Host` other than loopback or `web-retrieval`. Add it to `MCP_ALLOWED_HOSTS` in `.env` and run `docker compose up -d`. |
| HTTP 403 Forbidden Origin | A browser UI is served from a non-loopback origin. Add its `scheme://host:port` to `MCP_ALLOWED_ORIGINS`. |
| `search_not_configured` | No `search.searxng_url` in the settings in use (a direct run with no `MCP_SETTINGS_FILE`, or a library host). |
| `pdf_unavailable` | The `pdf` extra is not installed (library hosts). The Docker image always installs it. |
| `search_backend_failed` behind a proxy | The search request uses no environment proxy and asks for an uncompressed response; make SearXNG directly reachable and do not compress its responses in a reverse proxy. |
| Browser reports CORS | Check client/proxy support. Opening the port publicly does not solve CORS safely. |
| `searxng unreachable` | Inspect SearxNG logs, verify JSON search and allow startup time. |
| Sources but no passages | Fetches may be blocked or require JavaScript; snippets can exist without extracted pages. |
| `search_inconclusive` or `search.degraded=true` | Inspect `search.attempts` and SearXNG JSON or logs for engine failures. See the general search checks above. |
| Blank PDF output | Scanned PDFs require OCR, which is not implemented. Malformed/encrypted PDFs can also fail. |
| TOML edit ignored | Look for validation errors. A same-size edit with an unchanged timestamp may go unnoticed; save again/restart. |
| Container client cannot use `localhost` | Join the client's container to the Compose network and use `web-retrieval:8765` or configure a private network path. |

## Known ways to degrade

The process-wide limits (8 DNS lookups, 16 documents in memory, 6 HTML parsers and
1 PDF parser) are shared by every call in a server or host process. Each of the
following is a bounded loss; if it persists, restart the server or host.

- **A resolver that stalls** occupies lookup slots until the operating system's
  resolver gives up; other lookups time out meanwhile.
- **A parser that never returns** keeps its parser slot and its document slot until
  the process ends. A parser is bounded by its input, not a timer: HTML input is
  capped at four times `fetch.max_document_chars` (160,000 characters by default)
  and a PDF at `fetch.max_bytes` and `fetch.max_pdf_pages`.
- **Documents waiting behind a blocked PDF parser** hold document slots until their
  calls reach their deadlines, which can delay HTML documents of other calls.

Counting everything left behind by Stop, there are at most 8 lookup threads and 7
parser threads, and at most `16 × fetch.max_bytes` of document bytes (320 MB as
shipped; real pages are far smaller). The interpreter waits for running parser and
lookup threads at exit. No worker process or other parser isolation is used; the
container is the isolated deployment (see [security](security.md)).

## Installed size

Measured by `scripts/measure_install.sh` on Linux and regenerated at release. Docker
and SearXNG image sizes are recorded separately and never added to these figures.
CI on macOS checks only that `webSpell[pdf]` installs and extracts a fixture.

<!-- measure_install:begin -->
Linux x86_64, Python 3.13.18,
versions as resolved on 2026-11-16 by `scripts/measure_install.sh`.

| Install | Size | Packages | Note |
| --- | --- | --- | --- |
| web-retrieval-mcp 0.1.1 `requirements.txt` | 376 MB | 94 | The baseline. |
| **webSpell core** (`httpx`, `trafilatura`) | 64 MB | 23 | The library form. |
| `rank-bm25` | 53 MB | 2 | Replaced by the built-in BM25. |
| `h2`, `hpack`, `hyperframe` | 1 MB | 3 | The `httpx[http2]` extra, dropped. |
| `pymupdf` (2.1) | 251 MB | 12 | Removed. |
| `pypdfium2` 5.15.2 | 9 MB | 1 | The `pdf` extra. |
| `fastmcp` 4.1.6 | 66 MB | 62 | The `server` extra, as pinned in the image. |
| **webSpell core + pdf + server** | 137 MB | 81 | The standalone server image's Python packages. |
<!-- measure_install:end -->

## Uninstall and local files

`docker compose down` removes containers and network, preserving `.env` and
configuration on disk. Back up any custom settings before deleting the old
installation folder. There is no application database or index to migrate.
Avoid broad Docker prune commands just to uninstall this service.

## Developer tests and CI

Developer setup, required commands and maintenance rules are in
[DEVEL.md](../DEVEL.md). Cline's separate handover-review checklist is in the
repository-root `.clinerules`; it does not replace `DEVEL.md`.

CI runs on pushes/PRs targeting **`devel` or `main`** with a four-way matrix:
**Linux/macOS × Python 3.10/3.13**, installing `.[pdf,server,dev]`. Its offline
checks cover settings, the library API and `call`, Stop and deadlines, the
process-wide limits, the search client and its bounds, ranking parity with
rank-bm25, PDF fixtures, the byte limit, the validator against `TOOLS`, MCP
registration and the request guard, outbound URL policy and local TLS verification.
A core-only job runs the same tests without extras (extra-dependent tests skip),
and a packaging job builds the wheel, runs the release checks and installs core,
`[pdf]`, `[server]` and `[server,pdf]` offline from a prepared wheel directory.
They do **not** require SearXNG or external websites and do not replace the
optional Docker connectivity check above.

## Creating a release

Version control follows NEL's small release convention:

1. Develop and test on **`devel`**; update `release/VERSION` to the next `X.Y.Z`
   version and add its change notes to `NEWS.md` before merging to `main`.
2. CI must pass on the PR. Merge to **`main`**.
3. The [release workflow](https://github.com/alexchwong/webSpell/blob/main/.github/workflows/release.yml) validates the
   version and payload, runs offline tests, builds `webspell-X.Y.Z-py3-none-any.whl`,
   runs the release checks (`scripts/check_release.py`: NEWS section, wheel members
   exactly the `webspell` package and licence, requirement groups and
   `License-Expression: MIT`) and the offline install check, builds
   `webSpell-X.Y.Z.zip` from the files in `release/files.txt` plus the wheel in
   `dist/`, checks its members, and publishes the ZIP, the wheel and the wheel's
   sha256 on the GitHub release tagged **`vX.Y.Z`**. There is no PyPI release.
4. If this version already exists, publishing is skipped if the release files
   and package source are unchanged. Changed release files without a version bump **fail** rather
   than overwriting the existing GitHub release.

The release archive contains the tracked files listed in
[`release/files.txt`](../release/files.txt) and the wheel. It includes Compose,
templates, the Dockerfile and `docker/constraints.txt`, documentation, the licence
and third-party notices, `setup.sh`, the smoke and guard scripts and version
metadata. The image is built from the wheel; the ZIP carries no loose source or
tests. Tests and their fixtures remain in the source checkout for CI. The ZIP excludes `.env` secrets and GitHub workflows. `workflow_dispatch` can run the release
workflow manually; it still releases the current `main` commit, not `devel`.

**Release caveat:** A GitHub release is only available after the files have
been committed/merged into `main` and its release workflow has succeeded.
The local `devel` branch alone does not create a release asset.
