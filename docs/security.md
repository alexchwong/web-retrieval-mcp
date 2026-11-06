# Security and privacy

[Back to README](../README.md)

**This is a local/private experimental service, not a hardened public MCP
endpoint.** Both tools are read-only in the sense that they do not deliberately
modify application data, but they **make outbound network requests**. That
still creates security, privacy and resource-use risks.

## Actual protection in this repository

- Compose defaults to publishing both host ports on **`127.0.0.1` only**.
  Inside the `web-retrieval` container, `MCP_HOST=0.0.0.0` allows peers on
  the Docker network to reach it; the **host** publishing rule is separate.
- There is **no MCP authentication**, authorization, API-key requirement,
  rate limiter for the MCP tools, or per-client identity boundary.
- FastMCP 4.1.6 offers an optional HTTP Host/Origin protection guard, but the
  default is **off**. This repository does not enable it. The upstream MCP
  SDK's DNS-rebinding protection is also disabled by FastMCP's HTTP setup
  when this guard is not enabled.
- Browser CORS response policy, HTTP Host/Origin validation and MCP client
  authentication are **separate** controls. None should be mistaken for
  another. A reverse proxy cannot be presumed to protect requests unless it
  has actually been configured to do so.

Source: [FastMCP 4.1.6 settings](https://github.com/PrefectHQ/fastmcp/blob/v4.1.6/fastmcp_slim/fastmcp/settings.py#L277-L284),
[request guard](https://github.com/PrefectHQ/fastmcp/blob/v4.1.6/fastmcp_slim/fastmcp/server/http.py#L346-L370),
[SDK guard handling](https://github.com/PrefectHQ/fastmcp/blob/v4.1.6/fastmcp_slim/fastmcp/server/http.py#L739-L756).

## SSRF and internal-network requests

`read_url(url)` only checks that the URL scheme is `http` or `https`.
The HTTP client follows redirects. There is currently **no** hostname/IP
allowlist or blocklist, no DNS resolution check to prevent private, loopback,
link-local or cloud metadata addresses, and no redirect-target revalidation.
An untrusted or manipulated caller could therefore make the server request
internal network resources accessible **from inside the container** (SSRF).
`search_web` also fetches result URLs supplied by SearxNG; it does not impose
an independent destination safety policy.

The byte/page/character limits reduce individual response size but do not
make arbitrary URL fetching safe; neither does Docker's host-port loopback
binding prevent all Docker-network or same-host callers.

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
   a listener. These are **not implemented** in this documentation refactor.

## Limits of protection and testing

This document describes the source as currently written, **not** a penetration
test. There is no guarantee that a downloaded HTML page or PDF is trustworthy,
that extraction preserves its intended meaning, or that an AI model will ignore
instructions embedded in retrieved content. Treat web text as untrusted
reference material, not as instructions or verified facts.
