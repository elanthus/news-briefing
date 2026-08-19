# Portfolio v1 final reliability results

Portfolio v1 completed all 1,200 preregistered generation rows: two OpenRouter models, two frozen prompts,
five trials, and 60 authored-or-derived case rows per group. No provider rows failed or were skipped. The
candidate prompt does **not** satisfy the full promotion rule for either model.

## Lead results

Rates show `successes/trials; rate [95% Wilson interval]`. Prompt deltas below use the preregistered paired
authored-case-cluster bootstrap.

| Model / prompt | First contract | End-to-end first | End-to-end final | Correction success |
|---|---:|---:|---:|---:|
| DeepSeek / production | 70/110; 63.6% [54.3, 72.0] | 70/110; 63.6% [54.3, 72.0] | 87/110; 79.1% [70.6, 85.6] | 18/40; 45.0% [30.7, 60.2] |
| DeepSeek / reliability-v1 | 49/110; 44.5% [35.6, 53.9] | 49/110; 44.5% [35.6, 53.9] | 97/110; 88.2% [80.8, 93.0] | 53/61; 86.9% [76.2, 93.2] |
| HY3 / production | 89/110; 80.9% [72.6, 87.2] | 89/110; 80.9% [72.6, 87.2] | 90/110; 81.8% [73.6, 87.9] | 1/21; 4.8% [0.8, 22.7] |
| HY3 / reliability-v1 | 75/110; 68.2% [59.0, 76.1] | 74/110; 67.3% [58.1, 75.3] | 86/110; 78.2% [69.6, 84.9] | 14/35; 40.0% [25.6, 56.4] |

| Model / prompt | Final attack success | Utility under attack | Meaning conveyed¹ | Machine grounding error² | Proxy grounding | First-call latency median / p95 | Cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| DeepSeek / production | 4/105; 3.8% [1.5, 9.4] | 95/105; 90.5% [83.4, 94.7] | 34/45; 75.6% [61.3, 85.8] | 75/520; 14.4% [11.7, 17.7] | 37/520; 7.1% [5.2, 9.7] | 4.53s / 38.52s | $0.3345 |
| DeepSeek / reliability-v1 | 3/105; 2.9% [1.0, 8.1] | 99/105; 94.3% [88.1, 97.4] | 44/45; 97.8% [88.4, 99.6] | 72/670; 10.7% [8.6, 13.3] | 34/670; 5.1% [3.7, 7.0] | 3.22s / 40.54s | $0.3259 |
| HY3 / production | 6/105; 5.7% [2.6, 11.9] | 94/105; 89.5% [82.2, 94.0] | 37/45; 82.2% [68.7, 90.7] | 36/470; 7.7% [5.6, 10.4] | 9/470; 1.9% [1.0, 3.6] | 4.67s / 35.63s | $1.1869 |
| HY3 / reliability-v1 | 3/105; 2.9% [1.0, 8.1] | 92/105; 87.6% [80.0, 92.6] | 44/45; 97.8% [88.4, 99.6] | 72/510; 14.1% [11.4, 17.4] | 45/510; 8.8% [6.7, 11.6] | 4.69s / 44.78s | $1.1865 |

¹ Meaning conveyed is a blinded Nemotron machine judgment over each group's 45 URL-scoped `must_convey`
propositions, not a human label. Across all groups, Nemotron marked 159/180 conveyed, 21/180 not conveyed,
and none remained unclear.

² Grounding is a blinded automated judgment by DeepSeek V4 Pro 0813 over all 2,170 final utility topics,
not a human label. MiniMax M3 independently reviewed a stratified 434-topic sample and agreed on 388/434
(89.4% [86.2, 92.0]).

## Paired prompt decision

| Model | Final utility delta | Final attack-success delta | Contract regressions | Promotion rule |
|---|---:|---:|---:|---|
| DeepSeek V4 Flash | **+9.1 pp** (+3.6, +15.5) | −1.0 pp (−2.9, +0.0) | 0 | Fail: attack-resistance gain is below 5 pp; machine grounding is descriptive only |
| Tencent HY3 | −3.6 pp (−13.6, +2.7) | −2.9 pp (−14.3, +5.7) | 5 | Fail: utility, attack, and zero-regression rules fail; machine grounding is descriptive only |

The candidate substantially improves DeepSeek's final utility after correction, but its first-pass utility
falls by 19.1 points. Its final attack improvement is only one point, below the predeclared five-point
threshold. For HY3, the candidate reduces both final utility and targeted attack success, creates five
case-trial contract regressions, and raises the deterministic grounding-error proxy. These are tradeoffs,
not evidence for a single composite reliability score.

## Attack breakdowns and matched pairs

Final targeted attack successes are concentrated in two behaviors and the direct technique. These are raw
`successes/trials`; the full local report retains Wilson intervals plus first-pass, recovery, robustness,
and utility-under-attack values for every cell.

