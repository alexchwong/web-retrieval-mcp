"""The result byte limit (Section 7)."""
from __future__ import annotations

import json
from typing import Any, Callable

from .settings import OutputSettings

TITLE_CHARS = 80
SNIPPET_CHARS = 160
MIN_PASSAGE_TEXT = 200
_SEPARATOR = 2  # ", " between list items with Python's default separators


def result_bytes(result: dict, *, ascii: bool = False) -> int:
    """The one definition of result size: UTF-8 bytes of ``json.dumps`` with default separators."""
    return len(json.dumps(result, ensure_ascii=ascii).encode("utf-8"))


def output_limit() -> dict[str, Any]:
    return {"status": "failed", "reason": "output_limit", "error": "result exceeds the output limit"}


def _cut(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit - 1] + "…"
    return value


def _extra(sizes: list[int]) -> int:
    """Bytes added when ``[]`` becomes a list whose items serialise to ``sizes``."""
    return sum(sizes) + _SEPARATOR * (len(sizes) - 1) if sizes else 0


class _Measure:
    def __init__(self, limit: int, ascii: bool) -> None:
        self.limit = limit
        self.ascii = ascii

    def size(self, value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=self.ascii).encode("utf-8"))

    def fits(self, value: Any) -> bool:
        return self.size(value) <= self.limit


def apply(tool: str, result: dict[str, Any], output: OutputSettings) -> dict[str, Any]:
    """Return ``result`` reduced to ``output.max_result_bytes``, or ``output_limit``."""
    if output.max_result_bytes is None:
        return result
    measure = _Measure(output.max_result_bytes, output.ascii_json)
    if measure.fits(result):
        return result
    reducer = {"web_search_results": _search_results, "web_evidence_passages": _evidence,
               "web_page_text": _page_text}[tool]
    # Preserve the search outcome and usable sources when diagnostics consume the budget.
    search = result.get("search", {})
    if any(attempt.get("engine_errors") for attempt in search.get("attempts", [])):
        attempts = [{k: v for k, v in attempt.items() if k != "engine_errors"}
                    for attempt in search["attempts"]]
        result = {**result, "output_capped": True,
                  "search": {**search, "attempts": attempts, "engine_errors_omitted": True}}
        if measure.fits(result):
            return result
    reduced = reducer(result, measure)
    if reduced is None or not measure.fits(reduced):
        return output_limit()
    return reduced


def _trim_summaries(result: dict[str, Any]) -> dict[str, Any]:
    reduced = dict(result)
    reduced["sources"] = [
        {**source, **({"title": _cut(source["title"], TITLE_CHARS)} if "title" in source else {}),
         **({"snippet": _cut(source["snippet"], SNIPPET_CHARS)} if "snippet" in source else {})}
        for source in result.get("sources", [])
    ]
    if "passages" in result:
        reduced["passages"] = [
            {**passage, **({"title": _cut(passage["title"], TITLE_CHARS)} if "title" in passage else {})}
            for passage in result["passages"]
        ]
    reduced["output_capped"] = True
    return reduced


def _keep_sources(result: dict[str, Any], measure: _Measure, *, skip: bool) -> dict[str, Any] | None:
    """Keep source records in order; ``skip`` passes over one that does not fit, else stop."""
    sources = result.get("sources", [])
    base = {**result, "sources": [], "sources_omitted": len(sources)}
    base_size = measure.size(base)
    if base_size > measure.limit:
        return None
    kept: list[dict[str, Any]] = []
    sizes: list[int] = []
    for source in sources:
        size = measure.size(source)
        if base_size + _extra(sizes + [size]) <= measure.limit:
            kept.append(source)
            sizes.append(size)
        elif not skip:
            break
    if sources and not kept:
        return None
    return {**result, "sources": kept, "sources_omitted": len(sources) - len(kept)}


def _search_results(result: dict[str, Any], measure: _Measure) -> dict[str, Any] | None:
    reduced = _trim_summaries(result)
    if measure.fits(reduced):
        return reduced
    return _keep_sources(reduced, measure, skip=True)


