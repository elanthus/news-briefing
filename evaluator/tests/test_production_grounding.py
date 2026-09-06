import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from evaluator.adapters import Adapter, Generation
from evaluator.production_grounding import (
    ProductionRun,
    export_production_grounding_packets,
    production_run_topics,
    render_weekly_log_row,
    resolve_ready_run_dir,
    run_weekly_monitor,
    unverified_grounding_rate,
    upsert_weekly_log,
)

FIXTURE_RUN = Path(__file__).resolve().parents[1] / "fixtures" / "production-run"

# A destination-shaped string is enough to trip eval_briefing.url_spellings();
# a bare "citation_"/"item_" plus digits is the opaque-handle spelling banned
# from every model-visible packet.
_URL_LIKE = re.compile(r"https?://|www\.[a-z0-9-]+\.[a-z]{2,}", re.IGNORECASE)
_OPAQUE_HANDLE = re.compile(r"\b(?:citation|item)_\d+\b", re.IGNORECASE)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


class FakeGroundingJudgeAdapter(Adapter):
    provider = "offline-grounding-judge"

    def __init__(self, model: str, error_ids: set[str], cost_usd: float | None = 0.01):
        super().__init__(model)
        self.error_ids = error_ids
        self.cost_usd = cost_usd
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        raw_topics = prompt.split("TOPICS:\n", 1)[1].split("\n\nReturn JSON only", 1)[0]
        topics = json.loads(raw_topics)
        return Generation(
            text=json.dumps({
                "reviews": [
                    {
                        "review_id": topic["review_id"],
                        "grounding_error": topic["review_id"] in self.error_ids,
                        "rationale": "Fixture evidence supports this decision.",
                    }
                    for topic in topics
                ],
            }),
            latency_ms=1.0,
            input_tokens=10,
            output_tokens=5,
            cost_usd=self.cost_usd,
        )


class ResolveReadyRunDirTest(unittest.TestCase):
    def test_direct_manifest_resolves_to_its_own_directory(self) -> None:
        self.assertEqual(resolve_ready_run_dir(FIXTURE_RUN), FIXTURE_RUN)

    def test_missing_manifest_and_chain_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(resolve_ready_run_dir(Path(directory)))

    def test_fallback_chain_resolves_selected_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            day_dir = Path(directory)
            candidate = day_dir / "02-some-model"
            shutil.copytree(FIXTURE_RUN, candidate)
            (day_dir / "fallback-log.json").write_text(json.dumps({
                "status": "ready",
                "selected_model": "some-model",
                "selected_run_dir": "02-some-model",
            }), encoding="utf-8")
            self.assertEqual(resolve_ready_run_dir(day_dir), candidate)

    def test_failed_chain_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            day_dir = Path(directory)
            (day_dir / "fallback-log.json").write_text(json.dumps({
                "status": "failed",
                "selected_run_dir": None,
            }), encoding="utf-8")
            self.assertIsNone(resolve_ready_run_dir(day_dir))

    def test_path_traversal_in_selected_run_dir_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            day_dir = Path(directory)
            (day_dir / "fallback-log.json").write_text(json.dumps({
                "status": "ready",
                "selected_run_dir": "../escaped",
            }), encoding="utf-8")
            self.assertIsNone(resolve_ready_run_dir(day_dir))


class ProductionRunTopicsTest(unittest.TestCase):
    def test_fixture_run_yields_position_matched_records(self) -> None:
        records = production_run_topics(FIXTURE_RUN, "2026-09-01")
        self.assertEqual(len(records), 2)
        self.assertEqual([row["topic_index"] for row in records], [1, 2])
        self.assertTrue(all(row["artifact_dir"] == "2026-09-01" for row in records))
        self.assertTrue(all(row["stratum"] == "Top Stories" for row in records))
        first = records[0]["public"]
        self.assertEqual(first["title"], "Regulator opens review of grid reliability standards")
        self.assertEqual(len(first["evidence"]), 1)
        second = records[1]["public"]
        self.assertEqual(len(second["evidence"]), 2)

    def test_packet_content_carries_no_url_or_opaque_handle(self) -> None:
        records = production_run_topics(FIXTURE_RUN, "2026-09-01")
        serialized = _dump([row["public"] for row in records])
        self.assertIsNone(_URL_LIKE.search(serialized))
        self.assertIsNone(_OPAQUE_HANDLE.search(serialized))

    def test_non_ready_run_yields_no_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            shutil.copytree(FIXTURE_RUN, run_dir)
            manifest_path = run_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["final"]["status"] = "preview"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(production_run_topics(run_dir, "unready"), [])

    def test_mismatched_evidence_count_skips_the_whole_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            shutil.copytree(FIXTURE_RUN, run_dir)
            evidence_path = run_dir / "selected-evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            # Drop one evidence entry, as a structural repair dropping a
            # candidate topic after evidence was frozen would.
            del evidence["sections"]["Top Stories"]["topics"][0]
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            self.assertEqual(production_run_topics(run_dir, "misaligned"), [])

    def test_missing_structured_artifact_yields_no_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            shutil.copytree(FIXTURE_RUN, run_dir)
            (run_dir / "attempt-02-structured.json").unlink()
            self.assertEqual(production_run_topics(run_dir, "broken"), [])


