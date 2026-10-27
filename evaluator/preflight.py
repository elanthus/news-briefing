"""Credential-free preflight for a planned ``evaluator run``.

The preflight resolves the same evaluation plan that ``run`` would resolve,
binds the chosen suite, corpus, prompts, providers, and controls against the
protocol file, and checks the output directory, without loading credentials or
making a provider call. It is a dry run of everything the run command checks
before its first paid request, except the credential itself, which is only
reported as present or absent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent_runner.hashing import sha256_file

from evaluator.adapters import Adapter, production_adapter_for
from evaluator.checkpoint import _load_resume_manifest, _validate_resume_identity
from evaluator.plan import resolve_evaluation_plan
from evaluator.runner import CIRCUIT_BREAKER_THRESHOLD, ROOT, _git_provenance, final_source_provenance

CREDENTIAL_ENVIRONMENT = {
    "openrouter": "OPENROUTER_API_KEY",
}
COMMAND_PROVIDERS = {"codex-cli": "codex", "claude-code-cli": "claude"}


def _relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


class _Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, ok: bool, detail: str, *, fatal: bool = True) -> None:
        self.items.append({"name": name, "ok": ok, "detail": detail, "fatal": fatal})

    @property
    def ok(self) -> bool:
        return all(item["ok"] or not item["fatal"] for item in self.items)


def _protocol_checks(
    checks: _Checks,
    protocol: dict[str, Any],
    *,
    providers: list[tuple[str, str]],
    prompt_versions: dict[str, Path],
    suite_path: Path,
    trials: int,
    timeout: int,
    temperature: float | None,
    reasoning: str | None,
    run_kind: str,
    execution_seed: int | None,
    cost_ceiling_usd: float | None,
) -> None:
    entries = list(protocol.get("inputs", [])) + list(protocol.get("prompts", []))
    if isinstance(protocol.get("suite"), dict):
        entries.append(protocol["suite"])
    for entry in entries:
        target = ROOT / str(entry.get("path", ""))
        if not target.is_file():
            checks.add("protocol_hash", False, f"{entry.get('path')} is missing")
        else:
            actual = sha256_file(target)
            checks.add(
                "protocol_hash", actual == entry.get("sha256"),
                f"{entry['path']} {'matches' if actual == entry.get('sha256') else 'differs from'} the protocol hash",
            )
    status = protocol.get("status")
    if run_kind == "final":
        checks.add("protocol_status", status == "frozen_for_run",
                   f"protocol status is {status!r}; final runs require 'frozen_for_run'")
    else:
        checks.add("protocol_status", True, f"protocol status is {status!r}", fatal=False)
    suite = protocol.get("suite")
    if isinstance(suite, dict) and isinstance(suite.get("path"), str):
        expected = (ROOT / suite["path"]).resolve()
        checks.add("protocol_suite", expected == suite_path.resolve(),
                   f"--suite {_relative(suite_path)} versus protocol suite {suite['path']}")
    protocol_prompts = protocol.get("prompts")
    if isinstance(protocol_prompts, list) and protocol_prompts and all(
        isinstance(p, dict) and "id" in p for p in protocol_prompts
    ):
        expected_prompts = {str(p["id"]): (ROOT / str(p["path"])).resolve() for p in protocol_prompts}
        actual_prompts = {version: path.resolve() for version, path in prompt_versions.items()}
        checks.add("protocol_prompts", expected_prompts == actual_prompts,
                   "prompt versions " + ("match" if expected_prompts == actual_prompts else "differ from")
                   + f" the protocol: expected {sorted(expected_prompts)}, got {sorted(actual_prompts)}")
    models = protocol.get("models")
    if isinstance(models, list):
        expected_models = sorted((str(m.get("provider")), str(m.get("model"))) for m in models if isinstance(m, dict))
        checks.add("protocol_models", expected_models == sorted(providers),
                   f"providers {sorted(providers)} versus protocol models {expected_models}")
    controls = protocol.get("controls")
    if isinstance(controls, dict):
        expectations: dict[str, Any] = {
            "trials_per_case": trials,
            "timeout_seconds": timeout,
            "temperature": 0.0 if temperature is None else float(temperature),
            "reasoning": reasoning,
            "run_kind": run_kind,
            "execution_seed": execution_seed,
        }
        for field, actual in expectations.items():
            if field not in controls:
                continue
            pinned: Any = controls[field]
            if field == "temperature" and pinned is not None:
                pinned = float(pinned)
            checks.add(f"protocol_control:{field}", pinned == actual,
                       f"{field}: run uses {actual!r}, protocol pins {pinned!r}")
    authorization = protocol.get("authorization")
    if isinstance(authorization, dict):
        ceiling = authorization.get("total_openrouter_cost_ceiling_usd")
        if isinstance(ceiling, int | float) and not isinstance(ceiling, bool):
            within = cost_ceiling_usd is not None and float(cost_ceiling_usd) <= float(ceiling)
            checks.add("protocol_cost_ceiling", within,
                       f"--cost-ceiling-usd {cost_ceiling_usd!r} versus authorized ceiling {ceiling!r}")
        checks.add("protocol_authorized", authorization.get("authorized_at") is not None,
                   f"authorization.authorized_at is {authorization.get('authorized_at')!r}",
                   fatal=run_kind == "final")


def preflight_run(
    *,
    providers: list[tuple[str, str]],
    prompt_versions: dict[str, Path],
    trials: int,
    timeout: int,
    temperature: float | None,
    reasoning: str | None,
    reasoning_effort: str | None,
    suite_path: Path,
    corpus_path: Path,
    protocol_path: Path,
    output_dir: Path | None,
    resume: bool,
    run_kind: str,
    execution_seed: int | None,
    source_tag: str | None,
    cost_ceiling_usd: float | None,
    cost_ceiling_provider: str | None,
    before_tag: bool = False,
) -> dict[str, Any]:
    """Validate a planned run without credentials or a provider call.

    ``before_tag`` reports a missing final-run source tag as a deferred,
    non-fatal check so a frozen protocol can be rehearsed before the tag exists;
    the plan is then resolved as if the clean HEAD carried that tag.
    """
    checks = _Checks()
    if run_kind not in {"development", "pilot", "final"}:
        raise ValueError("run_kind must be development, pilot, or final")
    if trials <= 0:
        raise ValueError("trials must be positive")
    if resume and output_dir is None:
        raise ValueError("--resume requires --output-dir")
    if run_kind == "final" and not source_tag:
        raise ValueError("final runs require --source-tag pointing at the clean HEAD")

    adapters: list[Adapter] = []
    for provider, model in providers:
        try:
            adapters.append(production_adapter_for(
                provider, model, timeout, temperature=temperature,
                reasoning_enabled=None if reasoning is None else reasoning == "enabled",
                reasoning_effort=reasoning_effort,
            ))
            checks.add("provider", True, f"{provider}={model} builds a production-parity adapter")
        except ValueError as exc:
            checks.add("provider", False, f"{provider}={model}: {exc}")
    credentials = []
    for provider in sorted({provider for provider, _ in providers}):
        variable = CREDENTIAL_ENVIRONMENT.get(provider)
        command = COMMAND_PROVIDERS.get(provider)
        credentials.append({
            "provider": provider,
            "requirement": variable or (f"command {command!r}" if command else None),
            "present": bool(os.environ.get(variable)) if variable else None,
        })

    for version, path in prompt_versions.items():
        checks.add("prompt", path.is_file(), f"{version}={_relative(path)} {'exists' if path.is_file() else 'missing'}")
    for label, path in (("suite", suite_path), ("corpus", corpus_path), ("protocol", protocol_path)):
        checks.add(label, path.is_file(), f"{_relative(path)} {'exists' if path.is_file() else 'missing'}")

    protocol: dict[str, Any] | None = None
    if protocol_path.is_file():
        try:
            loaded = json.loads(protocol_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            checks.add("protocol_parse", False, f"protocol is not valid JSON: {exc}")
        else:
            if isinstance(loaded, dict) and isinstance(loaded.get("protocol_id"), str):
                protocol = loaded
                checks.add("protocol_parse", True, f"protocol_id {loaded['protocol_id']!r}")
            else:
                checks.add("protocol_parse", False, "protocol must be an object with a protocol_id")
    if protocol is not None:
        _protocol_checks(
            checks, protocol, providers=providers, prompt_versions=prompt_versions,
            suite_path=suite_path, trials=trials, timeout=timeout, temperature=temperature,
            reasoning=reasoning, run_kind=run_kind, execution_seed=execution_seed,
            cost_ceiling_usd=cost_ceiling_usd,
        )

    source_provenance: dict[str, Any] | None = None
    source_deferred = False
    if run_kind == "final":
        assert source_tag is not None
        try:
            source_provenance = final_source_provenance(source_tag)
            checks.add("source", True, f"tag {source_tag!r} points at the clean HEAD")
        except ValueError as exc:
            checks.add("source", False, str(exc), fatal=not before_tag)
            if before_tag:
                source_deferred = True
                provenance = _git_provenance()
                source_provenance = {
                    **provenance, "dirty": False, "source_tag": source_tag,
                    "tags": sorted(set(provenance["tags"]) | {source_tag}),
                }

    plan_summary: dict[str, Any] | None = None
    identity: dict[str, Any] | None = None
    if (protocol is not None and all(path.is_file() for path in (suite_path, corpus_path, protocol_path))
            and len(adapters) == len(providers)):
        try:
            plan = resolve_evaluation_plan(
                adapters=adapters, prompt_versions=prompt_versions, trials=trials,
                suite_path=suite_path, corpus_path=corpus_path, protocol_path=protocol_path,
                run_kind=run_kind, execution_seed=execution_seed,
                cost_ceiling_usd=cost_ceiling_usd, cost_ceiling_provider=cost_ceiling_provider,
                resume_manifest=None, source_provenance=source_provenance,
                provenance=_git_provenance, circuit_breaker_threshold=CIRCUIT_BREAKER_THRESHOLD,
            )
        except ValueError as exc:
            checks.add("plan", False, str(exc))
        else:
            identity = plan.identity
            plan_summary = {
                "planned_case_trials": identity["planned_case_trials"],
                "planned_matched_pair_trials": identity["planned_matched_pair_trials"],
                "case_trial_units": plan.case_trial_units,
                "execution_order": identity["execution_order"],
                "execution_seed": plan.execution_seed,
                "suite_sha256": identity["suite_sha256"],
                "protocol_sha256": identity["protocol_sha256"],
                "prompt_sha256": identity["prompt_sha256"],
                "generation_controls": identity["generation_controls"],
                "source_provenance_simulated": source_deferred,
            }
            checks.add("plan", True, f"{identity['planned_case_trials']} case-trials planned")
            if protocol is not None and isinstance(protocol.get("controls"), dict):
                planned = protocol["controls"].get("planned_rows")
                if planned is not None:
                    checks.add("protocol_planned_rows", planned == identity["planned_case_trials"],
                               f"plan has {identity['planned_case_trials']} rows; protocol expects {planned}")

    if output_dir is not None:
        if resume:
            try:
                manifest = _load_resume_manifest(output_dir)
                if identity is not None:
                    _validate_resume_identity(manifest, identity, run_kind)
                checks.add("output_dir", True, f"{_relative(output_dir)} holds a resumable running checkpoint")
            except ValueError as exc:
                checks.add("output_dir", False, str(exc))
        elif output_dir.exists() or output_dir.is_symlink():
            checks.add("output_dir", False, f"{_relative(output_dir)} already exists; a new run needs a new directory")
        else:
            parent = next((p for p in output_dir.resolve().parents if p.exists()), None)
            writable = parent is not None and os.access(parent, os.W_OK)
            checks.add("output_dir", writable,
                       f"{_relative(output_dir)} will be created under {parent}"
                       if writable else f"no writable parent for {_relative(output_dir)}")
    else:
        checks.add("output_dir", True, "default evaluator/results/<timestamp> directory", fatal=False)

    return {
        "ok": checks.ok,
        "provider_calls": 0,
        "credentials": credentials,
        "checks": checks.items,
        "plan": plan_summary,
    }
