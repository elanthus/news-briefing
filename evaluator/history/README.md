# Reliability history

This directory stores aggregate evaluator summaries, with one row per model/prompt condition.

## What may be committed here

Commit only validated aggregates.
Keep raw generations, provider payloads, human-review mappings, and credentials local and ignored.
Do not infer trends across different suite hashes or protocols without an explicit shared-case comparison.
Different suite hashes identify different measurements.

## Portfolio v1

These rows are a dated historical snapshot. Their model metrics are superseded by
[portfolio v2](../../docs/results/portfolio-v2.md), whose generation came from a clean tagged source tree;
do not cite them as the current reproducible result.

| Completed (UTC) | Suite | Model / prompt | Final utility | Final attack success | Human grounding | First-call median | Cost | Completeness | Decision |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| 2026-08-16 | `aa341680…` | DeepSeek V4 Flash / production | 87/110 (79.1%) | 4/105 (3.8%) | n/a; 520 unreviewed | 4.53s | $0.3345 | 300/300 | control |
| 2026-08-16 | `aa341680…` | DeepSeek V4 Flash / reliability-v1 | 97/110 (88.2%) | 3/105 (2.9%) | n/a; 670 unreviewed | 3.22s | $0.3259 | 300/300 | do not promote |
| 2026-08-16 | `aa341680…` | Tencent HY3 / production | 90/110 (81.8%) | 6/105 (5.7%) | n/a; 470 unreviewed | 4.67s | $1.1869 | 300/300 | control |
| 2026-08-16 | `aa341680…` | Tencent HY3 / reliability-v1 | 86/110 (78.2%) | 3/105 (2.9%) | n/a; 510 unreviewed | 4.69s | $1.1865 | 300/300 | do not promote |

Reading the columns:

- **Suite:** truncated SHA-256 of the case fixture. Different values are not directly comparable.
- **Final utility:** `end_to_end_utility_final`, after at most one checker-guided correction.
- **Final attack success:** `targeted_attack_success_final`, with its separate primary-attack denominator.
- **Human grounding:** `n/a` means blinded human review was not completed. Unreviewed topics remain visible; machine labels do not count here.
- **First-call median:** `first_call_latency_median_ms`, excluding correction calls.
- **Cost:** `reported_cost_usd`, the billed total returned by OpenRouter.
- **Completeness:** completed rows divided by planned rows. Incomplete runs cannot pass promotion.
- **Decision:** baseline `control` or the candidate verdict under the recorded policy.

See the [methodology](../../docs/evaluation-methodology.md#denominators-and-uncertainty) for denominator rules.
The historical machine record is [portfolio-v1.json](portfolio-v1.json).
The [current policy](../regression-policy.json) defines current comparison requirements; it does not rewrite historical decisions.
