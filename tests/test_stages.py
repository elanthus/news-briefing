"""Direct coverage of shared stage policy ordering and durable-attempt guards."""

import unittest
from unittest.mock import Mock, call, patch

import briefing_config
from agent_runner.stages import CorrectionBudget, DeterministicRepairResult, decide_stage


class StageDecisionTests(unittest.TestCase):
    def test_decision_order_and_attempt_guards(self):
        underfill = [{"level": "ERROR", "check": "slots_underfilled"}]
        invalid = [{"level": "ERROR", "check": "invalid_shape"}]
        warning = [{"level": "WARN", "check": "claim_exceeds_evidence"}]
        # name, stage, findings, previous attempt, offered promotion/repair,
        # corrections used, expected action, expected helper order.
        cases = [
            ("promotion before repair", "selection", underfill, "selection",
             True, True, 1, "selection_promotion", ["promote"]),
            ("promotion before acceptance", "selection", [], "selection",
             True, True, 0, "selection_promotion", ["promote"]),
            ("unavailable promotion falls through", "selection", underfill, "selection",
             False, True, 0, "selection_repair", ["promote", "repair"]),
            ("promotion guard", "selection", underfill, "selection_promotion",
             True, True, 0, "selection_repair", ["repair"]),
            ("other errors block promotion", "selection", invalid, "selection",
             True, True, 0, "selection_repair", ["repair"]),
            ("selection repair guard", "selection", invalid, "selection_repair",
             True, True, 0, "correct", []),
            ("selection correction resets repair", "selection", invalid, "selection_correction",
             False, True, 1, "selection_repair", ["repair"]),
            ("prose skips promotion", "prose", invalid, "prose",
             True, True, 1, "deterministic_repair", ["repair"]),
            ("warning repair before acceptance", "prose", warning, "prose",
             False, True, 0, "deterministic_repair", ["repair"]),
            ("prose repair guard", "prose", invalid, "deterministic_repair",
             False, True, 0, "correct", []),
            ("prose correction resets repair", "prose", invalid, "correction",
             False, True, 1, "deterministic_repair", ["repair"]),
            ("clean accepts with exhausted budget", "selection", [], "selection",
             False, False, 1, "accept", ["promote", "repair"]),
            ("unrepaired error corrects", "prose", invalid, "prose",
             False, False, 0, "correct", ["repair"]),
            ("unrepaired error exhausts", "prose", invalid, "prose",
             False, False, 1, "exhausted", ["repair"]),
        ]
        config = briefing_config.load_config()
        output = {"sections": []}
        citations = {}
        corpus = {"items": []}
        promotion = DeterministicRepairResult({"promoted": True}, [])
        repair = DeterministicRepairResult({"repaired": True}, [])
        for name, stage, findings, last_kind, promote, fix, used, action, order in cases:
            with (
                self.subTest(name=name),
                patch("agent_runner.stages.selection_promotion_candidate",
                      return_value=promotion if promote else None) as promote_mock,
                patch("agent_runner.stages.deterministic_repair_candidate",
                      return_value=repair if fix else None) as repair_mock,
            ):
                helpers = Mock()
                helpers.attach_mock(promote_mock, "promote")
                helpers.attach_mock(repair_mock, "repair")
                decision = decide_stage(
                    stage, output, findings, config=config, citations=citations,
                    budget=CorrectionBudget(limit=1, used=used), last_kind=last_kind,
                    corpus=corpus,
                )
                self.assertEqual(decision.action, action)
                expected_repair = (promotion if action == "selection_promotion" else
                                   repair if action.endswith("repair") else None)
                self.assertIs(decision.repair, expected_repair)
                expected_calls = {
                    "promote": call.promote(output, config=config, citations=citations),
                    "repair": call.repair(output, findings, config=config, citations=citations,
                                          corpus=corpus, selection_only=stage == "selection"),
                }
                self.assertEqual(helpers.mock_calls, [expected_calls[kind] for kind in order])
