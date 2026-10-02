"""Shared publication resolution and bounded, hash-bound deployment receipts.

A receipt is produced after deployment; an available ready run alone is never
publication evidence. Callers obtaining receipts from Actions must additionally
verify the matching workflow attempt's successful Pages deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from agent_runner.checkpoint import write_json_atomic
from agent_runner.jev_review import MAX_ARTIFACT_BYTES, load_topics
from agent_runner.semantic_repairs import load_public_audit


def _read_bytes(path: Path, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("publication artifact exceeds the input bound")
    return raw


def read_json(path: Path, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> Any:
    return json.loads(_read_bytes(path, max_bytes=max_bytes))


def selected_generation_run(root: Path) -> Path | None:
    """Resolve the original generation, rejecting escaping fallback children."""
    fallback_path = root / "fallback-log.json"
    if not fallback_path.exists():
        return root
    try:
        fallback = read_json(fallback_path)
        if not isinstance(fallback, dict) or fallback.get("status") != "ready":
            return None
        selected = fallback.get("selected_run_dir")
        if not isinstance(selected, str) or not selected or Path(selected).name != selected:
            return None
        child = root / selected
        if child.is_symlink() or child.resolve(strict=True).parent != root.resolve(strict=True):
            return None
        manifest = read_json(child / "manifest.json")
        if (not isinstance(manifest, dict) or manifest.get("status") != "complete"
                or not isinstance(manifest.get("final"), dict) or manifest["final"].get("status") != "ready"):
            return None
        return child
    except (ValueError, OSError, TypeError, RecursionError):
        return None


def resolve_publication_run(root: Path) -> tuple[dict[str, Any] | None, Path | None]:
    """Use the publication policy for accepted repairs and retained originals."""
    original = selected_generation_run(root)
    if original is None:
        return None, None
    if (root / "jev-review" / "audit.json").is_file():
        return load_public_audit(original, root / "jev-review")
    return None, original


def _verified_publication_artifacts(
    root: Path,
) -> tuple[Path, list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    """Verify artifact identity without replaying a potentially newer renderer."""
    _audit, selected = resolve_publication_run(root)
    if selected is None:
        raise ValueError("no ready publication candidate")
    topics, hashes = load_topics(selected)
    manifest_bytes = _read_bytes(selected / "manifest.json")
    if hashlib.sha256(manifest_bytes).hexdigest() != hashes["manifest.json"]:
        raise ValueError("publication manifest changed during verification")
    manifest = json.loads(manifest_bytes)
    final = manifest["final"]
    if final.get("artifact_type") != "final" or final.get("run_artifact") != "final.md":
        raise ValueError("invalid public artifact binding")
    markdown = _read_bytes(selected / "final.md")
    markdown.decode("utf-8")
    digest = hashlib.sha256(markdown).hexdigest()
    if manifest["artifacts"].get("final.md") != digest or final.get("output_sha256") != digest:
        raise ValueError("public artifact hash mismatch")
    return selected, topics, {**hashes, "final.md": digest}, manifest


def verified_publication_artifacts(root: Path) -> tuple[Path, list[dict[str, Any]], dict[str, str]]:
    """Verify frozen evidence and exact artifact hashes for receipt comparison.

    This establishes diagnostics integrity, not deployment. Historical monitors
    must additionally match these hashes to a receipt whose Actions deployment
    was independently verified. They must not replay the current Markdown
    renderer or checker against an older, already deployed artifact.
    """
    selected, topics, hashes, _manifest = _verified_publication_artifacts(root)
    return selected, topics, hashes


def verified_publication(root: Path) -> tuple[Path, list[dict[str, Any]], dict[str, str]]:
    """Verify newly published Markdown against this revision's exact rendering."""
    selected, topics, hashes, manifest = _verified_publication_artifacts(root)
    final = manifest["final"]
    # Re-render the verified candidate and its code-owned validation footer.
    # A prefix match alone would permit unreviewed prose after the footer.
    import briefing_config
    import corpus_schema
    import eval_briefing
    from agent_runner.outcomes import classify_outcome
    from agent_runner.output import PROMOTION_ACTION, project_corpus, render_briefing, render_validation_status
    from agent_runner.stages import SELECTION_ATTEMPT_KINDS
    from publication_schema import parse_repair_actions

    attempts = manifest["attempts"]
    if not isinstance(attempts, list) or type(final.get("attempt")) is not int:
        raise ValueError("invalid final attempt")
    matches = [row for row in attempts if isinstance(row, dict)
               and type(row.get("index")) is int and row["index"] == final["attempt"]]
    if len(matches) != 1:
        raise ValueError("final attempt is not unique")
    attempt = matches[0]
    actions = list(parse_repair_actions(attempt.get("repair_actions")))
    selections = [row for row in attempts if isinstance(row, dict) and row.get("kind") in SELECTION_ATTEMPT_KINDS]
    if selections:
        actions.extend(action for action in parse_repair_actions(selections[-1].get("repair_actions"))
                       if action["action"] == PROMOTION_ACTION)
    corpus = read_json(selected / "corpus.json")
    candidate = read_json(selected / attempt["structured_artifact"])
    config = briefing_config.parse_config(read_json(selected / "briefing-config.json"))
    rendered = render_briefing(candidate, corpus, config, project_corpus(corpus).citations, actions)
    findings = eval_briefing.evaluate(corpus, rendered, config)
    outcome = classify_outcome(findings, corpus.get("errors", []),
                               coverage_degraded=corpus_schema.corpus_health_degraded(corpus))
    expected = rendered.rstrip() + "\n" + render_validation_status(findings, corpus, outcome=outcome)
    after = eval_briefing.evaluate(corpus, expected, config)
    def fingerprint(rows: list[eval_briefing.Finding]) -> list[tuple[str, str, str]]:
        return [(row.level, row.check, row.message) for row in rows]
    if fingerprint(after) != fingerprint(findings):
        outcome = classify_outcome(after, corpus.get("errors", []),
                                   coverage_degraded=corpus_schema.corpus_health_degraded(corpus))
        expected = rendered.rstrip() + "\n" + render_validation_status(after, corpus, outcome=outcome)
        if fingerprint(eval_briefing.evaluate(corpus, expected, config)) != fingerprint(after):
            raise ValueError("published validation footer did not stabilize")
    markdown = _read_bytes(selected / "final.md")
    if hashlib.sha256(markdown).hexdigest() != hashes["final.md"]:
        raise ValueError("public artifact changed during verification")
    if markdown.decode("utf-8") != expected:
        raise ValueError("published Markdown differs from the verified candidate")
    return selected, topics, hashes


