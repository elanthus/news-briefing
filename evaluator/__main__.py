"""Command-line interface for the isolated news-briefing evaluator."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from evaluator.adapters import adapter_for, load_dotenv
from evaluator.cases import DEFAULT_SUITE as DEFAULT_CHECKER_SUITE
from evaluator.cases import run_deterministic_suite
from evaluator.runner import (
    DEFAULT_CORPUS,
    DEFAULT_SUITE,
    ROOT,
    apply_adjudications,
    markdown_report,
    run_evaluation,
    summarize,
)

EVALUATOR_DIR = Path(__file__).resolve().parent


def _provider_values(values: list[str], all_providers: bool) -> list[tuple[str, str]]:
    env_models = {
        "codex-cli": os.environ.get("CODEX_MODEL", "gpt-5.6-terra"),
        "claude-code-cli": os.environ.get("CLAUDE_CODE_MODEL", "claude-sonnet-5"),
        "openrouter": os.environ.get("OPENROUTER_MODEL", "openai/gpt-5.6-terra"),
        "nvidia": os.environ.get("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b"),
    }
    if all_providers:
        return list(env_models.items())
    parsed = []
    for value in values:
        if "=" not in value:
            raise ValueError("--provider must be PROVIDER=MODEL")
        provider, model = value.split("=", 1)
        if not provider or not model:
            raise ValueError("--provider must be PROVIDER=MODEL")
        parsed.append((provider, model))
    if not parsed:
        raise ValueError("select at least one --provider or use --all-providers")
    return parsed


def _prompt_values(values: list[str]) -> dict[str, Path]:
    if not values:
        return {"production": ROOT / "briefing-prompt.md"}
    prompts = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--prompt must be VERSION=PATH")
        version, raw_path = value.split("=", 1)
        prompts[version] = Path(raw_path).resolve()
    return prompts


def _preflight(providers: list[tuple[str, str]]) -> None:
    problems = []
    requirements = {
        "codex-cli": ("command", "codex"),
        "claude-code-cli": ("command", "claude"),
        "openrouter": ("environment variable", "OPENROUTER_API_KEY"),
        "nvidia": ("environment variable", "NVIDIA_API_KEY"),
    }
    for provider, _model in providers:
        kind, value = requirements.get(provider, ("unknown provider", provider))
        if kind == "command" and shutil.which(value) is None:
            problems.append(f"{provider} requires the {value!r} command")
        elif kind == "environment variable" and not os.environ.get(value):
            problems.append(f"{provider} requires non-empty {value}")
        elif kind == "unknown provider":
            problems.append(f"unknown provider {provider!r}")
    if problems:
        raise ValueError("provider preflight failed: " + "; ".join(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    checker = subparsers.add_parser("checker", help="run the fixed offline human-labeled suite")
    checker.add_argument("--suite", type=Path, default=DEFAULT_CHECKER_SUITE)
    checker.add_argument("--output", type=Path)

    run = subparsers.add_parser("run", help="run the generation suite against live models")
    run.add_argument("--provider", action="append", default=[], help="PROVIDER=MODEL; repeatable")
    run.add_argument("--all-providers", action="store_true")
    run.add_argument("--prompt", action="append", default=[], help="VERSION=PATH; repeatable")
    run.add_argument("--trials", type=int, default=1)
    run.add_argument("--timeout", type=int, default=300)
    run.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    run.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    run.add_argument("--output-dir", type=Path)
    run.add_argument("--env-file", type=Path, default=EVALUATOR_DIR / ".env")

    report_parser = subparsers.add_parser("report", help="rebuild reports from a saved manifest")
    report_parser.add_argument("manifest", type=Path)

    args = parser.parse_args()
    try:
        if args.command == "checker":
            result = run_deterministic_suite(args.suite)
            output = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(output, encoding="utf-8")
            print(json.dumps({"case_count": result["case_count"], "components": result["components"],
                              "heuristic_claim_false_positive_rate": result["heuristic_claim_false_positive_rate"]},
                             indent=2, sort_keys=True))
            return 0
        if args.command == "run":
            load_dotenv(args.env_file)
            providers = _provider_values(args.provider, args.all_providers)
            _preflight(providers)
            adapters = [adapter_for(provider, model, args.timeout) for provider, model in providers]
            prompts = _prompt_values(args.prompt)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            output_dir = args.output_dir or EVALUATOR_DIR / "results" / stamp
            result = run_evaluation(adapters, prompts, output_dir, args.trials, args.suite, args.corpus)
            print(json.dumps(result, indent=2, sort_keys=True))
            return int(bool(result["provider_error_trials"] or result["correction_error_trials"]))
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        apply_adjudications(manifest, args.manifest.parent)
        report = summarize(manifest)
        destination = args.manifest.parent
        (destination / "report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (destination / "report.md").write_text(markdown_report(report), encoding="utf-8")
        print(destination / "report.md")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    sys.exit(main())
