"""One-time extraction of retained research into a source-bound private dataset.

Original experiment files are content-addressed, never rewritten to pretend an
old experiment used today's policy. Current-schema run records are extracted
separately; historical review files remain evidence, not reusable judge caches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from agent_runner.checkpoint import sha256_bytes
from agent_runner.jev_review import load_verified_run

SCHEMA_VERSION = 1
MAX_FILE_BYTES = 50_000_000
MAX_TOTAL_BYTES = 2_000_000_000
MAX_FILES = 20_000
COLLECTIONS = {
    "jev-september": ".news-briefing/hy3-10x-2026-09-30",
    "published-text-review": ".news-briefing/jev-evaluation-2026-09-30",
    "independent-assessment": ".news-briefing/remediation-evaluation-2026-10-02",
    "citation-relevance": ".news-briefing/citation-relevance-2026-10-01",
}


def _relative(value: Any) -> str:
    if (not isinstance(value, str) or not value or "\\" in value
            or PurePosixPath(value).is_absolute() or any(p in {"", ".", ".."} for p in value.split("/"))):
        raise ValueError("invalid research member path")
    return value


def _read(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("research inputs must be regular files")
    with path.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("research file exceeds bound")
    return raw


def _files(root: Path, relative: str) -> list[Path]:
    directory = root / _relative(relative)
    if any(p.is_symlink() for p in (directory, *directory.parents)) or not directory.is_dir():
        raise ValueError("research collection must be an existing directory without symlinks")
    found = []
    for current, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        if any((Path(current) / d).is_symlink() for d in dirs):
            raise ValueError("research collection contains a symlink")
        for name in sorted(files):
            if name != ".DS_Store" and not name.endswith(".pyc"):
                found.append(Path(current) / name)
    return found


def _record(root: Path, run: str, collection: str, files: dict[str, dict[str, Any]]) -> dict[str, Any]:
    manifest_path = f"{run}/manifest.json"
    record: dict[str, Any] = {"run": run, "collection": collection,
                              "manifest_sha256": files[manifest_path]["sha256"],
                              "state": "unavailable", "reason": "unverifiable_run", "topics": None,
                              "markdown_sha256": None, "corpus_sha256": None}
    try:
        state = load_verified_run(root / run)
        markdown = _read(root / run / "final.md")
        digest = sha256_bytes(markdown)
        if (state.manifest["artifacts"].get("final.md") != digest
                or state.manifest["final"].get("output_sha256") != digest):
            raise ValueError("research Markdown is not bound to generation")
        record.update(state="ready", reason=None, topics=state.topics,
                      markdown_sha256=digest, corpus_sha256=state.hashes["corpus.json"])
    except (ValueError, OSError, KeyError, TypeError, RecursionError, OverflowError):
        # Failed, partial and obsolete runs remain in the sampling frame and
        # their complete original files remain in the dataset. No score is imputed.
        pass
    return record


def _records(root: Path, files: dict[str, dict[str, Any]], collections: dict[str, str]) -> list[dict[str, Any]]:
    records = []
    for name in sorted(files):
        if not name.endswith("/manifest.json"):
            continue
        run = name.removesuffix("/manifest.json")
        for collection, prefix in collections.items():
            if run.startswith(prefix + "/"):
                # Only runner checkpoints have this pair of adjacent inputs.
                if f"{run}/corpus.json" in files and f"{run}/briefing-config.json" in files:
                    records.append(_record(root, run, collection, files))
                break
    return records


def _pairs(records: list[dict[str, Any]], collections: dict[str, str]) -> list[dict[str, str]]:
    """Retain the September study's before/after correspondence, including failures."""
    prefix = collections.get("jev-september")
    if prefix is None:
        return []
    runs = {r["run"] for r in records}
    pairs = []
    for run in sorted(runs):
        if run.startswith(prefix + "/grouping-repairs/"):
            original = run.replace(prefix + "/grouping-repairs/", prefix + "/", 1)
            if original not in runs:
                raise ValueError("retained repair has no original run")
            pairs.append({"original": original, "repaired": run})
    return pairs


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
                    encoding="utf-8")


