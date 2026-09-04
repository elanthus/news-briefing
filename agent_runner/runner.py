"""Code-owned fetch, generation, validation, correction, and finalization loop."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import briefing_config
import corpus_schema
import eval_briefing
from agent_runner.checkpoint import (
    RunStore,
    sha256_bytes,
    sha256_file,
    utc_now,
    write_text_atomic,
)
from agent_runner.models import GenerationRequest, ModelProvider, ProviderError
from agent_runner.outcomes import classify_outcome, finding_domain
from agent_runner.output import (
    REPAIRABLE_CHECKS,
    Citation,
    ModelCorpus,
    OutputFinding,
    attach_frozen_selection,
    build_prose_schema,
    build_selection_schema,
    detach_prose,
    project_corpus,
    project_selected_evidence,
    redact_destinations,
    redact_opaque_references,
    redact_preview_value,
    render_briefing,
    render_candidate_preview,
    render_validation_status,
    repair_structural_output,
    validate_output,
    validate_prose_output,
    validate_selection,
)

ROOT = Path(__file__).resolve().parents[1]


def _portable_path(path: Path) -> str:
    """Describe a path without recording host-specific directory details."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return f"<external>/{resolved.name}"


def _sanitize_process_output(text: str, replacements: Sequence[tuple[str, str]]) -> str:
    """Replace known execution paths before process output becomes an artifact."""
    for host_path, portable in sorted(replacements, key=lambda row: len(row[0]), reverse=True):
        text = text.replace(host_path, portable)
    return text


@dataclass(frozen=True)
class RunnerSettings:
    config_path: Path
    sources_path: Path
    prompt_path: Path
    output_path: Path
    corpus_path: Path | None = None
    hours: int = 24
    source_cap: int = 25
    category_cap: int = 60
    timeout_seconds: int = 600
    max_corrections: int = 1
    strict: bool = False

    def identity(
        self,
        provider_info: dict[str, Any],
        code_info: dict[str, Any],
        *,
        corpus_snapshot: bytes | None = None,
    ) -> dict[str, Any]:
        identity = {
            "config_path": _portable_path(self.config_path),
            "config_sha256": sha256_file(self.config_path),
            "prompt_path": _portable_path(self.prompt_path),
            "prompt_sha256": sha256_file(self.prompt_path),
            "output_path": _portable_path(self.output_path),
            "timeout_seconds": self.timeout_seconds,
            "max_corrections": self.max_corrections,
            "strict": self.strict,
            "provider": provider_info,
            "code": {
                key: code_info[key]
                for key in ("commit", "python", "source_sha256")
            },
        }
        if self.corpus_path is not None:
            if corpus_snapshot is None:
                raise ValueError("replay identity requires the immutable corpus snapshot")
            identity["corpus_path"] = _portable_path(self.corpus_path)
            identity["corpus_sha256"] = sha256_bytes(corpus_snapshot)
        else:
            identity.update({
                "sources_path": _portable_path(self.sources_path),
                "sources_sha256": sha256_file(self.sources_path),
                "hours": self.hours,
                "source_cap": self.source_cap,
                "category_cap": self.category_cap,
            })
        return identity


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    run_dir: Path
    output_path: Path | None
    status: str


def _git_provenance() -> dict[str, Any]:
    def git_output(arguments: list[str]) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout.strip() if completed.returncode == 0 else None

    commit = git_output(["rev-parse", "HEAD"])
    status = git_output(["status", "--porcelain"])
    runtime_paths = [
        ROOT / "briefing_config.py",
        ROOT / "corpus_schema.py",
        ROOT / "eval_briefing.py",
        ROOT / "fetch_news.py",
        ROOT / "run_briefing.py",
        *sorted((ROOT / "agent_runner").glob("*.py")),
    ]
    return {
        "commit": commit,
        "dirty": bool(status) if status is not None else None,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "source_sha256": {
            str(path.relative_to(ROOT)): sha256_file(path)
            for path in runtime_paths
        },
    }


def _fetch_corpus(store: RunStore, settings: RunnerSettings) -> dict[str, Any]:
    corpus_path = store.root / "corpus.json"
    command = [
        sys.executable,
        str(ROOT / "fetch_news.py"),
        "--sources",
        str(settings.sources_path),
        "--hours",
        str(settings.hours),
        "--source-cap",
        str(settings.source_cap),
        "--category-cap",
        str(settings.category_cap),
        "--output",
        str(corpus_path),
    ]
    trace_command = [
        Path(sys.executable).name,
        "fetch_news.py",
        "--sources",
        _portable_path(settings.sources_path),
        "--hours",
        str(settings.hours),
        "--source-cap",
        str(settings.source_cap),
        "--category-cap",
        str(settings.category_cap),
        "--output",
        "corpus.json",
    ]
    store.trace("fetch_started", command=trace_command)
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            cwd=ROOT,
            timeout=settings.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"fetch_news.py exceeded the {settings.timeout_seconds}s fetch deadline"
        ) from exc
    replacements = [
        (str(corpus_path), "corpus.json"),
        (str(corpus_path.resolve()), "corpus.json"),
        (str(settings.sources_path), _portable_path(settings.sources_path)),
        (str(settings.sources_path.resolve()), _portable_path(settings.sources_path)),
        (str((ROOT / "fetch_news.py").resolve()), "fetch_news.py"),
        (str(Path(sys.executable).resolve()), Path(sys.executable).name),
        (str(ROOT), "."),
    ]
    store.write_text("fetch.stdout", _sanitize_process_output(completed.stdout, replacements))
    store.write_text("fetch.stderr", _sanitize_process_output(completed.stderr, replacements))
    if completed.returncode != 0:
        sanitized_stderr = _sanitize_process_output(completed.stderr, replacements).strip()
        raise RuntimeError(
            "fetch_news.py failed: "
            + (sanitized_stderr or f"exit status {completed.returncode}")
        )
    store.record_artifact("corpus.json")
    try:
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"fetch_news.py wrote invalid JSON: {exc}") from exc
    problems = corpus_schema.validate_corpus(corpus)
    if problems:
        raise RuntimeError("fetched corpus violates its schema: " + "; ".join(problems))
    store.trace(
        "fetch_completed",
        retained_items=sum(len(items) for items in corpus["categories"].values()),
        source_issues=corpus_schema.corpus_health_issue_count(corpus),
    )
    store.checkpoint("corpus_ready")
    return corpus


