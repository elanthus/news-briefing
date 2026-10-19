# Jev-stage evaluation v1 model card

Status: **execution and adjudication complete; pooled and per-side outcomes recorded**. The frozen 28-day
frame ran on 2026-10-16: all days produced ready briefings and retained schema-3
audits, with 17 complete reviews and 11 partial reviews. Both reviewers' labels
are frozen; adjudication decisions were returned on 2026-10-19 and recorded by
digest; the adjudication key was opened at 2026-10-19T22:40:56Z and the
packet correspondence key was opened at 2026-10-19T22:57:16Z to split the
adjudicated outcomes below by original and repaired side. Automated
metrics below come from the committed aggregate and per-day ledger.
Plan: [`jev-stage-eval-plan.md`](jev-stage-eval-plan.md).
Protocol: [`jev-stage-v1.json`](../../evaluator/protocols/jev-stage-v1.json)
(`complete_adjudicated`, frozen before execution on 2026-10-16).
Sample: [`jev-stage-sample.json`](jev-stage-sample.json).
The stage under test is described in [daily Jev checks and repairs](../jev-review.md).
Read the [evaluation methodology](../evaluation-methodology.md) before quoting any
number from this card.

The evaluation replays real dated corpora through the current daily command with the
review stage enabled, at a frozen revision: Jev review of the ready briefing, isolated
confirmation of each flag, one bounded HY3 repair round, follow-up review, and per-slot
acceptance. Only `schema_version: 3` audits are read. Freeze base commit
`a7dc2e4a65ddc1c7afe3cb3e24eb682fcfc19938`; run commit
`0a06089822a35e4777b6b77b9386eb5700522710`.

## Sample and coverage

Rates show `successes/trials; rate [95% Wilson interval]`. Pooled check-, flag- and
slot-level rates add a day-clustered bootstrap interval in a second bracket.

| Field | Value |
|---|---:|
| Planned frame | 2026-09-18 through 2026-10-15, 28 days |
| Days with a restorable corpus | 28 |
| Generation-failure rows | 0 |
| Days with complete initial and follow-up review | 17 |
| Days with partial or failed review | 11 partial; 0 failed |
| Missing days | 0 |
| Coverage (complete review over planned days) | 17/28; 60.714% [42.409, 76.434] |

## Flag and confirmation rates

| Check kind | Initial flags / returned checks | Confirmed / flags | Disputed / flags | Unconfirmed / flags |
|---|---:|---:|---:|---:|
| duplicate | 1/13255; 0.008% [0.001, 0.043] [0.000, 0.023] | 0/1; 0.000% [0.000, 79.345] [0.000, 0.000] | 1/1; 100.000% [20.655, 100.000] [100.000, 100.000] | 0/1; 0.000% [0.000, 79.345] [0.000, 0.000] |
| grouping | 13/343; 3.790% [2.228, 6.376] [1.869, 6.098] | 11/13; 84.615% [57.765, 95.674] [55.556, 100.000] | 2/13; 15.385% [4.326, 42.235] [0.000, 44.444] | 0/13; 0.000% [0.000, 22.810] [0.000, 0.000] |
| irrelevant_citation | 11/993; 1.108% [0.620, 1.973] [0.206, 2.235] | 8/11; 72.727% [43.435, 90.254] [33.333, 100.000] | 3/11; 27.273% [9.746, 56.565] [0.000, 66.667] | 0/11; 0.000% [0.000, 25.883] [0.000, 0.000] |
| reversed_claim | 0/374; 0.000% [0.000, 1.017] [0.000, 0.000] | 0/0; not estimable | 0/0; not estimable | 0/0; not estimable |
| strengthened_claim | 0/374; 0.000% [0.000, 1.017] [0.000, 0.000] | 0/0; not estimable | 0/0; not estimable | 0/0; not estimable |
| unsupported_claim | 0/374; 0.000% [0.000, 1.017] [0.000, 0.000] | 0/0; not estimable | 0/0; not estimable | 0/0; not estimable |
| all kinds | 25/15713; 0.159% [0.108, 0.235] [0.089, 0.235] | 19/25; 76.000% [56.570, 88.504] [53.333, 95.000] | 6/25; 24.000% [11.496, 43.430] [5.000, 46.667] | 0/25; 0.000% [0.000, 13.319] [0.000, 0.000] |

