"""Date, HTML-text, and publication-window helpers shared by source adapters."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape


def parse_feed_date(text: str | None) -> datetime | None:
    """Parse RFC822 or ISO8601 dates; return aware UTC datetime or None."""
    if not text:
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, IndexError):
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def strip_html(text: str | None) -> str:
    return re.sub(r"<[^>]+>", "", unescape(text or "")).strip()


def publication_in_window(
    published: datetime, cutoff: datetime, window_end: datetime
) -> bool:
    """Whether a publication belongs to the fixed half-open corpus window."""
    return cutoff <= published < window_end