def _replay_corpus(
    store: RunStore,
    corpus_path: Path,
    corpus_snapshot: bytes,
) -> dict[str, Any]:
    """Validate and archive an existing corpus without performing a live fetch."""
    portable = _portable_path(corpus_path)
    source_sha256 = sha256_bytes(corpus_snapshot)
    store.trace(
        "corpus_replay_started",
        source_path=portable,
        source_sha256=source_sha256,
    )
    try:
        corpus = json.loads(corpus_snapshot)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"replay corpus is invalid JSON: {exc}") from exc
    problems = corpus_schema.validate_corpus(corpus)
    if problems:
        raise RuntimeError("replay corpus violates its schema: " + "; ".join(problems))
    store.write_bytes("corpus.json", corpus_snapshot)
    store.trace(
        "corpus_replay_completed",
        source_path=portable,
        source_sha256=source_sha256,
        retained_items=sum(len(items) for items in corpus["categories"].values()),
        source_issues=corpus_schema.corpus_health_issue_count(corpus),
    )
    store.checkpoint("corpus_ready")
    return corpus


def _load_corpus(store: RunStore) -> dict[str, Any]:
    corpus = json.loads(store.read_verified_text("corpus.json"))
    problems = corpus_schema.validate_corpus(corpus)
    if problems:
        raise ValueError("checkpoint corpus violates its schema: " + "; ".join(problems))
    return corpus


def build_request(
    policy: str,
    config_data: dict[str, Any],
    projected: ModelCorpus,
) -> str:
    safe_config = redact_destinations(config_data)
    return (
        f"{policy.rstrip()}\n\n"
        "--- SELECTION PASS ---\n"
        "Select and group evidence only. Return citation_refs in the required schema; "
        "do not draft headlines, summaries, or exclusion reasons. Each corpus item has "
        "exactly one selectable citation_ref.\n\n"
        "--- TRUSTED BRIEFING CONFIG (JSON) ---\n"
        f"{json.dumps(safe_config, indent=2, ensure_ascii=False)}\n\n"
        "--- UNTRUSTED PROJECTED CORPUS (JSON) ---\n"
        f"{json.dumps(projected.document, indent=2, ensure_ascii=False)}\n"
    )


def build_prose_request(
    policy: str,
    config_data: dict[str, Any],
    selected_evidence: dict[str, Any],
) -> str:
    """Build a prose-only request containing no unselected corpus evidence."""
    safe_config = redact_destinations(config_data)
    return (
        f"{policy.rstrip()}\n\n"
        "--- PROSE PASS ---\n"
        "The evidence for every output position was selected and frozen by code. "
        "Write exactly one headline and summary (or exclusion reason) for each "
        "position, in order, using only that position's evidence. Return no citation "
        "fields and no opaque citation_ or item_ tokens followed by digits, of any "
        "width. Code will attach the "
        "frozen references after this response.\n\n"
        "--- TRUSTED BRIEFING CONFIG (JSON) ---\n"
        f"{json.dumps(safe_config, indent=2, ensure_ascii=False)}\n\n"
        "--- FROZEN POSITION-SCOPED EVIDENCE (JSON) ---\n"
        f"{json.dumps(selected_evidence, indent=2, ensure_ascii=False)}\n"
    )


def correction_request(
    original: str,
    prior_output: dict[str, Any],
    findings: list[dict[str, str]],
    *,
    prose_only: bool = False,
) -> str:
    safe_output = redact_opaque_references(
        redact_destinations(prior_output),
        include_citations=prose_only,
    )
    safe_findings = redact_opaque_references(
        redact_destinations(findings),
        include_citations=prose_only,
    )
    instruction = (
        "Return one complete replacement prose JSON object. Correct every deterministic "
        "finding below without adding citation fields or changing the frozen evidence order."
        if prose_only
        else
        "Return one complete replacement selection JSON object. Correct every deterministic "
        "finding below using only the supplied citation references and without drafting prose."
    )
    return (
        f"{original}\n\n"
        "--- CORRECTION PASS ---\n"
        f"{instruction}\n"
        f"Findings: {json.dumps(safe_findings, ensure_ascii=False)}\n"
        f"Previous structured output: {json.dumps(safe_output, ensure_ascii=False)}\n"
    )