Days with at least one flag over days with complete review: 11/17; 64.706% [41.300, 82.690]. Confirmation
is repeat agreement from the same judge on the same frozen evidence, not independent
verification.

## Repair rounds and per-slot acceptance

| Outcome | Slots / repair targets |
|---|---:|
| apply_repair | 13/16; 81.250% [56.991, 93.408] [56.250, 100.000] |
| withhold_repair | 1/16; 6.250% [1.112, 28.329] [0.000, 17.647] |
| failed | 2/16; 12.500% [3.498, 36.023] [0.000, 40.000] |
| skipped | 0/16; 0.000% [0.000, 19.361] [0.000, 0.000] |

Days with at least one repair target over days with complete review: 9/17; 52.941% [30.963, 73.835].

Causes by allowlisted code, with the outcome's slot count as denominator (causes can
overlap): `failed` / `invalid_candidate`: 2/2; 100.000% [34.238, 100.000] [100.000, 100.000];
`withhold_repair` / `followup_flag`: 1/1; 100.000% [20.655, 100.000] [100.000, 100.000].
No causes were recorded for applied or skipped slots.

## Publication outcomes

| Outcome | Days / days with complete review |
|---|---:|
| original_retained | 9/17; 52.941% [30.963, 73.835] |
| repaired_applied | 7/17; 41.176% [21.611, 63.995] |
| partial_applied | 1/17; 5.882% [1.046, 26.982] |

## Follow-up blockers

| Check kind | Re-flagged checks on repaired slots | Affected slots |
|---|---:|---:|
| duplicate | 0 | 0 |
| grouping | 1 | 1 |
| irrelevant_citation | 1 | 1 |
| reversed_claim | 0 | 0 |
| strengthened_claim | 0 | 0 |
| unsupported_claim | 0 | 0 |

A check that names two positions blocks both slots and is counted once as a check.

## Cost

| Field | Value |
|---|---:|
| Reported generation cost, all days (USD) | 0.869111 |
| Reported initial review and confirmation cost (USD) | 0.944354 |
| Reported repair cost (USD) | 0.031003 |
| Reported follow-up review cost (USD) | 0.252523 |
| Unknown-cost calls | 0 |
| Days stopped by an estimated spending guard | 11 |
| Per-day guards (USD) | generation allowance 25 (per-call reservation guard, not a cap); initial review 0.10; follow-up review 0.10; HY3 repair 0.10 |

Reported spend totals USD 2.096991 across all 28 days, including partial reviews.
The 11 spending-guard stops concern review completeness; all days still produced
a ready briefing. Rates above use only the 17 complete reviews, so they do not
estimate outcomes for the partial reviews. Reported spend is provider-reported
cost for this run and never an estimate of unreported billing.

## Blind human adjudication

Packets were exported with `agent_runner.audit_packets` over the frozen frame;
both reviewers' labels and rationales were validated and frozen before adjudication
on 2026-10-18 UTC (2026-10-17 Pacific). The
[freeze record](jev-stage-v1-labels-frozen.json) retains the file digests. The
adjudication key, which maps each adjudicated item back to its two packet ids and
the A/B reviewer order, was opened at 2026-10-19T22:40:56Z to compute the
outcomes below, after every input digest was checked against the freeze record.
The packet correspondence key, which names each pair's original side, was opened
at 2026-10-19T22:57:16Z after adjudication. The pooled counts remain unchanged;
the per-side table separates original and repaired versions.

| Field | Value |
|---|---:|
| Repair pairs (applied or withheld) | 14 |
| Control topics, first reviewer | 51 |
| Control topics, second reviewer | 17 |
| Items double-labeled | 31 (14 pairs; 17 controls) |

### Raw agreement

Exact denominators are the shared version labels for support, citations and
readability (two versions per pair plus controls), and shared pairs for meaning
preservation. Raw agreement is retained before adjudication.

| Question | Agreements / double-labeled versions or pairs |
|---|---:|
| Support | 28/45 |
| Meaning preservation (pairs only) | 3/14 |
| Citations | 44/45 |
| Readability | 45/45 |

### Adjudicated outcomes

