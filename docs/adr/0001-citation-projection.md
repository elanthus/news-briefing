# 0001: Keep citation destinations out of model input

## Status

Accepted

## Context

The model needs source evidence to select and summarize stories, but code owns
the rule that every published destination comes from the fetched corpus. Asking
the model to reproduce URLs would leave destination identity under model control.

## Decision

Before generation, code projects each corpus item to its model-visible evidence
and one opaque `citation_` handle. Article and Hacker News discussion URLs remain
together in a code-owned map that the model does not receive. Structured-output
validation accepts only eligible handles and rejects destinations in free-form
fields. After validation, the renderer resolves each selected handle to all of
its distinct code-owned destinations.

## Consequences

The model cannot author, substitute, or omit a destination that survives the
structured runner. Hacker News article and discussion links remain attached to
the same evidence item, and self-post destinations are emitted once. The
complete-Markdown checker remains an independent backstop for hand-authored and
historical output.

## References

- [Design notes: the code-owned runner](../design.md#the-code-owned-runner)
- [Runtime overview](../../README.md#architecture)
- [`agent_runner/output.py`](../../agent_runner/output.py)
