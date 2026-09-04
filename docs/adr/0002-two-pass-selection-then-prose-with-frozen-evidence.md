# 0002: Separate selection from prose and freeze evidence

## Status

Accepted

## Context

Selection must be checked against the complete closed corpus, while prose should
be written only from the evidence already accepted for each output position.
Provider schema acceptance does not prove the application's eligibility,
uniqueness, placement, or count rules.

## Decision

The first model pass returns section-positioned citation references and no prose.
Code validates and safely repairs that selection, then freezes its evidence
groups. The second pass receives only the selected, position-scoped evidence and
returns headlines, summaries, or exclusion reasons without citation fields.
Code restores the frozen references by position and validates the complete
candidate. PR #150 recorded this change as “Freeze evidence before generating
prose.”

## Consequences

The prose pass cannot select new evidence or change the accepted grouping.
Selection and prose failures use their own schemas and correction budgets. Code
still validates the complete candidate because provider schema handling is a
constraint for cooperative generation, not the application guarantee.

## References

- [Design notes: the code-owned runner](../design.md#the-code-owned-runner)
- [Design notes: orchestration view](../design.md#orchestration-view)
- [Evaluator production-parity path](../../evaluator/README.md#production-parity-generation-path)
- [`agent_runner/output.py`](../../agent_runner/output.py)
