"""Public-destination validation: URL syntax, address scope, and DNS answers."""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from typing import Any, NamedTuple

import corpus_schema

MAX_URL_BYTES = corpus_schema.ITEM_URL_MAX_BYTES


class ResolvedAddress(NamedTuple):
    """One DNS result captured before a connection is opened."""

    family: int
    sockaddr: tuple[Any, ...]



# Several IPv6 forms embed an IPv4 address in an IPv6 wrapper: IPv4-mapped
# (::ffff:0:0/96), IPv4-compatible (::/96, RFC 4291), IPv4-translated
# (::ffff:0:0:0/96, RFC 2765), 6to4 (2002::/16, RFC 3056) and the NAT64
# well-known prefix (64:ff9b::/96, RFC 6052 §2.1/§2.3). ``is_global`` judges the
# wrapper, not the embedded target, so 64:ff9b::7f00:1 or ::127.0.0.1 would
# otherwise pass as public while reaching a private IPv4. Unwrap the embedded
# IPv4 and judge that instead. Every form below places the IPv4 in the low 32
# bits, so a single extraction is correct. (Teredo, 2001::/32, encodes a
# relay/client pair rather than a single destination IPv4, so it is left to
# ``is_global``.)
_IPV4_IN_LOW32 = (
    ipaddress.ip_network("64:ff9b::/96"),    # NAT64 well-known (RFC 6052 §2.1/§2.3)
    ipaddress.ip_network("::/96"),           # IPv4-compatible (RFC 4291)
    ipaddress.ip_network("::ffff:0:0:0/96"),  # IPv4-translated (RFC 2765)
)
# The NAT64 local-use prefix is a /48 whose embedded-IPv4 layout is
# deployment-defined; RFC 8215 says applications MUST NOT assume it (RFC 6052
# even splits the IPv4 across bits 48-63 and 72-87 for a /48, so the low 32
# bits are suffix, not address). It cannot be decoded safely, so reject the
# whole range fail-closed instead of guessing.
_NAT64_LOCAL_USE = ipaddress.ip_network("64:ff9b:1::/48")


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 wrapper ultimately targets, if any."""
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if any(address in network for network in _IPV4_IN_LOW32):
        return ipaddress.IPv4Address(address.packed[-4:])
    return None


def _public_ip(value: str) -> bool:
    """Whether an address is globally routable, judging any embedded IPv4."""
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address):
        if address in _NAT64_LOCAL_USE:
            return False
        embedded = _embedded_ipv4(address)
        if embedded is not None:
            return _public_ip(str(embedded))
    return address.is_global


def http_destination(url: str) -> tuple[urllib.parse.SplitResult, str, int]:
    """Validate URL syntax before DNS resolution or a network request.

    Performs no network I/O, so configured sources are checked with it at load time.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("destination must be a non-empty URL")
    if len(url.encode("utf-8")) > MAX_URL_BYTES:
        raise ValueError(f"destination URL exceeded {MAX_URL_BYTES} bytes")
    if any(ord(character) < 0x20 or character.isspace() for character in url):
        raise ValueError("destination URL contains whitespace or control characters")
    try:
        parts = urllib.parse.urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"invalid destination URL: {exc}") from exc
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("destination must use HTTP or HTTPS")
    if not parts.netloc or not hostname:
        raise ValueError("destination must have a hostname")
    if parts.username is not None or parts.password is not None:
        raise ValueError("destination URL must not contain credentials")
    if "%" in hostname:
        raise ValueError("destination hostname must not contain an address scope")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("destination hostname is not valid IDNA") from exc
    try:
        literal = ipaddress.ip_address(ascii_hostname)
    except ValueError:
        pass
    else:
        if not _public_ip(str(literal)):
            raise ValueError("destination resolves to a non-public address")
    return parts, ascii_hostname, port or (443 if parts.scheme.lower() == "https" else 80)


def _resolve_public_addresses(hostname: str, port: int) -> tuple[ResolvedAddress, ...]:
    """Resolve once, reject any private answer, and return pinned addresses."""
    answers = socket.getaddrinfo(
        hostname, port, family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
    )
    resolved: list[ResolvedAddress] = []
    seen: set[tuple[int, tuple[Any, ...]]] = set()
    for family, _socktype, _proto, _canonname, sockaddr in answers:
        address = str(sockaddr[0])
        if not _public_ip(address):
            raise ValueError(
                f"destination {hostname!r} resolved to non-public address {address}")
        candidate = ResolvedAddress(family, tuple(sockaddr))
        key = (candidate.family, candidate.sockaddr)
        if key not in seen:
            seen.add(key)
            resolved.append(candidate)
    if not resolved:
        raise ValueError(f"destination {hostname!r} resolved to no usable addresses")
    return tuple(resolved)

