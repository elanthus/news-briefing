"""Production-parity generation attempts and their artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import briefing_config
import eval_briefing
from agent_runner.output import (
    Citation,
    ModelCorpus,
    attach_frozen_selection,
    build_prose_schema,
    detach_prose,
    project_selected_evidence,
    render_briefing,
    validate_output,
    validate_prose_output,
    validate_selection,
)
from agent_runner.runner import (
    build_prose_request as structured_prose_request,
)
from agent_runner.runner import (
    correction_request as structured_correction_request,
)
from agent_runner.runner import deterministic_repair_candidate

from evaluator.adapters import Adapter, Generation
from evaluator.checkpoint import _write_json_atomic, _write_text_atomic
from evaluator.plan import correction_request


def _evaluate_structured_generation(
    generation: Any,
    corpus: dict[str, Any],
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
    repair_actions: list[dict[str, str]] | None = None,
) -> tuple[str, dict[str, eval_briefing.Section], list[eval_briefing.Finding]]:
    """Validate and render one production-shaped structured response."""
    output = generation.structured_output
    if not isinstance(output, dict):
        findings = [
            eval_briefing.Finding(
                "ERROR", "structured_type", "provider returned no structured object"
            )
        ]
        return "", eval_briefing.parse_briefing("", config), findings
    findings = [
        eval_briefing.Finding(finding.level, finding.check, finding.message)
        for finding in validate_output(output, config, citations)
    ]
    if any(finding.level == eval_briefing.ERROR for finding in findings):
        return "", eval_briefing.parse_briefing("", config), findings
    rendered = render_briefing(
        output,
        corpus,
        config,
        citations,
        repair_actions=repair_actions or (),
    )
    sections = eval_briefing.parse_briefing(rendered, config)
    findings.extend(eval_briefing.evaluate_parsed(corpus, rendered, sections, config))
    return rendered, sections, findings


@dataclass(frozen=True)
class _ProductionParityAttempt:
    """One evaluator candidate assembled from production's staged contract."""

    generation: Generation
    text: str
    sections: dict[str, eval_briefing.Section]
    findings: list[eval_briefing.Finding]
    selection: dict[str, Any] | None
    prose: dict[str, Any] | None
    selected_evidence: dict[str, Any] | None
    prose_request: str | None
    prose_schema: dict[str, Any] | None
    correction_stage: str
    deterministic_repairs: list[dict[str, Any]]


class _ProductionParityProviderError(RuntimeError):
    """A failed stage plus the completed portion of its candidate."""

    def __init__(
        self,
        cause: Exception,
        completed_calls: list[tuple[str, Generation]],
        partial_attempt: _ProductionParityAttempt,
    ):
        super().__init__(str(cause))
        self.cause = cause
        self.completed_calls = completed_calls
        self.partial_attempt = partial_attempt


@dataclass(frozen=True)
class GenerationAttempt:
    """A provider generation and optional production-parity evaluation."""

    generation: Generation
    parity: _ProductionParityAttempt | None


def _output_findings(findings: list[Any]) -> list[eval_briefing.Finding]:
    return [
        eval_briefing.Finding(finding.level, finding.check, finding.message)
        for finding in findings
    ]


def _production_selection_findings(
    selection: Any,
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
) -> list[eval_briefing.Finding]:
    """Apply the selection checks used by the scheduled production runner."""
    findings = _output_findings(validate_selection(selection, config, citations))
    if any(finding.level == eval_briefing.ERROR for finding in findings):
        return findings
    if not isinstance(selection, dict):
        return findings

    used_items = {
        citations[ref].item_ref
        for section_value in selection["sections"].values()
        for entry in section_value["topics"]
        for ref in entry["citation_refs"]
    } | {
        citations[ref].item_ref
        for entries in selection["excluded_topics"].values()
        for entry in entries
        for ref in entry["citation_refs"]
    }
    for section in config.sections:
        if selection["sections"][section.name]["topics"]:
            continue
        unused_eligible = {
            citation.item_ref
            for citation in citations.values()
            if citation.category in section.corpus_categories
            and citation.item_ref not in used_items
        }
        if unused_eligible:
            findings.append(eval_briefing.Finding(
                eval_briefing.ERROR,
                "slots_underfilled",
                f"{section.name}: 0 topics, expected {section.target_stories}; "
                f"{len(unused_eligible)} unused eligible corpus item(s) remain",
            ))
    return findings


