"""Code-owned fetch, generation, validation, correction, and finalization loop."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from collections.abc import Sequence
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
    Citation,
    ModelCorpus,
    OutputFinding,
    build_output_schema,
    project_corpus,
    redact_destinations,
    redact_preview_value,
    render_briefing,
    render_candidate_preview,
    render_validation_status,
    validate_output,
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
        raise RuntimeError(
            "fetch_news.py failed: " + (completed.stderr.strip() or f"exit status {completed.returncode}")
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
        source_issues=len(corpus["errors"]),
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
        source_issues=len(corpus["errors"]),
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
        "--- TRUSTED BRIEFING CONFIG (JSON) ---\n"
        f"{json.dumps(safe_config, indent=2, ensure_ascii=False)}\n\n"
        "--- UNTRUSTED PROJECTED CORPUS (JSON) ---\n"
        f"{json.dumps(projected.document, indent=2, ensure_ascii=False)}\n"
    )


def correction_request(
    original: str,
    prior_output: dict[str, Any],
    findings: list[dict[str, str]],
) -> str:
    safe_output = redact_destinations(prior_output)
    safe_findings = redact_destinations(findings)
    return (
        f"{original}\n\n"
        "--- CORRECTION PASS ---\n"
        "Return one complete replacement JSON object. Correct every deterministic finding below while "
        "preserving grounded editorial content and using only the supplied citation references.\n"
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


def _attempt_paths(index: int) -> tuple[str, str, str, str, str]:
    prefix = f"attempt-{index:02d}"
    return (
        f"{prefix}-raw.txt",
        f"{prefix}-structured.json",
        f"{prefix}-provider-events.jsonl",
        f"{prefix}-briefing.md",
        f"{prefix}-findings.json",
    )


def _call_provider(
    store: RunStore,
    provider: ModelProvider,
    *,
    prompt: str,
    schema: dict[str, Any],
    timeout_seconds: int,
    kind: str,
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
    store.write_text(raw_name, response.raw_output)
    store.write_json(structured_name, response.structured_output)
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
        "structured_artifact": structured_name,
        "provider_events_artifact": events_name if response.provider_events else None,
        "generation": response.record(),
        "validated": False,
        "contract_success": None,
        "briefing_artifact": None,
        "findings_artifact": None,
    }
    store.manifest["attempts"].append(attempt)
    store.trace(
        "provider_call_completed",
        attempt=index,
        kind=kind,
        latency_ms=response.latency_ms,
        provider_request_id=response.provider_request_id,
        transport_attempts=response.attempts,
    )
    store.checkpoint(f"{kind}_received")
    return response.structured_output


def _validate_attempt(
    store: RunStore,
    attempt: dict[str, Any],
    output: dict[str, Any],
    *,
    corpus: dict[str, Any],
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
) -> list[dict[str, str]]:
    structured_findings = validate_output(output, config, citations)
    rendered: str | None = None
    checker_findings: list[eval_briefing.Finding] = []
    if not any(finding.level == "ERROR" for finding in structured_findings):
        rendered = render_briefing(output, corpus, config, citations)
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
    outcome = classify_outcome(findings, corpus.get("errors", []))
    completed = briefing.rstrip() + "\n" + render_validation_status(
        findings, corpus, outcome=outcome
    )
    after = eval_briefing.evaluate(corpus, completed, config)
    if _checker_fingerprint(after) != _checker_fingerprint(findings):
        outcome = classify_outcome(after, corpus.get("errors", []))
        completed = briefing.rstrip() + "\n" + render_validation_status(
            after, corpus, outcome=outcome
        )
        stabilized = eval_briefing.evaluate(corpus, completed, config)
        if _checker_fingerprint(stabilized) != _checker_fingerprint(after):
            raise RuntimeError("validation status did not stabilize after two completed-briefing checks")
        findings = stabilized
    else:
        findings = after
    outcome = classify_outcome(findings, corpus.get("errors", []))
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
        "source_issues": len(corpus.get("errors", [])),
        "artifact_type": artifact_type,
        "run_artifact": run_path.name,
        "requested_output_path": _portable_path(settings.output_path),
        "output_path": _portable_path(settings.output_path) if artifact_type == "final" else None,
        "output_sha256": output_sha256,
    }
    store.finalize(final)
    failed = outcome.disposition != "ready" or (
        settings.strict and bool(findings or corpus.get("errors"))
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
    outcome = classify_outcome(findings, corpus.get("errors", []))
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
        "source_issues": len(corpus.get("errors", [])),
        "artifact_type": "preview",
        "run_artifact": preview_path.name,
        "requested_output_path": _portable_path(settings.output_path),
        "output_path": None,
        "output_sha256": None,
    }
    store.finalize(final)
    return RunResult(1, store.root, preview_path, outcome.disposition)


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
        schema = build_output_schema(config)
        if not (store.root / "model-corpus.json").exists():
            store.write_json("model-corpus.json", projected.document)
            store.write_json(
                "citation-map.json",
                {ref: citation.__dict__ for ref, citation in projected.citations.items()},
            )
            store.write_json("output-schema.json", schema)
            store.checkpoint("request_ready")
        policy = settings.prompt_path.read_text(encoding="utf-8")
        original_request = build_request(policy, config_data, projected)
        if not (store.root / "request.txt").exists():
            store.write_text("request.txt", original_request)
            store.checkpoint("request_ready")

        if not store.manifest["attempts"]:
            output = _call_provider(
                store,
                provider,
                prompt=original_request,
                schema=schema,
                timeout_seconds=settings.timeout_seconds,
                kind="initial",
            )
        else:
            last = store.manifest["attempts"][-1]
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
                findings = _validate_attempt(
                    store,
                    attempt,
                    output,
                    corpus=corpus,
                    config=config,
                    citations=projected.citations,
                )
            if attempt["contract_success"]:
                return _finalize_candidate(
                    store,
                    attempt,
                    corpus=corpus,
                    config=config,
                    citations=projected.citations,
                    settings=settings,
                )
            corrections_used = sum(row["kind"] == "correction" for row in store.manifest["attempts"])
            if corrections_used >= settings.max_corrections:
                if attempt.get("briefing_artifact"):
                    return _finalize_candidate(
                        store,
                        attempt,
                        corpus=corpus,
                        config=config,
                        citations=projected.citations,
                        settings=settings,
                    )
                return _finalize_structured_preview(
                    store,
                    attempt,
                    corpus=corpus,
                    config=config,
                    citations=projected.citations,
                    settings=settings,
                )
            correction = correction_request(original_request, output, findings)
            try:
                output = _call_provider(
                    store,
                    provider,
                    prompt=correction,
                    schema=schema,
                    timeout_seconds=settings.timeout_seconds,
                    kind="correction",
                )
            except ProviderError as exc:
                store.manifest["correction_error"] = exc.record()
                store.trace("correction_failed", **exc.record())
                store.checkpoint("correction_failed")
                if attempt.get("briefing_artifact"):
                    return _finalize_candidate(
                        store,
                        attempt,
                        corpus=corpus,
                        config=config,
                        citations=projected.citations,
                        settings=settings,
                    )
                return _finalize_structured_preview(
                    store,
                    attempt,
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
        outcome = classify_outcome([], source_issues, protocol_completed=False)
        store.fail(error, outcome=outcome.record())
        raise
