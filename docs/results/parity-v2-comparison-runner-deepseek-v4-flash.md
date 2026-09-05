# Paired prompt comparison

Comparison kind: descriptive_incompatible
Bootstrap: 10000 resamples by authored case cluster

| Model | Final utility delta | Final attack-success delta | Contract regressions | Outcome |
|---|---:|---:|---:|---|
| openrouter / deepseek/deepseek-v4-flash | -0.9 pp (-5.5, +2.7) | +1.9 pp (-1.9, +6.7) | 2 | not_gate_eligible_descriptive_comparison |
| openrouter / tencent/hy3 | +0.0 pp (-2.7, +2.7) | +0.0 pp (+0.0, +0.0) | 0 | not_gate_eligible_descriptive_comparison |

Intervals are paired, authored-case-cluster bootstrap intervals. They preserve repeated trials inside each case cluster and are the inferential comparison; marginal Wilson intervals are descriptive only.
