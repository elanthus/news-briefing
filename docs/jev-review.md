# Daily Jev checks and repairs

After the fallback chain selects a ready briefing, the daily publisher requests a
Jev review, confirms each initial flag in isolation, and attempts one bounded HY3
repair round for confirmed included citation, grouping or prose problems. Accepted
repairs update the public report; the original run remains immutable. The existing
`OPENROUTER_API_KEY` authenticates both models.

The public integrity report shows the publication decision and action outcomes
before detailed statistics. It identifies skipped and failed repairs as well as
accepted and rejected candidates. Actual headline and summary differences appear
once per story, even when citation review triggered the repair. Unchanged prose
is omitted; removal and repair metadata remain visible. Initial and follow-up
review coverage, isolated confirmations, unresolved flags, and billing uncertainty
are reported separately. These are automated excerpt judgments, not human
verification.

The reviewer uses OpenRouter's Decisions API with `typesafe/jev-1.13` and the existing
`OPENROUTER_API_KEY`. It requires no TypeSafe key or third-party Python package. Jev
does not write prose or provide free-text rationales.

## Review an existing run

Use a completed runner directory whose manifest identifies a `ready` final candidate.
For a daily fallback chain, use its selected candidate directory, recorded in
`fallback-log.json`, rather than the chain's parent directory.

```bash
python3 -S -m agent_runner.jev_review \
  --run-dir .news-briefing/runs/RUN \
  --output-dir .news-briefing/jev-reviews/RUN
```

The output directory must be new and separate from the source run; it cannot contain
or be contained by the source directory. This preserves the source run and its
checkpoint hashes. To review again with different settings, choose a new directory.

To request an advisory-only review immediately after generation, add
`--jev-review-dir .news-briefing/jev-reviews/RUN` to `run_briefing.py`. Review
runs only when generation reaches `ready`. Add `--confirm-flags` to the standalone
review command for isolated confirmation. The daily fallback-chain command
`run_daily_briefing.py --jev-review-dir DIR` confirms flags and applies bounded
repairs only after full acceptance; scheduled publication always enables this
stage. The redundant `--jev-repair-mode` flag is removed. The daily producer has
one apply policy; historical candidate-only artifacts are archived as research
evidence under the [retention contract](research-retention.md). Current publication
accepts only schema-3 per-slot audits.
The standalone review command returns zero for complete coverage and one for partial
or failed review, regardless of how many findings it flags.

## Confirmation and repair policy

An initial score at or above its threshold triggers a single recheck with only that
finding's exact frozen evidence, prose and rubric. A pair check retains exactly
its two topics. Citation relevance uses **0.60** by default; other checks use
**0.80**. Both scores are preserved: **confirmed** requires both to reach the
cutoff; otherwise the flag is **disputed**. If the recheck cannot return, the label
is **unconfirmed**, coverage is partial and no repair is attempted.

Confirmed included citation, grouping and prose findings trigger repair. Each
citation check sees one source's title/excerpt and the published story's prose,
with no neighboring sources. Code removes that exact source only when both scores
reach 0.60. This favors excluding questionable links. Disputed and lower-scoring
citations remain. If all sources in a slot are confirmed irrelevant, skip the
repair round and retain the original report; the nonempty-citation contract still
applies. Use `--citation-threshold` on the standalone reviewer to change its
cutoff. Duplicate findings and excluded-topic grouping flags remain advisory. A round repairs at
most four slots; more targets skip the entire round. HY3 keeps affected topics
in their existing positions and code preserves every other topic and the
exclusion log. No section reranking occurs.

After confirmed citation removals, grouping repair selects a nonempty subset of
a slot's own source items, using local integer indexes, then writes prose against the refrozen subset.
Prose-only repair preserves its original references and source order. Code
keeps each repaired slot's original headline and asks the model only for a
summary under it. A slot that lost a source item, by confirmed removal or a
smaller grouping subset, may receive a new headline, which the normal validator
checks like any other. The verifier rejects a changed headline on a slot whose
sources are unchanged. No model receives a URL or opaque citation handle. Each actual repair call is limited to
10,000 output tokens and 180 seconds; the round makes at most two calls through
the existing provider adapter. A prose-only round preserves selection in code,
so it needs just one paid call.

