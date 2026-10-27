# Publication archive contract

This document defines scheduling, publication, retention, and public history for the GitHub Pages archive.
[`prepare_publication.py`](../prepare_publication.py) verifies publication inputs.
[`build_site.py`](../build_site.py) applies rendering and retention rules.

## Scheduling and corpus windows

The [daily workflow](../.github/workflows/daily-briefing.yml) runs at 13:30 UTC.
It labels reports with the current `America/New_York` date.
All modes check out `main` and use its merged code, prompts, and configuration.

| Mode | Target dates | Existing successful reports |
|---|---|---|
| Scheduled | Today | Apply the publication-rank safeguard. |
| Manual `single-day` | Today by default, or one explicit date inside the fourteen-date retention window | Replace successful reports for the target date. |
| Manual `backfill-7-days` | Today and the six prior Eastern dates | Replace successful reports for the target dates. |

A historical single-day run does not regenerate other archive dates.
The workflow captures one start timestamp.
Today's corpus covers the exact preceding 24 hours and is fetched fresh.
Earlier target dates use exact restored current-schema corpora.
If a historical corpus is missing, the workflow skips that date. It does not reconstruct it from live feeds.

A missing or expired archive starts a fresh retention window.
An unreadable archive fails restoration.
Each date has its own corpus, run directory, and report path.
Capture-time changes can create small overlaps or gaps between daily windows.
The first rolling window can also overlap an earlier calendar-day corpus.

Only encrypted operational archives leave the workflow as corpus artifacts.
The public builder emits text-free audit manifests instead of raw corpora.

## Repair, correction, and publication

### Precedence: deterministic repair before model correction

Code handles safe repairs before spending model corrections.
Permitted repairs remove complete ineligible or repeated entries and trim eligible over-limit tails.
Included stories take priority over exclusion logs.
Unknown evidence and malformed entries remain available for rejection; repair never removes them merely to pass validation.

A safe `claim_exceeds_evidence` repair substitutes deduplicated cited evidence for an oversized summary.
Every citation must have complete known support, and the normalized excerpt must contain no URL.
The action is `replace_summary_with_excerpt`. Rendering marks the topic `[verbatim]`.
Incomplete or URL-bearing evidence remains unresolved and can require review.
A Jev repair or assembly keeps the action, and the tag, on every slot it does not rewrite.
The published `repair_actions` list then names those inherited actions.

Before freezing evidence, promotion can fill unused section slots from that section's ordered accountability log.
The candidate is revalidated. A promotion that introduces a blocking finding is discarded.
The runner records `promote_excluded_entry` in a `selection_promotion` attempt.
Rendering adds `[promoted from the accountability log]`.
Promotions do not count as repairs. A resulting short log can still produce `exclusion_log_short`.

Selection schemas list section-eligible handles. Each handle retains its article and discussion destinations in the code-owned map.
After validation, code freezes the references by position.
The prose pass receives only selected evidence and has no citation field.
Code restores references after prose generation and checks the complete candidate.

