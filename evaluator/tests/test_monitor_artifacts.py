import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any

from evaluator.monitor_artifacts import _extract_runs, artifact_pairs, deployment_succeeded


def artifact(name: str, run_id: int = 123, *, expired: bool = False) -> dict[str, Any]:
    return {"id": 1, "name": name, "expired": expired, "workflow_run": {"id": run_id}}


class MonitorArtifactsTests(unittest.TestCase):
    def test_pairs_are_exact_attempts_and_not_latest_seven_artifacts(self) -> None:
        artifacts = []
        for attempt in range(1, 10):
            artifacts.extend([artifact(f"briefing-diagnostics-123-{attempt}"),
                              artifact(f"briefing-publication-123-{attempt}")])
        pairs, excluded = artifact_pairs(artifacts)
        self.assertEqual(len(pairs), 9)
        self.assertEqual(excluded, [])
        self.assertEqual([attempt for _diagnostic, _receipt, attempt in pairs], list(range(1, 10)))

    def test_missing_legacy_wrong_attempt_expired_duplicate_and_wrong_identity_excluded(self) -> None:
        pairs, excluded = artifact_pairs([
            artifact("briefing-diagnostics-123"),
            artifact("briefing-diagnostics-123-1"),
            artifact("briefing-publication-123-2"),
            artifact("briefing-diagnostics-123-3"),
            artifact("briefing-publication-123-3", expired=True),
            artifact("briefing-diagnostics-123-4"),
            artifact("briefing-publication-123-4"), artifact("briefing-publication-123-4"),
            artifact("briefing-diagnostics-123-5", run_id=124),
        ])
        self.assertEqual(pairs, [])
        self.assertEqual(len(excluded), 5)
        self.assertTrue(all(row["reason"] for row in excluded))

    def test_only_real_deployment_step_success_proves_publication(self) -> None:
        workflow = {"head_branch": "main", "path": ".github/workflows/daily-briefing.yml"}
        jobs: dict[str, Any] = {"jobs": [{"conclusion": "failure", "steps": [
            {"name": "Generate scheduled daily or manual backfill reports", "conclusion": "success"},
            {"name": "Deploy to GitHub Pages", "conclusion": "failure"},
        ]}]}
        self.assertFalse(deployment_succeeded(workflow, jobs))
        jobs["jobs"][0]["steps"][-1]["conclusion"] = "success"
        # A later generation alert can fail the workflow while its exact page
        # deployment remains valid proof for the reports actually present.
        self.assertTrue(deployment_succeeded(workflow, jobs))
        workflow["head_branch"] = "feature"
        self.assertFalse(deployment_succeeded(workflow, jobs))

    def test_diagnostics_extract_regular_run_files_only_and_reject_escaping_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "diagnostics.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                content = b"{}"
                member = tarfile.TarInfo("runs/2026-09-01/run/manifest.json")
                member.size = len(content)
                stream.addfile(member, io.BytesIO(content))
                private = tarfile.TarInfo("corpora/private.json")
                private.size = len(content)
                stream.addfile(private, io.BytesIO(content))
            _extract_runs(archive, root / "safe")
            self.assertEqual((root / "safe/runs/2026-09-01/run/manifest.json").read_bytes(), b"{}")
            self.assertFalse((root / "safe/corpora").exists())
            with tarfile.open(archive, "w:gz") as stream:
                link = tarfile.TarInfo("runs/2026-09-01/escape")
                link.type = tarfile.SYMTYPE
                link.linkname = "/tmp"
                stream.addfile(link)
            with self.assertRaisesRegex(ValueError, "unsafe"):
                _extract_runs(archive, root / "unsafe")


if __name__ == "__main__":
    unittest.main()
