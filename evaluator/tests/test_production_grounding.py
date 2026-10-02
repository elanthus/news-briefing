import importlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agent_runner.checkpoint import sha256_file, write_json_atomic
from agent_runner.jev_review import load_topics
from agent_runner.publication import publication_receipt, verified_publication
from agent_runner.semantic_repairs import daily_semantic_review
from evaluator.adapters import Adapter, Generation
from evaluator.production_grounding import (
    ProductionRun,
    export_production_grounding_packets,
    production_run_topics,
    resolve_ready_run_dir,
    run_weekly_monitor,
    sample_published_runs,
    upsert_weekly_log,
    verified_run_topics,
    week_window,
)

# Core fixtures deliberately remain outside the evaluator's typed module set.
# Import their offline providers dynamically without extending that type gate.
_fixture_helpers = importlib.import_module("tests.test_semantic_repairs")
Judge = _fixture_helpers.Judge
PatchProvider = _fixture_helpers.PatchProvider
make_run = _fixture_helpers.make_run

LEGACY_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "production-run"


def prepared_day(root: Path, day: str = "2026-09-01", *, workflow_id: int = 123,
                 run_attempt: int = 1) -> tuple[Path, Path, str]:
    runs = root / "runs"
    runs.mkdir(exist_ok=True)
    day_dir = runs / day
    day_dir.mkdir()
    original, headline = make_run(day_dir)
    write_json_atomic(day_dir / "fallback-log.json", {"status": "ready", "selected_run_dir": "run"})
    attach_receipt(day_dir, root, workflow_id=workflow_id, run_attempt=run_attempt)
    return day_dir, original, headline


def attach_receipt(day_dir: Path, root: Path, *, workflow_id: int = 123, run_attempt: int = 1) -> dict[str, Any]:
    selected = resolve_ready_run_dir(day_dir)
    assert selected is not None
    history = root / "history.json"
    write_json_atomic(history, {"entries": [{
        "date": day_dir.name, "disposition": "ready", "integrity": {"workflow_run_id": workflow_id},
        "markdown": (selected / "final.md").read_text(),
    }]})
    receipt = publication_receipt(day_dir.parent, history, workflow_id, run_attempt)
    # The downloader adds independently verified Actions step metadata. These
    # offline fixtures represent that API read-back rather than real deployments.
    receipt["deployment_completed_at"] = f"{day_dir.name}T13:45:{run_attempt:02d}+00:00"
    receipt["actions_deployment_verified"] = True
    write_json_atomic(day_dir / "publication-receipt.json", receipt)
    return receipt


class FakeJudge(Adapter):
    provider = "offline-grounding-judge"

    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        topics = json.loads(prompt.split("TOPICS:\n", 1)[1].split("\n\nReturn JSON only", 1)[0])
        return Generation(text=json.dumps({"reviews": [
            {"review_id": topic["review_id"], "grounding_error": False, "rationale": "Supported."}
            for topic in topics]}), latency_ms=1, input_tokens=10, output_tokens=5, cost_usd=0.01)


