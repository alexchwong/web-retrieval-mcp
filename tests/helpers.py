"""Offline fixtures: a threaded loopback HTTP server, a blocking resolver, settings."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import functools
import json
import socket
import threading
import time
from typing import Any, Callable, Iterator
from unittest.mock import patch

from webspell import limits, outbound
from webspell.settings import DEFAULT_SETTINGS, Settings

SETTINGS = DEFAULT_SETTINGS


def settings(**sections: dict[str, Any]) -> Settings:
    """``settings(search={"searxng_url": ...}, fetch={...})``: defaults with overrides."""
    current = DEFAULT_SETTINGS
    for name, values in sections.items():
        current = replace(current, **{name: replace(getattr(current, name), **values)})
    return current


class Exchange:
    """One request on the fixture server."""

    def __init__(self, server: "LoopbackServer", conn: socket.socket, method: str, target: str,
                 headers: dict[str, str]) -> None:
        self.server = server
        self.conn = conn
        self.method = method
        self.target = target
        self.path = target.split("?", 1)[0]
        self.headers = headers
        self.sent = 0
        self.aborted = False

    def send_head(self, status: int = 200, headers: dict[str, str] | None = None) -> None:
        lines = [f"HTTP/1.1 {status} X"] + [f"{k}: {v}" for k, v in (headers or {}).items()]
        lines.append("Connection: close")
        self.conn.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))

    def send_body(self, body: bytes, chunk: int = 65536, pause: float = 0.0) -> bool:
        """Send ``body``; return False if the client went away first."""
        try:
            for offset in range(0, len(body), chunk):
                self.conn.sendall(body[offset:offset + chunk])
                self.sent += len(body[offset:offset + chunk])
                if pause:
                    time.sleep(pause)
        except OSError:
            self.aborted = True
            self.server.closed_early.append(self.path)
            return False
        return True

    def respond(self, status: int = 200, body: bytes = b"", headers: dict[str, str] | None = None,
                **options: Any) -> None:
        headers = dict(headers or {})
        headers.setdefault("Content-Length", str(len(body)))
        self.send_head(status, headers)
        self.send_body(body, **options)

    def json(self, payload: Any) -> None:
        self.respond(200, json.dumps(payload).encode(), {"Content-Type": "application/json"})

    def stall(self, release: threading.Event | None = None, timeout: float = 30) -> None:
        """Send nothing until the client closes the connection (or ``release`` is set)."""
        self.conn.settimeout(0.02)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if release is not None and release.is_set():
                return
            try:
                if self.conn.recv(1) == b"":
                    self.server.closed.append(self.path)
                    return
            except socket.timeout:
                continue
            except OSError:
                self.server.closed.append(self.path)
                return


class LoopbackServer:
    """A plain HTTP/1.1 server on 127.0.0.1 in its own threads.

    ``handler(exchange)`` answers each request. The server records requests,
    connections the client closed (``closed``, ``closed_early``) and the most
    requests ever open at once (``max_open``).
    """

    def __init__(self, handler: Callable[[Exchange], None]) -> None:
        self.handler = handler
        self.requests: list[Exchange] = []
        self.closed: list[str] = []
        self.closed_early: list[str] = []
        self.open = 0
        self.max_open = 0
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(128)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _accept(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32768)
        try:
            data = b""
            conn.settimeout(10)
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
            head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
            method, target, _ = head[0].split(" ", 2)
            headers = {}
            for line in head[1:]:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
            exchange = Exchange(self, conn, method, target, headers)
            with self._lock:
                self.requests.append(exchange)
                self.open += 1
                self.max_open = max(self.max_open, self.open)
            try:
                self.handler(exchange)
            finally:
                with self._lock:
                    self.open -= 1
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        self._stop = True
        self._sock.close()

    def __enter__(self) -> "LoopbackServer":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


class BlockingResolver:
    """Stands in for ``socket.getaddrinfo``; blocks every lookup until released."""

    def __init__(self, addresses: dict[str, str] | None = None, block: bool = True) -> None:
        self.addresses = addresses or {}
        self.release = threading.Event()
        if not block:
            self.release.set()
        self.calls: list[str] = []
        self.inside = 0
        self.max_inside = 0
        self._lock = threading.Lock()

    def __call__(self, host: str, port: int, *args: Any, **kwargs: Any):
        with self._lock:
            self.calls.append(host)
            self.inside += 1
            self.max_inside = max(self.max_inside, self.inside)
        try:
            self.release.wait(30)
            name = host.removesuffix(".")
            if name not in self.addresses:
                raise socket.gaierror(socket.EAI_NONAME, "not found")
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                     (self.addresses[name], port))]
        finally:
            with self._lock:
                self.inside -= 1


@contextmanager
def resolver(fake: Callable[..., Any]) -> Iterator[Any]:
    with patch.object(limits, "_getaddrinfo", fake):
        yield fake


_real_download = outbound.download


@contextmanager
def loopback_documents() -> Iterator[None]:
    """Let the real document client reach the loopback fixture on any port.

    Only the address and port policy hooks are relaxed; DNS, redirects, the
    download cap and everything else run as in production.
    """
    download = functools.partial(_real_download, _public=lambda _ip: True,
                                 _allow_port=lambda _port: True)
    with patch.object(outbound, "download", download):
        yield


def slots_in_use() -> dict[str, int]:
    return {slots.name: slots.in_use for slots in
            (limits.lookups, limits.documents, limits.html_parsers, limits.pdf_parsers)}


def wait_idle(timeout: float = 10) -> dict[str, int]:
    """Wait until every process-wide slot is free; return the final counts."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        used = slots_in_use()
        if not any(used.values()):
            return used
        time.sleep(0.02)
    return slots_in_use()


def search_payload(*urls: str, **fields: Any) -> dict[str, Any]:
    return {"results": [{"url": url, "title": f"title {index}", "content": "snippet", **fields}
                        for index, url in enumerate(urls)]}