def _sum_reported_int(values: list[int | None]) -> int | None:
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _sum_reported_float(values: list[float | None]) -> float | None:
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _combine_structured_calls(
    calls: list[tuple[str, Generation]],
    structured_output: dict[str, Any] | None,
) -> Generation:
    """Represent one staged candidate without hiding its actual provider calls."""
    if not calls:
        raise ValueError("a production-parity candidate requires at least one provider call")
    cost_notes = list(dict.fromkeys(
        generation.cost_note
        for _stage, generation in calls
        if generation.cost_note
    ))
    return Generation(
        text=calls[-1][1].text,
        latency_ms=sum(generation.latency_ms for _stage, generation in calls),
        input_tokens=_sum_reported_int([
            generation.input_tokens for _stage, generation in calls
        ]),
        output_tokens=_sum_reported_int([
            generation.output_tokens for _stage, generation in calls
        ]),
        cost_usd=_sum_reported_float([
            generation.cost_usd for _stage, generation in calls
        ]),
        cost_note="; ".join(cost_notes) or None,
        provider_request_id=(
            calls[0][1].provider_request_id if len(calls) == 1 else None
        ),
        usage={"production_parity_calls": _stage_call_records(calls)},
        attempts=sum(generation.attempts for _stage, generation in calls),
        structured_output=structured_output,
    )


def _stage_call_records(
    calls: list[tuple[str, Generation]],
) -> list[dict[str, Any]]:
    return [
        {"stage": stage, **generation.record()}
        for stage, generation in calls
    ]


def _reported_stage_cost(calls: list[tuple[str, Generation]]) -> float:
    return sum(
        generation.cost_usd
        for _stage, generation in calls
        if generation.cost_usd is not None
    )


def _reported_generation_cost(generation: Generation) -> float:
    usage = generation.usage
    staged = usage.get("production_parity_calls") if isinstance(usage, dict) else None
    if isinstance(staged, list):
        return sum(
            cost
            for record in staged
            if isinstance(record, dict)
            and isinstance((cost := record.get("cost_usd")), (int, float))
            and not isinstance(cost, bool)
        )
    return generation.cost_usd or 0.0


def _empty_structured_result(
    config: briefing_config.BriefingConfig,
) -> tuple[str, dict[str, eval_briefing.Section]]:
    return "", eval_briefing.parse_briefing("", config)


def _finding_dicts(
    findings: list[eval_briefing.Finding],
) -> list[dict[str, str]]:
    return [finding._asdict() for finding in findings]


def _repair_record(
    stage: str,
    before: list[eval_briefing.Finding],
    after: list[eval_briefing.Finding],
    actions: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "stage": stage,
        "findings_before": _finding_dicts(before),
        "findings_after": _finding_dicts(after),
        "actions": actions,
    }


