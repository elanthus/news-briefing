# Jev-stage evaluation plan

Status: frozen for run, not run. No model call has been made for this plan. Protocol:
[`jev-stage-v1.json`](../../evaluator/protocols/jev-stage-v1.json) (status
`frozen_for_run`, frozen 2026-10-16). The committed sample is
[`jev-stage-sample.json`](jev-stage-sample.json) and the runner is
[`run_jev_stage.py`](../../run_jev_stage.py); the model card skeleton is
[`jev-stage-v1.md`](jev-stage-v1.md). The stage under test is described in
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
python3 -S restore_private_corpora.py \
  --artifact-dir .news-briefing/portfolio-preservation-2026-10-15/github \
  --newest YYYY-MM-DD --output-dir .news-briefing/jev-stage/YYYY-MM-DD/corpora
python3 -S run_daily_briefing.py \
  --corpus .news-briefing/jev-stage/YYYY-MM-DD/corpora/YYYY-MM-DD.json \
  --run-dir .news-briefing/jev-stage/YYYY-MM-DD/run \
  --jev-review-dir .news-briefing/jev-stage/YYYY-MM-DD/review \
  --output .news-briefing/jev-stage/YYYY-MM-DD/briefing.md \
  --generation-usd 1.0
```

The code constrains these commands in four ways the first draft of this plan did
not state, so the plan was corrected rather than the code. The restore refuses a
non-empty output directory and, without `--artifact-dir`, would call GitHub with
a token, so every day restores from the preserved local wrappers into its own
`corpora` directory. The restore also requires each wrapper's main-repository
`workflow_run` provenance, which the preservation `manifest.json` does not carry
but the preserved `inventory.json` does; the runner joins the two into
`.news-briefing/jev-stage/artifact-manifest.json` before the first restore and
passes it with `--artifact-manifest`, failing closed on any wrapper without
provenance and modifying neither preserved file. Days with more than one daily
run have more than one wrapper, each archiving a fresh fetch of that day's
corpus, and the restore fails closed when two wrappers disagree about a date;
the merged manifest keeps only the newest wrapper per creation day, the run whose
corpus the published briefing used and later wrappers carry forward, and records
the superseded wrapper ids. The daily runner exits 0 even when the review stage raises
and retains the original, so a ready chain without `review/audit.json` is a
failure, not a reviewed day. `--generation-usd` bounds each day's generation
spend; the runner's default allowance is USD 25 per invocation.
[`run_jev_stage.py`](../../run_jev_stage.py) executes both commands for every
date in the committed sample, in order, records each day's artifact SHA-256
values and reported costs, skips days that already have a record, continues past
a generation-failure row, and stops on any other failure:

```bash
python3 -S run_jev_stage.py \
  --sample docs/results/jev-stage-sample.json \
  --root .news-briefing/jev-stage \
  --artifact-dir .news-briefing/portfolio-preservation-2026-10-15/github \
  --generation-usd 1.0 \
  --ledger docs/results/jev-stage-v1-ledger.json
```

Only `schema_version: 3` audits (per-slot acceptance) are read. Audits written
under schema 1 or 2 are excluded, not migrated. Restoring from the encrypted
archive supplies at most the fourteen-date retention window, so a sample larger
than fourteen days spans two or more restore windows or adds fresh daily fetches.
A day whose fallback chain reaches no ready candidate is a generation-failure row
with no review metrics. A day whose review ends `partial` or `failed` counts in the
coverage denominator and in no rate conditioned on complete review.

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

The aggregator ([`agent_runner.audit_metrics`](../../agent_runner/audit_metrics.py))
and the packet exporter ([`agent_runner.audit_packets`](../../agent_runner/audit_packets.py))
implement the metrics and packets above over a declared `--start`/`--end` frame;
[daily Jev checks and repairs](../jev-review.md#aggregate-current-daily-audits)
documents both. Pooled check-, flag- and slot-level rates carry a day-clustered
bootstrap interval beside their Wilson interval. Each packet carries the label
rubric; reviewers return labels and rationales per item.

## Sample

[`jev-stage-sample.json`](jev-stage-sample.json) records the inventory and the
selection rule. The preserved encrypted wrappers under
`.news-briefing/portfolio-preservation-2026-10-15/github` were created daily from
2026-10-01 through 2026-10-15 and each supplies at most the fourteen dates ending
at its creation date; the live unexpired GitHub artifacts are the same wrappers
from 2026-10-02 onward. Nothing was decrypted for the inventory. The union of
those windows on or after the schema v7 cutover (2026-09-06) is 28 dates,
2026-09-18 through 2026-10-15, which is the primary frame. If a date in the
frame has no restorable corpus it stays in the frame as a `missing_day` row and
in the coverage denominator; the minimum reportable fallback is the fourteen
dates 2026-10-02 through 2026-10-15.

## Expected cost

Per day, generation is bounded at USD 1.00 by `--generation-usd`, the initial
review with confirmation and the follow-up review each stop at an estimated USD
0.10, and HY3 repair stops at USD 0.10 reported spend, so one day's ceiling is
about USD 1.30 and 28 days about USD 36. The September 30 study paid about USD
0.05 per generation and USD 0.68 for ten generations with review, confirmation
and repair, so the planning estimate is USD 0.15 to 0.35 per day and USD 4 to 10
for 28 days. The guards are estimated spending stops, not provider-enforced caps;
the aggregate reports observed cost and unknown-cost calls per day.

## Before the run

1. Freeze `jev-stage-v1.json`: set `status` to `frozen_for_run`, record the
   revision, and commit before the first day runs. Done on 2026-10-16; the
   protocol's `revision` block names the freeze base commit and the rule for the
   run commit, which the runner records per day.
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
