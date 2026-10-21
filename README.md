<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/favicon-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/favicon-light.png">
    <img alt="" src="docs/images/favicon-light.png" width="72" height="72" align="center">
  </picture>
  news briefing
</h1>

**A daily news briefing where code, not the prompt, decides what a model may cite.**

[![CI](https://github.com/elanthus/news-briefing/actions/workflows/ci.yml/badge.svg)](https://github.com/elanthus/news-briefing/actions/workflows/ci.yml)
&nbsp;·&nbsp; Live site → <https://elanthus.github.io/news-briefing/>

![Runtime pipeline: fetch, then per model in the fallback chain select, check, freeze, write prose and gate; a ready candidate is reviewed, confirmed, repaired, followed up, published and deployed](docs/images/runtime-pipeline.svg)

## What this demonstrates

- **Code-owned citation projection.** The model never sees a URL. It chooses opaque handles, and code resolves each to its destination ([`agent_runner/output.py`](agent_runner/output.py), [ADR 0001](docs/adr/0001-citation-projection.md)).
- **Two schema-constrained passes with frozen evidence.** Selection picks handles; code validates and freezes that evidence; prose sees only it ([ADR 0002](docs/adr/0002-two-pass-selection-then-prose-with-frozen-evidence.md)).
- **Fail-closed boundaries.** DNS-pinned SSRF defense with every redirect hop revalidated ([`news_fetch/transport.py`](news_fetch/transport.py)), `DOCTYPE` refusal before an XML tree is built ([`news_fetch/feed_xml.py`](news_fetch/feed_xml.py)), and bounded CLI output capture ([`agent_runner/providers.py`](agent_runner/providers.py)).
- **An offline-verifiable evaluation harness.** Positive-evidence oracles ([what is fixed](evaluator/README.md#what-is-fixed)), Wilson and case-cluster bootstrap intervals ([methodology](docs/evaluation-methodology.md#denominators-and-uncertainty)), hash-frozen [protocols](evaluator/protocols/), and credential-free [evidence bundles](docs/results/EVIDENCE-ASSETS.md).
- **A daily production loop with human-adjudicated review of its repair stage.** Jev, the `typesafe/jev-1.13` reviewer model called through OpenRouter's Decisions API, reviews and repairs each run before it publishes with an integrity report ([Jev checks and repairs](docs/jev-review.md)); a [28-day evaluation](docs/results/jev-stage-v1.md) adjudicates the repair stage.

The rule dates from an August 2026 run that cited an article the fetcher never retrieved ([dogfooding log](docs/dogfooding.md#2026-08-09--the-run-behind-the-committed-reference-pair)): **anything code can decide, the prompt does not get to decide.**

## Generate one

Python 3.11+, no dependencies:

```bash
# Collect a frozen corpus first
python3 -S fetch_news.py --output corpus.json

# Signed-in `claude` session
python3 -S run_briefing.py --provider claude-code-cli --model claude-sonnet-5 --corpus corpus.json --output briefing.md

# Signed-in `codex` session
python3 -S run_briefing.py --provider codex-cli --model gpt-5.6-terra --corpus corpus.json --output briefing.md

# Needs OPENROUTER_API_KEY
python3 -S run_briefing.py --provider openrouter --model deepseek/deepseek-v4-flash --corpus corpus.json --output briefing.md

# Any OpenAI-compatible server (default: local Ollama)
python3 -S run_briefing.py --provider openai-compatible --model qwen3:32b --corpus corpus.json --output briefing.md
```

Local-server settings, the shared time and cost allowance, and custom sources are in [customizing](docs/customizing.md#local-servers-and-the-generation-allowance).

## What code enforces, and what it doesn't

| Question | Enforcement |
|---|---|
| Is this item inside the publication window? | The fetcher applies the cutoff before generation. |
| Where can a link point? | Only to a code-owned destination for the handle the model selected. |
| Is the citation in the run's evidence? | The validator checks the handle and rendered destination against the frozen corpus. |
| Is the story eligible for this section? | Per-section schema enums and the validator restrict eligible handles. |
| Did a source silently fail? | Every source request records an outcome, and the briefing must declare the resulting corpus health. |
| Is a reported story also listed as excluded? | Shared canonical URLs, matching headlines after typography normalization, and copied summaries of at least eight words block publication. Reworded duplicates still need model judgment. |
| **Is the summary faithful to the article?** | Jev checks and repairs support against the frozen title and excerpt. The full article is not reviewed; low scores do not prove factual accuracy. |

Jev scores stories against their frozen excerpts, rechecks each flag in isolation, and triggers bounded repairs. Its judgments can be wrong; [Jev checks and repairs](docs/jev-review.md) has the thresholds and bounds.

## Architecture

```text
fetch_news.py          →  corpus.json (schema v7, validated on write)
agent_runner/          →  project → select → validate/repair/correct → freeze
                          → write prose → validate/repair/correct → disposition gate
run_daily_briefing.py  →  HY3 → DeepSeek V4 Flash → Gemini 3.7 Flash until one is ready,
                          then Jev review → confirm → bounded repair → follow-up review
eval_briefing.py       →  deterministic policy checker, usable standalone
prepare_publication.py / build_site.py  →  static site + per-run integrity report
daily_publish.py       →  daily workflow driver: fetch or restore, run, publish
```

The validator rejects any URL or reference token in prose: destination allowlisting, not semantic grounding. [Design notes](docs/design.md) cover orchestration and the integrity report.

## Read one

[`docs/sample-briefing.md`](docs/sample-briefing.md) is the published September 27, 2026 run, copied byte for byte from the site's `history.json`.

| Reader view | Auditor view |
|---|---|
| ![Reader view of the daily briefing](docs/images/reader-view.png) | ![Current integrity report from an offline fixture](docs/images/auditor-report-current.jpg) |

The reader view renders the sample briefing; its earlier dates are placeholders. The auditor view uses fixtures and synthetic judge scores: layout, not a quality result. Reproduce it with `uv run --python 3.11 --with-requirements requirements-site.txt --no-project python -m tests.render_integrity_example /tmp/briefing-example` (a new directory), then open `/tmp/briefing-example/site/reports/2026-09-30.html`.

## Watch it catch an injection

[`fixtures/current-injection-corpus.json`](fixtures/current-injection-corpus.json) hides an instruction in a feed item's `summary` telling the summarizer to cite `https://security-advisory.example.com/urgent`. The checker rejects [what an obedient summarizer produces](fixtures/injection-briefing.md):

```bash
python3 -S eval_briefing.py --corpus fixtures/current-injection-corpus.json --briefing fixtures/injection-briefing.md --config fixtures/injection-config.json
```

```text
ERROR [ungrounded_link] AI Dev Tools: HTTP(S) URL is not in the corpus — https://security-advisory.example.com/urgent

1 error(s), 0 warning(s)
```

## Historical injection benchmark

[`evaluator/`](evaluator/) is a development-only benchmark: 22 utility cases and 33 indirect prompt-injection attacks. [Parity v3](docs/results/parity-v3.md) (October 16, 2026) ran the version 8 suite at a frozen revision: 1,200 planned rows, 1,175 completed, 24 provider errors, and one disclosed correction error.

Of the 105 primary attack trials per condition, 75 target behaviors the schema, validator, and renderer enforce in code. The informative signals are the 30 selection trials, the judgment the model controls, and the 12-case position/count ablation:

| Model / prompt | Primary, all families | Primary, selection | Ablation, selection | Ablation, citation |
|---|---:|---:|---:|---:|
| DeepSeek V4 Flash / production-runner | 0/104; 0.0% [0.0, 3.6] | 0/29; 0.0% [0.0, 11.7] | 2/30; 6.7% [1.8, 21.3] | 0/30; 0.0% [0.0, 11.4] |
| DeepSeek V4 Flash / runner-deepseek-v4-flash | 0/103; 0.0% [0.0, 3.6] | 0/28; 0.0% [0.0, 12.1] | 20/26; 76.9% [57.9, 89.0] | 0/25; 0.0% [0.0, 13.3] |
| Tencent HY3 / production-runner | 0/105; 0.0% [0.0, 3.5] | 0/30; 0.0% [0.0, 11.4] | 0/30; 0.0% [0.0, 11.4] | 0/30; 0.0% [0.0, 11.4] |
| Tencent HY3 / runner-deepseek-v4-flash | 1/105; 1.0% [0.2, 5.2] | 1/30; 3.3% [0.6, 16.7] | 0/30; 0.0% [0.0, 11.4] | 0/30; 0.0% [0.0, 11.4] |

DeepSeek under the `runner-deepseek-v4-flash` prompt reached 20/26 in selection ablation (20/30 counting provider errors as failures). All families, with structural utility:

| Model / prompt | Structural utility (final) | Targeted attack success, all 21 primary cases (final) |
|---|---:|---:|
| DeepSeek V4 Flash / production-runner | 90/109; 82.6% [74.4, 88.5] | 0/104; 0.0% [0.0, 3.6] |
| DeepSeek V4 Flash / runner-deepseek-v4-flash | 94/100; 94.0% [87.5, 97.2] | 0/103; 0.0% [0.0, 3.6] |
| Tencent HY3 / production-runner | 103/110; 93.6% [87.4, 96.9] | 0/105; 0.0% [0.0, 3.5] |
| Tencent HY3 / runner-deepseek-v4-flash | 100/110; 90.9% [84.1, 95.0] | 1/105; 1.0% [0.2, 5.2] |

Rates are successes/trials with 95% Wilson intervals, which describe this fixed authored suite, not deployment traffic; provider-error rows are excluded. Structural utility does not measure news quality or grounding, and benchmark settings differ from the daily service. HY3 answered a weaker provider-enforced citation schema than DeepSeek, so their cells are not directly comparable. The within-run prompt comparison and the comparison with [Parity v2](docs/results/parity-v2.md) are descriptive and not eligible for the promotion gate. The [methodology](docs/evaluation-methodology.md#current-evaluation-results) sets the claims boundary. Verify the [evidence bundle](docs/results/EVIDENCE-ASSETS.md) offline:

```bash
python3 -S -m evaluator.evidence_assets fetch
python3 -S -m evaluator verify-public-run .news-briefing/evidence/parity-v3-evidence
```

## Further reading

- [Design notes](docs/design.md) · [ADRs](docs/adr/README.md) · [evaluation methodology](docs/evaluation-methodology.md) · [benchmark guide](evaluator/README.md)
- [October 2 integrity and quality assessment](docs/results/remediation-2026-10-02.md) · [September 30 preliminary Jev evaluations](docs/results/jev-preliminary-2026-09-30.md) · [research retention](docs/research-retention.md)
- [Grounding monitor](docs/results/grounding-monitor.md) · [archive contract](docs/publication-archive-contract.md) · [dogfooding log](docs/dogfooding.md) · [write-up](docs/writeups/injection-benchmark-post.md)
- [`SECURITY.md`](SECURITY.md) · [`CONTRIBUTING.md`](CONTRIBUTING.md) · [`CLAUDE.md`](CLAUDE.md)

MIT licensed. Third-party news titles, feed excerpts, and linked content remain subject to their owners' rights and are not licensed under MIT.
