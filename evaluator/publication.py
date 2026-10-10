"""Export and verify reviewer-facing evaluation evidence without provider identifiers."""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agent_runner.checkpoint import sha256_file

from evaluator.evidence_assets import read_sums
from evaluator.report import markdown_report, summarize
from evaluator.runner import ROOT
from evaluator.scoring import apply_adjudications

PRIVATE_KEYS = {"provider_request_id"}

SPLIT_RUN_IDENTITY_FIELDS = (
    "schema_version",
    "generation_path",
    "judgment_artifact_binding",
    "run_kind",
    "execution_order",
    "execution_seed",
    "cost_ceiling_provider",
    "circuit_breaker_threshold",
    "suite",
    "suite_sha256",
    "corpus_sha256",
    "case_corpus_sha256",
    "config_sha256",
    "protocol",
    "protocol_sha256",
    "prompt_sha256",
    "prompt_order",
    "trials_per_case",
    "matched_pair_case_ids",
    "code",
    "grounding_measure",
    "deterministic_summary",
)


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


def _adapter_key(value: dict[str, Any]) -> tuple[Any, Any]:
    return value.get("provider"), value.get("model")


#: Row statuses that carry a recorded failure instead of a scored outcome. The
#: runner guarantees such a row has an ``error`` dict and ``first``/``final``
#: of None, so it contributes to no aggregate. Public evidence keeps the row so
#: the bundle discloses the failure rather than silently dropping it.
_UNSCORED_ROW_STATUSES = frozenset({"provider_error", "skipped_circuit_open"})

#: Run statuses acceptable for public evidence. A run that recorded a provider
#: failure is still a complete run of the frozen matrix; every row is present
#: and the failures are disclosed in the component descriptor.
_PUBLISHABLE_RUN_STATUSES = frozenset({"complete", "completed_with_errors"})


def _is_publishable_row(row: dict[str, Any]) -> bool:
    status = row.get("status")
    if status == "completed":
        return True
    return (
        status in _UNSCORED_ROW_STATUSES
        and isinstance(row.get("error"), dict)
        and row.get("first") is None
        and row.get("final") is None
    )


def _error_row_count(source: dict[str, Any]) -> int:
    return sum(
        1 for row in source["results"] if row.get("status") in _UNSCORED_ROW_STATUSES
    )


def _component_descriptor(
    path: Path,
    source: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": path.parent.name,
        "manifest_sha256": sha256_file(path),
        "rows": len(source["results"]),
        "planned_case_trials": source.get("planned_case_trials"),
        "original_run_status": source.get("run_status"),
        "error_rows": _error_row_count(source),
        "observed_cost_usd": source.get("observed_ceiling_cost_usd"),
        "cost_ceiling_usd": source.get("cost_ceiling_usd"),
        "cost_ceiling_provider": source.get("cost_ceiling_provider"),
        "circuit_breaker_threshold": source.get("circuit_breaker_threshold"),
    }


def _expected_adapter_rows(source: dict[str, Any]) -> set[tuple[str, str, int]]:
    suite_path = Path(source.get("suite", ""))
    if not suite_path.is_absolute():
        suite_path = ROOT / suite_path
    if not suite_path.is_file() or sha256_file(suite_path) != source.get("suite_sha256"):
        raise ValueError("split final-run suite is missing or differs from its recorded hash")
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    cases = suite.get("cases")
    if not isinstance(cases, list) or any(not isinstance(case, dict) for case in cases):
        raise ValueError("split final-run suite cases must be a list of objects")
    case_ids = [
        result_case_id
        for case in cases
        for result_case_id in (
            [case.get("id"), f"{case.get('id')}__clean"]
            if case.get("matched_pair") else [case.get("id")]
        )
    ]
    if any(not isinstance(case_id, str) or not case_id for case_id in case_ids):
        raise ValueError("split final-run suite contains an invalid case id")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("split final-run suite produces duplicate result case ids")
    prompts = source.get("prompt_order")
    prompt_hashes = source.get("prompt_sha256")
    if (
        not isinstance(prompts, list)
        or any(not isinstance(prompt, str) or not prompt for prompt in prompts)
        or len(prompts) != len(set(prompts))
        or not isinstance(prompt_hashes, dict)
        or set(prompts) != set(prompt_hashes)
    ):
        raise ValueError("split final-run manifest has invalid prompt identity")
    trials = source.get("trials_per_case")
    if not isinstance(trials, int) or isinstance(trials, bool) or trials <= 0:
        raise ValueError("split final-run manifest has an invalid trial count")
    return {
        (prompt, case_id, trial)
        for prompt in prompts
        for case_id in case_ids
        for trial in range(1, trials + 1)
    }


