# Publication archive contract

This is the authoritative contract for the published GitHub Pages archive: scheduling, corpus windows,
repair-versus-correction precedence, hash-bound publication, retention, and the machine-readable history
format. Read it when you need to know exactly what the archive publishes, what it withholds, and why a
given run reached the page it did. [`prepare_publication.py`](../prepare_publication.py) enforces the
hash-bound publication gate, and [`build_site.py`](../build_site.py) implements the rendering and
retention rules described here.

## Scheduling and corpus windows

The GitHub Pages workflow runs daily at 13:30 UTC and generates one report labeled with the current `America/New_York` date. A manual dispatch offers two modes: `single-day` targets today by default or one explicit `YYYY-MM-DD` inside the fourteen-date private retention window, while `backfill-7-days` targets today plus the six prior Eastern report dates. A historical single-date run reuses only that restored corpus and never expands into an archive-wide regeneration. Both manual modes replace successful existing reports for their target dates. Scheduled runs retain the normal publication rank safeguard. All modes check out `main` so generation uses the latest merged code, prompts, and configuration.

The workflow captures one start timestamp and always fetches today's corpus fresh for the exact 24-hour interval ending at that instant. Earlier target dates reuse exact current-schema corpora restored from the newest authenticated encrypted GitHub Actions archive and are skipped when no stored corpus exists; they are never reconstructed from retention-limited live feeds. A missing or expired archive starts a fresh window. An unreadable private archive fails the run.

Window boundaries are therefore approximate across dates. The first rolling window can overlap a preceding calendar-day corpus, so adjacent reports may temporarily repeat stories. Each later report likewise covers the exact 24 hours ending at that run's independently captured timestamp, so changes in capture time can create small overlaps or gaps between windows.

Each date gets a separate corpus, run directory, and report path. Every run restores and prunes exact corpora to the fourteen-day window, then uploads only an encrypted archive. The static builder derives a text-free public audit manifest for each valid dated corpus. This preserves deterministic backfill inputs without redistributing source-owned titles, feed excerpts, or community post bodies through GitHub Pages.

## Repair, correction, and publication

### Precedence: deterministic repair before model correction

The correction budget is reserved for findings deterministic repair cannot fix.

Editorial placement errors — ineligible-category or globally repeated citations and over-limit sections — are repaired deterministically before any correction pass is spent: a recorded repair pass drops the complete later entry (or trims the over-limit tail), giving included stories priority over every exclusion log, and logs each change as a `repair_actions` entry rather than a checker finding.

A `claim_exceeds_evidence` warning takes the same code-owned path only when every citation has complete known support and the normalized excerpt contains no URL: the runner replaces the oversized model summary with its deduplicated cited corpus evidence, records `replace_summary_with_excerpt`, and visibly labels the rendered topic `[verbatim]` without spending a provider correction call. Incomplete or URL-bearing evidence is left untouched and remains review-required.

A section left under `target_stories` while eligible, unreported evidence remained in its own accountability log is filled from the front of that log before the evidence freezes. The runner records `promote_excluded_entry` on a `selection_promotion` attempt and labels the rendered topic `[promoted from the accountability log]`. The attempt kind is deliberately distinct from `selection_repair`: filling a reserved slot is routine editorial bookkeeping, not evidence the run had to be patched, so it stays out of the repair-action count on the published entry. Because the promoted story leaves the log, a promoted section's log can be one entry short of its target and raise the nonblocking `exclusion_log_short` advisory; the topic's tag is what explains it.

The selection schema exposes only the one item-aligned citation reference eligible for each corpus item in a section. Article and Hacker News discussion destinations remain paired behind that reference in the code-owned map. After independent validation, code freezes the selected reference sets. The prose request contains only the evidence selected for each output position, and the prose schema has no citation field; code attaches the frozen sets afterward. A model correction pass is spent only when a finding needs the model — such as an unknown reference, an opaque reference leaked into prose, a free-form URL, a schema-shape violation, or an error the checker raises against the rendered briefing — and the same deterministic repair still cleans up any repairable remainder once that bounded budget is exhausted. An eager repair re-enters the validation loop rather than ending the run, so the untouched correction budget stays available for findings the repaired render reveals.

Repair never trims an entry held for rejection: unknown evidence remains a rejection and is never normalized away.

### What reaches the public page

[`prepare_publication.py`](../prepare_publication.py) publishes a complete `ready` briefing only when the runner manifest identifies `final.md` as its final artifact and the file's SHA-256 matches the manifest. If other review-requiring findings remain after that bounded repair budget, a `review_required` run may expose its checker-generated `preview.md` under the same hash-bound rule.

