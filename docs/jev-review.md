# Daily Jev checks and repairs

After the fallback chain selects a ready briefing, the daily publisher requests a
Jev review, confirms each initial flag in isolation, and attempts one bounded HY3
repair round for confirmed included citation, grouping or prose problems. Accepted
repairs update the public report; the original run remains immutable. The existing
`OPENROUTER_API_KEY` authenticates both models.

The public integrity report summarizes counts by check category. It shows each
corrected story's before/after prose once, with the associated scores and repair
outcome. Citation-only removals appear as struck-through links with scores and
status. Unchanged stories and top-score examples are omitted. Full public check
records remain in history JSON. Frozen excerpts, prompts and call traces remain
in encrypted diagnostics. Missing coverage is explicit. See the
[preliminary evaluations](results/jev-preliminary-2026-09-30.md).

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
`--jev-review-dir .news-briefing/jev-reviews/RUN` to `run_briefing.py` or
`run_daily_briefing.py`. Review runs only when generation reaches `ready`. Add `--confirm-flags` to the standalone review command for isolated confirmation.
For the daily fallback-chain command, add `--jev-repair-mode apply` with
`--jev-review-dir` to enable automatic repairs; `--jev-repair-mode candidates`
retains candidates without applying them. Scheduled publication uses `apply`.
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
Prose-only repair preserves its original references and source order. No model
receives a URL or opaque citation handle. Each actual repair call is limited to
10,000 output tokens and 180 seconds; the round makes at most two calls through
the existing provider adapter. A prose-only round preserves selection in code,
so it needs just one paid call.

The normal runner validates and renders the complete candidate. A separate Jev
review then repeats citation, grouping and prose checks and duplicate comparisons
for the candidate. Its coverage must be complete, costs known, and all checks involving
an affected position must be below its respective threshold before the repaired
run becomes the publication input. A singleton source no longer needs a grouping check; the
audit identifies that code-level scope change without inventing a Jev score.
Removed citations likewise have a removal outcome, not an invented follow-up
score. Retained citations are matched back to their original frozen indexes so
removing one cannot attach the next source's score to it.
Unrelated findings remain visible and do not prevent an otherwise cleared repair.

One round is the limit: a rejected candidate is retained in the audit, with the
original ready briefing published. Failed or skipped repairs retain the original
as well. Applying an accepted candidate does not change the original fallback
run or its checkpoints. Publication revalidates input hashes, exact before/after
prose, unchanged topics, source subsets and the ready repaired run before using it.
An invalid audit is ignored and cannot replace the original publication input.

The repair instructions ask for concise complete sentences and forbid copying
truncated fragments. Code rejects empty strings, trailing ellipses, and summary
text without closing punctuation. Those checks catch some truncation errors;
they do not prove readability, truth or exhaustive support.

## Public audit and retained history

The public `semantic_audit` field contains bounded generated before/after prose,
positions, scores, labels, repair statuses and coverage/cost metadata. It never
contains frozen feed titles/excerpts or model prompts. HTML treats all prose as
plain escaped text; citation links come from validated code-owned destinations,
never a model. Every returned check remains in public JSON. The HTML page shows
counts (below threshold, confirmed, disputed, unconfirmed, requiring repair) for
each check category. Detail entries show only grouping/prose corrections, grouped
by story so multiple findings do not repeat its prose. Removed citations are
struck through; rejected or advisory candidate removals are labeled as proposals,
so they are not mistaken for changes to the published briefing. Older audits
remain readable and categories without returned checks show zero counts. Raw
reports and failed private traces stay in `runs/DATE/jev-review/` in encrypted
diagnostics.

The site writes history schema 8 and migrates the existing schema-7 archive,
adding a null semantic audit to old entries. Audit data survives history merges,
manual replacement and subsequent rebuilds, and appears only beside a public
ready briefing or review preview. Non-public dispositions retain no generated
semantic prose.

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
The daily repair policy is a monitored preliminary rollout authorized after the
local experiments; it does not establish calibrated thresholds. Continue checking
false alarms, missed errors and editorial losses in the public audit. The existing
deterministic gate remains mandatory.

## Sources and implementation

- [OpenRouter Jev tutorial and HTTP request/response examples](https://openrouter.ai/blog/tutorials/how-to-use-jev/)
- [OpenRouter model pricing](https://openrouter.ai/typesafe/jev-1.13)
- [`agent_runner/decisions.py`](../agent_runner/decisions.py): bounded Decisions API client.
- [`agent_runner/jev_review.py`](../agent_runner/jev_review.py): frozen-evidence verification, batching, isolated confirmation and reports.
- [`agent_runner/semantic_repairs.py`](../agent_runner/semantic_repairs.py): bounded repair patches and public audit projection.