Model correction handles remaining errors such as unknown references, leaked handles, destinations, invalid shapes, or rendered-contract failures.
Each eager repair returns to validation and preserves the remaining correction allowance.
After that allowance is exhausted, code can still apply a permitted final repair.
See [design notes](design.md#the-code-owned-runner) for stage decisions and limits.

### What reaches the public page

A `ready` result can publish only the manifest's `final.md` with its verified SHA-256.
A `review_required` result can retain its verified `preview.md` in publication metadata.
The public page displays a quarantine stub, not that preview.

Every status chip links to `reports/<date>.html`.
Briefing pages end with a "How this briefing is made" section: two paragraphs, the pipeline diagram (`pipeline.svg`, copied from `docs/images/runtime-pipeline.svg`), and links to the README, design notes, ADRs, evaluation methodology, evidence bundles, and Jev documentation.
Every page sends no referrer to the hosts it links, declares the share card `og-card.png`, and links the 192-pixel favicons.
A ready briefing closes with one line naming the generating model, the generation wall clock, and the recorded model charges, when any of them is recorded.
Integrity reports show the publication decision and reason, that same generation line, published changes, flagged stories, exclusion log, and affected sources.
Each figure states its own gap, so a missing value never reads as zero:

- A published day without provenance reads "The generating model was not recorded", followed by "; generation took …" when timing exists.
- Missing timing reads "Generation time was not recorded."
- A cost list without a generation row reads "for review; the generation charge was not recorded".
- A phase whose billing never arrived reads "with the generation charge not reported"; calls of unknown cost read "plus N calls of unknown cost".
- A recorded zero is printed as "$0.0000".

A published day with none of these recorded shows no line.
An unpublished day's report always carries the line, opening "No model produced this briefing", because the cost of a failed day is what a reader most needs.
Collapsed operator sections contain statistics, actions, scores, phases, costs, and hashes.
Ready pages have no inline review panels.
Their accountability log appears once in the integrity report, with a link from the briefing.
Review-required reports do not display the withheld preview or its accountability log.
Complete preview and finding context can remain in public history JSON.

Nonblocking quality notes do not contribute to `findings_count`.
These checks appear in `advisory_findings`, report details, and advisory counts:

- `slots_underfilled`.
- `exclusion_log_missing`.
- `exclusion_log_short`.
- `low_claim_evidence_overlap`.

`unsupported_figure` and `figure_supported_elsewhere` remain private run diagnostics and evaluation signals.
They do not appear in public advisory counts or the reader's Run outcome warning list.
The warning list displays "None" when no other warning remains.

Public `repair_actions` describe the final attempt only.
The integrity ledger separately identifies superseded generation actions without exposing earlier private prose.
The provenance repair total includes repairs across the original generation run.

`rejected`, `blocked`, and `no_result` entries contain status information only.
An exhausted fallback chain produces fixed public explanations for each model failure.
[`publication_failures.py`](../publication_failures.py) allowlists model identifiers and reason codes.
Raw exceptions, rejected prose, and story details stay private.

Fallback logs use schema 2 and failure records use schema 1.
Unversioned records are rejected. Finalized checker outcomes take precedence over earlier recoverable provider errors.
Public projection ignores private `failure_reason` text.
Unknown, malformed, missing, or old string-only records receive a generic explanation.
A status-only manual failure preserves an existing published page.
Encrypted diagnostics retain dated corpora, reports, and verified run directories for fourteen days.

## Site rendering and retention

The newest retained report appears on the home page.
The date bar links to the other retained reports.
Valid corpus-health JSON becomes a readable summary grouped by source type and status.
Stored Markdown retains the checked JSON. Malformed blocks remain escaped text.

The site and `history.json` retain up to seven report dates.
The builder removes the oldest entries only when more than seven exist. Date gaps do not remove history.

When the matching private corpus is available, its report links to `manifests/<date>.json`.
Audit-manifest schema 1 records:

- Corpus and item identities.
- Report and window timestamps.
- Item category, source, and canonical article/discussion URLs.
- SHA-256 hashes of exact UTF-8 title and optional excerpt bytes.
- The complete private corpus file hash.

Manifests contain no title or excerpt text.
The builder removes stale `site/corpora` before rendering.

## Operational metrics ledger

[`site_metrics.py`](../site_metrics.py) creates `metrics.json` and `metrics.html` without model calls or network access.
The ledger keeps one row per report date and is never truncated.
Rows come from all merged entries before the seven-date history limit applies.
The footer and "Behind this briefing" panel link to the metrics page.
The generation line at the foot of a briefing names the model, the wall clock, and the charges for that date only.

The workflow downloads prior metrics and history with bounded curl requests, `Cache-Control: no-cache`, and a per-run query parameter.
These controls reduce stale CDN responses.
A current row replaces a prior row only when its publication rank is at least as high.
Manual `--replace-existing` follows the same replacement policy as history.
A blocked or review-required retry cannot replace a ready date through the normal rank rule.

`build_site.py` requires `--prior-metrics` unless `--allow-empty-metrics` is explicit.
`--allow-empty-history` does not permit an empty metrics ledger.
The workflow enables empty metrics only after a definite HTTP 404.
It then initializes the ledger from retained history.
Other download failures and malformed prior ledgers stop the build.
The builder validates the merged ledger before writing output.

Ledger schema 2 has the fields below and no others.
Schema 1 rows upgrade with null `generation_cost_usd` and `generation_unknown_cost_calls`.
Invalid provider/model identifiers become null.
Rows contain no feed text, generated prose, finding messages, URLs, or hashes.

| Field | Meaning |
|---|---|
| `date`, `disposition` | Report date and publication disposition. |
| `provider`, `model`, `attempt_index`, `attempt_count` | Selected generation identity and fallback position from provenance. |
| `fallback_used`, `failed_attempts` | Selected index above 1 and prior attempts. Blocked days use the generation-failure count. |
| `review_status`, `followup_status`, `review_complete` | Integrity review status, with semantic-audit fallback. Completion refers to the initial review. |
| `repair_decision` | `original_retained`, `repaired_applied`, `partial_applied`, or `unpublished`. |
| `generation_cost_usd`, `generation_unknown_cost_calls` | Per-date generation spend across all attempts and calls without known billing. |
| `review_stage_cost_usd`, `phase_costs_usd`, `unknown_cost_calls` | Aggregate semantic-audit cost and separate initial-review, repair-generation, and follow-up costs. |
| `flags_raised`, `flags_confirmed` | Initial checks with a flag and those labeled `confirmed`. |
| `findings_count`, `advisory_findings` | Actionable findings and advisory count. |

Missing source fields remain null and display as "unknown". They never become zero.
The page reports ready rates, selected models, fallback use, repair outcomes, and known costs with unknown billing marked.
The 28-day rolling ready rate excludes dates without records from both counts.
These operational summaries do not measure independent grounding or editorial quality.
Charts are escaped inline SVG. The page loads no script.

## Reader retirement and research evidence

Current readers require history schema 10, integrity version 2, the complete sidecar shape, semantic-audit schema 3, and versioned failure records.
The October 15, 2026 cutover to schema 9 preserved inspected public Markdown without clearing history.
The October 26, 2026 cutover to schema 10 adds `generation_timing` to the integrity record and bumps it to version 2.
A deployed schema 9 history is refused, not upgraded, so every build after that cutover fails until the archive restarts.
Restart it with a manual `single-day` run and `allow_empty_history: true`.
That input skips the prior-history download and builds the archive from the run's own dates; it does not merely tolerate a failed download.
Scheduled runs never set it, so they merge into the restarted history from the next day on.
The metrics ledger keeps every earlier date's row, because it is merged from its own prior copy, not from history.
The earlier briefings leave the site; their run directories stay in the encrypted diagnostics archives for fourteen days, and their sidecars can be rebuilt with `prepare_publication.py` at a commit after the cutover.
Historical audits use a private dataset tied to their source revisions.
They are not replayed as current publication inputs.

Preservation retains original bytes, repair correspondence, failures, costs, and missing facts.
Frozen public benchmark bundles retain their original source revisions.
Encrypted operational archives are source evidence, not newly verified research samples.
See [research retention](research-retention.md) for verification and recovery.

## Private operational artifacts

The workflow requires `CORPUS_ARCHIVE_PASSPHRASE`.
It gzip-compresses corpora and diagnostics, then encrypts them with AES-256-CBC and a PBKDF2-SHA-256-derived key.
A separately derived HMAC-SHA-256 authenticates the versioned envelope.
Restoration verifies the MAC before decryption.
The passphrase enters through stdin, not process arguments.
Checkout uses `persist-credentials: false`.

| Step | Explicit secret bindings |
|---|---|
| Restore | `GITHUB_TOKEN`, `CORPUS_ARCHIVE_PASSPHRASE`. |
| Generate and fetch | `OPENROUTER_API_KEY`, `SCRAPECREATORS_API_KEY`. |
| Encrypt | `CORPUS_ARCHIVE_PASSPHRASE`. |

Job permissions are `actions: read`, `contents: read`, `pages: write`, and `id-token: write`.
They apply to every step. Actions does not isolate permissions through these environment bindings.
Any step can access `github.token` or request an OIDC token.

The online restore path selects eligible main-branch, same-repository corpus artifacts.
The local preserved-wrapper path requires the same workflow provenance.
Authenticated download follows only an absolute HTTPS artifact redirect. The redirected request carries no GitHub token.

Restoration accepts only regular `corpora/YYYY-MM-DD.json` members.
Current corpora must validate and have a `report_date` matching the filename.
Traversal paths, unexpected members, duplicate dates, oversized data, and invalid current corpora fail restoration.
Authenticated obsolete positive-integer schema versions are skipped, without upgrade.
Their date identities, uniqueness, and byte bounds still receive checks.
Missing, malformed, and future versions fail restoration.
An all-obsolete archive produces an empty window.

Members outside the retained window receive the same checks.
Only current corpora within the fourteen report dates ending on the requested date are written.
The output directory must be absent or empty, even when no archive is available.
See [offline wrapper restoration](research-retention.md#restoring-preserved-local-wrappers) for local recovery.

Ciphertext artifacts and exact corpora retain fourteen report dates.
Encrypted artifacts from this public repository can be publicly downloadable.
Confidentiality depends on the secret passphrase and authenticated envelope.
Rotating or deleting the passphrase makes prior artifacts unreadable.
Before rotation, re-encrypt retained archives or explicitly accept the resulting backfill gap.

## Generation provenance

A public artifact can carry `provenance` with these fields:

- Provider and exact model identifier.
- Fallback `attempt_index` and `attempt_count`.
- `selection_corrections` and `prose_corrections`.
- `repair_action_count` across the original run, including superseded repairs.
- `prompt_sha256` from the manifest identity.

Provenance contains identifiers, counters, and a hash, but no prompt text, corpus text, or URL.
`prepare_publication.py` derives it from the selected manifest and optional fallback log.
Standalone runs use index and count 1.
Missing recorded provider identity or prompt hash produces null provenance.
A disposition without a public artifact also has null provenance.

The integrity report displays provenance in its collapsed "Provenance and costs" section.
It omits the attempt-position clause for standalone runs.
The reader page shows only the model label, the generation wall clock, and the model charges, at the foot of the briefing.
Chain position, correction counts, and the prompt hash stay in the report.

## Daily semantic repair and audit

The daily stage reviews the ready generation and confirms flags in isolation.
Confirmed included citation, grouping, or prose findings can trigger one bounded HY3 repair round.
Follow-up must have complete coverage and known costs before any slot can apply.
Each applied slot must clear all checks involving its position, using the appropriate threshold.
A two-slot check can block both positions. Unrelated findings remain visible without blocking a cleared slot.

All cleared slots produce `repaired_applied`.
Some cleared slots produce a separately assembled and validated `partial_applied` run.
Otherwise, the original ready report remains selected.
Original generation artifacts remain unchanged.
See [Jev acceptance rules](jev-review.md#confirmation-and-repair-policy), including the limits of partial assembly.

Public `semantic_audit` retains generated before/after prose, actual scores, confirmations, outcomes, and costs.
It excludes frozen excerpts and prompts.
Publication verifies source and candidate artifacts, retained-source index correspondence, removed citations, unchanged topics, and per-slot decisions.
An invalid audit cannot replace the original or expose unverified audit details as applied.

HTML displays each changed headline or summary once per story.
Source removals identify confirmed irrelevance, grouping subset selection, or both.
Article and Hacker News discussion destinations count as one source item.
Proposed changes remain distinct from published changes. Missing scores are never invented.

Generation provenance describes the original selected fallback run.
`integrity.repair_generation` separately identifies the repair provider, model, and prompt hash.
Jev judges evidence; HY3 writes repairs.
Initial review, repair generation, and follow-up have separate costs that reconcile with the semantic audit.

The `generation` phase adds all per-attempt cost shares for that date, including failed and quarantined attempts.
It does not use the running backfill total.
Standalone generation uses its fresh manifest budget.
A missing attempt share makes the date's generation cost unknown; a partial sum cannot be reported as a total.
Generation costs do not participate in semantic-audit cost reconciliation.
Original correction counters exclude semantic repair work.

## Machine-readable history

The builder accepts only `history.json` schema 10.
Each entry requires every field below. Missing and extra fields fail validation.
The builder also writes `reports/<date>.json` for each retained date: that date's history entry plus `schema_version` 1.
Each integrity report links its own record first, so a reader opens one day's data rather than the seven-day file.
`parse_day_record` in `build_site.py` accepts only schema 1 with exactly the history fields and runs before the record is written.
Schema 1 means the history field set listed below; a change to that set bumps `DAY_RECORD_SCHEMA_VERSION` together with `HISTORY_SCHEMA_VERSION`, and a test pins the pairing.
The records are rebuilt from `history.json` on every build; nothing reads them back.
Current sidecars use the same metadata fields, including explicit nullable integrity and audit values.
Rebuilding never calls a model to fill historical gaps.
Recorded legacy provenance remains as recorded; code does not infer missing original-versus-repair identities.

| Field | Contents |
|---|---|
| `date` | Eastern report date. |
| `disposition` | Publication disposition. |
| `findings_count` | Actionable findings, excluding quality advisories. Zero on an infrastructure failure does not mean acceptance. |
| `findings` | Validated detail for `review_required` entries, with optional context from the selected hash-bound structured artifact. |
| `degraded_sources` | Fetch errors, empty outcomes, undated drops, and quiet sources from categories exceeding the quiet threshold. |
| `repair_actions` | Final-attempt deterministic repairs, only with a public artifact. |
| `generation_failures` | Allowlisted model identities and fixed reasons for an exhausted fallback chain. |
| `advisory_findings` | The four reader-relevant nonblocking checks listed above, for ready and review-required entries. |
| `provenance` | Original generation identity and counters, or null. Requires a public artifact. |
| `semantic_audit` | Validated generated prose, review scores, confirmations, repair outcomes, coverage, and costs; otherwise null. No frozen excerpts or prompts. |
| `integrity` | Bounded version-2 operational record with decisions, hashes, identities, phases, actions, costs, generation timing, and typed corpus health; otherwise null. |
| `markdown` | String for `ready` and `review_required`; null otherwise. |

An empty `degraded_sources` list means no recorded degradation. It does not prove complete source coverage.
Status-only integrity can include the date's generation cost because failed attempts can incur charges.
`generation_timing` holds the fallback chain's start and end timestamps, or null when the run left no usable record.
It spans every attempt, including failed ones, as the generation cost does. A standalone run records its own manifest span.
A reversed or unparseable pair is published as null with a warning, never guessed.

Finding context can contain a structured path and hash-bound original model prose.
Public JSON retains that context. HTML displays concise messages and safe subjects without full previews or unchanged stories.
Integrity actions refer to check indexes and verified artifact scopes.
Initial-prose hashes identify the first complete rendered candidate.
The first ready candidate supplies the semantic-review baseline.
Selection preparation precedes the initial-briefing boundary. Missing baselines remain unavailable.
Sequence numbers do not represent measured durations.

The metadata parser checks bounds and consistency. Structural validity alone does not authorize a repair.
Publication reconstructs or checks operational claims against hash-bound artifacts.
The site loader checks published digests against exact sidecar and history Markdown before rendering or replacement.
Null integrity supplies no digest.
Invalid audits retain the original with safe fixed explanations.
Workflow links require the configured repository and a validated numeric run ID.
Typed corpus health contains source identities, statuses, counts, and fixed reasons, without feed text or provider bodies.
