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
To pull a newer SearxNG image (Compose defaults to `latest`), run
`docker compose pull searxng` before `docker compose up -d --build`.

**GitHub release ZIP installations:** download a **new** attached versioned ZIP
from [Releases](https://github.com/alexchwong/web-retrieval-mcp/releases),
extract into a new directory, and copy your previous `.env` and any custom
`web-retrieval/settings.toml` changes into the new folder before running
`./setup.sh`. Do not overwrite newly released defaults without checking your
changes. Stop the old stack with `docker compose down` from its old directory
before starting the new one, to avoid host-port conflicts. The two folders
should not be run at the same time with default ports.

**Git clone installations:** inspect local modifications with `git status`,
then update with your normal `git pull` procedure and run
`docker compose up -d --build`.

There is no complete Python dependency lockfile: rebuilds can resolve newer
versions of dependencies other than the pinned FastMCP. `setup.sh` keeps your
existing secret, while the packaged `.env.example` does not contain one.

## Verify the installation

There is no `/health` endpoint in version 0.1.0. The following checks do not
require Python or MCP packages on the **host**: they use the container's Python
and FastMCP environment. They verify independent layers:

1. Confirm both containers are running with `docker compose ps`.
2. Run the following from the repository root:

```bash
docker compose exec -T web-retrieval python - <<'PY'
import asyncio
import httpx
from fastmcp import Client

async def check():
    async with httpx.AsyncClient(timeout=15) as http:
        response = await http.get(
            "http://searxng:8080/search",
            params={"q": "test", "format": "json"},
        )
        response.raise_for_status()
        payload = response.json()
        assert isinstance(payload.get("results"), list), "missing results array"
    print("PASS: SearxNG returned JSON search results")

    async with Client("http://127.0.0.1:8765/mcp") as client:
        names = {tool.name for tool in await client.list_tools()}
    required = {"search_web", "read_url"}
    assert required <= names, f"MCP tools missing: {required - names}"
    print("PASS: Streamable HTTP MCP advertises both tools")

asyncio.run(check())
PY
```

Expect **two `PASS` lines and exit code 0**. An exception or assertion is a
failure. These tests do not verify upstream search quality, PDF extraction
accuracy or individual AI clients. After they pass, make one live tool call
from your client. If using an external SearxNG instance, adjust the SearxNG
check to that server's URL.

## Troubleshooting

| Symptom | First check / action |
|---|---|
| `docker compose` unavailable | Install Docker Compose plugin and start the Docker daemon. |
| `SEARXNG_SECRET` missing | Run `./setup.sh`; do not paste `.env` secrets into logs or issues. |
| Port 8765 or 8888 in use | Stop the previous stack or change host ports in `.env`, then `docker compose up -d`. |
| MCP connection fails / tools missing | Use `/mcp` without a trailing slash; inspect `docker compose logs web-retrieval`; check the client's network path. |
| Browser reports CORS | Check client/proxy support. Opening the port publicly does not solve CORS safely. |
| `searxng unreachable` | Inspect SearxNG logs, verify JSON search and allow startup time. |
| Sources but no passages | Fetches may be blocked or require JavaScript; snippets can exist without extracted pages. |
| Empty search results | Check SearxNG JSON output/logs and possible upstream engine rate limits. |
| Blank PDF output | Scanned PDFs require OCR, which is not implemented. Malformed/encrypted PDFs can also fail. |
| TOML edit ignored | Look for validation errors. A same-size edit with an unchanged timestamp may go unnoticed; save again/restart. |
| Container client cannot use `localhost` | Join the client's container to the Compose network and use `web-retrieval:8765` or configure a private network path. |

## Uninstall and local files

`docker compose down` removes containers and network, preserving `.env` and
configuration on disk. Back up any custom settings before deleting the old
installation folder. There is no application database or index to migrate.
Avoid broad Docker prune commands just to uninstall this service.

## Developer tests and CI

CI runs on pushes/PRs targeting **`devel` or `main`** and supports a four-way
matrix: **Linux/macOS × Python 3.10/3.13**. For a local offline test run,
install the dependencies in an isolated environment and use:

```bash
python -m pip install -r web-retrieval/requirements.txt
python -m compileall -q web-retrieval
python -m unittest discover -s web-retrieval -p 'test_*.py' -v
```

The offline tests check settings validation and reload, MCP registration and
basic stateless route construction; they do **not** require SearxNG/network
access. They are not a replacement for the Docker connectivity check above.

## Creating a release

Version control follows NEL's small release convention:

1. Develop and test on **`devel`**; update `release/VERSION` to the next `X.Y.Z`
   version and add its change notes to `NEWS.md` before merging to `main`.
2. CI must pass on the PR. Merge to **`main`**.
3. The [release workflow](https://github.com/alexchwong/web-retrieval-mcp/blob/main/.github/workflows/release.yml) validates the
   version and payload, runs offline tests, builds `web-retrieval-mcp-X.Y.Z.zip`
   containing the installable project files, checks its members, and publishes
   the asset on the GitHub release tagged **`vX.Y.Z`**.
4. If this version already exists, publishing is skipped if the release files
   are unchanged. Changed release files without a version bump **fail** rather
   than overwriting the existing GitHub release.

The release archive contains only tracked files listed in
[`release/files.txt`](../release/files.txt). It includes Compose, templates,
source, tests, documentation, `setup.sh` and version metadata, but **not**
`.env` secrets or GitHub workflow files. `workflow_dispatch` can run the release
workflow manually; it still releases the current `main` commit, not `devel`.

**Release caveat:** A GitHub release is only available after the files have
been committed/merged into `main` and its release workflow has succeeded.
The local `devel` branch alone does not create a release asset.
