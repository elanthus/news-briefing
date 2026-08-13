# Offline evaluator baseline — 2026-08-12

This report was produced after human adjudication of the 10 cases disputed by the blinded Sonnet review, with Opus recommendations available, by running:

```bash
python3 -m evaluator checker
```

No external model was called while regenerating these metrics. The remaining 44 labels still require independent human approval before these values are publication-grade. Live model, prompt-version, correction, attack, grounding, latency, and cost rows therefore have **0 trials / not run** in this baseline; `python3 -m evaluator run` produces those rows from actual provider calls.

## Deterministic checker (42 cases)

| Metric | Result |
|---|---:|
| Precision | 96.7% (95% Wilson CI 83.3–99.4%; 29/30 predicted positives) |
| Recall | 80.6% (95% Wilson CI 65.0–90.2%; 29/36 human-positive labels) |
| Exact case match | 83.3% (95% Wilson CI 69.4–91.7%; 35/42 cases) |
| Heuristic claim false-positive rate | 100% (95% Wilson CI 20.7–100%; 1/1 deliberately valid claim case) |

The seven false-negative labels are semantic: unsupported prose in an overfilled case, a generic claim-exceeds-evidence label alongside an unsupported quotation, unsupported thin-evidence prose, conflicting evidence, over-consolidation, and category ambiguity plus unsupported prose in the ambiguous-selection case. The sole false positive among the deliberately valid heuristic claim cases is a semantically equivalent `one in two` → `50 percent` figure, which the deterministic figure check flags. The formerly valid thin paraphrase was adjudicated as exceeding its deliberately shortened evidence and is no longer in the false-positive denominator.

## Feed parser (12 cases)

| Metric | Result |
|---|---:|
| Precision | 85.7% (95% Wilson CI 48.7–97.4%; 6/7 predicted positives) |
| Recall | 100% (95% Wilson CI 61.0–100%; 6/6 human-positive labels) |
| Exact case match | 91.7% (95% Wilson CI 64.6–98.5%; 11/12 cases) |

The one false positive is a valid UTF-32 RSS document rejected by the current Expat/ElementTree path. UTF-8, UTF-16 with BOM, UTF-16 little-endian, UTF-16 big-endian, Atom, empty, wrong-shape, malformed, undeclared-entity, and encoded-DOCTYPE cases behaved as labeled.

## Live-model metrics

| Metric | Baseline status |
|---|---:|
| First-pass contract success by model and prompt | not run (0 trials) |
| Correction success | not run (0 attempts) |
| Prompt-injection attack success | not run (0 attack trials) |
| Grounding errors per generated topic | not run (0 topics) |
| Latency | not run (0 calls) |
| Cost | not run (0 calls; never imputed as zero) |

This distinction is intentional: deterministic offline results are committed, while provider results depend on credentials, model availability, billing, and the exact prompt/model combination and belong in timestamped run directories.
