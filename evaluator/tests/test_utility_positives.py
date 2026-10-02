"""Public authored utility examples must satisfy every active case's contract."""
from __future__ import annotations

import json
import unittest

import eval_briefing
from evaluator.runner import DEFAULT_SUITE
from evaluator.scoring import _oracle, _semantic_adjudication_template
from evaluator.tests.utility_controls import FIXTURES, utility_positive


class UtilityPositiveTests(unittest.TestCase):
    def test_every_utility_has_a_declared_positive_without_unused_case_ids(self) -> None:
        suite = json.loads(DEFAULT_SUITE.read_text())
        controls = json.loads((FIXTURES / "generation-v9-utility-positive.json").read_text())
        declared = controls["small_case_ids"] + controls["production_case_ids"]
        utility_ids = {case["id"] for case in suite["cases"] if case["kind"] == "utility"}
        self.assertEqual(len(declared), len(set(declared)))
        self.assertEqual(set(declared), utility_ids)
        self.assertLessEqual(set(controls["topic_overrides"]), set(controls["small_case_ids"]))

    def test_authored_positives_pass_structural_checker_and_utility_oracle(self) -> None:
        suite = json.loads(DEFAULT_SUITE.read_text())
        for case in suite["cases"]:
            if case["kind"] != "utility":
                continue
            with self.subTest(case=case["id"]):
                corpus, config, _output, markdown = utility_positive(case)
                findings = eval_briefing.evaluate(corpus, markdown, config)
                self.assertEqual(findings, [])
                sections = eval_briefing.parse_briefing(markdown, config)
                oracle = _oracle(case, markdown, findings, sections, corpus=corpus, config=config)
                self.assertFalse(oracle["case_failure"])
                self.assertTrue(oracle["utility_under_attack"])
                # Semantic correctness remains independently reviewable, with
                # every required proposition bound to actual cited topics.
                template = _semantic_adjudication_template(case, sections)
                for judgment in template["judgments"]:
                    self.assertTrue(judgment["topics"])
                    self.assertIsNone(judgment["judgment"])
                    self.assertIsNone(judgment["reviewer"])
