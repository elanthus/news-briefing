# AI reliability portfolio completion plan

**Status:** historical planning snapshot — implementation completed 2026-08-15
**Scope:** close the remaining evaluation-evidence gaps; do not rebuild capabilities that already exist  
**Primary audience:** a reviewer assessing the project as an AI reliability portfolio piece

## Goal

This document records the plan used to turn the evaluator from a strong implementation with an offline
baseline into a reproducible, model-reviewed, longitudinal evaluation with real model results.

The planned implementation is complete except for the explicitly pending blinded human grounding review.
The label review described below was ultimately performed by LLMs with repository-owner adjudication, not
by an independent human reviewer. As of 2026-08-26, all 81 current cases have completed model review,
including renewed exact-agreement reviews of two repaired fixtures. Zero cases have completed independent
human review. Full human review is recommended before production use.
Current outcomes are recorded in `docs/results/portfolio-v1.md`; this file preserves the original sequence
and acceptance criteria rather than serving as the current project-status source.

This plan follows NIST AI RMF's emphasis on documented test sets and methods, deployment-representative
conditions, independent assessment, uncertainty, and tracking risks over time. It also keeps benign utility
separate from targeted attack success, following the evaluation posture used by the peer-reviewed AgentDojo
and MELON work.

## Historical gap audit and completion state

| Requested capability | Current state | Remaining gap |
|---|---|---|
| Fixed 30–100-case suite | Completed: 81 checker/feed cases plus 55 authored generation cases (60 executed case units with matched clean pairs); all 81 checker/feed cases have model review | Independent human review remains incomplete |
| Fabricated, altered, bare, Markdown, and duplicate URLs | Covered | None |
| UTF-8/16/32 and malformed/feed-shape cases | Completed: valid BOM-aware UTF-32 is accepted and malformed, contradictory, and DOCTYPE inputs remain rejected | None |
| Degraded and partially degraded sources | Covered in checker and generation suites | None |
| Injection against citations, prose, selection, health, and formatting | Completed across 33 attacks, including positive controls, over-refusal decoys, matched clean twins, and an ablation cohort | None; the 1,200-row final benchmark is published |
| Thin/conflicting evidence, over-consolidation, and category ambiguity | Covered | Semantic misses are honestly reflected in checker recall; do not hide them by relabeling |
| Deliberately valid edge cases | Completed, including separately model-reviewed paired heuristic-claim boundaries | Independent human review remains incomplete |
| Checker precision/recall | Completed with Wilson intervals; all 81 labels have model review | Independent human review remains incomplete |
| First-pass contract, correction, attack success, grounding, latency, and cost | Completed for 1,200/1,200 final rows at a recorded cost of $3.0338 | Blinded human grounding review remains pending for 2,170 topics; machine semantic review is complete |
| Confidence intervals and trial counts | Completed with explicit trial counts and paired authored-case-cluster bootstrap intervals | None |
| Results over time | Completed with curated history, compatibility checks, paired comparison, and a regression policy | Add future compatible runs as they are completed |

## Historical pre-implementation decisions

These decisions should be recorded in the pull request or tracking issue before paid runs begin:

1. Choose exactly two live model families for the portfolio comparison. Prefer one tool-using CLI model and
   one API model so the comparison is not provider-specific.
2. Freeze two prompt versions: the current production prompt and one named candidate. A prompt comparison is
   not meaningful if either file can change under the same version name.
3. Approve the paid-run ceiling after a one-trial pilot reports actual call count, correction frequency,
   latency, and cost. Missing CLI cost must remain `null`; it must not be estimated as zero.
4. Name the reviewer for the six provisional checker labels and at least one second grounding reviewer.
   Record whether each reviewer is a human or a model, and keep model identity hidden while labeling outputs.

## Phase 1 — finish and strengthen the offline gold suite

### 1. Blind-review the six provisional labels

**Files:**

- `evaluator/fixtures/checker-cases.json`
- `evaluator/results/offline-baseline.md`
- `evaluator/README.md`
- new review packet and reviewer-provenance artifacts under `evaluator/results/`

**Work:**

1. Generate a randomized, opaque-ID packet containing only the six provisional cases, the existing rubric,
   and evidence. Do not expose fixture IDs, current labels, or checker predictions.
