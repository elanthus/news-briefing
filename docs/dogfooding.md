# Dogfooding log

This is the pre-launch operating record for news-briefing. It records the first run of the day, including degraded runs, rather than keeping only successful reruns.

For a complete daily run, record:

- the agent and execution environment;
- corpus item counts by category and elapsed fetch time;
- every failed source;
- the checker's first result;
- any correction made after checking; and
- the final checker result.

Generated corpora and briefings remain temporary. This log keeps only aggregate health information and review notes; the frozen regression pair in `fixtures/` remains the reproducible example.

## Pre-launch verification

### 2026-08-09 — live fetch and reference check

- Environment: Python 3.14.6, local macOS checkout.
- Live fetch: 24.1 seconds; 180 items — 27 US politics, 53 US news, 43 world, 7 AI/tech, and 50 developer-community.
- Source failures: `r/LocalLLaMA` and `r/cursor` returned HTTP 429. The fetch completed successfully and preserved both failures in `corpus.errors`.
- Live processing: 10 AI/tech items and 3 developer-community items failed the relevance filter; 2 US-news duplicates and 1 US-news item over the per-source cap were dropped. All processing counters reconciled.
- Reference checker: 0 errors, 0 warnings against `fixtures/corpus-2026-08-09.json` and `fixtures/briefing-2026-08-09.md`.
- Full live model run: not yet recorded. This verification entry does not count as a completed daily dogfood run.

## Daily runs

No complete daily runs recorded yet.
