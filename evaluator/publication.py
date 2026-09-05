"""Export and verify reviewer-facing evaluation evidence without provider identifiers."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evaluator.runner import ROOT, apply_adjudications, markdown_report, summarize

PRIVATE_KEYS = {"provider_request_id"}

SPLIT_RUN_IDENTITY_FIELDS = (
    "schema_version",
    "generation_path",
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    selected_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    selected = source["results"] if selected_rows is None else selected_rows
    return {
        "name": path.parent.name,
        "manifest_sha256": _sha256(path),
        "rows": len(source["results"]),
        "selected_rows": len(selected),
        "excluded_partial_rows": len(source["results"]) - len(selected),
        "selected_adapters": [
            {"provider": provider, "model": model}
            for provider, model in sorted({_adapter_key(row) for row in selected})
        ],
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
    if not suite_path.is_file() or _sha256(suite_path) != source.get("suite_sha256"):
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
        # Before completed_with_errors was publishable, "complete" alone implied
        # every row was scored, so no row-shape check was needed here. It is now:
        # accepting a run that recorded failures means accepting rows whose shape
        # the aggregates depend on, and the split path checks exactly this.
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

    controls: dict[tuple[Any, Any], dict[str, Any]] = {}
    timeouts: dict[tuple[Any, Any], dict[str, Any]] = {}
    rows_by_adapter: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    per_adapter_units: int | None = None
    per_adapter_matched_pairs: int | None = None
    block_candidates: dict[
        tuple[Any, Any],
        list[tuple[dict[str, Any], Path, list[dict[str, Any]]]],
    ] = {}
    partial_adapters: set[tuple[Any, Any]] = set()
    adjudicated_sources: list[tuple[dict[str, Any], Path]] = []
    observed_cost = 0.0
    expected_adapter_rows = _expected_adapter_rows(first)
    for source, path in loaded:
        source_controls = source.get("generation_controls")
        if not isinstance(source_controls, list) or not source_controls:
            raise ValueError("split final-run manifests require generation controls")
        source_control_keys = [_adapter_key(control) for control in source_controls]
        if len(source_control_keys) != len(set(source_control_keys)):
            raise ValueError("split final-run manifest has duplicate generation controls")
        planned = source.get("planned_case_trials")
        if (
            not isinstance(planned, int)
            or isinstance(planned, bool)
            or planned <= 0
            or planned % len(source_controls)
        ):
            raise ValueError("split final-run manifest has an invalid planned row count")
        source_per_adapter = planned // len(source_controls)
        if per_adapter_units is None:
            per_adapter_units = source_per_adapter
        elif source_per_adapter != per_adapter_units:
            raise ValueError("split final-run manifests disagree on rows per adapter")
        planned_pairs = source.get("planned_matched_pair_trials", 0)
        if (
            not isinstance(planned_pairs, int)
            or isinstance(planned_pairs, bool)
            or planned_pairs < 0
            or planned_pairs % len(source_controls)
        ):
            raise ValueError("split final-run manifest has an invalid matched-pair count")
        source_pairs_per_adapter = planned_pairs // len(source_controls)
        if per_adapter_matched_pairs is None:
            per_adapter_matched_pairs = source_pairs_per_adapter
        elif source_pairs_per_adapter != per_adapter_matched_pairs:
            raise ValueError("split final-run manifests disagree on matched pairs per adapter")

        for control in source_controls:
            key = _adapter_key(control)
            previous = controls.setdefault(key, control)
            if previous != control:
                raise ValueError(f"split final-run generation controls differ for {key}")
        source_timeouts = source.get("adapter_timeouts_seconds")
        if not isinstance(source_timeouts, list):
            raise ValueError("split final-run manifest requires adapter timeouts")
        source_timeout_keys = [_adapter_key(timeout) for timeout in source_timeouts]
        if (
            len(source_timeout_keys) != len(set(source_timeout_keys))
            or set(source_timeout_keys) != set(source_control_keys)
        ):
            raise ValueError("split final-run adapter timeouts do not match its controls")
        for timeout in source_timeouts:
            key = _adapter_key(timeout)
            previous = timeouts.setdefault(key, timeout)
            if previous != timeout:
                raise ValueError(f"split final-run adapter timeouts differ for {key}")

        source_rows: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
        for row in source["results"]:
            if not _is_publishable_row(row):
                raise ValueError(
                    "public split evidence requires every recorded row to be completed "
                    "or to carry a recorded provider failure"
                )
            adapter = _adapter_key(row)
            if adapter not in {_adapter_key(control) for control in source_controls}:
                raise ValueError("split final-run row references an unconfigured adapter")
            source_rows.setdefault(adapter, []).append(row)

        for adapter, rows in source_rows.items():
            row_keys = [(
                row.get("prompt_version"),
                row.get("case_id"),
                row.get("trial"),
            ) for row in rows]
            if len(row_keys) != len(set(row_keys)):
                raise ValueError(
                    f"split final-run manifest contains duplicate rows for {adapter}"
                )
            if not set(row_keys) <= expected_adapter_rows:
                raise ValueError(
                    "split final-run checkpoints may contain only exact whole adapter "
                    "matrices or strict prefixes superseded by a whole adapter matrix"
                )
            if set(row_keys) == expected_adapter_rows:
                block_candidates.setdefault(adapter, []).append((source, path, rows))
            else:
                partial_adapters.add(adapter)

        if source_per_adapter != len(expected_adapter_rows):
            raise ValueError("split final-run planned rows differ from the frozen suite matrix")
        if source.get("run_status") in _PUBLISHABLE_RUN_STATUSES:
            if (
                len(source["results"]) != planned
                or set(source_rows) != set(source_control_keys)
                or any(
                    len(rows) != source_per_adapter
                    for rows in source_rows.values()
                )
            ):
                raise ValueError("complete split final-run manifest is missing rows")
        elif source.get("run_status") != "running":
            raise ValueError(
                "split public evidence accepts only complete runs or clean interrupted checkpoints"
            )

        adjudicated = copy.deepcopy(source)
        apply_adjudications(adjudicated, path.parent)
        adjudicated_sources.append((adjudicated, path))
        cost = source.get("observed_ceiling_cost_usd", 0.0)
        if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0:
            raise ValueError("split final-run manifest has an invalid observed cost")
        observed_cost += float(cost)

    assert per_adapter_units is not None
    missing = sorted(set(controls) - set(block_candidates))
    if missing:
        raise ValueError(f"split final-run manifests are missing adapters: {missing}")
    duplicates = sorted(
        adapter for adapter, candidates in block_candidates.items() if len(candidates) != 1
    )
    if duplicates:
        raise ValueError(
            f"split final-run manifests contain duplicate whole adapter matrices: {duplicates}"
        )
    if partial_adapters - set(block_candidates):
        raise ValueError(
            "split final-run checkpoints contain partial adapter rows without a "
            "superseding whole adapter matrix"
        )

    selected_by_path: dict[Path, list[dict[str, Any]]] = {
        path: [] for _source, path in adjudicated_sources
    }
    for adapter, candidates in block_candidates.items():
        _source, path, _rows = candidates[0]
        adjudicated = next(
            source for source, source_path in adjudicated_sources if source_path == path
        )
        selected_by_path[path].extend(
            row for row in adjudicated["results"] if _adapter_key(row) == adapter
        )
    component_rows = []
    for source, path in adjudicated_sources:
        selected = selected_by_path[path]
        if not selected:
            continue
        component = copy.deepcopy(source)
        component["results"] = selected
        component_rows.append((component, path))
        for row in selected:
            rows_by_adapter.setdefault(_adapter_key(row), []).append(row)
    if any(len(rows) != per_adapter_units for rows in rows_by_adapter.values()):
        raise ValueError("split final-run manifests do not contain one full row set per adapter")

    merged = copy.deepcopy(first)
    control_order = list(controls)
    merged["generation_controls"] = [controls[key] for key in control_order]
    merged["adapter_timeouts_seconds"] = [
        timeouts[key] for key in control_order if key in timeouts
    ]
    merged["results"] = [
        row
        for key in control_order
        for row in rows_by_adapter[key]
    ]
    merged["planned_case_trials"] = len(merged["results"])
    merged["planned_matched_pair_trials"] = (
        (per_adapter_matched_pairs or 0) * len(control_order)
    )
    merged["observed_ceiling_cost_usd"] = observed_cost
    merged["run_status"] = (
        "completed_with_errors"
        if any(_error_row_count(source) for source, _path in component_rows)
        else "complete"
    )
    timestamps = [
        source.get("completed_at") or source.get("checkpointed_at")
        for source, _path in loaded
    ]
    merged["completed_at"] = max(value for value in timestamps if isinstance(value, str))
    merged["checkpointed_at"] = merged["completed_at"]
    descriptors = [
        _component_descriptor(path, source, selected_by_path[path])
        for source, path in loaded
    ]
    merged["split_run_components"] = descriptors
    return merged, component_rows, descriptors


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
    if len(components) == 1:
        apply_adjudications(manifest, components[0][1].parent)
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
    sums_path = output_dir / "SHA256SUMS"
    try:
        sums = dict(
            line.split("  ", 1)
            for line in sums_path.read_text(encoding="utf-8").splitlines()
            if line
        )
    except (OSError, UnicodeError, ValueError):
        sums = {}
        failures.append("SHA256SUMS (invalid)")
    expected_sum_names = {
        path.name
        for path in output_dir.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(sums.values()) != expected_sum_names:
        failures.append("SHA256SUMS (file list mismatch)")
    for expected_hash, name in sums.items():
        path = output_dir / name
        if not path.is_file() or _sha256(path) != expected_hash:
            failures.append(f"SHA256SUMS ({name})")
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    regenerated = summarize(manifest)
    published = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    regenerated["generated_at"] = published.get("generated_at")
    if regenerated != published:
        failures.append("report.json (aggregate mismatch)")
    expected_markdown = markdown_report(regenerated)
    try:
        published_markdown = (output_dir / "report.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        published_markdown = ""
    if published_markdown != expected_markdown:
        failures.append("report.md (aggregate mismatch)")
    ledger = json.loads((output_dir / "ledger.json").read_text(encoding="utf-8"))
    if ledger != _score_ledger(manifest):
        failures.append("ledger.json (row primitive mismatch)")
    if failures:
        raise ValueError(
            "public evidence verification failed: " + ", ".join(dict.fromkeys(failures))
        )
    return {
        "status": "verified",
        "source_tag": metadata["source_tag"],
        "rows": len(manifest["results"]),
        "verified_files": len(sums),
    }
