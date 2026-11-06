# Operations and troubleshooting

[Back to README](../README.md)

Run commands from the repository root unless otherwise stated.

## Start, stop, logs, update

```bash
./setup.sh                              # create .env if missing; build/start stack
docker compose ps                       # service status and published ports
docker compose logs --tail=80 searxng   # SearxNG startup/search errors
docker compose logs --tail=80 web-retrieval
docker compose down                     # stop/remove containers, keep local files
```

To start again with the existing `.env`: `docker compose up -d`. To rebuild
Python dependencies after updating source: `docker compose up -d --build`.
To fetch a newer SearxNG image (the Compose file defaults to `latest`), run
`docker compose pull searxng` before `docker compose up -d --build`.

For a source update on an ordinary clone, review local changes first with
`git status`; then use your usual `git pull` process followed by
`docker compose up -d --build`. **No automatic dependency lock is supplied**:
a rebuild at a later date can resolve different Python package versions.

## Verify the installation

There is no `/health` endpoint in this version. These checks do not require
installing Python or MCP packages on the **host**; they use Python and FastMCP
already in the `web-retrieval` container. They verify two independent layers:

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

Expect **two `PASS` lines and exit code 0**. An exception or assertion means
at least one layer has not passed. This checks service connectivity and MCP
discovery, **not** whether upstream web search engines will return useful
results, whether PDFs extract properly, or whether any particular AI client
can connect. Run one real tool call from your client after this test.

If you changed default addresses or provided an external SearxNG instance,
adapt the check URLs. The SearxNG URL in this check refers to the **bundled
service**, not whatever `search.searxng_url` might point to.

## Troubleshooting

| Symptom | First check / action |
|---|---|
| `docker compose` not available | Install Docker Compose plugin; Docker daemon must be running. |
| `SEARXNG_SECRET` missing | Run `./setup.sh`; inspect `.env` locally without pasting secrets into logs or issues. |
| Port 8765 or 8888 is already in use | Change host ports in `.env`, then `docker compose up -d` to recreate containers. Update client URLs. |
| MCP connection fails / no tools listed | Use `/mcp` **without** a trailing slash; check `docker compose logs web-retrieval`; ensure the client can reach the URL from its own process/network. |
| Browser reports CORS | Check whether the client supports a local HTTP proxy. CORS is not solved by adding `/mcp/` or opening the port to the internet. |
| `search_web` reports `searxng unreachable` | Inspect `docker compose logs searxng` and try the SearxNG half of the verification; allow startup time. |
| Search returns no passages but some sources | Upstream websites may block fetches, require JavaScript or return unsupported formats; sources may contain only search snippets. |
| Search gives empty results | Check SearxNG JSON output and logs; upstream engines may rate-limit the host or change behavior. |
| PDF returns empty text | Scanned/image-only PDFs need OCR, which is **not** implemented; PDF can also be malformed or encrypted. |
| TOML change seems ignored | Inspect MCP logs for validation failure; timestamp-plus-size fingerprint can miss a rapid same-size edit. Save again or restart the MCP container. |
| Dockerized client cannot use `localhost` | `localhost` means the **client container**. Put it on the same Compose network and use `web-retrieval:8765`, or design a secure, tested network path. |

## Uninstall and local files

`docker compose down` removes the containers and the Compose network; the
project's `.env` and committed configuration files remain on disk. If you
choose to remove the local project directory afterward, back up any edited
`settings.toml` and `.env` values you want to retain. This stack has no
application database, index or cache to migrate. Removing downloaded images
is a separate Docker maintenance decision; do not use aggressive Docker prune
commands merely to uninstall this project.

## Developer test

The repository includes a settings-only unittest module, not a full MCP
integration suite. With Python **3.11+** on a development machine:

```bash
cd web-retrieval
python -m unittest -v test_settings
```

A reported rapid-edit timing issue can make the live-reload test fail on some
filesystems; that is a **runtime maintenance issue**, not proof that the docs
or the MCP transport are wrong. Client examples in [clients.md](clients.md)
were checked against vendor docs but not executed end-to-end in this change.
