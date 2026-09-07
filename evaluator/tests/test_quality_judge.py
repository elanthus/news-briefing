"""Evaluator quality judge regression coverage."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import corpus_schema
import eval_briefing
from briefing_config import load_config
from evaluator.adapters import (
    Adapter,
    Generation,
)
from evaluator.quality import (
    QUALITY_AXES,
    _parse_judgment,
    _topics,
    matched_pairs,
    run_quality_judging,
)
from evaluator.runner import (
    DEFAULT_CORPUS,
    run_evaluation,
)
from evaluator.tests.support import (
    FakeAdapter,
)


class FakeContentAwareJudgeAdapter(Adapter):
    """A judge that tracks a content marker regardless of which slot it appears in."""

    provider = "offline-judge-content-aware"

    def __init__(self, model: str, prefers_marker: str):
        super().__init__(model)
        self.prefers_marker = prefers_marker
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        option_a = prompt[prompt.index("OPTION A:"):prompt.index("OPTION B:")]
        winner = "a" if self.prefers_marker in option_a else "b"
        payload = dict.fromkeys((*QUALITY_AXES, "overall"), winner)
        payload["rationale"] = "fixture rationale: tracks a content marker"
        return Generation(text=json.dumps(payload), latency_ms=1.0)



class FakeJudgeAdapter(Adapter):
    """A judge with pure position bias: it always prefers whichever text is labeled Option A."""

    provider = "offline-judge"

    def __init__(self, model: str):
        super().__init__(model)
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        payload = dict.fromkeys((*QUALITY_AXES, "overall"), "a")
        payload["rationale"] = "fixture rationale: always prefers Option A"
        return Generation(text=json.dumps(payload), latency_ms=1.0)



class FakeAdapterVariant(FakeAdapter):
    """A second, differently-worded model producing a topic on the same corpus URL as FakeAdapter."""

    provider = "offline-fixture-b"

    def generate(self, prompt: str) -> Generation:
        self.last_prompt = prompt
        return Generation(
            text=(
                "# Daily Briefing — August 11, 2026\n\n"
                "## AI Dev Tools\n\n"
                "**Subagents gain third-party model support** — A community patch lets subagents call "
                "other providers while billing stays on the subscription plan.\n"
                "🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/\n"
            ),
            latency_ms=10.0,
            input_tokens=90,
            output_tokens=25,
            cost_usd=0.001,
        )



class QualityJudgeTest(unittest.TestCase):
    def _minimal_run(self, directory: Path) -> Path:
        """Run two fake models against one case and return the manifest path."""
        config = directory / "config.json"
        config.write_text(
            (Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_text(),
            encoding="utf-8",
        )
        suite = directory / "suite.json"
        suite.write_text(json.dumps({
            "schema_version": 2,
            "case_count": 1,
            "cases": [{
                "id": "offline",
                "kind": "utility",
                "family": "valid_edge",
                "config": "config.json",
                "mutations": [],
            }],
        }), encoding="utf-8")
        prompt = directory / "prompt.md"
        prompt.write_text("Produce the briefing.", encoding="utf-8")
        output = directory / "results"
        run_evaluation(
            [FakeAdapter("fixture-1"), FakeAdapterVariant("fixture-1")],
            {"v1": prompt},
            output,
            suite_path=suite,
            corpus_path=DEFAULT_CORPUS,
        )
        return output / "manifest.json"

    def test_parse_judgment_accepts_fenced_json_and_rejects_bad_shapes(self) -> None:
        fenced = "```\n" + json.dumps({
            "faithfulness": "a", "salience": "b", "concision": "tie", "coherence": "a",
            "overall": "a", "rationale": "clear reason",
        }) + "\n```"
        parsed = _parse_judgment(fenced)
        self.assertEqual(parsed["salience"], "b")
        with self.assertRaisesRegex(ValueError, "must contain exactly"):
            _parse_judgment(json.dumps({"faithfulness": "a"}))
        with self.assertRaisesRegex(ValueError, "must be 'a', 'b', or 'tie'"):
            _parse_judgment(json.dumps({
                "faithfulness": "a", "salience": "a", "concision": "a", "coherence": "a",
                "overall": "definitely-a", "rationale": "reason",
            }))

    def test_matched_pairs_link_two_models_writing_about_the_same_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            configs = {"offline": load_config(temporary / "config.json")}
            pairs = matched_pairs(manifest, manifest_path.parent, configs)
            self.assertEqual(len(pairs), 1)
            self.assertEqual(pairs[0]["group_a"][0], "offline-fixture")
            self.assertEqual(pairs[0]["group_b"][0], "offline-fixture-b")
            self.assertIn("billing stays on the subscription plan", pairs[0]["topic_b"]["prose"])

    def test_multi_url_evidence_is_joined_in_canonical_url_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            case_dir = temporary / "case"
            case_dir.mkdir()
            corpus = json.loads(DEFAULT_CORPUS.read_text(encoding="utf-8"))
            urls = [
                "https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/",
                "https://news.ycombinator.com/item?id=90000001",
            ]
            (case_dir / "corpus.json").write_text(json.dumps(corpus), encoding="utf-8")
            (case_dir / "final.md").write_text(
                "# Daily Briefing\n\n## AI Dev Tools\n\n"
                "**Combined topic** — A concise combined summary.\n"
                f"🔗 {urls[0]}\n🔗 {urls[1]}\n",
                encoding="utf-8",
            )
            config = load_config(
                Path(__file__).parents[1] / "fixtures" / "generation-config-1.json"
            )

            topics = _topics(temporary, {"artifact_dir": "case"}, config)

            evidence = eval_briefing.corpus_evidence(corpus)
            expected = " ".join(
                dict.fromkeys(
                    evidence[url]
                    for url in sorted(corpus_schema.canonicalize_url(url) for url in urls)
                )
            )
            self.assertEqual(topics[0]["evidence"], expected)

    def test_position_biased_judge_is_flagged_as_inconsistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            judge = FakeJudgeAdapter("fixture-judge")

            result = run_quality_judging(manifest_path, judge, temporary / "quality")

            self.assertEqual(result["pairs_judged"], 1)
            self.assertEqual(judge.calls, 2)  # one original-order call, one swapped
            for axis in (*QUALITY_AXES, "overall"):
                self.assertEqual(result["position_consistency"][axis]["rate"], 0.0)
            report = (temporary / "quality" / "quality-report.md").read_text(encoding="utf-8")
            self.assertIn("Position-bias consistency", report)

    def test_content_aware_judge_is_consistent_and_wins_are_attributed_correctly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            judge = FakeContentAwareJudgeAdapter(
                "fixture-judge", prefers_marker="billing stays on the subscription plan"
            )

            result = run_quality_judging(manifest_path, judge, manifest_path.parent / "quality-judgments")

            for axis in (*QUALITY_AXES, "overall"):
                self.assertEqual(result["position_consistency"][axis]["rate"], 1.0)
            winners = {row["provider"]: row for row in result["win_rates"] if row["axis"] == "overall"}
            self.assertEqual(winners["offline-fixture-b"]["win_rate_excluding_ties"]["rate"], 1.0)
            self.assertEqual(winners["offline-fixture"]["win_rate_excluding_ties"]["rate"], 0.0)
            main_report = json.loads((manifest_path.parent / "report.json").read_text(encoding="utf-8"))
            editorial = main_report["score_families"]["editorial_quality"]
            self.assertEqual(editorial["pairwise_judging"]["status"], "available")
            self.assertEqual(editorial["pairwise_judging"]["pairs_judged"], 1)
            self.assertEqual(editorial["groups"][1]["pairwise_prose_quality"]["status"], "available")

    def test_rerunning_with_a_different_judge_in_the_same_output_dir_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            run_quality_judging(manifest_path, FakeJudgeAdapter("judge-one"), temporary / "quality")

            with self.assertRaisesRegex(ValueError, "different judge-quality run"):
                run_quality_judging(manifest_path, FakeJudgeAdapter("judge-two"), temporary / "quality")

    def test_rerunning_after_the_suite_changes_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            judge = FakeJudgeAdapter("fixture-judge")
            run_quality_judging(manifest_path, judge, temporary / "quality")

            suite_path = temporary / "suite.json"
            suite = json.loads(suite_path.read_text(encoding="utf-8"))
            suite["description"] = "changed after checkpoints were recorded"
            suite_path.write_text(json.dumps(suite), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "different judge-quality run"):
                run_quality_judging(manifest_path, judge, temporary / "quality")

    def test_suite_override_missing_a_manifest_case_reports_a_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            mismatched_suite = temporary / "mismatched-suite.json"
            mismatched_suite.write_text(json.dumps({
                "schema_version": 2,
                "case_count": 1,
                "cases": [{
                    "id": "different-case",
                    "kind": "utility",
                    "family": "valid_edge",
                    "config": "config.json",
                    "mutations": [],
                }],
            }), encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError, "case 'offline' is in the manifest but not in the suite"
            ):
                run_quality_judging(
                    manifest_path,
                    FakeJudgeAdapter("fixture-judge"),
                    temporary / "quality",
                    suite_path=mismatched_suite,
                )

    def test_resumed_run_reuses_checkpoints_without_a_second_paid_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            judge = FakeJudgeAdapter("fixture-judge")
            run_quality_judging(manifest_path, judge, temporary / "quality")
            self.assertEqual(judge.calls, 2)

            run_quality_judging(manifest_path, judge, temporary / "quality")
            self.assertEqual(judge.calls, 2)