2. Record reviewer identity and type, plus the reviewer's label set with rationales.
3. Have the repository owner adjudicate disagreements against the documented contract. Model review may
   surface inconsistencies but cannot be represented as independent human approval.
4. Update `label_provenance`, the case labels if adjudication changes them, and the committed offline report.

**Acceptance:**

- `provisional_count` is zero, or the report prominently states why any case remains provisional.
- Counts in fixture metadata, README, and the offline report agree.
- The before/after label and metric delta is preserved in the review artifact.

### 2. Fix the valid UTF-32 feed false positive

**Files:**

- `fetch_news.py`
- `test_fetch_news.py`
- `evaluator/fixtures/checker-cases.json` only if the label contract needs clarification

**Work:**

1. Add explicit, bounded UTF-32 decoding before the Expat/ElementTree passes. Accept BOM-marked UTF-32 and a
   consistent XML declaration; reject contradictory declarations and invalid byte sequences.
2. Preserve the current DOCTYPE security invariant after decoding. Add UTF-32 DOCTYPE and entity-expansion
   regression cases so encoding support cannot become a guard bypass.
3. Retain malformed UTF-32, wrong-shape, and empty-feed negatives.

**Acceptance:**

- `xml-utf32-rss` becomes a true negative rather than a known false positive.
- Encoded DOCTYPE payloads remain rejected in UTF-8, UTF-16, and UTF-32.
- Feed-parser precision reaches 100% on the fixed suite without lowering recall.

### 3. Replace the one-case heuristic false-positive denominator

**Files:**

- `evaluator/fixtures/checker-cases.json`
- `evaluator/cases.py`
- `evaluator/tests/test_evaluator.py`
- `evaluator/results/offline-baseline.md`

**Work:**

1. Add at least 12 gold-labeled, deliberately valid heuristic-claim cases, balanced across:
   fraction/percentage equivalence, rounded quantities, ranges, currency/unit normalization, dates, counts,
   quote punctuation/whitespace, qualified uncertainty, and multiple figures in one topic.
2. Pair each valid example with a minimally changed invalid neighbor where practical. This prevents a change
   that merely suppresses warnings from appearing to improve the false-positive rate.
3. Report false-positive rate overall and separately for `unsupported_figure`,
   `unsupported_quotation`, and `claim_exceeds_evidence`. Keep zero-denominator rows explicit.
4. Send the additions through the same blinded review workflow before calling them gold, and identify
   model review as automated development evidence rather than human validation.

**Acceptance:**

- The valid-case false-positive denominator is at least 12, with trial count and 95% Wilson interval.
- Every heuristic type has both a valid and invalid neighbor where its contract permits one.
- Precision, recall, exact case match, and per-check false-positive rate are regenerated from the fixed suite.

### 4. Document the deterministic/semantic boundary

Do not treat the current 73.2% checker recall as a bug to erase with broader heuristics. Its 11 misses are
mostly semantic judgments—conflicting evidence, over-consolidation, and unsupported paraphrase—that the
deterministic contract checker does not claim to prove.

Add a short methodology section that reports:

- deterministic checker capability against all gold labels;
- heuristic claim-check performance on its declared subset;
- human or model-assisted semantic review as a separate layer; and
- known limits, including the fixed suite's non-random construction.

This keeps a high-precision guardrail from being misrepresented as a complete factuality detector.

## Phase 2 — freeze a preregistered live benchmark

### 5. Freeze prompts, suite, models, and run protocol

**Files:**

- `evaluator/prompts/production-2026-08.md`
- `evaluator/prompts/<candidate-name>.md`
- new `evaluator/protocols/portfolio-v1.json`
- `evaluator/README.md`

The protocol file should record:

- suite, corpus, config, and prompt SHA-256 hashes;
- exact provider and model identifiers;
- temperature, seed, reasoning controls, timeout, and correction policy;
- planned versus pilot trials;
- which cases enter each denominator;
- grounding and semantic adjudication rules;
- practical regression thresholds; and
- the approved cost ceiling and stop conditions.

Run one pilot trial per model/prompt group to validate compatibility and estimate cost. Mark pilot rows as
pilot and exclude them from the preregistered benchmark. Do not tune the candidate prompt against final test
outputs; use a separate development subset or add newly discovered failures only to the next suite version.

### 6. Execute the live matrix

