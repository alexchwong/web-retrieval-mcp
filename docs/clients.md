# Connect an AI client

[Back to README](../README.md) · [Verify the server first](operations.md#verify-the-installation)

**Common endpoint:** `http://127.0.0.1:8765/mcp`, using **Streamable HTTP**.
Do **not** append `/` after `/mcp`; it can produce an HTTP redirect that some
clients do not follow. This server does **not** expose the legacy SSE transport
at `/sse`.

The examples below were checked against the linked **vendor documentation**
(as available on 6 November 2026). They have **not** been live-tested against
each installed client in this documentation change. Client versions and UI
labels may differ. Use the format intended for your client; there is **no
universal configuration JSON**.

## Cline (VS Code extension / CLI)

Open **MCP Servers → Remote Servers** and enter the URL above with **Streamable
HTTP** selected. Alternatively use Cline's **Configure MCP Servers** editor:

```json
{
  "mcpServers": {
    "webspell": {
      "type": "streamableHttp",
      "url": "http://127.0.0.1:8765/mcp",
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

Do not omit `type`: in Cline's documented format, it can default to legacy
SSE. Leave automatic approval off while validating tool behavior.

Source: [Cline MCP overview](https://github.com/cline/cline/blob/main/docs/mcp/mcp-overview.mdx).

## VS Code MCP (native workspace format)

In `.vscode/mcp.json` (or VS Code's user `mcp.json`), use the **`servers`** key:

```json
{
  "servers": {
    "webspell": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

**Different format:** VS Code's *portable Copilot* `.mcp.json` uses top-level
`mcpServers` with `"type": "http"`; do not paste the example above into that
file unchanged. Use **MCP: List Servers** to inspect connection status.

Source: [VS Code MCP configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration).

## Cursor

Add a remote MCP server in Cursor's **Customize → MCP** UI, or put this in
`.cursor/mcp.json` for a project or `~/.cursor/mcp.json` globally:

```json
{
  "mcpServers": {
    "webspell": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

Source: [Cursor MCP documentation](https://cursor.com/docs/mcp).

## LM Studio

In LM Studio, open **Program → Install → Edit mcp.json** and add the server
entry using its documented Cursor-compatible notation:

```json
{
  "mcpServers": {
    "webspell": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

If the editor already contains other servers, **merge the entry** rather than
replacing the entire file. Enable the MCP tool in the relevant chat session.

Source: [LM Studio MCP guide](https://lmstudio.ai/docs/app/mcp).

## Claude Code

For a CLI running on the **same host** as this MCP server:

```bash
claude mcp add --transport http webspell http://127.0.0.1:8765/mcp
```

Check configured servers with `claude mcp list` and approve the server when
prompted. Claude Code is separate from **Claude Desktop** and from hosted
claude.ai connectors: those do not necessarily accept a local unauthenticated
HTTP URL. Do not expose this server publicly to work around that limitation.

Source: [Claude Code MCP guide](https://code.claude.com/docs/en/mcp).

## llama.cpp / llama-server WebUI

First check that your build supports the WebUI MCP proxy:

```bash
./llama-server --help | grep -i mcp-proxy
```

Start llama-server with your own model path/options; this example enables the
WebUI's local MCP CORS proxy (the proxy is experimental):

```bash
./llama-server -m /path/to/model.gguf \
  --host 127.0.0.1 --port 8080 \
  --jinja --ui-mcp-proxy
```

Open `http://127.0.0.1:8080`, add a **Streamable HTTP** MCP server in WebUI
settings, enter `http://127.0.0.1:8765/mcp`, and enable the **llama-server
proxy** option. Enable tool use / the agentic loop for a model that supports
calling tools. If the connection fails, edit the saved server entry and
recheck its proxy toggle. The server retains `stateless_http=True` in part for
compatibility with older llama.cpp WebUI clients that did not preserve
`MCP-Session-Id` on subsequent requests.

Source: [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

## Containers, remote devices and browsers

- **Same host:** use the loopback URL above.
- **Another container:** its `127.0.0.1` is itself. A client container joined
  to the same Compose network may address the MCP service as
  `http://web-retrieval:8765/mcp` (service name DNS). Configure networking
  explicitly; an unrelated container cannot assume it can resolve this name.
- **Another device:** it cannot use your machine's loopback address. The
  defaults intentionally refuse external host connections. Use an appropriately
  secured private networking arrangement rather than exposing an
  unauthenticated server on `0.0.0.0` or the public internet.
- **Browser-origin requests:** Streamable HTTP, browser CORS response headers,
  and HTTP Host/Origin request validation are **different** concerns. A proxy
  may be required; changing only an MCP URL will not fix a browser CORS block.

## Host and Origin allowlists

The server validates `Host` and `Origin` on every request (see
[security](security.md)). Every example above uses `http://127.0.0.1:8765/mcp` and
needs no configuration, as does a browser UI served from loopback (such as the
llama.cpp WebUI at `http://127.0.0.1:8080`) and a container on the Compose network
using `http://web-retrieval:8765/mcp`.

- **A UI opened by LAN name or another host name** (for example
  `http://workstation.lan:8080`): add its origin to `MCP_ALLOWED_ORIGINS` in `.env`,
  as `MCP_ALLOWED_ORIGINS=http://workstation.lan:8080`.
- **A client that reaches the server under another host name** (a reverse proxy, an
  `/etc/hosts` alias, a container alias): add that name to `MCP_ALLOWED_HOSTS`, as
  `MCP_ALLOWED_HOSTS=mcp.internal`.
- Separate several entries with commas, then run `docker compose up -d`. For a direct
  run, set the same variables in the server's environment.
- Without them the server answers 421 (unlisted `Host`) or 403 (unlisted `Origin`)
  and no tool runs.

## Migrating from web-retrieval-mcp 0.1.1

webSpell 0.2.0 is web-retrieval-mcp under a new name, in the same repository.

- **Same port, same path, same tool names:** `http://127.0.0.1:8765/mcp`,
  `web_search_results`, `web_evidence_passages`, `web_page_text`. Result shapes are
  unchanged; `web_page_text` accepts `start` and `max_chars` and returns three more
  fields.
- **The repository URL changed** to `https://github.com/alexchwong/webSpell`; the
  old URL redirects.
- **The server's default name is `webspell`.** A client that shows or keys on the
  server name sees the new one; set `MCP_SERVER_NAME=web-retrieval` in `.env` to keep
  the old one. The client-side entry name in the examples above is your choice.
- **The Compose service is still `web-retrieval`,** so `http://web-retrieval:8765/mcp`
  still works from other containers on the Compose network.
- **The Host/Origin guard is on.** A client using a host name other than loopback or
  the service name, or a browser UI served from a non-loopback origin, needs an
  allowlist entry (above).
- Refresh the client's cached tool list after upgrading, because `web_page_text`
  advertises two new arguments.

After connecting, ask the client to **list MCP tools** (expect `web_search_results`,
`web_evidence_passages` and `web_page_text`) and then perform a small search that cites the source URLs.
See [troubleshooting](operations.md#troubleshooting) and [security](security.md).
