# Using webSpell as a library

[Back to README](../README.md)

This guide is for any application that embeds webSpell (a "host"). AgentWiz is the
first such host and has its own specification; nothing here is sized to it.

## Install

webSpell is distributed from GitHub releases only. Pin the wheel by URL and sha256
(the `.sha256` file is attached next to the wheel on the release page):

```text
webSpell[pdf] @ https://github.com/alexchwong/webSpell/releases/download/v0.2.0/webspell-0.2.0-py3-none-any.whl#sha256=<sha256>
```

Core (`httpx`, `trafilatura`) is about 64 MB installed; the `pdf` extra adds
pypdfium2 (about 9 MB). A host that finds the core too large should not install
webSpell; there is no smaller core (see [configuration](configuration.md#dependencies-and-supported-python-versions)).
Requirement ranges resolve from a prepared wheel directory with `--no-index`.
Use pip 23.3 or later: older pip does not normalise extra names (PEP 685) and
silently skips `lxml_html_clean`, which trafilatura's HTML path needs, so every
HTML page would report `extract_failed`.

## API (interface version 1)

```python
import webspell

webspell.__version__            # "0.2.0"
webspell.INTERFACE_VERSION      # 1
webspell.TOOLS                  # ({"name", "description", "input_schema"}, ...) for all three tools

await webspell.search_results(query, *, recency=None, domains=None, settings, deadline_s=None)
await webspell.evidence_passages(query, *, urls=None, max_passages=None, recency=None,
                                 domains=None, settings, deadline_s=None)
await webspell.page_text(url, *, start=0, max_chars=None, settings, deadline_s=None)

webspell.call(name, arguments, *, settings, cancel=None, deadline_s=None)   # -> dict
webspell.result_bytes(result, *, ascii=False)                               # -> int

webspell.Settings.from_mapping({...})   # validate a mapping
webspell.load_settings(path)            # validate a TOML file (the only file webSpell reads)
```

- **Two ways in.** An asyncio host awaits the three functions and cancels them as
  ordinary tasks. A threaded host uses `call`, which runs the tool on an event loop
  it owns in the calling thread; `call` may be used from several threads at once and
  raises `RuntimeError` if the calling thread already runs an event loop. `call`
  takes the tool name and raw arguments (for example a model's tool call); an
  unknown tool or bad arguments give the `invalid_input` result.
- **Results are plain JSON-compatible dicts**, in the shapes described in
  [configuration and tool contracts](configuration.md#mcp-tools-and-response-fields).
  Expected failures are results; programming errors (a wrong `settings` type, a
  non-positive `deadline_s`) raise. No `httpx` exception and no remote text other
  than extracted document content leaves the library.
- **No process state, with one exception.** Importing reads no environment variable,
  opens no file and configures no logging; no HTTP client reads proxy or certificate
  environment variables. The library logs to the `webspell` logger and never
  configures it. The exception is the process-wide limits below.
- **Stability.** Names, argument names and result fields of interface version 1
  change only with an `INTERFACE_VERSION` increase. Adding a result field is not a
  change.

## What a host needs to know

- **Version check.** Read `INTERFACE_VERSION` and refuse a value you were not
  written for.
- **Outbound.** Every tool sends the query or the URL off the machine. Whether and
  when a tool may be offered is the host's decision; the library has no notion of
  private data or trusted destinations. The outbound policy (public addresses only,
  DNS pinning, redirect checks, TLS identity, no proxies, download cap) cannot be
  relaxed.
- **Untrusted results.** Returned text comes from the open web and can attempt
  prompt injection.
- **Search is opt-in.** Without `search.searxng_url` the two search paths return
  `search_not_configured` and make no request. Page text and supplied-URL passages
  need no SearXNG and no Docker.
- **Stop and time.** Pass a `threading.Event` as `cancel` and the time you can spare
  as `deadline_s`. Network and DNS work stops promptly (target: one second) and
  `call` returns `{"status": "cancelled"}`. A parser that was already running is
  not killed: it runs to its end and its output is discarded. `deadline_s` only
  lowers a budget; when it expires before search answers the result is
  `deadline_exceeded`. Two synchronous blocks (handling the search response, and
  ranking plus reduction) are not interrupted; each is under 0.25 seconds at the
  shipped settings, so a call can end later than its deadline by at most one block
  ([details](configuration.md#synchronous-work)).
- **Result size.** Set `output.max_result_bytes` to what you can accept, less any
  wrapper you add. Set `output.ascii_json` if you serialise results as ASCII-only
  JSON. Every result then satisfies
  `result_bytes(result, ascii=ascii_json) <= max_result_bytes`; a reduced result
  carries `"output_capped": true`, and a URL is never shortened.
- **Process-wide limits.** 8 DNS lookups, 16 documents in memory, 6 HTML parsers and
  1 PDF parser are shared by every call in the process
  ([operations](operations.md#known-ways-to-degrade)).
- **Isolation.** Parsers run in your process ([security](security.md#running-inside-a-host-process-the-library)).
  Use the MCP server if you need a process boundary.

## Example

```python
import threading
import webspell

if webspell.INTERFACE_VERSION != 1:
    raise RuntimeError("unsupported webSpell interface")

settings = webspell.Settings.from_mapping({
    "search": {"searxng_url": "http://localhost:8888"},
    "output": {"max_result_bytes": 60_000, "ascii_json": True},
})
stop = threading.Event()
result = webspell.call("web_search_results", {"query": "WHO malaria report 2026", "recency": "1y"},
                       settings=settings, cancel=stop, deadline_s=15)
for source in result["sources"]:
    print(source["source_id"], source["url"])

# Paged reading of a long page
start = 0
while start is not None:
    page = webspell.call("web_page_text", {"url": "https://example.com/", "start": start,
                                           "max_chars": 8000}, settings=settings)
    if page["status"] != "complete":
        break
    print(page["text"])
    start = page["next_start"]
```
