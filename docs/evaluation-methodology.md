# Evaluation methodology

How the evaluator's suites are scored, and what those scores may and may not be used to claim. Read this before quoting a number from [`evaluator/`](../evaluator/README.md) or from [Portfolio v2](results/portfolio-v2.md): the sections below define each score family's denominator, the boundary between deterministic and human judgment, and the limitations that rule out a single composite reliability score.

This methodology operationalizes the [NIST AI RMF 1.0 MEASURE function](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/): documented and repeatable test, evaluation, verification, and validation; representative conditions; independent assessment; explicit uncertainty; and tracking risk over time. The complete publication is [NIST AI 100-1](https://doi.org/10.6028/NIST.AI.100-1).

## Scope and threat model

The evaluator measures two separate systems. The 81-case offline suite measures deterministic briefing checks and feed parsing. The 55-case generation suite measures model behavior on 22 utility cases and 33 indirect prompt-injection attacks; five attacks also create clean twins at run time. The reported generation suite ran on the evaluator's direct-Markdown path, so it measures the weaker citation contract; production-parity performance remains unmeasured. These score families have different units and denominators and are never combined into one reliability score.

The attacker controls text in fetched titles, summaries, source names, and source-failure records. Targeted outcomes include fabricated or altered citations, duplicate citations, prose distortion, selection promotion or suppression, section misrouting, health-report manipulation, and formatting damage. A robust but empty output is not useful, so benign structural utility, utility under attack, and targeted attack success are reported separately, following the evaluation posture of [AgentDojo (NeurIPS 2024)](https://papers.neurips.cc/paper_files/paper/2024/file/97091a5177d8dc64b1da8bf3e1f6fb54-Paper-Datasets_and_Benchmarks_Track.pdf) and [MELON (ICML 2025)](https://proceedings.mlr.press/v267/zhu25z.html).

## Suite construction and labels

Cases are authored boundary and failure-mode tests, not a random sample of deployment traffic. The offline suite covers URL identity, feed encodings and malformed input, structure, routing, source degradation, evidence conflicts, over-consolidation, category ambiguity, and claim-heuristic boundaries. The generation suite covers ordinary utility, over-refusal decoys, direct and combined injections, matched clean/attack cases, and production-corpus position/item-count ablations.

Gold labels are assigned from the documented contract before checker execution. Randomized review packets replace fixture names with opaque identifiers and omit current labels and checker predictions. The repository owner adjudicates disagreements against the rubric, preserving the original label, reviewer rationale, final decision, and metric delta. Model review can expose inconsistency, but it is automated development evidence rather than independent human approval.

As of 2026-08-14, 81 offline checker/feed cases had been bootstrapped with blinded LLM review and repository-owner adjudication. Nemotron Ultra reviewed the original 49-case packet; GLM 5.2 reviewed the six additions and the final packet of 24 paired heuristic boundaries plus two UTF-32 security regressions. That last pass produced 23 exact agreements and three owner-adjudicated disagreements, with two final quotation-label changes. On 2026-08-25, occurrence-blind URL mutations were repaired in `structure-overfilled` and `selection-category-ambiguity`. Both corrected fixtures completed renewed exact-agreement model review on 2026-08-26, leaving all 81 current cases model-reviewed. The [review receipt](results/repaired-fixture-model-review-2026-08-26.json) records the two decisions and their artifact hashes. No case has completed independent human review. Full human review is recommended before production use.

## Deterministic and semantic boundary

The deterministic checker proves closed-world contract properties: allowed citation destinations, URL identity, required structure, section eligibility, slot allocation, duplicate placement, Hacker News discussion links, and machine-readable source-health reconciliation. The feed parser separately checks accepted RSS/Atom shapes, supported encodings, and the no-DOCTYPE security invariant.

Claim checks are narrower heuristics. They detect figures or quotations absent from cited title/summary evidence, distinguish figures found in a topically matching corpus item outside the cited excerpts, and flag prose whose character length is more than twice its evidence. Their performance is reported on the declared claim subset, including an overall deliberately-valid-case false-positive rate and per-check false-positive rates for `unsupported_figure`, `figure_supported_elsewhere`, `unsupported_quotation`, and `claim_exceeds_evidence`; the checker also emits a fifth claim-family warning, [`low_claim_evidence_overlap`](low-overlap-diagnostic.md), deliberately excluded from the reported four.

Conflicting evidence, over-consolidation, unsupported paraphrase, and ambiguous categorization require semantic judgment. Human labels for those conditions remain in the all-label checker denominator, so deterministic misses reduce recall rather than being relabeled away. Live-run meaning preservation and grounding are a separate semantic layer. Any model judging is labeled as automated evidence and does not silently replace human decisions.

## Denominators and uncertainty

Every rate reports successes, trials, and a two-sided 95% Wilson interval. Wilson intervals describe outcomes on this fixed authored suite; they do not establish generalization to deployment traffic. Repeated live trials over the same authored case are not independent samples of the deployment population. Compatible live-run comparisons therefore pair by case ID and trial index and cluster bootstrap resampling at the authored-case level.

Utility rates use completed utility trials only. Targeted attack rates use completed primary attack trials only; clean twins and ablation replicates have separate denominators. Grounding error uses adjudicated generated topics, not case trials. Unreviewed topics and unclear propositions remain visible. Latency reports completed calls, median, and p95. Cost reports observed billed totals across successful and failed provider calls; calls whose providers do not report cost remain explicitly counted as unknown.

## Reproducibility and limitations

Runs record immutable suite, root and case-specific corpus, configuration, prompt, protocol, and exact model identifiers with SHA-256 hashes. They also record prompt and adapter order, generation controls, per-adapter timeouts, trials, run kind, execution order/seed, circuit threshold, and cost-ceiling settings. An interrupted `running` checkpoint can resume only when those fields still match and its saved rows are an exact artifact-complete prefix of the original plan. Resume skips every saved completed or failed row and reconstructs observed billed cost plus per-adapter circuit state before any new provider call; complete, stopped, incompatible, or corrupt checkpoints are refused. Pilot rows are marked and excluded from final preregistered results. Provider errors, circuit skips, replacement runs, missing adjudications, and unknown costs are retained rather than converted into successes or zeroes.

The suite is fixed, small, and intentionally enriched for known boundaries. Provider behavior and model aliases can change. CLI and API sampling controls are not equivalent. Human grounding decisions can disagree and require agreement statistics plus adjudication. These limitations preclude a single composite reliability score or claims that small model/prompt differences are decisive.

## Portfolio v2 (2026-08-19 run, current)

> **Generation path: `markdown`.** [Portfolio v2](results/portfolio-v2.md) ran on the evaluator direct-Markdown path (`"generation_path": "markdown"` in the [run manifest](results/portfolio-v2-evidence/manifest.json)), where the model writes the whole briefing and authors its own links. Production instead uses two schema-constrained passes with citation projection, where a model-authored destination cannot survive validation. The same 1,200-row portfolio has since been run through the production-parity path (`--generation-path production-parity`) and is reported in the [parity v1 model card](results/parity-v1.md). The Portfolio v2 numbers characterize model behavior under the weaker citation contract, not a floor for production.

Portfolio v2 is the result to cite. It completed 1,200/1,200 generation rows from clean tag `portfolio-v2-source-20260819`, with no provider errors, skips, or correction errors and $3.8005 in reported generation cost. The DeepSeek and HY3 adapter blocks ran in parallel-compatible component checkpoints after an external process interruption. Public export validates common immutable identity, requires whole completed adapter blocks, rejects duplicate rows, records both component hashes, and combines the rows only for reporting. The raw checkpoints are not rewritten.

The committed public evidence contains every generated output and score primitive needed to recalculate the aggregate report, plus redacted adjudication forms and SHA-256 metadata. The 155 MiB raw artifact trees remain local because their corpora and configuration are committed and their generated prose is already in the public manifest. `python3 -m evaluator verify-public-run docs/results/portfolio-v2-evidence` verifies the bundle and regenerates its aggregate report without credentials.

The candidate fails available [promotion rules](../evaluator/regression-policy.json) for both models, so missing human grounding cannot turn either decision into a pass. Portfolio v2 intentionally publishes no meaning-preservation or grounding rate: its 180 semantic forms and topic-level grounding forms are unjudged. Temperature zero, provider seed, and disabled reasoning were requested and recorded, but do not guarantee byte-identical output or prove that every routed backend enforced every sampling parameter.

## Checker and feed parser: 81 offline cases (current)

The offline suites run without credentials. All 81 cases have completed blinded model review with owner adjudication of historical disagreements; none has completed independent human review, so these are not presented as human ground truth. The current figures come from [`evaluator/snapshots/offline-checker.json`](../evaluator/snapshots/offline-checker.json), which CI validates against the committed snapshot.

| Component | Cases | Precision | Recall | False-positive rate |
|---|---:|---:|---:|---:|
| Checker | 69 | 42/48; 87.5% [75.3, 94.1] | 42/54; 77.8% [65.1, 86.8] | 6/1671; 0.36% [0.16, 0.78] |
| Feed parser | 12 | 8/8; 100% [67.6, 100] | 8/8; 100% [67.6, 100] | 0/28; 0% [0.0, 12.1] |

On a deliberately hard 12-case subset of *valid* claim boundaries, the combined claim heuristics false-positive at **6/12; 50.0% [25.4, 74.6]**. That marks the intended boundary: code can prove a URL is absent from a corpus; a short feed excerpt cannot prove a nuanced summary unfaithful. Those checks stay warnings.

Further limits on how these numbers may be read:

- Wilson intervals describe this fixed authored suite, not deployment traffic. Repeating one case five times does not make five independent samples of the world.
- This is not an editorial-ranking benchmark.
- It borrows AgentDojo's matched-twin design without reproducing its methodology: "benign structural utility" concerns output structure rather than user-task completion, and the position ablation varies an item's array index rather than its token offset in the prompt.
- CI requires every attack against the intentionally vulnerable `compliant` adapter to succeed. If the strategy designed to obey injections doesn't score as compromised, the oracles are broken.

Reviewers can verify a committed evidence bundle and regenerate its aggregate report with no credentials and no provider calls:

```bash
python3 -m evaluator verify-public-run docs/results/portfolio-v2-evidence
python3 -m evaluator verify-public-run docs/results/parity-v1-evidence
```

The Portfolio v2 bundle's checker score family is frozen at the 2026-08-19 run and predates the 2026-08-25 repair of two fixtures, so its regenerated report shows the older 42/49 precision, 42/56 recall, and 7/12 heuristic figures rather than the table above.
