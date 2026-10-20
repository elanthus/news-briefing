# Jev-stage v1 partial-day supplement

Status: **execution complete; supplemental automated outcomes recorded**.

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

## Results

Execution revision: `11e3b4b16cb7398b2e6d45c68b8ef70c76d310ec`. The [machine-readable result](jev-stage-v1-partial-supplement.json)
contains the frozen protocol digest, exact cohort, per-day artifact ledger and confidence intervals.

| Outcome | Supplemental result |
|---|---:|
| Complete initial and required follow-up review | 11/11 |
| Initial reviews reaching the call limit | 0/11 |
| Follow-up reviews reaching the call limit | 0/9 |
| Briefing generation calls | 0 |
| Unknown-cost calls | 0 |
| Reported supplemental spend (USD) | 0.796944 |

The original cohort had 0/11 complete reviews. The original full-frame result remains
17/28 complete reviews; this supplement is reported separately and does not revise it.

| Review phase | Calls used per executed phase | Reported spend (USD) |
|---|---:|---:|
| Initial review and confirmation | 132–165 | 0.422876 |
| Follow-up review and confirmation | 112–158 | 0.332700 |
| Repair generation | bounded to two calls per round | 0.041367 |

| Repair target outcome | Slots / targets |
|---|---:|
| Applied | 18/21 |
| Withheld | 3/21 |
| Failed | 0/21 |
| Skipped | 0/21 |

| Check kind | Initial flags / returned checks | Confirmed / flags |
|---|---:|---:|
| duplicate | 1/8280 | 0/1 |
| irrelevant_citation | 32/818 | 24/32 |
| reversed_claim | 0/239 | 0/0 |
| strengthened_claim | 0/239 | 0/0 |
| unsafe_grouping | 35/346 | 35/35 |
| unsupported_claim | 0/239 | 0/0 |

| Date | Review status | Initial / follow-up calls | Publication decision | Reported USD |
|---|---|---:|---|---:|
| 2026-09-19 | complete | 139 / 0 | original_retained | 0.038706 |
| 2026-09-21 | complete | 133 / 0 | original_retained | 0.035822 |
| 2026-09-22 | complete | 153 / 143 | repaired_applied | 0.089581 |
| 2026-09-23 | complete | 134 / 136 | original_retained | 0.075037 |
| 2026-09-29 | complete | 141 / 134 | partial_applied | 0.082816 |
| 2026-10-01 | complete | 132 / 121 | repaired_applied | 0.073277 |
| 2026-10-03 | complete | 138 / 134 | partial_applied | 0.079188 |
| 2026-10-06 | complete | 147 / 140 | repaired_applied | 0.082685 |
| 2026-10-09 | complete | 165 / 158 | repaired_applied | 0.090644 |
| 2026-10-12 | complete | 139 / 112 | repaired_applied | 0.071482 |
| 2026-10-13 | complete | 135 / 130 | repaired_applied | 0.077704 |

All original selected-day trees were verified unchanged after the replay.
These are automated review and repair outcomes; no new human adjudication was performed.

## Reproduce

Run from the revision recorded in the supplemental result, with the original
private tree available and `OPENROUTER_API_KEY` in the environment:

```bash
python3 -S rerun_jev_partials.py \
  --source-root .news-briefing/jev-stage \
  --output-root .news-briefing/jev-stage-v1-partial-supplement-repro \
  --output .news-briefing/jev-stage-v1-partial-supplement-repro.json
```

Use the committed supplemental JSON as the reference result. Reproductions make
fresh model calls, so their judgments, repairs and spend can differ. Choose new
unused output paths for each reproduction; retain the original committed result.

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
