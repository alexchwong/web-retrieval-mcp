"""SearXNG client, search arguments and supplied-URL sources."""
from __future__ import annotations

import asyncio
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from ipaddress import ip_address
import json
import re
import socket
from typing import Any

import httpx

from . import limits, outbound
from .settings import SearchSettings

SKIP_EXTS = re.compile(
    r"\.(zip|tar|gz|7z|rar|mp[34]|m4a|wav|mov|avi|mkv|jpg|jpeg|png|gif|webp|svg|ico)$", re.I
)
_NATIVE = {"1d": "day", "1w": "week", "7d": "week", "1m": "month", "1y": "year", "12m": "year"}

# Bounds on the synchronous work done on one search response (Section 8.5).
MAX_RESPONSE_BYTES = 2_000_000
MAX_ENTRIES = 200
MAX_URL_CHARS = 4096


class SearchFailed(Exception):
    """The search backend could not be used. Never carries remote text to a caller."""


@dataclass(frozen=True)
class Search:
    query: str
    domains: tuple[str, ...]
    cutoff: date | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Source:
    url: outbound.URL
    fields: dict[str, Any]


def _months_before(today: date, months: int) -> date:
    year, month = divmod(today.year * 12 + today.month - 1 - months, 12)
    return date(year, month + 1, min(today.day, monthrange(year, month + 1)[1]))


def search_input(query: str, recency: str | None, domains: list[str] | None,
                 today: date) -> Search:
    normalized: tuple[str, ...] = ()
    if domains is not None:
        if not 1 <= len(domains) <= 5:
            raise ValueError("domains must contain one to five DNS names")
        normalized = tuple(dict.fromkeys(outbound.dns_name(domain.strip()) for domain in domains))
    cutoff = None
    method, time_range = "none", None
    if recency is not None:
        match = re.fullmatch(r"([1-9][0-9]{0,2})([dwmy])", recency)
        if match is None:
            raise ValueError("recency must use a positive number and d, w, m or y")
        amount, unit = int(match[1]), match[2]
        if amount > {"d": 365, "w": 52, "m": 60, "y": 5}[unit]:
            raise ValueError("recency exceeds the allowed range")
        if unit in ("d", "w"):
            cutoff = today - timedelta(days=amount * (7 if unit == "w" else 1))
        else:
            cutoff = _months_before(today, amount * (12 if unit == "y" else 1))
        if recency in _NATIVE:
            method, time_range = "native", _NATIVE[recency]
        elif cutoff >= _months_before(today, 12):
            days = (today - cutoff).days
            time_range = next((window for cap, window in ((7, "week"), (28, "month"), (365, "year"))
                               if days <= cap), None)
        if method == "none":
            method = "native_widened" if time_range else "local_dates_only"
    if len(normalized) == 1:
        query += f" site:{normalized[0]}"
    return Search(query, normalized, cutoff, {
        "recency": recency, "recency_method": method, "time_range": time_range,
        "as_of_utc": today.isoformat(), "cutoff_utc": cutoff.isoformat() if cutoff else None,
        "domains": list(normalized),
    })


def _publication(value: Any) -> tuple[date | None, str | None]:
    if not isinstance(value, str):
        return None, None
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            parsed = date.fromisoformat(value)
            return parsed, parsed.isoformat()
        if not re.match(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ]", value):
            return None, None
        instant = datetime.fromisoformat(value.removesuffix("Z") + (
            "+00:00" if value.endswith("Z") else ""))
        if instant.tzinfo is None:
            return instant.date(), instant.date().isoformat()
        instant = instant.astimezone(timezone.utc)
        return instant.date(), instant.isoformat()
    except (ValueError, OverflowError):
        return None, None


def summary(value: Any, limit: int) -> str:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    return text if len(text) <= limit else text[:limit - 1] + "…"


def supplied(urls: list[str], limit: int) -> list[Source]:
    if len(urls) > limit:
        raise ValueError(f"urls may contain at most {limit} entries")
    sources = []
    seen = set()
    for index, value in enumerate(urls, 1):
        try:
            url = outbound.parse_url(value)
        except ValueError:
            raise ValueError(f"URL item {index} must be a valid absolute HTTP(S) URL") from None
        if not url.credentialed:
            if url.key in seen:
                continue
            seen.add(url.key)
        sources.append(Source(url, {"title": "", "origin": "supplied_url"}))
    return sources