def _production_parity_after_selection(
    *,
    adapter: Adapter,
    calls: list[tuple[str, Generation]],
    selection_generation: Generation,
    policy: str,
    config_data: dict[str, Any],
    corpus: dict[str, Any],
    config: briefing_config.BriefingConfig,
    projected: ModelCorpus,
    trace_id: str,
) -> _ProductionParityAttempt:
    selection = selection_generation.structured_output
    selection_findings = _production_selection_findings(
        selection, config, projected.citations
    )
    deterministic_repairs: list[dict[str, Any]] = []
    if isinstance(selection, dict):
        repair = deterministic_repair_candidate(
            selection,
            _finding_dicts(selection_findings),
            config=config,
            citations=projected.citations,
            selection_only=True,
        )
        if repair is not None:
            before_repair = selection_findings
            selection = repair.output
            selection_findings = _production_selection_findings(
                selection, config, projected.citations
            )
            deterministic_repairs.append(_repair_record(
                "selection",
                before_repair,
                selection_findings,
                repair.actions,
            ))
    if any(finding.level == eval_briefing.ERROR for finding in selection_findings):
        text, sections = _empty_structured_result(config)
        return _ProductionParityAttempt(
            generation=_combine_structured_calls(
                calls, selection if isinstance(selection, dict) else None
            ),
            text=text,
            sections=sections,
            findings=selection_findings,
            selection=selection if isinstance(selection, dict) else None,
            prose=None,
            selected_evidence=None,
            prose_request=None,
            prose_schema=None,
            correction_stage="selection",
            deterministic_repairs=deterministic_repairs,
        )
    if not isinstance(selection, dict):
        raise AssertionError("valid selection must be an object")

    selected_evidence = project_selected_evidence(selection, projected)
    prose_request = structured_prose_request(policy, config_data, selected_evidence)
    prose_schema = build_prose_schema(config, selection)
    try:
        prose_generation = adapter.generate_structured(
            prose_request, prose_schema, f"{trace_id}-prose"
        )
    except Exception as exc:
        text, sections = _empty_structured_result(config)
        partial_attempt = _ProductionParityAttempt(
            generation=_combine_structured_calls(calls, selection),
            text=text,
            sections=sections,
            findings=selection_findings,
            selection=selection,
            prose=None,
            selected_evidence=selected_evidence,
            prose_request=prose_request,
            prose_schema=prose_schema,
            correction_stage="prose",
            deterministic_repairs=deterministic_repairs,
        )
        raise _ProductionParityProviderError(
            exc, calls, partial_attempt
        ) from exc
    calls.append(("prose", prose_generation))
    prose = prose_generation.structured_output
    prose_findings = _output_findings(validate_prose_output(prose, config, selection))
    complete_output = attach_frozen_selection(selection, prose, config)
    combined = _combine_structured_calls(calls, complete_output)
    if any(finding.level == eval_briefing.ERROR for finding in prose_findings):
        text, sections = _empty_structured_result(config)
        findings = prose_findings
    else:
        text, sections, findings = _evaluate_structured_generation(
            combined, corpus, config, projected.citations
        )
    repair = deterministic_repair_candidate(
        complete_output,
        _finding_dicts(findings),
        corpus=corpus,
        config=config,
        citations=projected.citations,
    )
    if repair is not None:
        before_repair = findings
        complete_output = repair.output
        prose = detach_prose(complete_output, config)
        combined = _combine_structured_calls(calls, complete_output)
        text, sections, findings = _evaluate_structured_generation(
            combined,
            corpus,
            config,
            projected.citations,
            repair_actions=repair.actions,
        )
        deterministic_repairs.append(_repair_record(
            "prose",
            before_repair,
            findings,
            repair.actions,
        ))
    return _ProductionParityAttempt(
        generation=combined,
        text=text,
        sections=sections,
        findings=findings,
        selection=selection,
        prose=prose if isinstance(prose, dict) else None,
        selected_evidence=selected_evidence,
        prose_request=prose_request,
        prose_schema=prose_schema,
        correction_stage="prose",
        deterministic_repairs=deterministic_repairs,
    )


def _production_parity_first_attempt(
    *,
    adapter: Adapter,
    selection_request: str,
    selection_schema: dict[str, Any],
    policy: str,
    config_data: dict[str, Any],
    corpus: dict[str, Any],
    config: briefing_config.BriefingConfig,
    projected: ModelCorpus,
    trace_id: str,
) -> _ProductionParityAttempt:
    selection_generation = adapter.generate_structured(
        selection_request, selection_schema, f"{trace_id}-selection"
    )
    return _production_parity_after_selection(
        adapter=adapter,
        calls=[("selection", selection_generation)],
        selection_generation=selection_generation,
        policy=policy,
        config_data=config_data,
        corpus=corpus,
        config=config,
        projected=projected,
        trace_id=trace_id,
    )


def _production_parity_correction_attempt(
    *,
    adapter: Adapter,
    prior: _ProductionParityAttempt,
    selection_request: str,
    selection_schema: dict[str, Any],
    policy: str,
    config_data: dict[str, Any],
    corpus: dict[str, Any],
    config: briefing_config.BriefingConfig,
    projected: ModelCorpus,
    trace_id: str,
) -> _ProductionParityAttempt:
    finding_records = [finding._asdict() for finding in prior.findings]
    if prior.correction_stage == "selection":
        correction_prompt = structured_correction_request(
            selection_request,
            prior.selection or {},
            finding_records,
        )
        selection_generation = adapter.generate_structured(
            correction_prompt,
            selection_schema,
            f"{trace_id}-selection-correction",
        )
        return _production_parity_after_selection(
            adapter=adapter,
            calls=[("selection_correction", selection_generation)],
            selection_generation=selection_generation,
            policy=policy,
            config_data=config_data,
            corpus=corpus,
            config=config,
            projected=projected,
            trace_id=f"{trace_id}-after-selection-correction",
        )

    if (
        prior.selection is None
        or prior.prose_request is None
        or prior.prose_schema is None
    ):
        raise AssertionError("prose correction requires a frozen selection and prose contract")
    correction_prompt = structured_correction_request(
        prior.prose_request,
        prior.prose or {},
        finding_records,
        prose_only=True,
    )
    prose_generation = adapter.generate_structured(
        correction_prompt,
        prior.prose_schema,
        f"{trace_id}-prose-correction",
    )
    prose = prose_generation.structured_output
    prose_findings = _output_findings(
        validate_prose_output(prose, config, prior.selection)
    )
    complete_output = attach_frozen_selection(prior.selection, prose, config)
    combined = _combine_structured_calls(
        [("prose_correction", prose_generation)], complete_output
    )
    if any(finding.level == eval_briefing.ERROR for finding in prose_findings):
        text, sections = _empty_structured_result(config)
        findings = prose_findings
    else:
        text, sections, findings = _evaluate_structured_generation(
            combined, corpus, config, projected.citations
        )
    deterministic_repairs: list[dict[str, Any]] = []
    repair = deterministic_repair_candidate(
        complete_output,
        _finding_dicts(findings),
        corpus=corpus,
        config=config,
        citations=projected.citations,
    )
    if repair is not None:
        before_repair = findings
        complete_output = repair.output
        prose = detach_prose(complete_output, config)
        combined = _combine_structured_calls(
            [("prose_correction", prose_generation)], complete_output
        )
        text, sections, findings = _evaluate_structured_generation(
            combined,
            corpus,
            config,
            projected.citations,
            repair_actions=repair.actions,
        )
        deterministic_repairs.append(_repair_record(
            "prose",
            before_repair,
            findings,
            repair.actions,
        ))
    return _ProductionParityAttempt(
        generation=combined,
        text=text,
        sections=sections,
        findings=findings,
        selection=prior.selection,
        prose=prose if isinstance(prose, dict) else None,
        selected_evidence=prior.selected_evidence,
        prose_request=prior.prose_request,
        prose_schema=prior.prose_schema,
        correction_stage="prose",
        deterministic_repairs=deterministic_repairs,
    )