def _merge_public_sources(
    manifest_paths: Sequence[Path],
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], Path]], list[dict[str, Any]]]:
    if not manifest_paths:
        raise ValueError("public evidence requires at least one manifest")
    loaded = [
        (json.loads(path.read_text(encoding="utf-8")), path)
        for path in manifest_paths
    ]
    first, _first_path = loaded[0]
    for source, _path in loaded:
        code = source.get("code", {})
        if source.get("run_kind") != "final":
            raise ValueError("public evidence requires final-run manifests")
        if code.get("dirty") is not False or not code.get("source_tag"):
            raise ValueError("public evidence requires clean, tagged source manifests")
        if not isinstance(source.get("results"), list):
            raise ValueError("public evidence manifest results must be a list")

    if len(loaded) == 1:
        if first.get("run_status") not in _PUBLISHABLE_RUN_STATUSES:
            raise ValueError("public evidence requires a complete final run")
        # A completed_with_errors run is publishable, so every row must be
        # completed, a recorded provider_error, or a skipped_circuit_open row
        # with a recorded error; the aggregates depend on that shape, and the
        # split path checks the same rule.
        for row in first["results"]:
            if not _is_publishable_row(row):
                raise ValueError(
                    "public evidence requires every recorded row to be completed "
                    "or to carry a recorded provider failure"
                )
        return copy.deepcopy(first), loaded, [_component_descriptor(manifest_paths[0], first)]

    mismatches = sorted({
        field
        for source, _path in loaded[1:]
        for field in SPLIT_RUN_IDENTITY_FIELDS
        if source.get(field) != first.get(field)
    })
    if mismatches:
        raise ValueError(
            "split final-run manifests have incompatible identity fields: "
            + ", ".join(mismatches)
        )

    controls: list[dict[str, Any]] = []
    timeouts: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    adapters: set[tuple[Any, Any]] = set()
    observed_cost = 0.0
    expected_rows = _expected_adapter_rows(first)
    matched_pairs = len(first.get("matched_pair_case_ids", [])) * len(first["prompt_order"]) * first["trials_per_case"]
    for source, _path in loaded:
        if source.get("run_status") not in _PUBLISHABLE_RUN_STATUSES:
            raise ValueError("split public evidence requires complete components")
        source_controls = source.get("generation_controls")
        if not isinstance(source_controls, list) or not source_controls:
            raise ValueError("split final-run manifests require generation controls")
        source_keys = [_adapter_key(control) for control in source_controls]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("split final-run manifest has duplicate generation controls")
        if adapters.intersection(source_keys):
            raise ValueError("split final-run components require disjoint adapter sets")
        source_timeouts = source.get("adapter_timeouts_seconds")
        if not isinstance(source_timeouts, list):
            raise ValueError("split final-run manifest requires adapter timeouts")
        timeout_keys = [_adapter_key(timeout) for timeout in source_timeouts]
        if len(timeout_keys) != len(set(timeout_keys)) or set(timeout_keys) != set(source_keys):
            raise ValueError("split final-run adapter timeouts do not match its controls")
        planned = source.get("planned_case_trials")
        if (not isinstance(planned, int) or isinstance(planned, bool)
                or planned != len(expected_rows) * len(source_keys)):
            raise ValueError("split final-run planned rows differ from the frozen suite matrix")
        planned_pairs = source.get("planned_matched_pair_trials", 0)
        if (not isinstance(planned_pairs, int) or isinstance(planned_pairs, bool)
                or planned_pairs != matched_pairs * len(source_keys)):
            raise ValueError("split final-run planned matched pairs differ from the frozen suite matrix")
        source_rows: dict[tuple[Any, Any], set[tuple[Any, Any, Any]]] = {
            key: set() for key in source_keys
        }
        for row in source["results"]:
            if not _is_publishable_row(row):
                raise ValueError(
                    "public split evidence requires every recorded row to be completed "
                    "or to carry a recorded provider failure"
                )
            adapter = _adapter_key(row)
            if adapter not in source_rows:
                raise ValueError("split final-run row references an unconfigured adapter")
            key = (row.get("prompt_version"), row.get("case_id"), row.get("trial"))
            if key in source_rows[adapter]:
                raise ValueError(f"split final-run manifest contains duplicate rows for {adapter}")
            source_rows[adapter].add(key)
        if any(keys != expected_rows for keys in source_rows.values()):
            raise ValueError("split final-run components require exact whole adapter matrices")
        cost = source.get("observed_ceiling_cost_usd", 0.0)
        if not isinstance(cost, (int, float)) or isinstance(cost, bool) or not math.isfinite(cost) or cost < 0:
            raise ValueError("split final-run manifest has an invalid observed cost")
        observed_cost += float(cost)
        adapters.update(source_keys)
        controls.extend(source_controls)
        timeouts.extend(source_timeouts)
        rows.extend(copy.deepcopy(source["results"]))

    merged = copy.deepcopy(first)
    merged["generation_controls"] = controls
    merged["adapter_timeouts_seconds"] = timeouts
    merged["results"] = rows
    merged["planned_case_trials"] = len(rows)
    merged["planned_matched_pair_trials"] = matched_pairs * len(adapters)
    merged["observed_ceiling_cost_usd"] = observed_cost
    merged["run_status"] = "completed_with_errors" if _error_row_count(merged) else "complete"
    timestamps = [source.get("completed_at") for source, _path in loaded]
    if any(not isinstance(value, str) or not value for value in timestamps):
        raise ValueError("complete split final-run components require completion timestamps")
    merged["completed_at"] = max(timestamps)
    merged["checkpointed_at"] = merged["completed_at"]
    descriptors = [_component_descriptor(path, source) for source, path in loaded]
    merged["split_run_components"] = descriptors
    return merged, loaded, descriptors


