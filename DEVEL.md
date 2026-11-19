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
- Use `./setup.sh` and the verification steps in `docs/operations.md` for optional live Docker checks; report them separately from offline tests.
- Never weaken or delete a failing check merely to obtain a pass.

## Documentation, versions, and releases

- `README.md` is for installation and basic use; `docs/` owns detailed configuration, clients, operations, and security; `NEWS.md` records concise user-visible changes. Update the relevant document when behaviour changes.
- `release/VERSION` and `webspell/_version.py` hold the release version (currently `0.2.0`) and must agree; `scripts/check_release.py` checks this. Use `X.Y.Z` and add a corresponding `NEWS.md` entry when preparing a new release.
- The wheel is built from `pyproject.toml`; the installable ZIP is `release/files.txt` plus the wheel, created by `.github/workflows/release.yml` on `main`, following the NEL-style process. Releases are GitHub-only (no PyPI). Changes to an already published release's bundled files or package source require a version bump; never overwrite a published release.
- At release, regenerate the installed-size table with `bash scripts/measure_install.sh dist/webspell-X.Y.Z-py3-none-any.whl` on Linux.
- `DEVEL.md` and `.clinerules` are developer-only instructions, not part of the current installable ZIP. Changes to those instructions alone do not require a release.
