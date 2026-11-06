"""Policy and transport for untrusted public document URLs."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
import re
import socket
import ssl
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx

from settings import FetchSettings

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_NUMERIC = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)(?:\.(?:[0-9]+|0x[0-9a-f]+))*", re.I)
_FORBIDDEN = tuple(map(ip_network, (
    "100.64.0.0/10", "168.63.129.16/32", "192.0.0.0/24",
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",
    "64:ff9b::/96", "64:ff9b:1::/48", "2001::/32", "2002::/16",
)))


def dns_name(host: str) -> str:
    host = host.removesuffix(".")
    if not host or re.search(r"[%\s:/@\[\]\\?#\x00-\x1f\x7f]", host):
        raise ValueError("invalid DNS name")
    try:
        name = httpx.URL(f"http://{host}/").raw_host.decode("ascii").lower()
    except (httpx.InvalidURL, UnicodeError):
        raise ValueError("invalid DNS name") from None
    labels = name.split(".")
    if (len(name) > 253 or len(labels) < 2 or labels[-1].isdigit()
            or _NUMERIC.fullmatch(name)
            or not all(_LABEL.fullmatch(label) for label in labels)):
        raise ValueError("invalid DNS name")
    return name


@dataclass(frozen=True)
class URL:
    scheme: str
    host: str
    port: int
    path: str
    query: str
    has_query: bool
    original: str
    credentialed: bool

    @property
    def default_port(self) -> int:
        return 443 if self.scheme == "https" else 80

    @property
    def authority(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port == self.default_port else f"{host}:{self.port}"

    @property
    def key(self) -> str:
        return self.target(self.authority)

    @property
    def visible(self) -> str:
        if not self.credentialed:
            return self.original
        parts = urlsplit(self.original)
        return urlunsplit(parts._replace(netloc=parts.netloc.rsplit("@", 1)[-1]))

    def target(self, authority: str) -> str:
        query = f"?{self.query}" if self.has_query else ""
        return f"{self.scheme}://{authority}{self.path or '/'}{query}"


def parse_url(value: str) -> URL:
    if re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise ValueError("invalid URL syntax")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise ValueError("url must be http(s)")
    host = parts.hostname
    authority = parts.netloc.rsplit("@", 1)[-1]
    if not host or "%" in host or authority.endswith(":"):
        raise ValueError("invalid URL authority")
    if authority.startswith("[") and not re.fullmatch(r"\[[^\]]+\](?::[0-9]+)?", authority):
        raise ValueError("invalid URL authority")
    try:
        ip = ip_address(host)
    except ValueError:
        if authority.startswith("[") or _NUMERIC.fullmatch(host.removesuffix(".")):
            raise ValueError("invalid IP literal") from None
        host = dns_name(host)
    else:
        if authority.startswith("[") != (ip.version == 6):
            raise ValueError("invalid IP literal")
        host = str(ip)
    port = parts.port
    if port == 0:
        raise ValueError("invalid URL port")
    return URL(parts.scheme, host, port if port is not None else (443 if parts.scheme == "https" else 80),
               parts.path, parts.query, "?" in value.split("#", 1)[0], value, "@" in parts.netloc)


def matches(host: str, domains: tuple[str, ...]) -> bool:
    return not domains or any(host == d or host.endswith("." + d) for d in domains)


def public_ip(value: str) -> bool:
    ip = ip_address(value)
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return (ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback
            or ip.is_private or ip.is_link_local or ip.is_unspecified
            or getattr(ip, "is_site_local", False))
            and not any(ip in network for network in _FORBIDDEN))


async def resolve(host: str, port: int) -> list[str]:
    try:
        return [str(ip_address(host))]
    except ValueError:
        records = await asyncio.get_running_loop().getaddrinfo(
            host + ".", port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        return list(dict.fromkeys(record[4][0] for record in records))


@dataclass(frozen=True)
class Download:
    status: str
    error: str = ""
    final: URL | None = None
    data: bytes = b""
    kind: str = ""
    encoding: str = "utf-8"


async def wait_owned(tasks: list[asyncio.Task], timeout: float) -> set[asyncio.Task]:
    """Return tasks completed by the deadline, cancelling and draining the rest."""
    if not tasks:
        return set()
    done: set[asyncio.Task] = set()
    try:
        done, _ = await asyncio.wait(tasks, timeout=max(0, timeout))
        return done
    finally:
        pending = [task for task in tasks if task not in done]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def certificate_error(exc: BaseException) -> bool:
    pending = [exc]
    seen = set()
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, ssl.SSLCertVerificationError):
            return True
        pending.extend(link for link in (item.__cause__, item.__context__) if link is not None)
    return False


async def _request(url: URL, address: str, settings: FetchSettings, verify: ssl.SSLContext | bool):
    authority = f"[{address}]" if ":" in address else address
    if url.port != url.default_port:
        authority += f":{url.port}"
    # HTTPX normally removes dot segments from absolute URLs. Keep the logical
    # request target intact while using a numeric origin for the actual dial.
    path = quote(url.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(url.query, safe="/?%:@!$&'()*+,;=-._~[]")
    target = f"{path}?{query}" if url.has_query else path
    extensions: dict[str, str | bytes] = {"target": target.encode("ascii")}
    if url.scheme == "https":
        try:
            ip_address(url.host)
        except ValueError:
            extensions["sni_hostname"] = url.host
    async with httpx.AsyncClient(trust_env=False, http2=False, verify=verify,
                                 timeout=settings.timeout_seconds) as client:
        async with client.stream("GET", f"{url.scheme}://{authority}/", extensions=extensions, headers={
            "Host": url.authority, "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/pdf",
            "Accept-Encoding": "identity",
        }) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                return response.headers.get("location", "")
            if not response.is_success:
                return Download("http_error", "document server returned an HTTP error")
            media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media == "application/pdf" or (
                media in ("", "application/octet-stream", "binary/octet-stream")
                and url.path.lower().endswith(".pdf")
            ):
                kind = "fetched_pdf"
            elif media.endswith(("/html", "/xml", "+xml")) or media == "application/xhtml+xml":
                kind = "fetched_page"
            else:
                return Download("unsupported", "unsupported document media type")
            if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                return Download("unsupported", "unsupported document content encoding")
            length = response.headers.get("content-length", "")
            if length.isascii() and length.isdecimal() and int(length) > settings.max_bytes:
                return Download("too_large", "document exceeds byte limit")
            data = bytearray()
            async for chunk in response.aiter_raw():
                if len(data) + len(chunk) > settings.max_bytes:
                    return Download("too_large", "document exceeds byte limit")
                data.extend(chunk)
            return Download("fetched", final=url, data=bytes(data), kind=kind,
                            encoding=response.encoding or "utf-8")


async def download(url: URL, settings: FetchSettings, domains: tuple[str, ...] = (),
                   *, _resolve=resolve, _public=public_ip, _verify=True, _allow_port=None) -> Download:
    """Fetch with per-hop validation. Keyword overrides are for offline tests."""
    seen = set()
    for hop in range(6):
        if url.credentialed:
            return Download("blocked", "URL credentials are prohibited")
        if url.port != url.default_port and not (_allow_port and _allow_port(url.port)):
            return Download("blocked", "nonstandard URL port")
        if not matches(url.host, domains):
            return Download("blocked", "URL is outside requested domains")
        if url.key in seen:
            return Download("blocked", "redirect loop")
        seen.add(url.key)
        try:
            addresses = await _resolve(url.host, url.port)
            if not addresses:
                return Download("http_error", "no document destination resolved")
            if not all("%" not in address and _public(address) for address in addresses):
                return Download("blocked", "nonpublic document destination")
            addresses.sort(key=lambda address: ip_address(address).version)
        except asyncio.TimeoutError:
            return Download("timeout", "document lookup timed out")
        except (OSError, ValueError):
            return Download("http_error", "document lookup failed")
        for address in addresses:
            try:
                result = await _request(url, address, settings, _verify)
                break
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if certificate_error(exc):
                    return Download("http_error", "document TLS verification failed")
                result = (Download("timeout", "document connection timed out")
                          if isinstance(exc, httpx.ConnectTimeout)
                          else Download("http_error", "document connection failed"))
            except (asyncio.TimeoutError, httpx.TimeoutException):
                return Download("timeout", "document request timed out")
            except (httpx.HTTPError, httpx.InvalidURL, ValueError):
                return Download("http_error", "document request failed")
        if isinstance(result, Download):
            return result
        if hop == 5:
            return Download("blocked", "redirect limit exceeded")
        if not result or re.search(r"[\s\\\x00-\x1f\x7f]", result):
            return Download("blocked", "invalid redirect location")
        try:
            next_url = parse_url(urljoin(url.key, result))
        except ValueError:
            return Download("blocked", "invalid redirect location")
        if url.scheme == "https" and next_url.scheme != "https":
            return Download("blocked", "HTTPS redirect downgrade")
        url = next_url
    raise AssertionError("redirect loop exhausted")