def _finding_records(
    findings: Sequence[OutputFinding | eval_briefing.Finding],
) -> list[dict[str, str]]:
    return [
        {
            "level": finding.level,
            "check": finding.check,
            "domain": finding_domain(finding.check),
            "message": finding.message,
        }
        for finding in findings
    ]


@dataclass(frozen=True)
class DeterministicRepairResult:
    """A candidate repaired by the shared production/evaluator policy."""

    output: dict[str, Any]
    actions: list[dict[str, str]]


def deterministic_repair_candidate(
    output: dict[str, Any],
    findings: Sequence[Mapping[str, str]],
    *,
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
    corpus: dict[str, Any] | None = None,
    selection_only: bool = False,
) -> DeterministicRepairResult | None:
    """Apply the repair decision production makes before a model correction."""
    blocking = [finding for finding in findings if finding.get("level") == "ERROR"]
    repairable_blocking = bool(blocking) and all(
        finding.get("check") in REPAIRABLE_CHECKS for finding in blocking
    )
    claim_repair = not blocking and not selection_only and any(
        finding.get("check") == "claim_exceeds_evidence" for finding in findings
    )
    if not repairable_blocking and not claim_repair:
        return None
    evidence = (
        eval_briefing.corpus_evidence(corpus)
        if corpus is not None and not selection_only
        else None
    )
    repaired, actions = repair_structural_output(
        output,
        config,
        citations,
        evidence=evidence,
    )
    if not actions or not isinstance(repaired, dict):
        return None
    return DeterministicRepairResult(repaired, actions)


def _attempt_paths(index: int) -> tuple[str, str, str, str, str]:
    prefix = f"attempt-{index:02d}"
    return (
        f"{prefix}-raw.txt",
        f"{prefix}-structured.json",
        f"{prefix}-provider-events.jsonl",
        f"{prefix}-briefing.md",
        f"{prefix}-findings.json",
    )


