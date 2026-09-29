"""DNS-pinned HTTP client with a total request deadline and bounded bodies."""

from __future__ import annotations

import http.client
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
from typing import Any, NamedTuple

from news_fetch.destinations import ResolvedAddress, _http_destination, _resolve_public_addresses

# Feed operators see this traffic from every clone. Naming the project and
# linking it gives them something to look up, and someone to reach, before a
# block is their only option.
USER_AGENT = "news-briefing/1.0 (personal daily digest; +https://github.com/elanthus/news-briefing)"
TIMEOUT = 20
REDDIT_TIMEOUT = 10
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
RESPONSE_READ_CHUNK_BYTES = 64 * 1024
MAX_REDIRECTS = 5
TLS_CONTEXT = ssl.create_default_context()


class HttpResult(NamedTuple):
    status: int
    reason: str
    headers: Any
    data: bytes



def _connect_pinned(address: ResolvedAddress, timeout: int) -> socket.socket:
    sock = socket.socket(address.family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(address.sockaddr)
    except Exception:
        sock.close()
        raise
    return sock


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: ResolvedAddress, timeout: int):
        super().__init__(host, port=port, timeout=timeout)
        self._address = address
        self._pinned_timeout = timeout
        self.pinned_socket: socket.socket | None = None

    def connect(self) -> None:
        pinned_socket = _connect_pinned(self._address, self._pinned_timeout)
        self.sock = pinned_socket
        self.pinned_socket = pinned_socket


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: ResolvedAddress,
                 timeout: int, context: ssl.SSLContext):
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._address = address
        self._pinned_timeout = timeout
        self._pinned_context = context
        self.pinned_socket: socket.socket | None = None

    def connect(self) -> None:
        sock = _connect_pinned(self._address, self._pinned_timeout)
        try:
            pinned_socket = self._pinned_context.wrap_socket(
                sock, server_hostname=self.host
            )
            self.sock = pinned_socket
            self.pinned_socket = pinned_socket
        except Exception:
            sock.close()
            raise


def _request_once(url: str, parts: urllib.parse.SplitResult, hostname: str,
                  port: int, address: ResolvedAddress, user_agent: str,
                  timeout: int, extra_headers: dict[str, str] | None = None) -> HttpResult:
    """Make one request to an already validated and DNS-pinned address."""
    if parts.scheme.lower() == "https":
        connection: _PinnedHTTPConnection | _PinnedHTTPSConnection = _PinnedHTTPSConnection(
            hostname, port, address, timeout, TLS_CONTEXT)
    else:
        connection = _PinnedHTTPConnection(hostname, port, address, timeout)
    target = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
    target = urllib.parse.quote(target, safe="/%?&=;:+,$@!~*'()[]")
    headers = {"User-Agent": user_agent}
    if extra_headers:
        headers.update(extra_headers)
    try:
        deadline = time.monotonic() + timeout
        connection.request("GET", target, headers=headers)
        sock = connection.pinned_socket
        if sock is None:
            raise OSError("pinned connection socket is unavailable")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"request exceeded {timeout}s total deadline")
        sock.settimeout(remaining)

        # A socket timeout applies to each recv, so trickled header bytes can
        # keep getresponse() alive indefinitely. Shut down the pinned socket at
        # the absolute deadline and always join the watchdog before continuing.
        headers_complete = threading.Event()
        deadline_expired = threading.Event()

        def enforce_header_deadline() -> None:
            remaining = deadline - time.monotonic()
            if remaining > 0 and headers_complete.wait(remaining):
                return
            if headers_complete.is_set():
                return
            deadline_expired.set()
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        watchdog = threading.Thread(
            target=enforce_header_deadline,
            name="news-header-deadline",
        )
        watchdog.start()
        try:
            try:
                response = connection.getresponse()
            except TimeoutError as exc:
                raise TimeoutError(
                    f"request exceeded {timeout}s total deadline"
                ) from exc
            except (OSError, http.client.HTTPException) as exc:
                if deadline_expired.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"request exceeded {timeout}s total deadline"
                    ) from exc
                raise
        finally:
            headers_complete.set()
            watchdog.join()
        if deadline_expired.is_set() or time.monotonic() >= deadline:
            raise TimeoutError(f"request exceeded {timeout}s total deadline")
        data = (b"" if response.status in {301, 302, 303, 307, 308}
                else _read_response_body(
                    response, sock, deadline, timeout
                ))
        return HttpResult(response.status, str(response.reason), response.headers, data)
    finally:
        connection.close()


