# Historical semantic candidate fixture

`historical-semantic-candidate.json.gz` is a gzip-compressed UTF-8 JSON mapping
of the exact artifact paths needed to read a candidate-only semantic repair.
The fixed extraction paths and compressed SHA-256 are checked by
`tests/historical_semantic_candidate.py`. It has no runtime dependency.

SHA-256: `2ca987fd45d11ae9e3bee73583e4331bbc032c2afc80e9b947a6f028d1de1204`.

Recorded offline on October 10, 2026 from producer commit
`979c395c8e8c05b8f65772c72cdcd8335f0ca998`, using the repository's
`fixture_contract`, `make_run`, fake generation provider, `Judge`, and
`PatchProvider`. The removed producer was invoked once with
`apply_repairs=False`; every generation and judgment was fake, with synthetic
reported costs and no paid calls. Judge and repair calls use exactly representable
synthetic costs of 1/65536 USD, so identical arithmetic stays stable across supported
Python versions without changing production ledger validation. The snapshot contains the original run, an
accepted unpublished candidate, initial and follow-up reports, repair-call
records, and the hash-bound audit. Requests, raw provider responses, and unrelated
artifact files are omitted. The embedded manifest hashes still bind each retained
artifact exactly; byte contents are frozen, not reconstructed by current code.

These are compatibility inputs, not current operating evidence. Reader tests
establish that the candidate remains unpublished, public candidate scores and
integrity remain readable, and modified persisted inputs are rejected. The
current daily producer always applies a fully accepted repair; standalone Jev
advisory research remains available separately.
