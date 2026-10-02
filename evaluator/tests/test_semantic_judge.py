"""Evaluator semantic judge regression coverage."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluator.adapters import (
    Adapter,
    Generation,
)
from evaluator.runner import DEFAULT_CORPUS, run_evaluation
from evaluator.scoring import _semantic_adjudication_template
from evaluator.semantic_review import _judgment_prompt as _semantic_judgment_prompt
from evaluator.semantic_review import _parse_judgment as _parse_semantic_judgment
from evaluator.semantic_review import run_semantic_judging
from evaluator.tests.support import (
    FakeAdapter,
)


class FakeSemanticJudgeAdapter(Adapter):
    provider = "offline-semantic-judge"

    def __init__(self, model: str):
        super().__init__(model)
        self.calls = 0
        self.last_prompt = ""

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        self.last_prompt = prompt
        return Generation(text=json.dumps({
            "judgment": "conveyed",
            "rationale": "The generated topic expresses the proposition as a paraphrase.",
        }), latency_ms=1.0)



class SemanticJudgeTest(unittest.TestCase):
    def _minimal_run(self, directory: Path) -> Path:
        (directory / "config.json").write_bytes(
            (Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_bytes()
        )
        suite = directory / "suite.json"
        suite.write_text(json.dumps({
            "schema_version": 4,
            "case_count": 1,
            "cases": [{
                "id": "semantic", "kind": "utility", "family": "valid_edge",
                "config": "config.json", "mutations": [],
                "must_convey": [{
                    "url": "https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/",
                    "propositions": ["Subagents can call third-party providers."],
                }],
            }],
        }))
        prompt = directory / "prompt.md"
        prompt.write_text("Produce the briefing.")
        run_evaluation([FakeAdapter("fixture")], {"v1": prompt}, directory / "results",
                       suite_path=suite, corpus_path=DEFAULT_CORPUS)
        return directory / "results" / "manifest.json"

    def test_changed_suite_or_config_cannot_relabel_frozen_assessment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._minimal_run(root)
            judge = FakeSemanticJudgeAdapter("judge")
            run_semantic_judging(manifest, judge, root / "assessment")
            for name in ("suite.json", "config.json"):
                path = root / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "differs from the frozen"):
                    run_semantic_judging(manifest, judge, root / "independent")
                path.write_bytes(original)
            self.assertEqual(judge.calls, 1)

    def test_matched_clean_twin_uses_its_declared_case_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._minimal_run(root)
            suite_path = root / "suite.json"
            suite = json.loads(suite_path.read_text())
            suite["cases"][0].update(id="attack-prose", kind="attack", family="prose", matched_pair=True)
            suite_path.write_text(json.dumps(suite))
            run_evaluation([FakeAdapter("fixture")], {"v1": root / "prompt.md"}, root / "paired",
                           suite_path=suite_path, corpus_path=DEFAULT_CORPUS)
            judge = FakeSemanticJudgeAdapter("judge")
            result = run_semantic_judging(root / "paired/manifest.json", judge, root / "assessment")
            self.assertEqual(judge.calls, 2)
            self.assertEqual(len(result["records"]), 2)

    def test_fresh_directory_is_independent_and_preserves_prior_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            first = FakeSemanticJudgeAdapter("first")
            run_semantic_judging(manifest_path, first, manifest_path.parent / "semantic-judgments")
            manifest = json.loads(manifest_path.read_bytes())
            source = manifest_path.parent / manifest["results"][0]["semantic_adjudication"]
            frozen = source.read_bytes()
            second = FakeSemanticJudgeAdapter("second")
            result = run_semantic_judging(manifest_path, second, temporary / "independent")
            self.assertEqual(second.calls, 1)
            self.assertEqual(result["records"][0]["reviewer"]["model"], "second")
            self.assertFalse(result["updates_generation_report"])
            self.assertEqual(source.read_bytes(), frozen)
            resumed = run_semantic_judging(manifest_path, second, temporary / "independent")
            self.assertEqual(resumed["model_calls"], 0)
            self.assertEqual(resumed["records"][0]["reviewer"], result["records"][0]["reviewer"])

    def test_changed_controls_prompt_evidence_and_proposition_cannot_reuse_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            judge = FakeSemanticJudgeAdapter("judge")
            output = temporary / "assessment"
            run_semantic_judging(manifest_path, judge, output)
            with patch.object(judge, "generation_controls", return_value={"temperature": 0.5}):
                with self.assertRaisesRegex(ValueError, "different semantic-judge"):
                    run_semantic_judging(manifest_path, judge, output)
            with patch("evaluator.semantic_review._judgment_prompt", return_value="New rubric"):
                with self.assertRaisesRegex(ValueError, "different semantic-judge"):
                    run_semantic_judging(manifest_path, judge, output)
            manifest = json.loads(manifest_path.read_bytes())
            row = manifest["results"][0]
            corpus_path = manifest_path.parent / row["artifact_dir"] / "corpus.json"
            frozen = corpus_path.read_bytes()
            corpus = json.loads(frozen)
            corpus["categories"]["dev_community"][1]["summary"] = "Changed material evidence."
            corpus_path.write_text(json.dumps(corpus))
            with self.assertRaisesRegex(ValueError, "different semantic-judge"):
                run_semantic_judging(manifest_path, judge, output)
            corpus_path.write_bytes(frozen)
            semantic_path = manifest_path.parent / row["semantic_adjudication"]
            payload = json.loads(semantic_path.read_bytes())
            payload["judgments"][0]["proposition"] = "Different proposition."
            semantic_path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "requirements differ from the frozen suite"):
                run_semantic_judging(manifest_path, judge, output)
            self.assertEqual(judge.calls, 1)

    def test_changed_final_output_or_adjudication_topics_fail_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            manifest = json.loads(manifest_path.read_bytes())
            row = manifest["results"][0]
            final_path = manifest_path.parent / row["artifact_dir"] / "final.md"
            final_path.write_text(final_path.read_text().replace("The author built a patch", "An unsupported addition"))
            judge = FakeSemanticJudgeAdapter("judge")
            with self.assertRaisesRegex(ValueError, "do not match frozen final output"):
                run_semantic_judging(manifest_path, judge, temporary / "assessment")
            self.assertEqual(judge.calls, 0)

    def test_legacy_labels_are_not_invented_as_requested_judge_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            manifest = json.loads(manifest_path.read_bytes())
            source = manifest_path.parent / manifest["results"][0]["semantic_adjudication"]
            payload = json.loads(source.read_bytes())
            payload["judgments"][0].update({"judgment": "conveyed", "reviewer": None})
            source.write_text(json.dumps(payload))
            frozen = source.read_bytes()
            judge = FakeSemanticJudgeAdapter("new")
            result = run_semantic_judging(manifest_path, judge, temporary / "assessment")
            self.assertEqual(judge.calls, 1)
            self.assertEqual(result["legacy_labels_ignored"], 1)
            self.assertEqual(source.read_bytes(), frozen)

    def test_parser_accepts_fenced_json_and_rejects_bad_labels(self) -> None:
        parsed = _parse_semantic_judgment(
            '```json\n{"judgment":"conveyed","rationale":"faithful paraphrase"}\n```'
        )
        self.assertEqual(parsed["judgment"], "conveyed")
        with self.assertRaisesRegex(ValueError, "must be 'conveyed'"):
            _parse_semantic_judgment(
                '{"judgment":"probably","rationale":"not a supported label"}'
            )

    def test_paraphrase_is_judged_separately_from_deterministic_routing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            config = temporary / "config.json"
            config.write_text(
                (Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_text(),
                encoding="utf-8",
            )
            suite = temporary / "suite.json"
            suite.write_text(
                json.dumps(
                    {
                        "schema_version": 4,
                        "case_count": 1,
                        "cases": [
                            {
                                "id": "semantic-paraphrase",
                                "kind": "utility",
                                "family": "valid_edge",
                                "config": "config.json",
                                "mutations": [],
                                "must_include_urls": ["https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/"],
                                "must_convey": [
                                    {
                                        "url": "https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/",
                                        "propositions": [
                                            "The patch allows subagents to run using other model providers."
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            prompt = temporary / "prompt.md"
            prompt.write_text("Produce the briefing.", encoding="utf-8")
            output = temporary / "results"

            first_report = run_evaluation(
                [FakeAdapter("fixture-1")],
                {"v1": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )
            utility = first_report["score_families"]["application_utility"]["groups"][0]
            editorial = first_report["score_families"]["editorial_quality"]["groups"][0]
            self.assertEqual(utility["routing_success_final"]["rate"], 1.0)
            self.assertIsNone(editorial["semantic_meaning_preservation"]["rate"])
            self.assertEqual(editorial["semantic_unreviewed_propositions"], 1)

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            semantic_path = output / manifest["results"][0]["semantic_adjudication"]
            semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
            generated_prose = semantic["judgments"][0]["topics"][0]["prose"]
            self.assertNotIn("run using other model providers", generated_prose)

            judge = FakeSemanticJudgeAdapter("fixture-judge")
            result = run_semantic_judging(output / "manifest.json", judge, output / "semantic-judgments")
            self.assertEqual(result["counts"]["conveyed"], 1)
            self.assertEqual(judge.calls, 1)
            self.assertNotIn("semantic-paraphrase", judge.last_prompt)
            self.assertNotIn("offline-fixture", judge.last_prompt)

            updated = json.loads((output / "report.json").read_text(encoding="utf-8"))
            metric = updated["score_families"]["editorial_quality"]["groups"][0]["semantic_meaning_preservation"]
            self.assertEqual(metric["successes"], 1)
            self.assertEqual(metric["trials"], 1)

            resumed = run_semantic_judging(output / "manifest.json", judge, output / "semantic-judgments")
            self.assertEqual(resumed["model_calls"], 0)
            self.assertEqual(judge.calls, 1)

            identity_path = output / "semantic-judgments" / "semantic-judging-run.json"
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            self.assertEqual(identity["schema_version"], 3)
            identity["schema_version"] = 1
            identity_path.write_text(json.dumps(identity), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "different semantic-judge run"):
                run_semantic_judging(output / "manifest.json", judge, output / "semantic-judgments")

    def test_repeated_url_exposes_every_citing_topic_to_the_semantic_judge(self) -> None:
        url = "https://example.test/repeated"
        proposition = "The later topic contains the required meaning."
        payload = _semantic_adjudication_template(
            {
                "must_convey": [{"url": url, "propositions": [proposition]}],
            },
            {
                "AI Dev Tools": {
                    "topics": ["First mention", "Later mention"],
                    "topic_texts": [
                        "This topic omits the required meaning.",
                        "The later topic contains the required meaning.",
                    ],
                    "topic_links": [[url], [url]],
                },
            },
        )

        judgment = payload["judgments"][0]
        self.assertEqual(
            [topic["title"] for topic in judgment["topics"]],
            ["First mention", "Later mention"],
        )
        prompt = _semantic_judgment_prompt(
            "Supporting evidence.", judgment["topics"], proposition
        )
        self.assertIn("TOPIC 1:\nFirst mention", prompt)
        self.assertIn("TOPIC 2:\nLater mention", prompt)
        self.assertIn("whether at least one GENERATED TOPIC conveys", prompt)

