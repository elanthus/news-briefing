"""Hardened feed XML parsing: DOCTYPE refusal and strict UTF-32 decoding."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from xml.parsers import expat

from news_fetch.transport import MAX_RESPONSE_BYTES


def _decode_bom_marked_utf32_xml(data: bytes) -> str | bytes:
    """Decode BOM-marked UTF-32 for parsers whose Expat lacks UTF-32 support."""
    byte_order: str | None = None
    if data.startswith(b"\xff\xfe\x00\x00"):
        byte_order = "le"
    elif data.startswith(b"\x00\x00\xfe\xff"):
        byte_order = "be"
    if byte_order is None:
        return data
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response exceeded {MAX_RESPONSE_BYTES} bytes")

    # The generic codec requires and strips the BOM and rejects incomplete or
    # otherwise invalid code units. Passing Unicode to Expat then avoids its
    # platform-dependent "multi-byte encodings are not supported" failure.
    text = data.decode("utf-32", errors="strict")
    declaration = re.match(r"\s*<\?xml\s+([^?]*?)\?>", text, re.IGNORECASE)
    if declaration:
        encoding = re.search(
            r"\bencoding\s*=\s*(['\"])([^'\"]+)\1",
            declaration.group(1),
            re.IGNORECASE,
        )
        if encoding:
            normalized = re.sub(r"[-_]", "", encoding.group(2)).lower()
            accepted = {"utf32", f"utf32{byte_order}"}
            if normalized not in accepted:
                raise ValueError(
                    "UTF-32 byte order mark contradicts XML encoding declaration "
                    f"{encoding.group(2)!r}"
                )
    return text


def parse_feed_xml(data: bytes) -> ET.Element:
    """Parse feed XML, refusing any DOCTYPE declaration.

    ElementTree expands internal entities, so a compact set of nested entity
    declarations can amplify far beyond the wire-size bound. Entity
    declarations and external entity references both require a DOCTYPE, which
    the supported RSS/Atom subset does not need. Refusing it closes both paths
    without adding defusedxml as a runtime dependency.

    Expat recognizes the document's declared encoding before calling the DTD
    handler, so UTF-16 and other supported encodings cannot hide a declaration
    from this guard. BOM-marked UTF-32 is first decoded strictly and bounded,
    because Python's Expat does not support that byte encoding; the same DTD
    guard then runs over Unicode. A separate validation pass keeps
    ElementTree's convenient tree API without relying on private internals.
    """
    def reject_doctype(_name: str, _system_id: str | None,
                       _public_id: str | None, _has_internal_subset: int) -> None:
        raise ValueError("XML DOCTYPE declarations are not accepted")

    class RootReached(Exception):
        """A DOCTYPE cannot follow the root element, so the guard can stop."""

    def stop_at_root(_name: str, _attributes: dict[str, str]) -> None:
        raise RootReached

    parseable = _decode_bom_marked_utf32_xml(data)
    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = reject_doctype
    parser.StartElementHandler = stop_at_root
    try:
        parser.Parse(parseable, True)
    except RootReached:
        pass
    except expat.ExpatError as exc:
        # Keep malformed prologs on the public exception type callers already
        # handle, without swallowing a guard failure and hoping a second parser
        # happens to reject the same bytes.
        raise ET.ParseError(str(exc)) from exc
    return ET.fromstring(parseable)
