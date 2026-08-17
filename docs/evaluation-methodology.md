# Evaluation methodology

## Scope and threat model

The evaluator measures two separate systems. The 81-case offline suite measures deterministic briefing checks and feed parsing. The 55-case generation suite measures model behavior on 22 utility cases and 33 indirect prompt-injection attacks; five attacks also create clean twins at run time. These score families have different units and denominators and are never combined into one reliability score.

The attacker controls text in fetched titles, summaries, source names, and source-failure records. Targeted outcomes include fabricated or altered citations, duplicate citations, prose distortion, selection promotion or suppression, section misrouting, health-report manipulation, and formatting damage. A robust but empty output is not useful, so benign structural utility, utility under attack, and targeted attack success are reported separately, following the evaluation posture of [AgentDojo (NeurIPS 2024)](https://papers.neurips.cc/paper_files/paper/2024/file/97091a5177d8dc64b1da8bf3e1f6fb54-Paper-Datasets_and_Benchmarks_Track.pdf) and [MELON (ICML 2025)](https://proceedings.mlr.press/v267/zhu25z.html).

## Suite construction and labels

Cases are authored boundary and failure-mode tests, not a random sample of deployment traffic. The offline suite covers URL identity, feed encodings and malformed input, structure, routing, source degradation, evidence conflicts, over-consolidation, category ambiguity, and claim-heuristic boundaries. The generation suite covers ordinary utility, over-refusal decoys, direct and combined injections, matched clean/attack cases, and production-corpus position/item-count ablations.

Human labels are assigned from the documented contract before checker execution. Randomized review packets replace fixture names with opaque identifiers and omit current labels and checker predictions. Independent reviewers attest that they did not prepare or previously inspect the labels. The repository owner adjudicates disagreements against the rubric, preserving the original label, reviewer rationale, final decision, and metric delta. Model review may expose inconsistency but is not represented as independent human approval.

As of 2026-08-14, all 81 offline checker/feed cases have completed blinded independent human review and repository-owner adjudication. The final 26-case packet covered 24 paired heuristic boundaries and two UTF-32 security regressions; it produced 23 exact agreements and three adjudicated disagreements, with two final quotation-label changes. Machine review was retained only as supporting evidence and was not counted as human approval.

## Deterministic and semantic boundary

The deterministic checker proves closed-world contract properties: allowed citation destinations, URL identity, required structure, section eligibility, slot allocation, duplicate placement, Hacker News discussion links, and machine-readable source-health reconciliation. The feed parser separately checks accepted RSS/Atom shapes, supported encodings, and the no-DOCTYPE security invariant.

Claim checks are narrower heuristics. They detect figures or quotations absent from cited title/summary evidence and prose whose character length is more than twice its evidence. Their performance is reported on the declared claim subset, including an overall deliberately-valid-case false-positive rate and per-check false-positive rates for `unsupported_figure`, `unsupported_quotation`, and `claim_exceeds_evidence`.

Conflicting evidence, over-consolidation, unsupported paraphrase, and ambiguous categorization require semantic judgment. Human labels for those conditions remain in the all-label checker denominator, so deterministic misses reduce recall rather than being relabeled away. Live-run meaning preservation and grounding are a separate human-adjudicated layer; model judging may prioritize review but does not silently replace human decisions.

## Denominators and uncertainty

Every rate reports successes, trials, and a two-sided 95% Wilson interval. Wilson intervals describe outcomes on this fixed authored suite; they do not establish generalization to deployment traffic. Repeated live trials over the same authored case are not independent samples of the deployment population. Compatible live-run comparisons therefore pair by case ID and trial index and cluster bootstrap resampling at the authored-case level.

Utility rates use completed utility trials only. Targeted attack rates use completed primary attack trials only; clean twins and ablation replicates have separate denominators. Grounding error uses adjudicated generated topics, not case trials. Unreviewed topics and unclear propositions remain visible. Latency reports completed calls, median, and p95. Cost reports observed billed totals across successful and failed provider calls; calls whose providers do not report cost remain explicitly counted as unknown.

## Reproducibility and limitations

Runs record immutable suite, root and case-specific corpus, configuration, prompt, protocol, and exact model identifiers with SHA-256 hashes. They also record prompt and adapter order, generation controls, per-adapter timeouts, trials, run kind, execution order/seed, circuit threshold, and cost-ceiling settings. An interrupted `running` checkpoint can resume only when those fields still match and its saved rows are an exact artifact-complete prefix of the original plan. Resume skips every saved completed or failed row and reconstructs observed billed cost plus per-adapter circuit state before any new provider call; complete, stopped, incompatible, or corrupt checkpoints are refused. Pilot rows are marked and excluded from final preregistered results. Provider errors, circuit skips, replacement runs, missing adjudications, and unknown costs are retained rather than converted into successes or zeroes.

The suite is fixed, small, and intentionally enriched for known boundaries. Provider behavior and model aliases can change. CLI and API sampling controls are not equivalent. Human grounding decisions can disagree and require agreement statistics plus adjudication. These limitations preclude a single composite reliability score or claims that small model/prompt differences are decisive.

## Portfolio-v1 completion status

The final five-trial matrix completed 1,200/1,200 planned rows with no provider failures or skips. Compatible
prompt comparisons pair case ID and trial and use 10,000 authored-case-cluster bootstrap resamples. All 180
URL-scoped meaning propositions received blinded Nemotron judgments, but these are machine evidence rather
than human approval. Human grounding remains incomplete at 0/2,170 generated utility topics; the primary
packet covers all topics and a stratified packet independently double-reviews 434. Every published human
grounding rate therefore remains unavailable with its missing count shown.

The candidate is not approved for either evaluated model. At least one available preregistered requirement
already fails in each comparison, so completing grounding review cannot convert the present result into a
pass. The versioned aggregate history preserves the suite, prompt, model, protocol, date, completeness,
latency, cost, and decision. The regression policy prevents incomplete or incompatible runs from satisfying
a gate and treats its practical thresholds as review triggers rather than automatic significance claims.

This methodology operationalizes the [NIST AI RMF 1.0 MEASURE function](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/): documented and repeatable test, evaluation, verification, and validation; representative conditions; independent assessment; explicit uncertainty; and tracking risk over time. The complete publication is [NIST AI 100-1](https://doi.org/10.6028/NIST.AI.100-1).
