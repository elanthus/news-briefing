# Daily Jev checks and repairs

After generation selects a ready briefing, the daily stage requests a Jev review.
It confirms each initial flag in isolation and can attempt one bounded HY3 repair round.
Accepted slots update the public report. The original run remains unchanged.
Both models use `OPENROUTER_API_KEY`.

Jev uses OpenRouter's Decisions API with `typesafe/jev-1.13`.
It needs no TypeSafe key or third-party Python package.
It returns probabilities, not prose or free-text rationales.
These excerpt judgments are automated evidence, not human verification.

The integrity report distinguishes accepted, rejected, failed, and skipped repairs.
It displays changed prose once per story and separates initial review, confirmation, follow-up, and billing uncertainty.

## Review an existing run

Use a completed runner directory with a manifest identifying a `ready` final candidate.
For a fallback chain, find the selected child directory in `fallback-log.json`.
Do not use the chain's parent directory as the review input.
Use `--confirm-flags` to confirm each initial flag in isolation.

```bash
python3 -S -m agent_runner.jev_review \
  --run-dir .news-briefing/runs/RUN \
  --confirm-flags \
  --output-dir .news-briefing/jev-reviews/RUN
```

The output directory must be new and separate from the source directory.
Neither directory can contain the other.
Use a new directory to review with different settings.
The standalone command returns zero for complete coverage and one for partial or failed review.
Flag counts do not determine this exit status.

| Entry point | Review behavior |
|---|---|
| `run_briefing.py --jev-review-dir DIR` | Advisory review after a ready result. |
| `agent_runner.jev_review --confirm-flags` | Standalone review with isolated flag confirmation. |
| `run_daily_briefing.py --jev-review-dir DIR` | Confirmation and bounded repair, subject to full acceptance checks. |
| Scheduled publication | Always enables the daily review stage. |

The daily producer has one apply policy. The obsolete `--jev-repair-mode` option is removed.
Current publication accepts only schema-3 audits with per-slot decisions.
Historical candidate-only artifacts remain [research evidence](research-retention.md).

## Confirmation and repair policy

### Confirm each flag

An initial score at or above its threshold triggers one isolated recheck.
The request contains only that finding's frozen evidence, prose, and rubric.
A pair check retains exactly two topics.

| Check | Default threshold |
|---|---:|
| Citation relevance | 0.60 |
| Other checks | 0.80 |

Both scores must reach the threshold for `confirmed`.
Otherwise, the flag is `disputed`.
A missing confirmation produces `unconfirmed`, partial coverage, and no repair.
Use `--citation-threshold` to change the standalone citation cutoff.

### Prepare the repair

Confirmed included citation, grouping, and prose findings can trigger repair.
Duplicate findings and excluded-topic grouping remain advisory.
The round can repair at most four slots. More targets skip the entire round.
HY3 preserves topic positions. Code preserves other topics and the exclusion log.
The repair does not rerank sections.

Each citation check compares one source's title and excerpt with the story headline and prose, without neighboring sources.
Code removes that source only when both scores reach the citation threshold.
Disputed and lower-scoring citations remain.
If all sources in any target slot are confirmed irrelevant, the round is skipped and the original report remains.
Every included slot must retain evidence.

After confirmed citation removals, grouping repair can select a nonempty subset of the slot's sources using local integer indexes.
Code freezes that subset before prose generation.
Prose-only repair preserves references and source order.
It keeps the original headline and requests only a summary.
A slot that loses a source can receive a new headline, subject to normal validation.
The verifier rejects a changed headline when sources are unchanged.
Repair models receive no URLs or opaque citation handles.

Each repair call permits at most 10,000 output tokens and 180 seconds.
The round makes at most two calls through the existing provider adapter.
A prose-only round requires one call because code preserves the selection.

### Check and apply each slot

The normal runner validates and renders the complete candidate.
A separate Jev follow-up repeats citation, grouping, prose, and duplicate checks.
Coverage must be complete and costs known before any slot can apply.

A repaired slot applies only when every returned check involving its position is below the applicable threshold.
A check involving two positions blocks both when it reaches its threshold.
Unrelated findings remain visible but do not block cleared slots.

| Result | Publication decision |
|---|---|
| Every repaired slot clears | Publish the repaired run as `repaired_applied`. |
| Some repaired slots clear | Assemble and validate a separate run as `partial_applied`. |
| No slot clears, or repair fails/skips | Retain the original ready report. |