def run_first_attempt(
    *,
    adapter: Adapter,
    generation_path: str,
    request: str,
    selection_schema: dict[str, Any] | None,
    policy: str,
    config_data: dict[str, Any],
    corpus: dict[str, Any],
    config: briefing_config.BriefingConfig,
    projected: ModelCorpus | None,
    trace_id: str,
) -> GenerationAttempt:
    """Run the first provider attempt for either supported generation path."""
    if generation_path == "markdown":
        return GenerationAttempt(adapter.generate(request), None)
    if projected is None or selection_schema is None:
        raise AssertionError(
            "production-parity projection and selection schema were not built"
        )
    parity = _production_parity_first_attempt(
        adapter=adapter,
        selection_request=request,
        selection_schema=selection_schema,
        policy=policy,
        config_data=config_data,
        corpus=corpus,
        config=config,
        projected=projected,
        trace_id=trace_id,
    )
    return GenerationAttempt(parity.generation, parity)


def run_correction_attempt(
    *,
    adapter: Adapter,
    generation_path: str,
    prior: GenerationAttempt,
    request: str,
    findings: list[dict[str, str]],
    selection_schema: dict[str, Any] | None,
    policy: str,
    config_data: dict[str, Any],
    corpus: dict[str, Any],
    config: briefing_config.BriefingConfig,
    projected: ModelCorpus | None,
    trace_id: str,
) -> GenerationAttempt:
    """Run a checker-driven correction for either generation path."""
    if generation_path == "markdown":
        generation = adapter.generate(
            correction_request(request, prior.generation.text, findings)
        )
        return GenerationAttempt(generation, None)
    if prior.parity is None or projected is None or selection_schema is None:
        raise AssertionError(
            "production-parity projection and selection schema were not built"
        )
    parity = _production_parity_correction_attempt(
        adapter=adapter,
        prior=prior.parity,
        selection_request=request,
        selection_schema=selection_schema,
        policy=policy,
        config_data=config_data,
        corpus=corpus,
        config=config,
        projected=projected,
        trace_id=trace_id,
    )
    return GenerationAttempt(parity.generation, parity)


def _write_production_attempt_artifacts(
    case_dir: Path,
    prefix: str,
    attempt: _ProductionParityAttempt,
) -> None:
    """Preserve each stage so prompt/schema compatibility can be audited."""
    _write_json_atomic(case_dir / f"{prefix}-selection.json", attempt.selection)
    _write_json_atomic(case_dir / f"{prefix}-prose.json", attempt.prose)
    if attempt.selected_evidence is not None:
        _write_json_atomic(
            case_dir / f"{prefix}-selected-evidence.json",
            attempt.selected_evidence,
        )
    if attempt.prose_request is not None:
        _write_text_atomic(
            case_dir / f"{prefix}-prose-request.txt", attempt.prose_request
        )
    if attempt.prose_schema is not None:
        _write_json_atomic(
            case_dir / f"{prefix}-prose-schema.json", attempt.prose_schema
        )
    if attempt.deterministic_repairs:
        _write_json_atomic(
            case_dir / f"{prefix}-deterministic-repairs.json",
            attempt.deterministic_repairs,
        )
