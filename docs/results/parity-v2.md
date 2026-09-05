# Production parity v2 model card

Parity v2 reruns the 1,200-row parity matrix through the corrected production-parity path. The run used clean
tag `parity-v2-source-20260904` at commit `87db491cdf259d4089a40e27cada340cf1517c84`, protocol
[`parity-v1.json`](../../evaluator/protocols/parity-v1.json), two OpenRouter models, the `production-runner` and
`runner-deepseek-v4-flash` prompts, five trials, execution seed `20260901`, temperature 0, and disabled
reasoning. The public evidence is in [`parity-v2-evidence/`](parity-v2-evidence/).

The published matrix records 1,200/1,200 planned rows and has status `completed_with_errors`: 1,198 rows
completed and two DeepSeek rows ended in disclosed first-attempt malformed-JSON provider errors. HY3 completed
600/600 rows with no provider errors. The two raw components report $1.863904276212 in generation cost. That
total includes three HY3 calls from the interrupted primary checkpoint that the whole-adapter export excludes;
the 1,200 selected public rows account for $1.863241956212 across 2,461 reported calls, with two failed calls
whose total cost was unavailable.

## Lead results

Rates show `successes/trials; rate [95% Wilson interval]`. Utility is structural: contract-valid output,
populated routed sections, and declared case floors. It does not measure editorial quality or whether prose is
faithful to the linked article. Attack denominators are the 21 primary attack cases × 5 trials; the 12
position/count ablation replicates remain separate in the generated report.

| Model / prompt | Structural utility (first → final) | Targeted attack success (first → final) |
|---|---:|---:|
| DeepSeek V4 Flash / production-runner | 103/109; 94.5% [88.5, 97.5] → 103/109; 94.5% [88.5, 97.5] | 0/105; 0.0% [0.0, 3.5] → 0/105; 0.0% [0.0, 3.5] |
| DeepSeek V4 Flash / runner-deepseek-v4-flash | 99/110; 90.0% [83.0, 94.3] → 101/110; 91.8% [85.2, 95.6] | 5/105; 4.8% [2.1, 10.7] → 5/105; 4.8% [2.1, 10.7] |
| Tencent HY3 / production-runner | 103/110; 93.6% [87.4, 96.9] → 105/110; 95.5% [89.8, 98.0] | 1/105; 1.0% [0.2, 5.2] → 1/105; 1.0% [0.2, 5.2] |
| Tencent HY3 / runner-deepseek-v4-flash | 98/110; 89.1% [81.9, 93.6] → 103/110; 93.6% [87.4, 96.9] | 0/105; 0.0% [0.0, 3.5] → 0/105; 0.0% [0.0, 3.5] |

The two DeepSeek failures reduce only their affected completed-row denominators. One occurred on
`utility-production-cross-section-routing` trial 2 under `production-runner`; the other occurred on
`attack-citation-fabrication-late-multi` trial 5 under `runner-deepseek-v4-flash`. Both are retained as
`provider_error` rows and score neither success nor failure.

## Corrected deterministic-repair path

Unlike parity v1, this run measures the shared production repair decision before any model correction. The
first schema-constrained call selects opaque evidence handles. Code validates the selection and can apply the
bounded deterministic selection repair. It then freezes the selected evidence, sends only position-scoped
evidence to the prose pass, reattaches the frozen handles, validates the complete candidate, and can apply the
bounded deterministic prose repair. A model correction is used only if blocking findings remain. The renderer
finally restores code-owned destinations. The public manifest retains the repair records; 230 first results and
257 final results contain at least one deterministic repair record.

This path preserves the central boundary: models choose and write within projected evidence, while code owns
destination mapping, bounds, validation, repair eligibility, and final citation attachment. The measured
changes from first to final include deterministic repair and, where still needed, one model correction;
they do not isolate either mechanism's causal contribution.

## Comparison with parity v1

The comparator required the merged public export first because it accepts one candidate manifest, while the raw
parity v2 result is split across two components. Both same-prompt comparisons used 10,000 authored-case-cluster
bootstrap resamples with seed 1729. The policy requires at least +5.0 percentage points of final end-to-end
utility improvement, an attack-success delta interval whose upper bound is strictly below +5.0 points, zero
deterministic contract regressions, and no increase in human grounding error.

Every model/prompt result has the exact comparator outcome `not_gate_eligible_descriptive_comparison` and
`passes_all_available_rules: false`. Both runs contain provider-error rows, and two paired rows per prompt
comparison—one per model—have different adjudication-completion states, so neither comparison is
gate-compatible. For `production-runner`, the mismatches are DeepSeek `attack-category-selection` trial 5 and
HY3 `attack-category-selection` trial 1. For `runner-deepseek-v4-flash`, they are DeepSeek
`attack-citation-fabrication-late-multi` trial 3 and HY3 `attack-category-selection-combined` trial 1. Human
grounding nonincrease is undetermined.

| Model / prompt | Final utility delta (95% clustered interval) | Final attack-success delta (95% clustered interval) | Contract regressions | Available-rule checks |
|---|---:|---:|---:|---|
| DeepSeek / production-runner | +0.0 pp [-2.8, +2.7] | -3.8 pp [-9.5, 0.0] | 1 | utility fail; attack pass; contract fail |
| HY3 / production-runner | +3.6 pp [0.0, +9.1] | -1.0 pp [-2.9, 0.0] | 0 | utility fail; attack pass; contract pass |
| DeepSeek / runner-deepseek-v4-flash | -0.9 pp [-5.5, +2.7] | +1.9 pp [-1.9, +6.7] | 2 | utility fail; attack fail; contract fail |
| HY3 / runner-deepseek-v4-flash | +0.0 pp [-2.7, +2.7] | +0.0 pp [0.0, 0.0] | 0 | utility fail; attack pass; contract pass |

The machine-readable comparisons are
[`parity-v2-comparison-production-runner.json`](parity-v2-comparison-production-runner.json) and
[`parity-v2-comparison-runner-deepseek-v4-flash.json`](parity-v2-comparison-runner-deepseek-v4-flash.json).
Both record `regression_policy_sha256` as
`bf9ed083df18bcbcaef388909b41f819eff1b8d674a2ab8aed02de43c629ce46`.

## Provider schema support

HY3 received the selection schema with `uniqueItems` removed because its OpenRouter grammar backends do not
compile that keyword. `maxItems` still bounds each citation-reference array, and the code-owned validator still
rejects duplicates after generation, but HY3 answers a weaker provider-enforced citation contract than
DeepSeek. Their rates are therefore not cell-for-cell comparable.

## Public evidence and limitations

The exporter selected the complete 600-row DeepSeek block from the interrupted 603-row primary manifest and
the complete 600-row HY3 block from the dedicated manifest. It excluded the primary manifest's three partial,
duplicate HY3 rows without rewriting either raw checkpoint. The bundle contains only the supported redacted
manifest, score ledger, adjudications, generated reports, metadata, and checksums. Verify and regenerate it
without credentials or provider calls:

```bash
python3 -S -m evaluator verify-public-run docs/results/parity-v2-evidence
```

The 55 authored cases are deliberately enriched for known boundaries, not sampled deployment traffic. Wilson
intervals describe this fixed suite, and repeated trials do not establish deployment generalization. Provider
behavior and model aliases can change despite temperature 0. No pairwise prose-quality judging, independent
human semantic review, or human grounding review was completed. The generated grounding proxy is a checker
heuristic, not a semantic-quality measurement. These limits, the HY3 schema asymmetry, and the provider errors
preclude claims that this run proves news quality, factual grounding, or a generally superior prompt or model.
