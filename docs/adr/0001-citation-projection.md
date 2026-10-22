# 0001: Keep citation destinations out of model input

## Status

Accepted.

## Context

The model needs evidence to select and summarize stories.
Code must ensure that every published destination belongs to the corpus.
Model-authored URLs would leave destination identity under model control.

## Decision

Project each item into model-visible evidence and one opaque `citation_` handle.
Keep article and Hacker News discussion URLs together in a code-owned map outside model input.
Validate eligible handles and reject destinations in free-form fields.
After validation, expand each selected handle to all distinct stored destinations.

## Consequences

A model-authored, substituted, or omitted destination cannot survive the structured runner for a selected item.
Article and discussion destinations stay paired. Self-post destinations appear once.
The independent Markdown checker remains available for hand-authored and historical output.
The model cannot use a source URL or domain to assess evidence.
It sees titles, excerpts, and source names.

## References

- [Design notes: the code-owned runner](../design.md#the-code-owned-runner)
- [Runtime overview](../../README.md#architecture)
- [`agent_runner/output.py`](../../agent_runner/output.py)
