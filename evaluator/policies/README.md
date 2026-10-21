# Superseded regression policies

Each file here is a superseded `evaluator/regression-policy.json`, kept byte for byte. A committed
comparison records the SHA-256 of the policy it ran under. Regenerating that comparison under the
archived bytes reproduces its original decision rule and output shape. Never edit these files.

| File | Policy version | SHA-256 | In force |
|---|---:|---|---|
| [`regression-policy-v1.json`](regression-policy-v1.json) | 1 | `bf9ed083df18bcbcaef388909b41f819eff1b8d674a2ab8aed02de43c629ce46` | 2026-08-15 to 2026-10-20 |

Version 1 tested the utility point estimate only. Version 2 also requires the interval's lower bound
to exceed the policy floor, and it adds `promotion_requires`. See
[Denominators and uncertainty](../../docs/evaluation-methodology.md#denominators-and-uncertainty).

```bash
python3 -S -m evaluator compare BASELINE CANDIDATE --allow-descriptive \
  --regression-policy evaluator/policies/regression-policy-v1.json
```

`evaluator.comparison.regression_policy_for_sha256()` resolves a recorded hash to the current or
archived file.
