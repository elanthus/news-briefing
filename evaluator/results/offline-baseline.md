# Offline evaluator baseline — 2026-08-11

This report was produced from the committed provisional gold labels by running:

```bash
python3 -m evaluator checker
```

No external model was called. The labels still require independent human approval before these values are publication-grade. Live model, prompt-version, correction, attack, grounding, latency, and cost rows therefore have **0 trials / not run** in this baseline; `python3 -m evaluator run` produces those rows from actual provider calls.

## Deterministic checker (42 cases)

| Metric | Result |
|---|---:|
| Precision | 93.3% (95% Wilson CI 78.7–98.2%; 28/30 predicted positives) |
| Recall | 87.5% (95% Wilson CI 71.9–95.0%; 28/32 human-positive labels) |
| Exact case match | 85.7% (95% Wilson CI 72.2–93.3%; 36/42 cases) |
| Heuristic claim false-positive rate | 100% (95% Wilson CI 34.2–100%; 2/2 deliberately valid claim cases) |

The four checker false negatives are deliberate semantic probes: unsupported thin-evidence prose, conflicting evidence, over-consolidation, and category ambiguity. The two false positives are also deliberate: a semantically equivalent `one in two` → `50 percent` figure and a faithful paraphrase that is more than twice the character length of thin evidence.

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
