"""Validated settings. No environment reads and no file reads except ``load_settings``."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

T = TypeVar("T")

MIN_RESULT_BYTES = 4096


@dataclass(frozen=True, slots=True)
class SearchSettings:
    # Empty means search is not configured: both search paths return
    # ``search_not_configured`` and make no request.
    searxng_url: str = ""
    num_results: int = 8
    timeout_seconds: float = 10.0
    language: str = "en"
    safesearch: int = 0


@dataclass(frozen=True, slots=True)
class FetchSettings:
    concurrency: int = 6
    timeout_seconds: float = 12.0
    max_bytes: int = 20_000_000
    max_document_chars: int = 40_000
    max_pdf_pages: int = 40
    user_agent: str = "webSpell/0.2"


@dataclass(frozen=True, slots=True)
class ChunkSettings:
    words: int = 350
    overlap: int = 50


@dataclass(frozen=True, slots=True)
class RankingSettings:
    default_max_passages: int = 8
    max_passages: int = 20
    max_passages_per_source: int = 2


@dataclass(frozen=True, slots=True)
class OutputSettings:
    # None means no limit (0.1.1 behaviour).
    max_result_bytes: int | None = None
    ascii_json: bool = False


@dataclass(frozen=True, slots=True)
class Settings:
    search: SearchSettings = SearchSettings()
    fetch: FetchSettings = FetchSettings()
    chunk: ChunkSettings = ChunkSettings()
    ranking: RankingSettings = RankingSettings()
    output: OutputSettings = OutputSettings()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Settings":
        """Validate a mapping exactly as ``load_settings`` validates a TOML file."""
        return _from_mapping(data)


DEFAULT_SETTINGS = Settings()


def _value(
    section: Mapping[str, Any],
    key: str,
    default: T,
    convert: Callable[[Any], T],
) -> T:
    raw = section.get(key, default)
    if isinstance(raw, bool) and convert in (int, float):
        raise ValueError(f"{key} must be a number, not a boolean")
    try:
        return convert(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid value for {key}: {raw!r}") from exc


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"[{name}] must be a TOML table")
    return value


def _from_mapping(data: Mapping[str, Any]) -> Settings:
    if not isinstance(data, Mapping):
        raise ValueError("settings must be a mapping")
    search_data = _section(data, "search")
    fetch_data = _section(data, "fetch")
    chunk_data = _section(data, "chunk")
    ranking_data = _section(data, "ranking")
    output_data = _section(data, "output")
    defaults = DEFAULT_SETTINGS

    search = SearchSettings(
        searxng_url=_value(search_data, "searxng_url", defaults.search.searxng_url, str).rstrip("/"),
        num_results=_value(search_data, "num_results", defaults.search.num_results, int),
        timeout_seconds=_value(search_data, "timeout_seconds", defaults.search.timeout_seconds, float),
        language=_value(search_data, "language", defaults.search.language, str),
        safesearch=_value(search_data, "safesearch", defaults.search.safesearch, int),
    )
    fetch = FetchSettings(
        concurrency=_value(fetch_data, "concurrency", defaults.fetch.concurrency, int),
        timeout_seconds=_value(fetch_data, "timeout_seconds", defaults.fetch.timeout_seconds, float),
        max_bytes=_value(fetch_data, "max_bytes", defaults.fetch.max_bytes, int),
        max_document_chars=_value(
            fetch_data, "max_document_chars", defaults.fetch.max_document_chars, int),
        max_pdf_pages=_value(fetch_data, "max_pdf_pages", defaults.fetch.max_pdf_pages, int),
        user_agent=_value(fetch_data, "user_agent", defaults.fetch.user_agent, str),
    )
    chunk = ChunkSettings(
        words=_value(chunk_data, "words", defaults.chunk.words, int),
        overlap=_value(chunk_data, "overlap", defaults.chunk.overlap, int),
    )
    ranking = RankingSettings(
        default_max_passages=_value(
            ranking_data, "default_max_passages", defaults.ranking.default_max_passages, int),
        max_passages=_value(ranking_data, "max_passages", defaults.ranking.max_passages, int),
        max_passages_per_source=_value(
            ranking_data, "max_passages_per_source", defaults.ranking.max_passages_per_source, int),
    )
    max_result_bytes = output_data.get("max_result_bytes")
    if max_result_bytes is not None and (
        isinstance(max_result_bytes, bool) or not isinstance(max_result_bytes, int)
    ):
        raise ValueError("output.max_result_bytes must be an integer")
    ascii_json = output_data.get("ascii_json", defaults.output.ascii_json)
    if not isinstance(ascii_json, bool):
        raise ValueError("output.ascii_json must be a boolean")
    output = OutputSettings(max_result_bytes=max_result_bytes, ascii_json=ascii_json)

    if search.searxng_url and not search.searxng_url.startswith(("http://", "https://")):
        raise ValueError("search.searxng_url must be an http(s) URL")
    if search.num_results < 1:
        raise ValueError("search.num_results must be at least 1")
    if search.timeout_seconds <= 0:
        raise ValueError("search.timeout_seconds must be positive")
    if not search.language.strip():
        raise ValueError("search.language must not be empty")
    if search.safesearch not in (0, 1, 2):
        raise ValueError("search.safesearch must be 0, 1, or 2")
    if fetch.concurrency < 1 or fetch.timeout_seconds <= 0:
        raise ValueError("fetch concurrency and timeout_seconds must be positive")
    if min(fetch.max_bytes, fetch.max_document_chars, fetch.max_pdf_pages) < 1:
        raise ValueError("fetch size, character, and page limits must be positive")
    if not fetch.user_agent.strip():
        raise ValueError("fetch.user_agent must not be empty")
    if chunk.words < 1 or chunk.overlap < 0 or chunk.overlap >= chunk.words:
        raise ValueError("chunk.overlap must be non-negative and less than chunk.words")
    if ranking.default_max_passages < 1 or ranking.max_passages < 1:
        raise ValueError("ranking passage limits must be positive")
    if ranking.default_max_passages > ranking.max_passages:
        raise ValueError("ranking.default_max_passages cannot exceed ranking.max_passages")
    if ranking.max_passages_per_source < 1:
        raise ValueError("ranking.max_passages_per_source must be positive")
    if output.max_result_bytes is not None and output.max_result_bytes < MIN_RESULT_BYTES:
        raise ValueError(f"output.max_result_bytes must be at least {MIN_RESULT_BYTES}")

    return Settings(search=search, fetch=fetch, chunk=chunk, ranking=ranking, output=output)


def load_settings(path: Path | str) -> Settings:
    """Load and validate a TOML settings file, filling omitted values with defaults."""
    with Path(path).open("rb") as handle:
        data = tomllib.load(handle)
    return _from_mapping(data)
