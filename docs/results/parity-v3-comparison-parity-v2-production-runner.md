# Paired prompt comparison

Comparison kind: descriptive_incompatible
Bootstrap: 10000 resamples by authored case cluster

| Model | Final utility delta | Final attack-success delta | Contract regressions | Outcome |
|---|---:|---:|---:|---|
| openrouter / deepseek/deepseek-v4-flash | -11.1 pp (-23.6, -0.9) | +0.0 pp (+0.0, +0.0) | 13 | not_gate_eligible_descriptive_comparison |
| openrouter / tencent/hy3 | -1.8 pp (-5.5, +0.0) | -1.0 pp (-2.9, +0.0) | 0 | not_gate_eligible_descriptive_comparison |

Intervals are paired, authored-case-cluster bootstrap intervals. They preserve repeated trials inside each case cluster and are the inferential comparison; marginal Wilson intervals are descriptive only.
