"""Serialize a finished corpus as JSON or as a human-readable Markdown digest."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def render_markdown(corpus: dict[str, Any], window_hours: int, window_end: datetime) -> str:
    """A Markdown digest of every category followed by any fetch errors."""
    lines = [f"# News corpus — last {window_hours}h "
             f"(generated {window_end:%Y-%m-%d %H:%M} UTC)\n"]
    for category, items in corpus["categories"].items():
        lines.append(f"\n## {category} ({len(items)} items)\n")
        for item in items:
            lines.append(f"- **{item['title']}** ({item['source']}, {item['published'][:16]})\n"
                         f"  {item['url']}")
    if corpus["errors"]:
        lines.append("\n## Fetch errors\n")
        lines.extend(
            f"- {e['source_type']}:{e['source_id']}: "
            f"{e['error_type']}: {e['message']}"
            for e in corpus["errors"])
    return "\n".join(lines)


def render_corpus(corpus: dict[str, Any], markdown: bool, window_hours: int,
                  window_end: datetime) -> str:
    """The CLI's output text: the Markdown digest or indented corpus JSON."""
    if markdown:
        return render_markdown(corpus, window_hours, window_end)
    return json.dumps(corpus, indent=1)