def export_public_run(
    manifest_path: Path | Sequence[Path],
    output_dir: Path,
    *,
    ledger_output: Path | None = None,
    machine_grounding_path: Path | None = None,
) -> dict[str, Any]:
    """Write sufficient public evidence to recalculate and audit a completed final run."""
    manifest_paths = [manifest_path] if isinstance(manifest_path, Path) else list(manifest_path)
    source, components, component_descriptors = _merge_public_sources(manifest_paths)
    code = source["code"]
    if output_dir.exists():
        raise ValueError(f"public evidence output already exists: {output_dir}")

    manifest = copy.deepcopy(source)
    manifest["results"] = []
    for component, path in components:
        adjudicated = copy.deepcopy(component)
        apply_adjudications(adjudicated, path.parent)
        manifest["results"].extend(adjudicated["results"])
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
        {
            "schema_version": 1,
            "rows": [
                row
                for component, path in components
                for row in _adjudication_rows(component, path.parent)
            ],
        },
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
        "component_manifests": component_descriptors,
        "regeneration_command": (
            f"python3 -m evaluator verify-public-run {_portable_path(str(output_dir))}"
        ),
        "redactions": sorted(PRIVATE_KEYS),
        "files": {},
    }
    for path in sorted(output_dir.iterdir()):
        if path.name not in {"metadata.json", "SHA256SUMS"} and path.is_file():
            metadata["files"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
    _write_json(output_dir / "metadata.json", metadata)
    sums = "".join(
        f"{sha256_file(path)}  {path.name}\n"
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "SHA256SUMS"
    )
    (output_dir / "SHA256SUMS").write_text(sums, encoding="utf-8")
    return metadata


def verify_public_run(output_dir: Path) -> dict[str, Any]:
    """Verify declared bundle integrity and score primitives independently of report layout.

    The release archive digest and in-tree checksum files authenticate historical
    inputs before this check (see evidence_assets.fetch). A self-consistent bundle
    alone cannot prove the authenticity of its own rewritten checksum file.
    """
    try:
        metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
        sums = read_sums(output_dir)
        manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
        ledger = json.loads((output_dir / "ledger.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"public evidence verification failed: {exc}") from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("files"), dict):
        raise ValueError("public evidence verification failed: invalid metadata")
    failures = []
    if (output_dir / "SHA256SUMS").is_symlink():
        failures.append("SHA256SUMS (unsafe path)")
    declared = metadata["files"]
    required = {"manifest.json", "ledger.json", "report.json", "report.md", "adjudications.json"}
    actual_names = {path.name for path in output_dir.iterdir()}
    if not required <= set(declared) or set(sums) != set(declared) | {"metadata.json"}:
        failures.append("metadata.json (file list mismatch)")
    if actual_names != set(sums) | {"SHA256SUMS"}:
        failures.append("SHA256SUMS (file list mismatch)")
    # read_sums validates all names before they are used as filesystem paths.
    for name, digest in sums.items():
        path = output_dir / name
        if path.is_symlink() or not path.is_file() or sha256_file(path) != digest:
            failures.append(f"SHA256SUMS ({name})")
    for name, expected in declared.items():
        # Only checksum-declared plain names may be read.
        if name not in sums or not isinstance(expected, dict):
            failures.append(f"metadata.json ({name})")
            continue
        path = output_dir / name
        if (expected.get("sha256") != sums[name] or not path.is_file()
                or expected.get("bytes") != path.stat().st_size):
            failures.append(f"metadata.json ({name})")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("results"), list):
        raise ValueError("public evidence verification failed: invalid manifest")
    if any(not isinstance(row, dict) for row in manifest["results"]):
        raise ValueError("public evidence verification failed: invalid manifest rows")
    if ("split_run_components" in manifest and
            metadata.get("component_manifests") != manifest["split_run_components"]):
        failures.append("metadata.json (component identity mismatch)")
    code = manifest.get("code")
    if not isinstance(code, dict) or code.get("dirty") is not False:
        failures.append("manifest.json (source identity mismatch)")
    else:
        for field, code_field in (("source_tag", "source_tag"), ("source_commit", "commit"), ("source_tree", "tree")):
            if not metadata.get(field) or metadata[field] != code.get(code_field):
                failures.append(f"metadata.json ({field} mismatch)")
    if metadata.get("schema_version") != 1:
        failures.append("metadata.json (schema mismatch)")
    if manifest.get("run_kind") != "final" or manifest.get("run_status") not in _PUBLISHABLE_RUN_STATUSES:
        failures.append("manifest.json (incomplete final run)")
    if ledger != _score_ledger(manifest):
        failures.append("ledger.json (row primitive mismatch)")
    if failures:
        raise ValueError("public evidence verification failed: " + ", ".join(dict.fromkeys(failures)))
    return {
        "status": "verified",
        "source_tag": metadata["source_tag"],
        "rows": len(manifest["results"]),
        "verified_files": len(sums),
    }
