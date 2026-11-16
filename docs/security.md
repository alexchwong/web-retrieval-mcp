# Security and privacy

[Back to README](../README.md)

**This is a local/private experimental service, not a hardened public MCP
endpoint.** The tools are read-only in the sense that they do not deliberately
modify application data, but they **make outbound network requests**. That
still creates security, privacy and resource-use risks.

## Actual protection in this repository

- Compose defaults to publishing both host ports on **`127.0.0.1` only**.
  Inside the `web-retrieval` container, `MCP_HOST=0.0.0.0` allows peers on
  the Docker network to reach it; the **host** publishing rule is separate.
  Run directly (`python -m webspell`), the server binds `127.0.0.1`.
- There is **no MCP authentication**, authorization, API-key requirement,
  rate limiter for the MCP tools, or per-client identity boundary.
- **FastMCP's Host/Origin guard is on, in strict mode, on every launch path**
  (direct runs and Compose). webSpell passes `host_origin_protection=True`
  explicitly rather than relying on FastMCP's default. Requests whose `Host` is
  `127.0.0.1`, `localhost` or `::1`, with no `Origin` or a loopback `Origin`, are
  accepted with no configuration. `MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS`
  (comma-separated) add to those; Compose adds the service name `web-retrieval`
  so containers on its network can use `http://web-retrieval:8765/mcp`. Anything
  else is refused before tool dispatch: 421 for an unlisted `Host`, 403 for an
  unlisted `Origin`. This stops a web page that reaches the loopback port under
  another host name (DNS rebinding). **The guard is not authentication**: any
  local process can still call the server.
- Browser CORS response policy, HTTP Host/Origin validation and MCP client
  authentication are **separate** controls. None should be mistaken for
  another. A reverse proxy cannot be presumed to protect requests unless it
  has actually been configured to do so.

Source: [FastMCP 4.1.6 settings](https://github.com/PrefectHQ/fastmcp/blob/v4.1.6/fastmcp_slim/fastmcp/settings.py#L277-L284),
[request guard](https://github.com/PrefectHQ/fastmcp/blob/v4.1.6/fastmcp_slim/fastmcp/server/http.py#L346-L370),
[SDK guard handling](https://github.com/PrefectHQ/fastmcp/blob/v4.1.6/fastmcp_slim/fastmcp/server/http.py#L739-L756).

## SSRF and internal-network requests

Document requests accept public HTTP(S) destinations on the scheme's standard
port. They reject credentials, nonpublic addresses, ambiguous host syntax,
unsafe redirects and HTTPS downgrades. The entire DNS answer set is checked
and each request connects to a validated numeric address while verifying the
logical TLS identity. Each redirect repeats the checks. Caller-specified
search domains constrain redirect destinations too.

Document requests bypass environment proxies because a proxy can resolve or
reroute the target. Proxy-only egress therefore no longer works for document
fetches. Search requests also ignore `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`,
`SSL_CERT_FILE` and `SSL_CERT_DIR`, so the query goes to the configured
`search.searxng_url` and nowhere else. The configured SearxNG route can be
private: the public-address rule applies to documents, not to SearXNG. The
outbound policy is not a setting and cannot be relaxed through settings or the
library API. Discovery
summaries and extracted content are untrusted. A model can be steered by
fetched text into requesting an internal resource even on a loopback
deployment. Keep the service private and use network isolation where needed.

## Running inside a host process (the library)

In the Compose deployment the HTML and PDF parsers run in a container. A host
application that imports the library runs them **inside the host process**.

- The outbound checks prevent requests to private addresses. **They are not a
  parser sandbox.**
- Parser exceptions are caught and become `extract_failed`. A crash in native code
  (lxml, PDFium) cannot be caught and would end the host process.
- Parser inputs are bounded by the existing caps (`fetch.max_bytes`, four times
  `fetch.max_document_chars` of HTML, `fetch.max_pdf_pages`); memory and CPU inside
  a parse are not otherwise limited.
- The container deployment remains the isolated option. **A host that needs a
  process boundary should use the MCP server, not the library.**
- Retrieved text is untrusted and can attempt prompt injection. The library returns
  it only as document content, never as instructions or errors.
- Every tool sends the query or the URL off the machine. Whether and when a tool may
  be offered is the host's decision; the library has no notion of private data or
  trusted destinations.

## Information leaving the machine

- SearxNG forwards queries to configured external search engines. **Private
  SearxNG does not imply private upstream queries**; search providers can see
  requests from your network.
- The MCP fetcher visits third-party websites. Those sites may observe your
  public IP address, request timing, query parameters and User-Agent.
- Prompts, source material and retrieved pages may be sent by the **AI
  client** to a remote model provider; this repository does not control that.
- The system does **not** enforce `robots.txt`. Follow source terms, access
  rules and applicable law; avoid feeding private URLs or sensitive identifiers
  into open search unnecessarily.

## Safe operating assumptions

1. Leave `MCP_BIND_ADDRESS` and `SEARXNG_BIND_ADDRESS` at `127.0.0.1` for ordinary
   use. Do not forward the ports from a router or expose them on the public
   internet.
2. Only connect trusted local users/apps; verify tool arguments before
   approving requests. A browser page or a different local process can be an
   untrusted caller even on a personal computer.
3. Do not treat the tools' **read-only** label as equivalent to a security
   boundary: an internal URL may return credentials or private data.
4. If multi-user or off-host access is necessary, design and test proper
   authentication, destination restrictions (including redirects), network
   isolation, TLS, request limits and Host/Origin handling **before** opening
   a listener. The outbound checks cover document destinations. Off-host access still requires a separate deployment security design.

## Limits of protection and testing

This document describes the source as currently written, **not** a penetration
test. There is no guarantee that a downloaded HTML page or PDF is trustworthy,
that extraction preserves its intended meaning, or that an AI model will ignore
instructions embedded in retrieved content. Treat web text as untrusted
reference material, not as instructions or verified facts.