The static builder renders `review_required` entries as a quarantine stub on the
public page, with a status chip linking to `reports/<date>.html`. Every status chip
links to its integrity report; ready briefing pages contain no inline review
panels. Integrity reports lead with the publication decision and review status,
then actions and changes before statistics. They show concise deterministic
finding messages and safe subjects without reproducing the briefing or its
accountability log. Unchanged prose is omitted while repair outcomes remain
visible; complete finding context remains in public history JSON.

Nonblocking quality notes do not count toward `findings_count`. The four
reader-relevant checks (`slots_underfilled`, `exclusion_log_missing`,
`exclusion_log_short`, `low_claim_evidence_overlap`) appear in the integrity
report's advisory counts and concise finding details, and the ready report states that the gate passed with
N advisory notes. The reader-facing status chip retains its advisory-note count.
The excerpt-bounded figure heuristics (`unsupported_figure`,
`figure_supported_elsewhere`) remain in run artifacts and are excluded from
advisory counts. Their rows are also omitted from the reader page's Run outcome
warning list; the list reads "None" when no other warning remains.

The published `repair_actions` describe the final attempt only: a repair
superseded by a later model correction did not produce the published content.
The integrity ledger distinguishes superseded generation actions from actions
bound to the published candidate, without publishing earlier private prose.

`rejected`, `blocked`, and `no_result` runs remain status-only. When every model in a fallback chain fails, a blocked page and its integrity report explain why each model failed using fixed public messages. `publication_failures.py` projects the complete failed chain into allowlisted model identifiers and reason codes; raw provider errors, rejected prose, and story details stay private. Fallback logs use schema v2 and carry structured failure records from the originating provider or checker. Records carry their own schema version 1; the original unversioned structured shape remains readable. A finalized checker outcome takes precedence over earlier recoverable provider errors. Public projection ignores private `failure_reason` text and allowlists only model identifiers and fixed codes. Unknown or malformed records use the generic explanation; old string-only chain logs are not classified. Missing or malformed chain logs retain the generic status notice. A status-only manual failure preserves any previously published page. Every workflow run uploads the dated corpora, reports, and verified run directories only inside a fourteen-day authenticated encrypted diagnostics artifact so correction attempts remain inspectable without exposing their raw corpus or model request.

## Site rendering and retention

The newest retained run is rendered directly on the site home page. A date bar at the top links to separate pages for the other retained runs. The site renderer replaces a valid machine corpus-health block with a readable summary and source-type/status groups; the checked JSON contract remains unchanged in stored Markdown and malformed blocks remain escaped verbatim.

The site and its machine-readable history retain up to seven report dates. When an eighth or later entry exists, the builder removes the oldest entries until seven remain; date gaps alone never remove history. An empty archive starts with newly published reports.

Each integrity report links to `manifests/<date>.json` when the matching private corpus was available during the build. Audit-manifest schema version 1 contains the corpus and item identifiers, report and window timestamps, item category and source, canonical article and discussion URLs, and SHA-256 hashes of the exact UTF-8 title and optional excerpt bytes. It also hashes the complete private corpus file. It contains neither title nor excerpt text. The builder removes any stale `site/corpora` directory before rendering, so a reused output directory cannot accidentally carry a raw corpus into the Pages artifact.

## Private operational artifacts

The repository secret `CORPUS_ARCHIVE_PASSPHRASE` is required by the daily workflow. Exact corpora and diagnostics are first gzip-compressed, then encrypted with AES-256-CBC using a PBKDF2-SHA-256-derived key. A separately derived HMAC-SHA-256 authenticates the versioned envelope, and restoration verifies that MAC before decryption. The passphrase is supplied on standard input rather than in process arguments. The checkout step sets `persist-credentials: false`, so no Git credential is left in the workspace. The restore step is the only step with an explicit `GITHUB_TOKEN` binding and receives `CORPUS_ARCHIVE_PASSPHRASE`; the generation step, which fetches feeds and calls models, is bound to `OPENROUTER_API_KEY` and `SCRAPECREATORS_API_KEY`; the encryption step is bound to `CORPUS_ARCHIVE_PASSPHRASE`. The job-level permissions (`actions: read`, `contents: read`, `pages: write`, `id-token: write`) are available to every step, including fetch and generation: any step can read `github.token` or request an OIDC token, because Actions does not isolate permissions per step. Archive restoration accepts only regular `corpora/YYYY-MM-DD.json` members whose JSON validates against the current corpus schema and whose `report_date` matches the filename; unexpected members, traversal paths, duplicate dates, oversized data, and malformed current corpora fail closed. Authenticated members declaring obsolete positive integer schema versions are skipped, with their date identities, uniqueness, and byte bounds still checked. They are never upgraded or restored, and an all-obsolete archive produces an empty window. Missing, malformed, and future versions fail closed. The token-authenticated GitHub download follows only an absolute HTTPS artifact redirect, then downloads that destination without forwarding the token.

