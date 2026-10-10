# Historical semantic candidate fixture

`historical-semantic-candidate.json.gz` is a gzip-compressed UTF-8 JSON mapping
of the exact artifact paths needed to read a candidate-only semantic repair.
The fixed extraction paths and compressed SHA-256 are checked by
`tests/historical_semantic_candidate.py`. It has no runtime dependency.

SHA-256: `1d39d184181c78d0dce0898c609befe84e5e07096e54d59511deaf3a8ad2b8e3`.

Recorded offline on October 10, 2026 from producer commit
`3825f520bc77417a29813975e16f4e0103233aa3`, using the repository's
`fixture_contract`, `make_run`, fake generation provider, `Judge`, and
`PatchProvider`. The removed producer was invoked once with
`apply_repairs=False`; every generation and judgment was fake, with synthetic
reported costs and no paid calls. The snapshot contains the original run, an
accepted unpublished candidate, initial and follow-up reports, repair-call
records, and the hash-bound audit. Requests, raw provider responses, and unrelated
artifact files are omitted. The embedded manifest hashes still bind each retained
artifact exactly; byte contents are frozen, not reconstructed by current code.

These are compatibility inputs, not current operating evidence. Reader tests
establish that the candidate remains unpublished, public candidate scores and
integrity remain readable, and modified persisted inputs are rejected. The
current daily producer always applies a fully accepted repair; standalone Jev
advisory research remains available separately.
