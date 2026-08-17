#!/usr/bin/env python3
"""Run the dependency-free, code-owned daily news briefing workflow."""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

import briefing_config
import fetch_news
from agent_runner.models import ProviderError
from agent_runner.providers import provider_for
from agent_runner.runner import ROOT, RunnerSettings, run_workflow


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


def _default_run_dir() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / ".news-briefing" / "runs" / f"{stamp}-{secrets.token_hex(4)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        choices=("openrouter", "claude-code-cli", "codex-cli"),
        required=True,
    )
    parser.add_argument("--model", required=True, help="exact provider model identifier")
    parser.add_argument("--output", "-o", type=Path, required=True, help="final Markdown path")
    parser.add_argument("--run-dir", type=Path, help="artifact directory; generated when omitted")
    parser.add_argument(
        "--resume",
        type=Path,
        metavar="RUN_DIR",
        help="resume an interrupted run after verifying its invocation and artifacts",
    )
    parser.add_argument("--force", action="store_true", help="replace an existing --output file")
    parser.add_argument("--config", type=Path, default=briefing_config.DEFAULT_CONFIG_PATH)
    parser.add_argument("--sources", type=Path, default=fetch_news.DEFAULT_SOURCES_PATH)
    parser.add_argument("--prompt", type=Path, default=ROOT / "briefing-runner-prompt.md")
    parser.add_argument("--hours", type=_positive_int, default=fetch_news.DEFAULT_WINDOW_HOURS)
    parser.add_argument("--source-cap", type=_positive_int, default=fetch_news.DEFAULT_SOURCE_CAP)
    parser.add_argument("--category-cap", type=_positive_int, default=fetch_news.DEFAULT_CATEGORY_CAP)
    parser.add_argument("--timeout", type=_positive_int, default=600)
    parser.add_argument("--max-corrections", type=_nonnegative_int, choices=range(0, 4), default=1)
    parser.add_argument("--strict", action="store_true", help="return nonzero for WARN as well as ERROR")
    parser.add_argument("--temperature", type=float, default=0, help="OpenRouter sampling temperature")
    parser.add_argument("--max-tokens", type=_positive_int, default=100_000, help="OpenRouter output ceiling")
    parser.add_argument(
        "--reasoning",
        choices=("enabled", "disabled"),
        help="optional OpenRouter reasoning control",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("max", "xhigh", "high", "medium", "low", "minimal"),
        help="optional OpenRouter reasoning effort; implies enabled reasoning",
    )
    args = parser.parse_args()

    if args.resume and args.run_dir:
        parser.error("--resume and --run-dir cannot be combined")
    if args.output.exists() and not args.force and not args.resume:
        parser.error(f"output already exists: {args.output}; pass --force to replace it")
    for label, path in (("config", args.config), ("sources", args.sources), ("prompt", args.prompt)):
        if not path.is_file():
            parser.error(f"{label} file does not exist: {path}")

    reasoning_enabled = None if args.reasoning is None else args.reasoning == "enabled"
    try:
        provider = provider_for(
            args.provider,
            args.model,
            temperature=args.temperature,
            reasoning_enabled=reasoning_enabled,
            reasoning_effort=args.reasoning_effort,
            max_tokens=args.max_tokens,
        )
        settings = RunnerSettings(
            config_path=args.config.resolve(),
            sources_path=args.sources.resolve(),
            prompt_path=args.prompt.resolve(),
            output_path=args.output.resolve(),
            hours=args.hours,
            source_cap=args.source_cap,
            category_cap=args.category_cap,
            timeout_seconds=args.timeout,
            max_corrections=args.max_corrections,
            strict=args.strict,
        )
        run_dir = (args.resume or args.run_dir or _default_run_dir()).resolve()
        result = run_workflow(provider, settings, run_dir, resume=bool(args.resume))
    except (OSError, ValueError, RuntimeError, ProviderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{result.status}: wrote {result.output_path} (artifacts: {result.run_dir})")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
