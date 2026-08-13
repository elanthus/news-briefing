# Design notes

Why each stage of the pipeline works the way it does. The [README](../README.md) covers what the system guarantees; this covers the decisions behind those guarantees.

## Fetching

**Bounded, source-diverse context.** Broad technology feeds are filtered for AI relevance, tracking URLs are canonicalized before deduplication, and per-source and per-category caps stop one noisy publisher consuming the model's context window. Item counts alone are not a size bound, so schema v5 also caps each model-visible string, each configured source's serialized items, and the retained item set globally in both bytes and estimated tokens. Titles and summaries truncate on Unicode boundaries; URLs are dropped rather than rewritten. `processing`, per-source usage, and `context_budget` make every truncation and field/source/global drop observable.

**Every dropped item is accounted for.** `processing` reports `fetched`, `undated_dropped`, relevance/duplicate/cap drops, field/source/global budget drops, truncation counts, retained bytes/tokens, and `kept` per category. The drop reasons plus `kept` reconcile to `fetched`. `undated_dropped` catches a feed silently changing its date format — those items never reach the other counters, so without it a dead source looks identical to a quiet one.

**Every source fetch is observable.** `sources` records exact source type and ID, whether a request was attempted and reached HTTP success, recognized, dated, and finally retained entry/byte/token-estimate counts, status, typed error details, and wall-clock latency for every configured request; `fetch_duration_ms` records the whole retrieval phase. Valid XML with an unexpected layout and responses with no parseable dates are explicit `empty` outcomes. `errors` contains structured projections of every `empty` or `error` source.

**Public network destinations only.** Source URLs are syntax-checked before scheduling: only HTTP(S), without credentials, is accepted. For each request, the hostname is resolved exactly once; every answer must be globally routable, and the socket connects directly to one of those captured addresses while TLS still authenticates the original hostname. Redirects are handled manually and repeat the complete URL, DNS, address-scope, and pinning process at every hop. That closes direct, redirect, and DNS-rebinding paths to loopback, private, link-local, and metadata-service addresses without a third-party HTTP dependency.

**Relevance filtering removes noise, not importance.** Only the known-broad feeds listed in `SOURCE_RELEVANCE_FILTERS` are keyword-filtered; category-specific and community sources pass through untouched. Over-filtering is the more expensive mistake, because an item dropped at this stage cannot be ranked at all and a starved sub-category cannot fill its reserved slots.

**Recency is the only sort order.** An earlier version summed `points` with a never-populated `score` and used the timestamp only as a tiebreak, which put every Reddit post (no points) below every Hacker News post regardless of age. Recency is the one ordering that means the same thing across RSS, HN, and Reddit. Timestamps are required to include a time and UTC offset, and sorting compares the represented instants rather than their differently offset ISO spellings. Engagement stays on the item for the model to weigh.

**Untrusted XML is parsed defensively.** Feeds are remote and unauthenticated, and `xml.etree` expands internal entities, so a few hundred bytes can expand without bound in memory — the "billion laughs" pattern, which the response size limit does not stop because the payload is tiny on the wire. Before building the tree, an Expat declaration callback rejects every `DOCTYPE` after the parser has recognized the document encoding; it does not scan raw bytes or special-case UTF-16. Real RSS/Atom feeds do not use a DTD, and a rejection surfaces in `errors` like any other source failure. This closes the hole without depending on `defusedxml`, preserving the project’s zero-dependency runtime.

**Reddit's coarse buckets.** Reddit's `top` RSS endpoint accepts only `hour`/`day`/`week`/… buckets, not an arbitrary window. `fetch_news.py` picks the smallest bucket that fully covers `--hours`, then applies the exact cutoff in code, requesting proportionally more posts when the bucket overshoots so in-window coverage stays roughly constant. Reddit also rate-limits anonymous clients aggressively, so requests use a shorter timeout and a bounded two-attempt retry budget that honors `Retry-After`. A failed subreddit degrades coverage and appears in corpus health rather than holding the run open. Expect HTTP 429s: they are the normal case for anonymous access, not a misconfiguration.

## The corpus contract

[`corpus_schema.py`](../corpus_schema.py) is the single source of truth for the shape of `corpus.json` — field names, valid category shape, per-category processing consistency, counter semantics, and a `schema_version`. Three things depend on that shape: the fetcher writes it, the prompt instructs a model to read specific fields from it, and the checker validates a briefing against it. Before the contract existed, renaming a key in the fetcher produced no error anywhere, just a quietly worse briefing.

