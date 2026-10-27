"""One story position: a frozen value parsed once at the boundary and passed by value."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal, cast

_BUCKETS = frozenset({"sections", "excluded_topics"})
_PATH = re.compile(r"^(topics|excluded_topics)\.(.+)\[(\d+)\]$")


@dataclass(frozen=True, slots=True, order=True)
class StoryPosition:
    """Where a story sits in a briefing: a bucket, a section name, and an index in that section."""

    bucket: Literal["sections", "excluded_topics"]
    section: str
    index: int

    @classmethod
    def from_payload(cls, value: object) -> StoryPosition:
        """Validate a ``{"bucket", "section", "index"}`` payload with the integrity record's bounds."""
        if not isinstance(value, dict) or set(value) != {"bucket", "section", "index"}:
            raise ValueError("invalid integrity position fields")
        bucket, section, index = value["bucket"], value["section"], value["index"]
        if not isinstance(bucket, str) or bucket not in _BUCKETS:
            raise ValueError("invalid integrity enum")
        if (not isinstance(section, str) or not section.strip() or len(section) > 100
                or any(ord(c) < 32 or ord(c) == 127 for c in section)):
            raise ValueError("invalid integrity string")
        if type(index) is not int or not 0 <= index <= 63:
            raise ValueError("invalid integrity count")
        return cls(cast(Literal["sections", "excluded_topics"], bucket), section, index)

    def to_payload(self) -> dict[str, object]:
        """The JSON payload form."""
        return {"bucket": self.bucket, "section": self.section, "index": self.index}

    def json_key(self) -> str:
        """The sorted-JSON string that persisted records and published anchors were built from."""
        return json.dumps(self.to_payload(), sort_keys=True)

    def path(self) -> str:
        """The renderer's anchor path, such as ``topics.US News[2]``."""
        bucket = "topics" if self.bucket == "sections" else "excluded_topics"
        return f"{bucket}.{self.section}[{self.index}]"

    @classmethod
    def from_path(cls, text: str) -> StoryPosition:
        """Parse a renderer anchor path back into a validated position."""
        match = _PATH.match(text)
        if match is None:
            raise ValueError("invalid story path")
        prefix, section, index = match.groups()
        bucket = "sections" if prefix == "topics" else "excluded_topics"
        return cls.from_payload({"bucket": bucket, "section": section, "index": int(index)})

    def anchor_id(self) -> str:
        """The published HTML anchor; hashes the sorted-JSON payload so existing anchors stay stable."""
        return "story-" + hashlib.sha256(self.json_key().encode()).hexdigest()[:12]
