# Reliability history

Versioned aggregate summaries of completed evaluator runs, one row per model/prompt condition.

## What may be committed here

Only validated aggregate summaries belong here. Raw generations, provider payloads, human-review mappings,
and credentials remain local and ignored.

Do not draw a trend across different suite hashes or run protocols without an explicit shared-case
comparison. The suite is versioned by hash precisely because two rows with different `Suite` values are not
measuring the same thing.

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

- **Suite** is the truncated SHA-256 of the case fixture. Rows with different values are not comparable.
- **Final utility** is `end_to_end_utility_final` and **Final attack success** is
  `targeted_attack_success_final`, both measured after at most one checker-guided correction and on the
  separate utility and primary-attack denominators defined in the
  [evaluation methodology](../../docs/evaluation-methodology.md#denominators-and-uncertainty).
- **Human grounding** is `n/a` where blinded human review was never completed; the unreviewed topic count
  is retained rather than being reported as zero error. Machine judgments recorded in the JSON are not
  counted here.
- **First-call median** is `first_call_latency_median_ms`, the median latency of the initial generation
  call, excluding correction calls.
- **Cost** is `reported_cost_usd`, the billed total OpenRouter reported for that condition.
- **Completeness** is completed rows over planned rows. Incomplete runs cannot satisfy a promotion gate.
- **Decision** is the outcome under the regression policy: `control` for the baseline prompt, and a
  promotion verdict for the candidate.

The machine-readable record is [`portfolio-v1.json`](portfolio-v1.json). Compatibility, completeness,
review-trigger, and promotion rules are declared in [`../regression-policy.json`](../regression-policy.json).
