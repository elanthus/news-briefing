from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from prepare_publication import prepare_publication


class PreparePublicationTests(unittest.TestCase):
    def test_copies_hash_bound_review_preview_and_detailed_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            preview = b"review-required preview\n"
            (run / "preview.md").write_bytes(preview)
            findings = [
                {
                    "level": "WARN",
                    "check": "unsupported_figure",
                    "domain": "evidence",
                    "message": "Verify the figure.",
                }
            ]
            self._write_manifest(run, "review_required", "preview", "preview.md", preview, findings)
            corpus = root / "corpus.json"
            corpus.write_text(
                json.dumps(
                    {
                        "errors": [
                            {"source_type": "reddit", "source_id": "cursor"},
                            {"source_type": "reddit", "source_id": "cursor"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            history = root / "history"
            record = prepare_publication(run, corpus, history, date(2026, 8, 20))

            self.assertEqual(record.disposition, "review_required")
            self.assertEqual(record.findings_count, 1)
            self.assertEqual(record.findings[0].message, "Verify the figure.")
            self.assertEqual(record.degraded_sources, ("reddit:cursor",))
            self.assertEqual((history / "2026-08-20.md").read_bytes(), preview)
            sidecar = json.loads((history / "2026-08-20.json").read_text())
            self.assertEqual(sidecar["findings"], findings)

    def test_copies_hash_bound_ready_final_without_exposing_finding_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            content = b"ready briefing\n"
            (run / "final.md").write_bytes(content)
            raw_findings = [
                {"level": "WARN", "check": "editorial", "domain": "editorial", "message": "note"}
            ]
            self._write_manifest(run, "ready", "final", "final.md", content, raw_findings)

            history = root / "history"
            record = prepare_publication(run, root / "missing-corpus.json", history, date(2026, 8, 20))

            self.assertEqual(record.disposition, "ready")
            self.assertEqual(record.findings_count, 1)
            self.assertEqual(record.findings, ())
            self.assertEqual((history / "2026-08-20.md").read_bytes(), content)

    def test_rejected_run_remains_status_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            preview = b"rejected preview"
            (run / "preview.md").write_bytes(preview)
            findings = [
                {"level": "ERROR", "check": "ungrounded_link", "domain": "evidence", "message": "bad link"}
            ]
            self._write_manifest(run, "rejected", "preview", "preview.md", preview, findings)

            history = root / "history"
            record = prepare_publication(run, root / "corpus.json", history, date(2026, 8, 20))

            self.assertEqual(record.disposition, "rejected")
            self.assertEqual(record.findings_count, 1)
            self.assertEqual(record.findings, ())
            self.assertFalse((history / "2026-08-20.md").exists())

    def test_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            content = b"tampered preview"
            (run / "preview.md").write_bytes(content)
            findings = [
                {
                    "level": "WARN",
                    "check": "unsupported_figure",
                    "domain": "evidence",
                    "message": "Verify it.",
                }
            ]
            self._write_manifest(run, "review_required", "preview", "preview.md", b"different", findings)

            history = root / "history"
            record = prepare_publication(run, root / "corpus.json", history, date(2026, 8, 20))

            self.assertEqual(record.disposition, "blocked")
            self.assertEqual(record.findings_count, 0)
            self.assertFalse((history / "2026-08-20.md").exists())

    def test_malformed_review_finding_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            content = b"preview"
            (run / "preview.md").write_bytes(content)
            findings = [{"level": "WARN", "check": "unsupported_figure", "message": "missing domain"}]
            self._write_manifest(run, "review_required", "preview", "preview.md", content, findings)

            record = prepare_publication(
                run,
                root / "corpus.json",
                root / "history",
                date(2026, 8, 20),
            )

            self.assertEqual(record.disposition, "blocked")
            self.assertEqual(record.findings, ())

    @staticmethod
    def _write_manifest(
        run: Path,
        status: str,
        artifact_type: str,
        artifact_name: str,
        digest_content: bytes,
        findings: list[dict[str, str]],
    ) -> None:
        (run / "manifest.json").write_text(
            json.dumps(
                {
                    "status": "complete",
                    "artifacts": {artifact_name: hashlib.sha256(digest_content).hexdigest()},
                    "final": {
                        "status": status,
                        "artifact_type": artifact_type,
                        "run_artifact": artifact_name,
                        "findings": findings,
                    },
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
