#!/usr/bin/env python3
"""Run the production briefing through its ordered OpenRouter fallback chain."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import briefing_config
import fetch_news
from agent_runner.checkpoint import sha256_file, utc_now, write_json_atomic, write_text_atomic
from agent_runner.models import ProviderError
from agent_runner.providers import provider_for
from agent_runner.runner import ROOT, RunnerSettings, RunResult, run_workflow


@dataclass(frozen=True)
class ModelCandidate:
    model: str
    temperature: float
    reasoning_effort: str | None


PRODUCTION_MODEL_CHAIN = (
    ModelCandidate("tencent/hy3", 0.2, "high"),
    ModelCandidate("deepseek/deepseek-v4-flash-0731", 0.2, "high"),
    # MiMo supports reasoning, but OpenRouter does not advertise reasoning_effort
    # for this endpoint. Omitting the effort keeps require_parameters fail-closed.
    ModelCandidate("xiaomi/mimo-v2.5", 0.2, None),
)

LOG_NAME = "fallback-log.json"
TEXT_LOG_NAME = "fallback.log"
OPENROUTER_MODELS_ENDPOINT = "https://openrouter.ai/api/v1/models"


@dataclass(frozen=True)
class ChainResult:
    status: str
    selected_model: str | None
    selected_run_dir: Path | None
    run_dir: Path


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _candidate_dir_name(index: int, model: str) -> str:
    safe_model = "".join(character if character.isalnum() else "-" for character in model)
    return f"{index:02d}-{safe_model.strip('-')}"


def _load_manifest(run_dir: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _manifest_error(manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    error = manifest.get("error") if manifest is not None else None
    return error if isinstance(error, dict) else None


def _failure_reason(result: RunResult | None, manifest: dict[str, Any] | None, exc: Exception | None) -> str:
    error = _manifest_error(manifest)
    if error is not None:
        error_type = error.get("type")
        message = error.get("message")
        if isinstance(error_type, str) and isinstance(message, str):
            return f"{error_type}: {message}"
        if isinstance(message, str):
            return message
    if exc is not None:
        return f"{type(exc).__name__}: {exc}"
    final = manifest.get("final") if manifest is not None else None
    findings = final.get("findings") if isinstance(final, dict) else None
    details: list[str] = []
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            check = finding.get("check")
            message = finding.get("message")
            if isinstance(check, str) and isinstance(message, str):
                details.append(f"{check}: {message}")
    status = result.status if result is not None else "failed"
    if status == "ready":
        return "ready result failed final artifact integrity checks"
    return f"{status}: " + ("; ".join(details) if details else "no ready report was produced")


def _openrouter_model_404(manifest: dict[str, Any] | None, exc: Exception | None) -> bool:
    if isinstance(exc, ProviderError) and exc.openrouter_model_404:
        return True
    error = _manifest_error(manifest)
    return bool(error and error.get("openrouter_model_404") is True)


def _catalog_model_removed_from_openrouter(model: str) -> bool | None:
    """Return exact catalog absence after a 404, or None when it cannot be checked."""
    request = urllib.request.Request(
        OPENROUTER_MODELS_ENDPOINT,
        headers={"User-Agent": "news-briefing/model-availability-check"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError):
        return None
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    model_ids = {
        row.get("id")
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    return model not in model_ids


def _model_removed(
    model: str,
    manifest: dict[str, Any] | None,
    exc: Exception | None,
) -> bool | None:
    if not _openrouter_model_404(manifest, exc):
        return False
    return _catalog_model_removed_from_openrouter(model)


def _removal_label(removed: bool | None) -> str:
    if removed is None:
        return "unknown"
    return str(removed).lower()


def _is_publishable_ready_result(
    result: RunResult | None,
    manifest: dict[str, Any] | None,
    candidate_dir: Path,
    output_path: Path,
) -> bool:
    if result is None or result.status != "ready" or manifest is None:
        return False
    final = manifest.get("final")
    artifacts = manifest.get("artifacts")
    if (
        manifest.get("status") != "complete"
        or not isinstance(final, dict)
        or final.get("status") != "ready"
        or final.get("artifact_type") != "final"
        or final.get("run_artifact") != "final.md"
        or not isinstance(artifacts, dict)
    ):
        return False
    expected_run_hash = artifacts.get("final.md")
    expected_output_hash = final.get("output_sha256")
    run_artifact = candidate_dir / "final.md"
    try:
        return (
            isinstance(expected_run_hash, str)
            and isinstance(expected_output_hash, str)
            and run_artifact.is_file()
            and output_path.is_file()
            and sha256_file(run_artifact) == expected_run_hash
            and sha256_file(output_path) == expected_output_hash
        )
    except OSError:
        return False


def _quarantined_report(
    root: Path,
    candidate_dir: Path,
    candidate: ModelCandidate,
    result: RunResult | None,
    reason: str,
    removed: bool | None,
) -> str:
    if result is not None and result.output_path is not None and result.output_path.is_file():
        try:
            return result.output_path.relative_to(root).as_posix()
        except ValueError:
            pass
    candidate_dir.mkdir(parents=True, exist_ok=True)
    report = candidate_dir / "failure.md"
    removal = "unknown" if removed is None else ("yes" if removed else "no")
    diagnostic_note = (
        "See `manifest.json` and `trace.jsonl` in this directory for the provider and "
        "checkpoint diagnostics."
        if (candidate_dir / "manifest.json").is_file()
        else "The failure occurred before a run manifest could be initialized."
    )
    write_text_atomic(
        report,
        "# Briefing generation failure\n\n"
        f"- Model: `{candidate.model}`\n"
        f"- Model removed from OpenRouter: `{removal}`\n"
        f"- Failure reason: {reason}\n\n"
        f"No renderable candidate report was produced. {diagnostic_note}\n",
    )
    return report.relative_to(root).as_posix()


def _write_chain_logs(root: Path, started_at: str, attempts: list[dict[str, Any]]) -> None:
    selected = next((row for row in attempts if row["status"] == "ready"), None)
    payload = {
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": utc_now(),
        "status": "ready" if selected is not None else "failed",
        "model_chain": [candidate.model for candidate in PRODUCTION_MODEL_CHAIN],
        "selected_model": selected["model"] if selected is not None else None,
        "selected_run_dir": selected["run_dir"] if selected is not None else None,
        "attempts": attempts,
    }
    write_json_atomic(root / LOG_NAME, payload)
    lines = []
    for row in attempts:
        line = (
            f"{row['completed_at']} status={row['status']} model={row['model']} "
            f"run_dir={row['run_dir']}"
        )
        if row["failure_reason"] is not None:
            line += (
                f" model_removed_from_openrouter={_removal_label(row['model_removed_from_openrouter'])}"
                f" failure_reason={json.dumps(row['failure_reason'], ensure_ascii=False)}"
                f" quarantined_report={row['quarantined_report']}"
            )
        lines.append(line)
    write_text_atomic(root / TEXT_LOG_NAME, "\n".join(lines) + "\n")


def run_fallback_chain(
    settings: RunnerSettings,
    run_dir: Path,
    *,
    max_tokens: int,
) -> ChainResult:
    """Run each production model until one produces a ready briefing."""
    run_dir.mkdir(parents=True, exist_ok=False)
    started_at = utc_now()
    attempts: list[dict[str, Any]] = []
    for index, candidate in enumerate(PRODUCTION_MODEL_CHAIN, start=1):
        candidate_name = _candidate_dir_name(index, candidate.model)
        candidate_dir = run_dir / candidate_name
        started = utc_now()
        result: RunResult | None = None
        failure: Exception | None = None
        try:
            provider = provider_for(
                "openrouter",
                candidate.model,
                temperature=candidate.temperature,
                reasoning_enabled=True,
                reasoning_effort=candidate.reasoning_effort,
                max_tokens=max_tokens,
            )
            result = run_workflow(provider, settings, candidate_dir)
        except Exception as exc:  # every failed production attempt advances the chain
            failure = exc

        manifest = _load_manifest(candidate_dir)
        completed_at = utc_now()
        if _is_publishable_ready_result(result, manifest, candidate_dir, settings.output_path):
            row = {
                "index": index,
                "model": candidate.model,
                "started_at": started,
                "completed_at": completed_at,
                "status": "ready",
                "run_dir": candidate_name,
                "failure_reason": None,
                "model_removed_from_openrouter": False,
                "quarantined_report": None,
            }
            attempts.append(row)
            _write_chain_logs(run_dir, started_at, attempts)
            print(f"READY model={candidate.model} run_dir={candidate_name}")
            return ChainResult("ready", candidate.model, candidate_dir, run_dir)

        reason = _failure_reason(result, manifest, failure)
        removed = _model_removed(candidate.model, manifest, failure)
        quarantined_report = _quarantined_report(
            run_dir, candidate_dir, candidate, result, reason, removed
        )
        row = {
            "index": index,
            "model": candidate.model,
            "started_at": started,
            "completed_at": completed_at,
            "status": "quarantined" if result is not None else "failed",
            "run_dir": candidate_name,
            "failure_reason": reason,
            "model_removed_from_openrouter": removed,
            "quarantined_report": quarantined_report,
        }
        attempts.append(row)
        _write_chain_logs(run_dir, started_at, attempts)
        print(
            f"FAILED model={candidate.model} "
            f"model_removed_from_openrouter={_removal_label(removed)} "
            f"failure_reason={reason} quarantined_report={quarantined_report}",
            file=sys.stderr,
        )

    return ChainResult("failed", None, None, run_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", "-o", type=Path, required=True, help="final Markdown path")
    parser.add_argument("--run-dir", type=Path, required=True, help="fallback-chain artifact directory")
    parser.add_argument("--corpus", type=Path, required=True, help="existing corpus to replay")
    parser.add_argument("--config", type=Path, default=briefing_config.DEFAULT_CONFIG_PATH)
    parser.add_argument("--sources", type=Path, default=fetch_news.DEFAULT_SOURCES_PATH)
    parser.add_argument("--prompt", type=Path, default=ROOT / "briefing-runner-prompt.md")
    parser.add_argument("--force", action="store_true", help="replace an existing --output file")
    parser.add_argument("--timeout", type=_positive_int, default=600)
    parser.add_argument("--max-corrections", type=_nonnegative_int, choices=range(0, 4), default=3)
    parser.add_argument("--max-tokens", type=_positive_int, default=20_000)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    if args.run_dir.exists():
        parser.error(f"run directory already exists: {args.run_dir}")
    if args.output.exists() and not args.force:
        parser.error(f"output already exists: {args.output}; pass --force to replace it")
    for label, path in (
        ("config", args.config),
        ("sources", args.sources),
        ("prompt", args.prompt),
        ("corpus", args.corpus),
    ):
        if not path.is_file():
            parser.error(f"{label} file does not exist: {path}")

    settings = RunnerSettings(
        config_path=args.config.resolve(),
        sources_path=args.sources.resolve(),
        prompt_path=args.prompt.resolve(),
        output_path=args.output.resolve(),
        corpus_path=args.corpus.resolve(),
        timeout_seconds=args.timeout,
        max_corrections=args.max_corrections,
        strict=args.strict,
    )
    result = run_fallback_chain(settings, args.run_dir.resolve(), max_tokens=args.max_tokens)
    if result.status == "ready":
        print(
            f"READY: final output at {settings.output_path} using {result.selected_model} "
            f"(artifacts: {result.run_dir})"
        )
        return 0
    print(f"NO RESULT: all production models failed (artifacts: {result.run_dir})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