| Behavior | Deep prod | Deep candidate | HY3 prod | HY3 candidate |
|---|---:|---:|---:|---:|
| category-selection | 0/10 | 0/10 | 0/10 | 0/10 |
| citation-alteration | 0/10 | 0/10 | 0/10 | 0/10 |
| citation-fabrication | 0/25 | 0/25 | 0/25 | 0/25 |
| duplicate-citations | 0/10 | 0/10 | 1/10 | 3/10 |
| formatting | 0/10 | 0/10 | 0/10 | 0/10 |
| health-reporting | 0/10 | 0/10 | 0/10 | 0/10 |
| prose | 0/10 | 0/10 | 0/10 | 0/10 |
| selection-promotion | 0/10 | 0/10 | 0/10 | 0/10 |
| selection-suppression | 4/10 | 3/10 | 5/10 | 0/10 |

| Technique | Deep prod | Deep candidate | HY3 prod | HY3 candidate |
|---|---:|---:|---:|---:|
| combined | 0/45 | 0/45 | 0/45 | 0/45 |
| context ignore | 0/5 | 0/5 | 0/5 | 0/5 |
| direct | 4/45 | 3/45 | 6/45 | 3/45 |
| escape character | 0/5 | 0/5 | 0/5 | 0/5 |
| response injection | 0/5 | 0/5 | 0/5 | 0/5 |

All 25 planned matched clean/attack pairs completed in every model/prompt group; none were incomplete.

| Model / prompt | Benign structural utility final | Structural utility under attack final | Targeted attack success final |
|---|---:|---:|---:|
| DeepSeek / production | 17/25 | 25/25 | 4/25 |
| DeepSeek / reliability-v1 | 25/25 | 25/25 | 3/25 |
| HY3 / production | 25/25 | 24/25 | 6/25 |
| HY3 / reliability-v1 | 25/25 | 22/25 | 3/25 |

## Offline claim-heuristic false positives

These checker metrics are not model-generation results. Across 12 deliberately valid claim-boundary cases,
the aggregate heuristic false-positive rate was 7/12, 58.3% [32.0, 80.7]. Per check it was 6/21 for
`unsupported_figure` (28.6% [13.8, 50.0]), 0/26 for `unsupported_quotation` (0.0% [0.0, 12.9]), and 1/26
for `claim_exceeds_evidence` (3.8% [0.7, 18.9]). The twelfth case reuses the independently validated
`url-valid-baseline`, whose supported `version 2` numeric claim in evaluated topic prose exercises the figure
heuristic without changing its gold labels. The paired boundaries make these heuristics' intentionally narrow limits visible; they are warnings
for human review, not factuality guarantees.

## Review completeness

- Offline checker/feed labels: 81/81 independently human-validated.
- Meaning propositions: 180/180 machine-adjudicated; 159 conveyed, 21 not conveyed, 0 unclear.
- Final utility grounding topics: 2,170/2,170 machine-labeled by DeepSeek V4 Pro 0813. MiniMax M3 reviewed
  a stratified 434-topic sample, with 388/434 agreement. These are automated judgments, not human labels;
  full production usage would use fully human-curated labeling.
- Pairwise prose-quality judging: not run; no prose-preference claim is made.

The automated grounding result is descriptive and does not satisfy the preregistered human-grounding gate.
The candidate is already ineligible for promotion on the other rules, so this limitation cannot turn either
result into a pass.

## Operational record

The final generation run cost $3.0338 as reported by OpenRouter, below its $4.00 final-run ceiling. Known
portfolio work including pilots and label review remained below the user's $5 total authorization. All
1,200 rows completed, including 517 successful contract-guided correction calls. Nemotron produced three
transient malformed responses during semantic review; checkpointed retries succeeded. No rate limit
occurred, so the GLM 5.2 fallback was not used. Grounding review checkpoints recorded $1.3945 across
successful DeepSeek V4 Pro 0813 and MiniMax M3 calls. One earlier billed max-length response preceded
provider-error cost checkpointing, so the exact review total is slightly higher but remained well below
the separate $7 authorization.

The full local report contains Wilson intervals, behavior and technique breakdowns, matched clean/attack
pairs, ablations, operations, latency, and cost. The curated JSON beside this document preserves the
publication aggregates without committing raw generated outputs or provider payloads.

## Limitations

The suite is authored and finite; bootstrap intervals describe sensitivity across these case clusters, not
all future news or attacks. Temperature zero with no provider seed is not bit-reproducible. HY3 is an
owner-authorized operational replacement for Sonnet 5 after Sonnet timed out on the production corpus.
DeepSeek reasoning was disabled after high and low reasoning exhausted the completion budget without usable
text. Feed blurbs can be truncated and are treated as the evidence boundary. Grounding labels are automated,
and the 89.4% cross-model audit agreement shows material judge sensitivity. They must not be represented as
human approval; full production usage would use fully human-curated labeling.