Partial assembly uses the frozen original and cleared slots without another model call.
Code verifies exact candidate content for cleared slots and byte-identical original content for every other topic.
If assembly or verification fails, every target is recorded as failed and the original remains published.
The audit still retains scores, costs, and the slots that cleared.

Partial assembly does not receive another Jev review.
A cleared slot is therefore not compared with the original prose of a withheld slot.
This limits the duplicate-check guarantee for the assembled result.

A singleton source no longer requires grouping review.
Removed sources have removal outcomes, not invented follow-up scores.
Retained citation scores map back to original frozen indexes.
Removal must not shift one source's score onto another source.

Only one repair round is permitted.
The audit retains proposed prose for rejected slots.
Publication verifies hashes, before/after prose, unchanged topics, source subsets, per-slot acceptance, and the selected ready run.
It rejects audit metadata versions 1 and 2.
An invalid audit cannot replace the original publication input.

Repair instructions require complete sentences and prohibit copied truncated fragments.
Code rejects empty strings, trailing ellipses, and summaries without closing punctuation.
These checks do not prove readability, truth, or complete evidence support.

## Public audit and retained history

Public `semantic_audit` includes bounded generated before/after prose, positions, scores, labels, outcomes, coverage, and costs.
It excludes frozen feed titles, excerpts, and prompts.
Canonical `followup_checks` preserve actual returned questions, including blockers below the initial threshold.
Source indexes remain aligned with the original selection.

The versioned `integrity` record references canonical checks without duplicating story prose.
Publication verifies artifacts and checks decisions, coverage, removals, costs, and subjects.
Invalid audits expose only a safe verification-failed explanation and retain the original.
Fixed codes describe skip causes, failures, and rejections.
Raw exceptions, provider bodies, private paths, prompts, and excerpts stay private.

The current HTML layout leads with the publication decision and reader-relevant changes.
It then presents flagged stories, the exclusion log, and affected sources.
Operator sections contain the action ledger, statistics, scores, phases, costs, and hashes.
Proposed changes remain separate from published changes.
Exact word differences are escaped and displayed without interpreting model markup.
Article and Hacker News discussion destinations remain one source item.
Every returned check remains available in public JSON.

History schema 9 preserves valid metadata through merges, replacement, and rebuilds.
Older history schemas are rejected.
Explicit `integrity: null` and `semantic_audit: null` represent unavailable records.
Missing historical information is never recreated through new model calls.
Non-public dispositions retain status information only.
Encrypted diagnostics retain raw review files under `runs/DATE/jev-review/`.

## What is checked

| Check | Evidence and scope |
|---|---|
| Reworded duplicate | Compare included/included and included/excluded evidence across sections. Shared themes or entities alone are insufficient. Excluded/excluded pairs are omitted. |
| Citation relevance | Compare one included source's frozen evidence with the story headline and prose. Shared themes alone are insufficient. |
| Unsafe grouping | Determine whether a multi-source included or excluded topic combines distinct developments. |
| Unsupported claim | Determine whether included prose contains a material factual assertion unsupported by its own excerpts. |
| Strengthened claim | Detect increased certainty, scope, magnitude, or causality beyond the evidence. |
| Reversed claim | Detect a contradiction or reversal of the evidence. |

The private report retains redacted evidence, positions, rubrics, probabilities, model identity, request hash, latency, cost, and input hashes.
`--threshold` defaults to 0.8. `--citation-threshold` defaults to 0.6.
These provisional thresholds are not calibrated correctness guarantees.
Several checks can flag one topic. `flagged_checks` is not a topic error rate.

Before calling Jev, code verifies hashes, corpus, structured candidate, and reconstructed selected evidence.
Every final citation array must match its original section and position.
Dropped or reordered topics cause failure instead of reassignment to different evidence.
Jev receives no URLs or opaque citation/item handles.

## Bounds and failure reporting

| Limit | Value |
|---|---:|
| Included and excluded topics together | 64 |
| Default duplicate pairs | 1,000 |
| Maximum `--max-pairs` | 2,016 |
| Questions per request | 50 |
| Serialized ASCII request bytes | 24,000 |
| Response bytes | 128,000 |
| Default per-call timeout | 30 seconds |
| Calls per review phase | 256 |
| Overall review deadline | 120 seconds |
| Default `--cost-ceiling` | USD 0.10 |
| Maximum `--cost-ceiling` | USD 1.00 |

