# Jev-stage evaluation plan

Status: planned, not run. No model call has been made for this plan. Protocol:
[`jev-stage-v1.json`](../../evaluator/protocols/jev-stage-v1.json) (status
`draft_pending_freeze`). The stage under test is described in
[daily Jev checks and repairs](../jev-review.md).

## Goal

Size and ground a measurement of the daily semantic stage as it actually runs:
Jev review of a ready briefing, isolated confirmation of each flag, one bounded HY3
repair round, follow-up review, and per-slot acceptance. The
[September 30 study](jev-preliminary-2026-09-30.md) was ten reports from one corpus
day with advisory-only findings and manual spot checks; it predates confirmation
thresholds in production, citation removal, per-slot acceptance, and the schema 3
audit. It is used here only as a sizing prior.

## Sample unit

One real dated corpus, run once through the current daily command with the review
stage enabled, at a frozen revision:

```bash
python3 -S restore_private_corpora.py --newest YYYY-MM-DD --output-dir corpora
python3 -S run_daily_briefing.py \
  --corpus corpora/YYYY-MM-DD.json \
  --run-dir .news-briefing/jev-stage/YYYY-MM-DD/run \
  --jev-review-dir .news-briefing/jev-stage/YYYY-MM-DD/review \
  --output .news-briefing/jev-stage/YYYY-MM-DD/briefing.md
```

Only `schema_version: 3` audits (per-slot acceptance) are read. Audits written
under schema 1 or 2 are excluded, not migrated. Restoring from the encrypted
archive supplies at most the fourteen-date retention window, so a sample larger
than fourteen days spans two or more restore windows or adds fresh daily fetches.
A day whose fallback chain reaches no ready candidate is a generation-failure row
with no review metrics. A day whose review ends `partial` or `failed` counts in the
coverage denominator and in no rate conditioned on complete review. So does a day
whose review directory holds no schema 3 audit at all: when the review raises,
the daily runner prints the exception type, retains the original briefing, and
writes no audit. The coverage denominator is therefore every day with a ready
candidate, read from the run directory, and the aggregator must record a missing
audit as a review-failure row rather than skip the day.

## Sample size

The sizing prior is the September 30 study: 7 of 10 reports received a repair
round, 12 of 220 included topics carried a confirmed included grouping finding, and
17 of 21 initial flags were confirmed. Those are one day's observations under an
earlier policy, so they size the sample and nothing else.

Day-level rates are the binding constraint. Wilson 95% interval widths at a
day-level rate near the pilot's 0.7, computed with `evaluator.metrics.wilson_interval`:

| Days | Interval at 0.7 | Width |
|---:|---|---:|
| 10 | [0.40, 0.89] | 0.49 |
| 14 | [0.45, 0.88] | 0.43 |
| 28 | [0.53, 0.85] | 0.32 |
| 60 | [0.58, 0.80] | 0.23 |

The primary sample is **28 corpus days** (two retention windows), which bounds a
day-level rate to about ±16 points. The minimum reportable sample is 14 days (one
window), about ±22 points. At roughly 22 included topics per day, 28 days give
about 616 topic observations; a topic-level rate near the pilot's 5.5% would then
have a Wilson width near 3.6 points, but topics within a day are not independent,
so topic-level and check-level rates are reported with day-clustered bootstrap
intervals alongside their Wilson intervals. Sixty days would narrow the day-level
interval to about ±11 points and is the next step if 28 days leave a decision open.

## Metrics

Every rate is reported as successes, trials, and a two-sided 95% Wilson interval.

- **Flag rate.** Initial flags over returned checks, by check kind; and days with
  at least one flag over days with complete initial review.
- **Confirmation rate.** Confirmed over initial flags, with disputed and
  unconfirmed counted separately. Confirmation is repeat agreement from the same
  judge, not independent verification.
- **Repair round rate and per-slot acceptance.** Days with at least one repair
  target over days with complete review; `apply_repair` over repaired slots, with
  `withhold_repair`, failed, and skipped outcomes broken down by allowlisted code
  (too many targets, all sources confirmed irrelevant, partial follow-up coverage,
  unknown cost, assembly failure).
- **Publication outcome.** `original_retained`, `repaired_applied`,
  `partial_applied` per day.
- **Follow-up blockers.** Re-flagged checks on repaired slots by check kind,
  including two-position checks that block both slots.
- **Cost.** Reported USD per phase per day (initial review and confirmation,
  repair, follow-up), unknown-cost calls, and days stopped by an estimated
  spending guard. The daily flow gives initial and follow-up review separate
  USD 0.10 guards and stops HY3 repair at USD 0.10 reported spending.

## Blind human adjudication subset

Modeled on the [October 2 protocol](../../evaluator/protocols/independent-production-2026-10-02.json):
its partitioning, SHA-256 ordering, blinding, double review, and third-reviewer
adjudication are reused with human reviewers in place of model reviewers. Its
labels and cohort are not reused.

- **Repair pairs.** Every repaired slot, applied or withheld, becomes an
  original/repaired pair. Both sides are independently shuffled. Two reviewers
  label every pair for material-claim support, meaning preservation (lost, added,
  or changed material claims), retained-citation relevance, and readability
  (complete sentences, no copied fragments).
- **Control topics.** Per day, order unflagged included topics by
  `SHA256(protocol_id + report_date + section + position)` and take the first
  three. One reviewer labels all controls; the second labels the first third
  rounded up. Controls exist because the pilot found a missed attribution error
  that Jev scored well below threshold.
- **Blinding.** Packets contain frozen title/excerpt evidence and prose only. They
  omit model and provider names, Jev scores and labels, repair status, file paths,
  and which side of a pair is the original. Labels are frozen before unblinding.
- **Disagreements.** Both labels are retained. A third human adjudicates material
  disagreements from the blinded evidence and rationales only. Raw agreement and
  adjudicated outcomes are reported separately with exact denominators.

A packet exporter for the semantic audit does not exist yet; the evaluator's
`export-grounding-review` serves evaluator runs, not daily audits. Writing one is
a follow-up deliverable, as is an aggregator that reads each review directory's
schema 3 metadata and `public_audit` projection into the metrics above and
records days with a ready candidate but no audit as review failures.

## Before the run

1. Freeze `jev-stage-v1.json`: set `status` to `frozen_for_run`, record the
   revision, and commit before the first day runs.
2. Confirm `OPENROUTER_API_KEY` in the environment; the review and repair models
   use the same key. No other credential is needed.
3. Confirm the archive passphrase is available. The October 2 assessment had to
   amend its protocol when it was not.

## Claims boundary

The result may claim: observed flag, confirmation, repair, acceptance, blocker, and
cost rates for the daily stage at the frozen revision on the sampled days, with
intervals; human-adjudicated support and meaning-preservation outcomes for repaired
slots and the control sample, with agreement statistics.

The result may not claim: Jev precision or recall against ground truth (controls
are a small sample, so no overall miss rate); calibrated probabilities; factual
accuracy beyond the frozen excerpts (full articles are not reviewed); editorial
completeness or ranking quality (repairs remove sources and the pilot removed 27);
deployment reliability of the published archive (these are replays at a frozen
revision, and HY3 at temperature 0.2 is not deterministic); or generalization
beyond the sampled window, whose consecutive days share source health.

Once this runs, the September 30 study is cited only as the pilot that sized it.
