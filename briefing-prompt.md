# Daily News Briefing

Run this first to build the source corpus (hard 48-hour cutoff enforced in code):

```bash
python3 fetch_news.py -o corpus.json
```

Then read `corpus.json` and produce the briefing below. **Rank and summarize only items present in the corpus.** Do not search for or include anything else — the corpus is the complete universe of candidate stories. Every item already has a verified publish timestamp inside the window; do not second-guess dates. Use today's actual date in the briefing header.

Rank by real-world impact and significance, not virality or engagement counts.

CONSOLIDATION RULE: If multiple stories share a common theme (e.g., corporate layoffs across different companies, tariff actions across multiple countries), merge them into a single bullet with a brief summary of all instances.

## OUTPUT FORMAT

# Daily Briefing — [today's date]
Corpus window: [cutoff] → [generated_at] from corpus.json metadata.

## US Politics
[5 topics, ranked by impact, from the `us_politics` category]

## World Events
[5 topics, ranked by impact, from the `world` category]

## AI/Tech
Fixed slot allocation — do not let any sub-category crowd out the others:

**AI News (4 slots)** — economic impact, industry moves, regulation, funding, model announcements (from `ai_tech`)
**AI Dev Tools (3 slots)** — releases/updates to Claude Code, Cursor, Codex, comparable agentic coding tools; notable MCP servers or integrations (from `dev_community`, supplemented by `ai_tech`)
**AI Dev Practices (3 slots)** — prompting techniques, CLAUDE.md / rules-file practices, agentic workflow patterns, community-validated approaches (from `dev_community`)

For each topic across all categories:
**[Topic headline]** — [2-3 sentence summary of what's happening and why it matters]
🔗 [URL from the corpus item]

For Hacker News items, include both links — the article (`url`) and the HN discussion (`discussion`) — each on its own 🔗 line, followed by the engagement signal on its own line:
🔗 [article URL]
🔗 HN: [discussion URL]
`↑ [points] pts · [comments] comments`
Reddit vote counts are not available (Reddit blocks anonymous API access); omit any score line for Reddit items.

---

### Excluded Topics (accountability log)
For each of the 4 sections (US Politics, World Events, AI Dev Tools, AI Dev Practices), list the 5 next most significant topics that didn't make the cut, with a one-sentence reason each (e.g., "lower immediate impact," "regional rather than national significance," "consolidated into topic #4," "empty summary — insufficient corpus content to evaluate"). Include the 🔗 URL from the corpus item for each excluded topic, same as for included topics.
AI News excluded topics are not required.

---

### Corpus health
If `errors` in corpus.json is non-empty, list the failed sources at the end so degraded coverage is visible.
