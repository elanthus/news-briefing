"""Shared publication resolution and frozen artifact verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent_runner.jev_review import MAX_ARTIFACT_BYTES, VerifiedRun, load_verified_run
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


def resolve_publication_run(
    root: Path,
) -> tuple[dict[str, Any] | None, Path | None, Path | None]:
    """Use the publication policy for accepted repairs and retained originals.

    Returns ``(audit, selected, original)``: the verified public audit or
    ``None``, the run to publish, and the original generation it came from.
    """
    original = selected_generation_run(root)
    if original is None:
        return None, None, None
    if (root / "jev-review" / "audit.json").is_file():
        return (*load_public_audit(original, root / "jev-review"), original)
    return None, original, original


def _verified_publication_artifacts(
    root: Path,
) -> tuple[Path, VerifiedRun, dict[str, str]]:
    """Verify artifact identity without replaying a potentially newer renderer."""
    _audit, selected, _original = resolve_publication_run(root)
    if selected is None:
        raise ValueError("no ready publication candidate")
    state = load_verified_run(selected)
    hashes = state.hashes
    manifest_bytes = _read_bytes(selected / "manifest.json")
    if hashlib.sha256(manifest_bytes).hexdigest() != hashes["manifest.json"]:
        raise ValueError("publication manifest changed during verification")
    manifest = state.manifest
    final = manifest["final"]
    if final.get("artifact_type") != "final" or final.get("run_artifact") != "final.md":
        raise ValueError("invalid public artifact binding")
    markdown = _read_bytes(selected / "final.md")
    markdown.decode("utf-8")
    digest = hashlib.sha256(markdown).hexdigest()
    if manifest["artifacts"].get("final.md") != digest or final.get("output_sha256") != digest:
        raise ValueError("public artifact hash mismatch")
    return selected, state, {**hashes, "final.md": digest}


def verified_publication(root: Path) -> tuple[Path, list[dict[str, Any]], dict[str, str]]:
    """Verify newly published Markdown against this revision's exact rendering."""
    selected, state, hashes = _verified_publication_artifacts(root)
    manifest = state.manifest
    final = manifest["final"]
    # Re-render the verified candidate and its code-owned validation footer.
    # A prefix match alone would permit unreviewed prose after the footer.
    from agent_runner.output import complete_briefing, render_briefing
    from agent_runner.runner import promotion_actions
    from publication_schema import parse_repair_actions

    attempts = manifest["attempts"]
    if not isinstance(attempts, list) or type(final.get("attempt")) is not int:
        raise ValueError("invalid final attempt")
    matches = [row for row in attempts if isinstance(row, dict)
               and type(row.get("index")) is int and row["index"] == final["attempt"]]
    if len(matches) != 1:
        raise ValueError("final attempt is not unique")
    attempt = matches[0]
    actions = [*parse_repair_actions(attempt.get("repair_actions")), *promotion_actions(attempts)]
    rendered = render_briefing(state.candidate, state.corpus, state.config, state.projected.citations, actions)
    expected, _findings, _outcome = complete_briefing(rendered, state.corpus, state.config)
    markdown = _read_bytes(selected / "final.md")
    if hashlib.sha256(markdown).hexdigest() != hashes["final.md"]:
        raise ValueError("public artifact changed during verification")
    if markdown.decode("utf-8") != expected:
        raise ValueError("published Markdown differs from the verified candidate")
    return selected, state.topics, hashes
