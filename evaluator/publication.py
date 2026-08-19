"""Export and verify reviewer-facing evaluation evidence without provider identifiers."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from evaluator.runner import ROOT, apply_adjudications, markdown_report, summarize

PRIVATE_KEYS = {"provider_request_id"}


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _portable_path(value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        return value
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return "<external-path-redacted>"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact(item)
            for key, item in value.items()
            if key not in PRIVATE_KEYS
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _row_key(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in ("provider", "model", "prompt_version", "case_id", "trial")
    }


def _score_ledger(manifest: dict[str, Any]) -> dict[str, Any]:
    identity = {key: value for key, value in manifest.items() if key != "results"}
    rows = []
    for source in manifest["results"]:
        row = copy.deepcopy(source)
        for call_name in ("first", "correction"):
            call = row.get(call_name)
            if isinstance(call, dict):
                call.pop("text", None)
        rows.append(row)
    return {
        "schema_version": 1,
        "description": (
            "Redacted score primitives for every planned row. Raw generated outputs are in "
            "the release evidence bundle's manifest.json."
        ),
        "identity": identity,
        "results": rows,
    }


def _adjudication_rows(manifest: dict[str, Any], artifact_root: Path) -> list[dict[str, Any]]:
    rows = []
    for row in manifest["results"]:
        for kind, field in (
            ("semantic", "semantic_adjudication"),
            ("human_grounding", "grounding_adjudication"),
        ):
            relative = row.get(field)
            if not relative or not (artifact_root / relative).is_file():
                continue
            payload = json.loads((artifact_root / relative).read_text(encoding="utf-8"))
            if kind == "human_grounding" and not any(
                isinstance(topic.get("grounding_error"), bool)
                for topic in payload.get("topics", [])
            ):
                continue
            rows.append({"row": _row_key(row), "kind": kind, "payload": payload})
    return rows


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_public_run(
    manifest_path: Path,
    output_dir: Path,
    *,
    ledger_output: Path | None = None,
    machine_grounding_path: Path | None = None,
) -> dict[str, Any]:
    """Write sufficient public evidence to recalculate and audit a completed final run."""
    source = json.loads(manifest_path.read_text(encoding="utf-8"))
    code = source.get("code", {})
    if source.get("run_kind") != "final" or source.get("run_status") != "complete":
        raise ValueError("public evidence requires a complete final run")
    if code.get("dirty") is not False or not code.get("source_tag"):
        raise ValueError("public evidence requires a clean, tagged source manifest")
    if output_dir.exists():
        raise ValueError(f"public evidence output already exists: {output_dir}")

    manifest = copy.deepcopy(source)
    apply_adjudications(manifest, manifest_path.parent)
    manifest = _redact(manifest)
    manifest["suite"] = _portable_path(manifest["suite"])
    manifest["protocol"] = _portable_path(manifest["protocol"])
    output_dir.mkdir(parents=True)

    public_manifest = output_dir / "manifest.json"
    _write_json(public_manifest, manifest)
    report = summarize(manifest)
    _write_json(output_dir / "report.json", report)
    (output_dir / "report.md").write_text(markdown_report(report), encoding="utf-8")
    _write_json(
        output_dir / "adjudications.json",
        {"schema_version": 1, "rows": _adjudication_rows(source, manifest_path.parent)},
    )
    if machine_grounding_path is not None:
        _write_json(
            output_dir / "machine-grounding-review.json",
            _redact(json.loads(machine_grounding_path.read_text(encoding="utf-8"))),
        )

    ledger = _score_ledger(manifest)
    _write_json(output_dir / "ledger.json", ledger)
    if ledger_output is not None:
        ledger_output.parent.mkdir(parents=True, exist_ok=True)
        _write_json(ledger_output, ledger)

    metadata = {
        "schema_version": 1,
        "source_tag": code["source_tag"],
        "source_commit": code["commit"],
        "source_tree": code["tree"],
        "regeneration_command": (
            f"python3 -m evaluator verify-public-run {output_dir.name}"
        ),
        "redactions": sorted(PRIVATE_KEYS),
        "files": {},
    }
    for path in sorted(output_dir.iterdir()):
        if path.name not in {"metadata.json", "SHA256SUMS"} and path.is_file():
            metadata["files"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
    _write_json(output_dir / "metadata.json", metadata)
    sums = "".join(
        f"{_sha256(path)}  {path.name}\n"
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "SHA256SUMS"
    )
    (output_dir / "SHA256SUMS").write_text(sums, encoding="utf-8")
    return metadata


def verify_public_run(output_dir: Path) -> dict[str, Any]:
    """Verify evidence hashes and reproduce the committed aggregate report."""
    metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
    failures = []
    for name, expected in metadata["files"].items():
        path = output_dir / name
        if not path.is_file() or _sha256(path) != expected["sha256"]:
            failures.append(name)
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    regenerated = summarize(manifest)
    published = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    regenerated["generated_at"] = published.get("generated_at")
    if regenerated != published:
        failures.append("report.json (aggregate mismatch)")
    if failures:
        raise ValueError("public evidence verification failed: " + ", ".join(failures))
    return {
        "status": "verified",
        "source_tag": metadata["source_tag"],
        "rows": len(manifest["results"]),
        "verified_files": len(metadata["files"]),
    }
