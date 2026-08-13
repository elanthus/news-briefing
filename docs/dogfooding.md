# Dogfooding log

This is the operating record for news-briefing. It records the first run of the day, including degraded runs, rather than keeping only successful reruns.

For a complete daily run, record:

- the agent and execution environment;
- corpus item counts by category and elapsed fetch time;
- every failed source;
- the checker's first result;
- any correction made after checking; and
- the final checker result.

Generated corpora and briefings are archived per day under [`docs/runs/<date>/`](runs/) (corpus, briefing, and the `briefing-config.json` snapshot used), so each entry below can be re-derived instead of taken on trust. The frozen regression pair in `fixtures/` is separate: it is the fixed example the test suite pins to, and daily runs do not update it.

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

The regular `daily-news-briefing` scheduled task (fetch → rank and summarize → check loop), run unattended by Claude Code. The corpus, briefing, and config snapshot are archived at [`docs/runs/2026-08-10/`](runs/2026-08-10/), so this entry can be re-derived instead of taken on trust.

- Agent and execution environment: Claude Sonnet 5 in Claude Code, running the scheduled `daily-news-briefing` task unattended, local macOS checkout with Python 3.14.6.
- Corpus window: 2026-08-09 17:10:48 UTC → 2026-08-10 17:10:48 UTC (24h), default caps of 25 items per source and 60 per category.
- Corpus: 208 items — 26 US politics, 60 US news, 47 world, 15 AI/tech, 60 developer-community. Elapsed fetch time wasn't captured on the actual run; an immediate follow-up fetch under the same environment and script took 23.9 seconds, given here as representative.
- Source failures: `r/ClaudeCode` returned HTTP 429 — the only failure. All four other Reddit sources, Hacker News, and every RSS feed cleared the window.
- Processing: 19 AI/tech and 3 developer-community items failed the relevance filter; 1 US-news and 1 developer-community duplicate were dropped; the 60-per-category cap bound on US news (8 dropped) and developer-community (9 dropped). No source cap bound. All counters reconciled against `fetched`.
- Briefing: 22 reported topics, filling all six configured sections to target (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), plus a 25-row exclusion log and a corpus-health section naming the one failed source.
- Checker, first result: 3 errors, 7 warnings — three `category_ineligible` errors because the AI News section (eligible categories: `ai_tech`, `us_news`) cited two `us_politics` items (the Zuckerberg manifesto's "biggest risk" framing and the Sanders AI-pause letter); plus warnings for two unsupported figures (the Will Scharf item's "$400m" figure and the gas-price item's "$1" increase, both true but cited against items that didn't contain them), two unsupported quotations (the Netanyahu item's "historic" quote and the Zuckerberg item's "with as many people..." quote, both cited against items that didn't contain them), and three `claim_exceeds_evidence` warnings on the AI Dev Tools items sourced from Hacker News posts with empty corpus summaries, where the drafted summaries added unsupported framing beyond the bare title.
- Correction made after checking: moved the Sanders AI-pause letter into US Politics, where `us_politics` is an eligible category, dropping the progressive-primary-wins topic to the exclusion log to keep the section at 3; re-cited the Zuckerberg manifesto against eligible `ai_tech`/`us_news` sources and swapped in a Wired "AI slop backlash" item (from an eligible `ai_tech` source) to refill AI News's fourth slot; added the missing supporting citations for the Will Scharf and gas-price figures and the Netanyahu quote; and trimmed the three Hacker News-sourced AI Dev Tools items down to only what their (otherwise summary-less) titles support, per the empty-summary grounding rule.
- Checker, final result: 0 errors, 0 warnings — reproducible today:

  ```bash
  python3 eval_briefing.py --corpus docs/runs/2026-08-10/corpus-2026-08-10.json --briefing docs/runs/2026-08-10/briefing.md --config docs/runs/2026-08-10/briefing-config.json
  ```

### 2026-08-11 — Codex dogfood run and dated fixture sample

The complete fetch → rank and summarize → check loop requested as a dated sample. Unlike the fixed 2026-08-09 regression pair, this run is preserved as a separate dated fixture set: [`corpus-2026-08-11.json`](../fixtures/corpus-2026-08-11.json), [`briefing-2026-08-11.md`](../fixtures/briefing-2026-08-11.md), and [`briefing-config-2026-08-11.json`](../fixtures/briefing-config-2026-08-11.json).

- Agent and execution environment: OpenAI Codex desktop agent in a local macOS checkout with Python 3.14.6.
- Corpus window: 2026-08-10 16:47:41 UTC → 2026-08-11 16:47:41 UTC (24h), with the default caps of 25 items per source and 60 per category.
- Corpus: 230 items — 40 US politics, 60 US news, 55 world, 22 AI/tech, and 53 developer-community. Elapsed fetch time: 19.5 seconds.
- Source failures: the Hacker News query `prompt engineering` returned successfully but contained zero recognized entries; `r/ClaudeCode` and `r/LocalLLaMA` returned HTTP 429. The briefing's corpus-health prose and machine-readable manifest record all three coverage gaps.
- Processing: 40 AI/tech items and 1 developer-community item failed the relevance filter; 3 developer-community duplicates were dropped; the 60-per-category cap bound on US news, dropping 18 items. No source cap bound, and all counters reconcile against `fetched`.
- Briefing: 22 reported topics, filling all six configured sections to target (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), plus a 25-row exclusion log and a corpus-health section naming every failed or empty source.
- Checker, first result: 0 errors, 2 warnings — one `unsupported_figure` warning for adding the year 2026 to the homicide summary when that year was absent from its cited item, and one for spelling the emissions estimate as `5%` when the corpus used `5 percent`.
- Correction made after checking: removed the unsupported year from the homicide summary and changed the emissions estimate to the corpus-supported words “five percent.” No topics or citations changed.
- Checker, final result: 0 errors, 0 warnings. The full 197-test suite also passed. Reproduce the final checker result with:

  ```bash
  python3 eval_briefing.py --corpus fixtures/corpus-2026-08-11.json --briefing fixtures/briefing-2026-08-11.md --config fixtures/briefing-config-2026-08-11.json
  ```

### 2026-08-12 — Codex daily dogfood run

The complete fetch → rank and summarize → check loop run in Codex. The corpus, briefing, and configuration snapshot are archived at [`docs/runs/2026-08-12/`](runs/2026-08-12/).

- Agent and execution environment: OpenAI Codex desktop agent in a local macOS checkout with Python 3.14.6.
- Corpus window: 2026-08-11 18:54:09 UTC → 2026-08-12 18:54:09 UTC (24h), with the default caps of 25 items per source and 60 per category.
- Corpus: 210 items — 40 US politics, 60 US news, 58 world, 24 AI/tech, and 28 developer-community. Elapsed live fetch time: 25.4 seconds.
- Source failures: the Hacker News query `prompt engineering` returned successfully but contained zero recognized entries; `r/ClaudeCode`, `r/LocalLLaMA`, and `r/cursor` returned HTTP 429. The briefing's corpus-health prose and machine-readable manifest record all four coverage gaps.
- Processing: 55 AI/tech items and 3 developer-community items failed the relevance filter; the 60-per-category cap bound on US news, dropping 11 items. No duplicate, source-cap, field-budget, source-budget, or global-budget drops occurred, and all counters reconcile against `fetched`.
- Briefing: 22 reported topics, filling all six configured sections to target (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), plus a 25-row exclusion log and a corpus-health section naming every failed or empty source.
- Checker, first result: 1 error, 1 warning — an `ungrounded_link` error because the “second brain” Reddit citation used a percent-encoded form that did not exactly match the corpus URL, and an `unsupported_figure` warning because the Blacksmith valuation figure appeared in the item's URL but not in its title or summary.
- Corrections made after checking: replaced the Blacksmith valuation figure with the corpus-supported statement that its valuation jumped almost tenfold in less than a year. The first URL correction still included typographic quotation marks from the source title and produced 1 `ungrounded_link` error with 0 warnings on the intermediate check; the citation was then changed to the exact corpus URL slug.
- Checker, final result: 0 errors, 0 warnings. Reproduce the final checker result with:

  ```bash
  python3 eval_briefing.py --corpus docs/runs/2026-08-12/corpus-2026-08-12.json --briefing docs/runs/2026-08-12/briefing.md --config docs/runs/2026-08-12/briefing-config.json
  ```

### 2026-08-13 — Claude Code CLI dogfood run

The complete fetch → rank and summarize → check loop run with the Claude Code CLI. The corpus, corrected briefing, and configuration snapshot are archived at [`docs/runs/2026-08-13/`](runs/2026-08-13/).

- Agent and execution environment: Claude Code 2.1.220 using Claude Sonnet 5 at high effort, in a local macOS checkout with Python 3.14.6. The generation process was limited to the `Read` and `Write` tools; its recorded usage confirms zero web searches and zero web fetches.
- Corpus window: 2026-08-12 16:53:27 UTC → 2026-08-13 16:53:27 UTC (24h), with the default caps of 25 items per source and 60 per category.
- Corpus: 236 items — 35 US politics, 60 US news, 58 world, 23 AI/tech, and 60 developer-community. Elapsed live fetch time: 10.5 seconds.
- Source failures: the Hacker News query `prompt engineering` returned successfully but contained zero recognized entries. The briefing's corpus-health prose and machine-readable manifest record the resulting coverage gap.
- Processing: 36 AI/tech and 3 developer-community items failed the relevance filter; 1 US-news and 1 developer-community duplicate were dropped; a per-source cap dropped 6 world items; and the 60-per-category cap dropped 24 US-news and 43 developer-community items. No field, source-budget, or global-budget drops occurred, and all counters reconcile against `fetched`.
- Briefing: 22 reported topics, filling all six configured sections to target (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), plus a 25-row exclusion log and a corpus-health section naming the empty source.
- CLI behavior: the first buffered attempt was stopped after 369.1 seconds because it exposed no progress and had not written a file; it cost $0.3564 and made no web requests or file changes. A retry with streaming diagnostics completed in 363.4 seconds and cost $1.2746. That retry first wrote a partial one-section draft, recognized the truncation itself, and replaced it with the complete briefing. Total model cost across both attempts was $1.6310. User-level Claude Code startup hooks also loaded unrelated learning-mode and skill context despite the narrow tool allowlist; `--safe-mode` would make a future run more reproducible and less noisy.
- Checker, first result: 0 errors, 1 warning — `claim_exceeds_evidence` because the Hacker News item “Codex in ChatGPT desktop app for Linux is now in preview” had an empty summary, while the generated prose expanded its 56-character title into 184 characters of unsupported framing.
- Correction made after checking: reduced that summary to the title-supported statement, “Codex in the ChatGPT desktop app for Linux is now in preview.” No topic, citation, or section placement changed.
- Checker, final result: 0 errors, 0 warnings. Reproduce the final checker result with:

  ```bash
  python3 eval_briefing.py --corpus docs/runs/2026-08-13/corpus-2026-08-13.json --briefing docs/runs/2026-08-13/briefing.md --config docs/runs/2026-08-13/briefing-config.json
  ```

- Offline verification: all 219 core tests and all 38 evaluator tests passed.
