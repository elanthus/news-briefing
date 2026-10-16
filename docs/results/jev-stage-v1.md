# Jev-stage evaluation v1 model card

Status: **unrun**. This skeleton fixes the sections, tables and claims boundary before
any model call so the numbers can only be filled from the committed aggregate, the
per-day ledger and the adjudicated packets. Every value marked `unrun` is a
placeholder, not an observation. Plan: [`jev-stage-eval-plan.md`](jev-stage-eval-plan.md).
Protocol: [`jev-stage-v1.json`](../../evaluator/protocols/jev-stage-v1.json)
(`frozen_for_run`, frozen 2026-10-16). Sample: [`jev-stage-sample.json`](jev-stage-sample.json).
The stage under test is described in [daily Jev checks and repairs](../jev-review.md).
Read the [evaluation methodology](../evaluation-methodology.md) before quoting any
number from this card.

The evaluation replays real dated corpora through the current daily command with the
review stage enabled, at a frozen revision: Jev review of the ready briefing, isolated
confirmation of each flag, one bounded HY3 repair round, follow-up review, and per-slot
acceptance. Only `schema_version: 3` audits are read. Freeze base commit
`a7dc2e4a65ddc1c7afe3cb3e24eb682fcfc19938`; run commit `unrun`.

## Sample and coverage

Rates show `successes/trials; rate [95% Wilson interval]`. Pooled check-, flag- and
slot-level rates add a day-clustered bootstrap interval in a second bracket.

| Field | Value |
|---|---:|
| Planned frame | 2026-09-18 through 2026-10-15, 28 days |
| Days with a restorable corpus | unrun |
| Generation-failure rows | unrun |
| Days with complete initial and follow-up review | unrun |
| Days with partial or failed review | unrun |
| Missing days | unrun |
| Coverage (complete review over planned days) | unrun |

## Flag and confirmation rates

| Check kind | Initial flags / returned checks | Confirmed / flags | Disputed / flags | Unconfirmed / flags |
|---|---:|---:|---:|---:|
| duplicate | unrun | unrun | unrun | unrun |
| grouping | unrun | unrun | unrun | unrun |
| irrelevant_citation | unrun | unrun | unrun | unrun |
| reversed_claim | unrun | unrun | unrun | unrun |
| strengthened_claim | unrun | unrun | unrun | unrun |
| unsupported_claim | unrun | unrun | unrun | unrun |
| all kinds | unrun | unrun | unrun | unrun |

Days with at least one flag over days with complete initial review: unrun. Confirmation
is repeat agreement from the same judge on the same frozen evidence, not independent
verification.

## Repair rounds and per-slot acceptance

| Outcome | Slots / repair targets |
|---|---:|
| apply_repair | unrun |
| withhold_repair | unrun |
| failed | unrun |
| skipped | unrun |

Days with at least one repair target over days with complete review: unrun.

Causes by allowlisted code, with the outcome's slot count as denominator (causes can
overlap): unrun.

## Publication outcomes

| Outcome | Days / days with complete review |
|---|---:|
| original_retained | unrun |
| repaired_applied | unrun |
| partial_applied | unrun |

## Follow-up blockers

| Check kind | Re-flagged checks on repaired slots | Affected slots |
|---|---:|---:|
| duplicate | unrun | unrun |
| grouping | unrun | unrun |
| irrelevant_citation | unrun | unrun |
| reversed_claim | unrun | unrun |
| strengthened_claim | unrun | unrun |
| unsupported_claim | unrun | unrun |

A check that names two positions blocks both slots and is counted once as a check.

## Cost

| Field | Value |
|---|---:|
| Reported generation cost, all days (USD) | unrun |
| Reported initial review and confirmation cost (USD) | unrun |
| Reported repair cost (USD) | unrun |
| Reported follow-up review cost (USD) | unrun |
| Unknown-cost calls | unrun |
| Days stopped by an estimated spending guard | unrun |
| Per-day guards (USD) | generation allowance 25 (per-call reservation guard, not a cap); initial review 0.10; follow-up review 0.10; HY3 repair 0.10 |

Reported spend is provider-reported cost for this run and never an estimate of
unreported billing.

## Blind human adjudication

Packets were exported with `agent_runner.audit_packets` over the frozen frame;
correspondence stayed private until labels were frozen. Unblinding happened: unrun
(state the date and that both reviewers' labels and rationales were frozen first).

| Field | Value |
|---|---:|
| Repair pairs (applied or withheld) | unrun |
| Control topics, first reviewer | unrun |
| Control topics, second reviewer | unrun |
| Items double-labeled | unrun |

### Raw agreement

Exact denominators are the items both reviewers labeled.

| Question | Agreements / double-labeled items |
|---|---:|
| Support | unrun |
| Meaning preservation (pairs only) | unrun |
| Citations | unrun |
| Readability | unrun |

### Adjudicated outcomes

Material disagreements were adjudicated by a third human from the blinded evidence and
rationales only; both original labels are retained.

| Question | Repair pairs | Controls |
|---|---:|---:|
| Material claims supported | unrun | unrun |
| Meaning preserved (pairs) | unrun | not applicable |
| Retained citations relevant | unrun | unrun |
| Readable complete sentences | unrun | unrun |

Adjudicated items: unrun. Items whose labels were left blank or returned invalid: unrun.

## Evidence

Committed: the protocol, [`jev-stage-sample.json`](jev-stage-sample.json), the per-day
artifact digest ledger `jev-stage-v1-ledger.json` (unrun), the aggregate
`jev-stage-v1-metrics.json` (unrun), and reviewer agreement counts. Private and ignored:
corpora, run trees, review directories with frozen excerpts, prompts, raw judgments,
packets, labels and the correspondence key.

## Claims boundary

This card may claim: observed flag, confirmation, repair, acceptance, blocker and cost
rates for the daily stage at the frozen revision on the sampled days, with intervals;
human-adjudicated support and meaning-preservation outcomes for repaired slots and the
control sample, with agreement statistics.

This card may not claim: Jev precision or recall against ground truth (controls are a
small sample, so no overall miss rate); calibrated probabilities; factual accuracy
beyond the frozen excerpts (full articles are not reviewed); editorial completeness or
ranking quality (repairs remove sources); deployment reliability of the published
archive (these are replays at a frozen revision, and HY3 at temperature 0.2 is not
deterministic); or generalization beyond the sampled window, whose consecutive days
share source health. Once this runs, the [September 30 study](jev-preliminary-2026-09-30.md)
is cited only as the pilot that sized it.
