#!/usr/bin/env python3
"""Write and validate the public marker for private corpus storage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

STORAGE_MARKER_FILENAME = "corpus-storage.json"
STORAGE_MARKER: dict[str, Any] = {
    "schema_version": 1,
    "storage": "encrypted-actions-artifact",
    "public_corpus_text": False,
}


def write_storage_marker(output_dir: Path) -> Path:
    """Record that legacy public corpora have been replaced by private storage."""
    destination = output_dir / STORAGE_MARKER_FILENAME
    destination.write_text(
        json.dumps(STORAGE_MARKER, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def validate_storage_marker(path: Path) -> None:
    """Require the exact marker contract used by archive-gap recovery."""
    payload = json.loads(path.read_bytes())
    if payload != STORAGE_MARKER:
        raise ValueError("unrecognized corpus storage marker")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate",))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        validate_storage_marker(args.path)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
