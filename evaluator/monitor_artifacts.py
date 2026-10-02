"""Download diagnostic/receipt pairs with independent Actions deployment proof."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from agent_runner.checkpoint import write_json_atomic
from agent_runner.publication import read_json

MAX_DOWNLOAD_BYTES = 64_000_000
MAX_EXTRACTED_BYTES = 256_000_000
DIAGNOSTIC_NAME = re.compile(r"briefing-diagnostics-(\d+)-(\d+)\Z")


def artifact_pairs(
    artifacts: list[dict[str, Any]],
) -> tuple[list[tuple[dict[str, Any], dict[str, Any], int]], list[dict[str, str]]]:
    """Pair exact workflow attempts, retaining every available report-date candidate."""
    active = [row for row in artifacts if row.get("expired") is False]
    pairs = []
    excluded = []
    for diagnostic in active:
        name = diagnostic.get("name")
        if not isinstance(name, str) or not name.startswith("briefing-diagnostics-"):
            continue
        match = DIAGNOSTIC_NAME.fullmatch(name)
        if match is None:
            excluded.append({"run_id": name, "reason": "legacy_diagnostics_without_attempt_bound_publication_proof"})
            continue
        run_id, attempt = map(int, match.groups())
        workflow = diagnostic.get("workflow_run")
        if not isinstance(workflow, dict) or workflow.get("id") != run_id:
            excluded.append({"run_id": name, "reason": "diagnostics_workflow_identity_mismatch"})
            continue
        receipts = [row for row in active if row.get("name") == f"briefing-publication-{run_id}-{attempt}"
                    and isinstance(row.get("workflow_run"), dict) and row["workflow_run"].get("id") == run_id]
        if len(receipts) != 1:
            excluded.append({"run_id": name, "reason": "missing_or_duplicate_post_deployment_receipt"})
            continue
        pairs.append((diagnostic, receipts[0], attempt))
    return pairs, excluded


def deployment_succeeded(workflow: dict[str, Any], jobs: dict[str, Any]) -> bool:
    """Generation/job success alone does not establish deployment success."""
    return (workflow.get("head_branch") == "main"
            and workflow.get("path") == ".github/workflows/daily-briefing.yml"
            and any(isinstance(step, dict) and step.get("name") == "Deploy to GitHub Pages"
                    and step.get("conclusion") == "success"
                    for job in jobs.get("jobs", []) if isinstance(job, dict)
                    for step in job.get("steps", []) if isinstance(step, dict)))


def _gh_json(endpoint: str) -> Any:
    result = subprocess.run(["gh", "api", endpoint], check=True, capture_output=True)
    if len(result.stdout) > 4_000_000:
        raise ValueError("Actions API response exceeds the bound")
    return json.loads(result.stdout)


def _download(endpoint: str, target: Path) -> None:
    with target.open("wb") as output:
        subprocess.run(["gh", "api", endpoint], check=True, stdout=output, stderr=subprocess.DEVNULL)
    if target.stat().st_size > MAX_DOWNLOAD_BYTES:
        raise ValueError("Actions artifact exceeds the download bound")


def _zip_member(path: Path, basename: str, destination: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        matches = [member for member in archive.infolist() if not member.is_dir()
                   and Path(member.filename).name == basename]
        if len(matches) != 1 or matches[0].file_size > MAX_DOWNLOAD_BYTES:
            raise ValueError("missing, duplicate, or oversized artifact member")
        with archive.open(matches[0]) as source, destination.open("wb") as output:
            remaining = MAX_DOWNLOAD_BYTES
            while block := source.read(min(65536, remaining + 1)):
                remaining -= len(block)
                if remaining < 0:
                    raise ValueError("artifact exceeds the extracted bound")
                output.write(block)


def _extract_runs(archive_path: Path, destination: Path) -> None:
    """Extract regular run files only, with containment and total-byte bounds."""
    total = 0
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            parts = Path(member.name).parts
            if not parts or parts[0] != "runs":
                continue
            if member.isdir():
                continue
            if (not member.isfile() or Path(member.name).is_absolute() or ".." in parts
                    or member.size < 0 or member.size > MAX_DOWNLOAD_BYTES):
                raise ValueError("unsafe diagnostic archive member")
            total += member.size
            if total > MAX_EXTRACTED_BYTES:
                raise ValueError("diagnostic archive exceeds the extraction bound")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("unreadable diagnostic archive member")
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError("duplicate diagnostic archive member")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=65536)


def download_runs(
    repository: str, artifacts: list[dict[str, Any]], output: Path,
) -> tuple[list[str], list[dict[str, str]]]:
    output.mkdir(parents=True, exist_ok=True)
    pairs, exclusions = artifact_pairs(artifacts)
    args: list[str] = []
    for diagnostic, receipt_artifact, attempt in pairs:
        name = diagnostic["name"]
        run_id = diagnostic["workflow_run"]["id"]
        try:
            workflow = _gh_json(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
            jobs = _gh_json(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
            if not isinstance(workflow, dict) or not isinstance(jobs, dict) or not deployment_succeeded(workflow, jobs):
                raise ValueError("Pages deployment is not independently confirmed successful")
            with tempfile.TemporaryDirectory(dir=output.parent) as directory:
                work = Path(directory)
                receipt_zip = work / "receipt.zip"
                _download(f"repos/{repository}/actions/artifacts/{receipt_artifact['id']}/zip", receipt_zip)
                receipt_path = work / "publication-receipt.json"
                _zip_member(receipt_zip, receipt_path.name, receipt_path)
                receipt = read_json(receipt_path)
                if (not isinstance(receipt, dict) or receipt.get("workflow_run_id") != run_id
                        or receipt.get("run_attempt") != attempt or receipt.get("deployment_status") != "success"):
                    raise ValueError("receipt and deployed workflow attempt differ")
                # Workflow IDs order starts, not deployment completions: an
                # older workflow can be rerun after a newer report deployed.
                completed = [step.get("completed_at") for job in jobs["jobs"] for step in job.get("steps", [])
                             if step.get("name") == "Deploy to GitHub Pages" and step.get("conclusion") == "success"]
                if len(completed) != 1 or not isinstance(completed[0], str):
                    raise ValueError("successful deployment has no unique completion timestamp")
                stamp = datetime.fromisoformat(completed[0].replace("Z", "+00:00"))
                if stamp.utcoffset() is None:
                    raise ValueError("deployment completion timestamp has no timezone")
                receipt["deployment_completed_at"] = stamp.isoformat()
                receipt["actions_deployment_verified"] = True
                write_json_atomic(receipt_path, receipt)
                diagnostic_zip = work / "diagnostic.zip"
                _download(f"repos/{repository}/actions/artifacts/{diagnostic['id']}/zip", diagnostic_zip)
                encrypted = work / "briefing-diagnostics.tar.gz.enc"
                _zip_member(diagnostic_zip, encrypted.name, encrypted)
                decrypted = work / "diagnostics.tar.gz"
                subprocess.run([sys.executable, "private_archive.py", "decrypt", str(encrypted), str(decrypted)],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                extracted = work / "extracted"
                _extract_runs(decrypted, extracted)
                runs = extracted / "runs"
                if not runs.is_dir():
                    raise ValueError("diagnostics contain no run directories")
                for day in sorted(path for path in runs.iterdir() if path.is_dir()):
                    if date.fromisoformat(day.name).isoformat() != day.name:
                        raise ValueError("invalid diagnostic report-date directory")
                    destination = output / str(diagnostic["id"]) / day.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(day, destination)
                    shutil.copyfile(receipt_path, destination / "publication-receipt.json")
                    args.extend(("--run", f"{run_id}/{attempt}/{day.name}={destination}"))
        except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError, tarfile.TarError,
                zipfile.BadZipFile) as exc:
            # Keep raw API/provider/archive text private; reasons are code-owned.
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            exclusions.append({"run_id": name, "reason": reason})
    return args, exclusions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-pages", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclusions", type=Path, required=True)
    args = parser.parse_args()
    pages = read_json(args.artifact_pages)
    artifacts = [artifact for page in pages for artifact in page["artifacts"]]
    run_args, exclusions = download_runs(args.repository, artifacts, args.output)
    write_json_atomic(args.exclusions, exclusions)
    for excluded in exclusions:
        print(f"Skipped {excluded['run_id']}: {excluded['reason']}")
    with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as environment:
        environment.write("MONITOR_RUN_ARGS<<MONITOR_RUN_ARGS_EOF\n" + "\n".join(run_args) + "\nMONITOR_RUN_ARGS_EOF\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
