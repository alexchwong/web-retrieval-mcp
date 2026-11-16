"""Argument validation for the three tools, without a JSON Schema library.

``check`` enforces exactly the rules of each ``input_schema`` in ``webspell.TOOLS``
(types, ranges, required and unknown arguments); a test ties the two together.
There is no coercion: a string is not an integer, a boolean is not an integer and
an unknown argument is an error. Semantic rules that a schema does not express
(blank queries, recency syntax, DNS names, URL syntax, mode conflicts) are applied
by the tools afterwards and give the same ``invalid_input`` result.
"""
from __future__ import annotations

from typing import Any, Callable

MAX_QUERY_CHARS = 1000
MAX_URL_CHARS = 4096


class InvalidInput(ValueError):
    """Arguments rejected before any request is made."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _string(max_length: int | None = None) -> Callable[[str, Any], None]:
    def check(name: str, value: Any) -> None:
        if not isinstance(value, str):
            raise InvalidInput(f"{name} must be a string")
        if max_length is not None and len(value) > max_length:
            raise InvalidInput(f"{name} must be at most {max_length} characters")
    return check


def _integer(minimum: int | None = None) -> Callable[[str, Any], None]:
    def check(name: str, value: Any) -> None:
        if not _is_int(value):
            raise InvalidInput(f"{name} must be an integer")
        if minimum is not None and value < minimum:
            raise InvalidInput(f"{name} must be at least {minimum}")
    return check


def _string_list(item_max_length: int | None = None, min_items: int | None = None,
                 max_items: int | None = None, count_error: str | None = None,
                 item_error: Callable[[int], str] | None = None) -> Callable[[str, Any], None]:
    def check(name: str, value: Any) -> None:
        if not isinstance(value, list):
            raise InvalidInput(f"{name} must be an array of strings")
        if (min_items is not None and len(value) < min_items) or (
                max_items is not None and len(value) > max_items):
            raise InvalidInput(count_error or f"{name} has the wrong number of entries")
        for index, item in enumerate(value, 1):
            if not isinstance(item, str):
                raise InvalidInput(f"{name} must be an array of strings")
            if item_max_length is not None and len(item) > item_max_length:
                raise InvalidInput(item_error(index) if item_error else
                                   f"{name} entries must be at most {item_max_length} characters")
    return check


def _nullable(inner: Callable[[str, Any], None]) -> Callable[[str, Any], None]:
    def check(name: str, value: Any) -> None:
        if value is not None:
            inner(name, value)
    return check


_QUERY = _string(MAX_QUERY_CHARS)
_RECENCY = _nullable(_string())
_DOMAINS = _nullable(_string_list(min_items=1, max_items=5,
                                  count_error="domains must contain one to five DNS names"))
_URLS = _nullable(_string_list(
    item_max_length=MAX_URL_CHARS,
    item_error=lambda index: f"URL item {index} must be at most {MAX_URL_CHARS} characters"))

# name -> (checker, required, default)
FIELDS: dict[str, dict[str, tuple[Callable[[str, Any], None], bool, Any]]] = {
    "web_search_results": {
        "query": (_QUERY, True, None),
        "recency": (_RECENCY, False, None),
        "domains": (_DOMAINS, False, None),
    },
    "web_evidence_passages": {
        "query": (_QUERY, True, None),
        "max_passages": (_nullable(_integer()), False, None),
        "recency": (_RECENCY, False, None),
        "domains": (_DOMAINS, False, None),
        "urls": (_URLS, False, None),
    },
    "web_page_text": {
        "url": (_string(MAX_URL_CHARS), True, None),
        "start": (_integer(0), False, 0),
        "max_chars": (_nullable(_integer(1)), False, None),
    },
}


def check(tool: str, arguments: Any) -> dict[str, Any]:
    """Validate ``arguments`` for ``tool`` and return them with defaults filled in."""
    fields = FIELDS.get(tool)
    if fields is None:
        raise InvalidInput("unknown tool")
    if not isinstance(arguments, dict):
        raise InvalidInput("arguments must be an object")
    for key in arguments:
        if key not in fields:
            raise InvalidInput(f"unknown argument: {key}" if isinstance(key, str) and len(key) <= 64
                               else "unknown argument")
    checked: dict[str, Any] = {}
    for name, (checker, required, default) in fields.items():
        if name not in arguments:
            if required:
                raise InvalidInput(f"{name} is required")
            checked[name] = default
            continue
        checker(name, arguments[name])
        checked[name] = arguments[name]
    return checked
