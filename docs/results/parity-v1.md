# Production parity v1 model card

Parity v1 is the first portfolio-scale run of the production two-pass generation path: 1,200 preregistered
rows from clean tag `parity-v1-source-20260901c`, protocol [`parity-v1.json`](../../evaluator/protocols/parity-v1.json),
public evidence in [`parity-v1-evidence/`](parity-v1-evidence/). The matrix contains two OpenRouter models,
two frozen prompts, five trials, and 60 authored-or-derived case rows per model/prompt/trial group, the same
55 cases as [Portfolio v2](portfolio-v2.md). Reported generation cost was $1.80.

> **Generation path: `production-parity`.** The same cases run through the real two-pass runner
> (`--generation-path production-parity`): the selection call picks evidence against a schema enumerating only
> eligible identifiers, code freezes that selection, and a second call writes prose against a schema with no
> citation field. This is the path the daily briefing uses. Portfolio v2 ran the evaluator's direct-Markdown
> path, where the model authors its own links, so the two cards measure different contracts.

1,200 rows completed with one recorded provider failure: a malformed response from DeepSeek's backend,
published in the bundle as a `provider_error` row that scores nothing and is disclosed in the component
descriptor.

## Lead results

Rates show `successes/trials; rate [95% Wilson interval]`. Utility columns are **structural**: contract-valid
output, populated routed sections, and declared case floors. No column measures editorial quality or whether a
summary is faithful to the linked article. Attack denominators are the 21 primary attack cases × 5 trials; the 12
position/count ablation replicates are reported separately in the evidence bundle's report, as
[evaluation methodology](../evaluation-methodology.md#denominators-and-uncertainty) defines. The two prompts are the production runner prompt
([`briefing-runner-prompt.md`](../../briefing-runner-prompt.md)) and its DeepSeek variant
([`briefing-runner-prompt-deepseek-v4-flash.md`](../../briefing-runner-prompt-deepseek-v4-flash.md)).

| Model / prompt | Structural utility (after correction) | Targeted attack success (after correction) |
|---|---:|---:|
| DeepSeek V4 Flash / production-runner | 104/110; 94.5% [88.6, 97.5] | 4/105; 3.8% [1.5, 9.4] |
| DeepSeek V4 Flash / runner-deepseek | 102/110; 92.7% [86.3, 96.3] | 3/105; 2.9% [1.0, 8.1] |
| Tencent HY3 / production-runner | 101/110; 91.8% [85.2, 95.6] | 2/105; 1.9% [0.5, 6.7] |
| Tencent HY3 / runner-deepseek | 103/110; 93.6% [87.4, 96.9] | 0/105; 0.0% [0.0, 3.5] |

For comparison, Portfolio v2 on the direct-Markdown path scored 90.0% and 81.8% structural utility for the two
models under the production prompt, with 5.7% and 4.8% targeted attack success.

## What changed against the Markdown path

Parity scores higher on both axes, but the headline numbers are the least interesting part. **What changed is
which failures are possible at all.** 261 rows failed the contract on the Markdown path, against 42 here. The
Markdown failures were dominated by `missing_section` (162 rows), `category_ineligible` (97), and
`ungrounded_link` (9): the model failing to produce sections, filing stories into sections that were never
eligible, and citing things it was never given. On the parity path every one of those is zero, because the
schema enumerates each section's eligible identifiers and requires the sections. The 42 failing rows carry
`duplicate_item` (40 rows), `duplicate_citation_ref` (5), and `slots_underfilled` (1); the counts overlap,
since a row can trip more than one. Nearly all of it is the model selecting the same corpus item into two
different topics, which the schema does not forbid, so it survives to the checker.

Two caveats on the comparison. Citation-fabrication attacks fail 180/180 here, but they also failed 180/180 on
the Markdown path, where the checker caught them after the fact instead of the schema preventing them. The
improvement is concentrated in **selection** attacks (17/160 successes on Markdown, 7/160 on parity), and
parity is slightly worse on prose attacks (3/40, versus 0/40). And the correction loop contributes about
equally on both paths (+7.7 and +8.0 points), so the gap is not a repair artifact: first-attempt contract
validity is 78.9% on Markdown against 90.5% on parity.

## Provider schema support

`tencent/hy3` alone received the schema with `uniqueItems` removed, because its OpenRouter backends answer a
schema carrying that keyword with a grammar-compilation error instead of a completion. Its citation contract on
this run is therefore weaker than DeepSeek's, and its rates are not cell-for-cell comparable. The parity path
is less model-portable than the Markdown path for this reason: it needs a provider whose grammar engine
implements every keyword the production schema uses, and three of five models tried do not.

## Public evidence and verification

The committed evidence bundle contains every generated output and score primitive needed to recalculate the
aggregate report. Reviewers can verify it and regenerate the report with no credentials and no provider calls:

```bash
python3 -m evaluator verify-public-run docs/results/parity-v1-evidence
```

**"Structural utility" is not news quality.** Like Portfolio v2, this run publishes no human-reviewed
semantic-faithfulness or grounding score. Its review forms remain unjudged, and a blank cell is preferred to
another model's confidence standing in for human review. [Evaluation methodology](../evaluation-methodology.md)
defines every score family's denominator and the limits on how these numbers may be read.
