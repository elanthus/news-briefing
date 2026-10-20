# Jev-stage v1 partial-day supplement

Status: **protocol and cohort frozen before execution**.

This supplement replays the 11 partial-review days from
[Jev-stage v1](jev-stage-v1.md), using the exact same frozen ready briefings.
The Jev call cap rises from 128 to 256 per review phase. The USD 0.10 estimated
cost guard and 120-second deadline per phase, confirmation policy, bounded HY3
repairs and validation remain unchanged. Briefing generation is not repeated.

The [protocol](../../evaluator/protocols/jev-stage-v1-partial-supplement.json)
fixes the original metric digest and the selected dates before provider calls.
The runner records the clean execution revision, protocol digest, per-day input
and audit digests, reported costs, and verified outcomes in a separate result.
The original evaluation, human labels, adjudication, and results remain unchanged.

## Why these days were partial

The original metric calls these "estimated spending guard" stops, but the
implementation uses the same `budget` reason for its call and cost guards.
All 11 selected initial reviews exhausted the 128-call allowance; reported spend
per stopped review was approximately USD 0.035 to 0.038, below USD 0.10.
Eight stopped before screening finished, and three finished screening but ran
out of calls during isolated confirmation. Duplicate comparisons and repeated
evidence within the 24,000-byte request bound required many small batches.

The original record is retained as published. This supplement names the actual
call-bound condition without rewriting the historical metric.

## Reproduce

Run from the revision recorded in the supplemental result, with the original
private tree available and `OPENROUTER_API_KEY` in the environment:

```bash
python3 -S rerun_jev_partials.py \
  --source-root .news-briefing/jev-stage \
  --output-root .news-briefing/jev-stage-v1-partial-supplement \
  --output docs/results/jev-stage-v1-partial-supplement.json
```

The output tree and result must be new. The runner validates the original
artifacts, copies each selected ready run into the new tree, and runs Jev,
confirmation, repair and follow-up there. It verifies the original selected day
trees are byte-for-byte unchanged before publishing the result. Corpora, source
excerpts, prose, raw responses and credentials remain private and ignored.

## Claims boundary

This is a post hoc selected completion supplement. Report its denominator as
11 selected partial days, separately from the original 28-day evaluation.
Do not pool the new outcomes with the original frozen results or transfer the
original human labels to new repairs. Model calls are stochastic, so differences
cannot be attributed solely to the call cap. Completion and automated scores do
not establish Jev precision, recall, or ground-truth accuracy.