The normal runner validates and renders the complete candidate. A separate Jev
review then repeats citation, grouping and prose checks and duplicate comparisons
for the candidate. Its coverage must be complete and its costs known; otherwise no
slot is applied. Acceptance is then decided per slot: a repaired slot is applied
when every returned check involving its position is below its respective
threshold, and a re-flagged slot keeps its original prose and sources. A check
that names two positions blocks both. When every slot clears, the repaired run is
the publication input (`repaired_applied`). When only some clear, code assembles
a new run from the frozen original and the cleared slots through the normal
runner without a model call, revalidates it, and verifies that cleared slots
match the repaired run exactly and every other topic matches the original byte
for byte (`partial_applied`). The integrity record then lists the original,
candidate and published digests, and its action ledger records `apply_repair` or
`withhold_repair` for each repaired slot. If assembly or its verification fails,
every target is recorded as failed and the original is published; the audit
still keeps the follow-up scores, costs and the slots that had cleared. The assembled briefing is not reviewed
again: a cleared slot is not compared by a duplicate check with the original prose
of a withheld one. A singleton source no longer needs a grouping check; the
audit identifies that code-level scope change without inventing a Jev score.
Removed citations likewise have a removal outcome, not an invented follow-up
score. Retained citations are matched back to their original frozen indexes so
removing one cannot attach the next source's score to it.
Unrelated findings remain visible and do not prevent an otherwise cleared repair.

One round is the limit: rejected slots are retained in the audit with their
proposed prose, and the original ready briefing is published when no slot clears.
Failed or skipped repairs retain the original as well. Applying cleared slots
does not change the original fallback run or its checkpoints. Publication
revalidates input hashes, exact before/after prose, unchanged topics, source
subsets, per-slot acceptance and the ready repaired or assembled run before using
it. Audits written before per-slot acceptance (metadata versions 1 and 2) are
retired historical attachments; current publication rejects them.
An invalid audit is ignored and cannot replace the original publication input.

The repair instructions ask for concise complete sentences and forbid copying
truncated fragments. Code rejects empty strings, trailing ellipses, and summary
text without closing punctuation. Those checks catch some truncation errors;
they do not prove readability, truth or exhaustive support.

## Public audit and retained history

The public `semantic_audit` field contains bounded generated before/after prose,
positions, scores, labels, repair outcomes, and coverage/cost metadata. It never
contains frozen feed titles/excerpts or prompts. Canonical `followup_checks`
retain the actual returned follow-up questions, including blockers that were
below threshold initially. Source indexes stay aligned to the original frozen
selection. Removed citations and singleton grouping establish a code-owned scope
change; they do not receive invented Jev scores.

The versioned `integrity` record adds operational facts and references canonical
check records rather than duplicating story prose. The publication step verifies
artifacts and recomputes or cross-checks decisions, coverage, removals, costs, and
subjects. Invalid or tampered audits retain the original and expose only a safe
verification-failed explanation. Fixed allowlisted codes explain all applicable
skip causes, failures, and rejections. Raw exceptions, provider
bodies, private paths, prompts, and frozen excerpts remain private.

The report leads with publication and review status, then an action ledger,
then story differences and initial/follow-up statistics. Source items display
short destination labels; article and Hacker News links remain paired as one
source. Proposed changes are labeled separately from published changes. Every
returned check remains in public JSON. HTML escapes prose and highlights exact
word differences without interpreting model-authored markup. Definitions explain
probability thresholds, disputed/unconfirmed flags, missing confirmation, and
excerpt-only scope.

History schema 9 preserves valid integrity metadata through merges, replacement,
and rebuilds. Only schema 9 is accepted; schemas 7 and 8 are rejected. Current
entries may explicitly record `integrity: null` or `semantic_audit: null` when
unavailable. Missing operational history is never enriched by rerunning models.
Non-public dispositions remain status-only. Raw reports and failed traces stay
in encrypted diagnostics under `runs/DATE/jev-review/`.