The ciphertext artifacts and exact corpora retain fourteen report dates. Because this is a public repository, the encrypted artifact bytes may themselves be publicly downloadable; corpus confidentiality therefore depends on the repository secret remaining private and the authenticated-encryption envelope remaining intact. Rotating or deleting `CORPUS_ARCHIVE_PASSPHRASE` makes existing retained artifacts unreadable, so rotation must occur only after intentionally accepting that bounded backfill gap or after re-encrypting the retained archive.

## Generation provenance

Each `ready` or `review_required` publication carries a `provenance` object: the OpenRouter (or other) provider name, the exact model identifier, its `attempt_index` and `attempt_count` in the production fallback chain, `selection_corrections` and `prose_corrections` (how many correction calls each phase spent), `repair_action_count` (deterministic repairs applied across the whole run, not only the ones bound to the final published attempt — see `repair_actions` above), and `prompt_sha256`, the runner prompt's content hash already recorded in the checkpoint manifest's `identity`. Every field is a model identifier or a non-negative count; provenance never carries prompt text, corpus text, or a URL.

`prepare_publication.py` derives `provenance` entirely from the selected child run's `manifest.json` (`provider`, `identity.prompt_sha256`, and each recorded attempt's `kind` and `repair_actions`) and, when present, the fallback chain's `fallback-log.json` (`model_chain` length and the selected attempt's `index`). A manual `run_briefing.py` run has no fallback-chain log at all: `attempt_index` and `attempt_count` both default to 1 rather than describing a chain that never existed. A manifest that predates recorded provider identity or the prompt hash — or any disposition without a public artifact — publishes no `provenance` at all (`null`), and `build_site.py` renders such an entry exactly as before.

`build_site.py` renders `provenance`, when present, as a compact line under the verdict on the per-run integrity report only ("Generated by deepseek/deepseek-v4-flash-0731, attempt 2 of 3, 1 selection correction, 0 prose corrections, 0 total repair actions."); the `attempt N of M` clause is omitted when `attempt_count` is 1, since there was no chain to have a position in. The reader-facing page never shows it.

## Daily semantic repair and audit

