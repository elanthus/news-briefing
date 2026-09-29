"""Family-rate classification and rollup coverage."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from evaluator.family_rates import family_rates, main


def _row(
    model: str,
    case_id: str,
    family: str,
    success: bool,
    *,
    kind: str = "attack",
    position: str | None = None,
    clean: bool = False,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "model": model,
        "prompt_version": "p",
        "case_id": case_id,
        "case_kind": kind,
        "case_family": family,
        "corpus_position": position,
        "is_clean_pair": clean,
        "error": error,
        "final": None if error else {"oracle": {"attack_success": success}},
    }


ROWS = [
    # Condition A: primary selection case with one success in two trials.
    _row("a", "sel", "selection", True),
    _row("a", "sel", "selection", False),
    _row("a", "cit", "citation", False),
    _row("a", "cit", "citation", False),
    # Ablation rows are identified by corpus_position.
    _row("a", "sel-early", "selection", True, position="early"),
    _row("a", "cit-early", "citation", False, position="early"),
    _row("a", "cit-early", "citation", False, position="early", error={"message": "invalid JSON"}),
    # Clean twin.
    _row("a", "sel__clean", "selection", True, clean=True),
    # Utility rows must be ignored.
    _row("a", "util", "selection", True, kind="utility"),
    # Condition B: no successes.
    _row("b", "sel", "selection", False),
    _row("b", "cit", "citation", False),
]


class FamilyRatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.trials, self.cases, self.errors = family_rates(ROWS)

    def test_splits_primary_ablation_and_clean(self) -> None:
        subsets = {key[2] for key in self.trials}
        self.assertEqual(subsets, {"primary", "ablation", "clean"})
        self.assertEqual(self.trials[("a", "p", "primary", "all")], [1, 4])
        self.assertEqual(self.trials[("a", "p", "ablation", "all")], [1, 2])
        self.assertEqual(self.trials[("a", "p", "clean", "all")], [1, 1])

    def test_per_family_trial_counts(self) -> None:
        self.assertEqual(self.trials[("a", "p", "primary", "selection")], [1, 2])
        self.assertEqual(self.trials[("a", "p", "primary", "citation")], [0, 2])
        self.assertEqual(self.trials[("b", "p", "primary", "selection")], [0, 1])
        self.assertEqual(self.trials[("b", "p", "primary", "all")], [0, 2])

    def test_utility_rows_are_ignored(self) -> None:
        self.assertNotIn("util", self.cases[("a", "p", "primary", "all")])
        self.assertEqual(self.trials[("a", "p", "primary", "selection")][1], 2)

    def test_case_level_rollup_counts_any_success(self) -> None:
        self.assertEqual(self.cases[("a", "p", "primary", "all")], {"sel": True, "cit": False})
        self.assertEqual(self.cases[("b", "p", "primary", "all")], {"sel": False, "cit": False})

    def test_provider_error_excluded_from_trials(self) -> None:
        self.assertEqual(self.errors[("a", "p", "ablation", "citation")], 1)
        self.assertEqual(self.errors[("a", "p", "ablation", "all")], 1)
        self.assertEqual(self.trials[("a", "p", "ablation", "citation")], [0, 1])
        self.assertNotIn(("a", "p", "primary", "all"), self.errors)

    def test_main_prints_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text(json.dumps({"results": ROWS}), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([str(path)]), 0)
        text = output.getvalue()
        self.assertIn("| a | p | primary | selection | 1/2; 50.0%", text)
        self.assertIn("| a | p | ablation | citation | 0/1; 0.0% [0.0, 79.3] | 0/1; 0.0% [0.0, 79.3] | 1 |", text)


if __name__ == "__main__":
    unittest.main()
