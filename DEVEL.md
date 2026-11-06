# Development rules — web-retrieval-mcp

Read this file before changing the repository. Keep this project small: it is a two-tool, local-first MCP server, not a general agent framework.

## Scope and workflow

- Inspect the current branch, working tree, and relevant code before editing. Preserve uncommitted work.
- Make the smallest change that fulfils the request. Avoid unrelated refactors, new frameworks, or speculative features.
- Do not commit, push, merge, tag, or publish unless the owner explicitly requests it.
- Report changed files, tests actually run and their outcomes, and any unresolved risks. Do not describe skipped or unavailable checks as passed.

## Runtime boundaries

- Keep `search_web(query, max_passages=None)` and `read_url(url)` as the two public MCP tools unless a change is explicitly approved. Preserve their documented inputs and output shapes.
- Preserve stateless Streamable HTTP on `/mcp`, the SearxNG dependency, HTML/PDF extraction, and bounded BM25 passage retrieval unless the task calls for changing them.
- Default host-port bindings are loopback-only. Do not expose the unauthenticated MCP server publicly or imply its arbitrary URL fetching is SSRF-safe.
- Keep `.env` and real secrets out of Git. Do not introduce paid services, model dependencies, or new required packages without the owner's approval.
- Support **Linux and macOS, Python 3.10 and 3.13**. Python 3.10 requires the existing `tomli` fallback; do not introduce unguarded Python 3.11+ APIs. Docker currently uses Python 3.12.

## Checks

From the repository root, use an isolated virtual environment:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r web-retrieval/requirements.txt
python -m compileall -q web-retrieval
python -m unittest discover -s web-retrieval -p 'test_*.py' -v
```

- Run focused offline regression tests for changed behaviour. Keep tests deterministic and independent of live SearxNG, external websites, or Docker unless explicitly testing the integration.
- CI runs the offline checks on Linux/macOS × Python 3.10/3.13 (`.github/workflows/ci.yml`). A local pass does not establish that the whole matrix passed.
- Use `./setup.sh` and the verification steps in `docs/operations.md` for optional live Docker checks; report them separately from offline tests.
- Never weaken or delete a failing check merely to obtain a pass.

## Documentation, versions, and releases

- `README.md` is for installation and basic use; `docs/` owns detailed configuration, clients, operations, and security; `NEWS.md` records concise user-visible changes. Update the relevant document when behaviour changes.
- `release/VERSION` is the single release version (currently `0.1.0`). Use `X.Y.Z` and add a corresponding `NEWS.md` entry when preparing a new release.
- The installable ZIP is defined by `release/files.txt` and created by `.github/workflows/release.yml` on `main`, following the NEL-style process. Changes to an already published release's bundled files require a version bump; never overwrite a published release.
- `DEVEL.md` is a developer instruction file, not part of the current installable ZIP. Changes to developer instructions alone do not require a release.
