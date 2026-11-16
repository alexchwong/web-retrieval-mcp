"""The public tool API (interface version 1)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import math
import threading
from typing import Any, Awaitable, Callable

import httpx

from . import extract, limit, limits, outbound, rank, search, validate
from .extract import Document
from .limit import result_bytes
from .search import SearchFailed, Source
from .settings import FetchSettings, Settings

__all__ = ["TOOLS", "search_results", "evidence_passages", "page_text", "call", "result_bytes"]

_SEARCH_DESCRIPTION = (
    "Preview titles, URLs and search-engine summaries without opening pages.\n"
    "Recency is best effort. One domain targets search; several filter one general results page."
)
_EVIDENCE_DESCRIPTION = (
    "Fetch evidence passages ranked for lexical relevance to the query.\n"
    "A nonempty URL list bypasses search and disallows recency and domains.\n"
    "In web search mode, recency is best effort. One domain targets search;\n"
    "several domains filter one general results page."
)
_PAGE_DESCRIPTION = (
    "Extract the main text of one known public HTML or PDF URL, subject to document limits."
)


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}], "default": None}


_QUERY = {"type": "string", "maxLength": validate.MAX_QUERY_CHARS}
_RECENCY = _nullable({"type": "string"})
_DOMAINS = _nullable({"items": {"type": "string"}, "type": "array", "minItems": 1, "maxItems": 5})

TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "web_search_results",
        "description": _SEARCH_DESCRIPTION,
        "input_schema": {
            "additionalProperties": False,
            "properties": {"query": _QUERY, "recency": _RECENCY, "domains": _DOMAINS},
            "required": ["query"],
            "type": "object",
        },
    },
    {
        "name": "web_evidence_passages",
        "description": _EVIDENCE_DESCRIPTION,
        "input_schema": {
            "additionalProperties": False,
            "properties": {
                "query": _QUERY,
                "max_passages": _nullable({"type": "integer"}),
                "recency": _RECENCY,
                "domains": _DOMAINS,
                "urls": _nullable({"items": {"type": "string", "maxLength": validate.MAX_URL_CHARS},
                                   "type": "array"}),
            },
            "required": ["query"],
            "type": "object",
        },
    },
    {
        "name": "web_page_text",
        "description": _PAGE_DESCRIPTION,
        "input_schema": {
            "additionalProperties": False,
            "properties": {
                "url": {"type": "string", "maxLength": validate.MAX_URL_CHARS},
                "start": {"type": "integer", "minimum": 0, "default": 0},
                "max_chars": _nullable({"type": "integer", "minimum": 1}),
            },
            "required": ["url"],
            "type": "object",
        },
    },
)

_CANCELLED = {"status": "cancelled"}
_POLL_SECONDS = 0.05


# ---------- results --------------------------------------------------------- #

def _invalid(error: str, **fields: Any) -> dict[str, Any]:
    return {"status": "failed", "reason": "invalid_input", "error": error, **fields}


def _backend_failed() -> dict[str, str]:
    return {"status": "failed", "reason": "search_backend_failed", "error": "search backend failed"}


def _not_configured() -> dict[str, str]:
    return {"status": "failed", "reason": "search_not_configured", "error": "search is not configured"}


def _deadline_exceeded() -> dict[str, str]:
    return {"status": "failed", "reason": "deadline_exceeded",
            "error": "deadline expired before search completed"}


def _query(query: str) -> None:
    if not query.strip():
        raise ValueError("query must not be blank")


def _today():
    return datetime.now(timezone.utc).date()


def _source_fields(source: Source, index: int) -> dict[str, Any]:
    return {"source_id": f"s{index}", "url": source.url.visible, **source.fields}


_SEARCH_ERRORS = (SearchFailed, ValueError, httpx.HTTPError, httpx.InvalidURL,
                  asyncio.TimeoutError, OSError)


# ---------- fetching -------------------------------------------------------- #

async def _fetch(source: Source, settings: FetchSettings, domains: tuple[str, ...],
                 sem: asyncio.Semaphore, deadline: float,
                 extract_budget: float | None = None) -> Document:
    """Download and extract one document under the process-wide limits.

    The document slot is taken before the request is sent and released when the
    bytes are dropped: on a failed or refused download, on cancellation or timeout
    while waiting, or by the parser thread when the parser returns.
    """
    loop = asyncio.get_running_loop()
    async with sem:
        download_deadline = min(deadline, loop.time() + settings.timeout_seconds * 4)
        try:
            document = await limits.admit(limits.documents, download_deadline)
        except asyncio.TimeoutError:
            return extract.TIMEOUT
        try:
            task = asyncio.create_task(outbound.download(source.url, settings, domains))
            if not await outbound.wait_owned([task], download_deadline - loop.time()):
                document.release()
                return extract.TIMEOUT
            downloaded = task.result()
        except BaseException:
            document.release()
            raise
    if downloaded.status != "fetched":
        document.release()
        return Document(downloaded.status, downloaded.error)
    return await _parse(downloaded, settings, document, deadline, extract_budget)


async def _parse(downloaded: outbound.Download, settings: FetchSettings, document: limits.Hold,
                 deadline: float, extract_budget: float | None) -> Document:
    loop = asyncio.get_running_loop()
    try:
        pdf = downloaded.kind == "fetched_pdf"
        if pdf and not extract.pdf_available():
            document.release()
            return extract.PDF_UNAVAILABLE
        parse_deadline = deadline if extract_budget is None else min(
            deadline, loop.time() + extract_budget)
        try:
            parser = await limits.admit(limits.pdf_parsers if pdf else limits.html_parsers,
                                        parse_deadline)
        except asyncio.TimeoutError:
            document.release()
            return extract.TIMEOUT
        # From here the parser thread owns both slots and releases them when it returns.
        future = limits.start_thread(lambda: extract.extract(downloaded, settings),
                                     [parser, document], "webspell-parser")
    except BaseException:
        document.release()
        raise
    try:
        return await asyncio.wait_for(future, max(0.0, parse_deadline - loop.time()))
    except asyncio.TimeoutError:
        return extract.TIMEOUT
    except Exception:
        return Document("extract_failed", "document extraction failed")


# ---------- tools ----------------------------------------------------------- #

async def _search_results(arguments: Any, settings: Settings,
                          host_deadline: float | None) -> dict[str, Any]:
    try:
        args = validate.check("web_search_results", arguments)
        _query(args["query"])
        query = search.search_input(args["query"], args["recency"], args["domains"], _today())
    except ValueError as exc:
        return _invalid(str(exc), sources=[])
    result: dict[str, Any] = {"search": query.metadata, "sources": []}
    if not settings.search.searxng_url:
        return {**result, **_not_configured()}
    loop = asyncio.get_running_loop()
    try:
        if host_deadline is None:
            sources, reason = await search.searxng(query, settings.search)
        else:
            task = asyncio.create_task(search.searxng(query, settings.search))
            if not await outbound.wait_owned([task], host_deadline - loop.time()):
                return {**result, **_deadline_exceeded()}
            sources, reason = task.result()
    except _SEARCH_ERRORS:
        return {**result, **_backend_failed()}
    result.update(status="complete", sources=[
        {**_source_fields(source, index), "fetch_status": "not_attempted"}
        for index, source in enumerate(sources, 1)
    ])
    if not sources:
        result["reason"] = reason
    return result


async def _evidence_passages(arguments: Any, settings: Settings,
                             host_deadline: float | None) -> dict[str, Any]:
    result: dict[str, Any] = {"sources": [], "passages": []}
    try:
        args = validate.check("web_evidence_passages", arguments)
        _query(args["query"])
        if args["urls"]:
            if args["recency"] is not None or args["domains"] is not None:
                raise ValueError("omit recency and domains when supplying URLs")
            sources = search.supplied(args["urls"], settings.search.num_results)
            query = None
        else:
            query = search.search_input(args["query"], args["recency"], args["domains"], _today())
    except ValueError as exc:
        return _invalid(str(exc), **result)

    loop = asyncio.get_running_loop()
    fetch = settings.fetch
    if query is None:
        result["mode"] = "supplied_urls"
        own_deadline = loop.time() + min(45, fetch.timeout_seconds * 4)
    else:
        result.update(mode="web_search", search=query.metadata)
        own_deadline = loop.time() + min(45, settings.search.timeout_seconds + fetch.timeout_seconds * 3)
    deadline = own_deadline if host_deadline is None else min(own_deadline, host_deadline)
    host_limited = host_deadline is not None and host_deadline <= own_deadline

    if query is not None:
        if not settings.search.searxng_url:
            return {**result, **_not_configured()}
        task = asyncio.create_task(search.searxng(query, settings.search))
        try:
            if not await outbound.wait_owned([task], deadline - loop.time()):
                return {**result, **(_deadline_exceeded() if host_limited else _backend_failed())}
            sources, reason = task.result()
        except _SEARCH_ERRORS:
            return {**result, **_backend_failed()}
        if not sources:
            return {**result, "status": "complete", "reason": reason}

    remaining = deadline - loop.time()
    if remaining > 0:
        sem = asyncio.Semaphore(fetch.concurrency)
        domains = query.domains if query else ()
        tasks = [asyncio.create_task(_fetch(source, fetch, domains, sem, deadline))
                 for source in sources]
        done = await outbound.wait_owned(tasks, remaining)
        docs = [task.result() if task in done else extract.TIMEOUT for task in tasks]
    else:
        docs = [extract.TIMEOUT] * len(sources)

    # Finalization runs synchronously; its input is bounded (Section 8.5).
    result["sources"] = [
        {**_source_fields(source, index), **doc.metadata(source.url)}
        for index, (source, doc) in enumerate(zip(sources, docs), 1)
    ]
    ranking = settings.ranking
    requested = ranking.default_max_passages if args["max_passages"] is None else args["max_passages"]
    result["passages"] = rank.rank(args["query"], sources, docs, settings,
                                   max(1, min(requested, ranking.max_passages)))
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


async def _page_text(arguments: Any, settings: Settings,
                     host_deadline: float | None) -> dict[str, Any]:
    try:
        args = validate.check("web_page_text", arguments)
    except validate.InvalidInput as exc:
        return _invalid(str(exc))
    try:
        parsed = outbound.parse_url(args["url"])
    except ValueError as exc:
        error = "url must be http(s)" if str(exc) == "url must be http(s)" else "invalid URL syntax"
        return _invalid(error)
    loop = asyncio.get_running_loop()
    budget = settings.fetch.timeout_seconds * 4
    # Four fetch timeouts for the download, the same again for waiting and extraction.
    deadline = loop.time() + 2 * budget
    if host_deadline is not None:
        deadline = min(deadline, host_deadline)
    doc = await _fetch(Source(parsed, {}), settings.fetch, (), asyncio.Semaphore(1), deadline,
                       extract_budget=budget)
    result = {"url": parsed.visible, **doc.metadata(parsed)}
    if doc.status != "fetched":
        return {**result, "status": "failed", "error": doc.error}
    text = "\n".join(text for _, text in doc.units)
    start, max_chars = args["start"], args["max_chars"]
    piece = text[start:] if max_chars is None else text[start:start + max_chars]
    end = start + len(piece)
    result.update(status="complete", source_id="s1", origin=doc.origin, text=piece, chars=len(piece))
    if doc.pages_examined is not None:
        result["pages_examined"] = doc.pages_examined
    result.update(total_chars=len(text), start=start, next_start=end if end < len(text) else None)
    return result


_TOOL_FUNCTIONS: dict[str, Callable[[Any, Settings, float | None], Awaitable[dict[str, Any]]]] = {
    "web_search_results": _search_results,
    "web_evidence_passages": _evidence_passages,
    "web_page_text": _page_text,
}


def _check_call(settings: Any, deadline_s: Any) -> None:
    if not isinstance(settings, Settings):
        raise TypeError("settings must be a webspell Settings instance")
    if deadline_s is not None:
        if isinstance(deadline_s, bool) or not isinstance(deadline_s, (int, float)):
            raise TypeError("deadline_s must be a number of seconds or None")
        if not math.isfinite(deadline_s) or deadline_s <= 0:
            raise ValueError("deadline_s must be a positive finite number of seconds")


async def _run(name: str, arguments: Any, settings: Settings, deadline_s: float | None) -> dict[str, Any]:
    host_deadline = None
    if deadline_s is not None:
        host_deadline = asyncio.get_running_loop().time() + deadline_s
    function = _TOOL_FUNCTIONS.get(name)
    if function is None:
        result = _invalid("unknown tool")
    else:
        result = await function(arguments, settings, host_deadline)
        result = limit.apply(name, result, settings.output)
    return result


async def run_tool(name: str, arguments: Any, settings: Settings,
                   deadline_s: float | None = None) -> dict[str, Any]:
    """Run one tool from raw (model-supplied) arguments. Used by ``call`` and the MCP adapter."""
    _check_call(settings, deadline_s)
    return await _run(name, arguments, settings, deadline_s)


# ---------- public API ------------------------------------------------------ #

async def search_results(query: str, *, recency: str | None = None, domains: list[str] | None = None,
                         settings: Settings, deadline_s: float | None = None) -> dict[str, Any]:
    """Search titles, URLs and summaries without opening pages."""
    _check_call(settings, deadline_s)
    return await _run("web_search_results", {"query": query, "recency": recency, "domains": domains},
                      settings, deadline_s)


async def evidence_passages(query: str, *, urls: list[str] | None = None,
                            max_passages: int | None = None, recency: str | None = None,
                            domains: list[str] | None = None, settings: Settings,
                            deadline_s: float | None = None) -> dict[str, Any]:
    """Fetch passages ranked for lexical relevance, from search or supplied URLs."""
    _check_call(settings, deadline_s)
    return await _run("web_evidence_passages", {
        "query": query, "max_passages": max_passages, "recency": recency, "domains": domains,
        "urls": urls,
    }, settings, deadline_s)


async def page_text(url: str, *, start: int = 0, max_chars: int | None = None,
                    settings: Settings, deadline_s: float | None = None) -> dict[str, Any]:
    """Extract the text of one public HTML or PDF URL, optionally one slice of it."""
    _check_call(settings, deadline_s)
    return await _run("web_page_text", {"url": url, "start": start, "max_chars": max_chars},
                      settings, deadline_s)


async def _supervise(work: Awaitable[dict[str, Any]],
                     cancel: threading.Event | None) -> dict[str, Any]:
    task = asyncio.ensure_future(work)
    try:
        while True:
            if cancel is not None and cancel.is_set():
                break
            await asyncio.wait({task}, timeout=_POLL_SECONDS)
            if task.done():
                if cancel is not None and cancel.is_set():
                    break
                return task.result()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return dict(_CANCELLED)
    finally:
        if not task.done():
            task.cancel()


def _close(loop: asyncio.AbstractEventLoop) -> None:
    """Cancel what is left, let it finish cancelling, and close without joining the executor."""
    try:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
    finally:
        # BaseEventLoop.close() shuts the default executor down with wait=False.
        loop.close()


def call(name: str, arguments: dict, *, settings: Settings, cancel: threading.Event | None = None,
         deadline_s: float | None = None) -> dict[str, Any]:
    """Run one tool to completion on an event loop owned by the calling thread.

    Setting ``cancel`` stops network and DNS work promptly and returns
    ``{"status": "cancelled"}``. Safe to use from several threads at once.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("webspell.call() cannot be used in a thread that runs an event loop; "
                           "await the async API instead")
    _check_call(settings, deadline_s)
    if cancel is not None and not callable(getattr(cancel, "is_set", None)):
        raise TypeError("cancel must be a threading.Event or None")
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_supervise(_run(name, arguments, settings, deadline_s), cancel))
    finally:
        _close(loop)
