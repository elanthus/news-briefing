"""Evaluator semantic judge regression coverage."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluator.adapters import (
    Adapter,
    Generation,
)
from evaluator.runner import (
    DEFAULT_CORPUS,
    _semantic_adjudication_template,
    run_evaluation,
)
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
            self.assertEqual(identity["schema_version"], 2)
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

