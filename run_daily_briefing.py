#!/usr/bin/env python3
"""Run the production briefing through its ordered OpenRouter fallback chain."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import briefing_config
import fetch_news
from agent_runner.checkpoint import sha256_file, utc_now, write_json_atomic
from agent_runner.failures import FailureRecord, run_failure
from agent_runner.models import ProviderError
from agent_runner.providers import provider_for
from agent_runner.runner import ROOT, RunnerSettings, RunResult, run_workflow
from agent_runner.semantic_repairs import daily_semantic_review


@dataclass(frozen=True)
class ModelCandidate:
    model: str
    temperature: float
    reasoning_effort: str | None
    max_tokens_cap: int


PRODUCTION_MODEL_CHAIN = (
    ModelCandidate("tencent/hy3", 0.2, "high", 100_000),
    ModelCandidate("deepseek/deepseek-v4-flash-0731", 0.2, "high", 100_000),
    # Gemini 3.7 Flash advertises a maximum completion length of 65,536 tokens.
    ModelCandidate("google/gemini-3.7-flash", 0.2, None, 65_536),
)

LOG_NAME = "fallback-log.json"


@dataclass(frozen=True)
class ChainResult:
    status: str
    selected_model: str | None
    selected_run_dir: Path | None
    run_dir: Path


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


def _is_publishable_ready_result(
    result: RunResult | None,
    manifest: dict[str, Any] | None,
    candidate_dir: Path,
    output_path: Path,
) -> bool:
    if result is None or result.status != "ready" or result.exit_code != 0 or manifest is None:
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


def _write_chain_logs(root: Path, started_at: str, attempts: list[dict[str, Any]]) -> None:
    selected = next((row for row in attempts if row["status"] == "ready"), None)
    payload = {
        "schema_version": 2,
        "failure": (
            FailureRecord(
                "chain_exhausted" if len(attempts) == len(PRODUCTION_MODEL_CHAIN) else "chain_incomplete"
            ).payload() if selected is None else None
        ),
        "started_at": started_at,
        "completed_at": utc_now(),
        "status": "ready" if selected is not None else "failed",
        "model_chain": [candidate.model for candidate in PRODUCTION_MODEL_CHAIN],
        "selected_model": selected["model"] if selected is not None else None,
        "selected_run_dir": selected["run_dir"] if selected is not None else None,
        "attempts": attempts,
    }
    write_json_atomic(root / LOG_NAME, payload)


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
                max_tokens=min(max_tokens, candidate.max_tokens_cap),
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
                "failure": None,
            }
            attempts.append(row)
            _write_chain_logs(run_dir, started_at, attempts)
            print(f"READY model={candidate.model} run_dir={candidate_name}")
            return ChainResult("ready", candidate.model, candidate_dir, run_dir)

        reason = _failure_reason(result, manifest, failure)
        row = {
            "index": index,
            "model": candidate.model,
            "started_at": started,
            "completed_at": completed_at,
            "status": "quarantined" if result is not None else "failed",
            "run_dir": candidate_name,
            "failure_reason": reason,
            "failure": run_failure(
                manifest, failure.record() if isinstance(failure, ProviderError) else None
            ).payload(),
        }
        attempts.append(row)
        _write_chain_logs(run_dir, started_at, attempts)
        print(f"FAILED model={candidate.model} failure_reason={reason}", file=sys.stderr)

    return ChainResult("failed", None, None, run_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", "-o", type=Path, required=True, help="final Markdown path")
    parser.add_argument("--run-dir", type=Path, required=True, help="fallback-chain artifact directory")
    parser.add_argument("--jev-review-dir", type=Path,
                        help="write Jev advisory findings for the selected ready candidate")
    parser.add_argument("--jev-repair-mode", choices=("apply",),
                        help="confirm Jev flags and apply bounded HY3 repairs")
    parser.add_argument("--corpus", type=Path, required=True, help="existing corpus to replay")
    parser.add_argument("--force", action="store_true", help="replace an existing --output file")
    parser.add_argument("--max-corrections", type=_nonnegative_int, choices=range(0, 4), default=3)
    parser.add_argument("--max-tokens", type=fetch_news.positive_int, default=100_000)
    args = parser.parse_args()

    if (args.jev_repair_mode is None) != (args.jev_review_dir is None):
        parser.error("--jev-repair-mode and --jev-review-dir must be passed together")
    if args.run_dir.exists():
        parser.error(f"run directory already exists: {args.run_dir}")
    if args.output.exists() and not args.force:
        parser.error(f"output already exists: {args.output}; pass --force to replace it")
    if not args.corpus.is_file():
        parser.error(f"corpus file does not exist: {args.corpus}")

    settings = RunnerSettings(
        config_path=briefing_config.DEFAULT_CONFIG_PATH.resolve(),
        sources_path=fetch_news.DEFAULT_SOURCES_PATH.resolve(),
        prompt_path=(ROOT / "briefing-runner-prompt.md").resolve(),
        output_path=args.output.resolve(),
        corpus_path=args.corpus.resolve(),
        max_corrections=args.max_corrections,
    )
    result = run_fallback_chain(settings, args.run_dir.resolve(), max_tokens=args.max_tokens)
    if result.status == "ready":
        print(
            f"READY: final output at {settings.output_path} using {result.selected_model} "
            f"(artifacts: {result.run_dir})"
        )
        if args.jev_review_dir is not None and result.selected_run_dir is not None:
            try:
                audit = daily_semantic_review(result.selected_run_dir, args.jev_review_dir,
                                              apply_repairs=True)
                print(f"Jev daily checks: {audit['status']}; audit retained in {args.jev_review_dir}")
            except Exception as exc:
                print("Jev daily checks could not complete; original generation retained "
                      f"({type(exc).__name__}).", file=sys.stderr)
        return 0
    print(f"NO RESULT: all production models failed (artifacts: {result.run_dir})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
