"""
Web retrieval MCP server.

Exposes two tools to an LLM running in llama-server's WebUI:

  - search_web(query, max_passages=8)
      Search the web (SearxNG), fetch top hits in parallel, chunk, BM25-rank
      passages against the query, return the top-K passages with source URLs.
      This is the cheap path; the LLM never sees the raw HTML.

  - read_url(url)
      Pull a single page through trafilatura and return clean text.
      Escape valve when the model decides it needs to see one source in full.

Notes:
  - Runs as Streamable HTTP with stateless_http=True.
    Required because llama.cpp WebUI bug #20471 doesn't forward MCP-Session-Id
    on subsequent requests; stateless mode skips that handshake.
  - Bound to all interfaces inside the container; ports are mapped to
    loopback only on the host (see docker-compose.yml).
"""
from __future__ import annotations

import asyncio
import os
import re
import logging
from typing import Any
from urllib.parse import urlparse

import httpx
import pymupdf  # PyMuPDF; also importable as `fitz`
import trafilatura
from fastmcp import FastMCP
from rank_bm25 import BM25Okapi
from settings import FetchSettings, SearchSettings, SettingsManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("web-retrieval")

MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "8765"))
MCP_SERVER_NAME = os.getenv("MCP_SERVER_NAME", "web-retrieval")

# Filetypes we don't try to extract (archives, media). PDFs are handled.
SKIP_EXTS = re.compile(
    r"\.(zip|tar|gz|7z|rar|mp[34]|m4a|wav|mov|avi|mkv|jpg|jpeg|png|gif|webp|svg|ico)(\?|$)",
    re.IGNORECASE,
)

mcp = FastMCP(MCP_SERVER_NAME)
settings_manager = SettingsManager()


# ---------- helpers --------------------------------------------------------- #

def _tokenize(text: str) -> list[str]:
    """Cheap, deterministic tokenizer for BM25. Lowercase alphanumeric runs."""
    return re.findall(r"\w+", text.lower())


def _chunk_words(text: str, size: int, overlap: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    out, i, step = [], 0, max(1, size - overlap)
    while i < len(words):
        chunk = " ".join(words[i : i + size]).strip()
        if chunk:
            out.append(chunk)
        i += step
    return out


async def _searxng(
    client: httpx.AsyncClient, query: str, settings: SearchSettings
) -> list[dict]:
    r = await client.get(
        f"{settings.searxng_url}/search",
        params={
            "q": query,
            "format": "json",
            "safesearch": str(settings.safesearch),
            "language": settings.language,
        },
        timeout=settings.timeout_seconds,
    )
    r.raise_for_status()
    data = r.json()
    out = []
    for item in data.get("results", []):
        url = item.get("url")
        if not url or SKIP_EXTS.search(url):
            continue
        out.append({
            "url": url,
            "title": item.get("title", "") or "",
            "snippet": item.get("content", "") or "",
        })
        if len(out) >= settings.num_results:
            break
    return out


def _extract_html(html: str, max_chars: int) -> str:
    """Sync HTML->text extraction; run in a worker thread."""
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_recall=False,
        no_fallback=False,
    )
    return (text or "")[:max_chars]


def _extract_pdf(data: bytes, max_pages: int, max_chars: int) -> str:
    """Sync PDF->text extraction via PyMuPDF; run in a worker thread.

    Caps at the configured page and character limits so a 600-page
    PDF can't blow up the model's context. Returns "" on encrypted or
    malformed files rather than raising.
    """
    try:
        parts: list[str] = []
        total = 0
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            for i in range(min(doc.page_count, max_pages)):
                page_text = doc[i].get_text("text")
                parts.append(page_text)
                total += len(page_text)
                if total >= max_chars:
                    break
        return "\n".join(parts)[:max_chars]
    except Exception as e:  # encrypted, corrupt, or not actually a PDF
        log.info("pdf extract failed: %s", e)
        return ""


async def _fetch_one(
    client: httpx.AsyncClient,
    url: str,
    sem: asyncio.Semaphore,
    settings: FetchSettings,
) -> str:
    """Fetch one URL and return clean text. Handles HTML and PDF; skips the rest.

    Streams the body so an oversized response is abandoned mid-download
    instead of being buffered whole.
    """
    async with sem:
        try:
            async with client.stream(
                "GET",
                url,
                timeout=settings.timeout_seconds,
                follow_redirects=True,
                headers={
                    "User-Agent": settings.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/pdf",
                },
            ) as r:
                r.raise_for_status()
                ctype = r.headers.get("content-type", "").lower()
                path = urlparse(url).path.lower()
                # Trust content-type first, fall back to the URL extension.
                is_pdf = "application/pdf" in ctype or path.endswith(".pdf")
                is_html = "html" in ctype or "xml" in ctype
                if not (is_pdf or is_html):
                    return ""
                encoding = r.encoding or "utf-8"

                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > settings.max_bytes:
                        log.info("response too large, abandoned: %s", url)
                        return ""
        except Exception as e:
            log.info("fetch failed %s: %s", url, e)
            return ""

    # Extraction is CPU-bound and synchronous — offload to a worker thread.
    if is_pdf:
        return await asyncio.to_thread(
            _extract_pdf,
            bytes(buf),
            settings.max_pdf_pages,
            settings.max_document_chars,
        )
    html = buf.decode(encoding, errors="replace")[: settings.max_document_chars * 4]
    return await asyncio.to_thread(_extract_html, html, settings.max_document_chars)