## What is checked

| Check | Evidence and scope |
|---|---|
| Reworded duplicate | Compare frozen evidence between included topics and between included and excluded topics, across sections. Shared entities or themes alone are not duplicates; distinct developments are not duplicates. Excluded-versus-excluded pairs are omitted. |
| Citation relevance | For each included source, compare only its own frozen title/excerpt against the story headline and prose. Shared entities or themes alone do not make it relevant. |
| Unsafe grouping | For every included or excluded topic citing multiple evidence items, judge whether those items describe distinct developments that should be separate stories. |
| Unsupported claim | For each included headline and summary, judge whether any material factual assertion lacks support in its own frozen excerpts. |
| Strengthened claim | Judge whether the included prose increases certainty, scope, magnitude, or causality beyond its own evidence. |
| Reversed claim | Judge whether included prose contradicts or reverses its own evidence. |

The report includes redacted topic evidence and positions, the question rubrics, all
returned probabilities, per-call model identity, request hash, latency, reported cost, and input
artifact hashes. A score at or above `--threshold` (default `0.8`) flags a check;
citation relevance uses `--citation-threshold` (default `0.6`).
This is a provisional triage threshold, not a calibrated guarantee of correctness.
Several checks can flag one topic; `flagged_checks` is not a topic error rate.

Before calling Jev, code verifies artifact hashes, validates the corpus and structured
candidate, rebuilds selected evidence from the frozen selection and corpus, and checks
every final citation-reference array against its original section and position.
A repair that drops or reorders topics causes review to fail rather than attach prose
to different evidence. No URL or opaque citation/item handle is sent to Jev.

## Bounds and failure reporting

The reviewer accepts at most 64 included and excluded topics. It considers at most
1,000 duplicate pairs by default (`--max-pairs`, maximum 2,016); if capped, it prioritizes
headline word overlap and reports the omitted pair count. This candidate ordering
cannot identify all semantic duplicates.

Requests contain at most 50 questions and 24,000 serialized ASCII bytes. Questions
are batched with explicit field paths to their own evidence. A single oversized check
is skipped, with no evidence truncation. Responses are bounded to 128,000 bytes and
must contain exactly the requested Noul answer IDs with finite probabilities in
`[0, 1]`; unexpected response fields or answer types are rejected.

The default per-call timeout is 30 seconds, with at most 128 calls and a 120-second
overall review deadline. Each call's timeout is limited to the remaining review time. Calls are not
retried. The reviewer refuses redirects so authorization never follows another
destination, and never records remote error bodies.

`--cost-ceiling` defaults to USD 0.10 and is limited to USD 1.00. Before each Jev call,
serialized request bytes conservatively estimate input tokens at the documented
USD 0.042 per million input tokens; reported cost accumulates separately. This is an
estimated spending guard, not a provider-enforced hard cap. Pricing changes and
failed or interrupted calls can leave billing uncertain. The daily flow gives the
initial/confirmation review and the follow-up review separate USD 0.10 estimated
spending guards. HY3 generation stops if reported repair spending reaches USD
0.10 before another call; bounded output tokens limit individual calls, but this
is not a strict combined dollar cap. The audit sums all reported review and repair
costs and counts unknown-cost calls. No new call is made after an unknown cost. Missing cost stops further
calls; failed calls are retained with unknown billing. Already-returned results are
preserved when a later call fails.

`status: complete` means all planned checks returned validated answers within the
declared scope. It does not mean the briefing passed semantic review. Omitted pairs,
oversized checks, or an early stop produce `partial`; invalid source artifacts or
failure before any results produce `failed`. An interrupted review leaves `running`
and an `in_flight` call visible. Review reports are not resumable; do not automatically
repeat an interrupted request with ambiguous billing.

