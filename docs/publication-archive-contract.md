# Publication archive contract

This is the authoritative contract for the published GitHub Pages archive: scheduling, corpus windows,
repair-versus-correction precedence, hash-bound publication, retention, and the machine-readable history
format. Read it when you need to know exactly what the archive publishes, what it withholds, and why a
given run reached the page it did. [`build_site.py`](../build_site.py) implements the rendering and
retention rules described here.

## Scheduling and corpus windows

The GitHub Pages workflow runs daily at 13:30 UTC and generates one report labeled with the current `America/New_York` date. A manual dispatch offers two modes: `single-day` duplicates that scheduled run for today, while `backfill-7-days` targets today plus the six prior Eastern report dates. Both manual modes replace successful existing reports for their target dates. Scheduled runs retain the normal publication rank safeguard. All modes check out `main` so generation uses the latest merged code, prompts, and configuration.

The workflow captures one start timestamp and always fetches today's corpus fresh for the exact 24-hour interval ending at that instant. Earlier target dates reuse their published `site/corpora/YYYY-MM-DD.json` unchanged and are skipped when no stored corpus exists; they are never reconstructed from retention-limited live feeds.

Window boundaries are therefore approximate across dates. The first rolling window can overlap a preceding calendar-day corpus, so adjacent reports may temporarily repeat stories. Each later report likewise covers the exact 24 hours ending at that run's independently captured timestamp, so changes in capture time can create small overlaps or gaps between windows.

Each date gets a separate corpus, run directory, and report path. Every run carries forward stored corpora and the static builder publishes the valid dated files with a fourteen-day retention window, so the archive becomes independently regenerable as storage accumulates.

## Repair, correction, and publication

### Precedence: deterministic repair before model correction

The correction budget is reserved for findings deterministic repair cannot fix.

Editorial placement errors — ineligible-category or globally repeated citations and over-limit sections — are repaired deterministically before any correction pass is spent: a recorded repair pass drops the complete later entry (or trims the over-limit tail), giving included stories priority over every exclusion log, and logs each change as a `repair_actions` entry rather than a checker finding.

A `claim_exceeds_evidence` warning takes the same code-owned path only when every citation has complete known support and the normalized excerpt contains no URL: the runner replaces the oversized model summary with its deduplicated cited corpus evidence, records `replace_summary_with_excerpt`, and visibly labels the rendered topic `[verbatim]` without spending a provider correction call. Incomplete or URL-bearing evidence is left untouched and remains review-required.

The provider schema exposes only citation references eligible for each section. A model correction pass is spent only when a finding needs the model — such as an unknown reference, a free-form URL, a schema-shape violation, or an error the checker raises against the rendered briefing — and the same deterministic repair still cleans up any repairable remainder once that bounded budget is exhausted. An eager repair re-enters the validation loop rather than ending the run, so the untouched correction budget stays available for findings the repaired render reveals.

Repair never trims an entry held for rejection: unknown evidence remains a rejection and is never normalized away.

### What reaches the public page

The builder publishes a complete `ready` briefing only when the runner manifest identifies `final.md` as its final artifact and the file's SHA-256 matches the manifest. If other review-requiring findings remain after that bounded repair budget, a `review_required` run may expose its checker-generated `preview.md` under the same hash-bound rule.

The static builder renders `review_required` entries as a quarantine stub on the public page — a notice and a status chip linking to the per-run integrity report under `reports/<date>.html`. On that report, story context derived from both headline-based checks and structured paths attaches grouped, ordinary, and excluded affected stories to their actionable findings inline beside the annotated preview; only genuinely run-level findings remain in a separate panel. Every entry's status chip links to its report, and a `ready` page shows clean prose with no inline review panels. Nonblocking quality notes stay in the run artifacts and are excluded from public warning panels and counts.

When a previewed story actually redacts a model-authored destination, its report panel includes a closed disclosure containing the hash-verified original structured entry as escaped, non-clickable text.

The published `repair_actions` describe the final attempt only: a repair superseded by a later model correction is not the published content's provenance and remains in the manifest for audit.

`rejected`, `blocked`, and `no_result` runs remain status-only. A status-only manual failure preserves any previously published page. Every workflow run uploads the dated corpora, reports, and verified run directories as a seven-day diagnostics artifact so correction attempts remain inspectable after the runner exits.

## Site rendering and retention

The newest retained run is rendered directly on the site home page. A date bar at the top links to separate pages for the other retained runs. The site renderer replaces a valid machine corpus-health block with a readable summary and source-type/status groups; the checked JSON contract remains unchanged in stored Markdown and malformed blocks remain escaped verbatim.

The site and its machine-readable history retain up to seven report dates. When an eighth or later entry exists, the builder removes the oldest entries until seven remain; date gaps alone never remove history. The workflow can seed an initially empty archive from the hash-verified August 17 dogfood final and the August 18 DeepSeek dogfood preview.

## Machine-readable history

The generated `history.json` uses `schema_version: 4` while accepting previously deployed schema-version-1 through -3 histories during migration. Each entry contains `date`, `disposition`, `findings_count`, `findings`, `degraded_sources`, `repair_actions`, and `markdown`.

| Field | Contents |
|---|---|
| `date` | The Eastern report date the entry was labeled with. |
| `disposition` | The run's publication disposition. |
| `findings_count` | Actionable findings only, excluding nonblocking quality notes. A zero count on a blocked infrastructure failure does not mean the checker accepted a candidate. |
| `findings` | Validated detail for `review_required` entries only, plus optional story context from the hash-bound selected structured artifact. Other dispositions retain only `findings_count`, so rejected prose is not leaked through metadata. |
| `degraded_sources` | Fetch errors reported by the corpus. An empty list means no source failure was reported, not that every possible source was available or complete. |
| `repair_actions` | The deterministic repair log for the run, empty when nothing was repaired. Published only for entries with a public artifact. |
| `markdown` | A string for `ready` and `review_required` entries; `null` otherwise. |

Finding context may carry a structured `path` that the site uses to attach findings to their stories by producer-emitted anchors.
