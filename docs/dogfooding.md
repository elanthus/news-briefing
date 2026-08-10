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
- This is a fetch-only check, not a complete run: no model step was involved. It is also a *different* fetch from the one recorded below, an hour or so earlier in the day — same window length, different contents, which is what live retrieval looks like.

## Daily runs

### 2026-08-09 — the run behind the committed reference pair

The complete fetch → rank and summarize → check loop that produced [`fixtures/corpus-2026-08-09.json`](../fixtures/corpus-2026-08-09.json) and [`fixtures/briefing-2026-08-09.md`](../fixtures/briefing-2026-08-09.md). Every count below is derived from those two committed files, so this entry can be re-derived instead of taken on trust.

- Agent and execution environment: Claude Opus 5 subagent via Claude Desktop 2.1.222, in a local macOS checkout with Python 3.14.6.
- Corpus window: 2026-08-09 00:34 UTC → 2026-08-10 00:34 UTC (24h), with the default caps of 25 items per source and 60 per category.
- Corpus: 158 items — 27 US politics, 53 US news, 46 world, 7 AI/tech, 25 developer-community. Elapsed fetch time: 27.1 seconds.
- Source failures: `r/ClaudeCode`, `r/LocalLLaMA` and `r/cursor` all returned HTTP 429 — three of the four subreddits. No Hacker News item cleared the window either, so both dev sub-sections drew on r/ClaudeAI alone, with no engagement signal available for any dev-community item. The briefing says so in its corpus-health section rather than just looking thin.
- Processing: 10 AI/tech and 2 developer-community items failed the relevance filter; 2 US-news duplicates were dropped. Neither cap bound. All counters reconcile against `fetched`.
- Briefing: 22 reported topics, filling all six configured sections to target (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), plus a 25-row exclusion log and a corpus-health section naming all three failed sources.
- Checker, first result: 1 error, 1 warning — `ungrounded_link` for the AI Dev Practices item “Cowork Projects keep CLAUDE.md outside the project folder,” plus the expected transitional `slots_underfilled` warning because the checker still required 5 US Politics topics while the new prompt required 3.
- Correction made after checking: replaced the ungrounded “Cowork Projects” item and URL with the corpus-supported “A developer uses Claude to build tools around their own ADHD needs” item. The following checker-contract task changed the US Politics target from 5 to 3, removing the transitional warning without changing the briefing.
- Checker, final result: 0 errors, 0 warnings — still reproducible today:

  ```bash
  python3 eval_briefing.py --corpus fixtures/corpus-2026-08-09.json --briefing fixtures/briefing-2026-08-09.md --config fixtures/briefing-config-2026-08-09.json
  ```

Note on the claim-grounding checks: the four problems they caught on arrival (three over-reaching summaries, one misattributed quotation) were in the **2026-08-08** briefing, the baseline this pair replaced. That briefing was corrected in `30fafca` and removed from `fixtures/` in `2750a25`. This run's briefing has never been edited since it was committed.

### 2026-08-10 — scheduled daily-news-briefing task

The regular `daily-news-briefing` scheduled task (fetch → rank and summarize → check loop), run unattended by Claude Code. Corpus and briefing were not committed (per this log's policy of keeping only aggregate health information), so unlike the 2026-08-09 pair this entry cannot be re-derived from a fixture — only reported here.

- Agent and execution environment: Claude Sonnet 5 in Claude Code, running the scheduled `daily-news-briefing` task unattended, local macOS checkout with Python 3.14.6.
- Corpus window: 2026-08-09 17:10:48 UTC → 2026-08-10 17:10:48 UTC (24h), default caps of 25 items per source and 60 per category.
- Corpus: 208 items — 26 US politics, 60 US news, 47 world, 15 AI/tech, 60 developer-community. Elapsed fetch time wasn't captured on the actual run; an immediate follow-up fetch under the same environment and script took 23.9 seconds, given here as representative.
- Source failures: `r/ClaudeCode` returned HTTP 429 — the only failure. All four other Reddit sources, Hacker News, and every RSS feed cleared the window.
- Processing: 19 AI/tech and 3 developer-community items failed the relevance filter; 1 US-news and 1 developer-community duplicate were dropped; the 60-per-category cap bound on US news (8 dropped) and developer-community (9 dropped). No source cap bound. All counters reconciled against `fetched`.
- Briefing: 22 reported topics, filling all six configured sections to target (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), plus a 25-row exclusion log and a corpus-health section naming the one failed source.
- Checker, first result: 3 errors, 7 warnings — three `category_ineligible` errors because the AI News section (eligible categories: `ai_tech`, `us_news`) cited two `us_politics` items (the Zuckerberg manifesto's "biggest risk" framing and the Sanders AI-pause letter); plus warnings for two unsupported figures (the Will Scharf item's "$400m" figure and the gas-price item's "$1" increase, both true but cited against items that didn't contain them), two unsupported quotations (the Netanyahu item's "historic" quote and the Zuckerberg item's "with as many people..." quote, both cited against items that didn't contain them), and three `claim_exceeds_evidence` warnings on the AI Dev Tools items sourced from Hacker News posts with empty corpus summaries, where the drafted summaries added unsupported framing beyond the bare title.
- Correction made after checking: moved the Sanders AI-pause letter into US Politics, where `us_politics` is an eligible category, dropping the progressive-primary-wins topic to the exclusion log to keep the section at 3; re-cited the Zuckerberg manifesto against eligible `ai_tech`/`us_news` sources and swapped in a Wired "AI slop backlash" item (from an eligible `ai_tech` source) to refill AI News's fourth slot; added the missing supporting citations for the Will Scharf and gas-price figures and the Netanyahu quote; and trimmed the three Hacker News-sourced AI Dev Tools items down to only what their (otherwise summary-less) titles support, per the empty-summary grounding rule.
- Checker, final result: 0 errors, 0 warnings.
