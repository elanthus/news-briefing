"""Report final attack success by case family from a committed evidence ledger.

Usage::

    python3 -S -m evaluator.family_rates docs/results/parity-v2-evidence/ledger.json

The script reads only the ledger. It classifies attack rows the same way
``evaluator.report`` does: a row with ``corpus_position`` set is a
position/count ablation replicate, and every other non-clean attack row belongs
to the primary set. Clean twins (``is_clean_pair``) are reported on their own.

Trial-level rates count completed case-trials. Case-level rates count a case as
compromised when at least one of its completed trials succeeded; ``n`` is the
number of distinct cases. Both use ``evaluator.metrics.wilson_interval``.
Temperature-0 repeats of one case are not independent, so the case-level
interval is the more conservative reading.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from evaluator.metrics import wilson_interval


def _fmt(successes: int, trials: int) -> str:
    interval = wilson_interval(successes, trials)
    if interval is None:
        return f"{successes}/{trials}"
    low, high = (100 * value for value in interval)
    return f"{successes}/{trials}; {100 * successes / trials:.1f}% [{low:.1f}, {high:.1f}]"


def _subset(row: dict[str, Any]) -> str | None:
    if row.get("case_kind") != "attack":
        return None
    if row.get("is_clean_pair"):
        return "clean"
    return "ablation" if row.get("corpus_position") is not None else "primary"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    ledger = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    trials: dict[tuple[str, str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    cases: dict[tuple[str, str, str, str], dict[str, bool]] = defaultdict(dict)
    errors: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for row in ledger["results"]:
        subset = _subset(row)
        if subset is None:
            continue
        condition = (row["model"], row["prompt_version"])
        for family in (row["case_family"], "all"):
            key = (*condition, subset, family)
            if row.get("error") is not None:
                errors[key] += 1
                continue
            success = bool(row["final"]["oracle"]["attack_success"])
            trials[key][0] += success
            trials[key][1] += 1
            cases[key][row["case_id"]] = cases[key].get(row["case_id"], False) or success
    print("| Model | Prompt | Subset | Family | Trial-level | Case-level (>=1 success) | Provider errors |")
    print("|---|---|---|---|---:|---:|---:|")
    for key in sorted(trials):
        successes, completed = trials[key]
        compromised = sum(cases[key].values())
        print(
            f"| {key[0]} | {key[1]} | {key[2]} | {key[3]} | {_fmt(successes, completed)} | "
            f"{_fmt(compromised, len(cases[key]))} | {errors.get(key, 0)} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
