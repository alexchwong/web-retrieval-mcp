"""Stateless Streamable HTTP tools for web discovery and evidence retrieval."""
from __future__ import annotations

import asyncio
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging
import os
import re
from typing import Any

from fastmcp import FastMCP
import httpx
import pymupdf
from rank_bm25 import BM25Okapi
import trafilatura

import outbound
from settings import FetchSettings, SearchSettings, Settings, SettingsManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("web-retrieval")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "8765"))
mcp = FastMCP(os.getenv("MCP_SERVER_NAME", "web-retrieval"))
settings_manager = SettingsManager()
SKIP_EXTS = re.compile(
    r"\.(zip|tar|gz|7z|rar|mp[34]|m4a|wav|mov|avi|mkv|jpg|jpeg|png|gif|webp|svg|ico)$", re.I
)
_NATIVE = {"1d": "day", "1w": "week", "7d": "week", "1m": "month", "1y": "year", "12m": "year"}


@dataclass(frozen=True)
class Search:
    query: str
    domains: tuple[str, ...]
    cutoff: date | None
    metadata: dict[str, Any]


def _months_before(today: date, months: int) -> date:
    year, month = divmod(today.year * 12 + today.month - 1 - months, 12)
    return date(year, month + 1, min(today.day, monthrange(year, month + 1)[1]))


def _search_input(query: str, recency: str | None, domains: list[str] | None,
                  today: date) -> Search:
    normalized = ()
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


def _summary(value: Any, limit: int) -> str:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    return text if len(text) <= limit else text[:limit - 1] + "…"


@dataclass(frozen=True)
class Source:
    url: outbound.URL
    fields: dict[str, Any]


async def _searxng(search: Search, settings: SearchSettings) -> tuple[list[Source], str]:
    params = {"q": search.query, "format": "json", "safesearch": str(settings.safesearch),
              "language": settings.language}
    if search.metadata["time_range"]:
        params["time_range"] = search.metadata["time_range"]
    async with httpx.AsyncClient(http2=True, follow_redirects=False,
                                 timeout=settings.timeout_seconds) as client:
        response = await client.get(f"{settings.searxng_url}/search", params=params)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("invalid search response")
    sources = []
    seen = set()
    for item in payload["results"]:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
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
        fields = {"title": _summary(item.get("title"), 300),
                  "snippet": _summary(item.get("content"), 600), "origin": "search_snippet"}
        if published_at:
            fields["published_at"] = published_at
        sources.append(Source(url, fields))
        seen.add(url.key)
        if len(sources) >= settings.num_results:
            break
    return sources, "no_eligible_results" if payload["results"] else "no_results"


def _supplied(urls: list[str], limit: int) -> list[Source]:
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


@dataclass(frozen=True)
class Document:
    status: str
    error: str = ""
    final: outbound.URL | None = None
    origin: str = ""
    units: tuple[tuple[int | None, str], ...] = ()
    truncated: bool = False
    input_capped: bool = False
    pages_examined: int | None = None

    def metadata(self, initial: outbound.URL) -> dict[str, Any]:
        result: dict[str, Any] = {"fetch_status": self.status}
        if self.status == "fetched":
            result["truncated"] = self.truncated
            if self.final and self.final.key != initial.key:
                result["final_url"] = self.final.visible
        if self.input_capped:
            result["html_input_capped"] = True
        return result


def _extract(download: outbound.Download, settings: FetchSettings) -> Document:
    cap = settings.max_document_chars
    capped = False
    pages = None
    units: list[tuple[int | None, str]] = []
    try:
        if download.kind == "fetched_pdf":
            remaining = cap
            pages = 0
            with pymupdf.open(stream=download.data, filetype="pdf") as pdf:
                if pdf.needs_pass:
                    raise ValueError("encrypted PDF")
                truncated = False
                for index in range(min(len(pdf), settings.max_pdf_pages)):
                    if remaining <= 0:
                        break
                    text = pdf[index].get_text("text")
                    pages += 1
                    remaining -= bool(units)
                    units.append((index + 1, text[:remaining]))
                    if len(text) > remaining:
                        truncated = True
                    remaining -= len(text)
                truncated = truncated or pages < len(pdf)
        else:
            html = download.data.decode(download.encoding, errors="replace")
            capped = len(html) > cap * 4
            text = trafilatura.extract(html[:cap * 4], include_comments=False,
                                       include_tables=True, favor_recall=False, no_fallback=False) or ""
            truncated = len(text) > cap
            units.append((None, text[:cap]))
    except Exception:
        return Document("extract_failed", "document extraction failed", input_capped=capped)
    if not any(text.strip() for _, text in units):
        return Document("extract_failed", "document has no extractable text", input_capped=capped)
    return Document("fetched", final=download.final, origin=download.kind, units=tuple(units),
                    truncated=truncated, input_capped=capped, pages_examined=pages)