The fetcher validates against the contract before writing, so drift fails where it is introduced. `eval_briefing.py` validates a readable corpus before trusting its categories, items, timestamps, processing counters, errors, or source health, and refuses a corpus newer than it understands rather than misreading it.

Schema v4 replaces ambiguous error strings with structured identities and adds complete request outcomes. Schema v5 adds field/source/global context limits, per-source retained usage, and aggregate truncation/drop telemetry. It enforces those limits alongside item value types, absolute HTTP(S) URLs, timezone-aware full timestamps, and `cutoff <= published <= generated_at` rather than checking required keys alone.

**URL comparison lives here too.** `canonicalize_url` is part of the contract rather than the fetcher because two modules must agree on it: the fetcher deduplicates with it, and the checker decides whether a citation is in the corpus with it. When only the fetcher knew the rule, the checker compared raw strings and reported a citation differing by a trailing slash as one the corpus did not contain — the same finding it uses for a fabricated link.

`url_route` splits a canonical URL into a location (scheme, host, path) and its query parameters, and the name is a warning: a location is not an article identity. Query-routed publishers put many articles under one path, so `news.ycombinator.com/item` addresses every Hacker News story there is. The checker only calls a failed citation a rewrite when a corpus URL at the same location carries a strict superset of its parameters, and only when exactly one does. An earlier version compared location alone; it reported a citation of HN story 999 as a tidied version of story 123 and told the reader to paste 123 back, which is the opposite of what the distinction exists for.

## Ranking and checking

**Configured slot allocation.** The default reserves space for each section so high-volume AI industry news cannot crowd out dev tools and practices. A different mix can reserve that space differently without changing code.

**Claim grounding is sampled, not asserted.** Verifying that prose is entailed by its source needs a semantic judge, so the deterministic checker does not pretend to settle it. Its figure, quotation, and length checks are review signals, all at WARN. Building them immediately caught three over-reaching summaries and one misattributed quotation in the reference briefing committed at the time (2026-08-08, since replaced). The current reference pair evaluates clean, so run the checker on your own output rather than reading that as a settled result.

**Exclusion accountability.** The default asks for the next five stories dropped from each accountable section, with a reason; the configuration can change that target or exempt a section with `0`. The log heading itself is required only while at least one section is still accountable, so a configuration that exempts every section is not asked for an empty one. Silent omission is the failure mode you cannot otherwise detect.

**Corpus health reporting.** Fetch failures and silent empty outcomes are collected per source and surfaced in the briefing, so a degraded run looks degraded instead of just looking short. For v4 and newer corpora, the checker requires one fenced JSON manifest whose `source_type`, `source_id`, and `status` values exactly match the structured error records. Prose-only claims, paraphrased IDs, duplicate entries, omitted failures, and invented failures are errors. Older frozen corpora retain their legacy text check solely for reproducible historical fixtures.

**Untrusted-data boundary.** The briefing prompt treats all public-feed text as untrusted content, forbids following instructions embedded in it, and tells the summarizer to use no browsing or write-capable tools. The prompt is not an enforcement mechanism — it is an instruction that attacker-controlled corpus text can attempt to override. Output-link grounding enforces exactly one of the four channels between corpus text and the reader: every web destination in the complete output is allowlisted, independent of whether the model presents it as a required `🔗` citation, Markdown link, HTML link, autolink, protocol-relative link, bare `www.` link, or bare HTTP(S) text. Raw URLs and HTML-decoded candidates are checked separately so entity-encoded schemes cannot bypass validation without changing legitimate query parameters. See [the README](../README.md#what-injection-can-and-cannot-do-here) for the other three channels and what remains open.

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

For behavior rather than parser regression, the development-only [`evaluator/`](../evaluator/) runs a committed utility, quality, and attack set through Codex CLI, Claude Code CLI, OpenRouter, or NVIDIA. It preserves both model attempts, structured checker findings before and after correction, attack-oracle outcomes, model settings, timestamps, content hashes, and Git provenance. The deterministic human-labeled suite is separate from live generation so parser failures, semantic checker limits, and stochastic model behavior keep honest denominators.

## References and influences

- Frank Coyle, [“Why Agentic Systems Need Ontologies”](https://www.youtube.com/watch?v=Sir59K8ZDPU&t=8s), AI Engineer World's Fair (2026). The relevant architectural principle is probabilistic reasoning inside deterministic domain validation. This repository realizes that principle with closed-world corpus schemas and purpose-built checks rather than an RDF/OWL ontology.
