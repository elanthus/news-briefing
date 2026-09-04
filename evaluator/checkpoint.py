"""Evaluation manifest checkpointing, artifact verification, and resume state."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluator.adapters import Adapter, ProviderRequestError, is_transient_provider_error
from evaluator.plan import EvaluationPlan, _result_key
from evaluator.report import _operation_call_records, markdown_report, summarize

CIRCUIT_BREAKER_THRESHOLD = 3


@dataclass
class RunState:
    manifest: dict[str, Any]
    results: list[dict[str, Any]]
    observed_ceiling_cost_usd: float
    completed_report: dict[str, Any] | None = None


def _write_text_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write_json_atomic(path: Path, payload: Any) -> None:
    _write_text_atomic(
        path,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )


def _provider_error(stage: str, exc: Exception) -> dict[str, Any]:
    error: dict[str, Any] = {
        "stage": stage,
        "type": type(exc).__name__,
        "message": str(exc),
        "transient": is_transient_provider_error(exc),
    }
    if isinstance(exc, ProviderRequestError):
        error.update({
            "attempts": exc.attempts,
            "status_code": exc.status_code,
            "retry_after": exc.retry_after,
            "cost_usd": exc.cost_usd,
            "input_tokens": exc.input_tokens,
            "output_tokens": exc.output_tokens,
            "provider_request_id": exc.provider_request_id,
        })
    return error


def _checkpoint(manifest: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Atomically persist every completed or failed trial and its current report."""
    manifest["checkpointed_at"] = datetime.now(UTC).isoformat()
    _write_json_atomic(output_dir / "manifest.json", manifest)
    report = summarize(manifest, output_dir)
    _write_json_atomic(output_dir / "report.json", report)
    _write_text_atomic(output_dir / "report.md", markdown_report(report))
    return report


def _has_execution_errors(rows: list[dict[str, Any]]) -> bool:
    return any(
        row.get("status") in {"provider_error", "skipped_circuit_open"}
        or bool(row.get("correction_error"))
        for row in rows
    )
_RUNNER_ARTIFACT_FILES = (
    "corpus.json",
    "request.txt",
    "model-corpus.json",
    "citation-map.json",
    "output-schema.json",
    "selection-schema.json",
    "error.json",
    "correction-error.json",
    "first.md",
    "final.md",
    "first-structured.json",
    "final-structured.json",
    "first-selection.json",
    "first-prose.json",
    "first-selected-evidence.json",
    "first-prose-request.txt",
    "first-prose-schema.json",
    "first-deterministic-repairs.json",
    "correction-selection.json",
    "correction-prose.json",
    "correction-selected-evidence.json",
    "correction-prose-request.txt",
    "correction-prose-schema.json",
    "correction-deterministic-repairs.json",
    "final-selection.json",
    "final-prose.json",
    "final-selected-evidence.json",
    "final-prose-request.txt",
    "final-prose-schema.json",
    "final-deterministic-repairs.json",
    "grounding-adjudication.json",
    "semantic-adjudication.json",
)


def _prepare_artifact_dir(case_dir: Path, *, resume: bool) -> None:
    case_dir.mkdir(exist_ok=resume)
    if not resume:
        return
    for name in _RUNNER_ARTIFACT_FILES:
        path = case_dir / name
        if path.exists() and not path.is_file() and not path.is_symlink():
            raise ValueError(
                f"cannot resume corrupt checkpoint: runner artifact path is not a file: {path}"
            )
        path.unlink(missing_ok=True)


def _load_resume_manifest(output_dir: Path) -> dict[str, Any]:
    if not output_dir.is_dir():
        raise ValueError(f"resume output directory does not exist: {output_dir}")
    manifest_path = output_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot resume corrupt checkpoint {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"cannot resume corrupt checkpoint {manifest_path}: manifest must be an object")
    if manifest.get("run_status") != "running" or manifest.get("completed_at") is not None:
        raise ValueError(
            "resume requires an interrupted manifest with run_status='running' and no completed_at"
        )
    return manifest


