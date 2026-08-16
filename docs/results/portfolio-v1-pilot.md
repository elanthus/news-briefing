# Portfolio v1 pilot — 2026-08-15

This is an operational pilot, not the preregistered five-repetition result. Its rows are excluded from final estimates and must not be used to claim model or prompt superiority.

## Frozen inputs and amendments

The suite, corpus/config inputs, prompt hashes, original model choices, stop conditions, and dated amendments are recorded in `evaluator/protocols/portfolio-v1.json`.

The original Claude Sonnet 5 path completed nine small-corpus rows, then three consecutive production-corpus calls exceeded the frozen 300-second timeout and opened its circuit. The failed attempt is preserved under `evaluator/results/portfolio-v1-pilot-20260814/`. Per owner direction, OpenRouter `tencent/hy3` replaced Sonnet for the usable pilot.

DeepSeek V4 Flash with high reasoning produced seven `finish_reason='length'` responses before its circuit opened; low reasoning reproduced the same 8,192-token completion-budget exhaustion and was stopped to avoid redundant spend. Reasoning-disabled DeepSeek then completed the matrix. These conditions are separate and are not pooled.

## Usable one-trial groups

All values below use one authored observation per case. Wilson intervals remain in the generated reports, but this pilot is not an inferential comparison.

| Model | Prompt | First contract | Utility end-to-end, first | Utility end-to-end, final | Corrections | Final attack success | Utility under attack, final | First-call latency median / p95 | Reported cost |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| DeepSeek V4 Flash, reasoning disabled | production-2026-08 | 14/22 (63.6%) | 13/22 (59.1%) | 18/22 (81.8%) | 5/8 successful | 1/21 (4.8%) | 19/21 (90.5%) | 4.61s / 38.70s | $0.083169 |
| DeepSeek V4 Flash, reasoning disabled | reliability-v1 | 8/22 (36.4%) | 8/22 (36.4%) | 18/22 (81.8%) | 11/14 successful | 1/21 (4.8%) | 20/21 (95.2%) | 6.48s / 89.44s | $0.107732 |
| Tencent Hy3, reasoning disabled | production-2026-08 | 17/22 (77.3%) | 17/22 (77.3%) | 18/22 (81.8%) | 1/5 successful | 1/21 (4.8%) | 19/21 (90.5%) | 5.73s / 105.89s | $0.239529 |
| Tencent Hy3, reasoning disabled | reliability-v1 | 17/22 (77.3%) | 17/22 (77.3%) | 19/22 (86.4%) | 3/5 successful | 1/21 (4.8%) | 19/21 (90.5%) | 5.47s / 78.98s | $0.300672 |

The candidate's final utility delta was 0.0 percentage points on DeepSeek and +4.5 points on Hy3. Its final targeted attack-success delta was 0.0 points on both models. It therefore did not clear the preregistered +5-point utility and +5-point attack-resistance thresholds in this one-trial operational sample. This is a pilot observation, not a final decision.

The deterministic grounding proxy worsened from 5/103 to 9/134 generated utility topics on DeepSeek and from 2/94 to 14/134 on Hy3. Those denominators differ because prompts produced different topic counts. No pilot topics received human grounding adjudication, and all nine URL-scoped semantic propositions per group remain unreviewed. The proxy must not be described as a human grounding result.

## Operations and cost

- DeepSeek reasoning-disabled: 120/120 rows complete, zero provider/correction errors; $0.190901 reported.
- Hy3 reasoning-disabled: 120/120 rows complete, zero provider/correction errors; $0.540202 reported.
- Usable pilot total: $0.731103 reported.
- Known OpenRouter spend for benchmark attempts plus the earlier label-review pass is about $0.786. A small amount from pre-accounting-fix null-content envelopes and interrupted in-flight calls is not available in the local manifests; it is not silently treated as zero.
- No insufficient-credit, billing, or OpenRouter rate-limit error occurred.

At pilot rates, five repetitions of the two usable models × two prompts are estimated at roughly $3.66 in reported generation cost. The preregistered final run remains separately gated and has not been authorized by this pilot.

## Interpretation

The pilot established a compatible final configuration: OpenRouter DeepSeek V4 Flash and Tencent Hy3, both with temperature 0, no seed, reasoning disabled, and a 300-second per-call ceiling. It also exposed that reasoning-enabled DeepSeek and the Claude Code Sonnet path are operationally unsuitable for this corpus under the frozen controls.

Before any portfolio claim, the plan still requires a separately approved five-repetition interleaved/randomized final run, paired case-clustered comparison, complete human grounding and meaning adjudication, and the final result/model-card bundle.
