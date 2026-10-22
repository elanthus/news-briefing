# Superseded regression policies

These files preserve superseded policy bytes. Do not edit them.
A committed comparison records its policy hash.
Use those exact bytes to reproduce its original decision rules and output shape.

| File | Policy version | SHA-256 | In force |
|---|---:|---|---|
| [`regression-policy-v1.json`](regression-policy-v1.json) | 1 | `bf9ed083df18bcbcaef388909b41f819eff1b8d674a2ab8aed02de43c629ce46` | 2026-09-04 to 2026-10-20 (its `effective_date` field says 2026-08-15) |

Earlier policy bytes from August 15 through September 3, 2026 are available only in Git history.
This includes the policy used by Portfolio v2.
`docs/results/portfolio-v2-comparison.json` predates policy hashing and has no `regression_policy_sha256`.

Version 1 tested only the utility point estimate.
Version 2 also requires the interval lower bound to exceed the floor and adds `promotion_requires`.
See [denominators and uncertainty](../../docs/evaluation-methodology.md#denominators-and-uncertainty).

To reproduce a version 1 comparison:

```bash
python3 -S -m evaluator compare BASELINE CANDIDATE --allow-descriptive \
  --regression-policy evaluator/policies/regression-policy-v1.json
```

Replace `BASELINE` and `CANDIDATE` with report or manifest paths.
`evaluator.comparison.regression_policy_for_sha256()` resolves a recorded hash to the current or archived policy file.
