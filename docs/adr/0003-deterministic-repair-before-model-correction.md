# 0003: Run deterministic repair before model correction

## Status

Accepted.

## Context

Code can repair some placement errors directly.
Unknown references, free-form URLs, and invalid shapes can require rejection or model correction.
Using model corrections for mechanical errors reduces the allowance for errors that require model judgment.

## Decision

When all blocking findings permit deterministic repair, apply that repair before model correction.
Then validate again.
For a WARN-only oversized summary, substitute the complete known cited excerpt when safe.
After model corrections are exhausted, apply any permitted remaining repair before the disposition gate.

## Consequences

Deterministic repairs do not consume model corrections.
They can remove ineligible, repeated, or permitted over-limit entries.
They cannot remove unknown evidence or other unrepairable failures merely to pass validation.
Each attempt retains its actions in the manifest.
Public `repair_actions` includes only the final attempt's actions.
`provenance.repair_action_count` totals repairs across all attempts, including superseded attempts.

## References

- [Design notes: repair and correction](../design.md#the-code-owned-runner)
- [Design notes: orchestration view](../design.md#orchestration-view)
- [`agent_runner/output.py`](../../agent_runner/output.py)
- [`agent_runner/runner.py`](../../agent_runner/runner.py)
