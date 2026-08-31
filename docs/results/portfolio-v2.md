# Portfolio v2 model card (clean rerun)

Portfolio v2 is the current reproducible generation result: 1,200 preregistered rows from clean tag
`portfolio-v2-source-20260819` at commit `29d7e3dac9b6c7f6042b9341fb4375dc5fae160c`. The matrix contains two
OpenRouter models, two frozen prompts, five trials, and 60 authored-or-derived case rows per
model/prompt/trial group. All rows completed; none failed, were skipped, or had a correction error.

> **Generation path: `markdown`.** This run used the evaluator's historical direct-Markdown path, in which the
> model authors the entire briefing including its own citations. It is **not** the production two-pass
> selection/prose path with citation projection, where the model never receives a destination. These results
> characterize model behavior under the weaker contract; they are not a measurement of the production runner.
> Production parity is available (`--generation-path production-parity`) and has not been run at this scale.

The candidate prompt is **not approved** for either model. DeepSeek loses final utility and introduces eight
contract regressions. HY3 gains only 1.8 percentage points of final utility and 1.0 point of attack resistance,
below both preregistered five-point thresholds. Human grounding remains unreviewed, but cannot change either
failed structural decision into a pass.

## Lead results

Rates show `successes/trials; rate [95% Wilson interval]`. Utility columns are **structural**: contract-valid
output, populated routed sections, and declared case floors. No column measures editorial quality or whether a
summary is faithful to the linked article.

| Model / prompt | First contract | End-to-end final | Final attack success | Utility under attack |
|---|---:|---:|---:|---:|
| DeepSeek / production | 80/110; 72.7% [63.7, 80.2] | 99/110; 90.0% [83.0, 94.3] | 6/105; 5.7% [2.6, 11.9] | 102/105; 97.1% [91.9, 99.0] |
| DeepSeek / reliability-v1 | 87/110; 79.1% [70.6, 85.6] | 95/110; 86.4% [78.7, 91.6] | 3/105; 2.9% [1.0, 8.1] | 98/105; 93.3% [86.9, 96.7] |
| HY3 / production | 90/110; 81.8% [73.6, 87.9] | 90/110; 81.8% [73.6, 87.9] | 5/105; 4.8% [2.1, 10.7] | 95/105; 90.5% [83.4, 94.7] |
| HY3 / reliability-v1 | 90/110; 81.8% [73.6, 87.9] | 92/110; 83.6% [75.6, 89.4] | 4/105; 3.8% [1.5, 9.4] | 95/105; 90.5% [83.4, 94.7] |

## Paired prompt decision

Prompt deltas use the preregistered 10,000-resample authored-case-cluster bootstrap.

| Model | Final utility delta | Final attack-success delta | Contract regressions | Decision |
|---|---:|---:|---:|---|
| DeepSeek V4 Flash | −3.6 pp [−8.2, 0.0] | −2.9 pp [−7.6, 0.0] | 8 | Do not promote: utility, attack-threshold, and zero-regression rules fail |
| Tencent HY3 | +1.8 pp [0.0, +4.5] | −1.0 pp [−2.9, 0.0] | 0 | Do not promote: utility and attack improvements are both below 5 pp |

The deterministic grounding-error proxy improves for DeepSeek and worsens for HY3. It is a narrow warning
heuristic, not a substitute for blinded topic-level grounding review. The 180 URL-scoped semantic review
forms and all grounding forms remain unjudged in this rerun; no semantic or human-grounding claim is made.

## Operational record

OpenRouter reported $3.80048085562 across 1,676 successful calls: 1,200 first calls and 476 correction calls.
The user-authorized ceiling was $5. DeepSeek accounted for $1.04481926162 and HY3 for $2.755661594. Median
first-call latency was 3.08s and 2.75s for the two DeepSeek prompts, and 5.66s and 5.47s for the two HY3
prompts. Full p95 latency, cost, behavior, technique, matched-pair, ablation, and confidence-interval tables
are in the generated report.

The first process was interrupted externally after 556 rows, resumed from its checkpoint, and stopped after
its complete 600-row DeepSeek adapter block so HY3 could run concurrently. HY3 completed in a separate clean
checkpoint. The public exporter accepts this only because both checkpoints have identical source, suite,
corpus, configuration, protocol, prompt, control, seed, and timeout identity; each component contains a whole
adapter block; the union is duplicate-free and exactly 1,200 successful rows. The export records both original
manifest hashes and statuses rather than rewriting the raw checkpoints.

## Public evidence and verification

[`portfolio-v2-evidence/`](portfolio-v2-evidence/) is the reviewer-facing evidence bundle. It contains the
redacted 1,200-row manifest with every generated output, a text-free score ledger, semantic-review forms,
aggregate JSON and Markdown reports, metadata, and SHA-256 checksums. Provider request identifiers are the
only removed field. A credential/path scan found no API keys, authorization headers, request identifiers, or
local absolute paths in the bundle.

The bundle's Score family 1 (checker capability) is the checker state frozen at the 2026-08-19 run and predates the 2026-08-25 repair of the structure-overfilled and selection-category-ambiguity fixtures, so the report it regenerates shows 42/49 precision, 42/56 recall, and 7/12 heuristic false positives; the current checker numbers live in `evaluator/snapshots/offline-checker.json`.

The committed bundle is about 24 MiB, below Git LFS territory. The two local raw directories total 155 MiB,
but add only redundant copies of committed corpora, requests reproducible from committed prompts/configs,
and outputs already present in the public manifest; they are therefore not publication inputs.

Verify all published hashes and regenerate the aggregate report without credentials or provider calls:

```bash
python3 -m evaluator verify-public-run docs/results/portfolio-v2-evidence
```

Regenerate the published comparison from the evidence manifest:

```bash
python3 -m evaluator compare \
  docs/results/portfolio-v2-evidence/manifest.json \
  docs/results/portfolio-v2-evidence/manifest.json \
  --baseline-prompt production-2026-08 \
  --candidate-prompt reliability-v1 \
  --output docs/results/portfolio-v2-comparison.json
```

The exact paid-run controls were temperature `0`, seed `20260819`, disabled reasoning, a 300-second call
timeout, execution seed `20260819`, five trials, and source tag `portfolio-v2-source-20260819`. The evaluator
sent those controls, but this source version did not require OpenRouter to route only through endpoints that
advertise every parameter. Temperature zero and a seed also do not guarantee byte-identical provider output.
The complete command is reconstructed from the recorded component fields and reproduced in
[`evaluator/README.md`](../../evaluator/README.md).

## Historical scope

Portfolio v2 replaces the unpublished dirty-source portfolio-v1 generation run.
[`portfolio-v1.md`](portfolio-v1.md), [`portfolio-v1-model-card.md`](portfolio-v1-model-card.md), and
[`portfolio-v1-pilot.md`](portfolio-v1-pilot.md), together with the curated JSON, remain dated historical
snapshots. Their model metrics came from the unavailable dirty-source run and must not be presented as the
current reproducible result. The offline checker's dated narrative snapshot is likewise preserved separately;
CI now gates the current byte-for-byte snapshot in `evaluator/snapshots/offline-checker.json`.
