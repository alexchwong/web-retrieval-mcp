"""Validated, live-reloadable settings for the web retrieval MCP server."""
from __future__ import annotations

import logging
import os
import threading
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.10 backport
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar


log = logging.getLogger("web-retrieval")
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class SearchSettings:
    searxng_url: str = "http://localhost:8888"
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
    user_agent: str = "web-retrieval/1.0"


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
class Settings:
    search: SearchSettings = SearchSettings()
    fetch: FetchSettings = FetchSettings()
    chunk: ChunkSettings = ChunkSettings()
    ranking: RankingSettings = RankingSettings()


DEFAULT_SETTINGS = Settings()


def _value(
    section: dict[str, Any],
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


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a TOML table")
    return value


def load_settings(path: Path) -> Settings:
    """Load and validate a settings file, filling omitted values with defaults."""
    with path.open("rb") as handle:
        data = tomllib.load(handle)

    search_data = _section(data, "search")
    fetch_data = _section(data, "fetch")
    chunk_data = _section(data, "chunk")
    ranking_data = _section(data, "ranking")

    search = SearchSettings(
        searxng_url=_value(
            search_data, "searxng_url", DEFAULT_SETTINGS.search.searxng_url, str
        ).rstrip("/"),
        num_results=_value(
            search_data, "num_results", DEFAULT_SETTINGS.search.num_results, int
        ),
        timeout_seconds=_value(
            search_data, "timeout_seconds", DEFAULT_SETTINGS.search.timeout_seconds, float
        ),
        language=_value(search_data, "language", DEFAULT_SETTINGS.search.language, str),
        safesearch=_value(search_data, "safesearch", DEFAULT_SETTINGS.search.safesearch, int),
    )
    fetch = FetchSettings(
        concurrency=_value(fetch_data, "concurrency", DEFAULT_SETTINGS.fetch.concurrency, int),
        timeout_seconds=_value(
            fetch_data, "timeout_seconds", DEFAULT_SETTINGS.fetch.timeout_seconds, float
        ),
        max_bytes=_value(
            fetch_data, "max_bytes", DEFAULT_SETTINGS.fetch.max_bytes, int
        ),
        max_document_chars=_value(
            fetch_data,
            "max_document_chars",
            DEFAULT_SETTINGS.fetch.max_document_chars,
            int,
        ),
        max_pdf_pages=_value(
            fetch_data, "max_pdf_pages", DEFAULT_SETTINGS.fetch.max_pdf_pages, int
        ),
        user_agent=_value(fetch_data, "user_agent", DEFAULT_SETTINGS.fetch.user_agent, str),
    )
    chunk = ChunkSettings(
        words=_value(chunk_data, "words", DEFAULT_SETTINGS.chunk.words, int),
        overlap=_value(chunk_data, "overlap", DEFAULT_SETTINGS.chunk.overlap, int),
    )
    ranking = RankingSettings(
        default_max_passages=_value(
            ranking_data, "default_max_passages", DEFAULT_SETTINGS.ranking.default_max_passages, int
        ),
        max_passages=_value(
            ranking_data, "max_passages", DEFAULT_SETTINGS.ranking.max_passages, int
        ),
        max_passages_per_source=_value(
            ranking_data,
            "max_passages_per_source",
            DEFAULT_SETTINGS.ranking.max_passages_per_source,
            int,
        ),
    )

    if not search.searxng_url.startswith(("http://", "https://")):
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

    return Settings(search=search, fetch=fetch, chunk=chunk, ranking=ranking)


class SettingsManager:
    """Cache settings and atomically reload them when the TOML file changes."""

    def __init__(self, path: Path | None = None) -> None:
        default_path = Path(__file__).with_name("settings.toml")
        self.path = path or Path(os.getenv("MCP_SETTINGS_FILE", default_path))
        self._settings = DEFAULT_SETTINGS
        self._fingerprint: tuple[int, int] | None = None
        self._lock = threading.Lock()
        self._load_initial()

    def _load_initial(self) -> None:
        try:
            self._settings = load_settings(self.path)
            self._fingerprint = self._stat_fingerprint()
            log.info("loaded settings from %s", self.path)
        except Exception as exc:
            log.error("could not load %s; using built-in defaults: %s", self.path, exc)

    def _stat_fingerprint(self) -> tuple[int, int]:
        stat = self.path.stat()
        return stat.st_mtime_ns, stat.st_size

    def get(self) -> Settings:
        """Return one immutable snapshot, reloading a changed file if it is valid."""
        try:
            fingerprint = self._stat_fingerprint()
        except OSError as exc:
            log.warning("settings file unavailable; keeping last known-good settings: %s", exc)
            return self._settings

        if fingerprint == self._fingerprint:
            return self._settings

        with self._lock:
            try:
                fingerprint = self._stat_fingerprint()
                if fingerprint == self._fingerprint:
                    return self._settings
                updated = load_settings(self.path)
            except Exception as exc:
                # Remember this version to avoid logging the same bad edit on every request.
                self._fingerprint = fingerprint
                log.error("settings reload rejected; keeping last known-good settings: %s", exc)
                return self._settings

            self._settings = updated
            self._fingerprint = fingerprint
            log.info("reloaded settings from %s", self.path)
            return self._settings
