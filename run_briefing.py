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
        choices=("openrouter", "openai-compatible", "claude-code-cli", "codex-cli"),
        required=True,
    )
    parser.add_argument(
        "--endpoint",
        help=(
            "chat-completions URL for --provider openai-compatible "
            "(default: Ollama at http://127.0.0.1:11434/v1/chat/completions)"
        ),
    )
    parser.add_argument(
        "--lean-schema",
        action="store_true",
        help=(
            "openai-compatible only: send the output schema without array-size or string-length "
            "bounds, for servers that compile them slowly (LM Studio's MLX engine)"
        ),
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
    parser.add_argument(
        "--corpus",
        type=Path,
        help="replay an existing corpus instead of fetching live sources",
    )
    parser.add_argument("--prompt", type=Path, default=ROOT / "briefing-runner-prompt.md")
    parser.add_argument("--hours", type=_positive_int, default=fetch_news.DEFAULT_WINDOW_HOURS)
    parser.add_argument("--source-cap", type=_positive_int, default=fetch_news.DEFAULT_SOURCE_CAP)
    parser.add_argument("--category-cap", type=_positive_int, default=fetch_news.DEFAULT_CATEGORY_CAP)
    parser.add_argument(
        "--timeout",
        type=_positive_int,
        default=600,
        help="per-fetch and per-model-call deadline in seconds",
    )
    parser.add_argument("--max-corrections", type=_nonnegative_int, choices=range(0, 4), default=1)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return nonzero for any finding or degraded source coverage",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        help="sampling temperature for openrouter and openai-compatible (default: 0)",
    )
    parser.add_argument(
        "--max-tokens",
        type=_positive_int,
        help=(
            "output ceiling: openrouter defaults to 100000, openai-compatible to the server's default, "
            "or 16000 with --lean-schema"
        ),
    )
    parser.add_argument(
        "--reasoning",
        choices=("enabled", "disabled"),
        help="OpenRouter reasoning control (default: enabled)",
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
    required_paths = [("config", args.config), ("prompt", args.prompt)]
    if args.corpus is not None:
        required_paths.append(("corpus", args.corpus))
    else:
        required_paths.append(("sources", args.sources))
    for label, path in required_paths:
        if not path.is_file():
            parser.error(f"{label} file does not exist: {path}")
    if args.provider not in ("openrouter", "openai-compatible"):
        sampling_options = [
            option
            for option, value in (
                ("--temperature", args.temperature),
                ("--max-tokens", args.max_tokens),
            )
            if value is not None
        ]
        if sampling_options:
            parser.error(
                f"{', '.join(sampling_options)} applies to --provider openrouter or openai-compatible only"
            )
    if args.provider != "openrouter":
        openrouter_options = [
            option
            for option, value in (
                ("--reasoning", args.reasoning),
                ("--reasoning-effort", args.reasoning_effort),
            )
            if value is not None
        ]
        if openrouter_options:
            parser.error(
                f"{', '.join(openrouter_options)} applies to --provider openrouter only"
            )
    if args.provider != "openai-compatible":
        local_options = [
            option
            for option, value in (("--endpoint", args.endpoint), ("--lean-schema", args.lean_schema or None))
            if value is not None
        ]
        if local_options:
            parser.error(f"{', '.join(local_options)} applies to --provider openai-compatible only")

    reasoning_enabled = (
        args.reasoning == "enabled"
        if args.reasoning is not None
        else (True if args.provider == "openrouter" else None)
    )
    try:
        provider = provider_for(
            args.provider,
            args.model,
            temperature=0 if args.temperature is None else args.temperature,
            reasoning_enabled=reasoning_enabled,
            reasoning_effort=args.reasoning_effort,
            max_tokens=args.max_tokens,
            endpoint=args.endpoint,
            lean_schema=args.lean_schema,
        )
        settings = RunnerSettings(
            config_path=args.config.resolve(),
            sources_path=args.sources.resolve(),
            prompt_path=args.prompt.resolve(),
            output_path=args.output.resolve(),
            corpus_path=args.corpus.resolve() if args.corpus is not None else None,
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
    disposition = result.status.replace("_", " ").upper()
    artifact = "final output" if result.status == "ready" else "unpublished preview"
    print(f"{disposition}: {artifact} at {result.output_path} (artifacts: {result.run_dir})")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