async def _get(base: httpx.URL, address: str, params: dict[str, str], timeout: float,
               connect: float) -> bytes:
    """GET ``/search`` from one vetted address with the configured Host and TLS name."""
    host = base.raw_host.decode("ascii")
    url = base.copy_with(host=address, path=base.path.rstrip("/") + "/search", params=params)
    extensions: dict[str, str] = {}
    if base.scheme == "https":
        try:
            ip_address(host)
        except ValueError:
            extensions["sni_hostname"] = host
    headers = {"Host": base.netloc.decode("ascii"), "Accept-Encoding": "identity"}
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                 timeout=httpx.Timeout(timeout, connect=connect)) as client:
        async with client.stream("GET", url, headers=headers, extensions=extensions) as response:
            if not response.is_success:
                raise SearchFailed("search backend returned an HTTP error")
            encoding = response.headers.get("content-encoding", "identity").strip().lower()
            if encoding not in ("", "identity"):
                raise SearchFailed("search response is encoded")
            length = response.headers.get("content-length", "")
            if length.isascii() and length.isdecimal() and int(length) > MAX_RESPONSE_BYTES:
                raise SearchFailed("search response too large")
            data = bytearray()
            async for chunk in response.aiter_raw():
                if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise SearchFailed("search response too large")
                data.extend(chunk)
            return bytes(data)


async def _body(settings: SearchSettings, params: dict[str, str]) -> bytes:
    loop = asyncio.get_running_loop()
    # The connection budget covers waiting for a lookup slot, the lookup and the connect.
    connect_deadline = loop.time() + settings.timeout_seconds
    try:
        base = httpx.URL(settings.searxng_url)
        host = base.raw_host.decode("ascii")
    except (httpx.InvalidURL, UnicodeError):
        raise SearchFailed("invalid search URL") from None
    if base.scheme not in ("http", "https") or not host:
        raise SearchFailed("invalid search URL")
    port = base.port or (443 if base.scheme == "https" else 80)
    try:
        addresses = [str(ip_address(host))]
    except ValueError:
        # The configured name is resolved exactly as given: no trailing dot, so the
        # hosts file and resolver search suffixes still apply (``localhost``).
        records = await limits.getaddrinfo(host, port, type=socket.SOCK_STREAM,
                                           proto=socket.IPPROTO_TCP, deadline=connect_deadline)
        addresses = list(dict.fromkeys(record[4][0] for record in records))
    failure: BaseException | None = None
    for address in addresses:
        remaining = connect_deadline - loop.time()
        if remaining <= 0:
            break
        try:
            return await _get(base, address, params, settings.timeout_seconds, remaining)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            failure = exc
    raise SearchFailed("search backend unreachable") from failure


def _sources(data: bytes, search: Search, num_results: int) -> tuple[list[Source], str]:
    """Parse the response and select sources. Synchronous; its input is bounded."""
    try:
        payload = json.loads(data)
    except (ValueError, RecursionError):
        raise SearchFailed("invalid search response") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise SearchFailed("invalid search response")
    sources = []
    seen = set()
    for item in payload["results"][:MAX_ENTRIES]:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        if len(item["url"]) > MAX_URL_CHARS:
            continue
        try:
            url = outbound.parse_url(item["url"])
        except ValueError:
            continue
        if (url.credentialed or url.key in seen or SKIP_EXTS.search(url.path)
                or not outbound.matches(url.host, search.domains)):
            continue
        published_date, published_at = _publication(item.get("publishedDate"))
        if search.cutoff and published_date and published_date < search.cutoff:
            continue
        fields = {"title": summary(item.get("title"), 300),
                  "snippet": summary(item.get("content"), 600), "origin": "search_snippet"}
        if published_at:
            fields["published_at"] = published_at
        sources.append(Source(url, fields))
        seen.add(url.key)
        if len(sources) >= num_results:
            break
    return sources, "no_eligible_results" if payload["results"] else "no_results"


async def searxng(search: Search, settings: SearchSettings) -> tuple[list[Source], str]:
    """Query SearXNG. Every expected failure is raised as ``SearchFailed``."""
    params = {"q": search.query, "format": "json", "safesearch": str(settings.safesearch),
              "language": settings.language}
    if search.metadata["time_range"]:
        params["time_range"] = search.metadata["time_range"]
    try:
        data = await _body(settings, params)
    except SearchFailed:
        raise
    except (httpx.HTTPError, httpx.InvalidURL, OSError, ValueError, asyncio.TimeoutError) as exc:
        raise SearchFailed("search backend failed") from exc
    return _sources(data, search, settings.num_results)