def _validate_checkpoint_artifacts(
    row: dict[str, Any],
    output_dir: Path,
    expected_artifact_dir: str,
    generation_path: str,
) -> None:
    if row.get("artifact_dir") != expected_artifact_dir:
        raise ValueError("cannot resume corrupt checkpoint: result artifact_dir is inconsistent")
    case_dir = output_dir / expected_artifact_dir
    required = [case_dir / "corpus.json", case_dir / "request.txt"]
    if generation_path == "production-parity":
        required.extend([
            case_dir / "model-corpus.json",
            case_dir / "citation-map.json",
            case_dir / "output-schema.json",
            case_dir / "selection-schema.json",
        ])
    status = row.get("status")
    if status in {"provider_error", "skipped_circuit_open"}:
        required.append(case_dir / "error.json")
        completed_calls = (
            row.get("error", {}).get("completed_stage_calls")
            if isinstance(row.get("error"), dict)
            else None
        )
        if generation_path == "production-parity" and completed_calls:
            required.extend([
                case_dir / "first-selection.json",
                case_dir / "first-prose.json",
                case_dir / "first-selected-evidence.json",
                case_dir / "first-prose-request.txt",
                case_dir / "first-prose-schema.json",
            ])
    elif status in {"completed", "completed_with_correction_error"}:
        if row.get("grounding_adjudication") != (
            f"{expected_artifact_dir}/grounding-adjudication.json"
        ):
            raise ValueError(
                "cannot resume corrupt checkpoint: grounding adjudication path is inconsistent"
            )
        required.extend([
            case_dir / "first.md",
            case_dir / "final.md",
            case_dir / "grounding-adjudication.json",
        ])
        if generation_path == "production-parity":
            required.extend([
                case_dir / "first-structured.json",
                case_dir / "final-structured.json",
            ])
        if status == "completed_with_correction_error":
            required.append(case_dir / "correction-error.json")
            correction_calls = row.get("correction_error", {}).get(
                "completed_stage_calls"
            )
            if generation_path == "production-parity" and correction_calls:
                required.extend([
                    case_dir / "correction-selection.json",
                    case_dir / "correction-prose.json",
                    case_dir / "correction-selected-evidence.json",
                    case_dir / "correction-prose-request.txt",
                    case_dir / "correction-prose-schema.json",
                ])
        semantic_path = row.get("semantic_adjudication")
        if semantic_path is not None:
            if semantic_path != f"{expected_artifact_dir}/semantic-adjudication.json":
                raise ValueError(
                    "cannot resume corrupt checkpoint: semantic adjudication path is inconsistent"
                )
            required.append(output_dir / semantic_path)
    else:
        raise ValueError(f"cannot resume corrupt checkpoint: invalid result status {status!r}")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError(
            "cannot resume corrupt checkpoint: missing artifact files: " + ", ".join(missing)
        )


def _reconstruct_failure_state(
    rows: list[dict[str, Any]], provider: str, model: str
) -> tuple[int, dict[str, Any] | None]:
    consecutive_failures = 0
    circuit_reason: dict[str, Any] | None = None
    for row in rows:
        if (row.get("provider"), row.get("model")) != (provider, model):
            continue
        status = row.get("status")
        if circuit_reason is not None:
            if status != "skipped_circuit_open":
                raise ValueError(
                    "cannot resume corrupt checkpoint: a provider/model has results after its circuit opened"
                )
            continue
        if status == "skipped_circuit_open":
            raise ValueError(
                "cannot resume corrupt checkpoint: circuit-open skip appears before the circuit opened"
            )
        failure = row.get("error") if status == "provider_error" else row.get("correction_error")
        if isinstance(failure, dict):
            consecutive_failures += 1
            if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                circuit_reason = failure
        else:
            consecutive_failures = 0
    return consecutive_failures, circuit_reason


