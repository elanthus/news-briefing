# Jev advisory review

Jev reviews the completed briefing for semantic problems that deterministic code
cannot prove. The daily publisher requests a review of the fallback chain's selected
ready candidate and retains `runs/DATE/jev-review/report.json` in the existing encrypted
diagnostics archive. Findings and API failures never change the briefing, publication
disposition, fallback model choice, or generation exit code.

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

To request a review immediately after generation, add
`--jev-review-dir .news-briefing/jev-reviews/RUN` to `run_briefing.py` or
`run_daily_briefing.py`. Review runs only when generation reaches `ready`.
The standalone review command returns zero for complete coverage and one for partial
or failed review, regardless of how many findings it flags.

## What is checked

| Check | Evidence and scope |
|---|---|
| Reworded duplicate | Compare frozen evidence between included topics and between included and excluded topics, across sections. Shared entities or themes alone are not duplicates; distinct developments are not duplicates. Excluded-versus-excluded pairs are omitted. |
| Unsafe grouping | For every included or excluded topic citing multiple evidence items, judge whether those items describe distinct developments that should be separate stories. |
| Unsupported claim | For each included headline and summary, judge whether any material factual assertion lacks support in its own frozen excerpts. |
| Strengthened claim | Judge whether the included prose increases certainty, scope, magnitude, or causality beyond its own evidence. |
| Reversed claim | Judge whether included prose contradicts or reverses its own evidence. |

The report includes redacted topic evidence and positions, the question rubrics, all
returned probabilities, per-call model identity, request hash, latency, reported cost, and input
artifact hashes. A probability at or above `--threshold` (default `0.8`) flags a check.
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

`--cost-ceiling` defaults to USD 0.10 and is limited to USD 1.00. Before each call,
serialized request bytes conservatively estimate input tokens at the documented
USD 0.042 per million input tokens; reported cost accumulates separately. This is an
estimated spending guard, not a provider-enforced hard cap. Pricing changes and
failed or interrupted calls can leave billing uncertain. Missing cost stops further
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
Evaluate probabilities and thresholds against blinded human labels before using them
to block publication. The existing deterministic gate remains the publication authority.

## Sources and implementation

- [OpenRouter Jev tutorial and HTTP request/response examples](https://openrouter.ai/blog/tutorials/how-to-use-jev/)
- [OpenRouter model pricing](https://openrouter.ai/typesafe/jev-1.13)
- [`agent_runner/decisions.py`](../agent_runner/decisions.py): bounded Decisions API client.
- [`agent_runner/jev_review.py`](../agent_runner/jev_review.py): frozen-evidence verification, question batching, and reports.