def _deterministic_repair_attempt(
    store: RunStore,
    output: dict[str, Any],
    *,
    corpus: dict[str, Any],
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
    repair: DeterministicRepairResult | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if repair is None:
        evidence = eval_briefing.corpus_evidence(corpus)
        repaired, actions = repair_structural_output(
            output, config, citations, evidence=evidence
        )
    else:
        repaired, actions = repair.output, repair.actions
    current = store.manifest["attempts"][-1]
    if not actions or not isinstance(repaired, dict):
        return current, output

    index = len(store.manifest["attempts"]) + 1
    _raw_name, structured_name, _events_name, _briefing_name, _findings_name = _attempt_paths(index)
    store.write_json(structured_name, repaired)
    attempt = {
        "index": index,
        "kind": "deterministic_repair",
        "received_at": utc_now(),
        "raw_artifact": None,
        "structured_artifact": structured_name,
        "provider_events_artifact": None,
        "generation": None,
        "repair_actions": actions,
        "validated": False,
        "contract_success": None,
        "briefing_artifact": None,
        "findings_artifact": None,
    }
    store.manifest["attempts"].append(attempt)
    store.trace(
        "deterministic_repair_completed",
        attempt=index,
        source_attempt=current["index"],
        actions=len(actions),
    )
    store.checkpoint("deterministic_repair_received")
    _validate_attempt(
        store,
        attempt,
        repaired,
        corpus=corpus,
        config=config,
        citations=citations,
        repair_actions=actions,
    )
    return attempt, repaired


def _deterministic_selection_repair_attempt(
    store: RunStore,
    selection: dict[str, Any],
    *,
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
    repair: DeterministicRepairResult | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if repair is None:
        repaired, actions = repair_structural_output(selection, config, citations)
    else:
        repaired, actions = repair.output, repair.actions
    current = store.manifest["attempts"][-1]
    if not actions or not isinstance(repaired, dict):
        return current, selection

    index = len(store.manifest["attempts"]) + 1
    _raw_name, structured_name, _events_name, _briefing_name, _findings_name = _attempt_paths(index)
    store.write_json(structured_name, repaired)
    attempt = {
        "index": index,
        "kind": "selection_repair",
        "received_at": utc_now(),
        "raw_artifact": None,
        "model_output_artifact": None,
        "structured_artifact": structured_name,
        "provider_events_artifact": None,
        "generation": None,
        "repair_actions": actions,
        "validated": False,
        "contract_success": None,
        "briefing_artifact": None,
        "findings_artifact": None,
    }
    store.manifest["attempts"].append(attempt)
    store.trace(
        "selection_repair_completed",
        attempt=index,
        source_attempt=current["index"],
        actions=len(actions),
    )
    store.checkpoint("selection_repair_received")
    _validate_selection_attempt(
        store,
        attempt,
        repaired,
        config=config,
        citations=citations,
    )
    return attempt, repaired


def _call_provider(
    store: RunStore,
    provider: ModelProvider,
    *,
    prompt: str,
    schema: dict[str, Any],
    timeout_seconds: int,
    kind: str,
    transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    index = len(store.manifest["attempts"]) + 1
    store.trace("provider_call_started", attempt=index, kind=kind, provider=provider.name, model=provider.model)
    store.checkpoint(f"{kind}_call_started")
    response = provider.generate(GenerationRequest(
        prompt=prompt,
        output_schema=schema,
        timeout_seconds=timeout_seconds,
        trace_id=store.manifest["trace_id"],
    ))
    raw_name, structured_name, events_name, _briefing_name, _findings_name = _attempt_paths(index)
    model_output_name = f"attempt-{index:02d}-model-output.json"
    structured_output = (
        transform(response.structured_output)
        if transform is not None
        else response.structured_output
    )
    store.write_text(raw_name, response.raw_output)
    store.write_json(model_output_name, response.structured_output)
    store.write_json(structured_name, structured_output)
    if response.provider_events:
        store.write_text(
            events_name,
            "".join(
                json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n"
                for event in response.provider_events
            ),
        )
    attempt = {
        "index": index,
        "kind": kind,
        "received_at": utc_now(),
        "raw_artifact": raw_name,
        "model_output_artifact": model_output_name,
        "structured_artifact": structured_name,
        "provider_events_artifact": events_name if response.provider_events else None,
        "generation": response.record(),
        "validated": False,
        "contract_success": None,
        "briefing_artifact": None,
        "findings_artifact": None,
    }
    store.manifest["attempts"].append(attempt)
    totals = store.manifest.setdefault("generation_totals", {
        "calls": 0,
        "latency_ms": 0.0,
        "input_tokens": 0,
        "input_token_calls": 0,
        "output_tokens": 0,
        "output_token_calls": 0,
        "cost_usd": 0.0,
        "cost_calls": 0,
        "transport_attempts": 0,
        "by_stage": {},
    })
    stage = totals["by_stage"].setdefault(kind, {
        "calls": 0,
        "latency_ms": 0.0,
        "input_tokens": 0,
        "input_token_calls": 0,
        "output_tokens": 0,
        "output_token_calls": 0,
        "cost_usd": 0.0,
        "cost_calls": 0,
        "transport_attempts": 0,
    })
    for aggregate in (totals, stage):
        aggregate["calls"] += 1
        aggregate["latency_ms"] += response.latency_ms
        aggregate["transport_attempts"] += response.attempts
        if response.input_tokens is not None:
            aggregate["input_tokens"] += response.input_tokens
            aggregate["input_token_calls"] += 1
        if response.output_tokens is not None:
            aggregate["output_tokens"] += response.output_tokens
            aggregate["output_token_calls"] += 1
        if response.cost_usd is not None:
            aggregate["cost_usd"] += response.cost_usd
            aggregate["cost_calls"] += 1
    store.trace(
        "provider_call_completed",
        attempt=index,
        kind=kind,
        latency_ms=response.latency_ms,
        provider_request_id=response.provider_request_id,
        transport_attempts=response.attempts,
    )
    store.checkpoint(f"{kind}_received")
    return structured_output


def _validate_attempt(
    store: RunStore,
    attempt: dict[str, Any],
    output: dict[str, Any],
    *,
    corpus: dict[str, Any],
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
    repair_actions: Sequence[dict[str, str]] = (),
    pre_findings: Sequence[OutputFinding] = (),
) -> list[dict[str, str]]:
    # When prose-stage validation fails, persist those findings once instead
    # of checking the same fields again after attachment. This matches the
    # production-parity evaluator. Complete-output validation remains the
    # independent backstop after the prose-only contract passes.
    structured_findings = list(pre_findings)
    if not any(finding.level == "ERROR" for finding in structured_findings):
        structured_findings.extend(validate_output(output, config, citations))
    rendered: str | None = None
    checker_findings: list[eval_briefing.Finding] = []
    if not any(finding.level == "ERROR" for finding in structured_findings):
        rendered = render_briefing(
            output, corpus, config, citations, repair_actions=repair_actions
        )
        checker_findings = eval_briefing.evaluate(corpus, rendered, config)
    records = _finding_records([*structured_findings, *checker_findings])
    index = attempt["index"]
    _raw_name, _structured_name, _events_name, briefing_name, findings_name = _attempt_paths(index)
    if rendered is not None:
        store.write_text(briefing_name, rendered)
        attempt["briefing_artifact"] = briefing_name
    store.write_json(findings_name, records)
    attempt["findings_artifact"] = findings_name
    attempt["validated"] = True
    attempt["contract_success"] = not any(row["level"] == "ERROR" for row in records)
    store.trace(
        "candidate_validated",
        attempt=index,
        errors=sum(row["level"] == "ERROR" for row in records),
        warnings=sum(row["level"] == "WARN" for row in records),
    )
    store.checkpoint(f"attempt_{index}_validated")
    return records


def _validate_selection_attempt(
    store: RunStore,
    attempt: dict[str, Any],
    selection: dict[str, Any],
    *,
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
) -> list[dict[str, str]]:
    selection_findings = validate_selection(selection, config, citations)
    allocation_findings: list[OutputFinding] = []
    if not any(finding.level == "ERROR" for finding in selection_findings):
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
            actual = len(selection["sections"][section.name]["topics"])
            if actual:
                continue
            unused_eligible = {
                citation.item_ref
                for citation in citations.values()
                if citation.category in section.corpus_categories
                and citation.item_ref not in used_items
            }
            if unused_eligible:
                allocation_findings.append(OutputFinding(
                    "ERROR",
                    "slots_underfilled",
                    f"{section.name}: 0 topics, expected {section.target_stories}; "
                    f"{len(unused_eligible)} unused eligible corpus item(s) remain",
                ))
    records = _finding_records([*selection_findings, *allocation_findings])
    index = attempt["index"]
    _raw_name, _structured_name, _events_name, _briefing_name, findings_name = _attempt_paths(index)
    store.write_json(findings_name, records)
    attempt["findings_artifact"] = findings_name
    attempt["validated"] = True
    attempt["contract_success"] = not any(row["level"] == "ERROR" for row in records)
    store.trace(
        "selection_validated",
        attempt=index,
        errors=sum(row["level"] == "ERROR" for row in records),
        warnings=sum(row["level"] == "WARN" for row in records),
    )
    store.checkpoint(f"attempt_{index}_validated")
    return records


def _corrections_used(store: RunStore, kind: str) -> int:
    return sum(
        attempt.get("kind") == kind
        for attempt in store.manifest["attempts"]
    )


def _finalize_selection_preview(
    store: RunStore,
    attempt: dict[str, Any],
    selection: dict[str, Any],
    *,
    corpus: dict[str, Any],
    settings: RunnerSettings,
) -> RunResult:
    findings = json.loads(
        (store.root / attempt["findings_artifact"]).read_text(encoding="utf-8")
    )
    outcome = classify_outcome(
        findings,
        corpus.get("errors", []),
        coverage_degraded=corpus_schema.corpus_health_degraded(corpus),
    )
    safe_selection = redact_preview_value(selection)
    store.write_json("preview-structured.json", safe_selection)
    preview_path = store.write_text(
        "preview.md",
        "# Evidence selection rejected\n\n"
        "No prose was generated because the evidence-selection pass did not satisfy "
        "the deterministic contract. See `preview-structured.json` and the findings "
        "artifact for details.\n\n"
        "```json\n"
        f"{json.dumps(safe_selection, indent=2, ensure_ascii=False)}\n"
        "```\n",
    )
    final = {
        "status": outcome.disposition,
        "outcome": outcome.record(),
        "attempt": attempt["index"],
        "findings": findings,
        "source_issues": corpus_schema.corpus_health_issue_count(corpus),
        "artifact_type": "preview",
        "run_artifact": preview_path.name,
        "requested_output_path": _portable_path(settings.output_path),
        "output_path": None,
        "output_sha256": None,
    }
    store.finalize(final)
    return RunResult(1, store.root, preview_path, outcome.disposition)


def _checker_fingerprint(findings: list[eval_briefing.Finding]) -> list[tuple[str, str, str]]:
    return [(finding.level, finding.check, finding.message) for finding in findings]


def _finalize_candidate(
    store: RunStore,
    attempt: dict[str, Any],
    *,
    corpus: dict[str, Any],
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
    settings: RunnerSettings,
) -> RunResult:
    briefing_name = attempt.get("briefing_artifact")
    if not isinstance(briefing_name, str):
        raise RuntimeError("the final structured candidate could not be rendered")
    briefing = (store.root / briefing_name).read_text(encoding="utf-8")
    findings = eval_briefing.evaluate(corpus, briefing, config)
    outcome = classify_outcome(
        findings, corpus.get("errors", []),
        coverage_degraded=corpus_schema.corpus_health_degraded(corpus))
    completed = briefing.rstrip() + "\n" + render_validation_status(
        findings, corpus, outcome=outcome
    )
    after = eval_briefing.evaluate(corpus, completed, config)
    if _checker_fingerprint(after) != _checker_fingerprint(findings):
        outcome = classify_outcome(
            after, corpus.get("errors", []),
            coverage_degraded=corpus_schema.corpus_health_degraded(corpus))
        completed = briefing.rstrip() + "\n" + render_validation_status(
            after, corpus, outcome=outcome
        )
        stabilized = eval_briefing.evaluate(corpus, completed, config)
        if _checker_fingerprint(stabilized) != _checker_fingerprint(after):
            raise RuntimeError("validation status did not stabilize after two completed-briefing checks")
        findings = stabilized
    else:
        findings = after
    outcome = classify_outcome(
        findings, corpus.get("errors", []),
        coverage_degraded=corpus_schema.corpus_health_degraded(corpus))
    if outcome.disposition == "ready":
        store.write_text("briefing.md", completed)
        run_path = store.write_text("final.md", completed)
        write_text_atomic(settings.output_path, completed)
        output_path: Path | None = settings.output_path
        output_sha256: str | None = sha256_file(settings.output_path)
        artifact_type = "final"
    else:
        output = json.loads(
            (store.root / attempt["structured_artifact"]).read_text(encoding="utf-8")
        )
        preview = render_candidate_preview(
            output,
            corpus,
            config,
            citations,
            findings,
            outcome,
        )
        run_path = store.write_text("preview.md", preview)
        output_path = run_path
        output_sha256 = None
        artifact_type = "preview"
    final = {
        "status": outcome.disposition,
        "outcome": outcome.record(),
        "attempt": attempt["index"],
        "findings": _finding_records(findings),
        "source_issues": corpus_schema.corpus_health_issue_count(corpus),
        "artifact_type": artifact_type,
        "run_artifact": run_path.name,
        "requested_output_path": _portable_path(settings.output_path),
        "output_path": _portable_path(settings.output_path) if artifact_type == "final" else None,
        "output_sha256": output_sha256,
    }
    store.finalize(final)
    failed = outcome.disposition != "ready" or (
        settings.strict and bool(findings or corpus_schema.corpus_health_degraded(corpus))
    )
    return RunResult(1 if failed else 0, store.root, output_path, outcome.disposition)


def _finalize_structured_preview(
    store: RunStore,
    attempt: dict[str, Any],
    *,
    corpus: dict[str, Any],
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
    settings: RunnerSettings,
) -> RunResult:
    output = json.loads(
        (store.root / attempt["structured_artifact"]).read_text(encoding="utf-8")
    )
    findings = json.loads(
        (store.root / attempt["findings_artifact"]).read_text(encoding="utf-8")
    )
    outcome = classify_outcome(
        findings, corpus.get("errors", []),
        coverage_degraded=corpus_schema.corpus_health_degraded(corpus))
    if outcome.disposition == "ready":
        raise RuntimeError("an invalid structured candidate was unexpectedly classified as ready")
    store.write_json("preview-structured.json", redact_preview_value(output))
    preview = render_candidate_preview(
        output,
        corpus,
        config,
        citations,
        findings,
        outcome,
    )
    preview_path = store.write_text("preview.md", preview)
    final = {
        "status": outcome.disposition,
        "outcome": outcome.record(),
        "attempt": attempt["index"],
        "findings": findings,
        "source_issues": corpus_schema.corpus_health_issue_count(corpus),
        "artifact_type": "preview",
        "run_artifact": preview_path.name,
        "requested_output_path": _portable_path(settings.output_path),
        "output_path": None,
        "output_sha256": None,
    }
    store.finalize(final)
    return RunResult(1, store.root, preview_path, outcome.disposition)


def _finalize_after_deterministic_repair(
    store: RunStore,
    attempt: dict[str, Any],
    output: dict[str, Any],
    *,
    corpus: dict[str, Any],
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
    settings: RunnerSettings,
) -> RunResult:
    if attempt.get("kind") != "deterministic_repair":
        attempt, output = _deterministic_repair_attempt(
            store,
            output,
            corpus=corpus,
            config=config,
            citations=citations,
        )
    if attempt.get("briefing_artifact"):
        return _finalize_candidate(
            store,
            attempt,
            corpus=corpus,
            config=config,
            citations=citations,
            settings=settings,
        )
    return _finalize_structured_preview(
        store,
        attempt,
        corpus=corpus,
        config=config,
        citations=citations,
        settings=settings,
    )


def run_workflow(
    provider: ModelProvider,
    settings: RunnerSettings,
    run_dir: Path,
    *,
    resume: bool = False,
) -> RunResult:
    """Run or resume one complete briefing workflow."""
    corpus_snapshot = (
        settings.corpus_path.read_bytes()
        if settings.corpus_path is not None
        else None
    )
    provider_info = provider.info()
    code_info = _git_provenance()
    identity = settings.identity(
        provider_info,
        code_info,
        corpus_snapshot=corpus_snapshot,
    )
    store = (
        RunStore.resume(run_dir, identity=identity)
        if resume
        else RunStore.create(
            run_dir,
            identity=identity,
            provider=provider_info,
            code=code_info,
        )
    )
    corpus: dict[str, Any] | None = None
    try:
        corpus = (
            _load_corpus(store)
            if (store.root / "corpus.json").is_file()
            else (
                _replay_corpus(store, settings.corpus_path, corpus_snapshot)
                if settings.corpus_path is not None and corpus_snapshot is not None
                else _fetch_corpus(store, settings)
            )
        )
        config_data = json.loads(settings.config_path.read_text(encoding="utf-8"))
        config = briefing_config.parse_config(config_data)
        recorded_artifacts = store.manifest.get("artifacts", {})
        if not isinstance(recorded_artifacts, dict) or "briefing-config.json" not in recorded_artifacts:
            store.write_json("briefing-config.json", config_data)
            store.checkpoint("config_ready")
        category_problems = briefing_config.validate_corpus_categories(
            config, set(corpus["categories"])
        )
        if category_problems:
            raise ValueError("; ".join(category_problems))
        projected = project_corpus(corpus)
        selection_schema = build_selection_schema(config, projected.citations)
        retained_items = sum(len(items) for items in corpus["categories"].values())
        store.manifest["citation_cardinality"] = {
            "retained_items": retained_items,
            "model_visible_handles": len(projected.citations),
            "selected_items": None,
        }
        if not (store.root / "model-corpus.json").exists():
            store.write_json("model-corpus.json", projected.document)
            store.write_json(
                "citation-map.json",
                {ref: citation.__dict__ for ref, citation in projected.citations.items()},
            )
            store.write_json("selection-schema.json", selection_schema)
            store.checkpoint("request_ready")
        policy = settings.prompt_path.read_text(encoding="utf-8")
        selection_request = build_request(policy, config_data, projected)
        if not (store.root / "selection-request.txt").exists():
            store.write_text("selection-request.txt", selection_request)
            store.checkpoint("request_ready")

        frozen_path = store.root / "frozen-selection.json"
        if frozen_path.is_file():
            selection = json.loads(store.read_verified_text("frozen-selection.json"))
            selected_refs = {
                ref
                for section in selection["sections"].values()
                for entry in section["topics"]
                for ref in entry["citation_refs"]
            } | {
                ref
                for entries in selection["excluded_topics"].values()
                for entry in entries
                for ref in entry["citation_refs"]
            }
            store.manifest["citation_cardinality"]["selected_items"] = len(selected_refs)
        else:
            selection_attempts = [
                attempt for attempt in store.manifest["attempts"]
                if attempt.get("kind") in {
                    "selection", "selection_correction", "selection_repair"
                }
            ]
            if not selection_attempts:
                selection = _call_provider(
                    store,
                    provider,
                    prompt=selection_request,
                    schema=selection_schema,
                    timeout_seconds=settings.timeout_seconds,
                    kind="selection",
                )
            else:
                selection_attempt = selection_attempts[-1]
                selection = json.loads(
                    (store.root / selection_attempt["structured_artifact"]).read_text(
                        encoding="utf-8"
                    )
                )

            while True:
                selection_attempt = store.manifest["attempts"][-1]
                if selection_attempt.get("kind") not in {
                    "selection", "selection_correction", "selection_repair"
                }:
                    raise RuntimeError("prose generation started before evidence was frozen")
                if selection_attempt["validated"]:
                    selection_findings = json.loads(
                        (store.root / selection_attempt["findings_artifact"]).read_text(
                            encoding="utf-8"
                        )
                    )
                else:
                    selection_findings = _validate_selection_attempt(
                        store,
                        selection_attempt,
                        selection,
                        config=config,
                        citations=projected.citations,
                    )
                if selection_attempt["contract_success"]:
                    selected_refs = {
                        ref
                        for section in selection["sections"].values()
                        for entry in section["topics"]
                        for ref in entry["citation_refs"]
                    } | {
                        ref
                        for entries in selection["excluded_topics"].values()
                        for entry in entries
                        for ref in entry["citation_refs"]
                    }
                    store.write_json("frozen-selection.json", selection)
                    store.manifest["citation_cardinality"]["selected_items"] = len(selected_refs)
                    store.trace(
                        "evidence_selection_frozen",
                        attempt=selection_attempt["index"],
                        retained_items=retained_items,
                        model_visible_handles=len(projected.citations),
                        selected_items=len(selected_refs),
                    )
                    store.checkpoint("selection_frozen")
                    break
                selection_repair = deterministic_repair_candidate(
                    selection,
                    selection_findings,
                    config=config,
                    citations=projected.citations,
                    selection_only=True,
                )
                if (
                    selection_repair is not None
                    and selection_attempt.get("kind") != "selection_repair"
                ):
                    repair_attempt, selection = _deterministic_selection_repair_attempt(
                        store,
                        selection,
                        config=config,
                        citations=projected.citations,
                        repair=selection_repair,
                    )
                    if repair_attempt is not selection_attempt:
                        continue
                if (
                    _corrections_used(store, "selection_correction")
                    >= settings.max_corrections
                ):
                    return _finalize_selection_preview(
                        store,
                        selection_attempt,
                        selection,
                        corpus=corpus,
                        settings=settings,
                    )
                correction = correction_request(
                    selection_request,
                    selection,
                    selection_findings,
                )
                try:
                    selection = _call_provider(
                        store,
                        provider,
                        prompt=correction,
                        schema=selection_schema,
                        timeout_seconds=settings.timeout_seconds,
                        kind="selection_correction",
                    )
                except ProviderError as exc:
                    store.manifest["correction_error"] = exc.record()
                    store.trace("selection_correction_failed", **exc.record())
                    store.checkpoint("selection_correction_failed")
                    return _finalize_selection_preview(
                        store,
                        selection_attempt,
                        selection,
                        corpus=corpus,
                        settings=settings,
                    )

        selected_evidence = project_selected_evidence(selection, projected)
        prose_schema = build_prose_schema(config, selection)
        prose_request = build_prose_request(policy, config_data, selected_evidence)
        if not (store.root / "selected-evidence.json").exists():
            store.write_json("selected-evidence.json", selected_evidence)
            store.write_json("prose-schema.json", prose_schema)
            store.write_text("prose-request.txt", prose_request)
            store.checkpoint("prose_request_ready")

        prose_attempts = [
            attempt for attempt in store.manifest["attempts"]
            if attempt.get("kind") not in {
                "selection", "selection_correction", "selection_repair"
            }
        ]
        if not prose_attempts:
            output = _call_provider(
                store,
                provider,
                prompt=prose_request,
                schema=prose_schema,
                timeout_seconds=settings.timeout_seconds,
                kind="prose",
                transform=lambda prose: attach_frozen_selection(selection, prose, config),
            )
        else:
            last = prose_attempts[-1]
            output = json.loads(
                (store.root / last["structured_artifact"]).read_text(encoding="utf-8")
            )

        while True:
            attempt = store.manifest["attempts"][-1]
            if attempt["validated"]:
                findings = json.loads(
                    (store.root / attempt["findings_artifact"]).read_text(encoding="utf-8")
                )
            else:
                prose_findings: Sequence[OutputFinding] = ()
                if attempt.get("kind") in {"prose", "correction"}:
                    model_output = json.loads(
                        (store.root / attempt["model_output_artifact"]).read_text(
                            encoding="utf-8"
                        )
                    )
                    prose_findings = validate_prose_output(
                        model_output, config, selection
                    )
                findings = _validate_attempt(
                    store,
                    attempt,
                    output,
                    corpus=corpus,
                    config=config,
                    citations=projected.citations,
                    # A resumed repair attempt validated here must render its
                    # recorded swap markers exactly as the unresumed run would.
                    repair_actions=attempt.get("repair_actions") or (),
                    pre_findings=prose_findings,
                )
            if attempt["contract_success"]:
                # ``claim_exceeds_evidence`` is a WARN, so the candidate is
                # contract-clean — but finalizing now would classify it
                # review_required and withhold the briefing. The same
                # deterministic repair that handles blocking errors swaps the
                # oversized summary for its cited excerpt, so run it before
                # finalizing. The kind guard stops a repair whose swap was
                # declined (for example a URL-bearing excerpt) from looping;
                # it falls through and finalizes for review as before.
                deterministic_repair = deterministic_repair_candidate(
                    output,
                    findings,
                    corpus=corpus,
                    config=config,
                    citations=projected.citations,
                )
                if (
                    deterministic_repair is not None
                    and attempt.get("kind") != "deterministic_repair"
                ):
                    repair_attempt, output = _deterministic_repair_attempt(
                        store,
                        output,
                        corpus=corpus,
                        config=config,
                        citations=projected.citations,
                        repair=deterministic_repair,
                    )
                    if repair_attempt is not attempt:
                        continue
                return _finalize_candidate(
                    store,
                    attempt,
                    corpus=corpus,
                    config=config,
                    citations=projected.citations,
                    settings=settings,
                )
            # Editorial placement errors are repaired deterministically by
            # construction, so spending a model correction on them wastes budget
            # and adds a provider round-trip. When every blocking finding is
            # repairable, run repair now and re-enter the loop: the repaired
            # attempt finalizes when clean, and the untouched correction budget
            # stays available for findings repair cannot fix (unknown refs,
            # freeform URLs, schema shape, checker errors on the rendered
            # briefing). The kind guard keeps an unproductive repair from
            # re-entering this branch.
            deterministic_repair = deterministic_repair_candidate(
                output,
                findings,
                corpus=corpus,
                config=config,
                citations=projected.citations,
            )
            if (
                deterministic_repair is not None
                and attempt.get("kind") != "deterministic_repair"
            ):
                repair_attempt, output = _deterministic_repair_attempt(
                    store,
                    output,
                    corpus=corpus,
                    config=config,
                    citations=projected.citations,
                    repair=deterministic_repair,
                )
                if repair_attempt is not attempt:
                    continue
            if _corrections_used(store, "correction") >= settings.max_corrections:
                return _finalize_after_deterministic_repair(
                    store,
                    attempt,
                    output,
                    corpus=corpus,
                    config=config,
                    citations=projected.citations,
                    settings=settings,
                )
            try:
                correction = correction_request(
                    prose_request,
                    detach_prose(output, config),
                    findings,
                    prose_only=True,
                )
            except ValueError as exc:
                # Building the correction prompt redacts destinations out of the
                # prior output; a destination-bearing dict key raises here. That
                # is a fail-closed signal, not a reason to abort the whole run —
                # finalize the candidate as a quarantined preview instead of
                # letting the ValueError escape to the generic failure path.
                store.manifest["correction_error"] = {
                    "type": type(exc).__name__, "message": str(exc)}
                store.trace("correction_skipped", type=type(exc).__name__, message=str(exc))
                store.checkpoint("correction_skipped")
                return _finalize_after_deterministic_repair(
                    store,
                    attempt,
                    output,
                    corpus=corpus,
                    config=config,
                    citations=projected.citations,
                    settings=settings,
                )
            try:
                output = _call_provider(
                    store,
                    provider,
                    prompt=correction,
                    schema=prose_schema,
                    timeout_seconds=settings.timeout_seconds,
                    kind="correction",
                    transform=lambda prose: attach_frozen_selection(selection, prose, config),
                )
            except ProviderError as exc:
                store.manifest["correction_error"] = exc.record()
                store.trace("correction_failed", **exc.record())
                store.checkpoint("correction_failed")
                return _finalize_after_deterministic_repair(
                    store,
                    attempt,
                    output,
                    corpus=corpus,
                    config=config,
                    citations=projected.citations,
                    settings=settings,
                )
    except Exception as exc:
        error = (
            exc.record()
            if isinstance(exc, ProviderError)
            else {"type": type(exc).__name__, "message": str(exc)}
        )
        source_issues = corpus.get("errors", []) if corpus is not None else []
        outcome = classify_outcome(
            [], source_issues, protocol_completed=False,
            coverage_degraded=(corpus_schema.corpus_health_degraded(corpus)
                               if corpus is not None else False))
        store.fail(error, outcome=outcome.record())
        raise
