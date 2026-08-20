# news-briefing

**A daily news briefing whose citations are checked against the corpus it came from.**

[![CI](https://github.com/elanthus/news-briefing/actions/workflows/ci.yml/badge.svg)](https://github.com/elanthus/news-briefing/actions/workflows/ci.yml)

## Architecture

```mermaid
flowchart LR
    sources[Public sources] --> fetcher[Fetcher<br/>SSRF + XXE defenses<br/>context budgets]
    fetcher --> corpus[Closed corpus]
    corpus --> agent[Generator agent<br/>fail-closed tool policy]
    agent --> checker[Deterministic checker]
    checker --> findings{Blocking findings?}
    findings -- repair budget remains --> correct[Bounded correction]
    correct --> checker
    findings -- no or limit reached --> gate{Publication gate}
    gate -- ready --> publish[Publish]
    gate -- review_required or rejected --> quarantine[Quarantine preview]
    evaluator[Dev-only evaluator<br/>matched attacks, semantic + grounding judges] -. measures .-> agent
    evaluator -. measures .-> checker
```

The runner owns fetch → project → generate → validate → correct → finalize, with verified checkpoints shared across the loop. [The orchestration view](docs/design.md#orchestration-view) distinguishes this coordinated role design from concurrent multi-agent planning.

> **Anthropic turns Claude Code's auto mode on by default** *(consolidated)* — Anthropic is turning Claude Code's auto mode on by default, which TechCrunch says will mean programming with Claude Code requires even less human oversight. A community post dates the switch to Aug 14 and cites a controlled study of 1,053 paid testers in which auto mode blocked 89% of dangerous commands while human manual approval caught only 13.6%.
> 🔗 https://techcrunch.com/2026/08/09/anthropic-is-turning-claude-codes-auto-mode-on-by-default/
> 🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjqcvf/anthropic_flips_claude_code_to_auto_mode_by/

[Full frozen reference run →](docs/sample-briefing.md)

## Results at a glance

<!-- TODO: v2 numbers — replace these historical portfolio-v1 placeholders when a portfolio-v2 model card is committed. -->

The v2 model card is not yet committed. Values marked * are historical v1 placeholders, not current portfolio-v2 claims.

| Model / prompt | Attack success | Benign utility | Machine grounding error |
|---|---:|---:|---:|
| DeepSeek / production | 3.8%* | 17/25 (68%)* | 14.4% [11.7, 17.7]* |
| DeepSeek / reliability-v1 | 2.9%* | 25/25 (100%)* | 10.7% [8.6, 13.3]* |
| HY3 / production | 5.7%* | 25/25 (100%)* | 7.7% [5.6, 10.4]* |
| HY3 / reliability-v1 | 2.9%* | 25/25 (100%)* | 14.1% [11.4, 17.4]* |

Historical placeholder total generation cost: **$3.033816***. Historical suite SHA-256: `aa341680517d5f44b3b4dcb9fe4189a4102bcde75843eee8ffceb46f6dc14b5f`*. See the current [portfolio-v2 clean result](docs/results/portfolio-v2.md), the historical [portfolio-v1 model card](docs/results/portfolio-v1-model-card.md), and the [evaluator guide](evaluator/README.md).

## What this demonstrates

- Production agentic orchestration with a fail-closed provider tool policy and verified checkpoint/resume.
- Deterministic validation and bounded checker-guided correction loops before publication.
- Adversarial prompt-injection evaluation with matched pairs and position/count ablations, informed by AgentDojo and MELON.
- CI/CD-integrated quality gates, credential-free regression fixtures, review-controlled snapshots, and an [embedding-based near-duplicate retrieval benchmark](evaluator/results/dedup-study.md).
- Security engineering across SSRF, DNS rebinding, redirects, and XXE in a zero-dependency runtime.

## Quickstart

Python 3.11+ is the runtime requirement. Install the pinned static-site renderer dependencies before running the tests or building the site.
Fresh generation also needs an authenticated provider: set `OPENROUTER_API_KEY` for OpenRouter, or install and authenticate the `claude` or `codex` CLI.

```bash
git clone https://github.com/elanthus/news-briefing.git
cd news-briefing
python3 -m pip install --requirement requirements-site.txt
python3 -m unittest
python3 -m unittest discover -s evaluator/tests
python3 fetch_news.py -o corpus.json
python3 eval_briefing.py \
  --corpus fixtures/corpus-2026-08-09.json \
  --briefing fixtures/briefing-2026-08-09.md \
  --config fixtures/briefing-config-2026-08-09.json
```

Reddit retrieval is resilient per configured subreddit: anonymous Reddit RSS runs first, the free Arctic Shift archive supplies an exact-window fallback, and ScrapeCreators is called only when both free paths return no usable posts. Set `SCRAPECREATORS_API_KEY` to enable that final authenticated fallback; its subreddit endpoint charges one credit per request. Normal runs spend no credits when the free paths succeed, while the current four-subreddit configuration can spend at most four credits in the worst case.

Generate a fresh briefing with one authenticated provider:

```bash
OPENROUTER_API_KEY=... python3 run_briefing.py \
  --provider openrouter --model OPENROUTER_MODEL_ID --output briefing.md

python3 run_briefing.py \
  --provider claude-code-cli --model CLAUDE_MODEL_ID --output briefing.md

python3 run_briefing.py \
  --provider codex-cli --model CODEX_MODEL_ID --output briefing.md
```

OpenRouter receives no tools. Claude Code receives only its schema-emission tool. Codex runs with user configuration ignored, action-capable tools disabled, an empty read-only sandbox, and trace-event validation. Each run stores its corpus, request, schema, attempts, findings, manifest, hashes, and trace under `.news-briefing/runs/`; use `--resume` only with an exact compatible checkpoint.

## What is actually guaranteed

The LLM is handed a closed corpus and does the thing it is good at — ranking and summarizing — while showing what it left out. The prompt forbids outside knowledge; the checker verifies the parts of that instruction that are mechanically decidable. It does not pretend that a Markdown parser can prove the model chose the right story or summarized it faithfully.

| | Guarantee |
|---|---|
| What counts as **recent** | **Enforced in code.** The cutoff is applied before the model sees anything. |
| What is **eligible** | **Prompt-constrained.** The model is instructed to use only the closed corpus; semantic compliance is not proven. |
| What may be **linked** | **Enforced for the complete output.** Every web destination must exist in the corpus, including required `🔗` citations, Markdown and HTML links, autolinks, protocol-relative links, bare `www.` links, and bare HTTP(S) text. |
| Whether a citation supports the topic or belongs in its section | **Not proven.** The checker validates corpus membership, not semantic fit. |
| What is **important** | **Not claimed** — the model ranks. The exclusion log makes that judgment auditable, not absent. |
| Whether the prose is **faithful to the source** | **Heuristically sampled, not proven.** Figures absent from the bounded cited excerpt are retained as nonblocking quality notes because excerpt absence does not establish article absence. Unsupported quotations and prose that substantially outgrows its evidence remain review signals. |
| What the generating model can **do** beyond emit text | **Enforced for OpenRouter and Claude Code; defense in depth for Codex.** The runner supplies no OpenRouter tools and rejects tool calls; Claude Code receives only its internal `StructuredOutput` schema-emission tool; Codex runs with ignored user config/rules in an empty read-only sandbox and fails on non-message/reasoning trace events, but has no documented remove-all-tools flag. |

That last prose row is the real limit on what a Markdown parser can judge. The corpus stores a bounded feed blurb, not the article. Schema v6 raises that bound from 300 to 400 characters so ordinary feed sentences and dates near the old boundary survive, but a faithful summary is still a summary of an excerpt someone else selected.

### Publication archive contract

The scheduled GitHub Pages workflow gives the model up to three checker-guided correction passes before publication. It publishes a complete `ready` briefing only when the runner manifest identifies `final.md` as its final artifact and the file's SHA-256 matches the manifest. If review-requiring findings remain after that bounded repair budget, a `review_required` run may expose its checker-generated `preview.md` under the same hash-bound rule. The static builder renders that Markdown as HTML with embedded HTML disabled and wraps each affected story and its validated, actionable finding in one compact panel separated by a divider; unmatched run-level findings remain in a compact panel above the preview. Nonblocking quality notes stay in the run artifacts and are excluded from public warning panels and counts. When the public story actually redacts a model-authored destination, its panel includes a closed disclosure containing the hash-verified original structured entry as escaped, non-clickable text. `rejected`, `blocked`, and `no_result` runs remain status-only. A lower-ranked retry on a UTC date that already has a more publication-ready entry does not replace that entry.

The newest retained run is rendered directly on the site home page. A date bar at the top links to separate pages for the other retained runs. The site and its machine-readable history keep an inclusive seven-day UTC window—the newest run date and the preceding six dates—and discard older entries and pages. The workflow seeds that window from the hash-verified August 17 dogfood final and the August 18 DeepSeek dogfood preview; live records take precedence at equal or higher publication rank.

The generated `history.json` uses `schema_version: 3` while accepting previously deployed schema-version-1 and -2 histories during migration. Each entry contains `date`, `disposition`, `findings_count`, `findings`, `degraded_sources`, and `markdown`. `markdown` is a string for `ready` and `review_required` entries and is `null` otherwise. `findings_count` counts actionable findings rather than nonblocking quality notes. `findings` contains validated detail only for `review_required` entries, plus optional story context from the hash-bound selected structured artifact; other dispositions retain only `findings_count` so rejected prose is not leaked through metadata. A zero count on a blocked infrastructure failure does not mean the checker accepted a candidate. `degraded_sources` lists fetch errors reported by the corpus; an empty list means no source failure was reported, not that every possible source was available or complete.

## Further reading

- [Design and orchestration](docs/design.md)
- [Evaluation methodology](docs/evaluation-methodology.md)
- [Portfolio-v2 clean result](docs/results/portfolio-v2.md)
- [Dogfooding log](docs/dogfooding.md)
- [Full sample briefing](docs/sample-briefing.md)
- [Evaluator guide](evaluator/README.md)
- [MIT license](LICENSE)

Third-party news titles, feed excerpts, and linked content remain subject to their respective owners' rights and are not licensed under MIT.

The zero-dependency core is deliberate: it forced ownership of HTTP, XML, and validation layers usually delegated to libraries, with the resulting trade-offs documented in [design.md](docs/design.md).
