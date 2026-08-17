# Portfolio v1 reliability history

Only validated aggregate summaries belong here. Raw generations, provider payloads, human-review mappings,
and credentials remain local and ignored. Do not draw a trend across different suite hashes or run
protocols without an explicit shared-case comparison.

| Completed (UTC) | Suite | Model / prompt | Final utility | Final attack success | Human grounding | First-call median | Cost | Completeness | Decision |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| 2026-08-16 | `aa341680…` | DeepSeek V4 Flash / production | 87/110 (79.1%) | 4/105 (3.8%) | n/a; 520 unreviewed | 4.53s | $0.3345 | 300/300 | control |
| 2026-08-16 | `aa341680…` | DeepSeek V4 Flash / reliability-v1 | 97/110 (88.2%) | 3/105 (2.9%) | n/a; 670 unreviewed | 3.22s | $0.3259 | 300/300 | do not promote |
| 2026-08-16 | `aa341680…` | Tencent HY3 / production | 90/110 (81.8%) | 6/105 (5.7%) | n/a; 470 unreviewed | 4.67s | $1.1869 | 300/300 | control |
| 2026-08-16 | `aa341680…` | Tencent HY3 / reliability-v1 | 86/110 (78.2%) | 3/105 (2.9%) | n/a; 510 unreviewed | 4.69s | $1.1865 | 300/300 | do not promote |

The machine-readable record is [`portfolio-v1.json`](portfolio-v1.json). Compatibility, completeness,
review-trigger, and promotion rules are declared in [`../regression-policy.json`](../regression-policy.json).
