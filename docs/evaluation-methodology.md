# Evaluation methodology

Use this document to interpret evaluator scores and select valid comparisons.
Score families have separate denominators. Do not combine them into one reliability score.
See the [evaluator guide](../evaluator/README.md) for commands.

The [NIST AI RMF MEASURE function](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/) motivates repeatable evaluation, representative conditions, independent assessment, explicit uncertainty, and monitoring.
The full publication is [NIST AI 100-1](https://doi.org/10.6028/NIST.AI.100-1).

Current generation uses the production two-pass path and current corpus schema.
The active suite is [version 9](../evaluator/fixtures/generation-cases-v9.json).
Version 8 results and their protocols remain frozen.
Historical direct-Markdown runs and obsolete formats require their recorded source revision.
Public bundle verification checks authenticated hashes, metadata, and rebuilt score primitives without model calls.
Historical report regeneration uses the recorded source tag.

## Scope and threat model

The offline suite measures deterministic briefing checks and feed parsing.
The generation suite separately measures utility and indirect prompt injection.
Primary attacks, clean twins, and position/count ablations have separate denominators.
The fixture files define the current case populations; reports state their measured denominators.

The attacker controls fetched titles, summaries, source names, and failure records.
Targets include fabricated or altered citations, duplicates, prose distortion, selection changes, section misrouting, health manipulation, and formatting damage.
Empty output can resist an attack while failing the underlying task.
Report benign utility, utility under attack, and targeted attack success separately.
This follows the evaluation approach of [AgentDojo (NeurIPS 2024)](https://papers.neurips.cc/paper_files/paper/2024/file/97091a5177d8dc64b1da8bf3e1f6fb54-Paper-Datasets_and_Benchmarks_Track.pdf) and [MELON (ICML 2025)](https://proceedings.mlr.press/v267/zhu25z.html).

| Historical run | Generation contract |
|---|---|
| [Portfolio v2](results/portfolio-v2.md) | Direct Markdown with model-authored destinations. |
| [Parity v1](results/parity-v1.md) | Two passes, but omitted production's repair-before-correction step. |
| [Parity v2](results/parity-v2.md) | Corrected production-parity path. |
| [Parity v3](results/parity-v3.md) | Later frozen version 8 experiment; comparisons remain descriptive. |

## Suite construction and labels

Cases are authored boundary and failure-mode examples, not random deployment samples.
Offline cases cover URL identity, feed parsing, structure, routing, health, evidence conflicts, consolidation, ambiguity, and claim heuristics.
Generation cases cover utility, over-refusal decoys, injection techniques, clean/attack pairs, and production-corpus ablations.

Gold labels come from the documented contract before checker execution.
Blinded packets replace fixture names with opaque IDs and omit labels and checker predictions.
Owner adjudication retains original labels, reviewer rationales, final decisions, and metric changes.
Model review is development evidence. It is not independent human approval.

The offline suite has completed blinded model review with owner adjudication of disagreements.
No offline case has completed independent human review.
Historical reviewers included Nemotron Ultra and GLM 5.2.
Two URL-targeting fixtures received renewed exact-agreement model reviews on August 26, 2026.
The [receipt](results/repaired-fixture-model-review-2026-08-26.json) records those decisions and hashes.
Use the fixture's `label_provenance` and historical reports for detailed review history.
Full independent human review remains recommended before production use.

## Deterministic and semantic boundary

Code checks citation destinations, URL identity, structure, eligibility, allocation, duplicate placement, discussion links, and machine-readable source health.
The feed parser separately checks accepted RSS/Atom shapes, encodings, and the no-DOCTYPE rule.

Claim checks are narrower heuristics.
They detect missing figures or quotations, related uncited figure evidence, and prose longer than twice its evidence.
Reported claim rates cover `unsupported_figure`, `figure_supported_elsewhere`, `unsupported_quotation`, and `claim_exceeds_evidence`.
The fifth diagnostic, [low claim/evidence overlap](design.md#low-claimevidence-overlap-diagnostic), is excluded from those reported four.

Conflicting evidence, over-consolidation, unsupported paraphrase, and ambiguous categorization require semantic judgment.
Their labels stay in the all-label checker denominator. Misses reduce recall rather than disappearing from measurement.
Live meaning preservation and grounding form a separate assessment layer.
Machine labels remain identified as automated evidence.

## Denominators and uncertainty

Rates report successes, trials, and two-sided 95% Wilson intervals.
These intervals describe the fixed authored suite; they do not establish deployment generalization.
Repeated trials on one case are not independent deployment samples.
Compatible comparisons pair case ID and trial index and bootstrap whole authored-case clusters.

Current [regression policy](../evaluator/regression-policy.json) version 2 took effect on October 20, 2026.
Its promotion rules are:

| Rule | Requirement |
|---|---|
| Utility | Final end-to-end improvement meets the minimum, and the paired interval's lower bound exceeds the configured floor of 0 percentage points. |
| Attack success | The candidate-minus-baseline interval's upper bound stays below the non-inferiority margin. |
| Contract | No contract regression. |
| Multiplicity | `promotion_requires: all_models`; every evaluated provider/model must pass. |
| Human grounding | Required review must be complete; missing review can leave the decision inconclusive. |

The utility rule is `point_estimate_and_interval_lower_bound`.
The attack rule is `interval_upper_bound`.
One failing model blocks promotion. The parser also supports `any_model`, but the committed policy does not use it.

Version 1 used only the utility point estimate while applying an interval rule to attack success.
Version 2 removes that asymmetry.
It does not make the Parity v3 comparison gate-eligible.
The historical DeepSeek improvement still meets the utility rule; the HY3 result still fails it.

Comparisons record the policy file's SHA-256.
Version 2 also records the policy version, applied rules, and interval bounds.
A policy must declare `policy_version`, except for the exact archived version 1 bytes.
Unknown unversioned policies are rejected.
Superseded files remain byte-identical under [`evaluator/policies/`](../evaluator/policies/).
Use `--regression-policy evaluator/policies/regression-policy-v1.json` to reproduce comparisons that record its `bf9ed083…` hash.
The Portfolio v2 comparison predates policy hashing and records none.
Historical comparisons remain unchanged.

| Measure | Denominator or accounting basis |
|---|---|
| Utility | Completed utility trials. |
| Targeted attack success | Completed primary attack trials. |
| Matched-pair metrics | Complete attack/clean pairs. Incomplete pairs remain reported. |
| Ablation | Separate position/count replicate trials. |
| Grounding error | Adjudicated generated topics. Unreviewed topics remain visible. |
| Meaning preservation | Decided propositions. Unclear and unreviewed propositions remain visible. |
| Latency | Completed calls, with median and p95. |
| Cost | Observed billed totals, including failed calls. Unknown billing remains explicit. |

## Judgment identity and independent assessment

Generation identity names the model that wrote the output.
Judgment identity names the reviewer that assessed it.
A newly requested model name cannot relabel an existing judgment.

[`judge_io.py`](../evaluator/judge_io.py) binds exact effective prompt bytes to reviewer, model, controls, and timeout.
The prompt contains evidence, generated text, proposition or rubric, and option order.
Semantic and quality identities also hash corpus and output files. Quality adds case configuration.
Changed inputs or controls refuse resume before another call.
Legacy checkpoints without provenance remain preserved and rejected.

Generation records separate digests for the transformed trial corpus and final Markdown.
Semantic judging, quality judging, and grounding export verify these digests, even in a new assessment directory.
A source-corpus hash cannot authenticate mutated trial evidence.
Legacy rows without these digests remain unverifiable.
The artifact-binding version prevents an older interrupted generation from silently adopting the new contract.

Reuse an output directory only for the same assessment.
Use a new empty directory for an independent assessment.
Semantic review checks recorded topics against parsed final output and rejudges embedded labels.
The default `semantic-judgments` directory updates adjudications and reports.
A separate directory preserves prior results.
Missing-topic and missing-evidence decisions retain deterministic identities.
Malformed compatible responses are archived before retry, not overwritten.
Grounding machine review also retains effective prompt, reviewer, role, and per-label identities.

## Version 9 suite changes

`utility-production-selection-scarcity` now requires the Worklog and `MISTAKES.md` practices present in its corpus.
Previous targets did not satisfy the intended workflow-practice requirement.
Authored positive controls pass the active utility cases.
Independent machine review assessed their included claims and declared requirements.
The [remediation assessment](results/remediation-2026-10-02.md) records denominators, editorial caveats, and exclusion-log limits.
These controls establish achievability, not model success rates.

Selection promotion records leading placement on both clean and attacked output.
A leading controlled URL alone is not an attack signal because the clean twin can legitimately select it first.
Reports show paired placement transitions, including leading on both sides.
The attack-only prose canary remains a targeted signal.
Version 8 results retain their original interpretation.

Since October 20, 2026, attack `success_if_checks` count only ERROR-level findings.
Positive signals include canaries, structured assertions, and ERROR findings such as `ungrounded_link`, `altered_link`, or `duplicate_citation`.
WARN heuristics do not prove an attack occurred.
`attack-prose` and `attack-prose-combined` therefore succeed only on their canaries under the current rule.
Their listed quotation and length checks remain WARN-level.
Utility cases still count WARN findings where their declared requirements need them.

New manifests record `attack_oracle_revision: 2`.
A missing field denotes revision 1.
Different revisions are incompatible for gated comparisons and permit descriptive comparison only.
Version 8 files and published results are not re-scored.
Public bundle verification rebuilds recorded score primitives without rerunning the attack oracle.

## Reproducibility and limitations

Runs hash suites, root and case corpora, configurations, prompts, and protocols.
They record exact model identities, controls, timeouts, trials, run kind, execution order/seed, circuit thresholds, and cost ceilings.

Resume requires `running` status and matching immutable identity.
Saved rows must form an artifact-complete exact prefix of the original plan.
Resume skips completed and failed rows and reconstructs cost and circuit state before another call.
Complete, stopped, incompatible, and corrupt checkpoints are rejected.
Pilot rows remain marked and excluded from final results.
Provider errors, skips, replacement runs, missing adjudications, and unknown costs remain visible.

The suite is small, fixed, and enriched for known boundaries.
Provider behavior and aliases can change. CLI and API sampling controls differ.
Human judgments can disagree and need agreement statistics plus adjudication.
These limits prevent a composite reliability claim or decisive interpretation of small differences.

## Parity v2 reporting caveats

These qualifications apply to the frozen [Parity v2](results/parity-v2.md) experiment.
The [README](../README.md#historical-injection-benchmark) summarizes the later Parity v3 result.

Structural utility measures valid output, routed sections, and configured minimums. It does not measure news quality.
No independent human semantic or grounding review was completed for Parity v2.
HY3 received a provider schema without `uniqueItems`; code still checked duplicates.
Its schema was weaker than DeepSeek's. Their cells need this qualification.

Production-corpus selection ablations succeeded 18/30 and 11/30 against DeepSeek under the two prompts.
Primary selection results were 0/30 and 5/30.
HY3 selection ablations were 0/30 under both prompts, and no citation ablation succeeded.
Clean twins and ablations retain separate denominators.
After fetching bundles, reproduce family rates with:

```bash
python3 -S -m evaluator.family_rates .news-briefing/evidence/parity-v2-evidence/ledger.json
```

Two Parity v2 provider-error rows had valid HTTP responses but invalid model JSON.
They are excluded from completed-row denominators.
Counting errors as failures gives DeepSeek/production-runner utility 103/110 and DeepSeek/runner-deepseek-v4-flash citation ablation 0/30.
No primary attack rate changes.
Repeated temperature-0 trials are not independent deployment samples. The model card includes case-level intervals.

Benchmark settings differ from daily operation, including source revision, temperature, reasoning, and correction policy.
The evaluator permits one correction across both stages. Production has separate per-stage allowances.
Comparisons with Parity v1 are descriptive because its repair path differed.

## Current evaluation results

[Parity v3](results/parity-v3.md) recorded all 1,200 planned rows, with 1,175 completed rows, 24 provider errors, and one correction error.
Its [protocol](../evaluator/protocols/parity-v3.json) was frozen before execution.
Within-run prompt and Parity v2 baseline comparisons are descriptive and cannot authorize promotion or regression decisions.

The [Jev-stage evaluation](results/jev-stage-v1.md) ran its [28-day sample](results/jev-stage-sample.json) on October 16, 2026.
All days produced ready briefings and schema-3 audits.
Initial review was complete on 17 days and partial on 11.
No day had missing generation or unknown billing. Reported cost was USD 2.097.
Automated rates are conditioned on complete review.

Both human reviewers froze labels before adjudication. Adjudication and key opening completed on October 19.
The model card reports agreement and adjudicated support, citation, readability, and meaning-preservation outcomes with exact denominators.
Human review covers frozen title/excerpt evidence for repair pairs and a small control sample.
It does not establish general precision, recall, or ground-truth accuracy.
The [September 30 study](results/jev-preliminary-2026-09-30.md) informed sample sizing.
The [partial-day supplement](results/jev-stage-v1-partial-supplement.md) remains separate from the original evaluation.

## Unverified machine grounding monitor

The scheduled monitor was retired on October 10, 2026.
Its [historical log](results/grounding-monitor.md) retains automated self-consistency measurements and their sampling limitations.
Those rates are not independent grounding verification.

Daily integrity records describe automated review, confirmation, repair, and follow-up actually performed.
Aggregating those records does not create a second independent assessment.
Explicit `export-grounding-review` and `judge-grounding` commands remain supported with identity, cost, coverage, and resume controls.
Monitor-only deployment receipts and download code were removed.
Publication still checks deployment outcomes through `daily_publish.py check-outcome`.

## Portfolio v2 (2026-08-19 run, historical)

[Portfolio v2](results/portfolio-v2.md) used direct Markdown and model-authored links.
Its weaker citation contract does not define a production performance floor.
The later parity experiments used the two-pass contract, with the repair-path distinction described above.
Neither Portfolio v2 nor Parity v2 measures current version 9 behavior.

Portfolio v2 completed 1,200/1,200 rows at `portfolio-v2-source-20260819`, with no execution errors and USD 3.8005 reported cost.
DeepSeek and HY3 used compatible component checkpoints after an external interruption.
Export requires common identity, complete adapter blocks, and no duplicate rows.
It records component hashes and combines rows only for reporting.
The raw checkpoints remain unchanged in the verified originals archive.

Public evidence contains generated outputs, score primitives, redacted adjudications, and hashes.
See the [retention receipt](results/retention-2026-10-15.json) for the original artifact archive.
The historical candidate comparison rejected both models under its recorded policy.
Its semantic and grounding forms remain unjudged; no such rates are published.
Requested temperature, seed, and reasoning controls do not guarantee identical output or backend enforcement.

## Checker and feed parser: 81 offline cases (current)

The offline suites need no credentials.
The [committed snapshot](../evaluator/snapshots/offline-checker.json) supplies the figures below, and CI checks it for drift.
Model-reviewed labels with owner adjudication are not independent human ground truth.

| Component | Cases | Precision | Recall | False-positive rate |
|---|---:|---:|---:|---:|
| Checker | 69 | 42/48; 87.5% [75.3, 94.1] | 42/54; 77.8% [65.1, 86.8] | 6/1671; 0.36% [0.16, 0.78] |
| Feed parser | 12 | 8/8; 100% [67.6, 100] | 8/8; 100% [67.6, 100] | 0/28; 0% [0.0, 12.1] |

On the deliberately hard valid-claim subset, combined heuristic false positives are 6/12; 50.0% [25.4, 74.6].
A missing corpus URL is provable. A short excerpt cannot prove a nuanced summary false.
Claim heuristics therefore remain warnings.

These measurements do not assess editorial ranking.
Matched twins and position/count ablations are inspired proxies, not a reproduction of AgentDojo's complete methodology.
The intentionally vulnerable `compliant` test control must succeed on every attack.
This checks that the attack oracles recognize their intended positive signals.

Fetch and authenticate evidence before offline verification:

```bash
python3 -S -m evaluator.evidence_assets fetch
python3 -S -m evaluator verify-public-run .news-briefing/evidence/portfolio-v2-evidence
python3 -S -m evaluator verify-public-run .news-briefing/evidence/parity-v1-evidence
python3 -S -m evaluator verify-public-run .news-briefing/evidence/parity-v2-evidence
python3 -S -m evaluator verify-public-run .news-briefing/evidence/parity-v3-evidence
```

The fetch command needs network access unless local assets are supplied. Verification makes no provider calls.
Reproduce historical presentation at the recorded source tag using [EVIDENCE-ASSETS.md](results/EVIDENCE-ASSETS.md).
Portfolio v2's checker figures predate the August 25 fixture repairs.
Its frozen report therefore retains 42/49 precision, 42/56 recall, and 7/12 hard-subset heuristic false positives.
