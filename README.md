# news-briefing

A daily news briefing pipeline for use with an LLM agent (e.g. Claude Code). It's split into two deliberately separate steps:

1. **Fetch (deterministic, no LLM).** [`fetch_news.py`](fetch_news.py) pulls RSS feeds (NPR, Politico, The Hill, Axios, BBC, Al Jazeera, AP, The Verge, Ars Technica, Wired, TechCrunch), the Hacker News Algolia API, and Reddit (via public RSS) into a single JSON corpus. Everything older than a hard cutoff (default 48h) is dropped **in code**, not by the model — the LLM never gets to decide what counts as "recent."
2. **Rank & summarize (LLM).** [`briefing-prompt.md`](briefing-prompt.md) is the prompt an agent follows to turn that corpus into a ranked, categorized briefing (US Politics, World Events, AI/Tech with fixed sub-category slots) with an "excluded topics" accountability log so you can see what didn't make the cut and why.

No API keys or credentials required — every source is public RSS/HTTP.

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

## Customizing sources

Edit the `RSS_FEEDS`, `HN_QUERIES`, and `SUBREDDITS` constants at the top of `fetch_news.py` to change what gets pulled in.

## Automating it

This works well as a scheduled task in an agent harness that supports cron-like triggers (e.g. Claude Code's scheduled tasks): run the fetch step, then have the agent read `briefing-prompt.md` and produce the briefing on a daily cadence.

## License

MIT — see [LICENSE](LICENSE).