When pair capacity is insufficient, headline overlap determines priority. The report records omitted pairs.
This ordering cannot identify every semantic duplicate.
Requests use explicit field paths for each question's evidence.
An oversized individual check is skipped without truncating evidence.
Responses must contain exactly the requested Noul answer IDs and finite probabilities in `[0, 1]`.
Unexpected fields and answer types fail validation.

Each timeout is limited by the remaining review time.
Calls do not retry. Redirects are refused, and remote error bodies are not recorded.
The [partial-day supplement](results/jev-stage-v1-partial-supplement.md) used the higher call cap.
The original evaluation used 128 calls per phase.

The estimated spending guard uses request bytes and the configured USD 0.042-per-million input rate.
This rate is an implementation assumption, not a promise of current provider pricing.
Reported cost accumulates separately.
Initial/confirmation review and follow-up each have a USD 0.10 estimated guard.
Repair generation stops before another call when reported repair spend reaches USD 0.10.
Token limits bound individual calls, but these controls are not a strict combined billing cap.

Missing cost stops further calls. Failed calls retain unknown billing.
Returned results remain available when a later call fails.
The audit adds known review and repair costs and counts unknown-cost calls.

| Status | Meaning |
|---|---|
| `complete` | Every planned check returned a validated answer within the declared scope. This does not mean semantic acceptance. |
| `partial` | Pairs/checks were omitted or review stopped after some results. |
| `failed` | Source artifacts were invalid or failure occurred before any results. |
| `running` | Review was interrupted; an `in_flight` call can remain visible. |

Review reports cannot resume.
Do not automatically repeat an interrupted request with ambiguous billing.
The reviewer sees excerpts rather than full articles and does not check exclusion reasons for grounding.
Injected evidence can influence its judgments.
Monitor false alarms, missed errors, and editorial losses. The deterministic gate remains mandatory.

## Sources and implementation

