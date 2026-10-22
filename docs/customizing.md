# Customizing the briefing

Use `sources.json` to select inputs and `briefing-config.json` to define sections.
[`agent_runner/output.py`](../agent_runner/output.py) builds schemas and validates output.
The [production prompt](../briefing-runner-prompt.md) supplies editorial instructions.
See the [quick start](../README.md#generate-one) for generation commands.

## `sources.json`: where items come from

Define category names, then assign each source to a category.
Each RSS entry contains a display name and feed URL.
Hacker News uses search queries. Reddit uses subreddit names.

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

Feed URLs must use HTTP(S) and contain no credentials.
The fetcher rejects non-public addresses at each redirect.
Failed sources remain visible in corpus health. See [fetching](design.md#fetching).

`SOURCE_RELEVANCE_FILTERS` in [`news_fetch/relevance.py`](../news_fetch/relevance.py) defines keyword filters for known broad feeds.
These include The Verge, Ars Technica, Wired, GitHub Changelog, Simon Willison's Weblog, and Hacker News.
A new feed has no relevance filter unless you add one there.

`SOURCE_NOISE_FILTERS` in the same module drops items that match a pattern, per source.
It applies before the relevance filter.
The Codex Releases feed uses it to drop prerelease tags such as `0.164.0-alpha.12`.
Stable releases pass through.
Add a feed there when it publishes items that are never briefing material.

`dev_community` includes release feeds for Claude Code, Cursor, Codex, and MCP reference servers.
It also includes GitHub Changelog, Simon Willison's blog, and subreddits.
Reddit sources use the smaller of `--source-cap` and `REDDIT_SOURCE_CAP` in `news_fetch/curation.py`.

## `briefing-config.json`: what the briefing looks like

Each section defines its name, story target, eligible categories, editorial guidance, and exclusion-log target.

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

`corpus_categories` is an enforced eligibility rule.
For example, a section cannot select a `world` item unless it permits `world`.
The selection schema lists eligible handles. The validator checks the selection independently.

`excluded_stories` sets the exclusion-log target.
Set it to `0` to exempt a section from that log.
The log records rejected candidates and reasons.

## Preview the corpus before spending a model call

Print a digest grouped by category, with source outcomes:

```bash
python3 -S fetch_news.py --hours 24 --markdown
```

Check that each new feed parses, uses the expected category, and passes its relevance filter.
This command fetches live sources but makes no model call.

## Replay a saved corpus while you iterate

Save a corpus, then generate from those fixed bytes:

```bash
python3 -S fetch_news.py --hours 24 -o corpus.json
python3 -S run_briefing.py --provider claude-code-cli --model claude-sonnet-5 --corpus corpus.json --output briefing.md
```

Each successful generation requires separate selection and prose calls.
Corrections and optional semantic review or repair can add calls.
Use a new output path for each comparison, or use `--force` to replace an existing output.

## Run-time flags

Generation requires a saved corpus. Each generation uses a new run directory.
Interrupted production runs retain diagnostics and cannot resume.
The evaluator has a separate checkpoint-resume contract.

| Flag | Effect |
|---|---|
| `fetch_news.py --hours` | Sets the lookback window. Reddit RSS uses the top 25 posts from a covering time bucket. Longer windows can miss posts. |
| `fetch_news.py --source-cap` | Limits items per publisher. Reddit uses the lower override described above. |
| `fetch_news.py --category-cap` | Limits items per category. |
| `run_briefing.py --strict` | Returns nonzero for any finding or degraded coverage. This flag is not a fetcher option. |
| `run_briefing.py --corpus` | Supplies required frozen corpus bytes. The manifest binds their hash. |
| `run_briefing.py --max-corrections` | Allows 0–3 corrections per stage; default 1. The daily runner defaults to 3. |

Generation first applies permitted code repairs. It requests model corrections for remaining blocking findings when the correction allowance permits them.
The [README](../README.md#generate-one) shows each supported provider.

## Local servers and the generation allowance

`--provider openai-compatible` uses local Ollama by default.
Use `--endpoint` for another chat-completions URL.
A complete run can need a large context window. Input size depends on the corpus and schemas.
To increase Ollama's context allowance:

```bash
OLLAMA_CONTEXT_LENGTH=65536 ollama serve
```

For LM Studio's MLX engine, add `--lean-schema`.
This reduces schema complexity while code still validates the complete contract.

Selection, prose, corrections, and daily fallback models share 30 minutes and a USD 25 allowance by default.
Daily backfills share that allowance across dates.
Use `--generation-seconds` and `--generation-usd` to change these limits.
Missing billing data retains a conservative reservation. Ambiguous completion stops further generation calls.
These controls cannot guarantee a strict billing cap. See [design notes](design.md#the-code-owned-runner).