class VerifiedProductionTests(unittest.TestCase):
    def test_frozen_legacy_diagnostics_are_explicitly_unverifiable(self) -> None:
        self.assertEqual(production_run_topics(LEGACY_FIXTURE, "legacy"), [])
        self.assertIsNotNone(resolve_ready_run_dir(LEGACY_FIXTURE))

    def test_verified_records_are_blinded_and_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            day, original, _headline = prepared_day(Path(directory))
            records, identity = verified_run_topics(ProductionRun("first", day))
            self.assertTrue(records)
            self.assertTrue(all(row["public"]["evidence"] for row in records))
            self.assertEqual(identity["artifact_hashes"]["manifest.json"], sha256_file(original / "manifest.json"))
            self.assertNotRegex(json.dumps([row["public"] for row in records]), r'https?://|\b(?:citation|item)_\d+\b')

    def test_applied_repair_resolves_exact_published_headline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day, original, headline = prepared_day(root)
            daily_semantic_review(original, day / "jev-review", apply_repairs=True,
                                  judge=Judge(headline), repair_provider=PatchProvider())
            repaired = day / "jev-review" / "repair" / "run"
            self.assertEqual(resolve_ready_run_dir(day), repaired)
            # Original receipt cannot establish publication of the repaired candidate.
            self.assertEqual(production_run_topics(day, "before"), [])
            receipt = attach_receipt(day, root)
            self.assertEqual(receipt["reports"][0]["selected_run"], "jev-review/repair/run")
            records = production_run_topics(day, "after")
            self.assertIn("Repaired headline", [row["public"]["title"] for row in records])
            self.assertNotIn(headline, [row["public"]["title"] for row in records])

    def test_rejected_failed_partial_and_candidate_only_repairs_retain_original(self) -> None:
        cases = [
            {"apply_repairs": False},
            {"apply_repairs": True, "reject_after": True},
            {"apply_repairs": True, "fail_after_repair": True},
            {"apply_repairs": True, "incomplete": True},
            {"apply_repairs": True, "missing_cost": True},
        ]
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                day, original, headline = prepared_day(Path(directory))
                daily_semantic_review(original, day / "jev-review", apply_repairs=case["apply_repairs"],
                                      judge=Judge(headline, reject_after=case.get("reject_after", False),
                                                  fail_after_repair=case.get("fail_after_repair", False),
                                                  missing_cost=case.get("missing_cost", False)),
                                      repair_provider=PatchProvider(incomplete=case.get("incomplete", False)))
                self.assertEqual(resolve_ready_run_dir(day), original)
                self.assertIn(headline, [row["public"]["title"] for row in production_run_topics(day, "retained")])

    def test_missing_proof_and_failed_deployment_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            day, _original, _headline = prepared_day(Path(directory))
            receipt_path = day / "publication-receipt.json"
            receipt = json.loads(receipt_path.read_text())
            receipt_path.unlink()
            self.assertEqual(production_run_topics(day, "missing"), [])
            receipt["deployment_status"] = "failed"
            write_json_atomic(receipt_path, receipt)
            self.assertEqual(production_run_topics(day, "failed"), [])

    def test_tampered_candidate_evidence_and_markdown_are_rejected(self) -> None:
        for artifact in ("selected-evidence.json", "attempt-02-structured.json", "final.md"):
            with self.subTest(artifact=artifact), tempfile.TemporaryDirectory() as directory:
                day, original, _headline = prepared_day(Path(directory))
                target = original / artifact
                target.write_bytes(target.read_bytes() + b" ")
                self.assertEqual(production_run_topics(day, "tampered"), [])

    def test_equal_counts_do_not_prove_reference_position_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            day, original, _headline = prepared_day(Path(directory))
            manifest = json.loads((original / "manifest.json").read_text())
            attempt = next(row for row in manifest["attempts"] if row["index"] == manifest["final"]["attempt"])
            target = original / attempt["structured_artifact"]
            candidate = json.loads(target.read_text())
            section = next(value for value in candidate["sections"].values() if len(value["topics"]) >= 2)
            section["topics"][0]["citation_refs"], section["topics"][1]["citation_refs"] = (
                section["topics"][1]["citation_refs"], section["topics"][0]["citation_refs"])
            write_json_atomic(target, candidate)
            manifest["artifacts"][target.name] = sha256_file(target)
            write_json_atomic(original / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "aligns with frozen evidence"):
                verified_publication(day)

    def test_rehashed_evidence_must_equal_frozen_corpus_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            day, original, _headline = prepared_day(Path(directory))
            target = original / "selected-evidence.json"
            evidence = json.loads(target.read_text())
            section = next(value for value in evidence["sections"].values() if value["topics"])
            section["topics"][0]["evidence"][0]["summary"] = "Planted unrelated support."
            write_json_atomic(target, evidence)
            manifest = json.loads((original / "manifest.json").read_text())
            manifest["artifacts"][target.name] = sha256_file(target)
            write_json_atomic(original / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "frozen corpus projection"):
                verified_publication(day)

    def test_rehashed_candidate_prose_must_equal_published_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            day, original, _headline = prepared_day(Path(directory))
            manifest = json.loads((original / "manifest.json").read_text())
            attempt = next(row for row in manifest["attempts"] if row["index"] == manifest["final"]["attempt"])
            target = original / attempt["structured_artifact"]
            candidate = json.loads(target.read_text())
            section = next(value for value in candidate["sections"].values() if value["topics"])
            section["topics"][0]["headline"] = "Different candidate headline"
            write_json_atomic(target, candidate)
            manifest["artifacts"][target.name] = sha256_file(target)
            write_json_atomic(original / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "Markdown differs"):
                verified_publication(day)

    def test_receipt_rejects_stale_build_even_if_generation_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day, _original, _headline = prepared_day(root)
            history = json.loads((root / "history.json").read_text())
            history["entries"][0]["integrity"]["workflow_run_id"] = 122
            write_json_atomic(root / "history.json", history)
            receipt = publication_receipt(day.parent, root / "history.json", 123, 1)
            self.assertEqual(receipt["reports"], [])
            self.assertIn("stale", receipt["skipped"][0]["reason"])

    def test_missing_or_duplicate_attempt_skips_only_the_invalid_report(self) -> None:
        for invalid in ("missing", "duplicate"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bad_day, bad_run, _headline = prepared_day(root)
                good_day, good_run, _headline = prepared_day(root, "2026-09-02")
                write_json_atomic(root / "history.json", {"entries": [
                    {"date": day.name, "disposition": "ready", "integrity": {"workflow_run_id": 123},
                     "markdown": (run / "final.md").read_text()}
                    for day, run in ((bad_day, bad_run), (good_day, good_run))
                ]})
                manifest = json.loads((bad_run / "manifest.json").read_text())
                if invalid == "missing":
                    manifest["attempts"] = [row for row in manifest["attempts"]
                                            if row["index"] != manifest["final"]["attempt"]]
                else:
                    manifest["attempts"].append(next(row for row in manifest["attempts"]
                                                      if row["index"] == manifest["final"]["attempt"]))
                write_json_atomic(bad_run / "manifest.json", manifest)
                receipt = publication_receipt(bad_day.parent, root / "history.json", 123, 1)
                self.assertEqual([row["date"] for row in receipt["reports"]], [good_day.name])
                self.assertEqual(receipt["skipped"], [
                    {"date": bad_day.name, "reason": "final attempt is not unique"}
                ])

    def test_manifest_change_between_verified_reads_is_a_validation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day, original, _headline = prepared_day(root)

            def replace_manifest_after_verification(run: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
                verified = load_topics(run)
                manifest = json.loads((run / "manifest.json").read_text())
                manifest["attempts"] = []
                write_json_atomic(run / "manifest.json", manifest)
                return verified

            with patch("agent_runner.publication.load_topics", side_effect=replace_manifest_after_verification):
                receipt = publication_receipt(day.parent, root / "history.json", 123, 1)
            self.assertEqual(receipt["reports"], [])
            self.assertEqual(receipt["skipped"], [
                {"date": day.name, "reason": "publication manifest changed during verification"}
            ])
            self.assertEqual(json.loads((original / "manifest.json").read_text())["attempts"], [])

    def test_monitor_accepts_receipt_bound_artifacts_after_renderer_and_checker_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day, _original, _headline = prepared_day(root)
            primary, audit = FakeJudge("primary"), FakeJudge("audit")
            with (patch("agent_runner.output.render_briefing", side_effect=AssertionError("renderer drift")),
                  patch("eval_briefing.evaluate", side_effect=AssertionError("checker drift"))):
                result = run_weekly_monitor(
                    [ProductionRun("historic", day)], week_label="2026-W36",
                    packet_dir=root / "packets", review_output_dir=root / "review",
                    log_path=root / "log.md", primary_judge=primary, audit_judge=audit,
                )
            self.assertEqual(result["runs_reviewed"], 1)
            self.assertGreater(result["topic_count"], 0)
            self.assertGreater(primary.calls, 0)

    def test_monitor_rejects_rehashed_candidate_or_markdown_despite_renderer_drift(self) -> None:
        for artifact in ("attempt-02-structured.json", "final.md"):
            with self.subTest(artifact=artifact), tempfile.TemporaryDirectory() as directory:
                day, original, _headline = prepared_day(Path(directory))
                target = original / artifact
                if artifact.endswith(".json"):
                    candidate = json.loads(target.read_text())
                    section = next(value for value in candidate["sections"].values() if value["topics"])
                    section["topics"][0]["headline"] = "A different authored headline"
                    write_json_atomic(target, candidate)
                else:
                    target.write_bytes(target.read_bytes() + b"\nUnreconciled extra prose.\n")
                manifest = json.loads((original / "manifest.json").read_text())
                manifest["artifacts"][artifact] = sha256_file(target)
                if artifact == "final.md":
                    manifest["final"]["output_sha256"] = sha256_file(target)
                write_json_atomic(original / "manifest.json", manifest)
                with patch("agent_runner.output.render_briefing", side_effect=AssertionError("renderer drift")):
                    with self.assertRaisesRegex(ValueError, "artifact identity differs"):
                        verified_run_topics(ProductionRun("tampered", day))

    def test_new_receipts_still_require_the_current_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day, _original, _headline = prepared_day(root)
            render = importlib.import_module("agent_runner.output").render_briefing

            def drifting_renderer(*args: Any, **kwargs: Any) -> str:
                return str(render(*args, **kwargs)) + "\nDifferent renderer revision.\n"

            with patch("agent_runner.output.render_briefing", side_effect=drifting_renderer):
                receipt = publication_receipt(day.parent, root / "history.json", 123, 1)
            self.assertEqual(receipt["reports"], [])
            self.assertIn("Markdown differs", receipt["skipped"][0]["reason"])

    def test_retries_deduplicate_by_published_date_and_week(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, _original, _headline = prepared_day(root)
            other_root = root / "retry"
            other_root.mkdir()
            newer, _original, _headline = prepared_day(other_root, workflow_id=123, run_attempt=2)
            outside_root = root / "outside"
            outside_root.mkdir()
            outside, _original, _headline = prepared_day(outside_root, "2026-09-08", workflow_id=125)
            included, excluded = sample_published_runs([
                ProductionRun("old", first), ProductionRun("retry", newer), ProductionRun("outside", outside)
            ], "2026-W36")
            self.assertEqual([run.run_id for run in included], ["retry"])
            self.assertIn({"run_id": "old", "reason": "duplicate_or_superseded_report_date"}, excluded)
            self.assertIn({"run_id": "outside", "reason": "outside_requested_report_week"}, excluded)
            self.assertEqual(sum(row["reason"] == "no_verified_published_report_for_date" for row in excluded), 6)
            self.assertEqual(str(week_window("2026-W36")[0]), "2026-08-31")

    def test_older_workflow_rerun_deployed_later_supersedes_newer_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older, _original, _headline = prepared_day(root, workflow_id=120)
            second = root / "newer"
            second.mkdir()
            newer, _original, _headline = prepared_day(second, workflow_id=125)
            receipt = json.loads((older / "publication-receipt.json").read_text())
            receipt["deployment_completed_at"] = "2026-09-03T13:45:00+00:00"
            write_json_atomic(older / "publication-receipt.json", receipt)
            included, _excluded = sample_published_runs([
                ProductionRun("newer", newer), ProductionRun("older-rerun", older)
            ], "2026-W36")
            self.assertEqual([run.run_id for run in included], ["older-rerun"])

    def test_receipt_without_independent_actions_readback_is_not_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            day, _original, _headline = prepared_day(Path(directory))
            receipt = json.loads((day / "publication-receipt.json").read_text())
            receipt.pop("actions_deployment_verified")
            write_json_atomic(day / "publication-receipt.json", receipt)
            self.assertEqual(production_run_topics(day, "no-actions-proof"), [])

    def test_packet_export_records_hashes_and_explicit_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day, _original, _headline = prepared_day(root)
            result = export_production_grounding_packets([
                ProductionRun("verified", day), ProductionRun("legacy", LEGACY_FIXTURE)
            ], root / "packets")
            self.assertGreater(result["topic_count"], 0)
            self.assertEqual(result["skipped_runs"], ["legacy"])
            self.assertTrue(result["exclusions"][0]["reason"])
            manifest = json.loads(result["manifest_path"].read_text())
            self.assertIn("final.md", manifest["results"][0]["publication_identity"]["artifact_hashes"])
            primary = json.loads((root / "packets/reviewer-primary.json").read_text())
            self.assertIsNone(re.search(r'https?://|\b(?:citation|item)_\d+\b', json.dumps(primary)))

    def test_monitor_reviews_verified_run_and_discloses_missing_dates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day, _original, _headline = prepared_day(root)
            primary, audit = FakeJudge("primary"), FakeJudge("audit")
            result = run_weekly_monitor([ProductionRun("verified", day)], week_label="2026-W36",
                                       packet_dir=root / "packets", review_output_dir=root / "review",
                                       log_path=root / "log.md", primary_judge=primary, audit_judge=audit)
            self.assertEqual(result["runs_reviewed"], 1)
            self.assertGreater(primary.calls, 0)
            self.assertEqual(len(result["runs_skipped"]), 6)
            sampling = json.loads((root / "packets/sampling.json").read_text())
            self.assertEqual(sampling["report_date_end_exclusive"], "2026-09-07")

    def test_no_proof_means_no_judge_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary, audit = FakeJudge("primary"), FakeJudge("audit")
            result = run_weekly_monitor([ProductionRun("legacy", LEGACY_FIXTURE)], week_label="2026-W36",
                                       packet_dir=root / "packets", review_output_dir=root / "review",
                                       log_path=root / "log.md", primary_judge=primary, audit_judge=audit)
            self.assertEqual(result["topic_count"], 0)
            self.assertEqual(primary.calls, 0)
            self.assertEqual(audit.calls, 0)
            self.assertTrue(result["exclusions"])

    def test_repeated_log_rows_preserve_historical_measurements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log.md"
            upsert_weekly_log(path, "2026-W36", "| 2026-W36 | original |\n")
            text = upsert_weekly_log(path, "2026-W36", "| 2026-W36 | new |\n")
            self.assertIn("| 2026-W36 | original |", text)
            self.assertIn("| 2026-W36 (assessment 2) | new |", text)


if __name__ == "__main__":
    unittest.main()