def migrate(root: Path, output: Path, *, collections: dict[str, str] | None = None) -> dict[str, Any]:
    """Copy retained evidence, extract current runs, then verify before installation.

    There is deliberately no deletion or in-place rewrite mode. Exclusions from
    scoring are explicit records; originals include all reports and repair outcomes.
    """
    root, output = root.resolve(), output.absolute()
    selected = dict(COLLECTIONS if collections is None else collections)
    if not selected or any(not isinstance(k, str) or not k for k in selected):
        raise ValueError("research collections must have nonempty identities")
    prefixes = [_relative(p) for p in selected.values()]
    if len(set(prefixes)) != len(prefixes) or any(
        a.startswith(b + "/") for a in prefixes for b in prefixes if a != b
    ):
        raise ValueError("research collections overlap")
    if output.exists() or output.is_symlink():
        raise ValueError("research output must not already exist")
    if any(output.resolve().is_relative_to(root / p) for p in prefixes):
        raise ValueError("research output must be outside input collections")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="research-migration-", dir=output.parent) as temporary:
        staging = Path(temporary) / "dataset"
        (staging / "blobs").mkdir(parents=True)
        index: dict[str, dict[str, Any]] = {}
        total = 0
        for prefix in prefixes:
            for path in _files(root, prefix):
                raw = _read(path)
                total += len(raw)
                if total > MAX_TOTAL_BYTES or len(index) >= MAX_FILES:
                    raise ValueError("research collection exceeds bounds")
                digest = sha256_bytes(raw)
                blob = staging / "blobs" / digest
                if not blob.exists():
                    blob.write_bytes(raw)
                index[path.relative_to(root).as_posix()] = {"sha256": digest, "bytes": len(raw)}
        # Derive views from the captured bytes, not files which may have changed
        # during capture. Verification reconstructs the same views independently.
        with tempfile.TemporaryDirectory(prefix="research-captured-") as captured:
            _materialize(staging, index, Path(captured))
            records = _records(Path(captured), index, selected)
        manifest = {"schema_version": SCHEMA_VERSION, "collections": selected, "files": index,
                    "records": records, "pairs": _pairs(records, selected),
                    "assurance": "Captured source bytes; not new generation-time provenance or human validation."}
        _write_json(staging / "manifest.json", manifest)
        receipt = verify(staging)
        os.rename(staging, output)
    return receipt


def _materialize(dataset: Path, files: dict[str, dict[str, Any]], destination: Path) -> None:
    for name, description in files.items():
        member = destination / _relative(name)
        member.parent.mkdir(parents=True, exist_ok=True)
        member.write_bytes(_read(dataset / "blobs" / description["sha256"]))


def verify(dataset: Path) -> dict[str, Any]:
    """Verify every blob and rebuild all extracted run views from captured files."""
    manifest_raw = _read(dataset / "manifest.json")
    manifest = json.loads(manifest_raw)
    if (not isinstance(manifest, dict) or set(manifest) != {
        "schema_version", "collections", "files", "records", "pairs", "assurance",
    } or type(manifest["schema_version"]) is not int or manifest["schema_version"] != SCHEMA_VERSION):
        raise ValueError("invalid research dataset schema")
    collections, files = manifest["collections"], manifest["files"]
    if (not isinstance(collections, dict) or not collections
            or any(not isinstance(k, str) or not k for k in collections)
            or not isinstance(files, dict) or len(files) > MAX_FILES):
        raise ValueError("invalid research inventory")
    prefixes = [_relative(p) for p in collections.values()]
    if len(set(prefixes)) != len(prefixes) or any(
        a.startswith(b + "/") for a in prefixes for b in prefixes if a != b
    ):
        raise ValueError("research collections overlap")
    if (dataset / "blobs").is_symlink() or not (dataset / "blobs").is_dir():
        raise ValueError("invalid research blob directory")
    expected = set()
    total = 0
    for name, description in files.items():
        _relative(name)
        if (not any(name.startswith(p + "/") for p in prefixes) or not isinstance(description, dict)
                or set(description) != {"sha256", "bytes"}):
            raise ValueError("invalid research member")
        digest = description["sha256"]
        if (not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
                or type(description["bytes"]) is not int or not 0 <= description["bytes"] <= MAX_FILE_BYTES):
            raise ValueError("invalid research blob identity")
        expected.add(digest)
        raw = _read(dataset / "blobs" / digest)
        if len(raw) != description["bytes"] or sha256_bytes(raw) != digest:
            raise ValueError("research blob differs from inventory")
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("research collection exceeds bound")
    if (dataset / "blobs").is_symlink() or {p.name for p in (dataset / "blobs").iterdir()} != expected:
        raise ValueError("research blob inventory mismatch")
    with tempfile.TemporaryDirectory(prefix="research-verification-") as temporary:
        _materialize(dataset, files, Path(temporary))
        rebuilt = _records(Path(temporary), files, collections)
    if manifest["records"] != rebuilt or manifest["pairs"] != _pairs(rebuilt, collections):
        raise ValueError("research records differ from their captured source artifacts")
    return {"schema_version": SCHEMA_VERSION, "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "files": len(files), "unique_blobs": len(expected), "bytes": total, "records": len(rebuilt),
            "ready_records": sum(r["state"] == "ready" for r in rebuilt),
            "unavailable_records": sum(r["state"] == "unavailable" for r in rebuilt),
            "repair_pairs": len(manifest["pairs"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    migration = commands.add_parser("migrate")
    migration.add_argument("--root", type=Path, default=Path("."))
    migration.add_argument("--output", type=Path, required=True)
    verification = commands.add_parser("verify")
    verification.add_argument("dataset", type=Path)
    args = parser.parse_args()
    try:
        result = migrate(args.root, args.output) if args.command == "migrate" else verify(args.dataset)
    except (ValueError, OSError, KeyError, TypeError, RecursionError) as exc:
        parser.exit(1, f"Research migration failed: {type(exc).__name__}; source files were not changed.\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
