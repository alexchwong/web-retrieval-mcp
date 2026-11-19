"""Process-wide limits on work that cannot be stopped once started (Section 8.4).

Admission happens in the calling coroutine by polling, so a waiter can be cancelled
or timed out without ever starting the work. Work that runs in a thread (a DNS lookup
or a parser) releases its own slots when it returns, whether or not the call that
started it still exists. Nothing here uses an event loop's default executor.
"""
from __future__ import annotations

import asyncio
import socket
import threading
from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")

LOOKUP_LIMIT = 8
DOCUMENT_LIMIT = 16
HTML_PARSER_LIMIT = 6
PDF_PARSER_LIMIT = 1
POLL_SECONDS = 0.05

# Indirection so tests can install a blocking resolver.
_getaddrinfo = socket.getaddrinfo


class Slots:
    """A counter shared by every thread and event loop in the process."""

    def __init__(self, name: str, limit: int) -> None:
        self.name = name
        self.limit = limit
        self._used = 0
        self._lock = threading.Lock()

    @property
    def in_use(self) -> int:
        with self._lock:
            return self._used

    def try_acquire(self) -> bool:
        with self._lock:
            if self._used >= self.limit:
                return False
            self._used += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._used <= 0:  # pragma: no cover - programming error
                raise RuntimeError(f"{self.name} slot released twice")
            self._used -= 1


class Hold:
    """One taken slot. ``release`` is idempotent and thread-safe."""

    def __init__(self, slots: Slots) -> None:
        self._slots = slots
        self._lock = threading.Lock()
        self._held = True

    @property
    def held(self) -> bool:
        return self._held

    def release(self) -> None:
        with self._lock:
            if not self._held:
                return
            self._held = False
        self._slots.release()


lookups = Slots("lookup", LOOKUP_LIMIT)
documents = Slots("document", DOCUMENT_LIMIT)
html_parsers = Slots("html parser", HTML_PARSER_LIMIT)
pdf_parsers = Slots("pdf parser", PDF_PARSER_LIMIT)


async def admit(slots: Slots, deadline: float | None = None) -> Hold:
    """Take a slot, sleeping ``POLL_SECONDS`` between attempts.

    Raises ``asyncio.TimeoutError`` at ``deadline`` (event-loop time). Cancellation
    of the wait is ordinary; a cancelled or timed-out waiter holds nothing.
    """
    loop = asyncio.get_running_loop()
    while True:
        if slots.try_acquire():
            return Hold(slots)
        delay = POLL_SECONDS
        if deadline is not None:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            delay = min(delay, remaining)
        await asyncio.sleep(delay)


def start_thread(fn: Callable[[], T], holds: Iterable[Hold], name: str) -> "asyncio.Future[T]":
    """Start ``fn`` now in a thread of its own that releases ``holds`` when ``fn`` returns.

    Called synchronously, so there is no await between taking a slot and handing it
    to the thread. The thread is not a daemon, so the interpreter waits for it at
    exit. Cancelling the returned future does not stop the thread; its result is
    then discarded.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()
    owned = list(holds)

    def deliver(ok: bool, value: Any) -> None:
        if future.done():
            return
        if ok:
            future.set_result(value)
        else:
            future.set_exception(value)

    def target() -> None:
        ok, value = False, None
        try:
            value = fn()
            ok = True
        except BaseException as exc:  # delivered to the waiter, if any
            value = exc
        finally:
            for hold in owned:
                hold.release()
        try:
            loop.call_soon_threadsafe(deliver, ok, value)
        except RuntimeError:
            pass  # the call's loop is closed; nobody is waiting

    thread = threading.Thread(target=target, name=name, daemon=False)
    try:
        thread.start()
    except BaseException:
        for hold in owned:
            hold.release()
        raise
    return future


async def run_thread(fn: Callable[[], T], holds: Iterable[Hold], name: str) -> T:
    return await start_thread(fn, holds, name)


async def getaddrinfo(host: str, port: int, *, type: int = 0, proto: int = 0,
                      deadline: float | None = None) -> list[tuple[Any, ...]]:
    """Resolve ``host`` exactly as given, under the process-wide lookup limit.

    ``deadline`` (event-loop time) bounds both the wait for a slot and the wait for
    the answer. A lookup thread left behind ends when the system resolver gives up.
    """
    hold = await admit(lookups, deadline)
    work = start_thread(lambda: _getaddrinfo(host, port, type=type, proto=proto),
                        [hold], "webspell-lookup")
    if deadline is None:
        return await work
    remaining = deadline - asyncio.get_running_loop().time()
    return await asyncio.wait_for(work, max(0.0, remaining))