# ---------- tools ----------------------------------------------------------- #

@mcp.tool
async def search_web(query: str, max_passages: int | None = None) -> dict[str, Any]:
    """
    Search the web and return the most relevant passages for the query.

    Use this FIRST for any factual question that needs current information.
    Returns 8 short passages (each ~350 words) with source URLs. Cite them
    by URL in your answer. If a passage looks promising but incomplete,
    you can call read_url(url) to get the full page.

    Args:
        query: A focused search query. Keep it short (3-8 words works best).
        max_passages: Number of passages to return (default 8, max 20).

    Returns:
        {
          "passages": [{"text": "...", "url": "...", "title": "...", "score": 0.0}, ...],
          "sources":  [{"url": "...", "title": "...", "snippet": "..."}, ...]
        }
    """
    settings = settings_manager.get()
    requested_passages = (
        settings.ranking.default_max_passages if max_passages is None else int(max_passages)
    )
    max_passages = max(1, min(requested_passages, settings.ranking.max_passages))
    log.info("search_web: %r (k=%d)", query, max_passages)

    async with httpx.AsyncClient(http2=True) as client:
        try:
            hits = await _searxng(client, query, settings.search)
        except Exception as e:
            return {"error": f"searxng unreachable: {e}", "passages": [], "sources": []}

        if not hits:
            return {"passages": [], "sources": []}

        sem = asyncio.Semaphore(settings.fetch.concurrency)
        texts = await asyncio.gather(
            *[_fetch_one(client, h["url"], sem, settings.fetch) for h in hits]
        )

    # Build a flat list of (passage, source-meta) pairs
    passages: list[dict[str, Any]] = []
    for hit, text in zip(hits, texts):
        if not text:
            continue
        for chunk in _chunk_words(text, settings.chunk.words, settings.chunk.overlap):
            passages.append({"text": chunk, "url": hit["url"], "title": hit["title"]})

    sources = [
        {"url": h["url"], "title": h["title"], "snippet": h["snippet"]}
        for h in hits
    ]

    if not passages:
        # All fetches failed; the model still gets the snippets to work with
        return {"passages": [], "sources": sources, "note": "no pages fetched; snippets only"}

    # BM25 rank passages by relevance to the query
    tokenized = [_tokenize(p["text"]) for p in passages]
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(_tokenize(query))

    ranked_idx = sorted(range(len(scores)), key=lambda i: -scores[i])[:max_passages]
    top = []
    seen_urls: dict[str, int] = {}
    for i in ranked_idx:
        p = passages[i]
        # Light diversity: cap passages from any one source URL.
        if seen_urls.get(p["url"], 0) >= settings.ranking.max_passages_per_source:
            continue
        seen_urls[p["url"]] = seen_urls.get(p["url"], 0) + 1
        top.append({**p, "score": round(float(scores[i]), 3)})

    return {"passages": top, "sources": sources}


@mcp.tool
async def read_url(url: str) -> dict[str, Any]:
    """
    Fetch a single URL and return clean, extracted text.

    Handles both HTML pages and PDF files (PDFs are extracted with PyMuPDF,
    capped at the first 40 pages). Use SPARINGLY — only when a passage from
    search_web() looks promising but incomplete, or when the user gives you a
    URL directly. The full document can be large; prefer search_web() first.

    Args:
        url: A full http(s) URL, pointing to an HTML page or a PDF.

    Returns:
        {"url": "...", "text": "...", "chars": N} or {"error": "..."}
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"error": "url must be http(s)"}
    settings = settings_manager.get()
    log.info("read_url: %s", url)
    async with httpx.AsyncClient(http2=True) as client:
        sem = asyncio.Semaphore(1)
        text = await _fetch_one(client, url, sem, settings.fetch)
    if not text:
        return {"error": "fetch or extraction failed", "url": url}
    return {"url": url, "text": text, "chars": len(text)}


# ---------- entrypoint ------------------------------------------------------ #

if __name__ == "__main__":
    startup_settings = settings_manager.get()
    log.info(
        "starting web retrieval MCP on %s:%d (SearxNG=%s)",
        MCP_HOST,
        MCP_PORT,
        startup_settings.search.searxng_url,
    )
    # stateless_http=True is REQUIRED for llama.cpp WebUI compatibility
    # (it does not forward MCP-Session-Id on follow-up requests; see issue #20471)
    mcp.run(
        transport="streamable-http",
        host=MCP_HOST,
        port=MCP_PORT,
        stateless_http=True,
    )
