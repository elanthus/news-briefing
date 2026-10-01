# Preliminary Jev evaluations: grouping and prose repairs

September 30, 2026. These exploratory evaluations support trying monitored automatic
repairs in the daily GitHub Pages workflow. They do **not** establish production
precision, recall, calibrated probabilities, or independently verified factual accuracy.

Jev (`typesafe/jev-1.13`, served through OpenRouter) judged story grouping,
reworded duplicates and prose support. Tencent HY3 (`tencent/hy3`) generated the
local briefings and repair candidates. Code constrained the evidence and validated
all candidates. Findings remained advisory throughout these experiments; none of
the experimental candidates changed a published report.

## Results

| Evaluation | Scope | Observed result |
|---|---|---|
| Published archive review | Seven reports dated September 24–30; 5,511 text-only grouping/duplicate checks | Nine grouping flags at 0.80, of which manual inspection considered eight plausible and one an apparent false alarm. No duplicate flags. A visibly repeated event scored 0.75 and was missed. |
| Repeated local generation | Ten HY3 reports from the same September 30 corpus; 220 included topics | All reports passed the normal publication contract. Jev returned 8,641 of 8,651 planned checks, with 21 initial flags. |
| Isolated confirmation | Each of those 21 flags, with its exact frozen evidence and prose and the same rubric | 17 confirmed, four disputed. Confirmed findings included 12 grouping problems in included slots, four in exclusion entries and one already-handled duplicate. |
| Grouping repairs | The 12 confirmed included grouping problems, spread across seven reports | One bounded HY3 repair round per affected report. All 12 follow-up grouping scores fell below 0.80 (0.05–0.10). All 593 targeted follow-up checks returned, with no flagged grouping, prose or duplicate findings. |
| Prose challenge set | 12 faithful controls and 36 injected errors, spanning 12 events | All 36 errors were confirmed; no faithful control was flagged. Unsupported facts, overstated claims and reversed meaning each contributed 12 errors. |
| Prose repairs | One HY3 rewrite for each of the 36 confirmed injected errors | 34 rewrites had all three post-repair scores below 0.80. The remaining two flags appeared unsupported on manual inspection of the supplied evidence. |
| Original prose review | All 220 included topics from the ten original reports | No confirmed prose findings. A manual spot check nevertheless found a missed agency-attribution error. Three controlled repairs also copied incomplete feed sentences. |

“Confirmed” means both the initial score and the isolated recheck reached 0.80.
Both scores were retained. It means repeat agreement from the same judge, not
independent verification. A high first score followed by a lower recheck was
“disputed”; disputed findings did not trigger repair.

## Corpus and generation

The local corpus contained 243 retained items from the 24-hour window ending at
2026-09-30 20:45 UTC. Its SHA-256 was
`4cee2b504aaf5cc61381ef79756d68dafcc0e4844eca24faad33bab281fd8e81`.
The corpus reported degraded coverage, including a Politico fetch failure. Each
local report had 22 included topics. HY3 used temperature 0.2 and high reasoning.

The published-archive review had only rendered text. It could compare story
boundaries and duplicates but could not check prose against frozen evidence. The
local reviews verified the ready run's artifact hashes and citation alignment,
then judged only the position-specific frozen excerpts. No model received URLs.
One original local review left ten checks unresolved after an ambiguous provider
failure; that paid request was not repeated. Supplemental reviews covered only
checks that had not been sent.

## What changed during grouping repair

Each flagged topic kept its existing slot. HY3 selected a nonempty subset of that
slot's original sources for one event, then wrote prose against the reduced,
refrozen evidence. Code preserved all unaffected topics and the exclusion log.
Normal selection, output and citation checks validated every repaired candidate.

The trial removed 27 source items from the affected slots. Those removals were
listed in the local audit, rather than reranking the sections or moving the
removed events into new slots. The resulting prose was more coherent, but this
approach can remove an important story. Low post-repair grouping scores do not
measure editorial completeness or ranking quality.

## What the prose trial exposed

The challenge set used hand-authored faithful prose and injected unsupported
facts, stronger certainty/scope, or reversed meaning. Four variants of each event
shared the same frozen evidence. These are correlated, deliberately conspicuous
examples, not a representative sample of naturally occurring model errors.

The two remaining post-repair flags concerned a Channel-smuggling story. One
unsupported-claim score was 0.83 / 0.80 (confirmed); the other was 0.80 / 0.79
(disputed). Manual inspection found the repaired statements supported by the
frozen excerpts. The scores and automated labels were preserved rather than
rewriting repeatedly until the judge accepted them.

In an original report, a coordinated sentence attributed overseas voting-form
changes to the Justice Department; the evidence attributed those changes to the
Pentagon. Jev returned 0.29 for unsupported claims, 0.41 for strengthened claims
and 0.06 for reversed claims. The automated threshold missed the error. The
original-prose review lacks exhaustive independent labels, so it cannot supply
an overall miss rate.

Three controlled rewrites copied truncated excerpt fragments, including a
midword ending and an unfinished phrase. Grounding rubrics did not detect these
readability problems. The daily repair prompt now explicitly requests complete,
concise sentences and avoids truncated fragments. Code rejects empty prose,
trailing ellipses and summaries without closing punctuation; that catches some
failures but cannot prove sentence completeness or readability.

## Cost and retained evidence

Reported costs were approximately $0.110 for the seven published reports,
$0.530 for ten local generations plus review and isolated confirmation,
$0.054 for grouping repairs, and $0.094 for prose evaluation and repairs. The local
sequence totaled $0.678, with a further $0.01 reserve for an uncertain charge,
within its $4 budget. These are measured provider-reported costs for this run,
not forecasts or provider-enforced spending limits.

Detailed local evidence remains under these ignored experiment directories:

- `.news-briefing/jev-evaluation-2026-09-30/`: published-text review and report.
- `.news-briefing/hy3-10x-2026-09-30/`: original reports, verified run artifacts,
  review supplements, isolated confirmation and manual assessments.
- Its `grouping-repairs/` directory: selection/prose patches, repaired ready runs,
  removed-source records and follow-up checks.
- Its `prose-evaluation/` directory: labelled examples, individual scores,
  rewrites, budget ledger and manual spot-check notes.

Private corpora, frozen feed excerpts and raw model traces are not committed or
published with this summary. The experiment protocols and aggregate results are
recorded here; this document is not a public reproducibility bundle.

## Daily workflow decision

The user authorized repairing public daily reports while monitoring the audit
results over time. The daily workflow therefore confirms initial flags, attempts
one bounded HY3 repair round for confirmed grouping/prose findings, validates
it, and applies it only after a complete follow-up review clears the affected
slots and their duplicate comparisons. Unrelated topics retain their positions
and prose. Duplicate findings and exclusion-entry grouping findings remain
visible advisory checks; they do not trigger slot deletion or reranking.

The public integrity report shows original and changed generated prose for each
returned check, initial and confirmation scores, follow-up scores, and whether
a repair was applied, rejected, failed or skipped. Frozen feed excerpts and model
prompts remain private. Partial reviews or uncertain costs retain the original
briefing. This is a monitored improvement to model judgment; the deterministic
publication contract remains mandatory.

See [daily semantic checks and repairs](../jev-review.md) for commands, bounds and
failure handling, and the [publication archive contract](../publication-archive-contract.md)
for public history retention.
