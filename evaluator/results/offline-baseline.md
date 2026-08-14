# Offline evaluator baseline — 2026-08-13

This report was produced after human adjudication of the 10 cases disputed by the blinded Sonnet review, with Opus recommendations available, and after a 2026-08-13 redundancy trim (54 → 49 checker/feed cases; see "Redundancy trim" below), by running:

```bash
python3 -m evaluator checker
```

No external model was called while regenerating these metrics. The remaining 40 labels still require independent human approval before these values are publication-grade. Live model, prompt-version, correction, attack, grounding, latency, and cost rows therefore have **0 trials / not run** in this baseline; `python3 -m evaluator run` produces those rows from actual provider calls. The offline `baseline` provider (empty/echo/compliant) is the one exception — see "Offline generation-harness baselines" below, which reports real numbers from a zero-cost, zero-credential run.

## Deterministic checker (39 cases)

| Metric | Result |
|---|---:|
| Precision | 96.4% (95% Wilson CI 82.3–99.4%; 27/28 predicted positives) |
| Recall | 79.4% (95% Wilson CI 63.2–89.7%; 27/34 human-positive labels) |
| Exact case match | 82.1% (95% Wilson CI 67.3–91.0%; 32/39 cases) |
| Heuristic claim false-positive rate | 100% (95% Wilson CI 20.7–100%; 1/1 deliberately valid claim case) |

The seven false-negative labels are semantic: unsupported prose in an overfilled case, a generic claim-exceeds-evidence label alongside an unsupported quotation, unsupported thin-evidence prose, conflicting evidence, over-consolidation, and category ambiguity plus unsupported prose in the ambiguous-selection case. The sole false positive among the deliberately valid heuristic claim cases is a semantically equivalent `one in two` → `50 percent` figure, which the deterministic figure check flags. The formerly valid thin paraphrase was adjudicated as exceeding its deliberately shortened evidence and is no longer in the false-positive denominator.

## Feed parser (10 cases)

| Metric | Result |
|---|---:|
| Precision | 85.7% (95% Wilson CI 48.7–97.4%; 6/7 predicted positives) |
| Recall | 100% (95% Wilson CI 61.0–100%; 6/6 human-positive labels) |
| Exact case match | 90.0% (95% Wilson CI 59.6–98.2%; 9/10 cases) |

The one false positive is a valid UTF-32 RSS document rejected by the current Expat/ElementTree path. UTF-8, UTF-16 with BOM, Atom, empty, wrong-shape, malformed, undeclared-entity, and encoded-DOCTYPE cases behaved as labeled.

## Redundancy trim (2026-08-13)

Five cases were dropped from the 54-case suite (checker/feed only — the separate 63→43-case generation-suite trim is documented in `evaluator/README.md`): `xml-utf16le-rss`, `xml-utf16be-rss` (redundant passing UTF-16 byte-order variants; `xml-utf16-rss` and the honest `xml-utf32-rss` failure are kept), `health-valid-degraded` (a passing "well-formed input, zero findings" case redundant with six others), `health-prose-only` (shares its human label with the kept `health-malformed-json`), and `health-wrong-status` (the narrowest of four corpus-health-reconciliation cases; `health-partially-reported`, `health-invented-failure`, and `health-duplicate-record` are kept as more structurally distinct). Full rationale is recorded per-case in `evaluator/fixtures/checker-cases.json`'s `removed_cases` block.

**No case was removed on the basis of failing.** All five removed cases passed under their prior labels before removal; the 8 cases that fail under current labels (7 checker, 1 feed-parser) are all still in the suite. `health-wrong-status` was one of the 10 human-adjudicated cases from the blinded-review process; removing it moves the human-adjudicated count from 10 to 9 (see `label_provenance` in `checker-cases.json`), which is a bookkeeping consequence of the removal, not a re-review.

Before → after, exact figures from one `python3 -m evaluator checker` run before and after the trim (`git stash` around the fixture edit):

| Metric | Before (54 cases) | After (49 cases) | Delta |
|---|---:|---:|---:|
| Checker precision | 96.7% (29/30) | 96.4% (27/28) | −0.2pp |
| Checker recall | 80.6% (29/36) | 79.4% (27/34) | −1.1pp |
| Checker false-positive rate | 0.10% (1/1014) | 0.11% (1/902) | +0.01pp |
| Feed-parser precision | 85.7% (6/7) | 85.7% (6/7) | 0 |
| Feed-parser recall | 100% (6/6) | 100% (6/6) | 0 |
| Feed-parser false-positive rate | 3.33% (1/30) | 4.17% (1/24) | +0.83pp |

Every removed case was a *correct* prediction (a true positive or a true negative), so no false-positive or false-negative count changed anywhere — only the true-positive and true-negative denominators shrank. That is why precision and recall move slightly *down* (fewer correct positives in the numerator against the same false-positive/false-negative counts) rather than flatteringly up, and why the false-positive rate moves slightly up (the same one false positive against a smaller true-negative denominator). This is the expected, honest signature of removing passing redundant cases, not score inflation.

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
