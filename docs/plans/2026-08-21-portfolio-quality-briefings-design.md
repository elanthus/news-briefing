# Portfolio-quality briefing pages

Date: 2026-08-21
Status: approved

## Problem

The published GitHub Pages briefings are not yet presentable for a portfolio or
Show HN feature:

1. Several reports are withheld (`review_required` previews) solely because of
   the `claim_exceeds_evidence` WARN — the model-authored summary outgrew its
   cited excerpts.
2. The corpus-health line renders as raw compact JSON (`{"failed_sources":
   [...]}`) on the published page.
3. Several days show *every* RSS source `empty`. Investigation confirmed this
   is **not rate limiting**: a 429 or any HTTP failure produces `status:
   "error"` (`fetch_news.py`, `source_status`). `empty` means the fetch
   succeeded but zero entries fell in the requested one-day window. The
   affected days come from `backfill-7-days` runs re-fetching live RSS feeds
   for windows 3–6 days in the past — those items have rolled off the feeds,
   so retrying can never recover them.

## Design

### 1. Evidence-swap repair (unblocks withheld reports)

Extend the deterministic structural repair stage
(`agent_runner/output.py::repair_structural_output`) to handle
`claim_exceeds_evidence`:

- Replace the model-authored summary with the deduplicated cited excerpts,
  using the same join as the checker (`dict.fromkeys` over the item's links'
  evidence). The repair passes re-validation **by construction**: the prose
  becomes a subset of its own support, which also cannot trip
  `unsupported_figure` or `unsupported_quotation`.
- Add the check to `REPAIRABLE_CHECKS`; record a repair action through the
  existing `repair_actions` plumbing in `PublicationRecord`.
- **Producer labeling** (consistent with #100): the swapped item renders as a
  quoted excerpt with a visible marker — *"— source excerpt (model summary
  withheld: exceeded cited evidence)"* — and the sidecar producer field gains
  a new value, `corpus_excerpt`.
- Reports that land in `review_required` for this reason alone re-validate
  clean → `ready` → full publication.

### 2. Human-friendly corpus health rendering

The model's JSON contract is unchanged (the checker still validates the exact
machine block). `build_site.py` gains a render-time transform: the
corpus-health JSON block renders as a one-line summary — e.g. *"⚠ 15 RSS
feeds and 2 Hacker News searches returned no items for this day"* — followed
by a collapsible `<details>` list grouped by source type, with plain-language
status wording: `empty` → "no items in this day's window", `error` → "fetch
failed".

### 3. Persist corpora; backfill reads storage, not dead feeds

- `build_site.py` publishes each day's corpus to
  `site/corpora/YYYY-MM-DD.json` alongside `history.json`.
- The `backfill-7-days` loop resolves each of the 7 day offsets (newest to
  oldest):
  1. Stored corpus downloadable from the live site → regenerate from it, no
     live fetch.
  2. No stored corpus, day within the last 3 days → live fetch (feeds still
     retain it).
  3. No stored corpus, older than 3 days → skip the day with a workflow
     warning, leaving any existing page untouched. No more all-empty
     briefings.
- Once ~7 days of corpora accumulate on the site, backfill covers the full
  week from storage.
- Caveat: days already published from empty corpora cannot be recovered
  retroactively (no stored corpus; run artifacts expire after 7 days). The
  system self-heals going forward, and a fresh backfill after this lands
  replaces recent degraded pages with whatever is still recoverable.

## Testing

- Repair swap: by-construction re-validation tests in
  `test_briefing_output.py` (swap fires, re-validates clean, action recorded,
  producer label present).
- Render transform: fixture tests in `test_build_site.py` (summary line,
  grouping, status wording, no raw JSON in output HTML).
- Corpus persistence and backfill resolution: extend `test_build_site.py`
  (corpora published), `test_daily_workflow.py` and
  `test_prepare_publication.py` (per-day resolution order, skip path).

## Decisions

- Swap in evidence rather than downgrading the check or relying on prompt
  tuning (deterministic; strengthens the fail-closed repair narrative).
- Label swapped items visibly as source excerpts (producer-labeled output).
- Persist corpora as the root-cause backfill fix; cap live re-fetching at the
  last 3 days.
