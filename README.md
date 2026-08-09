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

Complete frozen reference result from a real run (`--hours 24`, 2026-08-08 — 164 items across 4 categories). The same result is stored unquoted in [`fixtures/briefing-2026-08-08.md`](fixtures/briefing-2026-08-08.md) for regression testing.

<details>
<summary><b>Click to expand full briefing</b></summary>

> # Daily Briefing — August 8, 2026
>
> Corpus window: 2026-08-08 00:08 UTC → 2026-08-09 00:08 UTC
>
> ## US Politics
>
> **Senate confirms Todd Blanche as Attorney General** — The GOP-controlled Senate narrowly confirmed former Trump personal attorney Todd Blanche as U.S. Attorney General in an early-morning vote. All Senate Democrats voted against the nomination and were overridden by the Republican majority. NPR frames the confirmation as a significant win for the president's approach to keeping the Justice Department close to the White House.
> 🔗 https://www.npr.org/2026/08/08/nx-s1-5925869/gop-controlled-senate-delivers-win-for-trump-with-blanche-confirmation
>
> **Iran publishes demands as Strait of Hormuz talks near a framework** *(consolidated)* — The secretary of Iran's Supreme National Security Council, Mohammad Bagher Zolghadr, laid out Iran's key demands for the U.S. amid negotiations to reopen the Strait of Hormuz. Iranian officials signaled that Iran and Oman are "close" to a final framework, though concessions remain outstanding.
> 🔗 https://thehill.com/policy/international/6018858-iran-demands-us-hormuz-negotiations/
> 🔗 https://thehill.com/homenews/administration/6018352-live-updates-senate-august-recess-blanche-spending-bill-iran-war/
>
> **Vance warns the US is still "in the middle of the game" with Iran** — Despite progress in negotiations, the Vice President cautioned that the conflict with Iran is not resolved, tempering expectations set by the Hormuz talks. The framing matters because it signals the administration is not treating a shipping-lane deal as an end to the broader confrontation.
> 🔗 https://thehill.com/homenews/administration/6018727-vance-iran-negotiation-progress/
>
> **USPS reports a $2.5B quarterly loss** — The United States Postal Service's net losses for the third quarter of 2026 reached $2.5 billion as leadership continues searching for a path to financial stability. Recurring losses of this size keep postal restructuring on the congressional agenda.
> 🔗 https://thehill.com/business/6018716-usps-financial-woes-2-billion-loss/
>
> **Treasury Secretary Bessent says the "K-shaped economy" is over** — Bessent pushed back on the framing that has become shorthand for diverging economic outcomes between high- and low-income Americans. The claim is a notable administration position on whether the recovery is broad-based.
> 🔗 https://thehill.com/business/6018058-scott-bessent-us-economy-defense/
>
> ## World Events
>
> **Vance says the US "destroyed" Iran's nuclear programme** — The Vice President claimed Washington has destroyed Iran's nuclear programme and degraded its military capability. The assertion lands while Hormuz negotiations are still unresolved, making it a significant statement of the administration's position on the conflict's outcome.
> 🔗 https://www.aljazeera.com/video/newsfeed/2026/8/8/vance-says-us-destroyed-irans-nuclear-programme?traffic_source=rss
>
> **Gaza recovery crews pull 19 bodies from a destroyed building** — More than 8,000 people remain missing under rubble in Gaza, with recovery efforts hindered by a lack of heavy machinery. The scale of the missing, set against the pace of recovery, is the story rather than any single building.
> 🔗 https://www.aljazeera.com/news/2026/8/8/crews-recover-19-bodies-from-rubble-of-destroyed-gaza-building?traffic_source=rss
>
> **Wildfire evacuations in British Columbia and northern Italy** *(consolidated)* — The Bald Range wildfire in British Columbia has doubled in size to more than 36 sq miles (95 sq km), remains out of control, and has forced thousands from their homes under new evacuation orders. Separately, at least 200 people were evacuated as a wildfire burned near Lake Garda in Italy.
> 🔗 https://www.bbc.co.uk/news/articles/cx25dkwk3e3o?at_medium=RSS&at_campaign=rss
> 🔗 https://www.aljazeera.com/video/newsfeed/2026/8/8/at-least-200-people-evacuated-as-wildfire-rages-near-lake-garda?traffic_source=rss
>
> **Car bomb attack in Colombia follows hardline president's inauguration** — An explosives attack on the Pan-American Highway in the country's southwest came shortly after the inauguration, with the government promising a harsh response. Early-term security incidents tend to shape the direction of a new administration's policy.
> 🔗 https://www.aljazeera.com/news/2026/8/8/car-bomb-attack-rattles-colombia-after-inauguration-of-hardline-president?traffic_source=rss
>
> **Turkey says its pact with Saudi Arabia and Pakistan does not target Iran** — Foreign Minister Hakan Fidan clarified that the NATO-like agreement is not aimed at any particular country, following speculation that it was formed in response to Iran. The denial is itself a signal of how the regional bloc is being read.
> 🔗 https://thehill.com/policy/international/6018834-turkey-says-iran-not-target-of-pact-with-saudi-arabia-pakistan/
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
> **DeepMind's hurricane model surprises weather scientists** — The open-source WeatherNext model produces accurate predictions from lower-resolution weather data, reportedly buying forecasters an extra day of lead time. It's a concrete case of an ML model displacing a physics-simulation workflow in an operational setting.
> 🔗 https://arstechnica.com/science/2026/08/deepminds-hurricane-model-bought-forecasters-an-extra-day/
>
> **Perseverance's autonomous driving proves out on Mars** — About 90 percent of the distance driven by the Perseverance rover has been autonomous, making it the first self-driving vehicle on Mars by a wide operational margin.
> 🔗 https://arstechnica.com/space/2026/08/the-first-self-driving-vehicle-on-mars-has-proven-to-be-a-smashing-success/
>
> **AI Dev Tools (3 slots)**
>
> **Claude Code adds cross-session messaging** — Claude Code sessions can now message each other.
> 🔗 https://code.claude.com/docs/en/cross-session-messaging
> 🔗 HN: https://news.ycombinator.com/item?id=49222824
> `↑ 44 pts · 25 comments`
>
> **llama.cpp opens Longcat-Flash support for testing** — A pull request adding Longcat-Flash support is ready for testing, validated so far against a small 8B sub-model extracted from the original. Model support landing in llama.cpp is typically the gate for local availability across downstream tooling.
> 🔗 https://www.reddit.com/r/LocalLLaMA/comments/1vipk8z/model_support_longcatflash_need_testing_by_ngxson/
>
> **Cursor 3.15.6 relocates the Claude Code panel** — Users report the Claude Code integration moved out of its previous position under Agents/Chats after the update, with no obvious setting to restore it. Worth tracking if you run Claude Code inside Cursor, since the change appears to be layout-level rather than configurable.
> 🔗 https://www.reddit.com/r/cursor/comments/1viv84t/claude_code_layout_changed_in_cursor_3156_how_do/
>
> **AI Dev Practices (3 slots)**
>
> **"Code was never the hard part" is an insult to all programmers** — A widely-discussed pushback on the claim that writing code is the easy part of programming.
> 🔗 https://blog.senko.net/code-was-never-the-hard-part-is-an-insult-to-all-programmers
> 🔗 HN: https://news.ycombinator.com/item?id=49222189
> `↑ 526 pts · 345 comments`
>
> **MoE vs dense in local coding tests: ~4x faster, smaller quality gap than expected** — A hands-on comparison of Qwen 35B-A3B MoE against Qwen 27B dense on local coding-maintenance tasks found the MoE model substantially faster with a narrower quality gap than anticipated. Useful as a concrete data point for local model selection rather than a benchmark claim.
> 🔗 https://www.reddit.com/r/LocalLLaMA/comments/1vinr66/qwen_35ba3b_moe_vs_27b_dense_in_local_coding/
>
> **Enabling PCI-E peer-to-peer on consumer Nvidia cards** — A setup writeup for vLLM users running two or more GPUs, reporting meaningful gains from enabling P2P on consumer cards. The author explicitly notes the post was written without LLM assistance, which is itself a signal about how community writeups are being received.
> 🔗 https://www.reddit.com/r/LocalLLaMA/comments/1vj7wey/enabling_pcie_p2p_for_consumer_nvidia_cards_will/
>
> ---
>
> ### Excluded Topics (accountability log)
>
> **US Politics**
> - *Dems blast Blanche as enabler of 'Trump's corruption'* — consolidated into US Politics topic #1. 🔗 https://thehill.com/homenews/senate/6018748-democrats-oppose-blanche-confirmation/
> - *Sunday shows preview: Iran-Hormuz deal hangs in balance* — preview of coverage rather than an event; consolidated into topic #2. 🔗 https://thehill.com/homenews/sunday-talk-shows/6018823-sunday-preview-vance-iran-negotiations-progress/
> - *El-Sayed faces 'risky bet' with Michigan's Black voters* — primary-race dynamics with lower immediate national impact. 🔗 https://thehill.com/homenews/campaign/6017875-el-sayed-michigan-senate-black-voters/
> - *Acting ICE chief knocks AP for 'misleading' body cam report* — agency press dispute rather than a policy change. 🔗 https://thehill.com/homenews/administration/6018782-ice-chief-david-venturella-ap-bodycam/
> - *Hunter Biden commends 'woken up' Massie, Greene* — commentary from a private individual with no policy consequence. 🔗 https://thehill.com/homenews/administration/6018639-hunter-biden-greene-massie-support/
>
> **World Events**
> - *British Columbia issues evacuation orders ahead of fast-moving wildfires* — consolidated into World Events topic #3. 🔗 https://www.aljazeera.com/news/2026/8/8/british-columbia-issues-evacuation-orders-ahead-of-fast-moving-wildfires?traffic_source=rss
> - *Gaza health chief urges action to save Dr. Abu Safia* — individual case within the broader Gaza situation covered in topic #2. 🔗 https://www.aljazeera.com/news/2026/8/8/gaza-health-chief-urges-action-to-save-dr-abu-safia-before-its-too-late?traffic_source=rss
> - *Four killed in helicopter crash in Brazil's Rio de Janeiro* — tragic but locally contained, no wider policy or security implication. 🔗 https://www.aljazeera.com/news/2026/8/8/four-killed-in-helicopter-crash-in-brazils-rio-de-janeiro?traffic_source=rss
> - *Caitlin Clark assessed 8th technical foul* — sports; outside the impact criteria for this section. 🔗 https://news.google.com/rss/articles/CBMimwFBVV95cUxPSWdHVGItRHlyRXgwQXNlWlZGZXRlZjN0S1hPSWxPTGI4Z2NFazFSRXQ3OGJyeE40UXJMNHBTVjdJVG5TZy1mN012amxWSVZaclBTcG9pbVIycG9DOUJfQkdOejFKdmtVREJSX09BQVFGSW5QVjYyNGNqanhob3loT3VtaFRHTGFaSGJvU3ZTekRWME5WYThTYnJtbw?oc=5
> - *Whitney Houston No. 1s album Q&A with Pat Houston* — entertainment feature, not a world event. 🔗 https://news.google.com/rss/articles/CBMingFBVV95cUxPYnFxLWl1TmJSY0t6R25JNHBBSjNOWEpjOERRUWhfSGVwRXdhWnBVMVFKczhFd1poVWFDWW5PTHczWUFZajRraVVQOFcyMC1sOGJ6WVBWdmJDTlloLWlzSzMxWWFPbEZJeUlFNTVPMVhZaGEwTzBidEpXd3FDal9Xb1ppME1DMEdFVGxBS3JNUU1raWN1VkNsUThUWE5Fdw?oc=5
>
> **AI Dev Tools**
> - *Can we import Codex conversations like Claude's?* — feature request, not a shipped capability. 🔗 https://www.reddit.com/r/cursor/comments/1vitsh5/can_we_import_codex_conversations_like_claudes/
> - *After update I can no longer open codex in the sidebar* — single-user support question with no resolution. 🔗 https://www.reddit.com/r/cursor/comments/1vintt0/after_update_i_can_no_longer_open_codex_in_the/
> - *Inline Edit gone?* — support question, likely local configuration. 🔗 https://www.reddit.com/r/cursor/comments/1vj4h71/inline_edit_gone/
> - *Why is Composer-2.5-fast both in Cursor & Other Models?* — billing confusion rather than a tooling change. 🔗 https://www.reddit.com/r/cursor/comments/1viqip0/why_is_composer25fast_both_in_cursor_other_models/
> - *Previous Cursor chats for a project was lost* — individual data-loss report, not yet a confirmed regression. 🔗 https://www.reddit.com/r/cursor/comments/1vim27d/previous_cursor_chats_for_a_project_was_lost/
>
> **AI Dev Practices**
> - *RPC model load PR speeds 300GB loads by 300%* — infrastructure optimization, narrower than a general practice. 🔗 https://www.reddit.com/r/LocalLLaMA/comments/1vilcil/i_got_tired_of_my_300gb_model_loads_taking_5min/
> - *Is anyone else finding DeepSeek-V4-Flash unreliable for non-coding tasks?* — open question without a validated conclusion. 🔗 https://www.reddit.com/r/LocalLLaMA/comments/1vikgrj/is_anyone_else_finding_deepseekv4flash_unreliable/
> - *Is Microsoft-Phi dead?* — speculation thread with no release or announcement to anchor it. 🔗 https://www.reddit.com/r/LocalLLaMA/comments/1vj8bxf/is_microsoftphi_dead/
> - *Beware of plan mode* — cautionary anecdote about usage consumption, single data point. 🔗 https://www.reddit.com/r/cursor/comments/1vixt64/beware_of_plan_mode/
> - *Showoff Saturday: Local 4x 6000 Pro* — hardware showcase rather than a transferable practice. 🔗 https://www.reddit.com/r/LocalLLaMA/comments/1vj18h4/showoff_saturday_local_4x_6000_pro_multiyear/
>
> ---
>
> ### Corpus health
>
> 2 sources failed during this run and are not represented above:
>
> - `r/ClaudeAI` — HTTP Error 429: Too Many Requests
> - `r/ClaudeCode` — HTTP Error 429: Too Many Requests
>
> Dev-community coverage is degraded accordingly: the AI Dev Tools and AI Dev Practices sections draw from r/LocalLLaMA, r/cursor, and Hacker News only.

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