def _read_response_body(
    response: http.client.HTTPResponse,
    sock: socket.socket | None,
    deadline: float,
    timeout: int,
) -> bytes:
    """Read one response within the request's total wall-clock deadline."""
    if sock is None:
        raise OSError("pinned connection socket is unavailable")
    payload = bytearray()
    while len(payload) <= MAX_RESPONSE_BYTES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"request exceeded {timeout}s total deadline")
        sock.settimeout(remaining)
        try:
            chunk = response.read1(min(
                RESPONSE_READ_CHUNK_BYTES,
                MAX_RESPONSE_BYTES + 1 - len(payload),
            ))
        except TimeoutError as exc:
            raise TimeoutError(f"request exceeded {timeout}s total deadline") from exc
        if not chunk:
            break
        payload.extend(chunk)
    return bytes(payload)


def http_get(url: str, user_agent: str = USER_AGENT, timeout: int = TIMEOUT) -> bytes:
    """Fetch a public HTTP(S) URL with DNS pinning and safe redirects."""
    current = url
    initial_scheme: str | None = None
    for redirect_count in range(MAX_REDIRECTS + 1):
        parts, hostname, port = _http_destination(current)
        if initial_scheme is None:
            initial_scheme = parts.scheme.lower()
        addresses = _resolve_public_addresses(hostname, port)
        result: HttpResult | None = None
        last_error: OSError | None = None
        for address in addresses:
            try:
                result = _request_once(
                    current, parts, hostname, port, address, user_agent, timeout)
                break
            except OSError as exc:
                last_error = exc
        if result is None:
            if last_error is not None:
                raise last_error
            raise OSError(f"could not connect to {hostname}")
        if result.status in {301, 302, 303, 307, 308}:
            location = result.headers.get("Location")
            if not location:
                raise urllib.error.HTTPError(
                    current, result.status, "redirect has no Location header",
                    result.headers, None)
            if redirect_count == MAX_REDIRECTS:
                raise ValueError(f"redirect limit of {MAX_REDIRECTS} exceeded")
            redirected = urllib.parse.urljoin(current, location)
            if (
                initial_scheme == "https"
                and urllib.parse.urlsplit(redirected).scheme.lower() == "http"
            ):
                raise ValueError("an HTTPS request cannot redirect to HTTP")
            current = redirected
            # The next loop revalidates syntax, credentials, DNS and address
            # scope before making the redirected request.
            continue
        if len(result.data) > MAX_RESPONSE_BYTES:
            raise ValueError(f"response exceeded {MAX_RESPONSE_BYTES} bytes")
        if result.status >= 400:
            raise urllib.error.HTTPError(
                current, result.status, result.reason, result.headers, None)
        return result.data
    raise AssertionError("unreachable redirect loop")


def scrapecreators_get(url: str, api_key: str, timeout: int = REDDIT_TIMEOUT) -> bytes:
    """Fetch one ScrapeCreators URL without ever forwarding its API key.

    This authenticated transport is intentionally locked to one HTTPS origin and
    refuses redirects. A generic redirect-following helper could disclose the
    ``x-api-key`` header to a destination selected by the response.
    """
    key = api_key.strip()
    if not key or "\r" in key or "\n" in key:
        raise ValueError("SCRAPECREATORS_API_KEY must be a non-empty single-line value")
    parts, hostname, port = _http_destination(url)
    if parts.scheme.lower() != "https" or hostname != "api.scrapecreators.com" or port != 443:
        raise ValueError("ScrapeCreators credentials may only be sent to its HTTPS API origin")
    addresses = _resolve_public_addresses(hostname, port)
    result: HttpResult | None = None
    last_error: OSError | None = None
    for address in addresses:
        try:
            result = _request_once(
                url,
                parts,
                hostname,
                port,
                address,
                USER_AGENT,
                timeout,
                {"Accept": "application/json", "x-api-key": key},
            )
            break
        except OSError as exc:
            last_error = exc
    if result is None:
        if last_error is not None:
            raise last_error
        raise OSError(f"could not connect to {hostname}")
    if result.status in {301, 302, 303, 307, 308}:
        raise urllib.error.HTTPError(
            url, result.status, "authenticated endpoint redirected", result.headers, None
        )
    if len(result.data) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response exceeded {MAX_RESPONSE_BYTES} bytes")
    if result.status >= 400:
        raise urllib.error.HTTPError(
            url, result.status, result.reason, result.headers, None
        )
    return result.data