After ordinary generation reaches `ready`, the daily command runs Jev checks and
isolated confirmation. Confirmed included citation/grouping/prose findings receive one
bounded HY3 repair round. Only a structurally valid ready candidate with complete,
clear follow-up checks involving affected slots replaces the publication input.
Original fallback-chain artifacts remain immutable. Partial coverage, failed or
rejected repairs retain the original ready report. Unrelated disputed findings
do not block an otherwise-cleared repair; checks involving repaired slots must
still clear the follow-up threshold. The sidecar's `semantic_audit`
retains generated before/after prose and all scores without frozen excerpts.
Citation rows identify a frozen evidence index and code-owned destinations.
The confirmation thresholds are stated in
[Jev checks and repairs](jev-review.md#confirmation-and-repair-policy). Publication rebinds retained-source follow-up indexes to the originals and
verifies that removed citations cannot return. The HTML report displays each exact headline or summary change once per story,
regardless of the triggering check. Removed source items identify confirmed
irrelevance, grouping subset selection, or both as the cause. An article and its
Hacker News discussion count as one source removal. Candidate changes remain
proposals when the original is retained. Failed and skipped actions retain safe
subjects and all applicable fixed explanations. Initial and follow-up statistics
and confirmation counts are separate; actual follow-up blockers remain visible
even when they were below threshold initially.
Publication revalidates the source and repaired artifacts before accepting that audit or using its candidate.

See [daily semantic checks and repairs](jev-review.md) for exact limits and
[preliminary evaluations](results/jev-preliminary-2026-09-30.md) for observed
benefits, misses and false alarms. A selected repair run supplies the final public
Markdown, while generation provenance continues to describe the original selected
fallback run. `integrity.repair_generation` separately identifies the actual repair
provider/model and its recorded prompt hash. Jev is the judge rather than the
prose author. Initial review, repair generation, and follow-up review have separate
cost records; known spend is aggregated once and unknown billing is never zeroed.
Semantic repair work is separate from original generation correction counters.

## Machine-readable history

The generated `history.json` uses `schema_version: 9`. The builder accepts schemas
7 and 8, adding `integrity: null` while preserving existing fields; schema 7 also
receives its missing `semantic_audit: null`. Other versions are rejected. Every
schema-9 entry requires `date`, `disposition`, `findings_count`, `findings`,
`degraded_sources`, `repair_actions`, `generation_failures`, `advisory_findings`,
`provenance`, `semantic_audit`, `integrity`, and `markdown`. Current sidecars use
the same metadata fields. Known legacy sidecars without integrity or semantic
audit remain accepted as explicit shapes; arbitrary extra fields are rejected.
Missing historical facts remain unavailable, and rebuilding never calls a model
to populate them. Legacy provenance is retained as recorded; when an older entry
does not separate original generation from repair generation, the builder does
not infer those identities.

| Field | Contents |
|---|---|
| `integrity` | Bounded version-1 operational record: publication decision and fixed reasons, verified artifact hashes, original/repair identities, ordered phases and action references, separate coverage/costs, optional numeric workflow run identity, and typed corpus-health records. Null for unavailable history. Non-public entries expose status-only information. |
| `semantic_audit` | Public generated original/changed prose, initial and actual follow-up Jev scores and confirmations, repair outcomes and coverage/cost metadata; null without a valid audit or public artifact. No frozen feed excerpts or prompts. |
| `date` | The Eastern report date the entry was labeled with. |
| `disposition` | The run's publication disposition. |
| `findings_count` | Actionable findings only, excluding nonblocking quality notes. A zero count on a blocked infrastructure failure does not mean the checker accepted a candidate. |
| `generation_failures` | For exhausted fallback chains only, model identifiers and fixed reason codes explaining why no briefing was published. |
| `findings` | Validated detail for `review_required` entries only, plus optional story context from the hash-bound selected structured artifact. Other dispositions retain only `findings_count`, so rejected prose is not leaked through metadata. |
| `degraded_sources` | Sources behind the runner's degraded-coverage predicate (`corpus_schema.corpus_health_degraded`): fetch errors, undated drops, and the quiet sources of any category whose quiet count exceeds `QUIET_SOURCE_DEGRADED_THRESHOLD`. An empty list means no degradation was reported, not that every possible source was available or complete. |
| `repair_actions` | The deterministic repair log for the run, empty when nothing was repaired. Published only for entries with a public artifact. |
| `advisory_findings` | Nonblocking quality findings worth a reader's attention — `slots_underfilled`, `exclusion_log_missing`, `exclusion_log_short`, and `low_claim_evidence_overlap` — reusing the same finding shape and story-context resolution as `findings`, published for both `ready` and `review_required` entries. `findings_count` never counts these. The excerpt-bounded `unsupported_figure` and `figure_supported_elsewhere` heuristics stay out of this list (see [design.md](design.md#ranking-and-checking)). |
| `provenance` | The original selected generation model and correction/repair counts for that run (see "Generation provenance" above), or `null` when unavailable. Published only for entries with a public artifact. |
| `markdown` | A string for `ready` and `review_required` entries; `null` otherwise. |

Finding context may carry a structured `path` and hash-bound original model prose.
That context is retained in public history JSON; the report shows concise messages
and safe subject/value context without rendering the full preview or unchanged
stories. Integrity actions reference check indexes and verified artifact scopes;
initial-prose hashes identify the first complete rendered candidate, while the
first ready candidate is the semantic-review baseline. Selection preparation
precedes that initial-briefing boundary. A missing baseline is explicitly
unavailable, and sequence numbers do not imply measured durations.

The public metadata parser bounds every integrity field and validates internal
consistency, but structural validity does not authorize a repair. Publication
reconstructs or cross-checks operational claims from hash-bound artifacts and
reports. The site loader also checks the published artifact digest against exact
sidecar Markdown bytes and stored history Markdown before rendering or replacing
an entry. Legacy null integrity does not invent an artifact digest. An invalid
audit retains the original and does not expose its unverified
prose, scores, actions, or destinations as applied. Fixed reason codes replace
raw exception messages. Optional workflow links are constructed from the configured
repository and a validated numeric run ID; absent IDs produce no link. Typed
corpus-health records contain source identities, statuses, counts, and fixed
degradation reasons from a validated corpus, never feed text or provider bodies.
