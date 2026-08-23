from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from prepare_publication import prepare_publication


class PreparePublicationTests(unittest.TestCase):
    def test_resolves_selected_ready_run_from_fallback_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chain = root / "run"
            selected = chain / "02-deepseek-deepseek-v4-flash-0731"
            selected.mkdir(parents=True)
            content = b"fallback briefing\n"
            (selected / "final.md").write_bytes(content)
            self._write_manifest(selected, "ready", "final", "final.md", content, [])
            (chain / "fallback-log.json").write_text(
                json.dumps(
                    {
                        "status": "ready",
                        "selected_run_dir": selected.name,
                        "selected_model": "deepseek/deepseek-v4-flash-0731",
                    }
                ),
                encoding="utf-8",
            )

            history = root / "history"
            record = prepare_publication(
                chain, root / "missing-corpus.json", history, date(2026, 8, 20)
            )

            self.assertEqual(record.disposition, "ready")
            self.assertEqual((history / "2026-08-20.md").read_bytes(), content)

    def test_fallback_log_without_a_safe_ready_selection_fails_closed(self) -> None:
        invalid_logs: tuple[object, ...] = (
            {"status": "ready", "selected_run_dir": ""},
            {"status": "ready", "selected_run_dir": ".."},
            {"status": "ready", "selected_run_dir": "../outside"},
            {"status": "failed", "selected_run_dir": "01-tencent-hy3"},
            ["malformed"],
        )
        for fallback_log in invalid_logs:
            with self.subTest(fallback_log=fallback_log), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                chain = root / "run"
                chain.mkdir()
                stale_content = b"stale root briefing\n"
                (chain / "final.md").write_bytes(stale_content)
                self._write_manifest(
                    chain, "ready", "final", "final.md", stale_content, []
                )
                (chain / "fallback-log.json").write_text(
                    json.dumps(fallback_log),
                    encoding="utf-8",
                )

                history = root / "history"
                record = prepare_publication(
                    chain, root / "missing-corpus.json", history, date(2026, 8, 20)
                )

                self.assertEqual(record.disposition, "blocked")
                self.assertFalse((history / "2026-08-20.md").exists())

    def test_fallback_selection_cannot_escape_through_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chain = root / "run"
            outside = root / "outside"
            chain.mkdir()
            outside.mkdir()
            content = b"outside briefing\n"
            (outside / "final.md").write_bytes(content)
            self._write_manifest(outside, "ready", "final", "final.md", content, [])
            (chain / "outbound").symlink_to(outside, target_is_directory=True)
            (chain / "fallback-log.json").write_text(
                json.dumps({"status": "ready", "selected_run_dir": "outbound"}),
                encoding="utf-8",
            )

            history = root / "history"
            record = prepare_publication(
                chain, root / "missing-corpus.json", history, date(2026, 8, 20)
            )

            self.assertEqual(record.disposition, "blocked")
            self.assertFalse((history / "2026-08-20.md").exists())

    def test_ready_fallback_log_cannot_select_a_review_required_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chain = root / "run"
            selected = chain / "01-tencent-hy3"
            selected.mkdir(parents=True)
            preview = b"review-required fallback preview\n"
            (selected / "preview.md").write_bytes(preview)
            self._write_manifest(
                selected,
                "review_required",
                "preview",
                "preview.md",
                preview,
                [
                    {
                        "level": "WARN",
                        "check": "unsupported_quotation",
                        "domain": "evidence",
                        "message": "Verify the selected fallback report.",
                    }
                ],
            )
            (chain / "fallback-log.json").write_text(
                json.dumps(
                    {
                        "status": "ready",
                        "selected_run_dir": selected.name,
                        "selected_model": "tencent/hy3",
                    }
                ),
                encoding="utf-8",
            )

            history = root / "history"
            record = prepare_publication(
                chain, root / "missing-corpus.json", history, date(2026, 8, 20)
            )

            self.assertEqual(record.disposition, "blocked")
            self.assertFalse((history / "2026-08-20.md").exists())

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

    def test_structured_paths_attach_included_and_excluded_story_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            preview = b"review-required preview\n"
            (run / "preview.md").write_bytes(preview)
            findings = [
                {
                    "level": "ERROR",
                    "check": "category_ineligible_ref",
                    "domain": "editorial",
                    "message": (
                        "topics.AI News[0] uses citation_0001 from ineligible "
                        "category us_politics"
                    ),
                },
                {
                    "level": "ERROR",
                    "check": "duplicate_item",
                    "domain": "editorial",
                    "message": (
                        "excluded_topics.US News[0] repeats item_0001, already used "
                        "by topics.US Politics[0]"
                    ),
                },
            ]
            structured = {
                "sections": {
                    "AI News": {"topics": [{
                        "headline": "AI story",
                        "summary": "Summary",
                        "citation_refs": ["citation_0001"],
                    }]},
                },
                "excluded_topics": {
                    "US News": [{
                        "headline": "Excluded story",
                        "reason": "Lower impact",
                        "citation_refs": ["citation_0002"],
                    }],
                },
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

            record = prepare_publication(
                run,
                root / "corpus.json",
                root / "history",
                date(2026, 8, 20),
            )

            included_context = record.findings[0].context
            excluded_context = record.findings[1].context
            self.assertIsNotNone(included_context)
            self.assertIsNotNone(excluded_context)
            assert included_context is not None
            assert excluded_context is not None
            self.assertEqual(included_context.section, "AI News")
            self.assertEqual(included_context.headline, "AI story")
            self.assertEqual(
                excluded_context.section,
                "Excluded Topics: US News",
            )
            self.assertEqual(excluded_context.headline, "Excluded story")

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

    def test_deterministic_repair_copies_repair_actions_to_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            content = b"ready briefing after repair\n"
            (run / "final.md").write_bytes(content)
            repair_actions = [
                {"action": "drop_entry", "path": "topics.AI News[1]", "reason": "duplicate item"},
                {"action": "drop_ref", "path": "topics.US News[0]", "reason": "ineligible category"},
            ]
            self._write_manifest(
                run, "ready", "final", "final.md", content, [],
                structured={"sections": {"AI News": {"topics": []}}},
                final_attempt_kind="deterministic_repair",
                final_repair_actions=repair_actions,
            )

            record = prepare_publication(
                run, root / "corpus.json", root / "history", date(2026, 8, 20),
            )

            self.assertEqual(record.disposition, "ready")
            self.assertEqual(len(record.repair_actions), 2)
            self.assertEqual(record.repair_actions[0]["action"], "drop_entry")
            self.assertEqual(record.repair_actions[0]["path"], "topics.AI News[1]")
            sidecar = json.loads((root / "history/2026-08-20.json").read_text())
            self.assertEqual(sidecar["repair_actions"], repair_actions)

    def test_ready_run_without_repair_has_empty_repair_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            content = b"ready briefing\n"
            (run / "final.md").write_bytes(content)
            self._write_manifest(run, "ready", "final", "final.md", content, [])

            record = prepare_publication(
                run, root / "corpus.json", root / "history", date(2026, 8, 20),
            )

            self.assertEqual(record.disposition, "ready")
            self.assertEqual(record.repair_actions, ())
            sidecar = json.loads((root / "history/2026-08-20.json").read_text())
            self.assertEqual(sidecar["repair_actions"], [])

    def test_rejected_run_keeps_repair_actions_out_of_the_sidecar(self) -> None:
        # Non-public dispositions retain only findings_count; repair provenance
        # is published only alongside a public artifact.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            preview = b"rejected preview\n"
            (run / "preview.md").write_bytes(preview)
            findings = [
                {
                    "level": "ERROR",
                    "check": "unknown_citation_ref",
                    "domain": "evidence",
                    "message": "topics.AI News[0] contains unknown citation ref 'citation_9999'",
                },
            ]
            self._write_manifest(
                run, "rejected", "preview", "preview.md", preview, findings,
                structured={"sections": {"AI News": {"topics": []}}},
                final_attempt_kind="deterministic_repair",
                final_repair_actions=[
                    {
                        "action": "drop_entry",
                        "path": "topics.AI News[1]",
                        "reason": "duplicate item",
                    },
                ],
            )

            record = prepare_publication(
                run, root / "corpus.json", root / "history", date(2026, 8, 20),
            )

            self.assertEqual(record.disposition, "rejected")
            self.assertEqual(record.repair_actions, ())
            sidecar = json.loads((root / "history/2026-08-20.json").read_text())
            self.assertEqual(sidecar["repair_actions"], [])

    def test_review_required_findings_carry_structured_path(self) -> None:
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
                        "topics.AI News[0].summary includes a quotation "
                        "not supported by the cited corpus excerpts"
                    ),
                },
            ]
            structured = {
                "sections": {
                    "AI News": {"topics": [{
                        "headline": "Big AI story",
                        "summary": "Summary here",
                        "citation_refs": ["citation_0001"],
                    }]},
                },
            }
            self._write_manifest(
                run, "review_required", "preview", "preview.md", preview, findings,
                structured=structured,
            )

            record = prepare_publication(
                run, root / "corpus.json", root / "history", date(2026, 8, 20),
            )

            self.assertEqual(record.disposition, "review_required")
            ctx = record.findings[0].context
            self.assertIsNotNone(ctx)
            assert ctx is not None
            self.assertEqual(ctx.path, "topics.AI News[0]")
            self.assertEqual(ctx.section, "AI News")
            sidecar = json.loads((root / "history/2026-08-20.json").read_text())
            self.assertEqual(sidecar["findings"][0]["context"]["path"], "topics.AI News[0]")

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
        final_attempt_kind: str | None = None,
        final_repair_actions: list[dict[str, str]] | None = None,
    ) -> None:
        artifacts = {artifact_name: hashlib.sha256(digest_content).hexdigest()}
        attempts: list[dict[str, object]] = []
        final_attempt: dict[str, object] = {}
        if structured is not None:
            structured_content = json.dumps(structured, ensure_ascii=False).encode("utf-8")
            # The manifest keeps the declared name, but the file is always written
            # flat, so a nested structured_name stays unresolvable on disk.
            (run / Path(structured_name).name).write_bytes(structured_content)
            artifacts[structured_name] = hashlib.sha256(structured_content).hexdigest()
            attempt_row: dict[str, object] = {
                "index": attempt_index,
                "structured_artifact": structured_name,
            }
            if final_attempt_kind is not None:
                attempt_row["kind"] = final_attempt_kind
            if final_repair_actions is not None:
                attempt_row["repair_actions"] = final_repair_actions
            attempts.append(attempt_row)
            # final.attempt is pinned to 1: an attempt_index other than 1 makes the
            # final attempt unmatchable, which the malformed-metadata test relies on.
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