- [OpenRouter Jev tutorial](https://openrouter.ai/blog/tutorials/how-to-use-jev/).
- [OpenRouter model page](https://openrouter.ai/typesafe/jev-1.13).
- [`agent_runner/decisions.py`](../agent_runner/decisions.py): bounded API client.
- [`agent_runner/jev_review.py`](../agent_runner/jev_review.py): verification, batching, confirmation, and reports.
- [`agent_runner/semantic_repairs.py`](../agent_runner/semantic_repairs.py): repair patches and public audit projection.

## Aggregate current daily audits

1. Create one private `YYYY-MM-DD` directory for each sampled day.
2. Include failed generation and incomplete-review days.
3. Declare the frozen sampling interval with `--start` and `--end`.
4. Put the ready run or fallback chain in each day's `run/` directory.
5. If a review audit exists, put it in exactly one directory: `review/` or `jev-review/`.

Missing day directories become `missing_day` rows.
An existing day without a review's `audit.json` becomes `missing_review`, with one exception.
If its fallback chain has status `failed` and no selected run, the day becomes `generation_failure`.
Folders outside the declared interval are rejected.

```bash
python3 -S -m agent_runner.audit_metrics .news-briefing/jev-stage \
  --start YYYY-MM-DD --end YYYY-MM-DD \
  --output .news-briefing/jev-stage-metrics.json
```

Replace date placeholders with the study's frozen dates.
The aggregator verifies schema-3 envelopes and corpus dates through `load_public_audit`.
Invalid envelopes, mismatched dates, and duplicate audits abort export.
Only verification reads private binding artifacts.
Metric calculation uses the public projection and parsed integrity record.
Output excludes prose, destinations, model identity, excerpts, requests, private metadata, and paths.

Every rate gives successes, trials, and a two-sided 95% Wilson interval, null when trials are zero.
Missing, partial, and failed reviews remain in coverage denominators and per-day costs.
They do not enter rates conditioned on complete review.
Failed chains without review become generation-failure rows.
Per-slot acceptance requires complete initial and follow-up review.
Report coverage and excluded-day statuses beside those rates.
Slot causes can overlap; their denominator is slots with that outcome.

Follow-up metrics separate check counts from affected-slot counts.
Initial costs include confirmation. Repair, follow-up, and unknown billing remain separate.
Reported spend is not an estimate of missing billing.
Pooled check, flag, and slot rates also carry `ci95_day_cluster_bootstrap`.
It uses 10,000 whole-day resamples with replacement and a fixed seed.
The record includes contributing days and dropped zero-denominator resamples.
Day-level rates use Wilson intervals only.

## Blind human packets for daily audits

Use the same private day tree:

```bash
python3 -S -m agent_runner.audit_packets .news-briefing/jev-stage \
  --start YYYY-MM-DD --end YYYY-MM-DD \
  --output-dir .news-briefing/jev-stage-packets \
  --private-key .news-briefing/jev-stage-correspondence.json
```

Both destinations must be new and outside the input tree.
The key must also be outside the packet directory. It receives owner-only permissions.
Share only `reviewer-primary.json` and `reviewer-double.json` with their respective reviewers.
Keep the correspondence key private until labels are frozen.
It contains source correspondence, outcomes, gaps, and the random seed.

The [independent-production protocol](../evaluator/protocols/independent-production-2026-10-02.json) defines partitioning and blinding.
Every verified changed slot, applied or withheld, appears as a pair in both packets.
Each reviewer receives independently shuffled records and pair sides.
Each side contains generated prose and its own frozen titles, excerpts, outlet names, and publication dates.
Packets omit model/provider metadata, scores, labels, outcomes, paths, original-side markers, and seeds.
Model names within news text remain unchanged.

Older packets omitted outlet names and dates.
Use `python3 -S -m agent_runner.audit_adjudication enrich PACKET ROOT --start ... --end ...` to enrich an adjudication packet.
It matches exact frozen titles and excerpts against verified runs without a correspondence key.
It preserves item IDs and A/B assignments.

For each day with complete initial review, select the first three unflagged included topics by the specified SHA-256 order.
The input is `jev-stage-v1` + report date + section + zero-based position, without separators.
The second reviewer receives the first third of selected controls, rounded up.
Partial initial reviews supply no controls.
The private key records the sampling frame and missing, partial, and failed days.
Invalid audits abort before output.

Reviewers assess support, meaning changes, retained/removed citation relevance, and readable complete sentences.
Controls receive the same applicable questions.
Each packet supplies the rubric, allowed values, and evidence-only instructions without identifying outcomes.
Reviewers return separate labels and a rationale for every item.
See the [reviewer instructions](results/jev-stage-reviewer-instructions.md) and [offline review page](results/jev-stage-review.html).

Validate labels without opening correspondence or writing files:

```bash
python3 -S -m agent_runner.audit_labels PACKET LABELS
```

Freeze both reviewers' labels and rationales before unblinding.
A third human adjudicates material disagreements from blinded evidence and rationales.
Report raw agreement and adjudicated outcomes separately with their denominators.

Export disagreements:

```bash
python3 -S -m agent_runner.audit_adjudication export \
  PACKET_R1 LABELS_R1 PACKET_R2 LABELS_R2 \
  --output-dir DIR --private-key KEY
```

Export validates labels and matches shared items by blinded content, without the packet correspondence key.
It records agreement per question.
The adjudication packet contains support, citation, or meaning-preservation disagreements with randomly ordered A/B label sets and rationales.
Its private key retains correspondence.
Use the [adjudication page](results/jev-stage-adjudicate.html) and validate the returned decisions with `audit_adjudication validate`.

Compute frozen outcomes:

```bash
python3 -S -m agent_runner.audit_adjudication outcomes \
  PACKET_R1 LABELS_R1 PACKET_R2 LABELS_R2 ADJUDICATION_PACKET DECISIONS \
  --key KEY --freeze-record RECORD
```

This validates all inputs against the freeze record and recomputes shared items.
Opening the key records `correspondence_opened_at`.
Any mismatch between the key and packet fails validation.
Agreed labels remain unchanged. Adjudicator decisions apply only to disputed questions.
The record separates pair and control counts with exact denominators.
Optional flagged counts remain separate from unfiltered counts.

Add `--correspondence PACKET_KEY` to record `outcomes.pairs_by_side` for original and repaired versions.
Every shared pair must map to exactly one original and one repaired side in both packets after alignment.
The record retains the key hash and first `packet_correspondence_opened_at` timestamp.
Reruns reject changed key hashes or omission of a previously supplied key.
These commands export and validate evidence; they do not supply human labels or approval.
Keep packets, excerpts, labels, and correspondence private and ignored.
