"""The standalone MCP server: a thin FastMCP adapter over the library API.

Imported only by ``webspell.__main__`` (it needs the ``server`` extra). This is the
one place that reads the environment, configures logging and reads a settings file
on its own.
"""
from __future__ import annotations

import copy
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from fastmcp import FastMCP
from fastmcp.tools import Tool
from pydantic import PrivateAttr

from . import api
from .settings import DEFAULT_SETTINGS, Settings, load_settings

log = logging.getLogger("webspell")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_SERVER_NAME = "webspell"
# The result shape is a JSON object; this matches what 0.1.1 advertised.
OUTPUT_SCHEMA: dict[str, Any] = {"additionalProperties": True, "type": "object"}


class SettingsManager:
    """Cache settings and atomically reload them when the TOML file changes.

    With no path, the built-in defaults are used, which leave search unconfigured.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self._settings = DEFAULT_SETTINGS
        self._fingerprint: tuple[int, int] | None = None
        self._lock = threading.Lock()
        if self.path is None:
            log.info("no settings file; using built-in defaults (search not configured)")
        else:
            self._load_initial()

    def _load_initial(self) -> None:
        assert self.path is not None
        try:
            self._settings = load_settings(self.path)
            self._fingerprint = self._stat_fingerprint()
            log.info("loaded settings from %s", self.path)
        except Exception as exc:
            log.error("could not load %s; using built-in defaults: %s", self.path, exc)

    def _stat_fingerprint(self) -> tuple[int, int]:
        assert self.path is not None
        stat = self.path.stat()
        return stat.st_mtime_ns, stat.st_size

    def get(self) -> Settings:
        """Return one immutable snapshot, reloading a changed file if it is valid."""
        if self.path is None:
            return self._settings
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


class WebSpellTool(Tool):
    """One ``TOOLS`` entry. Raw arguments go to webSpell's own validator."""

    _settings: Callable[[], Settings] = PrivateAttr()

    async def run(self, arguments: dict[str, Any]):  # type: ignore[override]
        # One line per dispatched call; the Compose guard check counts these.
        log.info("webSpell tool call: %s", self.name)
        result = await api.run_tool(self.name, arguments, self._settings())
        return self.convert_result(result)


def build_server(manager: SettingsManager, name: str = DEFAULT_SERVER_NAME) -> FastMCP:
    """Create a FastMCP server that advertises exactly ``webspell.TOOLS``."""
    mcp = FastMCP(name)
    for entry in api.TOOLS:
        tool = WebSpellTool(name=entry["name"], description=entry["description"],
                            parameters=copy.deepcopy(entry["input_schema"]),
                            output_schema=copy.deepcopy(OUTPUT_SCHEMA))
        tool._settings = manager.get
        mcp.add_tool(tool)
    return mcp


def _list(value: str | None) -> list[str] | None:
    items = [item.strip() for item in (value or "").split(",") if item.strip()]
    return items or None


def http_options(environ: Mapping[str, str]) -> dict[str, Any]:
    """Transport options shared by every launch path.

    The Host/Origin guard is always on, in strict mode, and does not depend on
    FastMCP's default. Loopback hosts and origins are accepted with no configuration;
    ``MCP_ALLOWED_HOSTS`` and ``MCP_ALLOWED_ORIGINS`` add to them.
    """
    return {
        "transport": "streamable-http",
        # stateless_http=True is REQUIRED for llama.cpp WebUI compatibility
        # (it does not forward MCP-Session-Id on follow-up requests; see issue #20471)
        "stateless_http": True,
        "host_origin_protection": True,
        "allowed_hosts": _list(environ.get("MCP_ALLOWED_HOSTS")),
        "allowed_origins": _list(environ.get("MCP_ALLOWED_ORIGINS")),
    }


def main(environ: Mapping[str, str] | None = None) -> None:
    environ = os.environ if environ is None else environ
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    host = environ.get("MCP_HOST", DEFAULT_HOST)
    port = int(environ.get("MCP_PORT", str(DEFAULT_PORT)))
    manager = SettingsManager(environ.get("MCP_SETTINGS_FILE") or None)
    mcp = build_server(manager, environ.get("MCP_SERVER_NAME", DEFAULT_SERVER_NAME))
    options = http_options(environ)
    log.info("starting webSpell MCP server on %s:%d", host, port)
    mcp.run(host=host, port=port, **options)
