# news-briefing

A daily news briefing pipeline built around one constraint: **the model never decides what's true or what's recent.**

Retrieval is deterministic and happens in code. The LLM is given a closed corpus and allowed to do only the thing it's actually good at — ranking and summarizing — and it has to show its work on what it left out.

1. **Fetch (deterministic, no LLM).** [`fetch_news.py`](fetch_news.py) pulls RSS feeds (NPR, Politico, The Hill, Axios, BBC, Al Jazeera, AP, The Verge, Ars Technica, Wired, TechCrunch), the Hacker News Algolia API, and Reddit (via public RSS) into a single JSON corpus. Everything older than a hard cutoff (default 48h) is dropped **in code**. Every item carries a parsed, timezone-normalized publish timestamp.
2. **Rank & summarize (LLM).** [`briefing-prompt.md`](briefing-prompt.md) is the prompt an agent follows to turn that corpus into a ranked briefing (US Politics, World Events, AI/Tech with fixed sub-category slots), plus an **excluded-topics log** so you can see what didn't make the cut and why.

Design notes worth calling out:

- **Fixed slot allocation.** Without it, high-volume AI industry news crowds out the dev-tools and dev-practices content, which is most of why I read this.
- **Exclusion accountability.** The model must name the next 5 stories it dropped per section, with a reason. Silent omission is the failure mode you can't otherwise detect.
- **Corpus health reporting.** Fetch failures are collected per-source and surfaced in the briefing, so a degraded run looks degraded instead of just looking short.

No API keys or credentials. Stdlib only — no `pip install`.

## Sample output

Abridged from a real run (`--hours 24`, 2026-08-08 — 164 items across 4 categories). The full briefing has 5 topics per news section and a complete exclusion log.

<details>
<summary><b>Click to expand sample briefing</b></summary>

> # Daily Briefing — August 8, 2026
> Corpus window: 2026-08-08 00:08 UTC → 2026-08-09 00:08 UTC
>
> ## US Politics
>
> **Senate confirms Todd Blanche as Attorney General** — The GOP-controlled Senate narrowly confirmed former Trump personal attorney Todd Blanche as U.S. Attorney General in an early-morning vote. All Senate Democrats voted against and were overridden by the GOP majority. NPR frames it as a significant win for the president's approach to keeping the Justice Department close to the White House.
> 🔗 https://www.npr.org/2026/08/08/g-s1-137631/senate-confirms-todd-blanche-attorney-general
>
> **Iran publishes demands as Strait of Hormuz talks continue** — The secretary of Iran's Supreme National Security Council, Mohammad Bagher Zolghadr, laid out the country's key demands for the U.S. amid negotiations to reopen the Strait of Hormuz. Vice President JD Vance said there has been "some progress over the last few days" in the Iran–Oman talks.
> 🔗 https://thehill.com/policy/international/6018858-iran-demands-us-hormuz-negotiations/
>
> ## World Events
>
> **Gaza recovery crews pull 19 bodies from a destroyed building** — More than 8,000 people remain missing under rubble in Gaza, with recovery efforts hindered by a lack of heavy machinery.
> 🔗 https://www.aljazeera.com/news/2026/8/8/crews-recover-19-bodies-from-rubble-of-destroyed-gaza-building
>
> **Wildfire evacuations in British Columbia and northern Italy** *(consolidated)* — The Bald Range wildfire in British Columbia has more than doubled in size to over 36 sq miles (95 sq km), remains out of control, and has forced thousands from their homes under new evacuation orders. Separately, at least 200 people were evacuated as a wildfire burned near Lake Garda.
> 🔗 https://www.bbc.co.uk/news/articles/cx25dkwk3e3o
> 🔗 https://www.aljazeera.com/news/2026/8/8/british-columbia-issues-evacuation-orders-ahead-of-fast-moving-wildfires
>
> ## AI/Tech
>
> **AI News (4 slots)**
>
> **Amazon's planned Texas data center could become the largest US climate polluter** *(consolidated)* — Amazon is investing in an on-site power plant for a planned West Texas data center that, per the New York Times, could become the single largest source of greenhouse gas emissions in the United States. Reported independently by TechCrunch and The Verge.
> 🔗 https://techcrunch.com/2026/08/08/planned-amazon-data-center-could-become-the-biggest-climate-polluter-in-the-u-s/
>
> **OpenAI acquires presentation startup NextSlide** — NextSlide says its team members are now working on ChatGPT, pointing at presentation generation as a first-party ChatGPT capability rather than a third-party integration.
> 🔗 https://techcrunch.com/2026/08/08/openai-acquires-presentation-startup-nextslide/
>
> **AI Dev Tools (3 slots)**
>
> **Claude Code adds cross-session messaging** — Claude Code sessions can now message each other, enabling coordination between parallel agent sessions rather than running them as isolated processes.
> 🔗 https://code.claude.com/docs/en/cross-session-messaging
> 🔗 HN: https://news.ycombinator.com/item?id=49222824
> `↑ 44 pts · 25 comments`
>
> **AI Dev Practices (3 slots)**
>
> **"Code was never the hard part" is an insult to all programmers** — A widely-discussed pushback on the claim that coding is the trivial part of software engineering, arguing the framing misreads where the difficulty actually lives. The comment volume relative to points suggests a genuinely contested thread rather than a consensus one.
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
python3 fetch_news.py --hours 24        # narrower window
python3 fetch_news.py --markdown        # human-readable digest instead of JSON
python3 fetch_news.py -o corpus.json    # write to file instead of stdout
```

## Tests

Stdlib `unittest`, no install step, no network:

```bash
python3 -m unittest -v
```

Coverage is on the parsing and filtering logic that's actually easy to get wrong — RFC 822 vs ISO 8601 date handling, timezone normalization, near-duplicate collapsing, and window selection. The fetchers themselves are thin HTTP wrappers and are deliberately not mocked.

## Customizing sources

Edit the `RSS_FEEDS`, `HN_QUERIES`, and `SUBREDDITS` constants at the top of `fetch_news.py`.

A note on Reddit: its `top` RSS endpoint accepts only coarse buckets (`hour`/`day`/`week`/…), not an arbitrary window. `fetch_news.py` picks the smallest bucket that fully covers `--hours` and then applies the exact cutoff in code, requesting proportionally more posts when the bucket overshoots so in-window coverage stays roughly constant. Reddit also rate-limits anonymous clients aggressively — 429s on some subreddits are normal and show up in the corpus-health section rather than failing the run.

## Automating it

This works well as a scheduled task in an agent harness that supports cron-like triggers (e.g. Claude Code's scheduled tasks): run the fetch step, then have the agent read `briefing-prompt.md` and produce the briefing on a daily cadence.

## License

MIT — see [LICENSE](LICENSE).
