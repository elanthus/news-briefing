"""Restore the explicit pre-apply-only candidate fixture without invoking a producer."""

import gzip
import hashlib
import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/historical-semantic-candidate.json.gz"
DIGEST = "1d39d184181c78d0dce0898c609befe84e5e07096e54d59511deaf3a8ad2b8e3"
FILES = {
    "fallback-log.json", "jev-review/audit.json", "jev-review/report.json",
    "jev-review/post-review/report.json", "jev-review/repair/repair-calls.json",
    *{f"{root}/{name}" for root in ("run", "jev-review/repair/run") for name in (
        "attempt-01-structured.json", "attempt-02-structured.json", "briefing-config.json", "corpus.json",
        "final.md", "frozen-selection.json", "manifest.json", "selected-evidence.json")},
}


def historical_candidate_run(root):
    compressed = FIXTURE.read_bytes()
    if hashlib.sha256(compressed).hexdigest() != DIGEST:
        raise ValueError("historical candidate fixture digest mismatch")
    with gzip.open(FIXTURE, "rb") as stream:
        raw = stream.read(3_000_001)
    if len(raw) > 3_000_000:
        raise ValueError("historical candidate fixture exceeds bound")
    payload = json.loads(raw)
    if payload["schema_version"] != 1 or set(payload["files"]) != FILES:
        raise ValueError("historical candidate fixture paths changed")
    for name in FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload["files"][name], encoding="utf-8")
    return root / "run"
