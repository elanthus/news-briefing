# Jev plus Decider sensitivity v1 model card

This card measures how often the daily prose-support review catches a claim that its evidence does not
support. The [October Jev-stage study](jev-stage-v1.md) recorded 0 flags in 374 checks for each
prose-support kind, while human reviewers found support problems in 11 of 51 unflagged control topics.
It could not say how often the review misses a problem. This study plants known problems in real
briefing prose and counts how many the review catches.

On seeded edits, the current review missed 14 of 60 unsupported claims, 43 of 60 strengthened claims,
and 20 of 60 reversed claims after Decider confirmation. It confirmed a flag on none of the 60 untouched
controls. These are rates for deliberately planted, single-claim edits; they are not the miss rate on
the errors the daily briefing actually makes. See the [claims boundary](#claims-boundary).

Read the [evaluation methodology](../evaluation-methodology.md) before quoting any number from this card.

## The review under test

The [daily review](../jev-review.md) asks Jev (`typesafe/jev-1.14`) three prose-support questions for
every included topic. Each question shows the topic's headline, its prose, and the title and excerpt of
every cited item.

| Check | Question, abridged |
|---|---|
| `unsupported_claim` | Does the headline or prose assert a material fact unsupported by all of the evidence? |
| `strengthened_claim` | Does it strengthen certainty, scope, magnitude, or causality beyond the evidence? |
| `reversed_claim` | Does it contradict or reverse the meaning of the evidence? |

Jev returns a probability for each question. A probability of at least 0.8 is a flag. Decider
(`perplexity/pplx-decider-v1.2`, another vendor's model) is asked the same question about each flag on its
own. A flag counts as confirmed when Decider's probability is also at least 0.8. Only a confirmed flag
can trigger a repair of the slot. The rubrics are in [`agent_runner/jev_review.py`](../../agent_runner/jev_review.py).

The October study used Jev to confirm its own flags. This is the first measurement of the Jev plus
Decider configuration the daily service runs.

## Design

| Field | Value |
|---|---|
| Protocol | [`jev-sensitivity-v1.json`](../../evaluator/protocols/jev-sensitivity-v1.json), frozen and authorized 2026-11-19 before the measured run |
| Source commit | `6324aa5`, the commit that froze the protocol; the protocol pins the fixture, harness, rubric, and client hashes |
| Fixture | [`jev-sensitivity-seeds.json`](../../evaluator/fixtures/jev-sensitivity-seeds.json): 63 topics, 252 items |
| Measured | 60 topics, 240 items, 720 prose-support checks |
| Served releases | `typesafe/jev-1.14-20261014`, `perplexity/pplx-decider-v1.2-20261016` |
| Run window | 2026-11-19T23:39:30Z to 23:41:05Z, one run, no interruption |
| Calls | 52 Jev review calls; 178 Decider confirmations |
| Reported cost (USD) | 0.016668, no unknown-cost call; ceiling 1.00 |

**Topics.** Every topic is real model-written briefing prose from runs committed to this repository,
with the frozen title-and-summary evidence it cited: the 2026-08-17 run and four runs over the
2026-08-18 corpus. A fixed seed drew 63 of 68 story keys (news day plus the title of the first cited
item). The first 60 are measured. The last three
were reviewed in a dry run and are never pooled. Published daily briefings were not used: their frozen
excerpts are private by design, so a committed fixture cannot hold them.

**Seeded items.** Each topic has four versions of its prose. Headlines and evidence never change.

| Kind | Edit | Example (topic T15) |
|---|---|---|
| control | none | "…exemptions rose in 41 states and Washington, D.C., to 4.2%." |
| unsupported | one appended sentence asserting a material fact found in no excerpt | adds "Idaho recorded the highest exemption rate at 15 percent." |
| strengthened | one claim made more certain, broader, larger, or causal | "rose in 41 states" becomes "rose in every state" |
| reversed | one claim contradicted | "fell for every tracked vaccine" becomes "rose for every tracked vaccine" |

The fixture records each edit as an operation on the original prose, and `load_fixture` checks that
every item follows from its topic and edit.

**Batching.** Each item is reviewed in a pseudo-briefing like a daily one. The 240 items are split into
four 60-topic arms in a Latin square: topic *i* contributes kind (*i* + arm) mod 4. Every arm mixes the
kinds, and no request ever holds two versions of one topic. Checks are packed under the production limits
of 50 questions and 24,000 request bytes.

**Scoring.** An item is *detected* when any of its three checks is flagged by Jev and confirmed by
Decider, the condition under which the daily service can repair the slot. It is *missed* otherwise. Every measured item was completely
reviewed, so none is excluded.

## Results

Rates show `count/items; rate [95% Wilson interval]`.

| Kind | Items | Jev flags any check | Confirmed by Decider | Jev flags the matching check | Miss after confirmation |
|---|---:|---:|---:|---:|---:|
| control | 60 | 4/60; 6.7% [2.6, 15.9] | 0/60; 0.0% [0.0, 6.0] | not applicable | not applicable |
| unsupported | 60 | 50/60; 83.3% [72.0, 90.7] | 46/60; 76.7% [64.6, 85.6] | 49/60; 81.7% [70.1, 89.4] | 14/60; 23.3% [14.4, 35.4] |
| strengthened | 60 | 35/60; 58.3% [45.7, 69.9] | 17/60; 28.3% [18.5, 40.8] | 11/60; 18.3% [10.6, 29.9] | 43/60; 71.7% [59.2, 81.5] |
| reversed | 60 | 43/60; 71.7% [59.2, 81.5] | 40/60; 66.7% [54.1, 77.3] | 20/60; 33.3% [22.7, 45.9] | 20/60; 33.3% [22.7, 45.9] |

How to read it:

- **Strengthened claims are mostly missed.** Jev flagged 35 of 60, and Decider confirmed 17. The matching
  `strengthened_claim` check fired on only 11. The other flags came from `unsupported_claim`.
- **Reversed claims are usually caught as unsupported.** 40 were confirmed, but the `reversed_claim` check
  itself fired on 20 of 60.
- **Decider removed every control flag.** Jev flagged 4 controls; Decider confirmed none. It disputed 50
  of the 178 flagged checks in all. On 18 strengthened items it disputed every flag Jev raised.
- **Miss rates for Jev alone** are 10/60 unsupported (16.7% [9.3, 28.0]), 25/60 strengthened (41.7%
  [30.1, 54.3]), and 17/60 reversed (28.3% [18.5, 40.8]). Confirmation raises each miss rate.

Jev's probabilities rank the seeded item above its own control far more often than the 0.8 threshold
detects it. Per topic, the seeded version's highest probability exceeded the control's in 59 of 60
unsupported, 60 of 60 strengthened, and 56 of 60 reversed pairs, with no ties. Median highest
probabilities were 0.42 for controls, 0.89 unsupported, 0.81 strengthened, and 0.885 reversed. Of the 43
strengthened misses, 18 were flagged by Jev and disputed by Decider, 23 had a highest probability between
0.5 and 0.8, and 2 were below 0.5. This is an observation about these items, not a calibration result;
no threshold was tuned or evaluated on this data.

## What this changes in the October reading

The October study's 0 flags in 374 checks per kind could have meant the review cannot detect support
problems at all. This study shows it can detect most planted unsupported claims and most planted
reversals, and that it confirms almost none of the planted strengthenings. It does not tell us how
often the October briefings contained support problems the review missed. Real errors are usually
subtler than one planted sentence. Of the 11 control problems the October reviewers found, 7 were labeled
strengthened, 1 reversed, and 3 unsupported; strengthening is the kind this study misses most.

## Evidence and reproduction

The in-tree bundle [`jev-sensitivity-v1-evidence/`](jev-sensitivity-v1-evidence/) holds the measured
`results.json` (every probability, flag, confirmation, served model, request hash, and cost),
`summary.json`, the dry run's `dry-run-results.json`, and `SHA256SUMS`. Provider request ids are not
kept. The summary is regenerated from the results without credentials or provider calls:

```bash
python3 -S -m evaluator.jev_sensitivity summarize \
  --fixture evaluator/fixtures/jev-sensitivity-seeds.json \
  --results docs/results/jev-sensitivity-v1-evidence/results.json \
  --output /tmp/jev-sensitivity-summary.json
```

`evaluator/tests/test_jev_sensitivity.py` checks that the committed summary equals this regeneration and
that the bundle matches its checksums.

A dry run on the three held-out topics preceded the freeze, as the protocol records: 4 Jev calls,
11 Decider confirmations, USD 0.000728. Nothing changed between the dry run and the freeze.

## Limitations

- **Controls are untouched, not verified as supported.** October's reviewers found support problems in
  about one control topic in five. The control rate bounds false alarms on this prose; it does not isolate
  them.
- **The edits are planted.** A model wrote each one, one claim per item, to be clearly wrong against the
  excerpts. They are not drawn from real briefing errors and are not graded by difficulty.
- **The topics are not independent.** They come from two news days and five runs, and several stories
  recur with different prose. The Wilson intervals treat the 60 topics as independent, which overstates
  the effective sample size.
- **Only the three prose-support checks ran.** The daily review asks grouping, citation-relevance, and
  duplicate questions in the same batches, so its requests are mixed differently.
- **Evidence is titles and excerpts.** Support is judged against the frozen excerpts, as in production,
  not against full articles.
- **Provider behavior can change.** Both models are pinned by slug, and the served dated releases are
  recorded above.

## Claims boundary

This card may claim: for the 240 seeded items at the recorded revision and served releases, the rate at
which Jev flagged and Decider confirmed a prose-support problem for each seeded kind and for untouched
controls, with Wilson intervals; Jev-alone rates; the matching-check rates; and the paired probability
comparison with each topic's control.

This card may not claim: the daily review's miss rate or false-alarm rate on real briefing errors; that
untouched controls are supported, or that 0/60 is a false-alarm rate; that the review's precision,
recall, or calibration generalizes beyond these planted single-claim edits, these two news days, or
these served releases; that a different threshold would perform better (no threshold was evaluated);
or that the October briefings were, or were not, free of unflagged support problems.
