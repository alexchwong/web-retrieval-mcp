# Development rules — webSpell

Read this file before changing the repository. Keep this project small: it is a three-tool, local-first web retrieval library and MCP server, not a general agent framework. Both forms (library and server) are first-class: do not make one worse to improve the other.

`.clinerules` contains only Cline's coding-agent handover-review checklist; it must not duplicate or override these general rules.

## Scope and workflow

- Inspect the current branch, working tree, and relevant code before editing. Preserve uncommitted work.
- Make the smallest change that fulfils the request. Avoid unrelated refactors, new frameworks, or speculative features.
- Do not commit, push, merge, tag, or publish unless the owner explicitly requests it.
- Report changed files, tests actually run and their outcomes, and any unresolved risks. Do not describe skipped or unavailable checks as passed.

## Runtime boundaries

- Keep `web_search_results`, `web_evidence_passages` and `web_page_text` as the three public tools unless a change is explicitly approved. Preserve their documented inputs and output shapes. Names, argument names and result fields of interface version 1 change only with an `INTERFACE_VERSION` increase; adding a result field is not a change.
- `webspell/api.py` holds the tool logic and `TOOLS`; `webspell/server.py` is a thin FastMCP adapter and the only module that reads the environment or configures logging. `import webspell` must have no side effects (`tests/test_import.py`).
- `webspell/validate.py` must stay in step with each `input_schema` in `TOOLS` (`tests/test_validate.py`).
- Preserve stateless Streamable HTTP on `/mcp`, the SearxNG dependency, HTML/PDF extraction, and bounded BM25 passage retrieval unless the task calls for changing them.
- Default host-port bindings are loopback-only. Do not expose the unauthenticated MCP server publicly and preserve the public-destination policy and redirect checks.
- Keep `.env` and real secrets out of Git. Do not introduce paid services, model dependencies, or new required packages without the owner's approval. Core requirements are `httpx` and `trafilatura`; `pypdfium2` is the `pdf` extra and `fastmcp` the `server` extra. Do not import an extra's package at `import webspell`.
- Support **Linux and macOS, Python 3.10 and 3.13**. Python 3.10 requires the existing `tomli` fallback; do not introduce unguarded Python 3.11+ APIs. Docker currently uses Python 3.12.

## Checks

From the repository root, use an isolated virtual environment:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[pdf,server,dev]'
python -m compileall -q webspell tests scripts
python -m unittest discover -s tests -t . -v
```

Packaging checks (as CI's packaging job):

```bash
python -m pip install build
python -m build --wheel --outdir dist .
python scripts/check_release.py dist/webspell-*-py3-none-any.whl
bash scripts/offline_install_check.sh dist/webspell-*-py3-none-any.whl
```

- Run focused offline regression tests for changed behaviour. Keep tests deterministic and independent of live SearxNG, external websites, or Docker unless explicitly testing the integration.
- CI runs the offline checks on Linux/macOS × Python 3.10/3.13 (`.github/workflows/ci.yml`). A local pass does not establish that the whole matrix passed.
- Never weaken or delete a failing check merely to obtain a pass.

### Live MCP smoke checks

These optional checks test the running Docker stack and depend on live web
services. Run the commands below from the directory containing `setup.sh`,
with Docker running.

**1. Start this checkout's stack.**

```bash
./setup.sh
```

If setup reports `port is already allocated`, another service is using a host
port (by default, `8888` for SearxNG or `8765` for MCP). To leave that service
running, retry with different host ports:

```bash
SEARXNG_HOST_PORT=18888 MCP_HOST_PORT=18765 ./setup.sh
```

If either alternative is also taken, choose another unused port. The overrides
apply only to that setup command; use the same overrides when rebuilding later
if the default ports are still occupied. The smoke script connects from inside
this checkout's `web-retrieval` container, so its command does not change and
it will not accidentally test another stack listening on the host.

**2. Check that both containers started.**

```bash
docker compose ps -a
```

Both `searxng` and `web-retrieval` should show `Up` in the `STATUS` column.
If either is missing, `Created`, `Exited`, or repeatedly restarting, don't run
the smoke checks yet. Read the error from `./setup.sh` first, then check
`docker compose logs --tail=80 searxng web-retrieval` for startup errors.
`service "web-retrieval" is not running` means the smoke script could not
enter the container; it did not run any MCP checks.

**3. Run the checks.**

```bash
bash scripts/smoke_mcp.sh
bash scripts/smoke_mcp.sh search_general
```

The default run checks registration and all three MCP tools, including both
evidence modes. `search_general` checks ordinary web queries against known
hosts and runs a separate recency probe. Look for `PASS` and `FAIL` lines;
any failed check makes the command exit nonzero. If both containers just came
up and the probe cannot connect, wait a few seconds and retry once. If it
still cannot connect, check `docker compose logs --tail=80 web-retrieval`.

If `PASS MCP tool registration` appears but search fails, the MCP connection
worked. To isolate search and see the full response, run:

```bash
SMOKE_LOG_RESPONSES=1 bash scripts/smoke_mcp.sh web_search_results
```

Check `search.attempts` in the response and run
`docker compose logs --tail=80 searxng`. Live search can fail when upstream
engines don't respond or don't return the expected results. See
[operations](docs/operations.md#verify-general-web-search) for category probes
and direct SearXNG checks. Report live outcomes separately from offline tests.

## Documentation, versions, and releases

- `README.md` is for installation and basic use; `docs/` owns detailed configuration, clients, operations, and security; `NEWS.md` records concise user-visible changes. Update the relevant document when behaviour changes.
- `release/VERSION` and `webspell/_version.py` hold the release version (currently `0.2.1`) and must agree; `scripts/check_release.py` checks this. Use `X.Y.Z` and add a corresponding `NEWS.md` entry when preparing a new release.
- The wheel is built from `pyproject.toml`; the installable ZIP is `release/files.txt` plus the wheel, created by `.github/workflows/release.yml` on `main`, following the NEL-style process. Releases are GitHub-only (no PyPI). Changes to an already published release's bundled files or package source require a version bump; never overwrite a published release.
- At release, regenerate the installed-size table with `bash scripts/measure_install.sh dist/webspell-X.Y.Z-py3-none-any.whl` on Linux.
- `DEVEL.md` and `.clinerules` are developer-only instructions, not part of the current installable ZIP. Changes to those instructions alone do not require a release.