Use this minimum final design unless the pilot forces a documented change:

| Axis | Final design |
|---|---|
| Models | 2 exact model versions from different provider paths |
| Prompts | frozen production + frozen candidate |
| Repetitions | 5 per model/prompt/case |
| Authored cases | 55, plus the 5 automatically derived clean twins |
| Rows per model/prompt group | 300 |
| Total result rows | 1,200 before any replacement runs |

Five repetitions yield 110 completed utility observations and 105 primary-attack observations per
model/prompt group. Behavior-level attack denominators remain modest, so every table must show counts and
intervals rather than ranking small differences as decisive.

Execute groups in an interleaved or randomized order where the adapters permit it, so provider drift or a
time-of-day outage does not affect only one prompt. Preserve all errors and circuit skips; never rerun only a
bad outcome. A replacement run is allowed only for a documented infrastructure failure and must retain the
original failed row.

### 7. Complete grounding and meaning adjudication

**Files:** generated `grounding-adjudication.json` and `semantic-adjudication.json` artifacts, plus a curated
aggregate result

**Work:**

1. Blind model and prompt identity in review packets.
2. Human-label every final utility topic for grounding error. The denominator is generated topics, not case
   trials; topics with no decision stay explicitly unreviewed.
3. Double-label a stratified 20% sample, including thin evidence, conflicting evidence, consolidation, and
   category ambiguity. Report raw agreement and Cohen's kappa, then adjudicate disagreements.
4. Review every URL-scoped `must_convey` proposition. LLM semantic judging may prioritize review, but final
   portfolio claims should distinguish human labels from model judgments.
5. Regenerate reports only after adjudication files pass schema and completeness checks.

**Acceptance:**

- 100% of final utility topics are adjudicated or the missing count is shown beside the rate.
- Every `must_convey` proposition is `conveyed`, `not_conveyed`, or `unclear`; none silently disappears.
- Reviewer agreement and adjudication counts are reported.

## Phase 3 — improve comparison statistics and report the results

### 8. Add a compatible-run comparator

**Files:**

- `evaluator/metrics.py`
- `evaluator/runner.py`
- `evaluator/__main__.py`
- `evaluator/tests/test_evaluator.py`

Add `python3 -m evaluator compare BASELINE_REPORT CANDIDATE_REPORT` with these rules:

1. Refuse comparison when suite, corpus, config, prompt grouping, adjudication status, or case IDs are
   incompatible, unless the caller explicitly requests a descriptive non-gated comparison.
2. Pair outcomes by case ID and trial index. Report absolute percentage-point deltas for contract success,
   end-to-end utility, attack success, correction success, and grounding error.
3. Use a paired, case-clustered bootstrap interval for model/prompt deltas so five repetitions of the same
   authored case are not treated as five independent samples of the deployment population. Keep existing
   Wilson intervals as descriptive within-suite intervals and document that they do not establish
   generalization beyond the authored cases.
4. Report latency median, p95, and completed-call count. Add a bootstrap interval for the median if enough
   calls completed. Report observed billed cost as total and per completed call; leave unknown cost `null`.
5. Never infer superiority from overlapping or non-overlapping marginal intervals alone. Use the paired delta
   and a predeclared practical threshold.

**Acceptance:**

- Identical reports compare to a zero delta with deterministic tests.
- Missing rows, provider errors, and unmatched pairs are visible and excluded by a documented rule.
- The comparator cannot combine pilot and final rows or silently compare different suite hashes.

### 9. Publish one portfolio-grade result bundle

Create a concise, committed bundle containing aggregate evidence but no secrets or raw provider payloads:

- `docs/evaluation-methodology.md` — threat model, suite construction, labels, denominators, uncertainty,
  review process, and limitations;
- `docs/results/portfolio-v1.md` — the readable model × prompt comparison;
- `docs/results/portfolio-v1.json` — the same aggregate metrics for reproducibility;
- `docs/results/portfolio-v1-model-card.md` — exact versions, dates, controls, cost coverage, failures, and
  interpretation limits.

The lead table should show, per model and prompt:

- first-pass contract success;
- end-to-end first and final success;
- correction attempts and successes;
- targeted injection attack success and utility under attack;
- grounding errors per adjudicated generated topic;
- heuristic claim-check false-positive rate from the offline suite, clearly separated from model results;
- latency and observed cost; and
- successes/trials plus 95% intervals for every rate.

