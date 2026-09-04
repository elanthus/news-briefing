"""End-to-end model evaluation orchestration and compatibility exports."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import eval_briefing as eval_briefing

from evaluator.adapters import Adapter
from evaluator.cases import run_deterministic_suite as run_deterministic_suite
from evaluator.checkpoint import _checkpoint as _checkpoint
from evaluator.execution import execute_evaluation
from evaluator.plan import (
    _attack_dimensions as _attack_dimensions,
)
from evaluator.plan import (
    _json as _json,
)
from evaluator.plan import (
    _mutate as _mutate,
)
from evaluator.plan import (
    _relocate as _relocate,
)
from evaluator.plan import (
    _set_source_failures as _set_source_failures,
)
from evaluator.plan import (
    _sha256,
)
from evaluator.plan import (
    _validate_generation_case as _validate_generation_case,
)
from evaluator.plan import (
    correction_request as correction_request,
)
from evaluator.plan import (
    model_request as model_request,
)
from evaluator.report import (
    _OPERATIONS_HEADER as _OPERATIONS_HEADER,
)
from evaluator.report import (
    _attack_breakdown as _attack_breakdown,
)
from evaluator.report import (
    _operations_row as _operations_row,
)
from evaluator.report import (
    markdown_report as markdown_report,
)
from evaluator.report import (
    summarize as summarize,
)
from evaluator.scoring import (
    _oracle as _oracle,
)
from evaluator.scoring import (
    _semantic_adjudication_template as _semantic_adjudication_template,
)
from evaluator.scoring import (
    apply_adjudications as apply_adjudications,
)

ROOT = Path(__file__).resolve().parents[1]
EVALUATOR_DIR = Path(__file__).resolve().parent
DEFAULT_SUITE = EVALUATOR_DIR / "fixtures" / "generation-cases.json"
DEFAULT_CORPUS = EVALUATOR_DIR / "fixtures" / "generation-corpus.json"
DEFAULT_PROTOCOL = EVALUATOR_DIR / "protocols" / "portfolio-v1.json"
ProgressCallback = Callable[[str, str, int, int, str], None]

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
    generation_path: str = "markdown",
    source_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one evaluation through the split planning and execution modules."""
    return execute_evaluation(
        adapters,
        prompt_versions,
        output_dir,
        trials,
        suite_path,
        corpus_path,
        progress,
        protocol_path=protocol_path,
        run_kind=run_kind,
        execution_seed=execution_seed,
        cost_ceiling_usd=cost_ceiling_usd,
        cost_ceiling_provider=cost_ceiling_provider,
        resume=resume,
        generation_path=generation_path,
        source_provenance=source_provenance,
    )