def _reconstruct_observed_cost(
    rows: list[dict[str, Any]], cost_ceiling_provider: str | None
) -> float:
    observed = 0.0
    for row in rows:
        if cost_ceiling_provider is not None and row.get("provider") != cost_ceiling_provider:
            continue
        for call in _operation_call_records(row):
            cost = call.get("cost_usd")
            if cost is None:
                continue
            if (
                not isinstance(cost, (int, float))
                or isinstance(cost, bool)
                or not math.isfinite(cost)
                or cost < 0
            ):
                raise ValueError("cannot resume corrupt checkpoint: invalid observed call cost")
            observed += float(cost)
    return observed


def _validate_resume_identity(
    manifest: dict[str, Any],
    identity: dict[str, Any],
    run_kind: str,
) -> None:
    incompatible = [
        field
        for field, expected in identity.items()
        if field != "code" and manifest.get(field) != expected
    ]
    code_identity_fields = {"runtime_source_sha256"}
    if run_kind == "final":
        code_identity_fields.update({"commit", "tree", "dirty", "source_tag"})
    code = identity["code"]
    expected_code = {key: code[key] for key in code_identity_fields if key in code}
    recorded_code = manifest.get("code")
    actual_code = (
        {key: recorded_code[key] for key in code_identity_fields if key in recorded_code}
        if isinstance(recorded_code, dict)
        else recorded_code
    )
    if actual_code != expected_code:
        incompatible.append("code")
    if incompatible:
        raise ValueError(
            "cannot resume incompatible run; immutable fields differ: "
            + ", ".join(incompatible)
        )


def _validate_resume_row(
    row: dict[str, Any],
    index: int,
    plan: EvaluationPlan,
    output_dir: Path,
    generation_path: str,
) -> None:
    from evaluator.scoring import _base_result

    if _result_key(row) != plan.planned_keys[index]:
        raise ValueError(
            "cannot resume corrupt checkpoint: recorded results are not an exact execution-plan prefix"
        )
    adapter, prompt_version, _path, case, variant = plan.planned_units[index]
    expected_base = _base_result(
        adapter,
        prompt_version,
        plan.prompt_sha256[prompt_version],
        case,
        variant,
        plan.planned_artifact_dirs[index],
        plan.case_corpus_sha256[case["id"]],
    )
    inconsistent = [
        field for field, expected in expected_base.items() if row.get(field) != expected
    ]
    if inconsistent:
        raise ValueError(
            "cannot resume corrupt checkpoint: result metadata differs: "
            + ", ".join(inconsistent)
        )
    status = row.get("status")
    if status in {"provider_error", "skipped_circuit_open"}:
        if (
            not isinstance(row.get("error"), dict)
            or row.get("first") is not None
            or row.get("final") is not None
        ):
            raise ValueError("cannot resume corrupt checkpoint: invalid failed result")
    elif status == "completed":
        if (
            not isinstance(row.get("first"), dict)
            or not isinstance(row.get("final"), dict)
            or row.get("error") is not None
            or row.get("correction_error") is not None
        ):
            raise ValueError("cannot resume corrupt checkpoint: invalid completed result")
    elif status == "completed_with_correction_error":
        if (
            not isinstance(row.get("first"), dict)
            or not isinstance(row.get("final"), dict)
            or not isinstance(row.get("correction_error"), dict)
        ):
            raise ValueError(
                "cannot resume corrupt checkpoint: invalid correction-error result"
            )
    if status in {"completed", "completed_with_correction_error"}:
        expected_semantic = (
            f"{plan.planned_artifact_dirs[index]}/semantic-adjudication.json"
            if case.get("must_convey")
            else None
        )
        if row.get("semantic_adjudication") != expected_semantic:
            raise ValueError(
                "cannot resume corrupt checkpoint: semantic adjudication presence is inconsistent"
            )
    _validate_checkpoint_artifacts(
        row, output_dir, plan.planned_artifact_dirs[index], generation_path
    )