The reviewer sees feed excerpts, not full articles, and does not assess exclusion
reasons for grounding. Its judgments can be wrong or influenced by injected evidence.
The daily repair policy is a monitored preliminary rollout informed by local
experiments; those experiments do not establish calibrated thresholds. Continue checking
false alarms, missed errors and editorial losses in the public audit. The existing
deterministic gate remains mandatory.

## Sources and implementation

- [OpenRouter Jev tutorial and HTTP request/response examples](https://openrouter.ai/blog/tutorials/how-to-use-jev/)
- [OpenRouter model pricing](https://openrouter.ai/typesafe/jev-1.13)
- [`agent_runner/decisions.py`](../agent_runner/decisions.py): bounded Decisions API client.
- [`agent_runner/jev_review.py`](../agent_runner/jev_review.py): frozen-evidence verification, batching, isolated confirmation and reports.
- [`agent_runner/semantic_repairs.py`](../agent_runner/semantic_repairs.py): bounded repair patches and public audit projection.

## Aggregate current daily audits

Keep one `YYYY-MM-DD` directory for every sampled day under a private root,
including days with no ready generation or incomplete review. Declare the frozen
first and last sample dates with required `--start` and `--end` options. Absent
folders become `missing_day` rows; folders outside that range are rejected.
Each available day has `run/` (the ready run or daily fallback chain) and `review/` or `jev-review/` from
`run_daily_briefing.py --jev-review-dir`. Exactly one review directory is allowed.
For example:

```bash
python3 -S -m agent_runner.audit_metrics .news-briefing/jev-stage \
  --start YYYY-MM-DD --end YYYY-MM-DD \
  --output .news-briefing/jev-stage-metrics.json
```

Replace the date placeholders with the study's frozen sampling frame. The
aggregator verifies schema-3 envelopes and corpus dates through `load_public_audit`;
an invalid envelope, mismatched corpus date or duplicated audit aborts the export.
Metric calculation consumes only the public audit projection and parsed integrity record. The verifier alone reads the private
binding artifacts. Output contains no prose, destinations, model identity,
excerpts, requests, private metadata, or paths.

Every rate includes successes, trials and a two-sided 95% Wilson interval (null
for zero trials). Partial or failed initial/follow-up days and missing reviews
remain in the coverage denominator and in per-day counts and costs, but never in
conditioned rates. A failed fallback chain without review is a generation-failure
row. Per-slot acceptance is conditional on complete initial and follow-up review;
show coverage and excluded-day status counts alongside it. Slot cause denominators
are slots with that outcome; causes can overlap.
Follow-up blocker counts distinguish checks from affected slots, so a two-slot
check blocks both. Initial review costs include isolated confirmation; repair
and follow-up costs and unknown-cost calls remain separate per day. Reported
spend is never an estimate of unreported billing. Rates that pool checks, flags or
slots across days (flag rate by kind, confirmation rates, per-slot acceptance and
slot causes) also carry `ci95_day_cluster_bootstrap`: a percentile interval from
10,000 resamples of whole days with replacement under a fixed seed, with the
contributing day count and dropped zero-denominator resamples recorded beside
it. Wilson intervals alone do not account for within-day clustering; day-level
rates (coverage, days with flags, repair round rate, publication outcomes) carry
Wilson intervals only.

## Blind human packets for daily audits

Use the same private day tree as the aggregator:

```bash
python3 -S -m agent_runner.audit_packets .news-briefing/jev-stage \
  --start YYYY-MM-DD --end YYYY-MM-DD \
  --output-dir .news-briefing/jev-stage-packets \
  --private-key .news-briefing/jev-stage-correspondence.json
```

Both destinations must be new and outside the input tree; the key must also be
outside the packet directory. It is created with owner-only permissions. Share
only `reviewer-primary.json` and `reviewer-double.json` with their respective
reviewers. The key contains source correspondence, outcomes, coverage gaps and
the private random seed. Keep it private until labels are frozen.

Following the partitioning and blinding of
[`independent-production-2026-10-02.json`](../evaluator/protocols/independent-production-2026-10-02.json),
every verified changed slot (applied or withheld) is a pair in both packets.
Record order and the two sides of each pair are independently shuffled for each
reviewer. Each side carries only generated headline/prose and its own frozen
source titles, excerpts, outlet names and publication dates, so removed sources
remain identifiable by comparison and a rationale can name the outlet it relies
on. No model/provider identity, scores, labels, repair status, paths,
original-side marker, or shuffle seed is exported. News text itself is
preserved; model names that are part of a news story are not metadata and are
not censored. Packets exported before 2026-10-19 omitted the outlet name and
date; `python3 -S -m agent_runner.audit_adjudication enrich PACKET ROOT --start
... --end ...` adds them to an adjudication packet in place by matching each
item's frozen title and excerpt against the verified runs, without the
correspondence key and without changing item ids or the A/B assignment.

Per day with complete initial review, controls are the first three unflagged
included topics ordered by SHA256(`jev-stage-v1` + report date + section +
zero-based position), without separators. The second reviewer receives the first
third of that day's selected controls, rounded up. Partial initial reviews supply
no controls. The private key records the declared frame and missing, partial and
failed days, including dates with no folder at all. Dates must
match the verified frozen corpus. An invalid audit aborts before any output.

Both reviewers assess every pair for material-claim support, lost/added/changed
meaning, relevance of retained and removed citations, and readable complete
sentences. Controls receive the same support, relevance and readability review.
Each packet file carries the `rubric` block with those four questions, the
allowed label values, and the instruction to judge only from the supplied
evidence; it names neither side of a pair nor any outcome. Reviewers return a
separate label file with a `labels` object and a free-text `rationale` for every
item, in the format given in the
[reviewer instructions](results/jev-stage-reviewer-instructions.md), typically
through the offline [review page](results/jev-stage-review.html), and check it
with `python3 -S -m agent_runner.audit_labels PACKET LABELS`, which validates
coverage, per-version label counts, rubric values and rationales without the
correspondence key and without writing anything.
Retain both reviewers' labels and rationales before unblinding. A third human
adjudicates material disagreements from blinded evidence and rationales; report
raw agreement and adjudicated outcomes separately with their denominators.
`python3 -S -m agent_runner.audit_adjudication export PACKET_R1 LABELS_R1
PACKET_R2 LABELS_R2 --output-dir DIR --private-key KEY` validates both label
files, matches the shared items by their blinded content (never by the
correspondence key), records raw agreement per question, and writes an
adjudication packet holding only the items with a support, citation, or
meaning-preservation disagreement, with the two label sets and rationales shown
as A and B in a per-item random order; the private key maps each item back. The
adjudicator works in the offline [adjudication page](results/jev-stage-adjudicate.html)
and returns a decisions file that `audit_adjudication validate` checks.
`python3 -S -m agent_runner.audit_adjudication outcomes PACKET_R1 LABELS_R1
PACKET_R2 LABELS_R2 ADJUDICATION_PACKET DECISIONS --key KEY --freeze-record
RECORD` then computes the adjudicated outcomes: it validates every input, checks
each digest against the committed freeze record, recomputes the shared items by
blinded content, reads the key (the unblinding step, recorded as
`correspondence_opened_at`), refuses any key entry that disagrees with the
adjudication packet, keeps every agreed label, applies the adjudicator's
decision to each disputed question, and writes per-question counts with exact
denominators for repair pairs and controls into the record. Flags never change
those counts; when present, the counts with flags applied are recorded beside
them. Add `--correspondence PACKET_KEY` to open the packet correspondence key and
also record `outcomes.pairs_by_side` for original and repaired versions, each
with the shared-pair denominator. Every shared pair must map to exactly one
original and one repaired side in both packets, consistent after version
alignment. The record retains the key's SHA-256 and first
`packet_correspondence_opened_at` UTC timestamp on reruns.
These commands export evidence, not labels or human approval. All packets, excerpts,
labels and correspondence remain private and ignored.
