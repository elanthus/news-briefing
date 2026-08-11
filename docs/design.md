# Design notes

Why each stage of the pipeline works the way it does. The [README](../README.md) covers what the system guarantees; this covers the decisions behind those guarantees.

## Fetching

**Bounded, source-diverse context.** Broad technology feeds are filtered for AI relevance, tracking URLs are canonicalized before deduplication, and per-source and per-category caps stop one noisy publisher consuming the model's context window. The corpus records each filtering stage in `processing` metadata.

**Every dropped item is accounted for.** `processing` reports `fetched`, `undated_dropped`, `relevance_dropped`, `duplicates_dropped`, `source_cap_dropped`, `category_cap_dropped`, and `kept` per category, and they reconcile: the drops plus `kept` equal `fetched`. `undated_dropped` catches a feed silently changing its date format — those items never reach the other counters, so without it a dead source looks identical to a quiet one.

**Every source fetch is observable.** `sources` records the status, item count, undated count, and wall-clock latency of each configured request; `fetch_duration_ms` records the whole retrieval phase. The legacy `errors` strings remain for existing corpus readers and briefing prompts.

**Relevance filtering removes noise, not importance.** Only the known-broad feeds listed in `SOURCE_RELEVANCE_FILTERS` are keyword-filtered; category-specific and community sources pass through untouched. Over-filtering is the more expensive mistake, because an item dropped at this stage cannot be ranked at all and a starved sub-category cannot fill its reserved slots.

**Recency is the only sort order.** An earlier version summed `points` with a never-populated `score` and used the timestamp only as a tiebreak, which put every Reddit post (no points) below every Hacker News post regardless of age. Recency is the one ordering that means the same thing across RSS, HN, and Reddit. Timestamps are required to include a time and UTC offset, and sorting compares the represented instants rather than their differently offset ISO spellings. Engagement stays on the item for the model to weigh.

**Untrusted XML is parsed defensively.** Feeds are remote and unauthenticated, and `xml.etree` expands internal entities, so a few hundred bytes can expand without bound in memory — the "billion laughs" pattern, which the response size limit does not stop because the payload is tiny on the wire. Before building the tree, an Expat declaration callback rejects every `DOCTYPE` after the parser has recognized the document encoding; it does not scan raw bytes or special-case UTF-16. Real RSS/Atom feeds do not use a DTD, and a rejection surfaces in `errors` like any other source failure. This closes the hole without depending on `defusedxml`, preserving the project’s zero-dependency runtime.

**Reddit's coarse buckets.** Reddit's `top` RSS endpoint accepts only `hour`/`day`/`week`/… buckets, not an arbitrary window. `fetch_news.py` picks the smallest bucket that fully covers `--hours`, then applies the exact cutoff in code, requesting proportionally more posts when the bucket overshoots so in-window coverage stays roughly constant. Reddit also rate-limits anonymous clients aggressively, so requests use a shorter timeout and a bounded two-attempt retry budget that honors `Retry-After`. A failed subreddit degrades coverage and appears in corpus health rather than holding the run open. Expect HTTP 429s: they are the normal case for anonymous access, not a misconfiguration.

## The corpus contract

[`corpus_schema.py`](../corpus_schema.py) is the single source of truth for the shape of `corpus.json` — field names, valid category shape, per-category processing consistency, counter semantics, and a `schema_version`. Three things depend on that shape: the fetcher writes it, the prompt instructs a model to read specific fields from it, and the checker validates a briefing against it. Before the contract existed, renaming a key in the fetcher produced no error anywhere, just a quietly worse briefing.

The fetcher validates against the contract before writing, so drift fails where it is introduced. `eval_briefing.py` refuses a corpus newer than it understands rather than misreading it.

Category names and their order come from trusted source configuration rather than application code, which is why `SCHEMA_VERSION` is 3: older readers expect the built-in v2 names and should refuse a new corpus instead of misdiagnosing a valid custom category as drift.

**URL comparison lives here too.** `canonicalize_url` is part of the contract rather than the fetcher because two modules must agree on it: the fetcher deduplicates with it, and the checker decides whether a citation is in the corpus with it. When only the fetcher knew the rule, the checker compared raw strings and reported a citation differing by a trailing slash as one the corpus did not contain — the same finding it uses for a fabricated link.

`url_route` splits a canonical URL into a location (scheme, host, path) and its query parameters, and the name is a warning: a location is not an article identity. Query-routed publishers put many articles under one path, so `news.ycombinator.com/item` addresses every Hacker News story there is. The checker only calls a failed citation a rewrite when a corpus URL at the same location carries a strict superset of its parameters, and only when exactly one does. An earlier version compared location alone; it reported a citation of HN story 999 as a tidied version of story 123 and told the reader to paste 123 back, which is the opposite of what the distinction exists for.

## Ranking and checking

**Configured slot allocation.** The default reserves space for each section so high-volume AI industry news cannot crowd out dev tools and practices. A different mix can reserve that space differently without changing code.

**Claim grounding is sampled, not asserted.** Verifying that prose is entailed by its source needs a semantic judge, so the deterministic checker does not pretend to settle it. Its figure, quotation, and length checks are review signals, all at WARN. Building them immediately caught three over-reaching summaries and one misattributed quotation in the reference briefing committed at the time (2026-08-08, since replaced). The current reference pair evaluates clean, so run the checker on your own output rather than reading that as a settled result.

**Exclusion accountability.** The default asks for the next five stories dropped from each accountable section, with a reason; the configuration can change that target or exempt a section with `0`. The log heading itself is required only while at least one section is still accountable, so a configuration that exempts every section is not asked for an empty one. Silent omission is the failure mode you cannot otherwise detect.

**Corpus health reporting.** Fetch failures are collected per source and surfaced in the briefing, so a degraded run looks degraded instead of just looking short. The checker requires every failed source's identifier in the corpus-health section; it tolerates case, line wrapping, `HN:` spacing, and the common `/r/subreddit` spelling, but a generic health claim or a mention elsewhere cannot satisfy the requirement. Error records use `<source>: <message>`, so source names, HN queries, and subreddit names reserve the `: ` delimiter and must be single-line; rejecting ambiguous configuration keeps the identifier recoverable without changing the corpus schema.

**Untrusted-data boundary.** The briefing prompt treats all public-feed text as untrusted content, forbids following instructions embedded in it, and tells the summarizer to use no browsing or write-capable tools. The prompt is not the enforcement mechanism; citation grounding is. See the injection fixture described in the [README](../README.md#grounding-is-also-injection-containment).

## Regression-testing a prompt change

[`fixtures/`](../fixtures) holds a frozen corpus, briefing, and briefing configuration. To check whether an edit to `briefing-prompt.md` made things worse, run the agent against the frozen corpus and configuration, then check the output against the same frozen inputs:

```bash
python3 eval_briefing.py \
  --corpus fixtures/corpus-2026-08-09.json \
  --briefing your-new-output.md \
  --config fixtures/briefing-config-2026-08-09.json
```

Diff it against [`fixtures/briefing-2026-08-09.md`](../fixtures/briefing-2026-08-09.md). The frozen inputs make this a controlled comparison, so any difference is attributable to the prompt.

The reference briefing is a **regression baseline, not a golden answer**. Ranking is judgment, and a prompt change that reorders two topics is not automatically a regression. What the baseline catches is the silent structural stuff: a dropped exclusion log, a collapsed sub-category, links drifting away from the corpus.