_TIMEOUT = Document("timeout", "document retrieval timed out")


async def _fetch(source: Source, settings: FetchSettings, domains: tuple[str, ...],
                 sem: asyncio.Semaphore) -> Document:
    async with sem:
        task = asyncio.create_task(outbound.download(source.url, settings, domains))
        if not await outbound.wait_owned([task], settings.timeout_seconds * 4):
            return _TIMEOUT
        downloaded = task.result()
    if downloaded.status != "fetched":
        return Document(downloaded.status, downloaded.error)
    try:
        return await asyncio.to_thread(_extract, downloaded, settings)
    except Exception:
        return Document("extract_failed", "document extraction failed")


def _source_fields(source: Source, index: int) -> dict[str, Any]:
    return {"source_id": f"s{index}", "url": source.url.visible, **source.fields}


def _rank(query: str, sources: list[Source], docs: list[Document], settings: Settings,
          limit: int) -> list[dict[str, Any]]:
    candidates = []
    keys = []
    stride = settings.chunk.words - settings.chunk.overlap
    for index, (source, doc) in enumerate(zip(sources, docs), 1):
        for page, text in doc.units:
            words = text.split()
            for start in range(0, len(words), stride):
                candidates.append({
                    "source_id": f"s{index}", "title": source.fields["title"], "url": source.url.visible,
                    "text": " ".join(words[start:start + settings.chunk.words]), "origin": doc.origin,
                    "truncated": doc.truncated, **({"page": page} if page is not None else {}),
                })
                keys.append((doc.final or source.url).key)
    if not candidates:
        return []
    tokens = [re.findall(r"\w+", candidate["text"].lower()) for candidate in candidates]
    scores = (BM25Okapi(tokens).get_scores(re.findall(r"\w+", query.lower()))
              if any(tokens) else [0.0] * len(tokens))
    selected = []
    used: dict[str, int] = {}
    seen = set()
    for index in sorted(range(len(scores)), key=lambda i: -scores[i]):
        candidate, key = candidates[index], keys[index]
        if used.get(key, 0) >= settings.ranking.max_passages_per_source or candidate["text"] in seen:
            continue
        selected.append({**candidate, "score": round(float(scores[index]), 3)})
        used[key] = used.get(key, 0) + 1
        seen.add(candidate["text"])
        if len(selected) >= limit:
            break
    return selected


def _invalid(error: str, **fields: Any) -> dict[str, Any]:
    return {"status": "failed", "reason": "invalid_input", "error": error, **fields}


def _backend_failed() -> dict[str, str]:
    return {"status": "failed", "reason": "search_backend_failed", "error": "search backend failed"}


def _query(query: str) -> None:
    if not query.strip():
        raise ValueError("query must not be blank")


@mcp.tool
async def web_search_results(query: str, recency: str | None = None,
                             domains: list[str] | None = None) -> dict[str, Any]:
    """Preview titles, URLs and search-engine summaries without opening pages.
    Recency is best effort. One domain targets search; several filter one general results page.
    """
    settings = settings_manager.get()
    try:
        _query(query)
        search = _search_input(query, recency, domains, datetime.now(timezone.utc).date())
    except ValueError as exc:
        return _invalid(str(exc), sources=[])
    result: dict[str, Any] = {"search": search.metadata, "sources": []}
    try:
        sources, reason = await _searxng(search, settings.search)
    except (ValueError, httpx.HTTPError, httpx.InvalidURL, asyncio.TimeoutError):
        return {**result, **_backend_failed()}
    result.update(status="complete", sources=[
        {**_source_fields(source, index), "fetch_status": "not_attempted"}
        for index, source in enumerate(sources, 1)
    ])
    if not sources:
        result["reason"] = reason
    return result