def publication_receipt(runs_dir: Path, history_path: Path, workflow_run_id: int,
                        run_attempt: int) -> dict[str, Any]:
    """Bind newly generated ready reports to the exact successfully deployed build."""
    if type(workflow_run_id) is not int or workflow_run_id <= 0 or type(run_attempt) is not int or run_attempt <= 0:
        raise ValueError("invalid deployment workflow identity")
    history = read_json(history_path, max_bytes=50_000_000)
    entries = history.get("entries") if isinstance(history, dict) else None
    if not isinstance(entries, list):
        raise ValueError("invalid built history")
    reports = []
    skipped = []
    for root in sorted(path for path in runs_dir.iterdir() if path.is_dir()):
        try:
            day = date.fromisoformat(root.name).isoformat()
            selected, _topics, hashes = verified_publication(root)
            matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("date") == day]
            if len(matches) != 1:
                raise ValueError("report is absent or duplicated in the deployed build")
            entry = matches[0]
            markdown = entry.get("markdown")
            integrity = entry.get("integrity")
            if (entry.get("disposition") != "ready" or not isinstance(markdown, str)
                    or hashlib.sha256(markdown.encode()).hexdigest() != hashes["final.md"]
                    or not isinstance(integrity, dict) or integrity.get("workflow_run_id") != workflow_run_id):
                raise ValueError("built report is stale or does not match this workflow's candidate")
            reports.append({"date": day, "selected_run": selected.relative_to(root).as_posix(),
                            "artifact_hashes": hashes})
        except (ValueError, OSError, KeyError, TypeError, RecursionError, OverflowError) as exc:
            skipped.append({"date": root.name, "reason": str(exc)})
    return {"schema_version": 1, "workflow_run_id": workflow_run_id, "run_attempt": run_attempt,
            "deployment_status": "success", "reports": reports, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--workflow-run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = publication_receipt(args.runs_dir, args.history, args.workflow_run_id, args.run_attempt)
    write_json_atomic(args.output, receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