class ExportProductionGroundingPacketsTest(unittest.TestCase):
    def test_packets_combine_runs_and_record_skips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            unready_dir = base / "unready-run"
            shutil.copytree(FIXTURE_RUN, unready_dir)
            manifest_path = unready_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["final"]["status"] = "preview"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            output_dir = base / "packets"
            result = export_production_grounding_packets(
                [
                    ProductionRun("2026-09-01", FIXTURE_RUN),
                    ProductionRun("2026-09-02", unready_dir),
                ],
                output_dir,
                double_fraction=1.0,
            )
            self.assertEqual(result["topic_count"], 2)
            self.assertEqual(result["skipped_runs"], ["2026-09-02"])

            primary = json.loads((output_dir / "reviewer-primary.json").read_text(encoding="utf-8"))
            self.assertEqual(len(primary["reviews"]), 2)
            serialized = _dump(primary) + _dump(
                json.loads((output_dir / "reviewer-double.json").read_text(encoding="utf-8"))
            )
            self.assertIsNone(_URL_LIKE.search(serialized))
            self.assertIsNone(_OPAQUE_HANDLE.search(serialized))

            review_map = json.loads((output_dir / "review-map.json").read_text(encoding="utf-8"))
            self.assertEqual(
                {row["review_id"] for row in review_map["primary"]},
                {row["review_id"] for row in primary["reviews"]},
            )
            for row in review_map["primary"]:
                self.assertEqual(row["artifact_dir"], "2026-09-01")

            manifest = json.loads(result["manifest_path"].read_text(encoding="utf-8"))
            self.assertEqual(manifest["results"], [{
                "artifact_dir": "2026-09-01",
                "provider": "openrouter",
                "model": "deepseek/deepseek-v4-flash-0731",
                "prompt_version": "production",
            }])


class WeeklyLogTest(unittest.TestCase):
    def test_grounding_rate_inverts_error_rate(self) -> None:
        result = unverified_grounding_rate({
            "primary": {"grounding_errors": {"successes": 1, "trials": 4, "rate": 0.25,
                                              "ci95_wilson": [0.01, 0.75]}}
        })
        self.assertEqual(result["successes"], 3)
        self.assertEqual(result["trials"], 4)
        self.assertAlmostEqual(result["rate"], 0.75)

    def test_row_renders_no_topics_reviewed_when_empty(self) -> None:
        row = render_weekly_log_row(
            week_label="2026-W36",
            runs_reviewed=0,
            runs_skipped=7,
            grounding_rate={"successes": 0, "trials": 0, "rate": None, "ci95_wilson": None},
            audit_agreement=None,
            primary_judge="openrouter/some-model",
            cost_usd=0.0,
        )
        self.assertIn("no topics reviewed", row)
        self.assertIn("n/a", row)

    def test_upsert_replaces_existing_week_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log.md"
            first = render_weekly_log_row(
                week_label="2026-W36", runs_reviewed=7, runs_skipped=0,
                grounding_rate={"successes": 6, "trials": 7, "rate": 6 / 7, "ci95_wilson": [0.4, 0.95]},
                audit_agreement={"rate": 1.0}, primary_judge="openrouter/m", cost_usd=0.5,
            )
            upsert_weekly_log(path, "2026-W36", first)
            second = render_weekly_log_row(
                week_label="2026-W36", runs_reviewed=7, runs_skipped=0,
                grounding_rate={"successes": 7, "trials": 7, "rate": 1.0, "ci95_wilson": [0.6, 1.0]},
                audit_agreement={"rate": 1.0}, primary_judge="openrouter/m", cost_usd=0.6,
            )
            text = upsert_weekly_log(path, "2026-W36", second)
            self.assertEqual(text.count("2026-W36"), 1)
            self.assertIn("7/7", text)


class RunWeeklyMonitorTest(unittest.TestCase):
    def test_end_to_end_publishes_one_row_and_no_urls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            log_path = base / "docs" / "grounding-monitor.md"
            primary = FakeGroundingJudgeAdapter("primary-model", error_ids=set())
            audit = FakeGroundingJudgeAdapter("audit-model", error_ids=set())
            result = run_weekly_monitor(
                [ProductionRun("2026-09-01", FIXTURE_RUN)],
                week_label="2026-W36",
                packet_dir=base / "packets",
                review_output_dir=base / "review",
                log_path=log_path,
                primary_judge=primary,
                audit_judge=audit,
                double_fraction=1.0,
            )
            self.assertEqual(result["topic_count"], 2)
            self.assertEqual(result["runs_reviewed"], 1)
            self.assertEqual(result["runs_skipped"], [])
            self.assertEqual(result["grounding_rate"]["successes"], 2)
            self.assertGreater(primary.calls, 0)
            self.assertGreater(audit.calls, 0)
            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn("2026-W36", log_text)
            self.assertIn("2/2", log_text)
            self.assertIsNone(_URL_LIKE.search(log_text))
            self.assertIsNone(_OPAQUE_HANDLE.search(log_text))
            # The private per-topic review stays out of the committed log.
            review_result = json.loads(
                (base / "review" / "machine-grounding-review.json").read_text(encoding="utf-8")
            )
            self.assertEqual(review_result["primary"]["reviewed_topics"], 2)

    def test_no_ready_runs_publishes_zero_row_without_calling_the_judge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            unready_dir = base / "unready"
            shutil.copytree(FIXTURE_RUN, unready_dir)
            manifest_path = unready_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["final"]["status"] = "preview"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            primary = FakeGroundingJudgeAdapter("primary-model", error_ids=set())
            audit = FakeGroundingJudgeAdapter("audit-model", error_ids=set())
            result = run_weekly_monitor(
                [ProductionRun("2026-09-03", unready_dir)],
                week_label="2026-W37",
                packet_dir=base / "packets",
                review_output_dir=base / "review",
                log_path=base / "log.md",
                primary_judge=primary,
                audit_judge=audit,
            )
            self.assertEqual(result["topic_count"], 0)
            self.assertEqual(result["runs_skipped"], ["2026-09-03"])
            self.assertEqual(primary.calls, 0)
            self.assertEqual(audit.calls, 0)


if __name__ == "__main__":
    unittest.main()