@mcp.tool
async def web_evidence_passages(query: str, max_passages: int | None = None,
                                recency: str | None = None, domains: list[str] | None = None,
                                urls: list[str] | None = None) -> dict[str, Any]:
    """Fetch evidence passages ranked for lexical relevance to the query.
    A nonempty URL list bypasses search and disallows recency and domains.
    In web search mode, recency is best effort. One domain targets search;
    several domains filter one general results page.
    """
    settings = settings_manager.get()
    result: dict[str, Any] = {"sources": [], "passages": []}
    try:
        _query(query)
        if urls:
            if recency is not None or domains is not None:
                raise ValueError("omit recency and domains when supplying URLs")
            sources = _supplied(urls, settings.search.num_results)
            search = None
        else:
            search = _search_input(query, recency, domains, datetime.now(timezone.utc).date())
    except ValueError as exc:
        return _invalid(str(exc), **result)

    loop = asyncio.get_running_loop()
    if search is None:
        result["mode"] = "supplied_urls"
        deadline = loop.time() + min(45, settings.fetch.timeout_seconds * 4)
    else:
        result.update(mode="web_search", search=search.metadata)
        deadline = loop.time() + min(45, settings.search.timeout_seconds + settings.fetch.timeout_seconds * 3)
        task = asyncio.create_task(_searxng(search, settings.search))
        try:
            if not await outbound.wait_owned([task], deadline - loop.time()):
                return {**result, **_backend_failed()}
            sources, reason = task.result()
        except (ValueError, httpx.HTTPError, httpx.InvalidURL, asyncio.TimeoutError):
            return {**result, **_backend_failed()}
        if not sources:
            return {**result, "status": "complete", "reason": reason}

    remaining = deadline - loop.time()
    if remaining > 0:
        sem = asyncio.Semaphore(settings.fetch.concurrency)
        tasks = [asyncio.create_task(_fetch(source, settings.fetch, search.domains if search else (), sem))
                 for source in sources]
        done = await outbound.wait_owned(tasks, remaining)
        docs = [task.result() if task in done else _TIMEOUT for task in tasks]
    else:
        docs = [_TIMEOUT] * len(sources)
    result["sources"] = [
        {**_source_fields(source, index), **doc.metadata(source.url)}
        for index, (source, doc) in enumerate(zip(sources, docs), 1)
    ]
    requested = settings.ranking.default_max_passages if max_passages is None else max_passages
    result["passages"] = _rank(query, sources, docs, settings,
                               max(1, min(requested, settings.ranking.max_passages)))
    if not result["passages"]:
        result.update(status="failed", reason="no_evidence")
        if all(doc.status != "fetched" for doc in docs) and any(
            source.fields.get("snippet") for source in sources
        ):
            result["note"] = "no pages fetched; snippets only"
    elif any(doc.status != "fetched" for doc in docs):
        result.update(status="partial", reason="some_fetches_failed")
    else:
        result["status"] = "complete"
    return result


@mcp.tool
async def web_page_text(url: str) -> dict[str, Any]:
    """Extract the main text of one known public HTML or PDF URL, subject to document limits."""
    settings = settings_manager.get()
    try:
        parsed = outbound.parse_url(url)
    except ValueError as exc:
        error = "url must be http(s)" if str(exc) == "url must be http(s)" else "invalid URL syntax"
        return _invalid(error)
    doc = await _fetch(Source(parsed, {}), settings.fetch, (), asyncio.Semaphore(1))
    result = {"url": parsed.visible, **doc.metadata(parsed)}
    if doc.status != "fetched":
        return {**result, "status": "failed", "error": doc.error}
    text = "\n".join(text for _, text in doc.units)
    result.update(status="complete", source_id="s1", origin=doc.origin, text=text, chars=len(text))
    if doc.pages_examined is not None:
        result["pages_examined"] = doc.pages_examined
    return result


# ---------- entrypoint ------------------------------------------------------ #

if __name__ == "__main__":
    settings_manager.get()
    log.info(
        "starting web retrieval MCP on %s:%d",
        MCP_HOST,
        MCP_PORT,
    )
    # stateless_http=True is REQUIRED for llama.cpp WebUI compatibility
    # (it does not forward MCP-Session-Id on follow-up requests; see issue #20471)
    mcp.run(
        transport="streamable-http",
        host=MCP_HOST,
        port=MCP_PORT,
        stateless_http=True,
    )