Adjudication decisions for the 23 shared items with material disagreements were
returned on 2026-10-19 and validated against the enriched packet; both original
labels are retained, and the [freeze record](jev-stage-v1-labels-frozen.json)
holds the decision file's digest, its label counts, and a note on the revision
made before the correspondence key was opened. The 2026-10-19 protocol amendments add a meaning
label tie-break, allow separate adjudicator flags on agreed labels, and enrich
the adjudication evidence with outlet names and publication dates. Reviewers
labeled without the tie-break.

Final labels were computed with `agent_runner.audit_adjudication outcomes` over
the 31 double-labeled items: an agreed label stays as both reviewers gave it, and
each disputed question takes the adjudicator's decision. Denominators are the
version labels of the shared pairs (two per pair, both sides pooled) and of the
shared controls, and the shared pairs for meaning preservation. The
[freeze record](jev-stage-v1-labels-frozen.json) holds the same counts with every
label value and the digests of the inputs used.

| Question | Repair pairs | Controls |
|---|---:|---:|
| Material claims supported | 21/28 | 13/17 |
| Meaning preserved (pairs) | 0/14 | not applicable |
| Retained citations relevant | 25/28 | 17/17 |
| Readable complete sentences | 24/28 | 17/17 |

Each side contributes one version per shared repair pair, with 14 pairs as its
denominator. Side assignments follow the first packet's version order and are
checked against the second packet after alignment. These are human-adjudicated
outcomes from each version's frozen title-and-excerpt evidence; the repair pairs
include applied and withheld repairs.

| Question | Original | Repaired |
|---|---:|---:|
| Material claims supported | 8/14 | 13/14 |
| Retained citations relevant | 12/14 | 13/14 |
| Readable complete sentences | 10/14 | 14/14 |

Labels other than the favorable one, pairs: support 3 unsupported, 3 strengthened,
1 reversed; meaning 11 added, 2 changed, 1 lost; citations 3 irrelevant listed;
readability 4 fragments or incomplete. Controls: support 2 unsupported, 2
strengthened. A meaning label of added, changed or lost says the two sides of a
pair do not carry the same supported material claims; it does not say which
side is the original or which side is better supported.

Adjudicated items: 23 of 31, with 29 decisions applied. Items whose labels were
left blank or returned invalid: 0 (both label files and the decisions file
validate completely). Disputed questions without a decision: 0. Flag
sensitivity: the returned file carries 0 flags, so the outcomes with flags
applied are identical to the table.

The first reviewer also labeled 34 controls that the second reviewer did not see.
They were not adjudicated and are reported apart from the table: material claims
supported 27/34 (5 strengthened, 1 unsupported, 1 reversed); retained citations
relevant 34/34; readable complete sentences 33/34.

## Evidence

Committed: the protocol, [`jev-stage-sample.json`](jev-stage-sample.json), the per-day
artifact digest [ledger](jev-stage-v1-ledger.json), the
[aggregate metrics](jev-stage-v1-metrics.json), and the
[label freeze, raw agreement and adjudicated outcome counts](jev-stage-v1-labels-frozen.json).
Private and ignored: corpora, run trees, review directories with frozen excerpts, prompts,
raw judgments, packets, labels, adjudication decisions and both keys.

## Claims boundary

This card may claim: observed flag, confirmation, repair, acceptance, blocker and cost
rates for the daily stage at the frozen revision on the sampled days, with intervals;
raw human reviewer agreement; human-adjudicated support, meaning-preservation,
citation and readability outcomes for the double-labeled repair pairs, pooled and
separately for original and repaired slots, and the shared controls, with the exact
denominators above. Meaning preservation is a pair-level outcome.

This card may not claim: Jev precision or recall against ground truth (controls are a
small sample, so no overall miss rate); ground-truth accuracy; calibrated probabilities; factual accuracy
beyond the frozen excerpts (full articles are not reviewed); editorial completeness or
ranking quality (repairs remove sources); deployment reliability of the published
archive (these are replays at a frozen revision, and HY3 at temperature 0.2 is not
deterministic); or generalization beyond the sampled window, whose consecutive days
share source health. The [September 30 study](jev-preliminary-2026-09-30.md) is the pilot that sized it.
