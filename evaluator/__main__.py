"""Command-line interface for the isolated news-briefing evaluator."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from evaluator.adapters import adapter_for, load_dotenv, production_adapter_for
from evaluator.cases import DEFAULT_SUITE as DEFAULT_CHECKER_SUITE
from evaluator.cases import run_deterministic_suite
from evaluator.comparison import compare_runs, markdown_comparison
from evaluator.grounding_machine_review import run_grounding_machine_review
from evaluator.grounding_review import export_grounding_review_packets
from evaluator.label_review import export_human_review_packet, run_label_review
from evaluator.publication import export_public_run, verify_public_run
from evaluator.quality import run_quality_judging
from evaluator.retrieval import (
    DEFAULT_EMBEDDING_CACHE,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_PAIR_FIXTURE,
    DEFAULT_STUDY_REPORT,
    DEFAULT_THRESHOLDS,
    build_embedding_cache,
    load_embedding_cache,
    load_pair_fixture,
    markdown_study,
    run_study,
)
from evaluator.runner import (
    DEFAULT_CORPUS,
    DEFAULT_PROTOCOL,
    DEFAULT_SUITE,
    ROOT,
    apply_adjudications,
    final_source_provenance,
    markdown_report,
    run_evaluation,
    summarize,
)
from evaluator.semantic_review import run_semantic_judging

EVALUATOR_DIR = Path(__file__).resolve().parent
DEFAULT_CHECKER_SNAPSHOT = EVALUATOR_DIR / "snapshots" / "offline-checker.json"


class ProgressBar:
    """One in-place progress line for each sequential provider/model run."""

    def __init__(self, stream: TextIO = sys.stderr, width: int = 24, interactive: bool | None = None):
        self.stream = stream
        self.width = width
        self.interactive = stream.isatty() if interactive is None else interactive
        self._active = False
        self._last_length = 0

    def __call__(self, provider: str, model: str, completed: int, total: int, status: str) -> None:
        filled = self.width if total == 0 else int(self.width * completed / total)
        bar = "#" * filled + "-" * (self.width - filled)
        percent = 100 if total == 0 else int(100 * completed / total)
        line = f"{provider} / {model} [{bar}] {completed}/{total} {percent:3d}%  {status}"
        if self.interactive:
            padding = " " * max(0, self._last_length - len(line))
            self.stream.write(f"\r{line}{padding}")
            self.stream.flush()
            self._active = completed < total
            self._last_length = len(line)
            if completed >= total:
                self.stream.write("\n")
                self.stream.flush()
        elif completed in {0, total} or "error" in status or "circuit" in status:
            self.stream.write(f"{line}\n")
            self.stream.flush()

    def finish(self) -> None:
        if self.interactive and self._active:
            self.stream.write("\n")
            self.stream.flush()
            self._active = False


def _models_from_env(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    models = [model.strip() for model in raw.split(",")]
    if any(not model for model in models):
        raise ValueError(f"{name} must be a comma-delimited list of non-empty model names")
    return models


def _provider_values(
    values: list[str], all_providers: bool, generation_path: str = "markdown"
) -> list[tuple[str, str]]:
    env_models = {
        "codex-cli": ("CODEX_MODEL", "gpt-5.6-terra"),
        "claude-code-cli": ("CLAUDE_CODE_MODEL", "claude-sonnet-5"),
        "openrouter": ("OPENROUTER_MODEL", "openai/gpt-5.6-terra"),
        "nvidia": ("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b"),
    }
    if all_providers:
        selected = (
            {key: value for key, value in env_models.items() if key != "nvidia"}
            if generation_path == "production-parity"
            else env_models
        )
        return [
            (provider, model)
            for provider, (name, default) in selected.items()
            for model in _models_from_env(name, default)
        ]
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


def _prompt_values(values: list[str], generation_path: str = "markdown") -> dict[str, Path]:
    if not values:
        default = (
            ROOT / "briefing-runner-prompt.md"
            if generation_path == "production-parity"
            else ROOT / "briefing-prompt.md"
        )
        return {"production": default}
    prompts = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--prompt must be VERSION=PATH")
        version, raw_path = value.split("=", 1)
        prompts[version] = Path(raw_path).resolve()
    return prompts


def _preflight(providers: list[tuple[str, str]]) -> None:
    problems = []
    requirements: dict[str, tuple[str, str]] = {
        "codex-cli": ("command", "codex"),
        "claude-code-cli": ("command", "claude"),
        "openrouter": ("environment variable", "OPENROUTER_API_KEY"),
        "nvidia": ("environment variable", "NVIDIA_API_KEY"),
        "baseline": ("none", ""),
    }
    for provider, _model in providers:
        kind, value = requirements.get(provider, ("unknown provider", provider))
        if kind == "none":
            continue
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

    checker = subparsers.add_parser("checker", help="run the fixed offline gold-label suite")
    checker.add_argument("--suite", type=Path, default=DEFAULT_CHECKER_SUITE)
    checker.add_argument("--output", type=Path)
    checker.add_argument(
        "--snapshot",
        type=Path,
        help="expected deterministic result (defaults to the committed snapshot for the default suite)",
    )
    checker.add_argument(
        "--update-snapshot",
        action="store_true",
        help="replace the expected snapshot explicitly; review and approve the resulting diff",
    )

    dedup_study = subparsers.add_parser(
        "dedup-study",
        help="benchmark embedding-based near-duplicate detection against the production heuristic",
    )
    dedup_study.add_argument(
        "--fetch-embeddings",
        action="store_true",
        help="refresh the committed credential-free embedding cache through OpenRouter",
    )
    dedup_study.add_argument("--pairs", type=Path, default=DEFAULT_PAIR_FIXTURE)
    dedup_study.add_argument("--embeddings", type=Path, default=DEFAULT_EMBEDDING_CACHE)
    dedup_study.add_argument("--model", default=DEFAULT_EMBEDDING_MODEL)
    dedup_study.add_argument("--env-file", type=Path, default=EVALUATOR_DIR / ".env")
    dedup_study.add_argument("--output", type=Path, default=DEFAULT_STUDY_REPORT)
    dedup_study.add_argument(
        "--threshold",
        type=float,
        action="append",
        default=[],
        help="cosine threshold; repeatable (default: 0.70 through 0.95 in 0.05 steps)",
    )

    label_review = subparsers.add_parser(
        "review-labels", help="blind-review provisional offline labels and adjudicate disagreements"
    )
    label_review.add_argument("--reviewer-model", default="claude-sonnet-5")
    label_review.add_argument("--reviewer-provider", default="claude-code-cli")
    label_review.add_argument(
        "--reviewer-reasoning",
        choices=("enabled", "disabled"),
        help="optional reasoning control for API reviewer providers",
    )
    label_review.add_argument(
        "--reviewer-reasoning-effort",
        choices=("max", "xhigh", "high", "medium", "low", "minimal"),
        help="optional API reviewer reasoning effort; implies reasoning enabled",
    )
    label_review.add_argument("--adjudicator-model", default="claude-opus-4-6")
    label_review.add_argument("--adjudicator-provider", default="claude-code-cli")
    label_review.add_argument(
        "--review-only",
        action="store_true",
        help="record blinded reviewer disagreements without model adjudication",
    )
    label_review.add_argument("--batch-size", type=int, default=10)
    label_review.add_argument("--timeout", type=int, default=600)
    label_review.add_argument("--suite", type=Path, default=DEFAULT_CHECKER_SUITE)
    label_review.add_argument(
        "--provisional-only",
        action="store_true",
        help="review only cases whose label_status is provisional",
    )
    label_review.add_argument("--output-dir", type=Path)
    label_review.add_argument("--env-file", type=Path, default=EVALUATOR_DIR / ".env")

    export_review = subparsers.add_parser(
        "export-label-review",
        help="export a randomized opaque-ID packet for independent human review",
    )
    export_review.add_argument("--suite", type=Path, default=DEFAULT_CHECKER_SUITE)
    export_review.add_argument("--output-dir", type=Path, required=True)
    export_review.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="specific case ID; repeatable (default: every provisional case)",
    )

    grounding_review = subparsers.add_parser(
        "export-grounding-review",
        help="export blinded primary and stratified double-review packets for final utility topics",
    )
    grounding_review.add_argument("manifest", type=Path)
    grounding_review.add_argument("--output-dir", type=Path, required=True)
    grounding_review.add_argument("--seed", type=int, default=8142026)
    grounding_review.add_argument("--double-fraction", type=float, default=0.20)

    machine_grounding = subparsers.add_parser(
        "judge-grounding",
        help="machine-label every blinded grounding topic and audit the stratified sample",
    )
    machine_grounding.add_argument("manifest", type=Path)
    machine_grounding.add_argument("--packet-dir", type=Path, required=True)
    machine_grounding.add_argument("--primary-provider", default="openrouter")
    machine_grounding.add_argument("--primary-model", required=True)
    machine_grounding.add_argument("--audit-provider", default="openrouter")
    machine_grounding.add_argument("--audit-model", required=True)
    machine_grounding.add_argument("--batch-size", type=int, default=25)
    machine_grounding.add_argument("--timeout", type=int, default=300)
    machine_grounding.add_argument("--cost-ceiling-usd", type=float, default=7.0)
    machine_grounding.add_argument("--cost-headroom-usd", type=float, default=0.10)
    machine_grounding.add_argument("--output-dir", type=Path, required=True)
    machine_grounding.add_argument("--env-file", type=Path, default=EVALUATOR_DIR / ".env")

    run = subparsers.add_parser("run", help="run the generation suite against live models")
    run.add_argument("--provider", action="append", default=[], help="PROVIDER=MODEL; repeatable")
    run.add_argument(
        "--all-providers",
        action="store_true",
        help="run every model in each comma-delimited provider MODEL environment variable",
    )
    run.add_argument(
        "--prompt",
        action="append",
        default=[],
        help="VERSION=PATH; repeatable (final runs require at least two)",
    )
    run.add_argument(
        "--generation-path",
        choices=("markdown", "production-parity"),
        default="markdown",
        help=(
            "markdown asks the model for the historical direct-Markdown contract; "
            "production-parity uses the real structured transport, corpus projection, "
            "validator, and renderer"
        ),
    )
    run.add_argument("--trials", type=int, default=1)
    run.add_argument("--timeout", type=int, default=300)
    run.add_argument(
        "--temperature",
        type=float,
        help="sampling temperature for API providers (default: 0)",
    )
    run.add_argument("--seed", type=int, help="optional sampling seed for API providers")
    run.add_argument(
        "--execution-seed",
        type=int,
        help="optional final-run ordering seed; generated and recorded when omitted",
    )
    run.add_argument(
        "--reasoning",
        choices=("enabled", "disabled"),
        help="optional reasoning control for API providers; omitted preserves provider default",
    )
    run.add_argument(
        "--reasoning-effort",
        choices=("max", "xhigh", "high", "medium", "low", "minimal"),
        help="optional API reasoning effort; implies reasoning enabled",
    )
    run.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    run.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    run.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    run.add_argument("--output-dir", type=Path)
    run.add_argument(
        "--resume",
        action="store_true",
        help="continue an interrupted checkpoint in --output-dir after validating run identity",
    )
    run.add_argument("--env-file", type=Path, default=EVALUATOR_DIR / ".env")
    run.add_argument(
        "--run-kind",
        choices=("development", "pilot", "final"),
        default="development",
    )
    run.add_argument(
        "--source-tag",
        help="annotated or lightweight tag pointing at the clean HEAD used for a final run",
    )
    run.add_argument("--cost-ceiling-usd", type=float)
    run.add_argument(
        "--cost-ceiling-provider",
        help="apply the cost ceiling only to this provider (for example, openrouter)",
    )

    report_parser = subparsers.add_parser("report", help="rebuild reports from a saved manifest")
    report_parser.add_argument("manifest", type=Path)

    compare = subparsers.add_parser(
        "compare", help="paired, case-clustered comparison of compatible prompt runs"
    )
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--baseline-prompt")
    compare.add_argument("--candidate-prompt")
    compare.add_argument("--allow-descriptive", action="store_true")
    compare.add_argument("--bootstrap-samples", type=int, default=10_000)
    compare.add_argument("--seed", type=int, default=1729)
    compare.add_argument("--output", type=Path)

    quality = subparsers.add_parser(
        "judge-quality",
        help="blinded pairwise LLM-judge comparison of briefing prose across a completed run",
    )
    quality.add_argument("manifest", type=Path)
    quality.add_argument("--judge-provider", default="claude-code-cli")
    quality.add_argument("--judge-model", default="claude-opus-4-6")
    quality.add_argument("--sample", type=int, help="cap the number of judged pairs; default judges all")
    quality.add_argument("--seed", type=int, default=0, help="sampling seed, for reproducible --sample subsets")
    quality.add_argument("--timeout", type=int, default=300)
    quality.add_argument("--suite", type=Path, help="override the suite path recorded in the manifest")
    quality.add_argument("--output-dir", type=Path)
    quality.add_argument("--env-file", type=Path, default=EVALUATOR_DIR / ".env")

    semantic = subparsers.add_parser(
        "judge-semantics",
        help="blind-review URL-scoped meaning-preservation propositions",
    )
    semantic.add_argument("manifest", type=Path)
    semantic.add_argument("--judge-provider", default="claude-code-cli")
    semantic.add_argument("--judge-model", default="claude-opus-4-6")
    semantic.add_argument("--timeout", type=int, default=300)
    semantic.add_argument("--output-dir", type=Path)
    semantic.add_argument("--env-file", type=Path, default=EVALUATOR_DIR / ".env")

    public_run = subparsers.add_parser(
        "export-public-run",
        help="export redacted row evidence and reproducible aggregates for a completed final run",
    )
    public_run.add_argument(
        "manifest",
        type=Path,
        nargs="+",
        help="one complete manifest, or compatible split final-run manifests",
    )
    public_run.add_argument("--output-dir", type=Path, required=True)
    public_run.add_argument("--ledger-output", type=Path)
    public_run.add_argument("--machine-grounding", type=Path)

    verify_public = subparsers.add_parser(
        "verify-public-run",
        help="verify public evidence hashes and regenerate its aggregate report",
    )
    verify_public.add_argument("output_dir", type=Path)

    args = parser.parse_args()
    try:
        if args.command == "dedup-study":
            pairs, label_provenance = load_pair_fixture(args.pairs)
            if args.fetch_embeddings:
                load_dotenv(args.env_file)
                api_key = os.environ.get("OPENROUTER_API_KEY")
                if not api_key:
                    raise ValueError("dedup-study --fetch-embeddings requires OPENROUTER_API_KEY")
                generated_on = datetime.now(UTC).date().isoformat()
                cache = build_embedding_cache(pairs, args.model, api_key, generated_on)
                args.embeddings.parent.mkdir(parents=True, exist_ok=True)
                args.embeddings.write_text(
                    json.dumps(cache, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                print(json.dumps({
                    "embedding_count": len(cache["embeddings"]),
                    "generated_on": generated_on,
                    "model": args.model,
                    "output": str(args.embeddings),
                }, indent=2, sort_keys=True))
                return 0
            cache = load_embedding_cache(args.embeddings)
            thresholds = args.threshold or list(DEFAULT_THRESHOLDS)
            try:
                study = run_study(pairs, cache["embeddings"], thresholds)
            except KeyError as exc:
                raise ValueError(
                    f"embedding cache {args.embeddings} is out of date ({exc}); "
                    "refresh it with `python3 -m evaluator dedup-study --fetch-embeddings`"
                ) from exc
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                markdown_study(study, pairs, cache, label_provenance),
                encoding="utf-8",
            )
            print(json.dumps({
                "chosen_threshold": study["chosen_threshold"],
                "embedding_f1": study["chosen_embedding_metrics"]["f1"],
                "heuristic_f1": study["heuristic_metrics"]["f1"],
                "output": str(args.output),
                "pair_count": study["pair_count"],
            }, indent=2, sort_keys=True))
            return 0
        if args.command == "checker":
            result = run_deterministic_suite(args.suite)
            output = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            snapshot = args.snapshot
            if snapshot is None and args.suite.resolve() == DEFAULT_CHECKER_SUITE.resolve():
                snapshot = DEFAULT_CHECKER_SNAPSHOT
            if args.update_snapshot:
                if snapshot is None:
                    raise ValueError("--update-snapshot requires --snapshot for a custom suite")
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                snapshot.write_text(output, encoding="utf-8")
            elif snapshot is not None:
                if not snapshot.is_file():
                    raise ValueError(
                        f"checker snapshot is missing: {snapshot}; create it with --update-snapshot"
                    )
                expected = snapshot.read_text(encoding="utf-8")
                if expected != output:
                    raise ValueError(
                        "checker result differs from the approved snapshot; inspect the per-case diff "
                        "and run `python3 -m evaluator checker --update-snapshot` only after approval"
                    )
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(output, encoding="utf-8")
            print(json.dumps({"case_count": result["case_count"], "components": result["components"],
                              "heuristic_claim_false_positive_rate": result["heuristic_claim_false_positive_rate"],
                              "heuristic_claim_false_positive_rates": result["heuristic_claim_false_positive_rates"]},
                             indent=2, sort_keys=True))
            return 0
        if args.command == "export-public-run":
            result = export_public_run(
                args.manifest,
                args.output_dir,
                ledger_output=args.ledger_output,
                machine_grounding_path=args.machine_grounding,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "verify-public-run":
            print(json.dumps(verify_public_run(args.output_dir), indent=2, sort_keys=True))
            return 0
        if args.command == "compare":
            result = compare_runs(
                args.baseline,
                args.candidate,
                baseline_prompt=args.baseline_prompt,
                candidate_prompt=args.candidate_prompt,
                allow_descriptive=args.allow_descriptive,
                bootstrap_samples=args.bootstrap_samples,
                seed=args.seed,
            )
            output_path = args.output
            if output_path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                output_path.with_suffix(".md").write_text(
                    markdown_comparison(result), encoding="utf-8"
                )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "export-grounding-review":
            result = export_grounding_review_packets(
                args.manifest,
                args.output_dir,
                seed=args.seed,
                double_fraction=args.double_fraction,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "judge-grounding":
            load_dotenv(args.env_file)
            selected = [
                (args.primary_provider, args.primary_model),
                (args.audit_provider, args.audit_model),
            ]
            _preflight(selected)
            primary_judge = adapter_for(
                args.primary_provider,
                args.primary_model,
                args.timeout,
                temperature=0,
                reasoning_enabled=False,
            )
            audit_judge = adapter_for(
                args.audit_provider,
                args.audit_model,
                args.timeout,
                temperature=0,
                reasoning_enabled=False,
            )
            progress = ProgressBar()
            try:
                result = run_grounding_machine_review(
                    args.manifest,
                    args.packet_dir,
                    primary_judge,
                    audit_judge,
                    args.output_dir,
                    batch_size=args.batch_size,
                    cost_ceiling_usd=args.cost_ceiling_usd,
                    cost_headroom_usd=args.cost_headroom_usd,
                    progress=progress,
                )
            finally:
                progress.finish()
            print(json.dumps({
                "status": result["status"],
                "primary": {
                    "reviewed_topics": result["primary"]["reviewed_topics"],
                    "grounding_errors": result["primary"]["grounding_errors"],
                },
                "audit": {
                    "reviewed_topics": result["audit"]["reviewed_topics"],
                    "grounding_errors": result["audit"]["grounding_errors"],
                    "agreement_with_primary": result["audit"]["agreement_with_primary"],
                },
                "observed_cost_usd": result["observed_cost_usd"],
                "report": str(args.output_dir / "machine-grounding-review.json"),
            }, indent=2, sort_keys=True))
            return 0
        if args.command == "run":
            if args.resume and args.output_dir is None:
                raise ValueError("--resume requires --output-dir")
            if args.run_kind == "final" and not args.source_tag:
                raise ValueError("final runs require --source-tag pointing at the clean HEAD")
            source_provenance = (
                final_source_provenance(args.source_tag)
                if args.run_kind == "final"
                else None
            )
            load_dotenv(args.env_file)
            providers = _provider_values(
                args.provider, args.all_providers, args.generation_path
            )
            _preflight(providers)
            adapter_factory = (
                production_adapter_for
                if args.generation_path == "production-parity"
                else adapter_for
            )
            adapters = [
                adapter_factory(
                    provider,
                    model,
                    args.timeout,
                    temperature=args.temperature,
                    seed=args.seed,
                    reasoning_enabled=(
                        None if args.reasoning is None else args.reasoning == "enabled"
                    ),
                    reasoning_effort=args.reasoning_effort,
                )
                for provider, model in providers
            ]
            prompts = _prompt_values(args.prompt, args.generation_path)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            output_dir = args.output_dir or EVALUATOR_DIR / "results" / stamp
            progress = ProgressBar()
            try:
                result = run_evaluation(
                    adapters,
                    prompts,
                    output_dir,
                    args.trials,
                    args.suite,
                    args.corpus,
                    progress,
                    protocol_path=args.protocol,
                    run_kind=args.run_kind,
                    execution_seed=args.execution_seed,
                    cost_ceiling_usd=args.cost_ceiling_usd,
                    cost_ceiling_provider=args.cost_ceiling_provider,
                    resume=args.resume,
                    generation_path=args.generation_path,
                    source_provenance=source_provenance,
                )
            finally:
                progress.finish()
            print(json.dumps(result, indent=2, sort_keys=True))
            operations = result["operations"]
            return int(bool(
                operations["provider_error_trials"]
                or operations["circuit_open_skipped_trials"]
                or operations["correction_error_trials"]
                or operations["run_status"] != "complete"
            ))
        if args.command == "export-label-review":
            result = export_human_review_packet(
                args.output_dir,
                args.suite,
                case_ids=set(args.case_id) if args.case_id else None,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "review-labels":
            load_dotenv(args.env_file)
            selected = [(args.reviewer_provider, args.reviewer_model)]
            if not args.review_only:
                selected.append((args.adjudicator_provider, args.adjudicator_model))
            _preflight(selected)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            output_dir = args.output_dir or EVALUATOR_DIR / "results" / f"label-review-{stamp}"
            reviewer = adapter_for(
                args.reviewer_provider,
                args.reviewer_model,
                args.timeout,
                reasoning_enabled=(
                    None if args.reviewer_reasoning is None
                    else args.reviewer_reasoning == "enabled"
                ),
                reasoning_effort=args.reviewer_reasoning_effort,
            )
            adjudicator = (
                None if args.review_only
                else adapter_for(args.adjudicator_provider, args.adjudicator_model, args.timeout)
            )
            result = run_label_review(
                reviewer,
                adjudicator,
                output_dir,
                args.suite,
                args.batch_size,
                case_ids=(
                    {
                        case["id"]
                        for case in json.loads(args.suite.read_text(encoding="utf-8"))["cases"]
                        if case.get("label_status") == "provisional"
                    }
                    if args.provisional_only else None
                ),
            )
            print(json.dumps({
                "status": result["status"],
                "case_count": result["case_count"],
                "exact_agreements": result["exact_agreements"],
                "disagreements_found": result["disagreements_found"],
                "disagreements_adjudicated": result["disagreements_adjudicated"],
                "report": str(output_dir / "label-review.json"),
            }, indent=2, sort_keys=True))
            return 0
        if args.command == "judge-quality":
            load_dotenv(args.env_file)
            _preflight([(args.judge_provider, args.judge_model)])
            judge = adapter_for(args.judge_provider, args.judge_model, args.timeout)
            output_dir = args.output_dir or args.manifest.parent / "quality-judgments"
            result = run_quality_judging(
                args.manifest, judge, output_dir, args.suite, args.sample, args.seed
            )
            print(json.dumps({
                "pairs_available": result["pairs_available"],
                "pairs_judged": result["pairs_judged"],
                "position_consistency": result["position_consistency"],
                "report": str(output_dir / "quality-report.md"),
            }, indent=2, sort_keys=True))
            return 0
        if args.command == "judge-semantics":
            load_dotenv(args.env_file)
            _preflight([(args.judge_provider, args.judge_model)])
            judge = adapter_for(args.judge_provider, args.judge_model, args.timeout)
            output_dir = args.output_dir or args.manifest.parent / "semantic-judgments"
            result = run_semantic_judging(args.manifest, judge, output_dir)
            print(json.dumps({
                "judgments_available": result["judgments_available"],
                "model_calls": result["model_calls"],
                "counts": result["counts"],
                "report": str(args.manifest.parent / "report.md"),
            }, indent=2, sort_keys=True))
            return 0
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        apply_adjudications(manifest, args.manifest.parent)
        destination = args.manifest.parent
        report = summarize(manifest, destination)
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