def _resume_state(
    manifest: dict[str, Any],
    plan: EvaluationPlan,
    adapters: list[Adapter],
    output_dir: Path,
    run_kind: str,
    generation_path: str,
    cost_ceiling_usd: float | None,
    cost_ceiling_provider: str | None,
    checkpoint: Callable[[dict[str, Any], Path], dict[str, Any]],
) -> RunState:
    _validate_resume_identity(manifest, plan.identity, run_kind)
    raw_results = manifest.get("results")
    if not isinstance(raw_results, list) or any(
        not isinstance(row, dict) for row in raw_results
    ):
        raise ValueError("cannot resume corrupt checkpoint: results must be a list of objects")
    results: list[dict[str, Any]] = raw_results
    if len(results) > len(plan.planned_units):
        raise ValueError("cannot resume corrupt checkpoint: more results than planned")
    for index, row in enumerate(results):
        _validate_resume_row(row, index, plan, output_dir, generation_path)
    observed = (
        _reconstruct_observed_cost(results, cost_ceiling_provider)
        if cost_ceiling_usd is not None
        else 0.0
    )
    saved_observed = manifest.get("observed_ceiling_cost_usd")
    if (
        not isinstance(saved_observed, (int, float))
        or isinstance(saved_observed, bool)
        or not math.isclose(float(saved_observed), observed, rel_tol=1e-9, abs_tol=1e-12)
    ):
        raise ValueError(
            "cannot resume corrupt checkpoint: observed ceiling cost is inconsistent"
        )
    for adapter in adapters:
        _reconstruct_failure_state(results, adapter.provider, adapter.model)
    history = manifest.setdefault("resume_history", [])
    if not isinstance(history, list):
        raise ValueError("cannot resume corrupt checkpoint: resume_history must be a list")
    history.append(datetime.now(UTC).isoformat())
    manifest["observed_ceiling_cost_usd"] = observed
    completed_report = None
    if len(results) == len(plan.planned_units):
        manifest["run_status"] = (
            "completed_with_errors" if _has_execution_errors(results) else "complete"
        )
        manifest["completed_at"] = datetime.now(UTC).isoformat()
        completed_report = checkpoint(manifest, output_dir)
    return RunState(manifest, results, observed, completed_report)


def initialize_run(
    *,
    plan: EvaluationPlan,
    adapters: list[Adapter],
    output_dir: Path,
    resume_manifest: dict[str, Any] | None,
    suite_path: Path,
    protocol_path: Path,
    run_kind: str,
    generation_path: str,
    cost_ceiling_usd: float | None,
    cost_ceiling_provider: str | None,
    checkpoint: Callable[[dict[str, Any], Path], dict[str, Any]],
    deterministic_suite: Callable[[], dict[str, Any]],
) -> RunState:
    """Create a new run manifest or validate and resume an interrupted one."""
    if resume_manifest is not None:
        state = _resume_state(
            resume_manifest,
            plan,
            adapters,
            output_dir,
            run_kind,
            generation_path,
            cost_ceiling_usd,
            cost_ceiling_provider,
            checkpoint,
        )
        if state.completed_report is None:
            checkpoint(state.manifest, output_dir)
        return state
    output_dir.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    deterministic = deterministic_suite()
    manifest = {
        **plan.identity,
        "run_status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "completed_at": None,
        "observed_ceiling_cost_usd": 0.0,
        "suite": str(suite_path),
        "protocol": str(protocol_path),
        "grounding_measure": (
            "Deterministic proxy: topic has no citation, an ungrounded citation, "
            "or a figure/quotation/length heuristic. "
            "Preserved outputs should be human-adjudicated for semantic publication claims."
        ),
        "deterministic_summary": {
            "case_count": deterministic["case_count"],
            "label_provenance": deterministic["label_provenance"],
            "components": deterministic["components"],
            "heuristic_claim_false_positive_rate": deterministic[
                "heuristic_claim_false_positive_rate"
            ],
            "heuristic_claim_false_positive_rates": deterministic[
                "heuristic_claim_false_positive_rates"
            ],
        },
        "results": results,
    }
    checkpoint(manifest, output_dir)
    return RunState(manifest, results, 0.0)
