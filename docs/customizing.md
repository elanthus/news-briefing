# Customizing the briefing

Two files decide what gets fetched and how the briefing is sectioned. The output contract itself lives in the production prompt, [`briefing-runner-prompt.md`](../briefing-runner-prompt.md). This page walks through both, then shows how to iterate on them without spending a model call per change. The [README](../README.md#point-it-at-your-own-news) has the short version.

## `sources.json`: where items come from

Categories are labels you invent. Each RSS feed is a `["Display name", "https://…"]` pair filed under one of them; Hacker News is a list of search queries and Reddit a list of subreddit names, each with the category its results land in.

```json
{
  "categories": ["world", "ai_tech", "dev_community"],
  "rss_feeds": {
    "world": [["BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"]],
    "ai_tech": [["Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"]]
  },
  "hn_category": "dev_community",
  "hn_queries": ["claude code", "mcp", "llm agent"],
  "reddit_category": "dev_community",
  "subreddits": ["ClaudeAI", "LocalLLaMA"]
}
```

The fetcher accepts only HTTP(S) feed URLs without embedded credentials, and it refuses to connect to non-public addresses at any redirect hop. A feed that fails is recorded as a degraded source rather than silently dropped; the briefing must declare it. [Design notes](design.md#fetching) cover the network and parser boundaries.

**Relevance filters.** Five broad feeds are keyword-filtered before ranking: The Verge, Ars Technica, Wired, GitHub Changelog, and Hacker News. The keyword lists live in `SOURCE_RELEVANCE_FILTERS` in [`fetch_news.py`](../fetch_news.py). Feeds you add are not filtered unless you add them there too, so a general-interest feed will contribute everything it publishes inside the window.

## `briefing-config.json`: what the briefing looks like

Each section names itself, says how many stories it wants, lists the corpus categories it may draw from, and gives the model a sentence of editorial direction.

```json
{
  "schema_version": 1,
  "sections": [
    {
      "name": "AI Dev Tools",
      "group": "AI/Tech",
      "target_stories": 3,
      "corpus_categories": ["dev_community", "ai_tech"],
      "guidance": "Releases and updates to Claude Code, Cursor, Codex, comparable agentic coding tools, and notable MCP servers or integrations.",
      "excluded_stories": 5
    }
  ]
}
```

`corpus_categories` is an eligibility rule the checker enforces, not a hint. A story that arrived under `world` cannot appear in a section that doesn't list `world`, whatever the model decides. In the production two-pass runner the rule is enforced twice: the selection schema for each section enumerates only that section's eligible item identifiers, and the deterministic checker independently rejects any placement that slipped through.

`excluded_stories` sets how many rejected candidates the section's exclusion log records, so a reader can see what the model passed over and why.

## Preview the corpus before spending a model call

This prints the fetched corpus as a readable digest, grouped by category, with every source's outcome:

```bash
python3 -S fetch_news.py --hours 24 --markdown
```

Use it to confirm a new feed parses, lands in the category you expect, and survives the relevance filters.

## Replay a saved corpus while you iterate

Once the feeds are right, save a corpus and replay it. Iterating on section wording then costs one model call instead of a fresh fetch each time, and every attempt runs against identical evidence:

```bash
python3 -S fetch_news.py --hours 24 -o corpus.json
python3 -S run_briefing.py --provider claude-code-cli --model claude-sonnet-5 --corpus corpus.json --output briefing.md
```

## Run-time flags

By default a run fetches live sources (`--corpus` replays a saved one instead), generates, validates, repairs what it can, and asks the model to correct what it can't.

| Flag | Effect |
|---|---|
| `--hours` | Moves the publication window. Items older than the cutoff are never shown to the model. |
| `--source-cap` | Bounds how many items any one publisher contributes to the corpus. |
| `--category-cap` | Bounds how many items any one category contributes. |
| `--strict` | Returns nonzero on any checker finding or degraded source, for use in automation. |
| `--corpus` | Replays a saved corpus instead of fetching. |

Provider selection (`--provider claude-code-cli`, `codex-cli`, `openrouter`, or `openai-compatible` for a local server such as Ollama or LM Studio, with `--endpoint` and `--lean-schema` for the latter's MLX engine) is described in the README's [Generate one](../README.md#generate-one) section.
