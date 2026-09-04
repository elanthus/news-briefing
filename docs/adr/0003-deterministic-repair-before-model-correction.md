# 0003: Run deterministic repair before model correction

## Status

Accepted

## Context

Some blocking findings are mechanical placement errors that code can repair by
construction. Other findings, such as unknown references, free-form URLs, and
schema-shape violations, require rejection or a model correction. Spending the
bounded correction budget on the first group leaves less budget for the second.

## Decision

When every blocking finding is repairable, the runner applies the deterministic
normalizer before asking the model for a correction and then re-enters
validation. It also replaces a WARN-only summary that exceeds its evidence with
the known cited excerpt when that excerpt is safe. After the model-correction
budget is exhausted, the normalizer runs again as a fallback before the
publication disposition gate.

## Consequences

Repairable findings do not consume a model correction. Repair can drop an
ineligible, repeated, or over-limit entry, but it does not remove unknown
evidence or other non-repairable failures to make a candidate pass. Repair
actions are retained in the run manifest, and public provenance contains only
actions that produced the final candidate.

## References

- [Design notes: repair and correction](../design.md#the-code-owned-runner)
- [Design notes: orchestration view](../design.md#orchestration-view)
- [`agent_runner/output.py`](../../agent_runner/output.py)
- [`agent_runner/runner.py`](../../agent_runner/runner.py)
