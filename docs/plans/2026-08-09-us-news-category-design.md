# US News category, tighter US Politics, one-placement rule

Date: 2026-08-09

## Problem

Three changes to the briefing contract:

1. US Politics is capped at 3 topics instead of 5.
2. A new US News section carries up to 4 topics a day.
3. A topic is reported once across the whole briefing, never twice.

The third is not incidental. US News and US Politics draw on overlapping
outlets — NPR, The Hill and Axios all file under both — so the new section
creates the duplication risk that rule exists to close.

## Decisions

### US News is a real corpus category, not a model-side split

`corpus_schema.CATEGORIES` gains `us_news`, fed by its own sources:

| Source | Feed | Items in a 24h window |
| --- | --- | --- |
| CBS News US | `https://www.cbsnews.com/latest/rss/us` | 26 |
| The Guardian US | `https://www.theguardian.com/us-news/rss` | 14 |
| PBS NewsHour | `https://www.pbs.org/newshour/feeds/rss/headlines` | 10 |
| NPR National | `https://feeds.npr.org/1003/rss.xml` | 5 |

The alternative was to let the model pull non-political stories out of the
existing `us_politics` corpus. That was rejected because it moves
categorization from the deterministic layer to the model, and it breaks the
property the checker depends on: that every briefing section has a corpus
category behind it.

Four sources rather than the minimum that fills the slots. The category needs
9 items a day to fill 4 topics plus a 5-entry exclusion log, and these four
supply 55, so the headroom is not about news volume — US domestic
news does not go quiet enough to starve the section. It is about a feed
dying. A 403, a DNS failure or a date-format change is permanent, and no
amount of re-fetching recovers it; only another source does. These four
survive losing any two.

The redundancy is plain extra sources, not conditional fallbacks. A backup
feed that fires only when a category looks thin would make corpus composition
depend on runtime volume, which breaks the property that the fetch layer is
deterministic and unit-testable. Partial failure is already first-class here
(`errors`, the Corpus health section), and `source_cap`/`category_cap` already
bound what an extra feed can cost.

PBS NewsHour files perhaps two world stories in ten. That bleed already exists
in `us_politics` through The Hill and Axios, and the one-placement rule is
what resolves it.

Rejected candidates, measured over the same window: NBC US (3 items), ABC US
(1, behind a 301), CNN US (0 in-window — 17 entries carry a date the parser
cannot read, which is what `undated_dropped` exists to surface), and USA Today,
whose feed carries a DOCTYPE and is refused by `parse_feed_xml`. That refusal
is the billion-laughs defense working as intended and is not something to
route around for one source.

No relevance filter is applied to these feeds. The existing filters cover
The Verge, Ars, Wired and the GitHub Changelog because those carry high
off-topic volume; curated hard-news feeds do not. Over-filtering is the more
expensive mistake, since a dropped item cannot be ranked at all.

### `SCHEMA_VERSION` goes 1 to 2

The bump is precautionary, and it is worth being exact about why, because the
obvious rationale is wrong. `validate_corpus` is strict about the category set,
but nothing reads a corpus through it — it is called once, on the write path
(`fetch_news.py`), against a corpus the same process just built. The read-side
gate is `is_readable`, and `eval_briefing.py` iterates
`corpus["categories"].values()` without ever keying by name, so a v1 checker
handed a v2 corpus would in fact read it correctly. Left unbumped, nothing
would break today.

It is bumped anyway because the alternative is reasoning, per change, about
which additive edits old readers happen to tolerate. That reasoning is cheap
to get wrong and expensive to get wrong silently, and the version gate only
works if the rule is mechanical: the category set is part of the contract, so
changing it bumps the version. `is_readable` already permits new code to read
old corpora, so the cost is one-directional — an old checkout refuses a new
corpus instead of guessing at it.

### Duplication is enforced in the prompt and the checker, not the fetcher

The corpus keeps an item in every category it legitimately belongs to, so the
evidence layer stays complete and the model retains the choice of where a
story fits best. The briefing is where the once-only rule binds:

- the prompt states that a story is reported in exactly one section, and that
  a reported story never also appears in an exclusion log;
- `check_no_repeated_topics` moves from WARN to ERROR.

Global deduplication in the fetcher was rejected: it would force a category
precedence guess, so a story arriving first through the US News feed could no
longer be ranked as politics.

Exact-URL repeats are now a contract violation. The same event covered by two
outlets under two URLs is not detectable this way and remains the model's job,
so the consolidation rule is extended to apply across sections rather than
only within one.

### The section boundary

Elections, Congress, the administration, federal policy and courts-as-politics
go to US Politics. Everything else US-domestic — disasters, crime, public
health, business, education, local government — goes to US News.

### The frozen fixture is regenerated, not patched

The 2026-08-08 reference pair cannot satisfy the new contract: it has five US
Politics topics and no US News section, and `CommittedFixtureTest` asserts
zero findings against it. Its corpus is a closed 24-hour window and cannot be
re-fetched.

A fresh run replaces it. Making the checker tolerant of pre-US-News corpora
was rejected because it adds version branching to the checker and leaves a
baseline that no longer reflects the shipped format; hand-editing the frozen
briefing was rejected because it stops being actual model output.

## Consequences

Total topics go from 20 to 22. US Politics loses two slots on a day when a
US-domestic section gains four, so a heavy political news day pushes more
genuinely significant politics into the exclusion log. That is the intended
trade; the log keeps it visible.
