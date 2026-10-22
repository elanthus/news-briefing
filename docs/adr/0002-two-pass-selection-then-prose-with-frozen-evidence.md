# 0002: Separate selection from prose and freeze evidence

## Status

Accepted.

## Context

Selection requires the complete corpus. Prose requires only accepted evidence for each output position.
Provider schema acceptance does not prove eligibility, uniqueness, placement, or count rules.

## Decision

The first pass returns citation references by section and position, without prose.
Code validates and safely repairs the selection, then freezes its evidence groups.
The second pass receives only that evidence and returns headlines, summaries, and exclusion reasons without citation fields.
Code restores references by position and validates the complete candidate.
PR #150 records this change as “Freeze evidence before generating prose.”

## Consequences

The prose pass cannot select new evidence or change the accepted grouping.
Production selection and prose use separate schemas and correction allowances.
The evaluator intentionally permits only one correction across both stages.
Code still validates the complete candidate independently of provider schema support.
A successful generation requires at least two model calls before any correction.

## References

- [Design notes: the code-owned runner](../design.md#the-code-owned-runner)
- [Design notes: orchestration view](../design.md#orchestration-view)
- [Evaluator production-parity path](../../evaluator/README.md#production-parity-generation-path)
- [`agent_runner/output.py`](../../agent_runner/output.py)