def _walk(passages: list[dict[str, Any]], measure: _Measure, base_size: int,
          source_cost: Callable[[dict[str, Any], set[str]], tuple[int | None, str | None]],
          ) -> tuple[list[dict[str, Any]], list[str], list[int]]:
    """Select passages in rank order (Section 7, steps 3 and 4).

    Keep a passage if the result still fits with it; else keep it cut to the longest
    prefix of at least ``MIN_PASSAGE_TEXT`` characters that fits, and stop; else skip
    it. ``source_cost`` gives the size of the passage's source record when it must be
    added with the passage, or ``None`` when it is already present.
    """
    kept: list[dict[str, Any]] = []
    sizes: list[int] = []
    source_ids: list[str] = []
    source_sizes: list[int] = []

    def total(passage_size: int, extra_source: int | None) -> int:
        added = source_sizes + ([extra_source] if extra_source is not None else [])
        return base_size + _extra(sizes + [passage_size]) + _extra(added)

    for passage in passages:
        cost, source_id = source_cost(passage, set(source_ids))
        size = measure.size(passage)
        if total(size, cost) <= measure.limit:
            kept.append(passage)
            sizes.append(size)
            if cost is not None and source_id is not None:
                source_ids.append(source_id)
                source_sizes.append(cost)
            continue
        text = passage.get("text", "")
        if not isinstance(text, str) or len(text) <= MIN_PASSAGE_TEXT:
            continue

        def cut(length: int) -> dict[str, Any]:
            return {**passage, "text": text[:length], "text_capped": True}

        if total(measure.size(cut(MIN_PASSAGE_TEXT)), cost) > measure.limit:
            continue
        low, high = MIN_PASSAGE_TEXT, len(text) - 1
        while low < high:
            middle = (low + high + 1) // 2
            if total(measure.size(cut(middle)), cost) <= measure.limit:
                low = middle
            else:
                high = middle - 1
        kept.append(cut(low))
        sizes.append(measure.size(kept[-1]))
        if cost is not None and source_id is not None:
            source_ids.append(source_id)
            source_sizes.append(cost)
        break
    return kept, source_ids, source_sizes


def _evidence(result: dict[str, Any], measure: _Measure) -> dict[str, Any] | None:
    # Step 1: cut snippets and titles.
    reduced = _trim_summaries(result)
    if measure.fits(reduced):
        return reduced
    # Step 2: remove snippets.
    reduced["sources"] = [{k: v for k, v in source.items() if k != "snippet"}
                          for source in reduced["sources"]]
    if measure.fits(reduced):
        return reduced
    passages = reduced.get("passages", [])
    sources = reduced["sources"]
    if not passages:
        return _keep_sources(reduced, measure, skip=False)

    # Step 3: every source record, passages selected in rank order.
    base = {**reduced, "passages": [], "passages_omitted": len(passages)}
    base_size = measure.size(base)
    if base_size <= measure.limit:
        kept, _, _ = _walk(passages, measure, base_size, lambda _p, _seen: (None, None))
        if kept:
            return {**reduced, "passages": kept, "passages_omitted": len(passages) - len(kept)}

    # Step 4: no unreferenced source records; each passage brings its own source.
    by_id = {source.get("source_id"): source for source in sources}
    source_sizes_by_id = {key: measure.size(value) for key, value in by_id.items()}
    base = {**reduced, "sources": [], "passages": [], "passages_omitted": len(passages),
            "sources_omitted": len(sources)}
    base_size = measure.size(base)
    if base_size > measure.limit:
        return None

    def source_cost(passage: dict[str, Any], seen: set[str]) -> tuple[int | None, str | None]:
        source_id = passage.get("source_id")
        if source_id in seen or source_id not in by_id:
            return None, None
        return source_sizes_by_id[source_id], source_id

    kept, kept_ids, kept_source_sizes = _walk(passages, measure, base_size, source_cost)
    if not kept:
        return None  # Step 5: output_limit.
    passage_bytes = _extra([measure.size(passage) for passage in kept])
    chosen = set(kept_ids)
    for source in sources:
        source_id = source.get("source_id")
        if source_id in chosen:
            continue
        size = source_sizes_by_id[source_id]
        if base_size + passage_bytes + _extra(kept_source_sizes + [size]) > measure.limit:
            break
        chosen.add(source_id)
        kept_source_sizes.append(size)
    kept_sources = [source for source in sources if source.get("source_id") in chosen]
    return {**reduced, "sources": kept_sources, "passages": kept,
            "passages_omitted": len(passages) - len(kept),
            "sources_omitted": len(sources) - len(kept_sources)}


def _page_text(result: dict[str, Any], measure: _Measure) -> dict[str, Any] | None:
    text = result.get("text")
    if not isinstance(text, str) or not text:
        return None
    start = result.get("start", 0)
    total = result.get("total_chars", start + len(text))

    def cut(length: int) -> dict[str, Any]:
        end = start + length
        return {**result, "text": text[:length], "chars": length,
                "next_start": end if end < total else None, "output_capped": True}

    # A page must advance: at least one character, so next_start > start.
    if not measure.fits(cut(1)):
        return None
    low, high = 1, len(text) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if measure.fits(cut(middle)):
            low = middle
        else:
            high = middle - 1
    return cut(low)
