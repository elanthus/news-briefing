# Low claim/evidence overlap diagnostic

`low_claim_evidence_overlap` is a deliberately narrow, nonblocking quality warning. It reports a topic only when both the model claim and its cited excerpts contain at least eight distinctive lexical terms, fewer than two terms overlap, and overlap is at most 8% of the claim terms. The 8% ceiling independently constrains the diagnostic: a one-term match needs at least 13 claim terms, while a zero-term match can still qualify at the eight-term minimum. It is a triage signal, not a claim that semantic entailment failed: paraphrase and named-entity variation remain outside deterministic proof.

## Predefined promotion gate

The warning may become blocking only after a prospective, human-reviewed clean set contains at least 400 topics across at least 14 report dates and the two-sided 95% Wilson interval's upper bound for false positives is at most 1%. The threshold and sample requirement are fixed before collecting that promotion set. `unsupported_figure` remains nonblocking independently because its known false positives measure a different heuristic.

## Retained-history baseline

The repository retains two clean historical briefings with matching corpora: the August 9 reference fixture and the hash-bound August 18 structured-output final. They contain 44 included topics in total. The diagnostic flagged 0 of 44. The two-sided 95% Wilson upper bound is about 8.0%, so this baseline is far too small to satisfy the promotion gate. The diagnostic therefore remains in the `quality` domain, is excluded from actionable public warning counts and panels, and is preserved in private run diagnostics.

`tests.test_eval_briefing.CommittedFixtureTest.test_low_overlap_on_retained_historical_clean_briefings` reproduces the baseline on every test run.
