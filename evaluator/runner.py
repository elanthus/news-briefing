"""End-to-end model evaluation orchestration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from evaluator import cases as cases_module
from evaluator import checkpoint as checkpoint_module
from evaluator.adapters import Adapter
from evaluator.checkpoint import (
    CIRCUIT_BREAKER_THRESHOLD,
    _has_execution_errors,
    _load_resume_manifest,
    initialize_run,
)
from evaluator.execution import ExecutionOptions, ProgressCallback, _run_adapter
from evaluator.plan import _sha256, resolve_evaluation_plan
from evaluator.report import finalize_run_report

ROOT = Path(__file__).resolve().parents[1]
EVALUATOR_DIR = Path(__file__).resolve().parent
DEFAULT_SUITE = EVALUATOR_DIR / "fixtures" / "generation-cases-v9.json"
DEFAULT_CORPUS = EVALUATOR_DIR / "fixtures" / "generation-corpus.json"
DEFAULT_PROTOCOL = EVALUATOR_DIR / "protocols" / "parity-v1.json"


def _git_provenance() -> dict[str, Any]:
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "no diagnostic output"
            raise ValueError(
                f"cannot determine Git provenance: git {' '.join(args)} "
                f"exited {result.returncode}: {detail}"
            )
        return result.stdout.strip()

    commit = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    dirty = bool(git("status", "--porcelain"))
    tags = sorted(filter(None, git("tag", "--points-at", "HEAD").splitlines()))
    runtime_paths = [
        Path(path)
        for path in git("ls-files").splitlines()
        if (
            path in {"briefing_config.py", "corpus_schema.py", "eval_briefing.py"}
            or (
                path.startswith("evaluator/")
                and path.endswith(".py")
                and not path.startswith("evaluator/tests/")
            )
            or (path.startswith("agent_runner/") and path.endswith(".py"))
        )
    ]
    runtime_source_sha256 = {
        path.as_posix(): _sha256((ROOT / path).read_bytes())
        for path in runtime_paths
        if (ROOT / path).is_file()
    }
    return {
        "commit": commit or None,
        "tree": tree or None,
        "dirty": dirty,
        "tags": tags,
        "runtime_source_sha256": runtime_source_sha256,
    }


def final_source_provenance(source_tag: str) -> dict[str, Any]:
    """Require a clean, tagged source revision before any final provider call."""
    provenance = _git_provenance()
    if provenance["dirty"]:
        raise ValueError("final runs require a clean Git worktree")
    if not provenance["commit"] or not provenance["tree"]:
        raise ValueError("final runs require a readable Git commit and tree")
    if source_tag not in provenance["tags"]:
        available = ", ".join(provenance["tags"]) or "none"
        raise ValueError(
            f"final source tag {source_tag!r} does not point at HEAD; tags at HEAD: {available}"
        )
    provenance["source_tag"] = source_tag
    return provenance


def run_evaluation(
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
    source_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Plan one evaluation, then run its bounded trial loop to a final report."""
    if trials <= 0:
        raise ValueError("trials must be positive")
    if run_kind not in {"development", "pilot", "final"}:
        raise ValueError("run_kind must be development, pilot, or final")
    resume_manifest = _load_resume_manifest(output_dir) if resume else None
    plan = resolve_evaluation_plan(
        adapters=adapters,
        prompt_versions=prompt_versions,
        trials=trials,
        suite_path=suite_path,
        corpus_path=corpus_path,
        protocol_path=protocol_path,
        run_kind=run_kind,
        execution_seed=execution_seed,
        cost_ceiling_usd=cost_ceiling_usd,
        cost_ceiling_provider=cost_ceiling_provider,
        resume_manifest=resume_manifest,
        source_provenance=source_provenance,
        provenance=_git_provenance,
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
        cost_ceiling_usd=cost_ceiling_usd,
        cost_ceiling_provider=cost_ceiling_provider,
        checkpoint=checkpoint_module._checkpoint,
        deterministic_suite=cases_module.run_deterministic_suite,
    )
    if state.completed_report is not None:
        return state.completed_report
    options = ExecutionOptions(
        output_dir,
        suite_path,
        corpus_path,
        cost_ceiling_usd,
        cost_ceiling_provider,
        resume_manifest is not None,
        checkpoint_module._checkpoint,
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
        checkpoint_module._checkpoint,
        has_errors=_has_execution_errors(state.results),
    )
