"""Bounded provider HTTP exchange with a deadline covering headers and bodies."""

from __future__ import annotations

import http.client
import io
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024
_context = threading.local()


class DeadlineError(TimeoutError):
    def __init__(self, *, status: int | None, request_id: str | None):
        super().__init__("provider exceeded the elapsed call deadline")
        self.status = status
        self.request_id = request_id


def _safe_request_id(value: Any) -> str | None:
    if (isinstance(value, str) and value and len(value) <= 256
            and all(ord(char) >= 32 and ord(char) != 127 for char in value)):
        return value
    return None


class ResponseLimitError(ValueError):
    def __init__(self, *, status: int | None, request_id: str | None):
        super().__init__("provider response exceeds the bounded output budget")
        self.status = status
        self.request_id = _safe_request_id(request_id)


class _Exchange:
    def __init__(self, deadline: float):
        self.status: int | None = None
        self.request_id: str | None = None
        self.deadline = deadline
        self.lock = threading.Lock()
        self.sockets: list[socket.socket] = []

    def check(self) -> None:
        if time.perf_counter() >= self.deadline:
            raise DeadlineError(status=self.status, request_id=self.request_id)

    def cancel(self) -> None:
        # Shutdown interrupts a blocked read without waiting on BufferedReader's lock.
        with self.lock:
            for sock in self.sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()

    def release(self) -> None:
        with self.lock:
            for sock in self.sockets:
                sock.close()

    def connection(self, kind: type[http.client.HTTPConnection], *args: Any,
                   **kwargs: Any) -> http.client.HTTPConnection:
        exchange = self

        class Connection(kind):  # type: ignore[valid-type, misc]
            def send(self, data: Any) -> None:
                exchange.check()
                super().send(data)

            def connect(self) -> None:
                exchange.check()
                super().connect()
                with exchange.lock:
                    if self.sock is not None:
                        exchange.sockets.append(socket.socket(fileno=os.dup(self.sock.fileno())))
                # A resolver/connect may outlive the caller. Never send a paid
                # request if it eventually completes after cancellation.
                try:
                    exchange.check()
                except TimeoutError:
                    self.close()
                    raise

        connection = Connection(*args, **kwargs)
        return connection


def deadline_handlers() -> list[urllib.request.BaseHandler]:
    """Install tracked connections only within a bounded exchange worker."""
    exchange: _Exchange | None = getattr(_context, "exchange", None)
    if exchange is None:
        return []

    active_exchange = exchange

    class HttpHandler(urllib.request.HTTPHandler):
        def http_open(self, req: urllib.request.Request) -> Any:
            return self.do_open(lambda *a, **kw: active_exchange.connection(http.client.HTTPConnection, *a, **kw), req)

    class HttpsHandler(urllib.request.HTTPSHandler):
        def https_open(self, req: urllib.request.Request) -> Any:
            return self.do_open(lambda *a, **kw: active_exchange.connection(http.client.HTTPSConnection, *a, **kw), req)

    return [HttpHandler(), HttpsHandler()]


def bounded_request(request: urllib.request.Request, *, deadline: float,
                    opener: Callable[..., Any], max_bytes: int = MAX_RESPONSE_BYTES,
                    error_max_bytes: int = MAX_ERROR_BYTES) -> tuple[bytes, str | None]:
    """Read at most limit+1 bytes and stop waiting at the absolute deadline.

    The daemon worker accommodates stdlib DNS calls that cannot be interrupted.
    Tracked sockets are shut down on expiry, and connections check the deadline
    before transmission so a late DNS resolution cannot send a new request.
    HTTP error bodies are bounded here, then exposed as in-memory HTTPError data.
    """
    exchange = _Exchange(deadline)
    done = threading.Event()
    result: list[tuple[bytes, str | None] | Exception] = []

    def worker() -> None:
        _context.exchange = exchange
        try:
            exchange.check()
            try:
                response = opener(request, timeout=max(0.001, deadline - time.perf_counter()))
            except urllib.error.HTTPError as exc:
                try:
                    request_id = exc.headers.get("x-request-id")
                    exchange.request_id = _safe_request_id(request_id)
                    exchange.status = exc.code
                    data = exc.read(error_max_bytes + 1)
                    exchange.check()
                    if len(data) > error_max_bytes:
                        raise ResponseLimitError(status=exc.code, request_id=request_id)
                finally:
                    exc.close()
                # The original socket is closed; subsequent error handling is memory-only.
                raise urllib.error.HTTPError(exc.url, exc.code, exc.msg, exc.headers, io.BytesIO(data)) from exc
            with response as opened:
                request_id = _safe_request_id(getattr(opened, "headers", {}).get("x-request-id"))
                exchange.request_id = request_id
                data = opened.read(max_bytes + 1)
                exchange.check()
                if len(data) > max_bytes:
                    raise ResponseLimitError(status=None, request_id=request_id)
                result.append((data, request_id))
        except Exception as exc:
            result.append(exc)
        finally:
            exchange.release()
            del _context.exchange
            done.set()

    exchange.check()
    thread = threading.Thread(target=worker, name="provider-http-deadline", daemon=True)
    thread.start()
    if not done.wait(max(0.0, deadline - time.perf_counter())):
        exchange.cancel()
        raise DeadlineError(status=exchange.status, request_id=exchange.request_id)
    exchange.check()
    outcome = result[0]
    if isinstance(outcome, Exception):
        raise outcome
    return outcome
