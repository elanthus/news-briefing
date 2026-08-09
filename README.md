# news-briefing

[![CI](https://github.com/elanthus/news-briefing/actions/workflows/ci.yml/badge.svg)](https://github.com/elanthus/news-briefing/actions/workflows/ci.yml)

A daily news briefing pipeline built around one boundary: **retrieval is deterministic, the model receives a closed corpus, and the output contract is checked in code.**

The LLM is handed a closed corpus and does the thing it's actually good at — ranking and summarizing — and has to show its work on what it left out. The prompt forbids outside knowledge; the checker then verifies the parts of that instruction that are mechanically decidable. It does not pretend that a Markdown parser can prove the model chose the right story or faithfully summarized it.

It *can* still write a summary that overstates its source, and that is the part worth being precise about:

| | Guarantee |
|---|---|
| What counts as **recent** | **Enforced in code.** The cutoff is applied before the model sees anything. |
| What is **eligible** | **Prompt-constrained.** The model is instructed to use only the closed corpus; semantic compliance is not proven. |
| What may be **cited** | **Enforced for the required `🔗` citation format.** Every parsed citation must exist in the corpus, exclusion log included. Arbitrary URLs elsewhere in the Markdown are outside this check. |
| Whether a citation supports the topic or belongs in its section | **Not proven.** The checker validates corpus membership, not semantic fit. |
| What is **important** | **Not claimed** — the model ranks. The exclusion log makes that judgment auditable, not absent. |
| Whether the prose is **faithful to the source** | **Heuristically sampled, not proven.** The checker warns on figures or quotations absent from the cited excerpt and on prose that substantially outgrows its evidence. |

That last row is the honest limit. The corpus stores a truncated feed blurb, not the article — around a quarter of items are clipped at the 300-character cap and a few carry only a headline — so a faithful summary is still a summary of an excerpt someone else selected. Thin evidence should produce a terse topic or an excluded one; `claim_exceeds_evidence` is what catches it when it doesn't.

The pipeline is three stages:

1. **Fetch (deterministic, no LLM).** [`fetch_news.py`](fetch_news.py) pulls public RSS feeds, including first-party OpenAI, Google DeepMind, and GitHub Changelog updates; the Hacker News Algolia API; and Reddit RSS into a single JSON corpus. Everything older than a hard cutoff (default 24h) is dropped **in code**. Every item carries a parsed, timezone-normalized publish timestamp. The default maps directly to Reddit's `day` bucket before the exact cutoff is applied.
2. **Rank & summarize (LLM).** [`briefing-prompt.md`](briefing-prompt.md) is the prompt an agent follows to turn that corpus into a ranked briefing (US Politics, World Events, AI/Tech with fixed sub-category slots), plus an **excluded-topics log** so you can see what didn't make the cut and why.
3. **Check (deterministic, no LLM).** [`eval_briefing.py`](eval_briefing.py) parses the required briefing format and validates it back against the corpus it came from — every topic and exclusion needs a recognized citation, every parsed link must exist in the corpus, slots must not be over-filled, a story can't be both included and excluded, and a degraded run must say so.

Design notes worth calling out:

- **Fixed slot allocation.** Without it, high-volume AI industry news crowds out the dev-tools and dev-practices content, which is most of why I read this.
- **Claim grounding is sampled, not asserted.** Verifying that prose is entailed by its source needs a semantic judge, so the deterministic checker doesn't pretend to settle it. Its figure, quotation, and length checks are review signals, all at WARN. Building them immediately caught three over-reaching summaries and one misattributed quotation in the committed reference briefing.
- **Exclusion accountability.** The model must name the next 5 stories it dropped per section, with a reason. Silent omission is the failure mode you can't otherwise detect.
- **Corpus health reporting.** Fetch failures are collected per-source and surfaced in the briefing, so a degraded run looks degraded instead of just looking short.
- **Bounded, source-diverse context.** Broad technology feeds are filtered for AI relevance, tracking URLs are canonicalized before deduplication, and per-source/category caps prevent one noisy publisher from consuming the model's context window. The corpus records each filtering stage in `processing` metadata.
- **Every dropped item is accounted for.** `processing` reports `fetched`, `undated_dropped`, `relevance_dropped`, `duplicates_dropped`, `source_cap_dropped`, `category_cap_dropped`, and `kept` per category, and they reconcile: the drops plus `kept` equal `fetched`. `undated_dropped` is the one that catches a feed silently changing its date format — those items never reach the other counters, so without it a dead source looks identical to a quiet one.
- **The corpus has a written contract.** [`corpus_schema.py`](corpus_schema.py) is the single source of truth for the shape of `corpus.json` — field names, category set, counter semantics, and a `schema_version`. `fetch_news.py` validates against it before writing and exits non-zero on a violation; `eval_briefing.py` refuses a corpus newer than it understands rather than misreading it. Previously all three (fetcher, prompt, checker) agreed only by convention, so renaming a key produced no error anywhere — just a quietly worse briefing.
- **Untrusted XML is parsed defensively.** Feeds are remote and unauthenticated, and `xml.etree` expands internal entities, so a few hundred bytes can expand without bound in memory. `parse_feed_xml` refuses any `DOCTYPE`, which is what entity declarations and external entity references both require. Real RSS/Atom feeds don't use one, and a rejection surfaces in `errors` like any other source failure.
- **Untrusted-data boundary.** The briefing prompt treats all public-feed text as untrusted content, forbids following instructions embedded in it, and tells the summarizer to use no browsing or write-capable tools.

No API keys or credentials. Python 3.11+, stdlib only — no `pip install`. Tests run offline on 3.11 through 3.14 in CI.

## Sample output

Abridged from a real run (`--hours 24`, 2026-08-08 — 164 items across 4 categories). The full briefing has 5 topics per news section and a complete exclusion log.

<details>
<summary><b>Click to expand sample briefing</b></summary>

> # Daily Briefing — August 8, 2026
> Corpus window: 2026-08-08 00:08 UTC → 2026-08-09 00:08 UTC
>
> ## US Politics
>
> **Senate confirms Todd Blanche as Attorney General** — The Senate confirmed former Trump personal attorney Todd Blanche as U.S. Attorney General in a 50–49 vote early Saturday morning.
> 🔗 https://www.npr.org/2026/08/08/g-s1-137631/senate-confirms-todd-blanche-attorney-general
>
> **Iran publishes demands as Strait of Hormuz talks continue** — The secretary of Iran's Supreme National Security Council, Mohammad Bagher Zolghadr, laid out the country's key demands for the U.S. amid negotiations to reopen the Strait of Hormuz.
> 🔗 https://thehill.com/policy/international/6018858-iran-demands-us-hormuz-negotiations/
>
> ## World Events
>
> **Gaza recovery crews pull 19 bodies from a destroyed building** — More than 8,000 people remain missing under rubble in Gaza, with recovery efforts hindered by a lack of heavy machinery.
> 🔗 https://www.aljazeera.com/news/2026/8/8/crews-recover-19-bodies-from-rubble-of-destroyed-gaza-building?traffic_source=rss
>
> **Wildfire evacuations in British Columbia and northern Italy** *(consolidated)* — The Bald Range wildfire in British Columbia has more than doubled in size to over 36 sq miles (95 sq km), remains out of control, and has forced thousands from their homes under new evacuation orders. Separately, at least 200 people were evacuated as a wildfire burned near Lake Garda.
> 🔗 https://www.bbc.co.uk/news/articles/cx25dkwk3e3o?at_medium=RSS&at_campaign=rss
> 🔗 https://www.aljazeera.com/video/newsfeed/2026/8/8/at-least-200-people-evacuated-as-wildfire-rages-near-lake-garda?traffic_source=rss
>
> ## AI/Tech
>
> **AI News (4 slots)**
>
> **Amazon's planned Texas data center could become the largest US climate polluter** *(consolidated)* — Amazon is investing in an on-site power plant for a planned West Texas data center that, per the New York Times, could become the single largest source of greenhouse gas emissions in the United States. Reported independently by TechCrunch and The Verge.
> 🔗 https://techcrunch.com/2026/08/08/planned-amazon-data-center-could-become-the-biggest-climate-polluter-in-the-u-s/
> 🔗 https://www.theverge.com/ai-artificial-intelligence/977124/amazon-data-center-worst-polluting-power-plant
>
> **OpenAI acquires presentation startup NextSlide** — NextSlide says its team members are now working on ChatGPT.
> 🔗 https://techcrunch.com/2026/08/08/openai-acquires-presentation-startup-nextslide/
>
> **AI Dev Tools (3 slots)**
>
> **Claude Code adds cross-session messaging** — Claude Code sessions can now message each other.
> 🔗 https://code.claude.com/docs/en/cross-session-messaging
> 🔗 HN: https://news.ycombinator.com/item?id=49222824
> `↑ 44 pts · 25 comments`
>
> **AI Dev Practices (3 slots)**
>
> **"Code was never the hard part" is an insult to all programmers** — A widely-discussed pushback on the claim that writing code is the easy part of programming.
> 🔗 https://blog.senko.net/code-was-never-the-hard-part-is-an-insult-to-all-programmers
> 🔗 HN: https://news.ycombinator.com/item?id=49222189
> `↑ 526 pts · 345 comments`
>
> ---
>
> ### Excluded Topics (accountability log)
>
> **US Politics**
> - *El-Sayed faces 'risky bet' with Michigan's Black voters* — primary-race dynamics; lower immediate national impact. 🔗 https://thehill.com/homenews/campaign/6017875-el-sayed-michigan-senate-black-voters/
> - *Acting ICE chief knocks AP for 'misleading' body cam report* — agency press dispute rather than policy change. 🔗 https://thehill.com/homenews/administration/6018782-ice-chief-david-venturella-ap-bodycam/
> - *Dems blast Blanche as enabler of 'Trump's corruption'* — consolidated into US Politics topic #1. 🔗 https://thehill.com/homenews/senate/6018748-democrats-oppose-blanche-confirmation/
>
> **AI Dev Practices**
> - *Is Microsoft-Phi dead?* — speculation thread, no release or announcement to anchor it. 🔗 https://www.reddit.com/r/LocalLLaMA/comments/1vj8bxf/is_microsoftphi_dead/
> - *Inline Edit gone?* — single-user tooling issue, not a validated practice. 🔗 https://www.reddit.com/r/cursor/comments/1vj4h71/inline_edit_gone/
>
> ---
>
> ### Corpus health
> 2 sources failed this run: `r/ClaudeAI` and `r/ClaudeCode` (HTTP 429). Dev-community coverage is degraded accordingly.

</details>

## Usage

```bash
python3 fetch_news.py -o corpus.json
```

Then hand `corpus.json` and `briefing-prompt.md` to your agent (or paste the prompt into a chat session) and have it produce the briefing.

Options:

```
python3 fetch_news.py --hours 12        # narrower window
python3 fetch_news.py --hours 48        # wider window (Reddit uses its week bucket)
python3 fetch_news.py --markdown        # human-readable digest instead of JSON
python3 fetch_news.py -o corpus.json    # write to file instead of stdout
python3 fetch_news.py --source-cap 15   # retain at most 15 items from one source
python3 fetch_news.py --category-cap 40 # retain at most 40 items in one category
```

The command returns a non-zero status if every source fails or filtering leaves no usable items, while still writing the corpus and its error log for diagnosis. Partial source failures remain successful and are surfaced through `errors`.

## Tests

Stdlib `unittest`, no install step, no network:

```bash
python3 -m unittest -v
```

The three pipeline modules are fully type-annotated and checked with mypy in CI (`disallow_untyped_defs`); the test modules deliberately are not.

Coverage targets the logic that's easy to get subtly wrong: date normalization, cutoff selection, relevance filtering, canonical URL deduplication, source/category budgets, oversized responses, empty-run failure behavior, briefing structure, and corpus-grounded citations. Tests patch network boundaries and run without making live requests.

## Evaluating the LLM step

The fetch step is deterministic, so it can be unit tested. The ranking step isn't — but most of the ways it goes wrong are *structural*, and structural failures can be checked exactly against the corpus the briefing was derived from. No second model required as a judge.

```bash
python3 eval_briefing.py --corpus corpus.json --briefing briefing.md
```

Findings come at two levels, and the split is the interesting part:

| Level | Meaning | Examples |
|---|---|---|
| **ERROR** | The parsed briefing violates a structural contract. The run isn't trustworthy without review. | a recognized citation that isn't in the corpus; a story listed as both included and excluded; a section exceeding its reserved slots; a degraded run reported as healthy |
| **WARN** | A quality target a thin corpus can legitimately miss, or a claim-grounding signal a human should read. | fewer topics than slots; a short exclusion log; an HN item cited without its discussion link; a figure or quotation absent from the cited item; a summary longer than the evidence behind it |

That distinction is deliberate. If only two dev-practices posts cleared the cutoff, three slots *cannot* be filled — that's the corpus's fault, not the model's, and failing the run for it would train you to ignore the checker. A recognized citation outside the corpus is never acceptable. Use `--strict` to fail on warnings too.

### Regression-testing a prompt change

[`fixtures/`](fixtures) holds a frozen corpus and the briefing generated from it. To check whether an edit to `briefing-prompt.md` made things worse:

```bash
python3 eval_briefing.py --corpus fixtures/corpus-2026-08-08.json --briefing your-new-output.md
```

Run the agent against the frozen corpus, check the output, and diff it against [`fixtures/briefing-2026-08-08.md`](fixtures/briefing-2026-08-08.md). The frozen corpus is what makes this a controlled comparison — same input, so any difference is attributable to the prompt.

The reference briefing is a **regression baseline, not a golden answer**. Ranking is judgment, and a prompt change that reorders two topics isn't automatically a regression. What the baseline catches is the silent structural stuff: a dropped exclusion log, a collapsed sub-category, links drifting away from the corpus.

## Customizing sources

Edit the `RSS_FEEDS`, `HN_QUERIES`, and `SUBREDDITS` constants at the top of `fetch_news.py`. Broad sources listed in `SOURCE_RELEVANCE_FILTERS` are keyword-filtered; category-specific sources pass through without keyword filtering.

A note on Reddit: its `top` RSS endpoint accepts only coarse buckets (`hour`/`day`/`week`/…), not an arbitrary window. `fetch_news.py` picks the smallest bucket that fully covers `--hours` and then applies the exact cutoff in code, requesting proportionally more posts when the bucket overshoots so in-window coverage stays roughly constant. Reddit also rate-limits anonymous clients aggressively, so requests use a shorter timeout and a bounded two-attempt retry budget. A failed subreddit degrades coverage and appears in corpus health instead of holding the whole run open indefinitely.

## Automating it

This works well as a scheduled task in an agent harness that supports cron-like triggers (e.g. Claude Code's scheduled tasks): run the fetch step, then have the agent read `briefing-prompt.md` and produce the briefing on a daily cadence.

## How this was built

AI-assisted, human-owned. I set the product goals, source policy, system boundaries, evaluation criteria, and acceptance tests; Claude Code and OpenAI Codex accelerated implementation. I reviewed the changes, investigated failures, and remain responsible for explaining and maintaining the result.

The git history is the honest record of that, and a couple of the pull requests are worth reading as artifacts of the process:

- [#5](https://github.com/elanthus/news-briefing/pull/5) (Codex) added relevance filtering to cut corpus noise. [#8](https://github.com/elanthus/news-briefing/pull/8) (Claude) found it was deleting the two stories the reference briefing had led with, using the committed fixture as ground truth to prove it rather than arguing from taste.
- [#9](https://github.com/elanthus/news-briefing/pull/9) narrowed this README's central claim. It previously said the model "never decides what's true"; a human pointed out that summarization is itself a judgment about truth. The checker now measures that gap instead of the README denying it.

Agents produce plausible work quickly, which is exactly why this repo leans on things that fail loudly rather than on anyone's confidence: a schema the fetcher validates before writing, drop counters that must reconcile against what was fetched, a checker whose findings are diffed against a frozen corpus, and tests that run offline on every push.

## License

MIT — see [LICENSE](LICENSE).
