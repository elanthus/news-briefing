"""Validate a reviewer's label file against a blinded daily-audit packet.

Run ``python3 -S -m agent_runner.audit_labels PACKET LABELS``. It reads both
files, checks that the labels cover every packet item exactly once with one
label per version from the packet's own rubric, and prints the counts. It never
writes, and it never needs the private correspondence key.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

MAX_BYTES = 50_000_000
PER_VERSION = ("support", "citations", "readability")


def _read(path: Path) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{path.name}: missing, oversized, or not a regular file")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_labels(packet_path: Path, labels_path: Path) -> dict[str, Any]:
    """Return counts when ``labels_path`` completely and validly labels ``packet_path``."""
    packet, labels = _read(packet_path), _read(labels_path)
    if not isinstance(packet, dict) or not isinstance(packet.get("items"), list):
        raise ValueError("packet has no items list")
    rubric = packet.get("rubric") if isinstance(packet.get("rubric"), dict) else None
    allowed = rubric.get("labels") if rubric is not None and isinstance(rubric.get("labels"), dict) else None
    if not isinstance(allowed, dict) or set(allowed) != {*PER_VERSION, "meaning_preservation"}:
        raise ValueError("packet rubric does not list the four label sets")
    if not isinstance(labels, dict) or labels.get("schema_version") != 1 or not isinstance(labels.get("items"), list):
        raise ValueError("labels file must be an object with schema_version 1 and an items list")
    if not isinstance(labels.get("reviewer"), str) or not labels["reviewer"]:
        raise ValueError("labels file must name its reviewer")
    expected = {item["packet_id"]: len(item["versions"]) for item in packet["items"]}
    seen: Counter[str] = Counter(entry.get("packet_id") for entry in labels["items"] if isinstance(entry, dict))
    missing = sorted(set(expected) - set(seen))
    extra = sorted(set(seen) - set(expected))
    duplicates = sorted(key for key, count in seen.items() if count > 1)
    if missing or extra or duplicates:
        raise ValueError(f"item coverage: {len(missing)} missing, {len(extra)} unknown, {len(duplicates)} duplicated"
                         + (f"; first missing {missing[0]}" if missing else "")
                         + (f"; first unknown {extra[0]}" if extra else "")
                         + (f"; first duplicated {duplicates[0]}" if duplicates else ""))
    label_counts: Counter[str] = Counter()
    for entry in labels["items"]:
        packet_id = entry["packet_id"]
        versions = expected[packet_id]
        values = entry.get("labels")
        if not isinstance(values, dict) or set(values) != {*PER_VERSION, "meaning_preservation"}:
            raise ValueError(f"{packet_id}: labels must have exactly support, meaning_preservation, "
                             "citations, readability")
        for question in PER_VERSION:
            chosen = values[question]
            if not isinstance(chosen, list) or len(chosen) != versions:
                raise ValueError(f"{packet_id}: {question} needs one label per version ({versions})")
            for label in chosen:
                if label not in allowed[question]:
                    raise ValueError(f"{packet_id}: {question} label {label!r} is not in the rubric")
                label_counts[f"{question}:{label}"] += 1
        meaning = values["meaning_preservation"]
        if meaning not in allowed["meaning_preservation"]:
            raise ValueError(f"{packet_id}: meaning_preservation label {meaning!r} is not in the rubric")
        if (versions == 1) != (meaning == "not_applicable"):
            raise ValueError(f"{packet_id}: meaning_preservation must be not_applicable exactly for one-version items")
        label_counts[f"meaning_preservation:{meaning}"] += 1
        rationale = entry.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError(f"{packet_id}: rationale must be a non-empty string")
    return {"reviewer": labels["reviewer"], "items": len(expected),
            "pairs": sum(1 for count in expected.values() if count == 2),
            "controls": sum(1 for count in expected.values() if count == 1),
            "label_counts": dict(sorted(label_counts.items()))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("labels", type=Path)
    args = parser.parse_args()
    try:
        result = validate_labels(args.packet, args.labels)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
