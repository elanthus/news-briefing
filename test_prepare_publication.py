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
                    "check": "unsupported_quotation",
                    "domain": "evidence",
                    "message": (
                        "US Politics: 'Fuel restrictions end early' includes a quotation "
                        "not supported by the cited corpus excerpts"
                    ),
                },
                {
                    "level": "WARN",
                    "check": "unsupported_figure",
                    "domain": "quality",
                    "message": "US Politics: 'Fuel restrictions end early' states '1'",
                },
            ]
            structured = {
                "sections": {
                    "US Politics": {
                        "topics": [
                            {
                                "headline": "Fuel restrictions end early",
                                "summary": "Starts Sept. 1; see https://model.example/claim",
                                "citation_refs": ["citation_0001"],
                            }
                        ]
                    }
                }
            }
            self._write_manifest(
                run,
                "review_required",
                "preview",
                "preview.md",
                preview,
                findings,
                structured=structured,
            )
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
            self.assertIn("includes a quotation", record.findings[0].message)
            self.assertIsNotNone(record.findings[0].context)
            assert record.findings[0].context is not None
            self.assertEqual(record.findings[0].context.section, "US Politics")
            self.assertIn("https://model.example/claim", record.findings[0].context.model_authored)
            self.assertEqual(record.degraded_sources, ("reddit:cursor",))
            self.assertEqual((history / "2026-08-20.md").read_bytes(), preview)
            sidecar = json.loads((history / "2026-08-20.json").read_text())
            self.assertEqual(sidecar["findings"][0]["message"], findings[0]["message"])
            self.assertNotIn("unsupported_figure", json.dumps(sidecar))
            self.assertEqual(
                sidecar["findings"][0]["context"]["headline"],
                "Fuel restrictions end early",
            )

    def test_copies_hash_bound_ready_final_without_exposing_finding_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            content = b"ready briefing\n"
            (run / "final.md").write_bytes(content)
            raw_findings = [
                {"level": "WARN", "check": "editorial", "domain": "editorial", "message": "note"},
                {
                    "level": "WARN",
                    "check": "unsupported_figure",
                    "domain": "quality",
                    "message": "figure absent from excerpt",
                },
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
                    "check": "unsupported_quotation",
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

    def test_tampered_structured_artifact_is_not_exposed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            preview = "## US Politics\n\n**Fuel story** — Starts Sept. 1.\n".encode()
            (run / "preview.md").write_bytes(preview)
            findings = [
                {
                    "level": "WARN",
                    "check": "unsupported_quotation",
                    "domain": "evidence",
                    "message": "US Politics: 'Fuel story' states '1', which is unsupported",
                }
            ]
            self._write_manifest(
                run,
                "review_required",
                "preview",
                "preview.md",
                preview,
                findings,
                structured={
                    "sections": {
                        "US Politics": {
                            "topics": [{"headline": "Fuel story", "summary": "Starts Sept. 1."}]
                        }
                    }
                },
            )
            (run / "attempt-01-structured.json").write_text(
                '{"sections":{"US Politics":{"topics":[{"headline":"Fuel story",'
                '"summary":"https://attacker.invalid"}]}}}',
                encoding="utf-8",
            )

            record = prepare_publication(
                run,
                root / "corpus.json",
                root / "history",
                date(2026, 8, 20),
            )

            self.assertEqual(record.disposition, "review_required")
            self.assertIsNone(record.findings[0].context)
            self.assertNotIn(
                "attacker.invalid",
                (root / "history/2026-08-20.json").read_text(encoding="utf-8"),
            )

    def test_malformed_structured_attempt_metadata_is_not_exposed(self) -> None:
        malformed = (
            {"structured_name": "nested/attempt-01-structured.json"},
            {"attempt_index": 2},
        )
        for overrides in malformed:
            with self.subTest(**overrides), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run = root / "run"
                run.mkdir()
                preview = "## US Politics\n\n**Fuel story** — Starts Sept. 1.\n".encode()
                (run / "preview.md").write_bytes(preview)
                findings = [{
                    "level": "WARN",
                    "check": "unsupported_quotation",
                    "domain": "evidence",
                    "message": "US Politics: 'Fuel story' states '1', which is unsupported",
                }]
                self._write_manifest(
                    run,
                    "review_required",
                    "preview",
                    "preview.md",
                    preview,
                    findings,
                    structured={"sections": {"US Politics": {"topics": [{
                        "headline": "Fuel story",
                        "summary": "Starts Sept. 1.",
                    }]}}},
                    **overrides,
                )

                record = prepare_publication(
                    run,
                    root / "corpus.json",
                    root / "history",
                    date(2026, 8, 20),
                )

                self.assertEqual(record.disposition, "review_required")
                self.assertIsNone(record.findings[0].context)

    def test_malformed_review_finding_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            content = b"preview"
            (run / "preview.md").write_bytes(content)
            findings = [{"level": "WARN", "check": "unsupported_quotation", "message": "missing domain"}]
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
        *,
        structured: dict[str, object] | None = None,
        structured_name: str = "attempt-01-structured.json",
        attempt_index: int = 1,
    ) -> None:
        artifacts = {artifact_name: hashlib.sha256(digest_content).hexdigest()}
        attempts: list[dict[str, object]] = []
        final_attempt: dict[str, object] = {}
        if structured is not None:
            structured_content = json.dumps(structured, ensure_ascii=False).encode("utf-8")
            (run / Path(structured_name).name).write_bytes(structured_content)
            artifacts[structured_name] = hashlib.sha256(structured_content).hexdigest()
            attempts.append({"index": attempt_index, "structured_artifact": structured_name})
            final_attempt["attempt"] = 1
        (run / "manifest.json").write_text(
            json.dumps(
                {
                    "status": "complete",
                    "artifacts": artifacts,
                    "attempts": attempts,
                    "final": {
                        "status": status,
                        "artifact_type": artifact_type,
                        "run_artifact": artifact_name,
                        "findings": findings,
                        **final_attempt,
                    },
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
