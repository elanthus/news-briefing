"""Bounded execution loop behind evaluator.runner.run_evaluation."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import briefing_config
import corpus_schema
from agent_runner.output import ModelCorpus, build_selection_schema, project_corpus
from agent_runner.runner import build_request as structured_model_request

from evaluator.adapters import Adapter, Generation, ProviderRequestError
from evaluator.checkpoint import (
    CIRCUIT_BREAKER_THRESHOLD,
    RunState,
    _has_execution_errors,
    _load_resume_manifest,
    _prepare_artifact_dir,
    _provider_error,
    _reconstruct_failure_state,
    _write_json_atomic,
    _write_text_atomic,
    initialize_run,
)
from evaluator.parity import (
    GenerationAttempt,
    _ProductionParityAttempt,
    _ProductionParityProviderError,
    _reported_generation_cost,
    _reported_stage_cost,
    _stage_call_records,
    _write_production_attempt_artifacts,
    run_correction_attempt,
    run_first_attempt,
)
from evaluator.plan import (
    EvaluationPlan,
    TrialVariant,
    _json,
    _mutate,
    _relocate,
    _result_key,
    _safe_artifact_key,
    _set_source_failures,
    model_request,
    resolve_evaluation_plan,
)
from evaluator.report import finalize_run_report
from evaluator.scoring import (
    ScoredAttempt,
    _adjudication_template,
    _base_result,
    _semantic_adjudication_template,
    build_completed_result,
    score_attempt,
)

EVALUATOR_DIR = Path(__file__).resolve().parent
DEFAULT_SUITE = EVALUATOR_DIR / "fixtures" / "generation-cases.json"
DEFAULT_CORPUS = EVALUATOR_DIR / "fixtures" / "generation-corpus.json"
DEFAULT_PROTOCOL = EVALUATOR_DIR / "protocols" / "portfolio-v1.json"
ProgressCallback = Callable[[str, str, int, int, str], None]
Checkpoint = Callable[[dict[str, Any], Path], dict[str, Any]]


@dataclass(frozen=True)
class ExecutionOptions:
    output_dir: Path
    suite_path: Path
    corpus_path: Path
    generation_path: str
    cost_ceiling_usd: float | None
    cost_ceiling_provider: str | None
    resumed: bool
    checkpoint: Checkpoint


@dataclass
class AdapterState:
    completed: int
    consecutive_failures: int
    circuit_reason: dict[str, Any] | None


@dataclass(frozen=True)
class TrialContext:
    adapter: Adapter
    prompt_version: str
    case: dict[str, Any]
    variant: TrialVariant
    prompt: str
    corpus: dict[str, Any]
    config_data: dict[str, Any]
    config: briefing_config.BriefingConfig
    projected: ModelCorpus | None
    selection_schema: dict[str, Any] | None
    request: str
    safe_key: str
    case_dir: Path
    base_result: dict[str, Any]
    ceiling_applies: bool
    ceiling_limit: float | None


def _set_observed_cost(state: RunState, value: float) -> None:
    state.observed_ceiling_cost_usd = value
    state.manifest["observed_ceiling_cost_usd"] = value


def _write_trial_inputs(
    case_dir: Path,
    corpus: dict[str, Any],
    request: str,
    projected: ModelCorpus | None,
    selection_schema: dict[str, Any] | None,
) -> None:
    _write_json_atomic(case_dir / "corpus.json", corpus)
    _write_text_atomic(case_dir / "request.txt", request)
    if projected is None or selection_schema is None:
        return
    _write_json_atomic(case_dir / "model-corpus.json", projected.document)
    _write_json_atomic(
        case_dir / "citation-map.json",
        {ref: citation.__dict__ for ref, citation in projected.citations.items()},
    )
    _write_json_atomic(case_dir / "output-schema.json", selection_schema)
    _write_json_atomic(case_dir / "selection-schema.json", selection_schema)


def _prepare_trial(
    *,
    adapter: Adapter,
    prompt_version: str,
    prompt_path: Path,
    case: dict[str, Any],
    variant: TrialVariant,
    plan: EvaluationPlan,
    options: ExecutionOptions,
) -> TrialContext:
    prompt = prompt_path.read_bytes().decode("utf-8")
    trial, result_case_id, mutations, source_failures, _is_clean_pair = variant
    result_key = (
        adapter.provider,
        adapter.model,
        prompt_version,
        result_case_id,
        trial,
    )
    case_corpus_path = (
        options.suite_path.parent / case["corpus"]
        if case.get("corpus")
        else options.corpus_path
    )
    corpus = copy.deepcopy(_json(case_corpus_path))
    _relocate(corpus, case.get("corpus_relocations", []))
    _mutate(corpus, mutations)
    _set_source_failures(corpus, source_failures)
    problems = corpus_schema.validate_corpus(corpus)
    if problems:
        raise ValueError(f"case {case['id']} has invalid corpus: {'; '.join(problems)}")
    config_path = options.suite_path.parent / case["config"]
    config_data = _json(config_path)
    config = briefing_config.load_config(config_path)
    projected: ModelCorpus | None = None
    selection_schema: dict[str, Any] | None = None
    if options.generation_path == "production-parity":
        projected = project_corpus(corpus)
        selection_schema = build_selection_schema(config, projected.citations)
        request = structured_model_request(prompt, config_data, projected)
    else:
        request = model_request(prompt, config_data, corpus)
    safe_key = _safe_artifact_key(result_key)
    case_dir = options.output_dir / safe_key
    _prepare_artifact_dir(case_dir, resume=options.resumed)
    _write_trial_inputs(case_dir, corpus, request, projected, selection_schema)
    base_result = _base_result(
        adapter,
        prompt_version,
        plan.prompt_sha256[prompt_version],
        case,
        variant,
        safe_key,
        plan.case_corpus_sha256[case["id"]],
    )
    ceiling_applies = options.cost_ceiling_usd is not None and (
        options.cost_ceiling_provider is None
        or adapter.provider == options.cost_ceiling_provider
    )
    return TrialContext(
        adapter,
        prompt_version,
        case,
        variant,
        prompt,
        corpus,
        config_data,
        config,
        projected,
        selection_schema,
        request,
        safe_key,
        case_dir,
        base_result,
        ceiling_applies,
        options.cost_ceiling_usd if ceiling_applies else None,
    )


def _failed_result(
    context: TrialContext, status: str, error: dict[str, Any]
) -> dict[str, Any]:
    return {
        **context.base_result,
        "status": status,
        "error": error,
        "grounding_adjudication": None,
        "semantic_adjudication": None,
        "first": None,
        "correction_attempted": False,
        "correction": None,
        "correction_error": None,
        "final": None,
    }


def _unwrap_provider_failure(
    exc: Exception,
) -> tuple[
    Exception,
    list[tuple[str, Generation]],
    _ProductionParityAttempt | None,
]:
    if isinstance(exc, _ProductionParityProviderError):
        return exc.cause, exc.completed_calls, exc.partial_attempt
    return exc, [], None


def _charge_provider_failure(
    state: RunState,
    context: TrialContext,
    provider_exc: Exception,
    completed_calls: list[tuple[str, Generation]],
) -> None:
    charge = 0.0
    if isinstance(provider_exc, ProviderRequestError) and provider_exc.cost_usd is not None:
        charge += provider_exc.cost_usd
    if completed_calls:
        charge += _reported_stage_cost(completed_calls)
    if context.ceiling_applies and charge:
        _set_observed_cost(state, state.observed_ceiling_cost_usd + charge)


def _provider_failure_record(
    *,
    stage: str,
    exc: Exception,
    context: TrialContext,
    state: RunState,
) -> dict[str, Any]:
    provider_exc, completed_calls, partial_attempt = _unwrap_provider_failure(exc)
    _charge_provider_failure(state, context, provider_exc, completed_calls)
    error = _provider_error(stage, provider_exc)
    if completed_calls:
        error["completed_stage_calls"] = _stage_call_records(completed_calls)
    if partial_attempt is not None:
        _write_production_attempt_artifacts(context.case_dir, stage, partial_attempt)
    error_name = "error.json" if stage == "first" else "correction-error.json"
    _write_json_atomic(context.case_dir / error_name, error)
    return error


def _run_correction(
    context: TrialContext,
    state: RunState,
    options: ExecutionOptions,
    first_attempt: GenerationAttempt,
    first: ScoredAttempt,
) -> tuple[GenerationAttempt | None, dict[str, Any] | None]:
    if first.contract_success:
        return None, None
    if (
        context.ceiling_limit is not None
        and state.observed_ceiling_cost_usd >= context.ceiling_limit
    ):
        error = {
            "stage": "correction",
            "type": "CostCeilingReached",
            "message": (
                f"correction skipped after observed {context.adapter.provider} cost "
                f"reached ${state.observed_ceiling_cost_usd:.6f}"
            ),
            "transient": False,
        }
        _write_json_atomic(context.case_dir / "correction-error.json", error)
        return None, error
    try:
        corrected = run_correction_attempt(
            adapter=context.adapter,
            generation_path=options.generation_path,
            prior=first_attempt,
            request=context.request,
            findings=[finding._asdict() for finding in first.findings],
            selection_schema=context.selection_schema,
            policy=context.prompt,
            config_data=context.config_data,
            corpus=context.corpus,
            config=context.config,
            projected=context.projected,
            trace_id=context.safe_key,
        )
    except Exception as exc:
        return None, _provider_failure_record(
            stage="correction", exc=exc, context=context, state=state
        )
    if context.ceiling_applies:
        _set_observed_cost(
            state,
            state.observed_ceiling_cost_usd
            + _reported_generation_cost(corrected.generation),
        )
    return corrected, None


def _write_completed_artifacts(
    context: TrialContext,
    options: ExecutionOptions,
    first_attempt: GenerationAttempt,
    first: ScoredAttempt,
    final_attempt: GenerationAttempt,
    final: ScoredAttempt,
) -> tuple[dict[str, Any], str | None]:
    _write_text_atomic(context.case_dir / "first.md", first.text)
    _write_text_atomic(context.case_dir / "final.md", final.text)
    if options.generation_path == "production-parity":
        if first_attempt.parity is None or final_attempt.parity is None:
            raise AssertionError("production-parity attempt was not evaluated")
        _write_production_attempt_artifacts(
            context.case_dir, "final", final_attempt.parity
        )
        _write_json_atomic(
            context.case_dir / "first-structured.json",
            first_attempt.generation.structured_output,
        )
        _write_json_atomic(
            context.case_dir / "final-structured.json",
            final_attempt.generation.structured_output,
        )
    _write_json_atomic(
        context.case_dir / "grounding-adjudication.json",
        _adjudication_template(final.sections),
    )
    semantic = _semantic_adjudication_template(context.case, final.sections)
    semantic_path = None
    if semantic["judgments"]:
        _write_json_atomic(context.case_dir / "semantic-adjudication.json", semantic)
        semantic_path = f"{context.safe_key}/semantic-adjudication.json"
    return semantic, semantic_path


def _record_circuit_skip(
    context: TrialContext,
    state: RunState,
    adapter_state: AdapterState,
    checkpoint: Checkpoint,
    output_dir: Path,
) -> str:
    reason = adapter_state.circuit_reason
    if reason is None:
        raise AssertionError("circuit skip requires an open circuit")
    error = {
        "stage": "first",
        "type": "CircuitOpen",
        "message": (
            f"{context.adapter.provider}/{context.adapter.model} skipped after "
            f"{CIRCUIT_BREAKER_THRESHOLD} consecutive provider failures"
        ),
        "transient": reason.get("transient", False),
        "trigger": reason,
    }
    _write_json_atomic(context.case_dir / "error.json", error)
    state.results.append(_failed_result(context, "skipped_circuit_open", error))
    checkpoint(state.manifest, output_dir)
    adapter_state.completed += 1
    return "circuit open; skipped"


def _run_case_trial(
    context: TrialContext,
    state: RunState,
    adapter_state: AdapterState,
    options: ExecutionOptions,
) -> str:
    if adapter_state.circuit_reason is not None:
        return _record_circuit_skip(
            context, state, adapter_state, options.checkpoint, options.output_dir
        )
    try:
        first_attempt = run_first_attempt(
            adapter=context.adapter,
            generation_path=options.generation_path,
            request=context.request,
            selection_schema=context.selection_schema,
            policy=context.prompt,
            config_data=context.config_data,
            corpus=context.corpus,
            config=context.config,
            projected=context.projected,
            trace_id=context.safe_key,
        )
    except Exception as exc:
        error = _provider_failure_record(
            stage="first", exc=exc, context=context, state=state
        )
        adapter_state.consecutive_failures += 1
        if adapter_state.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            adapter_state.circuit_reason = error
        state.results.append(_failed_result(context, "provider_error", error))
        options.checkpoint(state.manifest, options.output_dir)
        adapter_state.completed += 1
        if adapter_state.circuit_reason is not None:
            return "circuit opened after provider error"
        return (
            f"provider error {adapter_state.consecutive_failures}/"
            f"{CIRCUIT_BREAKER_THRESHOLD}"
        )
    if context.ceiling_applies:
        _set_observed_cost(
            state,
            state.observed_ceiling_cost_usd
            + _reported_generation_cost(first_attempt.generation),
        )
    if first_attempt.parity is not None:
        _write_production_attempt_artifacts(
            context.case_dir, "first", first_attempt.parity
        )
    first = score_attempt(context.case, context.corpus, context.config, first_attempt)
    corrected_attempt, correction_error = _run_correction(
        context, state, options, first_attempt, first
    )
    final_attempt = corrected_attempt or first_attempt
    final = (
        score_attempt(context.case, context.corpus, context.config, corrected_attempt)
        if corrected_attempt is not None
        else first
    )
    semantic, semantic_path = _write_completed_artifacts(
        context, options, first_attempt, first, final_attempt, final
    )
    state.results.append(build_completed_result(
        base_result=context.base_result,
        first_attempt=first_attempt,
        first=first,
        corrected_attempt=corrected_attempt,
        final=final,
        correction_error=correction_error,
        artifact_key=context.safe_key,
        semantic=semantic,
        semantic_path=semantic_path,
    ))
    options.checkpoint(state.manifest, options.output_dir)
    if correction_error:
        adapter_state.consecutive_failures += 1
        if adapter_state.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            adapter_state.circuit_reason = correction_error
    else:
        adapter_state.consecutive_failures = 0
    adapter_state.completed += 1
    if adapter_state.circuit_reason is not None:
        return "circuit opened after correction error"
    if correction_error:
        return (
            f"correction error {adapter_state.consecutive_failures}/"
            f"{CIRCUIT_BREAKER_THRESHOLD}"
        )
    return "completed"


def _run_adapter(
    *,
    adapter: Adapter,
    adapter_plan: list[tuple[str, Path, dict[str, Any], TrialVariant]],
    plan: EvaluationPlan,
    state: RunState,
    options: ExecutionOptions,
    progress: ProgressCallback | None,
    model_total: int,
) -> dict[str, Any] | None:
    adapter_rows = [
        row
        for row in state.results
        if (row.get("provider"), row.get("model"))
        == (adapter.provider, adapter.model)
    ]
    failures, circuit = _reconstruct_failure_state(
        state.results, adapter.provider, adapter.model
    )
    adapter_state = AdapterState(len(adapter_rows), failures, circuit)
    if progress:
        progress(
            adapter.provider,
            adapter.model,
            adapter_state.completed,
            model_total,
            "resuming" if options.resumed else "starting",
        )
    recorded_keys = {_result_key(row) for row in state.results}
    for prompt_version, prompt_path, case, variant in adapter_plan:
        key = (
            adapter.provider,
            adapter.model,
            prompt_version,
            variant[1],
            variant[0],
        )
        if key in recorded_keys:
            continue
        ceiling_limit = options.cost_ceiling_usd
        ceiling_applies = ceiling_limit is not None and (
            options.cost_ceiling_provider is None
            or adapter.provider == options.cost_ceiling_provider
        )
        if (
            ceiling_applies
            and ceiling_limit is not None
            and state.observed_ceiling_cost_usd >= ceiling_limit
        ):
            state.manifest["run_status"] = "stopped_cost_ceiling"
            state.manifest["completed_at"] = datetime.now(UTC).isoformat()
            state.manifest["observed_ceiling_cost_usd"] = state.observed_ceiling_cost_usd
            return options.checkpoint(state.manifest, options.output_dir)
        context = _prepare_trial(
            adapter=adapter,
            prompt_version=prompt_version,
            prompt_path=prompt_path,
            case=case,
            variant=variant,
            plan=plan,
            options=options,
        )
        status = _run_case_trial(context, state, adapter_state, options)
        if progress:
            progress(
                adapter.provider,
                adapter.model,
                adapter_state.completed,
                model_total,
                status,
            )
    return None


def execute_evaluation(
    adapters: list[Adapter],
    prompt_versions: dict[str, Path],
    output_dir: Path,
    trials: int = 1,
    suite_path: Path = DEFAULT_SUITE,
    corpus_path: Path = DEFAULT_CORPUS,
    progress: ProgressCallback | None = None,
    *,
    protocol_path: Path = DEFAULT_PROTOCOL,
    run_kind: str = "development",
    execution_seed: int | None = None,
    cost_ceiling_usd: float | None = None,
    cost_ceiling_provider: str | None = None,
    resume: bool = False,
    generation_path: str = "markdown",
    source_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from evaluator import runner as runner_module

    if trials <= 0:
        raise ValueError("trials must be positive")
    if run_kind not in {"development", "pilot", "final"}:
        raise ValueError("run_kind must be development, pilot, or final")
    if generation_path not in {"markdown", "production-parity"}:
        raise ValueError("generation_path must be markdown or production-parity")
    resume_manifest = _load_resume_manifest(output_dir) if resume else None
    plan = resolve_evaluation_plan(
        adapters=adapters,
        prompt_versions=prompt_versions,
        trials=trials,
        suite_path=suite_path,
        corpus_path=corpus_path,
        protocol_path=protocol_path,
        run_kind=run_kind,
        generation_path=generation_path,
        execution_seed=execution_seed,
        cost_ceiling_usd=cost_ceiling_usd,
        cost_ceiling_provider=cost_ceiling_provider,
        resume_manifest=resume_manifest,
        source_provenance=source_provenance,
        provenance=runner_module._git_provenance,
        circuit_breaker_threshold=CIRCUIT_BREAKER_THRESHOLD,
    )
    state = initialize_run(
        plan=plan,
        adapters=adapters,
        output_dir=output_dir,
        resume_manifest=resume_manifest,
        suite_path=suite_path,
        protocol_path=protocol_path,
        run_kind=run_kind,
        generation_path=generation_path,
        cost_ceiling_usd=cost_ceiling_usd,
        cost_ceiling_provider=cost_ceiling_provider,
        checkpoint=runner_module._checkpoint,
        deterministic_suite=runner_module.run_deterministic_suite,
    )
    if state.completed_report is not None:
        return state.completed_report
    options = ExecutionOptions(
        output_dir,
        suite_path,
        corpus_path,
        generation_path,
        cost_ceiling_usd,
        cost_ceiling_provider,
        resume_manifest is not None,
        runner_module._checkpoint,
    )
    model_total = len(prompt_versions) * plan.case_trial_units * trials
    for adapter, adapter_plan in plan.execution_plans:
        completed = _run_adapter(
            adapter=adapter,
            adapter_plan=adapter_plan,
            plan=plan,
            state=state,
            options=options,
            progress=progress,
            model_total=model_total,
        )
        if completed is not None:
            return completed
    return finalize_run_report(
        state.manifest,
        output_dir,
        runner_module._checkpoint,
        has_errors=_has_execution_errors(state.results),
    )