Also include attack behavior and technique breakdowns, matched clean/attack results, incomplete-run counts,
and an explicit limitations section. Avoid a single composite "reliability score"; the tradeoffs are the
portfolio story.

## Phase 4 — track reliability over time

### 10. Add a curated history and regression policy

**Files:**

- new `evaluator/history/portfolio-v1.jsonl` or versioned aggregate JSON files
- new `evaluator/regression-policy.json`
- optional `.github/workflows/evaluator-live.yml`

**Work:**

1. Append only validated aggregate summaries, keyed by suite hash, prompt hash, exact model ID, date, and run
   protocol. Raw generated artifacts remain local and ignored.
2. Gate deterministic checker/feed regressions in ordinary CI; this already runs offline and should remain
   credential-free.
3. Run the live benchmark manually for prompt/model changes and optionally on a monthly
   `workflow_dispatch`/schedule with a hard cost ceiling. Provider or billing failures produce an incomplete
   run, never a pass.
4. Predeclare practical thresholds. Suggested starting points are zero new deterministic contract failures,
   no more than a 5-point loss in end-to-end utility, no more than a 5-point increase in targeted attack
   success, and no increase in human grounding error. Treat these as review triggers, not automatic claims of
   statistical significance.
5. Generate a small trend table showing suite version, model/prompt, utility, attack success, grounding
   error, latency, cost, and completeness. Never draw a trend line across incompatible suite versions without
   a shared-case view.

**Acceptance:**

- A reviewer can reproduce a historical aggregate from the recorded protocol and hashes.
- A prompt change cannot overwrite its prior baseline.
- Incomplete and incompatible runs cannot satisfy a regression gate.

## Verification sequence

Run after each implementation phase:

```bash
python3 -m unittest -v
python3 -m unittest discover -s evaluator/tests -v
uvx ruff@0.14.2 check .
uvx mypy@1.14.1
uvx mypy@1.14.1 --config-file evaluator/pyproject.toml evaluator
python3 -m evaluator checker
python3 -m evaluator run --provider baseline=empty --provider baseline=echo --provider baseline=compliant
```

Before publishing the live bundle, additionally verify that:

- planned, recorded, completed, failed, skipped, corrected, and adjudicated counts reconcile;
- every aggregate row can be traced to immutable suite, corpus, config, prompt, and model identifiers;
- no API keys, environment files, raw prompts from provider logs, or unreviewed generated artifacts are
  committed; and
- a second person can follow the methodology and reproduce the offline tables from a clean checkout.

## Suggested delivery order

1. **Gold labels:** finish blinded review of the six provisional cases and record reviewer type.
2. **Offline validity:** fix UTF-32 and expand valid heuristic-claim pairs.
3. **Protocol:** freeze prompts, models, hashes, review rules, budget, and stopping criteria.
4. **Pilot:** one excluded trial per group; repair harness compatibility only.
5. **Final live run:** 2 models × 2 prompts × 5 repetitions.
6. **Human review:** complete grounding and proposition adjudication.
7. **Comparison/report:** add paired comparisons and publish the curated result bundle.
8. **Longitudinal layer:** add history, thresholds, and an optional budget-capped scheduled run.

The portfolio is credible after step 7. Step 8 turns it from a one-time benchmark into an ongoing reliability
practice.

## Evidence basis

- [NIST AI RMF Core, Measure](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/) — documented and
  repeatable TEVV, measures of uncertainty, deployment-representative conditions, independent assessment,
  and risk tracking over time.
- [NIST AI RMF Playbook, Measure](https://airc.nist.gov/airmf-resources/playbook/measure/) — construct
  validity, documented measurement assumptions, monitoring, and evaluation in context.
- [AgentDojo (NeurIPS 2024)](https://papers.neurips.cc/paper_files/paper/2024/file/97091a5177d8dc64b1da8bf3e1f6fb54-Paper-Datasets_and_Benchmarks_Track.pdf)
  — separates benign utility, utility under attack, and targeted attack success, and reports confidence
  intervals and attack-position results.
- [MELON (ICML 2025)](https://proceedings.mlr.press/v267/zhu25z.html) — evaluates indirect prompt injection
  prevention together with utility preservation and ablations.
